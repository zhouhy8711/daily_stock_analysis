# -*- coding: utf-8 -*-
"""
Local chip distribution model built from existing A-share daily K-line data.

This is not an exchange/vendor raw chip feed.  It follows the common CYQ
modelling idea used by open-source projects: distribute each day's turnover
across that day's price range, decay previous chips by turnover, then derive
profit ratio and concentration metrics from the resulting cost distribution.
"""

from __future__ import annotations

import logging
import math
from typing import Any, Callable, Optional, Tuple

import numpy as np
import pandas as pd

from .base import (
    BaseFetcher,
    DataFetchError,
    _is_etf_code,
    _is_hk_market,
    _is_us_market,
    normalize_stock_code,
)
from .realtime_types import ChipDistribution, ChipDistributionPoint

logger = logging.getLogger(__name__)

HistoryLoader = Callable[[str, int], Tuple[pd.DataFrame, str] | pd.DataFrame]
QuoteLoader = Callable[[str], Any]


class LocalChipModelFetcher(BaseFetcher):
    """Compute chip distribution from local daily K-line turnover data."""

    name = "LocalChipModelFetcher"
    priority = 98
    chip_priority = 2

    def __init__(
        self,
        history_loader: Optional[HistoryLoader] = None,
        quote_loader: Optional[QuoteLoader] = None,
        window_days: int = 180,
        max_price_points: int = 180,
    ) -> None:
        self.history_loader = history_loader
        self.quote_loader = quote_loader
        self.window_days = window_days
        self.max_price_points = max_price_points

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise DataFetchError(f"{self.name} 仅用于筹码分布模型，不提供日 K 数据")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        raise DataFetchError(f"{self.name} 仅用于筹码分布模型，不提供日 K 数据")

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        normalized_code = normalize_stock_code(stock_code)
        if not self._supports_stock(normalized_code):
            logger.debug("[本地筹码模型] %s 非 A 股普通股票，跳过", stock_code)
            return None

        try:
            history_df, history_source = self._load_history(normalized_code)
            chip = compute_chip_distribution_from_history(
                normalized_code,
                history_df,
                history_source=history_source,
                window_days=self.window_days,
                max_price_points=self.max_price_points,
                include_snapshots=True,
            )
            if chip is not None:
                logger.info(
                    "[本地筹码模型] %s 计算成功: rows=%s, source=%s, points=%s",
                    normalized_code,
                    len(history_df),
                    history_source,
                    len(chip.distribution),
                )
            return chip
        except Exception as exc:
            logger.warning("[本地筹码模型] %s 计算失败: %s", normalized_code, exc)
            return None

    @staticmethod
    def _supports_stock(stock_code: str) -> bool:
        return (
            stock_code.isdigit()
            and len(stock_code) == 6
            and not _is_hk_market(stock_code)
            and not _is_us_market(stock_code)
            and not _is_etf_code(stock_code)
        )

    def _load_history(self, stock_code: str) -> Tuple[pd.DataFrame, str]:
        if self.history_loader is not None:
            result = self.history_loader(stock_code, self.window_days)
            if isinstance(result, tuple):
                df, source = result
            else:
                df, source = result, "injected"
            if _has_turnover_data(df):
                return df, source
            derived_df = self._derive_turnover_from_float_shares(stock_code, df)
            if derived_df is not None and _has_turnover_data(derived_df):
                return derived_df, f"{source}:float_share_derived"
            return df, source

        from .akshare_fetcher import AkshareFetcher
        from .efinance_fetcher import EfinanceFetcher

        errors = []
        candidate_history: Optional[Tuple[pd.DataFrame, str]] = None
        for fetcher in (EfinanceFetcher(), AkshareFetcher(sleep_min=0.2, sleep_max=0.8)):
            try:
                df = fetcher.get_daily_data(stock_code, days=self.window_days)
                if df is not None and not df.empty and _has_turnover_data(df):
                    return df, fetcher.name
                if df is not None and not df.empty and candidate_history is None:
                    candidate_history = (df, fetcher.name)
                errors.append(f"{fetcher.name}: missing turnover_rate")
            except Exception as exc:
                errors.append(f"{fetcher.name}: {exc}")

        try:
            tushare_df = self._load_tushare_history_with_turnover(stock_code)
            if tushare_df is not None and not tushare_df.empty and _has_turnover_data(tushare_df):
                return tushare_df, "TushareFetcher:daily_basic"
            if tushare_df is not None and not tushare_df.empty and candidate_history is None:
                candidate_history = (tushare_df, "TushareFetcher")
            errors.append("TushareFetcher: missing daily_basic turnover_rate")
        except Exception as exc:
            errors.append(f"TushareFetcher: {exc}")

        if candidate_history is not None:
            candidate_df, candidate_source = candidate_history
            derived_df = self._derive_turnover_from_float_shares(stock_code, candidate_df)
            if derived_df is not None and _has_turnover_data(derived_df):
                return derived_df, f"{candidate_source}:float_share_derived"
            errors.append(f"{candidate_source}: unable to derive turnover_rate from float shares")

        raise DataFetchError("本地筹码模型无法取得含换手率的日 K 数据: " + "; ".join(errors))

    def _load_tushare_history_with_turnover(self, stock_code: str) -> Optional[pd.DataFrame]:
        from .tushare_fetcher import TushareFetcher

        fetcher = TushareFetcher()
        history_df = fetcher.get_daily_data(stock_code, days=self.window_days)
        if history_df is None or history_df.empty:
            return None

        ts_code = fetcher._convert_stock_code(stock_code)
        dates = pd.to_datetime(history_df["date"], errors="coerce").dropna()
        if dates.empty:
            return history_df

        start_date = dates.min().strftime("%Y%m%d")
        end_date = dates.max().strftime("%Y%m%d")
        basic_df = fetcher._call_api_with_rate_limit(
            "daily_basic",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,turnover_rate,turnover_rate_f",
        )
        if basic_df is None or basic_df.empty or "trade_date" not in basic_df.columns:
            return history_df

        basic = basic_df.copy()
        basic["date"] = pd.to_datetime(basic["trade_date"], format="%Y%m%d", errors="coerce")
        turnover_col = "turnover_rate_f" if "turnover_rate_f" in basic.columns else "turnover_rate"
        basic["turnover_rate"] = pd.to_numeric(basic.get(turnover_col), errors="coerce")

        history = history_df.copy()
        history["date"] = pd.to_datetime(history["date"], errors="coerce")
        if "turnover_rate" in history.columns:
            history = history.drop(columns=["turnover_rate"])
        return history.merge(basic[["date", "turnover_rate"]], on="date", how="left")

    def _derive_turnover_from_float_shares(self, stock_code: str, history_df: pd.DataFrame) -> Optional[pd.DataFrame]:
        quote = self._load_realtime_quote(stock_code)
        price = _safe_positive_float(getattr(quote, "price", None))
        circ_mv = _safe_positive_float(getattr(quote, "circ_mv", None))
        if price is None or circ_mv is None:
            return None

        float_shares = circ_mv / price
        if float_shares <= 0:
            return None

        df = history_df.copy()
        close = pd.to_numeric(df.get("close"), errors="coerce")
        amount = pd.to_numeric(df.get("amount"), errors="coerce") if "amount" in df.columns else pd.Series(index=df.index, dtype=float)
        volume = pd.to_numeric(df.get("volume"), errors="coerce") if "volume" in df.columns else pd.Series(index=df.index, dtype=float)

        amount_implied_volume = amount / close.replace(0, np.nan)
        shares = amount_implied_volume.where(amount_implied_volume > 0, volume)
        if not shares.dropna().empty:
            quote_turnover = _safe_positive_float(getattr(quote, "turnover_rate", None))
            derived_latest = _safe_positive_float((shares.dropna().iloc[-1] / float_shares) * 100)
            if quote_turnover and derived_latest and derived_latest * 50 < quote_turnover:
                shares = shares * 100

        df["turnover_rate"] = (shares / float_shares) * 100
        df["turnover_rate"] = pd.to_numeric(df["turnover_rate"], errors="coerce").clip(lower=0, upper=100)
        return df

    def _load_realtime_quote(self, stock_code: str) -> Any:
        if self.quote_loader is not None:
            return self.quote_loader(stock_code)

        from .akshare_fetcher import AkshareFetcher

        return AkshareFetcher(sleep_min=0.2, sleep_max=0.8).get_realtime_quote(stock_code, source="tencent")


