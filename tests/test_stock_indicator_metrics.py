from types import SimpleNamespace
from unittest.mock import patch

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
    assert result["major_holder_status"] == "ok"
    assert result["major_holders"][0]["name"] == "摩根士丹利"
    assert result["major_holders"][0]["holding_ratio"] == 2.35
