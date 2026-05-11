# -*- coding: utf-8 -*-
"""Close-session archive task for intraday minute hot-table rows."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import date, datetime, timedelta, time as dt_time
from typing import Any, Callable, Dict, Optional

import pandas as pd

from src.core import trading_calendar
from src.services.daily_history_enrichment import enrich_daily_history_with_quote_fields
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

DEFAULT_INTRADAY_ARCHIVE_INTERVAL_SECONDS = 30 * 60
DEFAULT_INTRADAY_ARCHIVE_AFTER = dt_time(16, 0)

_RUN_LOCK = threading.Lock()


class IntradayDailyArchiveService:
    """Archive same-day intraday rows to stock_daily after the A-share close."""

    def __init__(
        self,
        *,
        db_manager: Optional[DatabaseManager] = None,
        archive_after: dt_time = DEFAULT_INTRADAY_ARCHIVE_AFTER,
        now_provider: Optional[Callable[[], datetime]] = None,
        quote_loader: Optional[Callable[[str], Any]] = None,
    ) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.archive_after = archive_after
        self._now_provider = now_provider or datetime.now
        self._quote_loader = quote_loader

    def _load_quote(self, code: str) -> Any:
        if self._quote_loader is not None:
            return self._quote_loader(code)

        from src.services.stock_service import StockService

        return StockService().get_realtime_quote(code)

    def _refresh_daily_valuation(self, code: str, target_date: date) -> bool:
        rows = self.db.get_data_range(code, target_date, target_date)
        if not rows:
            return False

        try:
            quote = self._load_quote(code)
        except Exception as exc:
            logger.debug("[分钟热表收盘归档] %s %s 估值回填跳过: %s", target_date.isoformat(), code, exc)
            return False
        if quote is None:
            return False

        row = rows[0]
        source = getattr(row, "data_source", None) or "intraday_hot_table"
        enriched = enrich_daily_history_with_quote_fields(
            pd.DataFrame([row.to_dict()]),
            code,
            quote=quote,
        )
        if enriched is None or enriched.empty:
            return False

        metric_columns = ("pe_ratio", "total_mv", "circ_mv", "total_shares", "float_shares")
        before = {column: getattr(row, column, None) for column in metric_columns}
        payload = enriched.iloc[0]
        changed = any(
            before[column] in (None, 0)
            and pd.notna(payload.get(column))
            and payload.get(column) not in (None, 0)
            for column in metric_columns
        )
        if not changed:
            return False

        self.db.save_daily_data(enriched, code, data_source=source)
        return True

    def _refresh_missing_daily_valuations(self, codes: list[str], target_date: date) -> int:
        if len(codes) > 1 and self._quote_loader is None:
            try:
                from src.services.stock_service import StockService

                StockService().warm_realtime_quotes(codes, force_refresh=False)
            except Exception as exc:
                logger.debug("[分钟热表收盘归档] 批量预热估值 quote 跳过: %s", exc)

        refreshed = 0
        for code in codes:
            if self._refresh_daily_valuation(code, target_date):
                refreshed += 1
        return refreshed

    def _sync_chip_daily_for_code(self, code: str, target_date: date) -> bool:
        rows = self.db.get_data_range(code, target_date - timedelta(days=365), target_date)
        if not rows:
            return False

        history_df = pd.DataFrame([row.to_dict() for row in rows])
        if history_df.empty:
            return False

        try:
            from src.services.chip_daily_sync import sync_chip_daily_from_history

            saved_count = sync_chip_daily_from_history(
                self.db,
                code,
                history_df,
                data_source=getattr(rows[-1], "data_source", None) or "stock_daily",
                target_dates=[target_date],
                skip_existing=True,
            )
            return saved_count > 0
        except Exception as exc:
            logger.debug("[分钟热表收盘归档] %s %s 筹码日表同步跳过: %s", target_date.isoformat(), code, exc)
            return False

    def _sync_missing_chip_daily(self, codes: list[str], target_date: date) -> int:
        synced = 0
        for code in codes:
            if self._sync_chip_daily_for_code(code, target_date):
                synced += 1
        return synced

    def run_once(
        self,
        *,
        current_time: Optional[datetime] = None,
        reason: str = "manual",
    ) -> Dict[str, Any]:
        """Run one archive pass.

        The task keeps hot-table rows when archiving a code fails so the next
        30-minute pass can retry instead of silently dropping the only source.
        """
        now = current_time or self._now_provider()
        market_now = trading_calendar.get_market_now("cn", now)
        target_date = market_now.date()

        if not trading_calendar.is_market_open("cn", target_date):
            return {
                "status": "skipped",
                "reason": "market_closed",
                "trade_date": target_date.isoformat(),
                "market_time": market_now.strftime("%H:%M:%S"),
            }

        if market_now.time() < self.archive_after:
            return {
                "status": "skipped",
                "reason": "before_archive_time",
                "trade_date": target_date.isoformat(),
                "archive_after": self.archive_after.strftime("%H:%M"),
                "market_time": market_now.strftime("%H:%M:%S"),
            }

        codes = self.db.get_intraday_minute_codes(trade_date=target_date)
        if not codes:
            valuation_codes = self.db.get_daily_codes_missing_valuation(
                trade_date=target_date,
                data_source="intraday_hot_table",
            )
            valuation_refreshed_count = self._refresh_missing_daily_valuations(valuation_codes, target_date)
            chip_codes = self.db.get_daily_codes_missing_chip_snapshot(
                trade_date=target_date,
                data_source="intraday_hot_table",
            )
            chip_synced_count = self._sync_missing_chip_daily(chip_codes, target_date)
            if valuation_refreshed_count or chip_synced_count:
                return {
                    "status": "completed",
                    "reason": reason,
                    "trade_date": target_date.isoformat(),
                    "scanned_code_count": 0,
                    "archived_code_count": 0,
                    "skipped_completed_count": 0,
                    "purged_row_count": 0,
                    "failed_code_count": 0,
                    "failed_codes": [],
                    "valuation_refreshed_count": valuation_refreshed_count,
                    "chip_synced_count": chip_synced_count,
                }
            return {
                "status": "skipped",
                "reason": "no_intraday_rows",
                "trade_date": target_date.isoformat(),
                "scanned_code_count": 0,
                "archived_code_count": 0,
                "skipped_completed_count": 0,
                "purged_row_count": 0,
                "valuation_refreshed_count": 0,
                "chip_synced_count": 0,
            }

        completed_codes = set(
            self.db.get_completed_daily_archive_codes(
                trade_date=target_date,
                codes=codes,
            )
        )
        pending_codes = [code for code in codes if code not in completed_codes]
        purged_completed_rows = 0
        if completed_codes:
            purged_completed_rows = self.db.purge_intraday_minutes_for_date(
                trade_date=target_date,
                codes=sorted(completed_codes),
            )
        if not pending_codes:
            return {
                "status": "skipped",
                "reason": "daily_archive_already_complete",
                "trade_date": target_date.isoformat(),
                "scanned_code_count": len(codes),
                "archived_code_count": 0,
                "skipped_completed_count": len(completed_codes),
                "purged_row_count": purged_completed_rows,
                "failed_code_count": 0,
                "failed_codes": [],
                "valuation_refreshed_count": 0,
                "chip_synced_count": 0,
            }

        archived_codes = []
        failed_codes = []
        valuation_refreshed_count = 0
        for code in pending_codes:
            try:
                self.db.archive_intraday_minutes_to_daily(
                    trade_date=target_date,
                    codes=[code],
                )
                if self.db.has_today_data(code, target_date):
                    archived_codes.append(code)
                    if self._refresh_daily_valuation(code, target_date):
                        valuation_refreshed_count += 1
                else:
                    failed_codes.append({
                        "code": code,
                        "reason": "daily_row_missing_after_archive",
                    })
            except Exception as exc:
                failed_codes.append({"code": code, "reason": str(exc)})
                logger.warning(
                    "[分钟热表收盘归档] %s %s 归档失败: %s",
                    target_date.isoformat(),
                    code,
                    exc,
                    exc_info=True,
                )

        chip_codes = sorted(set(archived_codes) | set(
            self.db.get_daily_codes_missing_chip_snapshot(
                trade_date=target_date,
                data_source="intraday_hot_table",
            )
        ))
        chip_synced_count = self._sync_missing_chip_daily(chip_codes, target_date)

        purged_rows = purged_completed_rows + self.db.purge_intraday_minutes_for_date(
            trade_date=target_date,
            codes=archived_codes,
        )

        status = "completed"
        if failed_codes and archived_codes:
            status = "partial"
        elif failed_codes and not archived_codes:
            status = "failed"

        result = {
            "status": status,
            "reason": reason,
            "trade_date": target_date.isoformat(),
            "scanned_code_count": len(codes),
            "archived_code_count": len(archived_codes),
            "skipped_completed_count": len(completed_codes),
            "purged_row_count": purged_rows,
            "failed_code_count": len(failed_codes),
            "failed_codes": failed_codes[:20],
            "valuation_refreshed_count": valuation_refreshed_count,
            "chip_synced_count": chip_synced_count,
        }
        logger.info(
            "[分钟热表收盘归档] reason=%s status=%s trade_date=%s scanned=%s "
            "archived=%s skipped_completed=%s valuation_refreshed=%s chip_synced=%s purged_rows=%s failed=%s",
            reason,
            result["status"],
            result["trade_date"],
            result["scanned_code_count"],
            result["archived_code_count"],
            result["skipped_completed_count"],
            result["valuation_refreshed_count"],
            result["chip_synced_count"],
            result["purged_row_count"],
            result["failed_code_count"],
        )
        return result


def run_intraday_daily_archive_once(
    *,
    reason: str = "manual",
    current_time: Optional[datetime] = None,
    service_factory: Callable[[], IntradayDailyArchiveService] = IntradayDailyArchiveService,
) -> Dict[str, Any]:
    """Run one archive pass with process-wide overlap protection."""
    if not _RUN_LOCK.acquire(blocking=False):
        return {
            "status": "skipped",
            "reason": "already_running",
            "scanned_code_count": 0,
            "archived_code_count": 0,
            "purged_row_count": 0,
        }

    started = time.monotonic()
    try:
        result = service_factory().run_once(
            current_time=current_time,
            reason=reason,
        )
        result["elapsed_seconds"] = round(time.monotonic() - started, 3)
        return result
    finally:
        _RUN_LOCK.release()


class IntradayDailyArchiveWorker:
    """Threaded 30-minute worker used by the FastAPI app lifecycle."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], IntradayDailyArchiveService] = IntradayDailyArchiveService,
        interval_seconds: int = DEFAULT_INTRADAY_ARCHIVE_INTERVAL_SECONDS,
        startup_delay_seconds: int = 0,
    ) -> None:
        self._service_factory = service_factory
        self._interval_seconds = max(30, int(interval_seconds))
        self._startup_delay_seconds = max(0, int(startup_delay_seconds))
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

    def start(self, *, skip_pytest: bool = True) -> bool:
        """Start the archive worker thread."""
        if skip_pytest and ("pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST")):
            logger.debug("[分钟热表收盘归档] pytest 环境跳过后台线程")
            return False

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="intraday-daily-archive-worker",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "[分钟热表收盘归档] 已启动后台任务，interval=%ss startup_delay=%ss",
                self._interval_seconds,
                self._startup_delay_seconds,
            )
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the archive worker thread."""
        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run_loop(self) -> None:
        if self._startup_delay_seconds > 0 and self._stop_event.wait(self._startup_delay_seconds):
            return

        while not self._stop_event.is_set():
            run_intraday_daily_archive_once(
                reason="background",
                service_factory=self._service_factory,
            )
            if self._stop_event.wait(self._interval_seconds):
                return


_WORKER = IntradayDailyArchiveWorker()


def get_intraday_daily_archive_worker() -> IntradayDailyArchiveWorker:
    return _WORKER
