# -*- coding: utf-8 -*-

import sqlite3
import tempfile
from datetime import date
from pathlib import Path

from src.storage import DatabaseManager, StockDaily
from tools.repair_stock_daily_quality import run_repair


def test_repair_stock_daily_quality_quarantines_only_provisional_rows() -> None:
    DatabaseManager.reset_instance()
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "stock_quality.db"
        db = DatabaseManager(db_url=f"sqlite:///{db_path}")
        target_date = date(2026, 5, 7)
        try:
            with db.session_scope() as session:
                session.add(
                    StockDaily(
                        code="600519",
                        date=target_date,
                        open=10.0,
                        high=12.0,
                        low=9.8,
                        close=12.0,
                        volume=1300,
                        amount=1560000,
                        data_source="intraday_hot_table",
                    )
                )
                session.add(
                    StockDaily(
                        code="000001",
                        date=target_date,
                        open=10.0,
                        high=10.5,
                        low=9.8,
                        close=10.2,
                        volume=1000,
                        amount=1020000,
                        data_source="EfinanceFetcher",
                    )
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
                        "distribution": [{"price": 10.5, "percent": 1.0}],
                    }
                ],
                data_source="local_chip_model:intraday_hot_table",
            )

            dry_run = run_repair(
                database_path=db_path,
                start_date=target_date,
                end_date=target_date,
                apply=False,
            )
            assert dry_run.provisional_rows == 1
            assert dry_run.provisional_chip_rows == 1
            assert dry_run.deleted_rows == 0

            applied = run_repair(
                database_path=db_path,
                start_date=target_date,
                end_date=target_date,
                apply=True,
            )

            assert applied.provisional_rows == 1
            assert applied.provisional_chip_rows == 1
            assert applied.quarantined_rows == 1
            assert applied.deleted_rows == 1
            assert applied.quarantined_chip_rows == 1
            assert applied.deleted_chip_rows == 1
            assert applied.backup_path is not None
            assert Path(applied.backup_path).exists()

            connection = sqlite3.connect(str(db_path))
            try:
                stock_daily_count = connection.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0]
                quarantine_count = connection.execute("SELECT COUNT(*) FROM stock_daily_quarantine").fetchone()[0]
                chip_count = connection.execute("SELECT COUNT(*) FROM stock_chip_daily").fetchone()[0]
                chip_quarantine_count = connection.execute(
                    "SELECT COUNT(*) FROM stock_chip_daily_quarantine"
                ).fetchone()[0]
            finally:
                connection.close()
            assert stock_daily_count == 1
            assert quarantine_count == 1
            assert chip_count == 0
            assert chip_quarantine_count == 1
        finally:
            DatabaseManager.reset_instance()


def test_repair_stock_daily_quality_reports_noncanonical_a_share_codes() -> None:
    DatabaseManager.reset_instance()
    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "stock_quality_codes.db"
        db = DatabaseManager(db_url=f"sqlite:///{db_path}")
        target_date = date(2026, 5, 7)
        try:
            with db.session_scope() as session:
                session.add(
                    StockDaily(
                        code="600519.SH",
                        date=target_date,
                        open=10.0,
                        high=10.5,
                        low=9.8,
                        close=10.2,
                        volume=1000,
                        amount=1020000,
                        data_source="EfinanceFetcher",
                    )
                )
                session.add(
                    StockDaily(
                        code="000001",
                        date=target_date,
                        open=10.0,
                        high=10.5,
                        low=9.8,
                        close=10.2,
                        volume=1000,
                        amount=1020000,
                        data_source="EfinanceFetcher",
                    )
                )

            report = run_repair(
                database_path=db_path,
                start_date=target_date,
                end_date=target_date,
                apply=False,
            )

            assert report.provisional_rows == 0
            assert report.noncanonical_code_rows == 1
            assert report.deleted_rows == 0
        finally:
            DatabaseManager.reset_instance()
