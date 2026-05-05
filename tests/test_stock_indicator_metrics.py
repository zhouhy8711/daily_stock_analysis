from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from api.v1.endpoints.stocks import get_stock_history
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
            after_hours_volume=104,
            after_hours_amount=1_429_064,
            volume_ratio=1.23,
            turnover_rate=0.86,
            amplitude=2.1,
            pe_ratio=23.89,
            total_mv=2_200_000_000_000,
            circ_mv=2_180_000_000_000,
            price_speed=0.18,
            limit_up_price=134.48,
            limit_down_price=110.02,
            entrust_ratio=12.5,
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

    def get_capital_flow_context(self, stock_code, budget_seconds=None):
        assert stock_code == "600519"
        return {
            "status": "ok",
            "data": {
                "stock_flow": {
                    "main_net_inflow": 56_780_000,
                    "main_net_inflow_ratio": 3.2,
                    "inflow_5d": 120_000_000,
                    "inflow_10d": -50_000_000,
                }
            },
            "source_chain": [{"provider": "capital_flow", "result": "ok", "duration_ms": 23}],
            "errors": [],
        }

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
                "turnover_rate": [0.38, 0.19],
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
    assert result["pe_ratio"] == 23.89
    assert result["total_mv"] == 2_200_000_000_000
    assert result["circ_mv"] == 2_180_000_000_000
    assert result["total_shares"] == 2_200_000_000_000 / 123.45
    assert result["float_shares"] == 2_180_000_000_000 / 123.45
    assert result["limit_up_price"] == 134.48
    assert result["limit_down_price"] == 110.02
    assert result["price_speed"] == 0.18
    assert result["entrust_ratio"] == 12.5
    assert result["after_hours_volume"] == 104
    assert result["after_hours_amount"] == 1_429_064
    assert result["source"] == "efinance"


def test_realtime_quote_derives_after_hours_volume_from_amount_and_price() -> None:
    manager = _FakeManager()
    manager.quote.price = 137.41
    manager.quote.after_hours_volume = None
    manager.quote.after_hours_amount = 1_429_064

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_realtime_quote("600519")

    assert result is not None
    assert result["after_hours_volume"] == 104.0
    assert result["after_hours_amount"] == 1_429_064


def test_realtime_quote_derives_market_values_from_share_counts_and_price() -> None:
    manager = _FakeManager()
    manager.quote.price = 137.41
    manager.quote.total_mv = None
    manager.quote.circ_mv = None
    manager.quote.total_shares = 2_073_211_424
    manager.quote.float_shares = 1_590_014_737

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_realtime_quote("600519")

    assert result is not None
    assert result["total_mv"] == 2_073_211_424 * 137.41
    assert result["circ_mv"] == 1_590_014_737 * 137.41
    assert result["total_shares"] == 2_073_211_424
    assert result["float_shares"] == 1_590_014_737


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
    assert result["capital_flow"]["status"] == "ok"
    assert result["capital_flow"]["main_net_inflow"] == 56_780_000
    assert result["capital_flow"]["main_net_inflow_ratio"] == 3.2
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
    assert result["data"][0]["turnover_rate"] == 0.38
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


def test_history_endpoint_exposes_turnover_rate() -> None:
    payload = {
        "stock_name": "贵州茅台",
        "period": "daily",
        "data": [
            {
                "date": "2026-04-30",
                "open": 122.0,
                "high": 125.0,
                "low": 121.0,
                "close": 123.45,
                "volume": 1000000,
                "amount": 123450000,
                "change_percent": 0.98,
                "turnover_rate": 0.86,
            }
        ],
    }

    with patch("api.v1.endpoints.stocks.StockService") as service_class:
        service_class.return_value.get_history_data.return_value = payload
        response = get_stock_history("600519", period="daily", days=1)

    assert response.data[0].turnover_rate == 0.86


def test_related_news_reads_recent_news_without_model_call() -> None:
    long_snippet = "公司经营保持稳定，零售业务资产质量改善。" * 10
    fake_db = SimpleNamespace(
        get_recent_news=lambda code, days, limit: [
            SimpleNamespace(
                title="贵州茅台发布经营动态",
                snippet=long_snippet,
                url="https://example.com/news/maotai",
            )
        ],
    )

    with patch("src.storage.DatabaseManager.get_instance", return_value=fake_db):
        result = StockService().get_related_news("600519", limit=3, days=7)

    assert result["total"] == 1
    assert result["items"][0]["title"] == "贵州茅台发布经营动态"
    assert result["items"][0]["url"] == "https://example.com/news/maotai"
    assert len(result["items"][0]["snippet"]) <= 200


def test_related_news_refreshes_public_news_and_saves_to_db() -> None:
    saved = {}

    class FakeDb:
        def save_news_intel(self, **kwargs):
            saved.update(kwargs)
            return 1

        def get_recent_news(self, code, days, limit):
            assert code == "600519"
            return [
                SimpleNamespace(
                    title="刷新后的贵州茅台资讯",
                    snippet="公开资讯源返回的最新消息。",
                    url="https://example.com/news/refreshed",
                )
            ]

    fake_search_response = SimpleNamespace(
        success=True,
        results=[
            SimpleNamespace(
                title="刷新后的贵州茅台资讯",
                snippet="公开资讯源返回的最新消息。",
                url="https://example.com/news/refreshed",
                source="public",
                published_date="2026-04-30",
            )
        ],
        query="贵州茅台 600519 股票 最新消息",
    )
    fake_search_service = SimpleNamespace(
        search_stock_news=lambda stock_code, stock_name, max_results: fake_search_response
    )

    with (
        patch("src.storage.DatabaseManager.get_instance", return_value=FakeDb()),
        patch("data_provider.base.DataFetcherManager", return_value=_FakeManager()),
        patch("src.search_service.get_search_service", return_value=fake_search_service),
    ):
        result = StockService().get_related_news("600519", limit=3, days=7, refresh=True)

    assert saved["code"] == "600519"
    assert saved["name"] == "贵州茅台"
    assert saved["dimension"] == "latest_news"
    assert saved["query_context"]["query_source"] == "indicator_page"
    assert result["items"][0]["title"] == "刷新后的贵州茅台资讯"
