from fastapi import BackgroundTasks

from api.v1.endpoints import rules as rules_endpoint
from api.v1.schemas.rules import RuleBatchRunRequest, RuleTarget
from src.services.tenant_service import TenantContext


class _FakeTenantRuleService:
    def __init__(self, *, visible_rule_ids, run_id, target_count):
        self.visible_rule_ids = list(visible_rule_ids)
        self.run_id = run_id
        self.target_count = target_count
        self.started_rule_ids = []

    def list_rules(self):
        return [
            {"id": rule_id, "name": f"Rule {rule_id}", "is_active": True}
            for rule_id in self.visible_rule_ids
        ]

    def start_run_rules(self, rule_ids, **_kwargs):
        self.started_rule_ids.append(list(rule_ids))
        return {
            "run_id": self.run_id,
            "rule_id": rule_ids[0],
            "rule_ids": list(rule_ids),
            "rule_names": [f"Rule {rule_id}" for rule_id in rule_ids],
            "status": "running",
            "target_count": self.target_count,
            "completed_count": 0,
            "match_count": 0,
            "event_count": 0,
            "reused_run": False,
            "prewarm_only": False,
        }, None


def test_multi_tenant_async_rule_run_filters_visible_rules_and_aggregates(monkeypatch):
    tenants = [
        TenantContext(id=1, key="default", name="默认租户", is_default=True),
        TenantContext(id=2, key="quant_team", name="量化组", is_default=False),
    ]
    services = {
        "default": _FakeTenantRuleService(visible_rule_ids=[1, 2], run_id=101, target_count=10),
        "quant_team": _FakeTenantRuleService(visible_rule_ids=[1, 3], run_id=202, target_count=20),
    }

    monkeypatch.setattr(
        rules_endpoint,
        "_service_for_tenant",
        lambda tenant: services[tenant.key],
    )

    payload = RuleBatchRunRequest(
        rule_ids=[1, 2, 3],
        mode="history",
        target=RuleTarget(scope="watchlist"),
        tenant_keys=["default", "quant_team"],
    )

    response = rules_endpoint._start_async_rule_run_for_tenants(payload, tenants, BackgroundTasks())

    assert services["default"].started_rule_ids == [[1, 2]]
    assert services["quant_team"].started_rule_ids == [[1, 3]]
    assert response["run_id"] == 101
    assert response["run_ids"] == [101, 202]
    assert response["target_count"] == 30
    assert response["tenant_runs"][0]["tenant_key"] == "default"
    assert response["tenant_runs"][1]["tenant_key"] == "quant_team"
    assert response["errors"] == []
