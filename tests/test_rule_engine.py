from src.rules.engine import evaluate_rule, evaluate_rule_history
from src.rules.metrics import build_metric_frame
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
                "avg_cost": 12.3,
            }
        },
    )

    latest = frame.iloc[-1]

    assert latest["profit_ratio"] == 82
    assert round(latest["chip_concentration_90"], 6) == 14
    assert latest["avg_cost"] == 12.3


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
