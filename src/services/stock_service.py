# -*- coding: utf-8 -*-
"""
===================================
股票数据服务层
===================================

职责：
1. 封装股票数据获取逻辑
2. 提供实时行情和历史数据接口
"""

import logging
from datetime import datetime
import math
import re
from typing import Optional, Dict, Any, List

from src.core import trading_calendar
from src.repositories.stock_repo import StockRepository

logger = logging.getLogger(__name__)


def _to_optional_float(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        number = float(value)
        if math.isnan(number):
            return None
        return number
    except (TypeError, ValueError):
        return None


def _source_to_string(source: Any) -> Optional[str]:
    if source is None:
        return None
    value = getattr(source, "value", None)
    if value:
        return str(value)
    return str(source)


def _derive_share_count(market_value: Optional[float], price: Optional[float]) -> Optional[float]:
    if market_value is None or price is None or price <= 0:
        return None
    shares = market_value / price
    return shares if shares > 0 and math.isfinite(shares) else None


def _derive_market_value(shares: Optional[float], price: Optional[float]) -> Optional[float]:
    if shares is None or price is None or price <= 0:
        return None
    market_value = shares * price
    return market_value if market_value > 0 and math.isfinite(market_value) else None


def _derive_after_hours_volume(after_hours_amount: Optional[float], price: Optional[float]) -> Optional[float]:
    if after_hours_amount is None or price is None or price <= 0:
        return None
    volume_lot = after_hours_amount / price / 100
    return round(volume_lot, 2) if volume_lot > 0 and math.isfinite(volume_lot) else None


def _derive_after_hours_amount(after_hours_volume: Optional[float], price: Optional[float]) -> Optional[float]:
    if after_hours_volume is None or price is None or price <= 0:
        return None
    amount = after_hours_volume * 100 * price
    return amount if amount > 0 and math.isfinite(amount) else None


def _pure_stock_code(stock_code: str) -> str:
    value = str(stock_code or "").strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    value = re.sub(r"^(SH|SZ|BJ|HK)", "", value)
    return value


def _is_cn_equity_code(stock_code: str) -> bool:
    code = _pure_stock_code(stock_code)
    return bool(re.fullmatch(r"\d{6}", code))


def _infer_cn_limit_ratio(stock_code: str, stock_name: Optional[str]) -> float:
    code = _pure_stock_code(stock_code)
    name = stock_name or ""
    if "ST" in name.upper() or "退" in name:
        return 0.05
    if code.startswith(("300", "301", "688", "689", "8", "4", "920")):
        return 0.20
    return 0.10


def _round_price(value: float) -> float:
    return math.floor(value * 100 + 0.5) / 100.0


def _infer_limit_price(
    stock_code: str,
    stock_name: Optional[str],
    prev_close: Optional[float],
    direction: int,
) -> Optional[float]:
    if prev_close is None or prev_close <= 0 or not _is_cn_equity_code(stock_code):
        return None
    ratio = _infer_cn_limit_ratio(stock_code, stock_name)
    return _round_price(prev_close * (1 + direction * ratio))


def _build_quote_payload(quote: Any, fallback_code: str) -> Dict[str, Any]:
    stock_code = getattr(quote, "code", fallback_code)
    stock_name = getattr(quote, "name", None)
    price = _to_optional_float(getattr(quote, "price", None))
    total_mv = _to_optional_float(getattr(quote, "total_mv", None))
    circ_mv = _to_optional_float(getattr(quote, "circ_mv", None))
    prev_close = _to_optional_float(getattr(quote, "pre_close", None))
    total_shares = (
        _to_optional_float(getattr(quote, "total_shares", None))
        or _derive_share_count(total_mv, price)
    )
    float_shares = (
        _to_optional_float(getattr(quote, "float_shares", None))
        or _derive_share_count(circ_mv, price)
    )
    total_mv = total_mv or _derive_market_value(total_shares, price)
    circ_mv = circ_mv or _derive_market_value(float_shares, price)
    limit_up_price = (
        _to_optional_float(getattr(quote, "limit_up_price", None))
        or _infer_limit_price(stock_code, stock_name, prev_close, 1)
    )
    limit_down_price = (
        _to_optional_float(getattr(quote, "limit_down_price", None))
        or _infer_limit_price(stock_code, stock_name, prev_close, -1)
    )
    after_hours_volume = _to_optional_float(getattr(quote, "after_hours_volume", None))
    after_hours_amount = _to_optional_float(getattr(quote, "after_hours_amount", None))
    if after_hours_volume is None:
        after_hours_volume = _derive_after_hours_volume(after_hours_amount, price)
    if after_hours_amount is None:
        after_hours_amount = _derive_after_hours_amount(after_hours_volume, price)
    return {
        "stock_code": stock_code,
        "stock_name": stock_name,
        "current_price": price or 0.0,
        "change": getattr(quote, "change_amount", None),
        "change_percent": getattr(quote, "change_pct", None),
        "open": getattr(quote, "open_price", None),
        "high": getattr(quote, "high", None),
        "low": getattr(quote, "low", None),
        "prev_close": prev_close,
        "volume": getattr(quote, "volume", None),
        "amount": getattr(quote, "amount", None),
        "after_hours_volume": after_hours_volume,
        "after_hours_amount": after_hours_amount,
        "volume_ratio": getattr(quote, "volume_ratio", None),
        "turnover_rate": getattr(quote, "turnover_rate", None),
        "amplitude": getattr(quote, "amplitude", None),
        "pe_ratio": getattr(quote, "pe_ratio", None),
        "total_mv": total_mv,
        "circ_mv": circ_mv,
        "total_shares": total_shares,
        "float_shares": float_shares,
        "limit_up_price": limit_up_price,
        "limit_down_price": limit_down_price,
        "price_speed": getattr(quote, "price_speed", None),
        "entrust_ratio": getattr(quote, "entrust_ratio", None),
        "source": _source_to_string(getattr(quote, "source", None)),
        "update_time": datetime.now().isoformat(),
    }


class StockService:
    """
    股票数据服务
    
    封装股票数据获取的业务逻辑
    """
    
    def __init__(self):
        """初始化股票数据服务"""
        self.repo = StockRepository()
    
    def get_realtime_quote(self, stock_code: str) -> Optional[Dict[str, Any]]:
        """
        获取股票实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            实时行情数据字典
        """
        try:
            # 调用数据获取器获取实时行情
            from data_provider.base import DataFetcherManager
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None
            
            return _build_quote_payload(quote, stock_code)
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，使用占位数据")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None

    def get_realtime_quotes(self, stock_codes: List[str]) -> Dict[str, Any]:
        """
        批量获取股票实时行情。

        DataFetcherManager 会在 efinance/东财等全量行情源上复用模块级缓存，
        因此批量读取不会为每只股票重复拉取全市场行情。
        """
        seen = set()
        normalized_codes: List[str] = []
        for code in stock_codes:
            normalized = str(code or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            normalized_codes.append(normalized)

        if not normalized_codes:
            return {"items": [], "failed_codes": [], "update_time": datetime.now().isoformat()}

        try:
            from data_provider.base import DataFetcherManager, normalize_stock_code

            manager = DataFetcherManager()
            items: List[Dict[str, Any]] = []
            quote_by_code: Dict[str, Any] = {}
            normalized_to_original = {
                normalize_stock_code(code): code for code in normalized_codes
            }

            for fetcher in manager._get_fetchers_snapshot():
                if fetcher.name == "EfinanceFetcher" and hasattr(fetcher, "get_realtime_quotes"):
                    quote_by_code = fetcher.get_realtime_quotes(list(normalized_to_original.keys()))
                    break

            missing_normalized_codes = [
                normalized
                for normalized in normalized_to_original.keys()
                if normalized not in quote_by_code
            ]
            if missing_normalized_codes:
                for fetcher in manager._get_fetchers_snapshot():
                    if fetcher.name == "AkshareFetcher" and hasattr(fetcher, "get_realtime_quotes"):
                        fallback_quotes = fetcher.get_realtime_quotes(missing_normalized_codes)
                        quote_by_code.update(fallback_quotes)
                        break

            missing_codes = [
                original
                for normalized, original in normalized_to_original.items()
                if normalized not in quote_by_code
            ]
            if len(normalized_codes) <= 20 and missing_codes:
                for code in missing_codes:
                    quote = manager.get_realtime_quote(code, log_final_failure=False)
                    if quote is not None:
                        quote_by_code[normalize_stock_code(code)] = quote

            for normalized, original in normalized_to_original.items():
                quote = quote_by_code.get(normalized)
                if quote is None:
                    continue

                items.append(_build_quote_payload(quote, original))

            found_codes = {
                normalize_stock_code(str(item.get("stock_code") or ""))
                for item in items
            }
            failed_codes = [
                original
                for normalized, original in normalized_to_original.items()
                if normalized not in found_codes
            ]
            return {
                "items": items,
                "failed_codes": failed_codes,
                "update_time": datetime.now().isoformat(),
            }
        except ImportError:
            logger.warning("DataFetcherManager 未找到，批量行情返回空数据")
            return {
                "items": [],
                "failed_codes": normalized_codes,
                "update_time": datetime.now().isoformat(),
            }
        except Exception as e:
            logger.error(f"批量获取实时行情失败: {e}", exc_info=True)
            return {
                "items": [],
                "failed_codes": normalized_codes,
                "update_time": datetime.now().isoformat(),
            }

    def get_indicator_metrics(self, stock_code: str) -> Dict[str, Any]:
        """
        获取指标分析扩展数据：筹码分布与主力/机构持仓名称。

        这些数据源均为可选上下文，接口保持 fail-open，失败时返回空结构，
        不影响 K 线与实时行情展示。
        """
        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()
        stock_name = None
        chip_payload = None
        capital_flow_payload = None
        major_holder_status = "not_supported"
        major_holders: List[Dict[str, Any]] = []
        source_chain: List[Dict[str, Any]] = []
        errors: List[str] = []

        try:
            stock_name = manager.get_stock_name(stock_code, allow_realtime=False)
        except Exception as e:
            logger.debug(f"获取 {stock_code} 股票名称失败: {e}")

        try:
            chip = manager.get_chip_distribution(stock_code)
            if chip is not None:
                def normalize_distribution_points(raw_points):
                    normalized = []
                    for point in raw_points or []:
                        if isinstance(point, dict):
                            price = _to_optional_float(point.get("price"))
                            percent = _to_optional_float(point.get("percent"))
                        else:
                            price = _to_optional_float(getattr(point, "price", None))
                            percent = _to_optional_float(getattr(point, "percent", None))
                        if price is not None and price > 0 and percent is not None and percent > 0:
                            normalized.append({"price": price, "percent": percent})
                    return normalized

                def normalize_snapshot(snapshot):
                    if not isinstance(snapshot, dict):
                        return None
                    distribution = normalize_distribution_points(snapshot.get("distribution"))
                    if not distribution:
                        return None
                    return {
                        "code": snapshot.get("code") or getattr(chip, "code", stock_code),
                        "date": snapshot.get("date"),
                        "source": snapshot.get("source") or getattr(chip, "source", None),
                        "profit_ratio": _to_optional_float(snapshot.get("profit_ratio")),
                        "avg_cost": _to_optional_float(snapshot.get("avg_cost")),
                        "cost_90_low": _to_optional_float(snapshot.get("cost_90_low")),
                        "cost_90_high": _to_optional_float(snapshot.get("cost_90_high")),
                        "concentration_90": _to_optional_float(snapshot.get("concentration_90")),
                        "cost_70_low": _to_optional_float(snapshot.get("cost_70_low")),
                        "cost_70_high": _to_optional_float(snapshot.get("cost_70_high")),
                        "concentration_70": _to_optional_float(snapshot.get("concentration_70")),
                        "distribution": distribution,
                        "chip_status": snapshot.get("chip_status"),
                    }

                distribution = normalize_distribution_points(getattr(chip, "distribution", []) or [])
                snapshots = []
                for snapshot in getattr(chip, "snapshots", []) or []:
                    normalized_snapshot = normalize_snapshot(snapshot)
                    if normalized_snapshot is not None:
                        snapshots.append(normalized_snapshot)

                if not snapshots and distribution:
                    snapshots.append({
                        "code": getattr(chip, "code", stock_code),
                        "date": getattr(chip, "date", None),
                        "source": getattr(chip, "source", None),
                        "profit_ratio": _to_optional_float(getattr(chip, "profit_ratio", None)),
                        "avg_cost": _to_optional_float(getattr(chip, "avg_cost", None)),
                        "cost_90_low": _to_optional_float(getattr(chip, "cost_90_low", None)),
                        "cost_90_high": _to_optional_float(getattr(chip, "cost_90_high", None)),
                        "concentration_90": _to_optional_float(getattr(chip, "concentration_90", None)),
                        "cost_70_low": _to_optional_float(getattr(chip, "cost_70_low", None)),
                        "cost_70_high": _to_optional_float(getattr(chip, "cost_70_high", None)),
                        "concentration_70": _to_optional_float(getattr(chip, "concentration_70", None)),
                        "distribution": distribution,
                        "chip_status": None,
                    })

                raw_distribution = getattr(chip, "distribution", []) or []
                distribution = []
                for point in raw_distribution:
                    if isinstance(point, dict):
                        price = _to_optional_float(point.get("price"))
                        percent = _to_optional_float(point.get("percent"))
                    else:
                        price = _to_optional_float(getattr(point, "price", None))
                        percent = _to_optional_float(getattr(point, "percent", None))
                    if price is not None and price > 0 and percent is not None and percent > 0:
                        distribution.append({"price": price, "percent": percent})

                chip_payload = {
                    "code": getattr(chip, "code", stock_code),
                    "date": getattr(chip, "date", None),
                    "source": getattr(chip, "source", None),
                    "profit_ratio": _to_optional_float(getattr(chip, "profit_ratio", None)),
                    "avg_cost": _to_optional_float(getattr(chip, "avg_cost", None)),
                    "cost_90_low": _to_optional_float(getattr(chip, "cost_90_low", None)),
                    "cost_90_high": _to_optional_float(getattr(chip, "cost_90_high", None)),
                    "concentration_90": _to_optional_float(getattr(chip, "concentration_90", None)),
                    "cost_70_low": _to_optional_float(getattr(chip, "cost_70_low", None)),
                    "cost_70_high": _to_optional_float(getattr(chip, "cost_70_high", None)),
                    "concentration_70": _to_optional_float(getattr(chip, "concentration_70", None)),
                    "distribution": distribution,
                    "snapshots": snapshots,
                    "chip_status": None,
                }
        except Exception as e:
            logger.debug(f"获取 {stock_code} 筹码分布失败: {e}")
            errors.append(f"chip_distribution:{type(e).__name__}")

        try:
            capital_context = manager.get_capital_flow_context(stock_code)
            capital_status = str(capital_context.get("status", "not_supported"))
            capital_data = capital_context.get("data", {})
            stock_flow = {}
            if isinstance(capital_data, dict):
                raw_stock_flow = capital_data.get("stock_flow", {})
                if isinstance(raw_stock_flow, dict):
                    stock_flow = raw_stock_flow
            capital_flow_payload = {
                "status": capital_status,
                "main_net_inflow": _to_optional_float(stock_flow.get("main_net_inflow")),
                "main_net_inflow_ratio": _to_optional_float(stock_flow.get("main_net_inflow_ratio")),
                "inflow_5d": _to_optional_float(stock_flow.get("inflow_5d")),
                "inflow_10d": _to_optional_float(stock_flow.get("inflow_10d")),
            }
            source_chain.extend(capital_context.get("source_chain") or [])
            errors.extend(str(err) for err in (capital_context.get("errors") or []) if err)
        except Exception as e:
            logger.debug(f"获取 {stock_code} 资金流失败: {e}")
            capital_flow_payload = {
                "status": "failed",
                "main_net_inflow": None,
                "main_net_inflow_ratio": None,
                "inflow_5d": None,
                "inflow_10d": None,
            }
            errors.append(f"capital_flow:{type(e).__name__}")

        try:
            holder_context = manager.get_major_holders_context(stock_code, top_n=20)
            major_holder_status = str(holder_context.get("status", "not_supported"))
            holder_data = holder_context.get("data", {})
            if isinstance(holder_data, dict):
                raw_holders = holder_data.get("holders", [])
                if isinstance(raw_holders, list):
                    major_holders = [item for item in raw_holders if isinstance(item, dict)]
            source_chain.extend(holder_context.get("source_chain") or [])
            errors.extend(str(err) for err in (holder_context.get("errors") or []) if err)
        except Exception as e:
            logger.debug(f"获取 {stock_code} 主力持仓失败: {e}")
            major_holder_status = "failed"
            errors.append(f"major_holders:{type(e).__name__}")

        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "chip_distribution": chip_payload,
            "capital_flow": capital_flow_payload,
            "major_holders": major_holders,
            "major_holder_status": major_holder_status,
            "source_chain": source_chain,
            "errors": errors,
            "update_time": datetime.now().isoformat(),
        }

    def get_related_news(
        self,
        stock_code: str,
        *,
        limit: int = 8,
        days: Optional[int] = None,
        refresh: bool = False,
    ) -> Dict[str, Any]:
        """
        获取指标页右侧相关资讯。

        默认读取已有分析流程沉淀到 news_intel 表里的资讯；用户点击刷新时，
        触发一次公开财经/搜索通道拉取并写回本地库。该链路不调用 LLM。
        """
        from src.config import get_config
        from src.storage import DatabaseManager

        cfg = get_config()
        effective_days = days
        if effective_days is None:
            try:
                effective_days = cfg.get_effective_news_window_days()
            except Exception:
                effective_days = getattr(cfg, "news_max_age_days", 7)
        effective_days = max(1, min(int(effective_days or 7), 30))
        effective_limit = max(1, min(int(limit or 8), 20))

        db = DatabaseManager.get_instance()

        if refresh:
            stock_name = stock_code
            try:
                from data_provider.base import DataFetcherManager

                manager = DataFetcherManager()
                stock_name = manager.get_stock_name(stock_code, allow_realtime=False) or stock_code
            except Exception as e:
                logger.debug(f"刷新相关资讯时获取 {stock_code} 股票名称失败: {e}")

            try:
                from src.search_service import get_search_service

                response = get_search_service().search_stock_news(
                    stock_code,
                    stock_name,
                    max_results=effective_limit,
                )
                if response.success and response.results:
                    db.save_news_intel(
                        code=stock_code,
                        name=stock_name,
                        dimension="latest_news",
                        query=response.query,
                        response=response,
                        query_context={"query_source": "indicator_page"},
                    )
            except Exception as e:
                logger.warning(f"刷新 {stock_code} 相关资讯失败: {e}", exc_info=True)

        records = db.get_recent_news(code=stock_code, days=effective_days, limit=effective_limit)
        items: List[Dict[str, str]] = []
        for record in records:
            title = str(getattr(record, "title", "") or "").strip()
            url = str(getattr(record, "url", "") or "").strip()
            if not title or not url:
                continue
            snippet = str(getattr(record, "snippet", "") or "").strip()
            if len(snippet) > 200:
                snippet = f"{snippet[:197]}..."
            items.append({
                "title": title,
                "snippet": snippet,
                "url": url,
            })

        return {
            "total": len(items),
            "items": items,
            "update_time": datetime.now().isoformat(),
        }

    @staticmethod
    def _format_kline_date(value: Any, period: str) -> str:
        if hasattr(value, "strftime"):
            if period == "daily":
                return value.strftime("%Y-%m-%d")
            return value.strftime("%Y-%m-%d %H:%M")
        return str(value)

    @staticmethod
    def _build_kline_payload(df, period: str) -> List[Dict[str, Any]]:
        data: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            data.append({
                "date": StockService._format_kline_date(row.get("date"), period),
                "open": _to_optional_float(row.get("open")) or 0.0,
                "high": _to_optional_float(row.get("high")) or 0.0,
                "low": _to_optional_float(row.get("low")) or 0.0,
                "close": _to_optional_float(row.get("close")) or 0.0,
                "volume": _to_optional_float(row.get("volume")),
                "amount": _to_optional_float(row.get("amount")),
                "change_percent": _to_optional_float(row.get("pct_chg")),
                "turnover_rate": _to_optional_float(row.get("turnover_rate")),
            })
        return data
    
    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30
    ) -> Dict[str, Any]:
        """
        获取股票历史行情
        
        Args:
            stock_code: 股票代码
            period: K 线周期 (daily/1m/5m/15m/30m/60m)
            days: 获取天数
            
        Returns:
            历史行情数据字典
            
        Raises:
            ValueError: 当 period 不受支持时抛出
        """
        try:
            # 调用数据获取器获取历史数据
            from data_provider.base import DataFetcherManager, normalize_kline_period

            normalized_period = normalize_kline_period(period)
            
            manager = DataFetcherManager()
            if normalized_period == "daily":
                df, source = manager.get_daily_data(stock_code, days=days)
            else:
                intraday_kwargs: Dict[str, Any] = {
                    "period": normalized_period,
                    "days": max(1, min(int(days or 1), 30)),
                }
                try:
                    market = trading_calendar.get_market_for_stock(stock_code)
                    if market in {"cn", "hk"}:
                        market_today = trading_calendar.get_market_now(market).date()
                        if not trading_calendar.is_market_open(market, market_today):
                            effective_date = trading_calendar.get_effective_trading_date(market)
                            intraday_kwargs["end_date"] = effective_date.isoformat()
                except Exception as calendar_error:
                    logger.debug("分钟K交易日锚定失败，按默认日期拉取: %s", calendar_error)

                df, source = manager.get_intraday_data(stock_code, **intraday_kwargs)
            
            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                return {"stock_code": stock_code, "period": normalized_period, "data": []}
            
            # 获取股票名称
            stock_name = manager.get_stock_name(stock_code)
            
            # 转换为响应格式
            data = self._build_kline_payload(df, normalized_period)
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": normalized_period,
                "data": data,
            }
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，返回空数据")
            return {"stock_code": stock_code, "period": period, "data": []}
        except Exception as e:
            logger.error(f"获取历史数据失败: {e}", exc_info=True)
            return {"stock_code": stock_code, "period": period, "data": []}
    
    def _get_placeholder_quote(self, stock_code: str) -> Dict[str, Any]:
        """
        获取占位行情数据（用于测试）
        
        Args:
            stock_code: 股票代码
            
        Returns:
            占位行情数据
        """
        return {
            "stock_code": stock_code,
            "stock_name": f"股票{stock_code}",
            "current_price": 0.0,
            "change": None,
            "change_percent": None,
            "open": None,
            "high": None,
            "low": None,
            "prev_close": None,
            "volume": None,
            "amount": None,
            "after_hours_volume": None,
            "after_hours_amount": None,
            "volume_ratio": None,
            "turnover_rate": None,
            "amplitude": None,
            "pe_ratio": None,
            "total_mv": None,
            "circ_mv": None,
            "total_shares": None,
            "float_shares": None,
            "limit_up_price": None,
            "limit_down_price": None,
            "price_speed": None,
            "entrust_ratio": None,
            "source": "fallback",
            "update_time": datetime.now().isoformat(),
        }
