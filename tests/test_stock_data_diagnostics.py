import os
import tempfile
from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd
from fastapi.testclient import TestClient

try:
    import litellm  # noqa: F401
except ModuleNotFoundError:
    import sys

    sys.modules["litellm"] = MagicMock()

from api.app import create_app
from src.config import Config
from src.services.stock_data_diagnostics_service import StockDataDiagnosticsService
from src.services.stock_service import (
    _cache_quote_payload,
    _clear_realtime_quote_cache,
    _replace_realtime_quote_snapshot,
)
from src.storage import DatabaseManager
from tools import check_stock_data_diagnostics as diagnostics_tool


def _save_daily(db: DatabaseManager, code: str, dates: list[date]) -> None:
    df = pd.DataFrame(
        [
            {
                "date": item,
                "open": 10.0,
                "high": 11.0,
                "low": 9.0,
                "close": 10.5,
                "volume": 1000,
                "amount": 10500,
                "pct_chg": 0.1,
            }
            for item in dates
        ]
    )
    db.save_daily_data(df, code=code, data_source="unit_daily")


def _save_intraday(db: DatabaseManager, code: str, minute: datetime, price: float = 10.8) -> None:
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": code,
                "current_price": price,
                "volume": 1000,
                "amount": price * 1000,
                "source": "unit_snapshot",
            }
        ],
        snapshot_id=minute.strftime("%Y%m%d%H%M%S"),
        snapshot_time=minute,
    )


def setup_function() -> None:
    _clear_realtime_quote_cache()
    DatabaseManager.reset_instance()


def teardown_function() -> None:
    _clear_realtime_quote_cache()
    DatabaseManager.reset_instance()
    Config.reset_instance()
    os.environ.pop("ENV_FILE", None)
    os.environ.pop("DATABASE_PATH", None)


def test_stock_data_diagnostics_empty_db() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    result = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
        trade_date=date(2026, 5, 7),
    )

    assert result["total"] == 0
    assert result["summary"]["population_count"] == 0
    assert result["summary"]["history"]["row_count"] == 0
    assert result["summary"]["intraday"]["row_count"] == 0
    assert result["items"] == []


def test_stock_data_diagnostics_aggregates_history_intraday_and_quote_cache() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _save_daily(db, "600519", [date(2026, 5, 6), date(2026, 5, 7)])
    _save_intraday(db, "600519", datetime(2026, 5, 7, 10, 30))
    _replace_realtime_quote_snapshot(
        requested_codes=["600519", "000001"],
        items=[{"stock_code": "600519", "current_price": 10.8, "source": "unit"}],
        failed_codes=["000001"],
        snapshot_time=datetime(2026, 5, 7, 10, 30),
    )
    _cache_quote_payload("000001", {"stock_code": "000001", "current_price": 8.8})

    with patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")):
        result = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
            trade_date=date(2026, 5, 7),
            sort="code",
        )

    assert result["total"] == 2
    assert result["summary"]["history"]["stock_count"] == 1
    assert result["summary"]["history"]["row_count"] == 2
    assert result["summary"]["history"]["missing_count"] == 1
    assert result["summary"]["intraday"]["stock_count"] == 1
    assert result["summary"]["intraday"]["row_count"] == 1
    assert result["summary"]["quote"]["snapshot_hit_count"] == 1
    assert result["summary"]["quote"]["short_cache_hit_count"] == 1

    by_code = {item["stock_code"]: item for item in result["items"]}
    assert by_code["600519"]["history"]["rows"] == 2
    assert by_code["600519"]["history"]["last_date"] == "2026-05-07"
    assert by_code["600519"]["intraday"]["rows"] == 1
    assert by_code["600519"]["quote"]["snapshot_hit"] is True
    assert by_code["000001"]["quote"]["short_cache_hit"] is True


def test_stock_data_diagnostics_scope_filter_sort_and_paging() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _save_daily(db, "600519", [date(2026, 5, 6), date(2026, 5, 7)])
    _save_daily(db, "000001", [date(2026, 5, 7)])

    result = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
        trade_date=date(2026, 5, 7),
        scope="history_db",
        sort="history_rows_desc",
        limit=1,
        offset=0,
    )
    assert result["total"] == 2
    assert result["has_more"] is True
    assert result["items"][0]["stock_code"] == "600519"

    filtered = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
        trade_date=date(2026, 5, 7),
        scope="history_db",
        q="000",
    )
    assert filtered["total"] == 1
    assert filtered["items"][0]["stock_code"] == "000001"


