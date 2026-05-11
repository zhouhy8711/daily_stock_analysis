#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Backfill active A-share daily history and chip snapshots into local DB."""

from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, timedelta
import logging
import os
from pathlib import Path
import signal
import sys
import threading
from typing import Any, Callable, Iterable, Mapping, Sequence, TypeVar

import pandas as pd
from sqlalchemy import and_, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_provider.base import DataFetcherManager, normalize_stock_code  # noqa: E402
from data_provider.local_chip_model_fetcher import compute_chip_distribution_from_history  # noqa: E402
from src.data.stock_index_loader import get_all_a_share_stock_codes  # noqa: E402
from src.services.daily_history_enrichment import (  # noqa: E402
    OPTIONAL_DAILY_METRIC_COLUMNS,
    enrich_daily_history_with_quote_fields,
)
from src.storage import DatabaseManager, StockDaily  # noqa: E402


logger = logging.getLogger(__name__)
_THREAD_LOCAL = threading.local()
FETCHER_CHOICES = ("manager", "akshare", "baostock")
T = TypeVar("T")


class StockTimeoutError(TimeoutError):
    """Raised when a single-stock backfill exceeds the configured wall-clock budget."""


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
    chip_expected_count: int = 0
    chip_existing_count: int = 0
    chip_missing_count: int = 0
    chip_fetched_rows: int = 0
    chip_saved_count: int = 0
    chip_source_counts: Counter[str] = field(default_factory=Counter)
    errors: list[str] = field(default_factory=list)

    def source_summary(self) -> str:
        if not self.source_counts:
            return "-"
        return ",".join(f"{source}:{count}" for source, count in self.source_counts.items())

    def chip_source_summary(self) -> str:
        if not self.chip_source_counts:
            return "-"
        return ",".join(f"{source}:{count}" for source, count in self.chip_source_counts.items())


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


def load_existing_chip_dates(db: DatabaseManager, code: str, start_date: date, end_date: date) -> set[date]:
    rows = db.get_chip_daily_range(code, start_date, end_date)
    dates: set[date] = set()
    for row in rows:
        try:
            dates.add(date.fromisoformat(str(row.get("date", ""))[:10]))
        except ValueError:
            continue
    return dates


def _snapshot_date(snapshot: dict) -> date | None:
    try:
        return date.fromisoformat(str(snapshot.get("date", ""))[:10])
    except ValueError:
        return None


def _filter_snapshots_to_dates(snapshots: Sequence[dict], target_dates: set[date]) -> list[dict]:
    filtered: list[dict] = []
    for snapshot in snapshots:
        snapshot_day = _snapshot_date(snapshot)
        if snapshot_day in target_dates:
            filtered.append(snapshot)
    return filtered


def backfill_chip_for_stock(
    code: str,
    expected_dates: Sequence[date],
    db: DatabaseManager,
    fetcher_factory: Callable[[], DataFetcherManager],
) -> tuple[int, int, int, int, str, str | None]:
    """Compute and store missing chip snapshots for the requested trading dates."""
    start_date = expected_dates[0]
    end_date = expected_dates[-1]
    existing_dates = load_existing_chip_dates(db, code, start_date, end_date)
    missing_dates = set(expected_dates) - existing_dates
    if not missing_dates:
        return len(existing_dates), 0, 0, 0, "-", None

    warmup_start = start_date - timedelta(days=365)
    manager = fetcher_factory()
    try:
        history_df, source = manager.get_daily_data(
            code,
            start_date=warmup_start.isoformat(),
            end_date=end_date.isoformat(),
            days=(end_date - warmup_start).days + 1,
        )
        if history_df is None or history_df.empty:
            return len(existing_dates), len(missing_dates), 0, 0, "-", "chip_history_empty"

        chip = compute_chip_distribution_from_history(
            code,
            history_df,
            history_source=source,
            window_days=max(len(history_df), 2),
            include_snapshots=True,
            snapshot_limit=None,
        )
        if chip is None:
            return len(existing_dates), len(missing_dates), len(history_df), 0, source, "chip_compute_empty"

        snapshots = _filter_snapshots_to_dates(chip.snapshots or [], missing_dates)
        if not snapshots:
            history_dates: set[date] = set()
            if "date" in history_df.columns:
                history_dates = set(pd.to_datetime(history_df["date"], errors="coerce").dropna().dt.date)
            if missing_dates.isdisjoint(history_dates):
                return len(existing_dates), len(missing_dates), len(history_df), 0, source, None
            return len(existing_dates), len(missing_dates), len(history_df), 0, source, "chip_snapshots_empty"

        saved_count = db.save_chip_daily_snapshots(code, snapshots, data_source=chip.source)
        return len(existing_dates), len(missing_dates), len(history_df), saved_count, chip.source, None
    except Exception as exc:
        return len(existing_dates), len(missing_dates), 0, 0, "-", str(exc)


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

        sleep_min = float(os.getenv("BACKFILL_AKSHARE_SLEEP_MIN", "2.0"))
        sleep_max = float(os.getenv("BACKFILL_AKSHARE_SLEEP_MAX", "5.0"))
        return DataFetcherManager(fetchers=[AkshareFetcher(sleep_min=sleep_min, sleep_max=sleep_max)])
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


