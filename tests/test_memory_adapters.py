"""Tests for the in-memory reference adapters (GB-003).

These are conformance tests as much as unit tests. Each behaviour asserted here
is one the durable adapters must reproduce -- Postgres evidence (GB-005), KMS
signing (GB-006), Redis limits (GB-011), Redis baselines (GB-022) and the real
dispatcher (GB-033) -- so the suite is written to be reusable against any
implementation of the same port.

Almost every test corresponds to a measured v1 defect. Where it does, the
docstring says so.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List

import pytest

from glassbox.adapters.outbound.memory import (
    AllowListPolicyDecisionPoint,
    DevIdentityVerifier,
    FrozenClock,
    InMemoryBaselineStore,
    InMemoryDispatcher,
    InMemoryEvidenceStore,
    InMemoryLimitStore,
    InMemoryMandateStore,
    LocalMacSigner,
    ReferenceRiskEngine,
    SystemClock,
)
from glassbox.domain.action import ConsequenceClass, Exposure
from glassbox.domain.decision import (
    AuthorizationRequest,
    ExecutionOutcome,
    ExecutionStatus,
    StageOutcome,
    StageStatus,
)
from glassbox.domain.errors import (
    BaselineStoreUnavailable,
    DispatchRefusedError,
    EvidenceWriteError,
    IdentityError,
    LimitStoreUnavailable,
    PolicyBundleUnavailableError,
    RiskModelUnavailableError,
    SigningUnavailableError,
)
from glassbox.domain.evidence import (
    EvidenceReceipt,
    IntegrityStatus,
    OutcomeRecord,
)
from glassbox.domain.identity import CredentialType, RawCredential
from glassbox.domain.limits import LimitKey, LimitScope, Window
from glassbox.domain.risk import RiskFactor, RiskInputs, RiskLevel
from glassbox.ports.baseline import BaselineKey, BaselineScope
from tests.conformance_baseline import BaselineStoreConformance
from tests.conformance_evidence import EvidenceStoreConformance
from tests.conformance_limits import LimitStoreConformance
from tests.test_domain import NOW, make_action, make_intent, make_mandate

# --------------------------------------------------------------------------- #
# Signing
# --------------------------------------------------------------------------- #


class TestLocalMacSigner:
    """Keyed, attributable, constant-time, and never silently unkeyed."""

    def test_mac_is_keyed_not_a_bare_digest(self) -> None:
        """Two independently keyed signers must not agree on a payload.

        Regression: v1's chain was an unkeyed SHA-256, so anyone able to write a
        row could recompute the chain and a forged record re-verified as intact.
        """
        first = LocalMacSigner(key_id="k1")
        second = LocalMacSigner(key_id="k1")
        assert first.mac(b"payload") != second.mac(b"payload")

    def test_mac_is_at_least_256_bits(self) -> None:
        assert len(LocalMacSigner().mac(b"payload")) == 32

    def test_verify_accepts_only_the_authentic_mac(self) -> None:
        signer = LocalMacSigner(key_id="k1")
        mac = signer.mac(b"payload")
        assert signer.verify(b"payload", mac, key_id="k1") is True
        assert signer.verify(b"tampered", mac, key_id="k1") is False

    def test_rotation_preserves_historical_verification(self) -> None:
        signer = LocalMacSigner(key_id="k1")
        old_mac = signer.mac(b"payload")
        signer.rotate("k2")
        assert signer.key_id == "k2"
        assert signer.verify(b"payload", old_mac, key_id="k1") is True

    def test_unknown_key_is_unverifiable_not_intact(self) -> None:
        with pytest.raises(SigningUnavailableError):
            LocalMacSigner(key_id="k1").verify(b"payload", b"\x00" * 32, key_id="missing")

    def test_outage_raises_rather_than_degrading_to_unkeyed(self) -> None:
        signer = LocalMacSigner()
        signer.set_available(False)
        with pytest.raises(SigningUnavailableError):
            signer.mac(b"payload")


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


def evidence_store() -> InMemoryEvidenceStore:
    """Build a store with a deterministic signer."""
    return InMemoryEvidenceStore(signer=LocalMacSigner(key_id="test.key", key=b"\x11" * 32))


class TestEvidenceAppend:
    """Durable-before-return, idempotent, and sequenced under one lock."""

    def test_receipt_proves_the_record_was_stored(self) -> None:
        store = evidence_store()
        record = make_intent()
        receipt = store.append_intent(record)
        assert receipt.decision_id == record.decision_id
        assert receipt.seq == 0
        assert store.has_receipt(receipt) is True

    def test_sequence_numbers_are_contiguous(self) -> None:
        store = evidence_store()
        receipts = [
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            for index in range(5)
        ]
        assert [receipt.seq for receipt in receipts] == [0, 1, 2, 3, 4]

    def test_append_is_idempotent_on_decision_id(self) -> None:
        store = evidence_store()
        record = make_intent()
        assert store.append_intent(record) == store.append_intent(record)
        assert store.segment_size(record.segment_id) == 1

    def test_concurrent_appends_never_collide(self) -> None:
        """Regression: v1 derived entry_id from MAX(entry_id)+1 in process memory.

        Two replicas both produced ``entry_id: 0`` and one silently overwrote the
        other's decision -- ``decisionA_lost_from_wal: true``. Here the sequence
        is allocated inside the same critical section as the append, so 200
        concurrent writers still produce 200 distinct positions.
        """
        store = evidence_store()
        receipts: List[EvidenceReceipt] = []
        lock = threading.Lock()

        def append(index: int) -> None:
            receipt = store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            with lock:
                receipts.append(receipt)

        with ThreadPoolExecutor(max_workers=16) as pool:
            list(pool.map(append, range(200)))

        sequences = sorted(receipt.seq for receipt in receipts)
        assert sequences == list(range(200)), "sequence numbers collided or were skipped"
        assert len({receipt.decision_id for receipt in receipts}) == 200

    def test_signing_outage_prevents_the_write(self) -> None:
        """No evidence write means no dispatch: the caller cannot proceed."""
        signer = LocalMacSigner()
        store = InMemoryEvidenceStore(signer=signer)
        signer.set_available(False)
        with pytest.raises(SigningUnavailableError):
            store.append_intent(make_intent())

    def test_a_non_record_is_refused(self) -> None:
        with pytest.raises(EvidenceWriteError):
            evidence_store().append_intent({"decision_id": "d-1"})  # type: ignore[arg-type]

    def test_outcome_requires_a_matching_receipt(self) -> None:
        store = evidence_store()
        receipt = store.append_intent(make_intent())
        mismatched = OutcomeRecord(
            decision_id="decision-9999",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        with pytest.raises(ValueError):
            store.append_outcome(receipt, mismatched)

    def test_outcome_for_an_unknown_receipt_is_refused(self) -> None:
        store = evidence_store()
        stranger = EvidenceReceipt(
            decision_id="decision-9999",
            segment_id="seg-2026-08",
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="test.key",
            persisted_at=NOW,
        )
        record = OutcomeRecord(
            decision_id="decision-9999",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        with pytest.raises(EvidenceWriteError):
            store.append_outcome(stranger, record)


class TestEvidenceIntegrity:
    """Forgery, deletion and re-ordering are all detected."""

    def test_an_untouched_segment_verifies(self) -> None:
        store = evidence_store()
        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        report = store.verify("seg-2026-08", now=NOW)
        assert report.status is IntegrityStatus.INTACT
        assert report.is_acceptable is True
        assert report.records_checked == 3

    def test_a_forged_record_is_detected(self) -> None:
        """Regression: the measured v1 result was ``verify_after_forgery: true``.

        A record's context was rewritten to ``{"amount": 999999999}``, the chain
        was recomputed, and the tamper detector reported it as intact.
        """
        store = evidence_store()
        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.verify("seg-2026-08", now=NOW).status is IntegrityStatus.INTACT

        store.tamper_for_test(
            "seg-2026-08",
            seq=1,
            replacement=make_intent(
                decision_id="decision-0001",
                action=make_action(exposure=Exposure(monetary=999_999_999.0)),
            ),
        )
        report = store.verify("seg-2026-08", now=NOW)
        assert report.status is IntegrityStatus.BROKEN
        assert report.first_broken_seq == 1
        assert report.is_acceptable is False

    def test_an_unknown_segment_is_unverifiable_not_intact(self) -> None:
        report = evidence_store().verify("seg-does-not-exist", now=NOW)
        assert report.status is IntegrityStatus.UNVERIFIABLE
        assert report.is_acceptable is False

    def test_an_unavailable_key_is_unverifiable_not_intact(self) -> None:
        signer = LocalMacSigner()
        store = InMemoryEvidenceStore(signer=signer)
        store.append_intent(make_intent())
        signer.set_available(False)
        assert store.verify("seg-2026-08", now=NOW).status is IntegrityStatus.UNVERIFIABLE


class TestEvidenceRetention:
    """Lawful retention must not destroy verifiability."""

    def test_purging_after_seal_keeps_the_segment_verifiable(self) -> None:
        """Regression: v1's purge_old_records made verify_hash_chain return False.

        The measured result was ``verify_before_purge: true`` followed by
        ``verify_after_purging_oldest: false`` -- retention and integrity in
        direct, unresolved conflict.
        """
        store = evidence_store()
        for index in range(5):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.verify("seg-2026-08", now=NOW).status is IntegrityStatus.INTACT

        purged = store.seal_and_purge("seg-2026-08", before_seq=2)
        assert purged == 2

        report = store.verify("seg-2026-08", now=NOW)
        assert report.status is IntegrityStatus.SEALED_PURGED
        assert report.is_acceptable is True
        assert "purged under retention" in report.detail

    def test_tampering_after_a_purge_is_still_detected(self) -> None:
        store = evidence_store()
        for index in range(5):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        store.seal_and_purge("seg-2026-08", before_seq=2)
        store.tamper_for_test(
            "seg-2026-08",
            seq=3,
            replacement=make_intent(
                decision_id="decision-0003",
                action=make_action(exposure=Exposure(monetary=1.0)),
            ),
        )
        assert store.verify("seg-2026-08", now=NOW).status is IntegrityStatus.BROKEN

    def test_purging_nothing_is_a_no_op(self) -> None:
        store = evidence_store()
        store.append_intent(make_intent())
        assert store.seal_and_purge("seg-2026-08", before_seq=0) == 0


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


def limit_key(window_seconds: int = 60) -> LimitKey:
    """Build a canonical limit key."""
    return LimitKey(
        tenant_id="acme",
        scope=LimitScope.AGENT,
        subject="agent.treasury-bot",
        window=Window(window_seconds),
        action="payments.wire_transfer",
    )


class TestLimitStore:
    """Atomic, collision-free, cooldown-in-store, and never fail-open."""

    def test_admissions_stop_exactly_at_the_ceiling(self) -> None:
        store = InMemoryLimitStore(default_limit=3.0)
        key = limit_key()
        verdicts = [
            store.try_consume(key, cost=1.0, decision_id=f"decision-{i}", now=NOW + i * 0.001)
            for i in range(5)
        ]
        assert [verdict.admitted for verdict in verdicts] == [True, True, True, False, False]

    def test_concurrent_callers_never_exceed_the_limit(self) -> None:
        """The invariant v1's concurrency tests never asserted.

        ``tests/test_velocity_breaker_invariants.py:159`` launched 500 threads and
        asserted only that no exception was raised; it never checked that
        admissions stayed within ``max_decisions``.
        """
        store = InMemoryLimitStore(default_limit=50.0)
        key = limit_key()
        admitted: List[bool] = []
        lock = threading.Lock()

        def attempt(index: int) -> None:
            verdict = store.try_consume(key, cost=1.0, decision_id=f"decision-{index:04d}", now=NOW)
            with lock:
                admitted.append(verdict.admitted)

        with ThreadPoolExecutor(max_workers=32) as pool:
            list(pool.map(attempt, range(500)))

        assert sum(admitted) == 50, f"admitted {sum(admitted)} of a limit of 50"

    def test_same_tick_decisions_are_counted_separately(self) -> None:
        """Regression: v1's ``ZADD key now now`` collapsed same-tick admissions.

        Using the timestamp as both score and member meant two decisions in one
        clock tick became one member, and the window undercounted.
        """
        store = InMemoryLimitStore(default_limit=2.0)
        key = limit_key()
        first = store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        second = store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW)
        third = store.try_consume(key, cost=1.0, decision_id="decision-c", now=NOW)
        assert (first.admitted, second.admitted, third.admitted) == (True, True, False)

    def test_a_repeated_decision_id_is_the_same_admission(self) -> None:
        store = InMemoryLimitStore(default_limit=1.0)
        key = limit_key()
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted

    def test_cooldown_is_held_by_the_store(self) -> None:
        """Regression: v1 kept ``_tripped`` locally while counting in Redis, so the
        effective cooldown collapsed to the window length."""
        store = InMemoryLimitStore(default_limit=1.0, cooldown_seconds=300.0)
        key = limit_key(window_seconds=10)
        store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        tripped = store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW)
        assert tripped.admitted is False
        assert tripped.cooldown_until == NOW + 300.0

        # Past the window but still inside the cooldown.
        still_tripped = store.try_consume(key, cost=1.0, decision_id="decision-c", now=NOW + 20.0)
        assert still_tripped.admitted is False

        recovered = store.try_consume(key, cost=1.0, decision_id="decision-d", now=NOW + 400.0)
        assert recovered.admitted is True

    def test_the_window_slides(self) -> None:
        store = InMemoryLimitStore(default_limit=1.0, cooldown_seconds=0.0)
        key = limit_key(window_seconds=10)
        assert store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW).admitted
        assert store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW + 20.0).admitted

    def test_release_returns_budget_for_an_abandoned_decision(self) -> None:
        store = InMemoryLimitStore(default_limit=1.0)
        key = limit_key()
        store.try_consume(key, cost=1.0, decision_id="decision-a", now=NOW)
        store.release(key, decision_id="decision-a")
        assert store.try_consume(key, cost=1.0, decision_id="decision-b", now=NOW).admitted

    def test_an_outage_raises_rather_than_admitting_everything(self) -> None:
        """Regression: v1 failed open at velocity_breaker.py:604."""
        store = InMemoryLimitStore()
        store.set_available(False)
        with pytest.raises(LimitStoreUnavailable):
            store.try_consume(limit_key(), cost=1.0, decision_id="decision-a", now=NOW)

    def test_tracked_subjects_are_bounded(self) -> None:
        """Regression: v1's per-agent dicts grew without limit on attacker-controlled ids."""
        store = InMemoryLimitStore(default_limit=1000.0, max_subjects=100)
        for index in range(5_000):
            key = LimitKey(
                tenant_id="acme",
                scope=LimitScope.AGENT,
                subject=f"agent-{index}",
                window=Window(60),
            )
            store.try_consume(key, cost=1.0, decision_id=f"decision-{index}", now=NOW)
        assert store.tracked_subjects <= 100


