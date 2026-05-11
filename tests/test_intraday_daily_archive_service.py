# -*- coding: utf-8 -*-

from datetime import date, datetime
from types import SimpleNamespace

import pandas as pd

from src.services.intraday_daily_archive_service import (
    DEFAULT_INTRADAY_ARCHIVE_INTERVAL_SECONDS,
    IntradayDailyArchiveService,
)
from src.storage import DatabaseManager


def _seed_intraday_rows(db: DatabaseManager) -> None:
    first_time = datetime(2026, 5, 7, 10, 0, 0)
    second_time = datetime(2026, 5, 7, 14, 59, 0)
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": "600519",
                "current_price": 10.0,
                "volume": 1000,
                "amount": 10000,
                "turnover_rate": 0.5,
                "change_percent": 1.0,
                "source": "snapshot",
            }
        ],
        snapshot_id="20260507100000",
        snapshot_time=first_time,
    )
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": "600519",
                "current_price": 12.0,
                "volume": 1300,
                "amount": 15000,
                "turnover_rate": 0.8,
                "change_percent": 2.0,
                "source": "snapshot",
            }
        ],
        snapshot_id="20260507145900",
        snapshot_time=second_time,
    )


def _seed_daily_history_for_chip(db: DatabaseManager, target_date: date) -> None:
    db.save_daily_data(
        pd.DataFrame([
            {
                "date": date(2026, 5, 6),
                "open": 9.5,
                "high": 10.5,
                "low": 9.2,
                "close": 10.0,
                "volume": 1000,
                "amount": 10000,
                "turnover_rate": 1.2,
            }
        ]),
        "600519",
        data_source="EfinanceFetcher",
    )
    db.save_daily_data(
        pd.DataFrame([
            {
                "date": target_date,
                "open": 10.0,
                "high": 12.0,
                "low": 9.8,
                "close": 12.0,
                "volume": 1300,
                "amount": 15000,
                "pct_chg": 20.0,
                "turnover_rate": 0.8,
            }
        ]),
        "600519",
        data_source="intraday_hot_table",
    )


