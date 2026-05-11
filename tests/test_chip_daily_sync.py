# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import date, timedelta
from unittest.mock import MagicMock

import pandas as pd

from src.config import Config
from src.services.chip_daily_sync import (
    ensure_chip_daily_for_dates,
    sync_chip_daily_from_history,
)
from src.storage import DatabaseManager


class ChipDailySyncTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "chip_daily_sync.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    @staticmethod
    def _history_frame() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "date": "2026-01-02",
                    "open": 9.0,
                    "high": 10.5,
                    "low": 8.8,
                    "close": 10.0,
                    "volume": 1000,
                    "amount": 10000,
                    "turnover_rate": 1.2,
                },
                {
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 11.5,
                    "low": 9.8,
                    "close": 11.0,
                    "volume": 1200,
                    "amount": 13200,
                    "turnover_rate": 1.6,
                },
            ]
        )

    def test_sync_chip_daily_from_same_history_frame_without_network(self) -> None:
        saved = sync_chip_daily_from_history(
            self.db,
            "600519",
            self._history_frame(),
            data_source="unit_daily",
            target_dates=[date(2026, 1, 5)],
        )

        rows = self.db.get_chip_daily_range("600519", date(2026, 1, 1), date(2026, 1, 10))

        self.assertEqual(saved, 1)
        self.assertEqual([row["date"] for row in rows], ["2026-01-05"])
        self.assertTrue(rows[0]["source"].startswith("local_chip_model:unit_daily"))
        self.assertGreater(rows[0]["avg_cost"], 0)

    def test_ensure_chip_daily_skips_fetch_when_cache_exists(self) -> None:
        self.db.save_chip_daily_snapshots(
            "600519",
            [
                {
                    "date": "2026-01-05",
                    "profit_ratio": 0.6,
                    "avg_cost": 10.5,
                    "distribution": [{"price": 10.5, "percent": 1.0}],
                }
            ],
            data_source="unit",
        )
        manager = MagicMock()

        saved = ensure_chip_daily_for_dates(
            self.db,
            manager,
            "600519",
            [date(2026, 1, 5)],
        )

        self.assertEqual(saved, 0)
        manager.get_daily_data.assert_not_called()

    def test_ensure_chip_daily_fetches_missing_window_and_upserts(self) -> None:
        target_date = date(2026, 1, 5)
        manager = MagicMock()
        manager.get_daily_data.return_value = (self._history_frame(), "FakeDailyFetcher")

        saved = ensure_chip_daily_for_dates(
            self.db,
            manager,
            "600519",
            [target_date],
        )

        rows = self.db.get_chip_daily_range("600519", date(2026, 1, 1), date(2026, 1, 10))

        self.assertEqual(saved, 1)
        manager.get_daily_data.assert_called_once_with(
            "600519",
            start_date=(target_date - timedelta(days=365)).isoformat(),
            end_date=target_date.isoformat(),
            days=366,
        )
        self.assertEqual(rows[0]["date"], "2026-01-05")


if __name__ == "__main__":
    unittest.main()
