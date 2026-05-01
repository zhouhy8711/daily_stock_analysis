import logging
import os
import sys
import types
import unittest
from unittest.mock import patch

import pandas as pd
import requests

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.base import BaseFetcher, DataFetchError, DataFetcherManager
from data_provider.akshare_fetcher import AkshareFetcher
from data_provider.efinance_fetcher import EfinanceFetcher
from data_provider.yfinance_fetcher import YfinanceFetcher


def _sample_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-03-06", "2026-03-07"],
            "open": [10.0, 10.2],
            "high": [10.5, 10.4],
            "low": [9.8, 10.1],
            "close": [10.3, 10.35],
            "volume": [1000, 1200],
            "amount": [10300, 12420],
            "pct_chg": [1.0, 0.49],
        }
    )


class _SuccessFetcher(BaseFetcher):
    name = "SuccessFetcher"
    priority = 1

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        return _sample_df()

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _FailureFetcher(BaseFetcher):
    name = "FailureFetcher"
    priority = 0

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise DataFetchError(
            "Eastmoney 历史K线接口失败: "
            "endpoint=push2his.eastmoney.com/api/qt/stock/kline/get, "
            "category=remote_disconnect"
        )

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        return df


class _NamedDailyFetcher:
    def __init__(self, name: str, priority: int, result=None, error: Exception | None = None):
        self.name = name
        self.priority = priority
        self._result = result
        self._error = error
        self.calls = 0

    def get_daily_data(self, *args, **kwargs):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._result


