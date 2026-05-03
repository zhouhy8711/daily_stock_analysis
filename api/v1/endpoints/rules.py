# -*- coding: utf-8 -*-
"""Stock rule API endpoints."""

from fastapi import APIRouter, HTTPException, status

from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.rules import (
    RuleCreateRequest,
    RuleItem,
    RuleListResponse,
    RuleMetricRegistryResponse,
    RuleRunRequest,
    RuleRunResponse,
    RuleUpdateRequest,
)
from src.services.rule_service import RuleService, RuleValidationError

router = APIRouter()


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
        return RuleRunResponse(**service.run_rule(rule_id, mode=mode))
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"error": "not_found", "message": "规则不存在"}) from exc
    except RuleValidationError as exc:
        raise HTTPException(status_code=400, detail={"error": "invalid_rule", "message": str(exc)}) from exc
