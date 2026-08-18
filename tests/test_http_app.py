"""Tests for the v2 HTTP surface (GB-026a).

The claim under test: this layer parses transport, verifies identity through
the port, calls the service, and serialises the result -- and nothing else.
Tenancy in particular is never a toggle here (regression target: v1's
``tenant_scoping_required``, defaulting ``False``); a spoofed ``X-Tenant-ID`` is
checked against the verified principal by the service, on every request.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from glassbox.adapters.inbound.http.app import create_app
from glassbox.adapters.outbound.memory import (
    AllowListPolicyDecisionPoint,
    InMemoryActionCatalogue,
    memory_adapter_set,
    wire_receipt_check,
)
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.domain.action import BlastRadius, ConsequenceClass, Exposure, ProposedAction
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition, ExposureRule
from glassbox.domain.mandate import Mandate

TENANT = "acme"
AGENT = "agent.treasury-bot"
ACTION_NAME = "payments.wire_transfer"


def _dev_config() -> GlassBoxConfig:
    return GlassBoxConfig(profile=RuntimeProfile.DEV)


def _dev_credential_header(agent: str = AGENT, tenant: str = TENANT) -> Dict[str, str]:
    return {"Authorization": f"Bearer dev:{tenant}:{agent}:instance-01"}


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch):
    """A Flask test client wired to a fully governed dev runtime."""
    runtime = wire_receipt_check(build_runtime(_dev_config(), memory_adapter_set()))

    pdp = runtime.policy_decision_point
    assert isinstance(pdp, AllowListPolicyDecisionPoint)
    pdp.allow(TENANT, ACTION_NAME)

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

    dispatched = []

    def handler(action: ProposedAction) -> Dict[str, str]:
        dispatched.append(action.idempotency_key)
        return {"status": "sent"}

    runtime.dispatcher.register(ACTION_NAME, handler)

    app = create_app(runtime)
    app.config["TESTING"] = True
    app.dispatched = dispatched  # type: ignore[attr-defined]
    return app.test_client()


class TestHealthz:
    def test_reports_the_wired_adapter_set(self, client: Any) -> None:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.get_json()["adapter_set"] == "in-memory-reference"


class TestDecideAction:
    def test_a_permitted_action_is_allowed_and_dispatched(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            headers=_dev_credential_header(),
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "parameters": {"amount": 101.0},
                "idempotency_key": "idem-http-0001",
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body["decision"]["effect"] == "allow"
        assert body["execution"]["status"] == "executed"

    def test_missing_credential_is_401(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "idempotency_key": "idem-http-0002",
            },
        )
        assert resp.status_code == 401

    def test_a_spoofed_tenant_header_is_rejected_not_trusted(self, client: Any) -> None:
        """Regression: v1's tenant_scoping_required could default to False and
        accept X-Tenant-ID verbatim. Here the header is checked against the
        verified principal and a mismatch denies -- it never selects a tenant."""
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            headers={**_dev_credential_header(), "X-Tenant-ID": "evilcorp"},
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "parameters": {"amount": 101.0},
                "idempotency_key": "idem-http-0003",
            },
        )
        assert resp.status_code == 403
        assert "identity_unverified" in resp.get_json()["decision"]["reasons"]

    def test_an_ungoverned_action_is_denied_not_auto_executed(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.unknown_action",
            headers=_dev_credential_header(),
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "idempotency_key": "idem-http-0004",
            },
        )
        assert resp.status_code == 403
        assert "action_not_governed" in resp.get_json()["decision"]["reasons"]

    def test_a_non_json_body_is_400(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            headers=_dev_credential_header(),
            data="not json",
            content_type="text/plain",
        )
        assert resp.status_code == 400

    def test_a_missing_idempotency_key_is_400(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            headers=_dev_credential_header(),
            json={"resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT}},
        )
        assert resp.status_code == 400

    def test_cross_tenant_resource_is_rejected(self, client: Any) -> None:
        resp = client.post(
            "/v2/actions/payments.wire_transfer",
            headers=_dev_credential_header(),
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": "evilcorp"},
                "idempotency_key": "idem-http-0005",
            },
        )
        assert resp.status_code == 400


class TestReplay:
    def test_replay_never_dispatches(self, client: Any) -> None:
        resp = client.post(
            "/v2/replay",
            json={
                "principal": {
                    "agent_ref": AGENT,
                    "agent_instance_id": "instance-01",
                    "tenant_id": TENANT,
                    "credential_type": "oidc",
                    "credential_id": "cred-1",
                    "issued_at": 0.0,
                    "expires_at": 1_000_000_000.0,
                },
                "action": {
                    "action": ACTION_NAME,
                    "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                    "consequence": "reversible",
                    "exposure": {"blast_radius": "single", "monetary": 101.0},
                    "idempotency_key": "idem-replay-http-0001",
                },
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["execution"]["status"] == "replayed"

    def test_a_malformed_replay_body_is_400(self, client: Any) -> None:
        resp = client.post("/v2/replay", json={"principal": {}, "action": {}})
        assert resp.status_code == 400
