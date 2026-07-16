import json
import math
import sqlite3
import threading
import time
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from unittest import mock

from fastapi import BackgroundTasks
from sqlalchemy.exc import IntegrityError, OperationalError

from api.v1.endpoints import rules as rules_endpoint
from api.v1.schemas.rules import RuleBatchRunRequest, RuleRunResponse, RuleTarget
import src.services.rule_service as rule_service_module
import src.rules.engine as rule_engine_module
from src.rules.engine import evaluate_rule, evaluate_rule_history
from src.rules.metrics import build_metric_frame, get_metric_registry
from src.repositories.rule_repo import RuleRepository, _decode_rule_batch_metadata, encode_rule_batch_metadata
from src.repositories.tenant_repo import TenantRepository
from src.services.rule_service import RuleService, RuleValidationError
from src.services.tenant_service import TenantContext, TenantService
from src.storage import (
    DatabaseManager,
    SharedRuleRun,
    StockDaily,
    StockRule,
    StockRuleMatch,
    StockRuleMatchLink,
    StockRuleRun,
)


def _history():
    return [
        {"date": "2026-04-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
        {"date": "2026-04-02", "open": 10, "high": 12, "low": 10, "close": 11, "volume": 1200, "amount": 13200, "pct_chg": 10},
        {"date": "2026-04-03", "open": 11, "high": 13, "low": 11, "close": 12, "volume": 1500, "amount": 18000, "pct_chg": 9.09},
        {"date": "2026-04-04", "open": 12, "high": 14, "low": 12, "close": 13, "volume": 1800, "amount": 23400, "pct_chg": 8.33},
        {"date": "2026-04-05", "open": 13, "high": 15, "low": 13, "close": 14, "volume": 2500, "amount": 35000, "pct_chg": 7.69},
    ]


def _simple_rule_definition(scope="custom", stock_codes=None):
    return {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": scope, "stock_codes": stock_codes or ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 0},
                    }
                ],
            }
        ],
    }


def test_rule_engine_matches_aggregate_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {
                            "type": "aggregate",
                            "metric": "close",
                            "method": "max",
                            "window": 3,
                            "offset": 1,
                        },
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    assert result["matched_groups"][0]["id"] == "g1"


def test_rule_engine_history_scan_accepts_index_subset():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 0},
                    }
                ],
            }
        ],
    }

    events = evaluate_rule_history(definition, frame, indices=[1, 3])

    assert [event["date"] for event in events] == ["2026-04-02", "2026-04-04"]


def test_rule_engine_history_scan_short_circuits_failed_group(monkeypatch):
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "fail-first",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 999},
                    },
                    {
                        "id": "should-not-run",
                        "left": {"metric": "volume"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 0},
                    },
                ],
            }
        ],
    }
    calls = []
    original = rule_engine_module.evaluate_condition

    def spy_evaluate_condition(df, condition, index):
        calls.append(condition["id"])
        return original(df, condition, index)

    monkeypatch.setattr(rule_engine_module, "evaluate_condition", spy_evaluate_condition)

    assert evaluate_rule_history(definition, frame, indices=[0]) == []
    assert calls == ["fail-first"]


def test_rule_engine_does_not_match_missing_metric_value():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "not_available_metric"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 1},
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is False
    assert result["matched_groups"] == []
    condition = result["condition_results"][0]["conditions"][0]
    assert condition["matched"] is False
    assert condition["values"]["left"] is None


def test_rule_engine_matches_consecutive_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": "consecutive",
                        "compare": ">",
                        "right": {"type": "literal", "value": 11},
                        "lookback": 3,
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True


def test_rule_engine_matches_frequency_condition():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "pct_chg"},
                        "operator": "frequency",
                        "compare": ">",
                        "right": {"type": "literal", "value": 7},
                        "lookback": 4,
                        "min_count": 3,
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True


def test_rule_engine_matches_sandwich_number_current_price():
    frame = build_metric_frame(_history(), quote={"current_price": 14.24})
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "sandwich_number",
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    condition = result["matched_groups"][0]["conditions"][0]
    assert condition["values"]["left"] == 14.24
    assert condition["values"]["pattern"] == "4.24"
    assert "夹板数" in condition["explanation"]


def test_rule_engine_rejects_repeated_digits_as_sandwich_number():
    frame = build_metric_frame(_history(), quote={"current_price": 14.44})
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "sandwich_number",
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is False


def test_rule_engine_matches_pair_number_current_price():
    frame = build_metric_frame(_history(), quote={"current_price": 14.44})
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "pair_number",
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    condition = result["matched_groups"][0]["conditions"][0]
    assert condition["values"]["left"] == 14.44
    assert condition["values"]["pattern"] == ".44"
    assert "对子数" in condition["explanation"]


def test_rule_engine_rejects_non_pair_number_current_price():
    frame = build_metric_frame(_history(), quote={"current_price": 14.45})
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "pair_number",
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is False


def test_rule_engine_returns_matched_history_dates():
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 12},
                    }
                ],
            }
        ],
    }

    events = evaluate_rule_history(definition, frame)

    assert [event["date"] for event in events] == ["2026-04-04", "2026-04-05"]


def test_rule_repository_counts_persisted_match_event_rows():
    event_count = RuleRepository._count_event_rows_from_snapshots([
        '{"_matched_events":[{"date":"2026-04-04"},{"date":"2026-04-05"}]}',
        '{"_matched_dates":["2026-04-03"]}',
        '{"_matched_events":[],"_matched_dates":["2026-04-02"]}',
        "{}",
        None,
        "{broken",
    ])

    assert event_count == 4


