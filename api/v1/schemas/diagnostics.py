# -*- coding: utf-8 -*-
"""Schemas for stock data diagnostics APIs."""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, Field


class StockHistoryCoverage(BaseModel):
    rows: int = Field(..., description="Rows stored in stock_daily")
    first_date: Optional[str] = Field(None, description="Earliest stored daily date")
    last_date: Optional[str] = Field(None, description="Latest stored daily date")
    latest_source: Optional[str] = Field(None, description="Data source of the latest daily row")


class StockIntradayCoverage(BaseModel):
    rows: int = Field(..., description="Rows stored in stock_intraday_minute for trade_date")
    first_minute: Optional[str] = Field(None, description="Earliest minute timestamp")
    last_minute: Optional[str] = Field(None, description="Latest minute timestamp")
    sources: List[str] = Field(default_factory=list, description="Minute data sources")


class StockQuoteCoverage(BaseModel):
    snapshot_hit: bool = Field(..., description="Whether the stock exists in the latest quote snapshot")
    short_cache_hit: bool = Field(..., description="Whether the stock exists in the current short quote cache bucket")


class StockDataDiagnosticsItem(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    history: StockHistoryCoverage
    intraday: StockIntradayCoverage
    quote: StockQuoteCoverage


class StockDataHistorySummary(BaseModel):
    stock_count: int
    row_count: int
    first_date: Optional[str] = None
    last_date: Optional[str] = None
    missing_count: int


class StockDataIntradaySummary(BaseModel):
    stock_count: int
    row_count: int
    first_minute: Optional[str] = None
    last_minute: Optional[str] = None
    missing_count: int


class StockDataQuoteSummary(BaseModel):
    snapshot_id: Optional[str] = None
    snapshot_time: Optional[str] = None
    snapshot_age_seconds: Optional[int] = None
    snapshot_items: int
    short_cache_items: int
    snapshot_hit_count: int
    short_cache_hit_count: int


class StockDataDiagnosticsSummary(BaseModel):
    population_count: int
    history: StockDataHistorySummary
    intraday: StockDataIntradaySummary
    quote: StockDataQuoteSummary


class StockDataDiagnosticsResponse(BaseModel):
    generated_at: str
    trade_date: str
    scope: str
    limit: int
    offset: int
    total: int
    has_more: bool
    summary: StockDataDiagnosticsSummary
    items: List[StockDataDiagnosticsItem] = Field(default_factory=list)
