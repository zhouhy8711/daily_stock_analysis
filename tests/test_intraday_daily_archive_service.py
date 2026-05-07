# -*- coding: utf-8 -*-

from datetime import datetime

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


def test_intraday_daily_archive_skips_before_four_pm() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        _seed_intraday_rows(db)
        service = IntradayDailyArchiveService(db_manager=db)

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
        service = IntradayDailyArchiveService(db_manager=db)

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


def test_intraday_daily_archive_default_interval_is_thirty_minutes() -> None:
    assert DEFAULT_INTRADAY_ARCHIVE_INTERVAL_SECONDS == 30 * 60