class TestInMemoryLimitStoreConformance(LimitStoreConformance):
    """The reference adapter is held to the same specification the durable
    Redis adapter (GB-011) is checked against."""

    @pytest.fixture
    def store_factory(self):
        def factory(*, default_limit: float, cooldown_seconds: float) -> InMemoryLimitStore:
            return InMemoryLimitStore(
                default_limit=default_limit, cooldown_seconds=cooldown_seconds
            )

        return factory


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


def baseline_key(subject: str = "agent.treasury-bot") -> BaselineKey:
    """Build a canonical baseline key."""
    return BaselineKey(
        tenant_id="acme",
        scope=BaselineScope.AGENT,
        subject=subject,
        metric="transfer_amount",
        window=Window(86_400),
    )


class TestBaselineStore:
    """Cold start uses a peer prior; detection is never simply skipped."""

    def test_a_brand_new_subject_with_no_prior_is_anomalous(self) -> None:
        """Regression: v1's first 12 calls with amount=1e12 all returned false.

        ``min_samples=10`` meant an attacker only needed a fresh agent id.
        """
        store = InMemoryBaselineStore(min_samples=30)
        verdict = store.evaluate(
            baseline_key(),
            1e12,
            peer_group="treasury-agents",
            threshold=3.0,
            now=NOW,
        )
        assert verdict.anomalous is True
        assert verdict.used_peer_prior is True
        assert verdict.sample_count == 0

    def test_cold_start_falls_back_to_the_peer_group(self) -> None:
        store = InMemoryBaselineStore(min_samples=30)
        peer = baseline_key().peer_group_fallback("treasury-agents")
        for _ in range(50):
            store.observe(peer, 1_000.0, now=NOW)

        normal = store.evaluate(
            baseline_key("agent.brand-new"),
            1_000.0,
            peer_group="treasury-agents",
            threshold=3.0,
            now=NOW,
        )
        assert normal.used_peer_prior is True
        assert normal.anomalous is False

        extreme = store.evaluate(
            baseline_key("agent.brand-new"),
            1e12,
            peer_group="treasury-agents",
            threshold=3.0,
            now=NOW,
        )
        assert extreme.anomalous is True

    def test_an_established_subject_uses_its_own_history(self) -> None:
        store = InMemoryBaselineStore(min_samples=10)
        key = baseline_key()
        for value in range(100, 200):
            store.observe(key, float(value), now=NOW)
        verdict = store.evaluate(key, 150.0, peer_group="treasury-agents", threshold=3.0, now=NOW)
        assert verdict.used_peer_prior is False
        assert verdict.anomalous is False

    def test_a_constant_series_treats_any_change_as_maximally_anomalous(self) -> None:
        store = InMemoryBaselineStore(min_samples=5)
        key = baseline_key()
        for _ in range(20):
            store.observe(key, 100.0, now=NOW)
        verdict = store.evaluate(key, 101.0, peer_group="treasury-agents", threshold=3.0, now=NOW)
        assert verdict.anomalous is True

    def test_an_outage_raises_rather_than_reporting_normal(self) -> None:
        store = InMemoryBaselineStore()
        store.set_available(False)
        with pytest.raises(BaselineStoreUnavailable):
            store.evaluate(baseline_key(), 1.0, peer_group="peers", threshold=3.0, now=NOW)

    def test_tracked_subjects_are_bounded(self) -> None:
        """Regression: 20,000 agents produced 20,000 permanently retained stat objects."""
        store = InMemoryBaselineStore(max_subjects=100)
        for index in range(5_000):
            store.observe(baseline_key(f"agent-{index}"), float(index), now=NOW)
        assert store.tracked_subjects <= 100


