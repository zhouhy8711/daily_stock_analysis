# -*- coding: utf-8 -*-
"""Close-session archive task for intraday minute hot-table rows."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from datetime import datetime, time as dt_time
from typing import Any, Callable, Dict, Optional

from src.core import trading_calendar
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
    ) -> None:
        self.db = db_manager or DatabaseManager.get_instance()
        self.archive_after = archive_after
        self._now_provider = now_provider or datetime.now

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
            return {
                "status": "skipped",
                "reason": "no_intraday_rows",
                "trade_date": target_date.isoformat(),
                "scanned_code_count": 0,
                "archived_code_count": 0,
                "purged_row_count": 0,
            }

        archived_codes = []
        failed_codes = []
        for code in codes:
            try:
                self.db.archive_intraday_minutes_to_daily(
                    trade_date=target_date,
                    codes=[code],
                )
                if self.db.has_today_data(code, target_date):
                    archived_codes.append(code)
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

        purged_rows = self.db.purge_intraday_minutes_for_date(
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
            "purged_row_count": purged_rows,
            "failed_code_count": len(failed_codes),
            "failed_codes": failed_codes[:20],
        }
        logger.info(
            "[分钟热表收盘归档] reason=%s status=%s trade_date=%s scanned=%s "
            "archived=%s purged_rows=%s failed=%s",
            reason,
            result["status"],
            result["trade_date"],
            result["scanned_code_count"],
            result["archived_code_count"],
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
