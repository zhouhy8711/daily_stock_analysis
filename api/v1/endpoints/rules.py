# -*- coding: utf-8 -*-
"""Stock rule API endpoints."""

import hashlib
import json
import logging
import threading
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status

from api.deps import get_tenant_context, get_tenant_service
from api.v1.schemas.common import ErrorResponse, SuccessResponse
from api.v1.schemas.rules import (
    RuleBatchRunRequest,
    RuleCreateRequest,
    RuleItem,
    RuleListResponse,
    RuleMetricRegistryResponse,
    RuleRunHistoryResponse,
    RuleRunHistoryItem,
    RuleRunMatchListResponse,
    RuleRunNotifyRequest,
    RuleRunNotifyResponse,
    RuleRunRequest,
    RuleRunResponse,
    RuleUpdateRequest,
)
from src.repositories.rule_repo import encode_rule_batch_metadata
from src.services.rule_service import RuleService, RuleValidationError
from src.services.tenant_service import (
    TenantContext,
    TenantInactiveError,
    TenantNotFoundError,
    TenantService,
    TenantValidationError,
)

router = APIRouter()
logger = logging.getLogger(__name__)
_SHARED_RULE_EXECUTION_LOCKS_GUARD = threading.RLock()
_SHARED_RULE_EXECUTION_LOCKS: Dict[str, threading.RLock] = {}
SHARED_RULE_RUNNING_WAIT_TIMEOUT_SECONDS = 30 * 60
SHARED_RULE_RUNNING_WAIT_POLL_SECONDS = 1.0


def _get_shared_execution_lock(execution_key: str) -> threading.RLock:
    with _SHARED_RULE_EXECUTION_LOCKS_GUARD:
        lock = _SHARED_RULE_EXECUTION_LOCKS.get(execution_key)
        if lock is None:
            lock = threading.RLock()
            _SHARED_RULE_EXECUTION_LOCKS[execution_key] = lock
        return lock


def _complete_async_rule_batch(context: dict) -> None:
    tenant_context = None
    if context.get("tenant_id") is not None and context.get("tenant_key"):
        tenant_context = TenantContext(
            id=int(context["tenant_id"]),
            key=str(context["tenant_key"]),
            name=str(context.get("tenant_name") or context["tenant_key"]),
            is_default=bool(context.get("tenant_is_default")),
        )
    service = RuleService(tenant_context=tenant_context)
    context = {
        key: value
        for key, value in context.items()
        if key not in {"tenant_id", "tenant_key", "tenant_name", "tenant_is_default"}
    }
    service.complete_started_run_rules(**context)


def _tenant_context_from_payload(payload: Dict[str, Any]) -> TenantContext:
    return TenantContext(
        id=int(payload["tenant_id"]),
        key=str(payload["tenant_key"]),
        name=str(payload.get("tenant_name") or payload["tenant_key"]),
        is_default=bool(payload.get("tenant_is_default")),
    )


