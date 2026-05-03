# -*- coding: utf-8 -*-
"""Repository helpers for stock rules."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, desc, func, select

from src.storage import DatabaseManager, StockRule, StockRuleMatch, StockRuleRun


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


class RuleRepository:
    """DB access layer for the stock rule domain."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None):
        self.db = db_manager or DatabaseManager.get_instance()

    @staticmethod
    def rule_to_dict(row: StockRule) -> Dict[str, Any]:
        definition = _json_loads(row.definition_json, {})
        return {
            "id": row.id,
            "name": row.name,
            "description": row.description,
            "is_active": bool(row.is_active),
            "period": row.period,
            "lookback_days": row.lookback_days,
            "target_scope": row.target_scope,
            "target_codes": _json_loads(row.target_codes_json, []),
            "definition": definition,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        }

    def list_rules(self) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            last_run_at = (
                select(func.max(StockRuleRun.started_at))
                .where(StockRuleRun.rule_id == StockRule.id)
                .correlate(StockRule)
                .scalar_subquery()
            )
            last_match_count = (
                select(StockRuleRun.match_count)
                .where(StockRuleRun.rule_id == StockRule.id)
                .order_by(desc(StockRuleRun.started_at))
                .limit(1)
                .correlate(StockRule)
                .scalar_subquery()
            )
            rows = session.execute(
                select(StockRule, last_run_at, last_match_count).order_by(desc(StockRule.updated_at), desc(StockRule.id))
            ).all()
            items: List[Dict[str, Any]] = []
            for rule, run_at, match_count in rows:
                item = self.rule_to_dict(rule)
                item["last_run_at"] = run_at.isoformat() if run_at else None
                item["last_match_count"] = int(match_count or 0)
                items.append(item)
            return items

    @staticmethod
    def run_to_dict(row: StockRuleRun, rule_name: Optional[str] = None) -> Dict[str, Any]:
        return {
            "id": row.id,
            "rule_id": row.rule_id,
            "rule_name": rule_name,
            "status": row.status,
            "target_count": int(row.target_count or 0),
            "match_count": int(row.match_count or 0),
            "error": row.error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms,
        }

    def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockRuleRun, StockRule.name)
                .join(StockRule, StockRule.id == StockRuleRun.rule_id)
                .order_by(desc(StockRuleRun.started_at), desc(StockRuleRun.id))
                .limit(limit)
            ).all()
            return [self.run_to_dict(run, rule_name) for run, rule_name in rows]

    def get_rule(self, rule_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(select(StockRule).where(StockRule.id == rule_id).limit(1)).scalar_one_or_none()
            return self.rule_to_dict(row) if row else None

    def create_rule(self, data: Dict[str, Any]) -> Dict[str, Any]:
        definition = data["definition"]
        target = definition.get("target") or {}
        with self.db.get_session() as session:
            row = StockRule(
                name=data["name"],
                description=data.get("description"),
                is_active=bool(data.get("is_active", True)),
                period=definition.get("period", "daily"),
                lookback_days=int(definition.get("lookback_days", 120)),
                target_scope=target.get("scope", "watchlist"),
                target_codes_json=_json_dumps(target.get("stock_codes") or []),
                definition_json=_json_dumps(definition),
            )
            session.add(row)
            session.commit()
            session.refresh(row)
            return self.rule_to_dict(row)

    def update_rule(self, rule_id: int, data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(select(StockRule).where(StockRule.id == rule_id).limit(1)).scalar_one_or_none()
            if row is None:
                return None

            if "name" in data and data["name"] is not None:
                row.name = data["name"]
            if "description" in data:
                row.description = data["description"]
            if "is_active" in data and data["is_active"] is not None:
                row.is_active = bool(data["is_active"])
            if "definition" in data and data["definition"] is not None:
                definition = data["definition"]
                target = definition.get("target") or {}
                row.definition_json = _json_dumps(definition)
                row.period = definition.get("period", row.period)
                row.lookback_days = int(definition.get("lookback_days", row.lookback_days))
                row.target_scope = target.get("scope", row.target_scope)
                row.target_codes_json = _json_dumps(target.get("stock_codes") or [])
            row.updated_at = datetime.now()
            session.commit()
            session.refresh(row)
            return self.rule_to_dict(row)

    def delete_rule(self, rule_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.execute(select(StockRule).where(StockRule.id == rule_id).limit(1)).scalar_one_or_none()
            if row is None:
                return False
            session.execute(delete(StockRuleMatch).where(StockRuleMatch.rule_id == rule_id))
            session.execute(delete(StockRuleRun).where(StockRuleRun.rule_id == rule_id))
            session.delete(row)
            session.commit()
            return True

    def delete_run(self, run_id: int) -> bool:
        with self.db.get_session() as session:
            row = session.execute(select(StockRuleRun).where(StockRuleRun.id == run_id).limit(1)).scalar_one_or_none()
            if row is None:
                return False
            session.execute(delete(StockRuleMatch).where(StockRuleMatch.run_id == run_id))
            session.delete(row)
            session.commit()
            return True

    def create_run(self, rule_id: int, target_count: int) -> int:
        with self.db.get_session() as session:
            row = StockRuleRun(rule_id=rule_id, target_count=target_count, status="running")
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def finish_run(
        self,
        *,
        run_id: int,
        rule_id: int,
        status: str,
        started_at: datetime,
        matches: List[Dict[str, Any]],
        error: Optional[str] = None,
    ) -> Tuple[int, int]:
        finished_at = datetime.now()
        duration_ms = int((finished_at - started_at).total_seconds() * 1000)
        with self.db.get_session() as session:
            run = session.execute(select(StockRuleRun).where(StockRuleRun.id == run_id).limit(1)).scalar_one_or_none()
            if run is None:
                return (0, 0)
            run.status = status
            run.match_count = len(matches)
            run.error = error
            run.finished_at = finished_at
            run.duration_ms = duration_ms
            for match in matches:
                snapshot = dict(match.get("snapshot") or {})
                snapshot["_matched_dates"] = match.get("matched_dates") or []
                snapshot["_matched_events"] = match.get("matched_events") or []
                session.add(
                    StockRuleMatch(
                        run_id=run_id,
                        rule_id=rule_id,
                        stock_code=match["stock_code"],
                        stock_name=match.get("stock_name"),
                        matched_groups_json=_json_dumps(match.get("matched_groups") or []),
                        snapshot_json=_json_dumps(snapshot),
                        explanation=match.get("explanation"),
                    )
                )
            session.commit()
            return (len(matches), duration_ms)

    def list_matches(self, run_id: int) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockRuleMatch).where(StockRuleMatch.run_id == run_id).order_by(StockRuleMatch.id.asc())
            ).scalars().all()
            items: List[Dict[str, Any]] = []
            for row in rows:
                snapshot = _json_loads(row.snapshot_json, {}) or {}
                matched_dates = snapshot.pop("_matched_dates", [])
                matched_events = snapshot.pop("_matched_events", [])
                items.append({
                    "id": row.id,
                    "run_id": row.run_id,
                    "rule_id": row.rule_id,
                    "stock_code": row.stock_code,
                    "stock_name": row.stock_name,
                    "matched_dates": matched_dates,
                    "matched_events": matched_events,
                    "matched_groups": _json_loads(row.matched_groups_json, []),
                    "snapshot": snapshot,
                    "explanation": row.explanation,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                })
            return items
