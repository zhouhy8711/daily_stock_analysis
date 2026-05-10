# -*- coding: utf-8 -*-
import os
import tempfile
import unittest
from datetime import date

import pandas as pd

from src.config import Config
from src.storage import DatabaseManager
from tools.backfill_a_share_daily_history import (
    backfill_one_stock,
    filter_frame_to_dates,
    split_missing_segments,
)


class _FakeDailyFetcher:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.calls = []

    def get_daily_data(self, stock_code: str, start_date=None, end_date=None, days: int = 30):
        self.calls.append(
            {
                "stock_code": stock_code,
                "start_date": start_date,
                "end_date": end_date,
                "days": days,
            }
        )
        return self.df, "FakeDailyFetcher"


class BackfillAShareDailyHistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "daily_backfill.db")
        os.environ["DATABASE_PATH"] = self._db_path
        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        DatabaseManager.reset_instance()
        Config._instance = None
        os.environ.pop("DATABASE_PATH", None)
        self._temp_dir.cleanup()

    def _save_daily(self, code: str, daily_date: date, close: float) -> None:
        self.db.save_daily_data(
            pd.DataFrame(
                [
                    {
                        "date": daily_date,
                        "open": close - 1,
                        "high": close + 1,
                        "low": close - 2,
                        "close": close,
                        "volume": 1000,
                        "amount": 10000,
                        "pct_chg": 1.0,
                    }
                ]
            ),
            code=code,
            data_source="existing",
        )

    def test_split_missing_segments_uses_trading_date_adjacency(self) -> None:
        expected = [
            date(2026, 1, 2),
            date(2026, 1, 5),
            date(2026, 1, 6),
            date(2026, 1, 7),
        ]
        existing = {date(2026, 1, 5)}

        segments = split_missing_segments(expected, existing)

        self.assertEqual(
            [(segment.start, segment.end, list(segment.dates)) for segment in segments],
            [
                (date(2026, 1, 2), date(2026, 1, 2), [date(2026, 1, 2)]),
                (date(2026, 1, 6), date(2026, 1, 7), [date(2026, 1, 6), date(2026, 1, 7)]),
            ],
        )

    def test_filter_frame_to_dates_keeps_only_target_dates(self) -> None:
        df = pd.DataFrame(
            [
                {"date": "2026-01-02", "close": 10, "volume": 1000},
                {"date": "2026-01-05", "close": 11, "volume": 1000},
            ]
        )

        filtered = filter_frame_to_dates(df, {date(2026, 1, 5)})

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered.iloc[0]["close"], 11)

    def test_backfill_one_stock_fetches_and_saves_only_missing_dates(self) -> None:
        code = "600519"
        self._save_daily(code, date(2026, 1, 2), 10.0)
        remote_df = pd.DataFrame(
            [
                {"date": "2026-01-02", "open": 1, "high": 2, "low": 1, "close": 99, "volume": 100, "amount": 1000},
                {"date": "2026-01-05", "open": 2, "high": 3, "low": 2, "close": 11, "volume": 100, "amount": 1000},
                {"date": "2026-01-06", "open": 3, "high": 4, "low": 3, "close": 12, "volume": 100, "amount": 1000},
            ]
        )
        fake_fetcher = _FakeDailyFetcher(remote_df)

        result = backfill_one_stock(
            code,
            [date(2026, 1, 2), date(2026, 1, 5), date(2026, 1, 6)],
            self.db,
            fetcher_factory=lambda: fake_fetcher,
        )

        self.assertEqual(result.status, "fetched")
        self.assertEqual(result.missing_count, 2)
        self.assertEqual(result.saved_count, 2)
        self.assertEqual(
            fake_fetcher.calls,
            [
                {
                    "stock_code": code,
                    "start_date": "2026-01-05",
                    "end_date": "2026-01-06",
                    "days": 2,
                }
            ],
        )

        rows = self.db.get_data_range(code, date(2026, 1, 2), date(2026, 1, 6))
        by_date = {row.date: row.close for row in rows}
        self.assertEqual(by_date[date(2026, 1, 2)], 10.0)
        self.assertEqual(by_date[date(2026, 1, 5)], 11.0)
        self.assertEqual(by_date[date(2026, 1, 6)], 12.0)

    def test_backfill_one_stock_skips_when_all_dates_exist(self) -> None:
        code = "000001"
        self._save_daily(code, date(2026, 1, 2), 10.0)
        fake_fetcher = _FakeDailyFetcher(pd.DataFrame())

        result = backfill_one_stock(
            code,
            [date(2026, 1, 2)],
            self.db,
            fetcher_factory=lambda: fake_fetcher,
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.missing_count, 0)
        self.assertEqual(fake_fetcher.calls, [])


if __name__ == "__main__":
    unittest.main()
