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
    MetricDefinition("current_price", "最新价", "核心行情", unit="元"),
    MetricDefinition("change", "涨跌额", "核心行情", unit="元"),
    MetricDefinition("change_percent", "实时涨跌幅", "核心行情", unit="%"),
    MetricDefinition("total_mv", "总市值", "核心行情", unit="元"),
    MetricDefinition("circ_mv", "流通市值", "核心行情", unit="元"),
    MetricDefinition("pe_ratio", "市盈TTM", "核心行情"),
    MetricDefinition("open", "开盘价", "K线图", unit="元"),
    MetricDefinition("high", "最高价", "K线图", unit="元"),
    MetricDefinition("low", "最低价", "K线图", unit="元"),
    MetricDefinition("close", "收盘价", "K线图", unit="元"),
    MetricDefinition("prev_close", "昨收价", "K线图", unit="元"),
    MetricDefinition("pct_chg", "涨跌幅", "K线图", unit="%"),
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
    MetricDefinition("volume", "成交量", "成交量图", unit="股"),
    MetricDefinition("after_hours_volume", "盘后成交量", "成交量图", unit="股"),
    MetricDefinition("amount", "成交额", "成交量图", unit="元"),
    MetricDefinition("after_hours_amount", "盘后成交额", "成交量图", unit="元"),
    MetricDefinition("volume_ma5", "MAVOL5", "成交量图", unit="股"),
    MetricDefinition("volume_ma10", "MAVOL10", "成交量图", unit="股"),
    MetricDefinition("volume_ma20", "MAVOL20", "成交量图", unit="股"),
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
    MetricDefinition("cost_70_low", "70%筹码价格区间下限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("cost_70_high", "70%筹码价格区间上限", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_mid", "70%筹码价格区间中枢", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_width", "70%筹码价格区间宽度", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("price_range_70_width_pct", "70%筹码价格区间宽度率", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_concentration_70", "70%筹码集中度", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_peak_price", "筹码峰峰值价格", "筹码峰-全部筹码", unit="元"),
    MetricDefinition("chip_peak_percent", "筹码峰峰值占比", "筹码峰-全部筹码", unit="%"),
    MetricDefinition("chip_peak_distance_pct", "现价偏离筹码峰", "筹码峰-全部筹码", unit="%"),
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

    for window in (5, 10, 20, 30, 60):
        df[f"ma{window}"] = close.rolling(window=window, min_periods=window).mean()

    for window in (5, 10, 20):
        if "volume" in df.columns:
            df[f"volume_ma{window}"] = volume.rolling(window=window, min_periods=window).mean()
    if "volume" in df.columns and "volume_ma5" in df.columns:
        if "volume_ratio" not in df.columns:
            df["volume_ratio"] = pd.NA
        computed_volume_ratio = volume / df["volume_ma5"].replace(0, pd.NA)
        df["volume_ratio"] = pd.to_numeric(df["volume_ratio"], errors="coerce").fillna(computed_volume_ratio)

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

    return df
