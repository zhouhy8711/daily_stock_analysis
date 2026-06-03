# -*- coding: utf-8 -*-
"""Service layer for stock rules."""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import threading
import time
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
from sqlalchemy.exc import OperationalError

from src.config import get_config
from src.core import trading_calendar
from src.repositories.rule_repo import RuleRepository, encode_rule_batch_metadata
from src.rules.engine import (
    AGGREGATE_METHODS,
    COMPARE_OPERATORS,
    PAIR_NUMBER_OPERATOR,
    SANDWICH_NUMBER_OPERATOR,
    evaluate_rule_at_index,
    evaluate_rule_history,
)
from src.rules.metrics import METRIC_BY_KEY, build_metric_frame, get_metric_registry
from src.services.stock_service import StockService
from src.storage import EARNINGS_GAP_DAILY_METRIC_COLUMNS

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
    SANDWICH_NUMBER_OPERATOR,
    PAIR_NUMBER_OPERATOR,
}
DISABLED_OPERATORS = {"cross_up", "cross_down"}
MAX_RULE_TARGET_CODES = 10000
RUN_MODES = {"latest", "history"}
DATA_POLICIES = {"default", "snapshot_only", "cache_only", "db_only"}
RULE_RUN_DATA_POLICY = "db_only"
DEFAULT_RULE_RUN_WORKERS = 3
LIVE_SNAPSHOT_READY_TARGET_THRESHOLD = 1000
RULE_PROGRESS_EVERY_STOCK_LIMIT = 50
RULE_PROGRESS_BATCH_SIZE = 500
RULE_PROGRESS_MEDIUM_BATCH_SIZE = 100
RULE_PROGRESS_MIN_INTERVAL_SECONDS = 5.0
RULE_RUN_HISTORY_CACHE_TTL_SECONDS = 15 * 60
RULE_RUN_HISTORY_CACHE_MAX_ENTRIES = 8
_RULE_RUN_HISTORY_CACHE_LOCK = threading.RLock()
_RULE_RUN_HISTORY_CACHE: Dict[str, Dict[str, Any]] = {}
_LIVE_RULE_RUN_HISTORY_CACHE_LOCK = threading.RLock()
_LIVE_RULE_RUN_HISTORY_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}
_LIVE_RULE_RUN_SCAN_CACHE: Dict[str, Dict[str, Dict[str, Any]]] = {}
EARNINGS_GAP_METRIC_KEYS = set(EARNINGS_GAP_DAILY_METRIC_COLUMNS)
LIVE_REPRICED_CHIP_KEYS = ("chip_distribution", "main_chip_distribution")
FAST_LATEST_SIMPLE_OPERATORS = {
    *COMPARE_OPERATORS,
    "between",
    "not_between",
    "exists",
    "not_exists",
    SANDWICH_NUMBER_OPERATOR,
    PAIR_NUMBER_OPERATOR,
}
FAST_LATEST_QUOTE_METRIC_KEYS = {
    "open",
    "high",
    "low",
    "close",
    "current_price",
    "change_percent",
    "pct_chg",
    "volume",
    "amount",
    "turnover_rate",
}
FAST_LATEST_CHIP_METRIC_KEYS = {
    "profit_ratio",
    "trapped_ratio",
    "profit_trapped_spread",
    "avg_cost",
    "price_to_avg_cost_pct",
    "cost_90_low",
    "cost_90_high",
    "price_range_90_low_pct",
    "price_range_90_high_pct",
    "chip_concentration_90",
    "cost_70_low",
    "cost_70_high",
    "price_range_70_low_pct",
    "price_range_70_high_pct",
    "chip_concentration_70",
    "chip_peak_price",
    "chip_peak_percent",
    "chip_peak_distance_pct",
    "chip_peak_count",
    "chip_single_peak_signal",
    "chip_peak_low_price",
    "chip_peak_high_price",
    "chip_peak_price_ratio",
}
FAST_LATEST_METRIC_KEYS = FAST_LATEST_QUOTE_METRIC_KEYS | FAST_LATEST_CHIP_METRIC_KEYS


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

        if operator in {
            "trend_up",
            "trend_down",
            "new_high",
            "new_low",
            "exists",
            "not_exists",
            SANDWICH_NUMBER_OPERATOR,
            PAIR_NUMBER_OPERATOR,
        }:
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
        run_data_policy = self._normalize_rule_run_data_policy(run_mode, data_policy)
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
                **self._build_rule_run_quote_metadata(
                    stock_codes,
                    run_data_policy,
                    matches=matches,
                ),
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
        run_data_policy = self._normalize_rule_run_data_policy(run_mode, data_policy)
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
                **self._build_rule_run_quote_metadata(
                    self._resolve_batch_stock_codes(prepared),
                    run_data_policy,
                    matches=all_matches,
                ),
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
        live_cache_key: Optional[str] = None,
    ) -> tuple[Dict[str, Any], Optional[Dict[str, Any]]]:
        normalized_rule_ids = [int(rule_id) for rule_id in rule_ids if int(rule_id) > 0]
        if not normalized_rule_ids:
            raise RuleValidationError("至少选择一条规则")

        run_mode = self._normalize_run_mode(mode)
        run_data_policy = self._normalize_rule_run_data_policy(run_mode, data_policy)
        live_history_cache_key = (
            self._normalize_live_cache_key(live_cache_key)
            if run_mode == "latest"
            else None
        )
        date_from, date_to = self._normalize_date_range(start_date, end_date)
        self._cleanup_stale_running_runs()
        prepared = [
            (rule_id, *self._prepare_rule_run(rule_id, target_override))
            for rule_id in normalized_rule_ids
        ]
        primary_rule_id = prepared[0][0]
        rule_names = [str(rule.get("name") or f"规则 {rule_id}") for rule_id, rule, _, _ in prepared]
        stock_codes = self._resolve_batch_stock_codes(prepared)
        prewarm_only = self._is_live_prewarm_session(run_mode, stock_codes)
        require_snapshot_ready = (
            self._requires_live_snapshot_ready(prepared, run_mode, run_data_policy)
            and not prewarm_only
        )
        snapshot_metadata = self._validate_live_snapshot_session(
            run_mode,
            run_data_policy,
            stock_codes,
            require_snapshot_ready=require_snapshot_ready,
            prewarm_only=prewarm_only,
        )
        run_key = self._build_batch_run_key(
            normalized_rule_ids,
            run_mode,
            run_data_policy,
            date_from,
            date_to,
            stock_codes,
            snapshot_metadata.get("snapshot_id"),
        )
        reusable_run_getter = getattr(self.repo, "find_reusable_run_by_key", None)
        reusable_run = reusable_run_getter(run_key) if callable(reusable_run_getter) else None
        if reusable_run is not None:
            response = {
                "run_id": reusable_run["id"],
                "rule_id": reusable_run["rule_id"],
                "rule_ids": reusable_run.get("rule_ids") or normalized_rule_ids,
                "rule_names": reusable_run.get("rule_names") or rule_names,
                "status": reusable_run["status"],
                "target_count": reusable_run.get("target_count", len(stock_codes)),
                "completed_count": reusable_run.get("completed_count", 0),
                "match_count": reusable_run.get("match_count", 0),
                "event_count": reusable_run.get("event_count", 0),
                "mode": run_mode,
                "duration_ms": reusable_run.get("duration_ms") or 0,
                "matches": [],
                "errors": [reusable_run["error"]] if reusable_run.get("error") else [],
                **snapshot_metadata,
                "snapshot_id": reusable_run.get("snapshot_id") or snapshot_metadata.get("snapshot_id"),
                "snapshot_time": reusable_run.get("snapshot_time") or snapshot_metadata.get("snapshot_time"),
                "snapshot_age_seconds": reusable_run.get("snapshot_age_seconds") or snapshot_metadata.get("snapshot_age_seconds"),
                "quote_hit_count": reusable_run.get("quote_hit_count", snapshot_metadata.get("quote_hit_count", 0)),
                "quote_miss_count": reusable_run.get("quote_miss_count", snapshot_metadata.get("quote_miss_count", 0)),
                "reused_run": True,
                "prewarm_only": reusable_run.get("prewarm_only", snapshot_metadata.get("prewarm_only", False)),
                "prewarm_hit_count": reusable_run.get("prewarm_hit_count", snapshot_metadata.get("prewarm_hit_count", 0)),
                "prewarm_miss_count": reusable_run.get("prewarm_miss_count", snapshot_metadata.get("prewarm_miss_count", 0)),
            }
            logger.info(
                "异步批量规则回测复用已有任务: run_id=%s, status=%s, rules=%s, target_count=%s, mode=%s",
                response["run_id"],
                response["status"],
                normalized_rule_ids,
                len(stock_codes),
                run_mode,
            )
            return response, None

        batch_metadata = {
            "run_key": run_key,
            "mode": run_mode,
            "data_policy": run_data_policy,
            "live_cache_key": live_history_cache_key,
            **snapshot_metadata,
        }
        run_id = self.repo.create_run(
            primary_rule_id,
            len(stock_codes),
            error=encode_rule_batch_metadata(
                normalized_rule_ids,
                rule_names,
                [],
                completed_count=0,
                **batch_metadata,
            ),
        )
        started_at = datetime.now()

        if prewarm_only:
            logger.info(
                "A股开盘前规则实测历史数据预热已启动: run_id=%s, rules=%s, target_count=%s",
                run_id,
                normalized_rule_ids,
                len(stock_codes),
            )
        else:
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
            "reused_run": False,
            **snapshot_metadata,
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
            "live_cache_key": live_history_cache_key,
            "started_at": started_at,
            "batch_metadata": batch_metadata,
            "prewarm_only": prewarm_only,
        }
        return response, context

    def _cleanup_stale_running_runs(self) -> None:
        cleanup = getattr(self.repo, "fail_stale_running_runs", None)
        if not callable(cleanup):
            return
        try:
            cleaned = int(cleanup() or 0)
        except Exception as exc:
            logger.warning("清理超时规则实测任务失败: %s", exc)
            return
        if cleaned:
            logger.info("已清理超时规则实测任务: count=%s", cleaned)

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
        batch_metadata: Optional[Dict[str, Any]] = None,
        live_cache_key: Optional[str] = None,
        prewarm_only: bool = False,
    ) -> None:
        if prewarm_only:
            try:
                prewarm_metadata = self._prewarm_rule_scan_cache(
                    prepared,
                    stock_codes,
                    date_from,
                    data_policy,
                    live_cache_key=live_cache_key,
                )
                final_metadata = {
                    **(batch_metadata or {}),
                    **prewarm_metadata,
                }
                self.repo.finish_run(
                    run_id=run_id,
                    rule_id=primary_rule_id,
                    status="completed",
                    started_at=started_at,
                    matches=[],
                    error=encode_rule_batch_metadata(
                        rule_ids,
                        rule_names,
                        [],
                        completed_count=len(stock_codes),
                        **final_metadata,
                    ),
                )
                logger.info(
                    "A股开盘前规则实测历史数据预热完成: run_id=%s, rules=%s, target_count=%s, history_hit=%s, history_miss=%s",
                    run_id,
                    rule_ids,
                    len(stock_codes),
                    int(final_metadata.get("prewarm_hit_count") or 0),
                    int(final_metadata.get("prewarm_miss_count") or 0),
                )
            except Exception as exc:
                logger.error("A股开盘前规则实测历史数据预热失败: run_id=%s, error=%s", run_id, exc, exc_info=True)
                final_metadata = {
                    **(batch_metadata or {}),
                    "prewarm_only": True,
                }
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
                        **final_metadata,
                    ),
                )
            return

        with self._maybe_pause_realtime_quote_archive(run_mode, data_policy, stock_codes):
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
                    batch_metadata,
                    live_cache_key=live_cache_key,
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
                        **(batch_metadata or {}),
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
                        **(batch_metadata or {}),
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

    @classmethod
    def _value_expression_metric_keys(cls, expression: Any) -> List[str]:
        if not isinstance(expression, dict):
            return []

        metric_keys: List[str] = []
        metric = expression.get("metric")
        if isinstance(metric, str) and metric:
            metric_keys.append(metric)
        for key in ("min", "max", "left", "right"):
            metric_keys.extend(cls._value_expression_metric_keys(expression.get(key)))
        return metric_keys

    @classmethod
    def _definition_metric_keys(cls, definition: Dict[str, Any]) -> List[str]:
        metric_keys: List[str] = []
        for group in definition.get("groups") or []:
            for condition in group.get("conditions") or []:
                left_metric = ((condition.get("left") or {}).get("metric"))
                if isinstance(left_metric, str) and left_metric:
                    metric_keys.append(left_metric)
                metric_keys.extend(cls._value_expression_metric_keys(condition.get("right")))
        return metric_keys

    @classmethod
    def _definition_uses_chip_metrics(cls, definition: Dict[str, Any]) -> bool:
        for metric_key in cls._definition_metric_keys(definition):
            metric = METRIC_BY_KEY.get(metric_key)
            if metric and str(metric.category).startswith("筹码峰-"):
                return True
        return False

    @classmethod
    def _definition_uses_earnings_gap_metrics(cls, definition: Dict[str, Any]) -> bool:
        return any(
            metric_key in EARNINGS_GAP_METRIC_KEYS
            for metric_key in cls._definition_metric_keys(definition)
        )

    @staticmethod
    def _expression_has_zero_offset(expression: Dict[str, Any]) -> bool:
        try:
            return int(expression.get("offset") or 0) == 0
        except (TypeError, ValueError):
            return False

    @classmethod
    def _value_expression_fast_latest_compatible(cls, expression: Any) -> bool:
        if not isinstance(expression, dict):
            return False
        value_type = str(expression.get("type") or ("metric" if expression.get("metric") else "literal"))
        if value_type == "literal":
            return True
        if value_type != "metric":
            return False
        metric = str(expression.get("metric") or "")
        return metric in FAST_LATEST_METRIC_KEYS and cls._expression_has_zero_offset(expression)

    @classmethod
    def _condition_fast_latest_compatible(cls, condition: Dict[str, Any]) -> bool:
        operator = str(condition.get("operator") or "")
        if operator not in FAST_LATEST_SIMPLE_OPERATORS:
            return False

        left = condition.get("left") or {}
        left_metric = str(left.get("metric") or "")
        if left_metric not in FAST_LATEST_METRIC_KEYS or not cls._expression_has_zero_offset(left):
            return False

        if operator in {"exists", "not_exists", SANDWICH_NUMBER_OPERATOR, PAIR_NUMBER_OPERATOR}:
            return True

        if operator in {"between", "not_between"}:
            right = condition.get("right") or {}
            return (
                cls._value_expression_fast_latest_compatible(right.get("min"))
                and cls._value_expression_fast_latest_compatible(right.get("max"))
            )

        return cls._value_expression_fast_latest_compatible(condition.get("right"))

    @classmethod
    def _definition_fast_latest_compatible(cls, definition: Dict[str, Any]) -> bool:
        if str(definition.get("period") or "daily").lower() != "daily":
            return False
        groups = definition.get("groups") or []
        if not groups:
            return False
        for group in groups:
            conditions = group.get("conditions") or []
            if not conditions:
                return False
            if not all(cls._condition_fast_latest_compatible(condition) for condition in conditions):
                return False
        return True

    @classmethod
    def _can_use_fast_latest_batch_scan(
        cls,
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        run_mode: str,
        data_policy: str,
    ) -> bool:
        if run_mode != "latest" or data_policy not in {"snapshot_only", "cache_only", "db_only"}:
            return False
        if not prepared:
            return False
        return all(
            cls._definition_fast_latest_compatible(definition)
            for _rule_id, _rule, definition, _stock_codes in prepared
        )

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
        batch_metadata: Optional[Dict[str, Any]] = None,
        live_cache_key: Optional[str] = None,
    ) -> tuple[List[Dict[str, Any]], List[str]]:
        rule_stock_sets = {
            rule_id: set(rule_stock_codes)
            for rule_id, _rule, _definition, rule_stock_codes in prepared
        }
        fast_latest_scan = self._can_use_fast_latest_batch_scan(prepared, run_mode, data_policy)
        fast_latest_requires_chip = fast_latest_scan and any(
            self._definition_uses_chip_metrics(definition)
            for _rule_id, _rule, definition, _stock_codes in prepared
        )
        worker_count = self._resolve_run_workers(len(stock_codes))
        if fast_latest_scan:
            scan_cache = self._prepare_fast_latest_scan_cache(
                stock_codes,
                data_policy,
                require_chip_metrics=fast_latest_requires_chip,
                live_cache_key=live_cache_key,
            )
        else:
            scan_cache = self._prepare_batch_scan_cache(
                prepared,
                stock_codes,
                rule_stock_sets,
                run_mode,
                date_from,
                data_policy,
                live_cache_key=live_cache_key,
            )
        ordered_matches: List[List[Dict[str, Any]]] = [[] for _ in stock_codes]
        ordered_errors: List[List[str]] = [[] for _ in stock_codes]
        completed_count = 0
        target_count = len(stock_codes)
        progress_batch_size = self._resolve_progress_batch_size(target_count)
        progress_min_interval_seconds = self._resolve_progress_min_interval_seconds(target_count, progress_batch_size)
        last_progress_count = 0
        last_progress_update_at = time.monotonic()

        logger.info(
            "异步批量规则回测后台执行: run_id=%s, rules=%s, target_count=%s, workers=%s, fast_latest_scan=%s",
            run_id,
            rule_ids,
            len(stock_codes),
            worker_count,
            fast_latest_scan,
        )

        if not stock_codes:
            self._update_run_progress_best_effort(
                run_id=run_id,
                rule_ids=rule_ids,
                rule_names=rule_names,
                completed_count=0,
                target_count=0,
                metadata=batch_metadata,
            )
            return [], []

        def should_update_progress(count: int) -> bool:
            if progress_batch_size <= 1 or count >= target_count:
                return True
            elapsed_seconds = time.monotonic() - last_progress_update_at
            if progress_min_interval_seconds > 0 and elapsed_seconds < progress_min_interval_seconds:
                return False
            if count - last_progress_count >= progress_batch_size:
                return True
            return (
                progress_min_interval_seconds > 0
                and count > last_progress_count
                and elapsed_seconds >= progress_min_interval_seconds
            )

        def update_progress(count: int) -> None:
            nonlocal last_progress_count, last_progress_update_at
            current_errors = [
                error
                for stock_errors in ordered_errors
                for error in stock_errors
            ]
            self._update_run_progress_best_effort(
                run_id=run_id,
                rule_ids=rule_ids,
                rule_names=rule_names,
                completed_count=count,
                target_count=target_count,
                errors=current_errors,
                metadata=batch_metadata,
            )
            last_progress_count = count
            last_progress_update_at = time.monotonic()

        def execute_stock(index: int, stock_code: str) -> tuple[int, List[Dict[str, Any]], List[str]]:
            stock_matches: List[Dict[str, Any]] = []
            stock_errors: List[str] = []
            applicable_rules = [
                (rule_id, rule, definition)
                for rule_id, rule, definition, _rule_stock_codes in prepared
                if stock_code in rule_stock_sets.get(rule_id, set())
            ]
            if not applicable_rules:
                return index, stock_matches, stock_errors

            try:
                if fast_latest_scan:
                    context = self._build_fast_latest_stock_rule_context(
                        applicable_rules,
                        stock_code,
                        data_policy,
                        scan_cache=scan_cache,
                    )
                else:
                    context = self._build_stock_rule_context(
                        applicable_rules,
                        stock_code,
                        run_mode,
                        date_from,
                        data_policy,
                        scan_cache=scan_cache,
                    )
            except RuleDataUnavailable as exc:
                logger.info(
                    "异步批量规则回测跳过无缓存股票: run_id=%s, stock=%s, reason=%s",
                    run_id,
                    stock_code,
                    exc,
                )
                return index, stock_matches, stock_errors
            except Exception as exc:
                logger.warning(
                    "异步批量规则回测单股数据准备失败: run_id=%s, stock=%s, error=%s",
                    run_id,
                    stock_code,
                    exc,
                )
                return index, stock_matches, [
                    f"#{rule_id}:{stock_code}:{type(exc).__name__}"
                    for rule_id, _rule, _definition in applicable_rules
                ]

            for rule_id, rule, definition in applicable_rules:
                try:
                    match = self._evaluate_stock_from_context(
                        rule_id,
                        rule,
                        definition,
                        stock_code,
                        run_mode,
                        date_from,
                        date_to,
                        context,
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
                if should_update_progress(completed_count):
                    update_progress(completed_count)

        return (
            [match for stock_matches in ordered_matches for match in stock_matches],
            [error for stock_errors in ordered_errors for error in stock_errors],
        )

    def _maybe_pause_realtime_quote_archive(
        self,
        run_mode: str,
        data_policy: str,
        stock_codes: List[str],
    ):
        if (
            run_mode != "latest"
            or data_policy not in {"snapshot_only", "db_only"}
            or len(stock_codes) < LIVE_SNAPSHOT_READY_TARGET_THRESHOLD
        ):
            return nullcontext()
        pauser = getattr(self.stock_service, "pause_realtime_quote_intraday_archive", None)
        if not callable(pauser):
            return nullcontext()
        return pauser("rule_live_scan")

    def _prepare_batch_scan_cache(
        self,
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        stock_codes: List[str],
        rule_stock_sets: Dict[int, Set[str]],
        run_mode: str,
        start_date: Optional[date],
        data_policy: str,
        *,
        end_before_date: Optional[date] = None,
        live_cache_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        if not stock_codes:
            return {}
        if end_before_date is None:
            end_before_date = self._resolve_live_history_end_before_date(run_mode, stock_codes)
        days_by_code = self._resolve_batch_history_days_by_stock(
            prepared,
            stock_codes,
            rule_stock_sets,
            start_date,
        )
        history_data_policy = data_policy if data_policy in {"snapshot_only", "cache_only", "db_only"} else "default"
        history_by_code = self._get_or_load_rule_history_cache(
            stock_codes,
            days_by_code,
            history_data_policy,
            end_before_date=end_before_date,
            live_cache_key=live_cache_key,
        )
        quote_by_code = (
            self._load_batch_quote_cache(stock_codes, data_policy)
            if run_mode == "latest"
            else {}
        )
        logger.info(
            "规则实测数据预热完成: stocks=%s history_hit=%s quote_hit=%s mode=%s policy=%s",
            len(stock_codes),
            len(history_by_code),
            len(quote_by_code),
            run_mode,
            data_policy,
        )
        scan_extras = self._get_live_rule_scan_cache_extras(live_cache_key)
        return {
            "history_by_code": history_by_code,
            "quote_by_code": quote_by_code,
            "indicator_metrics_by_code": scan_extras["indicator_metrics_by_code"],
            "earnings_events_by_code": scan_extras["earnings_events_by_code"],
            "earnings_gap_metrics_by_code": scan_extras["earnings_gap_metrics_by_code"],
            "days_by_code": days_by_code,
        }

    def _prepare_fast_latest_scan_cache(
        self,
        stock_codes: List[str],
        data_policy: str,
        *,
        require_chip_metrics: bool,
        live_cache_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        scan_extras = self._get_live_rule_scan_cache_extras(live_cache_key)
        market_today = trading_calendar.get_market_now("cn").date()
        quote_by_code = self._load_fast_latest_quote_cache(
            stock_codes,
            data_policy,
            as_of=market_today,
        )
        chip_metrics_by_code = (
            self._load_fast_latest_chip_metrics_cache(
                stock_codes,
                as_of=market_today,
                scan_extras=scan_extras,
            )
            if require_chip_metrics
            else {}
        )
        logger.info(
            "规则实测快路径数据预热完成: stocks=%s quote_hit=%s chip_hit=%s policy=%s",
            len(stock_codes),
            len(quote_by_code),
            len(chip_metrics_by_code),
            data_policy,
        )
        return {
            "history_by_code": {},
            "quote_by_code": quote_by_code,
            "indicator_metrics_by_code": scan_extras["indicator_metrics_by_code"],
            "chip_metrics_by_code": chip_metrics_by_code,
            "earnings_events_by_code": scan_extras["earnings_events_by_code"],
            "earnings_gap_metrics_by_code": scan_extras["earnings_gap_metrics_by_code"],
            "days_by_code": {},
            "fast_latest_scan": True,
        }

    def _load_fast_latest_quote_cache(
        self,
        stock_codes: List[str],
        data_policy: str,
        *,
        as_of: date,
    ) -> Dict[str, Dict[str, Any]]:
        quote_cache: Dict[str, Dict[str, Any]] = {}
        normalized_codes = [self._normalize_scan_stock_code(code) for code in stock_codes]
        missing_codes = [code for code in normalized_codes if code and code not in quote_cache]
        if missing_codes:
            loaded_quotes: Dict[str, Dict[str, Any]] = {}
            if data_policy == "db_only":
                db = getattr(getattr(self.stock_service, "repo", None), "db", None)
                loader = getattr(db, "get_intraday_minute_latest_quotes_batch", None)
                if callable(loader):
                    try:
                        loaded_quotes = loader(missing_codes, trade_date=as_of) or {}
                    except Exception as exc:
                        logger.warning("批量读取实测分钟热表 quote 失败: %s", exc)
                        loaded_quotes = {}
            else:
                loaded_quotes = self._load_batch_quote_cache(missing_codes, data_policy)

            for code, payload in (loaded_quotes or {}).items():
                if not isinstance(payload, dict):
                    continue
                cache_key = self._normalize_scan_stock_code(payload.get("stock_code") or payload.get("code") or code)
                if cache_key:
                    quote_cache[cache_key] = dict(payload)

        return {
            code: dict(quote_cache[code])
            for code in normalized_codes
            if code in quote_cache and isinstance(quote_cache.get(code), dict)
        }

    def _load_fast_latest_chip_metrics_cache(
        self,
        stock_codes: List[str],
        *,
        as_of: date,
        scan_extras: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        chip_cache = scan_extras.setdefault("chip_metrics_by_code", {})
        normalized_codes = [self._normalize_scan_stock_code(code) for code in stock_codes]
        missing_codes = [code for code in normalized_codes if code and code not in chip_cache]
        if missing_codes:
            loaded_chips: Dict[str, Dict[str, Any]] = {}
            db = getattr(getattr(self.stock_service, "repo", None), "db", None)
            batch_loader = getattr(db, "get_latest_chip_daily_batch", None)
            if callable(batch_loader):
                try:
                    loaded_chips = batch_loader(missing_codes, as_of=as_of) or {}
                except Exception as exc:
                    logger.warning("批量读取筹码日缓存失败: %s", exc)
                    loaded_chips = {}
            else:
                single_loader = getattr(db, "get_latest_chip_daily", None)
                if callable(single_loader):
                    for code in missing_codes:
                        try:
                            chip = single_loader(code, as_of=as_of)
                        except Exception as exc:
                            logger.debug("读取 %s 筹码日缓存失败: %s", code, exc)
                            chip = None
                        if isinstance(chip, dict):
                            loaded_chips[code] = chip

            for code, payload in (loaded_chips or {}).items():
                if not isinstance(payload, dict):
                    continue
                cache_key = self._normalize_scan_stock_code(payload.get("code") or code)
                if cache_key:
                    chip_cache[cache_key] = dict(payload)

        return {
            code: copy.deepcopy(chip_cache[code])
            for code in normalized_codes
            if code in chip_cache and isinstance(chip_cache.get(code), dict)
        }

    def _prewarm_rule_scan_cache(
        self,
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        stock_codes: List[str],
        start_date: Optional[date],
        data_policy: str,
        *,
        live_cache_key: Optional[str] = None,
    ) -> Dict[str, Any]:
        rule_stock_sets = {
            rule_id: set(rule_stock_codes)
            for rule_id, _rule, _definition, rule_stock_codes in prepared
        }
        market_today = trading_calendar.get_market_now("cn").date()
        scan_cache = self._prepare_batch_scan_cache(
            prepared,
            stock_codes,
            rule_stock_sets,
            "history",
            start_date,
            data_policy,
            end_before_date=market_today,
            live_cache_key=live_cache_key,
        )
        history_by_code = scan_cache.get("history_by_code") or {}
        target_count = len(list(dict.fromkeys(stock_codes)))
        hit_count = len(history_by_code)
        return {
            "prewarm_only": True,
            "prewarm_hit_count": hit_count,
            "prewarm_miss_count": max(0, target_count - hit_count),
        }

    def _resolve_batch_history_days_by_stock(
        self,
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        stock_codes: List[str],
        rule_stock_sets: Dict[int, Set[str]],
        start_date: Optional[date],
    ) -> Dict[str, int]:
        days_by_code: Dict[str, int] = {}
        for stock_code in stock_codes:
            lookback_days = 1
            for rule_id, rule, definition, _rule_stock_codes in prepared:
                if stock_code not in rule_stock_sets.get(rule_id, set()):
                    continue
                lookback_days = max(
                    lookback_days,
                    self._resolve_history_fetch_days(definition, rule, start_date),
                )
            days_by_code[stock_code] = lookback_days
        return days_by_code

    @staticmethod
    def _resolve_live_history_end_before_date(run_mode: str, stock_codes: List[str]) -> Optional[date]:
        if run_mode != "latest":
            return None
        known_markets = {
            market
            for market in (trading_calendar.get_market_for_stock(code) for code in stock_codes)
            if market
        }
        if known_markets == {"cn"}:
            return trading_calendar.get_market_now("cn").date()
        return None

    @staticmethod
    def _normalize_live_cache_key(live_cache_key: Optional[str]) -> Optional[str]:
        value = str(live_cache_key or "").strip()
        if not value:
            return None
        normalized = "".join(
            char
            for char in value[:128]
            if char.isalnum() or char in {"-", "_", ":", "."}
        )
        return normalized or None

    @staticmethod
    def _build_empty_live_scan_cache() -> Dict[str, Dict[str, Any]]:
        return {
            "indicator_metrics_by_code": {},
            "chip_metrics_by_code": {},
            "earnings_events_by_code": {},
            "earnings_gap_metrics_by_code": {},
        }

    def _get_live_rule_scan_cache_extras(self, live_cache_key: Optional[str]) -> Dict[str, Dict[str, Any]]:
        normalized_key = self._normalize_live_cache_key(live_cache_key)
        if not normalized_key:
            return self._build_empty_live_scan_cache()
        with _LIVE_RULE_RUN_HISTORY_CACHE_LOCK:
            scan_cache = _LIVE_RULE_RUN_SCAN_CACHE.setdefault(
                normalized_key,
                self._build_empty_live_scan_cache(),
            )
            for key in self._build_empty_live_scan_cache().keys():
                scan_cache.setdefault(key, {})
            return scan_cache

    def clear_live_rule_history_cache(self, live_cache_key: Optional[str] = None) -> Dict[str, Any]:
        normalized_key = self._normalize_live_cache_key(live_cache_key)
        with _LIVE_RULE_RUN_HISTORY_CACHE_LOCK:
            if normalized_key:
                session_cache = _LIVE_RULE_RUN_HISTORY_CACHE.pop(normalized_key, None)
                scan_cache = _LIVE_RULE_RUN_SCAN_CACHE.pop(normalized_key, None)
                cleared_entries = len(session_cache or {})
                cleared_scan_entries = sum(
                    len(bucket or {})
                    for bucket in (scan_cache or {}).values()
                    if isinstance(bucket, dict)
                )
                remaining_sessions = max(
                    len(_LIVE_RULE_RUN_HISTORY_CACHE),
                    len(_LIVE_RULE_RUN_SCAN_CACHE),
                )
            else:
                cleared_entries = sum(
                    len(session_cache or {})
                    for session_cache in _LIVE_RULE_RUN_HISTORY_CACHE.values()
                )
                cleared_scan_entries = sum(
                    len(bucket or {})
                    for session_cache in _LIVE_RULE_RUN_SCAN_CACHE.values()
                    for bucket in (session_cache or {}).values()
                    if isinstance(bucket, dict)
                )
                _LIVE_RULE_RUN_HISTORY_CACHE.clear()
                _LIVE_RULE_RUN_SCAN_CACHE.clear()
                remaining_sessions = 0
        logger.info(
            "已清理规则实测数据缓存: "
            "live_cache_key=%s history_entries=%s scan_entries=%s remaining_sessions=%s",
            normalized_key or "*",
            cleared_entries,
            cleared_scan_entries,
            remaining_sessions,
        )
        return {
            "live_cache_key": normalized_key,
            "cleared_entries": cleared_entries,
            "cleared_scan_entries": cleared_scan_entries,
            "remaining_sessions": remaining_sessions,
        }

    def _get_or_load_rule_history_cache(
        self,
        stock_codes: List[str],
        days_by_code: Dict[str, int],
        data_policy: str,
        *,
        end_before_date: Optional[date] = None,
        live_cache_key: Optional[str] = None,
    ) -> Dict[str, Dict[str, Any]]:
        loader = getattr(self.stock_service, "get_daily_history_cache_batch", None)
        if not callable(loader):
            return {}
        if data_policy not in {"snapshot_only", "cache_only", "db_only"}:
            history_by_code = loader(stock_codes, days_by_code, data_policy=data_policy)
            return self._filter_history_cache_before_date({
                str(code or "").strip().upper(): payload
                for code, payload in (history_by_code or {}).items()
                if payload
            }, end_before_date)
        normalized_live_cache_key = self._normalize_live_cache_key(live_cache_key)
        cache_key = self._build_rule_history_cache_key(stock_codes, days_by_code, data_policy)
        now = time.monotonic()
        if normalized_live_cache_key:
            with _LIVE_RULE_RUN_HISTORY_CACHE_LOCK:
                session_cache = _LIVE_RULE_RUN_HISTORY_CACHE.setdefault(normalized_live_cache_key, {})
                cached = session_cache.get(cache_key)
                if cached is not None:
                    cached["last_used_at"] = now
                    if end_before_date:
                        cached["history_by_code"] = self._filter_history_cache_before_date(
                            cached.get("history_by_code") or {},
                            end_before_date,
                        )
                    return self._copy_history_cache(cached.get("history_by_code") or {})

            history_by_code = loader(stock_codes, days_by_code, data_policy=data_policy)
            normalized_history = {
                str(code or "").strip().upper(): payload
                for code, payload in (history_by_code or {}).items()
                if payload
            }
            normalized_history = self._filter_history_cache_before_date(normalized_history, end_before_date)
            with _LIVE_RULE_RUN_HISTORY_CACHE_LOCK:
                session_cache = _LIVE_RULE_RUN_HISTORY_CACHE.setdefault(normalized_live_cache_key, {})
                session_cache[cache_key] = {
                    "created_at": now,
                    "last_used_at": now,
                    "history_by_code": self._copy_history_cache(normalized_history),
                }
            return self._copy_history_cache(normalized_history)

        with _RULE_RUN_HISTORY_CACHE_LOCK:
            self._prune_rule_history_cache(now)
            cached = _RULE_RUN_HISTORY_CACHE.get(cache_key)
            if cached is not None:
                cached["last_used_at"] = now
                if end_before_date:
                    cached["history_by_code"] = self._filter_history_cache_before_date(
                        cached.get("history_by_code") or {},
                        end_before_date,
                    )
                return self._copy_history_cache(cached.get("history_by_code") or {})

        history_by_code = loader(stock_codes, days_by_code, data_policy=data_policy)
        normalized_history = {
            str(code or "").strip().upper(): payload
            for code, payload in (history_by_code or {}).items()
            if payload
        }
        normalized_history = self._filter_history_cache_before_date(normalized_history, end_before_date)
        with _RULE_RUN_HISTORY_CACHE_LOCK:
            _RULE_RUN_HISTORY_CACHE[cache_key] = {
                "created_at": now,
                "last_used_at": now,
                "history_by_code": self._copy_history_cache(normalized_history),
            }
            self._prune_rule_history_cache(now)
        return self._copy_history_cache(normalized_history)

    @staticmethod
    def _build_rule_history_cache_key(
        stock_codes: List[str],
        days_by_code: Dict[str, int],
        data_policy: str,
    ) -> str:
        normalized_codes = [str(code or "").strip().upper() for code in stock_codes]
        payload = {
            "codes": normalized_codes,
            "days": {code: int(days_by_code.get(code) or 0) for code in normalized_codes},
            "data_policy": data_policy,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _prune_rule_history_cache(now: Optional[float] = None) -> None:
        current = time.monotonic() if now is None else now
        expired_keys = [
            key
            for key, entry in _RULE_RUN_HISTORY_CACHE.items()
            if current - float(entry.get("created_at") or 0) > RULE_RUN_HISTORY_CACHE_TTL_SECONDS
        ]
        for key in expired_keys:
            _RULE_RUN_HISTORY_CACHE.pop(key, None)
        while len(_RULE_RUN_HISTORY_CACHE) > RULE_RUN_HISTORY_CACHE_MAX_ENTRIES:
            oldest_key = min(
                _RULE_RUN_HISTORY_CACHE,
                key=lambda item: float(_RULE_RUN_HISTORY_CACHE[item].get("last_used_at") or 0),
            )
            _RULE_RUN_HISTORY_CACHE.pop(oldest_key, None)

    @staticmethod
    def _copy_history_cache(history_by_code: Dict[str, Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
        copied: Dict[str, Dict[str, Any]] = {}
        for code, payload in history_by_code.items():
            data_rows = payload.get("data") or []
            copied[code] = {
                **payload,
                "data": [dict(row) for row in data_rows if isinstance(row, dict)],
            }
        return copied

    @classmethod
    def _filter_history_cache_before_date(
        cls,
        history_by_code: Dict[str, Dict[str, Any]],
        end_before_date: Optional[date],
    ) -> Dict[str, Dict[str, Any]]:
        copied = cls._copy_history_cache(history_by_code)
        if end_before_date is None:
            return copied
        for code, payload in list(copied.items()):
            rows = []
            for row in payload.get("data") or []:
                row_date = cls._coerce_history_row_date(row.get("date"))
                if row_date is None or row_date < end_before_date:
                    rows.append(row)
            if rows:
                payload["data"] = rows
            else:
                copied.pop(code, None)
        return copied

    @staticmethod
    def _coerce_history_row_date(value: Any) -> Optional[date]:
        if value in (None, ""):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        if hasattr(parsed, "date"):
            return parsed.date()
        return None

    def _load_batch_quote_cache(
        self,
        stock_codes: List[str],
        data_policy: str,
    ) -> Dict[str, Dict[str, Any]]:
        getter = getattr(self.stock_service, "get_realtime_quotes", None)
        if not callable(getter):
            return {}
        try:
            response = getter(stock_codes, data_policy=data_policy)
        except TypeError as exc:
            if "data_policy" not in str(exc):
                raise
            response = getter(stock_codes)
        items = response.get("items") if isinstance(response, dict) else []
        quote_by_code: Dict[str, Dict[str, Any]] = {}
        for item in items or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("stock_code") or item.get("code") or "").strip().upper()
            if code:
                quote_by_code[code] = dict(item)
        return quote_by_code

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
    def _resolve_progress_batch_size(target_count: int) -> int:
        if target_count <= 0:
            return 1
        if target_count <= RULE_PROGRESS_EVERY_STOCK_LIMIT:
            return 1
        try:
            configured_batch_size = int(
                getattr(get_config(), "rule_progress_batch_size", RULE_PROGRESS_BATCH_SIZE)
                or RULE_PROGRESS_BATCH_SIZE
            )
        except Exception:
            configured_batch_size = RULE_PROGRESS_BATCH_SIZE
        configured_batch_size = max(1, configured_batch_size)
        if target_count <= configured_batch_size:
            return min(RULE_PROGRESS_MEDIUM_BATCH_SIZE, target_count)
        return min(configured_batch_size, target_count)

    @staticmethod
    def _resolve_progress_min_interval_seconds(target_count: int, batch_size: int) -> float:
        if (
            target_count <= RULE_PROGRESS_EVERY_STOCK_LIMIT
            or target_count <= batch_size
            or target_count <= RULE_PROGRESS_BATCH_SIZE
        ):
            return 0.0
        try:
            return max(0.0, float(
                getattr(get_config(), "rule_progress_min_interval_seconds", RULE_PROGRESS_MIN_INTERVAL_SECONDS)
                or 0.0
            ))
        except Exception:
            return RULE_PROGRESS_MIN_INTERVAL_SECONDS

    def _update_run_progress_best_effort(
        self,
        *,
        run_id: int,
        rule_ids: List[int],
        rule_names: List[str],
        completed_count: int,
        target_count: int,
        errors: Optional[List[str]] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        try:
            self.repo.update_run_progress(
                run_id=run_id,
                rule_ids=rule_ids,
                rule_names=rule_names,
                completed_count=completed_count,
                errors=errors,
                metadata=metadata,
            )
        except OperationalError as exc:
            if self._is_sqlite_locked_error(exc):
                logger.warning(
                    "规则实测进度写入遇到 SQLite 写锁，跳过本次刷新: run_id=%s completed=%s/%s",
                    run_id,
                    completed_count,
                    target_count,
                )
                return
            raise

    def _is_sqlite_locked_error(self, exc: OperationalError) -> bool:
        checker = getattr(getattr(self.repo, "db", None), "_is_sqlite_locked_error", None)
        if callable(checker):
            return bool(checker(exc))
        err_text = str(getattr(exc, "orig", exc)).lower()
        return any(
            token in err_text
            for token in (
                "database is locked",
                "database schema is locked",
                "database table is locked",
            )
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

    @classmethod
    def _normalize_rule_run_data_policy(cls, run_mode: str, data_policy: str) -> str:
        requested_policy = cls._normalize_data_policy(data_policy)
        if requested_policy != RULE_RUN_DATA_POLICY:
            logger.info(
                "规则%s数据策略强制使用 %s，忽略请求值 %s",
                "实测" if run_mode == "latest" else "回测",
                RULE_RUN_DATA_POLICY,
                requested_policy,
            )
        return RULE_RUN_DATA_POLICY

    def _validate_live_snapshot_session(
        self,
        run_mode: str,
        data_policy: str,
        stock_codes: List[str],
        *,
        require_snapshot_ready: bool = False,
        prewarm_only: bool = False,
    ) -> Dict[str, Any]:
        if run_mode != "latest":
            return {}

        known_markets = {
            market
            for market in (trading_calendar.get_market_for_stock(code) for code in stock_codes)
            if market
        }
        if known_markets != {"cn"}:
            return {}

        if not RuleService._is_cn_live_test_allowed():
            raise RuleValidationError("A股实测仅在交易日 15:00 及以前运行，当前已超过实测时间或非交易日，实测已暂停")

        if prewarm_only:
            return self._build_preopen_prewarm_run_metadata()

        snapshot_metadata = self._build_rule_run_quote_metadata(stock_codes, data_policy)
        if require_snapshot_ready and (
            not snapshot_metadata.get("snapshot_id")
            or int(snapshot_metadata.get("quote_miss_count") or 0) > 0
        ):
            hit_count = int(snapshot_metadata.get("quote_hit_count") or 0)
            miss_count = int(snapshot_metadata.get("quote_miss_count") or 0)
            requested = hit_count + miss_count
            raise RuleValidationError(
                "A股全市场实测需要先完成本地实时行情预热入库，"
                f"当前本地覆盖 {hit_count}/{requested}，请等待 09:30 后预热完成再运行。"
            )
        return snapshot_metadata

    @staticmethod
    def _is_cn_live_test_allowed(current_time: Optional[datetime] = None) -> bool:
        market_now = trading_calendar.get_market_now("cn", current_time=current_time)
        if market_now.weekday() >= 5:
            return False
        if not trading_calendar.is_market_open("cn", market_now.date()):
            return False
        minute_of_day = market_now.hour * 60 + market_now.minute
        return minute_of_day <= 15 * 60

    @staticmethod
    def _is_cn_live_test_preopen(current_time: Optional[datetime] = None) -> bool:
        market_now = trading_calendar.get_market_now("cn", current_time=current_time)
        if market_now.weekday() >= 5:
            return False
        if not trading_calendar.is_market_open("cn", market_now.date()):
            return False
        minute_of_day = market_now.hour * 60 + market_now.minute
        return minute_of_day < 9 * 60 + 30

    @staticmethod
    def _is_live_prewarm_session(run_mode: str, stock_codes: List[str]) -> bool:
        if run_mode != "latest":
            return False
        known_markets = {
            market
            for market in (trading_calendar.get_market_for_stock(code) for code in stock_codes)
            if market
        }
        return known_markets == {"cn"} and RuleService._is_cn_live_test_preopen()

    @staticmethod
    def _build_preopen_prewarm_run_metadata() -> Dict[str, Any]:
        market_now = trading_calendar.get_market_now("cn")
        return {
            "snapshot_id": f"preopen:{market_now.date().isoformat()}",
            "snapshot_time": market_now.isoformat(),
            "snapshot_age_seconds": 0,
            "quote_hit_count": 0,
            "quote_miss_count": 0,
            "prewarm_only": True,
            "prewarm_hit_count": 0,
            "prewarm_miss_count": 0,
        }

    @staticmethod
    def _build_batch_run_key(
        rule_ids: List[int],
        run_mode: str,
        data_policy: str,
        start_date: Optional[date],
        end_date: Optional[date],
        stock_codes: List[str],
        snapshot_id: Optional[str],
    ) -> str:
        payload = {
            "rule_ids": [int(rule_id) for rule_id in rule_ids],
            "mode": run_mode,
            "data_policy": data_policy,
            "start_date": start_date.isoformat() if start_date else None,
            "end_date": end_date.isoformat() if end_date else None,
            "stock_codes": [str(code or "").strip().upper() for code in stock_codes],
            "snapshot_id": snapshot_id,
        }
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _requires_live_snapshot_ready(
        prepared: List[tuple[int, Dict[str, Any], Dict[str, Any], List[str]]],
        run_mode: str,
        data_policy: str,
    ) -> bool:
        if run_mode != "latest":
            return False
        stock_codes = RuleService._resolve_batch_stock_codes(prepared)
        if len(stock_codes) >= LIVE_SNAPSHOT_READY_TARGET_THRESHOLD:
            return True
        return any(
            ((definition.get("target") or {}).get("scope") == "all_a_shares")
            for _rule_id, _rule, definition, _stock_codes in prepared
        )

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
        snapshot_hit_count = len(self._load_batch_quote_cache(unique_codes, "snapshot_only"))
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

    def _build_rule_run_quote_metadata(
        self,
        stock_codes: List[str],
        data_policy: str,
        *,
        matches: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        if data_policy == "db_only":
            return self._build_intraday_hot_table_run_metadata(stock_codes, matches=matches)
        return self._build_snapshot_run_metadata(stock_codes, matches=matches)

    def _build_intraday_hot_table_run_metadata(
        self,
        stock_codes: List[str],
        *,
        matches: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        unique_codes = list(dict.fromkeys(str(code or "").strip() for code in stock_codes if str(code or "").strip()))
        requested = len(unique_codes)
        summary: Dict[str, Any] = {}
        try:
            market_today = trading_calendar.get_market_now("cn").date()
            summary_getter = getattr(getattr(self.stock_service, "repo", None), "db", None)
            summary_loader = getattr(summary_getter, "get_intraday_minute_snapshot_summary", None)
            summary = summary_loader(trade_date=market_today) if callable(summary_loader) else {}
        except Exception as exc:
            logger.debug("读取实测分钟热表元数据失败: %s", exc)
            summary = {}

        hot_codes = self._normalize_quote_code_set(summary.get("codes") or [])
        requested_codes = self._normalize_quote_code_set(unique_codes)
        hit_count = sum(1 for code in requested_codes if code in hot_codes)
        snapshot_id = summary.get("snapshot_id")
        snapshot_time = summary.get("snapshot_time")
        if matches and not snapshot_id:
            snapshot_id = self._first_snapshot_value(matches, "snapshot_id")
        if matches and not snapshot_time:
            snapshot_time = self._first_snapshot_value(matches, "snapshot_time")
        return {
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time,
            "snapshot_age_seconds": self._calculate_snapshot_age_seconds(snapshot_time),
            "quote_hit_count": hit_count,
            "quote_miss_count": max(0, requested - hit_count),
        }

    @staticmethod
    def _normalize_quote_code_set(stock_codes: List[str]) -> Set[str]:
        try:
            from data_provider.base import normalize_stock_code
        except Exception:
            normalize_stock_code = None

        normalized: Set[str] = set()
        for code in stock_codes or []:
            value = str(code or "").strip().upper()
            if not value:
                continue
            if callable(normalize_stock_code):
                try:
                    value = str(normalize_stock_code(value)).strip().upper()
                except Exception:
                    pass
            normalized.add(value)
        return normalized

    @staticmethod
    def _normalize_scan_stock_code(stock_code: Any) -> str:
        value = str(stock_code or "").strip().upper()
        if not value:
            return ""
        try:
            from data_provider.base import normalize_stock_code

            return str(normalize_stock_code(value)).strip().upper()
        except Exception:
            return value

    @staticmethod
    def _calculate_snapshot_age_seconds(snapshot_time: Any) -> Optional[int]:
        if not snapshot_time:
            return None
        parsed = pd.to_datetime(snapshot_time, errors="coerce")
        if pd.isna(parsed):
            return None
        snapshot_dt = parsed.to_pydatetime() if hasattr(parsed, "to_pydatetime") else parsed
        if snapshot_dt.tzinfo is not None:
            snapshot_dt = snapshot_dt.replace(tzinfo=None)
        return max(0, int((datetime.now() - snapshot_dt).total_seconds()))

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
                data_policy="db_only",
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

    @staticmethod
    def _normalize_live_event_day(value: Any) -> Optional[str]:
        if value in (None, ""):
            return None
        day = str(value).strip()[:10]
        return day if len(day) == 10 and day[4] == "-" and day[7] == "-" else None

    @staticmethod
    def _live_match_daily_key(match: Dict[str, Any], day: Optional[str]) -> Optional[Tuple[str, int, str]]:
        if not day:
            return None
        try:
            rule_id = int(match.get("rule_id") or 0)
        except (TypeError, ValueError):
            rule_id = 0
        stock_code = str(match.get("stock_code") or "").strip().upper()
        if rule_id <= 0 or not stock_code:
            return None
        return (day, rule_id, stock_code)

    @classmethod
    def _filter_compact_live_matches(
        cls,
        matches: List[Dict[str, Any]],
        previous_keys: Set[Tuple[str, int, str]],
    ) -> List[Dict[str, Any]]:
        seen_keys = set(previous_keys)
        filtered_matches: List[Dict[str, Any]] = []

        for match in matches:
            matched_events = match.get("matched_events") or []
            if matched_events:
                kept_events: List[Dict[str, Any]] = []
                kept_dates: List[str] = []
                for event in matched_events:
                    event_record = event if isinstance(event, dict) else {}
                    day = cls._normalize_live_event_day(event_record.get("date"))
                    if not day:
                        event_snapshot = event_record.get("snapshot") or {}
                        if isinstance(event_snapshot, dict):
                            day = cls._normalize_live_event_day(event_snapshot.get("snapshot_time"))
                    key = cls._live_match_daily_key(match, day)
                    if key and key in seen_keys:
                        continue
                    if key:
                        seen_keys.add(key)
                    kept_events.append(event_record)
                    if day and day not in kept_dates:
                        kept_dates.append(day)
                if not kept_events:
                    continue
                filtered = dict(match)
                filtered["matched_events"] = kept_events
                filtered["matched_dates"] = kept_dates
                filtered_matches.append(filtered)
                continue

            matched_dates = match.get("matched_dates") or []
            if matched_dates:
                kept_dates = []
                for matched_date in matched_dates:
                    day = cls._normalize_live_event_day(matched_date)
                    key = cls._live_match_daily_key(match, day)
                    if key and key in seen_keys:
                        continue
                    if key:
                        seen_keys.add(key)
                    kept_dates.append(str(matched_date))
                if not kept_dates:
                    continue
                filtered = dict(match)
                filtered["matched_dates"] = kept_dates
                filtered_matches.append(filtered)
                continue

            filtered_matches.append(dict(match))

        return filtered_matches

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

    def _get_previous_live_match_keys(self, run_id: int) -> Set[Tuple[str, int, str]]:
        getter = getattr(self.repo, "get_previous_live_match_keys", None)
        if not callable(getter):
            return set()
        try:
            previous = getter(run_id)
        except Exception as exc:
            logger.warning("读取今日实测命中去重键失败: run_id=%s, error=%s", run_id, exc)
            return set()
        if not isinstance(previous, dict):
            return set()

        keys: Set[Tuple[str, int, str]] = set()
        for raw_key in previous.get("keys") or []:
            try:
                day, rule_id, stock_code = raw_key
                normalized_day = self._normalize_live_event_day(day)
                normalized_rule_id = int(rule_id)
                normalized_stock_code = str(stock_code or "").strip().upper()
            except (TypeError, ValueError):
                continue
            if normalized_day and normalized_rule_id > 0 and normalized_stock_code:
                keys.add((normalized_day, normalized_rule_id, normalized_stock_code))
        return keys

    def notify_live_matches(
        self,
        run_id: int,
        *,
        execution_time: Optional[str] = None,
        rule_ids: Optional[List[int]] = None,
        rule_names: Optional[List[str]] = None,
        compact: bool = True,
    ) -> Dict[str, Any]:
        """Push live-test rule matches to every configured notification channel."""
        matches = self.repo.list_matches(run_id)
        event_count = self._count_notification_events(matches)
        original_event_count = event_count
        if event_count <= 0:
            return {
                "sent": False,
                "message": "本次实测没有命中结果，未推送通知",
                "match_count": len(matches),
                "event_count": 0,
                "deduplicated": False,
                "compact": compact,
                "original_event_count": original_event_count,
            }

        deduplicated = False
        if compact:
            previous_keys = self._get_previous_live_match_keys(run_id)
            filtered_matches = self._filter_compact_live_matches(matches, previous_keys)
            filtered_event_count = self._count_notification_events(filtered_matches)
            if filtered_event_count <= 0:
                logger.info(
                    "实测精简模式跳过今日重复命中推送: run_id=%s, original_event_count=%s",
                    run_id,
                    original_event_count,
                )
                return {
                    "sent": False,
                    "message": "精简模式：今日同一规则、股票和命中日已推送过，已跳过重复通知",
                    "match_count": 0,
                    "event_count": 0,
                    "deduplicated": True,
                    "compact": True,
                    "original_event_count": original_event_count,
                }
            deduplicated = filtered_event_count < original_event_count
            matches = filtered_matches
            event_count = filtered_event_count

        current_signature = self._build_live_match_signature(matches)
        previous = self._get_previous_live_match_signature(run_id)
        if not compact and current_signature and previous and current_signature == previous.get("signature"):
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
                "compact": compact,
                "original_event_count": original_event_count,
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
                    "deduplicated": deduplicated,
                    "compact": compact,
                    "original_event_count": original_event_count,
                }

            sent = notifier.send(content)
            message = "实测命中通知已发送" if sent else "实测命中通知发送失败"
            if sent and deduplicated:
                skipped_count = original_event_count - event_count
                message = f"{message}，精简模式已过滤 {skipped_count} 条今日重复命中"
            return {
                "sent": bool(sent),
                "message": message,
                "match_count": len(matches),
                "event_count": event_count,
                "deduplicated": deduplicated,
                "compact": compact,
                "original_event_count": original_event_count,
            }
        except Exception as exc:
            logger.error("实测命中通知异常: run_id=%s, error=%s", run_id, exc, exc_info=True)
            return {
                "sent": False,
                "message": f"实测命中通知异常: {type(exc).__name__}",
                "match_count": len(matches),
                "event_count": event_count,
                "deduplicated": deduplicated,
                "compact": compact,
                "original_event_count": original_event_count,
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

    def _build_stock_rule_context(
        self,
        rules: List[tuple[int, Dict[str, Any], Dict[str, Any]]],
        stock_code: str,
        mode: str,
        start_date: Optional[date],
        data_policy: str,
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        lookback_days_by_rule = {
            rule_id: self._resolve_history_fetch_days(definition, rule, start_date)
            for rule_id, rule, definition in rules
        }
        lookback_days = max(lookback_days_by_rule.values()) if lookback_days_by_rule else 120
        history_data_policy = data_policy if data_policy in {"snapshot_only", "cache_only", "db_only"} else "default"
        history = self._get_preloaded_history(scan_cache, stock_code, lookback_days)
        if history is None:
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
            return {
                "history": history,
                "history_rows": history_rows,
                "quote": None,
                "metric_frame": pd.DataFrame(),
                "lookback_days_by_rule": lookback_days_by_rule,
            }

        quote = self._get_stock_quote_for_rule_run(stock_code, mode, data_policy, scan_cache=scan_cache)
        require_chip_metrics = any(
            self._definition_uses_chip_metrics(definition)
            for _rule_id, _rule, definition in rules
        )
        require_earnings_gap_metrics = any(
            self._definition_uses_earnings_gap_metrics(definition)
            for _rule_id, _rule, definition in rules
        )
        indicator_metrics = self._get_indicator_metrics_for_context(
            stock_code,
            history_rows,
            mode,
            data_policy,
            require_chip_metrics=require_chip_metrics,
            scan_cache=scan_cache,
        )
        if mode == "latest" and quote is not None:
            quote = StockService._normalize_quote_payload_units(stock_code, quote) or quote
            history_rows = self._sync_latest_history_rows_with_quote(stock_code, history_rows, quote)
            if require_chip_metrics:
                indicator_metrics = self._build_live_chip_indicator_metrics(
                    stock_code,
                    history_rows,
                    indicator_metrics,
                )
        if require_earnings_gap_metrics:
            self._sync_earnings_gap_metrics_to_scan_cache(
                stock_code,
                history_rows,
                scan_cache=scan_cache,
            )

        return {
            "history": history,
            "history_rows": history_rows,
            "quote": quote,
            "metric_frame": build_metric_frame(history_rows, quote, indicator_metrics),
            "lookback_days_by_rule": lookback_days_by_rule,
        }

    def _build_fast_latest_stock_rule_context(
        self,
        rules: List[tuple[int, Dict[str, Any], Dict[str, Any]]],
        stock_code: str,
        data_policy: str,
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        quote = self._get_stock_quote_for_rule_run(
            stock_code,
            "latest",
            data_policy,
            scan_cache=scan_cache,
        )
        if quote is None:
            raise RuleDataUnavailable("quote_snapshot_miss")
        quote = StockService._normalize_quote_payload_units(stock_code, quote) or quote

        evaluation_date = self._resolve_latest_evaluation_date(stock_code, quote)
        if evaluation_date is None:
            raise RuleDataUnavailable("latest_date_miss")

        previous_close = (
            self._to_optional_float(quote.get("prev_close"))
            or self._to_optional_float(quote.get("pre_close"))
        )
        latest_row = StockService._build_realtime_daily_row(
            quote,
            stock_code,
            evaluation_date,
            previous_close,
        )
        for key in (
            "total_mv",
            "circ_mv",
            "pe_ratio",
            "total_shares",
            "float_shares",
            "limit_up_price",
            "limit_down_price",
            "volume_ratio",
            "amplitude",
            "price_speed",
            "entrust_ratio",
        ):
            if quote.get(key) not in (None, ""):
                latest_row[key] = quote.get(key)

        require_chip_metrics = any(
            self._definition_uses_chip_metrics(definition)
            for _rule_id, _rule, definition in rules
        )
        indicator_metrics = (
            self._build_fast_latest_chip_indicator_metrics(
                stock_code,
                latest_row,
                scan_cache=scan_cache,
            )
            if require_chip_metrics
            else {}
        )
        history = {
            "stock_code": stock_code,
            "stock_name": quote.get("stock_name"),
            "period": "daily",
            "data_source": "fast_latest_scan",
            "data": [latest_row],
        }
        return {
            "history": history,
            "history_rows": [latest_row],
            "quote": quote,
            "metric_frame": build_metric_frame([latest_row], quote, indicator_metrics),
            "lookback_days_by_rule": {
                rule_id: 1
                for rule_id, _rule, _definition in rules
            },
        }

    def _build_fast_latest_chip_indicator_metrics(
        self,
        stock_code: str,
        latest_row: Dict[str, Any],
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        chip_by_code = (scan_cache or {}).get("chip_metrics_by_code") or {}
        cache_key = self._normalize_scan_stock_code(stock_code)
        chip = chip_by_code.get(cache_key) or chip_by_code.get(str(stock_code or "").strip().upper())
        if not isinstance(chip, dict):
            return {}

        latest_date = self._coerce_history_row_date(latest_row.get("date"))
        latest_close = self._to_optional_float(latest_row.get("close"))
        if latest_date is None or latest_close is None or latest_close <= 0:
            return {"chip_distribution": copy.deepcopy(chip)}

        return {
            "chip_distribution": self._reprice_chip_distribution_for_live_row(
                chip,
                latest_date,
                latest_close,
                force=True,
            )
        }

    def _get_stock_quote_for_rule_run(
        self,
        stock_code: str,
        mode: str,
        data_policy: str,
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> Optional[Dict[str, Any]]:
        if mode != "latest":
            return None

        quote = self._get_preloaded_quote(scan_cache, stock_code)
        if data_policy == "db_only":
            if quote is None:
                quote = self._build_intraday_hot_table_quote(stock_code)
            if quote is None:
                raise RuleDataUnavailable("intraday_hot_table_miss")
            return quote
        if quote is None:
            quote = (
                self._get_stock_realtime_quote(stock_code, data_policy="snapshot_only")
                if data_policy == "snapshot_only"
                else self._get_stock_realtime_quote(stock_code)
            )
        if data_policy == "snapshot_only" and quote is None:
            quote = self._build_intraday_hot_table_quote(stock_code)
            if quote is None:
                raise RuleDataUnavailable("quote_snapshot_miss")
        return quote

    @staticmethod
    def _get_preloaded_history(
        scan_cache: Optional[Dict[str, Any]],
        stock_code: str,
        lookback_days: int,
    ) -> Optional[Dict[str, Any]]:
        history_by_code = (scan_cache or {}).get("history_by_code") or {}
        history = history_by_code.get(str(stock_code or "").strip().upper())
        if not history:
            return None
        rows = [dict(row) for row in (history.get("data") or []) if isinstance(row, dict)]
        if not rows:
            return None
        return {
            **history,
            "data": rows[-max(1, int(lookback_days or 1)):],
        }

    @staticmethod
    def _get_preloaded_quote(
        scan_cache: Optional[Dict[str, Any]],
        stock_code: str,
    ) -> Optional[Dict[str, Any]]:
        quote_by_code = (scan_cache or {}).get("quote_by_code") or {}
        cache_key = RuleService._normalize_scan_stock_code(stock_code)
        quote = quote_by_code.get(cache_key) or quote_by_code.get(str(stock_code or "").strip().upper())
        return dict(quote) if isinstance(quote, dict) else None

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
        rule_id = int(rule.get("id") or 0)
        context = self._build_stock_rule_context(
            [(rule_id, rule, definition)],
            stock_code,
            mode,
            start_date,
            data_policy,
        )
        return self._evaluate_stock_from_context(
            rule_id,
            rule,
            definition,
            stock_code,
            mode,
            start_date,
            end_date,
            context,
        )

    def _evaluate_stock_from_context(
        self,
        rule_id: int,
        rule: Dict[str, Any],
        definition: Dict[str, Any],
        stock_code: str,
        mode: str,
        start_date: Optional[date],
        end_date: Optional[date],
        context: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        history = context.get("history") or {}
        quote = context.get("quote")
        metric_frame = context.get("metric_frame")
        if metric_frame.empty:
            return None

        if mode == "latest":
            events = self._evaluate_latest_event(definition, metric_frame)
            if quote is not None:
                self._attach_live_quote_snapshot_metadata(events, quote)
        else:
            lookback_days = (context.get("lookback_days_by_rule") or {}).get(rule_id)
            if lookback_days is None:
                lookback_days = self._resolve_history_fetch_days(definition, rule, start_date)
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

    def _get_earnings_gap_events_for_context(
        self,
        stock_code: str,
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        cache_key = str(stock_code or "").strip().upper()
        events_cache = (scan_cache or {}).get("earnings_events_by_code")
        if isinstance(events_cache, dict) and cache_key in events_cache:
            cached = events_cache.get(cache_key)
            return [dict(item) for item in cached] if isinstance(cached, list) else []

        events: List[Dict[str, Any]] = []
        try:
            from data_provider.fundamental_adapter import AkshareFundamentalAdapter

            payload = AkshareFundamentalAdapter().get_deducted_profit_growth_events(stock_code)
            raw_events = payload.get("events") if isinstance(payload, dict) else []
            events = [dict(item) for item in raw_events or [] if isinstance(item, dict)]
        except Exception as exc:
            logger.debug("规则财务事件读取失败 %s: %s", stock_code, exc)

        if isinstance(events_cache, dict):
            events_cache[cache_key] = [dict(item) for item in events]
        return events

    def _enrich_history_rows_with_earnings_gap_metrics(
        self,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        events = self._get_earnings_gap_events_for_context(stock_code, scan_cache=scan_cache)
        enriched_rows = self._apply_earnings_gap_events_to_history(history_rows, events)
        self._sync_earnings_gap_metrics_to_scan_cache(stock_code, enriched_rows, scan_cache=scan_cache)
        self._persist_earnings_gap_metrics(stock_code, enriched_rows)
        return enriched_rows

    @classmethod
    def _extract_earnings_gap_metrics_by_date(
        cls,
        history_rows: List[Dict[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        metrics_by_date: Dict[str, Dict[str, Any]] = {}
        for row in history_rows or []:
            if not isinstance(row, dict):
                continue
            row_date = cls._coerce_history_date(row.get("date"))
            if row_date is None:
                continue
            metrics = {
                column: row.get(column)
                for column in EARNINGS_GAP_DAILY_METRIC_COLUMNS
                if column in row and row.get(column) is not None
            }
            if metrics:
                metrics_by_date[row_date.isoformat()] = metrics
        return metrics_by_date

    @classmethod
    def _sync_earnings_gap_metrics_to_scan_cache(
        cls,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        *,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not isinstance(scan_cache, dict):
            return
        cache_key = str(stock_code or "").strip().upper()
        metrics_by_date = cls._extract_earnings_gap_metrics_by_date(history_rows)
        metrics_cache = scan_cache.get("earnings_gap_metrics_by_code")
        if isinstance(metrics_cache, dict):
            metrics_cache[cache_key] = {date_key: dict(metrics) for date_key, metrics in metrics_by_date.items()}
        if not metrics_by_date:
            return

        history_by_code = scan_cache.get("history_by_code")
        if not isinstance(history_by_code, dict):
            return
        history_payload = history_by_code.get(cache_key)
        if not isinstance(history_payload, dict):
            return
        cached_rows = history_payload.get("data")
        if not isinstance(cached_rows, list):
            return
        for cached_row in cached_rows:
            if not isinstance(cached_row, dict):
                continue
            row_date = cls._coerce_history_date(cached_row.get("date"))
            if row_date is None:
                continue
            metrics = metrics_by_date.get(row_date.isoformat())
            if metrics:
                cached_row.update(metrics)

    def _persist_earnings_gap_metrics(self, stock_code: str, history_rows: List[Dict[str, Any]]) -> None:
        if not history_rows:
            return
        db = getattr(getattr(self.stock_service, "repo", None), "db", None)
        persist = getattr(db, "update_stock_daily_earnings_gap_metrics", None)
        if not callable(persist):
            return
        try:
            persist(stock_code, history_rows)
        except Exception as exc:
            logger.debug("规则财务事件指标落库失败 %s: %s", stock_code, exc)

    @classmethod
    def _apply_earnings_gap_events_to_history(
        cls,
        history_rows: List[Dict[str, Any]],
        events: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        if not history_rows or not events:
            return [dict(row) for row in history_rows]

        dated_rows: List[Tuple[int, date, Dict[str, Any]]] = []
        for index, row in enumerate(history_rows):
            if not isinstance(row, dict):
                continue
            row_date = cls._coerce_history_date(row.get("date"))
            if row_date is None:
                continue
            dated_rows.append((index, row_date, dict(row)))
        if not dated_rows:
            return [dict(row) for row in history_rows]

        dated_rows.sort(key=lambda item: (item[1], item[0]))
        for event in events:
            announcement_date = cls._coerce_history_date(event.get("announcement_date"))
            if announcement_date is None:
                continue
            target_position = next(
                (
                    position
                    for position, (_original_index, row_date, _row) in enumerate(dated_rows)
                    if row_date > announcement_date
                ),
                None,
            )
            if target_position is None:
                continue

            _original_index, _row_date, target_row = dated_rows[target_position]
            previous_row = dated_rows[target_position - 1][2] if target_position > 0 else {}
            previous_close = cls._to_optional_float(target_row.get("prev_close"))
            if previous_close is None:
                previous_close = cls._to_optional_float(previous_row.get("close"))
            open_price = cls._to_optional_float(target_row.get("open"))
            low_price = cls._to_optional_float(target_row.get("low"))
            current_volume = cls._to_optional_float(target_row.get("volume"))
            previous_volumes = [
                cls._to_optional_float(row.get("volume"))
                for _idx, _date, row in dated_rows[max(0, target_position - 5):target_position]
            ]
            previous_volumes = [value for value in previous_volumes if value is not None]

            gap_pct = (
                (open_price - previous_close) / previous_close * 100
                if open_price is not None and previous_close is not None and previous_close > 0
                else None
            )
            volume_ratio = (
                current_volume / (sum(previous_volumes) / len(previous_volumes))
                if current_volume is not None and len(previous_volumes) == 5 and sum(previous_volumes) > 0
                else None
            )
            gap_unfilled = (
                1.0
                if low_price is not None and previous_close is not None and low_price > previous_close
                else 0.0
                if low_price is not None and previous_close is not None
                else None
            )
            yoy_pct = cls._to_optional_float(event.get("deducted_net_profit_yoy_pct"))
            qoq_pct = cls._to_optional_float(event.get("deducted_net_profit_qoq_pct"))
            signal = (
                1.0
                if yoy_pct is not None
                and yoy_pct >= 100
                and qoq_pct is not None
                and qoq_pct >= 50
                and gap_pct is not None
                and gap_pct >= 3
                and volume_ratio is not None
                and volume_ratio >= 1.5
                and gap_unfilled == 1.0
                else 0.0
            )

            target_row["deducted_net_profit_yoy_pct"] = yoy_pct
            target_row["deducted_net_profit_qoq_pct"] = qoq_pct
            target_row["announcement_next_day_gap_pct"] = gap_pct
            target_row["announcement_next_day_volume_ratio"] = volume_ratio
            target_row["announcement_next_day_gap_unfilled"] = gap_unfilled
            target_row["net_profit_gap_signal"] = signal

        enriched_by_original_index = {original_index: row for original_index, _date, row in dated_rows}
        return [
            enriched_by_original_index.get(index, dict(row))
            for index, row in enumerate(history_rows)
            if isinstance(row, dict)
        ]

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
        *,
        require_chip_metrics: bool = True,
    ) -> Dict[str, Any]:
        if not require_chip_metrics:
            return {}
        if data_policy in {"snapshot_only", "cache_only", "db_only"}:
            local_metrics = self._build_history_chip_metrics(stock_code, history_rows)
            if local_metrics.get("chip_distribution"):
                return local_metrics
            return self._get_stock_indicator_metrics(stock_code, data_policy=data_policy)
        if mode == "history":
            local_metrics = self._build_history_chip_metrics(stock_code, history_rows)
            if local_metrics.get("chip_distribution"):
                return local_metrics
        return self._get_stock_indicator_metrics(stock_code, data_policy=data_policy)

    def _get_stock_indicator_metrics(self, stock_code: str, *, data_policy: str) -> Dict[str, Any]:
        getter = getattr(self.stock_service, "get_indicator_metrics", None)
        if not callable(getter):
            return {}
        try:
            return getter(stock_code, data_policy=data_policy) or {}
        except TypeError as exc:
            if "data_policy" not in str(exc):
                raise
            return getter(stock_code) or {}

    def _get_indicator_metrics_for_context(
        self,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        mode: str,
        data_policy: str,
        *,
        require_chip_metrics: bool,
        scan_cache: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not require_chip_metrics:
            return {}
        cache_key = str(stock_code or "").strip().upper()
        indicator_cache = (scan_cache or {}).get("indicator_metrics_by_code")
        if isinstance(indicator_cache, dict) and cache_key in indicator_cache:
            cached = indicator_cache.get(cache_key)
            return dict(cached) if isinstance(cached, dict) else {}
        metrics = self._get_indicator_metrics(
            stock_code,
            history_rows,
            mode,
            data_policy,
            require_chip_metrics=True,
        )
        if isinstance(indicator_cache, dict):
            indicator_cache[cache_key] = dict(metrics or {})
        return metrics

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

    def _build_live_chip_indicator_metrics(
        self,
        stock_code: str,
        history_rows: List[Dict[str, Any]],
        indicator_metrics: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not history_rows or not indicator_metrics:
            return indicator_metrics

        latest_row = history_rows[-1]
        latest_date = self._coerce_history_row_date(latest_row.get("date"))
        latest_close = self._to_optional_float(latest_row.get("close"))
        if latest_date is None or latest_close is None or latest_close <= 0:
            return indicator_metrics

        live_metrics = copy.deepcopy(indicator_metrics)
        live_history_metrics = self._build_history_chip_metrics(stock_code, history_rows)
        live_chip = live_history_metrics.get("chip_distribution")
        if isinstance(live_chip, dict):
            live_chip_date = self._coerce_history_row_date(live_chip.get("date"))
            if live_chip_date == latest_date:
                live_metrics["chip_distribution"] = live_chip

        for chip_key in LIVE_REPRICED_CHIP_KEYS:
            chip = live_metrics.get(chip_key)
            if isinstance(chip, dict):
                live_metrics[chip_key] = self._reprice_chip_distribution_for_live_row(
                    chip,
                    latest_date,
                    latest_close,
                )
        return live_metrics

    @classmethod
    def _reprice_chip_distribution_for_live_row(
        cls,
        chip: Dict[str, Any],
        latest_date: date,
        latest_close: float,
        *,
        force: bool = False,
    ) -> Dict[str, Any]:
        repriced = copy.deepcopy(chip)
        chip_date = cls._coerce_history_row_date(repriced.get("date"))
        if chip_date == latest_date and not force:
            return repriced

        profit_ratio = cls._estimate_profit_ratio_from_distribution(
            repriced.get("distribution"),
            latest_close,
        )
        repriced["date"] = latest_date.isoformat()
        if profit_ratio is not None:
            repriced["profit_ratio"] = profit_ratio

        source = str(repriced.get("source") or "").strip()
        if source and "live_repriced" not in source:
            repriced["source"] = f"{source}:live_repriced"
        elif not source:
            repriced["source"] = "live_repriced"
        return repriced

    @classmethod
    def _estimate_profit_ratio_from_distribution(
        cls,
        distribution: Any,
        current_price: float,
    ) -> Optional[float]:
        if not isinstance(distribution, list):
            return None

        total_percent = 0.0
        profit_percent = 0.0
        for point in distribution:
            if not isinstance(point, dict):
                continue
            price = cls._to_optional_float(point.get("price"))
            percent = cls._to_optional_float(point.get("percent", point.get("ratio")))
            if price is None or percent is None or percent <= 0:
                continue
            total_percent += percent
            if price <= current_price:
                profit_percent += percent

        if total_percent <= 0:
            return None
        return max(0.0, min(1.0, profit_percent / total_percent))

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
        elif scope == "all_a_shares":
            try:
                from src.data.stock_index_loader import get_all_a_share_stock_codes

                codes = get_all_a_share_stock_codes()
            except Exception as exc:
                logger.warning("读取 A 股股票索引失败，无法解析所有 A 股范围: %s", exc)
                codes = []
        else:
            codes = []
        normalized = self._normalize_codes(codes)
        if scope == "all_a_shares" and not normalized:
            raise RuleValidationError("A 股股票索引为空，无法运行所有 A 股范围")
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
