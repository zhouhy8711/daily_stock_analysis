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
            
            # UnifiedRealtimeQuote 是 dataclass，使用 getattr 安全访问字段
            # 字段映射: UnifiedRealtimeQuote -> API 响应
            # - code -> stock_code
            # - name -> stock_name
            # - price -> current_price
            # - change_amount -> change
            # - change_pct -> change_percent
            # - open_price -> open
            # - high -> high
            # - low -> low
            # - pre_close -> prev_close
            # - volume -> volume
            # - amount -> amount
            return {
                "stock_code": getattr(quote, "code", stock_code),
                "stock_name": getattr(quote, "name", None),
                "current_price": getattr(quote, "price", 0.0) or 0.0,
                "change": getattr(quote, "change_amount", None),
                "change_percent": getattr(quote, "change_pct", None),
                "open": getattr(quote, "open_price", None),
                "high": getattr(quote, "high", None),
                "low": getattr(quote, "low", None),
                "prev_close": getattr(quote, "pre_close", None),
                "volume": getattr(quote, "volume", None),
                "amount": getattr(quote, "amount", None),
                "volume_ratio": getattr(quote, "volume_ratio", None),
                "turnover_rate": getattr(quote, "turnover_rate", None),
                "amplitude": getattr(quote, "amplitude", None),
                "source": _source_to_string(getattr(quote, "source", None)),
                "update_time": datetime.now().isoformat(),
            }
            
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

                items.append({
                    "stock_code": getattr(quote, "code", original),
                    "stock_name": getattr(quote, "name", None),
                    "current_price": getattr(quote, "price", 0.0) or 0.0,
                    "change": getattr(quote, "change_amount", None),
                    "change_percent": getattr(quote, "change_pct", None),
                    "open": getattr(quote, "open_price", None),
                    "high": getattr(quote, "high", None),
                    "low": getattr(quote, "low", None),
                    "prev_close": getattr(quote, "pre_close", None),
                    "volume": getattr(quote, "volume", None),
                    "amount": getattr(quote, "amount", None),
                    "volume_ratio": getattr(quote, "volume_ratio", None),
                    "turnover_rate": getattr(quote, "turnover_rate", None),
                    "amplitude": getattr(quote, "amplitude", None),
                    "source": _source_to_string(getattr(quote, "source", None)),
                    "update_time": datetime.now().isoformat(),
                })

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
                    "chip_status": None,
                }
        except Exception as e:
            logger.debug(f"获取 {stock_code} 筹码分布失败: {e}")
            errors.append(f"chip_distribution:{type(e).__name__}")

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
            "major_holders": major_holders,
            "major_holder_status": major_holder_status,
            "source_chain": source_chain,
            "errors": errors,
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
            "volume_ratio": None,
            "turnover_rate": None,
            "amplitude": None,
            "source": "fallback",
            "update_time": datetime.now().isoformat(),
        }
