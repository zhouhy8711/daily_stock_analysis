# -*- coding: utf-8 -*-
"""Contract tests for get_chip_distribution agent tool."""

from unittest.mock import patch

from src.agent.tools.data_tools import _handle_get_chip_distribution


class _NoChipFetcherManager:
    def get_chip_distribution(self, stock_code: str):
        return None


def test_get_chip_distribution_marks_unavailable_as_non_retriable() -> None:
    with patch(
        "src.agent.tools.data_tools._get_fetcher_manager",
        return_value=_NoChipFetcherManager(),
    ):
        result = _handle_get_chip_distribution("300308.SZ")

    assert result["code"] == "300308.SZ"
    assert result["status"] == "unavailable"
    assert result["retriable"] is False
    assert "error" in result
    assert "Skip this tool" in result["note"]
