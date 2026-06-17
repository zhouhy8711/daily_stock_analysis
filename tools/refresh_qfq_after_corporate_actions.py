#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Refresh cached A-share qfq daily history after corporate actions.

The local ``stock_daily`` table stores adjusted A-share history.  After a
dividend, bonus share, transfer share, or similar ex-right event, qfq prices
before the event are recalculated by upstream providers.  A DB row that was
correct yesterday can therefore become stale without a missing-date signal.

This tool finds recent corporate-action events, verifies that DB prices differ
from the current qfq source by a stable ratio, and then optionally refreshes the
full cached history for the affected stock from the earliest local date.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import shutil
import sqlite3
import sys
from typing import Any, Callable, Iterable, Mapping, Sequence

import pandas as pd
from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from data_provider.akshare_fetcher import AkshareFetcher  # noqa: E402
from data_provider.base import normalize_stock_code  # noqa: E402
from src.services.chip_daily_sync import sync_chip_daily_from_history  # noqa: E402
from src.storage import DatabaseManager, StockDaily  # noqa: E402
from tools.backfill_a_share_daily_history import (  # noqa: E402
    refresh_stock_daily_derived_metrics_from_db,
)


HistoryFetcher = Callable[[str, date, date], tuple[pd.DataFrame, str]]

CODE_COLUMNS = (
    "股票代码",
    "证券代码",
    "代码",
    "code",
    "symbol",
)
EVENT_DATE_COLUMNS = (
    "除权除息日",
    "除权除息日期",
    "除息日",
    "除权日",
    "红股上市日",
    "实施公告日",
    "公告日期",
    "股权登记日",
)
SUMMARY_COLUMNS = (
    "分红方案",
    "方案",
    "送转股份-送股比例",
    "送转股份-转增比例",
    "派息比例",
    "派息",
    "送股",
    "转增",
    "进度",
)
QFQ_REFRESH_SINA_SOURCE = "AkshareSinaCorporateActionRefresh"
QFQ_REFRESH_TENCENT_SOURCE = "AkshareTencentCorporateActionRefresh"


@dataclass(frozen=True)
class CorporateActionEvent:
    code: str
    event_date: date
    summary: str
    source: str
    event_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "event_date": self.event_date.isoformat(),
            "summary": self.summary,
            "source": self.source,
            "event_hash": self.event_hash,
        }


@dataclass
class RefreshCandidate:
    event: CorporateActionEvent
    status: str
    reason: str
    earliest_date: date | None = None
    latest_date: date | None = None
    sample_start: date | None = None
    sample_end: date | None = None
    overlap_count: int = 0
    median_close_ratio: float | None = None
    median_close_ratio_diff: float | None = None
    max_close_abs_diff: float | None = None
    close_ratio_std: float | None = None
    close_ratio_consistency: float | None = None
    amount_match_ratio: float | None = None
    volume_match_ratio: float | None = None
    fresh_source: str | None = None
    error: str | None = None

    @property
    def code(self) -> str:
        return self.event.code

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.event.to_dict(),
            "status": self.status,
            "reason": self.reason,
            "earliest_date": _date_to_json(self.earliest_date),
            "latest_date": _date_to_json(self.latest_date),
            "sample_start": _date_to_json(self.sample_start),
            "sample_end": _date_to_json(self.sample_end),
            "overlap_count": self.overlap_count,
            "median_close_ratio": _round_or_none(self.median_close_ratio, 6),
            "median_close_ratio_diff": _round_or_none(self.median_close_ratio_diff, 6),
            "max_close_abs_diff": _round_or_none(self.max_close_abs_diff, 6),
            "close_ratio_std": _round_or_none(self.close_ratio_std, 6),
            "close_ratio_consistency": _round_or_none(self.close_ratio_consistency, 6),
            "amount_match_ratio": _round_or_none(self.amount_match_ratio, 6),
            "volume_match_ratio": _round_or_none(self.volume_match_ratio, 6),
            "fresh_source": self.fresh_source,
            "error": self.error,
        }


@dataclass
class RefreshApplyResult:
    code: str
    status: str
    earliest_date: date | None
    end_date: date
    event_hashes: list[str] = field(default_factory=list)
    fetched_rows: int = 0
    refreshed_rows: int = 0
    new_rows: int = 0
    derived_saved_rows: int = 0
    chip_saved_rows: int = 0
    source: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "status": self.status,
            "earliest_date": _date_to_json(self.earliest_date),
            "end_date": self.end_date.isoformat(),
            "event_hashes": self.event_hashes,
            "fetched_rows": self.fetched_rows,
            "refreshed_rows": self.refreshed_rows,
            "new_rows": self.new_rows,
            "derived_saved_rows": self.derived_saved_rows,
            "chip_saved_rows": self.chip_saved_rows,
            "source": self.source,
            "error": self.error,
        }


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _parse_report_date(value: Any) -> date | None:
    if value in (None, ""):
        return None
    parsed = _coerce_date(value)
    return parsed


