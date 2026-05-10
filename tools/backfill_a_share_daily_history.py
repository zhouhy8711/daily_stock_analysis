#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill active A-share daily history into stock_daily."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
from pathlib import Path
import sys
import threading
from typing import Callable, Iterable, Sequence

import pandas as pd
from sqlalchemy import and_, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_provider.base import DataFetcherManager, normalize_stock_code  # noqa: E402
from src.data.stock_index_loader import get_all_a_share_stock_codes  # noqa: E402
from src.storage import DatabaseManager, StockDaily  # noqa: E402


logger = logging.getLogger(__name__)
_THREAD_LOCAL = threading.local()
FETCHER_CHOICES = ("manager", "akshare", "baostock")


@dataclass(frozen=True)
class MissingSegment:
    start: date
    end: date
    dates: tuple[date, ...]


@dataclass
class BackfillResult:
    code: str
    status: str
    expected_count: int
    existing_count: int
    missing_count: int
    requested_segments: int = 0
    fetched_rows: int = 0
    saved_count: int = 0
    source_counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def source_summary(self) -> str:
        if not self.source_counts:
            return "-"
        return ",".join(f"{source}:{count}" for source, count in self.source_counts.items())


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc


def get_cn_trading_dates(start_date: date, end_date: date) -> list[date]:
    """Return China A-share trading dates; fall back to weekdays if calendar loading fails."""
    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XSHG")
        sessions = calendar.sessions_in_range(
            pd.Timestamp(start_date),
            pd.Timestamp(end_date),
        )
        return [session.date() for session in sessions]
    except Exception as exc:
        logger.warning("读取 A 股交易日历失败，降级为工作日判断: %s", exc)

    current = start_date
    dates: list[date] = []
    while current <= end_date:
        if current.weekday() < 5:
            dates.append(current)
        current += timedelta(days=1)
    return dates


def get_active_a_share_codes(raw_codes: Sequence[str] | None = None) -> list[str]:
    source_codes = raw_codes if raw_codes is not None else get_all_a_share_stock_codes()
    seen: set[str] = set()
    codes: list[str] = []
    for raw_code in source_codes:
        code = normalize_stock_code(str(raw_code)).strip().upper()
        if not code or code in seen:
            continue
        seen.add(code)
        codes.append(code)
    return codes


def load_existing_dates(db: DatabaseManager, code: str, start_date: date, end_date: date) -> set[date]:
    with db.get_session() as session:
        rows = session.execute(
            select(StockDaily.date).where(
                and_(
                    StockDaily.code == code,
                    StockDaily.date >= start_date,
                    StockDaily.date <= end_date,
                )
            )
        ).scalars().all()
    return set(rows)


def split_missing_segments(expected_dates: Sequence[date], existing_dates: set[date]) -> list[MissingSegment]:
    missing_dates = [item for item in expected_dates if item not in existing_dates]
    if not missing_dates:
        return []

    expected_index = {item: index for index, item in enumerate(expected_dates)}
    segments: list[MissingSegment] = []
    segment_dates = [missing_dates[0]]

    for item in missing_dates[1:]:
        previous = segment_dates[-1]
        if expected_index[item] == expected_index[previous] + 1:
            segment_dates.append(item)
            continue
        segments.append(MissingSegment(segment_dates[0], segment_dates[-1], tuple(segment_dates)))
        segment_dates = [item]

    segments.append(MissingSegment(segment_dates[0], segment_dates[-1], tuple(segment_dates)))
    return segments


def filter_frame_to_dates(df: pd.DataFrame, target_dates: Iterable[date]) -> pd.DataFrame:
    if df is None or df.empty or "date" not in df.columns:
        return pd.DataFrame()

    target_set = set(target_dates)
    filtered = df.copy()
    parsed_dates = pd.to_datetime(filtered["date"], errors="coerce").dt.date
    filtered = filtered.loc[parsed_dates.isin(target_set)].copy()
    if not filtered.empty:
        filtered["date"] = pd.to_datetime(filtered["date"], errors="coerce")
    return filtered


def create_fetcher_manager(fetcher: str) -> DataFetcherManager:
    if fetcher == "manager":
        return DataFetcherManager()
    if fetcher == "akshare":
        from data_provider.akshare_fetcher import AkshareFetcher

        return DataFetcherManager(fetchers=[AkshareFetcher()])
    if fetcher == "baostock":
        from data_provider.baostock_fetcher import BaostockFetcher

        return DataFetcherManager(fetchers=[BaostockFetcher()])
    raise ValueError(f"unsupported fetcher: {fetcher}")


def build_fetcher_factory(fetcher: str) -> Callable[[], DataFetcherManager]:
    def _factory() -> DataFetcherManager:
        managers = getattr(_THREAD_LOCAL, "fetcher_managers", None)
        if managers is None:
            managers = {}
            _THREAD_LOCAL.fetcher_managers = managers
        manager = managers.get(fetcher)
        if manager is None:
            manager = create_fetcher_manager(fetcher)
            managers[fetcher] = manager
        return manager

    return _factory


