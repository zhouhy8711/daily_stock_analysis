# -*- coding: utf-8 -*-
"""
===================================
API 依赖注入模块
===================================

职责：
1. 提供数据库 Session 依赖
2. 提供配置依赖
3. 提供服务层依赖
"""

from typing import Generator

from fastapi import Request
from sqlalchemy.orm import Session

from src.storage import DatabaseManager
from src.config import get_config, Config
from src.services.system_config_service import SystemConfigService
from src.services.tenant_service import (
    TenantContext,
    TenantInactiveError,
    TenantNotFoundError,
    TenantService,
    TenantValidationError,
)
from fastapi import HTTPException


def get_db() -> Generator[Session, None, None]:
    """
    获取数据库 Session 依赖
    
    使用 FastAPI 依赖注入机制，确保请求结束后自动关闭 Session
    
    Yields:
        Session: SQLAlchemy Session 对象
        
    Example:
        @router.get("/items")
        async def get_items(db: Session = Depends(get_db)):
            ...
    """
    db_manager = DatabaseManager.get_instance()
    session = db_manager.get_session()
    try:
        yield session
    finally:
        session.close()


def get_config_dep() -> Config:
    """
    获取配置依赖
    
    Returns:
        Config: 配置单例对象
    """
    return get_config()


def get_database_manager() -> DatabaseManager:
    """
    获取数据库管理器依赖
    
    Returns:
        DatabaseManager: 数据库管理器单例对象
    """
    return DatabaseManager.get_instance()


def get_system_config_service(request: Request) -> SystemConfigService:
    """Get app-lifecycle shared SystemConfigService instance."""
    service = getattr(request.app.state, "system_config_service", None)
    if service is None:
        service = SystemConfigService()
        request.app.state.system_config_service = service
    return service


def get_tenant_service(request: Request) -> TenantService:
    """Get app-lifecycle shared TenantService instance."""
    service = getattr(request.app.state, "tenant_service", None)
    if service is None:
        service = TenantService()
        request.app.state.tenant_service = service
    return service


def get_tenant_context(request: Request) -> TenantContext:
    """Resolve the current business tenant from X-DSA-Tenant."""
    service = get_tenant_service(request)
    tenant_key = request.headers.get("X-DSA-Tenant")
    try:
        return service.resolve_context(tenant_key)
    except TenantValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_tenant", "message": str(exc)},
        ) from exc
    except TenantNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail={"error": "tenant_not_found", "message": str(exc)},
        ) from exc
    except TenantInactiveError as exc:
        raise HTTPException(
            status_code=403,
            detail={"error": "tenant_inactive", "message": str(exc)},
        ) from exc
