# -*- coding: utf-8 -*-
"""Tenant workspace service."""

from __future__ import annotations

import copy
import re
from collections.abc import Sequence as SequenceABC
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from src.config import Config, get_config
from src.repositories.tenant_repo import DEFAULT_TENANT_KEY, TenantRepository, normalize_tenant_key


class TenantError(Exception):
    """Base class for tenant service errors."""


class TenantNotFoundError(TenantError):
    """Raised when a tenant key cannot be resolved."""


class TenantInactiveError(TenantError):
    """Raised when a tenant exists but is inactive."""


class TenantValidationError(TenantError):
    """Raised when tenant input is invalid."""


@dataclass(frozen=True)
class TenantContext:
    """Resolved tenant identity for request-scoped business data."""

    id: int
    key: str
    name: str
    is_default: bool = False


def _normalize_stock_code(value: Any) -> str:
    raw = str(value or "").strip().upper()
    if not raw:
        return ""
    match = re.search(
        r"(?:SH|SZ|BJ)\d{6}|\d{6}\.(?:SH|SZ|SS|BJ)|HK\d{1,5}|\d{1,5}\.HK|\d{6}|\d{5}|[A-Z]{1,5}(?:\.US)?",
        raw,
    )
    code = match.group(0) if match else raw.split()[0]
    hk_prefix = re.match(r"^HK(\d{1,5})$", code)
    if hk_prefix:
        return f"HK{hk_prefix.group(1).zfill(5)}"
    hk_suffix = re.match(r"^(\d{1,5})\.HK$", code)
    if hk_suffix:
        return f"{hk_suffix.group(1).zfill(5)}.HK"
    return code


def _normalize_stock_list(values: Sequence[Any]) -> List[str]:
    deduped: List[str] = []
    seen = set()
    for value in values:
        if isinstance(value, str) and any(separator in value for separator in (",", "，", ";", "；", "\n")):
            parts = re.split(r"[,，;；\n]+", value)
        else:
            parts = [value]
        for part in parts:
            code = _normalize_stock_code(part)
            if code and code not in seen:
                seen.add(code)
                deduped.append(code)
    return deduped