def _hash_payload(payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_stock_codes(stock_codes: List[str]) -> List[str]:
    return list(dict.fromkeys(
        str(code or "").strip().upper()
        for code in stock_codes
        if str(code or "").strip()
    ))


def _build_shared_execution_key(
    *,
    logic_fingerprint: str,
    run_mode: str,
    data_policy: str,
    date_from: Any,
    date_to: Any,
    snapshot_id: Optional[str],
    stock_codes: List[str],
) -> str:
    return _hash_payload({
        "version": 1,
        "logic_fingerprint": logic_fingerprint,
        "mode": run_mode,
        "data_policy": data_policy,
        "date_from": date_from.isoformat() if hasattr(date_from, "isoformat") and date_from else None,
        "date_to": date_to.isoformat() if hasattr(date_to, "isoformat") and date_to else None,
        "snapshot_id": snapshot_id,
        "stock_codes": _normalize_stock_codes(stock_codes),
    })


def _sanitize_shared_cache_key_part(value: str) -> str:
    return "".join(
        char
        for char in str(value or "").strip()[:128]
        if char.isalnum() or char in {"-", "_", ".", ":"}
    )


def _build_shared_live_cache_key(contexts: List[Dict[str, Any]]) -> Optional[str]:
    session_keys = [
        _sanitize_shared_cache_key_part(str((context.get("batch_metadata") or {}).get("raw_live_cache_key") or ""))
        for context in contexts
        if str((context.get("batch_metadata") or {}).get("raw_live_cache_key") or "").strip()
    ]
    fallback_keys = [
        str(context.get("live_cache_key") or "").strip()
        for context in contexts
        if str(context.get("live_cache_key") or "").strip()
    ]
    if not session_keys and not fallback_keys:
        return None
    tenant_keys = sorted({
        str(context.get("tenant_key") or "").strip()
        for context in contexts
        if str(context.get("tenant_key") or "").strip()
    })
    if session_keys:
        session_part = (
            session_keys[0]
            if len(set(session_keys)) == 1
            else _hash_payload({"session_keys": sorted(set(session_keys))})[:32]
        )
        tenant_part = _hash_payload({"tenant_keys": tenant_keys})[:16]
        return f"shared:{session_part}:{tenant_part}"
    return f"shared:{_hash_payload({'keys': sorted(set(fallback_keys))})[:32]}"


def _fanout_shared_links(
    shared_matches: List[Dict[str, Any]],
    consumers: List[Dict[str, Any]],
) -> Dict[int, List[Dict[str, Any]]]:
    links_by_run_id: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for match in shared_matches:
        stock_code = str(match.get("stock_code") or "").strip().upper()
        if not stock_code:
            continue
        for consumer in consumers:
            if stock_code not in consumer["stock_set"]:
                continue
            links_by_run_id[int(consumer["run_id"])].append({
                "rule_id": int(consumer["rule_id"]),
                "shared_run_id": int(match["shared_run_id"]),
                "shared_match_id": int(match["id"]),
                "stock_code": stock_code,
            })
    return links_by_run_id


def _complete_async_multi_tenant_rule_batch(orchestration: Dict[str, Any]) -> None:
    contexts = list(orchestration.get("contexts") or [])
    shared_groups = list(orchestration.get("shared_groups") or [])
    private_groups = list(orchestration.get("private_groups") or [])
    contexts_by_run_id = {int(context["run_id"]): context for context in contexts}
    tenant_payloads = {
        int(context["run_id"]): {
            "tenant_id": context.get("tenant_id"),
            "tenant_key": context.get("tenant_key"),
            "tenant_name": context.get("tenant_name"),
            "tenant_is_default": context.get("tenant_is_default"),
        }
        for context in contexts
    }
    services_by_run_id = {
        run_id: RuleService(tenant_context=_tenant_context_from_payload(tenant_payload))
        for run_id, tenant_payload in tenant_payloads.items()
    }
    private_matches_by_run: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    shared_links_by_run: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    errors_by_run: Dict[int, List[str]] = defaultdict(list)
    metadata_by_run: Dict[int, Dict[str, Any]] = {
        run_id: dict(context.get("batch_metadata") or {})
        for run_id, context in contexts_by_run_id.items()
    }

    try:
        for group in shared_groups:
            consumers = list(group.get("consumers") or [])
            if not consumers:
                continue
            representative = consumers[0]
            representative_context = contexts_by_run_id[int(representative["run_id"])]
            representative_tenant = _tenant_context_from_payload(representative_context)
            shared_service = RuleService(tenant_context=representative_tenant)
            shared_repo = shared_service.repo
            shared_run_id = int(group["shared_run_id"])
            shared_started_at = group.get("started_at") or datetime.now()
            prepared = [(
                int(group["representative_rule_id"]),
                dict(group["representative_rule"]),
                dict(group["representative_definition"]),
                list(group["stock_codes"]),
            )]
            metadata = dict(group.get("metadata") or {})
            metadata["completed_count"] = len(group.get("stock_codes") or [])
            cached_shared_run = group.get("reused_shared_run")
            execution_key = str(group.get("execution_key") or "")
            owns_shared_run = bool(group.get("owns_shared_run", True))
            if not cached_shared_run and execution_key and not owns_shared_run:
                wait_for_completed = getattr(shared_repo, "wait_for_completed_shared_run_by_key", None)
                if callable(wait_for_completed):
                    cached_shared_run = wait_for_completed(
                        execution_key,
                        timeout_seconds=SHARED_RULE_RUNNING_WAIT_TIMEOUT_SECONDS,
                        poll_interval_seconds=SHARED_RULE_RUNNING_WAIT_POLL_SECONDS,
                    )
                    if cached_shared_run:
                        shared_run_id = int(cached_shared_run["id"])
            with _get_shared_execution_lock(execution_key or str(shared_run_id)):
                if not cached_shared_run and execution_key:
                    cached_shared_run = shared_repo.find_completed_shared_run_by_key(execution_key)
                    if cached_shared_run:
                        shared_run_id = int(cached_shared_run["id"])
                if not cached_shared_run and execution_key and not owns_shared_run:
                    logger.warning(
                        "共享规则执行等待超时，接管执行: shared_run_id=%s execution_key=%s",
                        shared_run_id,
                        execution_key,
                    )
                if cached_shared_run:
                    shared_matches = shared_repo.list_shared_matches(shared_run_id)
                    shared_error = str(cached_shared_run.get("error") or "").strip()
                    shared_errors = [shared_error] if shared_error else []
                else:
                    stock_codes = list(group.get("stock_codes") or [])

                    def progress_callback(count: int, runtime_metadata: Dict[str, Any], current_errors: List[str]) -> None:
                        scanned_codes = set(stock_codes[: max(0, int(count or 0))])
                        shared_repo.update_shared_run_progress(
                            shared_run_id=shared_run_id,
                            completed_count=len(scanned_codes),
                            metadata={**metadata, **runtime_metadata, "completed_count": len(scanned_codes)},
                        )
                        for consumer in consumers:
                            run_id = int(consumer["run_id"])
                            service = services_by_run_id[run_id]
                            consumer_context = contexts_by_run_id[run_id]
                            consumer_completed = len(scanned_codes & consumer["stock_set"])
                            service._update_run_progress_best_effort(
                                run_id=run_id,
                                rule_ids=consumer_context["rule_ids"],
                                rule_names=consumer_context["rule_names"],
                                completed_count=min(consumer_completed, int(consumer_context.get("target_count") or 0)),
                                target_count=int(consumer_context.get("target_count") or 0),
                                errors=current_errors,
                                metadata={**dict(consumer_context.get("batch_metadata") or {}), **runtime_metadata},
                            )

                    with shared_service._maybe_pause_realtime_quote_archive(
                        str(group["run_mode"]),
                        str(group["data_policy"]),
                        stock_codes,
                    ):
                        shared_matches, shared_errors = shared_service._execute_batch_scan_by_stock(
                            0,
                            prepared,
                            stock_codes,
                            str(group["run_mode"]),
                            group.get("date_from"),
                            group.get("date_to"),
                            str(group["data_policy"]),
                            [int(group["representative_rule_id"])],
                            [str(group.get("representative_rule_name") or f"规则 {group['representative_rule_id']}")],
                            metadata,
                            live_cache_key=group.get("live_cache_key"),
                            progress_callback=progress_callback,
                        )
                    for match in shared_matches:
                        match["rule_fingerprint"] = group["logic_fingerprint"]
                    status = "completed" if not shared_errors else "partial"
                    shared_matches, _duration_ms = shared_repo.finish_shared_run(
                        shared_run_id=shared_run_id,
                        rule_id=int(group["representative_rule_id"]),
                        status=status,
                        started_at=shared_started_at,
                        matches=shared_matches,
                        metadata={**metadata, "completed_count": len(stock_codes)},
                        error="；".join(shared_errors) if shared_errors else None,
                    )
            fanout = _fanout_shared_links(shared_matches, consumers)
            for run_id, links in fanout.items():
                shared_links_by_run[run_id].extend(links)
            if shared_errors:
                for consumer in consumers:
                    errors_by_run[int(consumer["run_id"])].extend(shared_errors)

        for group in private_groups:
            run_id = int(group["run_id"])
            context = contexts_by_run_id[run_id]
            service = services_by_run_id[run_id]
            prepared = list(group.get("prepared") or [])
            stock_codes = list(group.get("stock_codes") or [])
            rule_ids = [int(rule_id) for rule_id, _rule, _definition, _codes in prepared]
            rule_names = [str(rule.get("name") or f"规则 {rule_id}") for rule_id, rule, _definition, _codes in prepared]
            if not prepared:
                continue
            if context.get("prewarm_only"):
                try:
                    prewarm_metadata = service._prewarm_rule_scan_cache(
                        prepared,
                        stock_codes,
                        context.get("date_from"),
                        str(context["data_policy"]),
                        live_cache_key=context.get("live_cache_key"),
                    )
                    metadata_by_run[run_id] = {
                        **metadata_by_run.get(run_id, {}),
                        **prewarm_metadata,
                    }
                except Exception as exc:
                    logger.error("多租户规则实测预热失败: run_id=%s, error=%s", run_id, exc, exc_info=True)
                    metadata_by_run[run_id] = {
                        **metadata_by_run.get(run_id, {}),
                        "prewarm_only": True,
                    }
                    errors_by_run[run_id].append(type(exc).__name__)
                continue
            with service._maybe_pause_realtime_quote_archive(
                str(context["run_mode"]),
                str(context["data_policy"]),
                stock_codes,
            ):
                matches, errors = service._execute_batch_scan_by_stock(
                    run_id,
                    prepared,
                    stock_codes,
                    str(context["run_mode"]),
                    context.get("date_from"),
                    context.get("date_to"),
                    str(context["data_policy"]),
                    rule_ids,
                    rule_names,
                    dict(context.get("batch_metadata") or {}),
                    live_cache_key=context.get("live_cache_key"),
                )
            private_matches_by_run[run_id].extend(matches)
            errors_by_run[run_id].extend(errors)
    except Exception as exc:
        logger.error("多租户规则共享执行失败: %s", exc, exc_info=True)
        for run_id, context in contexts_by_run_id.items():
            service = services_by_run_id[run_id]
            service.repo.finish_projected_run(
                run_id=run_id,
                rule_id=int(context["primary_rule_id"]),
                status="failed",
                started_at=context["started_at"],
                matches=[],
                shared_links=[],
                error=encode_rule_batch_metadata(
                    context["rule_ids"],
                    context["rule_names"],
                    [type(exc).__name__],
                    completed_count=0,
                    **dict(context.get("batch_metadata") or {}),
                ),
            )
        return

    for run_id, context in contexts_by_run_id.items():
        service = services_by_run_id[run_id]
        run_errors = errors_by_run.get(run_id) or []
        status = "completed" if not run_errors else "partial"
        service.repo.finish_projected_run(
            run_id=run_id,
            rule_id=int(context["primary_rule_id"]),
            status=status,
            started_at=context["started_at"],
            matches=private_matches_by_run.get(run_id) or [],
            shared_links=shared_links_by_run.get(run_id) or [],
            error=encode_rule_batch_metadata(
                context["rule_ids"],
                context["rule_names"],
                run_errors,
                completed_count=len(context.get("stock_codes") or []),
                **metadata_by_run.get(run_id, {}),
            ),
        )


def _service_for_tenant(tenant: TenantContext) -> RuleService:
    return RuleService(tenant_context=tenant)


def _tenant_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantValidationError):
        return HTTPException(status_code=400, detail={"error": "invalid_tenant", "message": str(exc)})
    if isinstance(exc, TenantNotFoundError):
        return HTTPException(status_code=404, detail={"error": "tenant_not_found", "message": str(exc)})
    if isinstance(exc, TenantInactiveError):
        return HTTPException(status_code=403, detail={"error": "tenant_inactive", "message": str(exc)})
    return HTTPException(status_code=500, detail={"error": "internal_error", "message": "Tenant operation failed"})


