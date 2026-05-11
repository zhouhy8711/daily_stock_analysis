# -*- coding: utf-8 -*-
"""
===================================
get_latest_data 测试
===================================

职责：
1. 验证 get_latest_data 方法
2. 测试返回数据按日期降序排列
3. 测试 days 参数限制
"""

import os
import tempfile
import unittest
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from src.config import Config
from src.services.stock_service import StockService
from src.storage import DatabaseManager, StockDaily


class _FakeDailyHistoryManager:
    def __init__(self, df: pd.DataFrame):
        self.df = df
        self.daily_calls = 0
        self.last_daily_kwargs = None

    def get_daily_data(self, stock_code: str, start_date=None, end_date=None, days: int = 30):
        self.daily_calls += 1
        self.last_daily_kwargs = {
            "stock_code": stock_code,
            "start_date": start_date,
            "end_date": end_date,
            "days": days,
        }
        return self.df, "FakeDailyFetcher"

    def get_stock_name(self, stock_code: str):
        return "贵州茅台"

    def get_realtime_quote(self, stock_code: str, **_kwargs):
        return SimpleNamespace(
            code=stock_code,
            name="贵州茅台",
            price=123.45,
            pe_ratio=24.0,
            total_mv=1_234_500_000,
            circ_mv=987_600_000,
            total_shares=10_000_000,
            float_shares=8_000_000,
        )


class _FakeRealtimeQuoteManager:
    def get_realtime_quote(self, stock_code: str):
        return SimpleNamespace(
            code=stock_code,
            name="贵州茅台",
            price=123.45,
            change_amount=1.2,
            change_pct=0.98,
            open_price=122.0,
            high=125.0,
            low=121.0,
            pre_close=122.25,
            volume=1000000,
            amount=123450000,
            after_hours_volume=None,
            after_hours_amount=None,
            volume_ratio=None,
            turnover_rate=None,
            amplitude=None,
            pe_ratio=None,
            total_mv=None,
            circ_mv=None,
            total_shares=None,
            float_shares=None,
            price_speed=None,
            limit_up_price=None,
            limit_down_price=None,
            entrust_ratio=None,
            source="test",
        )


