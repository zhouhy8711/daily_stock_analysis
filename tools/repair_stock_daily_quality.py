#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit and quarantine provisional rows from the canonical stock_daily table."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.config import get_config
from src.storage import PROVISIONAL_DAILY_DATA_SOURCES


@dataclass
class RepairReport:
    database_path: str
    start_date: str
    end_date: str
    apply: bool
    backup_path: str | None
    provisional_rows: int
    non_market_rows: int
    zero_volume_amount_rows: int
    mismatch_rows: int
    noncanonical_code_rows: int
    provisional_chip_rows: int
    quarantined_rows: int
    deleted_rows: int
    quarantined_chip_rows: int
    deleted_chip_rows: int
    samples: list[dict[str, Any]]


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD") from exc


def _resolve_database_path(value: str | None) -> Path:
    raw = value or get_config().database_path
    return Path(raw).expanduser().resolve()


def _source_placeholders() -> str:
    return ",".join("?" for _ in PROVISIONAL_DAILY_DATA_SOURCES)


def _build_where_clause(
    *,
    start_date: date,
    end_date: date,
    codes: Sequence[str] | None,
) -> tuple[str, list[Any]]:
    conditions = [
        f"data_source IN ({_source_placeholders()})",
        "date >= ?",
        "date <= ?",
    ]
    params: list[Any] = [
        *PROVISIONAL_DAILY_DATA_SOURCES,
        start_date.isoformat(),
        end_date.isoformat(),
    ]
    if codes:
        normalized = [str(code).strip() for code in codes if str(code).strip()]
        if normalized:
            conditions.append(f"code IN ({','.join('?' for _ in normalized)})")
            params.extend(normalized)
    return " AND ".join(conditions), params


def _build_range_where_clause(
    *,
    start_date: date,
    end_date: date,
    codes: Sequence[str] | None,
) -> tuple[str, list[Any]]:
    conditions = [
        "date >= ?",
        "date <= ?",
    ]
    params: list[Any] = [
        start_date.isoformat(),
        end_date.isoformat(),
    ]
    if codes:
        normalized = [str(code).strip() for code in codes if str(code).strip()]
        if normalized:
            conditions.append(f"code IN ({','.join('?' for _ in normalized)})")
            params.extend(normalized)
    return " AND ".join(conditions), params


def _build_chip_id_query(
    *,
    start_date: date,
    end_date: date,
    codes: Sequence[str] | None,
) -> tuple[str, list[Any]]:
    _ = (start_date, end_date, codes)
    key_sql = """
        SELECT stock_chip_daily.id
        FROM stock_chip_daily
        INNER JOIN repair_provisional_daily_keys
          ON repair_provisional_daily_keys.code = stock_chip_daily.code
         AND repair_provisional_daily_keys.date = stock_chip_daily.date
    """
    return key_sql, []


def _backup_database(db_path: Path) -> Path:
    backup_path = db_path.with_suffix(f"{db_path.suffix}.bak.{date.today().strftime('%Y%m%d')}")
    index = 1
    while backup_path.exists():
        backup_path = db_path.with_suffix(f"{db_path.suffix}.bak.{date.today().strftime('%Y%m%d')}.{index}")
        index += 1

    source = sqlite3.connect(str(db_path))
    try:
        target = sqlite3.connect(str(backup_path))
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    return backup_path


