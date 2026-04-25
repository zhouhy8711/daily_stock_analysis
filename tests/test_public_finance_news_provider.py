# -*- coding: utf-8 -*-
"""Tests for keyless public finance news fallback provider."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pandas as pd

from src.search_service import (
    PublicFinanceNewsProvider,
    SearchResponse,
    SearchResult,
    SearchService,
)


class PublicFinanceNewsProviderTestCase(unittest.TestCase):
    def _fake_akshare(self, *, news_rows=None, notice_rows=None):
        return SimpleNamespace(
            stock_news_em=MagicMock(return_value=pd.DataFrame(news_rows or [])),
            stock_individual_notice_report=MagicMock(return_value=pd.DataFrame(notice_rows or [])),
            stock_info_global_em=MagicMock(return_value=pd.DataFrame([])),
            stock_info_global_sina=MagicMock(return_value=pd.DataFrame([])),
            stock_info_global_futu=MagicMock(return_value=pd.DataFrame([])),
            stock_info_global_cls=MagicMock(return_value=pd.DataFrame([])),
        )

    def test_stock_news_maps_eastmoney_rows_without_api_key(self) -> None:
        fake_ak = self._fake_akshare(
            news_rows=[
                {
                    "新闻标题": "芯原股份新签45.16亿元订单",
                    "新闻内容": "AI算力相关订单占比超85%。",
                    "发布时间": "2026-04-20 18:45:00",
                    "文章来源": "财中社",
                    "新闻链接": "https://finance.example/news",
                }
            ]
        )

        with patch("src.search_service.importlib.util.find_spec", return_value=True), patch(
            "src.search_service.importlib.import_module",
            return_value=fake_ak,
        ):
            provider = PublicFinanceNewsProvider()
            response = provider.search("芯原股份 688521.SH 最新 新闻 重大 事件", max_results=3, days=3)

        self.assertTrue(response.success)
        fake_ak.stock_news_em.assert_called_once_with(symbol="688521")
        self.assertEqual(response.provider, "PublicFinance")
        self.assertEqual(response.results[0].title, "芯原股份新签45.16亿元订单")
        self.assertEqual(response.results[0].source, "财中社")
        self.assertEqual(response.results[0].published_date, "2026-04-20 18:45:00")

    def test_a_share_announcements_are_used_as_recent_related_news(self) -> None:
        fake_ak = self._fake_akshare(
            notice_rows=[
                {
                    "公告标题": "芯原股份:2026年4月20日投资者关系活动记录表",
                    "公告类型": "调研活动",
                    "公告日期": "2026-04-23",
                    "网址": "https://data.example/notice",
                }
            ]
        )

        with patch("src.search_service.importlib.util.find_spec", return_value=True), patch(
            "src.search_service.importlib.import_module",
            return_value=fake_ak,
        ):
            provider = PublicFinanceNewsProvider()
            response = provider.search("芯原股份 688521.SH 公司公告 重要公告", max_results=3, days=3)

        self.assertTrue(response.success)
        self.assertEqual(response.results[0].title, "芯原股份:2026年4月20日投资者关系活动记录表")
        self.assertEqual(response.results[0].source, "东方财富公告")
        self.assertEqual(response.results[0].published_date, "2026-04-23")


class SearchComprehensiveIntelFallbackTestCase(unittest.TestCase):
    def test_dimension_tries_next_provider_when_first_provider_empty(self) -> None:
        today = pd.Timestamp.now().date().isoformat()
        service = SearchService(
            public_finance_news_enabled=False,
            searxng_public_instances_enabled=False,
            news_max_age_days=3,
            news_strategy_profile="short",
        )
        empty_provider = SimpleNamespace(
            is_available=True,
            name="Empty",
            search=MagicMock(
                return_value=SearchResponse(
                    query="empty",
                    results=[],
                    provider="Empty",
                    success=True,
                )
            ),
        )
        next_provider = SimpleNamespace(
            is_available=True,
            name="Next",
            search=MagicMock(
                return_value=SearchResponse(
                    query="fresh",
                    results=[
                        SearchResult(
                            title="新公告",
                            snippet="摘要",
                            url="https://example.com/fresh",
                            source="example.com",
                            published_date=today,
                        )
                    ],
                    provider="Next",
                    success=True,
                )
            ),
        )
        service._providers = [empty_provider, next_provider]

        with patch("src.search_service.time.sleep"):
            intel = service.search_comprehensive_intel("600519", "贵州茅台", max_searches=1)

        self.assertEqual([item.title for item in intel["latest_news"].results], ["新公告"])
        empty_provider.search.assert_called_once()
        next_provider.search.assert_called_once()


if __name__ == "__main__":
    unittest.main()
