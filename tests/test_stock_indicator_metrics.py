import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import pytest

from api.v1.endpoints.stocks import get_stock_history
from data_provider.realtime_types import RealtimeSource
from src.repositories.stock_repo import StockRepository
from src.storage import DatabaseManager
from src.services.stock_service import (
    StockService,
    _clear_realtime_quote_cache,
    _replace_realtime_quote_snapshot,
    _realtime_quote_cache_size,
    get_realtime_quote_cache_stats,
)


@pytest.fixture(autouse=True)
def clear_realtime_quote_cache():
    _clear_realtime_quote_cache()
    yield
    _clear_realtime_quote_cache()


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


class _FakeRealtimeIntradayHistoryManager(_FakeHistoryManager):
    def get_realtime_quote(self, stock_code, **_kwargs):
        assert stock_code == "600519"
        return SimpleNamespace(
            code="600519",
            name="贵州茅台",
            price=1411.23,
            change_amount=3.24,
            change_pct=0.23,
            open_price=1408.0,
            high=1411.23,
            low=1405.1,
            pre_close=1407.99,
            volume=1000000,
            amount=141123000,
            source=RealtimeSource.EFINANCE,
        )


def _make_quote(code: str, name: str = "测试股票", price: float = 10.0):
    return SimpleNamespace(
        code=code,
        name=name,
        price=price,
        change_amount=0.1,
        change_pct=1.0,
        open_price=price - 0.1,
        high=price + 0.2,
        low=price - 0.2,
        pre_close=price - 0.1,
        volume=1000,
        amount=price * 1000,
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
        source=RealtimeSource.EFINANCE,
    )


class _CountingRealtimeManager:
    def __init__(self):
        self.calls = 0
        self.quote = _make_quote("600519", "贵州茅台", 123.45)

    def get_realtime_quote(self, stock_code, **_kwargs):
        self.calls += 1
        return self.quote


class _BatchQuoteFetcher:
    name = "EfinanceFetcher"

    def __init__(self):
        self.requested_batches = []

    def get_realtime_quotes(self, stock_codes):
        self.requested_batches.append(list(stock_codes))
        return {
            code: _make_quote(code, "平安银行" if code == "000001" else "测试股票", 11.23)
            for code in stock_codes
        }


class _EmptyBatchQuoteFetcher:
    name = "AkshareFetcher"

    def get_realtime_quotes(self, stock_codes):
        return {}


class _BatchRealtimeManager:
    def __init__(self, fetcher):
        self.fetcher = fetcher
        self.direct_calls = []

    def get_realtime_quote(self, stock_code, **_kwargs):
        self.direct_calls.append(stock_code)
        return _make_quote("600519", "贵州茅台", 1688.5)

    def _get_fetchers_snapshot(self):
        return [self.fetcher, _EmptyBatchQuoteFetcher()]


class _IntradayBackfillManager:
    def __init__(self):
        self.intraday_calls = []

    def get_intraday_data(self, stock_code, period="1m", days=1, **kwargs):
        self.intraday_calls.append({
            "stock_code": stock_code,
            "period": period,
            "days": days,
            **kwargs,
        })
        assert period == "1m"
        return pd.DataFrame(
            {
                "date": pd.to_datetime(["2026-05-07 09:30", "2026-05-07 09:31"]),
                "open": [10.0, 10.2],
                "high": [10.3, 10.4],
                "low": [9.9, 10.1],
                "close": [10.2, 10.35],
                "volume": [1000, 1200],
                "amount": [10200, 12420],
                "pct_chg": [0.1, 0.2],
                "turnover_rate": [0.01, 0.02],
            }
        ), "EfinanceFetcher"

    def get_stock_name(self, stock_code):
        return "测试股票"


def test_realtime_quote_exposes_volume_turnover_and_source_fields() -> None:
    manager = _FakeManager()

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_realtime_quote("600519")

    assert result is not None
    assert result["volume"] == 10000
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


def test_realtime_quote_derives_turnover_when_provider_reports_zero() -> None:
    manager = _FakeManager()
    manager.quote.turnover_rate = 0
    manager.quote.volume = 200_000
    manager.quote.amount = 2_468_000
    manager.quote.price = 12.34
    manager.quote.circ_mv = 12_340_000_000
    manager.quote.float_shares = None

    with patch("data_provider.base.DataFetcherManager", return_value=manager):
        result = StockService().get_realtime_quote("600519")

    assert result is not None
    assert result["volume"] == 2000
    assert result["float_shares"] == 1_000_000_000
    assert result["turnover_rate"] == pytest.approx(0.02)