def _resolve_run_tenants(
    tenant_keys: List[str],
    current_tenant: TenantContext,
    tenant_service: TenantService,
) -> List[TenantContext]:
    raw_keys = [str(key or "").strip() for key in tenant_keys if str(key or "").strip()]
    if not raw_keys:
        return [current_tenant]

    if any(key.lower() in {"*", "all"} for key in raw_keys):
        raw_keys = [
            str(item["key"])
            for item in tenant_service.list_tenants(include_inactive=False)
            if item.get("is_active")
        ]

    contexts: List[TenantContext] = []
    seen = set()
    try:
        for raw_key in raw_keys:
            context = tenant_service.resolve_context(raw_key)
            if context.key in seen:
                continue
            seen.add(context.key)
            contexts.append(context)
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc

    return contexts or [current_tenant]


def _resolve_rule_run_tenants(
    payload: RuleBatchRunRequest,
    current_tenant: TenantContext,
    tenant_service: TenantService,
) -> List[TenantContext]:
    tenant_keys = list(payload.tenant_keys or [])
    if str(payload.mode or "").lower() == "latest" and not tenant_keys:
        tenant_keys = ["all"]
    return _resolve_run_tenants(tenant_keys, current_tenant, tenant_service)


def _visible_rule_ids_for_tenant(service: RuleService, requested_rule_ids: List[int]) -> List[int]:
    visible_ids = {int(rule["id"]) for rule in service.list_rules()}
    selected: List[int] = []
    seen = set()
    for raw_rule_id in requested_rule_ids:
        rule_id = int(raw_rule_id)
        if rule_id in seen or rule_id not in visible_ids:
            continue
        seen.add(rule_id)
        selected.append(rule_id)
    return selected


