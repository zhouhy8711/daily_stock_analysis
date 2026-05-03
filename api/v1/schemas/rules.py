# -*- coding: utf-8 -*-
"""Schemas for stock rules."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class RuleTarget(BaseModel):
    scope: str = Field("watchlist", description="Target stock scope: watchlist/all_a_shares/custom")
    stock_codes: List[str] = Field(default_factory=list, description="Custom stock codes")


class MetricExpression(BaseModel):
    metric: str = Field(..., description="Metric key")
    offset: int = Field(0, ge=0, description="Periods before latest row")


class RuleValueExpression(BaseModel):
    type: str = Field("literal", description="literal/metric/aggregate/range")
    value: Optional[float] = Field(None, description="Literal numeric value")
    metric: Optional[str] = Field(None, description="Metric key")
    method: Optional[str] = Field(None, description="Aggregate method")
    window: Optional[int] = Field(None, ge=1, le=365, description="Aggregate window")
    offset: int = Field(0, ge=0, description="Periods before latest row")
    multiplier: Optional[float] = Field(None, description="Optional multiplier")
    min: Optional[Dict[str, Any]] = Field(None, description="Range lower expression")
    max: Optional[Dict[str, Any]] = Field(None, description="Range upper expression")


class RuleCondition(BaseModel):
    id: str = Field(..., description="Condition ID")
    left: MetricExpression
    operator: str = Field(..., description="Condition operator")
    right: Optional[RuleValueExpression] = None
    compare: Optional[str] = Field(None, description="Inner comparison operator for consecutive/frequency")
    lookback: Optional[int] = Field(None, ge=1, le=365, description="Lookback periods")
    min_count: Optional[int] = Field(None, ge=1, le=365, description="Minimum matched count")


class RuleGroup(BaseModel):
    id: str = Field(..., description="Group ID")
    conditions: List[RuleCondition] = Field(default_factory=list)


class RuleDefinition(BaseModel):
    period: str = Field("daily", description="K line period")
    lookback_days: int = Field(120, ge=20, le=365, description="History lookback days")
    target: RuleTarget = Field(default_factory=RuleTarget)
    groups: List[RuleGroup] = Field(default_factory=list)


class RuleCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: bool = True
    definition: RuleDefinition


class RuleUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = Field(None, max_length=1000)
    is_active: Optional[bool] = None
    definition: Optional[RuleDefinition] = None


class RuleRunRequest(BaseModel):
    mode: str = Field("history", description="latest/history")


class RuleItem(BaseModel):
    id: int
    name: str
    description: Optional[str] = None
    is_active: bool
    period: str
    lookback_days: int
    target_scope: str
    target_codes: List[str] = Field(default_factory=list)
    definition: Dict[str, Any]
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_run_at: Optional[str] = None
    last_match_count: int = 0


class RuleListResponse(BaseModel):
    items: List[RuleItem] = Field(default_factory=list)


class RuleMetricItem(BaseModel):
    key: str
    label: str
    category: str
    value_type: str
    unit: Optional[str] = None
    periods: List[str] = Field(default_factory=list)
    description: str = ""


class RuleMetricRegistryResponse(BaseModel):
    items: List[RuleMetricItem] = Field(default_factory=list)


class RuleMatchItem(BaseModel):
    stock_code: str
    stock_name: Optional[str] = None
    matched_dates: List[str] = Field(default_factory=list)
    matched_events: List[Dict[str, Any]] = Field(default_factory=list)
    matched_groups: List[Dict[str, Any]] = Field(default_factory=list)
    snapshot: Dict[str, Any] = Field(default_factory=dict)
    explanation: Optional[str] = None


class RuleRunResponse(BaseModel):
    run_id: int
    rule_id: int
    status: str
    target_count: int
    match_count: int
    event_count: int = 0
    mode: str = "history"
    duration_ms: int = 0
    matches: List[RuleMatchItem] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
