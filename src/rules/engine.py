# -*- coding: utf-8 -*-
"""Condition evaluation engine for stock rules."""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from src.rules.metrics import METRIC_DEFINITIONS, metric_label

COMPARE_OPERATORS = {">", ">=", "<", "<=", "=", "!="}
AGGREGATE_METHODS = {"max", "min", "avg", "sum", "median", "std"}


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None or pd.isna(value):
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _format_value(value: Optional[float]) -> str:
    if value is None:
        return "无值"
    if abs(value) >= 1000:
        return f"{value:,.2f}"
    return f"{value:.4g}"


def _compare(left: Optional[float], operator: str, right: Optional[float]) -> bool:
    if operator == "exists":
        return left is not None
    if operator == "not_exists":
        return left is None
    if left is None or right is None:
        return False
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == "=":
        return math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    if operator == "!=":
        return not math.isclose(left, right, rel_tol=1e-9, abs_tol=1e-9)
    return False


def _series_value(df: pd.DataFrame, metric: str, index: int, offset: int = 0) -> Optional[float]:
    if metric not in df.columns:
        return None
    target_index = index - int(offset or 0)
    if target_index < 0 or target_index >= len(df):
        return None
    return _to_float(df.iloc[target_index].get(metric))


def _aggregate_value(df: pd.DataFrame, expr: Dict[str, Any], index: int) -> Optional[float]:
    metric = str(expr.get("metric") or "")
    if metric not in df.columns:
        return None
    window = max(1, int(expr.get("window") or 1))
    offset = max(0, int(expr.get("offset") or 0))
    end = index - offset + 1
    start = end - window
    if end <= 0 or start < 0:
        return None
    values = pd.to_numeric(df.iloc[start:end][metric], errors="coerce").dropna()
    if len(values) == 0:
        return None
    method = str(expr.get("method") or "avg")
    if method == "max":
        result = values.max()
    elif method == "min":
        result = values.min()
    elif method == "sum":
        result = values.sum()
    elif method == "median":
        result = values.median()
    elif method == "std":
        result = values.std(ddof=0)
    else:
        result = values.mean()
    multiplier = _to_float(expr.get("multiplier"))
    if multiplier is not None:
        result = result * multiplier
    return _to_float(result)


def resolve_value(df: pd.DataFrame, expr: Optional[Dict[str, Any]], index: int) -> Optional[float]:
    if not expr:
        return None
    value_type = str(expr.get("type") or "metric")
    if value_type == "literal":
        value = _to_float(expr.get("value"))
    elif value_type == "aggregate":
        value = _aggregate_value(df, expr, index)
    else:
        value = _series_value(df, str(expr.get("metric") or ""), index, int(expr.get("offset") or 0))

    multiplier = _to_float(expr.get("multiplier")) if value_type != "aggregate" else None
    if value is not None and multiplier is not None:
        value *= multiplier
    return value


def _describe_value(expr: Optional[Dict[str, Any]]) -> str:
    if not expr:
        return "无右侧值"
    value_type = str(expr.get("type") or "metric")
    if value_type == "literal":
        return _format_value(_to_float(expr.get("value")))
    if value_type == "aggregate":
        method_label = {
            "max": "最大值",
            "min": "最小值",
            "avg": "平均值",
            "sum": "求和",
            "median": "中位数",
            "std": "标准差",
        }.get(str(expr.get("method") or "avg"), "平均值")
        multiplier = _to_float(expr.get("multiplier"))
        suffix = f" * {_format_value(multiplier)}" if multiplier is not None else ""
        offset = int(expr.get("offset") or 0)
        offset_text = "前" if offset > 0 else "近"
        return f"{offset_text}{int(expr.get('window') or 1)}期{metric_label(str(expr.get('metric') or ''))}{method_label}{suffix}"
    return metric_label(str(expr.get("metric") or ""))


def _condition_explanation(
    condition: Dict[str, Any],
    matched: bool,
    left_value: Optional[float],
    right_value: Optional[float] = None,
    extra: Optional[str] = None,
) -> str:
    left = condition.get("left") or {}
    left_label = metric_label(str(left.get("metric") or ""))
    operator = str(condition.get("operator") or "")
    if extra:
        detail = extra
    elif operator in {"exists", "not_exists"}:
        detail = f"{left_label} {operator}"
    else:
        detail = f"{left_label} {_format_value(left_value)} {operator} {_describe_value(condition.get('right'))} {_format_value(right_value)}"
    return f"{'命中' if matched else '未命中'}：{detail}"


