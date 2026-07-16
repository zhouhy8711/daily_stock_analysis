# -*- coding: utf-8 -*-
"""Tenant workspace endpoints."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status

from api.deps import get_tenant_service
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.tenants import (
    TenantConfigResponse,
    TenantConfigUpdateRequest,
    TenantCreateRequest,
    TenantItem,
    TenantListResponse,
    TenantUpdateRequest,
)
from src.services.tenant_service import (
    TenantInactiveError,
    TenantNotFoundError,
    TenantService,
    TenantValidationError,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _tenant_error_to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, TenantValidationError):
        return HTTPException(status_code=400, detail={"error": "invalid_tenant", "message": str(exc)})
    if isinstance(exc, TenantNotFoundError):
        return HTTPException(status_code=404, detail={"error": "tenant_not_found", "message": str(exc)})
    if isinstance(exc, TenantInactiveError):
        return HTTPException(status_code=403, detail={"error": "tenant_inactive", "message": str(exc)})
    return HTTPException(status_code=500, detail={"error": "internal_error", "message": "Tenant operation failed"})


@router.get("", response_model=TenantListResponse, summary="获取租户列表")
def list_tenants(
    include_inactive: bool = Query(False, description="是否包含已停用租户"),
    service: TenantService = Depends(get_tenant_service),
) -> TenantListResponse:
    return TenantListResponse(
        items=[TenantItem(**item) for item in service.list_tenants(include_inactive=include_inactive)]
    )


@router.post(
    "",
    response_model=TenantItem,
    status_code=status.HTTP_201_CREATED,
    responses={400: {"description": "租户无效", "model": ErrorResponse}},
    summary="创建租户",
)
def create_tenant(
    payload: TenantCreateRequest,
    service: TenantService = Depends(get_tenant_service),
) -> TenantItem:
    try:
        return TenantItem(**service.create_tenant(payload.model_dump()))
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc


@router.put(
    "/{tenant_key}",
    response_model=TenantItem,
    responses={
        400: {"description": "租户无效", "model": ErrorResponse},
        404: {"description": "租户不存在", "model": ErrorResponse},
    },
    summary="更新租户",
)
def update_tenant(
    tenant_key: str,
    payload: TenantUpdateRequest,
    service: TenantService = Depends(get_tenant_service),
) -> TenantItem:
    try:
        data = {key: value for key, value in payload.model_dump().items() if value is not None}
        return TenantItem(**service.update_tenant(tenant_key, data))
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc


@router.delete(
    "/{tenant_key}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        400: {"description": "租户无效", "model": ErrorResponse},
        404: {"description": "租户不存在", "model": ErrorResponse},
    },
    summary="停用租户",
)
def deactivate_tenant(
    tenant_key: str,
    service: TenantService = Depends(get_tenant_service),
) -> None:
    try:
        service.deactivate_tenant(tenant_key)
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc


@router.get(
    "/{tenant_key}/config",
    response_model=TenantConfigResponse,
    responses={404: {"description": "租户不存在", "model": ErrorResponse}},
    summary="获取租户配置",
)
def get_tenant_config(
    tenant_key: str,
    service: TenantService = Depends(get_tenant_service),
) -> TenantConfigResponse:
    try:
        return TenantConfigResponse(**service.get_tenant_config(tenant_key))
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc


@router.put(
    "/{tenant_key}/config",
    response_model=TenantConfigResponse,
    responses={
        400: {"description": "配置无效", "model": ErrorResponse},
        404: {"description": "租户不存在", "model": ErrorResponse},
    },
    summary="更新租户配置",
)
def update_tenant_config(
    tenant_key: str,
    payload: TenantConfigUpdateRequest,
    service: TenantService = Depends(get_tenant_service),
) -> TenantConfigResponse:
    try:
        data = {
            key: value
            for key, value in payload.model_dump().items()
            if key != "mask_token" and value is not None
        }
        return TenantConfigResponse(**service.update_tenant_config(
            tenant_key,
            data,
            mask_token=payload.mask_token,
        ))
    except (TenantValidationError, TenantNotFoundError, TenantInactiveError) as exc:
        raise _tenant_error_to_http(exc) from exc
