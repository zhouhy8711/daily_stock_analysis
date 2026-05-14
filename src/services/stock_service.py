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
from copy import deepcopy
from datetime import date, datetime, timedelta
import math
import re
import sys
from threading import RLock
import time
from typing import Optional, Dict, Any, List, Tuple

from src.core import trading_calendar
from src.config import get_config
from src.repositories.stock_repo import StockRepository
from src.services.daily_history_enrichment import enrich_daily_history_with_quote_fields

logger = logging.getLogger(__name__)


_REALTIME_QUOTE_CACHE_LOCK = RLock()
_REALTIME_QUOTE_CACHE_BUCKET: Optional[int] = None
_REALTIME_QUOTE_CACHE: Dict[str, Dict[str, Any]] = {}
_REALTIME_QUOTE_SNAPSHOT_LOCK = RLock()
_REALTIME_QUOTE_SNAPSHOT: Dict[str, Any] = {
    "snapshot_id": None,
    "snapshot_time": None,
    "items_by_code": {},
    "requested_count": 0,
    "hit_count": 0,
    "miss_count": 0,
}


def _get_realtime_cache_ttl() -> int:
    try:
        config = get_config()
        ttl = int(
            getattr(
                config,
                "realtime_quote_cache_seconds",
                getattr(config, "realtime_cache_ttl", 30),
            )
            or 0
        )
    except Exception:
        ttl = 30
    return max(0, ttl)


