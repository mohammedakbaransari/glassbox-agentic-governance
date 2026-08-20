"""Automated evidence retention (GB-007, Workstream E).

The plan's exit criterion is that scheduled retention "executes without manual
intervention" -- :class:`~glassbox.app.sealer.SegmentSealer` proves the
seal-before-purge ordering is safe, but nothing decides *when* to call it. This
module is that decision: given a segment's current state, seal it once it is
old enough, and purge a sealed prefix once its grace period has elapsed. It
never invents segment ids itself; the caller supplies which segments exist
(commonly today's and yesterday's few active ones, per the deterministic
per-tenant-per-day naming :func:`~glassbox.app.decision_service._segment_id_for`
uses), so this stays a pure policy over an ``EvidenceRetentionStore`` --
:class:`~glassbox.ports.retention.EvidenceRetentionStore` gains no new required
methods, and every existing conforming adapter works with it unchanged.

Only the port-conformant retention path (``segment_state`` / ``segment_leaves``
/ ``mark_sealed`` / ``purge_before``, driven through ``SegmentSealer``) is
automated here -- the bespoke ``seal_and_purge`` reference method some stores
also expose is a separate, manually-invoked mechanism and is left alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, List, Optional

from glassbox.app.observability import get_logger, log_error
from glassbox.app.sealer import SegmentSealer
from glassbox.domain.errors import EvidenceWriteError, SigningUnavailableError
from glassbox.ports.clock import Clock
from glassbox.ports.retention import EvidenceRetentionStore

__all__ = ["RetentionOutcome", "RetentionAction", "RetentionScheduler"]

_logger = get_logger("retention_scheduler")


class RetentionAction(Enum):
    """What the scheduler did with one segment on one pass."""

    SEALED = "sealed"
    PURGED = "purged"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RetentionOutcome:
    """The result of considering one segment during a scheduler pass."""

    segment_id: str
    action: RetentionAction
    detail: str = ""

    def as_evidence(self) -> dict:
        """Return a canonical representation for the operations log."""
        return {"segment_id": self.segment_id, "action": self.action.value, "detail": self.detail}


class RetentionScheduler:
    """Seals aging segments and purges sealed prefixes, on a policy the caller drives.

    Args:
        retention: Read access to segment state; the same store wired into
            ``sealer``.
        sealer: Performs the actual seal-then-purge, with its own ordering and
            anchoring guarantees. This class only decides *when*.
        clock: The only source of "now" (invariant I6).
        seal_after_seconds: A segment becomes eligible to seal once this long
            has elapsed since it was opened.
        purge_grace_seconds: A sealed segment becomes eligible to purge once
            this long has elapsed since it was sealed. Keeping a grace period
            between sealing and purging leaves a window in which a seal can be
            audited against its still-live source rows before they are gone.
    """

    __slots__ = ("_retention", "_sealer", "_clock", "_seal_after_seconds", "_purge_grace_seconds")

    def __init__(
        self,
        *,
        retention: EvidenceRetentionStore,
        sealer: SegmentSealer,
        clock: Clock,
        seal_after_seconds: float,
        purge_grace_seconds: float,
    ) -> None:
        if retention is None or sealer is None or clock is None:
            raise EvidenceWriteError(
                "a retention scheduler requires a retention store, a sealer and a clock"
            )
        if seal_after_seconds < 0 or purge_grace_seconds < 0:
            raise EvidenceWriteError(
                "seal_after_seconds and purge_grace_seconds must not be negative"
            )
        self._retention = retention
        self._sealer = sealer
        self._clock = clock
        self._seal_after_seconds = seal_after_seconds
        self._purge_grace_seconds = purge_grace_seconds

    def run_once(self, segment_ids: Iterable[str]) -> List[RetentionOutcome]:
        """Consider every given segment exactly once and act on eligible ones.

        Never raises for a single segment's failure: one segment's sealer or
        store error is recorded as :attr:`RetentionAction.FAILED` and the pass
        continues, so one bad segment cannot stall retention for every other
        tenant's evidence.
        """
        now = self._clock.now()
        outcomes: List[RetentionOutcome] = []
        for segment_id in segment_ids:
            outcomes.append(self._consider(segment_id, now=now))
        return outcomes

    def _consider(self, segment_id: str, *, now: float) -> RetentionOutcome:
        try:
            state = self._retention.segment_state(segment_id)
        except Exception as exc:  # noqa: BLE001 - reported per-segment, not raised
            log_error(_logger, exc, message="could not read segment state")
            return RetentionOutcome(segment_id, RetentionAction.FAILED, str(exc))

        if state is None:
            return RetentionOutcome(segment_id, RetentionAction.SKIPPED, "segment not found")

        if state.sealed_at is None:
            return self._maybe_seal(segment_id, now=now, opened_at=state.opened_at)
        return self._maybe_purge(
            segment_id, now=now, sealed_at=state.sealed_at, sealed_last_seq=state.last_seq,
            live_first_seq=state.first_seq,
        )

    def _maybe_seal(self, segment_id: str, *, now: float, opened_at: float) -> RetentionOutcome:
        if now - opened_at < self._seal_after_seconds:
            return RetentionOutcome(segment_id, RetentionAction.SKIPPED, "not old enough to seal")

        try:
            leaves = self._retention.segment_leaves(segment_id)
        except Exception as exc:  # noqa: BLE001 - reported per-segment, not raised
            log_error(_logger, exc, message="could not read segment leaves")
            return RetentionOutcome(segment_id, RetentionAction.FAILED, str(exc))
        if not leaves:
            return RetentionOutcome(segment_id, RetentionAction.SKIPPED, "nothing to seal yet")

        before_seq = leaves[-1].seq + 1
        try:
            result = self._sealer.seal(segment_id, before_seq=before_seq, now=now)
        except (EvidenceWriteError, SigningUnavailableError) as exc:
            log_error(_logger, exc, message="scheduled sealing failed")
            return RetentionOutcome(segment_id, RetentionAction.FAILED, str(exc))
        return RetentionOutcome(
            segment_id, RetentionAction.SEALED, f"sealed {result.leaves_sealed} record(s)"
        )

    def _maybe_purge(
        self,
        segment_id: str,
        *,
        now: float,
        sealed_at: float,
        sealed_last_seq: Optional[int],
        live_first_seq: int,
    ) -> RetentionOutcome:
        if now - sealed_at < self._purge_grace_seconds:
            return RetentionOutcome(
                segment_id, RetentionAction.SKIPPED, "within purge grace period"
            )
        if sealed_last_seq is None:
            return RetentionOutcome(segment_id, RetentionAction.SKIPPED, "no sealed boundary")

        before_seq = sealed_last_seq + 1
        if live_first_seq >= before_seq:
            return RetentionOutcome(segment_id, RetentionAction.SKIPPED, "already purged")

        try:
            result = self._sealer.purge(segment_id, before_seq=before_seq)
        except EvidenceWriteError as exc:
            log_error(_logger, exc, message="scheduled purge failed")
            return RetentionOutcome(segment_id, RetentionAction.FAILED, str(exc))
        return RetentionOutcome(
            segment_id, RetentionAction.PURGED, f"purged {result.purged} record(s)"
        )
