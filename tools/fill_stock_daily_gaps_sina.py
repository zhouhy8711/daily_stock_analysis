#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Fast gap filler for canonical A-share daily rows using Sina qfq history."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Iterable, Sequence

import pandas as pd
from sqlalchemy import and_, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_provider.akshare_fetcher import AkshareFetcher  # noqa: E402
from data_provider.base import normalize_stock_code  # noqa: E402
from src.data.stock_index_loader import get_all_a_share_stock_codes  # noqa: E402
from src.storage import DatabaseManager, StockDaily  # noqa: E402


@dataclass
class FillResult:
    code: str
    status: str
    missing_count: int
    fetched_count: int = 0
    saved_count: int = 0
    chip_missing_count: int = 0
    chip_saved_count: int = 0
    error: str | None = None


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def get_cn_trading_dates(start_date: date, end_date: date) -> list[date]:
    try:
        import exchange_calendars as xcals

        calendar = xcals.get_calendar("XSHG")
        sessions = calendar.sessions_in_range(pd.Timestamp(start_date), pd.Timestamp(end_date))
        return [session.date() for session in sessions]
    except Exception:
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


def load_existing_chip_dates(db: DatabaseManager, code: str, start_date: date, end_date: date) -> set[date]:
    rows = db.get_chip_daily_range(code, start_date, end_date)
    dates: set[date] = set()
    for row in rows:
        try:
            dates.add(date.fromisoformat(str(row.get("date", ""))[:10]))
        except ValueError:
            continue
    return dates


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


def _load_history_from_db(db: DatabaseManager, code: str, start_date: date, end_date: date) -> pd.DataFrame:
    with db.get_session() as session:
        rows = session.execute(
            StockDaily.__table__.select()
            .where(
                (StockDaily.code == code)
                & (StockDaily.date >= start_date)
                & (StockDaily.date <= end_date)
            )
            .order_by(StockDaily.date)
        ).mappings().all()
    if not rows:
        return pd.DataFrame()
    frame = pd.DataFrame([dict(row) for row in rows])
    for column in ("id", "created_at", "updated_at", "data_source"):
        if column in frame.columns:
            frame = frame.drop(columns=[column])
    return frame