def _evaluate_basic(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Tuple[bool, str, Dict[str, Any]]:
    left_expr = condition.get("left") or {}
    operator = str(condition.get("operator") or "")
    left_value = _series_value(df, str(left_expr.get("metric") or ""), index, int(left_expr.get("offset") or 0))

    if operator in {"exists", "not_exists"}:
        matched = _compare(left_value, operator, None)
        return matched, _condition_explanation(condition, matched, left_value), {"left": left_value}

    if operator in {"between", "not_between"}:
        right_expr = condition.get("right") or {}
        min_value = resolve_value(df, right_expr.get("min"), index)
        max_value = resolve_value(df, right_expr.get("max"), index)
        matched = left_value is not None and min_value is not None and max_value is not None and min_value <= left_value <= max_value
        if operator == "not_between":
            matched = not matched if left_value is not None and min_value is not None and max_value is not None else False
        extra = f"{metric_label(str(left_expr.get('metric') or ''))} {_format_value(left_value)} {operator} [{_format_value(min_value)}, {_format_value(max_value)}]"
        return matched, _condition_explanation(condition, matched, left_value, extra=extra), {
            "left": left_value,
            "min": min_value,
            "max": max_value,
        }

    right_value = resolve_value(df, condition.get("right"), index)
    matched = _compare(left_value, operator, right_value)
    return matched, _condition_explanation(condition, matched, left_value, right_value), {
        "left": left_value,
        "right": right_value,
    }


def _evaluate_consecutive(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Tuple[bool, str, Dict[str, Any]]:
    lookback = max(1, int(condition.get("lookback") or 1))
    compare = str(condition.get("compare") or ">")
    start = index - lookback + 1
    if start < 0:
        return False, f"未命中：数据不足，无法检查连续 {lookback} 次", {"matched_count": 0}

    matched_count = 0
    for row_index in range(start, index + 1):
        left_value = _series_value(df, str((condition.get("left") or {}).get("metric") or ""), row_index, 0)
        right_value = resolve_value(df, condition.get("right"), row_index)
        if _compare(left_value, compare, right_value):
            matched_count += 1
        else:
            break
    matched = matched_count == lookback
    extra = f"连续 {lookback} 次满足 {metric_label(str((condition.get('left') or {}).get('metric') or ''))} {compare} {_describe_value(condition.get('right'))}，实际 {matched_count} 次"
    return matched, _condition_explanation(condition, matched, None, extra=extra), {"matched_count": matched_count}


def _evaluate_frequency(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Tuple[bool, str, Dict[str, Any]]:
    lookback = max(1, int(condition.get("lookback") or 1))
    min_count = max(1, int(condition.get("min_count") or 1))
    compare = str(condition.get("compare") or ">")
    start = index - lookback + 1
    if start < 0:
        return False, f"未命中：数据不足，无法检查近 {lookback} 次至少 {min_count} 次", {"matched_count": 0}

    matched_count = 0
    for row_index in range(start, index + 1):
        left_value = _series_value(df, str((condition.get("left") or {}).get("metric") or ""), row_index, 0)
        right_value = resolve_value(df, condition.get("right"), row_index)
        if _compare(left_value, compare, right_value):
            matched_count += 1
    matched = matched_count >= min_count
    extra = f"近 {lookback} 次至少 {min_count} 次满足 {metric_label(str((condition.get('left') or {}).get('metric') or ''))} {compare} {_describe_value(condition.get('right'))}，实际 {matched_count} 次"
    return matched, _condition_explanation(condition, matched, None, extra=extra), {"matched_count": matched_count}


def _evaluate_trend(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Tuple[bool, str, Dict[str, Any]]:
    lookback = max(2, int(condition.get("lookback") or 2))
    metric = str((condition.get("left") or {}).get("metric") or "")
    start = index - lookback + 1
    if start < 0 or metric not in df.columns:
        return False, f"未命中：数据不足，无法检查 {metric_label(metric)} 趋势", {}
    values = [_series_value(df, metric, row_index, 0) for row_index in range(start, index + 1)]
    if any(value is None for value in values):
        return False, f"未命中：{metric_label(metric)} 趋势存在空值", {"values": values}
    operator = str(condition.get("operator") or "")
    if operator == "trend_down":
        matched = all(values[i] < values[i - 1] for i in range(1, len(values)))
        label = "连续下降"
    else:
        matched = all(values[i] > values[i - 1] for i in range(1, len(values)))
        label = "连续上升"
    extra = f"{metric_label(metric)} 最近 {lookback} 期{label}"
    return matched, _condition_explanation(condition, matched, None, extra=extra), {"values": values}


def _evaluate_new_high_low(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Tuple[bool, str, Dict[str, Any]]:
    lookback = max(1, int(condition.get("lookback") or 20))
    metric = str((condition.get("left") or {}).get("metric") or "")
    current_value = _series_value(df, metric, index, 0)
    if index - lookback < 0 or metric not in df.columns:
        return False, f"未命中：数据不足，无法检查 {lookback} 期新高新低", {"left": current_value}
    previous = pd.to_numeric(df.iloc[index - lookback:index][metric], errors="coerce").dropna()
    if len(previous) == 0 or current_value is None:
        return False, f"未命中：{metric_label(metric)} 新高新低存在空值", {"left": current_value}
    operator = str(condition.get("operator") or "")
    threshold = _to_float(previous.min() if operator == "new_low" else previous.max())
    matched = current_value < threshold if operator == "new_low" else current_value > threshold
    label = "新低" if operator == "new_low" else "新高"
    extra = f"{metric_label(metric)} {_format_value(current_value)} 创 {lookback} 期{label}，参考值 {_format_value(threshold)}"
    return matched, _condition_explanation(condition, matched, current_value, threshold, extra=extra), {
        "left": current_value,
        "threshold": threshold,
    }


def evaluate_condition(df: pd.DataFrame, condition: Dict[str, Any], index: int) -> Dict[str, Any]:
    operator = str(condition.get("operator") or "")
    if operator == "consecutive":
        matched, explanation, values = _evaluate_consecutive(df, condition, index)
    elif operator == "frequency":
        matched, explanation, values = _evaluate_frequency(df, condition, index)
    elif operator in {"trend_up", "trend_down"}:
        matched, explanation, values = _evaluate_trend(df, condition, index)
    elif operator in {"new_high", "new_low"}:
        matched, explanation, values = _evaluate_new_high_low(df, condition, index)
    else:
        matched, explanation, values = _evaluate_basic(df, condition, index)
    return {
        "id": condition.get("id"),
        "matched": matched,
        "explanation": explanation,
        "values": values,
    }


def _snapshot_at(metric_frame: pd.DataFrame, index: int) -> Dict[str, Optional[float]]:
    row = metric_frame.iloc[index]
    return {
        key: _to_float(row.get(key))
        for key in (definition.key for definition in METRIC_DEFINITIONS)
        if key in metric_frame.columns
    }


def evaluate_rule_at_index(definition: Dict[str, Any], metric_frame: pd.DataFrame, index: int) -> Dict[str, Any]:
    """Evaluate a rule definition on one row of a metric frame."""
    if metric_frame.empty:
        return {"matched": False, "matched_groups": [], "condition_results": [], "snapshot": {}}
    if index < 0 or index >= len(metric_frame):
        return {"matched": False, "matched_groups": [], "condition_results": [], "snapshot": {}}

    matched_groups: List[Dict[str, Any]] = []
    all_group_results: List[Dict[str, Any]] = []

    for group in definition.get("groups") or []:
        condition_results = [
            evaluate_condition(metric_frame, condition, index)
            for condition in (group.get("conditions") or [])
        ]
        group_matched = bool(condition_results) and all(result["matched"] for result in condition_results)
        group_result = {
            "id": group.get("id"),
            "matched": group_matched,
            "conditions": condition_results,
        }
        all_group_results.append(group_result)
        if group_matched:
            matched_groups.append(group_result)

    return {
        "matched": len(matched_groups) > 0,
        "matched_groups": matched_groups,
        "condition_results": all_group_results,
        "snapshot": _snapshot_at(metric_frame, index),
    }


def evaluate_rule(definition: Dict[str, Any], metric_frame: pd.DataFrame) -> Dict[str, Any]:
    """Evaluate a rule definition on the latest row of a metric frame."""
    return evaluate_rule_at_index(definition, metric_frame, len(metric_frame) - 1)


def evaluate_rule_history(definition: Dict[str, Any], metric_frame: pd.DataFrame) -> List[Dict[str, Any]]:
    """Evaluate a rule definition across all rows and return matched events."""
    if metric_frame.empty:
        return []

    events: List[Dict[str, Any]] = []
    for index in range(len(metric_frame)):
        result = evaluate_rule_at_index(definition, metric_frame, index)
        if not result.get("matched"):
            continue
        row = metric_frame.iloc[index]
        events.append({
            "date": str(row.get("date") or index),
            "index": index,
            "matched_groups": result.get("matched_groups") or [],
            "snapshot": result.get("snapshot") or {},
        })
    return events