class GetLatestDataTestCase(unittest.TestCase):
    """get_latest_data 方法测试"""

    def setUp(self) -> None:
        """Initialize an isolated database for each test case."""
        self._temp_dir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._temp_dir.name, "test_get_latest_data.db")
        os.environ["DATABASE_PATH"] = self._db_path

        Config._instance = None
        DatabaseManager.reset_instance()
        self.db = DatabaseManager.get_instance()

    def tearDown(self) -> None:
        """Clean up resources."""
        DatabaseManager.reset_instance()
        self._temp_dir.cleanup()

    def _insert_stock_data(self, code: str, days_ago: int, close: float) -> None:
        """插入测试用股票数据"""
        target_date = date.today() - timedelta(days=days_ago)
        df = pd.DataFrame([{
            'date': target_date,
            'open': close - 1,
            'high': close + 1,
            'low': close - 2,
            'close': close,
            'volume': 1000000,
            'amount': 10000000,
            'pct_chg': 1.5,
        }])
        self.db.save_daily_data(df, code, data_source="TestData")

    def test_get_latest_data_returns_empty_when_no_data(self) -> None:
        """无数据时返回空列表"""
        result = self.db.get_latest_data("999999", days=2)
        self.assertEqual(result, [])

    def test_get_latest_data_returns_correct_count(self) -> None:
        """返回正确数量的数据"""
        # 插入5天数据
        for i in range(5):
            self._insert_stock_data("600519", days_ago=i, close=100.0 + i)

        # 请求2天数据
        result = self.db.get_latest_data("600519", days=2)
        self.assertEqual(len(result), 2)

        # 请求5天数据
        result = self.db.get_latest_data("600519", days=5)
        self.assertEqual(len(result), 5)

    def test_get_latest_data_ordered_by_date_desc(self) -> None:
        """验证数据按日期降序排列"""
        # 插入3天数据
        for i in range(3):
            self._insert_stock_data("600519", days_ago=i, close=100.0 + i)

        result = self.db.get_latest_data("600519", days=3)

        # 验证日期降序（最新日期在前）
        self.assertEqual(len(result), 3)
        self.assertGreater(result[0].date, result[1].date)
        self.assertGreater(result[1].date, result[2].date)

    def test_get_latest_data_filters_by_code(self) -> None:
        """验证按股票代码过滤"""
        # 插入不同股票的数据
        self._insert_stock_data("600519", days_ago=0, close=100.0)
        self._insert_stock_data("000001", days_ago=0, close=50.0)

        result = self.db.get_latest_data("600519", days=5)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].code, "600519")

    def test_save_daily_data_batch_upsert_updates_existing_rows_and_keeps_insert_count(self) -> None:
        base_date = date(2026, 1, 2)
        first_batch = pd.DataFrame(
            [
                {
                    "date": base_date,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000.0,
                    "amount": 100000.0,
                    "pct_chg": 0.5,
                    "ma5": 98.0,
                },
                {
                    "date": base_date + timedelta(days=1),
                    "open": 101.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.0,
                    "volume": 1100.0,
                    "amount": 111000.0,
                    "pct_chg": 1.0,
                    "ma5": 99.0,
                },
            ]
        )
        second_batch = pd.DataFrame(
            [
                {
                    "date": base_date,
                    "open": 120.0,
                    "high": 121.0,
                    "low": 119.0,
                    "close": 120.0,
                    "volume": 2200.0,
                    "amount": 264000.0,
                    "pct_chg": 2.0,
                    "ma5": 110.0,
                    "volume_ratio": 1.8,
                },
                {
                    "date": base_date + timedelta(days=1),
                    "open": 121.0,
                    "high": 122.0,
                    "low": 120.0,
                    "close": 121.0,
                    "volume": 2300.0,
                    "amount": 278300.0,
                    "pct_chg": 1.5,
                    "ma5": 111.0,
                    "volume_ratio": 1.6,
                },
                {
                    "date": base_date + timedelta(days=2),
                    "open": 122.0,
                    "high": 123.0,
                    "low": 121.0,
                    "close": 122.0,
                    "volume": 2400.0,
                    "amount": 292800.0,
                    "pct_chg": 1.2,
                    "ma5": 112.0,
                    "volume_ratio": 1.4,
                },
            ]
        )

        saved_first = self.db.save_daily_data(first_batch, "600519", data_source="batch-1")
        saved_second = self.db.save_daily_data(second_batch, "600519", data_source="batch-2")

        self.assertEqual(saved_first, 2)
        self.assertEqual(saved_second, 1)

        rows = self.db.get_latest_data("600519", days=5)
        by_date = {row.date: row for row in rows}

        self.assertEqual(len(by_date), 3)
        self.assertAlmostEqual(by_date[base_date].close, 120.0, places=6)
        self.assertAlmostEqual(by_date[base_date].volume_ratio or 0.0, 1.8, places=6)
        self.assertEqual(by_date[base_date].data_source, "batch-2")
        self.assertAlmostEqual(by_date[base_date + timedelta(days=2)].close, 122.0, places=6)

    def test_history_data_uses_existing_daily_db_without_external_fetch(self) -> None:
        target_date = date.today() - timedelta(days=1)
        self._insert_stock_data("600519", days_ago=1, close=1688.5)

        with (
            patch(
                "data_provider.base.DataFetcherManager",
                side_effect=AssertionError("external fetch should not run"),
            ),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=None,
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=1)

        self.assertEqual(result["period"], "daily")
        self.assertEqual(result["stock_name"], "贵州茅台")
        self.assertEqual(len(result["data"]), 1)
        self.assertAlmostEqual(result["data"][0]["close"], 1688.5, places=6)

    def test_daily_cache_target_date_uses_effective_trading_date(self) -> None:
        effective_date = date(2026, 5, 11)

        with (
            patch("src.services.stock_service.trading_calendar.get_market_for_stock", return_value="cn"),
            patch(
                "src.services.stock_service.trading_calendar.get_effective_trading_date",
                return_value=effective_date,
            ) as effective_mock,
        ):
            result = StockService._resolve_daily_cache_target_date("688256")

        self.assertEqual(result, effective_date)
        effective_mock.assert_called_once_with("cn")

    def test_history_data_refreshes_stale_daily_db_before_returning(self) -> None:
        stale_date = date.today() - timedelta(days=5)
        target_date = date.today() - timedelta(days=1)
        self.db.save_daily_data(pd.DataFrame([{
            "date": stale_date,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000000,
            "amount": 10000000,
            "pct_chg": 1.5,
        }]), "600519", data_source="stale-cache")

        fetched_df = pd.DataFrame([{
            "date": target_date,
            "open": 120.0,
            "high": 125.0,
            "low": 119.0,
            "close": 123.45,
            "volume": 1000000,
            "amount": 123450000,
            "pct_chg": 0.98,
        }])
        manager = _FakeDailyHistoryManager(fetched_df)

        with (
            patch("data_provider.base.DataFetcherManager", return_value=manager),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=None,
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=1)

        self.assertEqual(manager.daily_calls, 1)
        self.assertEqual(manager.last_daily_kwargs["start_date"], (stale_date + timedelta(days=1)).isoformat())
        self.assertEqual(manager.last_daily_kwargs["end_date"], target_date.isoformat())
        self.assertEqual(result["stock_name"], "贵州茅台")
        self.assertEqual(result["data"][0]["date"], target_date.isoformat())
        self.assertAlmostEqual(result["data"][0]["close"], 123.45, places=6)

        rows = self.db.get_data_range("600519", target_date, target_date)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].close, 123.45, places=6)
        self.assertAlmostEqual(rows[0].total_mv, 1_234_500_000, places=6)
        self.assertAlmostEqual(rows[0].circ_mv, 987_600_000, places=6)
        self.assertAlmostEqual(rows[0].pe_ratio, 24.0, places=6)
        self.assertAlmostEqual(rows[0].turnover_rate, 12.5, places=6)
        self.assertEqual(rows[0].data_source, "FakeDailyFetcher")

    def test_history_data_fetches_full_window_when_daily_db_has_too_few_rows(self) -> None:
        target_date = date.today() - timedelta(days=1)
        self.db.save_daily_data(pd.DataFrame([{
            "date": target_date,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000000,
            "amount": 10000000,
            "pct_chg": 1.5,
        }]), "600519", data_source="short-cache")

        fetched_df = pd.DataFrame([
            {
                "date": target_date - timedelta(days=2),
                "open": 118.0,
                "high": 120.0,
                "low": 117.0,
                "close": 119.0,
                "volume": 900000,
                "amount": 107100000,
                "pct_chg": 0.5,
            },
            {
                "date": target_date - timedelta(days=1),
                "open": 119.0,
                "high": 122.0,
                "low": 118.0,
                "close": 121.0,
                "volume": 950000,
                "amount": 114950000,
                "pct_chg": 1.68,
            },
            {
                "date": target_date,
                "open": 121.0,
                "high": 124.0,
                "low": 120.0,
                "close": 123.0,
                "volume": 1000000,
                "amount": 123000000,
                "pct_chg": 1.65,
            },
        ])
        manager = _FakeDailyHistoryManager(fetched_df)

        with (
            patch("data_provider.base.DataFetcherManager", return_value=manager),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=None,
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=3)

        self.assertEqual(manager.daily_calls, 1)
        self.assertIsNone(manager.last_daily_kwargs["start_date"])
        self.assertEqual(manager.last_daily_kwargs["end_date"], target_date.isoformat())
        self.assertEqual(manager.last_daily_kwargs["days"], 3)
        self.assertEqual([item["date"] for item in result["data"]], [
            (target_date - timedelta(days=2)).isoformat(),
            (target_date - timedelta(days=1)).isoformat(),
            target_date.isoformat(),
        ])

        rows = self.db.get_data_range("600519", target_date - timedelta(days=2), target_date)
        self.assertEqual(len(rows), 3)
        self.assertAlmostEqual(rows[-1].close, 123.0, places=6)

    def test_history_data_fetches_and_upserts_when_daily_db_is_missing(self) -> None:
        target_date = date.today() - timedelta(days=1)
        fetched_df = pd.DataFrame([{
            "date": target_date,
            "open": 120.0,
            "high": 125.0,
            "low": 119.0,
            "close": 123.45,
            "volume": 1000000,
            "amount": 123450000,
            "pct_chg": 0.98,
        }])
        manager = _FakeDailyHistoryManager(fetched_df)

        with (
            patch("data_provider.base.DataFetcherManager", return_value=manager),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=None,
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=1)

        self.assertEqual(manager.daily_calls, 1)
        self.assertEqual(manager.last_daily_kwargs["end_date"], target_date.isoformat())
        self.assertEqual(result["stock_name"], "贵州茅台")
        self.assertEqual(result["data"][0]["date"], target_date.isoformat())

        rows = self.db.get_data_range("600519", target_date, target_date)
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(rows[0].close, 123.45, places=6)
        self.assertEqual(rows[0].data_source, "FakeDailyFetcher")

    def test_history_data_fetches_and_syncs_chip_cache_from_same_daily_frame(self) -> None:
        target_date = date.today() - timedelta(days=1)
        previous_date = target_date - timedelta(days=1)
        fetched_df = pd.DataFrame([
            {
                "date": previous_date,
                "open": 120.0,
                "high": 125.0,
                "low": 119.0,
                "close": 122.0,
                "volume": 1000000,
                "amount": 122000000,
                "pct_chg": 0.5,
                "turnover_rate": 1.1,
            },
            {
                "date": target_date,
                "open": 122.0,
                "high": 126.0,
                "low": 121.0,
                "close": 123.45,
                "volume": 1100000,
                "amount": 135795000,
                "pct_chg": 0.98,
                "turnover_rate": 1.4,
            },
        ])
        manager = _FakeDailyHistoryManager(fetched_df)

        with (
            patch("data_provider.base.DataFetcherManager", return_value=manager),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=None,
            ),
        ):
            StockService().get_history_data("600519", period="daily", days=2)

        rows = self.db.get_chip_daily_range("600519", previous_date, target_date)
        self.assertEqual([item["date"] for item in rows], [
            previous_date.isoformat(),
            target_date.isoformat(),
        ])
        self.assertTrue(rows[-1]["source"].startswith("local_chip_model:FakeDailyFetcher"))

    def test_daily_history_db_only_uses_partial_merged_db_rows_without_remote(self) -> None:
        first_date = date.today() - timedelta(days=5)
        second_date = date.today() - timedelta(days=4)
        self.db.save_daily_data(pd.DataFrame([{
            "date": first_date,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 1000000,
            "amount": 100500000,
            "pct_chg": 0.5,
        }]), "600519", data_source="TestData")
        self.db.save_daily_data(pd.DataFrame([{
            "date": second_date,
            "open": 101.0,
            "high": 102.0,
            "low": 100.0,
            "close": 101.5,
            "volume": 1100000,
            "amount": 111650000,
            "pct_chg": 1.0,
        }]), "600519.SH", data_source="TestData")

        with (
            patch(
                "data_provider.base.DataFetcherManager",
                side_effect=AssertionError("db_only must not fetch remotely"),
            ),
            patch(
                "src.services.stock_service.StockService._augment_daily_history_with_realtime",
                side_effect=AssertionError("db_only must not append realtime quotes"),
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=20, data_policy="db_only")

        self.assertEqual(result["data_source"], "db_cache")
        self.assertEqual([item["date"] for item in result["data"]], [
            first_date.isoformat(),
            second_date.isoformat(),
        ])

    def test_realtime_quote_does_not_write_daily_history_cache(self) -> None:
        with patch("data_provider.base.DataFetcherManager", return_value=_FakeRealtimeQuoteManager()):
            result = StockService().get_realtime_quote("600519")

        self.assertIsNotNone(result)
        self.assertEqual(self.db.get_latest_data("600519", days=5), [])

    def test_daily_history_appends_realtime_today_from_quote_cache_without_db_write(self) -> None:
        from src.services.stock_service import _clear_realtime_quote_cache

        _clear_realtime_quote_cache()
        target_date = date.today() - timedelta(days=1)
        realtime_date = date.today()
        self._insert_stock_data("600519", days_ago=1, close=122.25)

        with (
            patch("data_provider.base.DataFetcherManager", return_value=_FakeRealtimeQuoteManager()),
            patch(
                "src.services.stock_service.StockService._resolve_daily_cache_target_date",
                return_value=target_date,
            ),
            patch(
                "src.services.stock_service.StockService._resolve_realtime_daily_date",
                return_value=realtime_date,
            ),
        ):
            result = StockService().get_history_data("600519", period="daily", days=1)

        self.assertEqual([item["date"] for item in result["data"]], [
            target_date.isoformat(),
            realtime_date.isoformat(),
        ])
        today_point = result["data"][-1]
        self.assertAlmostEqual(today_point["open"], 122.0, places=6)
        self.assertAlmostEqual(today_point["high"], 125.0, places=6)
        self.assertAlmostEqual(today_point["low"], 121.0, places=6)
        self.assertAlmostEqual(today_point["close"], 123.45, places=6)
        self.assertAlmostEqual(today_point["change_percent"], 0.98, places=6)
        self.assertEqual(self.db.get_data_range("600519", realtime_date, realtime_date), [])

        with patch(
            "data_provider.base.DataFetcherManager",
            side_effect=AssertionError("cached realtime quote should be reused"),
        ):
            cached_quote = StockService().get_realtime_quote("600519")

        self.assertIsNotNone(cached_quote)
        self.assertAlmostEqual(cached_quote["current_price"], 123.45, places=6)


if __name__ == "__main__":
    unittest.main()
