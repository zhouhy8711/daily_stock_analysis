# -*- coding: utf-8 -*-
"""Tests for iFinD/Tonghuashun chip data source routing."""

from types import SimpleNamespace

import pandas as pd

from data_provider.base import DataFetcherManager
from data_provider.ifind_chip_fetcher import IfindChipFetcher
from data_provider.realtime_types import ChipDistribution, ChipDistributionPoint


class _FakeIfindClient:
    def __init__(self, login_result=0):
        self.login_result = login_result
        self.login_calls = []

    def THS_iFinDLogin(self, username, password):
        self.login_calls.append((username, password))
        return self.login_result

    def THS_BD(self, code, indicators, params):
        if indicators == "summary":
            return SimpleNamespace(
                errorcode=0,
                data=pd.DataFrame(
                    [
                        {
                            "日期": "20260430",
                            "获利比例": 59.75,
                            "平均成本": 135.72,
                            "90成本-低": 125.19,
                            "90成本-高": 173.94,
                            "90集中度": 16.30,
                            "70成本-低": 129.0,
                            "70成本-高": 160.0,
                            "70集中度": 10.72,
                        }
                    ]
                ),
            )
        if indicators == "distribution":
            return SimpleNamespace(
                errorcode=0,
                data=pd.DataFrame(
                    [
                        {"price": 125.19, "percent": 20},
                        {"price": 135.72, "percent": 50},
                        {"price": 173.94, "percent": 30},
                    ]
                ),
            )
        return SimpleNamespace(errorcode=0, data=pd.DataFrame())


class _DummyChipFetcher:
    def __init__(self, name, priority, chip_priority, chip):
        self.name = name
        self.priority = priority
        self.chip_priority = chip_priority
        self.chip = chip

    def get_chip_distribution(self, stock_code):
        return self.chip


def test_ifind_chip_fetcher_maps_summary_and_distribution() -> None:
    client = _FakeIfindClient()
    fetcher = IfindChipFetcher(
        client=client,
        username="demo",
        password="secret",
        summary_indicators="summary",
        distribution_indicators="distribution",
    )

    chip = fetcher.get_chip_distribution("300274")

    assert chip is not None
    assert chip.source == "ifind"
    assert chip.date == "2026-04-30"
    assert chip.profit_ratio == 0.5975
    assert chip.avg_cost == 135.72
    assert chip.concentration_90 == 0.163
    assert [point.price for point in chip.distribution] == [125.19, 135.72, 173.94]
    assert [point.percent for point in chip.distribution] == [0.2, 0.5, 0.3]
    assert client.login_calls == [("demo", "secret")]


def test_ifind_chip_fetcher_returns_none_when_login_fails() -> None:
    fetcher = IfindChipFetcher(
        client=_FakeIfindClient(login_result=-1),
        username="demo",
        password="bad",
        summary_indicators="summary",
        distribution_indicators="distribution",
    )

    assert fetcher.get_chip_distribution("300274") is None


def test_chip_manager_uses_tushare_after_ifind_unavailable() -> None:
    tushare_chip = ChipDistribution(
        code="600519",
        source="tushare_cyq_chips",
        distribution=[ChipDistributionPoint(price=10.0, percent=1.0)],
    )
    manager = DataFetcherManager(
        fetchers=[
            _DummyChipFetcher("IfindChipFetcher", priority=99, chip_priority=-2, chip=None),
            _DummyChipFetcher("TushareFetcher", priority=2, chip_priority=2, chip=tushare_chip),
        ]
    )

    assert manager.get_chip_distribution("600519") is tushare_chip


def test_chip_manager_returns_akshare_summary_fallback() -> None:
    akshare_chip = ChipDistribution(code="600519", source="akshare_em", avg_cost=118.5)
    manager = DataFetcherManager(
        fetchers=[
            _DummyChipFetcher("IfindChipFetcher", priority=99, chip_priority=-2, chip=None),
            _DummyChipFetcher("TushareFetcher", priority=2, chip_priority=2, chip=None),
            _DummyChipFetcher("AkshareFetcher", priority=1, chip_priority=3, chip=akshare_chip),
        ]
    )

    assert manager.get_chip_distribution("600519") is akshare_chip
    assert akshare_chip.distribution == []


def test_chip_manager_returns_none_when_all_sources_unavailable() -> None:
    manager = DataFetcherManager(
        fetchers=[
            _DummyChipFetcher("IfindChipFetcher", priority=99, chip_priority=-2, chip=None),
            _DummyChipFetcher("TushareFetcher", priority=2, chip_priority=2, chip=None),
            _DummyChipFetcher("AkshareFetcher", priority=1, chip_priority=3, chip=None),
        ]
    )

    assert manager.get_chip_distribution("600519") is None