def compute_chip_distribution_from_history(
    stock_code: str,
    history_df: pd.DataFrame,
    history_source: str = "history",
    window_days: int = 180,
    max_price_points: int = 180,
    include_snapshots: bool = False,
) -> Optional[ChipDistribution]:
    prepared = _prepare_history(history_df, window_days)
    if prepared.empty or len(prepared) < 2:
        return None

    prices = _build_price_axis(prepared, max_price_points=max_price_points)
    if prices.size == 0:
        return None

    chips = np.zeros(prices.size, dtype=float)
    snapshots: list[dict[str, Any]] = []

    for _, row in prepared.iterrows():
        turnover = _normalize_turnover(row["turnover_rate"])
        if turnover <= 0:
            continue

        low = float(row["low"])
        high = float(row["high"])
        avg_price = _daily_average_price(row)
        weights = _daily_price_weights(prices, low=low, high=high, peak=avg_price)
        if weights.sum() <= 0:
            continue

        chips *= 1 - turnover
        chips += weights * turnover

        if include_snapshots:
            total = float(chips.sum())
            if total > 0:
                snapshot = _build_chip_distribution(
                    stock_code=stock_code,
                    source=f"local_chip_model:{history_source}",
                    prices=prices,
                    chips=chips / total,
                    latest=row,
                )
                if snapshot is not None:
                    snapshots.append(_chip_to_snapshot_dict(snapshot))

    total = float(chips.sum())
    if total <= 0:
        return None

    chip = _build_chip_distribution(
        stock_code=stock_code,
        source=f"local_chip_model:{history_source}",
        prices=prices,
        chips=chips / total,
        latest=prepared.iloc[-1],
    )
    if chip is None:
        return None
    if include_snapshots:
        chip.snapshots = snapshots[-120:]
    return chip


