"""Shared ``EvidenceStore`` conformance suite (GB-005).

One behavioural specification, run against **every** implementation of the port.
This is the direct answer to the review's Liskov finding: v1's local anomaly
store used a sliding window while its Redis store used exponential forgetting,
so the same input produced different answers depending on where the code ran, and
no test could have noticed because each was tested separately.

The file is deliberately **not** named ``test_*.py``, so pytest does not collect
the abstract class. Each adapter's own module subclasses
:class:`EvidenceStoreConformance` and supplies a store::

    class TestInMemoryConformance(EvidenceStoreConformance):
        @pytest.fixture
        def store(self):
            return InMemoryEvidenceStore(signer=LocalMacSigner())

Every test here corresponds to a measured v1 defect or to a non-negotiable
invariant, and the docstrings say which.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, List, Optional, Protocol

import pytest

from glassbox.domain.action import Exposure
from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
from glassbox.domain.errors import EvidenceWriteError, SigningUnavailableError
from glassbox.domain.evidence import (
    EvidenceReceipt,
    IntegrityStatus,
    IntentRecord,
    OutcomeRecord,
)
from tests.test_domain import NOW, make_action, make_intent

SEGMENT = "seg-2026-08"


class SupportsTamper(Protocol):
    """Optional store capability used by the forgery scenarios."""

    def tamper_for_test(self, segment_id: str, seq: int, replacement: IntentRecord) -> None:
        """Overwrite a stored record without updating its MAC."""
        ...


class EvidenceStoreConformance:
    """Behaviour every ``EvidenceStore`` must exhibit.

    Subclasses provide a ``store`` fixture. Capabilities that not every backend
    exposes -- forgery injection, retention purge, signer outage -- are probed with
    ``hasattr`` and skipped rather than assumed, so a partial implementation is
    still meaningfully covered.
    """

    # ----------------------------------------------------------------- #
    # Hooks
    # ----------------------------------------------------------------- #

    @staticmethod
    def _signer_of(store: Any) -> Optional[Any]:
        """Return the store's signer if it can be reached, else ``None``."""
        return getattr(store, "_signer", None)

    @staticmethod
    def _require(store: Any, capability: str) -> Any:
        """Return a store capability, skipping the test when it is absent."""
        hook = getattr(store, capability, None)
        if hook is None:
            pytest.skip(f"{type(store).__name__} does not implement {capability}")
        return hook

    # ----------------------------------------------------------------- #
    # Append: durability, sequencing, idempotency
    # ----------------------------------------------------------------- #

    def test_receipt_proves_the_record_is_stored(self, store: Any) -> None:
        """Invariant I1: possession of a receipt means the write committed."""
        record = make_intent()
        receipt = store.append_intent(record)
        assert isinstance(receipt, EvidenceReceipt)
        assert receipt.decision_id == record.decision_id
        assert receipt.segment_id == record.segment_id
        assert receipt.seq == 0
        assert len(receipt.record_hmac) >= 32

    def test_sequence_numbers_are_contiguous(self, store: Any) -> None:
        receipts = [
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            for index in range(5)
        ]
        assert [receipt.seq for receipt in receipts] == [0, 1, 2, 3, 4]

    def test_append_is_idempotent_on_decision_id(self, store: Any) -> None:
        """A retry must not create a second row describing the same decision."""
        record = make_intent()
        first = store.append_intent(record)
        second = store.append_intent(record)
        assert first.seq == second.seq
        assert first.record_hmac == second.record_hmac
        assert store.verify(SEGMENT, now=NOW).records_checked == 1

    def test_concurrent_appends_never_collide(self, store: Any) -> None:
        """Regression: v1 derived entry_id from MAX(entry_id)+1 in process memory.

        Two replicas each produced ``entry_id: 0`` and one silently overwrote the
        other -- the measured result was ``decisionA_lost_from_wal: true``.
        """
        receipts: List[EvidenceReceipt] = []
        lock = threading.Lock()

        def append(index: int) -> None:
            receipt = store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            with lock:
                receipts.append(receipt)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(append, range(60)))

        assert sorted(receipt.seq for receipt in receipts) == list(range(60))
        assert len({receipt.decision_id for receipt in receipts}) == 60

    def test_a_non_record_is_refused(self, store: Any) -> None:
        with pytest.raises(EvidenceWriteError):
            store.append_intent({"decision_id": "decision-0001"})

    def test_signing_outage_prevents_the_write(self, store: Any) -> None:
        """No evidence means no dispatch; unkeyed evidence is not a fallback."""
        signer = self._signer_of(store)
        if signer is None or not hasattr(signer, "set_available"):
            pytest.skip("store does not expose a controllable signer")
        signer.set_available(False)
        try:
            with pytest.raises(SigningUnavailableError):
                store.append_intent(make_intent())
        finally:
            signer.set_available(True)

    # ----------------------------------------------------------------- #
    # Outcome
    # ----------------------------------------------------------------- #

    def test_outcome_is_recorded_against_its_intent(self, store: Any) -> None:
        receipt = store.append_intent(make_intent())
        store.append_outcome(
            receipt,
            OutcomeRecord(
                decision_id=receipt.decision_id,
                outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
            ),
        )

    def test_outcome_requires_a_matching_decision(self, store: Any) -> None:
        receipt = store.append_intent(make_intent())
        mismatched = OutcomeRecord(
            decision_id="decision-9999",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        with pytest.raises(ValueError):
            store.append_outcome(receipt, mismatched)

    def test_outcome_for_an_unknown_receipt_is_refused(self, store: Any) -> None:
        stranger = EvidenceReceipt(
            decision_id="decision-9999",
            segment_id=SEGMENT,
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="unknown.key",
            persisted_at=NOW,
        )
        record = OutcomeRecord(
            decision_id="decision-9999",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        with pytest.raises(EvidenceWriteError):
            store.append_outcome(stranger, record)

    # ----------------------------------------------------------------- #
    # Integrity
    # ----------------------------------------------------------------- #

    def test_an_untouched_segment_verifies(self, store: Any) -> None:
        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.INTACT
        assert report.is_acceptable is True
        assert report.records_checked == 3

    def test_an_empty_store_reports_unverifiable_for_an_unknown_segment(self, store: Any) -> None:
        report = store.verify("seg-does-not-exist", now=NOW)
        assert report.status is IntegrityStatus.UNVERIFIABLE
        assert report.is_acceptable is False

    def test_a_forged_record_is_detected(self, store: Any) -> None:
        """Regression: the measured v1 result was ``verify_after_forgery: true``.

        A record's context was rewritten to ``{"amount": 999999999}``, the unkeyed
        chain was recomputed, and the tamper detector reported it as intact.
        """
        tamper = self._require(store, "tamper_for_test")
        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.INTACT

        tamper(
            SEGMENT,
            1,
            make_intent(
                decision_id="decision-0001",
                action=make_action(exposure=Exposure(monetary=999_999_999.0)),
            ),
        )
        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.BROKEN
        assert report.first_broken_seq == 1
        assert report.is_acceptable is False

    def test_an_unavailable_key_is_unverifiable_not_intact(self, store: Any) -> None:
        signer = self._signer_of(store)
        if signer is None or not hasattr(signer, "set_available"):
            pytest.skip("store does not expose a controllable signer")
        store.append_intent(make_intent())
        signer.set_available(False)
        try:
            assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.UNVERIFIABLE
        finally:
            signer.set_available(True)

    # ----------------------------------------------------------------- #
    # Retention
    # ----------------------------------------------------------------- #

    def test_purging_after_seal_keeps_the_segment_verifiable(self, store: Any) -> None:
        """Regression: v1's purge_old_records permanently broke verification.

        The measured sequence was ``verify_before_purge: true`` then
        ``verify_after_purging_oldest: false`` -- lawful retention and integrity in
        direct conflict.
        """
        purge = self._require(store, "seal_and_purge")
        for index in range(5):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.INTACT

        assert purge(SEGMENT, before_seq=2) == 2

        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.SEALED_PURGED
        assert report.is_acceptable is True

    def test_tampering_after_a_purge_is_still_detected(self, store: Any) -> None:
        purge = self._require(store, "seal_and_purge")
        tamper = self._require(store, "tamper_for_test")
        for index in range(5):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        purge(SEGMENT, before_seq=2)
        tamper(
            SEGMENT,
            3,
            make_intent(
                decision_id="decision-0003",
                action=make_action(exposure=Exposure(monetary=1.0)),
            ),
        )
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.BROKEN
