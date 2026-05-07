# -*- coding: utf-8 -*-
"""Background warmer for all A-share realtime quote caches."""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from typing import Any, Callable, Dict, Optional

from src.config import get_config
from src.services.stock_service import StockService

logger = logging.getLogger(__name__)

DEFAULT_WARM_INTERVAL_SECONDS = 60
DEFAULT_STARTUP_DELAY_SECONDS = 5


class RealtimeQuoteCacheWarmer:
    """Periodically fills the in-process realtime quote cache for all A-shares."""

    def __init__(
        self,
        *,
        service_factory: Callable[[], StockService] = StockService,
        interval_seconds: int = DEFAULT_WARM_INTERVAL_SECONDS,
        startup_delay_seconds: int = DEFAULT_STARTUP_DELAY_SECONDS,
    ) -> None:
        self._service_factory = service_factory
        self._interval_seconds = max(1, int(interval_seconds))
        self._startup_delay_seconds = max(0, int(startup_delay_seconds))
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.RLock()
        self._run_lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._last_log_monotonic = 0.0

    def _enabled_state(self) -> tuple[bool, str]:
        try:
            config = get_config()
        except Exception as exc:
            return False, f"config_unavailable:{exc}"

        if not getattr(config, "prefetch_realtime_quotes", True):
            return False, "PREFETCH_REALTIME_QUOTES=false"
        if not getattr(config, "enable_realtime_quote", True):
            return False, "ENABLE_REALTIME_QUOTE=false"
        quote_cache_seconds = int(
            getattr(
                config,
                "realtime_quote_cache_seconds",
                getattr(config, "realtime_cache_ttl", 30),
            )
            or 0
        )
        if quote_cache_seconds <= 0:
            return False, "REALTIME_QUOTE_CACHE_SECONDS=0"
        return True, "enabled"

    def start(self, *, skip_pytest: bool = True) -> bool:
        """Start the background warmer thread when realtime prefetch is enabled."""
        if skip_pytest and ("pytest" in sys.modules or os.getenv("PYTEST_CURRENT_TEST")):
            logger.debug("[实时行情缓存预热] pytest 环境跳过后台线程")
            return False

        enabled, reason = self._enabled_state()
        if not enabled:
            logger.info("[实时行情缓存预热] 未启动: %s", reason)
            return False

        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return True

            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._run_loop,
                name="realtime-quote-cache-warmer",
                daemon=True,
            )
            self._thread.start()
            logger.info(
                "[实时行情缓存预热] 已启动后台全 A 股预热，interval=%ss startup_delay=%ss",
                self._interval_seconds,
                self._startup_delay_seconds,
            )
            return True

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the background warmer thread."""
        with self._lifecycle_lock:
            thread = self._thread
            self._stop_event.set()

        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)

        with self._lifecycle_lock:
            if self._thread is thread and (thread is None or not thread.is_alive()):
                self._thread = None

    def run_once(self, *, force_refresh: bool = False, reason: str = "manual") -> Dict[str, Any]:
        """Run one all-A-share cache warm pass."""
        enabled, disabled_reason = self._enabled_state()
        if not enabled:
            result = {
                "status": "skipped",
                "reason": disabled_reason,
                "requested_count": 0,
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": 0,
            }
            self._log_result(result, reason=reason, force=True)
            return result

        if not self._run_lock.acquire(blocking=False):
            return {
                "status": "skipped",
                "reason": "already_running",
                "requested_count": 0,
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": 0,
            }

        started = time.monotonic()
        try:
            result = self._service_factory().warm_all_a_share_realtime_quotes(
                force_refresh=force_refresh
            )
            result["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self._log_result(result, reason=reason)
            return result
        except Exception as exc:
            result = {
                "status": "error",
                "reason": str(exc),
                "requested_count": 0,
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": 0,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            logger.warning("[实时行情缓存预热] 执行失败: %s", exc, exc_info=True)
            return result
        finally:
            self._run_lock.release()

    def _run_loop(self) -> None:
        if self._startup_delay_seconds > 0 and self._stop_event.wait(self._startup_delay_seconds):
            return

        while not self._stop_event.is_set():
            self.run_once(force_refresh=True, reason="background")
            if self._stop_event.wait(self._interval_seconds):
                return

    def _log_result(self, result: Dict[str, Any], *, reason: str, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_log_monotonic < self._interval_seconds:
            return

        self._last_log_monotonic = now
        logger.info(
            "[实时行情缓存预热] reason=%s status=%s A股=%s cached_before=%s "
            "fetched=%s failed=%s cached_after=%s elapsed=%ss",
            reason,
            result.get("status"),
            result.get("requested_count", 0),
            result.get("cached_before", 0),
            result.get("fetched_count", 0),
            result.get("failed_count", 0),
            result.get("cached_after", result.get("cached_before", 0)),
            result.get("elapsed_seconds", 0),
        )


_WARMER = RealtimeQuoteCacheWarmer()


def get_realtime_quote_cache_warmer() -> RealtimeQuoteCacheWarmer:
    return _WARMER
