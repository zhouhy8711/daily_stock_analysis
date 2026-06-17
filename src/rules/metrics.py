# -*- coding: utf-8 -*-
"""Metric registry and indicator calculation for stock rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    category: str
    value_type: str = "number"
    unit: Optional[str] = None
    periods: tuple[str, ...] = ("daily",)
    description: str = ""


METRIC_DEFINITIONS: List[MetricDefinition] = [
    MetricDefinition("current_price", "最新价", "核心行情", unit="元"),
    MetricDefinition("change", "涨跌额", "核心行情", unit="元"),
    MetricDefinition("change_percent", "实时涨跌幅", "核心行情", unit="%"),
    MetricDefinition("total_mv", "总市值", "核心行情", unit="元"),
    MetricDefinition("circ_mv", "流通市值", "核心行情", unit="元"),
    MetricDefinition("pe_ratio", "市盈TTM", "核心行情"),
    MetricDefinition(
        "pe_ratio_percentile_250d",
        "市盈TTM 250日分位",
        "核心行情",
        unit="%",
        description="当前市盈TTM在近 250 个交易日正 PE 样本中的历史分位，用于刻画高估值/高预期状态",
    ),
    MetricDefinition("open", "开盘价", "K线图", unit="元"),
    MetricDefinition("high", "最高价", "K线图", unit="元"),
    MetricDefinition("low", "最低价", "K线图", unit="元"),
    MetricDefinition("close", "收盘价", "K线图", unit="元"),
    MetricDefinition("prev_close", "昨收价", "K线图", unit="元"),
    MetricDefinition("pct_chg", "涨跌幅", "K线图", unit="%"),
    MetricDefinition(
        "price_range_30d_pct",
        "近30日最高最低振幅",
        "K线图",
        unit="%",
        description="近 30 个交易日最高价与最低价的区间宽度，相对区间最低价计算",
    ),
    MetricDefinition(
        "price_range_60d_pct",
        "近60日最高最低振幅",
        "K线图",
        unit="%",
        description="近 60 个交易日最高价与最低价的区间宽度，相对区间最低价计算",
    ),
    MetricDefinition(
        "prev_5d_return_pct",
        "前5日累计涨幅",
        "额外",
        unit="%",
        description="当前判断日前 5 个交易日的复利累计涨幅，不包含当前判断日",
    ),
    MetricDefinition(
        "prev_20d_return_pct",
        "前20日累计涨幅",
        "额外",
        unit="%",
        description="当前判断日前 20 个交易日的复利累计涨幅，不包含当前判断日",
    ),
    MetricDefinition(
        "bias_ma5_pct",
        "偏离MA5",
        "趋势起涨",
        unit="%",
        description="收盘价相对 MA5 的偏离幅度，用于过滤已经明显追高的趋势股",
    ),
    MetricDefinition(
        "ma_bullish_alignment_signal",
        "均线多头排列信号",
        "趋势起涨",
        description="收盘价 > MA5 > MA10 > MA20 > MA30 时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "ma_uptrend_signal",
        "均线上行信号",
        "趋势起涨",
        description="MA5、MA10 和 MA20 均相对近几日抬升时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "price_breakout_20d_signal",
        "突破20日高点信号",
        "趋势起涨",
        description="收盘价突破前 20 个交易日最高价时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "prior_10d_breakout_20d_count",
        "前10日20日突破次数",
        "趋势起涨",
        description="当前判断日前 10 个交易日内突破 20 日高点的次数，用于识别冷却后的首次突破",
    ),
    MetricDefinition(
        "price_breakout_60d_signal",
        "突破60日高点信号",
        "趋势起涨",
        description="收盘价突破前 60 个交易日最高价时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "volume_expansion_20d_ratio",
        "成交量/20日均量",
        "趋势起涨",
        unit="倍",
        description="当日成交量相对 20 日均量的倍数",
    ),
    MetricDefinition(
        "volume_expansion_signal",
        "放量确认信号",
        "趋势起涨",
        description="成交量至少达到 20 日均量 1.2 倍时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "trend_start_signal",
        "趋势起涨信号",
        "趋势起涨",
        description="同时满足多头排列、均线上行、20/60 日突破、放量、MACD 多头且偏离 MA5 不超过 15% 时记为 1",
    ),
    MetricDefinition(
        "trend_live_setup_score",
        "实盘起涨观察分",
        "趋势起涨",
        description="仅使用当前及历史数据，对 MACD 转强、均线修复、放量、RSI 转强和 20 日高位突破进行加权打分",
    ),
    MetricDefinition(
        "trend_live_watch_signal",
        "实盘起涨观察信号",
        "趋势起涨",
        description="实盘起涨观察分达到 7 分、未触发过热风险和失效条件时记为 1",
    ),
    MetricDefinition(
        "trend_live_confirm_signal",
        "实盘起涨确认信号",
        "趋势起涨",
        description="观察分达到 9 分，同时具备 20 日高位突破、放量、MACD 多头且未过热时记为 1",
    ),
    MetricDefinition(
        "trend_overheat_risk_score",
        "趋势追高风险分",
        "趋势起涨",
        description="根据前 5/20 日涨幅、MA5 乖离、RSI 过热和放量长阳一致性计算追高风险分",
    ),
    MetricDefinition(
        "trend_overheat_risk_signal",
        "趋势追高风险信号",
        "趋势起涨",
        description="趋势追高风险分达到 3 分时记为 1，用于过滤已进入明显加速段的候选股",
    ),
    MetricDefinition(
        "trend_failure_signal",
        "趋势起涨失效信号",
        "趋势起涨",
        description="收盘跌破 MA20，或跌破 MA10 且 MACD 转弱，或大跌并跌破 MA5 时记为 1",
    ),
    MetricDefinition(
        "history_trading_days_count",
        "历史交易日数",
        "趋势起涨",
        description="当前股票在本轮历史窗口内截至判断日的交易日序号，用于区分成熟股与新股/次新股形态",
    ),
    MetricDefinition("amplitude", "振幅", "K线图", unit="%"),
    MetricDefinition("limit_up_price", "涨幅限价", "K线图", unit="元"),
    MetricDefinition("limit_down_price", "跌幅限价", "K线图", unit="元"),
    MetricDefinition("price_speed", "涨速", "K线图", unit="%"),
    MetricDefinition("entrust_ratio", "委比", "K线图", unit="%"),
    MetricDefinition("ma5", "MA5", "K线图", unit="元"),
    MetricDefinition("ma10", "MA10", "K线图", unit="元"),
    MetricDefinition("ma20", "MA20", "K线图", unit="元"),
    MetricDefinition("ma30", "MA30", "K线图", unit="元"),
    MetricDefinition("ma60", "MA60", "K线图", unit="元"),
    MetricDefinition("volume_ratio", "量比", "K线图", unit="倍"),
    MetricDefinition("total_shares", "总股本", "K线图", unit="股"),
    MetricDefinition("float_shares", "流通股本", "K线图", unit="股"),
    MetricDefinition("volume", "成交量", "成交量图", unit="手"),
    MetricDefinition("after_hours_volume", "盘后成交量", "成交量图", unit="手"),
    MetricDefinition("amount", "成交额", "成交量图", unit="元"),
    MetricDefinition("after_hours_amount", "盘后成交额", "成交量图", unit="元"),
    MetricDefinition("volume_ma5", "MAVOL5", "成交量图", unit="手"),
    MetricDefinition("volume_ma10", "MAVOL10", "成交量图", unit="手"),
    MetricDefinition("volume_ma20", "MAVOL20", "成交量图", unit="手"),
    MetricDefinition("amount_ma5", "MAAMT5", "成交量图", unit="元"),
    MetricDefinition("amount_ma10", "MAAMT10", "成交量图", unit="元"),
    MetricDefinition("ema12", "EMA12", "MACD图"),
    MetricDefinition("ema26", "EMA26", "MACD图"),
    MetricDefinition("macd_dif", "DIF", "MACD图"),
    MetricDefinition("macd_dea", "DEA", "MACD图"),
    MetricDefinition("macd", "MACD", "MACD图"),
    MetricDefinition("rsi6", "RSI6", "RSI图"),
    MetricDefinition("rsi12", "RSI12", "RSI图"),
    MetricDefinition("rsi24", "RSI24", "RSI图"),
    MetricDefinition("profit_ratio", "收盘获利", "筹码峰-全部筹码", unit="%", description="筹码获利/解套比例"),
    MetricDefinition("trapped_ratio", "套牢盘", "筹码峰-全部筹码", unit="%", description="100% - 收盘获利"),
    MetricDefinition("profit_trapped_spread", "获利套牢差", "筹码峰-全部筹码", unit="百分点", description="收盘获利 - 套牢盘"),
    MetricDefinition("avg_cost", "平均成本", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_to_avg_cost_pct", "现价偏离平均成本", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("cost_90_low", "90%筹码价格区间下限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("cost_90_high", "90%筹码价格区间上限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_90_mid", "90%筹码价格区间中枢", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_90_width", "90%筹码价格区间宽度", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_90_width_pct", "90%筹码价格区间宽度率", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_concentration_90", "90%筹码集中度", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_concentration_90_avg_30d", "近30日平均90%筹码集中度", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_concentration_90_avg_60d", "近60日平均90%筹码集中度", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("cost_70_low", "70%筹码价格区间下限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("cost_70_high", "70%筹码价格区间上限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_mid", "70%筹码价格区间中枢", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_width", "70%筹码价格区间宽度", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_width_pct", "70%筹码价格区间宽度率", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_concentration_70", "70%筹码集中度", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_peak_price", "筹码峰峰值价格", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("chip_peak_percent", "筹码峰峰值占比", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_peak_distance_pct", "现价偏离筹码峰", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_peak_count", "筹码峰数量", "筹码峰-全部筹码"),
    MetricDefinition("chip_single_peak_signal", "单峰集中信号", "筹码峰-全部筹码", description="逐价位筹码分布仅存在一个主要峰时记为 1，否则记为 0"),
    MetricDefinition("chip_peak_low_price", "筹码峰最低价", "筹码峰-全部筹码", unit="元", description="主要筹码峰价格带的最低价"),
    MetricDefinition("chip_peak_high_price", "筹码峰最高价", "筹码峰-全部筹码", unit="元", description="主要筹码峰价格带的最高价"),
    MetricDefinition("chip_peak_price_ratio", "筹码峰高低价比", "筹码峰-全部筹码", unit="倍", description="主要筹码峰最高价 / 最低价"),
    MetricDefinition("main_profit_ratio", "主力收盘获利", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_trapped_ratio", "主力套牢盘", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_profit_trapped_spread", "主力获利套牢差", "筹码峰-主力筹码", unit="百分点"),
    MetricDefinition("main_avg_cost", "主力平均成本", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_to_avg_cost_pct", "现价偏离主力平均成本", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_cost_90_low", "主力90%筹码价格区间下限", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_cost_90_high", "主力90%筹码价格区间上限", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_90_mid", "主力90%筹码价格区间中枢", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_90_width", "主力90%筹码价格区间宽度", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_90_width_pct", "主力90%筹码价格区间宽度率", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_chip_concentration_90", "主力90%筹码集中度", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_cost_70_low", "主力70%筹码价格区间下限", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_cost_70_high", "主力70%筹码价格区间上限", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_70_mid", "主力70%筹码价格区间中枢", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_70_width", "主力70%筹码价格区间宽度", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_price_range_70_width_pct", "主力70%筹码价格区间宽度率", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_chip_concentration_70", "主力70%筹码集中度", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_chip_peak_price", "主力筹码峰峰值价格", "筹码峰-主力筹码", unit="元"),
    MetricDefinition("main_chip_peak_percent", "主力筹码峰峰值占比", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("main_chip_peak_distance_pct", "现价偏离主力筹码峰", "筹码峰-主力筹码", unit="%"),
    MetricDefinition("turnover_rate", "换手率", "实时监控", unit="%"),
    MetricDefinition("main_net_volume_pct", "主力净量", "实时监控", unit="%", description="主力净流入相对流通市值占比"),
    MetricDefinition("main_force_net", "主力净流入", "实时监控", unit="元", description="基于价量关系估算的主力净流入"),
    MetricDefinition("net_super_large_order", "净特大单", "实时监控", unit="元", description="基于主力净额拆分的估算值"),
    MetricDefinition("net_large_order", "净大单", "实时监控", unit="元", description="基于主力净额拆分的估算值"),
    MetricDefinition("net_medium_order", "净中单", "实时监控", unit="元", description="基于主力净额拆分的估算值"),
    MetricDefinition("net_small_order", "净小单", "实时监控", unit="元", description="基于主力净额拆分的估算值"),
    MetricDefinition(
        "deducted_net_profit_yoy_pct",
        "扣非净利同比",
        "财务事件",
        unit="%",
        description="扣除非经常性损益后的净利润同比增速；用于财报/业绩公告事件映射",
    ),
    MetricDefinition(
        "deducted_net_profit_qoq_pct",
        "扣非净利环比",
        "财务事件",
        unit="%",
        description="扣除非经常性损益后的单季净利润环比增速；优先使用数据源字段，缺失时由连续报告期扣非净利推导",
    ),
    MetricDefinition(
        "announcement_next_day_gap_pct",
        "公告次日跳空缺口",
        "财务事件",
        unit="%",
        description="公告后第一个交易日开盘价相对上一交易日收盘价的跳空幅度",
    ),
    MetricDefinition(
        "announcement_next_day_volume_ratio",
        "公告次日量能/5日均量",
        "财务事件",
        unit="倍",
        description="公告后第一个交易日成交量相对前 5 个交易日平均成交量的倍数",
    ),
    MetricDefinition(
        "announcement_next_day_gap_unfilled",
        "公告次日缺口未回补",
        "财务事件",
        description="公告后第一个交易日最低价高于上一交易日收盘价时记为 1，否则记为 0",
    ),
    MetricDefinition(
        "net_profit_gap_signal",
        "净利润断层信号",
        "财务事件",
        description="扣非净利同比 >=100%、环比 >=50%、公告次日跳空 >=3%、量能 >=1.5 倍且当日不回补缺口时记为 1",
    ),
]

METRIC_BY_KEY: Dict[str, MetricDefinition] = {item.key: item for item in METRIC_DEFINITIONS}


def get_metric_registry() -> List[Dict[str, Any]]:
    """Return metric definitions as serializable dictionaries."""
    return [asdict(item) for item in METRIC_DEFINITIONS]


def metric_label(metric_key: str) -> str:
    definition = METRIC_BY_KEY.get(metric_key)
    return definition.label if definition else metric_key


def _to_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if pd.isna(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0).rolling(window=period, min_periods=period).sum()
    losses = (-delta.clip(upper=0)).rolling(window=period, min_periods=period).sum()
    rs = gains / losses.replace(0, pd.NA)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.mask((gains == 0) & (losses == 0), 50)
    rsi = rsi.mask((losses == 0) & (gains > 0), 100)
    return rsi


def _rolling_positive_percentile_rank(
    series: pd.Series,
    *,
    window: int,
    min_periods: int,
) -> pd.Series:
    values = pd.to_numeric(series, errors="coerce").to_numpy(dtype=float, copy=False)
    result = np.full(len(values), np.nan)
    for index, current in enumerate(values):
        if not np.isfinite(current) or current <= 0:
            continue
        start = max(0, index - window + 1)
        window_values = values[start:index + 1]
        valid = window_values[np.isfinite(window_values) & (window_values > 0)]
        if len(valid) < min_periods:
            continue
        result[index] = float(np.sum(valid <= current) / len(valid) * 100)
    return pd.Series(result, index=series.index)


def _normalize_ratio_percent(value: Any) -> Optional[float]:
    number = _to_float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return number * 100
    return number


def _clip_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return max(0.0, min(100.0, value))


def _normalize_date_key(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        parsed = pd.to_datetime(value, errors="coerce")
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed.strftime("%Y-%m-%d")


def _range_values(low: Optional[float], high: Optional[float], prefix: str) -> Dict[str, Optional[float]]:
    mid = (low + high) / 2 if low is not None and high is not None else None
    width = high - low if low is not None and high is not None else None
    width_pct = width / mid * 100 if width is not None and mid and mid > 0 else None
    return {
        f"{prefix}mid": mid,
        f"{prefix}width": width,
        f"{prefix}width_pct": width_pct,
    }


def _chip_peak_values(chip: Dict[str, Any], current_price: Optional[float]) -> Dict[str, Optional[float]]:
    distribution = chip.get("distribution") if isinstance(chip.get("distribution"), list) else []
    peak_price: Optional[float] = None
    peak_percent: Optional[float] = None
    for point in distribution:
        if not isinstance(point, dict):
            continue
        price = _to_float(point.get("price"))
        percent = _normalize_ratio_percent(point.get("percent"))
        if price is None or percent is None:
            continue
        if peak_percent is None or percent > peak_percent:
            peak_price = price
            peak_percent = percent
    peak_distance_pct = (
        (current_price - peak_price) / peak_price * 100
        if current_price is not None and peak_price is not None and peak_price > 0
        else None
    )
    return {
        "chip_peak_price": peak_price,
        "chip_peak_percent": peak_percent,
        "chip_peak_distance_pct": peak_distance_pct,
    }


def calculate_chip_shape_metrics(chip: Dict[str, Any]) -> Dict[str, Optional[float]]:
    """Return simple shape metrics for a chip distribution."""
    stored_peak_count = _to_float(chip.get("chip_peak_count", chip.get("peak_count")))
    stored_single_peak = _to_float(chip.get("chip_single_peak_signal", chip.get("single_peak_signal")))
    stored_peak_low = _to_float(chip.get("chip_peak_low_price", chip.get("peak_low_price")))
    stored_peak_high = _to_float(chip.get("chip_peak_high_price", chip.get("peak_high_price")))
    stored_peak_ratio = _to_float(chip.get("chip_peak_price_ratio", chip.get("peak_price_ratio")))

    distribution = chip.get("distribution") if isinstance(chip.get("distribution"), list) else []
    points: List[tuple[float, float]] = []
    for point in distribution:
        if not isinstance(point, dict):
            continue
        price = _to_float(point.get("price"))
        percent = _normalize_ratio_percent(point.get("percent", point.get("ratio")))
        if price is None or percent is None or percent <= 0:
            continue
        points.append((price, percent))

    if not points:
        return {
            "chip_peak_count": stored_peak_count,
            "chip_single_peak_signal": stored_single_peak,
            "chip_peak_low_price": stored_peak_low,
            "chip_peak_high_price": stored_peak_high,
            "chip_peak_price_ratio": stored_peak_ratio,
        }

    points.sort(key=lambda item: item[0])
    values = [percent for _price, percent in points]
    max_percent = max(values)
    if max_percent <= 0:
        return {
            "chip_peak_count": 0.0,
            "chip_single_peak_signal": 0.0,
            "chip_peak_low_price": stored_peak_low,
            "chip_peak_high_price": stored_peak_high,
            "chip_peak_price_ratio": stored_peak_ratio,
        }

    material_peak_floor = max(1.0, max_percent * 0.35)
    peak_count = 0
    peak_indices: List[int] = []
    for index, value in enumerate(values):
        previous_value = values[index - 1] if index > 0 else float("-inf")
        next_value = values[index + 1] if index < len(values) - 1 else float("-inf")
        is_local_peak = value >= previous_value and value >= next_value and (
            value > previous_value or value > next_value
        )
        if is_local_peak and value >= material_peak_floor:
            peak_count += 1
            peak_indices.append(index)

    if peak_count == 0 and max_percent >= material_peak_floor:
        peak_count = 1
        peak_indices = [values.index(max_percent)]

    material_prices = [
        price for price, percent in points
        if percent >= material_peak_floor
    ]
    if not material_prices and peak_indices:
        material_prices = [points[index][0] for index in peak_indices]
    peak_low = min(material_prices) if material_prices else stored_peak_low
    peak_high = max(material_prices) if material_prices else stored_peak_high
    peak_ratio = (
        peak_high / peak_low
        if peak_low is not None and peak_high is not None and peak_low > 0 and peak_high > 0
        else stored_peak_ratio
    )

    return {
        "chip_peak_count": float(peak_count),
        "chip_single_peak_signal": 1.0 if peak_count == 1 else 0.0,
        "chip_peak_low_price": peak_low,
        "chip_peak_high_price": peak_high,
        "chip_peak_price_ratio": peak_ratio,
    }


def _chip_metric_values(chip: Dict[str, Any], current_price: Optional[float] = None, prefix: str = "") -> Dict[str, Optional[float]]:
    profit_ratio = _clip_percent(_normalize_ratio_percent(chip.get("profit_ratio")))
    trapped_ratio = 100 - profit_ratio if profit_ratio is not None else None
    avg_cost = _to_float(chip.get("avg_cost"))
    cost_90_low = _to_float(chip.get("cost_90_low"))
    cost_90_high = _to_float(chip.get("cost_90_high"))
    cost_70_low = _to_float(chip.get("cost_70_low"))
    cost_70_high = _to_float(chip.get("cost_70_high"))
    values = {
        "profit_ratio": profit_ratio,
        "trapped_ratio": trapped_ratio,
        "profit_trapped_spread": profit_ratio - trapped_ratio if profit_ratio is not None and trapped_ratio is not None else None,
        "avg_cost": avg_cost,
        "price_to_avg_cost_pct": (current_price - avg_cost) / avg_cost * 100 if current_price is not None and avg_cost and avg_cost > 0 else None,
        "cost_90_low": cost_90_low,
        "cost_90_high": cost_90_high,
        **_range_values(cost_90_low, cost_90_high, "price_range_90_"),
        "chip_concentration_90": _normalize_ratio_percent(chip.get("concentration_90")),
        "cost_70_low": cost_70_low,
        "cost_70_high": cost_70_high,
        **_range_values(cost_70_low, cost_70_high, "price_range_70_"),
        "chip_concentration_70": _normalize_ratio_percent(chip.get("concentration_70")),
        **_chip_peak_values(chip, current_price),
        **calculate_chip_shape_metrics(chip),
    }
    if not prefix:
        return values
    return {f"{prefix}{metric_key}": value for metric_key, value in values.items()}


def _apply_chip_metrics(df: pd.DataFrame, index: int, chip: Dict[str, Any], prefix: str = "") -> None:
    current_price = _to_float(df.at[index, "close"]) if "close" in df.columns else None
    for metric_key, value in _chip_metric_values(chip, current_price, prefix).items():
        if value is None:
            continue
        if metric_key not in df.columns:
            df[metric_key] = pd.NA
        df.at[index, metric_key] = value


def _apply_chip_distribution(df: pd.DataFrame, chip: Dict[str, Any], prefix: str = "") -> None:
    if len(df) == 0:
        return

    date_to_index: Dict[str, int] = {}
    if "date" in df.columns:
        for index, value in df["date"].items():
            date_key = _normalize_date_key(value)
            if date_key:
                date_to_index[date_key] = int(index)

    snapshots = chip.get("snapshots") if isinstance(chip.get("snapshots"), list) else []
    for snapshot in snapshots:
        if not isinstance(snapshot, dict):
            continue
        date_key = _normalize_date_key(snapshot.get("date"))
        if not date_key or date_key not in date_to_index:
            continue
        _apply_chip_metrics(df, date_to_index[date_key], snapshot, prefix)

    top_level_index = date_to_index.get(_normalize_date_key(chip.get("date"))) if date_to_index else None
    if top_level_index is None:
        top_level_index = int(df.index[-1])
    _apply_chip_metrics(df, top_level_index, chip, prefix)


def build_metric_frame(
    history: Iterable[Dict[str, Any]],
    quote: Optional[Dict[str, Any]] = None,
    extra_metrics: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Build a metric DataFrame sorted by date ascending."""
    df = pd.DataFrame(list(history))
    if df.empty:
        return df

    numeric_columns = (
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
        "change",
        "change_percent",
        "pct_chg",
        "turnover_rate",
        "volume_ratio",
        "amplitude",
        "prev_close",
        "pre_close",
        "after_hours_volume",
        "after_hours_amount",
        "total_mv",
        "circ_mv",
        "pe_ratio",
        "total_shares",
        "float_shares",
        "limit_up_price",
        "limit_down_price",
        "price_speed",
        "entrust_ratio",
        "deducted_net_profit_yoy_pct",
        "deducted_net_profit_qoq_pct",
        "announcement_next_day_gap_pct",
        "announcement_next_day_volume_ratio",
        "announcement_next_day_gap_unfilled",
        "net_profit_gap_signal",
    )
    for col in numeric_columns:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "pre_close" in df.columns and "prev_close" not in df.columns:
        df["prev_close"] = df["pre_close"]

    if "pct_chg" not in df.columns:
        df["pct_chg"] = df.get("change_percent")
    if "change_percent" not in df.columns:
        df["change_percent"] = df.get("pct_chg")

    if "date" in df.columns:
        df = df.sort_values("date", ascending=True).reset_index(drop=True)

    df["history_trading_days_count"] = pd.Series(range(1, len(df) + 1), index=df.index, dtype="float64")

    if "pe_ratio" in df.columns:
        df["pe_ratio_percentile_250d"] = _rolling_positive_percentile_rank(
            df["pe_ratio"],
            window=250,
            min_periods=80,
        )
    else:
        df["pe_ratio_percentile_250d"] = pd.NA

    close = pd.to_numeric(df["close"], errors="coerce")
    volume = df["volume"] if "volume" in df.columns else pd.Series(dtype="float64")
    amount = df["amount"] if "amount" in df.columns else pd.Series(dtype="float64")
    previous_close = close.shift(1)

    if "prev_close" not in df.columns:
        df["prev_close"] = previous_close
    else:
        df["prev_close"] = pd.to_numeric(df["prev_close"], errors="coerce").fillna(previous_close)

    if "change" not in df.columns:
        df["change"] = close - df["prev_close"]
    else:
        df["change"] = pd.to_numeric(df["change"], errors="coerce").fillna(close - df["prev_close"])

    computed_pct_chg = (df["change"] / df["prev_close"].replace(0, pd.NA)) * 100
    df["pct_chg"] = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(computed_pct_chg)
    df["change_percent"] = pd.to_numeric(df["change_percent"], errors="coerce").fillna(df["pct_chg"])
    daily_return_factor = 1 + (pd.to_numeric(df["pct_chg"], errors="coerce") / 100)
    for window in (5, 20):
        df[f"prev_{window}d_return_pct"] = (
            daily_return_factor
            .rolling(window=window, min_periods=window)
            .apply(lambda values: values.prod(), raw=True)
            .shift(1)
            .sub(1)
            .mul(100)
        )

    if "amplitude" not in df.columns:
        df["amplitude"] = pd.NA
    amplitude_base = df["prev_close"].fillna(df.get("open")).replace(0, pd.NA)
    computed_amplitude = ((df["high"] - df["low"]) / amplitude_base) * 100
    df["amplitude"] = pd.to_numeric(df["amplitude"], errors="coerce").fillna(computed_amplitude)

    high = pd.to_numeric(df["high"], errors="coerce")
    low = pd.to_numeric(df["low"], errors="coerce")
    for window in (30, 60):
        rolling_high = high.rolling(window=window, min_periods=window).max()
        rolling_low = low.rolling(window=window, min_periods=window).min()
        df[f"price_range_{window}d_pct"] = (
            (rolling_high - rolling_low) / rolling_low.replace(0, pd.NA) * 100
        )

    for window in (5, 10, 20, 30, 60):
        df[f"ma{window}"] = close.rolling(window=window, min_periods=window).mean()

    df["bias_ma5_pct"] = (close - df["ma5"]) / df["ma5"].replace(0, pd.NA) * 100
    ma_columns = ["ma5", "ma10", "ma20", "ma30"]
    ma_ready = df[ma_columns].notna().all(axis=1) & close.notna()
    ma_bullish = (
        (close > df["ma5"])
        & (df["ma5"] > df["ma10"])
        & (df["ma10"] > df["ma20"])
        & (df["ma20"] > df["ma30"])
    )
    df["ma_bullish_alignment_signal"] = ma_bullish.astype(float)
    df.loc[~ma_ready, "ma_bullish_alignment_signal"] = pd.NA

    ma_uptrend_ready = (
        df["ma5"].notna()
        & df["ma10"].notna()
        & df["ma20"].notna()
        & df["ma5"].shift(3).notna()
        & df["ma10"].shift(3).notna()
        & df["ma20"].shift(5).notna()
    )
    ma_uptrend = (
        (df["ma5"] > df["ma5"].shift(3))
        & (df["ma10"] > df["ma10"].shift(3))
        & (df["ma20"] > df["ma20"].shift(5))
    )
    df["ma_uptrend_signal"] = ma_uptrend.astype(float)
    df.loc[~ma_uptrend_ready, "ma_uptrend_signal"] = pd.NA

    for window in (20, 60):
        prior_high = high.rolling(window=window, min_periods=window).max().shift(1)
        signal_key = f"price_breakout_{window}d_signal"
        breakout = close > prior_high
        df[signal_key] = breakout.astype(float)
        df.loc[prior_high.isna() | close.isna(), signal_key] = pd.NA
    df["prior_10d_breakout_20d_count"] = (
        pd.to_numeric(df["price_breakout_20d_signal"], errors="coerce")
        .fillna(0)
        .shift(1)
        .rolling(window=10, min_periods=1)
        .sum()
        .fillna(0)
    )

    for window in (5, 10, 20):
        if "volume" in df.columns:
            df[f"volume_ma{window}"] = volume.rolling(window=window, min_periods=window).mean()
    if "volume" in df.columns and "volume_ma5" in df.columns:
        if "volume_ratio" not in df.columns:
            df["volume_ratio"] = pd.NA
        computed_volume_ratio = volume / df["volume_ma5"].replace(0, pd.NA)
        df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(computed_volume_ratio)
    if "volume" in df.columns and "volume_ma20" in df.columns:
        df["volume_expansion_20d_ratio"] = volume / df["volume_ma20"].replace(0, pd.NA)
        volume_expansion = pd.to_numeric(df["volume_expansion_20d_ratio"], errors="coerce") >= 1.2
        df["volume_expansion_signal"] = volume_expansion.astype(float)
        df.loc[df["volume_expansion_20d_ratio"].isna(), "volume_expansion_signal"] = pd.NA
    else:
        df["volume_expansion_20d_ratio"] = pd.NA
        df["volume_expansion_signal"] = pd.NA

    for window in (5, 10):
        if "amount" in df.columns:
            df[f"amount_ma{window}"] = amount.rolling(window=window, min_periods=window).mean()

    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema26"] = close.ewm(span=26, adjust=False).mean()
    df["macd_dif"] = df["ema12"] - df["ema26"]
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd"] = (df["macd_dif"] - df["macd_dea"]) * 2
    df["rsi6"] = _rsi(close, 6)
    df["rsi12"] = _rsi(close, 12)
    df["rsi24"] = _rsi(close, 24)

    ma_bullish_signal = pd.to_numeric(df["ma_bullish_alignment_signal"], errors="coerce")
    ma_uptrend_signal = pd.to_numeric(df["ma_uptrend_signal"], errors="coerce")
    breakout_20d_signal = pd.to_numeric(df["price_breakout_20d_signal"], errors="coerce")
    breakout_60d_signal = pd.to_numeric(df["price_breakout_60d_signal"], errors="coerce")
    volume_expansion_signal = pd.to_numeric(df["volume_expansion_signal"], errors="coerce")
    bias_ma5 = pd.to_numeric(df["bias_ma5_pct"], errors="coerce")
    macd_ready = df["macd_dif"].notna() & df["macd_dea"].notna() & df["macd"].notna()
    macd_bullish = (df["macd_dif"] > df["macd_dea"]) & (df["macd_dif"] > 0) & (df["macd"] > 0)
    breakout_available = breakout_20d_signal.notna() | breakout_60d_signal.notna()
    breakout_confirmed = breakout_20d_signal.eq(1) | breakout_60d_signal.eq(1)
    trend_start = (
        ma_bullish_signal.eq(1)
        & ma_uptrend_signal.eq(1)
        & breakout_confirmed
        & volume_expansion_signal.eq(1)
        & macd_bullish
        & bias_ma5.between(0, 15)
    )
    trend_start_ready = (
        ma_bullish_signal.notna()
        & ma_uptrend_signal.notna()
        & breakout_available
        & volume_expansion_signal.notna()
        & bias_ma5.notna()
        & macd_ready
    )
    df["trend_start_signal"] = trend_start.astype(float)
    df.loc[~trend_start_ready, "trend_start_signal"] = pd.NA

    volume_ratio = pd.to_numeric(
        df["volume_ratio"] if "volume_ratio" in df.columns else pd.Series(pd.NA, index=df.index),
        errors="coerce",
    )
    volume_expansion_20d_ratio = pd.to_numeric(df["volume_expansion_20d_ratio"], errors="coerce")
    volume_pulse_ratio = pd.concat(
        [volume_ratio, volume_expansion_20d_ratio],
        axis=1,
    ).max(axis=1, skipna=True)
    prior_20d_high = high.rolling(window=20, min_periods=20).max().shift(1)
    near_20d_high = close >= prior_20d_high * 0.95
    price_breakout_20d = breakout_20d_signal.eq(1)
    ma5_above_ma10 = df["ma5"] > df["ma10"]
    ma10_above_ma20 = df["ma10"] > df["ma20"]
    close_above_ma20 = close > df["ma20"]
    ma5_cross_up = ma5_above_ma10 & (df["ma5"].shift(1) <= df["ma10"].shift(1))
    macd_cross_up = (
        ((df["macd_dif"] > df["macd_dea"]) & (df["macd_dif"].shift(1) <= df["macd_dea"].shift(1)))
        | ((df["macd"] > 0) & (df["macd"].shift(1) <= 0))
    )
    macd_turning_up = (
        (df["macd_dif"] > df["macd_dif"].shift(1))
        & (df["macd"] > df["macd"].shift(1))
    )
    rsi6 = pd.to_numeric(df["rsi6"], errors="coerce")
    prev_5d_return = pd.to_numeric(df["prev_5d_return_pct"], errors="coerce")
    prev_20d_return = pd.to_numeric(df["prev_20d_return_pct"], errors="coerce")
    pct_chg = pd.to_numeric(df["pct_chg"], errors="coerce")

    live_ready = (
        close.notna()
        & df["ma5"].notna()
        & df["ma10"].notna()
        & df["ma20"].notna()
        & prior_20d_high.notna()
        & volume_pulse_ratio.notna()
        & rsi6.notna()
        & macd_ready
    )
    setup_score = pd.Series(0.0, index=df.index)
    setup_score += macd_cross_up.fillna(False).astype(float) * 2
    setup_score += (macd_turning_up & ~macd_cross_up.fillna(False)).fillna(False).astype(float)
    setup_score += ma5_cross_up.fillna(False).astype(float) * 2
    setup_score += (ma5_above_ma10 & ~ma5_cross_up.fillna(False)).fillna(False).astype(float)
    setup_score += close_above_ma20.fillna(False).astype(float)
    setup_score += ma10_above_ma20.fillna(False).astype(float)
    setup_score += volume_pulse_ratio.ge(1.2).fillna(False).astype(float) * 2
    setup_score += (volume_pulse_ratio.ge(1.0) & volume_pulse_ratio.lt(1.2)).fillna(False).astype(float)
    setup_score += rsi6.between(55, 88).fillna(False).astype(float) * 2
    setup_score += rsi6.between(50, 55, inclusive="left").fillna(False).astype(float)
    setup_score += price_breakout_20d.fillna(False).astype(float) * 2
    setup_score += (near_20d_high & ~price_breakout_20d.fillna(False)).fillna(False).astype(float)
    setup_score += ma_uptrend_signal.eq(1).fillna(False).astype(float)

    overheat_score = pd.Series(0.0, index=df.index)
    overheat_score += prev_5d_return.ge(35).fillna(False).astype(float) * 2
    overheat_score += prev_20d_return.ge(80).fillna(False).astype(float)
    overheat_score += bias_ma5.gt(15).fillna(False).astype(float) * 2
    overheat_score += rsi6.gt(88).fillna(False).astype(float)
    overheat_score += pct_chg.ge(9).fillna(False).astype(float)
    overheat_score += (
        volume_pulse_ratio.ge(3.0)
        & pct_chg.ge(7)
    ).fillna(False).astype(float)

    failure = (
        (close < df["ma20"])
        | ((close < df["ma10"]) & (df["macd_dif"] < df["macd_dea"]))
        | (pct_chg.le(-7) & (close < df["ma5"]))
    )
    watch_signal = (
        setup_score.ge(7)
        & overheat_score.le(2)
        & ~failure.fillna(False)
    )
    confirm_signal = (
        setup_score.ge(9)
        & (price_breakout_20d | trend_start.eq(True))
        & volume_pulse_ratio.ge(1.2)
        & (macd_bullish | macd_cross_up)
        & overheat_score.le(2)
        & ~failure.fillna(False)
    )

    df["trend_live_setup_score"] = setup_score
    df["trend_live_watch_signal"] = watch_signal.astype(float)
    df["trend_live_confirm_signal"] = confirm_signal.astype(float)
    df["trend_overheat_risk_score"] = overheat_score
    df["trend_overheat_risk_signal"] = overheat_score.ge(3).astype(float)
    df["trend_failure_signal"] = failure.astype(float)
    for column in (
        "trend_live_setup_score",
        "trend_live_watch_signal",
        "trend_live_confirm_signal",
        "trend_overheat_risk_score",
        "trend_overheat_risk_signal",
        "trend_failure_signal",
    ):
        df.loc[~live_ready, column] = pd.NA

    if quote and len(df) > 0:
        latest_index = df.index[-1]
        quote_mapping = {
            "current_price": "current_price",
            "change": "change",
            "change_percent": "change_percent",
            "prev_close": "prev_close",
            "volume": "volume",
            "amount": "amount",
            "after_hours_volume": "after_hours_volume",
            "after_hours_amount": "after_hours_amount",
            "turnover_rate": "turnover_rate",
            "volume_ratio": "volume_ratio",
            "amplitude": "amplitude",
            "total_mv": "total_mv",
            "circ_mv": "circ_mv",
            "pe_ratio": "pe_ratio",
            "total_shares": "total_shares",
            "float_shares": "float_shares",
            "limit_up_price": "limit_up_price",
            "limit_down_price": "limit_down_price",
            "price_speed": "price_speed",
            "entrust_ratio": "entrust_ratio",
        }
        for metric_key, quote_key in quote_mapping.items():
            value = _to_float(quote.get(quote_key))
            if value is not None:
                if metric_key not in df.columns:
                    df[metric_key] = pd.NA
                df.at[latest_index, metric_key] = value

    amount_for_flow = df["amount"] if "amount" in df.columns else close * volume
    flow_volume_ratio = pd.to_numeric(
        df["volume_ratio"] if "volume_ratio" in df.columns else pd.Series(1, index=df.index),
        errors="coerce",
    ).fillna(1)
    flow_change_pct = pd.to_numeric(df["pct_chg"], errors="coerce").fillna(0)
    flow_return5 = (close.pct_change(5) * 100).fillna(0)
    flow_ratio = (
        (flow_change_pct / 100) * 0.9
        + (flow_return5 / 100) * 0.32
        + (flow_volume_ratio - 1) * 0.055
    ).clip(lower=-0.26, upper=0.26)
    df["main_force_net"] = amount_for_flow * flow_ratio
    if "circ_mv" in df.columns:
        circ_mv = pd.to_numeric(df["circ_mv"], errors="coerce").replace(0, pd.NA)
        df["main_net_volume_pct"] = (df["main_force_net"] / circ_mv) * 100
    else:
        df["main_net_volume_pct"] = pd.NA
    df["net_super_large_order"] = df["main_force_net"] * 0.44
    df["net_large_order"] = df["main_force_net"] * 0.30
    df["net_medium_order"] = df["main_force_net"] * 0.18
    df["net_small_order"] = df["main_force_net"] * -0.08

    if extra_metrics and len(df) > 0:
        chip = extra_metrics.get("chip_distribution") if isinstance(extra_metrics.get("chip_distribution"), dict) else {}
        _apply_chip_distribution(df, chip)
        main_chip = (
            extra_metrics.get("main_chip_distribution")
            if isinstance(extra_metrics.get("main_chip_distribution"), dict)
            else extra_metrics.get("main_chip")
            if isinstance(extra_metrics.get("main_chip"), dict)
            else {}
        )
        _apply_chip_distribution(df, main_chip, "main_")

    if "chip_concentration_90" in df.columns:
        chip_concentration = pd.to_numeric(df["chip_concentration_90"], errors="coerce")
        for window in (30, 60):
            df[f"chip_concentration_90_avg_{window}d"] = (
                chip_concentration.rolling(window=window, min_periods=window).mean()
            )
    else:
        df["chip_concentration_90_avg_30d"] = pd.NA
        df["chip_concentration_90_avg_60d"] = pd.NA

    return df