def _build_chip_distribution(
    stock_code: str,
    source: str,
    prices: np.ndarray,
    chips: np.ndarray,
    latest: pd.Series,
) -> Optional[ChipDistribution]:
    current_price = float(latest["close"])
    date_value = latest["date"]
    date_text = date_value.strftime("%Y-%m-%d") if hasattr(date_value, "strftime") else str(date_value)
    profit_ratio = float(chips[prices <= current_price].sum())
    avg_cost = float(np.average(prices, weights=chips))
    cost_90_low, cost_90_high = _weighted_price_interval(prices, chips, 0.90)
    cost_70_low, cost_70_high = _weighted_price_interval(prices, chips, 0.70)

    distribution = [
        ChipDistributionPoint(price=round(float(price), 4), percent=round(float(percent), 8))
        for price, percent in zip(prices, chips)
        if percent > 0.000001
    ]
    if not distribution:
        return None

    return ChipDistribution(
        code=stock_code,
        date=date_text,
        source=source,
        profit_ratio=round(profit_ratio, 6),
        avg_cost=round(avg_cost, 4),
        cost_90_low=round(cost_90_low, 4),
        cost_90_high=round(cost_90_high, 4),
        concentration_90=round(_concentration(cost_90_low, cost_90_high), 6),
        cost_70_low=round(cost_70_low, 4),
        cost_70_high=round(cost_70_high, 4),
        concentration_70=round(_concentration(cost_70_low, cost_70_high), 6),
        distribution=distribution,
    )


def _chip_to_snapshot_dict(chip: ChipDistribution) -> dict[str, Any]:
    return {
        "code": chip.code,
        "date": chip.date,
        "source": chip.source,
        "profit_ratio": chip.profit_ratio,
        "avg_cost": chip.avg_cost,
        "cost_90_low": chip.cost_90_low,
        "cost_90_high": chip.cost_90_high,
        "concentration_90": chip.concentration_90,
        "cost_70_low": chip.cost_70_low,
        "cost_70_high": chip.cost_70_high,
        "concentration_70": chip.concentration_70,
        "distribution": [point.to_dict() for point in chip.distribution],
    }