class TestInMemoryBaselineStoreConformance(BaselineStoreConformance):
    """The reference adapter is held to the same specification the durable
    Redis adapter (GB-022) is checked against."""

    @pytest.fixture
    def store_factory(self):
        def factory(*, min_samples: int) -> InMemoryBaselineStore:
            return InMemoryBaselineStore(min_samples=min_samples)

        return factory


# --------------------------------------------------------------------------- #
# Mandates
# --------------------------------------------------------------------------- #


class TestMandateStore:
    """Absence is denial, and the tenant is always part of the key."""

    def test_an_unknown_agent_has_no_mandate(self) -> None:
        assert InMemoryMandateStore().get("acme", "agent.unknown", now=NOW) is None

    def test_a_stored_mandate_is_returned(self) -> None:
        store = InMemoryMandateStore()
        store.put(make_mandate())
        assert store.get("acme", "agent.treasury-bot", now=NOW) is not None

    def test_another_tenants_mandate_is_not_visible(self) -> None:
        """Regression: v1's query(tenant_id=None) returned every tenant's rows."""
        store = InMemoryMandateStore()
        store.put(make_mandate())
        assert store.get("evilcorp", "agent.treasury-bot", now=NOW) is None

    def test_revocation_is_immediate(self) -> None:
        store = InMemoryMandateStore()
        store.put(make_mandate())
        assert store.is_revoked("acme", "agent.treasury-bot", now=NOW) is False
        store.revoke("acme", "agent.treasury-bot")
        assert store.is_revoked("acme", "agent.treasury-bot", now=NOW) is True

    def test_an_unknown_agent_counts_as_revoked(self) -> None:
        assert InMemoryMandateStore().is_revoked("acme", "agent.unknown", now=NOW) is True


