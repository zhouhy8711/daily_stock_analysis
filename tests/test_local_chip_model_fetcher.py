# -*- coding: utf-8 -*-
"""Tests for local turnover-based chip distribution fallback."""

from types import SimpleNamespace

import pandas as pd

from data_provider.base import DataFetcherManager
from data_provider.local_chip_model_fetcher import (
    LocalChipModelFetcher,
    compute_chip_distribution_from_history,
)
from data_provider.realtime_types import ChipDistribution, ChipDistributionPoint


def _sample_history() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-04-20", periods=6, freq="D"),
            "open": [130.0, 132.0, 134.0, 133.0, 136.0, 137.0],
            "high": [136.0, 138.0, 139.0, 140.0, 142.0, 141.0],
            "low": [128.0, 130.0, 131.0, 132.0, 134.0, 135.0],
            "close": [132.0, 134.0, 133.0, 137.0, 138.0, 137.41],
            "volume": [1000, 1200, 1100, 1300, 1250, 1400],
            "amount": [132000, 160800, 146300, 178100, 172500, 192374],
            "pct_chg": [0.2, 1.5, -0.7, 3.0, 0.7, -0.4],
            "turnover_rate": [2.1, 2.3, 1.9, 3.4, 2.8, 4.0],
        }
    )


def test_compute_chip_distribution_from_turnover_history() -> None:
    chip = compute_chip_distribution_from_history(
        "300274",
        _sample_history(),
        history_source="unit",
        max_price_points=60,
    )

    assert chip is not None
    assert chip.source == "local_chip_model:unit"
    assert chip.date == "2026-04-25"
    assert 0 < chip.profit_ratio < 1
    assert chip.avg_cost > 0
    assert chip.cost_90_low <= chip.cost_90_high
    assert chip.cost_70_low <= chip.cost_70_high
    assert chip.concentration_90 >= chip.concentration_70
    assert len(chip.distribution) > 0
    assert abs(sum(point.percent for point in chip.distribution) - 1.0) < 0.0001


def test_local_chip_model_fetcher_uses_injected_history_loader() -> None:
    fetcher = LocalChipModelFetcher(
        history_loader=lambda code, days: (_sample_history(), "unit_loader"),
        max_price_points=60,
    )

    chip = fetcher.get_chip_distribution("SZ300274")

    assert chip is not None
    assert chip.code == "300274"
    assert chip.source == "local_chip_model:unit_loader"
    assert len(chip.distribution) > 0
    assert len(chip.snapshots) > 0
    assert chip.snapshots[-1]["date"] == chip.date
    assert len(chip.snapshots[-1]["distribution"]) > 0


def test_local_chip_model_returns_none_without_turnover() -> None:
    history = _sample_history().drop(columns=["turnover_rate"])

    assert compute_chip_distribution_from_history("300274", history) is None


def test_local_chip_model_derives_turnover_from_float_market_value() -> None:
    history = _sample_history().drop(columns=["turnover_rate"])
    fetcher = LocalChipModelFetcher(
        history_loader=lambda code, days: (history, "unit_history"),
        quote_loader=lambda code: SimpleNamespace(price=137.41, circ_mv=2184.8 * 100000000),
        max_price_points=60,
    )

    chip = fetcher.get_chip_distribution("300274")

    assert chip is not None
    assert chip.source == "local_chip_model:unit_history:float_share_derived"
    assert len(chip.distribution) > 0


class _DummyChipFetcher:
    def __init__(self, name, priority, chip_priority, chip):
        self.name = name
        self.priority = priority
        self.chip_priority = chip_priority
        self.chip = chip

    def get_chip_distribution(self, stock_code):
        return self.chip


def test_chip_manager_uses_local_model_before_akshare_summary() -> None:
    local_chip = ChipDistribution(
        code="600519",
        source="local_chip_model:unit",
        distribution=[ChipDistributionPoint(price=10.0, percent=1.0)],
    )
    akshare_chip = ChipDistribution(code="600519", source="akshare_em", avg_cost=118.5)
    manager = DataFetcherManager(
        fetchers=[
            _DummyChipFetcher("IfindChipFetcher", priority=99, chip_priority=-2, chip=None),
            _DummyChipFetcher("TushareFetcher", priority=2, chip_priority=1, chip=None),
            _DummyChipFetcher("LocalChipModelFetcher", priority=98, chip_priority=2, chip=local_chip),
            _DummyChipFetcher("AkshareFetcher", priority=1, chip_priority=3, chip=akshare_chip),
        ]
    )

    assert manager.get_chip_distribution("600519") is local_chip