def _date_to_json(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _round_or_none(value: float | None, digits: int) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(value, digits)


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return str(value)


def _coerce_date(value: Any) -> date | None:
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, pd.Timestamp):
        return value.date()
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"nan", "none", "nat", "--"}:
        return None
    parsed = pd.to_datetime(text[:10], errors="coerce")
    if pd.isna(parsed):
        parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return parsed.date()


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(parsed):
        return None
    return parsed


def _first_existing_column(columns: Iterable[str], candidates: Sequence[str]) -> str | None:
    column_set = {str(column): column for column in columns}
    for candidate in candidates:
        if candidate in column_set:
            return column_set[candidate]
    for column in column_set:
        for candidate in candidates:
            if candidate and candidate in column:
                return column_set[column]
    return None


def _normalize_code(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return normalize_stock_code(text).strip().upper()


def _event_summary(row: Mapping[str, Any], columns: Sequence[str]) -> str:
    parts: list[str] = []
    for column in SUMMARY_COLUMNS:
        if column not in columns:
            continue
        value = row.get(column)
        if value is None:
            continue
        text = str(value).strip()
        if text and text.lower() not in {"nan", "none", "--"}:
            parts.append(f"{column}={text}")
    if parts:
        return "; ".join(parts)
    return json.dumps({str(k): _json_default(v) for k, v in row.items()}, ensure_ascii=False, sort_keys=True)


def _event_hash(code: str, event_date: date, summary: str, source: str) -> str:
    payload = f"{code}|{event_date.isoformat()}|{summary}|{source}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def parse_corporate_action_frame(
    frame: pd.DataFrame,
    *,
    source: str,
    start_date: date,
    end_date: date,
    codes: set[str] | None = None,
    default_code: str | None = None,
) -> list[CorporateActionEvent]:
    if frame is None or frame.empty:
        return []

    code_column = _first_existing_column(frame.columns, CODE_COLUMNS)
    date_column = _first_existing_column(frame.columns, EVENT_DATE_COLUMNS)
    fallback_code = _normalize_code(default_code)
    if (not code_column and not fallback_code) or not date_column:
        return []

    parsed_events: list[CorporateActionEvent] = []
    columns = [str(column) for column in frame.columns]
    for row in frame.to_dict(orient="records"):
        code = _normalize_code(row.get(code_column)) if code_column else fallback_code
        if not code:
            continue
        if codes is not None and code not in codes:
            continue
        event_day = _coerce_date(row.get(date_column))
        if event_day is None or event_day < start_date or event_day > end_date:
            continue
        summary = _event_summary(row, columns)
        event_source = f"{source}:{date_column}"
        parsed_events.append(
            CorporateActionEvent(
                code=code,
                event_date=event_day,
                summary=summary,
                source=event_source,
                event_hash=_event_hash(code, event_day, summary, event_source),
            )
        )
    return parsed_events


def fetch_corporate_action_events(
    start_date: date,
    end_date: date,
    *,
    codes: Sequence[str] | None = None,
    include_code_detail: bool = True,
) -> tuple[list[CorporateActionEvent], list[str]]:
    """Fetch recent corporate-action events from public AkShare endpoints."""
    import akshare as ak

    normalized_codes = {_normalize_code(code) for code in codes or []}
    normalized_codes.discard("")
    code_filter = normalized_codes or None
    events: list[CorporateActionEvent] = []
    errors: list[str] = []

    def _append_frame(
        fetch_name: str,
        loader: Callable[[], pd.DataFrame],
        *,
        default_code: str | None = None,
    ) -> None:
        try:
            frame = loader()
            events.extend(
                parse_corporate_action_frame(
                    frame,
                    source=fetch_name,
                    start_date=start_date,
                    end_date=end_date,
                    codes=code_filter,
                    default_code=default_code,
                )
            )
        except Exception as exc:  # pragma: no cover - network/provider dependent
            errors.append(f"{fetch_name}: {exc}")

    _append_frame("ak.stock_history_dividend", ak.stock_history_dividend)

    # Annual profit-distribution tables are keyed by report year.  A 2025
    # annual plan can have a 2026 ex-right date, so include the previous year.
    for year in range(start_date.year - 1, end_date.year + 1):
        _append_frame(
            f"ak.stock_fhps_em[{year}]",
            lambda year=year: ak.stock_fhps_em(date=f"{year}1231"),
        )

    if include_code_detail and normalized_codes:
        for code in sorted(normalized_codes):
            _append_frame(
                f"ak.stock_fhps_detail_em[{code}]",
                lambda code=code: ak.stock_fhps_detail_em(symbol=code),
                default_code=code,
            )
            try:
                frame = ak.stock_history_dividend_detail(symbol=code, indicator="分红")
                events.extend(
                    parse_corporate_action_frame(
                        frame,
                        source=f"ak.stock_history_dividend_detail[{code}]",
                        start_date=start_date,
                        end_date=end_date,
                        codes={code},
                        default_code=code,
                    )
                )
            except Exception as exc:  # pragma: no cover - network/provider dependent
                errors.append(f"ak.stock_history_dividend_detail[{code}]: {exc}")

    deduped: dict[tuple[str, date, str], CorporateActionEvent] = {}
    for event in events:
        deduped.setdefault((event.code, event.event_date, event.summary), event)
    return sorted(deduped.values(), key=lambda item: (item.code, item.event_date, item.event_hash)), errors


