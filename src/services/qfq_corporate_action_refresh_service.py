# -*- coding: utf-8 -*-
"""Scheduled qfq refresh after A-share corporate actions."""

from __future__ import annotations

import logging
import json
import os
import sys
import threading
import time
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Optional

from src.config import Config, get_config
from src.core import trading_calendar
from src.storage import DatabaseManager
from tools.refresh_qfq_after_corporate_actions import REPO_ROOT, run_refresh

logger = logging.getLogger(__name__)

DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_AFTER = dt_time(16, 30)
DEFAULT_QFQ_CORPORATE_ACTION_EVENT_LOOKBACK_DAYS = 60
DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_INTERVAL_SECONDS = 30 * 60
DEFAULT_QFQ_CORPORATE_ACTION_REPORT_DIR = (
    REPO_ROOT / "outputs" / "qfq_corporate_action_refresh" / "scheduled"
)
STATE_FILE_NAME = "qfq_corporate_action_refresh_state.json"

RefreshRunner = Callable[..., tuple[dict[str, Any], Path]]

_RUN_LOCK = threading.Lock()
_COMPLETED_TRADE_DATES: set[date] = set()


def _parse_refresh_after(value: object) -> dt_time:
    """Parse HH:MM into a time value with a safe default."""
    if isinstance(value, dt_time):
        return value

    candidate = str(value or "").strip()
    if not candidate:
        return DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_AFTER

    try:
        hour_text, minute_text = candidate.split(":", 1)
        hour = int(hour_text)
        minute = int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError(candidate)
        return dt_time(hour, minute)
    except Exception:
        logger.warning(
            "[qfq corporate action refresh] invalid refresh-after time %r, fallback to %s",
            value,
            DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_AFTER.strftime("%H:%M"),
        )
        return DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_AFTER


def _is_cn_trading_day(target_date: date) -> bool:
    if target_date.weekday() >= 5:
        return False
    return trading_calendar.is_market_open("cn", target_date)


def _count_failed_apply_results(report: Dict[str, Any]) -> int:
    return sum(
        1
        for item in report.get("apply_results", [])
        if isinstance(item, dict) and item.get("status") == "failed"
    )