# --------------------------------------------------------------------------- #
# Identity, policy and risk
# --------------------------------------------------------------------------- #


class TestDevIdentityVerifier:
    """Tenancy comes out of the credential and nowhere else."""

    @staticmethod
    def _credential(material: str = "dev:acme:agent.treasury-bot:instance-01") -> RawCredential:
        return RawCredential(
            credential_type=CredentialType.OIDC, material=material, presented_at=NOW
        )

    def test_tenant_is_derived_from_the_credential(self) -> None:
        principal = DevIdentityVerifier().verify(self._credential(), now=NOW)
        assert principal.tenant_id == "acme"
        assert principal.agent_ref == "agent.treasury-bot"

    def test_a_malformed_credential_is_refused(self) -> None:
        with pytest.raises(IdentityError):
            DevIdentityVerifier().verify(self._credential("not-a-credential"), now=NOW)

    def test_a_non_credential_is_refused(self) -> None:
        with pytest.raises(IdentityError):
            DevIdentityVerifier().verify("dev:acme:a:b", now=NOW)  # type: ignore[arg-type]

    def test_a_matching_assertion_is_accepted(self) -> None:
        verifier = DevIdentityVerifier()
        principal = verifier.verify(self._credential(), now=NOW)
        verifier.assert_matches_assertion(principal, asserted_tenant_id="acme")

    def test_a_spoofed_tenant_header_is_refused(self) -> None:
        """Regression: v1 copied X-Tenant-ID into the request context verbatim."""
        verifier = DevIdentityVerifier()
        principal = verifier.verify(self._credential(), now=NOW)
        with pytest.raises(IdentityError) as excinfo:
            verifier.assert_matches_assertion(principal, asserted_tenant_id="evilcorp")
        assert "evilcorp" in excinfo.value.context["mismatches"]

    def test_a_spoofed_subject_header_is_refused(self) -> None:
        verifier = DevIdentityVerifier()
        principal = verifier.verify(self._credential(), now=NOW)
        with pytest.raises(IdentityError):
            verifier.assert_matches_assertion(principal, asserted_subject="mallory")


