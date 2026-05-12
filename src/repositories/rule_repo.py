# -*- coding: utf-8 -*-
"""Repository helpers for stock rules."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, desc, func, or_, select

from src.storage import DatabaseManager, StockRule, StockRuleMatch, StockRuleRun

RULE_BATCH_META_PREFIX = "__rule_batch_meta__:"


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_loads(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except Exception:
        return fallback


def encode_rule_batch_metadata(
    rule_ids: List[int],
    rule_names: List[str],
    errors: List[str],
    completed_count: Optional[int] = None,
) -> str:
    payload = {
        "rule_ids": rule_ids,
        "rule_names": rule_names,
        "errors": errors,
    }
    if completed_count is not None:
        payload["completed_count"] = max(0, int(completed_count or 0))
    return RULE_BATCH_META_PREFIX + _json_dumps(payload)


def _decode_rule_batch_metadata(error: Optional[str]) -> Tuple[Dict[str, Any], Optional[str]]:
    if not error or not error.startswith(RULE_BATCH_META_PREFIX):
        return {}, error
    metadata = _json_loads(error[len(RULE_BATCH_META_PREFIX):], {})
    errors = metadata.get("errors") if isinstance(metadata, dict) else []
    public_error = "；".join(str(item) for item in errors if item) if isinstance(errors, list) else None
    return metadata if isinstance(metadata, dict) else {}, public_error or None


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
        batch_metadata, public_error = _decode_rule_batch_metadata(row.error)
        metadata_rule_ids = batch_metadata.get("rule_ids")
        metadata_rule_names = batch_metadata.get("rule_names")
        rule_ids = [int(rule_id) for rule_id in metadata_rule_ids] if isinstance(metadata_rule_ids, list) else [row.rule_id]
        rule_names = [str(name) for name in metadata_rule_names] if isinstance(metadata_rule_names, list) else (
            [rule_name] if rule_name else []
        )
        return {
            "id": row.id,
            "rule_id": row.rule_id,
            "rule_ids": rule_ids,
            "rule_name": f"多规则回测（{len(rule_ids)} 条）" if len(rule_ids) > 1 else rule_name,
            "rule_names": rule_names,
            "status": row.status,
            "target_count": int(row.target_count or 0),
            "completed_count": int(batch_metadata.get("completed_count") or 0),
            "match_count": int(row.match_count or 0),
            "event_count": int(row.match_count or 0),
            "error": public_error,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms,
        }

    @staticmethod
    def _count_event_rows_from_snapshots(snapshot_json_values: List[Optional[str]]) -> int:
        total = 0
        for snapshot_json in snapshot_json_values:
            snapshot = _json_loads(snapshot_json, {}) or {}
            if not isinstance(snapshot, dict):
                continue
            matched_events = snapshot.get("_matched_events")
            if isinstance(matched_events, list) and matched_events:
                total += len(matched_events)
                continue
            matched_dates = snapshot.get("_matched_dates")
            if isinstance(matched_dates, list):
                total += len(matched_dates)
        return total

    @staticmethod
    def _snapshot_has_live_metadata(snapshot_json: Optional[str]) -> bool:
        snapshot = _json_loads(snapshot_json, {}) or {}
        if not isinstance(snapshot, dict):
            return False
        if snapshot.get("snapshot_id") or snapshot.get("snapshot_time"):
            return True
        for event in snapshot.get("_matched_events") or []:
            if not isinstance(event, dict):
                continue
            event_snapshot = event.get("snapshot") or {}
            if isinstance(event_snapshot, dict) and (
                event_snapshot.get("snapshot_id") or event_snapshot.get("snapshot_time")
            ):
                return True
        return False

    @staticmethod
    def _normalize_match_signature_rows(rows: List[Tuple[int, str, Optional[str]]]) -> Tuple[Tuple[int, str], ...]:
        pairs = {
            (int(rule_id), str(stock_code or "").strip().upper())
            for rule_id, stock_code, _snapshot_json in rows
            if rule_id and str(stock_code or "").strip()
        }
        return tuple(sorted(pairs))

    def get_previous_live_match_signature(self, run_id: int) -> Optional[Dict[str, Any]]:
        """Return today's previous live-test rule/stock signature before run_id."""
        with self.db.get_session() as session:
            current = session.execute(
                select(StockRuleRun).where(StockRuleRun.id == run_id).limit(1)
            ).scalar_one_or_none()
            if current is None or current.started_at is None:
                return None

            day_start = current.started_at.replace(hour=0, minute=0, second=0, microsecond=0)
            next_day_start = day_start + timedelta(days=1)
            previous_run_ids = session.execute(
                select(StockRuleRun.id)
                .where(
                    StockRuleRun.id != run_id,
                    StockRuleRun.status.in_(("completed", "partial")),
                    StockRuleRun.match_count > 0,
                    StockRuleRun.started_at >= day_start,
                    StockRuleRun.started_at < next_day_start,
                    or_(
                        StockRuleRun.started_at < current.started_at,
                        (StockRuleRun.started_at == current.started_at) & (StockRuleRun.id < run_id),
                    ),
                )
                .order_by(desc(StockRuleRun.started_at), desc(StockRuleRun.id))
            ).scalars().all()

            for previous_run_id in previous_run_ids:
                rows = session.execute(
                    select(StockRuleMatch.rule_id, StockRuleMatch.stock_code, StockRuleMatch.snapshot_json)
                    .where(StockRuleMatch.run_id == previous_run_id)
                    .order_by(StockRuleMatch.id.asc())
                ).all()
                row_values = [
                    (int(rule_id), str(stock_code), snapshot_json)
                    for rule_id, stock_code, snapshot_json in rows
                ]
                if not row_values:
                    continue
                if not any(
                    self._snapshot_has_live_metadata(snapshot_json)
                    for _rule_id, _stock_code, snapshot_json in row_values
                ):
                    continue
                signature = self._normalize_match_signature_rows(row_values)
                if signature:
                    return {
                        "run_id": int(previous_run_id),
                        "signature": signature,
                    }
        return None

    def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        with self.db.get_session() as session:
            rows = session.execute(
                select(StockRuleRun, StockRule.name)
                .join(StockRule, StockRule.id == StockRuleRun.rule_id)
                .order_by(desc(StockRuleRun.started_at), desc(StockRuleRun.id))
                .limit(limit)
            ).all()
            items: List[Dict[str, Any]] = []
            for run, rule_name in rows:
                item = self.run_to_dict(run, rule_name)
                snapshot_json_values = session.execute(
                    select(StockRuleMatch.snapshot_json).where(StockRuleMatch.run_id == run.id)
                ).scalars().all()
                item["event_count"] = self._count_event_rows_from_snapshots(list(snapshot_json_values))
                match_rules = session.execute(
                    select(StockRuleMatch.rule_id, StockRule.name)
                    .join(StockRule, StockRule.id == StockRuleMatch.rule_id)
                    .where(StockRuleMatch.run_id == run.id)
                    .distinct()
                    .order_by(StockRuleMatch.rule_id.asc())
                ).all()
                if match_rules and len(item.get("rule_ids") or []) <= 1:
                    rule_ids = [int(rule_id) for rule_id, _ in match_rules]
                    rule_names = [str(name) for _, name in match_rules if name]
                    item["rule_ids"] = rule_ids
                    item["rule_names"] = rule_names
                    if len(rule_ids) > 1:
                        item["rule_name"] = f"多规则回测（{len(rule_ids)} 条）"
                items.append(item)
            return items

    def get_run(self, run_id: int) -> Optional[Dict[str, Any]]:
        with self.db.get_session() as session:
            row = session.execute(
                select(StockRuleRun, StockRule.name)
                .join(StockRule, StockRule.id == StockRuleRun.rule_id)
                .where(StockRuleRun.id == run_id)
                .limit(1)
            ).one_or_none()
            if row is None:
                return None
            run, rule_name = row
            item = self.run_to_dict(run, rule_name)
            snapshot_json_values = session.execute(
                select(StockRuleMatch.snapshot_json).where(StockRuleMatch.run_id == run.id)
            ).scalars().all()
            item["event_count"] = self._count_event_rows_from_snapshots(list(snapshot_json_values))
            return item

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

    def create_run(self, rule_id: int, target_count: int, error: Optional[str] = None) -> int:
        with self.db.get_session() as session:
            row = StockRuleRun(rule_id=rule_id, target_count=target_count, status="running", error=error)
            session.add(row)
            session.commit()
            session.refresh(row)
            return int(row.id)

    def update_run_progress(
        self,
        *,
        run_id: int,
        rule_ids: List[int],
        rule_names: List[str],
        completed_count: int,
        errors: Optional[List[str]] = None,
    ) -> None:
        with self.db.get_session() as session:
            run = session.execute(select(StockRuleRun).where(StockRuleRun.id == run_id).limit(1)).scalar_one_or_none()
            if run is None or run.status != "running":
                return
            run.error = encode_rule_batch_metadata(
                rule_ids,
                rule_names,
                errors or [],
                completed_count=completed_count,
            )
            session.commit()

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
                        rule_id=int(match.get("rule_id") or rule_id),
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