def test_realtime_quote_uses_cache_within_time_bucket() -> None:
    manager = _CountingRealtimeManager()

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", return_value=1000),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        first = StockService().get_realtime_quote("600519")
        second = StockService().get_realtime_quote("600519")

    assert first == second
    assert manager.calls == 1


def test_realtime_quote_refreshes_after_bucket_changes() -> None:
    manager = _CountingRealtimeManager()

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", side_effect=[1000, 1000, 1031, 1031]),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")
        StockService().get_realtime_quote("600519")

    assert manager.calls == 2


def test_realtime_quote_clears_old_bucket() -> None:
    manager = _CountingRealtimeManager()

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", side_effect=[1000, 1000]),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")

    assert _realtime_quote_cache_size() == 1

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", side_effect=[1031, 1031]),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")

    assert manager.calls == 2
    assert _realtime_quote_cache_size() == 1


def test_realtime_quote_cache_disabled_when_ttl_zero() -> None:
    manager = _CountingRealtimeManager()

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=0)),
        patch("src.services.stock_service.time.time", return_value=1000),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")
        StockService().get_realtime_quote("600519")

    assert manager.calls == 2
    assert _realtime_quote_cache_size() == 0


def test_realtime_quote_snapshot_only_does_not_call_remote() -> None:
    manager = _CountingRealtimeManager()
    _replace_realtime_quote_snapshot(
        requested_codes=["600519"],
        items=[{
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1688.5,
            "source": "efinance",
        }],
        failed_codes=[],
        snapshot_time=datetime(2026, 5, 7, 10, 30, 0),
    )

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch(
            "src.services.stock_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 7, 10, 31, 0),
        ),
    ):
        hit = StockService().get_realtime_quote("600519", data_policy="snapshot_only")
        miss = StockService().get_realtime_quote("000001", data_policy="snapshot_only")

    assert hit is not None
    assert hit["snapshot_id"] == "20260507103000"
    assert miss is None
    assert manager.calls == 0


def test_realtime_quote_snapshot_only_ignores_previous_market_day() -> None:
    manager = _CountingRealtimeManager()
    _replace_realtime_quote_snapshot(
        requested_codes=["600519"],
        items=[{
            "stock_code": "600519",
            "stock_name": "贵州茅台",
            "current_price": 1688.5,
            "source": "efinance",
        }],
        failed_codes=[],
        snapshot_time=datetime(2026, 5, 7, 14, 59, 0),
    )

    def fake_market_now(_market, current_time=None):
        if current_time is not None:
            return current_time
        return datetime(2026, 5, 8, 9, 0, 0)

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch("src.services.stock_service.trading_calendar.get_market_now", side_effect=fake_market_now),
    ):
        hit = StockService().get_realtime_quote("600519", data_policy="snapshot_only")

    stats = get_realtime_quote_cache_stats()
    assert hit is None
    assert stats["quote_snapshot_items"] == 0
    assert manager.calls == 0


def test_realtime_quote_cache_stats_reports_memory_usage() -> None:
    manager = _CountingRealtimeManager()

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", return_value=1000),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")
        stats = get_realtime_quote_cache_stats()

    assert stats["quote_cache_items"] == 1
    assert stats["bucket_start"] == 990
    assert stats["quote_cache_memory_bytes"] > 0
    assert stats["total_memory_bytes"] >= stats["quote_cache_memory_bytes"]
    assert isinstance(stats["total_memory_mb"], float)


def test_intraday_history_cache_only_reads_hot_table_without_remote_fetch() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)
    snapshot_time = datetime.now().replace(hour=10, minute=31, second=0, microsecond=0)
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "current_price": 12.3,
                "volume": 1000,
                "amount": 12300,
                "source": "snapshot",
            }
        ],
        snapshot_id=snapshot_time.strftime("%Y%m%d%H%M%S"),
        snapshot_time=snapshot_time,
    )

    with (
        patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=snapshot_time.date()),
        patch.object(StockService, "_is_before_intraday_session_start", return_value=False),
    ):
        result = service.get_history_data("600519", period="1m", days=1, data_policy="cache_only")

    assert result["data_source"] == "intraday_hot_table"
    assert len(result["data"]) == 1
    assert result["data"][0]["close"] == 12.3
    assert result["data"][0]["data_source"] == "snapshot"
    DatabaseManager.reset_instance()


