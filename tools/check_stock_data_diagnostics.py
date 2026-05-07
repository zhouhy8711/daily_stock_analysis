#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTTP smoke tool for stock data diagnostics."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Dict, List
from urllib import error, parse, request


DEFAULT_BASE_URL = "http://127.0.0.1:8000"


def _parse_header(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise argparse.ArgumentTypeError("header must use 'Name: Value' format")
    name, raw = value.split(":", 1)
    name = name.strip()
    if not name:
        raise argparse.ArgumentTypeError("header name cannot be empty")
    return name, raw.strip()


def _build_url(args: argparse.Namespace) -> str:
    base = args.base_url.rstrip("/")
    params = {
        "scope": args.scope,
        "limit": str(args.limit),
        "offset": str(args.offset),
        "sort": args.sort,
    }
    if args.trade_date:
        params["trade_date"] = args.trade_date
    if args.q:
        params["q"] = args.q
    return f"{base}/api/v1/diagnostics/stock-data?{parse.urlencode(params)}"


def _fetch_json(url: str, headers: List[tuple[str, str]], timeout: float) -> Dict[str, Any]:
    req = request.Request(url, headers={name: value for name, value in headers})
    try:
        with request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8")
            status = getattr(response, "status", 200)
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Request failed: {exc}") from exc

    if status < 200 or status >= 300:
        raise RuntimeError(f"HTTP {status}: {body}")
    try:
        data = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid JSON response: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("Diagnostics response must be a JSON object")
    return data


def _require_shape(data: Dict[str, Any]) -> None:
    for key in ("summary", "items", "total", "limit", "offset", "trade_date"):
        if key not in data:
            raise RuntimeError(f"Diagnostics response missing key: {key}")
    summary = data.get("summary")
    if not isinstance(summary, dict):
        raise RuntimeError("Diagnostics summary must be an object")
    for key in ("history", "intraday", "quote"):
        if key not in summary:
            raise RuntimeError(f"Diagnostics summary missing key: {key}")


def _print_summary(data: Dict[str, Any]) -> None:
    summary = data["summary"]
    history = summary["history"]
    intraday = summary["intraday"]
    quote = summary["quote"]
    print("Stock Data Diagnostics")
    print(f"  generated_at : {data.get('generated_at')}")
    print(f"  trade_date   : {data.get('trade_date')}")
    print(f"  scope        : {data.get('scope')}")
    print(f"  total        : {data.get('total')} (limit={data.get('limit')} offset={data.get('offset')})")
    print("")
    print("History DB")
    print(f"  stocks       : {history.get('stock_count')}")
    print(f"  rows         : {history.get('row_count')}")
    print(f"  range        : {history.get('first_date') or '--'} -> {history.get('last_date') or '--'}")
    print(f"  missing      : {history.get('missing_count')}")
    print("")
    print("Intraday Hot Table")
    print(f"  stocks       : {intraday.get('stock_count')}")
    print(f"  rows         : {intraday.get('row_count')}")
    print(f"  range        : {intraday.get('first_minute') or '--'} -> {intraday.get('last_minute') or '--'}")
    print(f"  missing      : {intraday.get('missing_count')}")
    print("")
    print("Quote Cache")
    print(f"  snapshot     : {quote.get('snapshot_id') or '--'} at {quote.get('snapshot_time') or '--'}")
    print(f"  snapshot hit : {quote.get('snapshot_hit_count')} / {quote.get('snapshot_items')} items")
    print(f"  short cache  : {quote.get('short_cache_hit_count')} / {quote.get('short_cache_items')} items")


def _print_items(items: List[Dict[str, Any]]) -> None:
    if not items:
        print("\nNo stock rows returned.")
        return

    print("\nStocks")
    print("code       name             daily rows  daily last   intraday rows  intraday range")
    print("-" * 86)
    for item in items:
        history = item.get("history") or {}
        intraday = item.get("intraday") or {}
        code = str(item.get("stock_code") or "")[:10]
        name = str(item.get("stock_name") or "--")[:14]
        daily_rows = history.get("rows", 0)
        daily_last = history.get("last_date") or "--"
        intraday_rows = intraday.get("rows", 0)
        first_minute = intraday.get("first_minute") or "--"
        last_minute = intraday.get("last_minute") or "--"
        print(
            f"{code:<10} {name:<14} {daily_rows:>10}  {daily_last:<10} "
            f"{intraday_rows:>13}  {first_minute} -> {last_minute}"
        )


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check stock data diagnostics through the HTTP API")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help=f"API base URL, default {DEFAULT_BASE_URL}")
    parser.add_argument("--trade-date", help="Trade date for intraday diagnostics, YYYY-MM-DD")
    parser.add_argument("--scope", default="observed", choices=["observed", "history_db", "active_a_share"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--q", help="Filter by stock code or name")
    parser.add_argument(
        "--sort",
        default="code",
        choices=["code", "history_rows_desc", "intraday_rows_desc", "latest_daily_desc"],
    )
    parser.add_argument("--header", action="append", default=[], type=_parse_header, help="Extra HTTP header")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="Print raw JSON response")
    args = parser.parse_args(argv)

    try:
        data = _fetch_json(_build_url(args), args.header, args.timeout)
        _require_shape(data)
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
    else:
        _print_summary(data)
        _print_items(data.get("items") or [])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