def get_thread_quote_manager() -> DataFetcherManager:
    manager = getattr(_THREAD_LOCAL, "quote_manager", None)
    if manager is None:
        manager = DataFetcherManager()
        _THREAD_LOCAL.quote_manager = manager
    return manager


def prefetch_valuation_quotes(codes: Sequence[str]) -> dict[str, Any]:
    """Fetch one batch quote snapshot for stock_daily valuation enrichment."""
    try:
        from data_provider.akshare_fetcher import AkshareFetcher
        from data_provider.realtime_types import get_realtime_circuit_breaker

        fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
        quote_map: dict[str, Any] = {}
        normalized_codes = [normalize_stock_code(code) for code in codes if str(code or "").strip()]
        chunk_size = 100
        circuit_breaker = get_realtime_circuit_breaker()
        for start in range(0, len(normalized_codes), chunk_size):
            chunk = normalized_codes[start:start + chunk_size]
            if not chunk:
                continue
            circuit_breaker.reset("akshare_tencent")
            quotes = fetcher.get_realtime_quotes(chunk)
            quote_map.update(
                {
                    normalize_stock_code(code): quote
                    for code, quote in quotes.items()
                    if quote is not None
                }
            )
        return quote_map
    except Exception as exc:
        logger.warning("批量预取估值 quote 失败，估值字段将按可用数据写入: %s", exc)
        return {}