class TestFetcherLogging(unittest.TestCase):
    def test_base_fetcher_logs_start_and_success(self):
        fetcher = _SuccessFetcher()

        with self.assertLogs("data_provider.base", level="INFO") as captured:
            df = fetcher.get_daily_data("600519", start_date="2026-03-01", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertFalse(df.empty)
        self.assertIn("[SuccessFetcher] 开始获取 600519 日线数据", log_text)
        self.assertIn("[SuccessFetcher] 600519 获取成功:", log_text)
        self.assertIn("rows=2", log_text)

    def test_manager_logs_fallback_and_final_success(self):
        manager = DataFetcherManager(fetchers=[_FailureFetcher(), _SuccessFetcher()])

        with self.assertLogs("data_provider.base", level="INFO") as captured:
            df, source = manager.get_daily_data("601006", start_date="2026-01-07", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertFalse(df.empty)
        self.assertEqual(source, "SuccessFetcher")
        self.assertIn("[数据源尝试 1/2] [FailureFetcher] 获取 601006...", log_text)
        self.assertIn("[数据源失败 1/2] [FailureFetcher] 601006:", log_text)
        self.assertIn("[数据源切换] 601006: [FailureFetcher] -> [SuccessFetcher]", log_text)
        self.assertIn("[数据源完成] 601006 使用 [SuccessFetcher] 获取成功:", log_text)

    def test_efinance_logs_eastmoney_endpoint_on_remote_disconnect(self):
        fetcher = EfinanceFetcher()
        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(
                get_quote_history=lambda **kwargs: (_ for _ in ()).throw(
                    requests.exceptions.ConnectionError("Remote end closed connection without response")
                )
            )
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                with self.assertLogs(level="INFO") as captured:
                    with self.assertRaises(DataFetchError):
                        fetcher.get_daily_data("601006", start_date="2026-01-07", end_date="2026-03-08")

        log_text = "\n".join(captured.output)
        self.assertIn("Eastmoney 历史K线接口失败:", log_text)
        self.assertIn("endpoint=push2his.eastmoney.com/api/qt/stock/kline/get", log_text)
        self.assertIn("category=remote_disconnect", log_text)
        self.assertIn("[EfinanceFetcher] 601006 获取失败:", log_text)

    def test_efinance_daily_data_supports_us_stock_history(self):
        fetcher = EfinanceFetcher()
        captured_kwargs = {}
        fake_df = pd.DataFrame(
            {
                "股票名称": ["阿里巴巴", "阿里巴巴"],
                "股票代码": ["BABA", "BABA"],
                "日期": ["2026-04-23", "2026-04-24"],
                "开盘": [132.0, 133.68],
                "收盘": [134.5, 135.82],
                "最高": [135.0, 136.2],
                "最低": [131.5, 133.0],
                "成交量": [8000000, 9046794],
                "成交额": [1076000000.0, 1229000000.0],
                "涨跌幅": [1.2, 0.98],
            }
        )

        def fake_get_quote_history(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_df

        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_quote_history=fake_get_quote_history)
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                df = fetcher.get_daily_data("BABA", start_date="2026-04-01", end_date="2026-04-25")

        self.assertFalse(df.empty)
        self.assertEqual(captured_kwargs["stock_codes"], "BABA")
        self.assertEqual(list(df["code"].unique()), ["BABA"])
        self.assertAlmostEqual(df.iloc[-1]["close"], 135.82)

    def test_efinance_intraday_data_uses_minute_klt(self):
        fetcher = EfinanceFetcher()
        captured_kwargs = {}
        fake_df = pd.DataFrame(
            {
                "股票名称": ["贵州茅台", "贵州茅台"],
                "股票代码": ["600519", "600519"],
                "日期": ["2026-04-30 09:35", "2026-04-30 09:40"],
                "开盘": [1408.0, 1408.0],
                "收盘": [1407.99, 1406.4],
                "最高": [1410.0, 1409.88],
                "最低": [1405.1, 1406.0],
                "成交量": [3865, 1969],
                "成交额": [544054149.0, 277169247.0],
                "涨跌幅": [-0.11, -0.11],
            }
        )

        def fake_get_quote_history(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_df

        fake_efinance = types.SimpleNamespace(
            stock=types.SimpleNamespace(get_quote_history=fake_get_quote_history)
        )

        with patch.dict(sys.modules, {"efinance": fake_efinance}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                df = fetcher.get_intraday_data(
                    "600519",
                    period="5m",
                    start_date="2026-04-30",
                    end_date="2026-04-30",
                )

        self.assertFalse(df.empty)
        self.assertEqual(captured_kwargs["stock_codes"], "600519")
        self.assertEqual(captured_kwargs["klt"], 5)
        self.assertEqual(captured_kwargs["fqt"], 1)
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:40")
        self.assertAlmostEqual(df.iloc[-1]["close"], 1406.4)

    def test_akshare_intraday_data_maps_time_column(self):
        fetcher = AkshareFetcher()
        captured_kwargs = {}
        fake_df = pd.DataFrame(
            {
                "时间": ["2026-04-30 09:30:00", "2026-04-30 09:31:00"],
                "开盘": [138.47, 138.43],
                "收盘": [138.47, 138.90],
                "最高": [138.47, 139.00],
                "最低": [138.47, 138.18],
                "成交量": [9800, 20897],
                "成交额": [135700600.0, 289512016.0],
                "均价": [138.47, 138.519],
            }
        )

        def fake_stock_zh_a_hist_min_em(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_df

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist_min_em=fake_stock_zh_a_hist_min_em)

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                df = fetcher.get_intraday_data(
                    "300274",
                    period="1m",
                    start_date="2026-04-30",
                    end_date="2026-04-30",
                )

        self.assertFalse(df.empty)
        self.assertEqual(captured_kwargs["symbol"], "300274")
        self.assertEqual(captured_kwargs["period"], "1")
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:31")
        self.assertAlmostEqual(df.iloc[-1]["close"], 138.90)

    def test_akshare_intraday_resamples_one_minute_when_kline_fails(self):
        fetcher = AkshareFetcher()
        calls = []
        fake_1m_df = pd.DataFrame(
            {
                "时间": [
                    "2026-04-30 09:30:00",
                    "2026-04-30 09:31:00",
                    "2026-04-30 09:32:00",
                    "2026-04-30 09:33:00",
                    "2026-04-30 09:34:00",
                    "2026-04-30 09:35:00",
                ],
                "开盘": [138.0, 138.1, 138.2, 138.3, 138.4, 138.5],
                "收盘": [138.1, 138.2, 138.3, 138.4, 138.5, 138.6],
                "最高": [138.2, 138.3, 138.4, 138.5, 138.6, 138.7],
                "最低": [137.9, 138.0, 138.1, 138.2, 138.3, 138.4],
                "成交量": [100, 200, 300, 400, 500, 600],
                "成交额": [13800, 27600, 41400, 55200, 69000, 82800],
                "均价": [138.0, 138.1, 138.2, 138.3, 138.4, 138.5],
            }
        )

        def fake_stock_zh_a_hist_min_em(**kwargs):
            calls.append(kwargs["period"])
            if kwargs["period"] == "5":
                raise requests.exceptions.ProxyError("temporary eastmoney failure")
            return fake_1m_df

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist_min_em=fake_stock_zh_a_hist_min_em)

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ):
                df = fetcher.get_intraday_data(
                    "300274",
                    period="5m",
                    start_date="2026-04-30",
                    end_date="2026-04-30",
                )

        self.assertEqual(calls, ["5", "1"])
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:35")
        self.assertAlmostEqual(df.iloc[-1]["open"], 138.1)
        self.assertAlmostEqual(df.iloc[-1]["close"], 138.6)
        self.assertEqual(df.iloc[-1]["volume"], 2000)

    def test_akshare_intraday_uses_tencent_when_eastmoney_minute_fails(self):
        fetcher = AkshareFetcher()
        calls = []

        def fake_stock_zh_a_hist_min_em(**kwargs):
            calls.append(kwargs["period"])
            raise requests.exceptions.ProxyError("temporary eastmoney failure")

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "sz300274": {
                            "data": {
                                "data": [
                                    "0930 138.47 9800 135700600.00",
                                    "0931 138.90 30697 425212615.86",
                                    "0932 138.49 46452 643326398.86",
                                    "0933 139.86 61965 859277613.53",
                                    "0934 139.77 78706 1093675388.41",
                                    "0935 138.84 88217 1226175323.49",
                                ]
                            }
                        }
                    }
                }

        fake_akshare = types.SimpleNamespace(stock_zh_a_hist_min_em=fake_stock_zh_a_hist_min_em)

        with patch.dict(sys.modules, {"akshare": fake_akshare}):
            with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
                fetcher, "_enforce_rate_limit", return_value=None
            ), patch("data_provider.akshare_fetcher.requests.get", return_value=FakeResponse()):
                df = fetcher.get_intraday_data(
                    "300274",
                    period="5m",
                    start_date="2026-04-30",
                    end_date="2026-04-30",
                )

        self.assertEqual(calls, ["5", "1"])
        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:35")
        self.assertAlmostEqual(df.iloc[-1]["open"], 138.47)
        self.assertAlmostEqual(df.iloc[-1]["close"], 138.84)
        self.assertAlmostEqual(df.iloc[-1]["volume"], 78417)

    def test_akshare_us_intraday_uses_nasdaq_chart_fallback(self):
        fetcher = AkshareFetcher()

        def nasdaq_style_x(value: str) -> int:
            return int(pd.Timestamp(value).timestamp() * 1000)

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "data": {
                        "chart": [
                            {
                                "x": nasdaq_style_x("2026-04-30 09:30:00"),
                                "y": 130.15,
                                "z": {"dateTime": "9:30 AM ET"},
                            },
                            {
                                "x": nasdaq_style_x("2026-04-30 09:31:00"),
                                "y": 130.20,
                                "z": {"dateTime": "9:31 AM ET"},
                            },
                            {
                                "x": nasdaq_style_x("2026-04-30 09:32:00"),
                                "y": 130.35,
                                "z": {"dateTime": "9:32 AM ET"},
                            },
                            {
                                "x": nasdaq_style_x("2026-04-30 09:33:00"),
                                "y": 130.10,
                                "z": {"dateTime": "9:33 AM ET"},
                            },
                            {
                                "x": nasdaq_style_x("2026-04-30 09:34:00"),
                                "y": 130.50,
                                "z": {"dateTime": "9:34 AM ET"},
                            },
                            {
                                "x": nasdaq_style_x("2026-04-30 09:35:00"),
                                "y": 130.80,
                                "z": {"dateTime": "9:35 AM ET"},
                            },
                        ]
                    }
                }

        with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
            fetcher, "_enforce_rate_limit", return_value=None
        ), patch("data_provider.akshare_fetcher.requests.get", return_value=FakeResponse()):
            df = fetcher.get_intraday_data(
                "BABA",
                period="5m",
                start_date="2026-04-30",
                end_date="2026-04-30",
            )

        self.assertFalse(df.empty)
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:35")
        self.assertAlmostEqual(df.iloc[-1]["open"], 130.15)
        self.assertAlmostEqual(df.iloc[-1]["high"], 130.80)
        self.assertAlmostEqual(df.iloc[-1]["low"], 130.10)
        self.assertAlmostEqual(df.iloc[-1]["close"], 130.80)
        self.assertEqual(df.iloc[-1]["volume"], 0)

    def test_akshare_us_intraday_prefers_cnbc_multiday_ohlcv(self):
        fetcher = AkshareFetcher()

        class FakeResponse:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "barData": {
                        "priceBars": [
                            {
                                "tradeTime": "20260429155900",
                                "open": "130.10",
                                "high": "130.30",
                                "low": "130.00",
                                "close": "130.20",
                                "volume": 1000,
                            },
                            {
                                "tradeTime": "20260429180000",
                                "open": "130.30",
                                "high": "130.40",
                                "low": "130.20",
                                "close": "130.35",
                                "volume": 900,
                            },
                            {
                                "tradeTime": "20260430070000",
                                "open": "130.80",
                                "high": "131.10",
                                "low": "130.70",
                                "close": "131.00",
                                "volume": 800,
                            },
                            {
                                "tradeTime": "20260430103000",
                                "open": "131.10",
                                "high": "131.40",
                                "low": "131.00",
                                "close": "131.30",
                                "volume": 1200,
                            },
                        ]
                    }
                }

        with patch.object(fetcher, "_set_random_user_agent", return_value=None), patch.object(
            fetcher, "_enforce_rate_limit", return_value=None
        ), patch("data_provider.akshare_fetcher.requests.get", return_value=FakeResponse()) as mocked_get:
            df = fetcher.get_intraday_data(
                "BABA",
                period="1m",
                start_date="2026-04-29",
                end_date="2026-04-30",
            )

        self.assertFalse(df.empty)
        self.assertEqual(mocked_get.call_args.args[0], "https://ts-api.cnbc.com/harmony/app/charts/1D.json")
        self.assertEqual(len(df), 2)
        self.assertEqual(df.iloc[0]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-29 15:59")
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 10:30")
        self.assertAlmostEqual(df.iloc[-1]["close"], 131.30)
        self.assertAlmostEqual(df.iloc[-1]["volume"], 1200)

    def test_yfinance_intraday_data_maps_datetime_column(self):
        fetcher = YfinanceFetcher()
        captured_kwargs = {}
        fake_df = pd.DataFrame(
            {
                "Open": [132.0, 132.5],
                "High": [133.0, 133.2],
                "Low": [131.8, 132.1],
                "Close": [132.6, 133.0],
                "Volume": [120000, 180000],
            },
            index=pd.to_datetime(["2026-04-30 09:35", "2026-04-30 09:40"]),
        )
        fake_df.index.name = "Datetime"

        def fake_download(**kwargs):
            captured_kwargs.update(kwargs)
            return fake_df

        fake_yfinance = types.SimpleNamespace(download=fake_download)

        with patch.dict(sys.modules, {"yfinance": fake_yfinance}):
            df = fetcher.get_intraday_data(
                "BABA",
                period="5m",
                start_date="2026-04-30",
                end_date="2026-04-30",
            )

        self.assertFalse(df.empty)
        self.assertEqual(captured_kwargs["tickers"], "BABA")
        self.assertEqual(captured_kwargs["interval"], "5m")
        self.assertEqual(captured_kwargs["period"], "1d")
        self.assertEqual(df.iloc[-1]["date"].strftime("%Y-%m-%d %H:%M"), "2026-04-30 09:40")
        self.assertAlmostEqual(df.iloc[-1]["close"], 133.0)

    def test_us_stock_history_uses_efinance_as_yfinance_fallback(self):
        yfinance = _NamedDailyFetcher(
            "YfinanceFetcher",
            4,
            error=DataFetchError("Yahoo Finance rate limited"),
        )
        efinance = _NamedDailyFetcher("EfinanceFetcher", 0, result=_sample_df())
        longbridge = _NamedDailyFetcher("LongbridgeFetcher", 5, result=_sample_df())
        manager = DataFetcherManager(fetchers=[efinance, yfinance, longbridge])

        df, source = manager.get_daily_data("BABA", start_date="2026-04-01", end_date="2026-04-25")

        self.assertFalse(df.empty)
        self.assertEqual(source, "EfinanceFetcher")
        self.assertEqual(yfinance.calls, 1)
        self.assertEqual(efinance.calls, 1)
        self.assertEqual(longbridge.calls, 0)

    def test_us_stock_history_uses_akshare_after_yfinance_and_efinance_fail(self):
        yfinance = _NamedDailyFetcher(
            "YfinanceFetcher",
            4,
            error=DataFetchError("Yahoo Finance rate limited"),
        )
        efinance = _NamedDailyFetcher(
            "EfinanceFetcher",
            0,
            error=DataFetchError("Eastmoney remote disconnected"),
        )
        akshare = _NamedDailyFetcher("AkshareFetcher", 1, result=_sample_df())
        longbridge = _NamedDailyFetcher(
            "LongbridgeFetcher",
            5,
            error=DataFetchError("Longbridge not configured"),
        )
        manager = DataFetcherManager(fetchers=[efinance, akshare, yfinance, longbridge])

        df, source = manager.get_daily_data("BABA", start_date="2026-04-01", end_date="2026-04-25")

        self.assertFalse(df.empty)
        self.assertEqual(source, "AkshareFetcher")
        self.assertEqual(yfinance.calls, 1)
        self.assertEqual(efinance.calls, 1)
        self.assertEqual(akshare.calls, 1)
        self.assertEqual(longbridge.calls, 0)


if __name__ == "__main__":
    unittest.main()