class TestAllowListPolicyDecisionPoint:
    """Deny by default, and every allow cites its bundle."""

    @staticmethod
    def _request(action_name: str = "payments.wire_transfer") -> AuthorizationRequest:
        from tests.test_domain import make_principal

        return AuthorizationRequest(
            decision_id="decision-0001",
            principal=make_principal(),
            action=make_action(action=action_name),
            evaluated_at=NOW,
        )

    def test_an_unlisted_action_is_denied(self) -> None:
        decision = AllowListPolicyDecisionPoint().decide(self._request())
        assert decision.is_denied is True
        assert decision.permits_dispatch() is False

    def test_a_listed_action_is_allowed_and_cites_the_bundle(self) -> None:
        pdp = AllowListPolicyDecisionPoint()
        pdp.allow("acme", "payments.wire_transfer")
        decision = pdp.decide(self._request())
        assert decision.permits_dispatch() is True
        assert decision.policy_bundle_sha256 == pdp.active_bundle_digest("acme")

    def test_the_digest_changes_when_the_policy_changes(self) -> None:
        """A decision must be attributable to the exact policy that produced it."""
        pdp = AllowListPolicyDecisionPoint()
        before = pdp.active_bundle_digest("acme")
        pdp.allow("acme", "payments.wire_transfer")
        assert pdp.active_bundle_digest("acme") != before

    def test_another_tenants_allow_list_does_not_apply(self) -> None:
        """Regression: a policy registered on the v1 default pipeline blocked every tenant."""
        pdp = AllowListPolicyDecisionPoint()
        pdp.allow("evilcorp", "payments.wire_transfer")
        assert pdp.decide(self._request()).is_denied is True

    def test_an_unavailable_bundle_raises_rather_than_allowing(self) -> None:
        pdp = AllowListPolicyDecisionPoint()
        pdp.set_available(False)
        with pytest.raises(PolicyBundleUnavailableError):
            pdp.decide(self._request())