def test_intraday_history_cache_only_ignores_after_hours_quote_samples() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)
    snapshot_time = datetime.now().replace(hour=15, minute=52, second=0, microsecond=0)
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": "600519",
                "current_price": 12.3,
                "volume": 1000,
                "amount": 12300,
                "source": "snapshot",
            }
        ],
        snapshot_id=snapshot_time.strftime("%Y%m%d%H%M%S"),
        snapshot_time=snapshot_time,
    )

    with patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")):
        result = service.get_history_data("600519.SH", period="1m", days=1, data_policy="cache_only")

    assert result["data_source"] == "intraday_hot_table_miss"
    assert result["data"] == []
    DatabaseManager.reset_instance()


def test_intraday_history_default_backfills_hot_table_from_remote_without_daily_fallback() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)
    manager = _IntradayBackfillManager()

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=date(2026, 5, 7)),
        patch.object(StockService, "_is_before_intraday_session_start", return_value=False),
        patch("src.services.stock_service.trading_calendar.is_market_open", return_value=True),
        patch.object(service, "get_realtime_quote", return_value=None),
    ):
        result = service.get_history_data("600519.SH", period="1m", days=1, data_policy="default")

    assert manager.intraday_calls == [{"stock_code": "600519.SH", "period": "1m", "days": 1}]
    assert result["data_source"] == "intraday_hot_table"
    assert len(result["data"]) == 2
    assert result["data"][0]["date"] == "2026-05-07 09:30"
    assert result["data"][0]["data_source"] == "EfinanceFetcher"

    with (
        patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=date(2026, 5, 7)),
        patch.object(StockService, "_is_before_intraday_session_start", return_value=False),
    ):
        cached = service.get_history_data("600519.SH", period="1m", days=1, data_policy="cache_only")

    assert cached["data_source"] == "intraday_hot_table"
    assert len(cached["data"]) == 2
    DatabaseManager.reset_instance()


def test_intraday_history_default_discards_single_day_remote_rows_outside_target_date() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)
    manager = _IntradayBackfillManager()

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=date(2026, 5, 8)),
        patch.object(service, "get_realtime_quote", return_value=None),
    ):
        result = service.get_history_data("600519.SH", period="1m", days=1, data_policy="default")

    assert result["data_source"] == "intraday_hot_table_miss"
    assert result["data"] == []
    assert db.get_intraday_minute_data("600519", trade_date=date(2026, 5, 7)).empty
    DatabaseManager.reset_instance()


def test_timeshare_history_before_session_start_returns_empty_without_remote_fetch() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)
    snapshot_time = datetime(2026, 5, 12, 9, 30)
    db.save_intraday_quote_samples(
        [
            {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "current_price": 268.44,
                "volume": 2308,
                "amount": 61969700,
                "source": "AkshareFetcher",
            }
        ],
        snapshot_id=snapshot_time.strftime("%Y%m%d%H%M%S"),
        snapshot_time=snapshot_time,
    )

    with (
        patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")),
        patch("src.services.stock_service.trading_calendar.get_market_for_stock", return_value="cn"),
        patch("src.services.stock_service.trading_calendar.get_market_now", return_value=datetime(2026, 5, 12, 8, 52)),
        patch("src.services.stock_service.trading_calendar.is_market_open", return_value=True),
    ):
        result = service.get_history_data("600519.SH", period="1m", days=1, data_policy="default")

    assert result["data_source"] == "intraday_hot_table_miss"
    assert result["data"] == []
    DatabaseManager.reset_instance()


