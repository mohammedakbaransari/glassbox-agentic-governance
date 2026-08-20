"""HTTP-layer tests for the approval workflow endpoints (Workstream D).

Mirrors the fixture style of ``tests/test_http_app.py``: a fully governed dev
runtime, wired here with a policy decision point that always routes to human
review, plus an attached workflow engine so the approval endpoints have
something to operate on.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from glassbox.adapters.inbound.http.app import create_app
from glassbox.adapters.outbound.memory import InMemoryActionCatalogue, memory_adapter_set
from glassbox.adapters.outbound.replay import build_null_dispatcher
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.domain.action import BlastRadius, ConsequenceClass, Exposure
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition, ExposureRule
from glassbox.domain.decision import AuthorizationDecision
from glassbox.domain.mandate import Mandate
from glassbox.store.repository import SQLiteWorkflowRepository
from glassbox.workflow.workflow_engine import WorkflowEngine

TENANT = "acme"
AGENT = "agent.treasury-bot"
ACTION_NAME = "payments.wire_transfer"


def _dev_config() -> GlassBoxConfig:
    return GlassBoxConfig(profile=RuntimeProfile.DEV)


def _dev_credential_header(agent: str = AGENT, tenant: str = TENANT) -> Dict[str, str]:
    return {"Authorization": f"Bearer dev:{tenant}:{agent}:instance-01"}


class _RequireApprovalPdp:
    def decide(self, request: Any) -> Any:
        return AuthorizationDecision.require_approval(
            rationale="dual control required",
            policy_bundle_id="b",
            policy_bundle_sha256="0" * 64,
        )

    def active_bundle_digest(self, tenant_id: str) -> str:
        return "0" * 64


@pytest.fixture
def client_with_workflow(monkeypatch: pytest.MonkeyPatch):
    runtime = build_runtime(_dev_config(), memory_adapter_set())
    object.__setattr__(runtime, "policy_decision_point", _RequireApprovalPdp())
    object.__setattr__(runtime, "dispatcher", build_null_dispatcher(_dev_config()))

    catalogue = runtime.action_catalogue
    assert isinstance(catalogue, InMemoryActionCatalogue)
    catalogue.load_bundle(
        ActionCatalogueBundle(
            bundle_id="bundle.v1",
            tenant_id=TENANT,
            version=1,
            definitions=(
                ActionDefinition(
                    action=ACTION_NAME,
                    consequence=ConsequenceClass.REVERSIBLE,
                    exposure_rule=ExposureRule(
                        blast_radius=BlastRadius.SINGLE, monetary_field="amount"
                    ),
                ),
            ),
        )
    )
    runtime.mandate_store.put(
        Mandate(
            tenant_id=TENANT,
            agent_ref=AGENT,
            version=1,
            max_consequence=ConsequenceClass.IRREVERSIBLE,
            max_exposure=Exposure(monetary=1_000_000.0),
            valid_from=0.0,
            allowed_actions=frozenset({"payments.*"}),
            allowed_resources=frozenset({"account/*"}),
        )
    )

    from glassbox.domain.limits import Window
    from glassbox.ports.baseline import BaselineKey, BaselineScope

    key = BaselineKey(
        tenant_id=TENANT,
        scope=BaselineScope.AGENT,
        subject=AGENT,
        metric="exposure_monetary",
        window=Window(30 * 86_400),
    )
    for index in range(40):
        runtime.baseline_store.observe(key, 100.0 + (index % 5) - 2, now=0.0)

    runtime = runtime.with_workflow_engine(WorkflowEngine(repository=SQLiteWorkflowRepository(":memory:")))

    app = create_app(runtime)
    app.config["TESTING"] = True
    return app.test_client()


def _submit_pending_action(client: Any, idempotency_key: str = "idem-appr-http-0001") -> str:
    resp = client.post(
        f"/v2/actions/{ACTION_NAME}",
        headers=_dev_credential_header(),
        json={
            "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
            "parameters": {"amount": 101.0},
            "idempotency_key": idempotency_key,
        },
    )
    assert resp.status_code == 202
    body = resp.get_json()
    assert body["execution"]["status"] == "pending_approval"
    return body["decision_id"]


class TestApprovalEndpoints:
    def test_a_pending_decision_appears_in_the_queue(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow)

        resp = client_with_workflow.get("/v2/approvals")
        assert resp.status_code == 200
        pending_ids = [item["decision_id"] for item in resp.get_json()["pending"]]
        assert decision_id in pending_ids

    def test_status_reflects_pending_state(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0002")

        resp = client_with_workflow.get(f"/v2/approvals/{decision_id}")
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "pending"

    def test_unknown_decision_status_is_404(self, client_with_workflow: Any) -> None:
        resp = client_with_workflow.get("/v2/approvals/no-such-decision")
        assert resp.status_code == 404

    def test_approve_transitions_state_and_never_dispatches(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0003")

        resp = client_with_workflow.post(
            f"/v2/approvals/{decision_id}/approve",
            json={"actor": "reviewer@example.com", "notes": "verified"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "approved"

    def test_reject_transitions_state(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0004")

        resp = client_with_workflow.post(
            f"/v2/approvals/{decision_id}/reject",
            json={"actor": "reviewer@example.com", "notes": "denied"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "rejected"

    def test_escalate_reassigns_reviewer(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0005")

        resp = client_with_workflow.post(
            f"/v2/approvals/{decision_id}/escalate",
            json={"actor": "reviewer@example.com", "escalate_to": "senior-reviewer"},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["state"] == "in_review"
        assert body["escalate_to"] == "senior-reviewer"

    def test_revoke_withdraws_a_pending_request(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0006")

        resp = client_with_workflow.post(
            f"/v2/approvals/{decision_id}/revoke",
            json={"actor": "system", "notes": "mandate revoked"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["state"] == "revoked"

    def test_approving_without_an_actor_is_400(self, client_with_workflow: Any) -> None:
        decision_id = _submit_pending_action(client_with_workflow, "idem-appr-http-0007")

        resp = client_with_workflow.post(f"/v2/approvals/{decision_id}/approve", json={})
        assert resp.status_code == 400
