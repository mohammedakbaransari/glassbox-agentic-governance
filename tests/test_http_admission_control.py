"""Tests for HTTP-layer admission control (Workstream B).

Exercises the guard exactly as wired in ``create_app``: a fixed-window
per-client-key rate limit enforced by a ``before_request`` hook, before any
identity verification or governance work runs.
"""

from __future__ import annotations

from typing import Any, Dict

import pytest

from glassbox.adapters.inbound.http.admission_control import (
    AdmissionVerdict,
    HttpAdmissionController,
)
from glassbox.adapters.inbound.http.app import create_app
from glassbox.adapters.outbound.memory import memory_adapter_set
from glassbox.adapters.outbound.memory.clock import FrozenClock
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, HttpAdmissionConfig, RuntimeProfile

TENANT = "acme"
AGENT = "agent.treasury-bot"


def _dev_credential_header(agent: str = AGENT, tenant: str = TENANT) -> Dict[str, str]:
    return {"Authorization": f"Bearer dev:{tenant}:{agent}:instance-01"}


class TestHttpAdmissionControllerUnit:
    """The controller's own sliding-window policy, isolated from Flask."""

    def test_admits_up_to_the_configured_ceiling(self) -> None:
        clock = FrozenClock(instant=0.0)
        controller = HttpAdmissionController(clock=clock, max_requests=3, window_seconds=10.0)
        for _ in range(3):
            assert controller.check("client-a").admitted

    def test_rejects_once_the_ceiling_is_exceeded(self) -> None:
        clock = FrozenClock(instant=0.0)
        controller = HttpAdmissionController(clock=clock, max_requests=2, window_seconds=10.0)
        controller.check("client-a")
        controller.check("client-a")
        verdict = controller.check("client-a")
        assert not verdict.admitted
        assert verdict.retry_after_s > 0.0

    def test_a_different_client_key_has_its_own_budget(self) -> None:
        clock = FrozenClock(instant=0.0)
        controller = HttpAdmissionController(clock=clock, max_requests=1, window_seconds=10.0)
        assert controller.check("client-a").admitted
        assert not controller.check("client-a").admitted
        assert controller.check("client-b").admitted

    def test_the_window_resets_after_it_elapses(self) -> None:
        clock = FrozenClock(instant=0.0)
        controller = HttpAdmissionController(clock=clock, max_requests=1, window_seconds=10.0)
        assert controller.check("client-a").admitted
        assert not controller.check("client-a").admitted
        clock.instant = 10.1
        assert controller.check("client-a").admitted

    def test_rejects_invalid_construction_arguments(self) -> None:
        clock = FrozenClock(instant=0.0)
        with pytest.raises(ValueError):
            HttpAdmissionController(clock=clock, max_requests=0, window_seconds=10.0)
        with pytest.raises(ValueError):
            HttpAdmissionController(clock=clock, max_requests=1, window_seconds=0.0)

    def test_evicts_the_oldest_tracked_client_once_capacity_is_reached(self) -> None:
        clock = FrozenClock(instant=0.0)
        controller = HttpAdmissionController(
            clock=clock, max_requests=5, window_seconds=10.0, max_tracked_clients=2
        )
        controller.check("client-a")
        controller.check("client-b")
        controller.check("client-c")
        assert "client-a" not in controller._buckets  # type: ignore[attr-defined]
        assert len(controller._buckets) <= 2  # type: ignore[attr-defined]


@pytest.fixture
def admission_client() -> Any:
    """A dev runtime with a very tight admission budget, for HTTP-level tests."""
    config = GlassBoxConfig(
        profile=RuntimeProfile.DEV,
        http_admission=HttpAdmissionConfig(max_requests=2, window_seconds=10.0),
    )
    runtime = build_runtime(config, memory_adapter_set())
    app = create_app(runtime)
    app.config["TESTING"] = True
    return app.test_client()


class TestAdmissionControlOverHttp:
    def test_healthz_is_never_rate_limited(self, admission_client: Any) -> None:
        for _ in range(5):
            resp = admission_client.get("/healthz")
            assert resp.status_code == 200

    def test_requests_within_budget_reach_the_pipeline(self, admission_client: Any) -> None:
        resp = admission_client.post(
            "/v2/actions/payments.unknown_action",
            headers=_dev_credential_header(),
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "idempotency_key": "idem-admission-1",
            },
        )
        # Denied for being ungoverned -- proves the request reached the
        # pipeline, which is the point: admission did not block it.
        assert resp.status_code == 403

    def test_a_client_over_budget_gets_429_before_the_pipeline_runs(
        self, admission_client: Any
    ) -> None:
        for index in range(2):
            admission_client.post(
                "/v2/actions/payments.unknown_action",
                headers=_dev_credential_header(),
                json={
                    "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                    "idempotency_key": f"idem-admission-budget-{index}",
                },
            )
        resp = admission_client.post(
            "/v2/actions/payments.unknown_action",
            headers=_dev_credential_header(),
            json={
                "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                "idempotency_key": "idem-admission-over-budget",
            },
        )
        assert resp.status_code == 429
        assert resp.get_json()["error_class"] == "AdmissionRejected"
        assert "Retry-After" in resp.headers

    def test_admission_control_can_be_disabled(self) -> None:
        config = GlassBoxConfig(
            profile=RuntimeProfile.DEV,
            http_admission=HttpAdmissionConfig(enabled=False),
        )
        runtime = build_runtime(config, memory_adapter_set())
        app = create_app(runtime)
        app.config["TESTING"] = True
        client = app.test_client()
        for _ in range(10):
            resp = client.post(
                "/v2/actions/payments.unknown_action",
                headers=_dev_credential_header(),
                json={
                    "resource": {"kind": "account", "id": "ACC-1", "tenant_id": TENANT},
                    "idempotency_key": "idem-disabled",
                },
            )
            assert resp.status_code != 429
