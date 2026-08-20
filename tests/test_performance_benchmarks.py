"""P99 latency benchmark for the v2 decision path (P3 roadmap item).

Not run by default: latency assertions are environment-sensitive, and this
suite's purpose is a repeatable local/CI-opt-in signal, not a per-commit gate
that would make the pipeline flaky on shared/noisy CI runners. Enable with
``GLASSBOX_RUN_BENCHMARKS=1``.
"""

from __future__ import annotations

import os
import statistics
import time

import pytest

from glassbox.adapters.outbound.memory import (
    AllowListPolicyDecisionPoint,
    memory_adapter_set,
    wire_receipt_check,
)
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.app.decision_service import DecisionService
from glassbox.domain.action import ConsequenceClass, Exposure, ProposedAction, ResourceRef
from glassbox.domain.identity import CredentialType, RawCredential
from glassbox.domain.mandate import Mandate

pytestmark = pytest.mark.skipif(
    not os.environ.get("GLASSBOX_RUN_BENCHMARKS"),
    reason="opt-in benchmark; set GLASSBOX_RUN_BENCHMARKS=1 to run",
)

TENANT = "acme"
AGENT = "agent.benchmark"
ITERATIONS = 500


def _percentile(samples: list, pct: float) -> float:
    ordered = sorted(samples)
    index = min(len(ordered) - 1, int(len(ordered) * pct))
    return ordered[index]


class TestDecisionPathLatency:
    def test_p99_latency_for_a_reversible_action_is_bounded(self) -> None:
        config = GlassBoxConfig(profile=RuntimeProfile.DEV)
        runtime = wire_receipt_check(build_runtime(config, memory_adapter_set()))
        pdp = runtime.policy_decision_point
        assert isinstance(pdp, AllowListPolicyDecisionPoint)
        pdp.allow(TENANT, "payments.*")
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
        runtime.dispatcher.register("payments.wire_transfer", lambda action: {"status": "sent"})
        service = DecisionService(runtime)
        credential = RawCredential(
            credential_type=CredentialType.OIDC,
            material=f"dev:{TENANT}:{AGENT}:instance-01",
            presented_at=0.0,
        )

        samples = []
        for index in range(ITERATIONS):
            proposed = ProposedAction(
                action="payments.wire_transfer",
                resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
                consequence=ConsequenceClass.REVERSIBLE,
                exposure=Exposure(monetary=100.0),
                idempotency_key=f"idem-bench-{index}",
            )
            started = time.perf_counter()
            service.decide_and_dispatch(credential, proposed)
            samples.append(time.perf_counter() - started)

        p50 = _percentile(samples, 0.50)
        p99 = _percentile(samples, 0.99)
        print(f"decide_and_dispatch p50={p50 * 1000:.3f}ms p99={p99 * 1000:.3f}ms mean={statistics.mean(samples) * 1000:.3f}ms")
        # Generous bound: this asserts "no gross regression" (e.g. an
        # accidental O(n) scan added to the hot path), not a tuned SLA.
        assert p99 < 0.25, f"p99 latency regressed: {p99 * 1000:.3f}ms"
