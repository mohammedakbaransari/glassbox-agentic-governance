"""In-memory reference adapter set (GB-003).

**This adapter set provides no assurance and must never serve production.** It is
marked ``dev_only``, and :func:`~glassbox.app.composition.build_runtime` refuses
to wire it into a profile that claims to provide assurance.

It exists for three reasons:

1. it makes the ``dev`` profile real, so the system can be exercised on a laptop;
2. it is the **conformance reference** for the durable adapters in GB-005
   (Postgres evidence), GB-006 (KMS signing), GB-011 (Redis limits) and GB-022
   (Redis baselines) -- the semantics are far easier to read here than in a
   schema plus a transaction, and the shared conformance suite will run against
   both;
3. it unblocks GB-008, which needs a complete object graph to build a decision
   service against.

Its limitations are honest and total: state is per-process, nothing survives a
restart, the signing key is readable by the writer, and N replicas would enforce
N times every limit.
"""

from __future__ import annotations

from typing import Optional

from glassbox.adapters.outbound.memory.catalogue import (
    InMemoryActionCatalogue,
    InMemoryAttestationProvider,
    build_action_catalogue,
    build_attestation_provider,
)
from glassbox.adapters.outbound.memory.clock import FrozenClock, SystemClock, build_clock
from glassbox.adapters.outbound.memory.decisioning import (
    REFERENCE_RISK_MODEL_VERSION,
    AllowListPolicyDecisionPoint,
    DevIdentityVerifier,
    ReferenceRiskEngine,
    build_identity_verifier,
    build_policy_decision_point,
    build_risk_engine,
)
from glassbox.adapters.outbound.memory.dispatch import (
    EffectHandler,
    InMemoryDispatcher,
    build_dispatcher,
)
from glassbox.adapters.outbound.memory.evidence import (
    InMemoryEvidenceStore,
    StoredRecord,
    build_evidence_store,
)
from glassbox.adapters.outbound.memory.governance_state import (
    InMemoryBaselineStore,
    InMemoryLimitStore,
    InMemoryMandateStore,
    build_baseline_store,
    build_limit_store,
    build_mandate_store,
)
from glassbox.adapters.outbound.memory.kill_switch import InMemoryKillSwitch, build_kill_switch
from glassbox.adapters.outbound.memory.policy import (
    DeclarativePolicyDecisionPoint,
    build_declarative_policy_decision_point,
)
from glassbox.adapters.outbound.memory.signing import LocalMacSigner, build_mac_signer
from glassbox.adapters.outbound.memory.tool_registry import (
    InMemoryToolRegistry,
    build_tool_registry,
)
from glassbox.app.composition import AdapterSet, GovernanceRuntime
from glassbox.app.config import GlassBoxConfig

__all__ = [
    "MEMORY_ADAPTER_SET_NAME",
    "REFERENCE_RISK_MODEL_VERSION",
    "AllowListPolicyDecisionPoint",
    "DeclarativePolicyDecisionPoint",
    "DevIdentityVerifier",
    "EffectHandler",
    "FrozenClock",
    "InMemoryActionCatalogue",
    "InMemoryAttestationProvider",
    "InMemoryBaselineStore",
    "InMemoryDispatcher",
    "InMemoryEvidenceStore",
    "InMemoryKillSwitch",
    "InMemoryLimitStore",
    "InMemoryMandateStore",
    "InMemoryToolRegistry",
    "LocalMacSigner",
    "ReferenceRiskEngine",
    "StoredRecord",
    "SystemClock",
    "build_declarative_policy_decision_point",
    "memory_adapter_set",
    "wire_memory_adapter_set",
    "wire_receipt_check",
]

MEMORY_ADAPTER_SET_NAME = "in-memory-reference"


def memory_adapter_set() -> AdapterSet:
    """Return the complete in-memory adapter set.

    Always ``dev_only``. Marking it otherwise would let an in-memory evidence
    store, a locally-held signing key and a single-process limit store reach a
    production deployment, which is the failure mode the whole rebuild exists to
    eliminate.
    """
    return AdapterSet(
        name=MEMORY_ADAPTER_SET_NAME,
        dev_only=True,
        factories={
            "clock": build_clock,
            "identity_verifier": build_identity_verifier,
            "mandate_store": build_mandate_store,
            "policy_decision_point": build_policy_decision_point,
            "risk_engine": build_risk_engine,
            "limit_store": build_limit_store,
            "baseline_store": build_baseline_store,
            "mac_signer": build_mac_signer,
            "evidence_store": build_evidence_store,
            "dispatcher": build_dispatcher,
            "action_catalogue": build_action_catalogue,
            "attestation_provider": build_attestation_provider,
            "tool_registry": build_tool_registry,
            "kill_switch": build_kill_switch,
        },
    )


def wire_memory_adapter_set(runtime: GovernanceRuntime) -> GovernanceRuntime:
    """Close the cross-component wiring the composition root cannot express.

    ``build_runtime`` calls every factory with only the config, so two
    independently-built components cannot share state that isn't derivable from
    config alone. Two such gaps exist in this adapter set, and both are closed
    here rather than papered over in a test:

    * **The evidence store's signer must be the same object as ``mac_signer``.**
      Left unwired, ``build_evidence_store`` and ``build_mac_signer`` each
      construct their own ``LocalMacSigner``, and -- because no explicit key was
      configured -- each one generates a *different* random key. The evidence
      store would then sign with a key that ``runtime.mac_signer`` cannot verify
      or disable, silently defeating any test or operational tool that expects
      them to be the same signer.
    * **The dispatcher must verify receipts against the real evidence store**
      (invariant I1), not merely accept anything shaped like one.

    Returns:
        The same runtime, for call chaining.
    """
    store = runtime.evidence_store
    if isinstance(store, InMemoryEvidenceStore):
        store._signer = runtime.mac_signer

    dispatcher = runtime.dispatcher
    if isinstance(dispatcher, InMemoryDispatcher) and isinstance(store, InMemoryEvidenceStore):
        dispatcher.set_receipt_check(store.has_receipt)
    return runtime


#: Backward-compatible alias; prefer :func:`wire_memory_adapter_set`.
wire_receipt_check = wire_memory_adapter_set
