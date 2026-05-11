# -*- coding: utf-8 -*-
"""Unit tests for Baostock fetcher code normalization."""

from data_provider.baostock_fetcher import BaostockFetcher


def test_convert_stock_code_supports_new_a_share_prefixes() -> None:
    fetcher = BaostockFetcher()

    assert fetcher._convert_stock_code("605288") == "sh.605288"
    assert fetcher._convert_stock_code("689009") == "sh.689009"
    assert fetcher._convert_stock_code("001872") == "sz.001872"
    assert fetcher._convert_stock_code("003010") == "sz.003010"
    assert fetcher._convert_stock_code("301018") == "sz.301018"