def test_database_manager_adds_stock_rule_disable_column_for_existing_sqlite(tmp_path):
    DatabaseManager.reset_instance()
    db_file = tmp_path / "legacy_rules.db"
    with sqlite3.connect(db_file) as connection:
        connection.execute(
            """
            CREATE TABLE stock_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                period VARCHAR(16) NOT NULL DEFAULT 'daily',
                lookback_days INTEGER NOT NULL DEFAULT 120,
                target_scope VARCHAR(16) NOT NULL DEFAULT 'watchlist',
                target_codes_json TEXT,
                definition_json TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )

    db = DatabaseManager(db_url=f"sqlite:///{db_file}")
    try:
        with db._engine.begin() as connection:
            columns = {
                str(row[1]): row
                for row in connection.exec_driver_sql("PRAGMA table_info(stock_rules)").fetchall()
            }
            connection.exec_driver_sql(
                """
                INSERT INTO stock_rules (
                    name,
                    period,
                    lookback_days,
                    target_scope,
                    target_codes_json,
                    definition_json
                )
                VALUES ('放量观察', 'daily', 120, 'custom', '[]', '{}')
                """
            )
            is_disable = connection.exec_driver_sql("SELECT is_disable FROM stock_rules").scalar_one()

        assert "is_disable" in columns
        assert int(is_disable) == 0
    finally:
        DatabaseManager.reset_instance()


def test_database_manager_adds_tenant_columns_and_default_tenant_for_existing_sqlite(tmp_path):
    DatabaseManager.reset_instance()
    db_file = tmp_path / "legacy_tenant_rules.db"
    with sqlite3.connect(db_file) as connection:
        connection.execute(
            """
            CREATE TABLE stock_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name VARCHAR(100) NOT NULL,
                description TEXT,
                is_active BOOLEAN NOT NULL DEFAULT 1,
                period VARCHAR(16) NOT NULL DEFAULT 'daily',
                lookback_days INTEGER NOT NULL DEFAULT 120,
                target_scope VARCHAR(16) NOT NULL DEFAULT 'watchlist',
                target_codes_json TEXT,
                definition_json TEXT NOT NULL,
                created_at DATETIME,
                updated_at DATETIME
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE stock_rule_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'running',
                target_count INTEGER NOT NULL DEFAULT 0,
                match_count INTEGER NOT NULL DEFAULT 0,
                error TEXT,
                started_at DATETIME,
                finished_at DATETIME,
                duration_ms INTEGER
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE stock_rule_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL,
                rule_id INTEGER NOT NULL,
                stock_code VARCHAR(16) NOT NULL,
                stock_name VARCHAR(80),
                matched_groups_json TEXT,
                snapshot_json TEXT,
                explanation TEXT,
                created_at DATETIME
            )
            """
        )
        connection.execute(
            """
            INSERT INTO stock_rules (
                id, name, period, lookback_days, target_scope, target_codes_json, definition_json
            )
            VALUES (1, '旧规则', 'daily', 120, 'custom', '[]', '{}')
            """
        )
        connection.execute(
            """
            INSERT INTO stock_rule_runs (id, rule_id, status, target_count, match_count, started_at)
            VALUES (10, 1, 'completed', 1, 1, '2026-05-08 10:00:00')
            """
        )
        connection.execute(
            """
            INSERT INTO stock_rule_matches (id, run_id, rule_id, stock_code, matched_groups_json, snapshot_json)
            VALUES (20, 10, 1, '600519', '[]', '{}')
            """
        )
        connection.commit()

    db = DatabaseManager(db_url=f"sqlite:///{db_file}")
    try:
        with db._engine.begin() as connection:
            default_tenant_id = connection.exec_driver_sql(
                "SELECT id FROM tenants WHERE key = 'default'"
            ).scalar_one()
            rule_row = connection.exec_driver_sql(
                "SELECT tenant_id, visibility, is_disable FROM stock_rules WHERE id = 1"
            ).fetchone()
            run_tenant_id = connection.exec_driver_sql(
                "SELECT tenant_id FROM stock_rule_runs WHERE id = 10"
            ).scalar_one()
            match_tenant_id = connection.exec_driver_sql(
                "SELECT tenant_id FROM stock_rule_matches WHERE id = 20"
            ).scalar_one()
            tenant_config_count = connection.exec_driver_sql(
                "SELECT COUNT(*) FROM tenant_configs WHERE tenant_id = ?",
                (default_tenant_id,),
            ).scalar_one()
            shared_table_count = connection.exec_driver_sql(
                """
                SELECT COUNT(*)
                FROM sqlite_master
                WHERE type = 'table'
                  AND name IN (
                    'shared_rule_runs',
                    'shared_rule_matches',
                    'stock_rule_run_segments',
                    'stock_rule_match_links'
                  )
                """
            ).scalar_one()

        assert rule_row[0] is None
        assert rule_row[1] == "shared"
        assert int(rule_row[2]) == 0
        assert run_tenant_id == default_tenant_id
        assert match_tenant_id == default_tenant_id
        assert tenant_config_count == 1
        assert shared_table_count == 4
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_scopes_rules_runs_and_matches_by_tenant():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "desk_b", "name": "Desk B"})
        default_repo = RuleRepository(db, tenant_id=default_tenant["id"], tenant_key=default_tenant["key"])
        other_repo = RuleRepository(db, tenant_id=other_tenant["id"], tenant_key=other_tenant["key"])

        with db.get_session() as session:
            shared_rule = StockRule(
                name="共享规则",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition()),
            )
            default_rule = StockRule(
                name="默认租户规则",
                tenant_id=default_tenant["id"],
                visibility="tenant",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition()),
            )
            other_rule = StockRule(
                name="其他租户规则",
                tenant_id=other_tenant["id"],
                visibility="tenant",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition()),
            )
            session.add_all([shared_rule, default_rule, other_rule])
            session.commit()
            session.refresh(shared_rule)
            session.refresh(default_rule)
            session.refresh(other_rule)
            default_run = StockRuleRun(
                tenant_id=default_tenant["id"],
                rule_id=default_rule.id,
                status="completed",
                target_count=1,
                match_count=1,
            )
            other_run = StockRuleRun(
                tenant_id=other_tenant["id"],
                rule_id=other_rule.id,
                status="completed",
                target_count=1,
                match_count=1,
            )
            session.add_all([default_run, other_run])
            session.commit()
            session.refresh(default_run)
            session.refresh(other_run)
            session.add_all([
                StockRuleMatch(
                    tenant_id=default_tenant["id"],
                    run_id=default_run.id,
                    rule_id=default_rule.id,
                    stock_code="600519",
                    matched_groups_json="[]",
                    snapshot_json="{}",
                ),
                StockRuleMatch(
                    tenant_id=other_tenant["id"],
                    run_id=other_run.id,
                    rule_id=other_rule.id,
                    stock_code="000001",
                    matched_groups_json="[]",
                    snapshot_json="{}",
                ),
            ])
            session.commit()
            shared_rule_id = shared_rule.id
            default_run_id = default_run.id
            other_run_id = other_run.id

        assert [rule["name"] for rule in default_repo.list_rules()] == ["默认租户规则", "共享规则"]
        assert [rule["name"] for rule in other_repo.list_rules()] == ["其他租户规则", "共享规则"]
        assert default_repo.get_run(other_run_id) is None
        assert other_repo.get_run(default_run_id) is None
        assert default_repo.list_matches(default_run_id)[0]["stock_code"] == "600519"
        assert other_repo.list_matches(other_run_id)[0]["stock_code"] == "000001"

        cloned = default_repo.clone_rule(shared_rule_id)
        assert cloned is not None
        assert cloned["tenant_id"] == default_tenant["id"]
        assert cloned["visibility"] == "tenant"
        assert cloned["is_shared"] is False
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_projects_shared_matches_to_tenant_runs():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        default_repo = RuleRepository(db, tenant_id=default_tenant["id"], tenant_key=default_tenant["key"])
        other_repo = RuleRepository(db, tenant_id=other_tenant["id"], tenant_key=other_tenant["key"])
        with db.get_session() as session:
            rule = StockRule(
                name="共享规则",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition()),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        default_run_id = default_repo.create_run(rule_id, 1)
        other_run_id = other_repo.create_run(rule_id, 1)
        shared_run_id = default_repo.create_shared_run(
            execution_key="shared-once",
            mode="history",
            data_policy="db_only",
            target_count=1,
            metadata={"logic_fingerprint": "fp"},
        )
        shared_matches, _duration_ms = default_repo.finish_shared_run(
            shared_run_id=shared_run_id,
            rule_id=rule_id,
            status="completed",
            started_at=datetime(2026, 5, 8, 10, 0),
            matches=[{
                "rule_id": rule_id,
                "rule_fingerprint": "fp",
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "matched_groups": [{"id": "g1"}],
                "matched_dates": ["2026-05-08"],
                "matched_events": [{"date": "2026-05-08"}],
                "snapshot": {"close": 100},
                "explanation": "hit",
            }],
            metadata={"completed_count": 1},
        )
        link = {
            "rule_id": rule_id,
            "shared_run_id": shared_run_id,
            "shared_match_id": shared_matches[0]["id"],
            "stock_code": "600519",
        }
        default_repo.finish_projected_run(
            run_id=default_run_id,
            rule_id=rule_id,
            status="completed",
            started_at=datetime(2026, 5, 8, 10, 0),
            matches=[],
            shared_links=[link],
            error=encode_rule_batch_metadata([rule_id], ["共享规则"], [], completed_count=1),
        )
        other_repo.finish_projected_run(
            run_id=other_run_id,
            rule_id=rule_id,
            status="completed",
            started_at=datetime(2026, 5, 8, 10, 0),
            matches=[],
            shared_links=[link],
            error=encode_rule_batch_metadata([rule_id], ["共享规则"], [], completed_count=1),
        )

        default_matches = default_repo.list_matches(default_run_id)
        other_matches = other_repo.list_matches(other_run_id)

        assert default_matches[0]["stock_code"] == "600519"
        assert default_matches[0]["shared_run_id"] == shared_run_id
        assert default_repo.get_run(default_run_id)["event_count"] == 1
        assert other_matches[0]["stock_name"] == "贵州茅台"

        assert default_repo.delete_run(default_run_id) is True
        with db.get_session() as session:
            assert session.query(SharedRuleRun).count() == 1
            assert session.query(StockRuleMatchLink).count() == 1
        assert other_repo.list_matches(other_run_id)[0]["stock_code"] == "600519"
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_create_shared_run_reuses_existing_after_integrity_conflict(monkeypatch):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            row = SharedRuleRun(
                execution_key="same-shared-execution",
                status="running",
                mode="history",
                data_policy="db_only",
                target_count=3,
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            existing_id = int(row.id)

        def raise_integrity(_operation_name, _write_operation):
            raise IntegrityError(
                "INSERT INTO shared_rule_runs ...",
                {},
                Exception("UNIQUE constraint failed: shared_rule_runs.execution_key"),
            )

        monkeypatch.setattr(repo, "_run_write_transaction", raise_integrity)

        assert repo.create_shared_run(
            execution_key="same-shared-execution",
            mode="history",
            data_policy="db_only",
            target_count=3,
            metadata={},
        ) == existing_id
        shared_run_id, created = repo.create_shared_run_with_state(
            execution_key="same-shared-execution",
            mode="history",
            data_policy="db_only",
            target_count=3,
            metadata={},
        )
        assert shared_run_id == existing_id
        assert created is False
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_waits_for_completed_shared_run_by_key(tmp_path):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url=f"sqlite:///{tmp_path / 'shared-wait.db'}")
    try:
        repo = RuleRepository(db)
        shared_run_id, created = repo.create_shared_run_with_state(
            execution_key="wait-shared-execution",
            mode="history",
            data_policy="db_only",
            target_count=1,
            metadata={},
        )
        assert created is True

        def finish_shared_run():
            time.sleep(0.05)
            repo.finish_shared_run(
                shared_run_id=shared_run_id,
                rule_id=1,
                status="completed",
                started_at=datetime.now(),
                matches=[],
                metadata={"completed_count": 1},
            )

        thread = threading.Thread(target=finish_shared_run)
        thread.start()
        try:
            completed = repo.wait_for_completed_shared_run_by_key(
                "wait-shared-execution",
                timeout_seconds=1.0,
                poll_interval_seconds=0.01,
            )
        finally:
            thread.join(timeout=1)

        assert completed is not None
        assert completed["id"] == shared_run_id
        assert completed["status"] == "completed"
    finally:
        DatabaseManager.reset_instance()


def test_rule_service_blocks_shared_rule_update_and_delete_in_tenant_workspace():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant = TenantRepository(db).ensure_default_tenant()
        repo = RuleRepository(db, tenant_id=tenant["id"], tenant_key=tenant["key"])
        with db.get_session() as session:
            row = StockRule(
                name="共享规则",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition()),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            rule_id = row.id

        service = RuleService(
            repo=repo,
            stock_service=mock.Mock(),
            tenant_context=TenantContext(id=tenant["id"], key=tenant["key"], name=tenant["name"], is_default=True),
        )

        with mock.patch.object(repo, "update_rule") as update_rule:
            with mock.patch.object(repo, "delete_rule") as delete_rule:
                try:
                    service.update_rule(rule_id, {"name": "新名称"})
                    assert False, "expected shared update to fail"
                except RuleValidationError:
                    pass
                try:
                    service.delete_rule(rule_id)
                    assert False, "expected shared delete to fail"
                except RuleValidationError:
                    pass

        update_rule.assert_not_called()
        delete_rule.assert_not_called()
    finally:
        DatabaseManager.reset_instance()


def test_tenant_notification_config_default_falls_back_to_global(monkeypatch):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        global_config = SimpleNamespace(
            feishu_webhook_url="https://global.example/webhook",
            feishu_webhook_secret="global-secret",
            feishu_webhook_keyword="global-keyword",
            feishu_max_bytes=18000,
        )
        monkeypatch.setattr("src.services.tenant_service.get_config", lambda: global_config)
        service = TenantService(tenant_repo)

        default_config = service.build_notification_config(default_tenant["id"])
        other_config = service.build_notification_config(other_tenant["id"])

        assert default_config.feishu_webhook_url == "https://global.example/webhook"
        assert default_config.feishu_webhook_secret == "global-secret"
        assert default_config.feishu_webhook_keyword == "global-keyword"
        assert other_config.feishu_webhook_url is None
        assert other_config.feishu_webhook_secret is None

        service.update_tenant_config(
            "quant_team",
            {
                "feishu_webhook_url": "https://tenant.example/webhook",
                "feishu_webhook_secret": "tenant-secret",
                "feishu_webhook_keyword": "tenant-keyword",
            },
        )
        configured_other = service.build_notification_config(other_tenant["id"])
        assert configured_other.feishu_webhook_url == "https://tenant.example/webhook"
        assert configured_other.feishu_webhook_secret == "tenant-secret"
        assert configured_other.feishu_webhook_keyword == "tenant-keyword"
    finally:
        DatabaseManager.reset_instance()


def test_latest_batch_run_without_tenant_keys_targets_all_active_tenants():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        tenant_repo.create_tenant({"key": "archived_team", "name": "归档组"})
        tenant_repo.update_tenant("archived_team", {"is_active": False})
        tenant_service = TenantService(tenant_repo)
        current_tenant = TenantContext(
            id=default_tenant["id"],
            key=default_tenant["key"],
            name=default_tenant["name"],
            is_default=True,
        )

        live_payload = RuleBatchRunRequest(
            rule_ids=[1, 2],
            mode="latest",
            target=RuleTarget(scope="watchlist", stock_codes=[]),
        )
        history_payload = RuleBatchRunRequest(
            rule_ids=[1, 2],
            mode="history",
            target=RuleTarget(scope="watchlist", stock_codes=[]),
        )

        live_tenants = rules_endpoint._resolve_rule_run_tenants(
            live_payload,
            current_tenant,
            tenant_service,
        )
        history_tenants = rules_endpoint._resolve_rule_run_tenants(
            history_payload,
            current_tenant,
            tenant_service,
        )

        assert [tenant.key for tenant in live_tenants] == ["default", "quant_team"]
        assert [tenant.key for tenant in history_tenants] == ["default"]
    finally:
        DatabaseManager.reset_instance()


def test_rule_run_response_preserves_fast_latest_skip_metadata():
    response = RuleRunResponse(
        run_id=1,
        run_ids=[1],
        tenant_id=1,
        tenant_key="default",
        rule_id=7,
        rule_ids=[7],
        rule_names=["实测规则"],
        status="partial",
        target_count=3,
        completed_count=3,
        match_count=1,
        event_count=1,
        mode="latest",
        fast_latest_scan=True,
        skipped_count=2,
        skip_counts={"history_cache_miss": 2},
    )

    payload = response.model_dump()

    assert payload["fast_latest_scan"] is True
    assert payload["skipped_count"] == 2
    assert payload["skip_counts"] == {"history_cache_miss": 2}


def test_rule_service_watchlist_uses_tenant_stock_list():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        tenant = tenant_repo.ensure_default_tenant()
        tenant_repo.update_config(tenant["id"], {"stock_list": ["600519", "AAPL"]})
        repo = RuleRepository(db, tenant_id=tenant["id"], tenant_key=tenant["key"])
        definition = _simple_rule_definition(scope="watchlist")
        definition["target"]["stock_codes"] = []
        created_rule = repo.create_rule({
            "name": "租户股票池规则",
            "is_active": True,
            "definition": definition,
        })
        tenant_service = TenantService(repo=tenant_repo)
        service = RuleService(
            repo=repo,
            stock_service=mock.Mock(),
            tenant_context=TenantContext(id=tenant["id"], key=tenant["key"], name=tenant["name"], is_default=True),
            tenant_service=tenant_service,
        )

        _rule, _definition, stock_codes = service._prepare_rule_run(created_rule["id"], None)

        assert stock_codes == ["600519", "AAPL"]
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_list_rules_hides_disabled_rules():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            visible_rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            hidden_rule = StockRule(
                name="隐藏观察",
                is_disable=True,
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add_all([visible_rule, hidden_rule])
            session.commit()
            session.refresh(hidden_rule)
            hidden_rule_id = hidden_rule.id

        assert [rule["name"] for rule in repo.list_rules()] == ["放量观察"]
        hidden = repo.get_rule(hidden_rule_id)
        assert hidden is not None
        assert hidden["is_disable"] is True
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_previous_live_match_signature_skips_non_live_runs():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)

            live_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1,
                match_count=1,
                started_at=datetime(2026, 5, 8, 10, 0, 0),
            )
            non_live_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1,
                match_count=1,
                started_at=datetime(2026, 5, 8, 10, 0, 30),
            )
            current_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1,
                match_count=1,
                started_at=datetime(2026, 5, 8, 10, 1, 0),
            )
            session.add_all([live_run, non_live_run, current_run])
            session.commit()
            session.refresh(live_run)
            session.refresh(non_live_run)
            session.refresh(current_run)

            session.add_all([
                StockRuleMatch(
                    run_id=live_run.id,
                    rule_id=rule.id,
                    stock_code="300274.SZ",
                    snapshot_json=json.dumps({
                        "_matched_events": [
                            {
                                "snapshot": {
                                    "snapshot_id": "20260508100000",
                                    "snapshot_time": "2026-05-08T10:00:00",
                                }
                            }
                        ]
                    }),
                ),
                StockRuleMatch(
                    run_id=non_live_run.id,
                    rule_id=rule.id,
                    stock_code="000001.SZ",
                    snapshot_json=json.dumps({"_matched_events": [{"date": "2026-04-30"}]}),
                ),
            ])
            session.commit()
            current_run_id = current_run.id
            live_run_id = live_run.id
            rule_id = rule.id

        previous = repo.get_previous_live_match_signature(current_run_id)
        previous_keys = repo.get_previous_live_match_keys(current_run_id)

        assert previous == {
            "run_id": live_run_id,
            "signature": ((rule_id, "300274.SZ"),),
        }
        assert previous_keys == {
            "run_ids": [live_run_id],
            "keys": (("2026-05-08", rule_id, "300274.SZ"),),
        }
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_uses_database_retry_runner_for_run_writes():
    db = mock.Mock()
    db._run_write_transaction.return_value = 42
    repo = RuleRepository(db)

    def write_operation(_session):
        return 0

    result = repo._run_write_transaction("stock_rule_run.test", write_operation)

    assert result == 42
    db._run_write_transaction.assert_called_once_with("stock_rule_run.test", write_operation)


def test_rule_repository_update_progress_ignores_reserved_metadata_keys():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="进度规则",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        run_id = repo.create_run(rule_id, target_count=5)
        repo.update_run_progress(
            run_id=run_id,
            rule_ids=[rule_id],
            rule_names=["进度规则"],
            completed_count=2,
            errors=["temporary miss"],
            metadata={
                "completed_count": 99,
                "rule_ids": [999],
                "rule_names": ["错误规则"],
                "errors": ["wrong error"],
                "run_key": "progress-key",
                "skip_counts": {"history_cache_miss": 1},
            },
        )

        with db.get_session() as session:
            row = session.get(StockRuleRun, run_id)
            metadata, public_error = _decode_rule_batch_metadata(row.error)

        assert public_error == "temporary miss"
        assert metadata["completed_count"] == 2
        assert metadata["rule_ids"] == [rule_id]
        assert metadata["rule_names"] == ["进度规则"]
        assert metadata["errors"] == ["temporary miss"]
        assert metadata["run_key"] == "progress-key"
        assert metadata["skip_counts"] == {"history_cache_miss": 1}
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_fail_stale_running_runs_marks_only_old_running_runs():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    now = datetime(2026, 5, 8, 15, 0, 0)
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)

            old_run = StockRuleRun(
                rule_id=rule.id,
                status="running",
                target_count=120,
                match_count=0,
                started_at=now - timedelta(hours=7),
                error=encode_rule_batch_metadata(
                    [rule.id],
                    ["放量观察"],
                    [],
                    completed_count=80,
                    run_key="same-snapshot",
                ),
            )
            recent_run = StockRuleRun(
                rule_id=rule.id,
                status="running",
                target_count=120,
                match_count=0,
                started_at=now - timedelta(minutes=30),
            )
            completed_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=120,
                match_count=0,
                started_at=now - timedelta(hours=8),
            )
            session.add_all([old_run, recent_run, completed_run])
            session.commit()
            session.refresh(old_run)
            session.refresh(recent_run)
            session.refresh(completed_run)
            old_run_id = old_run.id
            recent_run_id = recent_run.id
            completed_run_id = completed_run.id

        cleaned = repo.fail_stale_running_runs(now=now, stale_after=timedelta(hours=6))

        assert cleaned == 1
        old_item = repo.get_run(old_run_id)
        recent_item = repo.get_run(recent_run_id)
        completed_item = repo.get_run(completed_run_id)
        assert old_item["status"] == "failed"
        assert old_item["completed_count"] == 80
        assert "运行超时" in old_item["error"]
        assert recent_item["status"] == "running"
        assert completed_item["status"] == "completed"
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_fail_running_runs_started_before_marks_orphaned_runs():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    process_started_at = datetime(2026, 5, 8, 15, 0, 0)
    now = process_started_at + timedelta(minutes=5)
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)

            orphaned_run = StockRuleRun(
                rule_id=rule.id,
                status="running",
                target_count=120,
                match_count=0,
                started_at=process_started_at - timedelta(seconds=1),
                error=encode_rule_batch_metadata(
                    [rule.id],
                    ["放量观察"],
                    [],
                    completed_count=60,
                    run_key="same-snapshot",
                ),
            )
            active_run = StockRuleRun(
                rule_id=rule.id,
                status="running",
                target_count=120,
                match_count=0,
                started_at=process_started_at + timedelta(seconds=1),
            )
            session.add_all([orphaned_run, active_run])
            session.commit()
            session.refresh(orphaned_run)
            session.refresh(active_run)
            orphaned_run_id = orphaned_run.id
            active_run_id = active_run.id

        cleaned = repo.fail_running_runs_started_before(process_started_at, now=now)

        assert cleaned == 1
        orphaned_item = repo.get_run(orphaned_run_id)
        active_item = repo.get_run(active_run_id)
        assert orphaned_item["status"] == "failed"
        assert orphaned_item["completed_count"] == 60
        assert "服务已重启" in orphaned_item["error"]
        assert active_item["status"] == "running"
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_reusable_run_reports_event_count_from_snapshots():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1,
                match_count=1,
                error=encode_rule_batch_metadata(
                    [rule.id],
                    ["放量观察"],
                    [],
                    completed_count=1,
                    run_key="same-snapshot",
                ),
            )
            session.add(run)
            session.commit()
            session.refresh(run)
            session.add(StockRuleMatch(
                run_id=run.id,
                rule_id=rule.id,
                stock_code="600519",
                stock_name="贵州茅台",
                matched_groups_json="[]",
                snapshot_json=json.dumps({
                    "_matched_events": [
                        {"date": "2026-05-07"},
                        {"date": "2026-05-08"},
                    ],
                }),
            ))
            session.commit()

        item = repo.find_reusable_run_by_key("same-snapshot")

        assert item["match_count"] == 1
        assert item["event_count"] == 2
        assert item["reused_run"] is True
    finally:
        DatabaseManager.reset_instance()


def test_rule_repository_list_runs_skips_large_snapshot_event_recount():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        with db.get_session() as session:
            rule = StockRule(
                name="放量观察",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json="{}",
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            small_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1,
                match_count=1,
                started_at=datetime(2026, 5, 7, 9, 30),
            )
            large_run = StockRuleRun(
                rule_id=rule.id,
                status="completed",
                target_count=1001,
                match_count=1001,
                started_at=datetime(2026, 5, 7, 10, 30),
            )
            session.add_all([small_run, large_run])
            session.commit()
            session.refresh(small_run)
            session.refresh(large_run)
            small_run_id = small_run.id
            large_run_id = large_run.id
            for run in (small_run, large_run):
                session.add(StockRuleMatch(
                    run_id=run.id,
                    rule_id=rule.id,
                    stock_code="600519",
                    stock_name="贵州茅台",
                    matched_groups_json="[]",
                    snapshot_json=json.dumps({
                        "_matched_events": [
                            {"date": "2026-05-07"},
                            {"date": "2026-05-08"},
                        ],
                    }),
                ))
            session.commit()

        items = repo.list_runs(limit=2)
        by_id = {item["id"]: item for item in items}

        assert by_id[small_run_id]["event_count"] == 2
        assert by_id[large_run_id]["event_count"] == 1001
        assert repo.get_run(large_run_id)["event_count"] == 2
    finally:
        DatabaseManager.reset_instance()


def test_metric_frame_maps_chip_ratios_to_percent_values():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "profit_ratio": 0.82,
                "concentration_90": 0.14,
                "concentration_70": 0.08,
                "avg_cost": 12.3,
                "cost_90_low": 9.8,
                "cost_90_high": 13.2,
                "cost_70_low": 10.1,
                "cost_70_high": 12.7,
                "distribution": [
                    {"price": 10.0, "percent": 0.2},
                    {"price": 12.0, "percent": 0.5},
                ],
            }
        },
    )

    latest = frame.iloc[-1]

    assert latest["profit_ratio"] == 82
    assert latest["trapped_ratio"] == 18
    assert round(latest["chip_concentration_90"], 6) == 14
    assert round(latest["chip_concentration_70"], 6) == 8
    assert latest["avg_cost"] == 12.3
    assert latest["cost_90_low"] == 9.8
    assert latest["cost_90_high"] == 13.2
    assert latest["cost_70_low"] == 10.1
    assert latest["cost_70_high"] == 12.7
    assert latest["price_range_90_mid"] == 11.5
    assert round(latest["price_range_90_width"], 6) == 3.4
    assert round(latest["price_range_90_width_pct"], 6) == round(3.4 / 11.5 * 100, 6)
    assert latest["chip_peak_price"] == 12.0
    assert latest["chip_peak_percent"] == 50
    assert round(latest["chip_peak_distance_pct"], 6) == round((14 - 12) / 12 * 100, 6)
    assert latest["chip_peak_count"] == 1
    assert latest["chip_single_peak_signal"] == 1
    assert latest["chip_peak_low_price"] == 10.0
    assert latest["chip_peak_high_price"] == 12.0
    assert latest["chip_peak_price_ratio"] == 1.2


def test_metric_frame_calculates_rolling_range_and_chip_average_metrics():
    start = datetime(2026, 1, 1)
    history = []
    snapshots = []
    for index in range(60):
        day = (start + timedelta(days=index)).strftime("%Y-%m-%d")
        low = 100 + index
        history.append({
            "date": day,
            "open": low + 1,
            "high": low + 10,
            "low": low,
            "close": low + 5,
            "volume": 1000 + index,
            "amount": (low + 5) * (1000 + index),
            "pct_chg": 0.1,
        })
        snapshots.append({
            "date": day,
            "concentration_90": 0.10,
            "distribution": [
                {"price": 10.0, "percent": 0.15},
                {"price": 11.0, "percent": 0.50},
                {"price": 12.0, "percent": 0.15},
            ],
        })

    frame = build_metric_frame(
        history,
        extra_metrics={"chip_distribution": {"date": history[-1]["date"], "snapshots": snapshots}},
    )
    latest = frame.iloc[-1]

    assert round(latest["price_range_30d_pct"], 6) == round((169 - 130) / 130 * 100, 6)
    assert round(latest["price_range_60d_pct"], 6) == round((169 - 100) / 100 * 100, 6)
    assert round(latest["chip_concentration_90_avg_30d"], 6) == 10
    assert round(latest["chip_concentration_90_avg_60d"], 6) == 10
    assert latest["chip_peak_count"] == 1
    assert latest["chip_single_peak_signal"] == 1
    assert latest["chip_peak_low_price"] == 11.0
    assert latest["chip_peak_high_price"] == 11.0
    assert latest["chip_peak_price_ratio"] == 1.0


def test_metric_registry_groups_indicator_page_metrics_by_chart_area():
    registry = {item["key"]: item for item in get_metric_registry()}

    assert registry["current_price"]["category"] == "核心行情"
    assert registry["total_mv"]["category"] == "核心行情"
    assert registry["pe_ratio_percentile_250d"]["category"] == "核心行情"
    assert registry["pe_ratio_percentile_250d"]["unit"] == "%"
    assert registry["close"]["category"] == "K线图"
    assert registry["price_range_30d_pct"]["category"] == "K线图"
    assert registry["prev_5d_return_pct"]["category"] == "额外"
    assert registry["prev_20d_return_pct"]["category"] == "额外"
    assert registry["trend_start_signal"]["category"] == "趋势起涨"
    assert registry["trend_live_setup_score"]["category"] == "趋势起涨"
    assert registry["trend_live_confirm_signal"]["category"] == "趋势起涨"
    assert registry["trend_overheat_risk_signal"]["category"] == "趋势起涨"
    assert registry["trend_failure_signal"]["category"] == "趋势起涨"
    assert registry["history_trading_days_count"]["category"] == "趋势起涨"
    assert registry["prior_10d_breakout_20d_count"]["category"] == "趋势起涨"
    assert registry["volume_expansion_20d_ratio"]["unit"] == "倍"
    assert registry["limit_up_price"]["category"] == "K线图"
    assert registry["volume_ma5"]["category"] == "成交量图"
    assert registry["volume"]["unit"] == "手"
    assert registry["volume_ma5"]["unit"] == "手"
    assert registry["after_hours_amount"]["category"] == "成交量图"
    assert registry["macd_dif"]["category"] == "MACD图"
    assert registry["rsi24"]["category"] == "RSI图"
    assert registry["trapped_ratio"]["category"] == "筹码峰-全部筹码"
    assert registry["chip_concentration_90_avg_30d"]["category"] == "筹码峰-全部筹码"
    assert registry["chip_single_peak_signal"]["category"] == "筹码峰-全部筹码"
    assert registry["chip_peak_price_ratio"]["unit"] == "倍"
    assert registry["main_profit_ratio"]["category"] == "筹码峰-主力筹码"
    assert registry["main_net_volume_pct"]["category"] == "实时监控"
    assert registry["main_force_net"]["category"] == "实时监控"
    assert registry["deducted_net_profit_yoy_pct"]["category"] == "财务事件"
    assert registry["announcement_next_day_gap_pct"]["unit"] == "%"
    assert registry["announcement_next_day_volume_ratio"]["unit"] == "倍"


def test_metric_frame_maps_main_chip_distribution_when_available():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "main_chip_distribution": {
                "profit_ratio": 0.6,
                "avg_cost": 13,
                "cost_90_low": 11,
                "cost_90_high": 15,
                "concentration_90": 0.15,
            }
        },
    )
    latest = frame.iloc[-1]

    assert latest["main_profit_ratio"] == 60
    assert latest["main_trapped_ratio"] == 40
    assert latest["main_avg_cost"] == 13
    assert latest["main_price_range_90_mid"] == 13
    assert latest["main_price_range_90_width"] == 4
    assert latest["main_chip_concentration_90"] == 15
    assert round(latest["main_price_to_avg_cost_pct"], 6) == round((14 - 13) / 13 * 100, 6)


def test_metric_frame_calculates_indicator_page_metrics_for_rules():
    frame = build_metric_frame(_history())
    latest = frame.iloc[-1]

    assert latest["change"] == 1
    assert round(latest["volume_ratio"], 6) == round(2500 / 1600, 6)
    assert latest["amount_ma5"] == 19920
    assert latest["main_force_net"] > 0
    assert round(latest["net_super_large_order"], 6) == round(latest["main_force_net"] * 0.44, 6)


def test_metric_frame_calculates_pe_ratio_percentile_250d():
    history = []
    base = date(2025, 1, 1)
    for index in range(85):
        close = 10 + index * 0.1
        history.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1000 + index,
            "amount": close * (1000 + index),
            "pct_chg": 1,
            "pe_ratio": 20 + index,
        })

    frame = build_metric_frame(history)

    assert math.isnan(frame.iloc[78]["pe_ratio_percentile_250d"])
    assert frame.iloc[79]["pe_ratio_percentile_250d"] == 100
    assert frame.iloc[-1]["pe_ratio_percentile_250d"] == 100


def test_live_history_sync_carries_forward_valuation_fields_for_latest_metrics():
    history = []
    base = date(2025, 1, 1)
    for index in range(85):
        close = 10 + index * 0.1
        history.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close - 0.1,
            "high": close + 0.2,
            "low": close - 0.2,
            "close": close,
            "volume": 1000 + index,
            "amount": close * (1000 + index),
            "pct_chg": 1,
            "pe_ratio": 20 + index,
            "total_mv": 1_000_000 + index * 10_000,
            "circ_mv": 800_000 + index * 8_000,
            "total_shares": 100_000,
            "float_shares": 80_000,
        })

    previous_close = history[-1]["close"]
    previous_pe = history[-1]["pe_ratio"]
    quote = {
        "stock_code": "600519",
        "current_price": previous_close * 1.1,
        "open": previous_close,
        "high": previous_close * 1.12,
        "low": previous_close * 0.99,
        "volume": 2000,
        "amount": previous_close * 1.1 * 2000,
        "change_percent": 10,
        "snapshot_time": "2025-04-01T10:00:00",
        "quote_time": "2025-04-01T10:00:00",
        "source": "intraday_hot_table",
    }

    rows = RuleService._sync_latest_history_rows_with_quote("600519", history, quote)
    frame = build_metric_frame(rows, quote)
    latest = frame.iloc[-1]

    assert latest["date"] == "2025-04-01"
    assert round(latest["pe_ratio"], 6) == round(previous_pe * 1.1, 6)
    assert latest["pe_ratio_percentile_250d"] == 100
    assert latest["total_shares"] == 100_000
    assert latest["float_shares"] == 80_000


def test_metric_frame_tracks_history_days_and_prior_breakout_count():
    base = date(2026, 1, 1)
    history = []
    for index in range(25):
        close = 10 + index * 0.1
        history.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000 + index * 10,
            "amount": close * (1000 + index * 10),
        })

    frame = build_metric_frame(history)

    assert frame.iloc[0]["history_trading_days_count"] == 1
    assert frame.iloc[-1]["history_trading_days_count"] == 25
    first_breakout_index = frame.index[frame["price_breakout_20d_signal"] == 1][0]
    assert frame.iloc[first_breakout_index]["prior_10d_breakout_20d_count"] == 0
    assert frame.iloc[first_breakout_index + 1]["prior_10d_breakout_20d_count"] == 1


def _trend_start_history(latest_volume=3000):
    base = date(2026, 1, 1)
    history = []
    for index in range(60):
        close = 10 + (index % 3) * 0.02
        history.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": 1000,
            "amount": close * 1000,
        })

    for offset, close in enumerate([11, 12, 13, 14], start=60):
        history.append({
            "date": (base + timedelta(days=offset)).isoformat(),
            "open": close - 0.2,
            "high": close + 0.2,
            "low": close - 0.5,
            "close": close,
            "volume": 1200,
            "amount": close * 1200,
        })

    history.append({
        "date": (base + timedelta(days=64)).isoformat(),
        "open": 14.2,
        "high": 15.0,
        "low": 14.1,
        "close": 14.8,
        "volume": latest_volume,
        "amount": 14.8 * latest_volume,
    })
    return history


def _live_trend_setup_history(latest_close=11.0, latest_volume=1800):
    base = date(2026, 1, 1)
    history = []
    for index in range(60):
        close = 10 + (index % 5) * 0.03 - (0.02 if index % 2 else 0)
        history.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close - 0.02,
            "high": close + 0.08,
            "low": close - 0.08,
            "close": close,
            "volume": 1000,
            "amount": close * 1000,
        })

    for offset, close in enumerate([10.15, 10.05, 10.25, 10.20, 10.42], start=60):
        history.append({
            "date": (base + timedelta(days=offset)).isoformat(),
            "open": close - 0.05,
            "high": close + 0.08,
            "low": close - 0.1,
            "close": close,
            "volume": 1100,
            "amount": close * 1100,
        })

    history.append({
        "date": (base + timedelta(days=65)).isoformat(),
        "open": 10.48 if latest_close >= 10 else 10.2,
        "high": max(10.25, latest_close + 0.05),
        "low": min(10.45, latest_close - 0.05),
        "close": latest_close,
        "volume": latest_volume,
        "amount": latest_close * latest_volume,
    })
    return history


def test_metric_frame_calculates_trend_start_signal():
    frame = build_metric_frame(_trend_start_history())
    latest = frame.iloc[-1]

    assert latest["ma_bullish_alignment_signal"] == 1
    assert latest["ma_uptrend_signal"] == 1
    assert latest["price_breakout_20d_signal"] == 1
    assert latest["price_breakout_60d_signal"] == 1
    assert latest["volume_expansion_signal"] == 1
    assert latest["macd_dif"] > latest["macd_dea"] > 0
    assert 0 < latest["bias_ma5_pct"] <= 15
    assert latest["trend_start_signal"] == 1


def test_metric_frame_calculates_live_trend_confirm_signal_without_future_returns():
    frame = build_metric_frame(_live_trend_setup_history())
    latest = frame.iloc[-1]

    assert latest["price_breakout_20d_signal"] == 1
    assert latest["volume_expansion_20d_ratio"] >= 1.2
    assert latest["macd_dif"] > latest["macd_dea"] > 0
    assert latest["trend_live_setup_score"] >= 9
    assert latest["trend_live_watch_signal"] == 1
    assert latest["trend_live_confirm_signal"] == 1
    assert latest["trend_overheat_risk_signal"] == 0
    assert latest["trend_failure_signal"] == 0


def test_metric_frame_blocks_live_trend_confirm_when_overheated():
    frame = build_metric_frame(_trend_start_history())
    latest = frame.iloc[-1]

    assert latest["trend_live_setup_score"] >= 7
    assert latest["trend_overheat_risk_score"] >= 3
    assert latest["trend_overheat_risk_signal"] == 1
    assert latest["trend_live_watch_signal"] == 0
    assert latest["trend_live_confirm_signal"] == 0


def test_metric_frame_flags_live_trend_failure_after_losing_ma20():
    frame = build_metric_frame(_live_trend_setup_history(latest_close=9.7, latest_volume=1200))
    latest = frame.iloc[-1]

    assert latest["close"] < latest["ma20"]
    assert latest["trend_failure_signal"] == 1
    assert latest["trend_live_watch_signal"] == 0
    assert latest["trend_live_confirm_signal"] == 0


def test_rule_engine_matches_trend_start_signal_metric():
    frame = build_metric_frame(_trend_start_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "trend_start_signal"},
                        "operator": "=",
                        "right": {"type": "literal", "value": 1},
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    assert result["matched_groups"][0]["conditions"][0]["values"]["left"] == 1


def test_rule_engine_matches_live_trend_confirm_signal_metric():
    frame = build_metric_frame(_live_trend_setup_history())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "trend_live_confirm_signal"},
                        "operator": "=",
                        "right": {"type": "literal", "value": 1},
                    }
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)

    assert result["matched"] is True
    assert result["matched_groups"][0]["conditions"][0]["values"]["left"] == 1


def test_metric_frame_rejects_trend_start_without_volume_expansion():
    frame = build_metric_frame(_trend_start_history(latest_volume=900))
    latest = frame.iloc[-1]

    assert latest["ma_bullish_alignment_signal"] == 1
    assert latest["price_breakout_60d_signal"] == 1
    assert latest["volume_expansion_signal"] == 0
    assert latest["trend_start_signal"] == 0


def test_metric_frame_calculates_previous_window_cumulative_return_metrics():
    history = [
        {"date": "2026-04-01", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
        {"date": "2026-04-02", "open": 10, "high": 11, "low": 10, "close": 11, "volume": 1000, "amount": 11000, "pct_chg": 10},
        {"date": "2026-04-03", "open": 11, "high": 12, "low": 11, "close": 12, "volume": 1000, "amount": 12000, "pct_chg": 9.090909},
        {"date": "2026-04-04", "open": 12, "high": 13, "low": 12, "close": 13, "volume": 1000, "amount": 13000, "pct_chg": 8.333333},
        {"date": "2026-04-05", "open": 13, "high": 14, "low": 13, "close": 14, "volume": 1000, "amount": 14000, "pct_chg": 7.692308},
        {"date": "2026-04-06", "open": 14, "high": 15, "low": 14, "close": 15, "volume": 1000, "amount": 15000, "pct_chg": 7.142857},
        {"date": "2026-04-07", "open": 15, "high": 30, "low": 15, "close": 30, "volume": 1000, "amount": 30000, "pct_chg": 100},
    ]

    frame = build_metric_frame(history)
    latest = frame.iloc[-1]

    assert round(latest["prev_5d_return_pct"], 6) == round((15 / 10 - 1) * 100, 6)

    history_20 = [
        {
            "date": f"2026-05-{day + 1:02d}",
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1000,
            "amount": close * 1000,
        }
        for day, close in enumerate([100 + step for step in range(21)] + [200])
    ]
    frame_20 = build_metric_frame(history_20)
    latest_20 = frame_20.iloc[-1]

    assert round(latest_20["prev_20d_return_pct"], 6) == round((120 / 100 - 1) * 100, 6)


def test_rule_service_maps_net_profit_gap_event_to_announcement_next_trading_day():
    history = [
        {"date": "2026-04-13", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-14", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-15", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-16", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-17", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-20", "open": 10.4, "high": 11, "low": 10.2, "close": 10.8, "volume": 2000, "amount": 21600},
    ]
    events = [
        {
            "announcement_date": "2026-04-17",
            "deducted_net_profit_yoy_pct": 120,
            "deducted_net_profit_qoq_pct": 60,
        }
    ]
    enriched = RuleService._apply_earnings_gap_events_to_history(history, events)
    frame = build_metric_frame(enriched)
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "net-profit-gap",
                "conditions": [
                    {
                        "id": "c-yoy",
                        "left": {"metric": "deducted_net_profit_yoy_pct"},
                        "operator": ">=",
                        "right": {"type": "literal", "value": 100},
                    },
                    {
                        "id": "c-qoq",
                        "left": {"metric": "deducted_net_profit_qoq_pct"},
                        "operator": ">=",
                        "right": {"type": "literal", "value": 50},
                    },
                    {
                        "id": "c-gap",
                        "left": {"metric": "announcement_next_day_gap_pct"},
                        "operator": ">=",
                        "right": {"type": "literal", "value": 3},
                    },
                    {
                        "id": "c-volume",
                        "left": {"metric": "announcement_next_day_volume_ratio"},
                        "operator": ">=",
                        "right": {"type": "literal", "value": 1.5},
                    },
                    {
                        "id": "c-unfilled",
                        "left": {"metric": "announcement_next_day_gap_unfilled"},
                        "operator": "=",
                        "right": {"type": "literal", "value": 1},
                    },
                ],
            }
        ],
    }

    result = evaluate_rule(definition, frame)
    latest = frame.iloc[-1]

    assert result["matched"] is True
    assert round(latest["announcement_next_day_gap_pct"], 6) == 4
    assert latest["announcement_next_day_volume_ratio"] == 2
    assert latest["announcement_next_day_gap_unfilled"] == 1
    assert latest["net_profit_gap_signal"] == 1


def test_rule_service_syncs_earnings_gap_metrics_to_cache_and_db():
    class _Db:
        def __init__(self):
            self.calls = []

        def update_stock_daily_earnings_gap_metrics(self, stock_code, rows):
            self.calls.append((stock_code, [dict(row) for row in rows]))
            return 1

    db = _Db()
    stock_service = mock.Mock()
    stock_service.repo.db = db
    service = RuleService(repo=mock.Mock(), stock_service=stock_service)
    history = [
        {"date": "2026-04-13", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-14", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-15", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-16", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-17", "open": 10, "high": 10.2, "low": 9.8, "close": 10, "volume": 1000, "amount": 10000},
        {"date": "2026-04-20", "open": 10.4, "high": 11, "low": 10.2, "close": 10.8, "volume": 2000, "amount": 21600},
    ]
    scan_cache = {
        "history_by_code": {
            "600519": {
                "data": [dict(row) for row in history],
            },
        },
        "earnings_gap_metrics_by_code": {},
    }
    events = [
        {
            "announcement_date": "2026-04-17",
            "deducted_net_profit_yoy_pct": 120,
            "deducted_net_profit_qoq_pct": 60,
        }
    ]

    with mock.patch.object(service, "_get_earnings_gap_events_for_context", return_value=events):
        enriched = service._enrich_history_rows_with_earnings_gap_metrics(
            "600519",
            history,
            scan_cache=scan_cache,
        )

    target_row = next(row for row in enriched if row["date"] == "2026-04-20")
    cached_metrics = scan_cache["earnings_gap_metrics_by_code"]["600519"]["2026-04-20"]
    cached_history_row = scan_cache["history_by_code"]["600519"]["data"][-1]

    assert target_row["net_profit_gap_signal"] == 1
    assert cached_metrics["deducted_net_profit_yoy_pct"] == 120.0
    assert round(cached_metrics["announcement_next_day_gap_pct"], 6) == 4.0
    assert cached_history_row["net_profit_gap_signal"] == 1.0
    assert db.calls[0][0] == "600519"
    assert db.calls[0][1][-1]["net_profit_gap_signal"] == 1.0


def test_rule_service_uses_persisted_earnings_gap_metrics_without_fetching_events():
    class _PersistedEarningsGapStockService(_FakeStockService):
        def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
            return {
                "stock_code": stock_code,
                "stock_name": "测试股票",
                "period": period,
                "data": [
                    {"date": "2026-04-17", "open": 10, "high": 10, "low": 10, "close": 10, "volume": 1000, "amount": 10000},
                    {
                        "date": "2026-04-20",
                        "open": 10.4,
                        "high": 11,
                        "low": 10.2,
                        "close": 10.8,
                        "volume": 2000,
                        "amount": 21600,
                        "net_profit_gap_signal": 1,
                    },
                ],
            }

    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "net_profit_gap_signal"},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        }
    ]
    service = RuleService(repo=_FakeRuleRepo(rule), stock_service=_PersistedEarningsGapStockService())

    with mock.patch.object(
        service,
        "_get_earnings_gap_events_for_context",
        side_effect=AssertionError("rule scan must not fetch financial events"),
    ):
        result = service.run_rule(1, mode="history")

    assert result["status"] == "completed"
    assert result["event_count"] == 1
    assert result["matches"][0]["matched_dates"] == ["2026-04-20"]


def test_metric_frame_maps_realtime_quote_metrics_for_rules():
    frame = build_metric_frame(
        _history(),
        quote={
            "current_price": 14.5,
            "change": 0.5,
            "change_percent": 3.57,
            "volume": 2600,
            "amount": 37700,
            "after_hours_volume": 120,
            "after_hours_amount": 1740,
            "total_mv": 1_450_000_000,
            "circ_mv": 1_160_000_000,
            "pe_ratio": 18.2,
            "total_shares": 100_000_000,
            "float_shares": 80_000_000,
            "limit_up_price": 15.4,
            "limit_down_price": 12.6,
            "price_speed": 0.42,
            "entrust_ratio": 11.5,
        },
    )
    latest = frame.iloc[-1]

    assert latest["current_price"] == 14.5
    assert latest["total_mv"] == 1_450_000_000
    assert latest["circ_mv"] == 1_160_000_000
    assert latest["pe_ratio"] == 18.2
    assert latest["after_hours_volume"] == 120
    assert latest["after_hours_amount"] == 1740
    assert latest["limit_up_price"] == 15.4
    assert latest["limit_down_price"] == 12.6
    assert latest["price_speed"] == 0.42
    assert latest["entrust_ratio"] == 11.5
    assert latest["main_net_volume_pct"] > 0


def test_metric_frame_maps_chip_snapshots_by_date():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "date": "2026-04-05",
                "profit_ratio": 0.99,
                "concentration_90": 0.05,
                "avg_cost": 14.2,
                "snapshots": [
                    {
                        "date": "2026-04-03",
                        "profit_ratio": 0.4097,
                        "concentration_90": 0.1167,
                        "avg_cost": 9.54,
                    },
                    {
                        "date": "2026-04-04",
                        "profit_ratio": 86,
                        "concentration_90": 14,
                        "avg_cost": 13.4,
                    },
                ],
            }
        },
    )

    snapshot_row = frame[frame["date"] == "2026-04-03"].iloc[0]
    latest_row = frame.iloc[-1]

    assert round(snapshot_row["profit_ratio"], 6) == 40.97
    assert round(snapshot_row["chip_concentration_90"], 6) == 11.67
    assert snapshot_row["avg_cost"] == 9.54
    assert latest_row["profit_ratio"] == 99
    assert latest_row["chip_concentration_90"] == 5
    assert latest_row["avg_cost"] == 14.2


def test_rule_engine_uses_dated_chip_snapshots_for_history_matches():
    frame = build_metric_frame(
        _history(),
        extra_metrics={
            "chip_distribution": {
                "date": "2026-04-05",
                "profit_ratio": 0.99,
                "concentration_90": 0.05,
                "avg_cost": 14.2,
                "snapshots": [
                    {
                        "date": "2026-04-03",
                        "profit_ratio": 0.4097,
                        "concentration_90": 0.1167,
                        "avg_cost": 9.54,
                    },
                    {
                        "date": "2026-04-04",
                        "profit_ratio": 0.20,
                        "concentration_90": 0.10,
                        "avg_cost": 11.0,
                    },
                ],
            }
        },
    )
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "profit_ratio"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 40},
                    },
                    {
                        "id": "c2",
                        "left": {"metric": "profit_ratio"},
                        "operator": "<",
                        "right": {"type": "literal", "value": 50},
                    },
                    {
                        "id": "c3",
                        "left": {"metric": "chip_concentration_90"},
                        "operator": "<",
                        "right": {"type": "literal", "value": 12},
                    },
                ],
            }
        ],
    }

    events = evaluate_rule_history(definition, frame)

    assert [event["date"] for event in events] == ["2026-04-03"]
    assert events[0]["matched_groups"][0]["conditions"][0]["left_metric"] == "profit_ratio"


def test_rule_service_accepts_sandwich_number_condition_without_right_value():
    service = RuleService(repo=mock.Mock(), stock_service=mock.Mock())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "sandwich_number",
                    }
                ],
            }
        ],
    }

    service.validate_definition(definition)


def test_rule_service_accepts_pair_number_condition_without_right_value():
    service = RuleService(repo=mock.Mock(), stock_service=mock.Mock())
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "current_price"},
                        "operator": "pair_number",
                    }
                ],
            }
        ],
    }

    service.validate_definition(definition)


class _FakeRuleRepo:
    def __init__(self, rule):
        self.rule = rule
        self.finished_matches = None

    def get_rule(self, rule_id):
        return self.rule if rule_id == self.rule["id"] else None

    def create_run(self, rule_id, target_count):
        return 101

    def finish_run(self, **kwargs):
        self.finished_matches = kwargs["matches"]
        return len(kwargs["matches"]), 12


class _FakeMultiRuleRepo(_FakeRuleRepo):
    def __init__(self, rules):
        self.rules = {rule["id"]: rule for rule in rules}
        self.finished_matches = None

    def get_rule(self, rule_id):
        return self.rules.get(rule_id)


class _ProgressRuleRepo(_FakeMultiRuleRepo):
    def __init__(self, rules):
        super().__init__(rules)
        self.progress_updates = []
        self.finished_status = None

    def create_run(self, rule_id, target_count, error=None):
        self.created_error = error
        self.created_target_count = target_count
        return 202

    def update_run_progress(self, **kwargs):
        self.progress_updates.append(kwargs)

    def finish_run(self, **kwargs):
        self.finished_status = kwargs["status"]
        self.finished_error = kwargs.get("error")
        self.finished_matches = kwargs["matches"]
        return len(kwargs["matches"]), 34


class _FakeStockService:
    def get_history_data(self, stock_code, period="daily", days=30):
        return {
            "stock_code": stock_code,
            "stock_name": "测试股票",
            "period": period,
            "data": [
                {"date": "2026-04-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
                {"date": "2026-04-02", "open": 10, "high": 16, "low": 10, "close": 15, "volume": 3000, "amount": 45000, "pct_chg": 50},
                {"date": "2026-04-03", "open": 15, "high": 15, "low": 9, "close": 10, "volume": 1200, "amount": 12000, "pct_chg": -33.33},
            ],
        }

    def get_realtime_quote(self, stock_code):
        return None

    def get_indicator_metrics(self, stock_code):
        return {}

    def get_realtime_quote_snapshot_info(self):
        return {}


class _RealtimeRuleStockService(_FakeStockService):
    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        if period == "1m":
            return {
                "stock_code": stock_code,
                "stock_name": "盛景微",
                "period": period,
                "data_source": "intraday_hot_table",
                "data": [
                    {
                        "date": "2026-05-08 09:31",
                        "open": 39.5,
                        "high": 39.5,
                        "low": 39.5,
                        "close": 39.5,
                        "volume": 200_000,
                        "amount": 790_000_000,
                        "change_percent": 2.73,
                        "snapshot_id": "20260508093100",
                        "snapshot_time": "2026-05-08T09:31:00",
                        "data_source": "intraday_hot_table",
                    }
                ],
            }
        return {
            "stock_code": stock_code,
            "stock_name": "盛景微",
            "period": period,
            "data": [
                {"date": "2026-05-04", "open": 38, "high": 39, "low": 37, "close": 38, "volume": 40000, "amount": 152000000, "pct_chg": 0},
                {"date": "2026-05-05", "open": 38, "high": 39, "low": 37, "close": 38, "volume": 50000, "amount": 190000000, "pct_chg": 0},
                {"date": "2026-05-06", "open": 38, "high": 39, "low": 37, "close": 38, "volume": 60000, "amount": 228000000, "pct_chg": 0},
                {"date": "2026-05-07", "open": 38, "high": 39, "low": 37, "close": 38, "volume": 70000, "amount": 266000000, "pct_chg": 0},
            ],
        }

    def get_realtime_quote(self, stock_code, data_policy="default"):
        return {
            "stock_code": stock_code,
            "stock_name": "盛景微",
            "current_price": 39.5,
            "open": 38.06,
            "high": 39.57,
            "low": 38.0,
            "prev_close": 38.45,
            "volume": 20_000_000,
            "amount": 790_000_000,
            "change_percent": 2.73,
            "quote_time": "2026-05-07T15:00:00",
            "snapshot_id": "20260508091506",
            "snapshot_time": "2026-05-08T09:15:06",
        }


def _service_rule_for_run_mode():
    return {
        "id": 1,
        "name": "测试规则",
        "lookback_days": 36500,
        "definition": {
            "period": "daily",
            "lookback_days": 36500,
            "target": {"scope": "custom", "stock_codes": ["600519"]},
            "groups": [
                {
                    "id": "g1",
                    "conditions": [
                        {
                            "id": "c1",
                            "left": {"metric": "close", "offset": 0},
                            "operator": ">",
                            "right": {"type": "literal", "value": 12},
                        }
                    ],
                }
            ],
        },
    }


def _service_rule_for_codes(codes):
    rule = _service_rule_for_run_mode()
    rule["definition"] = {
        **rule["definition"],
        "target": {"scope": "custom", "stock_codes": codes},
    }
    return rule


def test_rule_service_batch_run_key_changes_when_rule_definition_changes():
    first_rule = _service_rule_for_codes(["600519"])
    second_rule = _service_rule_for_codes(["600519"])
    second_rule["definition"]["groups"][0]["conditions"][0]["right"]["value"] = 13

    first_fingerprint = RuleService._build_rule_definition_fingerprints([
        (1, first_rule, first_rule["definition"], ["600519"]),
    ])
    second_fingerprint = RuleService._build_rule_definition_fingerprints([
        (1, second_rule, second_rule["definition"], ["600519"]),
    ])
    first_key = RuleService._build_batch_run_key(
        [1],
        first_fingerprint,
        "history",
        "db_only",
        date(2026, 1, 1),
        date(2026, 6, 7),
        ["600519"],
        None,
    )
    second_key = RuleService._build_batch_run_key(
        [1],
        second_fingerprint,
        "history",
        "db_only",
        date(2026, 1, 1),
        date(2026, 6, 7),
        ["600519"],
        None,
    )

    assert first_fingerprint != second_fingerprint
    assert first_key != second_key


def test_rule_service_history_events_passes_only_requested_date_indices(monkeypatch):
    frame = build_metric_frame(_history())
    definition = {
        "period": "daily",
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 0},
                    }
                ],
            }
        ],
    }
    captured = {}

    def fake_evaluate_rule_history(_definition, metric_frame, indices=None):
        captured["indices"] = list(indices or [])
        return [
            {
                "date": str(metric_frame.iloc[index].get("date")),
                "index": index,
                "matched_groups": [{"id": "g1", "matched": True, "conditions": []}],
                "condition_results": [],
                "snapshot": {},
            }
            for index in captured["indices"]
        ]

    monkeypatch.setattr(rule_service_module, "evaluate_rule_history", fake_evaluate_rule_history)
    service = RuleService.__new__(RuleService)

    events = service._evaluate_history_events(
        definition,
        frame,
        lookback_days=120,
        start_date=date(2026, 4, 3),
        end_date=date(2026, 4, 4),
    )

    assert captured["indices"] == [2, 3]
    assert [event["date"] for event in events] == ["2026-04-03", "2026-04-04"]


class _PartiallyFailingStockService(_FakeStockService):
    def get_history_data(self, stock_code, period="daily", days=30):
        if stock_code == "000001":
            raise RuntimeError("boom")
        return super().get_history_data(stock_code, period=period, days=days)


class _SnapshotMissingStockService(_FakeStockService):
    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        return {
            "stock_code": stock_code,
            "stock_name": "测试股票",
            "period": period,
            "data": [],
            "data_source": "daily_cache_miss",
        }

    def get_realtime_quote(self, stock_code, data_policy="default"):
        return None


class _SnapshotMissingWithIntradayHotTableStockService(_FakeStockService):
    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        if period == "1m":
            return {
                "stock_code": stock_code,
                "stock_name": "热表股票",
                "period": period,
                "data_source": "intraday_hot_table",
                "data": [
                    {
                        "date": "2026-05-08 09:30",
                        "open": 10,
                        "high": 10.5,
                        "low": 9.8,
                        "close": 10.2,
                        "volume": 1000,
                        "amount": 10200,
                        "change_percent": 1,
                        "snapshot_id": "20260508093000",
                        "snapshot_time": "2026-05-08T09:30:00",
                        "data_source": "intraday_hot_table",
                    },
                    {
                        "date": "2026-05-08 09:31",
                        "open": 10.2,
                        "high": 16.2,
                        "low": 10.1,
                        "close": 16,
                        "volume": 3000,
                        "amount": 48000,
                        "change_percent": 60,
                        "snapshot_id": "20260508093100",
                        "snapshot_time": "2026-05-08T09:31:00",
                        "data_source": "intraday_hot_table",
                    },
                ],
            }
        return {
            "stock_code": stock_code,
            "stock_name": "热表股票",
            "period": period,
            "data": [
                {"date": "2026-05-06", "open": 9, "high": 10, "low": 8, "close": 9, "volume": 1000, "amount": 9000, "pct_chg": 0},
                {"date": "2026-05-07", "open": 9, "high": 10, "low": 8, "close": 9, "volume": 1000, "amount": 9000, "pct_chg": 0},
            ],
        }

    def get_realtime_quote(self, stock_code, data_policy="default"):
        return None


class _PolicyRecordingStockService(_FakeStockService):
    def __init__(self):
        self.history_policies = []

    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        self.history_policies.append(data_policy)
        return super().get_history_data(stock_code, period=period, days=days)


class _NotifyRuleRepo:
    def __init__(self, matches, previous_signature=None, previous_run_id=None, previous_keys=None):
        self.matches = matches
        self.previous_signature = previous_signature
        self.previous_run_id = previous_run_id
        self.previous_keys = previous_keys

    def list_matches(self, run_id):
        return self.matches

    def get_previous_live_match_signature(self, run_id):
        if not self.previous_signature:
            return None
        return {
            "run_id": self.previous_run_id,
            "signature": self.previous_signature,
        }

    def get_previous_live_match_keys(self, run_id):
        if not self.previous_keys:
            return None
        return {
            "run_ids": [self.previous_run_id] if self.previous_run_id else [],
            "keys": self.previous_keys,
        }


class _ConcurrentProbeStockService(_FakeStockService):
    def __init__(self):
        self._lock = threading.Lock()
        self._active_calls = 0
        self.max_active_calls = 0

    def get_history_data(self, stock_code, period="daily", days=30):
        with self._lock:
            self._active_calls += 1
            self.max_active_calls = max(self.max_active_calls, self._active_calls)
        try:
            time.sleep(0.05)
            return super().get_history_data(stock_code, period=period, days=days)
        finally:
            with self._lock:
                self._active_calls -= 1


class _CountingStockService(_FakeStockService):
    def __init__(self):
        self.history_calls = []
        self.indicator_calls = []

    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        self.history_calls.append((stock_code, period, days, data_policy))
        return super().get_history_data(stock_code, period=period, days=days)

    def get_indicator_metrics(self, stock_code):
        self.indicator_calls.append(stock_code)
        return {}


class _BatchHistoryOnlyStockService(_FakeStockService):
    def __init__(self):
        self.batch_history_calls = []
        self.per_stock_history_calls = []

    def get_daily_history_cache_batch(self, stock_codes, days_by_code, *, data_policy="snapshot_only"):
        self.batch_history_calls.append({
            "stock_codes": list(stock_codes),
            "days_by_code": dict(days_by_code),
            "data_policy": data_policy,
        })
        return {
            code: {
                "stock_code": code,
                "stock_name": "测试股票",
                "period": "daily",
                "data": [
                    {"date": "2026-04-01", "open": 10, "high": 11, "low": 9, "close": 10, "volume": 1000, "amount": 10000, "pct_chg": 0},
                    {"date": "2026-04-02", "open": 10, "high": 16, "low": 10, "close": 15, "volume": 3000, "amount": 45000, "pct_chg": 50},
                    {"date": "2026-04-03", "open": 15, "high": 15, "low": 9, "close": 10, "volume": 1200, "amount": 12000, "pct_chg": -33.33},
                ],
                "data_source": "db_cache",
            }
            for code in stock_codes
        }

    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        self.per_stock_history_calls.append((stock_code, period, days, data_policy))
        raise AssertionError("rule scan should use preloaded batch history")


class _PreopenBatchHistoryStockService(_BatchHistoryOnlyStockService):
    def get_daily_history_cache_batch(self, stock_codes, days_by_code, *, data_policy="snapshot_only"):
        payload = super().get_daily_history_cache_batch(
            stock_codes,
            days_by_code,
            data_policy=data_policy,
        )
        for item in payload.values():
            item["data"].append({
                "date": "2026-05-08",
                "open": 20,
                "high": 21,
                "low": 19,
                "close": 20,
                "volume": 2000,
                "amount": 40000,
                "pct_chg": 0,
            })
        return payload

    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        if period == "1m":
            return {
                "stock_code": stock_code,
                "stock_name": "测试股票",
                "period": period,
                "data": [],
                "data_source": "intraday_hot_table",
            }
        return super().get_history_data(stock_code, period=period, days=days, data_policy=data_policy)


class _BatchHistoryWithIntradayQuoteDb:
    def __init__(self):
        self.quote_batch_calls = []

    def get_intraday_minute_latest_quotes_batch(self, codes, *, trade_date=None):
        self.quote_batch_calls.append((list(codes), trade_date))
        return {
            code: {
                "stock_code": code,
                "stock_name": "测试股票",
                "current_price": 16,
                "open": 15,
                "high": 17,
                "low": 14,
                "volume": 3000,
                "amount": 48000,
                "change_percent": 6.67,
                "quote_time": "2026-05-08T10:00:00",
                "snapshot_id": "slow-path-batch-snapshot",
                "snapshot_time": "2026-05-08T10:00:00",
                "source": "intraday_hot_table",
            }
            for code in codes
        }


class _BatchHistoryWithIntradayQuoteStockService(_BatchHistoryOnlyStockService):
    def __init__(self):
        super().__init__()
        self.db = _BatchHistoryWithIntradayQuoteDb()
        self.repo = mock.Mock(db=self.db)


class _FastLatestRuleDb:
    def __init__(self):
        self.quote_batch_calls = []
        self.chip_batch_calls = []

    def get_intraday_minute_snapshot_summary(self, *, trade_date=None):
        return {
            "codes": ["600519", "000001"],
            "snapshot_id": "fast-snapshot",
            "snapshot_time": "2026-05-08T10:00:00",
        }

    def get_intraday_minute_latest_quotes_batch(self, codes, *, trade_date=None):
        self.quote_batch_calls.append((list(codes), trade_date))
        return {
            "600519": {
                "stock_code": "600519",
                "stock_name": None,
                "current_price": 15.0,
                "open": 12.0,
                "high": 15.2,
                "low": 11.8,
                "volume": 1000,
                "amount": 15000,
                "turnover_rate": 100,
                "change_percent": 25,
                "quote_time": "2026-05-08T10:00:00",
                "snapshot_id": "fast-snapshot",
                "snapshot_time": "2026-05-08T10:00:00",
                "source": "intraday_hot_table",
            },
            "000001": {
                "stock_code": "000001",
                "stock_name": "平安银行",
                "current_price": 8.0,
                "open": 8.0,
                "high": 8.1,
                "low": 7.9,
                "volume": 1000,
                "amount": 8000,
                "turnover_rate": 1,
                "change_percent": 0,
                "quote_time": "2026-05-08T10:00:00",
                "snapshot_id": "fast-snapshot",
                "snapshot_time": "2026-05-08T10:00:00",
                "source": "intraday_hot_table",
            },
        }

    def get_latest_chip_daily_batch(self, codes, *, as_of=None):
        self.chip_batch_calls.append((list(codes), as_of))
        return {
            "600519": {
                "code": "600519",
                "date": "2026-05-07",
                "source": "stock_chip_daily",
                "profit_ratio": 0.1,
                "distribution": [
                    {"price": 10.0, "percent": 0.9},
                    {"price": 20.0, "percent": 0.1},
                ],
                "chip_single_peak_signal": 1,
                "chip_peak_low_price": 10.0,
                "chip_peak_high_price": 10.0,
                "chip_peak_price_ratio": 1.0,
            },
            "000001": {
                "code": "000001",
                "date": "2026-05-07",
                "source": "stock_chip_daily",
                "profit_ratio": 0.9,
                "distribution": [
                    {"price": 10.0, "percent": 0.9},
                    {"price": 20.0, "percent": 0.1},
                ],
                "chip_single_peak_signal": 1,
                "chip_peak_low_price": 10.0,
                "chip_peak_high_price": 10.0,
                "chip_peak_price_ratio": 1.0,
            },
        }


class _FastLatestRuleStockRepo:
    def __init__(self, db):
        self.db = db


class _FastLatestRuleStockService:
    def __init__(self):
        self.db = _FastLatestRuleDb()
        self.repo = _FastLatestRuleStockRepo(self.db)
        self.batch_history_calls = []

    def get_daily_history_cache_batch(self, stock_codes, days_by_code, *, data_policy="snapshot_only"):
        self.batch_history_calls.append((list(stock_codes), dict(days_by_code), data_policy))
        raise AssertionError("fast latest scan should not load daily history")

    def get_history_data(self, stock_code, period="daily", days=30, data_policy="default"):
        raise AssertionError("fast latest scan should not load per-stock history")

    def get_realtime_quote_snapshot_info(self):
        return {}


def test_rule_service_run_modes_separate_latest_from_history():
    repo = _FakeRuleRepo(_service_rule_for_run_mode())
    service = RuleService(repo=repo, stock_service=_FakeStockService())

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        latest_result = service.run_rule(1, mode="latest")
    history_result = service.run_rule(1, mode="history")

    assert latest_result["mode"] == "latest"
    assert latest_result["match_count"] == 0
    assert latest_result["event_count"] == 0
    assert history_result["mode"] == "history"
    assert history_result["match_count"] == 1
    assert history_result["event_count"] == 1
    assert history_result["matches"][0]["matched_dates"] == ["2026-04-02"]
    assert history_result["matches"][0]["matched_events"][0]["date"] == "2026-04-02"
    assert history_result["matches"][0]["matched_events"][0]["snapshot"]["close"] == 15


def test_rule_service_latest_mode_uses_db_intraday_day_against_previous_window():
    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "volume", "offset": 0},
            "operator": ">",
            "right": {
                "type": "aggregate",
                "metric": "volume",
                "method": "avg",
                "window": 3,
                "offset": 1,
                "multiplier": 2,
            },
        }
    ]
    service = RuleService(repo=_FakeRuleRepo(rule), stock_service=_RealtimeRuleStockService())

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        result = service.run_rule(1, mode="latest", data_policy="snapshot_only")

    assert result["event_count"] == 1
    event = result["matches"][0]["matched_events"][0]
    values = event["matched_groups"][0]["conditions"][0]["values"]
    assert event["date"] == "2026-05-08"
    assert values["left"] == 200000
    assert values["right"] == 120000
    assert result["matches"][0]["matched_dates"] == ["2026-05-08"]


def test_rule_service_latest_mode_reprices_chip_metrics_for_live_row():
    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "profit_ratio", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 80},
        },
        {
            "id": "c2",
            "left": {"metric": "chip_single_peak_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        },
    ]
    service = RuleService(repo=_FakeRuleRepo(rule), stock_service=_RealtimeRuleStockService())
    service._build_history_chip_metrics = mock.Mock(return_value={
        "chip_distribution": {
            "date": "2026-05-07",
            "profit_ratio": 0.2,
            "source": "stock_chip_daily",
            "distribution": [
                {"price": 36.0, "percent": 0.2},
                {"price": 37.5, "percent": 0.6},
                {"price": 39.0, "percent": 0.2},
            ],
            "snapshots": [
                {
                    "date": "2026-05-07",
                    "profit_ratio": 0.2,
                    "distribution": [
                        {"price": 36.0, "percent": 0.2},
                        {"price": 37.5, "percent": 0.6},
                        {"price": 39.0, "percent": 0.2},
                    ],
                }
            ],
        }
    })

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        result = service.run_rule(1, mode="latest", data_policy="snapshot_only")

    assert result["event_count"] == 1
    event = result["matches"][0]["matched_events"][0]
    assert event["date"] == "2026-05-08"
    snapshot = event["snapshot"]
    assert snapshot["profit_ratio"] == 100
    assert snapshot["chip_single_peak_signal"] == 1
    assert snapshot["data_source"] == "intraday_hot_table"
    assert service._build_history_chip_metrics.call_count == 2


def test_rule_service_latest_mode_recomputes_chip_shape_after_live_row():
    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "profit_ratio", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 80},
        },
        {
            "id": "c2",
            "left": {"metric": "chip_single_peak_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        },
        {
            "id": "c3",
            "left": {"metric": "chip_peak_price_ratio", "offset": 0},
            "operator": "<=",
            "right": {"type": "literal", "value": 1.1},
        },
    ]
    service = RuleService(repo=_FakeRuleRepo(rule), stock_service=_RealtimeRuleStockService())

    def _chip_metrics_for_rows(_stock_code, rows):
        latest_date = rows[-1]["date"]
        if latest_date == "2026-05-08":
            return {
                "chip_distribution": {
                    "date": "2026-05-08",
                    "profit_ratio": 1.0,
                    "source": "local_chip_model:rule_backtest",
                    "distribution": [
                        {"price": 38.5, "percent": 0.1},
                        {"price": 39.0, "percent": 0.8},
                        {"price": 39.5, "percent": 0.1},
                    ],
                }
            }
        return {
            "chip_distribution": {
                "date": "2026-05-07",
                "profit_ratio": 0.2,
                "source": "local_chip_model:rule_backtest",
                "distribution": [
                    {"price": 36.0, "percent": 0.45},
                    {"price": 37.5, "percent": 0.1},
                    {"price": 39.0, "percent": 0.45},
                ],
            }
        }

    service._build_history_chip_metrics = mock.Mock(side_effect=_chip_metrics_for_rows)

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        result = service.run_rule(1, mode="latest", data_policy="snapshot_only")

    assert result["event_count"] == 1
    snapshot = result["matches"][0]["matched_events"][0]["snapshot"]
    assert snapshot["profit_ratio"] == 100
    assert snapshot["chip_single_peak_signal"] == 1
    assert snapshot["chip_peak_price_ratio"] == 1
    assert service._build_history_chip_metrics.call_count == 2


def test_rule_service_async_latest_fast_scan_uses_chip_daily_without_history_prewarm():
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    try:
        rule = _service_rule_for_codes(["600519", "000001"])
        rule["id"] = 10
        rule["name"] = "获利盘80"
        rule["definition"]["groups"][0]["conditions"] = [
            {
                "id": "cond-profit-ratio-gt-80",
                "left": {"metric": "profit_ratio", "offset": 0},
                "operator": ">",
                "right": {"type": "literal", "value": 80},
            },
        ]
        repo = _ProgressRuleRepo([rule])
        stock_service = _FastLatestRuleStockService()
        service = RuleService(repo=repo, stock_service=stock_service)
        service._resolve_run_workers = lambda target_count: 1
        service._build_history_chip_metrics = mock.Mock(side_effect=AssertionError("chip model should be skipped"))
        live_cache_key = "fast-latest-session"

        with mock.patch(
            "src.services.rule_service.RuleService._is_cn_live_test_allowed",
            return_value=True,
        ), mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 10, 0),
        ), mock.patch(
            "src.services.rule_service.StockService._get_local_stock_name",
            return_value="贵州茅台",
        ):
            for _ in range(2):
                response, context = service.start_run_rules(
                    [10],
                    mode="latest",
                    target_override={"scope": "custom", "stock_codes": ["600519", "000001"]},
                    data_policy="default",
                    live_cache_key=live_cache_key,
                )
                service.complete_started_run_rules(**context)

        assert response["status"] == "running"
        assert stock_service.batch_history_calls == []
        service._build_history_chip_metrics.assert_not_called()
        assert stock_service.db.quote_batch_calls == [
            (["600519", "000001"], date(2026, 5, 8)),
            (["600519", "000001"], date(2026, 5, 8)),
        ]
        assert stock_service.db.chip_batch_calls == [(["600519", "000001"], date(2026, 5, 8))]
        assert repo.finished_status == "completed"
        assert [match["stock_code"] for match in repo.finished_matches] == ["600519"]
        assert repo.finished_matches[0]["stock_name"] == "贵州茅台"
        snapshot = repo.finished_matches[0]["snapshot"]
        assert snapshot["profit_ratio"] == 90
        assert snapshot["snapshot_id"] == "fast-snapshot"
    finally:
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_rule_service_fast_latest_scan_refreshes_quotes_between_live_cycles():
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    stock_service = _FastLatestRuleStockService()
    service = RuleService(repo=object(), stock_service=stock_service)
    quote_calls = []

    def load_quotes(codes, *, trade_date=None):
        quote_calls.append((list(codes), trade_date))
        sequence = len(quote_calls)
        return {
            "600519": {
                "stock_code": "600519",
                "stock_name": None,
                "current_price": 14.0 + sequence,
                "open": 12.0,
                "high": 15.0 + sequence,
                "low": 11.8,
                "volume": 1000 + sequence,
                "amount": 15000 + sequence,
                "change_percent": 20 + sequence,
                "quote_time": f"2026-05-08T10:0{sequence}:00",
                "snapshot_id": f"fast-snapshot-{sequence}",
                "snapshot_time": f"2026-05-08T10:0{sequence}:00",
                "source": "intraday_hot_table",
            },
        }

    stock_service.db.get_intraday_minute_latest_quotes_batch = load_quotes
    try:
        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 10, 0),
        ):
            first_cache = service._prepare_fast_latest_scan_cache(
                ["600519"],
                "db_only",
                require_chip_metrics=False,
                live_cache_key="live-refresh-test",
            )
            second_cache = service._prepare_fast_latest_scan_cache(
                ["600519"],
                "db_only",
                require_chip_metrics=False,
                live_cache_key="live-refresh-test",
            )

        assert first_cache["quote_by_code"]["600519"]["snapshot_id"] == "fast-snapshot-1"
        assert second_cache["quote_by_code"]["600519"]["snapshot_id"] == "fast-snapshot-2"
        assert second_cache["quote_by_code"]["600519"]["current_price"] == 16.0
        assert quote_calls == [
            (["600519"], date(2026, 5, 8)),
            (["600519"], date(2026, 5, 8)),
        ]
        assert "quote_by_code" not in rule_service_module._LIVE_RULE_RUN_SCAN_CACHE["live-refresh-test"]
    finally:
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_rule_service_fast_latest_scan_skips_quote_miss_without_per_stock_fallback():
    rule = _service_rule_for_codes(["600519"])
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-close",
            "left": {"metric": "close", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 10},
        },
    ]
    repo = _ProgressRuleRepo([rule])
    stock_service = _FastLatestRuleStockService()
    stock_service.db.get_intraday_minute_latest_quotes_batch = lambda codes, *, trade_date=None: {}
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1
    service._build_intraday_hot_table_quote = mock.Mock(side_effect=AssertionError("per-stock fallback should be skipped"))

    with mock.patch(
        "src.services.rule_service.trading_calendar.get_market_now",
        return_value=datetime(2026, 5, 8, 10, 0),
    ):
        _response, context = service.start_run_rules(
            [1],
            mode="latest",
            target_override={"scope": "custom", "stock_codes": ["600519"]},
            data_policy="default",
            live_cache_key="quote-miss-fast-path",
        )
        service.complete_started_run_rules(**context)

    service._build_intraday_hot_table_quote.assert_not_called()
    assert repo.finished_status == "completed"
    assert repo.finished_matches == []
    metadata, public_error = _decode_rule_batch_metadata(repo.finished_error)
    assert public_error is None
    assert metadata["skip_counts"] == {"intraday_hot_table_miss": 1}
    assert metadata["skipped_count"] == 1


def test_rule_service_fast_latest_scan_computes_chip_shape_incrementally():
    rule = _service_rule_for_codes(["600519"])
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-single-peak",
            "left": {"metric": "chip_single_peak_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        },
        {
            "id": "c-peak-ratio",
            "left": {"metric": "chip_peak_price_ratio", "offset": 0},
            "operator": "<=",
            "right": {"type": "literal", "value": 1.5},
        },
    ]
    repo = _ProgressRuleRepo([rule])
    stock_service = _FastLatestRuleStockService()
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1
    service._build_history_chip_metrics = mock.Mock(side_effect=AssertionError("incremental fast path should not rebuild history"))

    with mock.patch(
        "src.services.rule_service.trading_calendar.get_market_now",
        return_value=datetime(2026, 5, 8, 10, 0),
    ):
        _response, context = service.start_run_rules(
            [1],
            mode="latest",
            target_override={"scope": "custom", "stock_codes": ["600519"]},
            data_policy="default",
            live_cache_key="chip-shape-fast-path",
        )
        service.complete_started_run_rules(**context)

    service._build_history_chip_metrics.assert_not_called()
    assert repo.finished_status == "completed"
    assert [match["stock_code"] for match in repo.finished_matches] == ["600519"]
    snapshot = repo.finished_matches[0]["snapshot"]
    assert snapshot["chip_single_peak_signal"] == 1
    assert snapshot["chip_peak_price_ratio"] <= 1.5
    assert repo.finished_matches[0]["matched_dates"] == ["2026-05-08"]


def test_rule_service_fast_latest_scan_records_skip_reason_when_incremental_chip_lacks_turnover():
    rule = _service_rule_for_codes(["600519"])
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-single-peak",
            "left": {"metric": "chip_single_peak_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        },
    ]
    repo = _ProgressRuleRepo([rule])
    stock_service = _FastLatestRuleStockService()
    original_quote_loader = stock_service.db.get_intraday_minute_latest_quotes_batch

    def load_quote_without_turnover(codes, *, trade_date=None):
        payload = original_quote_loader(codes, trade_date=trade_date)
        payload["600519"].pop("turnover_rate", None)
        return payload

    stock_service.db.get_intraday_minute_latest_quotes_batch = load_quote_without_turnover
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1

    with mock.patch(
        "src.services.rule_service.trading_calendar.get_market_now",
        return_value=datetime(2026, 5, 8, 10, 0),
    ):
        _response, context = service.start_run_rules(
            [1],
            mode="latest",
            target_override={"scope": "custom", "stock_codes": ["600519"]},
            data_policy="default",
            live_cache_key="chip-turnover-miss",
        )
        service.complete_started_run_rules(**context)

    metadata, public_error = _decode_rule_batch_metadata(repo.finished_error)
    assert public_error is None
    assert repo.finished_status == "completed"
    assert repo.finished_matches == []
    assert metadata["fast_latest_scan"] is True
    assert metadata["skipped_count"] == 1
    assert metadata["skip_counts"] == {"chip_turnover_miss": 1}


def test_rule_service_fast_latest_scan_invalidates_chip_cache_by_as_of_and_rejects_stale_chip():
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    stock_service = _FastLatestRuleStockService()
    service = RuleService(repo=object(), stock_service=stock_service)
    try:
        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 10, 0),
        ):
            first_cache = service._prepare_fast_latest_scan_cache(
                ["600519"],
                "db_only",
                require_chip_metrics=True,
                live_cache_key="chip-asof-test",
            )
        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 9, 10, 0),
        ):
            second_cache = service._prepare_fast_latest_scan_cache(
                ["600519"],
                "db_only",
                require_chip_metrics=True,
                live_cache_key="chip-asof-test",
            )
        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 20, 10, 0),
        ):
            stale_cache = service._prepare_fast_latest_scan_cache(
                ["600519"],
                "db_only",
                require_chip_metrics=True,
                live_cache_key="chip-asof-test",
            )

        assert "600519" in first_cache["chip_metrics_by_code"]
        assert "600519" in second_cache["chip_metrics_by_code"]
        assert "600519" not in stale_cache["chip_metrics_by_code"]
        assert stock_service.db.chip_batch_calls == [
            (["600519"], date(2026, 5, 8)),
            (["600519"], date(2026, 5, 9)),
            (["600519"], date(2026, 5, 20)),
        ]
    finally:
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_rule_service_fast_latest_scan_allows_incremental_chip_shape_but_rejects_not_exists_and_non_cn_targets():
    chip_shape_rule = _service_rule_for_codes(["600519"])
    chip_shape_rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-chip-shape",
            "left": {"metric": "chip_single_peak_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        },
    ]
    chip_not_exists_rule = _service_rule_for_codes(["600519"])
    chip_not_exists_rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-chip-not-exists",
            "left": {"metric": "profit_ratio", "offset": 0},
            "operator": "not_exists",
        },
    ]
    non_cn_rule = _service_rule_for_codes(["AAPL"])
    non_cn_rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c-close",
            "left": {"metric": "close", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 10},
        },
    ]

    assert RuleService._can_use_fast_latest_batch_scan(
        [(1, chip_shape_rule, chip_shape_rule["definition"], ["600519"])],
        "latest",
        "db_only",
    )
    assert not RuleService._can_use_fast_latest_batch_scan(
        [(1, chip_not_exists_rule, chip_not_exists_rule["definition"], ["600519"])],
        "latest",
        "db_only",
    )
    assert not RuleService._can_use_fast_latest_batch_scan(
        [(1, non_cn_rule, non_cn_rule["definition"], ["AAPL"])],
        "latest",
        "db_only",
    )


def test_rule_service_fast_latest_reprice_drops_profit_ratio_without_distribution():
    repriced = RuleService._reprice_chip_distribution_for_live_row(
        {
            "date": "2026-05-07",
            "profit_ratio": 0.9,
            "source": "stock_chip_daily",
            "distribution": [],
        },
        date(2026, 5, 8),
        5.0,
        force=True,
    )

    assert "profit_ratio" not in repriced
    assert repriced["date"] == "2026-05-08"
    assert "live_repriced" in repriced["source"]


def test_rule_service_fast_latest_scan_rejects_history_aggregate_rule():
    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "volume", "offset": 0},
            "operator": ">",
            "right": {
                "type": "aggregate",
                "metric": "volume",
                "method": "avg",
                "window": 3,
                "offset": 1,
                "multiplier": 2,
            },
        }
    ]

    assert not RuleService._can_use_fast_latest_batch_scan(
        [(1, rule, rule["definition"], ["600519"])],
        "latest",
        "db_only",
    )


def test_rule_service_fast_latest_scan_rejects_history_dependent_latest_metrics():
    rule = _service_rule_for_run_mode()
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "volume_ratio", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 2},
        }
    ]

    assert not RuleService._can_use_fast_latest_batch_scan(
        [(1, rule, rule["definition"], ["600519"])],
        "latest",
        "db_only",
    )


def test_rule_service_history_mode_respects_date_range():
    service = RuleService(repo=_FakeRuleRepo(_service_rule_for_run_mode()), stock_service=_FakeStockService())

    included = service.run_rule(1, mode="history", start_date="2026-04-02", end_date="2026-04-02")
    excluded = service.run_rule(1, mode="history", start_date="2026-04-03", end_date="2026-04-03")

    assert included["event_count"] == 1
    assert included["matches"][0]["matched_dates"] == ["2026-04-02"]
    assert excluded["event_count"] == 0
    assert excluded["matches"] == []


def test_rule_service_run_rule_keeps_stock_order_with_worker_errors():
    repo = _FakeRuleRepo(_service_rule_for_codes(["600519", "000001", "AAPL"]))
    service = RuleService(repo=repo, stock_service=_PartiallyFailingStockService())

    def use_two_workers(target_count):
        return 2

    service._resolve_run_workers = use_two_workers

    result = service.run_rule(1, mode="history")

    assert result["status"] == "partial"
    assert [match["stock_code"] for match in result["matches"]] == ["600519", "AAPL"]
    assert result["errors"] == ["000001:RuntimeError"]
    assert [match["stock_code"] for match in repo.finished_matches] == ["600519", "AAPL"]


def test_rule_service_run_rules_treats_snapshot_cache_miss_as_empty_result():
    first_rule = _service_rule_for_codes(["600519", "000001"])
    second_rule = {**_service_rule_for_codes(["600519", "000001"]), "id": 2, "name": "第二条规则"}
    repo = _FakeMultiRuleRepo([first_rule, second_rule])
    service = RuleService(repo=repo, stock_service=_SnapshotMissingStockService())
    service._resolve_run_workers = lambda target_count: 1
    service._resolve_batch_rule_workers = lambda rule_count: 1

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        result = service.run_rules([1, 2], mode="latest", data_policy="snapshot_only")

    assert result["status"] == "completed"
    assert result["matches"] == []
    assert result["errors"] == []
    assert repo.finished_matches == []


def test_rule_service_latest_snapshot_miss_uses_intraday_hot_table_fallback():
    rule = _service_rule_for_codes(["600519"])
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "close", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 12},
        }
    ]
    service = RuleService(
        repo=_FakeRuleRepo(rule),
        stock_service=_SnapshotMissingWithIntradayHotTableStockService(),
    )

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ):
        result = service.run_rule(1, mode="latest", data_policy="snapshot_only")

    assert result["event_count"] == 1
    assert result["snapshot_id"] == "20260508093100"
    event = result["matches"][0]["matched_events"][0]
    assert event["date"] == "2026-05-08"
    assert event["snapshot"]["close"] == 16
    assert event["snapshot"]["current_price"] == 16
    assert event["snapshot"]["data_source"] == "intraday_hot_table"


def test_rule_service_live_snapshot_a_share_scan_rejects_closed_session():
    repo = _FakeRuleRepo(_service_rule_for_codes(["600519", "000001"]))
    service = RuleService(repo=repo, stock_service=_RealtimeRuleStockService())

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=False,
    ):
        try:
            service.run_rule(1, mode="latest", data_policy="snapshot_only")
        except RuleValidationError as exc:
            assert "15:00" in str(exc)
        else:
            raise AssertionError("expected RuleValidationError")


def test_rule_service_live_test_window_allows_preopen_and_lunch_before_15():
    with mock.patch("src.services.rule_service.trading_calendar.is_market_open", return_value=True):
        assert RuleService._is_cn_live_test_allowed(datetime(2026, 5, 8, 8, 45))
        assert RuleService._is_cn_live_test_allowed(datetime(2026, 5, 8, 12, 0))
        assert RuleService._is_cn_live_test_allowed(datetime(2026, 5, 8, 15, 0))
        assert RuleService._is_cn_live_test_allowed(datetime(2026, 5, 8, 15, 0, 59))
        assert not RuleService._is_cn_live_test_allowed(datetime(2026, 5, 8, 15, 1))


def test_rule_service_history_mode_forces_db_only_data_policy():
    stock_service = _PolicyRecordingStockService()
    service = RuleService(repo=_FakeRuleRepo(_service_rule_for_run_mode()), stock_service=stock_service)

    result = service.run_rule(1, mode="history")

    assert result["status"] == "completed"
    assert stock_service.history_policies == ["db_only"]


def test_rule_service_history_db_only_cache_miss_is_empty_result():
    repo = _FakeRuleRepo(_service_rule_for_run_mode())
    service = RuleService(repo=repo, stock_service=_SnapshotMissingStockService())
    service._resolve_run_workers = lambda target_count: 1

    result = service.run_rule(1, mode="history", data_policy="db_only")

    assert result["status"] == "completed"
    assert result["matches"] == []
    assert result["errors"] == []
    assert repo.finished_matches == []


def test_rule_service_async_batch_updates_completed_stock_progress():
    first_rule = _service_rule_for_codes(["600519", "000001"])
    second_rule = {**_service_rule_for_codes(["600519", "000001"]), "id": 2, "name": "第二条规则"}
    repo = _ProgressRuleRepo([first_rule, second_rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())
    service._resolve_run_workers = lambda target_count: 1

    response, context = service.start_run_rules(
        [1, 2],
        mode="history",
        target_override={"scope": "custom", "stock_codes": ["600519", "000001"]},
    )
    service.complete_started_run_rules(**context)

    assert response["status"] == "running"
    assert response["target_count"] == 2
    assert [item["completed_count"] for item in repo.progress_updates] == [1, 2]
    assert repo.finished_status == "completed"
    assert len(repo.finished_matches) == 4
    finished_metadata, _ = _decode_rule_batch_metadata(repo.finished_error)
    assert finished_metadata.get("run_key")
    assert "rule_fingerprints" not in finished_metadata


def test_rule_service_async_batch_skips_locked_progress_and_finishes_run():
    class LockedOnceProgressRepo(_ProgressRuleRepo):
        def __init__(self, rules):
            super().__init__(rules)
            self.progress_attempts = 0

        def update_run_progress(self, **kwargs):
            self.progress_attempts += 1
            if self.progress_attempts == 1:
                raise OperationalError(
                    "BEGIN IMMEDIATE",
                    None,
                    sqlite3.OperationalError("database is locked"),
                )
            super().update_run_progress(**kwargs)

    rule = _service_rule_for_codes(["600519", "000001"])
    repo = LockedOnceProgressRepo([rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())
    service._resolve_run_workers = lambda target_count: 1

    _response, context = service.start_run_rules(
        [1],
        mode="history",
        target_override={"scope": "custom", "stock_codes": ["600519", "000001"]},
    )
    service.complete_started_run_rules(**context)

    assert repo.progress_attempts == 2
    assert [item["completed_count"] for item in repo.progress_updates] == [2]
    assert repo.finished_status == "completed"
    assert len(repo.finished_matches) == 2


def test_rule_service_async_batch_cleans_stale_runs_before_starting_new_run():
    class CleanupRuleRepo(_ProgressRuleRepo):
        def __init__(self, rules):
            super().__init__(rules)
            self.cleaned_stale_runs = False
            self.orphaned_cutoff = None

        def fail_running_runs_started_before(self, cutoff):
            self.orphaned_cutoff = cutoff
            return 1

        def fail_stale_running_runs(self):
            self.cleaned_stale_runs = True
            return 1

    rule = _service_rule_for_codes(["600519"])
    repo = CleanupRuleRepo([rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())

    response, context = service.start_run_rules(
        [1],
        mode="history",
        target_override={"scope": "custom", "stock_codes": ["600519"]},
    )

    assert response["status"] == "running"
    assert context is not None
    assert repo.orphaned_cutoff == rule_service_module.RULE_SERVICE_PROCESS_STARTED_AT
    assert repo.cleaned_stale_runs is True


def test_rule_service_async_batch_throttles_large_progress_and_keeps_run_metadata():
    codes = [f"600{index:03d}" for index in range(120)]
    rule = _service_rule_for_codes(codes)
    repo = _ProgressRuleRepo([rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())
    service._resolve_run_workers = lambda target_count: 1

    response, context = service.start_run_rules(
        [1],
        mode="history",
        target_override={"scope": "custom", "stock_codes": codes},
    )
    service.complete_started_run_rules(**context)

    assert response["status"] == "running"
    assert [item["completed_count"] for item in repo.progress_updates] == [100, 120]
    assert all((item.get("metadata") or {}).get("run_key") for item in repo.progress_updates)
    assert repo.finished_status == "completed"
    assert len(repo.finished_matches) == 120


def test_rule_service_async_batch_uses_larger_progress_step_for_full_market_like_runs():
    codes = [f"600{index:03d}" for index in range(600)]
    rule = _service_rule_for_codes(codes)
    repo = _ProgressRuleRepo([rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())
    service._resolve_run_workers = lambda target_count: 1
    service._resolve_progress_min_interval_seconds = lambda target_count, batch_size: 0.0

    _response, context = service.start_run_rules(
        [1],
        mode="history",
        target_override={"scope": "custom", "stock_codes": codes},
    )
    service.complete_started_run_rules(**context)

    assert [item["completed_count"] for item in repo.progress_updates] == [500, 600]
    assert repo.finished_status == "completed"
    assert len(repo.finished_matches) == 600


def test_rule_service_async_batch_reuses_preloaded_history_cache():
    rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
    try:
        codes = ["600519", "000001"]
        rule = _service_rule_for_codes(codes)
        repo = _ProgressRuleRepo([rule])
        stock_service = _BatchHistoryOnlyStockService()
        service = RuleService(repo=repo, stock_service=stock_service)
        service._resolve_run_workers = lambda target_count: 1

        for _ in range(2):
            _response, context = service.start_run_rules(
                [1],
                mode="history",
                target_override={"scope": "custom", "stock_codes": codes},
                data_policy="snapshot_only",
            )
            service.complete_started_run_rules(**context)

        assert len(stock_service.batch_history_calls) == 1
        assert stock_service.batch_history_calls[0]["data_policy"] == "db_only"
        assert stock_service.per_stock_history_calls == []
        assert repo.finished_status == "completed"
        assert len(repo.finished_matches) == 2
    finally:
        rule_service_module._RULE_RUN_HISTORY_CACHE.clear()


def test_rule_service_large_history_batch_skips_shared_history_cache():
    rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
    try:
        codes = [f"6{index:05d}" for index in range(rule_service_module.RULE_RUN_HISTORY_CACHE_MAX_STOCKS + 1)]
        stock_service = _BatchHistoryOnlyStockService()
        service = RuleService(repo=mock.Mock(), stock_service=stock_service)

        history = service._get_or_load_rule_history_cache(
            codes,
            {code: 120 for code in codes},
            "db_only",
        )

        assert len(stock_service.batch_history_calls) == 1
        assert len(history) == len(codes)
        assert rule_service_module._RULE_RUN_HISTORY_CACHE == {}
    finally:
        rule_service_module._RULE_RUN_HISTORY_CACHE.clear()


def test_rule_service_preopen_latest_only_prewarms_history_cache():
    rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    try:
        codes = ["600519", "000001"]
        rule = _service_rule_for_codes(codes)
        repo = _ProgressRuleRepo([rule])
        stock_service = _PreopenBatchHistoryStockService()
        service = RuleService(repo=repo, stock_service=stock_service)
        live_cache_key = "live-test-session"

        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 8, 45),
        ), mock.patch(
            "src.services.rule_service.trading_calendar.is_market_open",
            return_value=True,
        ):
            response, context = service.start_run_rules(
                [1],
                mode="latest",
                target_override={"scope": "custom", "stock_codes": codes},
                data_policy="default",
                live_cache_key=live_cache_key,
            )
            assert context is not None
            assert response["status"] == "running"
            assert response["prewarm_only"] is True
            assert response["prewarm_hit_count"] == 0
            assert response["prewarm_miss_count"] == 0
            assert response["quote_hit_count"] == 0
            assert response["quote_miss_count"] == 0
            assert stock_service.batch_history_calls == []
            service.complete_started_run_rules(**context)

            second_response, second_context = service.start_run_rules(
                [1],
                mode="latest",
                target_override={"scope": "custom", "stock_codes": codes},
                data_policy="default",
                live_cache_key=live_cache_key,
            )
            assert second_context is not None
            assert second_response["status"] == "running"
            assert second_response["prewarm_only"] is True
            service.complete_started_run_rules(**second_context)

        assert len(stock_service.batch_history_calls) == 1
        assert stock_service.batch_history_calls[0]["data_policy"] == "db_only"
        assert stock_service.per_stock_history_calls == []
        assert repo.finished_status == "completed"
        assert repo.finished_matches == []
        assert live_cache_key in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE
        assert live_cache_key in rule_service_module._LIVE_RULE_RUN_SCAN_CACHE

        cached_dates = [
            row["date"]
            for entry in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE[live_cache_key].values()
            for payload in (entry.get("history_by_code") or {}).values()
            for row in payload.get("data") or []
        ]
        assert "2026-05-08" not in cached_dates

        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 9, 35),
        ), mock.patch(
            "src.services.rule_service.trading_calendar.is_market_open",
            return_value=True,
        ):
            live_response, live_context = service.start_run_rules(
                [1],
                mode="latest",
                target_override={"scope": "custom", "stock_codes": codes},
                data_policy="default",
                live_cache_key=live_cache_key,
            )
            service.complete_started_run_rules(**live_context)

        assert live_response["status"] == "running"
        assert len(stock_service.batch_history_calls) == 1
        assert stock_service.per_stock_history_calls == []

        cleared = service.clear_live_rule_history_cache(live_cache_key)
        assert cleared["cleared_entries"] == 1
        assert live_cache_key not in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE
        assert live_cache_key not in rule_service_module._LIVE_RULE_RUN_SCAN_CACHE
    finally:
        rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_shared_live_cache_key_stays_stable_for_one_live_session():
    contexts = [
        {
            "tenant_key": "default",
            "live_cache_key": "tenant:default:live-session",
            "batch_metadata": {
                "raw_live_cache_key": "live-session",
                "snapshot_id": "snapshot-1",
            },
        },
        {
            "tenant_key": "quant_team",
            "live_cache_key": "tenant:quant_team:live-session",
            "batch_metadata": {
                "raw_live_cache_key": "live-session",
                "snapshot_id": "snapshot-1",
            },
        },
    ]

    first_key = rules_endpoint._build_shared_live_cache_key(contexts)
    second_key = rules_endpoint._build_shared_live_cache_key([
        {
            **context,
            "batch_metadata": {
                **context["batch_metadata"],
                "snapshot_id": "snapshot-2",
            },
        }
        for context in contexts
    ])

    assert first_key == second_key
    assert first_key is not None
    assert first_key.startswith("shared:live-session:")


def test_clear_live_rule_history_cache_removes_shared_session_cache():
    rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    tenant = TenantContext(id=1, key="default", name="默认租户", is_default=True)
    service = RuleService(repo=mock.Mock(), stock_service=mock.Mock(), tenant_context=tenant)
    tenant_key = "tenant:default:live-session"
    shared_key = "shared:live-session:abc123"
    other_shared_key = "shared:other-session:abc123"
    try:
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE[tenant_key] = {
            "tenant-history": {"history_by_code": {"600519": {"data": []}}},
        }
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE[tenant_key] = {
            "indicator_metrics_by_code": {"600519": {}},
        }
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE[shared_key] = {
            "shared-history": {"history_by_code": {"000001": {"data": []}}},
        }
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE[shared_key] = {
            "chip_metrics_by_code": {"600519": {}, "000001": {}},
        }
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE[other_shared_key] = {
            "other-history": {"history_by_code": {"300750": {"data": []}}},
        }

        cleared = service.clear_live_rule_history_cache("live-session")

        assert cleared["cleared_entries"] == 2
        assert cleared["cleared_scan_entries"] == 3
        assert tenant_key not in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE
        assert tenant_key not in rule_service_module._LIVE_RULE_RUN_SCAN_CACHE
        assert shared_key not in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE
        assert shared_key not in rule_service_module._LIVE_RULE_RUN_SCAN_CACHE
        assert other_shared_key in rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE
    finally:
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_rule_service_preopen_prewarm_metadata_persists_in_repository():
    DatabaseManager.reset_instance()
    rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        repo = RuleRepository(db)
        codes = ["600519", "000001"]
        rule_payload = _service_rule_for_codes(codes)
        created_rule = repo.create_rule({
            "name": "开盘前预热",
            "definition": rule_payload["definition"],
        })
        stock_service = _PreopenBatchHistoryStockService()
        service = RuleService(repo=repo, stock_service=stock_service)
        live_cache_key = "persisted-prewarm-session"

        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 8, 45),
        ), mock.patch(
            "src.services.rule_service.trading_calendar.is_market_open",
            return_value=True,
        ):
            response, context = service.start_run_rules(
                [created_rule["id"]],
                mode="latest",
                target_override={"scope": "custom", "stock_codes": codes},
                data_policy="default",
                live_cache_key=live_cache_key,
            )
            service.complete_started_run_rules(**context)

        persisted = repo.get_run(response["run_id"])

        assert persisted is not None
        assert persisted["status"] == "completed"
        assert persisted["prewarm_only"] is True
        assert persisted["prewarm_hit_count"] == 2
        assert persisted["prewarm_miss_count"] == 0
        assert persisted["completed_count"] == 2
        assert persisted["match_count"] == 0
    finally:
        DatabaseManager.reset_instance()
        rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_multi_tenant_async_rule_run_executes_shared_logic_once(monkeypatch):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        with db.get_session() as session:
            rule = StockRule(
                name="共享逻辑",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition(stock_codes=["600519", "000001"])),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        calls = []

        def fake_execute(
            self,
            run_id,
            prepared,
            stock_codes,
            run_mode,
            date_from,
            date_to,
            data_policy,
            rule_ids,
            rule_names,
            batch_metadata=None,
            live_cache_key=None,
            progress_callback=None,
        ):
            calls.append({
                "run_id": run_id,
                "rule_ids": list(rule_ids),
                "stock_codes": list(stock_codes),
                "live_cache_key": live_cache_key,
            })
            if progress_callback is not None:
                progress_callback(len(stock_codes), {"fast_latest_scan": False, "skip_counts": {}, "skipped_count": 0}, [])
            return ([{
                "rule_id": prepared[0][0],
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "matched_groups": [{"id": "g1"}],
                "matched_dates": ["2026-05-08"],
                "matched_events": [{"date": "2026-05-08"}],
                "snapshot": {"close": 100},
                "explanation": "hit",
            }], [])

        monkeypatch.setattr(RuleService, "_execute_batch_scan_by_stock", fake_execute)

        tenants = [
            TenantContext(
                id=default_tenant["id"],
                key=default_tenant["key"],
                name=default_tenant["name"],
                is_default=True,
            ),
            TenantContext(
                id=other_tenant["id"],
                key=other_tenant["key"],
                name=other_tenant["name"],
                is_default=False,
            ),
        ]
        payload = RuleBatchRunRequest(
            rule_ids=[rule_id],
            mode="history",
            target=RuleTarget(scope="custom", stock_codes=["600519", "000001"]),
            tenant_keys=["default", "quant_team"],
        )
        background_tasks = BackgroundTasks()

        response = rules_endpoint._start_async_rule_run_for_tenants(payload, tenants, background_tasks)
        for task in background_tasks.tasks:
            task.func(*task.args, **task.kwargs)

        assert len(calls) == 1
        assert calls[0]["run_id"] == 0
        assert calls[0]["stock_codes"] == ["600519", "000001"]
        assert response["run_ids"] == [item["run_id"] for item in response["tenant_runs"]]

        run_by_tenant = {item["tenant_key"]: item["run_id"] for item in response["tenant_runs"]}
        default_repo = RuleRepository(db, tenant_id=default_tenant["id"], tenant_key=default_tenant["key"])
        other_repo = RuleRepository(db, tenant_id=other_tenant["id"], tenant_key=other_tenant["key"])
        assert default_repo.get_run(run_by_tenant["default"])["status"] == "completed"
        assert other_repo.get_run(run_by_tenant["quant_team"])["status"] == "completed"
        assert default_repo.list_matches(run_by_tenant["default"])[0]["stock_code"] == "600519"
        assert other_repo.list_matches(run_by_tenant["quant_team"])[0]["stock_code"] == "600519"
        with db.get_session() as session:
            assert session.query(SharedRuleRun).count() == 1
            assert session.query(StockRuleMatchLink).count() == 2
    finally:
        DatabaseManager.reset_instance()


def test_concurrent_multi_tenant_async_batches_share_completed_execution(monkeypatch, tmp_path):
    DatabaseManager.reset_instance()
    db_path = tmp_path / "rules-concurrency.db"
    db = DatabaseManager(db_url=f"sqlite:///{db_path}")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        with db.get_session() as session:
            rule = StockRule(
                name="并发共享逻辑",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition(stock_codes=["600519", "000001"])),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        calls = []
        calls_lock = threading.Lock()
        first_execute_entered = threading.Event()
        release_execute = threading.Event()

        def fake_execute(
            self,
            run_id,
            prepared,
            stock_codes,
            run_mode,
            date_from,
            date_to,
            data_policy,
            rule_ids,
            rule_names,
            batch_metadata=None,
            live_cache_key=None,
            progress_callback=None,
        ):
            with calls_lock:
                calls.append({
                    "run_id": run_id,
                    "rule_ids": list(rule_ids),
                    "stock_codes": list(stock_codes),
                })
            first_execute_entered.set()
            assert release_execute.wait(timeout=5), "shared execution was not released"
            if progress_callback is not None:
                progress_callback(len(stock_codes), {"fast_latest_scan": False, "skip_counts": {}, "skipped_count": 0}, [])
            return ([{
                "rule_id": prepared[0][0],
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "matched_groups": [{"id": "g1"}],
                "matched_dates": ["2026-05-08"],
                "matched_events": [{"date": "2026-05-08"}],
                "snapshot": {"close": 100},
                "explanation": "hit",
            }], [])

        monkeypatch.setattr(RuleService, "_execute_batch_scan_by_stock", fake_execute)

        tenants = [
            TenantContext(
                id=default_tenant["id"],
                key=default_tenant["key"],
                name=default_tenant["name"],
                is_default=True,
            ),
            TenantContext(
                id=other_tenant["id"],
                key=other_tenant["key"],
                name=other_tenant["name"],
                is_default=False,
            ),
        ]
        payload = RuleBatchRunRequest(
            rule_ids=[rule_id],
            mode="history",
            target=RuleTarget(scope="custom", stock_codes=["600519", "000001"]),
            tenant_keys=["default", "quant_team"],
        )
        planned_barrier = threading.Barrier(3, timeout=5)
        results = []
        errors = []
        result_lock = threading.Lock()

        def worker() -> None:
            try:
                background_tasks = BackgroundTasks()
                response = rules_endpoint._start_async_rule_run_for_tenants(payload, tenants, background_tasks)
                planned_barrier.wait()
                for task in background_tasks.tasks:
                    task.func(*task.args, **task.kwargs)
                with result_lock:
                    results.append(response)
            except Exception as exc:  # pragma: no cover - collected for assertion
                with result_lock:
                    errors.append(exc)
                try:
                    planned_barrier.abort()
                except Exception:
                    pass

        threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
        for thread in threads:
            thread.start()
        planned_barrier.wait()
        assert first_execute_entered.wait(timeout=5)
        time.sleep(0.05)
        with calls_lock:
            assert len(calls) == 1
        release_execute.set()
        for thread in threads:
            thread.join(timeout=5)

        assert all(not thread.is_alive() for thread in threads)
        assert errors == []
        assert len(results) == 2
        with calls_lock:
            assert len(calls) == 1
            assert calls[0]["run_id"] == 0
            assert calls[0]["stock_codes"] == ["600519", "000001"]

        for response in results:
            assert response["status"] == "running"
            assert len(response["tenant_runs"]) == 2
            for item in response["tenant_runs"]:
                repo = RuleRepository(
                    db,
                    tenant_id=default_tenant["id"] if item["tenant_key"] == "default" else other_tenant["id"],
                    tenant_key=item["tenant_key"],
                )
                persisted = repo.get_run(item["run_id"])
                assert persisted["status"] == "completed"
                assert repo.list_matches(item["run_id"])[0]["stock_code"] == "600519"

        with db.get_session() as session:
            assert session.query(SharedRuleRun).count() == 1
            assert session.query(StockRuleMatchLink).count() == 4
    finally:
        release_execute.set()
        DatabaseManager.reset_instance()


def test_multi_tenant_sync_rule_run_executes_shared_logic_once(monkeypatch):
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        with db.get_session() as session:
            rule = StockRule(
                name="同步共享逻辑",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition(stock_codes=["600519", "000001"])),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        calls = []

        def fake_execute(
            self,
            run_id,
            prepared,
            stock_codes,
            run_mode,
            date_from,
            date_to,
            data_policy,
            rule_ids,
            rule_names,
            batch_metadata=None,
            live_cache_key=None,
            progress_callback=None,
        ):
            calls.append({
                "run_id": run_id,
                "rule_ids": list(rule_ids),
                "stock_codes": list(stock_codes),
            })
            if progress_callback is not None:
                progress_callback(len(stock_codes), {"fast_latest_scan": False, "skip_counts": {}, "skipped_count": 0}, [])
            return ([{
                "rule_id": prepared[0][0],
                "stock_code": "600519",
                "stock_name": "贵州茅台",
                "matched_groups": [{"id": "g1"}],
                "matched_dates": ["2026-05-08"],
                "matched_events": [{"date": "2026-05-08"}],
                "snapshot": {"close": 100},
                "explanation": "hit",
            }], [])

        monkeypatch.setattr(RuleService, "_execute_batch_scan_by_stock", fake_execute)

        tenants = [
            TenantContext(
                id=default_tenant["id"],
                key=default_tenant["key"],
                name=default_tenant["name"],
                is_default=True,
            ),
            TenantContext(
                id=other_tenant["id"],
                key=other_tenant["key"],
                name=other_tenant["name"],
                is_default=False,
            ),
        ]
        payload = RuleBatchRunRequest(
            rule_ids=[rule_id],
            mode="history",
            target=RuleTarget(scope="custom", stock_codes=["600519", "000001"]),
            tenant_keys=["default", "quant_team"],
        )

        response = rules_endpoint._run_rules_for_tenants(payload, tenants)

        assert len(calls) == 1
        assert calls[0]["run_id"] == 0
        assert calls[0]["stock_codes"] == ["600519", "000001"]
        assert response["status"] == "completed"
        assert response["run_ids"] == [item["run_id"] for item in response["tenant_runs"]]

        run_by_tenant = {item["tenant_key"]: item["run_id"] for item in response["tenant_runs"]}
        default_repo = RuleRepository(db, tenant_id=default_tenant["id"], tenant_key=default_tenant["key"])
        other_repo = RuleRepository(db, tenant_id=other_tenant["id"], tenant_key=other_tenant["key"])
        assert default_repo.get_run(run_by_tenant["default"])["status"] == "completed"
        assert other_repo.get_run(run_by_tenant["quant_team"])["status"] == "completed"
        with db.get_session() as session:
            assert session.query(SharedRuleRun).count() == 1
            assert session.query(StockRuleMatchLink).count() == 2
    finally:
        DatabaseManager.reset_instance()


def test_multi_tenant_async_prewarm_uses_prewarm_path(monkeypatch):
    DatabaseManager.reset_instance()
    rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
    rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        tenant_repo = TenantRepository(db)
        default_tenant = tenant_repo.ensure_default_tenant()
        other_tenant = tenant_repo.create_tenant({"key": "quant_team", "name": "量化组"})
        with db.get_session() as session:
            rule = StockRule(
                name="预热共享规则",
                tenant_id=None,
                visibility="shared",
                period="daily",
                lookback_days=120,
                target_scope="custom",
                target_codes_json="[]",
                definition_json=json.dumps(_simple_rule_definition(stock_codes=["600519", "000001"])),
            )
            session.add(rule)
            session.commit()
            session.refresh(rule)
            rule_id = rule.id

        prewarm_calls = []

        def fake_prewarm(self, prepared, stock_codes, start_date, data_policy, *, live_cache_key=None):
            prewarm_calls.append({
                "stock_codes": list(stock_codes),
                "data_policy": data_policy,
                "live_cache_key": live_cache_key,
            })
            return {
                "prewarm_only": True,
                "prewarm_hit_count": len(stock_codes),
                "prewarm_miss_count": 0,
            }

        def fail_execute(*_args, **_kwargs):
            raise AssertionError("prewarm-only multi-tenant runs must not execute rule scan")

        monkeypatch.setattr(RuleService, "_prewarm_rule_scan_cache", fake_prewarm)
        monkeypatch.setattr(RuleService, "_execute_batch_scan_by_stock", fail_execute)

        tenants = [
            TenantContext(
                id=default_tenant["id"],
                key=default_tenant["key"],
                name=default_tenant["name"],
                is_default=True,
            ),
            TenantContext(
                id=other_tenant["id"],
                key=other_tenant["key"],
                name=other_tenant["name"],
                is_default=False,
            ),
        ]
        payload = RuleBatchRunRequest(
            rule_ids=[rule_id],
            mode="latest",
            target=RuleTarget(scope="custom", stock_codes=["600519", "000001"]),
            data_policy="default",
            live_cache_key="prewarm-session",
            tenant_keys=["default", "quant_team"],
        )
        background_tasks = BackgroundTasks()

        with mock.patch(
            "src.services.rule_service.trading_calendar.get_market_now",
            return_value=datetime(2026, 5, 8, 8, 45),
        ), mock.patch(
            "src.services.rule_service.trading_calendar.is_market_open",
            return_value=True,
        ):
            response = rules_endpoint._start_async_rule_run_for_tenants(payload, tenants, background_tasks)
            for task in background_tasks.tasks:
                task.func(*task.args, **task.kwargs)

        assert len(prewarm_calls) == 2
        assert response["prewarm_only"] is True
        run_by_tenant = {item["tenant_key"]: item["run_id"] for item in response["tenant_runs"]}
        default_repo = RuleRepository(db, tenant_id=default_tenant["id"], tenant_key=default_tenant["key"])
        other_repo = RuleRepository(db, tenant_id=other_tenant["id"], tenant_key=other_tenant["key"])
        default_run = default_repo.get_run(run_by_tenant["default"])
        other_run = other_repo.get_run(run_by_tenant["quant_team"])
        assert default_run["status"] == "completed"
        assert default_run["prewarm_only"] is True
        assert default_run["match_count"] == 0
        assert other_run["status"] == "completed"
        assert other_run["prewarm_hit_count"] == 2
    finally:
        DatabaseManager.reset_instance()
        rule_service_module._RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_HISTORY_CACHE.clear()
        rule_service_module._LIVE_RULE_RUN_SCAN_CACHE.clear()


def test_rule_logic_fingerprint_ignores_target_stock_universe():
    first = _simple_rule_definition(stock_codes=["600519"])
    second = _simple_rule_definition(stock_codes=["000001", "300750"])

    assert RuleService._build_rule_logic_fingerprint(first) == RuleService._build_rule_logic_fingerprint(second)


def test_rule_service_slow_latest_batch_preloads_intraday_hot_table_quotes():
    rule = _service_rule_for_codes(["600519", "000001"])
    rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "cond-trend-start",
            "left": {"metric": "trend_start_signal", "offset": 0},
            "operator": "=",
            "right": {"type": "literal", "value": 1},
        }
    ]
    repo = _ProgressRuleRepo([rule])
    stock_service = _BatchHistoryWithIntradayQuoteStockService()
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1
    service._build_intraday_hot_table_quote = mock.Mock(
        side_effect=AssertionError("slow latest batch should use preloaded quote cache")
    )

    with mock.patch(
        "src.services.rule_service.RuleService._is_cn_live_test_allowed",
        return_value=True,
    ), mock.patch(
        "src.services.rule_service.trading_calendar.get_market_now",
        return_value=datetime(2026, 5, 8, 10, 0),
    ), mock.patch(
        "src.services.rule_service.trading_calendar.is_market_open",
        return_value=True,
    ):
        response, context = service.start_run_rules(
            [1],
            mode="latest",
            target_override={"scope": "custom", "stock_codes": ["600519", "000001"]},
            data_policy="default",
        )
        service.complete_started_run_rules(**context)

    assert response["status"] == "running"
    assert stock_service.db.quote_batch_calls == [
        (["600519", "000001"], date(2026, 5, 8)),
    ]
    assert stock_service.per_stock_history_calls == []
    service._build_intraday_hot_table_quote.assert_not_called()
    assert repo.finished_status == "completed"


def test_rule_service_async_batch_reuses_history_and_skips_chip_for_light_rules():
    first_rule = _service_rule_for_codes(["600519", "000001"])
    second_rule = {**_service_rule_for_codes(["600519", "000001"]), "id": 2, "name": "第二条规则"}
    repo = _ProgressRuleRepo([first_rule, second_rule])
    stock_service = _CountingStockService()
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1
    service._build_history_chip_metrics = mock.Mock(return_value={})

    response, context = service.start_run_rules(
        [1, 2],
        mode="history",
        target_override={"scope": "custom", "stock_codes": ["600519", "000001"]},
    )
    service.complete_started_run_rules(**context)

    assert response["status"] == "running"
    assert [call[0] for call in stock_service.history_calls] == ["600519", "000001"]
    assert stock_service.indicator_calls == []
    service._build_history_chip_metrics.assert_not_called()
    assert repo.finished_status == "completed"
    assert len(repo.finished_matches) == 4


def test_rule_service_async_batch_builds_chip_metrics_once_when_needed():
    first_rule = _service_rule_for_codes(["600519"])
    chip_rule = _service_rule_for_codes(["600519"])
    chip_rule = {**chip_rule, "id": 2, "name": "筹码规则"}
    chip_rule["definition"]["groups"][0]["conditions"] = [
        {
            "id": "c1",
            "left": {"metric": "profit_ratio", "offset": 0},
            "operator": ">",
            "right": {"type": "literal", "value": 40},
        }
    ]
    repo = _ProgressRuleRepo([first_rule, chip_rule])
    service = RuleService(repo=repo, stock_service=_CountingStockService())
    service._resolve_run_workers = lambda target_count: 1
    service._build_history_chip_metrics = mock.Mock(return_value={
        "chip_distribution": {"profit_ratio": 0.82}
    })

    response, context = service.start_run_rules(
        [1, 2],
        mode="history",
        target_override={"scope": "custom", "stock_codes": ["600519"]},
    )
    service.complete_started_run_rules(**context)

    assert response["status"] == "running"
    service._build_history_chip_metrics.assert_called_once()
    assert repo.finished_status == "completed"
    assert [match["rule_id"] for match in repo.finished_matches] == [1, 2]


def test_rule_service_notify_live_matches_sends_configured_notifications():
    repo = _NotifyRuleRepo([
        {
            "run_id": 12,
            "rule_id": 7,
            "stock_code": "300274.SZ",
            "stock_name": "阳光电源",
            "matched_dates": ["2026-05-08"],
            "matched_events": [
                {
                    "date": "2026-05-08",
                    "snapshot": {
                        "snapshot_id": "20260508100000",
                        "snapshot_time": "2026-05-08T10:00:00",
                    },
                    "matched_groups": [
                        {
                            "id": "group-1",
                            "conditions": [
                                {
                                    "id": "cond-1",
                                    "left_metric": "volume",
                                    "operator": ">",
                                    "values": {"left": 123456, "right": 100000},
                                }
                            ],
                        }
                    ],
                    "explanation": "成交量放大",
                }
            ],
            "matched_groups": [],
            "snapshot": {},
            "explanation": "成交量放大",
        }
    ])
    fake_notifier = mock.Mock()
    fake_notifier.is_available.return_value = True
    fake_notifier.send.return_value = True
    service = RuleService(repo=repo, stock_service=object())

    with mock.patch("src.notification.NotificationService", return_value=fake_notifier):
        result = service.notify_live_matches(
            12,
            execution_time="2026-05-08 10:00:00",
            rule_ids=[7],
            rule_names=["放量观察"],
        )

    assert result["sent"] is True
    assert result["match_count"] == 1
    assert result["event_count"] == 1
    message = fake_notifier.send.call_args.args[0]
    assert "规则实测命中提醒" in message
    assert "#7 放量观察" in message
    assert "阳光电源(300274.SZ)" in message
    assert "成交量: 123,456 > 100,000" in message


def test_rule_service_notify_live_matches_skips_duplicate_signature():
    matches = [
        {
            "run_id": 12,
            "rule_id": 7,
            "stock_code": "300274.SZ",
            "stock_name": "阳光电源",
            "matched_dates": ["2026-05-08"],
            "matched_events": [
                {
                    "date": "2026-05-08",
                    "snapshot": {
                        "snapshot_id": "20260508100100",
                        "snapshot_time": "2026-05-08T10:01:00",
                    },
                    "matched_groups": [],
                }
            ],
            "matched_groups": [],
            "snapshot": {},
            "explanation": None,
        }
    ]
    repo = _NotifyRuleRepo(
        matches,
        previous_signature=((7, "300274.SZ"),),
        previous_run_id=11,
    )
    fake_notifier = mock.Mock()
    service = RuleService(repo=repo, stock_service=object())

    with mock.patch("src.notification.NotificationService", return_value=fake_notifier):
        result = service.notify_live_matches(
            12,
            execution_time="2026-05-08 10:01:00",
            rule_ids=[7],
            rule_names=["放量观察"],
            compact=False,
        )

    assert result["sent"] is False
    assert result["deduplicated"] is True
    assert result["event_count"] == 1
    assert "跳过重复推送" in result["message"]
    fake_notifier.send.assert_not_called()


def test_rule_service_notify_live_matches_compact_filters_same_day_rule_stock_duplicates():
    matches = [
        {
            "run_id": 12,
            "rule_id": 7,
            "stock_code": "300274.SZ",
            "stock_name": "阳光电源",
            "matched_dates": ["2026-05-08"],
            "matched_events": [
                {
                    "date": "2026-05-08",
                    "snapshot": {
                        "snapshot_id": "20260508100100",
                        "snapshot_time": "2026-05-08T10:01:00",
                    },
                    "matched_groups": [],
                }
            ],
            "matched_groups": [],
            "snapshot": {},
            "explanation": None,
        },
        {
            "run_id": 12,
            "rule_id": 7,
            "stock_code": "688521.SH",
            "stock_name": "芯原股份",
            "matched_dates": ["2026-05-08"],
            "matched_events": [
                {
                    "date": "2026-05-08",
                    "snapshot": {
                        "snapshot_id": "20260508100100",
                        "snapshot_time": "2026-05-08T10:01:00",
                    },
                    "matched_groups": [],
                }
            ],
            "matched_groups": [],
            "snapshot": {},
            "explanation": None,
        },
    ]
    repo = _NotifyRuleRepo(
        matches,
        previous_keys={("2026-05-08", 7, "300274.SZ")},
        previous_run_id=11,
    )
    fake_notifier = mock.Mock()
    fake_notifier.is_available.return_value = True
    fake_notifier.send.return_value = True
    service = RuleService(repo=repo, stock_service=object())

    with mock.patch("src.notification.NotificationService", return_value=fake_notifier):
        result = service.notify_live_matches(
            12,
            execution_time="2026-05-08 10:01:00",
            rule_ids=[7],
            rule_names=["放量观察"],
            compact=True,
        )

    assert result["sent"] is True
    assert result["deduplicated"] is True
    assert result["event_count"] == 1
    assert result["original_event_count"] == 2
    message = fake_notifier.send.call_args.args[0]
    assert "芯原股份(688521.SH)" in message
    assert "阳光电源(300274.SZ)" not in message


def test_rule_service_notify_live_matches_compact_skips_when_all_same_day_keys_seen():
    matches = [
        {
            "run_id": 12,
            "rule_id": 7,
            "stock_code": "300274.SZ",
            "stock_name": "阳光电源",
            "matched_dates": ["2026-05-08"],
            "matched_events": [
                {
                    "date": "2026-05-08",
                    "snapshot": {
                        "snapshot_id": "20260508100100",
                        "snapshot_time": "2026-05-08T10:01:00",
                    },
                    "matched_groups": [],
                }
            ],
            "matched_groups": [],
            "snapshot": {},
            "explanation": None,
        }
    ]
    repo = _NotifyRuleRepo(
        matches,
        previous_keys={("2026-05-08", 7, "300274.SZ")},
        previous_run_id=11,
    )
    fake_notifier = mock.Mock()
    service = RuleService(repo=repo, stock_service=object())

    with mock.patch("src.notification.NotificationService", return_value=fake_notifier):
        result = service.notify_live_matches(
            12,
            execution_time="2026-05-08 10:01:00",
            rule_ids=[7],
            rule_names=["放量观察"],
            compact=True,
        )

    assert result["sent"] is False
    assert result["deduplicated"] is True
    assert result["event_count"] == 0
    assert result["original_event_count"] == 1
    assert "精简模式" in result["message"]
    fake_notifier.send.assert_not_called()


def test_rule_service_notify_live_matches_skips_empty_result():
    fake_notifier = mock.Mock()
    service = RuleService(repo=_NotifyRuleRepo([]), stock_service=object())

    with mock.patch("src.notification.NotificationService", return_value=fake_notifier):
        result = service.notify_live_matches(12)

    assert result["sent"] is False
    assert result["event_count"] == 0
    fake_notifier.send.assert_not_called()


def test_rule_service_run_rules_creates_one_run_with_rule_tagged_matches():
    first_rule = _service_rule_for_codes(["600519"])
    second_rule = _service_rule_for_codes(["AAPL"])
    second_rule = {**second_rule, "id": 2, "name": "第二条规则"}
    repo = _FakeMultiRuleRepo([first_rule, second_rule])
    service = RuleService(repo=repo, stock_service=_FakeStockService())

    def use_one_worker(target_count):
        return 1

    service._resolve_run_workers = use_one_worker

    result = service.run_rules([1, 2], mode="history")

    assert result["run_id"] == 101
    assert result["rule_id"] == 1
    assert result["rule_ids"] == [1, 2]
    assert result["event_count"] == 2
    assert [match["rule_id"] for match in result["matches"]] == [1, 2]
    assert [match["rule_id"] for match in repo.finished_matches] == [1, 2]


def test_rule_service_run_rules_executes_rules_concurrently_and_preserves_order():
    first_rule = _service_rule_for_codes(["600519"])
    second_rule = _service_rule_for_codes(["AAPL"])
    second_rule = {**second_rule, "id": 2, "name": "第二条规则"}
    repo = _FakeMultiRuleRepo([first_rule, second_rule])
    stock_service = _ConcurrentProbeStockService()
    service = RuleService(repo=repo, stock_service=stock_service)
    service._resolve_run_workers = lambda target_count: 1
    service._resolve_batch_rule_workers = lambda rule_count: 2

    result = service.run_rules([1, 2], mode="history")

    assert stock_service.max_active_calls >= 2
    assert [match["rule_id"] for match in result["matches"]] == [1, 2]
    assert [match["rule_id"] for match in repo.finished_matches] == [1, 2]


def test_rule_service_rejects_cross_operators():
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "custom", "stock_codes": ["600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": "cross_up",
                        "right": {"type": "metric", "metric": "ma20"},
                    }
                ],
            }
        ],
    }

    try:
        RuleService(repo=object(), stock_service=object()).validate_definition(definition)
    except RuleValidationError as exc:
        assert "上穿/下穿" in str(exc)
    else:
        raise AssertionError("cross_up should be rejected")


def test_rule_service_prefers_explicit_codes_for_watchlist_scope():
    service = RuleService(repo=object(), stock_service=object())

    codes = service._resolve_target_codes({
        "scope": "watchlist",
        "stock_codes": ["600519", "600519", " aapl "],
    })

    assert codes == ["600519", "AAPL"]


def test_rule_service_accepts_all_a_shares_scope_with_explicit_codes():
    definition = {
        "period": "daily",
        "lookback_days": 120,
        "target": {"scope": "all_a_shares", "stock_codes": ["000001", "600519"]},
        "groups": [
            {
                "id": "g1",
                "conditions": [
                    {
                        "id": "c1",
                        "left": {"metric": "close"},
                        "operator": ">",
                        "right": {"type": "literal", "value": 1},
                    }
                ],
            }
        ],
    }

    RuleService(repo=object(), stock_service=object()).validate_definition(definition)


def test_rule_service_resolves_all_a_shares_scope_from_stock_index():
    service = RuleService(repo=object(), stock_service=object())

    with mock.patch(
        "src.data.stock_index_loader.get_all_a_share_stock_codes",
        return_value=["000001", "600519", "000001"],
    ):
        codes = service._resolve_target_codes({"scope": "all_a_shares", "stock_codes": []})

    assert codes == ["000001", "600519"]


def test_rule_service_all_a_shares_merges_local_daily_codes_missing_from_index():
    DatabaseManager.reset_instance()
    db = DatabaseManager(db_url="sqlite:///:memory:")
    try:
        with db.get_session() as session:
            session.add_all([
                StockDaily(code="688813", date=date(2026, 4, 30), close=186.8),
                StockDaily(code="920125", date=date(2026, 5, 27), close=189.83),
                StockDaily(code="00700", date=date(2026, 5, 27), close=500),
            ])
            session.commit()

        service = RuleService.__new__(RuleService)
        service.stock_service = mock.Mock(repo=mock.Mock(db=db))

        with mock.patch(
            "src.data.stock_index_loader.get_all_a_share_stock_codes",
            return_value=["600519"],
        ):
            codes = service._resolve_target_codes({"scope": "all_a_shares", "stock_codes": []})

        assert codes[:1] == ["600519"]
        assert "688813" in codes
        assert "920125" in codes
        assert "00700" not in codes
    finally:
        DatabaseManager.reset_instance()


def test_rule_service_rejects_empty_all_a_shares_stock_index():
    service = RuleService(repo=object(), stock_service=object())

    with mock.patch("src.data.stock_index_loader.get_all_a_share_stock_codes", return_value=[]):
        try:
            service._resolve_target_codes({"scope": "all_a_shares", "stock_codes": []})
        except RuleValidationError as exc:
            assert "A 股股票索引为空" in str(exc)
        else:
            raise AssertionError("empty all A-share index should be rejected")
