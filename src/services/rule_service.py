# -*- coding: utf-8 -*-
"""Service layer for stock rules."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from src.config import get_config
from src.repositories.rule_repo import RuleRepository
from src.rules.engine import AGGREGATE_METHODS, COMPARE_OPERATORS, evaluate_rule_at_index, evaluate_rule_history
from src.rules.metrics import METRIC_BY_KEY, build_metric_frame, get_metric_registry
from src.services.stock_service import StockService

logger = logging.getLogger(__name__)

ALLOWED_OPERATORS = {
    ">",
    ">=",
    "<",
    "<=",
    "=",
    "!=",
    "between",
    "not_between",
    "consecutive",
    "frequency",
    "trend_up",
    "trend_down",
    "new_high",
    "new_low",
    "exists",
    "not_exists",
}
DISABLED_OPERATORS = {"cross_up", "cross_down"}
MAX_RULE_TARGET_CODES = 10000
RUN_MODES = {"latest", "history"}


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


class RuleValidationError(ValueError):
    """Raised when a rule definition is invalid."""


class RuleService:
    """Rules orchestration service."""

    def __init__(self, repo: Optional[RuleRepository] = None, stock_service: Optional[StockService] = None):
        self.repo = repo or RuleRepository()
        self.stock_service = stock_service or StockService()

    def get_metrics(self) -> List[Dict[str, Any]]:
        return get_metric_registry()

    def list_rules(self) -> List[Dict[str, Any]]:
        return self.repo.list_rules()

    def get_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        return self.repo.get_rule(rule_id)

    def create_rule(self, payload: Any) -> Dict[str, Any]:
        data = _model_to_dict(payload)
        definition = data.get("definition") or {}
        self.validate_definition(definition)
        data["definition"] = definition
        return self.repo.create_rule(data)

    def update_rule(self, rule_id: int, payload: Any) -> Optional[Dict[str, Any]]:
        data = {key: value for key, value in _model_to_dict(payload).items() if value is not None}
        definition = data.get("definition")
        if definition is not None:
            self.validate_definition(definition)
        return self.repo.update_rule(rule_id, data)

    def delete_rule(self, rule_id: int) -> bool:
        return self.repo.delete_rule(rule_id)

    def delete_run(self, run_id: int) -> bool:
        return self.repo.delete_run(run_id)

    def validate_definition(self, definition: Dict[str, Any]) -> None:
        if str(definition.get("period") or "daily") != "daily":
            raise RuleValidationError("第一版规则模块仅支持 daily 周期")

        groups = definition.get("groups") or []
        if not groups:
            raise RuleValidationError("规则至少需要一个条件组")

        for group in groups:
            conditions = group.get("conditions") or []
            if not conditions:
                raise RuleValidationError("每个条件组至少需要一个子条件")
            for condition in conditions:
                self._validate_condition(condition)

        target = definition.get("target") or {}
        scope = target.get("scope") or "watchlist"
        if scope not in {"watchlist", "all_a_shares", "custom"}:
            raise RuleValidationError("股票范围仅支持 watchlist/all_a_shares/custom")
        if scope == "custom" and not self._normalize_codes(target.get("stock_codes") or []):
            raise RuleValidationError("自定义股票范围至少需要一个股票代码")

    def _validate_condition(self, condition: Dict[str, Any]) -> None:
        left = condition.get("left") or {}
        metric = left.get("metric")
        if metric not in METRIC_BY_KEY:
            raise RuleValidationError(f"不支持的指标 key: {metric}")

        operator = str(condition.get("operator") or "")
        if operator in DISABLED_OPERATORS:
            raise RuleValidationError("上穿/下穿暂未纳入本版规则模块")
        if operator not in ALLOWED_OPERATORS:
            raise RuleValidationError(f"不支持的操作符: {operator}")

        if operator in {"consecutive", "frequency"}:
            compare = str(condition.get("compare") or "")
            if compare not in COMPARE_OPERATORS:
                raise RuleValidationError("连续/频次条件需要有效的 compare 操作符")
            if int(condition.get("lookback") or 0) <= 0:
                raise RuleValidationError("连续/频次条件需要 lookback")
            if operator == "frequency" and int(condition.get("min_count") or 0) <= 0:
                raise RuleValidationError("频次条件需要 min_count")
            self._validate_value_expression(condition.get("right"))
            return

        if operator in {"trend_up", "trend_down", "new_high", "new_low", "exists", "not_exists"}:
            return

        if operator in {"between", "not_between"}:
            right = condition.get("right") or {}
            if not right.get("min") or not right.get("max"):
                raise RuleValidationError("区间条件需要 min/max")
            self._validate_value_expression(right.get("min"))
            self._validate_value_expression(right.get("max"))
            return

        self._validate_value_expression(condition.get("right"))

    def _validate_value_expression(self, expr: Optional[Dict[str, Any]]) -> None:
        if not expr:
            raise RuleValidationError("比较条件需要右侧值")

        value_type = str(expr.get("type") or "literal")
        if value_type == "literal":
            if expr.get("value") is None:
                raise RuleValidationError("固定数值条件需要 value")
            return

        metric = expr.get("metric")
        if metric not in METRIC_BY_KEY:
            raise RuleValidationError(f"不支持的右侧指标 key: {metric}")

        if value_type == "aggregate":
            if str(expr.get("method") or "avg") not in AGGREGATE_METHODS:
                raise RuleValidationError("不支持的历史聚合方法")
            if int(expr.get("window") or 0) <= 0:
                raise RuleValidationError("历史聚合需要 window")

    def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        return self.repo.list_runs(limit=limit)

    def list_run_matches(self, run_id: int) -> List[Dict[str, Any]]:
        return self.repo.list_matches(run_id)

    def run_rule(
        self,
        rule_id: int,
        mode: str = "history",
        target_override: Optional[Dict[str, Any]] = None,
        start_date: Any = None,
        end_date: Any = None,
    ) -> Dict[str, Any]:
        rule = self.repo.get_rule(rule_id)
        if rule is None:
            raise KeyError(f"rule not found: {rule_id}")

        definition = rule.get("definition") or {}
        if target_override is not None:
            definition = {
                **definition,
                "target": target_override,
            }
        self.validate_definition(definition)
        run_mode = self._normalize_run_mode(mode)
        date_from, date_to = self._normalize_date_range(start_date, end_date)
        stock_codes = self._resolve_target_codes(definition.get("target") or {})
        run_id = self.repo.create_run(rule_id, len(stock_codes))
        started_at = datetime.now()
        matches: List[Dict[str, Any]] = []
        errors: List[str] = []

        try:
            for code in stock_codes:
                try:
                    match = self._evaluate_stock(rule, definition, code, run_mode, date_from, date_to)
                except Exception as exc:
                    logger.warning("规则 %s 执行 %s 失败: %s", rule_id, code, exc)
                    errors.append(f"{code}:{type(exc).__name__}")
                    continue
                if match:
                    matches.append(match)

            status = "completed" if not errors else "partial"
            match_count, duration_ms = self.repo.finish_run(
                run_id=run_id,
                rule_id=rule_id,
                status=status,
                started_at=started_at,
                matches=matches,
                error=";".join(errors) if errors else None,
            )
            return {
                "run_id": run_id,
                "rule_id": rule_id,
                "status": status,
                "target_count": len(stock_codes),
                "match_count": match_count,
                "event_count": self._count_match_events(matches),
                "mode": run_mode,
                "duration_ms": duration_ms,
                "matches": matches,
                "errors": errors,
            }
        except Exception as exc:
            self.repo.finish_run(
                run_id=run_id,
                rule_id=rule_id,
                status="failed",
                started_at=started_at,
                matches=[],
                error=str(exc),
            )
            raise

    @staticmethod
    def _normalize_run_mode(mode: str) -> str:
        run_mode = str(mode or "history").strip().lower()
        if run_mode not in RUN_MODES:
            raise RuleValidationError("运行模式仅支持 latest/history")
        return run_mode

    @staticmethod
    def _count_match_events(matches: List[Dict[str, Any]]) -> int:
        return sum(len(match.get("matched_events") or []) for match in matches)

    @classmethod
    def _normalize_date_range(cls, start_date: Any, end_date: Any) -> tuple[Optional[date], Optional[date]]:
        date_from = cls._coerce_date(start_date)
        date_to = cls._coerce_date(end_date)
        if date_from and date_to and date_from > date_to:
            raise RuleValidationError("开始日期不能晚于结束日期")
        return date_from, date_to

    @staticmethod
    def _coerce_date(value: Any) -> Optional[date]:
        if value is None or value == "":
            return None
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            raise RuleValidationError("日期格式需要为 YYYY-MM-DD")
        return parsed.date()

    def _evaluate_stock(
        self,
        rule: Dict[str, Any],
        definition: Dict[str, Any],
        stock_code: str,
        mode: str,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> Optional[Dict[str, Any]]:
        lookback_days = self._resolve_history_fetch_days(definition, rule, start_date)
        history = self.stock_service.get_history_data(stock_code, period="daily", days=lookback_days)
        history_rows = history.get("data") or []
        if not history_rows:
            return None

        quote = self.stock_service.get_realtime_quote(stock_code) if mode == "latest" else None
        indicator_metrics = self._get_indicator_metrics(stock_code, history_rows, mode)
        metric_frame = build_metric_frame(history_rows, quote, indicator_metrics)
        if metric_frame.empty:
            return None

        if mode == "latest":
            events = self._evaluate_latest_event(definition, metric_frame)
        else:
            events = self._evaluate_history_events(definition, metric_frame, lookback_days, start_date, end_date)
        if not events:
            return None

        matched_events = [self._build_match_event(event) for event in events]
        latest_event = matched_events[-1]
        matched_groups = latest_event.get("matched_groups") or []
        matched_dates = [str(event.get("date")) for event in matched_events if event.get("date")]
        explanation = self._build_history_match_explanation(matched_events)
        return {
            "stock_code": stock_code,
            "stock_name": history.get("stock_name") or (quote or {}).get("stock_name"),
            "matched_groups": matched_groups,
            "matched_dates": matched_dates,
            "matched_events": matched_events,
            "snapshot": latest_event.get("snapshot") or {},
            "explanation": explanation,
        }

    def _evaluate_latest_event(self, definition: Dict[str, Any], metric_frame: pd.DataFrame) -> List[Dict[str, Any]]:
        latest_index = len(metric_frame) - 1
        result = evaluate_rule_at_index(definition, metric_frame, latest_index)
        if not result.get("matched"):
            return []
        row = metric_frame.iloc[latest_index]
        return [{
            "date": str(row.get("date") or latest_index),
            "index": latest_index,
            "matched_groups": result.get("matched_groups") or [],
            "condition_results": result.get("condition_results") or [],
            "snapshot": result.get("snapshot") or {},
        }]

    def _evaluate_history_events(
        self,
        definition: Dict[str, Any],
        metric_frame: pd.DataFrame,
        lookback_days: int,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:
        events = evaluate_rule_history(definition, metric_frame)
        if not events:
            return []
        cutoff = datetime.now().date() - timedelta(days=lookback_days)
        filtered_events: List[Dict[str, Any]] = []
        for event in events:
            parsed = pd.to_datetime(event.get("date"), errors="coerce")
            if pd.isna(parsed):
                filtered_events.append(event)
                continue
            event_date = parsed.date()
            if start_date and event_date < start_date:
                continue
            if end_date and event_date > end_date:
                continue
            if start_date is None and event_date < cutoff:
                continue
            filtered_events.append(event)
        return filtered_events

    def _build_match_event(self, event: Dict[str, Any]) -> Dict[str, Any]:
        matched_groups = event.get("matched_groups") or []
        return {
            "date": str(event.get("date") or ""),
            "index": event.get("index"),
            "matched_groups": matched_groups,
            "condition_results": event.get("condition_results") or [],
            "snapshot": event.get("snapshot") or {},
            "explanation": self._build_match_explanation(matched_groups),
        }

    def _build_history_match_explanation(self, events: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for event in events[-10:]:
            date_text = str(event.get("date") or "")
            explanation = self._build_match_explanation(event.get("matched_groups") or [])
            parts.append(f"{date_text}: {explanation}" if explanation else date_text)
        prefix = f"共 {len(events)} 个交易日命中"
        return f"{prefix}；" + " / ".join(parts)

    def _get_indicator_metrics(
        self,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        mode: str,
    ) -> Dict[str, Any]:
        if mode == "history":
            local_metrics = self._build_history_chip_metrics(stock_code, history_rows)
            if local_metrics.get("chip_distribution"):
                return local_metrics
        return self.stock_service.get_indicator_metrics(stock_code)

    def _build_history_chip_metrics(self, stock_code: str, history_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not history_rows:
            return {}
        try:
            from data_provider.base import normalize_stock_code
            from data_provider.local_chip_model_fetcher import compute_chip_distribution_from_history

            history_df = pd.DataFrame(history_rows)
            chip = compute_chip_distribution_from_history(
                normalize_stock_code(stock_code),
                history_df,
                history_source="rule_backtest",
                window_days=max(len(history_rows), 2),
                include_snapshots=True,
                snapshot_limit=None,
            )
            if chip is None:
                return {}
            return {"chip_distribution": chip.to_dict()}
        except Exception as exc:
            logger.debug("规则历史回测本地筹码模型失败 %s: %s", stock_code, exc)
            return {}

    def _build_match_explanation(self, matched_groups: List[Dict[str, Any]]) -> str:
        parts: List[str] = []
        for group in matched_groups:
            condition_text = [
                str(condition.get("explanation") or "")
                for condition in (group.get("conditions") or [])
                if condition.get("explanation")
            ]
            if condition_text:
                parts.append("；".join(condition_text))
        return " / ".join(parts)

    def _resolve_target_codes(self, target: Dict[str, Any]) -> List[str]:
        scope = target.get("scope") or "watchlist"
        explicit_codes = self._normalize_codes(target.get("stock_codes") or [])
        if explicit_codes:
            return explicit_codes[:MAX_RULE_TARGET_CODES]
        if scope == "watchlist":
            config = get_config()
            try:
                config.refresh_stock_list()
            except Exception as exc:
                logger.debug("刷新 STOCK_LIST 失败，使用当前配置: %s", exc)
            codes = config.stock_list
        else:
            codes = []
        normalized = self._normalize_codes(codes)
        return normalized[:MAX_RULE_TARGET_CODES]

    @staticmethod
    def _normalize_codes(codes: List[Any]) -> List[str]:
        normalized: List[str] = []
        seen = set()
        for raw in codes:
            code = str(raw or "").strip().upper()
            if not code or code in seen:
                continue
            seen.add(code)
            normalized.append(code)
        return normalized

    @staticmethod
    def _resolve_history_fetch_days(
        definition: Dict[str, Any],
        rule: Dict[str, Any],
        start_date: Optional[date] = None,
    ) -> int:
        lookback_days = int(definition.get("lookback_days") or rule.get("lookback_days") or 120)
        if start_date is None:
            return lookback_days
        calendar_days = (datetime.now().date() - start_date).days + 10
        return max(lookback_days, calendar_days)
