# -*- coding: utf-8 -*-
"""Tenant API schemas."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class TenantItem(BaseModel):
    id: int
    key: str
    name: str
    description: Optional[str] = None
    is_active: bool
    is_default: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class TenantListResponse(BaseModel):
    items: List[TenantItem] = Field(default_factory=list)


class TenantCreateRequest(BaseModel):
    key: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)


class TenantUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: Optional[bool] = None


class TenantSummary(BaseModel):
    id: int
    key: str
    name: str
    is_default: bool = False


class TenantConfigResponse(BaseModel):
    tenant: TenantSummary
    mask_token: str = "******"
    stock_list: List[str] = Field(default_factory=list)
    feishu_webhook_url: str = ""
    feishu_webhook_secret: str = ""
    feishu_webhook_secret_exists: bool = False
    feishu_webhook_keyword: str = ""
    feishu_max_bytes: int = 20000


class TenantConfigUpdateRequest(BaseModel):
    mask_token: str = "******"
    stock_list: Optional[List[str] | str] = None
    feishu_webhook_url: Optional[str] = None
    feishu_webhook_secret: Optional[str] = None
    feishu_webhook_keyword: Optional[str] = None
    feishu_max_bytes: Optional[int] = Field(None, ge=1024)
