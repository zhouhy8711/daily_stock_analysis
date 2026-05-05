from src.rules.engine import evaluate_rule, evaluate_rule_history
from src.rules.metrics import build_metric_frame, get_metric_registry
from src.services.rule_service import RuleService, RuleValidationError


def _history():
    return [
        {"date": "2026-04-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
        {"date": "2026-04-02", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 1200, "amount": 13200, "pct_chg": 10},
        {"date": "2026-04-03", "open": 11, "high": 13, "low": 11, "close": 12, "volume": 1500, "amount": 18000, "pct_chg": 9.09},
        {"date": "2026-04-04", "open": 12, "high": 14, "low": 12, "close": 13, "volume": 1800, "amount": 23400, "pct_chg": 8.33},
        {"date": "2026-04-05", "open": 13, "high": 15, "low": 13, "close": 14, "volume": 2500, "amount": 35000, "pct_chg": 7.69},
    ]


def test_rule_engine_matches_aggregate_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {
                            "type": "aggregate",
                            "metric": "close",
                            "method": "max",
                            "window": 3,
                            "offset": 1,
                        },
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    assert result["matched_groups"][0]["id"] == "g1"


def test_rule_engine_matches_consecutive_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": "consecutive",
                        "compare": ">",
                        "right": {"type": "literal", "value": 11},
                        "lookback": 3,
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True


def test_rule_engine_matches_frequency_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "pct_chg"},
                        "operator": "frequency",
                        "compare": ">",
                        "right": {"type": "literal", "value": 7},
                        "lookback": 4,
                        "min_count": 3,
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True


def test_rule_engine_returns_matched_history_dates():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 12},
                    }
                ],
            }
        ],
    }

    events = evaluate_rule_history(definition, frame)

    assert [event["date"] for event in events] == ["2026-04-04", "2026-04-05"]


def test_metric_frame_maps_chip_ratios_to_percent_values():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "profit_ratio": 0.82,
                "concentration_90": 0.14,
                "concentration_70": 0.08,
                "avg_cost": 12.3,
                "cost_90_low": 9.8,
                "cost_90_high": 13.2,
                "cost_70_low": 10.1,
                "cost_70_high": 12.7,
                "distribution": [
                    {"price": 10.0, "percent": 0.2},
                    {"price": 12.0, "percent": 0.5},
                ],
            }
        },
    )

    latest = frame.iloc[-1]

    assert latest["profit_ratio"] == 82
    assert latest["trapped_ratio"] == 18
    assert round(latest["chip_concentration_90"], 6) == 14
    assert round(latest["chip_concentration_70"], 6) == 8
    assert latest["avg_cost"] == 12.3
    assert latest["cost_90_low"] == 9.8
    assert latest["cost_90_high"] == 13.2
    assert latest["cost_70_low"] == 10.1
    assert latest["cost_70_high"] == 12.7
    assert latest["price_range_90_mid"] == 11.5
    assert round(latest["price_range_90_width"], 6) == 3.4
    assert round(latest["price_range_90_width_pct"], 6) == round(3.4 / 11.5 * 100, 6)
    assert latest["chip_peak_price"] == 12.0
    assert latest["chip_peak_percent"] == 50
    assert round(latest["chip_peak_distance_pct"], 6) == round((14 - 12) / 12 * 100, 6)


def test_metric_registry_groups_indicator_page_metrics_by_chart_area():
    registry = {item["key"]: item for item in get_metric_registry()}

    assert registry["current_price"]["category"] == "核心行情"
    assert registry["total_mv"]["category"] == "核心行情"
    assert registry["close"]["category"] == "K线图"
    assert registry["prev_5d_return_pct"]["category"] == "额外"
    assert registry["prev_20d_return_pct"]["category"] == "额外"
    assert registry["limit_up_price"]["category"] == "K线图"
    assert registry["volume_ma5"]["category"] == "成交量图"
    assert registry["after_hours_amount"]["category"] == "成交量图"
    assert registry["macd_dif"]["category"] == "MACD图"
    assert registry["rsi24"]["category"] == "RSI图"
    assert registry["trapped_ratio"]["category"] == "筹码峰-全部筹码"
    assert registry["main_profit_ratio"]["category"] == "筹码峰-主力筹码"
    assert registry["main_net_volume_pct"]["category"] == "实时监控"
    assert registry["main_force_net"]["category"] == "实时监控"


def test_metric_frame_maps_main_chip_distribution_when_available():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "main_chip_distribution": {
                "profit_ratio": 0.6,
                "avg_cost": 13,
                "cost_90_low": 11,
                "cost_90_high": 15,
                "concentration_90": 0.15,
            }
        },
    )
    latest = frame.iloc[-1]

    assert latest["main_profit_ratio"] == 60
    assert latest["main_trapped_ratio"] == 40
    assert latest["main_avg_cost"] == 13
    assert latest["main_price_range_90_mid"] == 13
    assert latest["main_price_range_90_width"] == 4
    assert latest["main_chip_concentration_90"] == 15
    assert round(latest["main_price_to_avg_cost_pct"], 6) == round((14 - 13) / 13 * 100, 6)


