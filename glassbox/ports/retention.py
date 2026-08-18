"""Evidence retention port (GB-007).

Deliberately separate from :class:`~glassbox.ports.evidence.EvidenceStore`.

Sealing and purging are privileged, batch, out-of-band operations run by a
retention job. The decision path needs none of them, and a decision service that
*could* call ``purge_before`` is a decision service that can be made to. v1
scored 1/5 on interface segregation and its ``TamperEvidentAuditLogger`` mixed
appending, verifying and purging into one class -- with ``UPDATE`` and ``DELETE``
statements sitting next to the append path.

Splitting the ports means the two capabilities can be granted to different
database roles and wired into different processes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, Sequence, runtime_checkable

from glassbox.domain.errors import DomainValidationError
from glassbox.domain.evidence import EvidenceSegment, WormAnchor

__all__ = ["SegmentLeaf", "EvidenceRetentionStore"]


@dataclass(frozen=True, slots=True)
class SegmentLeaf:
    """One record's contribution to a segment's Merkle tree.

    Only the sequence number and the MAC are needed. The sealer never reads
    record contents, so a compromised or merely over-curious sealer learns
    nothing about the decisions it is sealing.
    """

    seq: int
    record_hmac: bytes

    def __post_init__(self) -> None:
        if isinstance(self.seq, bool) or not isinstance(self.seq, int):
            raise DomainValidationError("seq must be an integer", field="seq")
        if self.seq < 0:
            raise DomainValidationError("seq must not be negative", field="seq", value=self.seq)
        if not isinstance(self.record_hmac, (bytes, bytearray)) or len(self.record_hmac) < 32:
            raise DomainValidationError(
                "record_hmac must be at least 32 bytes", field="record_hmac"
            )
        object.__setattr__(self, "record_hmac", bytes(self.record_hmac))


@runtime_checkable
class EvidenceRetentionStore(Protocol):
    """Privileged operations used only by the retention job."""

    def segment_state(self, segment_id: str) -> Optional[EvidenceSegment]:
        """Return the segment's current state, or ``None`` if it does not exist."""
        ...

    def segment_leaves(
        self, segment_id: str, *, before_seq: Optional[int] = None
    ) -> Sequence[SegmentLeaf]:
        """Return the segment's live record MACs in sequence order.

        Args:
            segment_id: Segment to read.
            before_seq: When set, return only records below this sequence number,
                which is the prefix a seal covers.

        Raises:
            glassbox.domain.errors.EvidenceIntegrityError: If the leaves cannot
                be read. Sealing an incomplete set would produce a root that no
                subsequent proof matches.
        """
        ...

    def mark_sealed(self, segment_id: str, anchor: WormAnchor, *, locator: str) -> None:
        """Record that a segment prefix has been sealed and anchored.

        Called **after** the anchor is durable. Recording the seal first would
        allow a purge against an anchor that was never written.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the seal cannot be
                recorded.
        """
        ...

    def purge_before(self, segment_id: str, *, before_seq: int) -> int:
        """Delete records below ``before_seq`` and return how many were removed.

        Implementations must refuse to purge a range that is not covered by a
        durable anchor. That check belongs here, at the last possible moment,
        rather than only in the caller.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the range is not
                sealed and anchored, or the deletion fails.
        """
        ...
