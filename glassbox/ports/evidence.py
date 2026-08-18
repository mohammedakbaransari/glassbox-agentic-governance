"""Evidence store port (GB-002, WS-2).

The most safety-critical contract in the system. Read the
:meth:`EvidenceStore.append_intent` docstring before implementing an adapter.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.evidence import (
    EvidenceReceipt,
    IntegrityReport,
    IntentRecord,
    OutcomeRecord,
)

__all__ = ["EvidenceStore"]


@runtime_checkable
class EvidenceStore(Protocol):
    """Append-only, MAC-chained evidence with durable-before-effect semantics."""

    def append_intent(self, record: IntentRecord) -> EvidenceReceipt:
        """Persist a pre-effect record and return proof that it is durable.

        This method carries invariant **I1** and must satisfy all of the
        following. Each corresponds to a measured v1 defect.

        * **Durable before return.** The transaction must be committed and
          fsynced before the receipt is produced. Returning after an enqueue is
          not acceptable: v1's pipeline marked ``audit_saved=True`` while the
          record sat in an in-memory queue.
        * **Never swallow.** Any failure raises
          :class:`~glassbox.domain.errors.EvidenceWriteError`. v1's
          ``_persist_record`` caught every exception and continued, so evidence
          loss was silent and the side effect still happened.
        * **Sequence allocated in-transaction.** ``seq`` comes from a per-segment
          database sequence inside the same transaction. v1 derived it from
          ``MAX(entry_id)+1`` in process memory, so two replicas both produced
          ``entry_id: 0`` and one silently overwrote the other's decision.
        * **Keyed MAC.** ``record_hmac`` is produced by
          :class:`~glassbox.ports.keys.MacSigner` over
          :meth:`~glassbox.domain.evidence.IntentRecord.chain_payload`. A bare
          digest is forbidden: v1's unkeyed chain re-verified as intact after a
          forged rewrite.
        * **Idempotent.** Re-appending the same ``decision_id`` returns the
          original receipt rather than creating a second row.

        Args:
            record: The complete pre-effect record.

        Returns:
            A receipt proving durability. The dispatcher requires one.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the record could not be
                made durable. The caller **must not** dispatch.
            glassbox.domain.errors.SigningUnavailableError: If the MAC signer is
                unavailable. Degrading to an unkeyed digest is forbidden.
        """
        ...

    def append_outcome(self, receipt: EvidenceReceipt, record: OutcomeRecord) -> None:
        """Record what happened after the intent was made durable.

        Off the critical path: a failure here must be retried and alerted, and it
        never retroactively authorises or forbids the effect that already
        occurred.

        Args:
            receipt: The receipt returned by :meth:`append_intent`.
            record: The terminal outcome.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the write fails after
                the adapter's retry budget is exhausted.
            ValueError: If ``receipt.decision_id`` and ``record.decision_id``
                disagree.
        """
        ...

    def verify(self, segment_id: str, *, now: float) -> IntegrityReport:
        """Verify the MAC chain of one segment.

        Verification must recompute each record's payload from stored fields --
        never from a cached digest -- and must check ``seq`` continuity so that a
        deleted or re-ordered row is detected, not only a mutated one.

        A segment whose records were lawfully purged after sealing must report
        :attr:`~glassbox.domain.evidence.IntegrityStatus.SEALED_PURGED`, not
        ``BROKEN``: v1's ``purge_old_records`` permanently broke verification,
        putting retention and integrity in direct conflict.

        Args:
            segment_id: Segment to verify.
            now: Current time in POSIX epoch seconds.

        Returns:
            A report localising the first failing record when broken.

        Raises:
            glassbox.domain.errors.EvidenceIntegrityError: If verification itself
                could not be performed. A *failed* verification is a returned
                report, not an exception.
        """
        ...