class TenantService:
    """Service layer for tenant workspaces and tenant-scoped settings."""

    def __init__(self, repo: Optional[TenantRepository] = None):
        self.repo = repo or TenantRepository()

    def resolve_context(self, tenant_key: Optional[str] = None) -> TenantContext:
        """Resolve an active tenant from an optional request header value."""
        if tenant_key and tenant_key.strip():
            try:
                key = normalize_tenant_key(tenant_key)
            except ValueError as exc:
                raise TenantValidationError(str(exc)) from exc
            tenant = self.repo.get_by_key(key)
            if tenant is None:
                raise TenantNotFoundError(f"租户不存在: {key}")
        else:
            tenant = self.repo.get_default()

        if not tenant.get("is_active"):
            raise TenantInactiveError(f"租户已停用: {tenant.get('key')}")

        return TenantContext(
            id=int(tenant["id"]),
            key=str(tenant["key"]),
            name=str(tenant["name"]),
            is_default=bool(tenant.get("is_default")),
        )

    def list_tenants(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        return self.repo.list_tenants(include_inactive=include_inactive)

    def create_tenant(self, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return self.repo.create_tenant(data)
        except ValueError as exc:
            raise TenantValidationError(str(exc)) from exc

    def update_tenant(self, key: str, data: Dict[str, Any]) -> Dict[str, Any]:
        try:
            tenant = self.repo.update_tenant(key, data)
        except ValueError as exc:
            raise TenantValidationError(str(exc)) from exc
        if tenant is None:
            raise TenantNotFoundError(f"租户不存在: {key}")
        return tenant

    def deactivate_tenant(self, key: str) -> None:
        try:
            deleted = self.repo.deactivate_tenant(key)
        except ValueError as exc:
            raise TenantValidationError(str(exc)) from exc
        if not deleted:
            raise TenantNotFoundError(f"租户不存在: {key}")

    def get_tenant_config(self, tenant_key: str, mask_token: str = "******") -> Dict[str, Any]:
        context = self.resolve_context(tenant_key)
        config = self.repo.get_config(context.id)
        secret_exists = bool((config.get("feishu_webhook_secret") or "").strip())
        return {
            "tenant": {
                "id": context.id,
                "key": context.key,
                "name": context.name,
                "is_default": context.is_default,
            },
            "mask_token": mask_token,
            "stock_list": config.get("stock_list") or [],
            "feishu_webhook_url": config.get("feishu_webhook_url") or "",
            "feishu_webhook_secret": mask_token if secret_exists else "",
            "feishu_webhook_secret_exists": secret_exists,
            "feishu_webhook_keyword": config.get("feishu_webhook_keyword") or "",
            "feishu_max_bytes": int(config.get("feishu_max_bytes") or getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
        }

    def update_tenant_config(
        self,
        tenant_key: str,
        data: Dict[str, Any],
        *,
        mask_token: str = "******",
    ) -> Dict[str, Any]:
        context = self.resolve_context(tenant_key)
        current = self.repo.get_config(context.id)
        updates: Dict[str, Any] = {}

        if "stock_list" in data:
            raw_stock_list = data.get("stock_list")
            if isinstance(raw_stock_list, str):
                stock_values: Sequence[Any] = [raw_stock_list]
            elif isinstance(raw_stock_list, SequenceABC):
                stock_values = list(raw_stock_list)
            else:
                stock_values = []
            updates["stock_list"] = _normalize_stock_list(stock_values)

        for field in ("feishu_webhook_url", "feishu_webhook_keyword"):
            if field in data:
                updates[field] = str(data.get(field) or "").strip()

        if "feishu_webhook_secret" in data:
            value = str(data.get("feishu_webhook_secret") or "")
            if value == mask_token and current.get("feishu_webhook_secret"):
                pass
            else:
                updates["feishu_webhook_secret"] = value.strip()

        if "feishu_max_bytes" in data:
            try:
                max_bytes = int(data.get("feishu_max_bytes") or 20000)
            except (TypeError, ValueError) as exc:
                raise TenantValidationError("FEISHU_MAX_BYTES 必须是整数") from exc
            if max_bytes < 1024:
                raise TenantValidationError("FEISHU_MAX_BYTES 不能小于 1024")
            updates["feishu_max_bytes"] = max_bytes

        self.repo.update_config(context.id, updates)
        return self.get_tenant_config(context.key, mask_token=mask_token)

    def get_stock_list(self, tenant_id: int) -> List[str]:
        return list(self.repo.get_config(tenant_id).get("stock_list") or [])

    def build_notification_config(self, tenant_id: int) -> Config:
        """Return a runtime config with tenant Feishu values overriding global Feishu."""
        tenant_config = self.repo.get_config(tenant_id)
        global_config = get_config()
        config = copy.copy(global_config)
        default_tenant = self.repo.get_default()
        is_default_tenant = int(default_tenant.get("id") or 0) == int(tenant_id)

        def tenant_or_default(field: str) -> Optional[str]:
            tenant_value = str(tenant_config.get(field) or "").strip()
            if tenant_value:
                return tenant_value
            if is_default_tenant:
                global_value = str(getattr(global_config, field, None) or "").strip()
                return global_value or None
            return None

        config.feishu_webhook_url = tenant_or_default("feishu_webhook_url")
        config.feishu_webhook_secret = tenant_or_default("feishu_webhook_secret")
        config.feishu_webhook_keyword = tenant_or_default("feishu_webhook_keyword")
        config.feishu_max_bytes = int(
            tenant_config.get("feishu_max_bytes")
            or getattr(global_config, "feishu_max_bytes", 20000)
            or 20000
        )
        return config