def _tenant_run_item(
    tenant: TenantContext,
    response: Dict[str, Any],
    *,
    error: str | None = None,
) -> Dict[str, Any]:
    return {
        "tenant_id": tenant.id,
        "tenant_key": tenant.key,
        "tenant_name": tenant.name,
        "run_id": int(response.get("run_id") or 0),
        "rule_id": int(response.get("rule_id") or 0),
        "rule_ids": list(response.get("rule_ids") or []),
        "rule_names": list(response.get("rule_names") or []),
        "status": str(response.get("status") or "failed"),
        "target_count": int(response.get("target_count") or 0),
        "completed_count": int(response.get("completed_count") or 0),
        "match_count": int(response.get("match_count") or 0),
        "event_count": int(response.get("event_count") or 0),
        "reused_run": bool(response.get("reused_run")),
        "prewarm_only": bool(response.get("prewarm_only")),
        "error": error,
    }


def _aggregate_tenant_run_response(
    tenant_runs: List[Dict[str, Any]],
    *,
    mode: str,
    errors: List[str] | None = None,
) -> Dict[str, Any]:
    if not tenant_runs:
        raise RuleValidationError("所选租户没有可运行规则")

    first = tenant_runs[0]
    statuses = [str(item.get("status") or "") for item in tenant_runs]
    if any(status == "running" for status in statuses):
        status_text = "running"
    elif any(status in {"failed", "partial"} for status in statuses):
        status_text = "partial"
    else:
        status_text = "completed"

    rule_ids: List[int] = []
    rule_names: List[str] = []
    for item in tenant_runs:
        for rule_id in item.get("rule_ids") or []:
            if rule_id not in rule_ids:
                rule_ids.append(int(rule_id))
        for rule_name in item.get("rule_names") or []:
            if rule_name not in rule_names:
                rule_names.append(str(rule_name))

    return {
        "run_id": int(first["run_id"]),
        "run_ids": [int(item["run_id"]) for item in tenant_runs],
        "tenant_id": first["tenant_id"],
        "tenant_key": first["tenant_key"],
        "rule_id": int(first["rule_id"]),
        "rule_ids": rule_ids,
        "rule_names": rule_names,
        "status": status_text,
        "target_count": sum(int(item.get("target_count") or 0) for item in tenant_runs),
        "completed_count": sum(int(item.get("completed_count") or 0) for item in tenant_runs),
        "match_count": sum(int(item.get("match_count") or 0) for item in tenant_runs),
        "event_count": sum(int(item.get("event_count") or 0) for item in tenant_runs),
        "mode": mode,
        "duration_ms": 0,
        "matches": [],
        "errors": list(errors or []),
        "reused_run": all(bool(item.get("reused_run")) for item in tenant_runs),
        "prewarm_only": bool(tenant_runs) and all(bool(item.get("prewarm_only")) for item in tenant_runs),
        "tenant_runs": tenant_runs,
    }