def _prepare_history(history_df: pd.DataFrame, window_days: int) -> pd.DataFrame:
    if history_df is None or history_df.empty:
        return pd.DataFrame()

    required = ["date", "open", "high", "low", "close", "turnover_rate"]
    missing = [col for col in required if col not in history_df.columns]
    if missing:
        logger.debug("[本地筹码模型] 日 K 缺少字段: %s", missing)
        return pd.DataFrame()

    df = history_df[required].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    for col in ["open", "high", "low", "close", "turnover_rate"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    df = df[
        (df["open"] > 0)
        & (df["high"] > 0)
        & (df["low"] > 0)
        & (df["close"] > 0)
        & (df["high"] >= df["low"])
        & (df["turnover_rate"] > 0)
    ]
    if df.empty:
        return df
    return df.sort_values("date", ascending=True).tail(max(2, int(window_days))).reset_index(drop=True)


def _has_turnover_data(df: pd.DataFrame) -> bool:
    if df is None or df.empty or "turnover_rate" not in df.columns:
        return False
    values = pd.to_numeric(df["turnover_rate"], errors="coerce")
    return bool((values > 0).any())


def _build_price_axis(history_df: pd.DataFrame, max_price_points: int) -> np.ndarray:
    price_low = float(history_df["low"].min())
    price_high = float(history_df["high"].max())
    if not math.isfinite(price_low) or not math.isfinite(price_high) or price_low <= 0 or price_high <= 0:
        return np.array([])

    price_range = max(price_high - price_low, 0.01)
    point_count = max(40, int(max_price_points))
    raw_step = max(0.01, price_range / point_count)
    step = math.ceil(raw_step * 100) / 100
    axis_low = math.floor(price_low / step) * step
    axis_high = math.ceil(price_high / step) * step
    return np.round(np.arange(axis_low, axis_high + step * 0.5, step), 4)


def _normalize_turnover(turnover_rate: float) -> float:
    if not math.isfinite(float(turnover_rate)):
        return 0.0
    return max(0.0, min(float(turnover_rate) / 100.0, 1.0))


def _safe_positive_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(result) or result <= 0:
        return None
    return result


def _daily_average_price(row: pd.Series) -> float:
    values = [float(row[col]) for col in ("open", "high", "low", "close")]
    return float(sum(values) / len(values))


def _daily_price_weights(prices: np.ndarray, low: float, high: float, peak: float) -> np.ndarray:
    if high < low:
        low, high = high, low
    if high == low:
        weights = np.zeros(prices.size, dtype=float)
        weights[int(np.argmin(np.abs(prices - low)))] = 1.0
        return weights

    peak = min(max(float(peak), low), high)
    mask = (prices >= low) & (prices <= high)
    weights = np.zeros(prices.size, dtype=float)
    if not mask.any():
        weights[int(np.argmin(np.abs(prices - peak)))] = 1.0
        return weights

    width = max(high - low, 0.01)
    side_width = max(peak - low, high - peak, width / 2, 0.01)
    selected = prices[mask]
    triangular = 1.0 - (np.abs(selected - peak) / side_width)
    weights[mask] = np.clip(triangular, 0.0, 1.0) + 0.05
    return weights / weights.sum()


def _weighted_price_interval(prices: np.ndarray, weights: np.ndarray, coverage: float) -> Tuple[float, float]:
    tail = max(0.0, min(1.0 - coverage, 1.0)) / 2.0
    return _weighted_quantile(prices, weights, tail), _weighted_quantile(prices, weights, 1.0 - tail)


def _weighted_quantile(prices: np.ndarray, weights: np.ndarray, quantile: float) -> float:
    cumsum = np.cumsum(weights)
    index = int(np.searchsorted(cumsum, quantile, side="left"))
    index = max(0, min(index, len(prices) - 1))
    return float(prices[index])


def _concentration(low: float, high: float) -> float:
    denominator = high + low
    if denominator <= 0:
        return 0.0
    return float((high - low) / denominator)