def test_intraday_daily_archive_skips_before_four_pm() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        _seed_intraday_rows(db)
        service = IntradayDailyArchiveService(db_manager=db, quote_loader=lambda code: None)

        result = service.run_once(current_time=datetime(2026, 5, 7, 15, 30, 0))

        assert result["status"] == "skipped"
        assert result["reason"] == "before_archive_time"
        assert db.get_intraday_minute_codes(trade_date=datetime(2026, 5, 7).date()) == ["600519"]
        assert not db.has_today_data("600519", datetime(2026, 5, 7).date())
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_archives_and_purges_after_four_pm() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 7).date()
    try:
        _seed_intraday_rows(db)
        service = IntradayDailyArchiveService(db_manager=db, quote_loader=lambda code: None)

        result = service.run_once(current_time=datetime(2026, 5, 7, 16, 5, 0))

        daily_rows = db.get_latest_data("600519", days=1)
        assert result["status"] == "completed"
        assert result["scanned_code_count"] == 1
        assert result["archived_code_count"] == 1
        assert result["purged_row_count"] == 2
        assert result["failed_code_count"] == 0
        assert db.get_intraday_minute_codes(trade_date=target_date) == []
        assert len(daily_rows) == 1
        assert daily_rows[0].date == target_date
        assert daily_rows[0].open == 10.0
        assert daily_rows[0].close == 12.0
        assert daily_rows[0].data_source == "intraday_hot_table"
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_skips_insert_when_daily_tables_complete() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 7).date()

    def fail_quote_loader(code):
        raise AssertionError("completed daily rows should not reload quote")

    try:
        _seed_intraday_rows(db)
        db.save_daily_data(
            pd.DataFrame([
                {
                    "date": target_date,
                    "open": 11.0,
                    "high": 13.0,
                    "low": 10.5,
                    "close": 13.0,
                    "volume": 1800,
                    "amount": 22000,
                    "pct_chg": 8.3,
                    "turnover_rate": 1.1,
                    "pe_ratio": 22.0,
                    "total_mv": 1_300_000_000.0,
                    "circ_mv": 975_000_000.0,
                    "total_shares": 100_000_000.0,
                    "float_shares": 75_000_000.0,
                }
            ]),
            "600519",
            data_source="EfinanceFetcher",
        )
        db.save_chip_daily_snapshots(
            "600519",
            [
                {
                    "date": target_date,
                    "profit_ratio": 0.8,
                    "avg_cost": 10.5,
                    "cost_90_low": 9.0,
                    "cost_90_high": 12.0,
                    "concentration_90": 0.25,
                    "cost_70_low": 9.5,
                    "cost_70_high": 11.5,
                    "concentration_70": 0.18,
                    "distribution": [{"price": 10.5, "percent": 1.0}],
                    "chip_status": "available",
                }
            ],
            data_source="local_chip_model:EfinanceFetcher",
        )
        service = IntradayDailyArchiveService(db_manager=db, quote_loader=fail_quote_loader)

        result = service.run_once(current_time=datetime(2026, 5, 7, 16, 5, 0))

        daily_rows = db.get_data_range("600519", target_date, target_date)
        assert result["status"] == "skipped"
        assert result["reason"] == "daily_archive_already_complete"
        assert result["archived_code_count"] == 0
        assert result["skipped_completed_count"] == 1
        assert result["purged_row_count"] == 2
        assert db.get_intraday_minute_codes(trade_date=target_date) == []
        assert daily_rows[0].close == 13.0
        assert daily_rows[0].data_source == "EfinanceFetcher"
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_backfills_daily_valuation_after_archive(monkeypatch) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 7).date()
    quote = SimpleNamespace(
        price=12.0,
        pe_ratio=20.0,
        total_mv=1_200_000_000.0,
        circ_mv=900_000_000.0,
    )
    seen_codes = []

    from src.services.stock_service import StockService

    def fake_get_realtime_quote(self, code):
        seen_codes.append(code)
        return quote

    monkeypatch.setattr(StockService, "get_realtime_quote", fake_get_realtime_quote)
    try:
        _seed_intraday_rows(db)
        service = IntradayDailyArchiveService(db_manager=db)

        result = service.run_once(current_time=datetime(2026, 5, 7, 16, 5, 0))

        daily_rows = db.get_data_range("600519", target_date, target_date)
        assert result["status"] == "completed"
        assert result["valuation_refreshed_count"] == 1
        assert seen_codes == ["600519"]
        assert len(daily_rows) == 1
        assert daily_rows[0].pe_ratio == 20.0
        assert daily_rows[0].total_mv == 1_200_000_000.0
        assert daily_rows[0].circ_mv == 900_000_000.0
        assert daily_rows[0].total_shares == 100_000_000.0
        assert daily_rows[0].float_shares == 75_000_000.0
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_skips_closed_market_dates() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 9).date()
    try:
        db.save_intraday_quote_samples(
            [
                {
                    "stock_code": "600519",
                    "current_price": 12.0,
                    "volume": 1300,
                    "amount": 15000,
                    "turnover_rate": 0.8,
                    "change_percent": 2.0,
                    "source": "snapshot",
                }
            ],
            snapshot_id="20260509100000",
            snapshot_time=datetime(2026, 5, 9, 10, 0, 0),
        )
        service = IntradayDailyArchiveService(db_manager=db)

        result = service.run_once(current_time=datetime(2026, 5, 9, 16, 5, 0))

        assert result["status"] == "skipped"
        assert result["reason"] == "market_closed"
        assert db.get_intraday_minute_codes(trade_date=target_date) == ["600519"]
        assert not db.has_today_data("600519", target_date)
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_refreshes_existing_daily_valuation_without_hot_rows(monkeypatch) -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 7).date()
    quote = SimpleNamespace(
        price=12.0,
        pe_ratio=20.0,
        total_mv=1_200_000_000.0,
        circ_mv=900_000_000.0,
    )

    from src.services.stock_service import StockService

    monkeypatch.setattr(StockService, "get_realtime_quote", lambda self, code: quote)
    try:
        db.save_daily_data(
            pd.DataFrame([
                {
                    "date": target_date,
                    "open": 10.0,
                    "high": 12.0,
                    "low": 9.8,
                    "close": 12.0,
                    "volume": 1300,
                    "amount": 15000,
                    "pct_chg": 20.0,
                }
            ]),
            "600519",
            data_source="intraday_hot_table",
        )
        service = IntradayDailyArchiveService(db_manager=db)

        result = service.run_once(current_time=datetime(2026, 5, 7, 16, 5, 0))

        daily_rows = db.get_data_range("600519", target_date, target_date)
        assert result["status"] == "completed"
        assert result["archived_code_count"] == 0
        assert result["valuation_refreshed_count"] == 1
        assert daily_rows[0].pe_ratio == 20.0
        assert daily_rows[0].total_mv == 1_200_000_000.0
        assert daily_rows[0].circ_mv == 900_000_000.0
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_syncs_missing_chip_daily_without_hot_rows() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    target_date = datetime(2026, 5, 7).date()
    try:
        _seed_daily_history_for_chip(db, target_date)
        service = IntradayDailyArchiveService(db_manager=db, quote_loader=lambda code: None)

        result = service.run_once(current_time=datetime(2026, 5, 7, 16, 5, 0))

        rows = db.get_chip_daily_range("600519", target_date, target_date)
        assert result["status"] == "completed"
        assert result["archived_code_count"] == 0
        assert result["valuation_refreshed_count"] == 0
        assert result["chip_synced_count"] == 1
        assert len(rows) == 1
        assert rows[0]["date"] == target_date.isoformat()
    finally:
        DatabaseManager.reset_instance()


def test_intraday_daily_archive_default_interval_is_thirty_minutes() -> None:
    assert DEFAULT_INTRADAY_ARCHIVE_INTERVAL_SECONDS == 30 * 60