def _build_multi_tenant_orchestration(planned_runs: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped_entries: Dict[str, Dict[str, Any]] = {}
    private_prepared_by_run: Dict[int, List[Any]] = defaultdict(list)
    services_by_run_id = {int(item["context"]["run_id"]): item["service"] for item in planned_runs}

    for item in planned_runs:
        context = item["context"]
        if context.get("prewarm_only"):
            private_prepared_by_run[int(context["run_id"])].extend(context.get("prepared") or [])
            continue

        prepared = list(context.get("prepared") or [])
        logic_fingerprints = list(item["plan"].get("logic_fingerprints") or [])
        for index, prepared_entry in enumerate(prepared):
            rule_id, rule, definition, stock_codes = prepared_entry
            logic_fingerprint = (
                str(logic_fingerprints[index])
                if index < len(logic_fingerprints)
                else RuleService._build_rule_logic_fingerprint(definition)
            )
            group_key = _hash_payload({
                "version": 1,
                "logic_fingerprint": logic_fingerprint,
                "mode": context.get("run_mode"),
                "data_policy": context.get("data_policy"),
                "date_from": context.get("date_from").isoformat() if context.get("date_from") else None,
                "date_to": context.get("date_to").isoformat() if context.get("date_to") else None,
                "snapshot_id": (context.get("batch_metadata") or {}).get("snapshot_id"),
            })
            grouped = grouped_entries.setdefault(group_key, {
                "logic_fingerprint": logic_fingerprint,
                "entries": [],
            })
            grouped["entries"].append({
                "run_id": int(context["run_id"]),
                "tenant_key": context.get("tenant_key"),
                "rule_id": int(rule_id),
                "rule": rule,
                "definition": definition,
                "stock_codes": _normalize_stock_codes(stock_codes),
                "context": context,
                "service": item["service"],
            })

    shared_groups: List[Dict[str, Any]] = []
    for grouped in grouped_entries.values():
        entries = list(grouped["entries"])
        if len(entries) <= 1:
            entry = entries[0]
            private_prepared_by_run[int(entry["run_id"])].append((
                int(entry["rule_id"]),
                entry["rule"],
                entry["definition"],
                list(entry["stock_codes"]),
            ))
            continue

        representative = entries[0]
        union_stock_codes = _normalize_stock_codes([
            code
            for entry in entries
            for code in entry["stock_codes"]
        ])
        context = representative["context"]
        execution_key = _build_shared_execution_key(
            logic_fingerprint=str(grouped["logic_fingerprint"]),
            run_mode=str(context["run_mode"]),
            data_policy=str(context["data_policy"]),
            date_from=context.get("date_from"),
            date_to=context.get("date_to"),
            snapshot_id=(context.get("batch_metadata") or {}).get("snapshot_id"),
            stock_codes=union_stock_codes,
        )
        service = representative["service"]
        shared_run = service.repo.find_completed_shared_run_by_key(execution_key)
        owns_shared_run = False
        if shared_run:
            shared_run_id = int(shared_run["id"])
        else:
            shared_metadata = {
                "execution_key": execution_key,
                "logic_fingerprint": grouped["logic_fingerprint"],
                "run_mode": context["run_mode"],
                "data_policy": context["data_policy"],
                "snapshot_id": (context.get("batch_metadata") or {}).get("snapshot_id"),
                "tenant_run_ids": [int(entry["run_id"]) for entry in entries],
            }
            creator = getattr(service.repo, "create_shared_run_with_state", None)
            if callable(creator):
                shared_run_id, owns_shared_run = creator(
                    execution_key=execution_key,
                    mode=str(context["run_mode"]),
                    data_policy=str(context["data_policy"]),
                    target_count=len(union_stock_codes),
                    metadata=shared_metadata,
                )
            else:
                shared_run_id = service.repo.create_shared_run(
                    execution_key=execution_key,
                    mode=str(context["run_mode"]),
                    data_policy=str(context["data_policy"]),
                    target_count=len(union_stock_codes),
                    metadata=shared_metadata,
                )
                owns_shared_run = True
        consumers: List[Dict[str, Any]] = []
        for entry in entries:
            consumer = {
                "run_id": int(entry["run_id"]),
                "rule_id": int(entry["rule_id"]),
                "stock_codes": list(entry["stock_codes"]),
                "stock_set": set(entry["stock_codes"]),
            }
            consumers.append(consumer)
            entry["service"].repo.create_run_segment(
                run_id=int(entry["run_id"]),
                shared_run_id=shared_run_id,
                segment_key=execution_key,
                segment_type="shared",
                rule_ids=[int(entry["rule_id"])],
                rule_fingerprints=[str(grouped["logic_fingerprint"])],
                stock_codes=list(entry["stock_codes"]),
            )

        shared_groups.append({
            "shared_run_id": shared_run_id,
            "execution_key": execution_key,
            "logic_fingerprint": str(grouped["logic_fingerprint"]),
            "representative_rule_id": int(representative["rule_id"]),
            "representative_rule_name": str(representative["rule"].get("name") or f"规则 {representative['rule_id']}"),
            "representative_rule": representative["rule"],
            "representative_definition": representative["definition"],
            "stock_codes": union_stock_codes,
            "run_mode": context["run_mode"],
            "data_policy": context["data_policy"],
            "date_from": context.get("date_from"),
            "date_to": context.get("date_to"),
            "live_cache_key": _build_shared_live_cache_key(
                [entry["context"] for entry in entries],
            ),
            "metadata": {
                "execution_key": execution_key,
                "logic_fingerprint": grouped["logic_fingerprint"],
                "shared_tenant_count": len({str(entry.get("tenant_key") or "") for entry in entries}),
                "tenant_run_ids": [int(entry["run_id"]) for entry in entries],
            },
            "started_at": datetime.now(),
            "consumers": consumers,
            "reused_shared_run": shared_run,
            "owns_shared_run": owns_shared_run,
        })

    private_groups: List[Dict[str, Any]] = []
    for run_id, prepared in private_prepared_by_run.items():
        if not prepared:
            continue
        context = next(item["context"] for item in planned_runs if int(item["context"]["run_id"]) == run_id)
        stock_codes = RuleService._resolve_batch_stock_codes(prepared)
        logic_fingerprints = [
            RuleService._build_rule_logic_fingerprint(definition)
            for _rule_id, _rule, definition, _stock_codes in prepared
        ]
        services_by_run_id[run_id].repo.create_run_segment(
            run_id=run_id,
            segment_key=_hash_payload({
                "version": 1,
                "run_id": run_id,
                "rule_ids": [int(rule_id) for rule_id, _rule, _definition, _codes in prepared],
                "stock_codes": stock_codes,
            }),
            segment_type="private",
            rule_ids=[int(rule_id) for rule_id, _rule, _definition, _codes in prepared],
            rule_fingerprints=logic_fingerprints,
            stock_codes=stock_codes,
        )
        private_groups.append({
            "run_id": run_id,
            "prepared": prepared,
            "stock_codes": stock_codes,
        })

    return {
        "contexts": [item["context"] for item in planned_runs],
        "shared_groups": shared_groups,
        "private_groups": private_groups,
    }


def _start_async_rule_run_for_tenants_legacy(
    payload: RuleBatchRunRequest,
    tenants: List[TenantContext],
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    target = payload.target.model_dump() if payload.target is not None else None
    tenant_runs: List[Dict[str, Any]] = []
    errors: List[str] = []
    for tenant in tenants:
        service = _service_for_tenant(tenant)
        tenant_rule_ids = _visible_rule_ids_for_tenant(service, payload.rule_ids)
        if not tenant_rule_ids:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        try:
            response, context = service.start_run_rules(
                tenant_rule_ids,
                mode=payload.mode,
                target_override=target,
                start_date=payload.start_date,
                end_date=payload.end_date,
                data_policy=payload.data_policy,
                live_cache_key=payload.live_cache_key,
            )
        except KeyError:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        if context is not None:
            background_tasks.add_task(_complete_async_rule_batch, context)
        tenant_runs.append(_tenant_run_item(tenant, response))
    return _aggregate_tenant_run_response(tenant_runs, mode=payload.mode, errors=errors)


def _start_async_rule_run_for_tenants(
    payload: RuleBatchRunRequest,
    tenants: List[TenantContext],
    background_tasks: BackgroundTasks,
) -> Dict[str, Any]:
    target = payload.target.model_dump() if payload.target is not None else None
    tenant_runs: List[Dict[str, Any]] = []
    errors: List[str] = []
    planned_runs: List[Dict[str, Any]] = []
    for tenant in tenants:
        service = _service_for_tenant(tenant)
        if not hasattr(service, "prepare_rule_run_plan") or not hasattr(service, "create_started_run_from_plan"):
            return _start_async_rule_run_for_tenants_legacy(payload, tenants, background_tasks)
        tenant_rule_ids = _visible_rule_ids_for_tenant(service, payload.rule_ids)
        if not tenant_rule_ids:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        try:
            plan = service.prepare_rule_run_plan(
                tenant_rule_ids,
                mode=payload.mode,
                target_override=target,
                start_date=payload.start_date,
                end_date=payload.end_date,
                data_policy=payload.data_policy,
                live_cache_key=payload.live_cache_key,
            )
            response, context = service.create_started_run_from_plan(
                plan,
                allow_reuse=len(tenants) <= 1,
                metadata_extra={"multi_tenant_strategy": "shared_projection_v1"},
            )
        except KeyError:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        if context is not None:
            planned_runs.append({
                "tenant": tenant,
                "service": service,
                "plan": plan,
                "context": context,
            })
        tenant_runs.append(_tenant_run_item(tenant, response))
    if planned_runs:
        orchestration = _build_multi_tenant_orchestration(planned_runs)
        background_tasks.add_task(_complete_async_multi_tenant_rule_batch, orchestration)
    return _aggregate_tenant_run_response(tenant_runs, mode=payload.mode, errors=errors)


def _run_rules_for_tenants(payload: RuleBatchRunRequest, tenants: List[TenantContext]) -> Dict[str, Any]:
    target = payload.target.model_dump() if payload.target is not None else None
    tenant_runs: List[Dict[str, Any]] = []
    errors: List[str] = []
    planned_runs: List[Dict[str, Any]] = []
    for tenant in tenants:
        service = _service_for_tenant(tenant)
        tenant_rule_ids = _visible_rule_ids_for_tenant(service, payload.rule_ids)
        if not tenant_rule_ids:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        try:
            plan = service.prepare_rule_run_plan(
                tenant_rule_ids,
                mode=payload.mode,
                target_override=target,
                start_date=payload.start_date,
                end_date=payload.end_date,
                data_policy=payload.data_policy,
                live_cache_key=payload.live_cache_key,
            )
            response, context = service.create_started_run_from_plan(
                plan,
                allow_reuse=False,
                metadata_extra={
                    "multi_tenant_strategy": "shared_projection_v1",
                    "sync_multi_tenant": True,
                },
            )
        except KeyError:
            errors.append(f"{tenant.name}({tenant.key}) 没有可运行的所选规则，已跳过")
            continue
        if context is not None:
            planned_runs.append({
                "tenant": tenant,
                "service": service,
                "plan": plan,
                "context": context,
            })
        tenant_runs.append(_tenant_run_item(tenant, response))
    if planned_runs:
        orchestration = _build_multi_tenant_orchestration(planned_runs)
        _complete_async_multi_tenant_rule_batch(orchestration)
        completed_runs: List[Dict[str, Any]] = []
        for item in planned_runs:
            tenant = item["tenant"]
            service = item["service"]
            run_id = int(item["context"]["run_id"])
            persisted = service.get_run(run_id)
            if persisted is None:
                continue
            completed_runs.append(_tenant_run_item(tenant, {**persisted, "run_id": persisted["id"]}))
        if completed_runs:
            tenant_runs = completed_runs
    return _aggregate_tenant_run_response(tenant_runs, mode=payload.mode, errors=errors)


@router.get(
    "/metrics",
    response_model=RuleMetricRegistryResponse,
    summary="获取规则指标注册表",
)
def get_rule_metrics() -> RuleMetricRegistryResponse:
    service = RuleService()
    return RuleMetricRegistryResponse(items=service.get_metrics())


@router.get("", response_model=RuleListResponse, summary="获取规则列表")
def list_rules(tenant: TenantContext = Depends(get_tenant_context)) -> RuleListResponse:
    service = _service_for_tenant(tenant)
    return RuleListResponse(items=[RuleItem(**item) for item in service.list_rules()])


@router.get("/runs", response_model=RuleRunHistoryResponse, summary="获取规则运行历史")
def list_rule_runs(
    limit: int = 30,
    tenant: TenantContext = Depends(get_tenant_context),
) -> RuleRunHistoryResponse:
    service = _service_for_tenant(tenant)
    bounded_limit = min(max(int(limit or 30), 1), 100)
    return RuleRunHistoryResponse(items=service.list_runs(limit=bounded_limit))


@router.get(
    "/runs/{run_id}",
    response_model=RuleRunHistoryItem,
    responses={404: {"description": "运行记录不存在", "model": ErrorResponse}},
    summary="获取规则运行状态",
)
def get_rule_run(run_id: int, tenant: TenantContext = Depends(get_tenant_context)) -> RuleRunHistoryItem:
    service = _service_for_tenant(tenant)
    run = service.get_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "运行记录不存在"})
    return RuleRunHistoryItem(**run)


