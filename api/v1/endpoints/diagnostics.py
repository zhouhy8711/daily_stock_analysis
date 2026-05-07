# -*- coding: utf-8 -*-
"""Read-only diagnostics endpoints."""

from __future__ import annotations

import logging
from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from api.deps import get_database_manager
from api.v1.schemas.common import ErrorResponse
from api.v1.schemas.diagnostics import StockDataDiagnosticsResponse
from src.services.stock_data_diagnostics_service import (
    MAX_DIAGNOSTIC_LIMIT,
    StockDataDiagnosticsService,
    VALID_DIAGNOSTIC_SCOPES,
    VALID_DIAGNOSTIC_SORTS,
)
from src.storage import DatabaseManager

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/stock-data",
    response_model=StockDataDiagnosticsResponse,
    responses={
        200: {"description": "Stock data diagnostics loaded"},
        400: {"description": "Invalid diagnostics parameter", "model": ErrorResponse},
        500: {"description": "Internal server error", "model": ErrorResponse},
    },
    summary="Get stock data diagnostics",
    description="Inspect stock_daily, stock_intraday_minute, and in-process realtime quote cache coverage.",
)
def get_stock_data_diagnostics(
    trade_date: Optional[date] = Query(None, description="Trade date for intraday hot-table diagnostics"),
    scope: str = Query("observed", description="observed/history_db/active_a_share"),
    limit: int = Query(200, ge=1, le=MAX_DIAGNOSTIC_LIMIT, description="Page size"),
    offset: int = Query(0, ge=0, description="Page offset"),
    q: Optional[str] = Query(None, description="Filter by stock code or name"),
    sort: str = Query("code", description="code/history_rows_desc/intraday_rows_desc/latest_daily_desc"),
    db_manager: DatabaseManager = Depends(get_database_manager),
) -> StockDataDiagnosticsResponse:
    if scope not in VALID_DIAGNOSTIC_SCOPES:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_scope", "message": f"Unsupported scope: {scope}"},
        )
    if sort not in VALID_DIAGNOSTIC_SORTS:
        raise HTTPException(
            status_code=400,
            detail={"error": "invalid_sort", "message": f"Unsupported sort: {sort}"},
        )

    try:
        payload = StockDataDiagnosticsService(db_manager).get_stock_data_diagnostics(
            trade_date=trade_date,
            scope=scope,
            limit=limit,
            offset=offset,
            q=q,
            sort=sort,
        )
        return StockDataDiagnosticsResponse.model_validate(payload)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to load stock data diagnostics: %s", exc, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={
                "error": "internal_error",
                "message": "Failed to load stock data diagnostics",
            },
        )
