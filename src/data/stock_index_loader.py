# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import RLock
from typing import Any, Dict, Iterable

from src.data.stock_mapping import is_meaningful_stock_name

logger = logging.getLogger(__name__)

_STOCK_INDEX_FILENAME = "stocks.index.json"
_STOCK_INDEX_CACHE: Dict[str, str] | None = None
_ALL_A_SHARE_CODES_CACHE: list[str] | None = None
_STOCK_INDEX_CACHE_LOCK = RLock()


def get_stock_index_candidate_paths() -> tuple[Path, ...]:
    """Return the supported locations for the generated stock index."""
    repo_root = Path(__file__).resolve().parents[2]
    return (
        repo_root / "apps" / "dsa-web" / "public" / _STOCK_INDEX_FILENAME,
        repo_root / "static" / _STOCK_INDEX_FILENAME,
    )


def _add_lookup_key(keys: set[str], value: str) -> None:
    candidate = str(value or "").strip()
    if not candidate:
        return
    keys.add(candidate)
    keys.add(candidate.upper())


def _build_lookup_keys(canonical_code: str, display_code: str) -> Iterable[str]:
    keys: set[str] = set()
    _add_lookup_key(keys, canonical_code)
    _add_lookup_key(keys, display_code)

    canonical_upper = str(canonical_code or "").strip().upper()
    display_upper = str(display_code or "").strip().upper()

    if "." in canonical_upper:
        base, suffix = canonical_upper.rsplit(".", 1)
        if suffix in {"SH", "SZ", "SS", "BJ"} and base.isdigit():
            _add_lookup_key(keys, base)
        elif suffix == "HK" and base.isdigit() and 1 <= len(base) <= 5:
            digits = base.zfill(5)
            _add_lookup_key(keys, digits)
            _add_lookup_key(keys, f"HK{digits}")

    for candidate in (canonical_upper, display_upper):
        if candidate.startswith("HK"):
            digits = candidate[2:]
            if digits.isdigit() and 1 <= len(digits) <= 5:
                digits = digits.zfill(5)
                _add_lookup_key(keys, digits)
                _add_lookup_key(keys, f"HK{digits}")

    return keys


def _load_stock_index_file(index_path: Path) -> Dict[str, str]:
    raw_items = _read_stock_index_items(index_path)

    stock_name_map: Dict[str, str] = {}
    for item in raw_items:
        if not isinstance(item, list) or len(item) < 3:
            continue

        canonical_code, display_code, name_zh = item[0], item[1], item[2]
        if not is_meaningful_stock_name(name_zh, str(display_code or canonical_code or "")):
            continue

        for key in _build_lookup_keys(str(canonical_code or ""), str(display_code or "")):
            stock_name_map[key] = str(name_zh).strip()

    return stock_name_map


def _read_stock_index_items(index_path: Path) -> list[Any]:
    with index_path.open("r", encoding="utf-8") as fh:
        raw_items = json.load(fh)

    if not isinstance(raw_items, list):
        raise ValueError(
            f"Unexpected {_STOCK_INDEX_FILENAME} payload type: {type(raw_items).__name__}"
        )
    return raw_items


def _get_index_field(item: Any, tuple_index: int, object_key: str) -> Any:
    if isinstance(item, list):
        return item[tuple_index] if len(item) > tuple_index else None
    if isinstance(item, dict):
        return item.get(object_key)
    return None


def _normalize_a_share_display_code(value: Any) -> str | None:
    code = str(value or "").strip().upper()
    if not code:
        return None
    if "." in code:
        code = code.split(".", 1)[0]
    if code.startswith(("SH", "SZ", "BJ")):
        code = code[2:]
    if not (code.isdigit() and len(code) == 6):
        return None
    return code


def _load_all_a_share_codes(index_path: Path) -> list[str]:
    raw_items = _read_stock_index_items(index_path)
    seen: set[str] = set()
    codes: list[str] = []

    for item in raw_items:
        market = str(_get_index_field(item, 6, "market") or "").upper()
        asset_type = str(_get_index_field(item, 7, "assetType") or _get_index_field(item, 7, "asset_type") or "")
        active = _get_index_field(item, 8, "active")
        if active is False or str(active).lower() == "false":
            continue
        if market not in {"CN", "BSE"} or asset_type != "stock":
            continue

        display_code = _get_index_field(item, 1, "displayCode")
        canonical_code = _get_index_field(item, 0, "canonicalCode")
        code = _normalize_a_share_display_code(display_code) or _normalize_a_share_display_code(canonical_code)
        if code and code not in seen:
            seen.add(code)
            codes.append(code)

    return codes


def get_stock_name_index_map() -> Dict[str, str]:
    """Lazily load and cache the generated stock-name index."""
    global _STOCK_INDEX_CACHE

    if _STOCK_INDEX_CACHE is not None:
        return _STOCK_INDEX_CACHE

    with _STOCK_INDEX_CACHE_LOCK:
        if _STOCK_INDEX_CACHE is not None:
            return _STOCK_INDEX_CACHE

        for candidate_path in get_stock_index_candidate_paths():
            if not candidate_path.is_file():
                continue

            try:
                _STOCK_INDEX_CACHE = _load_stock_index_file(candidate_path)
                logger.debug(
                    "[股票名称] 已加载前端股票索引映射: %s (%d 条)",
                    candidate_path,
                    len(_STOCK_INDEX_CACHE),
                )
                return _STOCK_INDEX_CACHE
            except (OSError, TypeError, ValueError) as exc:
                logger.debug("[股票名称] 读取股票索引失败 %s: %s", candidate_path, exc)

        _STOCK_INDEX_CACHE = {}
        return _STOCK_INDEX_CACHE


def get_index_stock_name(stock_code: str) -> str | None:
    """Resolve a stock name from the generated frontend stock index."""
    code = str(stock_code or "").strip()
    if not code:
        return None

    stock_name_map = get_stock_name_index_map()
    for key in _build_lookup_keys(code, code):
        name = stock_name_map.get(key)
        if is_meaningful_stock_name(name, code):
            return name

    return None


def get_all_a_share_stock_codes() -> list[str]:
    """Return active A-share stock codes from the generated stock index."""
    global _ALL_A_SHARE_CODES_CACHE

    if _ALL_A_SHARE_CODES_CACHE is not None:
        return list(_ALL_A_SHARE_CODES_CACHE)

    with _STOCK_INDEX_CACHE_LOCK:
        if _ALL_A_SHARE_CODES_CACHE is not None:
            return list(_ALL_A_SHARE_CODES_CACHE)

        for candidate_path in get_stock_index_candidate_paths():
            if not candidate_path.is_file():
                continue

            try:
                _ALL_A_SHARE_CODES_CACHE = _load_all_a_share_codes(candidate_path)
                logger.debug(
                    "[股票索引] 已加载 A 股代码列表: %s (%d 只)",
                    candidate_path,
                    len(_ALL_A_SHARE_CODES_CACHE),
                )
                return list(_ALL_A_SHARE_CODES_CACHE)
            except (OSError, TypeError, ValueError) as exc:
                logger.debug("[股票索引] 读取 A 股代码列表失败 %s: %s", candidate_path, exc)

        _ALL_A_SHARE_CODES_CACHE = []
        return []


def _clear_stock_index_cache_for_tests() -> None:
    global _STOCK_INDEX_CACHE, _ALL_A_SHARE_CODES_CACHE
    with _STOCK_INDEX_CACHE_LOCK:
        _STOCK_INDEX_CACHE = None
        _ALL_A_SHARE_CODES_CACHE = None
