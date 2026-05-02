from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from data_provider.realtime_types import RealtimeSource
from src.services.stock_service import StockService


class _FakeManager:
    def __init__(self):
        self.quote = SimpleNamespace(
            code="600519",
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
            volume_ratio=1.23,
            turnover_rate=0.86,
            amplitude=2.1,
            source=RealtimeSource.EFINANCE,
        )
        self.chip = SimpleNamespace(
            code="600519",
            date="2026-04-24",
            source="akshare",
            profit_ratio=0.68,
            avg_cost=118.5,
            cost_90_low=110.2,
            cost_90_high=130.8,
            concentration_90=0.12,
            cost_70_low=114.1,
            cost_70_high=126.2,
            concentration_70=0.09,
            distribution=[
                {"price": 110.2, "percent": 0.2},
                SimpleNamespace(price=118.5, percent=0.5),
                {"price": 130.8, "percent": 0.3},
            ],
        )

    def get_realtime_quote(self, stock_code, **_kwargs):
        assert stock_code == "600519"
        return self.quote

    def get_stock_name(self, stock_code, allow_realtime=True):
        assert stock_code == "600519"
        assert allow_realtime is False
        return "贵州茅台"

    def get_chip_distribution(self, stock_code):
        assert stock_code == "600519"
        return self.chip

    def get_major_holders_context(self, stock_code, top_n=20):
        assert stock_code == "600519"
        assert top_n == 20
        return {
            "status": "ok",
            "data": {
                "holders": [
                    {
                        "name": "摩根士丹利",
                        "holder_type": "QFII",
                        "holding_ratio": 2.35,
                        "report_date": "2026-03-31",
                    }
                ],
            },
            "source_chain": [{"provider": "major_holders", "result": "ok", "duration_ms": 12}],
            "errors": [],
        }


class _FakeHistoryManager:
    def __init__(self):
        self.last_intraday_kwargs = {}

    def get_intraday_data(self, stock_code, period="5m", days=1, **kwargs):
        self.last_intraday_kwargs = {"period": period, "days": days, **kwargs}
        assert stock_code == "600519"
        assert period == "5m"
        assert days == 1
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-04-30 09:35", "2026-04-30 09:40"]),
                "open": [1408.0, 1408.0],
                "high": [1410.0, 1409.88],
                "low": [1405.1, 1406.0],
                "close": [1407.99, 1406.4],
                "volume": [3865, 1969],
                "amount": [544054149.0, 277169247.0],
                "pct_chg": [-0.11, -0.11],
            }
        ), "EfinanceFetcher"

    def get_stock_name(self, stock_code):
        assert stock_code == "600519"
        return "贵州茅台"


def test_realtime_quote_exposes_volume_turnover_and_source_fields() -> None:
    manager = _FakeManager()

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_realtime_quote("600519")

    assert result is not None
    assert result["volume_ratio"] == 1.23
    assert result["turnover_rate"] == 0.86
    assert result["amplitude"] == 2.1
    assert result["source"] == "efinance"


def test_indicator_metrics_maps_chip_distribution_and_major_holders() -> None:
    manager = _FakeManager()

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_indicator_metrics("600519")

    assert result["stock_name"] == "贵州茅台"
    assert result["chip_distribution"]["avg_cost"] == 118.5
    assert result["chip_distribution"]["concentration_90"] == 0.12
    assert result["chip_distribution"]["distribution"] == [
        {"price": 110.2, "percent": 0.2},
        {"price": 118.5, "percent": 0.5},
        {"price": 130.8, "percent": 0.3},
    ]
    assert result["major_holder_status"] == "ok"
    assert result["major_holders"][0]["name"] == "摩根士丹利"
    assert result["major_holders"][0]["holding_ratio"] == 2.35


def test_history_data_supports_intraday_period() -> None:
    manager = _FakeHistoryManager()

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_history_data("600519", period="5m", days=1)

    assert result["period"] == "5m"
    assert result["stock_name"] == "贵州茅台"
    assert result["data"][0]["date"] == "2026-04-30 09:35"
    assert result["data"][0]["open"] == 1408.0
    assert result["data"][1]["change_percent"] == -0.11


def test_history_data_anchors_cn_intraday_to_previous_session_when_market_closed() -> None:
    manager = _FakeHistoryManager()

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch("src.services.stock_service.trading_calendar.get_market_for_stock", return_value="cn"),
        patch("src.services.stock_service.trading_calendar.get_market_now", return_value=datetime(2026, 5, 1, 10, 0)),
        patch("src.services.stock_service.trading_calendar.is_market_open", return_value=False),
        patch("src.services.stock_service.trading_calendar.get_effective_trading_date", return_value=date(2026, 4, 30)),
    ):
        result = StockService().get_history_data("600519", period="5m", days=1)

    assert result["period"] == "5m"
    assert manager.last_intraday_kwargs["end_date"] == "2026-04-30"
