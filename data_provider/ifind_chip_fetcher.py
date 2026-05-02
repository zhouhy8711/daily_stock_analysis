# -*- coding: utf-8 -*-
"""
iFinD / 同花顺筹码数据源。

该 fetcher 只负责筹码分布，不参与 K 线数据获取。iFinD 的具体指标
代码需要在已授权账号的数据浏览器中确认，因此这里通过环境变量配置
指标名，并做宽松的返回结构解析，方便在不同授权口径下接入。
"""

import importlib
import logging
import os
from datetime import date, datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from .base import (
    BaseFetcher,
    DataFetchError,
    _is_etf_code,
    _is_hk_market,
    _is_us_market,
    is_bse_code,
    normalize_stock_code,
)
from .realtime_types import ChipDistribution, ChipDistributionPoint, safe_float
from src.config import get_config

logger = logging.getLogger(__name__)


SUMMARY_FIELD_ORDER = (
    "profit_ratio",
    "avg_cost",
    "cost_90_low",
    "cost_90_high",
    "concentration_90",
    "cost_70_low",
    "cost_70_high",
    "concentration_70",
)

SUMMARY_ALIASES: Dict[str, tuple[str, ...]] = {
    "profit_ratio": ("profit_ratio", "获利比例", "收盘获利", "收盘获利比例", "获利盘", "benefit_part", "benefitpart"),
    "avg_cost": ("avg_cost", "平均成本", "平均持仓成本", "筹码平均成本", "avgcost"),
    "cost_90_low": ("cost_90_low", "90成本-低", "90%成本低", "90%筹码成本下限", "90筹码低"),
    "cost_90_high": ("cost_90_high", "90成本-高", "90%成本高", "90%筹码成本上限", "90筹码高"),
    "concentration_90": ("concentration_90", "90集中度", "90%集中度", "90%筹码集中度"),
    "cost_70_low": ("cost_70_low", "70成本-低", "70%成本低", "70%筹码成本下限", "70筹码低"),
    "cost_70_high": ("cost_70_high", "70成本-高", "70%成本高", "70%筹码成本上限", "70筹码高"),
    "concentration_70": ("concentration_70", "70集中度", "70%集中度", "70%筹码集中度"),
}

PRICE_ALIASES = ("price", "价格", "成本价", "筹码价格", "cost_price")
PERCENT_ALIASES = ("percent", "占比", "筹码占比", "比例", "ratio", "weight")
DATE_ALIASES = ("date", "trade_date", "日期", "交易日期", "time")
CURRENT_PRICE_ALIASES = ("current_price", "close", "收盘价", "最新价", "price_current")
NON_METRIC_KEYS = ("code", "tscode", "股票代码", "证券代码", "name", "股票名称", "证券名称", *DATE_ALIASES)


def _split_config(value: str) -> List[str]:
    return [item.strip() for item in (value or "").replace(";", ",").split(",") if item.strip()]


def _normalize_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "").replace("-", "").replace("%", "")


def _find_column(columns: List[Any], aliases: tuple[str, ...]) -> Optional[Any]:
    alias_keys = {_normalize_key(alias) for alias in aliases}
    for column in columns:
        if _normalize_key(column) in alias_keys:
            return column
    return None


def _normalize_ratio(value: Any) -> Optional[float]:
    number = safe_float(value)
    if number is None:
        return None
    if abs(number) > 1:
        number = number / 100
    return number


class _IfindModuleClient:
    """Thin wrapper around the vendor iFinDPy module."""

    def __init__(self) -> None:
        self._module = importlib.import_module("iFinDPy")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._module, name)


