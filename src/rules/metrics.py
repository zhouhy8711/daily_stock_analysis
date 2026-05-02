# -*- coding: utf-8 -*-
"""Metric registry and indicator calculation for stock rules."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, Iterable, List, Optional

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
    MetricDefinition("open", "开盘价", "基础行情", unit="元"),
    MetricDefinition("high", "最高价", "基础行情", unit="元"),
    MetricDefinition("low", "最低价", "基础行情", unit="元"),
    MetricDefinition("close", "收盘价", "基础行情", unit="元"),
    MetricDefinition("volume", "成交量", "基础行情", unit="股"),
    MetricDefinition("amount", "成交额", "基础行情", unit="元"),
    MetricDefinition("pct_chg", "涨跌幅", "基础行情", unit="%"),
    MetricDefinition("current_price", "最新价", "实时行情", unit="元"),
    MetricDefinition("change_percent", "实时涨跌幅", "实时行情", unit="%"),
    MetricDefinition("turnover_rate", "换手率", "实时行情", unit="%"),
    MetricDefinition("volume_ratio", "量比", "实时行情", unit="倍"),
    MetricDefinition("amplitude", "振幅", "实时行情", unit="%"),
    MetricDefinition("ma5", "MA5", "均线", unit="元"),
    MetricDefinition("ma10", "MA10", "均线", unit="元"),
    MetricDefinition("ma20", "MA20", "均线", unit="元"),
    MetricDefinition("ma30", "MA30", "均线", unit="元"),
    MetricDefinition("ma60", "MA60", "均线", unit="元"),
    MetricDefinition("volume_ma5", "成交量 MA5", "成交量", unit="股"),
    MetricDefinition("volume_ma10", "成交量 MA10", "成交量", unit="股"),
    MetricDefinition("volume_ma20", "成交量 MA20", "成交量", unit="股"),
    MetricDefinition("ema12", "EMA12", "技术指标"),
    MetricDefinition("ema26", "EMA26", "技术指标"),
    MetricDefinition("macd_dif", "MACD DIF", "技术指标"),
    MetricDefinition("macd_dea", "MACD DEA", "技术指标"),
    MetricDefinition("macd", "MACD", "技术指标"),
    MetricDefinition("rsi6", "RSI6", "技术指标"),
    MetricDefinition("rsi12", "RSI12", "技术指标"),
    MetricDefinition("profit_ratio", "解套率", "筹码", unit="%", description="筹码获利/解套比例"),
    MetricDefinition("chip_concentration_90", "筹码集中度", "筹码", unit="%", description="90% 筹码集中度"),
    MetricDefinition("avg_cost", "平均筹码成本", "筹码", unit="元"),
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


def _normalize_ratio_percent(value: Any) -> Optional[float]:
    number = _to_float(value)
    if number is None:
        return None
    if 0 <= number <= 1:
        return number * 100
    return number


def build_metric_frame(
    history: Iterable[Dict[str, Any]],
    quote: Optional[Dict[str, Any]] = None,
    extra_metrics: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Build a metric DataFrame sorted by date ascending."""
    df = pd.DataFrame(list(history))
    if df.empty:
        return df

    for col in ("open", "high", "low", "close", "volume", "amount", "change_percent", "pct_chg"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "pct_chg" not in df.columns:
        df["pct_chg"] = df.get("change_percent")
    if "change_percent" not in df.columns:
        df["change_percent"] = df.get("pct_chg")

    if "date" in df.columns:
        df = df.sort_values("date", ascending=True).reset_index(drop=True)

    close = df["close"]
    volume = df["volume"] if "volume" in df.columns else pd.Series(dtype="float64")

    for window in (5, 10, 20, 30, 60):
        df[f"ma{window}"] = close.rolling(window=window, min_periods=window).mean()

    for window in (5, 10, 20):
        if "volume" in df.columns:
            df[f"volume_ma{window}"] = volume.rolling(window=window, min_periods=window).mean()

    df["ema12"] = close.ewm(span=12, adjust=False).mean()
    df["ema26"] = close.ewm(span=26, adjust=False).mean()
    df["macd_dif"] = df["ema12"] - df["ema26"]
    df["macd_dea"] = df["macd_dif"].ewm(span=9, adjust=False).mean()
    df["macd"] = (df["macd_dif"] - df["macd_dea"]) * 2
    df["rsi6"] = _rsi(close, 6)
    df["rsi12"] = _rsi(close, 12)

    if quote and len(df) > 0:
        latest_index = df.index[-1]
        quote_mapping = {
            "current_price": "current_price",
            "change_percent": "change_percent",
            "turnover_rate": "turnover_rate",
            "volume_ratio": "volume_ratio",
            "amplitude": "amplitude",
        }
        for metric_key, quote_key in quote_mapping.items():
            value = _to_float(quote.get(quote_key))
            if value is not None:
                if metric_key not in df.columns:
                    df[metric_key] = pd.NA
                df.at[latest_index, metric_key] = value

    if extra_metrics and len(df) > 0:
        latest_index = df.index[-1]
        chip = extra_metrics.get("chip_distribution") if isinstance(extra_metrics.get("chip_distribution"), dict) else {}
        chip_mapping = {
            "profit_ratio": _normalize_ratio_percent(chip.get("profit_ratio")),
            "chip_concentration_90": _normalize_ratio_percent(chip.get("concentration_90")),
            "avg_cost": _to_float(chip.get("avg_cost")),
        }
        for metric_key, value in chip_mapping.items():
            if value is not None:
                if metric_key not in df.columns:
                    df[metric_key] = pd.NA
                df.at[latest_index, metric_key] = value

    return df
