# -*- coding: utf-8 -*-
"""Helpers for keeping ``stock_chip_daily`` in step with daily K-line sync."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Optional, Sequence

import pandas as pd

from data_provider.base import DataFetcherManager, normalize_stock_code
from data_provider.local_chip_model_fetcher import compute_chip_distribution_from_history
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)


def _coerce_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if isinstance(value, str) and len(value) >= 10:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _frame_dates(history_df: pd.DataFrame) -> set[date]:
    if history_df is None or history_df.empty or "date" not in history_df.columns:
        return set()
    parsed = pd.to_datetime(history_df["date"], errors="coerce").dropna().dt.date
    return {item for item in parsed if isinstance(item, date)}


def _normalize_target_dates(
    target_dates: Optional[Iterable[date]],
    history_df: Optional[pd.DataFrame] = None,
) -> list[date]:
    dates: set[date] = set()
    if target_dates is not None:
        for item in target_dates:
            parsed = _coerce_date(item)
            if parsed is not None:
                dates.add(parsed)
    elif history_df is not None:
        dates = _frame_dates(history_df)
    return sorted(dates)


def _existing_chip_dates(db: DatabaseManager, code: str, dates: Sequence[date]) -> set[date]:
    if not dates:
        return set()
    rows = db.get_chip_daily_range(code, dates[0], dates[-1])
    existing: set[date] = set()
    wanted = set(dates)
    for row in rows:
        parsed = _coerce_date(row.get("date"))
        if parsed in wanted:
            existing.add(parsed)
    return existing


def _snapshot_date(snapshot: dict[str, Any]) -> Optional[date]:
    return _coerce_date(snapshot.get("date"))


def sync_chip_daily_from_history(
    db: DatabaseManager,
    code: str,
    history_df: pd.DataFrame,
    *,
    data_source: Optional[str] = None,
    target_dates: Optional[Iterable[date]] = None,
    skip_existing: bool = True,
) -> int:
    """
    Compute chip snapshots from an already-fetched daily history frame and upsert them.

    This path performs no network IO. It is intended to be called right after the
    same frame has been written to ``stock_daily`` so the chip cache follows the
    daily cache without changing the daily provider chain.
    """
    if history_df is None or history_df.empty:
        return 0

    normalized_code = normalize_stock_code(str(code or "").strip())
    if not normalized_code:
        return 0

    dates = _normalize_target_dates(target_dates, history_df)
    if not dates:
        return 0

    missing_dates = set(dates)
    if skip_existing:
        missing_dates -= _existing_chip_dates(db, normalized_code, dates)
        if not missing_dates:
            return 0

    chip = compute_chip_distribution_from_history(
        normalized_code,
        history_df,
        history_source=data_source or "daily_history",
        window_days=max(len(history_df.index), 2),
        include_snapshots=True,
        snapshot_limit=None,
    )
    if chip is None:
        logger.debug("[%s] 筹码峰同步跳过：日线缺少可计算的换手率或样本不足", normalized_code)
        return 0

    snapshots = [
        snapshot
        for snapshot in (chip.snapshots or [])
        if isinstance(snapshot, dict) and _snapshot_date(snapshot) in missing_dates
    ]
    if not snapshots:
        return 0

    return db.save_chip_daily_snapshots(
        normalized_code,
        snapshots,
        data_source=chip.source,
    )


def ensure_chip_daily_for_dates(
    db: DatabaseManager,
    fetcher_manager: DataFetcherManager,
    code: str,
    target_dates: Iterable[date],
    *,
    history_df: Optional[pd.DataFrame] = None,
    history_source: Optional[str] = None,
    warmup_days: int = 365,
) -> int:
    """
    Ensure ``stock_chip_daily`` has snapshots for target dates.

    The function mirrors the ``stock_daily`` sync contract:
    - check DB first;
    - if missing and a daily frame is already available, compute from it and upsert;
    - if still missing, fetch the required warmup window through DataFetcherManager
      and upsert the computed snapshots.
    """
    normalized_code = normalize_stock_code(str(code or "").strip())
    dates = _normalize_target_dates(target_dates)
    if not normalized_code or not dates:
        return 0

    missing_dates = set(dates) - _existing_chip_dates(db, normalized_code, dates)
    if not missing_dates:
        return 0

    saved_total = 0
    if history_df is not None and not history_df.empty:
        saved_total += sync_chip_daily_from_history(
            db,
            normalized_code,
            history_df,
            data_source=history_source,
            target_dates=missing_dates,
        )
        missing_dates -= _existing_chip_dates(db, normalized_code, dates)
        if not missing_dates:
            return saved_total

    start_date = min(missing_dates) - timedelta(days=max(0, int(warmup_days)))
    end_date = max(missing_dates)
    history, source = fetcher_manager.get_daily_data(
        normalized_code,
        start_date=start_date.isoformat(),
        end_date=end_date.isoformat(),
        days=(end_date - start_date).days + 1,
    )
    saved_total += sync_chip_daily_from_history(
        db,
        normalized_code,
        history,
        data_source=source,
        target_dates=missing_dates,
    )
    return saved_total
