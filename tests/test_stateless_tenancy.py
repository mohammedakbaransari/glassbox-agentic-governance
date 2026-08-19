"""Tests for stateless tenant handling (GB-027).

v1's ``MultiTenantPipeline`` built one ``GovernancePipeline`` -- with its own
``ThreadPoolExecutor``, ``RLock`` and audit thread -- per tenant, and never
evicted one, so a tenant that was removed kept being served by a stale cached
pipeline `[measured]`. With all governance state external (GB-005, GB-006,
GB-011, GB-015, GB-018, GB-022), :class:`DecisionService` needs no per-tenant
object at all: this suite proves that both structurally (there is no attribute
slot to hold such a cache) and empirically (thread count is flat across
thousands of distinct tenants).
"""

from __future__ import annotations

import threading

import pytest

from glassbox.adapters.outbound.memory import memory_adapter_set, wire_receipt_check
from glassbox.app.composition import GovernanceRuntime, build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.app.decision_service import DecisionService
from glassbox.domain.action import ResourceRef
from glassbox.domain.decision import DecisionEffect
from glassbox.domain.identity import CredentialType, RawCredential

ACTION_NAME = "payments.wire_transfer"


def _dev_config() -> GlassBoxConfig:
    return GlassBoxConfig(profile=RuntimeProfile.DEV)


def _runtime() -> GovernanceRuntime:
    return wire_receipt_check(build_runtime(_dev_config(), memory_adapter_set()))


def _credential(tenant_id: str) -> RawCredential:
    return RawCredential(
        credential_type=CredentialType.OIDC,
        material=f"dev:{tenant_id}:agent.bot:instance-01",
        presented_at=0.0,
    )


class TestNoPerTenantAttributeSlot:
    """Structural proof: neither class has anywhere to put a per-tenant cache."""

    def test_decision_service_has_no_slot_beyond_the_shared_runtime(self) -> None:
        assert DecisionService.__slots__ == ("_runtime",)

    def test_a_decision_service_instance_cannot_gain_an_attribute(self) -> None:
        service = DecisionService(_runtime())
        with pytest.raises(AttributeError):
            service._pipelines = {}  # type: ignore[attr-defined]

    def test_the_runtime_has_no_dict_to_smuggle_a_cache_into(self) -> None:
        """A ``@dataclass(frozen=True, slots=True)`` instance has no ``__dict__``:
        there is no fallback attribute store a later change could repurpose."""
        assert not hasattr(_runtime(), "__dict__")

    def test_the_runtime_cannot_gain_an_attribute_either(self) -> None:
        runtime = _runtime()
        # CPython 3.11 raises TypeError for an unknown field on this
        # frozen+slotted dataclass; newer supported versions raise AttributeError.
        with pytest.raises((AttributeError, TypeError)):
            runtime.tenant_pipelines = {}  # type: ignore[attr-defined]
        assert not hasattr(runtime, "tenant_pipelines")


class TestConstantThreadCountAcrossTenants:
    """Regression for v1's one-pipeline-plus-executor-per-tenant, never evicted."""

    def test_thread_count_does_not_grow_with_tenant_count(self) -> None:
        runtime = _runtime()
        service = DecisionService(runtime)
        baseline = threading.active_count()

        # An ungoverned action name denies at the catalogue stage -- cheap, and
        # it still exercises identity, catalogue and evidence for a genuinely
        # distinct tenant on every iteration, which is what would have grown a
        # per-tenant cache under v1's design.
        for index in range(2_000):
            tenant_id = f"tenant-{index:05d}"
            outcome = service.decide_and_dispatch_for_request(
                _credential(tenant_id),
                action_name=ACTION_NAME,
                resource=ResourceRef(kind="account", id="ACC-1", tenant_id=tenant_id),
                parameters={},
                idempotency_key=f"idem-{tenant_id}",
            )
            assert outcome.decision.effect is DecisionEffect.DENY

        after = threading.active_count()
        assert (
            after == baseline
        ), f"thread count grew from {baseline} to {after} across 2,000 tenants"

    def test_the_same_runtime_instance_serves_every_tenant(self) -> None:
        """There is exactly one wired object graph, shared, never rebuilt per tenant."""
        runtime = _runtime()
        service = DecisionService(runtime)
        for tenant_id in ("tenant-a", "tenant-b", "tenant-c"):
            service.decide_and_dispatch_for_request(
                _credential(tenant_id),
                action_name=ACTION_NAME,
                resource=ResourceRef(kind="account", id="ACC-1", tenant_id=tenant_id),
                parameters={},
                idempotency_key=f"idem-{tenant_id}",
            )
        assert service._runtime is runtime
