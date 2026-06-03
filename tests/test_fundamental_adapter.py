# -*- coding: utf-8 -*-
"""
Tests for fundamental adapter helpers.
"""

import os
import sys
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from data_provider.fundamental_adapter import (
    AkshareFundamentalAdapter,
    _build_dividend_payload,
    _build_deducted_profit_growth_events,
    _extract_latest_row,
    _parse_dividend_plan_to_per_share,
)


class TestFundamentalAdapter(unittest.TestCase):
    def test_parse_dividend_plan_to_per_share_supports_cn_patterns(self) -> None:
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("10派3元(含税)"), 0.3, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每10股派发2.5元"), 0.25, places=6)
        self.assertAlmostEqual(_parse_dividend_plan_to_per_share("每股派0.8元"), 0.8, places=6)
        self.assertIsNone(_parse_dividend_plan_to_per_share("仅送股，不现金分红"))

    def test_extract_latest_row_returns_none_when_code_mismatch(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600000", "000001"],
                "值": [1, 2],
            }
        )
        row = _extract_latest_row(df, "600519")
        self.assertIsNone(row)

    def test_extract_latest_row_fallback_when_no_code_column(self) -> None:
        df = pd.DataFrame({"值": [1, 2]})
        row = _extract_latest_row(df, "600519")
        self.assertIsNotNone(row)
        self.assertEqual(row["值"], 1)

    def test_dragon_tiger_no_match_with_code_column_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        df = pd.DataFrame(
            {
                "股票代码": ["600000"],
                "日期": ["2026-01-01"],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["is_on_list"])
        self.assertEqual(result["recent_count"], 0)

    def test_dragon_tiger_match_is_ok(self) -> None:
        adapter = AkshareFundamentalAdapter()
        today = pd.Timestamp.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "日期": [today],
            }
        )
        with patch.object(adapter, "_call_df_candidates", return_value=(df, "stock_lhb_stock_statistic_em", [])):
            result = adapter.get_dragon_tiger_flag("600519")
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["is_on_list"])
        self.assertGreaterEqual(result["recent_count"], 1)

    def test_fundamental_bundle_includes_financial_report_and_dividend_payload(self) -> None:
        adapter = AkshareFundamentalAdapter()
        now = datetime.now()
        within_ttm = (now - timedelta(days=30)).strftime("%Y-%m-%d")
        future_day = (now + timedelta(days=10)).strftime("%Y-%m-%d")
        old_day = (now - timedelta(days=500)).strftime("%Y-%m-%d")
        fin_df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "报告期": [within_ttm],
                "营业总收入": [1000.0],
                "归母净利润": [300.0],
                "经营活动产生的现金流量净额": [500.0],
                "净资产收益率": [18.2],
                "营业收入同比": [12.0],
                "净利润同比": [9.5],
            }
        )
        forecast_df = pd.DataFrame({"股票代码": ["600519"], "预告": ["预增"]})
        quick_df = pd.DataFrame({"股票代码": ["600519"], "快报": ["快报摘要"]})
        dividend_df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519", "600519", "600519"],
                "除息日": [within_ttm, within_ttm, future_day, old_day],
                "分配方案": ["10派3元(含税)", "10派3元(含税)", "10派5元", "10派1元"],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            side_effect=[
                (fin_df, "stock_financial_abstract", []),
                (forecast_df, "stock_yjyg_em", []),
                (quick_df, "stock_yjkb_em", []),
                (dividend_df, "stock_fhps_detail_em", []),
                (None, None, []),
                (None, None, []),
            ],
        ):
            result = adapter.get_fundamental_bundle("600519")

        financial_report = result["earnings"].get("financial_report", {})
        self.assertEqual(financial_report.get("report_date"), within_ttm)
        self.assertEqual(financial_report.get("revenue"), 1000.0)
        self.assertEqual(financial_report.get("net_profit_parent"), 300.0)
        self.assertEqual(financial_report.get("operating_cash_flow"), 500.0)
        self.assertEqual(financial_report.get("roe"), 18.2)

        dividend_payload = result["earnings"].get("dividend", {})
        events = dividend_payload.get("events", [])
        self.assertEqual(len(events), 2)  # duplicate + future day filtered
        self.assertEqual(dividend_payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(dividend_payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_build_dividend_payload_returns_empty_when_code_not_matched(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["000001"],
                "除息日": [now],
                "分配方案": ["10派3元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_skips_after_tax_plan(self) -> None:
        now = datetime.now().strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519"],
                "除息日": [now],
                "分配方案": ["10派3元(税后)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload, {})

    def test_build_dividend_payload_ttm_window_boundary(self) -> None:
        now = datetime.now()
        day_365 = (now - timedelta(days=365)).strftime("%Y-%m-%d")
        day_366 = (now - timedelta(days=366)).strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "股票代码": ["600519", "600519"],
                "除息日": [day_365, day_366],
                "分配方案": ["10派3元(含税)", "10派5元(含税)"],
            }
        )

        payload = _build_dividend_payload(df, stock_code="600519")
        self.assertEqual(payload.get("ttm_event_count"), 1)
        self.assertAlmostEqual(payload.get("ttm_cash_dividend_per_share"), 0.3, places=6)

    def test_build_deducted_profit_growth_events_derives_single_quarter_growth(self) -> None:
        df = pd.DataFrame(
            {
                "股票代码": ["600519"] * 5,
                "报告期": [
                    "2025-03-31",
                    "2025-06-30",
                    "2025-09-30",
                    "2025-12-31",
                    "2026-03-31",
                ],
                "公告日期": [
                    "2025-04-20",
                    "2025-08-20",
                    "2025-10-25",
                    "2026-03-30",
                    "2026-04-20",
                ],
                "扣除非经常性损益后的净利润": [100.0, 160.0, 200.0, 240.0, 250.0],
            }
        )

        events = _build_deducted_profit_growth_events(df, stock_code="600519")
        latest = events[-1]

        self.assertEqual(latest["report_date"], "2026-03-31")
        self.assertEqual(latest["announcement_date"], "2026-04-20")
        self.assertEqual(latest["single_quarter_deducted_net_profit"], 250.0)
        self.assertAlmostEqual(latest["deducted_net_profit_yoy_pct"], 150.0, places=6)
        self.assertAlmostEqual(latest["deducted_net_profit_qoq_pct"], 525.0, places=6)

    def test_build_deducted_profit_growth_events_supports_eastmoney_indicator_columns(self) -> None:
        df = pd.DataFrame(
            {
                "SECURITY_CODE": ["600519", "600519"],
                "SECUCODE": ["600519.SH", "600519.SH"],
                "REPORT_DATE": ["2025-12-31 00:00:00", "2026-03-31 00:00:00"],
                "NOTICE_DATE": ["2026-04-17 00:00:00", "2026-04-25 00:00:00"],
                "KCFJCXSYJLR": [100.0, 250.0],
                "DJD_DEDUCTDPNP_YOY": [20.0, 120.0],
                "DJD_DEDUCTDPNP_QOQ": [30.0, 80.0],
                "KCFJCXSYJLRTZ": [15.0, 110.0],
            }
        )

        events = _build_deducted_profit_growth_events(df, stock_code="600519")
        latest = events[-1]

        self.assertEqual(latest["report_date"], "2026-03-31")
        self.assertEqual(latest["announcement_date"], "2026-04-25")
        self.assertEqual(latest["deducted_net_profit"], 250.0)
        self.assertEqual(latest["deducted_net_profit_yoy_pct"], 120.0)
        self.assertEqual(latest["deducted_net_profit_qoq_pct"], 80.0)

    def test_deducted_profit_growth_events_prefers_eastmoney_indicator_source(self) -> None:
        adapter = AkshareFundamentalAdapter()
        df = pd.DataFrame(
            {
                "SECURITY_CODE": ["600519"],
                "REPORT_DATE": ["2026-03-31"],
                "NOTICE_DATE": ["2026-04-25"],
                "KCFJCXSYJLR": [250.0],
                "DJD_DEDUCTDPNP_YOY": [120.0],
                "DJD_DEDUCTDPNP_QOQ": [80.0],
            }
        )

        with patch.object(
            adapter,
            "_call_df_candidates",
            return_value=(df, "stock_financial_analysis_indicator_em", []),
        ) as mocked:
            result = adapter.get_deducted_profit_growth_events("600519")

        candidates = mocked.call_args.args[0]
        self.assertEqual(
            candidates[0],
            ("stock_financial_analysis_indicator_em", {"symbol": "600519.SH", "indicator": "按报告期"}),
        )
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["source_chain"], ["deducted_profit_growth:stock_financial_analysis_indicator_em"])
        self.assertEqual(result["events"][0]["announcement_date"], "2026-04-25")


if __name__ == "__main__":
    unittest.main()
