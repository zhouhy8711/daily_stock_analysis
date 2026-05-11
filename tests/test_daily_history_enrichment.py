# -*- coding: utf-8 -*-
from types import SimpleNamespace

import pandas as pd

from src.services.daily_history_enrichment import enrich_daily_history_with_quote_fields


def test_enrich_daily_history_with_quote_fields_derives_valuation_and_turnover() -> None:
    df = pd.DataFrame(
        [
            {
                "date": "2026-01-19",
                "open": 29.27,
                "high": 29.86,
                "low": 29.0,
                "close": 29.6,
                "volume": 10_000,
                "amount": 29_600_000,
            }
        ]
    )
    quote = SimpleNamespace(
        price=29.6,
        pe_ratio=36.47,
        total_shares=101_185_810,
        float_shares=96_709_459,
    )

    enriched = enrich_daily_history_with_quote_fields(df, "002859", quote=quote)

    assert enriched.iloc[0]["total_shares"] == 101_185_810
    assert enriched.iloc[0]["float_shares"] == 96_709_459
    assert round(enriched.iloc[0]["total_mv"], 2) == round(101_185_810 * 29.6, 2)
    assert round(enriched.iloc[0]["circ_mv"], 2) == round(96_709_459 * 29.6, 2)
    assert enriched.iloc[0]["pe_ratio"] == 36.47
    assert round(enriched.iloc[0]["turnover_rate"], 4) == round((1_000_000 / 96_709_459) * 100, 4)


def test_enrich_daily_history_keeps_existing_optional_metrics() -> None:
    df = pd.DataFrame(
        [
            {
                "date": "2026-01-19",
                "close": 29.6,
                "volume": 10_000,
                "amount": 29_600_000,
                "turnover_rate": 5.72,
                "pe_ratio": 30.0,
                "total_mv": 100.0,
                "circ_mv": 90.0,
            }
        ]
    )
    quote = SimpleNamespace(
        price=29.6,
        pe_ratio=36.47,
        total_shares=101_185_810,
        float_shares=96_709_459,
    )

    enriched = enrich_daily_history_with_quote_fields(df, "002859", quote=quote)

    assert enriched.iloc[0]["turnover_rate"] == 5.72
    assert enriched.iloc[0]["pe_ratio"] == 30.0
    assert enriched.iloc[0]["total_mv"] == 100.0
    assert enriched.iloc[0]["circ_mv"] == 90.0