def test_stock_data_diagnostics_merges_normalized_daily_codes() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _save_daily(db, "603375", [date(2026, 5, 6), date(2026, 5, 7)])
    _save_daily(db, "603375.SH", [date(2026, 5, 7)])

    result = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
        trade_date=date(2026, 5, 7),
        scope="history_db",
    )

    assert result["total"] == 1
    assert result["summary"]["history"]["stock_count"] == 1
    assert result["summary"]["history"]["row_count"] == 2
    assert result["items"][0]["stock_code"] == "603375"
    assert result["items"][0]["history"]["rows"] == 2
    assert result["items"][0]["history"]["first_date"] == "2026-05-06"
    assert result["items"][0]["history"]["last_date"] == "2026-05-07"


def test_stock_data_diagnostics_active_a_share_scope() -> None:
    db = DatabaseManager(db_url="sqlite:///:memory:")
    _save_daily(db, "600519", [date(2026, 5, 7)])

    with patch(
        "src.services.stock_data_diagnostics_service.get_all_a_share_stock_codes",
        return_value=["600519", "000001", "300750"],
    ):
        result = StockDataDiagnosticsService(db).get_stock_data_diagnostics(
            trade_date=date(2026, 5, 7),
            scope="active_a_share",
        )

    assert result["total"] == 3
    assert result["summary"]["history"]["stock_count"] == 1
    assert result["summary"]["history"]["missing_count"] == 2


def test_stock_data_diagnostics_endpoint() -> None:
    with tempfile.TemporaryDirectory() as temp_dir:
        data_dir = Path(temp_dir)
        env_path = data_dir / ".env"
        db_path = data_dir / "diagnostics_api_test.db"
        env_path.write_text(
            "\n".join(
                [
                    "ADMIN_AUTH_ENABLED=false",
                    "GEMINI_API_KEY=test",
                    f"DATABASE_PATH={db_path}",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        os.environ["ENV_FILE"] = str(env_path)
        os.environ["DATABASE_PATH"] = str(db_path)
        Config.reset_instance()
        DatabaseManager.reset_instance()

        app = create_app(static_dir=data_dir / "empty-static")
        client = TestClient(app)
        db = DatabaseManager.get_instance()
        _save_daily(db, "600519", [date(2026, 5, 7)])

        response = client.get(
            "/api/v1/diagnostics/stock-data",
            params={"trade_date": "2026-05-07", "scope": "history_db"},
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["stock_code"] == "600519"


def test_tools_check_stock_data_diagnostics_success(monkeypatch, capsys) -> None:
    payload = {
        "generated_at": "2026-05-07T10:30:00",
        "trade_date": "2026-05-07",
        "scope": "observed",
        "limit": 20,
        "offset": 0,
        "total": 1,
        "has_more": False,
        "summary": {
            "population_count": 1,
            "history": {"stock_count": 1, "row_count": 2, "first_date": "2026-05-06", "last_date": "2026-05-07", "missing_count": 0},
            "intraday": {"stock_count": 1, "row_count": 1, "first_minute": "2026-05-07T10:30:00", "last_minute": "2026-05-07T10:30:00", "missing_count": 0},
            "quote": {"snapshot_id": "20260507103000", "snapshot_time": "2026-05-07T10:30:00", "snapshot_age_seconds": 1, "snapshot_items": 1, "short_cache_items": 0, "snapshot_hit_count": 1, "short_cache_hit_count": 0},
        },
        "items": [
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "history": {"rows": 2, "first_date": "2026-05-06", "last_date": "2026-05-07", "latest_source": "unit"},
                "intraday": {"rows": 1, "first_minute": "2026-05-07T10:30:00", "last_minute": "2026-05-07T10:30:00", "sources": ["unit"]},
                "quote": {"snapshot_hit": True, "short_cache_hit": False},
            }
        ],
    }
    monkeypatch.setattr(diagnostics_tool, "_fetch_json", lambda *_args, **_kwargs: payload)

    exit_code = diagnostics_tool.main(["--limit", "1"])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Stock Data Diagnostics" in captured.out
    assert "600519" in captured.out


def test_tools_check_stock_data_diagnostics_fails_on_bad_shape(monkeypatch, capsys) -> None:
    monkeypatch.setattr(diagnostics_tool, "_fetch_json", lambda *_args, **_kwargs: {"items": []})

    exit_code = diagnostics_tool.main([])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "missing key" in captured.err