@router.get(
    "/runs/{run_id}/matches",
    response_model=RuleRunMatchListResponse,
    responses={404: {"description": "运行记录不存在", "model": ErrorResponse}},
    summary="获取规则运行命中明细",
)
def list_rule_run_matches(
    run_id: int,
    tenant: TenantContext = Depends(get_tenant_context),
) -> RuleRunMatchListResponse:
    service = _service_for_tenant(tenant)
    return RuleRunMatchListResponse(items=service.list_run_matches(run_id))


@router.post(
    "/runs/{run_id}/notify",
    response_model=RuleRunNotifyResponse,
    summary="推送规则实测命中通知",
)
def notify_rule_run_matches(
    run_id: int,
    payload: RuleRunNotifyRequest | None = None,
    tenant: TenantContext = Depends(get_tenant_context),
) -> RuleRunNotifyResponse:
    service = _service_for_tenant(tenant)
    data = payload or RuleRunNotifyRequest()
    result = service.notify_live_matches(
        run_id,
        execution_time=data.execution_time,
        rule_ids=data.rule_ids,
        rule_names=data.rule_names,
        compact=data.compact,
    )
    return RuleRunNotifyResponse(**result)


@router.delete(
    "/runs/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "运行记录不存在", "model": ErrorResponse}},
    summary="删除规则运行记录",
)
def delete_rule_run(run_id: int, tenant: TenantContext = Depends(get_tenant_context)) -> None:
    service = _service_for_tenant(tenant)
    if not service.delete_run(run_id):
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "运行记录不存在"})