class IfindChipFetcher(BaseFetcher):
    """同花顺 iFinD 筹码数据源，优先服务 A 股筹码分布。"""

    name = "IfindChipFetcher"
    priority = int(os.getenv("IFIND_PRIORITY", "99"))
    chip_priority = int(os.getenv("IFIND_CHIP_PRIORITY", "-2"))

    def __init__(self, client: Optional[Any] = None, **overrides: Any) -> None:
        config = get_config()
        self.username = overrides.get("username", getattr(config, "ifind_username", None))
        self.password = overrides.get("password", getattr(config, "ifind_password", None))
        self.summary_function = overrides.get("summary_function", getattr(config, "ifind_chip_summary_function", "THS_BD"))
        self.distribution_function = overrides.get(
            "distribution_function",
            getattr(config, "ifind_chip_distribution_function", "THS_BD"),
        )
        self.summary_indicators = overrides.get(
            "summary_indicators",
            getattr(config, "ifind_chip_summary_indicators", ""),
        )
        self.distribution_indicators = overrides.get(
            "distribution_indicators",
            getattr(config, "ifind_chip_distribution_indicators", ""),
        )
        self.params = overrides.get("params", getattr(config, "ifind_chip_params", ""))
        self._client = client
        self._logged_in = False

    def _fetch_raw_data(self, stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        raise DataFetchError("IfindChipFetcher only supports chip distribution")

    def _normalize_data(self, df: pd.DataFrame, stock_code: str) -> pd.DataFrame:
        raise DataFetchError("IfindChipFetcher only supports chip distribution")

    def is_available(self) -> bool:
        return bool(self.username and self.password and (self.summary_indicators or self.distribution_indicators))

    def get_chip_distribution(self, stock_code: str) -> Optional[ChipDistribution]:
        if not self.is_available():
            logger.debug("[iFinD] 筹码配置不完整，跳过")
            return None

        normalized = normalize_stock_code(stock_code)
        if _is_us_market(normalized) or _is_hk_market(normalized) or _is_etf_code(normalized):
            logger.debug("[iFinD] %s 非 A 股普通股票，跳过筹码分布", stock_code)
            return None

        try:
            client = self._get_client()
            self._ensure_login(client)
            ifind_code = self._to_ifind_code(normalized)
            summary_df = self._call_ifind(client, self.summary_function, ifind_code, self.summary_indicators)
            distribution_df = self._call_ifind(
                client,
                self.distribution_function,
                ifind_code,
                self.distribution_indicators,
            )

            summary = self._parse_summary(summary_df)
            distribution = self._parse_distribution(distribution_df)
            if not summary and not distribution:
                return None

            metrics = self._derive_missing_metrics(summary, distribution)
            return ChipDistribution(
                code=normalized,
                source="ifind",
                date=metrics.get("date") or self._latest_date(summary_df, distribution_df),
                profit_ratio=metrics.get("profit_ratio") or 0.0,
                avg_cost=metrics.get("avg_cost") or 0.0,
                cost_90_low=metrics.get("cost_90_low") or 0.0,
                cost_90_high=metrics.get("cost_90_high") or 0.0,
                concentration_90=metrics.get("concentration_90") or 0.0,
                cost_70_low=metrics.get("cost_70_low") or 0.0,
                cost_70_high=metrics.get("cost_70_high") or 0.0,
                concentration_70=metrics.get("concentration_70") or 0.0,
                distribution=distribution,
            )
        except Exception as exc:
            logger.warning("[iFinD] 获取 %s 筹码分布失败: %s", stock_code, exc)
            return None

    def _get_client(self) -> Any:
        if self._client is None:
            self._client = _IfindModuleClient()
        return self._client

    def _ensure_login(self, client: Any) -> None:
        if self._logged_in:
            return
        login = getattr(client, "THS_iFinDLogin", None)
        if login is None:
            raise DataFetchError("iFinD client missing THS_iFinDLogin")
        result = login(self.username, self.password)
        if not self._login_success(result):
            raise DataFetchError(f"iFinD login failed: {result}")
        self._logged_in = True

    @staticmethod
    def _login_success(result: Any) -> bool:
        if result in (0, "0", True):
            return True
        if isinstance(result, dict):
            return result.get("errorcode") in (0, "0") or result.get("error_code") in (0, "0")
        error_code = getattr(result, "errorcode", getattr(result, "error_code", None))
        return error_code in (0, "0")

    @staticmethod
    def _to_ifind_code(stock_code: str) -> str:
        code = normalize_stock_code(stock_code)
        if is_bse_code(code):
            return f"{code}.BJ"
        if code.startswith(("6", "5", "9")):
            return f"{code}.SH"
        return f"{code}.SZ"

    def _call_ifind(self, client: Any, function_name: str, code: str, indicators: str) -> Optional[pd.DataFrame]:
        if not indicators:
            return None
        func = getattr(client, function_name, None)
        if func is None:
            raise DataFetchError(f"iFinD client missing {function_name}")
        raw = func(code, indicators, self.params)
        return self._extract_dataframe(raw)

    @staticmethod
    def _extract_dataframe(raw: Any) -> Optional[pd.DataFrame]:
        if raw is None:
            return None
        error_code = getattr(raw, "errorcode", None)
        if error_code not in (None, 0, "0"):
            raise DataFetchError(getattr(raw, "errmsg", None) or f"iFinD error {error_code}")
        if isinstance(raw, pd.DataFrame):
            return raw
        data = getattr(raw, "data", raw.get("data") if isinstance(raw, dict) else None)
        if data is None:
            return None
        if isinstance(data, pd.DataFrame):
            return data
        if isinstance(data, dict):
            return pd.DataFrame([data])
        if isinstance(data, list):
            return pd.DataFrame(data)
        return None

    @staticmethod
    def _first_row(df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
        if df is None or df.empty:
            return None
        return df.iloc[-1]

    def _parse_summary(self, df: Optional[pd.DataFrame]) -> Dict[str, Any]:
        row = self._first_row(df)
        if row is None:
            return {}

        columns = list(row.index)
        parsed: Dict[str, Any] = {}
        used_columns = set()
        for field, aliases in SUMMARY_ALIASES.items():
            column = _find_column(columns, aliases)
            if column is None:
                continue
            used_columns.add(column)
            parsed[field] = _normalize_ratio(row[column]) if field.startswith("concentration") or field == "profit_ratio" else safe_float(row[column])

        date_column = _find_column(columns, DATE_ALIASES)
        if date_column is not None:
            parsed["date"] = self._format_date(row[date_column])

        current_column = _find_column(columns, CURRENT_PRICE_ALIASES)
        if current_column is not None:
            parsed["current_price"] = safe_float(row[current_column])

        if parsed:
            return parsed

        numeric_values = [
            safe_float(row[column])
            for column in columns
            if column not in used_columns
            and _normalize_key(column) not in {_normalize_key(item) for item in NON_METRIC_KEYS}
            and safe_float(row[column]) is not None
        ]
        for field, value in zip(SUMMARY_FIELD_ORDER, numeric_values):
            parsed[field] = _normalize_ratio(value) if field.startswith("concentration") or field == "profit_ratio" else value
        return parsed

    def _parse_distribution(self, df: Optional[pd.DataFrame]) -> List[ChipDistributionPoint]:
        if df is None or df.empty:
            return []

        price_col = _find_column(list(df.columns), PRICE_ALIASES)
        percent_col = _find_column(list(df.columns), PERCENT_ALIASES)
        if price_col is None or percent_col is None:
            numeric_cols = [
                column
                for column in df.columns
                if pd.to_numeric(df[column], errors="coerce").notna().any()
            ]
            if len(numeric_cols) >= 2:
                price_col, percent_col = numeric_cols[:2]
        if price_col is None or percent_col is None:
            return []

        work = df[[price_col, percent_col]].copy()
        work.columns = ["price", "percent"]
        work["price"] = pd.to_numeric(work["price"], errors="coerce")
        work["percent"] = pd.to_numeric(work["percent"], errors="coerce")
        work = work.dropna()
        work = work[(work["price"] > 0) & (work["percent"] > 0)]
        if work.empty:
            return []

        work = work.groupby("price", as_index=False)["percent"].sum().sort_values("price")
        total = float(work["percent"].sum())
        if total <= 0:
            return []
        return [
            ChipDistributionPoint(price=round(float(row["price"]), 4), percent=round(float(row["percent"]) / total, 8))
            for _, row in work.iterrows()
        ]

    def _derive_missing_metrics(
        self,
        summary: Dict[str, Any],
        distribution: List[ChipDistributionPoint],
    ) -> Dict[str, Any]:
        metrics = dict(summary)
        if not distribution:
            return metrics

        items = sorted(distribution, key=lambda item: item.price)
        total = sum(point.percent for point in items)
        if total <= 0:
            return metrics

        def quantile(target: float) -> float:
            cumulative = 0.0
            for point in items:
                cumulative += point.percent
                if cumulative >= target:
                    return point.price
            return items[-1].price

        if metrics.get("avg_cost") is None:
            metrics["avg_cost"] = round(sum(point.price * point.percent for point in items) / total, 4)
        if metrics.get("cost_90_low") is None:
            metrics["cost_90_low"] = quantile(total * 0.05)
        if metrics.get("cost_90_high") is None:
            metrics["cost_90_high"] = quantile(total * 0.95)
        if metrics.get("cost_70_low") is None:
            metrics["cost_70_low"] = quantile(total * 0.15)
        if metrics.get("cost_70_high") is None:
            metrics["cost_70_high"] = quantile(total * 0.85)
        if metrics.get("concentration_90") is None:
            metrics["concentration_90"] = self._concentration(metrics.get("cost_90_low"), metrics.get("cost_90_high"))
        if metrics.get("concentration_70") is None:
            metrics["concentration_70"] = self._concentration(metrics.get("cost_70_low"), metrics.get("cost_70_high"))

        current_price = metrics.get("current_price")
        if metrics.get("profit_ratio") is None and current_price:
            metrics["profit_ratio"] = sum(point.percent for point in items if point.price <= current_price) / total
        return metrics

    @staticmethod
    def _concentration(low: Any, high: Any) -> float:
        low_value = safe_float(low, 0.0) or 0.0
        high_value = safe_float(high, 0.0) or 0.0
        return (high_value - low_value) / (high_value + low_value) if high_value + low_value > 0 else 0.0

    @classmethod
    def _latest_date(cls, *frames: Optional[pd.DataFrame]) -> str:
        for frame in frames:
            row = cls._first_row(frame)
            if row is None:
                continue
            column = _find_column(list(row.index), DATE_ALIASES)
            if column is not None:
                return cls._format_date(row[column])
        return date.today().isoformat()

    @staticmethod
    def _format_date(value: Any) -> str:
        if isinstance(value, (datetime, date)):
            return value.strftime("%Y-%m-%d")
        text = str(value or "").strip()
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text