def test_batch_realtime_quotes_fetches_only_missing_codes() -> None:
    fetcher = _BatchQuoteFetcher()
    manager = _BatchRealtimeManager(fetcher)

    with (
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", return_value=1000),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        StockService().get_realtime_quote("600519")
        result = StockService().get_realtime_quotes(["600519", "000001"])

    assert fetcher.requested_batches == [["000001"]]
    assert {item["stock_code"] for item in result["items"]} == {"600519", "000001"}
    assert result["failed_codes"] == []


def test_warm_all_a_share_realtime_quotes_fills_missing_current_bucket() -> None:
    fetcher = _BatchQuoteFetcher()
    manager = _BatchRealtimeManager(fetcher)

    with (
        patch("src.data.stock_index_loader.get_all_a_share_stock_codes", return_value=["600519", "000001"]),
        patch("src.services.stock_service.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
        patch("src.services.stock_service.time.time", return_value=1000),
        patch("data_provider.base.DataFetcherManager", return_value=manager),
    ):
        first = StockService().warm_all_a_share_realtime_quotes()
        second = StockService().warm_all_a_share_realtime_quotes()

    assert fetcher.requested_batches == [["600519", "000001"]]
    assert first["status"] == "refreshed"
    assert first["cached_before"] == 0
    assert first["fetched_count"] == 2
    assert first["cached_after"] == 2
    assert second["status"] == "cache_hit"
    assert second["cached_before"] == 2
    assert second["fetched_count"] == 0


def test_data_provider_realtime_cache_seconds_reads_config() -> None:
    from data_provider.akshare_fetcher import (
        _realtime_cache as akshare_cache,
        _refresh_realtime_cache_ttl as refresh_akshare_ttl,
    )
    from data_provider.efinance_fetcher import (
        _realtime_cache as efinance_cache,
        _refresh_realtime_cache_ttl as refresh_efinance_ttl,
    )

    with patch("src.config.get_config", return_value=SimpleNamespace(realtime_quote_cache_seconds=17, realtime_cache_ttl=99)):
        assert refresh_efinance_ttl(efinance_cache) == 17
        assert refresh_akshare_ttl(akshare_cache) == 17


def test_efinance_realtime_cache_coalesces_concurrent_refreshes() -> None:
    from data_provider.efinance_fetcher import (
        EfinanceFetcher,
        _realtime_cache,
        _realtime_cache_lock,
    )

    fake_df = pd.DataFrame({
        "股票代码": ["600519", "000001"],
        "股票名称": ["贵州茅台", "平安银行"],
        "最新价": [123.45, 12.34],
        "涨跌幅": [0.98, -0.5],
        "涨跌额": [1.2, -0.06],
        "成交量": [1000000, 2000000],
        "成交额": [123450000, 24680000],
        "换手率": [0.86, 1.1],
        "振幅": [2.1, 1.7],
        "最高": [125.0, 12.5],
        "最低": [121.0, 12.1],
        "开盘": [122.0, 12.2],
        "昨收": [122.25, 12.4],
        "量比": [1.23, 0.92],
        "市盈率": [23.89, 8.4],
        "总市值": [2_200_000_000_000, 100_000_000_000],
        "流通市值": [2_180_000_000_000, 90_000_000_000],
    })
    call_count = 0
    call_lock = threading.Lock()
    first_call_started = threading.Event()

    def fake_get_realtime_quotes(*_args, **_kwargs):
        nonlocal call_count
        with call_lock:
            call_count += 1
        first_call_started.set()
        time.sleep(0.1)
        return fake_df

    fake_efinance = SimpleNamespace(
        stock=SimpleNamespace(get_realtime_quotes=fake_get_realtime_quotes)
    )
    fetcher = EfinanceFetcher()

    with _realtime_cache_lock:
        _realtime_cache["data"] = None
        _realtime_cache["timestamp"] = 0

    try:
        with (
            patch.dict(sys.modules, {"efinance": fake_efinance}),
            patch("src.config.get_config", return_value=SimpleNamespace(realtime_cache_ttl=30)),
            patch("data_provider.efinance_fetcher.time.time", return_value=1000),
            patch.object(fetcher, "_set_random_user_agent", return_value=None),
            patch.object(fetcher, "_enforce_rate_limit", return_value=None),
            ThreadPoolExecutor(max_workers=2) as executor,
        ):
            first = executor.submit(fetcher.get_realtime_quotes, ["600519"])
            assert first_call_started.wait(timeout=1)
            second = executor.submit(fetcher.get_realtime_quotes, ["000001"])

            first_result = first.result(timeout=2)
            second_result = second.result(timeout=2)

        assert call_count == 1
        assert set(first_result) == {"600519"}
        assert set(second_result) == {"000001"}
    finally:
        with _realtime_cache_lock:
            _realtime_cache["data"] = None
            _realtime_cache["timestamp"] = 0


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


def test_kline_payload_normalizes_raw_share_volume_to_lots() -> None:
    df = pd.DataFrame([
        {
            "date": date(2026, 5, 7),
            "open": 11.23,
            "high": 11.48,
            "low": 11.20,
            "close": 11.37,
            "volume": 563_111_758,
            "amount": 7_439_315_238.5,
            "pct_chg": 1.23,
            "data_source": "intraday_hot_table",
        }
    ])

    payload = StockService._build_kline_payload(df, "daily", "000001")

    assert payload[0]["volume"] == pytest.approx(5_631_117.58)


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
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=date(2026, 4, 30)),
    ):
        result = service.get_history_data("600519", period="5m", days=1)

    assert result["period"] == "5m"
    assert result["stock_name"] == "贵州茅台"
    assert result["data"][0]["date"] == "2026-04-30 09:35"
    assert result["data"][0]["open"] == 1408.0
    assert result["data"][0]["turnover_rate"] == 0.38
    assert result["data"][1]["change_percent"] == -0.11
    DatabaseManager.reset_instance()