def _fetch_count(connection: sqlite3.Connection, sql: str, params: Sequence[Any]) -> int:
    row = connection.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def run_repair(
    *,
    database_path: Path,
    start_date: date,
    end_date: date,
    codes: Sequence[str] | None = None,
    apply: bool = False,
    sample_limit: int = 20,
) -> RepairReport:
    where_clause, params = _build_where_clause(
        start_date=start_date,
        end_date=end_date,
        codes=codes,
    )
    range_where_clause, range_params = _build_range_where_clause(
        start_date=start_date,
        end_date=end_date,
        codes=codes,
    )
    chip_id_query, chip_params = _build_chip_id_query(
        start_date=start_date,
        end_date=end_date,
        codes=codes,
    )
    backup_path: Path | None = None

    if apply:
        backup_path = _backup_database(database_path)

    connection = sqlite3.connect(str(database_path))
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS repair_provisional_daily_keys (
                code TEXT NOT NULL,
                date TEXT NOT NULL,
                PRIMARY KEY (code, date)
            )
            """
        )
        connection.execute("DELETE FROM repair_provisional_daily_keys")
        connection.execute(
            f"""
            INSERT OR IGNORE INTO repair_provisional_daily_keys (code, date)
            SELECT code, date
            FROM stock_daily
            WHERE {where_clause}
            """,
            params,
        )
        provisional_rows = _fetch_count(
            connection,
            f"SELECT COUNT(*) FROM stock_daily WHERE {where_clause}",
            params,
        )
        non_market_rows = _fetch_count(
            connection,
            f"""
            SELECT COUNT(*)
            FROM stock_daily
            WHERE {where_clause}
              AND strftime('%w', date) IN ('0', '6')
            """,
            params,
        )
        zero_volume_amount_rows = _fetch_count(
            connection,
            f"""
            SELECT COUNT(*)
            FROM stock_daily
            WHERE {where_clause}
              AND coalesce(volume, 0) <= 0
              AND coalesce(amount, 0) <= 0
            """,
            params,
        )
        mismatch_rows = _fetch_count(
            connection,
            f"""
            SELECT COUNT(*)
            FROM stock_daily
            WHERE {where_clause}
              AND amount IS NOT NULL
              AND amount > 0
              AND volume IS NOT NULL
              AND volume > 0
              AND close IS NOT NULL
              AND close > 0
              AND (
                  amount / (volume * 100.0) < coalesce(low, close) * 0.75
                  OR amount / (volume * 100.0) > coalesce(high, close) * 1.25
              )
            """,
            params,
        )
        noncanonical_code_rows = _fetch_count(
            connection,
            f"""
            SELECT COUNT(*)
            FROM stock_daily
            WHERE {range_where_clause}
              AND (
                  upper(code) LIKE '%.SH'
                  OR upper(code) LIKE '%.SS'
                  OR upper(code) LIKE '%.SZ'
                  OR upper(code) LIKE '%.BJ'
                  OR (
                      length(code) = 8
                      AND (
                          upper(code) LIKE 'SH%'
                          OR upper(code) LIKE 'SZ%'
                          OR upper(code) LIKE 'BJ%'
                      )
                      AND substr(upper(code), 3) GLOB '[0-9][0-9][0-9][0-9][0-9][0-9]'
                  )
              )
            """,
            range_params,
        )
        provisional_chip_rows = _fetch_count(
            connection,
            f"SELECT COUNT(*) FROM ({chip_id_query})",
            chip_params,
        )
        sample_rows = connection.execute(
            f"""
            SELECT code, date, open, high, low, close, volume, amount, pct_chg, data_source
            FROM stock_daily
            WHERE {where_clause}
            ORDER BY date DESC, code ASC
            LIMIT ?
            """,
            [*params, max(0, sample_limit)],
        ).fetchall()
        samples = [dict(row) for row in sample_rows]

        quarantined_rows = 0
        deleted_rows = 0
        quarantined_chip_rows = 0
        deleted_chip_rows = 0
        if apply and provisional_chip_rows:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_chip_daily_quarantine AS
                SELECT stock_chip_daily.*, NULL AS quarantined_at, NULL AS quarantine_reason
                FROM stock_chip_daily
                WHERE 0
                """
            )
            chip_id_rows = connection.execute(
                chip_id_query,
                chip_params,
            ).fetchall()
            chip_ids = [row["id"] for row in chip_id_rows]
            if chip_ids:
                chip_id_placeholders = ",".join("?" for _ in chip_ids)
                quarantined_chip_rows = connection.execute(
                    f"""
                    INSERT INTO stock_chip_daily_quarantine
                    SELECT stock_chip_daily.*, datetime('now'), 'provisional_daily_source'
                    FROM stock_chip_daily
                    WHERE id IN ({chip_id_placeholders})
                    """,
                    chip_ids,
                ).rowcount
                deleted_chip_rows = connection.execute(
                    f"DELETE FROM stock_chip_daily WHERE id IN ({chip_id_placeholders})",
                    chip_ids,
                ).rowcount
        if apply and provisional_rows:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS stock_daily_quarantine AS
                SELECT stock_daily.*, NULL AS quarantined_at, NULL AS quarantine_reason
                FROM stock_daily
                WHERE 0
                """
            )
            id_rows = connection.execute(
                f"SELECT id FROM stock_daily WHERE {where_clause}",
                params,
            ).fetchall()
            ids = [row["id"] for row in id_rows]
            if ids:
                id_placeholders = ",".join("?" for _ in ids)
                quarantined_rows = connection.execute(
                    f"""
                    INSERT INTO stock_daily_quarantine
                    SELECT stock_daily.*, datetime('now'), 'provisional_daily_source'
                    FROM stock_daily
                    WHERE id IN ({id_placeholders})
                    """,
                    ids,
                ).rowcount
                deleted_rows = connection.execute(
                    f"DELETE FROM stock_daily WHERE id IN ({id_placeholders})",
                    ids,
                ).rowcount
            connection.commit()

        return RepairReport(
            database_path=str(database_path),
            start_date=start_date.isoformat(),
            end_date=end_date.isoformat(),
            apply=apply,
            backup_path=str(backup_path) if backup_path else None,
            provisional_rows=provisional_rows,
            non_market_rows=non_market_rows,
            zero_volume_amount_rows=zero_volume_amount_rows,
            mismatch_rows=mismatch_rows,
            noncanonical_code_rows=noncanonical_code_rows,
            provisional_chip_rows=provisional_chip_rows,
            quarantined_rows=int(quarantined_rows or 0),
            deleted_rows=int(deleted_rows or 0),
            quarantined_chip_rows=int(quarantined_chip_rows or 0),
            deleted_chip_rows=int(deleted_chip_rows or 0),
            samples=samples,
        )
    finally:
        connection.close()


def build_parser() -> argparse.ArgumentParser:
    today = date.today()
    default_start = today - timedelta(days=730)
    parser = argparse.ArgumentParser(
        description="Audit and quarantine provisional stock_daily rows before official backfill."
    )
    parser.add_argument("--database-path", help="SQLite database path; defaults to configured DATABASE_PATH")
    parser.add_argument("--start-date", type=_parse_date, default=default_start, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end-date", type=_parse_date, default=today, help="End date, YYYY-MM-DD")
    parser.add_argument("--codes", help="Optional comma-separated stock codes")
    parser.add_argument("--sample-limit", type=int, default=20, help="Number of suspicious rows to print")
    parser.add_argument("--apply", action="store_true", help="Backup DB, quarantine rows, and delete them from stock_daily")
    parser.add_argument("--json", action="store_true", help="Print JSON report")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.start_date > args.end_date:
        parser.error("--start-date must be earlier than or equal to --end-date")
    if args.sample_limit < 0:
        parser.error("--sample-limit must be non-negative")

    db_path = _resolve_database_path(args.database_path)
    if not db_path.exists():
        parser.error(f"database does not exist: {db_path}")

    codes = args.codes.split(",") if args.codes else None
    report = run_repair(
        database_path=db_path,
        start_date=args.start_date,
        end_date=args.end_date,
        codes=codes,
        apply=args.apply,
        sample_limit=args.sample_limit,
    )

    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print("Stock Daily Quality Repair")
        print(f"  database       : {report.database_path}")
        print(f"  range          : {report.start_date} -> {report.end_date}")
        print(f"  mode           : {'APPLY' if report.apply else 'DRY-RUN'}")
        print(f"  provisional    : {report.provisional_rows}")
        print(f"  non-market     : {report.non_market_rows}")
        print(f"  zero vol/amount: {report.zero_volume_amount_rows}")
        print(f"  amount mismatch: {report.mismatch_rows}")
        print(f"  noncanonical code: {report.noncanonical_code_rows}")
        print(f"  chip provisional: {report.provisional_chip_rows}")
        print(f"  quarantined    : {report.quarantined_rows}")
        print(f"  deleted        : {report.deleted_rows}")
        print(f"  chip quarantine: {report.quarantined_chip_rows}")
        print(f"  chip deleted   : {report.deleted_chip_rows}")
        if report.backup_path:
            print(f"  backup         : {report.backup_path}")
        if report.samples:
            print("\nSamples")
            for row in report.samples:
                print(
                    f"  {row['code']} {row['date']} close={row['close']} "
                    f"volume={row['volume']} amount={row['amount']} source={row['data_source']}"
                )
        if not report.apply and report.provisional_rows:
            print("\nDry-run only. Re-run with --apply after confirming the sample rows.")
        if report.apply:
            print("\nNext: run the official daily backfill for the same date range.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