@router.post(
    "",
    response_model=RuleItem,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "规则无效", "model": ErrorResponse}},
    summary="创建规则",
)
def create_rule(payload: RuleCreateRequest, tenant: TenantContext = Depends(get_tenant_context)) -> RuleItem:
    service = _service_for_tenant(tenant)
    try:
        return RuleItem(**service.create_rule(payload))
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc


@router.get(
    "/{rule_id}",
    response_model=RuleItem,
    responses={404: {"description": "规则不存在", "model": ErrorResponse}},
    summary="获取规则详情",
)
def get_rule(rule_id: int, tenant: TenantContext = Depends(get_tenant_context)) -> RuleItem:
    service = _service_for_tenant(tenant)
    rule = service.get_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"})
    return RuleItem(**rule)


@router.put(
    "/{rule_id}",
    response_model=RuleItem,
    responses={
        400: {"description": "规则无效", "model": ErrorResponse},
        404: {"description": "规则不存在", "model": ErrorResponse},
    },
    summary="更新规则",
)
def update_rule(
    rule_id: int,
    payload: RuleUpdateRequest,
    tenant: TenantContext = Depends(get_tenant_context),
) -> RuleItem:
    service = _service_for_tenant(tenant)
    try:
        rule = service.update_rule(rule_id, payload)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc
    if rule is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"})
    return RuleItem(**rule)