def test_history_data_anchors_cn_intraday_to_previous_session_when_market_closed() -> None:
    manager = _FakeHistoryManager()
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch("src.services.stock_service.trading_calendar.get_market_for_stock", return_value="cn"),
        patch("src.services.stock_service.trading_calendar.get_market_now", return_value=datetime(2026, 5, 1, 10, 0)),
        patch("src.services.stock_service.trading_calendar.is_market_open", return_value=False),
        patch("src.services.stock_service.trading_calendar.get_effective_trading_date", return_value=date(2026, 4, 30)),
    ):
        result = service.get_history_data("600519", period="5m", days=1)

    assert result["period"] == "5m"
    assert manager.last_intraday_kwargs["end_date"] == "2026-04-30"
    DatabaseManager.reset_instance()


def test_history_data_syncs_intraday_tail_with_realtime_quote() -> None:
    manager = _FakeRealtimeIntradayHistoryManager()
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    service = StockService()
    service.repo = StockRepository(db)

    with (
        patch("data_provider.base.DataFetcherManager", return_value=manager),
        patch.object(StockService, "_resolve_intraday_cache_target_date", return_value=date(2026, 4, 30)),
        patch(
            "src.services.stock_service.StockService._resolve_realtime_daily_date",
            return_value=date(2026, 4, 30),
        ),
    ):
        result = service.get_history_data("600519", period="5m", days=1)

    assert result["period"] == "5m"
    assert result["data"][-1]["date"] == "2026-04-30 09:40"
    assert result["data"][-1]["close"] == 1411.23
    assert result["data"][-1]["high"] == 1411.23
    assert result["data"][-1]["low"] == 1406.0
    DatabaseManager.reset_instance()


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
                "volume_ratio": 1.23,
                "turnover_rate": 0.86,
                "pe_ratio": 23.89,
                "total_mv": 2_200_000_000_000,
                "circ_mv": 2_180_000_000_000,
                "total_shares": 1_256_197_800,
                "float_shares": 1_256_197_800,
                "data_source": float("nan"),
                "snapshot_id": float("nan"),
                "snapshot_time": float("nan"),
            }
        ],
    }

    with patch("api.v1.endpoints.stocks.StockService") as service_class:
        service_class.return_value.get_history_data.return_value = payload
        response = get_stock_history("600519", period="daily", days=1)

    assert response.data[0].turnover_rate == 0.86
    assert response.data[0].volume_ratio == 1.23
    assert response.data[0].pe_ratio == 23.89
    assert response.data[0].total_mv == 2_200_000_000_000
    assert response.data[0].circ_mv == 2_180_000_000_000
    assert response.data[0].total_shares == 1_256_197_800
    assert response.data[0].float_shares == 1_256_197_800
    assert response.data[0].data_source is None
    assert response.data[0].snapshot_id is None
    assert response.data[0].snapshot_time is None


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


def test_indicator_metrics_db_only_reads_chip_daily_without_fetcher() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        db.save_chip_daily_snapshots(
            "600519",
            [
                {
                    "date": "2026-05-06",
                    "source": "unit",
                    "profit_ratio": 0.72,
                    "avg_cost": 10.2,
                    "distribution": [{"price": 10.2, "percent": 1.0}],
                },
                {
                    "date": "2026-05-07",
                    "source": "unit",
                    "profit_ratio": 0.81,
                    "avg_cost": 10.8,
                    "distribution": [{"price": 10.8, "percent": 1.0}],
                },
            ],
            data_source="unit",
        )

        service = StockService()
        service.repo = StockRepository(db)

        with patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")):
            result = service.get_indicator_metrics(
                "600519",
                data_policy="db_only",
                trade_date="2026-05-07",
                days=30,
            )

        chip = result["chip_distribution"]
        assert chip["date"] == "2026-05-07"
        assert chip["avg_cost"] == 10.8
        assert len(chip["snapshots"]) == 2
        assert result["capital_flow"] is None
        assert result["major_holders"] == []
        assert result["source_chain"][0]["provider"] == "stock_chip_daily"
    finally:
        DatabaseManager.reset_instance()


def test_indicator_metrics_db_only_returns_miss_fast_when_chip_absent() -> None:
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        service = StockService()
        service.repo = StockRepository(db)

        with patch("data_provider.base.DataFetcherManager", side_effect=AssertionError("remote fetch forbidden")):
            result = service.get_indicator_metrics(
                "600519",
                data_policy="db_only",
                trade_date=date(2026, 5, 7).isoformat(),
                days=30,
            )

        assert result["chip_distribution"] is None
        assert result["errors"] == ["chip_daily_miss"]
    finally:
        DatabaseManager.reset_instance()
