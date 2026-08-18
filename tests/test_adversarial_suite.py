"""The adversarial test suite.

Codifies the 20-threat security model described in ``docs/CLAIMS.md`` as
permanent tests. Each test below proves the corresponding behaviour closes
its threat -- structurally, not by policy or configuration a caller could
get wrong.

This suite is deliberately an index, not a duplicate of every edge case
already covered elsewhere: where a threat has deep, dedicated coverage in
another file (replay, tool registry, dispatcher admission control, ...), the
test here is the concise, canonical proof that *this exact attack* fails, and
the docstring points to where the fuller behavioural suite lives.
"""

from __future__ import annotations

import hashlib
import re
import threading
from pathlib import Path
from typing import List

import pytest

from glassbox.adapters.outbound.memory import (
    AllowListPolicyDecisionPoint,
    DevIdentityVerifier,
    InMemoryActionCatalogue,
    InMemoryAttestationProvider,
    InMemoryBaselineStore,
    InMemoryDispatcher,
    InMemoryEvidenceStore,
    InMemoryLimitStore,
    LocalMacSigner,
)
from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition, ExposureRule
from glassbox.domain.decision import DecisionEffect, DenialReason, ExecutionStatus
from glassbox.domain.errors import (
    DispatchRefusedError,
    DomainValidationError,
    IdentityError,
    LimitStoreUnavailable,
)
from glassbox.domain.evidence import IntegrityStatus
from glassbox.domain.identity import CredentialType, RawCredential
from glassbox.domain.limits import LimitKey, LimitScope, Window
from glassbox.domain.policy_bundle import PolicyBundle, PolicyRule, RuleEffect
from tests.test_decision_service import (
    ACTION_NAME,
    AGENT,
    TENANT,
    Runtime,
    _load_tool_registry,
    credential,
    mandate,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REBUILT_LAYER_ROOTS = (
    _REPO_ROOT / "glassbox" / "domain",
    _REPO_ROOT / "glassbox" / "ports",
    _REPO_ROOT / "glassbox" / "app",
    _REPO_ROOT / "glassbox" / "adapters" / "outbound",
    _REPO_ROOT / "glassbox" / "adapters" / "inbound",
)


@pytest.fixture
def rt() -> Runtime:
    return Runtime()


class TestThreat01AuditForgery:
    """v1: rewrite a row and recompute an unkeyed SHA-256 -- it re-verified as
    intact `[measured]`. v2: the MAC is keyed (GB-006); a forged row is
    detected. Full behavioural suite: test_memory_adapters.py::TestEvidenceIntegrity."""

    def test_a_forged_record_is_detected_not_re_verified_as_intact(self, rt: Runtime) -> None:
        rt.happy_path()
        outcome = rt.service.decide_and_dispatch(credential(), _reversible_action())
        store = rt.runtime.evidence_store
        assert isinstance(store, InMemoryEvidenceStore)
        store.tamper_for_test(
            outcome.receipt.segment_id,
            seq=outcome.receipt.seq,
            replacement=_tampered_intent(rt, outcome.receipt.decision_id),
        )
        report = store.verify(outcome.receipt.segment_id, now=0.0)
        assert report.status is IntegrityStatus.BROKEN
        assert report.is_acceptable is False


class TestThreat02TenantImpersonation:
    """v1: `X-Tenant-ID` header copied verbatim into context. v2: a header is,
    at most, an assertion checked against the verified principal; a mismatch
    denies and is evidenced. Full suite: tests/test_http_app.py."""

    def test_a_spoofed_tenant_assertion_is_rejected(self, rt: Runtime) -> None:
        rt.happy_path()
        outcome = rt.service.decide_and_dispatch(
            credential(), _reversible_action(), asserted_tenant_id="evilcorp"
        )
        assert outcome.decision.effect is DecisionEffect.DENY
        assert DenialReason.IDENTITY_UNVERIFIED in outcome.decision.reasons


class TestThreat03UserImpersonation:
    """v1: `X-User-ID` header copied verbatim. v2: same assertion-vs-principal
    check as tenant, on the delegating subject. Full suite:
    tests/test_memory_adapters.py::TestDevIdentityVerifier."""

    def test_a_spoofed_subject_assertion_is_rejected(self) -> None:
        verifier = DevIdentityVerifier()
        principal = verifier.verify(
            RawCredential(
                credential_type=CredentialType.OIDC,
                material=f"dev:{TENANT}:{AGENT}:instance-01",
                presented_at=0.0,
            ),
            now=0.0,
        )
        with pytest.raises(IdentityError):
            verifier.assert_matches_assertion(principal, asserted_subject="mallory")


class TestThreat04CrossTenantDataRead:
    """v1: `tenant_scoping_required` defaulted `False`; a query without it
    returned every tenant's rows. v2: `tenant_id` is not optional on any
    signature that matters -- a resource targeting a different tenant than the
    credential raises before any read happens at all."""

    def test_a_resource_for_a_different_tenant_is_refused(self, rt: Runtime) -> None:
        rt.happy_path()
        with pytest.raises(DomainValidationError):
            rt.service.decide_and_dispatch(
                credential(),
                ProposedAction(
                    action=ACTION_NAME,
                    resource=ResourceRef(kind="account", id="ACC-1", tenant_id="evilcorp"),
                    consequence=ConsequenceClass.REVERSIBLE,
                    exposure=Exposure(monetary=1.0),
                    idempotency_key="idem-threat-04",
                ),
            )


class TestThreat05CrossTenantPolicyLeak:
    """v1: one shared default `GovernancePipeline` meant a policy registered for
    any tenant governed every tenant. v2: `AllowListPolicyDecisionPoint` (and
    every real PDP) is keyed by tenant; another tenant's allow-list does not
    apply. Full suite: tests/test_memory_adapters.py::TestAllowListPolicyDecisionPoint."""

    def test_another_tenants_policy_allow_does_not_leak(self, rt: Runtime) -> None:
        pdp = rt.runtime.policy_decision_point
        assert isinstance(pdp, AllowListPolicyDecisionPoint)
        pdp.allow("evilcorp", ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        outcome = rt.service.decide_and_dispatch(credential(), _reversible_action())
        assert outcome.decision.effect is DecisionEffect.DENY


class TestThreat06RiskDowngrade:
    """v1: caller-supplied `confidence`/`environment` in the request body were
    read directly into the risk/decision path. v2: there is no field for
    either on the governed request signature at all --
    `decide_and_dispatch_for_request` derives consequence/exposure from the
    catalogue only, never from caller parameters."""

    def test_a_forged_consequence_in_parameters_is_ignored(self, rt: Runtime) -> None:
        rt.happy_path()
        catalogue = rt.runtime.action_catalogue
        assert isinstance(catalogue, InMemoryActionCatalogue)
        catalogue.load_bundle(
            ActionCatalogueBundle(
                bundle_id="bundle.threat06",
                tenant_id=TENANT,
                version=1,
                definitions=(
                    ActionDefinition(
                        action=ACTION_NAME,
                        consequence=ConsequenceClass.IRREVERSIBLE,
                        exposure_rule=ExposureRule(
                            blast_radius=BlastRadius.SINGLE, monetary_field="amount"
                        ),
                    ),
                ),
            )
        )
        outcome = rt.service.decide_and_dispatch_for_request(
            credential(),
            action_name=ACTION_NAME,
            resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
            # A caller cannot even name a "confidence" or "environment" field on
            # this signature; the closest forgery attempt is smuggling a
            # consequence-shaped key into the transactional parameters, which
            # the catalogue's fixed `consequence` overrides entirely.
            parameters={"amount": 1.0, "consequence": "advisory", "environment": "sandbox"},
            idempotency_key="idem-threat-06",
        )
        record = rt.runtime.evidence_store._segments[  # type: ignore[attr-defined]
            outcome.receipt.segment_id
        ]
        stored = next(
            item.record for item in record if item.record.decision_id == outcome.decision_id
        )
        assert stored.action.consequence is ConsequenceClass.IRREVERSIBLE


class TestThreat07ControlBypass:
    """v1: policy rules read self-asserted `ctr_filed`, `change_window_approved`
    straight out of the request body. v2: required attestations are resolved
    from an `AttestationProvider` (a system of record); a same-named
    parameter is never consulted. Full suite:
    tests/test_decision_service.py::TestActionCatalogueStage."""

    def test_a_forged_attestation_field_is_never_read(self, rt: Runtime) -> None:
        rt.happy_path()
        catalogue = rt.runtime.action_catalogue
        assert isinstance(catalogue, InMemoryActionCatalogue)
        catalogue.load_bundle(
            ActionCatalogueBundle(
                bundle_id="bundle.threat07",
                tenant_id=TENANT,
                version=1,
                definitions=(
                    ActionDefinition(
                        action=ACTION_NAME,
                        consequence=ConsequenceClass.REVERSIBLE,
                        exposure_rule=ExposureRule(
                            blast_radius=BlastRadius.SINGLE, monetary_field="amount"
                        ),
                        required_attestations=("ctr_filed",),
                    ),
                ),
            )
        )
        provider = rt.runtime.attestation_provider
        assert isinstance(provider, InMemoryAttestationProvider)
        # Never recorded as satisfied in the system of record -- only forged in
        # the request body, which this path never reads for attestations.
        outcome = rt.service.decide_and_dispatch_for_request(
            credential(),
            action_name=ACTION_NAME,
            resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
            parameters={"amount": 1.0, "ctr_filed": True},
            idempotency_key="idem-threat-07",
        )
        assert outcome.decision.effect is DecisionEffect.DENY
        assert DenialReason.ATTESTATION_NOT_SATISFIED in outcome.decision.reasons


class TestThreat08AnomalyEvasion:
    """v1: `min_samples=10` meant the first ten observations from any agent id
    were never flagged -- an attacker only needed a fresh id `[measured]`. v2:
    cold start falls back to a peer-group prior; a brand-new agent's first
    extreme action is flagged. Full suite:
    tests/test_memory_adapters.py::TestBaselineStore."""

    def test_a_brand_new_agent_id_is_not_a_free_pass(self) -> None:
        from glassbox.ports.baseline import BaselineKey, BaselineScope

        store = InMemoryBaselineStore(min_samples=30)
        peer = BaselineKey(
            tenant_id=TENANT,
            scope=BaselineScope.AGENT,
            subject="agent.any",
            metric="exposure_monetary",
            window=Window(86_400),
        ).peer_group_fallback("treasury-agents")
        for _ in range(50):
            store.observe(peer, 1_000.0, now=0.0)

        verdict = store.evaluate(
            BaselineKey(
                tenant_id=TENANT,
                scope=BaselineScope.AGENT,
                subject="agent.freshly-minted",
                metric="exposure_monetary",
                window=Window(86_400),
            ),
            1_000_000_000_000.0,
            peer_group="treasury-agents",
            threshold=3.0,
            now=0.0,
        )
        assert verdict.anomalous is True


class TestThreat09VelocityEvasionViaOutage:
    """v1: a Redis outage fell open, admitting all traffic. v2: an unavailable
    limit store raises; non-advisory actions deny (invariant I4). Full suite:
    tests/test_memory_adapters.py::TestLimitStore, tests/test_redis_limits.py."""

    def test_a_limit_store_outage_denies_non_advisory_actions(self, rt: Runtime) -> None:
        rt.happy_path()
        store = rt.runtime.limit_store
        assert isinstance(store, InMemoryLimitStore)
        store.set_available(False)
        outcome = rt.service.decide_and_dispatch(credential(), _reversible_action())
        assert outcome.decision.effect is DecisionEffect.DENY
        assert DenialReason.DEPENDENCY_UNAVAILABLE in outcome.decision.reasons


class TestThreat10VelocityUndercount:
    """v1: `ZADD key now now` used the timestamp as both score and member, so
    two decisions in the same clock tick collapsed into one and the window
    undercounted. v2: the member is `f"{decision_id}:{now}"` --
    collision-free by construction."""

    def test_same_tick_decisions_are_never_collapsed_into_one(self) -> None:
        store = InMemoryLimitStore(default_limit=2.0)
        key = LimitKey(tenant_id=TENANT, scope=LimitScope.AGENT, subject=AGENT, window=Window(60))
        first = store.try_consume(key, cost=1.0, decision_id="decision-a", now=0.0)
        second = store.try_consume(key, cost=1.0, decision_id="decision-b", now=0.0)
        third = store.try_consume(key, cost=1.0, decision_id="decision-c", now=0.0)
        assert (first.admitted, second.admitted, third.admitted) == (True, True, False)


class TestThreat11UnknownToolExecution:
    """v1: `_TOOL_TYPE_MAP` mapped 13 names; anything else fell through to
    `CUSTOM` -> flat risk 25 -> `AUTO_EXECUTE`. Measured:
    `wipe_production_database` executed. v2: an unregistered tool is denied,
    `TOOL_NOT_GOVERNED`, before any other stage runs. Full suite:
    tests/test_decision_service.py::TestToolRegistryStage."""

    def test_an_unregistered_tool_is_denied_not_auto_executed(self, rt: Runtime) -> None:
        rt.happy_path()
        _load_tool_registry(rt)  # registers a different tool than the one requested below
        outcome = rt.service.decide_and_dispatch_for_tool_call(
            credential(),
            tool_name="wipe_production_database",
            definition_sha256="a" * 64,
            resource=ResourceRef(kind="database", id="prod", tenant_id=TENANT),
            parameters={},
            idempotency_key="idem-threat-11",
        )
        assert outcome.decision.effect is DecisionEffect.DENY
        assert DenialReason.TOOL_NOT_GOVERNED in outcome.decision.reasons
        assert outcome.execution.status is ExecutionStatus.DENIED


class TestThreat12ReplayTriggeredReExecution:
    """v1: `decision_replay.replay_one` called the live `pipeline.process()`
    directly -- `POST /decisions/<id>/replay` could re-execute a wire transfer.
    v2: replay's dispatcher is structurally a `NullDispatcher` that raises if
    ever invoked; the service marks the outcome `REPLAYED`, never dispatched.
    Full suite: tests/test_replay.py."""

    def test_replay_of_an_allow_never_dispatches(self, rt: Runtime) -> None:
        from glassbox.adapters.outbound.replay import NullDispatcher

        rt.happy_path()
        principal = DevIdentityVerifier().verify(credential(), now=0.0)
        object.__setattr__(rt.runtime, "dispatcher", NullDispatcher())
        outcome = rt.service.replay(principal, _reversible_action(idempotency_key="idem-threat-12"))
        assert outcome.execution.status is ExecutionStatus.REPLAYED


class TestThreat13ThresholdTampering:
    """v1: `policy_parameters.set()` had no authorisation check and used
    `INSERT OR REPLACE` (no history). v2: every threshold lives inside a
    `PolicyBundle`, which can only take effect once signed
    (`SignedPolicyBundle`); there is no bare setter for a threshold anywhere on
    the v2 policy path. Full suite: tests/test_policy_bundle.py."""

    def test_a_policy_bundle_has_no_unsigned_mutation_path(self) -> None:
        bundle = PolicyBundle(
            bundle_id="bundle.v1",
            tenant_id=TENANT,
            version=1,
            created_at=0.0,
            rules=(PolicyRule(name="allow-all", effect=RuleEffect.ALLOW, priority=10),),
        )
        # PolicyRule/PolicyBundle are frozen dataclasses: there is no `.set(...)`
        # method, no setter, and no attribute assignment path at all.
        assert not hasattr(bundle, "set")
        with pytest.raises(AttributeError):
            bundle.rules = ()  # type: ignore[misc]


class TestThreat14SelfDos:
    """v1: the batch endpoint submitted up to 500 tasks into the pipeline's own
    shared `ThreadPoolExecutor` with no bound. v2: the dispatcher enforces a
    hard `max_in_flight` bound; the 501st concurrent attempt is refused
    immediately, never queued. Full suite: tests/test_batch_admission_control.py."""

    def test_dispatcher_refuses_rather_than_queues_past_its_bound(self) -> None:
        store = InMemoryEvidenceStore(signer=LocalMacSigner(key_id="k", key=b"\x11" * 32))
        dispatcher = InMemoryDispatcher(max_in_flight=2, receipt_check=store.has_receipt)
        release = threading.Event()
        dispatcher.register("payments.wire_transfer", lambda action: release.wait(timeout=5.0))

        from tests.test_domain import make_intent

        receipts = [store.append_intent(make_intent(decision_id=f"d-{i}")) for i in range(3)]

        def occupy(i: int) -> None:
            dispatcher.dispatch(
                _reversible_action(idempotency_key=f"idem-occupy-{i}"),
                receipts[i],
                timeout_s=5.0,
                now=0.0,
            )

        threads = [threading.Thread(target=occupy, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        try:
            import time as _time

            deadline = _time.monotonic() + 2.0
            refused = False
            while _time.monotonic() < deadline:
                try:
                    dispatcher.dispatch(
                        _reversible_action(idempotency_key="idem-threat-14"),
                        receipts[2],
                        timeout_s=0.01,
                        now=0.0,
                    )
                except DispatchRefusedError:
                    refused = True
                    break
            assert refused, "the third concurrent dispatch should have been refused, not queued"
        finally:
            release.set()
            for t in threads:
                t.join(timeout=5.0)
            dispatcher.shutdown()


class TestThreat15MemoryExhaustion:
    """v1: per-agent dicts in the anomaly detector and velocity breaker grew
    without bound on attacker-controlled agent ids `[measured]`. v2: every such
    store takes a `max_subjects` bound and evicts. Full suite:
    tests/test_memory_adapters.py (`test_tracked_subjects_are_bounded`, both stores)."""

    def test_limit_store_tracked_subjects_are_bounded(self) -> None:
        store = InMemoryLimitStore(default_limit=1000.0, max_subjects=100)
        for index in range(5_000):
            key = LimitKey(
                tenant_id=TENANT,
                scope=LimitScope.AGENT,
                subject=f"agent-{index}",
                window=Window(60),
            )
            store.try_consume(key, cost=1.0, decision_id=f"decision-{index}", now=0.0)
        assert store.tracked_subjects <= 100

    def test_baseline_store_tracked_subjects_are_bounded(self) -> None:
        from glassbox.ports.baseline import BaselineKey, BaselineScope

        store = InMemoryBaselineStore(max_subjects=100)
        for index in range(5_000):
            store.observe(
                BaselineKey(
                    tenant_id=TENANT,
                    scope=BaselineScope.AGENT,
                    subject=f"agent-{index}",
                    metric="exposure_monetary",
                    window=Window(86_400),
                ),
                float(index),
                now=0.0,
            )
        assert store.tracked_subjects <= 100


class TestThreat16EvidenceLoss:
    """v1: `_persist_record` caught every exception and continued -- a side
    effect could occur with no trace of itself. v2: `append_intent` raises
    `EvidenceWriteError`/`SigningUnavailableError`, and `DecisionService` never
    reaches the dispatcher when it does. Full suite:
    tests/test_decision_service.py::TestEvidenceBeforeEffect."""

    def test_an_evidence_write_failure_is_never_swallowed(self, rt: Runtime) -> None:
        rt.happy_path()
        signer = rt.runtime.mac_signer
        assert isinstance(signer, LocalMacSigner)
        signer.set_available(False)
        with pytest.raises(Exception):
            rt.service.decide_and_dispatch(credential(), _reversible_action())
        assert (
            rt.dispatched == []
        ), "the dispatcher must never be reached on an evidence-write failure"


class TestThreat17WalOverwrite:
    """v1: `entry_id` was derived from `MAX(entry_id)+1` in process memory; two
    replicas each produced `entry_id: 0` and one silently overwrote the other
    `[measured]`. v2: `seq` is allocated inside the same critical section as
    the append itself; concurrent writers never collide. Full suite:
    tests/test_memory_adapters.py::test_concurrent_appends_never_collide,
    tests/test_postgres_evidence.py (`SELECT ... FOR UPDATE`)."""

    def test_concurrent_appends_produce_distinct_contiguous_sequences(self) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from tests.test_domain import make_intent

        store = InMemoryEvidenceStore(signer=LocalMacSigner(key_id="k", key=b"\x11" * 32))
        lock = threading.Lock()
        sequences: List[int] = []

        def append(index: int) -> None:
            receipt = store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            with lock:
                sequences.append(receipt.seq)

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(append, range(200)))

        assert sorted(sequences) == list(range(200)), "sequence numbers collided or were skipped"


class TestThreat18SanitizerAvailabilityDos:
    """v1: a regex WAF blocked ordinary business language -- 'Create purchase
    order for Q3 and update the supplier record' among the measured false
    positives. v2: a schema allow-list validates shape, never content; the
    exact measured false positives now pass cleanly. Full suite:
    tests/test_sanitizer_false_positives.py."""

    def test_the_measured_v1_false_positives_are_accepted_by_the_v2_schema(self) -> None:
        from glassbox.domain.catalogue import ParameterField, ParameterType

        definition = ActionDefinition(
            action=ACTION_NAME,
            consequence=ConsequenceClass.REVERSIBLE,
            parameter_schema=(ParameterField(name="memo", type=ParameterType.STRING),),
        )
        for memo in (
            "Create purchase order for Q3 and update the supplier record",
            "Delete stale cache entries after deploy",
            "0xA1B2C3D4E5F6",
            "Grupo \u00c1gua & Caf\u00e9 Ltda",
        ):
            assert definition.validate_parameters({"memo": memo}) == ()


class TestThreat19KeySprawl:
    """v1: a single shared `GLASSBOX_API_KEY` authenticated every caller as
    every tenant. v2: `CredentialType` has no shared-bearer-key member at all
    -- only SPIFFE, OIDC and mTLS, each cryptographically bound to one
    principal."""

    def test_credential_type_has_no_shared_api_key_option(self) -> None:
        assert {member.value for member in CredentialType} == {"spiffe", "oidc", "mtls"}
        with pytest.raises(ValueError):
            CredentialType("api_key")


class TestThreat20FipsHostFailure:
    """v1: `hashlib.md5(...)` without `usedforsecurity=False` crashes outright
    on a FIPS-enforcing host. v2: no rebuilt-layer module calls `hashlib.md5`
    at all -- every digest is SHA-256, checked here statically across every
    file in the rebuilt layers, not just the ones exercised by other tests."""

    def test_no_rebuilt_layer_module_calls_hashlib_md5(self) -> None:
        pattern = re.compile(r"\bhashlib\.md5\s*\(")
        offenders = []
        for root in _REBUILT_LAYER_ROOTS:
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                text = path.read_text(encoding="utf-8")
                if pattern.search(text):
                    offenders.append(str(path.relative_to(_REPO_ROOT)))
        assert not offenders, f"hashlib.md5 used in rebuilt layers: {offenders}"

    def test_the_evidence_mac_is_at_least_256_bits(self) -> None:
        signer = LocalMacSigner(key_id="k")
        assert len(signer.mac(b"payload")) * 8 >= 256


# --------------------------------------------------------------------------- #
# Shared helpers
# --------------------------------------------------------------------------- #


def _reversible_action(*, idempotency_key: str = "idem-adversarial") -> ProposedAction:
    return ProposedAction(
        action=ACTION_NAME,
        resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
        consequence=ConsequenceClass.REVERSIBLE,
        exposure=Exposure(monetary=101.0),
        idempotency_key=idempotency_key,
    )


def _tampered_intent(rt: Runtime, decision_id: str):
    from tests.test_domain import make_intent

    return make_intent(
        decision_id=decision_id,
        action=ProposedAction(
            action=ACTION_NAME,
            resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
            consequence=ConsequenceClass.REVERSIBLE,
            exposure=Exposure(monetary=999_999_999.0),
            idempotency_key="idem-tampered",
        ),
    )