class TestReferenceRiskEngine:
    """Deterministic, non-saturating, floored and versioned."""

    @staticmethod
    def _inputs(*factors: RiskFactor, consequence: ConsequenceClass) -> RiskInputs:
        return RiskInputs(
            consequence=consequence,
            exposure=Exposure(monetary=50_000_000.0),
            evaluated_at=NOW,
            factors=factors,
        )

    def test_a_benign_factor_cannot_dilute_a_severe_one(self) -> None:
        """Regression: v1's weighted mean scored $50M irreversible at 27.5 / 'medium'."""
        engine = ReferenceRiskEngine()
        severe = RiskFactor(name="amount", score=90.0, rationale="42x the peer median")
        benign = RiskFactor(name="destination", score=1.0, rationale="known counterparty")
        score = engine.score(
            self._inputs(severe, benign, consequence=ConsequenceClass.IRREVERSIBLE)
        )
        assert score.value >= 90.0
        assert score.level is RiskLevel.CRITICAL

    def test_the_consequence_floor_applies_to_a_quiet_irreversible_action(self) -> None:
        engine = ReferenceRiskEngine()
        quiet = RiskFactor(name="amount", score=1.0, rationale="small")
        score = engine.score(self._inputs(quiet, consequence=ConsequenceClass.IRREVERSIBLE))
        assert score.level is RiskLevel.HIGH
        assert score.floor_applied is True
        assert score.raw_value == 1.0

    def test_scoring_is_deterministic(self) -> None:
        """v1 read datetime.now().hour, so a replay produced a different answer."""
        engine = ReferenceRiskEngine()
        inputs = self._inputs(
            RiskFactor(name="amount", score=40.0, rationale="elevated"),
            consequence=ConsequenceClass.REVERSIBLE,
        )
        assert engine.score(inputs).value == engine.score(inputs).value

    def test_adding_a_factor_never_lowers_the_score(self) -> None:
        engine = ReferenceRiskEngine()
        one = engine.score(
            self._inputs(
                RiskFactor(name="amount", score=60.0, rationale="elevated"),
                consequence=ConsequenceClass.REVERSIBLE,
            )
        )
        two = engine.score(
            self._inputs(
                RiskFactor(name="amount", score=60.0, rationale="elevated"),
                RiskFactor(name="velocity", score=30.0, rationale="unusual"),
                consequence=ConsequenceClass.REVERSIBLE,
            )
        )
        assert two.value >= one.value

    def test_the_score_is_pinned_to_a_model_version(self) -> None:
        engine = ReferenceRiskEngine()
        score = engine.score(self._inputs(consequence=ConsequenceClass.ADVISORY))
        assert score.model_version == engine.model_version

    def test_an_unknown_historical_model_is_refused(self) -> None:
        """Replaying against today's model answers a different question."""
        engine = ReferenceRiskEngine()
        with pytest.raises(RiskModelUnavailableError):
            engine.score_with_model(
                self._inputs(consequence=ConsequenceClass.ADVISORY), "risk-v0.0.1"
            )


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