def test_metric_frame_calculates_indicator_page_metrics_for_rules():
    frame = build_metric_frame(_history())
    latest = frame.iloc[-1]

    assert latest["change"] == 1
    assert round(latest["volume_ratio"], 6) == round(2500 / 1600, 6)
    assert latest["amount_ma5"] == 19920
    assert latest["main_force_net"] > 0
    assert round(latest["net_super_large_order"], 6) == round(latest["main_force_net"] * 0.44, 6)


def test_metric_frame_calculates_previous_window_cumulative_return_metrics():
    history = [
        {"date": "2026-04-01", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
        {"date": "2026-04-02", "open": 10, "high": 11, "low": 10, "close": 11, "volume": 1000, "amount": 11000, "pct_chg": 10},
        {"date": "2026-04-03", "open": 11, "high": 12, "low": 11, "close": 12, "volume": 1000, "amount": 12000, "pct_chg": 9.090909},
        {"date": "2026-04-04", "open": 12, "high": 13, "low": 12, "close": 13, "volume": 1000, "amount": 13000, "pct_chg": 8.333333},
        {"date": "2026-04-05", "open": 13, "high": 14, "low": 13, "close": 14, "volume": 1000, "amount": 14000, "pct_chg": 7.692308},
        {"date": "2026-04-06", "open": 14, "high": 15, "low": 14, "close": 15, "volume": 1000, "amount": 15000, "pct_chg": 7.142857},
        {"date": "2026-04-07", "open": 15, "high": 30, "low": 15, "close": 30, "volume": 1000, "amount": 30000, "pct_chg": 100},
    ]

    frame = build_metric_frame(history)
    latest = frame.iloc[-1]

    assert round(latest["prev_5d_return_pct"], 6) == round((15 / 10 - 1) * 100, 6)

    history_20 = [
        {
            "date": f"2026-05-{day + 1:02d}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "amount": close * 1000,
        }
        for day, close in enumerate([100 + step for step in range(21)] + [200])
    ]
    frame_20 = build_metric_frame(history_20)
    latest_20 = frame_20.iloc[-1]

    assert round(latest_20["prev_20d_return_pct"], 6) == round((120 / 100 - 1) * 100, 6)


def test_metric_frame_maps_realtime_quote_metrics_for_rules():
    frame = build_metric_frame(
        _history(),
        quote={
            "current_price": 14.5,
            "change": 0.5,
            "change_percent": 3.57,
            "volume": 2600,
            "amount": 37700,
            "after_hours_volume": 120,
            "after_hours_amount": 1740,
            "total_mv": 1_450_000_000,
            "circ_mv": 1_160_000_000,
            "pe_ratio": 18.2,
            "total_shares": 100_000_000,
            "float_shares": 80_000_000,
            "limit_up_price": 15.4,
            "limit_down_price": 12.6,
            "price_speed": 0.42,
            "entrust_ratio": 11.5,
        },
    )
    latest = frame.iloc[-1]

    assert latest["current_price"] == 14.5
    assert latest["total_mv"] == 1_450_000_000
    assert latest["circ_mv"] == 1_160_000_000
    assert latest["pe_ratio"] == 18.2
    assert latest["after_hours_volume"] == 120
    assert latest["after_hours_amount"] == 1740
    assert latest["limit_up_price"] == 15.4
    assert latest["limit_down_price"] == 12.6
    assert latest["price_speed"] == 0.42
    assert latest["entrust_ratio"] == 11.5
    assert latest["main_net_volume_pct"] > 0


def test_metric_frame_maps_chip_snapshots_by_date():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "date": "2026-04-05",
                "profit_ratio": 0.99,
                "concentration_90": 0.05,
                "avg_cost": 14.2,
                "snapshots": [
                    {
                        "date": "2026-04-03",
                        "profit_ratio": 0.4097,
                        "concentration_90": 0.1167,
                        "avg_cost": 9.54,
                    },
                    {
                        "date": "2026-04-04",
                        "profit_ratio": 86,
                        "concentration_90": 14,
                        "avg_cost": 13.4,
                    },
                ],
            }
        },
    )

    snapshot_row = frame[frame["date"] == "2026-04-03"].iloc[0]
    latest_row = frame.iloc[-1]

    assert round(snapshot_row["profit_ratio"], 6) == 40.97
    assert round(snapshot_row["chip_concentration_90"], 6) == 11.67
    assert snapshot_row["avg_cost"] == 9.54
    assert latest_row["profit_ratio"] == 99
    assert latest_row["chip_concentration_90"] == 5
    assert latest_row["avg_cost"] == 14.2


