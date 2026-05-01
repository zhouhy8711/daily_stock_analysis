# -*- coding: utf-8 -*-
"""
===================================
股票数据相关模型
===================================

职责：
1. 定义股票实时行情模型
2. 定义历史 K 线数据模型
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class StockQuote(BaseModel):
    """股票实时行情"""
    
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    current_price: float = Field(..., description="当前价格")
    change: Optional[float] = Field(None, description="涨跌额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    open: Optional[float] = Field(None, description="开盘价")
    high: Optional[float] = Field(None, description="最高价")
    low: Optional[float] = Field(None, description="最低价")
    prev_close: Optional[float] = Field(None, description="昨收价")
    volume: Optional[float] = Field(None, description="成交量（股）")
    amount: Optional[float] = Field(None, description="成交额（元）")
    volume_ratio: Optional[float] = Field(None, description="量比")
    turnover_rate: Optional[float] = Field(None, description="换手率 (%)")
    amplitude: Optional[float] = Field(None, description="振幅 (%)")
    source: Optional[str] = Field(None, description="行情数据源")
    update_time: Optional[str] = Field(None, description="更新时间")
    
    class Config:
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "current_price": 1800.00,
                "change": 15.00,
                "change_percent": 0.84,
                "open": 1785.00,
                "high": 1810.00,
                "low": 1780.00,
                "prev_close": 1785.00,
                "volume": 10000000,
                "amount": 18000000000,
                "volume_ratio": 1.2,
                "turnover_rate": 0.8,
                "amplitude": 1.6,
                "source": "efinance",
                "update_time": "2024-01-01T15:00:00"
            }
        }


class StockQuotesRequest(BaseModel):
    """批量实时行情请求"""

    stock_codes: List[str] = Field(..., description="股票代码列表")


class StockQuotesResponse(BaseModel):
    """批量实时行情响应"""

    items: List[StockQuote] = Field(default_factory=list, description="成功获取的行情列表")
    failed_codes: List[str] = Field(default_factory=list, description="未获取到行情的股票代码")
    update_time: Optional[str] = Field(None, description="接口响应时间")


class KLineData(BaseModel):
    """K 线数据点"""
    
    date: str = Field(..., description="日期或分钟时间")
    open: float = Field(..., description="开盘价")
    high: float = Field(..., description="最高价")
    low: float = Field(..., description="最低价")
    close: float = Field(..., description="收盘价")
    volume: Optional[float] = Field(None, description="成交量")
    amount: Optional[float] = Field(None, description="成交额")
    change_percent: Optional[float] = Field(None, description="涨跌幅 (%)")
    
    class Config:
        json_schema_extra = {
            "example": {
                "date": "2024-01-01",
                "open": 1785.00,
                "high": 1810.00,
                "low": 1780.00,
                "close": 1800.00,
                "volume": 10000000,
                "amount": 18000000000,
                "change_percent": 0.84
            }
        }


class ExtractItem(BaseModel):
    """单条提取结果（代码、名称、置信度）"""

    code: Optional[str] = Field(None, description="股票代码，None 表示解析失败")
    name: Optional[str] = Field(None, description="股票名称（如有）")
    confidence: str = Field("medium", description="置信度：high/medium/low")


class ExtractFromImageResponse(BaseModel):
    """图片股票代码提取响应"""

    codes: List[str] = Field(..., description="提取的股票代码（已去重，向后兼容）")
    items: List[ExtractItem] = Field(default_factory=list, description="提取结果明细（代码+名称+置信度）")
    raw_text: Optional[str] = Field(None, description="原始 LLM 响应（调试用）")


class StockHistoryResponse(BaseModel):
    """股票历史行情响应"""
    
    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    period: str = Field(..., description="K 线周期: daily/1m/5m/15m/30m/60m")
    data: List[KLineData] = Field(default_factory=list, description="K 线数据列表")
    
    class Config:
        json_schema_extra = {
            "example": {
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "period": "daily",
                "data": []
            }
        }


class ChipDistributionMetrics(BaseModel):
    """筹码分布指标"""

    code: str = Field(..., description="股票代码")
    date: Optional[str] = Field(None, description="数据日期")
    source: Optional[str] = Field(None, description="筹码数据源")
    profit_ratio: Optional[float] = Field(None, description="获利比例，0-1")
    avg_cost: Optional[float] = Field(None, description="平均持仓成本")
    cost_90_low: Optional[float] = Field(None, description="90% 筹码成本下限")
    cost_90_high: Optional[float] = Field(None, description="90% 筹码成本上限")
    concentration_90: Optional[float] = Field(None, description="90% 筹码集中度，0-1")
    cost_70_low: Optional[float] = Field(None, description="70% 筹码成本下限")
    cost_70_high: Optional[float] = Field(None, description="70% 筹码成本上限")
    concentration_70: Optional[float] = Field(None, description="70% 筹码集中度，0-1")
    chip_status: Optional[str] = Field(None, description="基于现价推导的筹码状态")


class MajorHolder(BaseModel):
    """主力/机构持仓名称与持股摘要"""

    name: str = Field(..., description="股东或机构名称")
    holder_type: Optional[str] = Field(None, description="股东/机构类型")
    share_type: Optional[str] = Field(None, description="股份类型")
    shares: Optional[float] = Field(None, description="持股数量")
    holding_ratio: Optional[float] = Field(None, description="持股比例 (%)")
    change: Optional[str] = Field(None, description="持股变化")
    change_ratio: Optional[float] = Field(None, description="持股变化比例 (%)")
    report_date: Optional[str] = Field(None, description="报告期")
    announce_date: Optional[str] = Field(None, description="公告日期")
    rank: Optional[int] = Field(None, description="排名")
    source: Optional[str] = Field(None, description="数据源")


class StockIndicatorMetricsResponse(BaseModel):
    """指标分析扩展数据响应"""

    stock_code: str = Field(..., description="股票代码")
    stock_name: Optional[str] = Field(None, description="股票名称")
    chip_distribution: Optional[ChipDistributionMetrics] = Field(None, description="筹码分布指标")
    major_holders: List[MajorHolder] = Field(default_factory=list, description="主力/机构持仓名称")
    major_holder_status: str = Field("not_supported", description="主力/机构持仓数据状态")
    source_chain: List[Dict[str, Any]] = Field(default_factory=list, description="数据源链路")
    errors: List[str] = Field(default_factory=list, description="降级错误信息")
    update_time: Optional[str] = Field(None, description="更新时间")
