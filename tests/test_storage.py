# -*- coding: utf-8 -*-
import unittest
import sys
import os
import tempfile
import threading
from datetime import date, datetime
from unittest.mock import patch

import pandas as pd
from sqlalchemy import and_, select
from sqlalchemy.sql import func

# Ensure src module can be imported
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from src.config import Config
from src.storage import DatabaseManager, StockDaily

class TestStorage(unittest.TestCase):
    
    def test_parse_sniper_value(self):
        """测试解析狙击点位数值"""
        
        # 1. 正常数值
        self.assertEqual(DatabaseManager._parse_sniper_value(100), 100.0)
        self.assertEqual(DatabaseManager._parse_sniper_value(100.5), 100.5)
        self.assertEqual(DatabaseManager._parse_sniper_value("100"), 100.0)
        self.assertEqual(DatabaseManager._parse_sniper_value("100.5"), 100.5)
        
        # 2. 包含中文描述和"元"
        self.assertEqual(DatabaseManager._parse_sniper_value("建议在 100 元附近买入"), 100.0)
        self.assertEqual(DatabaseManager._parse_sniper_value("价格：100.5元"), 100.5)
        
        # 3. 包含干扰数字（修复的Bug场景）
        # 之前 "MA5" 会被错误提取为 5.0，现在应该提取 "元" 前面的 100
        text_bug = "无法给出。需等待MA5数据恢复，在股价回踩MA5且乖离率<2%时考虑100元"
        self.assertEqual(DatabaseManager._parse_sniper_value(text_bug), 100.0)
        
        # 4. 更多干扰场景
        text_complex = "MA10为20.5，建议在30元买入"
        self.assertEqual(DatabaseManager._parse_sniper_value(text_complex), 30.0)
        
        text_multiple = "支撑位10元，阻力位20元" # 应该提取最后一个"元"前面的数字，即20，或者更复杂的逻辑？
        # 当前逻辑是找最后一个冒号，然后找之后的第一个"元"，提取中间的数字。
        # 测试没有冒号的情况
        self.assertEqual(DatabaseManager._parse_sniper_value("30元"), 30.0)
        
        # 测试多个数字在"元"之前
        self.assertEqual(DatabaseManager._parse_sniper_value("MA5 10 20元"), 20.0)
        
        # 5. Fallback: no "元" character — extracts last non-MA number
        self.assertEqual(DatabaseManager._parse_sniper_value("102.10-103.00（MA5附近）"), 103.0)
        self.assertEqual(DatabaseManager._parse_sniper_value("97.62-98.50（MA10附近）"), 98.5)
        self.assertEqual(DatabaseManager._parse_sniper_value("93.40下方（MA20支撑）"), 93.4)
        self.assertEqual(DatabaseManager._parse_sniper_value("108.00-110.00（前期高点阻力）"), 110.0)

        # 6. 无效输入
        self.assertIsNone(DatabaseManager._parse_sniper_value(None))
        self.assertIsNone(DatabaseManager._parse_sniper_value(""))
        self.assertIsNone(DatabaseManager._parse_sniper_value("没有数字"))
        self.assertIsNone(DatabaseManager._parse_sniper_value("MA5但没有元"))

        # 7. 回归：括号内技术指标数字不应被提取
        self.assertNotEqual(DatabaseManager._parse_sniper_value("1.52-1.53 (回踩MA5/10附近)"), 10.0)
        self.assertNotEqual(DatabaseManager._parse_sniper_value("1.55-1.56(MA5/M20支撑)"), 20.0)
        self.assertNotEqual(DatabaseManager._parse_sniper_value("1.49-1.50(MA60附近企稳)"), 60.0)
        # 验证正确值在区间内
        self.assertIn(DatabaseManager._parse_sniper_value("1.52-1.53 (回踩MA5/10附近)"), [1.52, 1.53])
        self.assertIn(DatabaseManager._parse_sniper_value("1.55-1.56(MA5/M20支撑)"), [1.55, 1.56])
        self.assertIn(DatabaseManager._parse_sniper_value("1.49-1.50(MA60附近企稳)"), [1.49, 1.50])

    def test_get_chat_sessions_prefix_is_scoped_by_colon_boundary(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")

        db.save_conversation_message("telegram_12345:chat", "user", "first user")
        db.save_conversation_message("telegram_123456:chat", "user", "second user")

        sessions = db.get_chat_sessions(session_prefix="telegram_12345")

        self.assertEqual(len(sessions), 1)
        self.assertEqual(sessions[0]["session_id"], "telegram_12345:chat")

        DatabaseManager.reset_instance()

    def test_get_chat_sessions_can_include_legacy_exact_session_id(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")

        db.save_conversation_message("feishu_u1", "user", "legacy chat")
        db.save_conversation_message("feishu_u1:ask_600519", "user", "ask session")

        sessions = db.get_chat_sessions(
            session_prefix="feishu_u1:",
            extra_session_ids=["feishu_u1"],
        )

        self.assertEqual({item["session_id"] for item in sessions}, {"feishu_u1", "feishu_u1:ask_600519"})

        DatabaseManager.reset_instance()

    def test_save_daily_data_persists_optional_market_metrics_and_keeps_existing_on_null_update(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")
        trade_date = date(2026, 1, 19)

        db.save_daily_data(
            pd.DataFrame([
                {
                    "date": trade_date,
                    "open": 29.27,
                    "high": 29.86,
                    "low": 29.0,
                    "close": 29.6,
                    "volume": 10000,
                    "amount": 29600000,
                    "pct_chg": 1.13,
                    "turnover_rate": 5.72,
                    "pe_ratio": 36.47,
                    "total_mv": 2_995_100_000,
                    "circ_mv": 2_862_600_000,
                    "total_shares": 101_185_810,
                    "float_shares": 96_709_459,
                }
            ]),
            "002859",
            data_source="TestFetcher",
        )
        db.save_daily_data(
            pd.DataFrame([
                {
                    "date": trade_date,
                    "open": 29.30,
                    "high": 30.00,
                    "low": 29.1,
                    "close": 29.8,
                    "volume": 12000,
                    "amount": 35760000,
                    "pct_chg": 1.8,
                }
            ]),
            "002859",
            data_source="NoMetricFetcher",
        )

        row = db.get_data_range("002859", trade_date, trade_date)[0]
        self.assertEqual(row.close, 29.8)
        self.assertEqual(row.turnover_rate, 5.72)
        self.assertEqual(row.pe_ratio, 36.47)
        self.assertEqual(row.total_mv, 2_995_100_000)
        self.assertEqual(row.circ_mv, 2_862_600_000)
        self.assertEqual(row.total_shares, 101_185_810)
        self.assertEqual(row.float_shares, 96_709_459)
        DatabaseManager.reset_instance()

    def test_intraday_minute_hot_table_upserts_and_archives_to_daily(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")
        snapshot_time = datetime(2026, 5, 7, 10, 30, 12)

        first = db.save_intraday_quote_samples(
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
            snapshot_id="20260507103012",
            snapshot_time=snapshot_time,
        )
        second = db.save_intraday_quote_samples(
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
            snapshot_id="20260507103030",
            snapshot_time=snapshot_time.replace(second=30),
        )

        minute_df = db.get_intraday_minute_data("600519", trade_date=snapshot_time.date())

        self.assertEqual(first["saved_count"], 1)
        self.assertEqual(second["saved_count"], 1)
        self.assertEqual(len(minute_df), 1)
        row = minute_df.iloc[0]
        self.assertEqual(row["open"], 10.0)
        self.assertEqual(row["high"], 12.0)
        self.assertEqual(row["low"], 10.0)
        self.assertEqual(row["close"], 12.0)
        self.assertEqual(row["snapshot_id"], "20260507103030")

        archived = db.archive_intraday_minutes_to_daily(trade_date=snapshot_time.date(), codes=["600519"])
        daily_rows = db.get_latest_data("600519", days=1)

        self.assertEqual(archived, 1)
        self.assertEqual(len(daily_rows), 1)
        self.assertEqual(daily_rows[0].open, 10.0)
        self.assertEqual(daily_rows[0].close, 12.0)
        self.assertEqual(daily_rows[0].data_source, "intraday_hot_table")

        DatabaseManager.reset_instance()

    def test_intraday_quote_samples_normalize_raw_share_volume_before_archive(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")
        first_time = datetime(2026, 5, 7, 10, 0, 0)
        second_time = datetime(2026, 5, 7, 10, 1, 0)

        try:
            db.save_intraday_quote_samples(
                [
                    {
                        "stock_code": "000001",
                        "current_price": 10.0,
                        "volume": 100_000,
                        "amount": 1_000_000,
                        "source": "snapshot",
                    }
                ],
                snapshot_id="20260507100000",
                snapshot_time=first_time,
            )
            db.save_intraday_quote_samples(
                [
                    {
                        "stock_code": "000001",
                        "current_price": 10.2,
                        "volume": 130_000,
                        "amount": 1_306_000,
                        "source": "snapshot",
                    }
                ],
                snapshot_id="20260507100100",
                snapshot_time=second_time,
            )

            minute_df = db.get_intraday_minute_data("000001", trade_date=first_time.date())
            archived = db.archive_intraday_minutes_to_daily(trade_date=first_time.date(), codes=["000001"])
            daily_rows = db.get_latest_data("000001", days=1)

            self.assertEqual(list(minute_df["volume"]), [1000.0, 300.0])
            self.assertEqual(archived, 1)
            self.assertEqual(daily_rows[0].volume, 1300.0)
        finally:
            DatabaseManager.reset_instance()

    def test_file_sqlite_enables_wal_and_busy_timeout(self):
        temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(temp_dir.name, "sqlite_pragmas.db")
        original_env = {
            "DATABASE_PATH": os.environ.get("DATABASE_PATH"),
            "SQLITE_BUSY_TIMEOUT_MS": os.environ.get("SQLITE_BUSY_TIMEOUT_MS"),
            "SQLITE_WAL_ENABLED": os.environ.get("SQLITE_WAL_ENABLED"),
        }

        try:
            os.environ["DATABASE_PATH"] = db_path
            os.environ["SQLITE_BUSY_TIMEOUT_MS"] = "1234"
            os.environ["SQLITE_WAL_ENABLED"] = "true"
            Config.reset_instance()
            DatabaseManager.reset_instance()

            db = DatabaseManager.get_instance()
            with db.get_session() as session:
                journal_mode = session.connection().exec_driver_sql("PRAGMA journal_mode").scalar()
                busy_timeout = session.connection().exec_driver_sql("PRAGMA busy_timeout").scalar()

            self.assertEqual(str(journal_mode).lower(), "wal")
            self.assertEqual(int(busy_timeout), 1234)
        finally:
            DatabaseManager.reset_instance()
            Config.reset_instance()
            for key, value in original_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            temp_dir.cleanup()

    def test_sqlite_write_transactions_begin_immediate(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")
        session = db.get_session()
        connection = session.connection()

        try:
            with patch.object(db, "get_session", return_value=session):
                with patch.object(connection, "exec_driver_sql", wraps=connection.exec_driver_sql) as mock_exec:
                    result = db._run_write_transaction("unit-test", lambda current_session: 7)

            self.assertEqual(result, 7)
            self.assertTrue(
                any(call.args == ("BEGIN IMMEDIATE",) for call in mock_exec.call_args_list)
            )
        finally:
            DatabaseManager.reset_instance()

    def test_stock_chip_daily_snapshots_upsert_and_read_range(self):
        DatabaseManager.reset_instance()
        db = DatabaseManager(db_url="sqlite:///:memory:")

        first = db.save_chip_daily_snapshots(
            "600519",
            [
                {
                    "date": "2026-05-07",
                    "source": "unit",
                    "profit_ratio": 0.8,
                    "avg_cost": 10.5,
                    "cost_90_low": 9.5,
                    "cost_90_high": 11.5,
                    "concentration_90": 0.12,
                    "cost_70_low": 10.0,
                    "cost_70_high": 11.0,
                    "concentration_70": 0.08,
                    "distribution": [{"price": 10.5, "percent": 1.0}],
                }
            ],
            data_source="unit",
        )
        second = db.save_chip_daily_snapshots(
            "600519",
            [
                {
                    "date": "2026-05-07",
                    "source": "unit_updated",
                    "profit_ratio": 0.9,
                    "avg_cost": 10.8,
                    "distribution": [{"price": 10.8, "percent": 1.0}],
                }
            ],
        )

        rows = db.get_chip_daily_range("600519", date(2026, 5, 1), date(2026, 5, 10))
        latest = db.get_latest_chip_daily("600519", as_of=date(2026, 5, 8))

        self.assertEqual(first, 1)
        self.assertEqual(second, 0)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["date"], "2026-05-07")
        self.assertEqual(rows[0]["avg_cost"], 10.8)
        self.assertEqual(rows[0]["distribution"], [{"price": 10.8, "percent": 1.0}])
        self.assertEqual(latest["profit_ratio"], 0.9)

        DatabaseManager.reset_instance()

    def test_save_daily_data_sqlite_concurrent_same_code_date_counts_only_new_rows(self):
        DatabaseManager.reset_instance()
        temp_dir = tempfile.TemporaryDirectory()
        db_path = os.path.join(temp_dir.name, "sqlite_daily_concurrency.db")
        db = DatabaseManager(db_url=f"sqlite:///{db_path}")

        results = []
        results_lock = threading.Lock()
        start_barrier = threading.Barrier(2)

        def worker() -> None:
            start_barrier.wait()
            count = db.save_daily_data(
                pd.DataFrame(
                    [
                        {
                            'date': date(2026, 4, 1),
                            'open': 10,
                            'high': 11,
                            'low': 9,
                            'close': 10.5,
                            'volume': 100,
                            'amount': 1050,
                            'pct_chg': 1.2,
                            'ma5': 10.1,
                            'ma10': 10.2,
                            'ma20': 10.3,
                            'volume_ratio': 1.0,
                        }
                    ]
                ),
                code='600519',
                data_source='test',
            )
            with results_lock:
                results.append(count)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        try:
            self.assertCountEqual(results, [1, 0])

            with db.get_session() as session:
                total = session.execute(
                    select(func.count()).select_from(StockDaily).where(
                        and_(
                            StockDaily.code == '600519',
                            StockDaily.date == date(2026, 4, 1),
                        )
                    )
                ).scalar()

            self.assertEqual(total, 1)
        finally:
            temp_dir.cleanup()
            DatabaseManager.reset_instance()

if __name__ == '__main__':
    unittest.main()
