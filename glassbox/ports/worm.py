"""Write-once anchor storage port (GB-007).

The anchor is what survives a retention purge. Once it exists, deleting the rows
it covers no longer destroys the ability to prove what they were -- which is the
conflict v1 could not resolve, where ``purge_old_records`` permanently broke
``verify_hash_chain``.

The contract is therefore **write-once, not just append-only**: an implementation
that lets an anchor be replaced offers no more assurance than the mutable table
it was meant to escape. Adapters are expected to lean on the storage system for
this -- S3 Object Lock in compliance mode, an immutable blob container, a WORM
appliance -- rather than on application logic, for the same reason the evidence
table has a database trigger and not just a code path.
"""

from __future__ import annotations

from typing import Optional, Protocol, Sequence, runtime_checkable

from glassbox.domain.evidence import WormAnchor

__all__ = ["WormAnchorStore"]


@runtime_checkable
class WormAnchorStore(Protocol):
    """Durable, write-once storage for sealed segment anchors."""

    def put(self, anchor: WormAnchor) -> str:
        """Store ``anchor`` and return its storage locator.

        Must be durable before returning: the sealer purges records only after
        this call succeeds, so an anchor that is still buffered is an anchor that
        can be lost together with the rows it was meant to attest to.

        Storing the *identical* anchor twice must succeed idempotently -- a
        retried seal is not an attack. Storing a **different** anchor under an
        existing id must raise.

        Args:
            anchor: The signed attestation.

        Returns:
            A storage locator recorded on the segment.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the anchor could not be
                made durable, or if a *different* anchor already exists under the
                same id. The sealer must not proceed to purge.
        """
        ...

    def get(self, anchor_id: str) -> Optional[WormAnchor]:
        """Return a stored anchor, or ``None`` when absent.

        Raises:
            glassbox.domain.errors.EvidenceIntegrityError: If the anchor exists
                but cannot be read or parsed. Absence and corruption are
                different findings and must not be collapsed.
        """
        ...

    def list_for_segment(self, segment_id: str) -> Sequence[WormAnchor]:
        """Return every anchor covering ``segment_id``, oldest first.

        A segment sealed more than once -- successive retention periods -- has
        several anchors, and an auditor needs all of them to cover the whole
        history.
        """
        ...