def normalize_akshare_history(fetcher: AkshareFetcher, code: str, raw: pd.DataFrame) -> pd.DataFrame:
    if raw is None or raw.empty:
        return pd.DataFrame()
    frame = fetcher._normalize_data(raw, code)
    frame = fetcher._clean_data(frame)
    return fetcher._calculate_indicators(frame)


def fetch_qfq_history(code: str, start_date: date, end_date: date) -> tuple[pd.DataFrame, str]:
    """Fetch current qfq daily history through the stable Sina/Tencent paths."""
    fetcher = AkshareFetcher(sleep_min=0.0, sleep_max=0.0)
    errors: list[str] = []
    for source, method in (
        (QFQ_REFRESH_SINA_SOURCE, fetcher._fetch_stock_data_sina),
        (QFQ_REFRESH_TENCENT_SOURCE, fetcher._fetch_stock_data_tx),
    ):
        try:
            raw = method(code, start_date.isoformat(), end_date.isoformat())
            frame = normalize_akshare_history(fetcher, code, raw)
            if not frame.empty:
                return frame, source
        except Exception as exc:  # pragma: no cover - provider dependent
            errors.append(f"{source}: {exc}")
    return pd.DataFrame(), "; ".join(errors) if errors else "qfq_source_empty"


def load_stock_daily_bounds(db: DatabaseManager, code: str) -> tuple[date | None, date | None, int]:
    normalized_code = _normalize_code(code)
    if not normalized_code:
        return None, None, 0
    with db.get_session() as session:
        row = session.execute(
            select(
                func.min(StockDaily.date),
                func.max(StockDaily.date),
                func.count(StockDaily.id),
            ).where(StockDaily.code == normalized_code)
        ).one()
    return row[0], row[1], int(row[2] or 0)


