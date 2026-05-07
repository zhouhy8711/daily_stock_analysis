# -*- coding: utf-8 -*-
"""Read-only diagnostics for stock history DB and realtime caches."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Set

from sqlalchemy import and_, func, select

from data_provider.base import normalize_stock_code
from src.data.stock_index_loader import get_all_a_share_stock_codes, get_index_stock_name
from src.services.stock_service import get_realtime_quote_cache_diagnostics
from src.storage import DatabaseManager, StockDaily, StockIntradayMinute


VALID_DIAGNOSTIC_SCOPES = {"observed", "history_db", "active_a_share"}
VALID_DIAGNOSTIC_SORTS = {"code", "history_rows_desc", "intraday_rows_desc", "latest_daily_desc"}
MAX_DIAGNOSTIC_LIMIT = 1000


@dataclass(frozen=True)
class _DailyCoverage:
    rows: int = 0
    first_date: Optional[date] = None
    last_date: Optional[date] = None
    latest_source: Optional[str] = None


@dataclass(frozen=True)
class _IntradayCoverage:
    rows: int = 0
    first_minute: Optional[datetime] = None
    last_minute: Optional[datetime] = None
    sources: tuple[str, ...] = ()


class StockDataDiagnosticsService:
    """Build stock data coverage diagnostics without mutating DB or caches."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or DatabaseManager.get_instance()

    def get_stock_data_diagnostics(
        self,
        *,
        trade_date: Optional[date] = None,
        scope: str = "observed",
        limit: int = 200,
        offset: int = 0,
        q: Optional[str] = None,
        sort: str = "code",
    ) -> Dict[str, Any]:
        target_date = trade_date or date.today()
        normalized_scope = scope if scope in VALID_DIAGNOSTIC_SCOPES else "observed"
        normalized_sort = sort if sort in VALID_DIAGNOSTIC_SORTS else "code"
        bounded_limit = max(1, min(int(limit or 200), MAX_DIAGNOSTIC_LIMIT))
        safe_offset = max(0, int(offset or 0))

        daily_by_code = self._load_daily_coverage()
        intraday_by_code = self._load_intraday_coverage(target_date)
        quote_state = get_realtime_quote_cache_diagnostics()
        snapshot_codes = {normalize_stock_code(code).upper() for code in quote_state.get("snapshot_codes", [])}
        short_cache_codes = {normalize_stock_code(code).upper() for code in quote_state.get("short_cache_codes", [])}

        history_codes = set(daily_by_code.keys())
        intraday_codes = set(intraday_by_code.keys())
        active_codes = {normalize_stock_code(code).upper() for code in get_all_a_share_stock_codes()}
        if normalized_scope == "active_a_share":
            scope_codes = active_codes
        elif normalized_scope == "history_db":
            scope_codes = history_codes
        else:
            scope_codes = history_codes | intraday_codes | snapshot_codes | short_cache_codes

        filtered_codes = self._filter_codes(scope_codes, q)
        ordered_codes = self._sort_codes(filtered_codes, normalized_sort, daily_by_code, intraday_by_code)
        total = len(ordered_codes)
        page_codes = ordered_codes[safe_offset : safe_offset + bounded_limit]

        return {
            "generated_at": datetime.now().isoformat(),
            "trade_date": target_date.isoformat(),
            "scope": normalized_scope,
            "limit": bounded_limit,
            "offset": safe_offset,
            "total": total,
            "has_more": safe_offset + bounded_limit < total,
            "summary": self._build_summary(
                codes=set(filtered_codes),
                daily_by_code=daily_by_code,
                intraday_by_code=intraday_by_code,
                snapshot_codes=snapshot_codes,
                short_cache_codes=short_cache_codes,
                quote_state=quote_state,
            ),
            "items": [
                self._build_item(code, daily_by_code, intraday_by_code, snapshot_codes, short_cache_codes)
                for code in page_codes
            ],
        }

    def _load_daily_coverage(self) -> Dict[str, _DailyCoverage]:
        latest_dates = (
            select(
                StockDaily.code.label("code"),
                func.max(StockDaily.date).label("latest_date"),
            )
            .group_by(StockDaily.code)
            .subquery()
        )

        with self.db.get_session() as session:
            aggregate_rows = session.execute(
                select(
                    StockDaily.code,
                    func.count(StockDaily.id),
                    func.min(StockDaily.date),
                    func.max(StockDaily.date),
                ).group_by(StockDaily.code)
            ).all()
            source_rows = session.execute(
                select(StockDaily.code, StockDaily.data_source)
                .join(
                    latest_dates,
                    and_(
                        StockDaily.code == latest_dates.c.code,
                        StockDaily.date == latest_dates.c.latest_date,
                    ),
                )
            ).all()

        latest_source_by_raw_code = {str(code or "").strip(): source for code, source in source_rows}
        raw_coverage: Dict[str, _DailyCoverage] = {}
        raw_codes_by_normalized: Dict[str, List[str]] = {}
        for code, row_count, first_date, last_date in aggregate_rows:
            raw_code = str(code or "").strip()
            normalized = self._normalize_code(raw_code)
            if not normalized:
                continue
            raw_coverage[raw_code] = _DailyCoverage(
                rows=int(row_count or 0),
                first_date=first_date,
                last_date=last_date,
                latest_source=latest_source_by_raw_code.get(raw_code),
            )
            raw_codes_by_normalized.setdefault(normalized, []).append(raw_code)

        collision_raw_codes = [
            raw_code
            for raw_codes in raw_codes_by_normalized.values()
            if len(raw_codes) > 1
            for raw_code in raw_codes
        ]
        collision_coverage = self._load_daily_collision_coverage(collision_raw_codes)

        daily_by_code: Dict[str, _DailyCoverage] = {}
        for normalized, raw_codes in raw_codes_by_normalized.items():
            if len(raw_codes) > 1:
                daily_by_code[normalized] = collision_coverage.get(
                    normalized,
                    self._merge_daily_coverages(raw_coverage[raw_code] for raw_code in raw_codes),
                )
            else:
                daily_by_code[normalized] = raw_coverage[raw_codes[0]]
        return daily_by_code

    def _load_daily_collision_coverage(self, raw_codes: List[str]) -> Dict[str, _DailyCoverage]:
        if not raw_codes:
            return {}

        with self.db.get_session() as session:
            rows = session.execute(
                select(StockDaily.code, StockDaily.date, StockDaily.data_source)
                .where(StockDaily.code.in_(raw_codes))
                .order_by(StockDaily.code, StockDaily.date)
            ).all()

        dates_by_code: Dict[str, Set[date]] = {}
        latest_source_by_code: Dict[str, tuple[date, Optional[str]]] = {}
        for raw_code, daily_date, data_source in rows:
            normalized = self._normalize_code(raw_code)
            if not normalized or daily_date is None:
                continue
            dates_by_code.setdefault(normalized, set()).add(daily_date)
            current_latest = latest_source_by_code.get(normalized)
            if current_latest is None or daily_date >= current_latest[0]:
                latest_source_by_code[normalized] = (daily_date, data_source)

        return {
            code: _DailyCoverage(
                rows=len(dates),
                first_date=min(dates) if dates else None,
                last_date=max(dates) if dates else None,
                latest_source=(latest_source_by_code.get(code) or (None, None))[1],
            )
            for code, dates in dates_by_code.items()
        }

    @staticmethod
    def _merge_daily_coverages(items: Any) -> _DailyCoverage:
        rows = 0
        first_date: Optional[date] = None
        last_date: Optional[date] = None
        latest_source: Optional[str] = None
        for item in items:
            rows += item.rows
            if item.first_date and (first_date is None or item.first_date < first_date):
                first_date = item.first_date
            if item.last_date and (last_date is None or item.last_date >= last_date):
                last_date = item.last_date
                latest_source = item.latest_source
        return _DailyCoverage(
            rows=rows,
            first_date=first_date,
            last_date=last_date,
            latest_source=latest_source,
        )

    def _load_intraday_coverage(self, trade_date: date) -> Dict[str, _IntradayCoverage]:
        with self.db.get_session() as session:
            aggregate_rows = session.execute(
                select(
                    StockIntradayMinute.code,
                    func.count(StockIntradayMinute.id),
                    func.min(StockIntradayMinute.minute_ts),
                    func.max(StockIntradayMinute.minute_ts),
                )
                .where(StockIntradayMinute.trade_date == trade_date)
                .group_by(StockIntradayMinute.code)
            ).all()
            source_rows = session.execute(
                select(StockIntradayMinute.code, StockIntradayMinute.source)
                .where(StockIntradayMinute.trade_date == trade_date)
                .distinct()
            ).all()

        sources_by_code: Dict[str, Set[str]] = {}
        for code, source in source_rows:
            normalized = self._normalize_code(code)
            if not normalized or not source:
                continue
            sources_by_code.setdefault(normalized, set()).add(str(source))

        return {
            self._normalize_code(code): _IntradayCoverage(
                rows=int(row_count or 0),
                first_minute=first_minute,
                last_minute=last_minute,
                sources=tuple(sorted(sources_by_code.get(self._normalize_code(code), set()))),
            )
            for code, row_count, first_minute, last_minute in aggregate_rows
            if self._normalize_code(code)
        }

    def _filter_codes(self, codes: Set[str], query: Optional[str]) -> List[str]:
        ordered = sorted(code for code in codes if code)
        text = str(query or "").strip()
        if not text:
            return ordered
        text_lower = text.lower()
        normalized_text = self._normalize_code(text)
        return [
            code
            for code in ordered
            if text_lower in code.lower()
            or (normalized_text and normalized_text in code)
            or text_lower in str(get_index_stock_name(code) or "").lower()
        ]

    @staticmethod
    def _sort_codes(
        codes: List[str],
        sort: str,
        daily_by_code: Dict[str, _DailyCoverage],
        intraday_by_code: Dict[str, _IntradayCoverage],
    ) -> List[str]:
        ordered = sorted(codes)
        if sort == "history_rows_desc":
            return sorted(ordered, key=lambda code: daily_by_code.get(code, _DailyCoverage()).rows, reverse=True)
        if sort == "intraday_rows_desc":
            return sorted(ordered, key=lambda code: intraday_by_code.get(code, _IntradayCoverage()).rows, reverse=True)
        if sort == "latest_daily_desc":
            min_date = date.min
            return sorted(
                ordered,
                key=lambda code: daily_by_code.get(code, _DailyCoverage()).last_date or min_date,
                reverse=True,
            )
        return ordered

    def _build_summary(
        self,
        *,
        codes: Set[str],
        daily_by_code: Dict[str, _DailyCoverage],
        intraday_by_code: Dict[str, _IntradayCoverage],
        snapshot_codes: Set[str],
        short_cache_codes: Set[str],
        quote_state: Dict[str, Any],
    ) -> Dict[str, Any]:
        daily_items = [daily_by_code[code] for code in codes if code in daily_by_code]
        intraday_items = [intraday_by_code[code] for code in codes if code in intraday_by_code]
        first_dates = [item.first_date for item in daily_items if item.first_date]
        last_dates = [item.last_date for item in daily_items if item.last_date]
        first_minutes = [item.first_minute for item in intraday_items if item.first_minute]
        last_minutes = [item.last_minute for item in intraday_items if item.last_minute]
        snapshot_hits = codes & snapshot_codes
        short_cache_hits = codes & short_cache_codes

        return {
            "population_count": len(codes),
            "history": {
                "stock_count": len(daily_items),
                "row_count": sum(item.rows for item in daily_items),
                "first_date": min(first_dates).isoformat() if first_dates else None,
                "last_date": max(last_dates).isoformat() if last_dates else None,
                "missing_count": len(codes) - len(daily_items),
            },
            "intraday": {
                "stock_count": len(intraday_items),
                "row_count": sum(item.rows for item in intraday_items),
                "first_minute": min(first_minutes).isoformat() if first_minutes else None,
                "last_minute": max(last_minutes).isoformat() if last_minutes else None,
                "missing_count": len(codes) - len(intraday_items),
            },
            "quote": {
                "snapshot_id": quote_state.get("snapshot_id"),
                "snapshot_time": quote_state.get("snapshot_time"),
                "snapshot_age_seconds": quote_state.get("snapshot_age_seconds"),
                "snapshot_items": int(quote_state.get("quote_snapshot_items") or 0),
                "short_cache_items": int(quote_state.get("short_cache_items") or 0),
                "snapshot_hit_count": len(snapshot_hits),
                "short_cache_hit_count": len(short_cache_hits),
            },
        }

    def _build_item(
        self,
        code: str,
        daily_by_code: Dict[str, _DailyCoverage],
        intraday_by_code: Dict[str, _IntradayCoverage],
        snapshot_codes: Set[str],
        short_cache_codes: Set[str],
    ) -> Dict[str, Any]:
        daily = daily_by_code.get(code, _DailyCoverage())
        intraday = intraday_by_code.get(code, _IntradayCoverage())
        return {
            "stock_code": code,
            "stock_name": get_index_stock_name(code),
            "history": {
                "rows": daily.rows,
                "first_date": daily.first_date.isoformat() if daily.first_date else None,
                "last_date": daily.last_date.isoformat() if daily.last_date else None,
                "latest_source": daily.latest_source,
            },
            "intraday": {
                "rows": intraday.rows,
                "first_minute": intraday.first_minute.isoformat() if intraday.first_minute else None,
                "last_minute": intraday.last_minute.isoformat() if intraday.last_minute else None,
                "sources": list(intraday.sources),
            },
            "quote": {
                "snapshot_hit": code in snapshot_codes,
                "short_cache_hit": code in short_cache_codes,
            },
        }

    @staticmethod
    def _normalize_code(code: Any) -> str:
        text = str(code or "").strip()
        if not text:
            return ""
        return normalize_stock_code(text).strip().upper()
