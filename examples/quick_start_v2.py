"""A minimal, runnable end-to-end example of the v2 decision path.

Uses the in-memory reference adapter set: no database, Redis, or KMS
required. This is the development/demo configuration only — see
docs/DEPLOYMENT/guide.md for a production adapter set (Postgres, Redis, a
managed KMS key, and durable WORM anchoring).

Run:
    python examples/quick_start_v2.py
"""

from __future__ import annotations

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
from glassbox.domain.limits import Window
from glassbox.domain.mandate import Mandate
from glassbox.ports.baseline import BaselineKey, BaselineScope

TENANT = "acme"
AGENT = "agent.procurement-bot"


def main() -> None:
    config = GlassBoxConfig(profile=RuntimeProfile.DEV)
    runtime = wire_receipt_check(build_runtime(config, memory_adapter_set()))

    # Deny by default: an action must be explicitly permitted by policy...
    pdp = runtime.policy_decision_point
    assert isinstance(pdp, AllowListPolicyDecisionPoint)
    pdp.allow(TENANT, "procurement.purchase_order")

    # ...and by a mandate scoping what the agent may do and up to what exposure.
    runtime.mandate_store.put(
        Mandate(
            tenant_id=TENANT,
            agent_ref=AGENT,
            version=1,
            max_consequence=ConsequenceClass.COMPENSABLE,
            max_exposure=Exposure(monetary=250_000.0),
            valid_from=0.0,
            allowed_actions=frozenset({"procurement.*"}),
            allowed_resources=frozenset({"purchase_order/*"}),
        )
    )

    # A behavioural baseline avoids a false-positive cold-start anomaly denial:
    # the first observation for a subject has nothing to compare against.
    baseline_key = BaselineKey(
        tenant_id=TENANT,
        scope=BaselineScope.AGENT,
        subject=AGENT,
        metric="exposure_monetary",
        window=Window(30 * 86_400),
    )
    for index in range(40):
        jitter = (index % 5) - 2  # jittered, not identical: stddev must not be zero
        runtime.baseline_store.observe(baseline_key, 75_000.0 + jitter * 1_000, now=0.0)

    # A dispatcher handler is what actually performs the effect once allowed.
    runtime.dispatcher.register(
        "procurement.purchase_order", lambda action: {"status": "submitted"}
    )

    service = DecisionService(runtime)
    credential = RawCredential(
        credential_type=CredentialType.OIDC,
        material=f"dev:{TENANT}:{AGENT}:instance-01",
        presented_at=0.0,
    )
    action = ProposedAction(
        action="procurement.purchase_order",
        resource=ResourceRef(kind="purchase_order", id="PO-1042", tenant_id=TENANT),
        consequence=ConsequenceClass.COMPENSABLE,
        exposure=Exposure(monetary=75_000.0),
        idempotency_key="idem-po-1042",
    )

    outcome = service.decide_and_dispatch(credential, action)
    print("effect:      ", outcome.decision.effect)
    print("rationale:   ", outcome.decision.rationale)
    print("execution:   ", outcome.execution.status)
    print("evidence at: ", outcome.receipt.segment_id, outcome.receipt.seq)


if __name__ == "__main__":
    main()