def _get_realtime_cache_bucket(now: Optional[float] = None, ttl: Optional[int] = None) -> Optional[int]:
    effective_ttl = _get_realtime_cache_ttl() if ttl is None else max(0, int(ttl))
    if effective_ttl <= 0:
        return None
    current_time = time.time() if now is None else now
    return int(current_time // effective_ttl) * effective_ttl


def _clear_realtime_quote_cache() -> None:
    global _REALTIME_QUOTE_CACHE_BUCKET
    with _REALTIME_QUOTE_CACHE_LOCK:
        _REALTIME_QUOTE_CACHE.clear()
        _REALTIME_QUOTE_CACHE_BUCKET = None
    _clear_realtime_quote_snapshot()


def _clear_realtime_quote_snapshot() -> None:
    with _REALTIME_QUOTE_SNAPSHOT_LOCK:
        _REALTIME_QUOTE_SNAPSHOT.update({
            "snapshot_id": None,
            "snapshot_time": None,
            "items_by_code": {},
            "requested_count": 0,
            "hit_count": 0,
            "miss_count": 0,
        })


def _realtime_quote_cache_size() -> int:
    with _REALTIME_QUOTE_CACHE_LOCK:
        return len(_REALTIME_QUOTE_CACHE)


def _get_realtime_quote_cache_codes() -> set[str]:
    bucket = _get_realtime_cache_bucket()
    if bucket is None:
        return set()
    global _REALTIME_QUOTE_CACHE_BUCKET
    with _REALTIME_QUOTE_CACHE_LOCK:
        if _REALTIME_QUOTE_CACHE_BUCKET != bucket:
            _REALTIME_QUOTE_CACHE.clear()
            _REALTIME_QUOTE_CACHE_BUCKET = bucket
            return set()
        return set(_REALTIME_QUOTE_CACHE.keys())


def _get_snapshot_payload(normalized_code: str) -> Optional[Dict[str, Any]]:
    with _REALTIME_QUOTE_SNAPSHOT_LOCK:
        payload = (_REALTIME_QUOTE_SNAPSHOT.get("items_by_code") or {}).get(normalized_code)
        payload_copy = deepcopy(payload) if payload is not None else None
    if payload_copy is None:
        return None
    if not _snapshot_payload_matches_market_day(normalized_code, payload_copy):
        logger.info(
            "实时行情快照已跨交易日失效，忽略旧快照: code=%s snapshot_time=%s",
            normalized_code,
            payload_copy.get("snapshot_time"),
        )
        _clear_realtime_quote_snapshot()
        return None
    return payload_copy


def _coerce_snapshot_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _snapshot_payload_matches_market_day(normalized_code: str, payload: Dict[str, Any]) -> bool:
    market = trading_calendar.get_market_for_stock(normalized_code)
    if not market:
        return True

    snapshot_dt = _coerce_snapshot_datetime(payload.get("snapshot_time"))
    if snapshot_dt is None:
        return False

    snapshot_date = trading_calendar.get_market_now(market, current_time=snapshot_dt).date()
    current_date = trading_calendar.get_market_now(market).date()
    return snapshot_date == current_date


def _get_realtime_quote_snapshot_info() -> Dict[str, Any]:
    with _REALTIME_QUOTE_SNAPSHOT_LOCK:
        snapshot_time = _REALTIME_QUOTE_SNAPSHOT.get("snapshot_time")
        age_seconds = None
        if isinstance(snapshot_time, datetime):
            age_seconds = max(0, int((datetime.now() - snapshot_time).total_seconds()))
        return {
            "snapshot_id": _REALTIME_QUOTE_SNAPSHOT.get("snapshot_id"),
            "snapshot_time": snapshot_time.isoformat() if isinstance(snapshot_time, datetime) else snapshot_time,
            "snapshot_age_seconds": age_seconds,
            "quote_snapshot_items": len(_REALTIME_QUOTE_SNAPSHOT.get("items_by_code") or {}),
            "snapshot_requested_count": int(_REALTIME_QUOTE_SNAPSHOT.get("requested_count") or 0),
            "snapshot_hit_count": int(_REALTIME_QUOTE_SNAPSHOT.get("hit_count") or 0),
            "snapshot_miss_count": int(_REALTIME_QUOTE_SNAPSHOT.get("miss_count") or 0),
        }


def get_realtime_quote_cache_diagnostics() -> Dict[str, Any]:
    """Return code-level realtime quote cache diagnostics without exposing payloads."""
    snapshot_info = _get_realtime_quote_snapshot_info()
    with _REALTIME_QUOTE_SNAPSHOT_LOCK:
        snapshot_codes = sorted((_REALTIME_QUOTE_SNAPSHOT.get("items_by_code") or {}).keys())

    bucket = _get_realtime_cache_bucket()
    with _REALTIME_QUOTE_CACHE_LOCK:
        if bucket is not None and _REALTIME_QUOTE_CACHE_BUCKET == bucket:
            short_cache_codes = sorted(_REALTIME_QUOTE_CACHE.keys())
        else:
            short_cache_codes = []

    return {
        **snapshot_info,
        "snapshot_codes": snapshot_codes,
        "short_cache_codes": short_cache_codes,
        "short_cache_items": len(short_cache_codes),
    }


def _replace_realtime_quote_snapshot(
    *,
    requested_codes: List[str],
    items: List[Dict[str, Any]],
    failed_codes: List[str],
    snapshot_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    snapshot_time = snapshot_time or datetime.now()
    snapshot_id = snapshot_time.strftime("%Y%m%d%H%M%S")
    items_by_code: Dict[str, Dict[str, Any]] = {}
    for item in items:
        code = str(item.get("stock_code") or item.get("code") or "").strip().upper()
        if not code:
            continue
        payload = {
            **item,
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time.isoformat(),
            "quote_time": item.get("update_time") or snapshot_time.isoformat(),
        }
        items_by_code[code] = deepcopy(payload)

    requested_set = {str(code or "").strip().upper() for code in requested_codes if str(code or "").strip()}
    failed_set = {str(code or "").strip().upper() for code in failed_codes if str(code or "").strip()}
    missing_count = len((requested_set - set(items_by_code.keys())) | failed_set)
    with _REALTIME_QUOTE_SNAPSHOT_LOCK:
        _REALTIME_QUOTE_SNAPSHOT.update({
            "snapshot_id": snapshot_id,
            "snapshot_time": snapshot_time,
            "items_by_code": items_by_code,
            "requested_count": len(requested_set),
            "hit_count": len(items_by_code),
            "miss_count": missing_count,
        })
    return _get_realtime_quote_snapshot_info()


def _deep_getsizeof(value: Any, seen: Optional[set[int]] = None) -> int:
    seen = seen or set()
    object_id = id(value)
    if object_id in seen:
        return 0
    seen.add(object_id)

    if value is None:
        return 0

    memory_usage = getattr(value, "memory_usage", None)
    if callable(memory_usage):
        try:
            usage = memory_usage(deep=True)
            if hasattr(usage, "sum"):
                return int(usage.sum()) + sys.getsizeof(value)
            return int(usage) + sys.getsizeof(value)
        except Exception:
            pass

    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_getsizeof(key, seen) + _deep_getsizeof(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_deep_getsizeof(item, seen) for item in value)
    return int(size)


def _estimate_realtime_quote_cache_bytes() -> int:
    with _REALTIME_QUOTE_CACHE_LOCK:
        if not _REALTIME_QUOTE_CACHE:
            return 0
        snapshot = deepcopy(_REALTIME_QUOTE_CACHE)
    return _deep_getsizeof(snapshot)


def _provider_cache_memory_bytes(cache: Dict[str, Any]) -> int:
    data = cache.get("data")
    if data is None:
        return 0
    return _deep_getsizeof(data)


def get_realtime_quote_cache_stats() -> Dict[str, Any]:
    """Return lightweight memory stats for in-process realtime quote caches."""
    quote_cache_bytes = _estimate_realtime_quote_cache_bytes()
    provider_cache_bytes = 0

    provider_caches: List[Tuple[str, Dict[str, Any]]] = []
    for module_name in ("data_provider.efinance_fetcher", "data_provider.akshare_fetcher"):
        try:
            module = __import__(module_name, fromlist=["_realtime_cache", "_etf_realtime_cache"])
            provider_caches.extend([
                (f"{module_name}._realtime_cache", getattr(module, "_realtime_cache", {})),
                (f"{module_name}._etf_realtime_cache", getattr(module, "_etf_realtime_cache", {})),
            ])
        except Exception:
            continue

    provider_breakdown: List[Dict[str, Any]] = []
    for name, cache in provider_caches:
        if not isinstance(cache, dict):
            continue
        cache_bytes = _provider_cache_memory_bytes(cache)
        provider_cache_bytes += cache_bytes
        provider_breakdown.append({
            "name": name,
            "memory_bytes": cache_bytes,
            "memory_mb": round(cache_bytes / 1024 / 1024, 4),
        })

    total_bytes = quote_cache_bytes + provider_cache_bytes
    with _REALTIME_QUOTE_CACHE_LOCK:
        bucket_start = _REALTIME_QUOTE_CACHE_BUCKET
        quote_cache_items = len(_REALTIME_QUOTE_CACHE)

    return {
        "total_memory_bytes": total_bytes,
        "total_memory_mb": round(total_bytes / 1024 / 1024, 4),
        "quote_cache_items": quote_cache_items,
        "quote_cache_memory_bytes": quote_cache_bytes,
        "quote_cache_memory_mb": round(quote_cache_bytes / 1024 / 1024, 4),
        "provider_cache_memory_bytes": provider_cache_bytes,
        "provider_cache_memory_mb": round(provider_cache_bytes / 1024 / 1024, 4),
        "bucket_start": bucket_start,
        "provider_breakdown": provider_breakdown,
        **_get_realtime_quote_snapshot_info(),
    }


def _get_cached_quote_payload(normalized_code: str) -> Optional[Dict[str, Any]]:
    bucket = _get_realtime_cache_bucket()
    if bucket is None:
        return None
    global _REALTIME_QUOTE_CACHE_BUCKET
    with _REALTIME_QUOTE_CACHE_LOCK:
        if _REALTIME_QUOTE_CACHE_BUCKET != bucket:
            _REALTIME_QUOTE_CACHE.clear()
            _REALTIME_QUOTE_CACHE_BUCKET = bucket
            return None
        payload = _REALTIME_QUOTE_CACHE.get(normalized_code)
        return deepcopy(payload) if payload is not None else None


def _cache_quote_payload(normalized_code: str, payload: Dict[str, Any]) -> None:
    bucket = _get_realtime_cache_bucket()
    if bucket is None:
        return
    global _REALTIME_QUOTE_CACHE_BUCKET
    with _REALTIME_QUOTE_CACHE_LOCK:
        if _REALTIME_QUOTE_CACHE_BUCKET != bucket:
            _REALTIME_QUOTE_CACHE.clear()
            _REALTIME_QUOTE_CACHE_BUCKET = bucket
        _REALTIME_QUOTE_CACHE[normalized_code] = deepcopy(payload)


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


def _to_optional_string(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, "isoformat"):
        value = value.isoformat()
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None
    return text


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


def _relative_gap(value: float, target: float) -> float:
    if target <= 0:
        return math.inf
    return abs(value - target) / target


def _pure_stock_code(stock_code: str) -> str:
    value = str(stock_code or "").strip().upper()
    if "." in value:
        value = value.split(".", 1)[0]
    value = re.sub(r"^(SH|SZ|BJ|HK)", "", value)
    return value


def _is_cn_equity_code(stock_code: str) -> bool:
    code = _pure_stock_code(stock_code)
    return bool(re.fullmatch(r"\d{6}", code))


def _normalize_cn_volume_to_lots(
    stock_code: str,
    volume: Any,
    amount: Any,
    price: Any,
) -> Optional[float]:
    volume_value = _to_optional_float(volume)
    if volume_value is None:
        return None
    if not _is_cn_equity_code(stock_code):
        return volume_value

    amount_value = _to_optional_float(amount)
    price_value = _to_optional_float(price)
    if amount_value is None or price_value is None or price_value <= 0:
        return volume_value

    inferred_shares = amount_value / price_value
    inferred_lots = inferred_shares / 100
    if inferred_shares <= 0 or inferred_lots <= 0:
        return volume_value

    if (
        _relative_gap(volume_value, inferred_shares) <= 0.2
        and _relative_gap(volume_value, inferred_lots) > 0.2
    ):
        return volume_value / 100
    return volume_value


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
    amount = _to_optional_float(getattr(quote, "amount", None))
    volume = _normalize_cn_volume_to_lots(stock_code, getattr(quote, "volume", None), amount, price)
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
        "volume": volume,
        "amount": amount,
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

    def get_realtime_quote_snapshot_info(self) -> Dict[str, Any]:
        """Return metadata for the latest warmed realtime quote snapshot."""
        return _get_realtime_quote_snapshot_info()
    
    def get_realtime_quote(
        self,
        stock_code: str,
        *,
        data_policy: str = "default",
    ) -> Optional[Dict[str, Any]]:
        """
        获取股票实时行情
        
        Args:
            stock_code: 股票代码
            
        Returns:
            实时行情数据字典
        """
        try:
            # 调用数据获取器获取实时行情
            from data_provider.base import DataFetcherManager, normalize_stock_code

            normalized_code = normalize_stock_code(stock_code)
            normalized_policy = str(data_policy or "default").strip().lower()
            snapshot_payload = _get_snapshot_payload(normalized_code)
            if snapshot_payload is not None:
                return snapshot_payload
            if normalized_policy in {"snapshot_only", "db_only"}:
                return None

            cached_payload = _get_cached_quote_payload(normalized_code)
            if cached_payload is not None:
                return cached_payload
            if normalized_policy in {"cache_only", "db_only"}:
                return None
            
            manager = DataFetcherManager()
            quote = manager.get_realtime_quote(stock_code)
            
            if quote is None:
                logger.warning(f"获取 {stock_code} 实时行情失败")
                return None
            
            payload = _build_quote_payload(quote, stock_code)
            _cache_quote_payload(normalized_code, payload)
            return payload
            
        except ImportError:
            logger.warning("DataFetcherManager 未找到，使用占位数据")
            return self._get_placeholder_quote(stock_code)
        except Exception as e:
            logger.error(f"获取实时行情失败: {e}", exc_info=True)
            return None

    def get_realtime_quotes(
        self,
        stock_codes: List[str],
        *,
        force_refresh: bool = False,
        data_policy: str = "default",
    ) -> Dict[str, Any]:
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

            normalized_policy = str(data_policy or "default").strip().lower()
            items: List[Dict[str, Any]] = []
            cached_by_code: Dict[str, Dict[str, Any]] = {}
            quote_by_code: Dict[str, Any] = {}
            normalized_to_original = {
                normalize_stock_code(code): code for code in normalized_codes
            }
            missing_for_fetch: List[str] = []
            for normalized in normalized_to_original.keys():
                snapshot_payload = None if force_refresh else _get_snapshot_payload(normalized)
                if snapshot_payload is not None:
                    cached_by_code[normalized] = snapshot_payload
                    continue
                if normalized_policy in {"snapshot_only", "db_only"}:
                    missing_for_fetch.append(normalized)
                    continue
                cached_payload = None if force_refresh else _get_cached_quote_payload(normalized)
                if cached_payload is None:
                    missing_for_fetch.append(normalized)
                else:
                    cached_by_code[normalized] = cached_payload

            if normalized_policy in {"snapshot_only", "cache_only", "db_only"}:
                found_codes = {
                    normalize_stock_code(str(item.get("stock_code") or ""))
                    for item in cached_by_code.values()
                }
                return {
                    "items": list(cached_by_code.values()),
                    "failed_codes": [
                        original
                        for normalized, original in normalized_to_original.items()
                        if normalized not in found_codes
                    ],
                    "update_time": datetime.now().isoformat(),
                    **_get_realtime_quote_snapshot_info(),
                }

            manager = DataFetcherManager() if missing_for_fetch else None
            if missing_for_fetch and manager is not None:
                for fetcher in manager._get_fetchers_snapshot():
                    if fetcher.name == "EfinanceFetcher" and hasattr(fetcher, "get_realtime_quotes"):
                        quote_by_code = fetcher.get_realtime_quotes(missing_for_fetch)
                        break

            missing_normalized_codes = [
                normalized
                for normalized in missing_for_fetch
                if normalized not in quote_by_code
            ]
            if missing_normalized_codes and manager is not None:
                for fetcher in manager._get_fetchers_snapshot():
                    if fetcher.name == "AkshareFetcher" and hasattr(fetcher, "get_realtime_quotes"):
                        fallback_quotes = fetcher.get_realtime_quotes(missing_normalized_codes)
                        quote_by_code.update(fallback_quotes)
                        break

            missing_codes = [
                original
                for normalized, original in normalized_to_original.items()
                if normalized not in cached_by_code and normalized not in quote_by_code
            ]
            if len(normalized_codes) <= 20 and missing_codes and manager is not None:
                for code in missing_codes:
                    quote = manager.get_realtime_quote(code, log_final_failure=False)
                    if quote is not None:
                        quote_by_code[normalize_stock_code(code)] = quote

            for normalized, original in normalized_to_original.items():
                cached_payload = cached_by_code.get(normalized)
                if cached_payload is not None:
                    items.append(cached_payload)
                    continue

                quote = quote_by_code.get(normalized)
                if quote is None:
                    continue

                payload = _build_quote_payload(quote, original)
                _cache_quote_payload(normalized, payload)
                items.append(payload)

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
                **_get_realtime_quote_snapshot_info(),
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

    def warm_realtime_quotes(self, stock_codes: List[str], *, force_refresh: bool = False) -> Dict[str, Any]:
        """
        Preload realtime quote payloads into the process cache.

        The method fills only missing codes in the current REALTIME_QUOTE_CACHE_SECONDS bucket by default.
        When the bucket has rolled over, all requested codes are considered missing and will be
        fetched again through the provider-level full-market cache.
        """
        try:
            from data_provider.base import normalize_stock_code
        except ImportError:
            return {
                "status": "skipped",
                "reason": "data_fetcher_unavailable",
                "requested_count": len(stock_codes),
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": len(stock_codes),
            }

        normalized_codes = list(dict.fromkeys(
            normalize_stock_code(code)
            for code in stock_codes
            if str(code or "").strip()
        ))
        if not normalized_codes:
            return {
                "status": "skipped",
                "reason": "empty_stock_codes",
                "requested_count": 0,
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": 0,
            }

        if _get_realtime_cache_ttl() <= 0:
            return {
                "status": "skipped",
                "reason": "realtime_cache_disabled",
                "requested_count": len(normalized_codes),
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": len(normalized_codes),
            }

        snapshot_time = datetime.now()
        current_cached_codes = _get_realtime_quote_cache_codes()
        cached_before = sum(1 for code in normalized_codes if code in current_cached_codes)
        missing_codes = normalized_codes if force_refresh else [
            code for code in normalized_codes if code not in current_cached_codes
        ]
        if not missing_codes:
            snapshot_items = [
                payload
                for code in normalized_codes
                for payload in [_get_cached_quote_payload(code)]
                if payload is not None
            ]
            snapshot_info = _replace_realtime_quote_snapshot(
                requested_codes=normalized_codes,
                items=snapshot_items,
                failed_codes=[],
                snapshot_time=snapshot_time,
            )
            intraday_saved = self.repo.db.save_intraday_quote_samples(
                snapshot_items,
                snapshot_id=str(snapshot_info.get("snapshot_id") or ""),
                snapshot_time=snapshot_time,
            )
            return {
                "status": "cache_hit",
                "requested_count": len(normalized_codes),
                "cached_before": cached_before,
                "fetched_count": 0,
                "failed_count": 0,
                "cached_after": cached_before,
                "intraday_saved_count": intraday_saved.get("saved_count", 0),
                **snapshot_info,
            }

        response = self.get_realtime_quotes(missing_codes, force_refresh=force_refresh)
        fetched_count = len(response.get("items", []))
        failed_count = len(response.get("failed_codes", []))
        cached_after_codes = _get_realtime_quote_cache_codes()
        cached_after = sum(1 for code in normalized_codes if code in cached_after_codes)
        snapshot_items = [
            payload
            for code in normalized_codes
            for payload in [_get_cached_quote_payload(code)]
            if payload is not None
        ]
        snapshot_info = _replace_realtime_quote_snapshot(
            requested_codes=normalized_codes,
            items=snapshot_items,
            failed_codes=response.get("failed_codes", []),
            snapshot_time=snapshot_time,
        )
        intraday_saved = self.repo.db.save_intraday_quote_samples(
            snapshot_items,
            snapshot_id=str(snapshot_info.get("snapshot_id") or ""),
            snapshot_time=snapshot_time,
        )
        try:
            self.repo.db.purge_intraday_minutes_older_than(3)
        except Exception as exc:
            logger.debug("清理分钟热表旧数据失败: %s", exc)
        return {
            "status": "refreshed" if fetched_count > 0 else "miss",
            "requested_count": len(normalized_codes),
            "missing_before": len(missing_codes),
            "cached_before": cached_before,
            "fetched_count": fetched_count,
            "failed_count": failed_count,
            "cached_after": cached_after,
            "update_time": response.get("update_time"),
            "intraday_saved_count": intraday_saved.get("saved_count", 0),
            **snapshot_info,
        }

    def warm_all_a_share_realtime_quotes(self, *, force_refresh: bool = False) -> Dict[str, Any]:
        """Preload realtime quote payloads for all active A-share stocks."""
        from src.data.stock_index_loader import get_all_a_share_stock_codes

        codes = get_all_a_share_stock_codes()
        if not codes:
            return {
                "status": "skipped",
                "reason": "empty_a_share_index",
                "requested_count": 0,
                "cached_before": 0,
                "fetched_count": 0,
                "failed_count": 0,
            }
        return self.warm_realtime_quotes(codes, force_refresh=force_refresh)

    @staticmethod
    def _parse_optional_date(value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
        except ValueError:
            return None

    @staticmethod
    def _empty_indicator_metrics_payload(
        stock_code: str,
        stock_name: Optional[str],
        *,
        chip_payload: Optional[Dict[str, Any]] = None,
        source_chain: Optional[List[Dict[str, Any]]] = None,
        errors: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        return {
            "stock_code": stock_code,
            "stock_name": stock_name,
            "chip_distribution": chip_payload,
            "capital_flow": None,
            "major_holders": [],
            "major_holder_status": "not_supported",
            "source_chain": source_chain or [],
            "errors": errors or [],
            "update_time": datetime.now().isoformat(),
        }

    def _load_chip_distribution_from_db(
        self,
        stock_code: str,
        *,
        as_of: Optional[date] = None,
        days: int = 365,
    ) -> Optional[Dict[str, Any]]:
        end_date = as_of or datetime.now().date()
        requested_days = max(1, min(int(days or 365), 730))
        start_date = end_date - timedelta(days=int(requested_days * 1.8) + 10)

        for candidate in self._daily_cache_code_candidates(stock_code):
            rows = self.repo.db.get_chip_daily_range(candidate, start_date, end_date)
            if not rows:
                continue
            latest = rows[-1]
            payload = dict(latest)
            payload["snapshots"] = rows
            return payload
        return None

    def _save_chip_distribution_payload(self, stock_code: str, chip_payload: Optional[Dict[str, Any]]) -> None:
        if not chip_payload:
            return
        snapshots = chip_payload.get("snapshots") if isinstance(chip_payload, dict) else None
        if not snapshots and chip_payload.get("date"):
            snapshots = [chip_payload]
        if not isinstance(snapshots, list) or not snapshots:
            return
        try:
            from data_provider.base import normalize_stock_code

            normalized_code = normalize_stock_code(stock_code)
            saved_count = self.repo.db.save_chip_daily_snapshots(
                normalized_code,
                [item for item in snapshots if isinstance(item, dict)],
                data_source=chip_payload.get("source"),
            )
            if saved_count:
                logger.info("已写入 %s 筹码峰日缓存 %s 条", normalized_code, saved_count)
        except Exception as exc:
            logger.debug("写入 %s 筹码峰日缓存失败: %s", stock_code, exc)

    def _sync_chip_daily_cache_from_history(
        self,
        stock_code: str,
        history_df,
        *,
        data_source: Optional[str] = None,
    ) -> None:
        """Persist chip snapshots from the same daily frame just written to stock_daily."""
        try:
            from src.services.chip_daily_sync import sync_chip_daily_from_history

            saved_count = sync_chip_daily_from_history(
                self.repo.db,
                stock_code,
                history_df,
                data_source=data_source,
            )
            if saved_count:
                logger.info("已同步 %s 筹码峰日缓存 %s 条", stock_code, saved_count)
        except Exception as exc:
            logger.debug("同步 %s 筹码峰日缓存失败: %s", stock_code, exc)

    def get_indicator_metrics(
        self,
        stock_code: str,
        data_policy: str = "default",
        trade_date: Optional[str] = None,
        days: int = 365,
    ) -> Dict[str, Any]:
        """
        获取指标分析扩展数据：筹码分布与主力/机构持仓名称。

        这些数据源均为可选上下文，接口保持 fail-open，失败时返回空结构，
        不影响 K 线与实时行情展示。
        """
        normalized_policy = str(data_policy or "default").strip().lower()
        stock_name = None
        chip_payload = None
        capital_flow_payload = None
        major_holder_status = "not_supported"
        major_holders: List[Dict[str, Any]] = []
        source_chain: List[Dict[str, Any]] = []
        errors: List[str] = []

        try:
            stock_name = self._get_local_stock_name(stock_code)
        except Exception as e:
            logger.debug(f"获取 {stock_code} 本地股票名称失败: {e}")

        if normalized_policy in {"cache_only", "db_only", "snapshot_only"}:
            as_of = self._parse_optional_date(trade_date)
            chip_payload = self._load_chip_distribution_from_db(stock_code, as_of=as_of, days=days)
            source_chain = [{
                "provider": "stock_chip_daily",
                "result": "ok" if chip_payload else "miss",
                "duration_ms": 0,
            }]
            errors = [] if chip_payload else ["chip_daily_miss"]
            return self._empty_indicator_metrics_payload(
                stock_code,
                stock_name,
                chip_payload=chip_payload,
                source_chain=source_chain,
                errors=errors,
            )

        from data_provider.base import DataFetcherManager

        manager = DataFetcherManager()

        try:
            stock_name = stock_name or manager.get_stock_name(stock_code, allow_realtime=False)
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
                self._save_chip_distribution_payload(stock_code, chip_payload)
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
    def _build_kline_payload(df, period: str, stock_code: Optional[str] = None) -> List[Dict[str, Any]]:
        data: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            close = _to_optional_float(row.get("close")) or 0.0
            volume = _to_optional_float(row.get("volume"))
            if volume is not None:
                volume = _normalize_cn_volume_to_lots(
                    stock_code or row.get("code"),
                    volume,
                    row.get("amount"),
                    close,
                )
            data.append({
                "date": StockService._format_kline_date(row.get("date"), period),
                "open": _to_optional_float(row.get("open")) or 0.0,
                "high": _to_optional_float(row.get("high")) or 0.0,
                "low": _to_optional_float(row.get("low")) or 0.0,
                "close": close,
                "volume": volume,
                "after_hours_volume": _to_optional_float(row.get("after_hours_volume")),
                "amount": _to_optional_float(row.get("amount")),
                "change_percent": _to_optional_float(row.get("pct_chg")) or _to_optional_float(row.get("change_percent")),
                "volume_ratio": _to_optional_float(row.get("volume_ratio")),
                "turnover_rate": _to_optional_float(row.get("turnover_rate")),
                "pe_ratio": _to_optional_float(row.get("pe_ratio")),
                "total_mv": _to_optional_float(row.get("total_mv")),
                "circ_mv": _to_optional_float(row.get("circ_mv")),
                "total_shares": _to_optional_float(row.get("total_shares")),
                "float_shares": _to_optional_float(row.get("float_shares")),
                "data_source": _to_optional_string(row.get("data_source")),
                "snapshot_id": _to_optional_string(row.get("snapshot_id")),
                "snapshot_time": _to_optional_string(row.get("snapshot_time")),
            })
        return data

    @staticmethod
    def _normalize_daily_cache_date(value: Any) -> Optional[date]:
        if isinstance(value, datetime):
            return value.date()
        if isinstance(value, date):
            return value
        if isinstance(value, str) and len(value) >= 10:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
        return None

    @staticmethod
    def _resolve_daily_cache_target_date(stock_code: str) -> date:
        try:
            market = trading_calendar.get_market_for_stock(stock_code)
            return trading_calendar.get_effective_trading_date(market)
        except Exception as calendar_error:
            logger.debug("解析 %s 最新有效交易日失败，按昨天校验日线缓存: %s", stock_code, calendar_error)
            return datetime.now().date() - timedelta(days=1)

    def _get_daily_cache_latest_date(self, stock_code: str) -> Optional[date]:
        latest_date: Optional[date] = None
        for candidate in self._daily_cache_code_candidates(stock_code):
            for bar in self.repo.get_latest(candidate, days=1):
                bar_date = self._normalize_daily_cache_date(getattr(bar, "date", None))
                if bar_date and (latest_date is None or bar_date > latest_date):
                    latest_date = bar_date
        return latest_date

    @staticmethod
    def _daily_cache_code_candidates(stock_code: str) -> List[str]:
        from data_provider.base import normalize_stock_code

        raw_code = str(stock_code or "").strip()
        normalized_code = normalize_stock_code(raw_code) if raw_code else ""
        candidates = [raw_code, normalized_code]
        upper_code = raw_code.upper()
        has_exchange_hint = (
            upper_code.startswith(("SH", "SZ", "BJ"))
            or upper_code.endswith((".SH", ".SS", ".SZ", ".BJ"))
        )
        if normalized_code.isdigit() and len(normalized_code) == 6:
            hinted_suffix = None
            if upper_code.startswith(("SH", "SZ", "BJ")):
                hinted_suffix = upper_code[:2]
            elif upper_code.endswith((".SH", ".SS")):
                hinted_suffix = "SH"
            elif upper_code.endswith(".SZ"):
                hinted_suffix = "SZ"
            elif upper_code.endswith(".BJ"):
                hinted_suffix = "BJ"
            if hinted_suffix:
                candidates.append(f"{normalized_code}.{hinted_suffix}")
            elif not has_exchange_hint:
                if normalized_code.startswith(("5", "6")):
                    candidates.append(f"{normalized_code}.SH")
                elif normalized_code.startswith(("0", "2", "3", "15", "16", "18")):
                    candidates.append(f"{normalized_code}.SZ")
                elif normalized_code.startswith(("8", "9")):
                    candidates.append(f"{normalized_code}.BJ")
        return list(dict.fromkeys(code for code in candidates if code))

    def _load_daily_history_from_db(
        self,
        stock_code: str,
        days: int,
        *,
        require_fresh: bool = True,
        end_date: Optional[date] = None,
        allow_partial: bool = False,
    ):
        import pandas as pd

        requested_days = max(1, int(days or 1))
        target_date = end_date or (
            self._resolve_daily_cache_target_date(stock_code) if require_fresh else None
        )
        query_end_date = target_date or datetime.now().date()
        start_date = query_end_date - timedelta(days=int(requested_days * 1.8) + 10)
        required_rows = requested_days if require_fresh else 1
        candidates = self._daily_cache_code_candidates(stock_code)

        if allow_partial:
            merged_bars: Dict[date, Any] = {}
            for candidate in candidates:
                for bar in self.repo.get_range(candidate, start_date, query_end_date):
                    bar_date = self._normalize_daily_cache_date(getattr(bar, "date", None))
                    if not bar_date:
                        continue
                    if bar_date not in merged_bars or candidate == stock_code:
                        merged_bars[bar_date] = bar
            if merged_bars:
                selected_bars = [
                    merged_bars[bar_date]
                    for bar_date in sorted(merged_bars.keys())[-requested_days:]
                ]
                latest_cached_date = (
                    self._normalize_daily_cache_date(getattr(selected_bars[-1], "date", None))
                    if selected_bars else None
                )
                if require_fresh and target_date and latest_cached_date and latest_cached_date < target_date:
                    logger.info(
                        "日线缓存过期，刷新 %s: cache_latest=%s target=%s",
                        stock_code,
                        latest_cached_date.isoformat(),
                        target_date.isoformat(),
                    )
                    return None
                df = pd.DataFrame([bar.to_dict() for bar in selected_bars])
                if not df.empty:
                    return df, "db_cache"
            return None

        for candidate in candidates:
            bars = self.repo.get_range(candidate, start_date, query_end_date)
            if len(bars) < required_rows:
                continue
            selected_bars = bars[-requested_days:]
            latest_cached_date = (
                self._normalize_daily_cache_date(getattr(selected_bars[-1], "date", None))
                if selected_bars else None
            )
            if require_fresh and target_date and latest_cached_date and latest_cached_date < target_date:
                logger.info(
                    "日线缓存过期，刷新 %s: cache_latest=%s target=%s",
                    stock_code,
                    latest_cached_date.isoformat(),
                    target_date.isoformat(),
                )
                continue
            df = pd.DataFrame([bar.to_dict() for bar in selected_bars])
            if not df.empty:
                return df, "db_cache"

        return None

    @staticmethod
    def _history_frame_row_count(cached_history) -> int:
        if cached_history is None:
            return 0
        df, _source = cached_history
        try:
            return len(df.index)
        except Exception:
            return 0

    @staticmethod
    def _filter_intraday_frame_to_trade_date(df, trade_date: date):
        if df is None or df.empty or "date" not in df.columns:
            return df

        import pandas as pd

        parsed_dates = pd.to_datetime(df["date"], errors="coerce").dt.date
        return df.loc[parsed_dates == trade_date].reset_index(drop=True)

    @staticmethod
    def _resolve_realtime_daily_date(stock_code: str) -> Optional[date]:
        try:
            market = trading_calendar.get_market_for_stock(stock_code)
            market_today = trading_calendar.get_market_now(market).date()
            if market and not trading_calendar.is_market_open(market, market_today):
                return None
            return market_today
        except Exception as calendar_error:
            logger.debug("解析 %s 当天实时渲染日期失败，按本地自然日处理: %s", stock_code, calendar_error)
            return datetime.now().date()

    @staticmethod
    def _resolve_intraday_cache_target_date(stock_code: str) -> date:
        try:
            market = trading_calendar.get_market_for_stock(stock_code)
            market_today = trading_calendar.get_market_now(market).date()
            if market in {"cn", "hk"} and not trading_calendar.is_market_open(market, market_today):
                return trading_calendar.get_effective_trading_date(market)
            return market_today
        except Exception as calendar_error:
            logger.debug("解析 %s 分钟热表交易日失败，按本地日期读取: %s", stock_code, calendar_error)
            return datetime.now().date()

    @staticmethod
    def _is_before_intraday_session_start(stock_code: str) -> bool:
        try:
            market = trading_calendar.get_market_for_stock(stock_code)
            sessions = getattr(trading_calendar, "MARKET_LIVE_SESSIONS", {}).get(market or "")
            if not market or not sessions:
                return False

            market_now = trading_calendar.get_market_now(market)
            if not trading_calendar.is_market_open(market, market_now.date()):
                return False

            first_start = min(start for start, _end in sessions)
            return market_now.time() < first_start
        except Exception as calendar_error:
            logger.debug("解析 %s 分钟开盘时间失败，按已开盘处理: %s", stock_code, calendar_error)
            return False

    @staticmethod
    def _quote_has_realtime_daily_signal(quote_payload: Optional[Dict[str, Any]]) -> bool:
        if not quote_payload:
            return False
        price = _to_optional_float(quote_payload.get("current_price"))
        if price is None or price <= 0:
            return False
        for key in ("open", "high", "low", "volume", "amount", "change", "change_percent"):
            value = _to_optional_float(quote_payload.get(key))
            if value is not None:
                return True
        return False

    @staticmethod
    def _quote_has_realtime_price(quote_payload: Optional[Dict[str, Any]]) -> bool:
        if not quote_payload:
            return False
        price = _to_optional_float(quote_payload.get("current_price"))
        return price is not None and price > 0

    @staticmethod
    def _normalize_quote_payload_units(stock_code: str, quote_payload: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if quote_payload is None:
            return None
        payload = dict(quote_payload)
        price = _to_optional_float(payload.get("current_price"))
        payload["volume"] = _normalize_cn_volume_to_lots(
            stock_code,
            payload.get("volume"),
            payload.get("amount"),
            price,
        )
        return payload

    @staticmethod
    def _build_realtime_daily_row(
        quote_payload: Dict[str, Any],
        stock_code: str,
        realtime_date: date,
        previous_close: Optional[float],
    ) -> Dict[str, Any]:
        price = _to_optional_float(quote_payload.get("current_price")) or 0.0
        open_price = (
            _to_optional_float(quote_payload.get("open"))
            or _to_optional_float(quote_payload.get("prev_close"))
            or previous_close
            or price
        )
        high_price = _to_optional_float(quote_payload.get("high")) or price
        low_price = _to_optional_float(quote_payload.get("low")) or price
        high_price = max(high_price, open_price, price)
        low_price = min(low_price, open_price, price)
        change_percent = _to_optional_float(quote_payload.get("change_percent"))
        if change_percent is None and previous_close and previous_close > 0:
            change_percent = (price - previous_close) / previous_close * 100

        return {
            "code": stock_code,
            "date": realtime_date,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": price,
            "volume": _normalize_cn_volume_to_lots(
                stock_code,
                quote_payload.get("volume"),
                quote_payload.get("amount"),
                price,
            ) or 0,
            "after_hours_volume": _to_optional_float(quote_payload.get("after_hours_volume")),
            "amount": _to_optional_float(quote_payload.get("amount")) or 0,
            "pct_chg": change_percent if change_percent is not None else 0,
            "turnover_rate": _to_optional_float(quote_payload.get("turnover_rate")),
        }

    def _augment_daily_history_with_realtime(
        self,
        df,
        stock_code: str,
        *,
        data_policy: str = "default",
    ) -> Tuple[Any, Optional[Dict[str, Any]]]:
        if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
            return df, None

        realtime_date = self._resolve_realtime_daily_date(stock_code)
        if realtime_date is None:
            return df, None

        quote_payload = self.get_realtime_quote(stock_code, data_policy=data_policy)
        if not self._quote_has_realtime_daily_signal(quote_payload):
            return df, quote_payload
        quote_payload = self._normalize_quote_payload_units(stock_code, quote_payload)

        import pandas as pd

        df = df.copy()
        last_value = df["date"].max()
        last_date = self._normalize_daily_cache_date(last_value)
        if last_date and last_date > realtime_date:
            return df, quote_payload

        previous_close = _to_optional_float(df.iloc[-1].get("close")) if len(df.index) > 0 else None
        realtime_row = self._build_realtime_daily_row(
            quote_payload or {},
            stock_code,
            realtime_date,
            previous_close,
        )

        if last_date and last_date == realtime_date:
            idx = df.index[-1]
            for key, value in realtime_row.items():
                if key == "code" or value is None:
                    continue
                df.loc[idx, key] = value
            return df, quote_payload

        df = pd.concat([df, pd.DataFrame([realtime_row])], ignore_index=True)
        return df, quote_payload

    def _augment_intraday_history_with_realtime(
        self,
        df,
        stock_code: str,
        *,
        data_policy: str = "default",
    ) -> Tuple[Any, Optional[Dict[str, Any]]]:
        if df is None or df.empty or "date" not in df.columns or "close" not in df.columns:
            return df, None

        realtime_date = self._resolve_realtime_daily_date(stock_code)
        if realtime_date is None:
            return df, None

        latest_date = self._normalize_daily_cache_date(df.iloc[-1].get("date"))
        if latest_date != realtime_date:
            return df, None

        quote_payload = self.get_realtime_quote(stock_code, data_policy=data_policy)
        if not self._quote_has_realtime_price(quote_payload):
            return df, quote_payload

        price = _to_optional_float((quote_payload or {}).get("current_price")) or 0.0
        df = df.copy()
        idx = df.index[-1]
        open_price = _to_optional_float(df.loc[idx].get("open")) or price
        high_price = _to_optional_float(df.loc[idx].get("high")) or price
        low_price = _to_optional_float(df.loc[idx].get("low")) or price

        df.loc[idx, "close"] = price
        df.loc[idx, "high"] = max(high_price, open_price, price)
        df.loc[idx, "low"] = min(low_price, open_price, price)
        return df, quote_payload

    @staticmethod
    def _get_local_stock_name(stock_code: str) -> Optional[str]:
        try:
            from data_provider.base import (
                STOCK_NAME_MAP,
                get_index_stock_name,
                is_meaningful_stock_name,
                normalize_stock_code,
            )
        except Exception:
            return None

        normalized_code = normalize_stock_code(stock_code)
        for name in (STOCK_NAME_MAP.get(normalized_code), get_index_stock_name(normalized_code)):
            if is_meaningful_stock_name(name, normalized_code):
                return name
        return None
    
    def get_history_data(
        self,
        stock_code: str,
        period: str = "daily",
        days: int = 30,
        data_policy: str = "default",
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
            manager = None
            stock_name = None
            normalized_policy = str(data_policy or "default").strip().lower()
            cache_only = normalized_policy in {"cache_only", "snapshot_only", "db_only"}
            db_only = normalized_policy == "db_only"
            if normalized_period == "daily":
                cached_history = self._load_daily_history_from_db(
                    stock_code,
                    days,
                    require_fresh=not db_only,
                    allow_partial=db_only,
                )
                if cached_history is not None:
                    df, source = cached_history
                    stock_name = self._get_local_stock_name(stock_code)
                elif cache_only:
                    logger.info("日线缓存未命中，%s 跳过远程获取: %s", normalized_policy, stock_code)
                    return {
                        "stock_code": stock_code,
                        "period": normalized_period,
                        "data": [],
                        "data_source": "daily_cache_miss",
                    }
                else:
                    target_date = self._resolve_daily_cache_target_date(stock_code)
                    latest_cached_date = self._get_daily_cache_latest_date(stock_code)
                    stale_cached_history = self._load_daily_history_from_db(
                        stock_code,
                        days,
                        require_fresh=False,
                        end_date=target_date,
                    )
                    manager = DataFetcherManager()
                    loaded_from_stale_cache = False
                    try:
                        stale_row_count = self._history_frame_row_count(stale_cached_history)
                        if (
                            latest_cached_date
                            and latest_cached_date < target_date
                            and stale_row_count >= max(1, int(days or 1))
                        ):
                            start_date = latest_cached_date + timedelta(days=1)
                            logger.info(
                                "补齐 %s 日线缓存: start=%s end=%s latest_cache=%s",
                                stock_code,
                                start_date.isoformat(),
                                target_date.isoformat(),
                                latest_cached_date.isoformat(),
                            )
                            df, source = manager.get_daily_data(
                                stock_code,
                                start_date=start_date.isoformat(),
                                end_date=target_date.isoformat(),
                                days=days,
                            )
                        else:
                            logger.info(
                                "刷新 %s 日线缓存窗口: rows=%s latest_cache=%s target=%s days=%s",
                                stock_code,
                                stale_row_count,
                                latest_cached_date.isoformat() if latest_cached_date else None,
                                target_date.isoformat(),
                                days,
                            )
                            df, source = manager.get_daily_data(
                                stock_code,
                                end_date=target_date.isoformat(),
                                days=days,
                            )
                    except Exception as fetch_error:
                        if stale_cached_history is None:
                            raise
                        logger.warning(
                            "刷新 %s 日线失败，暂用过期 DB 缓存兜底: %s",
                            stock_code,
                            fetch_error,
                        )
                        df, source = stale_cached_history
                        stock_name = self._get_local_stock_name(stock_code)
                        loaded_from_stale_cache = True
                    if df is not None and not df.empty:
                        if not loaded_from_stale_cache:
                            try:
                                df = enrich_daily_history_with_quote_fields(
                                    df,
                                    stock_code,
                                    quote_loader=lambda code: manager.get_realtime_quote(
                                        code,
                                        log_final_failure=False,
                                    ),
                                )
                                self.repo.save_dataframe(df, stock_code, source)
                                self._sync_chip_daily_cache_from_history(
                                    stock_code,
                                    df,
                                    data_source=source,
                                )
                                refreshed_cache = self._load_daily_history_from_db(stock_code, days)
                                if refreshed_cache is None:
                                    refreshed_cache = self._load_daily_history_from_db(
                                        stock_code,
                                        days,
                                        require_fresh=False,
                                        end_date=target_date,
                                    )
                                if refreshed_cache is not None:
                                    df, source = refreshed_cache
                                    stock_name = self._get_local_stock_name(stock_code)
                            except Exception as save_error:
                                logger.debug("保存 %s 日线缓存失败: %s", stock_code, save_error)
                    elif stale_cached_history is not None:
                        logger.warning("刷新 %s 日线返回空结果，暂用过期 DB 缓存兜底", stock_code)
                        df, source = stale_cached_history
                        stock_name = self._get_local_stock_name(stock_code)
            else:
                intraday_days = max(1, min(int(days or 1), 30))
                intraday_trade_date = self._resolve_intraday_cache_target_date(stock_code)
                if normalized_period == "1m" and intraday_days == 1 and self._is_before_intraday_session_start(stock_code):
                    logger.info("开盘前跳过当天分时热表与远程回源: %s period=%s", stock_code, normalized_period)
                    return {"stock_code": stock_code, "period": normalized_period, "data": [], "data_source": "intraday_hot_table_miss"}
                hot_df = self.repo.db.get_intraday_minute_data(
                    stock_code,
                    days=intraday_days,
                    period=normalized_period,
                    trade_date=intraday_trade_date,
                )
                if hot_df is not None and not hot_df.empty:
                    df, source = hot_df, "intraday_hot_table"
                    stock_name = self._get_local_stock_name(stock_code)
                elif cache_only:
                    logger.info("分钟热表未命中，cache_only 跳过远程获取: %s period=%s", stock_code, normalized_period)
                    return {"stock_code": stock_code, "period": normalized_period, "data": [], "data_source": "intraday_hot_table_miss"}
                else:
                    manager = DataFetcherManager()
                    intraday_kwargs: Dict[str, Any] = {
                        "period": normalized_period,
                        "days": intraday_days,
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
                    if intraday_days == 1 and df is not None and not df.empty:
                        remote_row_count = len(df.index)
                        df = self._filter_intraday_frame_to_trade_date(df, intraday_trade_date)
                        if df is None or df.empty:
                            logger.info(
                                "忽略 %s 非目标交易日分钟数据: target=%s period=%s source=%s remote_rows=%s",
                                stock_code,
                                intraday_trade_date.isoformat(),
                                normalized_period,
                                source,
                                remote_row_count,
                            )
                            source = "intraday_hot_table_miss"
                    if df is not None and not df.empty:
                        try:
                            self.repo.db.save_intraday_minute_dataframe(
                                df,
                                stock_code,
                                data_source=source,
                                snapshot_time=datetime.now(),
                            )
                            refreshed_hot_df = self.repo.db.get_intraday_minute_data(
                                stock_code,
                                days=intraday_days,
                                period=normalized_period,
                                trade_date=intraday_trade_date,
                            )
                            if refreshed_hot_df is not None and not refreshed_hot_df.empty:
                                df, source = refreshed_hot_df, "intraday_hot_table"
                        except Exception as save_error:
                            logger.debug("保存 %s 分钟热表失败: %s", stock_code, save_error)
            
            if df is None or df.empty:
                logger.warning(f"获取 {stock_code} 历史数据失败")
                payload = {"stock_code": stock_code, "period": normalized_period, "data": []}
                if normalized_period != "daily":
                    payload["data_source"] = "intraday_hot_table_miss"
                return payload
            
            # 获取股票名称
            if stock_name is None and manager is not None:
                stock_name = manager.get_stock_name(stock_code)

            quote_payload = None
            if normalized_period == "daily":
                if not db_only:
                    df, quote_payload = self._augment_daily_history_with_realtime(
                        df,
                        stock_code,
                        data_policy="snapshot_only" if cache_only else "default",
                    )
                if stock_name is None and quote_payload:
                    stock_name = quote_payload.get("stock_name")
            else:
                if not db_only:
                    df, quote_payload = self._augment_intraday_history_with_realtime(
                        df,
                        stock_code,
                        data_policy="snapshot_only" if cache_only else "default",
                    )
                if stock_name is None and quote_payload:
                    stock_name = quote_payload.get("stock_name")
            
            # 转换为响应格式
            data = self._build_kline_payload(df, normalized_period, stock_code)
            
            return {
                "stock_code": stock_code,
                "stock_name": stock_name,
                "period": normalized_period,
                "data": data,
                "data_source": source,
                **_get_realtime_quote_snapshot_info(),
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