def _normalize_akshare_frame(fetcher: AkshareFetcher, code: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = fetcher._normalize_data(raw, code)
    frame = fetcher._clean_data(frame)
    return fetcher._calculate_indicators(frame)


def _fetch_sina_daily(code: str, start_date: date, end_date: date) -> tuple[pd.DataFrame, str]:
    fetcher = AkshareFetcher(sleep_min=0.0, sleep_max=0.0)
    errors: list[str] = []
    for source, method in (
        ("AkshareSinaRepair", fetcher._fetch_stock_data_sina),
        ("AkshareTencentRepair", fetcher._fetch_stock_data_tx),
    ):
        try:
            raw = method(code, start_date.isoformat(), end_date.isoformat())
            frame = _normalize_akshare_frame(fetcher, code, raw)
            if not frame.empty:
                return frame, source
        except Exception as exc:
            errors.append(f"{source}:{exc}")
    if errors:
        return pd.DataFrame(), "; ".join(errors)
    return pd.DataFrame(), "AkshareRepairNoData"


def fill_one_code(
    code: str,
    expected_dates: Sequence[date],
    *,
    rebuild_chip: bool,
) -> FillResult:
    db = DatabaseManager.get_instance()
    normalized = normalize_stock_code(code)
    existing_dates = load_existing_dates(db, normalized, expected_dates[0], expected_dates[-1])
    missing_dates = set(expected_dates) - existing_dates
    if not missing_dates:
        result = FillResult(normalized, "skipped", 0)
    else:
        result = FillResult(normalized, "pending", len(missing_dates))
        try:
            frame, source = _fetch_sina_daily(normalized, min(missing_dates), max(missing_dates))
            filtered = filter_frame_to_dates(frame, missing_dates)
            if filtered.empty:
                result.status = "no_data"
                result.error = source if "Repair:" in source else None
            else:
                result.fetched_count = len(filtered.index)
                result.saved_count = db.save_daily_data(filtered, normalized, data_source=source)
                result.status = "fetched" if result.saved_count or result.fetched_count else "skipped"
        except Exception as exc:
            result.status = "failed"
            result.error = str(exc)
            return result

    if rebuild_chip:
        chip_existing = load_existing_chip_dates(db, normalized, expected_dates[0], expected_dates[-1])
        chip_missing = set(expected_dates) - chip_existing
        result.chip_missing_count = len(chip_missing)
        if chip_missing:
            from src.services.chip_daily_sync import sync_chip_daily_from_history

            history_start = expected_dates[0] - timedelta(days=365)
            history = _load_history_from_db(db, normalized, history_start, expected_dates[-1])
            if not history.empty:
                result.chip_saved_count = sync_chip_daily_from_history(
                    db,
                    normalized,
                    history,
                    data_source="stock_daily",
                    target_dates=chip_missing,
                    skip_existing=True,
                )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Fill missing A-share stock_daily rows through Sina qfq history.")
    parser.add_argument("--start-date", required=True, type=_parse_date)
    parser.add_argument("--end-date", required=True, type=_parse_date)
    parser.add_argument("--codes", help="Optional comma-separated stock codes")
    parser.add_argument(
        "--parallelism",
        "-j",
        type=int,
        default=1,
        help="Worker count. Keep at 1 because AkShare's Sina/Tencent history path is not thread-safe here.",
    )
    parser.add_argument("--skip-chip", action="store_true", help="Do not rebuild stock_chip_daily from local stock_daily")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.start_date > args.end_date:
        parser.error("--start-date must be earlier than or equal to --end-date")
    if args.parallelism < 1:
        parser.error("--parallelism must be at least 1")
    if args.parallelism > 1:
        parser.error("AkShare/Sina history fetching is not thread-safe in this environment; use --parallelism 1")

    expected_dates = get_cn_trading_dates(args.start_date, args.end_date)
    if not expected_dates:
        print("目标区间内没有 A 股交易日，无需补齐。")
        return 0

    scoped_codes = args.codes.split(",") if args.codes else None
    codes = get_active_a_share_codes(scoped_codes)
    DatabaseManager.get_instance()
    import akshare  # noqa: F401

    print(
        f"开始快速补齐 A 股日线缺口: stocks={len(codes)} "
        f"range={expected_dates[0]}~{expected_dates[-1]} "
        f"trading_days={len(expected_dates)} parallelism={args.parallelism} "
        f"chip={'off' if args.skip_chip else 'on'}",
        flush=True,
    )

    results: list[FillResult] = []
    with ThreadPoolExecutor(max_workers=args.parallelism) as executor:
        futures = {
            executor.submit(
                fill_one_code,
                code,
                expected_dates,
                rebuild_chip=not args.skip_chip,
            ): code
            for code in codes
        }
        total = len(futures)
        for index, future in enumerate(as_completed(futures), start=1):
            try:
                result = future.result()
            except Exception as exc:
                result = FillResult(str(futures[future]), "failed", 0, error=str(exc))
            results.append(result)
            if result.status != "skipped" or result.chip_saved_count:
                print(
                    f"[{index}/{total}] {result.code} {result.status} "
                    f"missing={result.missing_count} fetched={result.fetched_count} "
                    f"saved={result.saved_count} chip_missing={result.chip_missing_count} "
                    f"chip_saved={result.chip_saved_count} error={result.error or '-'}",
                    flush=True,
                )

    status_counts = Counter(result.status for result in results)
    print("完成快速补齐:")
    print(f"  status        : {dict(status_counts)}")
    print(f"  missing       : {sum(result.missing_count for result in results)}")
    print(f"  fetched       : {sum(result.fetched_count for result in results)}")
    print(f"  saved         : {sum(result.saved_count for result in results)}")
    print(f"  chip_missing  : {sum(result.chip_missing_count for result in results)}")
    print(f"  chip_saved    : {sum(result.chip_saved_count for result in results)}")
    failed = [result for result in results if result.status == "failed"]
    if failed:
        print("失败样本:")
        for result in failed[:20]:
            print(f"  {result.code}: {result.error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