def backfill_one_stock(
    code: str,
    expected_dates: Sequence[date],
    db: DatabaseManager,
    fetcher_factory: Callable[[], DataFetcherManager] = get_thread_fetcher_manager,
    backfill_daily: bool = True,
    backfill_chip: bool = True,
    refresh_existing: bool = False,
    enrich_valuation: bool = True,
    quote_lookup: Mapping[str, Any] | None = None,
) -> BackfillResult:
    start_date = expected_dates[0]
    end_date = expected_dates[-1]
    existing_dates = (
        set()
        if backfill_daily and refresh_existing
        else load_existing_dates(db, code, start_date, end_date) if backfill_daily else set()
    )
    segments = split_missing_segments(expected_dates, existing_dates) if backfill_daily else []

    result = BackfillResult(
        code=code,
        status="skipped" if not segments else "pending",
        expected_count=len(expected_dates),
        existing_count=len(existing_dates),
        missing_count=sum(len(segment.dates) for segment in segments),
        requested_segments=len(segments),
        chip_expected_count=len(expected_dates) if backfill_chip else 0,
    )
    if not backfill_daily and not backfill_chip:
        result.status = "skipped"
        return result
    if not segments and not backfill_chip:
        return result

    if backfill_daily and segments:
        manager = fetcher_factory()
        for segment in segments:
            try:
                df, source = manager.get_daily_data(
                    code,
                    start_date=segment.start.isoformat(),
                    end_date=segment.end.isoformat(),
                    days=len(segment.dates),
                )
                if enrich_valuation:
                    normalized_code = normalize_stock_code(code)
                    quote = quote_lookup.get(normalized_code) if quote_lookup is not None else None
                    df = enrich_daily_history_with_quote_fields(
                        df,
                        code,
                        quote=quote,
                        quote_loader=(
                            None
                            if quote_lookup is not None
                            else lambda stock_code: get_thread_quote_manager().get_realtime_quote(
                                stock_code,
                                log_final_failure=False,
                            )
                        ),
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

    if backfill_chip:
        (
            result.chip_existing_count,
            result.chip_missing_count,
            result.chip_fetched_rows,
            result.chip_saved_count,
            chip_source,
            chip_error,
        ) = backfill_chip_for_stock(code, expected_dates, db, fetcher_factory)
        if chip_source and chip_source != "-":
            result.chip_source_counts[chip_source] += result.chip_saved_count
        if chip_error:
            message = f"chip:{chip_error}"
            result.errors.append(message)
            logger.warning("%s 补齐筹码峰失败: %s", code, chip_error)

    has_saved_rows = result.saved_count > 0 or result.chip_saved_count > 0
    if result.errors and has_saved_rows:
        result.status = "partial_failed"
    elif result.errors:
        result.status = "failed"
    elif result.fetched_rows == 0 and result.chip_missing_count == 0:
        result.status = "skipped"
    elif result.fetched_rows == 0 and result.chip_saved_count == 0:
        result.status = "no_data"
    elif result.fetched_rows > 0 and result.saved_count == 0 and result.chip_saved_count == 0:
        result.status = "fetched"
    elif result.saved_count == 0 and result.chip_saved_count > 0:
        result.status = "chip_fetched"
    else:
        result.status = "fetched"
    return result


def refresh_stock_daily_valuation_from_db(
    code: str,
    expected_dates: Sequence[date],
    db: DatabaseManager,
    quote_lookup: Mapping[str, Any],
) -> BackfillResult:
    start_date = expected_dates[0]
    end_date = expected_dates[-1]
    quote = quote_lookup.get(normalize_stock_code(code))
    result = BackfillResult(
        code=code,
        status="pending",
        expected_count=len(expected_dates),
        existing_count=0,
        missing_count=0,
    )
    if quote is None:
        result.status = "skipped"
        result.errors.append("quote_missing")
        return result

    bars = db.get_data_range(code, start_date, end_date)
    if not bars:
        result.status = "no_data"
        return result

    target_dates = set(expected_dates)
    df = pd.DataFrame([bar.to_dict() for bar in bars if bar.date in target_dates])
    if df.empty:
        result.status = "no_data"
        return result

    result.existing_count = len(df)
    result.fetched_rows = len(df)
    enriched = enrich_daily_history_with_quote_fields(df, code, quote=quote)
    enriched_by_date = {
        pd.to_datetime(row.get("date"), errors="coerce").date(): row
        for row in enriched.to_dict(orient="records")
        if pd.notna(pd.to_datetime(row.get("date"), errors="coerce"))
    }

    def _num(value: Any) -> float | None:
        if value is None:
            return None
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if pd.notna(parsed) else None

    def _write(session) -> int:  # type: ignore[no-untyped-def]
        rows = session.execute(
            select(StockDaily).where(
                and_(
                    StockDaily.code == code,
                    StockDaily.date >= start_date,
                    StockDaily.date <= end_date,
                )
            )
        ).scalars().all()
        updated_count = 0
        for row in rows:
            payload = enriched_by_date.get(row.date)
            if not payload:
                continue
            changed = False
            for field_name in OPTIONAL_DAILY_METRIC_COLUMNS:
                value = _num(payload.get(field_name))
                if value is None:
                    continue
                if getattr(row, field_name) != value:
                    setattr(row, field_name, value)
                    changed = True
            if changed:
                updated_count += 1
        return updated_count

    result.saved_count = db._run_write_transaction(
        f"refresh_stock_daily_valuation[{code}]",
        _write,
    )
    result.status = "fetched" if result.saved_count > 0 else "skipped"
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
    parser.add_argument(
        "--skip-chip",
        action="store_true",
        help="Only backfill stock_daily; do not compute stock_chip_daily snapshots",
    )
    parser.add_argument(
        "--skip-daily",
        action="store_true",
        help="Only compute stock_chip_daily snapshots; do not scan or backfill stock_daily gaps",
    )
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="Fetch and upsert every trading day in range, including dates already present in stock_daily",
    )
    parser.add_argument(
        "--skip-valuation",
        action="store_true",
        help="Do not enrich stock_daily rows with quote-derived valuation/share metrics",
    )
    parser.add_argument(
        "--valuation-only",
        action="store_true",
        help="Only refresh valuation/share columns on existing stock_daily rows from a batch quote snapshot",
    )
    parser.add_argument(
        "--stock-timeout-seconds",
        type=int,
        default=60,
        help="Single-stock wall-clock timeout in serial mode; set 0 to disable, default 60",
    )
    parser.add_argument("--log-level", default="INFO", help="Python logging level, default INFO")
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run_with_stock_timeout(fn: Callable[[], T], timeout_seconds: int) -> T:
    """Run one stock task with a SIGALRM timeout in single-thread mode."""
    if timeout_seconds <= 0 or not hasattr(signal, "SIGALRM"):
        return fn()

    previous_handler = signal.getsignal(signal.SIGALRM)

    def _handle_timeout(signum, frame):  # type: ignore[no-untyped-def]
        raise StockTimeoutError(f"stock task exceeded {timeout_seconds}s")

    signal.signal(signal.SIGALRM, _handle_timeout)
    signal.setitimer(signal.ITIMER_REAL, float(timeout_seconds))
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def print_result(index: int, total: int, result: BackfillResult) -> None:
    print(
        f"[{index}/{total}] {result.code} {result.status} "
        f"expected={result.expected_count} existing={result.existing_count} "
        f"missing={result.missing_count} segments={result.requested_segments} "
        f"fetched={result.fetched_rows} saved={result.saved_count} "
        f"sources={result.source_summary()} "
        f"chip_missing={result.chip_missing_count} chip_fetched={result.chip_fetched_rows} "
        f"chip_saved={result.chip_saved_count} chip_sources={result.chip_source_summary()} "
        f"errors={len(result.errors)}",
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
    if args.valuation_only and args.skip_valuation:
        parser.error("--valuation-only cannot be used with --skip-valuation")
    if not args.valuation_only and args.skip_daily and args.skip_chip:
        parser.error("--skip-daily and --skip-chip cannot both be set")
    if args.stock_timeout_seconds < 0:
        parser.error("--stock-timeout-seconds must be non-negative")

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
        f"trading_days={len(expected_dates)} parallelism={args.parallelism} "
        f"fetcher={args.fetcher} daily={'off' if args.skip_daily else 'on'} "
        f"chip={'off' if args.skip_chip else 'on'} "
        f"refresh_existing={args.refresh_existing} "
        f"valuation={'only' if args.valuation_only else 'off' if args.skip_valuation else 'on'}",
        flush=True,
    )

    results: list[BackfillResult] = []
    fetcher_factory = build_fetcher_factory(args.fetcher)
    quote_lookup: dict[str, Any] | None = None
    if not args.skip_daily and not args.skip_valuation:
        print("批量预取估值 quote 快照...", flush=True)
        quote_lookup = prefetch_valuation_quotes(codes)
        print(f"估值 quote 快照: {len(quote_lookup)}/{total}", flush=True)

    if args.valuation_only:
        if quote_lookup is None:
            print("批量预取估值 quote 快照...", flush=True)
            quote_lookup = prefetch_valuation_quotes(codes)
            print(f"估值 quote 快照: {len(quote_lookup)}/{total}", flush=True)
        for index, code in enumerate(codes, start=1):
            try:
                result = refresh_stock_daily_valuation_from_db(
                    code,
                    expected_dates,
                    db,
                    quote_lookup,
                )
            except Exception as exc:
                result = BackfillResult(
                    code=code,
                    status="failed",
                    expected_count=len(expected_dates),
                    existing_count=0,
                    missing_count=0,
                    errors=[str(exc)],
                )
            results.append(result)
            print_result(index, total, result)
    elif args.parallelism == 1:
        for index, code in enumerate(codes, start=1):
            try:
                result = run_with_stock_timeout(
                    lambda code=code: backfill_one_stock(
                        code,
                        expected_dates,
                        db,
                        fetcher_factory,
                        not args.skip_daily,
                        not args.skip_chip,
                        args.refresh_existing,
                        not args.skip_valuation,
                        quote_lookup,
                    ),
                    args.stock_timeout_seconds,
                )
            except Exception as exc:
                result = BackfillResult(
                    code=code,
                    status="failed",
                    expected_count=len(expected_dates),
                    existing_count=0,
                    missing_count=len(expected_dates),
                    chip_expected_count=0 if args.skip_chip else len(expected_dates),
                    errors=[str(exc)],
                )
            results.append(result)
            print_result(index, total, result)
    else:
        if args.stock_timeout_seconds:
            logger.warning("--stock-timeout-seconds 仅在 --parallelism 1 串行模式生效")
        with ThreadPoolExecutor(max_workers=args.parallelism) as executor:
            futures = {
                executor.submit(
                    backfill_one_stock,
                    code,
                    expected_dates,
                    db,
                    fetcher_factory,
                    not args.skip_daily,
                    not args.skip_chip,
                    args.refresh_existing,
                    not args.skip_valuation,
                    quote_lookup,
                ): code
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
                        chip_expected_count=0 if args.skip_chip else len(expected_dates),
                        errors=[str(exc)],
                    )
                results.append(result)
                print_result(index, total, result)

    status_counts = Counter(result.status for result in results)
    total_saved = sum(result.saved_count for result in results)
    total_fetched = sum(result.fetched_rows for result in results)
    total_chip_saved = sum(result.chip_saved_count for result in results)
    total_chip_fetched = sum(result.chip_fetched_rows for result in results)
    total_errors = sum(len(result.errors) for result in results)

    print("")
    print("补齐完成")
    print(f"  stocks        : {total}")
    print(f"  status        : {dict(status_counts)}")
    print(f"  fetched_rows  : {total_fetched}")
    print(f"  saved_new_rows: {total_saved}")
    print(f"  chip_fetched  : {total_chip_fetched}")
    print(f"  chip_saved    : {total_chip_saved}")
    print(f"  errors        : {total_errors}")

    return 1 if status_counts.get("failed") or status_counts.get("partial_failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
