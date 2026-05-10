# -*- coding: utf-8 -*-
"""Stock rule API endpoints."""

from fastapi import APIRouter, BackgroundTasks, HTTPException, status

from api.v1.schemas.common import ErrorResponse
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
from src.services.rule_service import RuleService, RuleValidationError

router = APIRouter()


def _complete_async_rule_batch(context: dict) -> None:
    service = RuleService()
    service.complete_started_run_rules(**context)


@router.get(
    "/metrics",
    response_model=RuleMetricRegistryResponse,
    summary="获取规则指标注册表",
)
def get_rule_metrics() -> RuleMetricRegistryResponse:
    service = RuleService()
    return RuleMetricRegistryResponse(items=service.get_metrics())


@router.get("", response_model=RuleListResponse, summary="获取规则列表")
def list_rules() -> RuleListResponse:
    service = RuleService()
    return RuleListResponse(items=[RuleItem(**item) for item in service.list_rules()])


@router.get("/runs", response_model=RuleRunHistoryResponse, summary="获取规则运行历史")
def list_rule_runs(limit: int = 30) -> RuleRunHistoryResponse:
    service = RuleService()
    bounded_limit = min(max(int(limit or 30), 1), 100)
    return RuleRunHistoryResponse(items=service.list_runs(limit=bounded_limit))


@router.get(
    "/runs/{run_id}",
    response_model=RuleRunHistoryItem,
    responses={404: {"description": "运行记录不存在", "model": ErrorResponse}},
    summary="获取规则运行状态",
)
def get_rule_run(run_id: int) -> RuleRunHistoryItem:
    service = RuleService()
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
def list_rule_run_matches(run_id: int) -> RuleRunMatchListResponse:
    service = RuleService()
    return RuleRunMatchListResponse(items=service.list_run_matches(run_id))


@router.post(
    "/runs/{run_id}/notify",
    response_model=RuleRunNotifyResponse,
    summary="推送规则实测命中通知",
)
def notify_rule_run_matches(run_id: int, payload: RuleRunNotifyRequest | None = None) -> RuleRunNotifyResponse:
    service = RuleService()
    data = payload or RuleRunNotifyRequest()
    result = service.notify_live_matches(
        run_id,
        execution_time=data.execution_time,
        rule_ids=data.rule_ids,
        rule_names=data.rule_names,
    )
    return RuleRunNotifyResponse(**result)


@router.delete(
    "/runs/{run_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "运行记录不存在", "model": ErrorResponse}},
    summary="删除规则运行记录",
)
def delete_rule_run(run_id: int) -> None:
    service = RuleService()
    if not service.delete_run(run_id):
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "运行记录不存在"})


@router.post(
    "",
    response_model=RuleItem,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "规则无效", "model": ErrorResponse}},
    summary="创建规则",
)
def create_rule(payload: RuleCreateRequest) -> RuleItem:
    service = RuleService()
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
def get_rule(rule_id: int) -> RuleItem:
    service = RuleService()
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
def update_rule(rule_id: int, payload: RuleUpdateRequest) -> RuleItem:
    service = RuleService()
    try:
        rule = service.update_rule(rule_id, payload)
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc
    if rule is None:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"})
    return RuleItem(**rule)


@router.delete(
    "/{rule_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={404: {"description": "规则不存在", "model": ErrorResponse}},
    summary="删除规则",
)
def delete_rule(rule_id: int) -> None:
    service = RuleService()
    if not service.delete_rule(rule_id):
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
def start_async_rule_run(payload: RuleBatchRunRequest, background_tasks: BackgroundTasks) -> RuleRunResponse:
    service = RuleService()
    try:
        target = payload.target.model_dump() if payload.target is not None else None
        response, context = service.start_run_rules(
            payload.rule_ids,
            mode=payload.mode,
            target_override=target,
            start_date=payload.start_date,
            end_date=payload.end_date,
            data_policy=payload.data_policy,
        )
        background_tasks.add_task(_complete_async_rule_batch, context)
        return RuleRunResponse(**response)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"}) from exc
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc


@router.post(
    "/run-batch",
    response_model=RuleRunResponse,
    responses={
        400: {"description": "规则无效", "model": ErrorResponse},
        404: {"description": "规则不存在", "model": ErrorResponse},
    },
    summary="批量运行规则",
)
def run_rules(payload: RuleBatchRunRequest) -> RuleRunResponse:
    service = RuleService()
    try:
        target = payload.target.model_dump() if payload.target is not None else None
        return RuleRunResponse(**service.run_rules(
            payload.rule_ids,
            mode=payload.mode,
            target_override=target,
            start_date=payload.start_date,
            end_date=payload.end_date,
            data_policy=payload.data_policy,
        ))
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
def run_rule(rule_id: int, payload: RuleRunRequest | None = None) -> RuleRunResponse:
    service = RuleService()
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
