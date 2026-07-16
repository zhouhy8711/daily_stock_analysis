# -*- coding: utf-8 -*-
"""Repository helpers for tenant workspaces."""

from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from sqlalchemy import desc, select

from src.config import get_config
from src.storage import DatabaseManager, Tenant, TenantConfig

DEFAULT_TENANT_KEY = "default"
_TENANT_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def normalize_tenant_key(value: str) -> str:
    """Normalize and validate tenant keys used in URLs and headers."""
    key = str(value or "").strip().lower()
    if not _TENANT_KEY_RE.match(key):
        raise ValueError("租户 key 只能包含小写字母、数字、短横线和下划线，且必须以字母或数字开头")
    return key


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


class TenantRepository:
    """DB access layer for tenant metadata and tenant-scoped settings."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @staticmethod
    def tenant_to_dict(row: Tenant) -> Dict[str, Any]:
        return {
            "id": row.id,
            "key": row.key,
            "name": row.name,
            "description": row.description,
            "is_active": bool(row.is_active),
            "is_default": bool(row.is_default),
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    @staticmethod
    def config_to_dict(row: Optional[TenantConfig]) -> Dict[str, Any]:
        if row is None:
            return {
                "stock_list": [],
                "feishu_webhook_url": "",
                "feishu_webhook_secret": "",
                "feishu_webhook_keyword": "",
                "feishu_max_bytes": int(getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
            }
        return {
            "stock_list": [
                str(item).strip()
                for item in (_json_loads(row.stock_list_json, []) or [])
                if str(item).strip()
            ],
            "feishu_webhook_url": row.feishu_webhook_url or "",
            "feishu_webhook_secret": row.feishu_webhook_secret or "",
            "feishu_webhook_keyword": row.feishu_webhook_keyword or "",
            "feishu_max_bytes": int(row.feishu_max_bytes or getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
        }

    def ensure_default_tenant(self) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(Tenant).where(Tenant.key == DEFAULT_TENANT_KEY).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = Tenant(
                    key=DEFAULT_TENANT_KEY,
                    name="默认租户",
                    description="升级兼容创建的默认业务租户",
                    is_active=True,
                    is_default=True,
                )
                session.add(row)
                session.flush()
            else:
                row.is_active = True
                row.is_default = True

            config = session.execute(
                select(TenantConfig).where(TenantConfig.tenant_id == row.id).limit(1)
            ).scalar_one_or_none()
            if config is None:
                config = TenantConfig(
                    tenant_id=row.id,
                    stock_list_json=_json_dumps(list(getattr(get_config(), "stock_list", []) or [])),
                    feishu_max_bytes=int(getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
                )
                session.add(config)

            session.commit()
            session.refresh(row)
            return self.tenant_to_dict(row)

    def list_tenants(self, include_inactive: bool = False) -> List[Dict[str, Any]]:
        self.ensure_default_tenant()
        with self.db.get_session() as session:
            query = select(Tenant)
            if not include_inactive:
                query = query.where(Tenant.is_active.is_(True))
            rows = session.execute(
                query.order_by(desc(Tenant.is_default), Tenant.name.asc(), Tenant.id.asc())
            ).scalars().all()
            return [self.tenant_to_dict(row) for row in rows]

    def get_by_key(self, key: str) -> Optional[Dict[str, Any]]:
        normalized_key = normalize_tenant_key(key)
        self.ensure_default_tenant()
        with self.db.get_session() as session:
            row = session.execute(
                select(Tenant).where(Tenant.key == normalized_key).limit(1)
            ).scalar_one_or_none()
            return self.tenant_to_dict(row) if row else None

    def get_default(self) -> Dict[str, Any]:
        self.ensure_default_tenant()
        with self.db.get_session() as session:
            row = session.execute(
                select(Tenant).where(Tenant.is_default.is_(True)).order_by(Tenant.id.asc()).limit(1)
            ).scalar_one_or_none()
            if row is None:
                return self.ensure_default_tenant()
            return self.tenant_to_dict(row)

    def create_tenant(self, data: Dict[str, Any]) -> Dict[str, Any]:
        key = normalize_tenant_key(data.get("key") or "")
        name = str(data.get("name") or "").strip()
        if not name:
            raise ValueError("租户名称不能为空")
        with self.db.get_session() as session:
            existing = session.execute(select(Tenant).where(Tenant.key == key).limit(1)).scalar_one_or_none()
            if existing is not None:
                raise ValueError("租户 key 已存在")
            row = Tenant(
                key=key,
                name=name,
                description=data.get("description"),
                is_active=True,
                is_default=False,
            )
            session.add(row)
            session.flush()
            config = TenantConfig(
                tenant_id=row.id,
                stock_list_json=_json_dumps([]),
                feishu_max_bytes=int(getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
            )
            session.add(config)
            session.commit()
            session.refresh(row)
            return self.tenant_to_dict(row)

    def update_tenant(self, key: str, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        normalized_key = normalize_tenant_key(key)
        with self.db.get_session() as session:
            row = session.execute(select(Tenant).where(Tenant.key == normalized_key).limit(1)).scalar_one_or_none()
            if row is None:
                return None
            if "name" in data and data["name"] is not None:
                name = str(data["name"] or "").strip()
                if not name:
                    raise ValueError("租户名称不能为空")
                row.name = name
            if "description" in data:
                row.description = data.get("description")
            if "is_active" in data and data["is_active"] is not None:
                row.is_active = bool(data["is_active"]) or bool(row.is_default)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return self.tenant_to_dict(row)

    def deactivate_tenant(self, key: str) -> bool:
        normalized_key = normalize_tenant_key(key)
        with self.db.get_session() as session:
            row = session.execute(select(Tenant).where(Tenant.key == normalized_key).limit(1)).scalar_one_or_none()
            if row is None:
                return False
            if row.is_default:
                raise ValueError("默认租户不能停用")
            row.is_active = False
            row.updated_at = datetime.now()
            session.commit()
            return True

    def get_config(self, tenant_id: int) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(TenantConfig).where(TenantConfig.tenant_id == tenant_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = TenantConfig(
                    tenant_id=tenant_id,
                    stock_list_json=_json_dumps([]),
                    feishu_max_bytes=int(getattr(get_config(), "feishu_max_bytes", 20000) or 20000),
                )
                session.add(row)
                session.commit()
                session.refresh(row)
            return self.config_to_dict(row)

    def update_config(self, tenant_id: int, data: Dict[str, Any]) -> Dict[str, Any]:
        with self.db.get_session() as session:
            row = session.execute(
                select(TenantConfig).where(TenantConfig.tenant_id == tenant_id).limit(1)
            ).scalar_one_or_none()
            if row is None:
                row = TenantConfig(tenant_id=tenant_id, stock_list_json=_json_dumps([]))
                session.add(row)
                session.flush()

            if "stock_list" in data:
                row.stock_list_json = _json_dumps(data.get("stock_list") or [])
            if "feishu_webhook_url" in data:
                row.feishu_webhook_url = data.get("feishu_webhook_url") or None
            if "feishu_webhook_secret" in data:
                row.feishu_webhook_secret = data.get("feishu_webhook_secret") or None
            if "feishu_webhook_keyword" in data:
                row.feishu_webhook_keyword = data.get("feishu_webhook_keyword") or None
            if "feishu_max_bytes" in data and data.get("feishu_max_bytes") is not None:
                row.feishu_max_bytes = int(data.get("feishu_max_bytes") or 20000)
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return self.config_to_dict(row)
