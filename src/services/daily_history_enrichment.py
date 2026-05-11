# -*- coding: utf-8 -*-
"""Helpers for enriching stock_daily rows before they are written to DB."""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


OPTIONAL_DAILY_METRIC_COLUMNS = (
    "turnover_rate",
    "pe_ratio",
    "total_mv",
    "circ_mv",
    "total_shares",
    "float_shares",
)


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if not pd.notna(parsed):
        return None
    return parsed


def _get_quote_value(quote: Any, *names: str) -> Optional[float]:
    if quote is None:
        return None
    for name in names:
        if isinstance(quote, dict):
            value = quote.get(name)
        else:
            value = getattr(quote, name, None)
        parsed = _safe_float(value)
        if parsed is not None:
            return parsed
    return None


def _derive_shares(market_value: Optional[float], price: Optional[float]) -> Optional[float]:
    if market_value is None or price is None or price <= 0:
        return None
    shares = market_value / price
    return shares if shares > 0 else None


def _positive_series(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _ensure_column(df: pd.DataFrame, column: str) -> None:
    if column not in df.columns:
        df[column] = pd.NA


def _fill_missing_positive(df: pd.DataFrame, column: str, values: Any) -> None:
    _ensure_column(df, column)
    current = _positive_series(df[column])
    missing_mask = current.isna() | (current <= 0)
    if not missing_mask.any():
        return
    if isinstance(values, pd.Series):
        df.loc[missing_mask, column] = values.loc[missing_mask]
    else:
        df.loc[missing_mask, column] = values


def _estimate_turnover_rate(df: pd.DataFrame, float_shares: float) -> pd.Series:
    close = _positive_series(df.get("close", pd.Series(index=df.index, dtype=float)))
    amount = _positive_series(df.get("amount", pd.Series(index=df.index, dtype=float)))
    volume = _positive_series(df.get("volume", pd.Series(index=df.index, dtype=float)))

    amount_implied_shares = amount / close.where(close > 0)
    volume_implied_shares = volume * 100
    traded_shares = amount_implied_shares.where(amount_implied_shares > 0, volume_implied_shares)
    turnover = (traded_shares / float_shares) * 100
    return turnover.where((turnover >= 0) & (turnover <= 100))


def enrich_daily_history_with_quote_fields(
    df: pd.DataFrame,
    stock_code: str,
    *,
    quote: Any = None,
    quote_loader: Optional[Callable[[str], Any]] = None,
) -> pd.DataFrame:
    """
    Fill optional stock_daily metrics from a quote snapshot before persisting history.

    Historical K-line providers often return OHLCV plus turnover only. During offline
    sync we can use one quote snapshot to derive share count and approximate daily
    valuation fields, then store them in stock_daily so UI reads stay DB-only.
    """
    if df is None or df.empty:
        return df

    quote_payload = quote
    if quote_payload is None and quote_loader is not None:
        try:
            quote_payload = quote_loader(stock_code)
        except Exception as exc:
            logger.debug("%s quote enrichment skipped: %s", stock_code, exc)
            quote_payload = None
    if quote_payload is None:
        return df

    out = df.copy()
    close = _positive_series(out.get("close", pd.Series(index=out.index, dtype=float)))

    quote_price = _get_quote_value(quote_payload, "price", "current_price", "currentPrice")
    quote_pe = _get_quote_value(quote_payload, "pe_ratio", "peRatio", "pe")
    quote_total_mv = _get_quote_value(quote_payload, "total_mv", "totalMv")
    quote_circ_mv = _get_quote_value(quote_payload, "circ_mv", "circMv")
    total_shares = _get_quote_value(quote_payload, "total_shares", "totalShares")
    float_shares = _get_quote_value(quote_payload, "float_shares", "floatShares")

    if total_shares is None:
        total_shares = _derive_shares(quote_total_mv, quote_price)
    if float_shares is None:
        float_shares = _derive_shares(quote_circ_mv, quote_price)

    if total_shares is not None and total_shares > 0:
        _fill_missing_positive(out, "total_shares", total_shares)
        _fill_missing_positive(out, "total_mv", close * total_shares)
    elif quote_total_mv is not None and quote_total_mv > 0:
        _fill_missing_positive(out, "total_mv", quote_total_mv)

    if float_shares is not None and float_shares > 0:
        _fill_missing_positive(out, "float_shares", float_shares)
        _fill_missing_positive(out, "circ_mv", close * float_shares)
        _fill_missing_positive(out, "turnover_rate", _estimate_turnover_rate(out, float_shares))
    elif quote_circ_mv is not None and quote_circ_mv > 0:
        _fill_missing_positive(out, "circ_mv", quote_circ_mv)

    if quote_pe is not None and quote_pe > 0:
        if quote_price is not None and quote_price > 0:
            _fill_missing_positive(out, "pe_ratio", quote_pe * (close / quote_price))
        else:
            _fill_missing_positive(out, "pe_ratio", quote_pe)

    return out