def _history_frame_from_db_rows(rows: Sequence[StockDaily]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame([row.to_dict() for row in rows])


def _date_indexed_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    normalized = frame.copy()
    normalized["date"] = pd.to_datetime(normalized["date"], errors="coerce").dt.date
    normalized = normalized.dropna(subset=["date"])
    if normalized.empty:
        return normalized
    normalized = normalized.drop_duplicates(subset=["date"], keep="last")
    return normalized.set_index("date").sort_index()


def _match_ratio(db_values: pd.Series, fresh_values: pd.Series, *, tolerance: float) -> float | None:
    matches = 0
    compared = 0
    for left, right in zip(db_values, fresh_values):
        db_value = _to_float(left)
        fresh_value = _to_float(right)
        if db_value is None or fresh_value is None or fresh_value == 0:
            continue
        compared += 1
        if abs(db_value - fresh_value) / abs(fresh_value) <= tolerance:
            matches += 1
    if compared == 0:
        return None
    return matches / compared


def detect_refresh_candidate(
    db: DatabaseManager,
    event: CorporateActionEvent,
    *,
    history_fetcher: HistoryFetcher = fetch_qfq_history,
    lookback_days: int = 180,
    min_overlap: int = 5,
    min_ratio_diff: float = 0.02,
    max_ratio_std: float = 0.035,
    min_ratio_consistency: float = 0.8,
) -> RefreshCandidate:
    earliest_date, latest_date, row_count = load_stock_daily_bounds(db, event.code)
    candidate = RefreshCandidate(
        event=event,
        status="skipped",
        reason="not_checked",
        earliest_date=earliest_date,
        latest_date=latest_date,
    )
    if row_count == 0 or earliest_date is None or latest_date is None:
        candidate.reason = "no_local_daily_history"
        return candidate

    sample_start = max(earliest_date, event.event_date - timedelta(days=max(1, lookback_days)))
    sample_end = min(latest_date, event.event_date - timedelta(days=1))
    candidate.sample_start = sample_start
    candidate.sample_end = sample_end
    if sample_start > sample_end:
        candidate.reason = "no_pre_event_local_window"
        return candidate

    db_rows = db.get_data_range(event.code, sample_start, sample_end)
    db_frame = _date_indexed_frame(_history_frame_from_db_rows(db_rows))
    if len(db_frame.index) < min_overlap:
        candidate.reason = "insufficient_local_overlap"
        candidate.overlap_count = len(db_frame.index)
        return candidate

    try:
        fresh_frame, source = history_fetcher(event.code, sample_start, sample_end)
        candidate.fresh_source = source
    except Exception as exc:
        candidate.status = "failed"
        candidate.reason = "fresh_fetch_failed"
        candidate.error = str(exc)
        return candidate

    fresh_indexed = _date_indexed_frame(fresh_frame)
    if fresh_indexed.empty:
        candidate.reason = "fresh_history_empty"
        return candidate

    overlap_dates = sorted(set(db_frame.index).intersection(fresh_indexed.index))
    candidate.overlap_count = len(overlap_dates)
    if len(overlap_dates) < min_overlap:
        candidate.reason = "insufficient_fresh_overlap"
        return candidate

    db_overlap = db_frame.loc[overlap_dates]
    fresh_overlap = fresh_indexed.loc[overlap_dates]
    ratios: list[float] = []
    close_abs_diffs: list[float] = []
    for db_close, fresh_close in zip(db_overlap.get("close", []), fresh_overlap.get("close", [])):
        db_value = _to_float(db_close)
        fresh_value = _to_float(fresh_close)
        if db_value is None or fresh_value is None or db_value <= 0 or fresh_value <= 0:
            continue
        ratios.append(db_value / fresh_value)
        close_abs_diffs.append(abs(db_value - fresh_value) / fresh_value)

    if len(ratios) < min_overlap:
        candidate.reason = "insufficient_valid_close_overlap"
        return candidate

    ratio_series = pd.Series(ratios, dtype="float64")
    median_ratio = float(ratio_series.median())
    ratio_diff = abs(median_ratio - 1.0)
    ratio_std = float(ratio_series.std(ddof=0)) if len(ratio_series.index) > 1 else 0.0
    ratio_consistency = float((ratio_series.sub(median_ratio).abs() <= max_ratio_std).mean())
    candidate.median_close_ratio = median_ratio
    candidate.median_close_ratio_diff = ratio_diff
    candidate.max_close_abs_diff = max(close_abs_diffs) if close_abs_diffs else None
    candidate.close_ratio_std = ratio_std
    candidate.close_ratio_consistency = ratio_consistency
    if "amount" in db_overlap.columns and "amount" in fresh_overlap.columns:
        candidate.amount_match_ratio = _match_ratio(
            db_overlap["amount"],
            fresh_overlap["amount"],
            tolerance=0.05,
        )
    if "volume" in db_overlap.columns and "volume" in fresh_overlap.columns:
        candidate.volume_match_ratio = _match_ratio(
            db_overlap["volume"],
            fresh_overlap["volume"],
            tolerance=0.05,
        )

    if ratio_diff < min_ratio_diff:
        candidate.reason = "qfq_prices_already_current"
        return candidate
    if ratio_std > max_ratio_std and ratio_consistency < min_ratio_consistency:
        candidate.reason = "unstable_price_ratio"
        return candidate

    candidate.status = "triggered"
    candidate.reason = "stable_qfq_ratio_mismatch_after_corporate_action"
    return candidate


def _load_history_from_db(db: DatabaseManager, code: str, start_date: date, end_date: date) -> pd.DataFrame:
    with db.get_session() as session:
        rows = session.execute(
            StockDaily.__table__.select()
            .where(
                (StockDaily.code == _normalize_code(code))
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


def _frame_trade_dates(frame: pd.DataFrame, start_date: date, end_date: date) -> list[date]:
    if frame is None or frame.empty or "date" not in frame.columns:
        return []
    parsed = pd.to_datetime(frame["date"], errors="coerce").dropna().dt.date
    dates = sorted({item for item in parsed if start_date <= item <= end_date})
    return dates


def _filter_frame_to_range(frame: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    if frame is None or frame.empty or "date" not in frame.columns:
        return pd.DataFrame()
    filtered = frame.copy()
    parsed = pd.to_datetime(filtered["date"], errors="coerce").dt.date
    filtered = filtered.loc[(parsed >= start_date) & (parsed <= end_date)].copy()
    if filtered.empty:
        return filtered
    filtered["date"] = pd.to_datetime(filtered["date"], errors="coerce")
    return filtered


def apply_refresh_for_code(
    db: DatabaseManager,
    candidate: RefreshCandidate,
    *,
    end_date: date,
    history_fetcher: HistoryFetcher = fetch_qfq_history,
    skip_chip: bool = False,
) -> RefreshApplyResult:
    result = RefreshApplyResult(
        code=candidate.code,
        status="pending",
        earliest_date=candidate.earliest_date,
        end_date=end_date,
        event_hashes=[candidate.event.event_hash],
    )
    if candidate.earliest_date is None:
        result.status = "skipped"
        result.error = "no_earliest_date"
        return result

    try:
        full_frame, source = history_fetcher(candidate.code, candidate.earliest_date, end_date)
        result.source = source
        full_frame = _filter_frame_to_range(full_frame, candidate.earliest_date, end_date)
        if full_frame.empty:
            result.status = "no_data"
            result.error = source
            return result

        target_dates = _frame_trade_dates(full_frame, candidate.earliest_date, end_date)
        result.fetched_rows = len(full_frame.index)
        result.refreshed_rows = len(target_dates)
        result.new_rows = db.save_daily_data(full_frame, candidate.code, data_source=source)

        derived = refresh_stock_daily_derived_metrics_from_db(candidate.code, target_dates, db)
        result.derived_saved_rows = derived.derived_saved_count
        if derived.errors:
            result.error = "; ".join(derived.errors)

        if not skip_chip and target_dates:
            history = _load_history_from_db(db, candidate.code, candidate.earliest_date, end_date)
            if not history.empty:
                result.chip_saved_rows = sync_chip_daily_from_history(
                    db,
                    candidate.code,
                    history,
                    data_source="stock_daily:qfq_corporate_action_refresh",
                    target_dates=target_dates,
                    skip_existing=False,
                )

        result.status = "refreshed"
        return result
    except Exception as exc:
        result.status = "failed"
        result.error = str(exc)
        return result


def backup_sqlite_database(db: DatabaseManager, *, report_dir: Path) -> Path | None:
    engine = getattr(db, "_engine", None)
    if engine is None or engine.url.get_backend_name() != "sqlite":
        return None
    database = engine.url.database
    if not database or database == ":memory:":
        return None
    db_path = Path(database)
    if not db_path.exists():
        return None
    report_dir.mkdir(parents=True, exist_ok=True)
    backup_path = report_dir / f"{db_path.name}.bak.{datetime.now().strftime('%Y%m%d%H%M%S')}.qfq"
    try:
        source = sqlite3.connect(str(db_path))
        target = sqlite3.connect(str(backup_path))
        with target:
            source.backup(target)
        source.close()
        target.close()
    except Exception:
        if backup_path.exists():
            backup_path.unlink()
        shutil.copy2(db_path, backup_path)
    return backup_path


def group_triggered_candidates(candidates: Sequence[RefreshCandidate]) -> list[RefreshCandidate]:
    selected: dict[str, RefreshCandidate] = {}
    for candidate in candidates:
        if candidate.status != "triggered":
            continue
        current = selected.get(candidate.code)
        if current is None:
            selected[candidate.code] = candidate
            continue
        current_diff = current.median_close_ratio_diff or 0.0
        next_diff = candidate.median_close_ratio_diff or 0.0
        if next_diff > current_diff or candidate.event.event_date > current.event.event_date:
            selected[candidate.code] = candidate
    return sorted(selected.values(), key=lambda item: item.code)


def select_latest_event_per_code(events: Sequence[CorporateActionEvent]) -> list[CorporateActionEvent]:
    selected: dict[str, CorporateActionEvent] = {}
    for event in events:
        current = selected.get(event.code)
        if current is None or event.event_date > current.event_date:
            selected[event.code] = event
    return sorted(selected.values(), key=lambda item: (item.code, item.event_date, item.event_hash))


def write_report(report: Mapping[str, Any], report_dir: Path) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / f"qfq_corporate_action_refresh_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return report_path


def candidate_from_report_item(item: Mapping[str, Any]) -> RefreshCandidate:
    event_date = _parse_report_date(item.get("event_date"))
    if event_date is None:
        raise ValueError(f"candidate missing event_date: {item.get('code')}")
    event = CorporateActionEvent(
        code=_normalize_code(item.get("code")),
        event_date=event_date,
        summary=str(item.get("summary") or ""),
        source=str(item.get("source") or "report"),
        event_hash=str(item.get("event_hash") or ""),
    )
    return RefreshCandidate(
        event=event,
        status=str(item.get("status") or ""),
        reason=str(item.get("reason") or ""),
        earliest_date=_parse_report_date(item.get("earliest_date")),
        latest_date=_parse_report_date(item.get("latest_date")),
        sample_start=_parse_report_date(item.get("sample_start")),
        sample_end=_parse_report_date(item.get("sample_end")),
        overlap_count=int(item.get("overlap_count") or 0),
        median_close_ratio=_to_float(item.get("median_close_ratio")),
        median_close_ratio_diff=_to_float(item.get("median_close_ratio_diff")),
        max_close_abs_diff=_to_float(item.get("max_close_abs_diff")),
        close_ratio_std=_to_float(item.get("close_ratio_std")),
        close_ratio_consistency=_to_float(item.get("close_ratio_consistency")),
        amount_match_ratio=_to_float(item.get("amount_match_ratio")),
        volume_match_ratio=_to_float(item.get("volume_match_ratio")),
        fresh_source=str(item.get("fresh_source") or "") or None,
        error=str(item.get("error") or "") or None,
    )


def load_triggered_candidates_from_report(report_path: Path) -> tuple[list[RefreshCandidate], dict[str, Any]]:
    report = json.loads(report_path.read_text(encoding="utf-8"))
    candidates = [
        candidate_from_report_item(item)
        for item in report.get("candidates", [])
        if item.get("status") == "triggered"
    ]
    return group_triggered_candidates(candidates), report


def run_apply_from_report(
    db: DatabaseManager,
    *,
    dry_run_report_path: Path,
    history_fetcher: HistoryFetcher = fetch_qfq_history,
    skip_chip: bool = False,
    report_dir: Path = REPO_ROOT / "outputs" / "qfq_corporate_action_refresh",
    backup: bool = True,
    max_codes: int | None = None,
) -> tuple[dict[str, Any], Path]:
    candidates, dry_run_report = load_triggered_candidates_from_report(dry_run_report_path)
    if max_codes is not None:
        candidates = candidates[:max_codes]
    end_date = _parse_report_date(dry_run_report.get("end_date"))
    if end_date is None:
        raise ValueError(f"dry-run report missing end_date: {dry_run_report_path}")

    backup_path: Path | None = None
    if candidates and backup:
        backup_path = backup_sqlite_database(db, report_dir=report_dir)
        if backup_path:
            print(f"已备份数据库: {backup_path}", flush=True)

    apply_results: list[RefreshApplyResult] = []
    for index, candidate in enumerate(candidates, start=1):
        result = apply_refresh_for_code(
            db,
            candidate,
            end_date=end_date,
            history_fetcher=history_fetcher,
            skip_chip=skip_chip,
        )
        apply_results.append(result)
        print(
            f"[apply {index}/{len(candidates)}] {result.code} {result.status} "
            f"fetched={result.fetched_rows} refreshed={result.refreshed_rows} "
            f"new={result.new_rows} derived={result.derived_saved_rows} "
            f"chip={result.chip_saved_rows} error={result.error or '-'}",
            flush=True,
        )

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply-from-report",
        "dry_run_report_path": str(dry_run_report_path),
        "dry_run_generated_at": dry_run_report.get("generated_at"),
        "event_start_date": dry_run_report.get("event_start_date"),
        "end_date": end_date.isoformat(),
        "skip_chip": skip_chip,
        "triggered_code_count": len(candidates),
        "triggered_codes": [candidate.code for candidate in candidates],
        "backup_path": str(backup_path) if backup_path else None,
        "apply_results": [result.to_dict() for result in apply_results],
    }
    report_path = write_report(report, report_dir)
    return report, report_path


def run_refresh(
    db: DatabaseManager,
    *,
    event_start_date: date,
    end_date: date,
    codes: Sequence[str] | None = None,
    events: Sequence[CorporateActionEvent] | None = None,
    history_fetcher: HistoryFetcher = fetch_qfq_history,
    apply: bool = False,
    skip_chip: bool = False,
    lookback_days: int = 180,
    min_overlap: int = 5,
    min_ratio_diff: float = 0.02,
    max_ratio_std: float = 0.035,
    min_ratio_consistency: float = 0.8,
    report_dir: Path = REPO_ROOT / "outputs" / "qfq_corporate_action_refresh",
    backup: bool = True,
    max_events: int | None = None,
    max_codes: int | None = None,
    check_all_events: bool = False,
) -> tuple[dict[str, Any], Path]:
    normalized_codes = [_normalize_code(code) for code in codes or []]
    normalized_codes = [code for code in normalized_codes if code]

    event_errors: list[str] = []
    if events is None:
        loaded_events, event_errors = fetch_corporate_action_events(
            event_start_date,
            end_date,
            codes=normalized_codes or None,
        )
    else:
        loaded_events = list(events)

    if normalized_codes:
        allowed = set(normalized_codes)
        loaded_events = [event for event in loaded_events if event.code in allowed]
    if max_events is not None:
        loaded_events = loaded_events[:max_events]
    checked_events = loaded_events if check_all_events else select_latest_event_per_code(loaded_events)

    candidates: list[RefreshCandidate] = []
    for index, event in enumerate(checked_events, start=1):
        candidate = detect_refresh_candidate(
            db,
            event,
            history_fetcher=history_fetcher,
            lookback_days=lookback_days,
            min_overlap=min_overlap,
            min_ratio_diff=min_ratio_diff,
            max_ratio_std=max_ratio_std,
            min_ratio_consistency=min_ratio_consistency,
        )
        candidates.append(candidate)
        if candidate.status == "triggered" or index % 50 == 0:
            print(
                f"[{index}/{len(checked_events)}] {event.code} {event.event_date} "
                f"{candidate.status} reason={candidate.reason} "
                f"ratio={_round_or_none(candidate.median_close_ratio, 4)} "
                f"overlap={candidate.overlap_count}",
                flush=True,
            )

    triggered = group_triggered_candidates(candidates)
    if max_codes is not None:
        triggered = triggered[:max_codes]

    backup_path: Path | None = None
    if apply and triggered and backup:
        backup_path = backup_sqlite_database(db, report_dir=report_dir)
        if backup_path:
            print(f"已备份数据库: {backup_path}", flush=True)

    apply_results: list[RefreshApplyResult] = []
    if apply:
        for index, candidate in enumerate(triggered, start=1):
            result = apply_refresh_for_code(
                db,
                candidate,
                end_date=end_date,
                history_fetcher=history_fetcher,
                skip_chip=skip_chip,
            )
            same_code_hashes = [
                item.event.event_hash
                for item in candidates
                if item.code == candidate.code and item.status == "triggered"
            ]
            result.event_hashes = sorted(set(same_code_hashes))
            apply_results.append(result)
            print(
                f"[apply {index}/{len(triggered)}] {result.code} {result.status} "
                f"fetched={result.fetched_rows} refreshed={result.refreshed_rows} "
                f"new={result.new_rows} derived={result.derived_saved_rows} "
                f"chip={result.chip_saved_rows} error={result.error or '-'}",
                flush=True,
            )

    status_counts = Counter(candidate.status for candidate in candidates)
    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "mode": "apply" if apply else "dry-run",
        "event_start_date": event_start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "codes": normalized_codes,
        "lookback_days": lookback_days,
        "min_overlap": min_overlap,
        "min_ratio_diff": min_ratio_diff,
        "max_ratio_std": max_ratio_std,
        "min_ratio_consistency": min_ratio_consistency,
        "skip_chip": skip_chip,
        "event_count": len(loaded_events),
        "checked_event_count": len(checked_events),
        "check_all_events": check_all_events,
        "event_errors": event_errors,
        "candidate_status_counts": dict(status_counts),
        "triggered_code_count": len(triggered),
        "triggered_codes": [candidate.code for candidate in triggered],
        "backup_path": str(backup_path) if backup_path else None,
        "candidates": [candidate.to_dict() for candidate in candidates],
        "apply_results": [result.to_dict() for result in apply_results],
    }
    report_path = write_report(report, report_dir)
    return report, report_path


def build_parser() -> argparse.ArgumentParser:
    today = date.today()
    parser = argparse.ArgumentParser(
        description="Detect and refresh stale qfq A-share daily history after corporate actions."
    )
    parser.add_argument(
        "--event-start-date",
        type=parse_date,
        default=today - timedelta(days=365),
        help="Corporate-action event scan start date, default today-365d.",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        default=today,
        help="Refresh and event scan end date, default today.",
    )
    parser.add_argument("--codes", help="Optional comma-separated stock codes.")
    parser.add_argument("--apply", action="store_true", help="Write refreshed qfq history into stock_daily.")
    parser.add_argument(
        "--apply-from-report",
        type=Path,
        help="Apply triggered candidates from a previous dry-run JSON report. Requires --apply.",
    )
    parser.add_argument("--skip-chip", action="store_true", help="Do not rebuild stock_chip_daily snapshots.")
    parser.add_argument("--lookback-days", type=int, default=180, help="Pre-event comparison window.")
    parser.add_argument("--min-overlap", type=int, default=5, help="Minimum overlapping pre-event rows.")
    parser.add_argument(
        "--min-ratio-diff",
        type=float,
        default=0.02,
        help="Minimum stable close ratio difference required to refresh.",
    )
    parser.add_argument(
        "--max-ratio-std",
        type=float,
        default=0.035,
        help="Close-ratio cluster tolerance and full-window std reference.",
    )
    parser.add_argument(
        "--min-ratio-consistency",
        type=float,
        default=0.8,
        help="Minimum share of rows that must belong to the median close-ratio cluster.",
    )
    parser.add_argument("--max-events", type=int, help="Debug limit for scanned events.")
    parser.add_argument("--max-codes", type=int, help="Debug limit for triggered codes to refresh.")
    parser.add_argument(
        "--check-all-events",
        action="store_true",
        help="Check every event instead of only the latest event per stock.",
    )
    parser.add_argument(
        "--report-dir",
        type=Path,
        default=REPO_ROOT / "outputs" / "qfq_corporate_action_refresh",
        help="Directory for JSON reports and DB backups.",
    )
    parser.add_argument("--no-backup", action="store_true", help="Do not create a SQLite backup before apply.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.event_start_date > args.end_date:
        parser.error("--event-start-date must be earlier than or equal to --end-date")
    if args.lookback_days < 1:
        parser.error("--lookback-days must be positive")
    if args.min_overlap < 1:
        parser.error("--min-overlap must be positive")
    if args.min_ratio_diff <= 0:
        parser.error("--min-ratio-diff must be positive")
    if args.max_ratio_std <= 0:
        parser.error("--max-ratio-std must be positive")
    if not 0 < args.min_ratio_consistency <= 1:
        parser.error("--min-ratio-consistency must be within (0, 1]")
    if args.apply_from_report is not None and not args.apply:
        parser.error("--apply-from-report requires --apply")

    codes = [item.strip() for item in args.codes.split(",")] if args.codes else None
    db = DatabaseManager.get_instance()
    if args.apply_from_report is not None:
        print(
            f"开始按 dry-run 报告刷新前复权历史: report={args.apply_from_report} "
            f"chip={'off' if args.skip_chip else 'on'}",
            flush=True,
        )
        report, report_path = run_apply_from_report(
            db,
            dry_run_report_path=args.apply_from_report,
            skip_chip=args.skip_chip,
            report_dir=args.report_dir,
            backup=not args.no_backup,
            max_codes=args.max_codes,
        )
        print("按报告刷新完成:")
        print(f"  triggered_codes  : {report['triggered_code_count']}")
        print(f"  report           : {report_path}")
        return 0 if not any(result.get("status") == "failed" for result in report["apply_results"]) else 1

    print(
        f"开始扫描前复权除权除息刷新: mode={'apply' if args.apply else 'dry-run'} "
        f"events={args.event_start_date}~{args.end_date} "
        f"codes={len(codes) if codes else 'all'} chip={'off' if args.skip_chip else 'on'}",
        flush=True,
    )
    report, report_path = run_refresh(
        db,
        event_start_date=args.event_start_date,
        end_date=args.end_date,
        codes=codes,
        apply=args.apply,
        skip_chip=args.skip_chip,
        lookback_days=args.lookback_days,
        min_overlap=args.min_overlap,
        min_ratio_diff=args.min_ratio_diff,
        max_ratio_std=args.max_ratio_std,
        min_ratio_consistency=args.min_ratio_consistency,
        report_dir=args.report_dir,
        backup=not args.no_backup,
        max_events=args.max_events,
        max_codes=args.max_codes,
        check_all_events=args.check_all_events,
    )
    print("扫描完成:")
    print(f"  event_count      : {report['event_count']}")
    print(f"  checked_events   : {report['checked_event_count']}")
    print(f"  status_counts    : {report['candidate_status_counts']}")
    print(f"  triggered_codes  : {report['triggered_codes']}")
    print(f"  report           : {report_path}")
    return 0 if not any(result.get("status") == "failed" for result in report["apply_results"]) else 1


if __name__ == "__main__":
    raise SystemExit(main())