def test_rule_engine_uses_dated_chip_snapshots_for_history_matches():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "date": "2026-04-05",
                "profit_ratio": 0.99,
                "concentration_90": 0.05,
                "avg_cost": 14.2,
                "snapshots": [
                    {
                        "date": "2026-04-03",
                        "profit_ratio": 0.4097,
                        "concentration_90": 0.1167,
                        "avg_cost": 9.54,
                    },
                    {
                        "date": "2026-04-04",
                        "profit_ratio": 0.20,
                        "concentration_90": 0.10,
                        "avg_cost": 11.0,
                    },
                ],
            }
        },
    )
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "profit_ratio"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 40},
                    },
                    {
                        "id": "c2",
                        "left": {"metric": "profit_ratio"},
                        "operator": "<",
                        "right": {"type": "literal", "value": 50},
                    },
                    {
                        "id": "c3",
                        "left": {"metric": "chip_concentration_90"},
                        "operator": "<",
                        "right": {"type": "literal", "value": 12},
                    },
                ],
            }
        ],
    }

    events = evaluate_rule_history(definition, frame)

    assert [event["date"] for event in events] == ["2026-04-03"]
    assert events[0]["matched_groups"][0]["conditions"][0]["left_metric"] == "profit_ratio"


class _FakeRuleRepo:
    def __init__(self, rule):
        self.rule = rule
        self.finished_matches = None

    def get_rule(self, rule_id):
        return self.rule if rule_id == self.rule["id"] else None

    def create_run(self, rule_id, target_count):
        return 101

    def finish_run(self, **kwargs):
        self.finished_matches = kwargs["matches"]
        return len(kwargs["matches"]), 12


class _FakeStockService:
    def get_history_data(self, stock_code, period="daily", days=30):
        return {
            "stock_code": stock_code,
            "stock_name": "测试股票",
            "period": period,
            "data": [
                {"date": "2026-04-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
                {"date": "2026-04-02", "open": 10, "high": 16, "low": 10, "close": 15, "volume": 3000, "amount": 45000, "pct_chg": 50},
                {"date": "2026-04-03", "open": 15, "high": 15, "low": 9, "close": 10, "volume": 1200, "amount": 12000, "pct_chg": -33.33},
            ],
        }

    def get_realtime_quote(self, stock_code):
        return None

    def get_indicator_metrics(self, stock_code):
        return {}


def _service_rule_for_run_mode():
    return {
        "id": 1,
        "name": "测试规则",
        "lookback_days": 36500,
        "definition": {
            "period": "daily",
            "lookback_days": 36500,
            "target": {"scope": "custom", "stock_codes": ["600519"]},
            "groups": [
                {
                    "id": "g1",
                    "conditions": [
                        {
                            "id": "c1",
                            "left": {"metric": "close", "offset": 0},
                            "operator": ">",
                            "right": {"type": "literal", "value": 12},
                        }
                    ],
                }
            ],
        },
    }


def test_rule_service_run_modes_separate_latest_from_history():
    repo = _FakeRuleRepo(_service_rule_for_run_mode())
    service = RuleService(repo=repo, stock_service=_FakeStockService())

    latest_result = service.run_rule(1, mode="latest")
    history_result = service.run_rule(1, mode="history")

    assert latest_result["mode"] == "latest"
    assert latest_result["match_count"] == 0
    assert latest_result["event_count"] == 0
    assert history_result["mode"] == "history"
    assert history_result["match_count"] == 1
    assert history_result["event_count"] == 1
    assert history_result["matches"][0]["matched_dates"] == ["2026-04-02"]
    assert history_result["matches"][0]["matched_events"][0]["date"] == "2026-04-02"
    assert history_result["matches"][0]["matched_events"][0]["snapshot"]["close"] == 15


def test_rule_service_history_mode_respects_date_range():
    service = RuleService(repo=_FakeRuleRepo(_service_rule_for_run_mode()), stock_service=_FakeStockService())

    included = service.run_rule(1, mode="history", start_date="2026-04-02", end_date="2026-04-02")
    excluded = service.run_rule(1, mode="history", start_date="2026-04-03", end_date="2026-04-03")

    assert included["event_count"] == 1
    assert included["matches"][0]["matched_dates"] == ["2026-04-02"]
    assert excluded["event_count"] == 0
    assert excluded["matches"] == []


def test_rule_service_rejects_cross_operators():
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": "cross_up",
                        "right": {"type": "metric", "metric": "ma20"},
                    }
                ],
            }
        ],
    }

    try:
        RuleService(repo=object(), stock_service=object()).validate_definition(definition)
    except RuleValidationError as exc:
        assert "上穿/下穿" in str(exc)
    else:
        raise AssertionError("cross_up should be rejected")


def test_rule_service_prefers_explicit_codes_for_watchlist_scope():
    service = RuleService(repo=object(), stock_service=object())

    codes = service._resolve_target_codes({
        "scope": "watchlist",
        "stock_codes": ["600519", "600519", " aapl "],
    })

    assert codes == ["600519", "AAPL"]


def test_rule_service_accepts_all_a_shares_scope_with_explicit_codes():
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "all_a_shares", "stock_codes": ["000001", "600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 1},
                    }
                ],
            }
        ],
    }

    RuleService(repo=object(), stock_service=object()).validate_definition(definition)
