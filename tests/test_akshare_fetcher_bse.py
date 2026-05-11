# -*- coding: utf-8 -*-
"""Unit tests for AkShare BSE history normalization."""

import sys
from types import SimpleNamespace

import pandas as pd

from data_provider.akshare_fetcher import AkshareFetcher


def test_fetch_stock_data_sina_maps_decimal_turnover_to_percent(monkeypatch) -> None:
    calls = {}

    def _fake_stock_zh_a_daily(**kwargs):
        calls.update(kwargs)
        return pd.DataFrame(
            [
                {
                    "date": "2026-01-05",
                    "open": 10.0,
                    "high": 11.0,
                    "low": 9.8,
                    "close": 10.5,
                    "volume": 1000.0,
                    "amount": 10500.0,
                    "outstanding_share": 40000.0,
                    "turnover": 0.025,
                }
            ]
        )

    monkeypatch.setitem(
        sys.modules,
        "akshare",
        SimpleNamespace(stock_zh_a_daily=_fake_stock_zh_a_daily),
    )
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)

    df = fetcher._fetch_stock_data_sina("920974", "2026-01-05", "2026-01-05")

    assert calls["symbol"] == "bj920974"
    assert df.loc[0, "换手率"] == 2.5


def test_fetch_stock_data_uses_sina_first_for_bse(monkeypatch) -> None:
    fetcher = AkshareFetcher(sleep_min=0, sleep_max=0)
    calls = []

    def _fake_sina(stock_code: str, start_date: str, end_date: str):
        calls.append("sina")
        return pd.DataFrame([{"日期": "2026-01-05", "收盘": 10.0, "换手率": 2.5}])

    def _fake_em(stock_code: str, start_date: str, end_date: str):
        calls.append("em")
        raise AssertionError("BSE should try Sina before Eastmoney")

    monkeypatch.setattr(fetcher, "_fetch_stock_data_sina", _fake_sina)
    monkeypatch.setattr(fetcher, "_fetch_stock_data_em", _fake_em)

    df = fetcher._fetch_stock_data("920974", "2026-01-05", "2026-01-05")

    assert calls == ["sina"]
    assert not df.empty