class TestDispatcher:
    """No effect without a receipt the evidence store actually issued."""

    @staticmethod
    def _wired() -> tuple:
        store = evidence_store()
        dispatcher = InMemoryDispatcher(max_in_flight=4, receipt_check=store.has_receipt)
        dispatcher.register("payments.wire_transfer", lambda action: {"status": "sent"})
        return store, dispatcher

    def test_a_dispatch_backed_by_evidence_succeeds(self) -> None:
        store, dispatcher = self._wired()
        receipt = store.append_intent(make_intent())
        outcome = dispatcher.dispatch(make_action(), receipt, timeout_s=5.0, now=NOW)
        assert outcome.status is ExecutionStatus.EXECUTED
        assert outcome.result_digest is not None

    def test_a_forged_receipt_is_refused(self) -> None:
        """Invariant I1: possessing a receipt-shaped object is not enough."""
        _, dispatcher = self._wired()
        forged = EvidenceReceipt(
            decision_id="decision-0001",
            segment_id="seg-2026-08",
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="test.key",
            persisted_at=NOW,
        )
        with pytest.raises(DispatchRefusedError):
            dispatcher.dispatch(make_action(), forged, timeout_s=5.0, now=NOW)

    def test_a_non_receipt_is_refused(self) -> None:
        _, dispatcher = self._wired()
        with pytest.raises(DispatchRefusedError):
            dispatcher.dispatch(make_action(), None, timeout_s=5.0, now=NOW)  # type: ignore[arg-type]

    def test_an_unregistered_action_is_refused(self) -> None:
        store, dispatcher = self._wired()
        receipt = store.append_intent(make_intent())
        with pytest.raises(DispatchRefusedError):
            dispatcher.dispatch(
                make_action(action="payments.unknown"), receipt, timeout_s=5.0, now=NOW
            )

    def test_a_repeated_idempotency_key_does_not_re_execute(self) -> None:
        store = evidence_store()
        dispatcher = InMemoryDispatcher(max_in_flight=4, receipt_check=store.has_receipt)
        executions: List[int] = []
        dispatcher.register(
            "payments.wire_transfer", lambda action: executions.append(1) or {"ok": True}
        )
        receipt = store.append_intent(make_intent())
        action = make_action()
        dispatcher.dispatch(action, receipt, timeout_s=5.0, now=NOW)
        dispatcher.dispatch(action, receipt, timeout_s=5.0, now=NOW)
        assert len(executions) == 1

    def test_a_failing_handler_records_its_error_class(self) -> None:
        store = evidence_store()
        dispatcher = InMemoryDispatcher(max_in_flight=4, receipt_check=store.has_receipt)

        def explode(action: Any) -> Any:
            raise KeyError("downstream rejected the transfer")

        dispatcher.register("payments.wire_transfer", explode)
        receipt = store.append_intent(make_intent())
        outcome = dispatcher.dispatch(make_action(), receipt, timeout_s=5.0, now=NOW)
        assert outcome.status is ExecutionStatus.FAILED
        assert outcome.error_class == "KeyError"

    def test_a_timeout_is_indeterminate_not_failed(self) -> None:
        """After a timeout the effect may have occurred; 'failed' would be a lie."""
        store = evidence_store()
        dispatcher = InMemoryDispatcher(max_in_flight=2, receipt_check=store.has_receipt)
        started = threading.Event()
        release = threading.Event()

        def slow(action: Any) -> Any:
            started.set()
            release.wait(timeout=5.0)
            return {"ok": True}

        dispatcher.register("payments.wire_transfer", slow)
        receipt = store.append_intent(make_intent())
        try:
            outcome = dispatcher.dispatch(make_action(), receipt, timeout_s=0.05, now=NOW)
            assert outcome.status is ExecutionStatus.INDETERMINATE
            assert outcome.error_class == "DispatchTimeout"
        finally:
            release.set()
            dispatcher.shutdown()

    def test_the_result_payload_is_never_stored_verbatim(self) -> None:
        store, dispatcher = self._wired()
        dispatcher.register("payments.wire_transfer", lambda action: {"account_number": "12345678"})
        receipt = store.append_intent(make_intent())
        outcome = dispatcher.dispatch(make_action(), receipt, timeout_s=5.0, now=NOW)
        assert outcome.result_digest is not None
        assert "12345678" not in outcome.result_digest


# --------------------------------------------------------------------------- #
# Clock
# --------------------------------------------------------------------------- #


class TestClocks:
    """Time is injected, so a replay can pin it."""

    def test_the_system_clock_advances(self) -> None:
        clock = SystemClock()
        assert clock.now() > 1_700_000_000.0

    def test_a_frozen_clock_never_moves_on_its_own(self) -> None:
        clock = FrozenClock(NOW)
        assert clock.now() == clock.now() == NOW
        assert clock.read_count == 2

    def test_a_frozen_clock_can_be_advanced_explicitly(self) -> None:
        clock = FrozenClock(NOW)
        assert clock.advance(60.0) == NOW + 60.0
        assert clock.now() == NOW + 60.0


class TestInMemoryEvidenceConformance(EvidenceStoreConformance):
    """The in-memory reference store must satisfy the shared port specification."""

    @pytest.fixture
    def store(self) -> InMemoryEvidenceStore:
        return InMemoryEvidenceStore(signer=LocalMacSigner(key_id="test.key", key=b"\x11" * 32))
