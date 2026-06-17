# -*- coding: utf-8 -*-
"""Tests for scheduled qfq corporate-action refresh service."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.services import qfq_corporate_action_refresh_service as service_module


@pytest.fixture(autouse=True)
def reset_refresh_state():
    service_module.reset_qfq_corporate_action_refresh_state()
    yield
    service_module.reset_qfq_corporate_action_refresh_state()


def _config(
    *,
    enabled=True,
    refresh_after="16:30",
    lookback_days=60,
    interval_seconds=1800,
):
    return SimpleNamespace(
        qfq_corporate_action_refresh_enabled=enabled,
        qfq_corporate_action_refresh_after=refresh_after,
        qfq_corporate_action_refresh_lookback_days=lookback_days,
        qfq_corporate_action_refresh_interval_seconds=interval_seconds,
    )


def test_refresh_service_skips_when_disabled(tmp_path):
    calls = []
    service = service_module.QfqCorporateActionRefreshService(
        db=object(),
        config_provider=lambda: _config(enabled=False),
        refresh_runner=lambda *args, **kwargs: calls.append(kwargs),
        report_dir=tmp_path,
    )

    result = service.run_once(current_time=datetime(2026, 6, 5, 17, 0))

    assert result["status"] == "skipped"
    assert result["reason"] == "disabled"
    assert calls == []


def test_refresh_service_skips_before_refresh_time(tmp_path):
    calls = []
    service = service_module.QfqCorporateActionRefreshService(
        db=object(),
        config_provider=lambda: _config(refresh_after="16:30"),
        refresh_runner=lambda *args, **kwargs: calls.append(kwargs),
        report_dir=tmp_path,
    )

    with patch.object(service_module.trading_calendar, "is_market_open", return_value=True):
        result = service.run_once(current_time=datetime(2026, 6, 5, 16, 29))

    assert result["status"] == "skipped"
    assert result["reason"] == "before_refresh_time"
    assert result["trade_date"] == "2026-06-05"
    assert calls == []


def test_refresh_service_skips_non_trading_day(tmp_path):
    calls = []
    service = service_module.QfqCorporateActionRefreshService(
        db=object(),
        config_provider=lambda: _config(refresh_after="16:30"),
        refresh_runner=lambda *args, **kwargs: calls.append(kwargs),
        report_dir=tmp_path,
    )

    result = service.run_once(current_time=datetime(2026, 6, 6, 17, 0))

    assert result["status"] == "skipped"
    assert result["reason"] == "market_closed"
    assert calls == []


def test_refresh_service_runs_apply_once_per_trade_date(tmp_path):
    calls = []
    report_path = tmp_path / "report.json"

    def fake_runner(db, **kwargs):
        calls.append({"db": db, **kwargs})
        return (
            {
                "event_count": 3,
                "checked_event_count": 2,
                "triggered_code_count": 1,
                "triggered_codes": ["688498"],
                "backup_path": None,
                "apply_results": [{"code": "688498", "status": "completed"}],
            },
            report_path,
        )

    db = object()
    service = service_module.QfqCorporateActionRefreshService(
        db=db,
        config_provider=lambda: _config(refresh_after="16:30", lookback_days=60),
        refresh_runner=fake_runner,
        report_dir=tmp_path,
    )

    with patch.object(service_module.trading_calendar, "is_market_open", return_value=True):
        first = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 16, 31))
        second = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 17, 0))

    assert first["status"] == "completed"
    assert first["triggered_code_count"] == 1
    assert first["report_path"] == str(report_path)
    assert second["status"] == "skipped"
    assert second["reason"] == "already_completed_for_trade_date"
    assert len(calls) == 1
    call = calls[0]
    assert call["db"] is db
    assert call["event_start_date"] == date(2026, 6, 5) - timedelta(days=60)
    assert call["end_date"] == date(2026, 6, 5)
    assert call["apply"] is True
    assert call["skip_chip"] is False
    assert call["report_dir"] == tmp_path
    assert call["backup"] is True

    service_module.reset_qfq_corporate_action_refresh_state()
    restarted_service = service_module.QfqCorporateActionRefreshService(
        db=db,
        config_provider=lambda: _config(refresh_after="16:30", lookback_days=60),
        refresh_runner=fake_runner,
        report_dir=tmp_path,
    )
    with patch.object(service_module.trading_calendar, "is_market_open", return_value=True):
        after_restart = restarted_service.run_once(
            reason="test",
            current_time=datetime(2026, 6, 5, 17, 1),
        )

    assert after_restart["status"] == "skipped"
    assert after_restart["reason"] == "already_completed_for_trade_date"
    assert len(calls) == 1


def test_refresh_service_failure_does_not_mark_trade_date_completed(tmp_path):
    calls = {"count": 0}
    report_path = tmp_path / "report.json"

    def flaky_runner(db, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("network down")
        return (
            {
                "event_count": 0,
                "checked_event_count": 0,
                "triggered_code_count": 0,
                "triggered_codes": [],
                "backup_path": None,
                "apply_results": [],
            },
            report_path,
        )

    service = service_module.QfqCorporateActionRefreshService(
        db=object(),
        config_provider=lambda: _config(refresh_after="16:30"),
        refresh_runner=flaky_runner,
        report_dir=tmp_path,
    )

    with patch.object(service_module.trading_calendar, "is_market_open", return_value=True):
        first = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 16, 31))
        second = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 16, 32))

    assert first["status"] == "failed"
    assert first["error"] == "network down"
    assert second["status"] == "completed"
    assert calls["count"] == 2


def test_refresh_service_partial_apply_failure_is_retried_same_day(tmp_path):
    calls = {"count": 0}
    report_path = tmp_path / "report.json"

    def runner(db, **kwargs):
        calls["count"] += 1
        if calls["count"] == 1:
            return (
                {
                    "event_count": 1,
                    "checked_event_count": 1,
                    "triggered_code_count": 1,
                    "triggered_codes": ["688498"],
                    "backup_path": None,
                    "apply_results": [{"code": "688498", "status": "failed"}],
                },
                report_path,
            )
        return (
            {
                "event_count": 1,
                "checked_event_count": 1,
                "triggered_code_count": 0,
                "triggered_codes": [],
                "backup_path": None,
                "apply_results": [],
            },
            report_path,
        )

    service = service_module.QfqCorporateActionRefreshService(
        db=object(),
        config_provider=lambda: _config(refresh_after="16:30"),
        refresh_runner=runner,
        report_dir=tmp_path,
    )

    with patch.object(service_module.trading_calendar, "is_market_open", return_value=True):
        first = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 16, 31))
        second = service.run_once(reason="test", current_time=datetime(2026, 6, 5, 16, 32))

    assert first["status"] == "partial"
    assert first["failed_apply_count"] == 1
    assert second["status"] == "completed"
    assert calls["count"] == 2