def get_thread_fetcher_manager() -> DataFetcherManager:
    manager = getattr(_THREAD_LOCAL, "fetcher_manager", None)
    if manager is None:
        manager = DataFetcherManager()
        _THREAD_LOCAL.fetcher_manager = manager
    return manager


def backfill_one_stock(
    code: str,
    expected_dates: Sequence[date],
    db: DatabaseManager,
    fetcher_factory: Callable[[], DataFetcherManager] = get_thread_fetcher_manager,
) -> BackfillResult:
    start_date = expected_dates[0]
    end_date = expected_dates[-1]
    existing_dates = load_existing_dates(db, code, start_date, end_date)
    segments = split_missing_segments(expected_dates, existing_dates)

    result = BackfillResult(
        code=code,
        status="skipped" if not segments else "pending",
        expected_count=len(expected_dates),
        existing_count=len(existing_dates),
        missing_count=sum(len(segment.dates) for segment in segments),
        requested_segments=len(segments),
    )
    if not segments:
        return result

    manager = fetcher_factory()
    for segment in segments:
        try:
            df, source = manager.get_daily_data(
                code,
                start_date=segment.start.isoformat(),
                end_date=segment.end.isoformat(),
                days=len(segment.dates),
            )
            filtered = filter_frame_to_dates(df, segment.dates)
            if filtered.empty:
                continue
            result.fetched_rows += len(filtered)
            result.saved_count += db.save_daily_data(filtered, code=code, data_source=source)
            result.source_counts[source] += len(filtered)
        except Exception as exc:
            message = f"{segment.start.isoformat()}~{segment.end.isoformat()}: {exc}"
            result.errors.append(message)
            logger.warning("%s 补齐区间失败: %s", code, message)

    if result.errors and result.saved_count > 0:
        result.status = "partial_failed"
    elif result.errors:
        result.status = "failed"
    elif result.fetched_rows == 0:
        result.status = "no_data"
    else:
        result.status = "fetched"
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Backfill active A-share daily history into the stock_daily table."
    )
    parser.add_argument("--start-date", required=True, type=parse_date, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end-date", required=True, type=parse_date, help="End date, YYYY-MM-DD")
    parser.add_argument(
        "--parallelism",
        "-j",
        type=int,
        default=10,
        help="Concurrent stock fetch workers, default 10",
    )
    parser.add_argument(
        "--codes",
        help="Optional comma-separated stock codes for a scoped run; defaults to all active A-shares",
    )
    parser.add_argument(
        "--fetcher",
        choices=FETCHER_CHOICES,
        default="manager",
        help="Daily data fetcher mode: manager fallback chain, akshare only, or baostock only; default manager",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level, default INFO")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def print_result(index: int, total: int, result: BackfillResult) -> None:
    print(
        f"[{index}/{total}] {result.code} {result.status} "
        f"expected={result.expected_count} existing={result.existing_count} "
        f"missing={result.missing_count} segments={result.requested_segments} "
        f"fetched={result.fetched_rows} saved={result.saved_count} "
        f"sources={result.source_summary()} errors={len(result.errors)}",
        flush=True,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    configure_logging(args.log_level)

    if args.start_date > args.end_date:
        parser.error("--start-date must be earlier than or equal to --end-date")
    if args.parallelism < 1:
        parser.error("--parallelism must be at least 1")

    expected_dates = get_cn_trading_dates(args.start_date, args.end_date)
    if not expected_dates:
        print("目标区间内没有 A 股交易日，无需补齐。")
        return 0

    scoped_codes = args.codes.split(",") if args.codes else None
    codes = get_active_a_share_codes(scoped_codes)
    if not codes:
        print("未找到 A 股股票代码，请先生成 stocks.index.json 或通过 --codes 指定。", file=sys.stderr)
        return 1

    db = DatabaseManager.get_instance()
    total = len(codes)
    print(
        f"开始补齐 A 股日线: stocks={total} range={expected_dates[0]}~{expected_dates[-1]} "
        f"trading_days={len(expected_dates)} parallelism={args.parallelism} fetcher={args.fetcher}",
        flush=True,
    )

    results: list[BackfillResult] = []
    fetcher_factory = build_fetcher_factory(args.fetcher)
    with ThreadPoolExecutor(max_workers=args.parallelism) as executor:
        futures = {
            executor.submit(backfill_one_stock, code, expected_dates, db, fetcher_factory): code
            for code in codes
        }
        for index, future in enumerate(as_completed(futures), start=1):
            code = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                result = BackfillResult(
                    code=code,
                    status="failed",
                    expected_count=len(expected_dates),
                    existing_count=0,
                    missing_count=len(expected_dates),
                    errors=[str(exc)],
                )
            results.append(result)
            print_result(index, total, result)

    status_counts = Counter(result.status for result in results)
    total_saved = sum(result.saved_count for result in results)
    total_fetched = sum(result.fetched_rows for result in results)
    total_errors = sum(len(result.errors) for result in results)

    print("")
    print("补齐完成")
    print(f"  stocks        : {total}")
    print(f"  status        : {dict(status_counts)}")
    print(f"  fetched_rows  : {total_fetched}")
    print(f"  saved_new_rows: {total_saved}")
    print(f"  errors        : {total_errors}")

    return 1 if status_counts.get("failed") or status_counts.get("partial_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