class QfqCorporateActionRefreshService:
    """Run the daily post-close stale-qfq detection and repair pass."""

    def __init__(
        self,
        *,
        db: Optional[DatabaseManager] = None,
        config_provider: Callable[[], Config] = get_config,
        refresh_runner: RefreshRunner = run_refresh,
        report_dir: Path = DEFAULT_QFQ_CORPORATE_ACTION_REPORT_DIR,
    ) -> None:
        self._db = db
        self._config_provider = config_provider
        self._refresh_runner = refresh_runner
        self._report_dir = report_dir
        self._state_path = report_dir / STATE_FILE_NAME

    @property
    def db(self) -> DatabaseManager:
        if self._db is None:
            self._db = DatabaseManager.get_instance()
        return self._db

    def run_once(
        self,
        *,
        reason: str = "manual",
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        config = self._config_provider()
        if not bool(getattr(config, "qfq_corporate_action_refresh_enabled", True)):
            return {
                "status": "skipped",
                "reason": "disabled",
            }

        market_now = trading_calendar.get_market_now("cn", current_time=current_time)
        trade_date = market_now.date()
        refresh_after = _parse_refresh_after(
            getattr(config, "qfq_corporate_action_refresh_after", "16:30")
        )

        if not _is_cn_trading_day(trade_date):
            return {
                "status": "skipped",
                "reason": "market_closed",
                "trade_date": trade_date.isoformat(),
            }
        if market_now.time() < refresh_after:
            return {
                "status": "skipped",
                "reason": "before_refresh_time",
                "trade_date": trade_date.isoformat(),
                "refresh_after": refresh_after.strftime("%H:%M"),
            }

        if self._has_completed_trade_date(trade_date):
            return {
                "status": "skipped",
                "reason": "already_completed_for_trade_date",
                "trade_date": trade_date.isoformat(),
            }

        lookback_days = int(
            max(
                1,
                getattr(
                    config,
                    "qfq_corporate_action_refresh_lookback_days",
                    DEFAULT_QFQ_CORPORATE_ACTION_EVENT_LOOKBACK_DAYS,
                ),
            )
        )
        event_start_date = trade_date - timedelta(days=lookback_days)

        if not _RUN_LOCK.acquire(blocking=False):
            return {
                "status": "skipped",
                "reason": "already_running",
                "trade_date": trade_date.isoformat(),
            }

        started = time.monotonic()
        try:
            if self._has_completed_trade_date(trade_date):
                return {
                    "status": "skipped",
                    "reason": "already_completed_for_trade_date",
                    "trade_date": trade_date.isoformat(),
                }

            logger.info(
                "[qfq corporate action refresh] start reason=%s trade_date=%s events=%s~%s",
                reason,
                trade_date.isoformat(),
                event_start_date.isoformat(),
                trade_date.isoformat(),
            )
            report, report_path = self._refresh_runner(
                self.db,
                event_start_date=event_start_date,
                end_date=trade_date,
                apply=True,
                skip_chip=False,
                report_dir=self._report_dir,
                backup=True,
            )
            failed_apply_count = _count_failed_apply_results(report)
            triggered_count = int(report.get("triggered_code_count") or 0)
            status = "completed" if failed_apply_count == 0 else "partial"

            result = {
                "status": status,
                "reason": reason,
                "trade_date": trade_date.isoformat(),
                "event_start_date": event_start_date.isoformat(),
                "end_date": trade_date.isoformat(),
                "event_count": int(report.get("event_count") or 0),
                "checked_event_count": int(report.get("checked_event_count") or 0),
                "triggered_code_count": triggered_count,
                "triggered_codes": list(report.get("triggered_codes") or []),
                "failed_apply_count": failed_apply_count,
                "backup_path": report.get("backup_path"),
                "report_path": str(report_path),
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            if status == "completed":
                self._record_completed_trade_date(
                    trade_date=trade_date,
                    result=result,
                )
            logger.info(
                "[qfq corporate action refresh] done status=%s trade_date=%s "
                "events=%s checked=%s triggered=%s failed=%s report=%s",
                result["status"],
                result["trade_date"],
                result["event_count"],
                result["checked_event_count"],
                result["triggered_code_count"],
                result["failed_apply_count"],
                result["report_path"],
            )
            return result
        except Exception as exc:
            elapsed_seconds = round(time.monotonic() - started, 3)
            logger.warning(
                "[qfq corporate action refresh] failed reason=%s trade_date=%s elapsed=%ss: %s",
                reason,
                trade_date.isoformat(),
                elapsed_seconds,
                exc,
                exc_info=True,
            )
            return {
                "status": "failed",
                "reason": reason,
                "trade_date": trade_date.isoformat(),
                "event_start_date": event_start_date.isoformat(),
                "end_date": trade_date.isoformat(),
                "error": str(exc),
                "elapsed_seconds": elapsed_seconds,
            }
        finally:
            _RUN_LOCK.release()

    def _has_completed_trade_date(self, trade_date: date) -> bool:
        if trade_date in _COMPLETED_TRADE_DATES:
            return True
        try:
            state = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return False
        except Exception as exc:
            logger.warning(
                "[qfq corporate action refresh] ignore unreadable state file %s: %s",
                self._state_path,
                exc,
            )
            return False

        if state.get("trade_date") == trade_date.isoformat() and state.get("status") == "completed":
            _COMPLETED_TRADE_DATES.add(trade_date)
            return True
        return False

    def _record_completed_trade_date(
        self,
        *,
        trade_date: date,
        result: Dict[str, Any],
    ) -> None:
        _COMPLETED_TRADE_DATES.add(trade_date)
        try:
            self._report_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "trade_date": trade_date.isoformat(),
                "status": "completed",
                "recorded_at": datetime.now().isoformat(timespec="seconds"),
                "report_path": result.get("report_path"),
                "triggered_code_count": result.get("triggered_code_count"),
                "failed_apply_count": result.get("failed_apply_count"),
            }
            temp_path = self._state_path.with_suffix(self._state_path.suffix + ".tmp")
            temp_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            temp_path.replace(self._state_path)
        except Exception as exc:
            logger.warning(
                "[qfq corporate action refresh] failed to write state file %s: %s",
                self._state_path,
                exc,
            )


def run_qfq_corporate_action_refresh_once(
    *,
    reason: str = "manual",
    current_time: Optional[datetime] = None,
    service_factory: Callable[[], QfqCorporateActionRefreshService] = QfqCorporateActionRefreshService,
) -> Dict[str, Any]:
    """Run one scheduled qfq refresh pass."""
    return service_factory().run_once(reason=reason, current_time=current_time)


class QfqCorporateActionRefreshWorker:
    """Threaded worker used by the FastAPI app lifecycle."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], QfqCorporateActionRefreshService] = QfqCorporateActionRefreshService,
        interval_seconds: Optional[int] = None,
        startup_delay_seconds: int = 0,
    ) -> None:
        self._service_factory = service_factory
        self._interval_seconds = interval_seconds
        self._startup_delay_seconds = max(0, int(startup_delay_seconds))
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._thread: Optional[threading.Thread] = None

    @staticmethod
    def _resolve_interval_seconds() -> int:
        try:
            config = get_config()
            interval = int(
                getattr(
                    config,
                    "qfq_corporate_action_refresh_interval_seconds",
                    DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_INTERVAL_SECONDS,
                )
            )
        except Exception:
            interval = DEFAULT_QFQ_CORPORATE_ACTION_REFRESH_INTERVAL_SECONDS
        return max(30, interval)

    def start(self, *, skip_pytest: bool = True) -> bool:
        """Start the refresh worker thread."""
        if skip_pytest and ("pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST")):
            logger.debug("[qfq corporate action refresh] pytest environment skip worker")
            return False

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return True

            interval = self._interval_seconds or self._resolve_interval_seconds()
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="qfq-corporate-action-refresh-worker",
                daemon=True,
                args=(interval,),
            )
            self._thread.start()
            logger.info(
                "[qfq corporate action refresh] worker started interval=%ss startup_delay=%ss",
                interval,
                self._startup_delay_seconds,
            )
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the refresh worker thread."""
        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def _run_loop(self, interval_seconds: int) -> None:
        if self._startup_delay_seconds > 0 and self._stop_event.wait(self._startup_delay_seconds):
            return

        while not self._stop_event.is_set():
            run_qfq_corporate_action_refresh_once(
                reason="background",
                service_factory=self._service_factory,
            )
            interval_seconds = self._interval_seconds or self._resolve_interval_seconds()
            if self._stop_event.wait(max(30, int(interval_seconds))):
                return


def reset_qfq_corporate_action_refresh_state() -> None:
    """Reset process-local daily guard state; intended for tests."""
    with _RUN_LOCK:
        _COMPLETED_TRADE_DATES.clear()


_WORKER = QfqCorporateActionRefreshWorker()


def get_qfq_corporate_action_refresh_worker() -> QfqCorporateActionRefreshWorker:
    return _WORKER