@router.post(
    "/{rule_id}/clone",
    response_model=RuleItem,
    responses={404: {"description": "规则不存在", "model": ErrorResponse}},
    summary="克隆共享规则到当前租户",
)
def clone_rule(rule_id: int, tenant: TenantContext = Depends(get_tenant_context)) -> RuleItem:
    service = _service_for_tenant(tenant)
    rule = service.clone_rule(rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"})
    return RuleItem(**rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "规则不存在", "model": ErrorResponse}},
    summary="删除规则",
)
def delete_rule(rule_id: int, tenant: TenantContext = Depends(get_tenant_context)) -> None:
    service = _service_for_tenant(tenant)
    try:
        deleted = service.delete_rule(rule_id)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"})


@router.post(
    "/run-batch/async",
    response_model=RuleRunResponse,
    responses={
        400: {"description": "规则无效", "model": ErrorResponse},
        404: {"description": "规则不存在", "model": ErrorResponse},
    },
    summary="异步批量运行规则",
)
def start_async_rule_run(
    payload: RuleBatchRunRequest,
    background_tasks: BackgroundTasks,
    tenant: TenantContext = Depends(get_tenant_context),
    tenant_service: TenantService = Depends(get_tenant_service),
) -> RuleRunResponse:
    try:
        run_tenants = _resolve_rule_run_tenants(payload, tenant, tenant_service)
        if len(run_tenants) > 1 or (run_tenants and run_tenants[0].key != tenant.key):
            return RuleRunResponse(**_start_async_rule_run_for_tenants(payload, run_tenants, background_tasks))

        service = _service_for_tenant(tenant)
        target = payload.target.model_dump() if payload.target is not None else None
        response, context = service.start_run_rules(
            payload.rule_ids,
            mode=payload.mode,
            target_override=target,
            start_date=payload.start_date,
            end_date=payload.end_date,
            data_policy=payload.data_policy,
            live_cache_key=payload.live_cache_key,
        )
        if context is not None:
            background_tasks.add_task(_complete_async_rule_batch, context)
        response["run_ids"] = [response["run_id"]]
        response["tenant_id"] = tenant.id
        response["tenant_key"] = tenant.key
        response["tenant_runs"] = [_tenant_run_item(tenant, response)]
        return RuleRunResponse(**response)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"}) from exc
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc


@router.delete(
    "/live-cache/{live_cache_key}",
    response_model=SuccessResponse,
    summary="清理规则实测数据缓存",
)
def clear_live_rule_history_cache(
    live_cache_key: str,
    tenant: TenantContext = Depends(get_tenant_context),
) -> SuccessResponse:
    service = _service_for_tenant(tenant)
    result = service.clear_live_rule_history_cache(live_cache_key)
    return SuccessResponse(success=True, message="规则实测数据缓存已清理", data=result)


@router.post(
    "/run-batch",
    response_model=RuleRunResponse,
    responses={
        400: {"description": "规则无效", "model": ErrorResponse},
        404: {"description": "规则不存在", "model": ErrorResponse},
    },
    summary="批量运行规则",
)
def run_rules(
    payload: RuleBatchRunRequest,
    tenant: TenantContext = Depends(get_tenant_context),
    tenant_service: TenantService = Depends(get_tenant_service),
) -> RuleRunResponse:
    try:
        run_tenants = _resolve_rule_run_tenants(payload, tenant, tenant_service)
        if len(run_tenants) > 1 or (run_tenants and run_tenants[0].key != tenant.key):
            return RuleRunResponse(**_run_rules_for_tenants(payload, run_tenants))

        service = _service_for_tenant(tenant)
        target = payload.target.model_dump() if payload.target is not None else None
        response = service.run_rules(
            payload.rule_ids,
            mode=payload.mode,
            target_override=target,
            start_date=payload.start_date,
            end_date=payload.end_date,
            data_policy=payload.data_policy,
        )
        response["run_ids"] = [response["run_id"]]
        response["tenant_id"] = tenant.id
        response["tenant_key"] = tenant.key
        response["tenant_runs"] = [_tenant_run_item(tenant, response)]
        return RuleRunResponse(**response)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"}) from exc
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc


@router.post(
    "/{rule_id}/run",
    response_model=RuleRunResponse,
    responses={
        400: {"description": "规则无效", "model": ErrorResponse},
        404: {"description": "规则不存在", "model": ErrorResponse},
    },
    summary="手动运行规则",
)
def run_rule(
    rule_id: int,
    payload: RuleRunRequest | None = None,
    tenant: TenantContext = Depends(get_tenant_context),
) -> RuleRunResponse:
    service = _service_for_tenant(tenant)
    try:
        mode = payload.mode if payload is not None else "history"
        target = payload.target.model_dump() if payload is not None and payload.target is not None else None
        return RuleRunResponse(**service.run_rule(
            rule_id,
            mode=mode,
            target_override=target,
            start_date=payload.start_date if payload is not None else None,
            end_date=payload.end_date if payload is not None else None,
            data_policy=payload.data_policy if payload is not None else "default",
        ))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"}) from exc
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc
