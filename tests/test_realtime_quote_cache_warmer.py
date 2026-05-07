# -*- coding: utf-8 -*-

from types import SimpleNamespace
from unittest.mock import patch

from src.services.realtime_quote_cache_warmer import (
    DEFAULT_WARM_INTERVAL_SECONDS,
    RealtimeQuoteCacheWarmer,
)


class _WarmService:
    def __init__(self):
        self.calls = 0

    def warm_all_a_share_realtime_quotes(self, *, force_refresh=False):
        self.calls += 1
        return {
            "status": "refreshed",
            "requested_count": 2,
            "cached_before": 0,
            "fetched_count": 2,
            "failed_count": 0,
            "cached_after": 2,
        }


def test_realtime_quote_cache_warmer_runs_one_all_share_pass() -> None:
    service = _WarmService()
    warmer = RealtimeQuoteCacheWarmer(
        service_factory=lambda: service,
        interval_seconds=600,
        startup_delay_seconds=0,
    )

    with patch(
        "src.services.realtime_quote_cache_warmer.get_config",
        return_value=SimpleNamespace(
            prefetch_realtime_quotes=True,
            enable_realtime_quote=True,
            realtime_cache_ttl=30,
        ),
    ):
        result = warmer.run_once(reason="test")

    assert service.calls == 1
    assert result["status"] == "refreshed"
    assert result["requested_count"] == 2
    assert result["cached_after"] == 2


def test_realtime_quote_cache_warmer_default_interval_is_one_minute() -> None:
    assert DEFAULT_WARM_INTERVAL_SECONDS == 60


def test_realtime_quote_cache_warmer_skips_when_prefetch_disabled() -> None:
    service = _WarmService()
    warmer = RealtimeQuoteCacheWarmer(service_factory=lambda: service)

    with patch(
        "src.services.realtime_quote_cache_warmer.get_config",
        return_value=SimpleNamespace(
            prefetch_realtime_quotes=False,
            enable_realtime_quote=True,
            realtime_cache_ttl=30,
        ),
    ):
        result = warmer.run_once(reason="test")

    assert service.calls == 0
    assert result["status"] == "skipped"
    assert result["reason"] == "PREFETCH_REALTIME_QUOTES=false"
