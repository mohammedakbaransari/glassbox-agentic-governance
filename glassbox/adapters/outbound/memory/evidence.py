"""In-memory evidence store (GB-003, reference implementation for GB-005).

**Development only.** Nothing here survives a restart, so it provides no
assurance. It exists because the semantics it implements are the ones GB-005 must
reproduce against Postgres, and they are far easier to read -- and to test
against -- in eighty lines than in a schema plus a transaction.

Every property below corresponds to a measured v1 defect.

============================  ==================================================
Property                      v1 defect it prevents
============================  ==================================================
Sequence allocated under the  ``_next_entry_id`` came from ``MAX(entry_id)+1`` in
same lock as the append       process memory, so two replicas both produced
                              ``entry_id: 0`` and one silently overwrote the
                              other's decision.
Nothing is swallowed          ``_persist_record`` caught every exception and
                              continued, so evidence loss was invisible while the
                              side effect still happened.
MAC is keyed, and ``seq`` and The chain was an unkeyed SHA-256 over the record
``prev_hash`` are bound in    only, so a forged row re-verified as intact and a
                              deleted row was undetectable.
Purge after seal reports      ``purge_old_records`` permanently broke
``SEALED_PURGED``             ``verify_hash_chain``, putting lawful retention and
                              integrity in direct conflict.
Idempotent on ``decision_id`` A retry created a second row describing the same
                              decision.
============================  ==================================================
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import EvidenceWriteError, SigningUnavailableError
from glassbox.domain.evidence import (
    GENESIS_PREV_HASH,
    EvidenceReceipt,
    EvidenceSegment,
    IntegrityReport,
    IntegrityStatus,
    IntentRecord,
    OutcomeRecord,
    WormAnchor,
)
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.keys import MacSigner
from glassbox.ports.retention import SegmentLeaf

__all__ = ["InMemoryEvidenceStore", "StoredRecord", "build_evidence_store"]


@dataclass(frozen=True, slots=True)
class StoredRecord:
    """One appended intent, with everything needed to re-verify it."""

    record: IntentRecord
    seq: int
    prev_hash: bytes
    record_hmac: bytes
    signer_key_id: str


class InMemoryEvidenceStore:
    """Append-only, MAC-chained evidence held in process memory.

    Args:
        signer: The MAC signer. Required -- there is no unkeyed mode.
    """

    __slots__ = (
        "_signer",
        "_lock",
        "_segments",
        "_receipts",
        "_outcomes",
        "_chain_anchor",
        "_purged",
        "_worm_anchors",
        "_tenants",
        "_opened_at",
    )

    def __init__(self, signer: MacSigner) -> None:
        if signer is None:
            raise EvidenceWriteError("an evidence store requires a MAC signer")
        self._signer = signer
        self._lock = threading.RLock()
        self._segments: Dict[str, List[StoredRecord]] = {}
        self._receipts: Dict[str, EvidenceReceipt] = {}
        self._outcomes: Dict[str, OutcomeRecord] = {}
        # The MAC of the last purged record, used as the chain link for the first
        # surviving one. Distinct from the Merkle root, which attests to the
        # sealed *set* rather than linking the chain.
        self._chain_anchor: Dict[str, bytes] = {}
        self._purged: Dict[str, int] = {}
        self._worm_anchors: Dict[str, Tuple[WormAnchor, str]] = {}
        self._tenants: Dict[str, str] = {}
        self._opened_at: Dict[str, float] = {}

    # ----------------------------------------------------------------- #
    # EvidenceStore
    # ----------------------------------------------------------------- #

    def append_intent(self, record: IntentRecord) -> EvidenceReceipt:
        """Persist a pre-effect record and return proof that it is durable.

        The sequence number, the chain link and the MAC are all produced inside
        one critical section. Splitting them is what let v1's replicas collide.

        Raises:
            EvidenceWriteError: If the record is not an :class:`IntentRecord`, or
                if signing fails. The caller must not dispatch.
        """
        if not isinstance(record, IntentRecord):
            raise EvidenceWriteError(
                "append_intent requires an IntentRecord",
                offending_type=type(record).__name__,
            )

        with self._lock:
            existing = self._receipts.get(record.decision_id)
            if existing is not None:
                return existing

            segment = self._segments.setdefault(record.segment_id, [])
            self._tenants.setdefault(record.segment_id, record.tenant_id)
            self._opened_at.setdefault(record.segment_id, record.created_at)
            seq = self._purged.get(record.segment_id, 0) + len(segment)
            prev_hash = segment[-1].record_hmac if segment else GENESIS_PREV_HASH

            payload = record.chain_payload(seq=seq, prev_hash=prev_hash)
            try:
                mac = self._signer.mac(payload)
            except SigningUnavailableError:
                raise
            except Exception as exc:
                raise EvidenceWriteError(
                    "MAC computation failed; refusing to write unkeyed evidence",
                    decision_id=record.decision_id,
                    cause=type(exc).__name__,
                ) from exc

            key_id = self._signer.key_id
            segment.append(
                StoredRecord(
                    record=record,
                    seq=seq,
                    prev_hash=prev_hash,
                    record_hmac=mac,
                    signer_key_id=key_id,
                )
            )
            receipt = EvidenceReceipt(
                decision_id=record.decision_id,
                segment_id=record.segment_id,
                seq=seq,
                record_hmac=mac,
                signer_key_id=key_id,
                persisted_at=record.created_at,
            )
            self._receipts[record.decision_id] = receipt
            return receipt

    def append_outcome(self, receipt: EvidenceReceipt, record: OutcomeRecord) -> None:
        """Record what happened after the intent was made durable.

        Raises:
            ValueError: If the receipt and the outcome describe different decisions.
            EvidenceWriteError: If the receipt is unknown to this store.
        """
        if receipt.decision_id != record.decision_id:
            raise ValueError(
                "receipt and outcome describe different decisions: "
                f"{receipt.decision_id!r} != {record.decision_id!r}"
            )
        with self._lock:
            if receipt.decision_id not in self._receipts:
                raise EvidenceWriteError(
                    "no intent record exists for this receipt",
                    decision_id=receipt.decision_id,
                )
            self._outcomes[record.decision_id] = record

    def verify(self, segment_id: str, *, now: float) -> IntegrityReport:
        """Verify the MAC chain of one segment.

        Recomputes each payload from the stored fields rather than trusting a
        cached digest, and checks ``seq`` continuity, so deletion and re-ordering
        are detected as well as mutation.
        """
        with self._lock:
            segment = self._segments.get(segment_id)
            if segment is None:
                return IntegrityReport(
                    segment_id=segment_id,
                    status=IntegrityStatus.UNVERIFIABLE,
                    records_checked=0,
                    verified_at=now,
                    detail="segment not found",
                )

            purged = self._purged.get(segment_id, 0)
            expected_seq = purged
            expected_prev = (
                self._chain_anchor.get(segment_id, GENESIS_PREV_HASH)
                if purged
                else GENESIS_PREV_HASH
            )

            for stored in segment:
                if stored.seq != expected_seq:
                    return IntegrityReport(
                        segment_id=segment_id,
                        status=IntegrityStatus.BROKEN,
                        records_checked=expected_seq - purged,
                        verified_at=now,
                        first_broken_seq=stored.seq,
                        detail=f"sequence discontinuity: expected {expected_seq}",
                    )
                if stored.prev_hash != expected_prev:
                    return IntegrityReport(
                        segment_id=segment_id,
                        status=IntegrityStatus.BROKEN,
                        records_checked=expected_seq - purged,
                        verified_at=now,
                        first_broken_seq=stored.seq,
                        detail="chain link does not match the previous record",
                    )
                payload = stored.record.chain_payload(seq=stored.seq, prev_hash=stored.prev_hash)
                try:
                    authentic = self._signer.verify(
                        payload, stored.record_hmac, key_id=stored.signer_key_id
                    )
                except SigningUnavailableError:
                    return IntegrityReport(
                        segment_id=segment_id,
                        status=IntegrityStatus.UNVERIFIABLE,
                        records_checked=expected_seq - purged,
                        verified_at=now,
                        detail=f"signing key {stored.signer_key_id!r} is unavailable",
                    )
                if not authentic:
                    return IntegrityReport(
                        segment_id=segment_id,
                        status=IntegrityStatus.BROKEN,
                        records_checked=expected_seq - purged,
                        verified_at=now,
                        first_broken_seq=stored.seq,
                        detail="record MAC does not authenticate the stored fields",
                    )
                expected_prev = stored.record_hmac
                expected_seq += 1

            status = (
                IntegrityStatus.SEALED_PURGED
                if purged and segment_id in self._chain_anchor
                else IntegrityStatus.INTACT
            )
            return IntegrityReport(
                segment_id=segment_id,
                status=status,
                records_checked=len(segment),
                verified_at=now,
                detail=(
                    f"{purged} record(s) purged under retention; sealed root retained"
                    if status is IntegrityStatus.SEALED_PURGED
                    else ""
                ),
            )

    # ----------------------------------------------------------------- #
    # Retention (reference behaviour for GB-007)
    # ----------------------------------------------------------------- #

    def seal_and_purge(self, segment_id: str, *, before_seq: int) -> int:
        """Seal the segment prefix and purge it, keeping verification possible.

        The MAC of the last purged record is retained as the sealed anchor, so
        the surviving records still chain to something verifiable. This is the
        behaviour v1's ``purge_old_records`` lacked.

        Returns:
            The number of records purged.
        """
        with self._lock:
            segment = self._segments.get(segment_id, [])
            keep_from = 0
            anchor: Optional[bytes] = None
            for index, stored in enumerate(segment):
                if stored.seq >= before_seq:
                    keep_from = index
                    break
                anchor = stored.record_hmac
                keep_from = index + 1
            if anchor is None:
                return 0
            purged_count = keep_from
            for stored in segment[:keep_from]:
                self._receipts.pop(stored.record.decision_id, None)
                self._outcomes.pop(stored.record.decision_id, None)
            self._segments[segment_id] = segment[keep_from:]
            self._chain_anchor[segment_id] = anchor
            self._purged[segment_id] = self._purged.get(segment_id, 0) + purged_count
            return purged_count

    # ----------------------------------------------------------------- #
    # EvidenceRetentionStore (GB-007)
    # ----------------------------------------------------------------- #

    def segment_state(self, segment_id: str) -> Optional[EvidenceSegment]:
        """Return the segment's current state, or ``None`` if it does not exist."""
        with self._lock:
            if segment_id not in self._segments:
                return None
            purged = self._purged.get(segment_id, 0)
            anchor_entry = self._worm_anchors.get(segment_id)
            anchor = anchor_entry[0] if anchor_entry else None
            return EvidenceSegment(
                segment_id=segment_id,
                tenant_id=self._tenants.get(segment_id, "unknown"),
                opened_at=self._opened_at.get(segment_id, 0.0) or 1.0,
                first_seq=purged,
                sealed_at=anchor.sealed_at if anchor else None,
                last_seq=anchor.last_seq if anchor else None,
                merkle_root=anchor.merkle_root if anchor else None,
                seal_signature=anchor.root_signature if anchor else None,
                worm_anchor_id=anchor.anchor_id if anchor else None,
            )

    def segment_leaves(
        self, segment_id: str, *, before_seq: Optional[int] = None
    ) -> Sequence[SegmentLeaf]:
        """Return the segment's live record MACs in sequence order."""
        with self._lock:
            records = list(self._segments.get(segment_id, ()))
        return [
            SegmentLeaf(seq=stored.seq, record_hmac=stored.record_hmac)
            for stored in sorted(records, key=lambda stored: stored.seq)
            if before_seq is None or stored.seq < before_seq
        ]

    def mark_sealed(self, segment_id: str, anchor: WormAnchor, *, locator: str) -> None:
        """Record that a segment prefix has been sealed and anchored."""
        with self._lock:
            if segment_id not in self._segments:
                raise EvidenceWriteError("segment not found", segment_id=segment_id)
            self._worm_anchors[segment_id] = (anchor, locator)

    def purge_before(self, segment_id: str, *, before_seq: int) -> int:
        """Delete records below ``before_seq``, refusing an unanchored range.

        The check is repeated here rather than trusted from the sealer: this is
        the last point at which an unattested deletion can be stopped.
        """
        with self._lock:
            entry = self._worm_anchors.get(segment_id)
            if entry is None or entry[0].last_seq != before_seq - 1:
                raise EvidenceWriteError(
                    "refusing to purge a range that is not covered by a durable anchor",
                    segment_id=segment_id,
                    before_seq=before_seq,
                )
        return self.seal_and_purge(segment_id, before_seq=before_seq)

    def anchor_for(self, segment_id: str) -> Optional[WormAnchor]:
        """Return the recorded anchor for a segment, if any."""
        with self._lock:
            entry = self._worm_anchors.get(segment_id)
            return entry[0] if entry else None

    # ----------------------------------------------------------------- #
    # Inspection
    # ----------------------------------------------------------------- #

    def has_receipt(self, receipt: EvidenceReceipt) -> bool:
        """Return whether this store issued ``receipt``.

        Wired into the dispatcher so that invariant I1 is checked against real
        stored state rather than against the shape of the argument.
        """
        with self._lock:
            issued = self._receipts.get(receipt.decision_id)
            return issued is not None and issued == receipt

    def outcome_for(self, decision_id: str) -> Optional[OutcomeRecord]:
        """Return the recorded outcome, or ``None`` if none was written."""
        with self._lock:
            return self._outcomes.get(decision_id)

    def segment_size(self, segment_id: str) -> int:
        """Return the number of live records in a segment."""
        with self._lock:
            return len(self._segments.get(segment_id, ()))

    def tamper_for_test(self, segment_id: str, seq: int, replacement: IntentRecord) -> None:
        """Overwrite a stored record without updating its MAC.

        Test-only. Present so that the forgery scenario the review reproduced can
        be exercised as a permanent regression test.
        """
        with self._lock:
            segment = self._segments[segment_id]
            for index, stored in enumerate(segment):
                if stored.seq == seq:
                    segment[index] = StoredRecord(
                        record=replacement,
                        seq=stored.seq,
                        prev_hash=stored.prev_hash,
                        record_hmac=stored.record_hmac,
                        signer_key_id=stored.signer_key_id,
                    )
                    return
            raise KeyError(f"no record at seq {seq} in segment {segment_id!r}")


def build_evidence_store(config: GlassBoxConfig) -> EvidenceStore:
    """Factory used by the adapter set.

    Builds its own signer so the factory signature stays uniform; the composed
    runtime's :attr:`~glassbox.app.composition.GovernanceRuntime.mac_signer` is
    the same class, wired separately.
    """
    from glassbox.adapters.outbound.memory.signing import build_mac_signer

    return InMemoryEvidenceStore(signer=build_mac_signer(config))
