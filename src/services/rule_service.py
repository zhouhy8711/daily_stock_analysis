# -*- coding: utf-8 -*-
"""Service layer for stock rules."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

import pandas as pd

from src.config import get_config
from src.core import trading_calendar
from src.repositories.rule_repo import RuleRepository, encode_rule_batch_metadata
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
DATA_POLICIES = {"default", "snapshot_only", "cache_only", "db_only"}
DEFAULT_RULE_RUN_WORKERS = 3


def _model_to_dict(value: Any) -> Dict[str, Any]:
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "dict"):
        return value.dict()
    return dict(value)


class RuleValidationError(ValueError):
    """Raised when a rule definition is invalid."""


class RuleDataUnavailable(RuntimeError):
    """Raised when snapshot-only rule execution cannot read required local data."""


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

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        return self.repo.get_run(run_id)

    def run_rule(
        self,
        rule_id: int,
        mode: str = "history",
        target_override: Optional[Dict[str, Any]] = None,
        start_date: Any = None,
        end_date: Any = None,
        data_policy: str = "default",
    ) -> Dict[str, Any]:
        run_mode = self._normalize_run_mode(mode)
        run_data_policy = self._normalize_data_policy(data_policy)
        date_from, date_to = self._normalize_date_range(start_date, end_date)
        rule, definition, stock_codes = self._prepare_rule_run(rule_id, target_override)
        self._validate_live_snapshot_session(run_mode, run_data_policy, stock_codes)
        run_id = self.repo.create_run(rule_id, len(stock_codes))
        started_at = datetime.now()

        try:
            matches, errors = self._execute_rule_scan(
                rule_id,
                rule,
                definition,
                stock_codes,
                run_mode,
                date_from,
                date_to,
                run_data_policy,
            )

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
                "rule_ids": [rule_id],
                "rule_names": [str(rule.get("name") or f"规则 {rule_id}")],
                "status": status,
                "target_count": len(stock_codes),
                "completed_count": len(stock_codes),
                "match_count": match_count,
                "event_count": self._count_match_events(matches),
                "mode": run_mode,
                "duration_ms": duration_ms,
                "matches": matches,
                "errors": errors,
                **self._build_snapshot_run_metadata(stock_codes, matches=matches),
            }
        except Exception as exc:
            logger.error("规则 %s 执行失败: %s", rule_id, exc, exc_info=True)
            self.repo.finish_run(
                run_id=run_id,
                rule_id=rule_id,
                status="failed",
                started_at=started_at,
                matches=[],
                error=str(exc),
            )
            raise

    def run_rules(
        self,
        rule_ids: List[int],
        mode: str = "history",
        target_override: Optional[Dict[str, Any]] = None,
        start_date: Any = None,
        end_date: Any = None,
        data_policy: str = "default",
    ) -> Dict[str, Any]:
        normalized_rule_ids = [int(rule_id) for rule_id in rule_ids if int(rule_id) > 0]
        if not normalized_rule_ids:
            raise RuleValidationError("至少选择一条规则")

        run_mode = self._normalize_run_mode(mode)
        run_data_policy = self._normalize_data_policy(data_policy)
        date_from, date_to = self._normalize_date_range(start_date, end_date)
        prepared = [
            (rule_id, *self._prepare_rule_run(rule_id, target_override))
            for rule_id in normalized_rule_ids
        ]
        primary_rule_id = prepared[0][0]
        target_count = len(prepared[0][3])
        self._validate_live_snapshot_session(
            run_mode,
            run_data_policy,
            self._resolve_batch_stock_codes(prepared),
        )
        run_id = self.repo.create_run(primary_rule_id, target_count)
        started_at = datetime.now()
        all_matches: List[Dict[str, Any]] = []
        all_errors: List[str] = []
        rule_names = [str(rule.get("name") or f"规则 {rule_id}") for rule_id, rule, _, _ in prepared]

        try:
            rule_worker_count = self._resolve_batch_rule_workers(len(prepared))
            logger.info(
                "批量规则回测开始: run_id=%s, rules=%s, target_count=%s, mode=%s, rule_workers=%s",
                run_id,
                normalized_rule_ids,
                target_count,
                run_mode,
                rule_worker_count,
            )

            def execute_prepared_rule(
                index: int,
                rule_id: int,
                rule: Dict[str, Any],
                definition: Dict[str, Any],
                stock_codes: List[str],
            ) -> tuple[int, List[Dict[str, Any]], List[str]]:
                logger.info(
                    "批量规则回测子任务开始: run_id=%s, rule_id=%s, target_count=%s",
                    run_id,
                    rule_id,
                    len(stock_codes),
                )
                matches, errors = self._execute_rule_scan(
                    rule_id,
                    rule,
                    definition,
                    stock_codes,
                    run_mode,
                    date_from,
                    date_to,
                    run_data_policy,
                )
                for match in matches:
                    match["rule_id"] = rule_id
                    match["rule_name"] = rule.get("name")
                tagged_errors = [f"#{rule_id}:{error}" for error in errors]
                logger.info(
                    "批量规则回测子任务完成: run_id=%s, rule_id=%s, matched_stocks=%s, matched_events=%s, errors=%s",
                    run_id,
                    rule_id,
                    len(matches),
                    self._count_match_events(matches),
                    len(tagged_errors),
                )
                return index, matches, tagged_errors

            rule_results: List[tuple[List[Dict[str, Any]], List[str]]] = [
                ([], []) for _ in prepared
            ]
            with ThreadPoolExecutor(
                max_workers=rule_worker_count,
                thread_name_prefix=f"rule-batch-{run_id}",
            ) as executor:
                future_to_context = {
                    executor.submit(
                        execute_prepared_rule,
                        index,
                        rule_id,
                        rule,
                        definition,
                        stock_codes,
                    ): (index, rule_id)
                    for index, (rule_id, rule, definition, stock_codes) in enumerate(prepared)
                }
                for future in as_completed(future_to_context):
                    index, rule_id = future_to_context[future]
                    try:
                        result_index, matches, errors = future.result()
                        rule_results[result_index] = (matches, errors)
                    except Exception as exc:
                        logger.error(
                            "批量规则回测子任务失败: run_id=%s, rule_id=%s, error=%s",
                            run_id,
                            rule_id,
                            exc,
                            exc_info=True,
                        )
                        rule_results[index] = ([], [f"#{rule_id}:{type(exc).__name__}"])

            for matches, errors in rule_results:
                all_matches.extend(matches)
                all_errors.extend(errors)

            status = "completed" if not all_errors else "partial"
            match_count, duration_ms = self.repo.finish_run(
                run_id=run_id,
                rule_id=primary_rule_id,
                status=status,
                started_at=started_at,
                matches=all_matches,
                error=encode_rule_batch_metadata(
                    normalized_rule_ids,
                    rule_names,
                    all_errors,
                    completed_count=target_count,
                ),
            )
            return {
                "run_id": run_id,
                "rule_id": primary_rule_id,
                "rule_ids": normalized_rule_ids,
                "rule_names": rule_names,
                "status": status,
                "target_count": target_count,
                "completed_count": target_count,
                "match_count": match_count,
                "event_count": self._count_match_events(all_matches),
                "mode": run_mode,
                "duration_ms": duration_ms,
                "matches": all_matches,
                "errors": all_errors,
                **self._build_snapshot_run_metadata(prepared[0][3], matches=all_matches),
            }
        except Exception as exc:
            logger.error("批量规则回测失败: %s", exc, exc_info=True)
            self.repo.finish_run(
                run_id=run_id,
                rule_id=primary_rule_id,
                status="failed",
                started_at=started_at,
                matches=[],
                error=str(exc),
            )
            raise

    def start_run_rules(
        self,
        rule_ids: List[int],
        mode: str = "history",
        target_override: Optional[Dict[str, Any]] = None,
        start_date: Any = None,
        end_date: Any = None,
        data_policy: str = "default",
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        normalized_rule_ids = [int(rule_id) for rule_id in rule_ids if int(rule_id) > 0]
        if not normalized_rule_ids:
            raise RuleValidationError("至少选择一条规则")

        run_mode = self._normalize_run_mode(mode)
        run_data_policy = self._normalize_data_policy(data_policy)
        date_from, date_to = self._normalize_date_range(start_date, end_date)
        prepared = [
            (rule_id, *self._prepare_rule_run(rule_id, target_override))
            for rule_id in normalized_rule_ids
        ]
        primary_rule_id = prepared[0][0]
        rule_names = [str(rule.get("name") or f"规则 {rule_id}") for rule_id, rule, _, _ in prepared]
        stock_codes = self._resolve_batch_stock_codes(prepared)
        self._validate_live_snapshot_session(run_mode, run_data_policy, stock_codes)
        run_id = self.repo.create_run(
            primary_rule_id,
            len(stock_codes),
            error=encode_rule_batch_metadata(
                normalized_rule_ids,
                rule_names,
                [],
                completed_count=0,
            ),
        )
        started_at = datetime.now()
        logger.info(
            "异步批量规则回测已启动: run_id=%s, rules=%s, target_count=%s, mode=%s",
            run_id,
            normalized_rule_ids,
            len(stock_codes),
            run_mode,
        )
        response = {
            "run_id": run_id,
            "rule_id": primary_rule_id,
            "rule_ids": normalized_rule_ids,
            "rule_names": rule_names,
            "status": "running",
            "target_count": len(stock_codes),
            "completed_count": 0,
            "match_count": 0,
            "event_count": 0,
            "mode": run_mode,
            "duration_ms": 0,
            "matches": [],
            "errors": [],
        }
        context = {
            "run_id": run_id,
            "primary_rule_id": primary_rule_id,
            "rule_ids": normalized_rule_ids,
            "rule_names": rule_names,
            "prepared": prepared,
            "stock_codes": stock_codes,
            "run_mode": run_mode,
            "date_from": date_from,
            "date_to": date_to,
            "data_policy": run_data_policy,
            "started_at": started_at,
        }
        return response, context

    def complete_started_run_rules(
        self,
        *,
        run_id: int,
        primary_rule_id: int,
        rule_ids: List[int],
        rule_names: List[str],
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        stock_codes: List[str],
        run_mode: str,
        date_from: Optional[date],
        date_to: Optional[date],
        data_policy: str,
        started_at: datetime,
    ) -> None:
        try:
            all_matches, all_errors = self._execute_batch_scan_by_stock(
                run_id,
                prepared,
                stock_codes,
                run_mode,
                date_from,
                date_to,
                data_policy,
                rule_ids,
                rule_names,
            )
            status = "completed" if not all_errors else "partial"
            self.repo.finish_run(
                run_id=run_id,
                rule_id=primary_rule_id,
                status=status,
                started_at=started_at,
                matches=all_matches,
                error=encode_rule_batch_metadata(
                    rule_ids,
                    rule_names,
                    all_errors,
                    completed_count=len(stock_codes),
                ),
            )
            logger.info(
                "异步批量规则回测完成: run_id=%s, status=%s, matched_stocks=%s, matched_events=%s, errors=%s",
                run_id,
                status,
                len(all_matches),
                self._count_match_events(all_matches),
                len(all_errors),
            )
        except Exception as exc:
            logger.error("异步批量规则回测失败: run_id=%s, error=%s", run_id, exc, exc_info=True)
            self.repo.finish_run(
                run_id=run_id,
                rule_id=primary_rule_id,
                status="failed",
                started_at=started_at,
                matches=[],
                error=encode_rule_batch_metadata(
                    rule_ids,
                    rule_names,
                    [type(exc).__name__],
                    completed_count=0,
                ),
            )

    def _prepare_rule_run(
        self,
        rule_id: int,
        target_override: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], Dict[str, Any], List[str]]:
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
        stock_codes = self._resolve_target_codes(definition.get("target") or {})
        return rule, definition, stock_codes

    @staticmethod
    def _resolve_batch_stock_codes(
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
    ) -> List[str]:
        return list(dict.fromkeys(
            stock_code
            for _rule_id, _rule, _definition, stock_codes in prepared
            for stock_code in stock_codes
        ))

    def _execute_batch_scan_by_stock(
        self,
        run_id: int,
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        stock_codes: List[str],
        run_mode: str,
        date_from: Optional[date],
        date_to: Optional[date],
        data_policy: str,
        rule_ids: List[int],
        rule_names: List[str],
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        worker_count = self._resolve_run_workers(len(stock_codes))
        rule_stock_sets = {
            rule_id: set(rule_stock_codes)
            for rule_id, _rule, _definition, rule_stock_codes in prepared
        }
        ordered_matches: List[List[Dict[str, Any]]] = [[] for _ in stock_codes]
        ordered_errors: List[List[str]] = [[] for _ in stock_codes]
        completed_count = 0

        logger.info(
            "异步批量规则回测后台执行: run_id=%s, rules=%s, target_count=%s, workers=%s",
            run_id,
            rule_ids,
            len(stock_codes),
            worker_count,
        )

        if not stock_codes:
            self.repo.update_run_progress(
                run_id=run_id,
                rule_ids=rule_ids,
                rule_names=rule_names,
                completed_count=0,
            )
            return [], []

        def execute_stock(index: int, stock_code: str) -> tuple[int, List[Dict[str, Any]], List[str]]:
            stock_matches: List[Dict[str, Any]] = []
            stock_errors: List[str] = []
            for rule_id, rule, definition, _rule_stock_codes in prepared:
                if stock_code not in rule_stock_sets.get(rule_id, set()):
                    continue
                try:
                    match = self._evaluate_stock_for_run(
                        rule_id,
                        rule,
                        definition,
                        stock_code,
                        run_mode,
                        date_from,
                        date_to,
                        data_policy,
                        index + 1,
                        len(stock_codes),
                    )
                    if match:
                        match["rule_id"] = rule_id
                        match["rule_name"] = rule.get("name")
                        stock_matches.append(match)
                except Exception as exc:
                    logger.warning(
                        "异步批量规则回测单股失败: run_id=%s, rule_id=%s, stock=%s, error=%s",
                        run_id,
                        rule_id,
                        stock_code,
                        exc,
                    )
                    stock_errors.append(f"#{rule_id}:{stock_code}:{type(exc).__name__}")
            return index, stock_matches, stock_errors

        with ThreadPoolExecutor(
            max_workers=worker_count,
            thread_name_prefix=f"rule-batch-stock-{run_id}",
        ) as executor:
            future_to_context = {
                executor.submit(execute_stock, index, stock_code): (index, stock_code)
                for index, stock_code in enumerate(stock_codes)
            }
            for future in as_completed(future_to_context):
                index, stock_code = future_to_context[future]
                try:
                    result_index, stock_matches, stock_errors = future.result()
                    ordered_matches[result_index] = stock_matches
                    ordered_errors[result_index] = stock_errors
                except Exception as exc:
                    logger.error(
                        "异步批量规则回测单股任务失败: run_id=%s, stock=%s, error=%s",
                        run_id,
                        stock_code,
                        exc,
                        exc_info=True,
                    )
                    ordered_errors[index] = [f"{stock_code}:{type(exc).__name__}"]
                completed_count += 1
                current_errors = [
                    error
                    for stock_errors in ordered_errors
                    for error in stock_errors
                ]
                self.repo.update_run_progress(
                    run_id=run_id,
                    rule_ids=rule_ids,
                    rule_names=rule_names,
                    completed_count=completed_count,
                    errors=current_errors,
                )

        return (
            [match for stock_matches in ordered_matches for match in stock_matches],
            [error for stock_errors in ordered_errors for error in stock_errors],
        )

    def _execute_rule_scan(
        self,
        rule_id: int,
        rule: Dict[str, Any],
        definition: Dict[str, Any],
        stock_codes: List[str],
        run_mode: str,
        date_from: Optional[date],
        date_to: Optional[date],
        data_policy: str,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        worker_count = self._resolve_run_workers(len(stock_codes))
        logger.info(
            "规则 %s 开始执行: mode=%s, target_count=%s, workers=%s, start_date=%s, end_date=%s",
            rule_id,
            run_mode,
            len(stock_codes),
            worker_count,
            date_from,
            date_to,
        )

        ordered_matches: List[Optional[Dict[str, Any]]] = [None] * len(stock_codes)
        ordered_errors: List[Optional[str]] = [None] * len(stock_codes)

        if stock_codes:
            with ThreadPoolExecutor(
                max_workers=worker_count,
                thread_name_prefix=f"rule-{rule_id}",
            ) as executor:
                future_to_context = {
                    executor.submit(
                        self._evaluate_stock_for_run,
                        rule_id,
                        rule,
                        definition,
                        code,
                        run_mode,
                        date_from,
                        date_to,
                        data_policy,
                        index + 1,
                        len(stock_codes),
                    ): (index, code)
                    for index, code in enumerate(stock_codes)
                }

                for future in as_completed(future_to_context):
                    index, code = future_to_context[future]
                    try:
                        ordered_matches[index] = future.result()
                    except RuleDataUnavailable as exc:
                        logger.info(
                            "规则 %s 股票 %s 数据暂不可用，按无命中跳过: %s",
                            rule_id,
                            code,
                            exc,
                        )
                    except Exception as exc:
                        ordered_errors[index] = f"{code}:{type(exc).__name__}"

        return (
            [match for match in ordered_matches if match],
            [error for error in ordered_errors if error],
        )

    @staticmethod
    def _resolve_run_workers(target_count: int) -> int:
        if target_count <= 0:
            return 1
        try:
            configured_workers = int(
                getattr(get_config(), "max_workers", DEFAULT_RULE_RUN_WORKERS)
                or DEFAULT_RULE_RUN_WORKERS
            )
        except Exception:
            configured_workers = DEFAULT_RULE_RUN_WORKERS
        return max(1, min(configured_workers, target_count))

    @staticmethod
    def _resolve_batch_rule_workers(rule_count: int) -> int:
        if rule_count <= 0:
            return 1
        try:
            configured_workers = int(
                getattr(get_config(), "max_workers", DEFAULT_RULE_RUN_WORKERS)
                or DEFAULT_RULE_RUN_WORKERS
            )
        except Exception:
            configured_workers = DEFAULT_RULE_RUN_WORKERS
        return max(1, min(configured_workers, rule_count))

    def _evaluate_stock_for_run(
        self,
        rule_id: int,
        rule: Dict[str, Any],
        definition: Dict[str, Any],
        stock_code: str,
        mode: str,
        start_date: Optional[date],
        end_date: Optional[date],
        data_policy: str,
        ordinal: int,
        total: int,
    ) -> Optional[Dict[str, Any]]:
        started_at = time.monotonic()
        logger.info("规则 %s 股票 %s 开始分析 (%s/%s)", rule_id, stock_code, ordinal, total)
        try:
            match = self._evaluate_stock(rule, definition, stock_code, mode, start_date, end_date, data_policy)
        except RuleDataUnavailable as exc:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.info(
                "规则 %s 股票 %s 数据暂不可用，跳过 (%s/%s)，耗时 %s ms: %s",
                rule_id,
                stock_code,
                ordinal,
                total,
                elapsed_ms,
                exc,
            )
            return None
        except Exception as exc:
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            logger.warning(
                "规则 %s 股票 %s 分析失败 (%s/%s)，耗时 %s ms: %s",
                rule_id,
                stock_code,
                ordinal,
                total,
                elapsed_ms,
                exc,
            )
            raise

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        matched_events = len(match.get("matched_events") or []) if match else 0
        logger.info(
            "规则 %s 股票 %s 分析结束 (%s/%s)，耗时 %s ms，命中交易日 %s 个",
            rule_id,
            stock_code,
            ordinal,
            total,
            elapsed_ms,
            matched_events,
        )
        return match

    @staticmethod
    def _normalize_run_mode(mode: str) -> str:
        run_mode = str(mode or "history").strip().lower()
        if run_mode not in RUN_MODES:
            raise RuleValidationError("运行模式仅支持 latest/history")
        return run_mode

    @staticmethod
    def _normalize_data_policy(data_policy: str) -> str:
        policy = str(data_policy or "default").strip().lower()
        if policy not in DATA_POLICIES:
            raise RuleValidationError("数据策略仅支持 default/snapshot_only/cache_only/db_only")
        return policy

    @staticmethod
    def _validate_live_snapshot_session(run_mode: str, data_policy: str, stock_codes: List[str]) -> None:
        if run_mode != "latest" or data_policy != "snapshot_only":
            return

        known_markets = {
            market
            for market in (trading_calendar.get_market_for_stock(code) for code in stock_codes)
            if market
        }
        if known_markets != {"cn"}:
            return

        if not RuleService._is_cn_live_test_allowed():
            raise RuleValidationError("A股实测仅在交易日 15:00 及以前运行，当前已超过实测时间或非交易日，实测已暂停")

    @staticmethod
    def _is_cn_live_test_allowed(current_time: Optional[datetime] = None) -> bool:
        market_now = trading_calendar.get_market_now("cn", current_time=current_time)
        if market_now.weekday() >= 5:
            return False
        if not trading_calendar.is_market_open("cn", market_now.date()):
            return False
        minute_of_day = market_now.hour * 60 + market_now.minute
        return minute_of_day <= 15 * 60

    def _build_snapshot_run_metadata(
        self,
        stock_codes: List[str],
        *,
        matches: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        snapshot_info_getter = getattr(self.stock_service, "get_realtime_quote_snapshot_info", None)
        snapshot_info = snapshot_info_getter() if callable(snapshot_info_getter) else {}
        unique_codes = list(dict.fromkeys(stock_codes))
        requested = len(unique_codes)
        snapshot_hit_count = sum(
            1
            for code in unique_codes
            if self._get_stock_realtime_quote(code, data_policy="snapshot_only") is not None
        )
        snapshot_id = snapshot_info.get("snapshot_id")
        snapshot_time = snapshot_info.get("snapshot_time")
        if matches and not snapshot_id:
            snapshot_id = self._first_snapshot_value(matches, "snapshot_id")
        if matches and not snapshot_time:
            snapshot_time = self._first_snapshot_value(matches, "snapshot_time")
        return {
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time,
            "snapshot_age_seconds": snapshot_info.get("snapshot_age_seconds"),
            "quote_hit_count": snapshot_hit_count,
            "quote_miss_count": max(0, requested - snapshot_hit_count),
        }

    def _get_stock_history_data(
        self,
        stock_code: str,
        *,
        period: str,
        days: int,
        data_policy: str,
    ) -> Dict[str, Any]:
        try:
            return self.stock_service.get_history_data(
                stock_code,
                period=period,
                days=days,
                data_policy=data_policy,
            )
        except TypeError as exc:
            if "data_policy" not in str(exc):
                raise
            return self.stock_service.get_history_data(
                stock_code,
                period=period,
                days=days,
            )

    def _get_stock_realtime_quote(self, stock_code: str, *, data_policy: str = "default") -> Optional[Dict[str, Any]]:
        getter = getattr(self.stock_service, "get_realtime_quote", None)
        if not callable(getter):
            return None
        try:
            return getter(stock_code, data_policy=data_policy)
        except TypeError as exc:
            if "data_policy" not in str(exc):
                raise
            return getter(stock_code)

    @staticmethod
    def _to_optional_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if pd.isna(number):
            return None
        return number

    @classmethod
    def _last_non_null_value(cls, rows: List[Dict[str, Any]], key: str) -> Any:
        for row in reversed(rows):
            value = row.get(key)
            if value not in (None, ""):
                return value
        return None

    def _build_intraday_hot_table_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """Build a live-test fallback quote from today's local intraday hot table."""
        try:
            intraday = self._get_stock_history_data(
                stock_code,
                period="1m",
                days=1,
                data_policy="snapshot_only",
            )
        except Exception as exc:
            logger.debug("读取 %s 实测分钟热表 fallback 失败: %s", stock_code, exc)
            return None

        raw_rows = intraday.get("data") or []
        if not raw_rows:
            return None

        rows_with_ts: List[tuple[datetime, Dict[str, Any]]] = []
        for raw_row in raw_rows:
            if not isinstance(raw_row, dict):
                continue
            close_price = self._to_optional_float(raw_row.get("close"))
            if close_price is None or close_price <= 0:
                continue
            parsed = pd.to_datetime(raw_row.get("date"), errors="coerce")
            if pd.isna(parsed):
                continue
            row_time = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
            rows_with_ts.append((row_time, dict(raw_row)))

        if not rows_with_ts:
            return None

        rows_with_ts.sort(key=lambda item: item[0])
        latest_time = rows_with_ts[-1][0]
        latest_trade_date = latest_time.date()
        same_day_rows = [
            (row_time, row)
            for row_time, row in rows_with_ts
            if row_time.date() == latest_trade_date
        ]
        if not same_day_rows:
            return None

        rows = [row for _row_time, row in same_day_rows]
        first_row = rows[0]
        last_row = rows[-1]

        open_price = self._to_optional_float(first_row.get("open"))
        if open_price is None:
            open_price = self._to_optional_float(first_row.get("close"))
        close_price = self._to_optional_float(last_row.get("close"))
        if close_price is None or close_price <= 0:
            return None

        highs = [
            value
            for value in (self._to_optional_float(row.get("high")) for row in rows)
            if value is not None
        ]
        lows = [
            value
            for value in (self._to_optional_float(row.get("low")) for row in rows)
            if value is not None
        ]
        volumes = [self._to_optional_float(row.get("volume")) for row in rows]
        amounts = [self._to_optional_float(row.get("amount")) for row in rows]
        snapshot_time = self._last_non_null_value(rows, "snapshot_time") or latest_time.isoformat()
        snapshot_id = self._last_non_null_value(rows, "snapshot_id") or latest_time.strftime("%Y%m%d%H%M%S")

        quote: Dict[str, Any] = {
            "stock_code": stock_code,
            "stock_name": intraday.get("stock_name"),
            "current_price": close_price,
            "open": open_price or close_price,
            "high": max(highs) if highs else close_price,
            "low": min(lows) if lows else close_price,
            "volume": sum(value or 0 for value in volumes),
            "amount": sum(value or 0 for value in amounts),
            "turnover_rate": self._last_non_null_value(rows, "turnover_rate"),
            "change_percent": self._last_non_null_value(rows, "change_percent"),
            "quote_time": latest_time.isoformat(),
            "snapshot_id": str(snapshot_id),
            "snapshot_time": snapshot_time,
            "source": "intraday_hot_table",
        }
        logger.info(
            "实测 %s 实时快照未命中，使用分钟热表聚合 fallback: date=%s rows=%s snapshot_id=%s",
            stock_code,
            latest_trade_date.isoformat(),
            len(rows),
            quote["snapshot_id"],
        )
        return quote

    @staticmethod
    def _count_match_events(matches: List[Dict[str, Any]]) -> int:
        return sum(len(match.get("matched_events") or []) for match in matches)

    @staticmethod
    def _count_notification_events(matches: List[Dict[str, Any]]) -> int:
        total = 0
        for match in matches:
            matched_events = match.get("matched_events") or []
            if matched_events:
                total += len(matched_events)
                continue
            matched_dates = match.get("matched_dates") or []
            total += len(matched_dates)
        return total

    @staticmethod
    def _build_live_match_signature(matches: List[Dict[str, Any]]) -> tuple[tuple[int, str], ...]:
        pairs = {
            (int(match.get("rule_id") or 0), str(match.get("stock_code") or "").strip().upper())
            for match in matches
            if int(match.get("rule_id") or 0) > 0 and str(match.get("stock_code") or "").strip()
        }
        return tuple(sorted(pairs))

    def _get_previous_live_match_signature(self, run_id: int) -> Optional[Dict[str, Any]]:
        getter = getattr(self.repo, "get_previous_live_match_signature", None)
        if not callable(getter):
            return None
        try:
            previous = getter(run_id)
        except Exception as exc:
            logger.warning("读取上一轮实测命中签名失败: run_id=%s, error=%s", run_id, exc)
            return None
        if not isinstance(previous, dict):
            return None
        raw_signature = previous.get("signature") or []
        signature = tuple(
            sorted(
                {
                    (int(rule_id), str(stock_code or "").strip().upper())
                    for rule_id, stock_code in raw_signature
                    if int(rule_id) > 0 and str(stock_code or "").strip()
                }
            )
        )
        if not signature:
            return None
        return {
            "run_id": previous.get("run_id"),
            "signature": signature,
        }

    def notify_live_matches(
        self,
        run_id: int,
        *,
        execution_time: Optional[str] = None,
        rule_ids: Optional[List[int]] = None,
        rule_names: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Push live-test rule matches to every configured notification channel."""
        matches = self.repo.list_matches(run_id)
        event_count = self._count_notification_events(matches)
        if event_count <= 0:
            return {
                "sent": False,
                "message": "本次实测没有命中结果，未推送通知",
                "match_count": len(matches),
                "event_count": 0,
                "deduplicated": False,
            }

        current_signature = self._build_live_match_signature(matches)
        previous = self._get_previous_live_match_signature(run_id)
        if current_signature and previous and current_signature == previous.get("signature"):
            previous_run_id = previous.get("run_id")
            previous_text = f"（上一轮运行 #{previous_run_id}）" if previous_run_id else ""
            logger.info(
                "实测命中与今日上一轮一致，跳过重复推送: "
                "run_id=%s, previous_run_id=%s, signature=%s",
                run_id,
                previous_run_id,
                current_signature,
            )
            message = f"本次实测命中与今日上一轮命中完全一致{previous_text}，已跳过重复推送"
            return {
                "sent": False,
                "message": message,
                "match_count": len(matches),
                "event_count": event_count,
                "deduplicated": True,
            }

        content = self._build_live_match_notification(
            run_id,
            matches,
            execution_time=execution_time,
            rule_ids=rule_ids or [],
            rule_names=rule_names or [],
        )

        try:
            from src.notification import NotificationService

            notifier = NotificationService()
            if not notifier.is_available():
                logger.warning("通知渠道未配置，实测命中未推送: run_id=%s", run_id)
                return {
                    "sent": False,
                    "message": "通知渠道未配置，未推送",
                    "match_count": len(matches),
                    "event_count": event_count,
                    "deduplicated": False,
                }

            sent = notifier.send(content)
            return {
                "sent": bool(sent),
                "message": "实测命中通知已发送" if sent else "实测命中通知发送失败",
                "match_count": len(matches),
                "event_count": event_count,
                "deduplicated": False,
            }
        except Exception as exc:
            logger.error("实测命中通知异常: run_id=%s, error=%s", run_id, exc, exc_info=True)
            return {
                "sent": False,
                "message": f"实测命中通知异常: {type(exc).__name__}",
                "match_count": len(matches),
                "event_count": event_count,
                "deduplicated": False,
            }

    @classmethod
    def _build_live_match_notification(
        cls,
        run_id: int,
        matches: List[Dict[str, Any]],
        *,
        execution_time: Optional[str],
        rule_ids: List[int],
        rule_names: List[str],
    ) -> str:
        metric_labels = {
            str(item.get("key")): str(item.get("label") or item.get("key"))
            for item in get_metric_registry()
        }
        rule_name_by_id = {
            int(rule_id): rule_names[index]
            for index, rule_id in enumerate(rule_ids)
            if index < len(rule_names)
        }
        event_count = cls._count_notification_events(matches)
        snapshot_id = cls._first_snapshot_value(matches, "snapshot_id") or "unknown"
        snapshot_time = cls._first_snapshot_value(matches, "snapshot_time")
        title_time = execution_time or snapshot_time or datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            "# 规则实测命中提醒",
            "",
            f"> 执行时间：{title_time} | 运行 #{run_id} | 快照：{snapshot_id}",
            f"> 命中股票：{len(matches)} 只 | 命中记录：{event_count} 条",
            "",
        ]

        for match in matches:
            stock_name = match.get("stock_name") or ""
            stock_code = match.get("stock_code") or ""
            display_name = f"{stock_name}({stock_code})" if stock_name else str(stock_code)
            events = match.get("matched_events") or []
            if not events and match.get("matched_dates"):
                events = [
                    {
                        "date": matched_date,
                        "matched_groups": match.get("matched_groups") or [],
                        "snapshot": match.get("snapshot") or {},
                    }
                    for matched_date in match.get("matched_dates") or []
                ]

            for event in events:
                rule_id = int(match.get("rule_id") or 0)
                rule_label = rule_name_by_id.get(rule_id) or f"规则 {rule_id}" if rule_id else "规则"
                event_date = event.get("date") or "--"
                lines.extend([
                    f"## #{rule_id} {rule_label}",
                    f"- {display_name} | 命中日：{event_date}",
                ])
                condition_lines = cls._format_matched_conditions(
                    event.get("matched_groups") or match.get("matched_groups") or [],
                    metric_labels,
                )
                if condition_lines:
                    lines.extend(f"  - {line}" for line in condition_lines)
                explanation = event.get("explanation") or match.get("explanation")
                if explanation:
                    lines.append(f"  - 说明：{explanation}")
                lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def _first_snapshot_value(matches: List[Dict[str, Any]], key: str) -> Optional[str]:
        for match in matches:
            snapshot = match.get("snapshot") or {}
            if snapshot.get(key):
                return str(snapshot.get(key))
            for event in match.get("matched_events") or []:
                event_snapshot = event.get("snapshot") or {}
                if event_snapshot.get(key):
                    return str(event_snapshot.get(key))
        return None

    @classmethod
    def _format_matched_conditions(
        cls,
        matched_groups: List[Dict[str, Any]],
        metric_labels: Dict[str, str],
    ) -> List[str]:
        lines: List[str] = []
        for group in matched_groups:
            conditions = group.get("conditions") or []
            for condition in conditions:
                metric_key = str(condition.get("left_metric") or condition.get("leftMetric") or "")
                metric_label = metric_labels.get(metric_key, metric_key or "指标")
                operator = str(condition.get("operator") or "")
                values = condition.get("values") or {}
                left_value = cls._format_condition_value(values.get("left"))
                right_value = cls._format_condition_right_value(values)
                if right_value:
                    lines.append(f"{metric_label}: {left_value} {operator} {right_value}")
                else:
                    lines.append(f"{metric_label}: {left_value} {operator}".strip())
        return lines

    @staticmethod
    def _format_condition_right_value(values: Dict[str, Any]) -> str:
        if values.get("right") is not None:
            return RuleService._format_condition_value(values.get("right"))
        if values.get("threshold") is not None:
            return RuleService._format_condition_value(values.get("threshold"))
        if values.get("min") is not None or values.get("max") is not None:
            return (
                f"{RuleService._format_condition_value(values.get('min'))}"
                f" - {RuleService._format_condition_value(values.get('max'))}"
            )
        if values.get("matched_count") is not None:
            return RuleService._format_condition_value(values.get("matched_count"))
        return ""

    @staticmethod
    def _format_condition_value(value: Any) -> str:
        if value is None:
            return "--"
        if isinstance(value, float):
            return f"{value:,.4f}".rstrip("0").rstrip(".")
        if isinstance(value, int):
            return f"{value:,}"
        return str(value)

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
        data_policy: str = "default",
    ) -> Optional[Dict[str, Any]]:
        lookback_days = self._resolve_history_fetch_days(definition, rule, start_date)
        history_data_policy = data_policy if data_policy in {"snapshot_only", "cache_only", "db_only"} else "default"
        history = self._get_stock_history_data(
            stock_code,
            period="daily",
            days=lookback_days,
            data_policy=history_data_policy,
        )
        history_rows = history.get("data") or []
        if not history_rows:
            if data_policy in {"snapshot_only", "cache_only", "db_only"}:
                raise RuleDataUnavailable("history_cache_miss")
            return None

        quote = (
            self._get_stock_realtime_quote(stock_code, data_policy="snapshot_only")
            if mode == "latest" and data_policy == "snapshot_only"
            else self._get_stock_realtime_quote(stock_code)
            if mode == "latest"
            else None
        )
        if mode == "latest" and data_policy == "snapshot_only" and quote is None:
            quote = self._build_intraday_hot_table_quote(stock_code)
            if quote is None:
                raise RuleDataUnavailable("quote_snapshot_miss")
        indicator_metrics = self._get_indicator_metrics(stock_code, history_rows, mode, data_policy)
        if mode == "latest" and quote is not None:
            quote = StockService._normalize_quote_payload_units(stock_code, quote) or quote
            history_rows = self._sync_latest_history_rows_with_quote(stock_code, history_rows, quote)
        metric_frame = build_metric_frame(history_rows, quote, indicator_metrics)
        if metric_frame.empty:
            return None

        if mode == "latest":
            events = self._evaluate_latest_event(definition, metric_frame)
            if quote is not None:
                self._attach_live_quote_snapshot_metadata(events, quote)
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

    @staticmethod
    def _attach_live_quote_snapshot_metadata(events: List[Dict[str, Any]], quote: Dict[str, Any]) -> None:
        if not events or not quote:
            return
        metadata = {
            "snapshot_id": quote.get("snapshot_id"),
            "snapshot_time": quote.get("snapshot_time"),
            "quote_time": quote.get("quote_time") or quote.get("update_time"),
            "data_source": quote.get("source") or quote.get("data_source"),
        }
        for event in events:
            snapshot = event.setdefault("snapshot", {})
            if not isinstance(snapshot, dict):
                continue
            for key, value in metadata.items():
                if value not in (None, ""):
                    snapshot[key] = value

    @staticmethod
    def _coerce_history_date(value: Any) -> Optional[date]:
        return StockService._normalize_daily_cache_date(value)

    @classmethod
    def _resolve_latest_evaluation_date(cls, stock_code: str, quote: Optional[Dict[str, Any]]) -> Optional[date]:
        market = trading_calendar.get_market_for_stock(stock_code)
        for key in ("snapshot_time", "quote_time", "update_time"):
            parsed = pd.to_datetime((quote or {}).get(key), errors="coerce")
            if pd.isna(parsed):
                continue
            quote_datetime = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
            quote_datetime = trading_calendar.get_market_now(market, current_time=quote_datetime)
            quote_date = quote_datetime.date()
            if market and not trading_calendar.is_market_open(market, quote_date):
                return trading_calendar.get_effective_trading_date(market, current_time=quote_datetime)
            return quote_date

        try:
            market_today = trading_calendar.get_market_now(market).date()
            if market and not trading_calendar.is_market_open(market, market_today):
                return trading_calendar.get_effective_trading_date(market)
            return market_today
        except Exception as calendar_error:
            logger.debug("解析 %s 实时规则判断日失败，按本地自然日处理: %s", stock_code, calendar_error)
            return datetime.now().date()

    @classmethod
    def _sync_latest_history_rows_with_quote(
        cls,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        quote: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if not history_rows:
            return history_rows
        evaluation_date = cls._resolve_latest_evaluation_date(stock_code, quote)
        if evaluation_date is None:
            return history_rows

        rows = []
        for row in history_rows:
            normalized_row = dict(row)
            row_date = cls._coerce_history_date(normalized_row.get("date"))
            if row_date:
                normalized_row["date"] = row_date.isoformat()
            rows.append(normalized_row)
        last_date = cls._coerce_history_date(rows[-1].get("date"))
        if last_date and last_date > evaluation_date:
            return rows

        previous_close = None
        if last_date == evaluation_date and len(rows) >= 2:
            previous_close = rows[-2].get("close")
        elif rows:
            previous_close = rows[-1].get("close")

        realtime_row = StockService._build_realtime_daily_row(
            quote,
            stock_code,
            evaluation_date,
            previous_close,
        )
        realtime_row["date"] = evaluation_date.isoformat()
        realtime_row["snapshot_id"] = quote.get("snapshot_id")
        realtime_row["snapshot_time"] = quote.get("snapshot_time")
        realtime_row["data_source"] = quote.get("source") or "realtime_quote"

        if last_date == evaluation_date:
            rows[-1].update({key: value for key, value in realtime_row.items() if value is not None})
            return rows

        rows.append(realtime_row)
        return rows

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
        data_policy: str = "default",
    ) -> Dict[str, Any]:
        if data_policy == "snapshot_only":
            return self._build_history_chip_metrics(stock_code, history_rows)
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
