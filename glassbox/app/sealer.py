"""Segment sealing and retention (GB-007).

Resolves the conflict the review measured directly: v1's ``purge_old_records``
turned ``verify_hash_chain`` from ``true`` to ``false`` permanently, so an
organisation could either honour its retention policy or keep its evidence
verifiable, but not both.

The resolution is to publish a signed, write-once attestation *before* deleting
anything::

    read leaves -> merkle root -> sign root -> write WORM anchor (durable)
                -> record the seal -> only then purge

That ordering is the same invariant as **I1**, applied to retention: no
irreversible act until the evidence of it is durable. :meth:`SegmentSealer.purge`
will not run against a range that is not covered by an anchor it can re-read, and
the store refuses too, so the guarantee does not rest on a single check.

After a purge the segment reports
:attr:`~glassbox.domain.evidence.IntegrityStatus.SEALED_PURGED`, which
:attr:`~glassbox.domain.evidence.IntegrityStatus.is_acceptable` treats as sound.
An auditor holding a purged record and its
:class:`~glassbox.domain.merkle.MerkleProof` can still prove it was part of the
sealed set.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

from glassbox.app.observability import get_logger, log_error
from glassbox.domain.errors import (
    EvidenceIntegrityError,
    EvidenceWriteError,
    SigningUnavailableError,
)
from glassbox.domain.evidence import WormAnchor
from glassbox.domain.merkle import MerkleProof, build_inclusion_proof, merkle_root
from glassbox.ports.keys import MacSigner
from glassbox.ports.retention import EvidenceRetentionStore, SegmentLeaf
from glassbox.ports.worm import WormAnchorStore

__all__ = ["SealResult", "PurgeResult", "SegmentSealer"]

_logger = get_logger("sealer")


@dataclass(frozen=True, slots=True)
class SealResult:
    """The outcome of sealing a segment prefix."""

    anchor: WormAnchor
    locator: str
    leaves_sealed: int

    def as_evidence(self) -> Mapping[str, Any]:
        """Return a canonical summary for the operations log."""
        return {
            **dict(self.anchor.as_evidence()),
            "locator": self.locator,
            "leaves_sealed": self.leaves_sealed,
        }


@dataclass(frozen=True, slots=True)
class PurgeResult:
    """The outcome of purging a sealed prefix."""

    segment_id: str
    purged: int
    anchor_id: str

    def as_evidence(self) -> Mapping[str, Any]:
        """Return a canonical summary for the operations log."""
        return {
            "segment_id": self.segment_id,
            "purged": self.purged,
            "anchor_id": self.anchor_id,
        }


class SegmentSealer:
    """Seals segment prefixes and purges them under retention.

    Args:
        retention: Privileged store operations. Deliberately a different port
            from the one the decision path holds.
        anchors: Write-once anchor storage.
        signer: Signs the anchor. The same key that MACs evidence records, so a
            forged anchor is no easier to produce than a forged record.
    """

    __slots__ = ("_retention", "_anchors", "_signer")

    def __init__(
        self,
        retention: EvidenceRetentionStore,
        anchors: WormAnchorStore,
        signer: MacSigner,
    ) -> None:
        if retention is None or anchors is None or signer is None:
            raise EvidenceWriteError(
                "a sealer requires a retention store, an anchor store and a signer"
            )
        self._retention = retention
        self._anchors = anchors
        self._signer = signer

    # ----------------------------------------------------------------- #
    # Sealing
    # ----------------------------------------------------------------- #

    def seal(self, segment_id: str, *, before_seq: int, now: float) -> SealResult:
        """Seal the records below ``before_seq`` and anchor the result.

        Idempotent: re-sealing the same prefix recomputes the same root and
        re-stores the identical anchor, which write-once storage accepts.

        Args:
            segment_id: Segment to seal.
            before_seq: Seal records whose ``seq`` is strictly below this.
            now: Epoch seconds, from the injected clock.

        Returns:
            The anchor and its storage locator.

        Raises:
            EvidenceWriteError: If there is nothing to seal, or the anchor could
                not be made durable. Nothing is purged in either case.
            SigningUnavailableError: If the anchor cannot be signed. An unsigned
                anchor attests to nothing, so it is not written.
        """
        state = self._retention.segment_state(segment_id)
        if state is None:
            raise EvidenceWriteError("segment not found", segment_id=segment_id)

        leaves = list(self._retention.segment_leaves(segment_id, before_seq=before_seq))
        if not leaves:
            raise EvidenceWriteError(
                "nothing to seal in this range",
                segment_id=segment_id,
                before_seq=before_seq,
            )

        root = merkle_root([leaf.record_hmac for leaf in leaves])
        first_seq = leaves[0].seq
        last_seq = leaves[-1].seq
        self._require_contiguous(segment_id, leaves)

        anchor_id = f"{segment_id}:{first_seq}-{last_seq}"
        unsigned = _unsigned_anchor(
            anchor_id=anchor_id,
            segment_id=segment_id,
            tenant_id=state.tenant_id,
            root=root,
            tree_size=len(leaves),
            first_seq=first_seq,
            last_seq=last_seq,
            sealed_at=now,
        )
        try:
            signature = self._signer.mac(unsigned.signing_payload())
        except SigningUnavailableError:
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "anchor signing failed; refusing to write an unsigned anchor",
                segment_id=segment_id,
                cause=type(exc).__name__,
            ) from exc

        anchor = WormAnchor(
            anchor_id=anchor_id,
            segment_id=segment_id,
            tenant_id=state.tenant_id,
            merkle_root=root,
            tree_size=len(leaves),
            first_seq=first_seq,
            last_seq=last_seq,
            sealed_at=now,
            root_signature=signature,
            signer_key_id=self._signer.key_id,
        )

        locator = self._anchors.put(anchor)
        self._retention.mark_sealed(segment_id, anchor, locator=locator)

        _logger.info(
            "evidence segment sealed",
            extra={
                "segment_id": segment_id,
                "anchor_id": anchor_id,
                "tree_size": len(leaves),
                "locator": locator,
            },
        )
        return SealResult(anchor=anchor, locator=locator, leaves_sealed=len(leaves))

    # ----------------------------------------------------------------- #
    # Purging
    # ----------------------------------------------------------------- #

    def purge(self, segment_id: str, *, before_seq: int) -> PurgeResult:
        """Purge records below ``before_seq``, but only if they are anchored.

        The anchor is re-read from storage rather than trusted from the seal
        call, so a purge cannot run against an anchor that was recorded but never
        actually stored.

        Raises:
            EvidenceWriteError: If the range is not covered by a durable anchor,
                or the deletion fails.
        """
        anchor = self.covering_anchor(segment_id, before_seq=before_seq)
        if anchor is None:
            raise EvidenceWriteError(
                "refusing to purge a range that is not covered by a durable anchor",
                segment_id=segment_id,
                before_seq=before_seq,
            )
        purged = self._retention.purge_before(segment_id, before_seq=before_seq)
        _logger.warning(
            "evidence purged under retention",
            extra={
                "segment_id": segment_id,
                "before_seq": before_seq,
                "purged": purged,
                "anchor_id": anchor.anchor_id,
            },
        )
        return PurgeResult(segment_id=segment_id, purged=purged, anchor_id=anchor.anchor_id)

    def seal_and_purge(self, segment_id: str, *, before_seq: int, now: float) -> PurgeResult:
        """Seal a prefix and then purge it, in that order.

        The ordering is the whole point and is not a caller's responsibility.
        """
        self.seal(segment_id, before_seq=before_seq, now=now)
        return self.purge(segment_id, before_seq=before_seq)

    # ----------------------------------------------------------------- #
    # Verification
    # ----------------------------------------------------------------- #

    def covering_anchor(self, segment_id: str, *, before_seq: int) -> Optional[WormAnchor]:
        """Return a *verified* anchor covering everything below ``before_seq``.

        An anchor whose signature does not check out is treated as absent, so a
        tampered anchor cannot authorise a purge.
        """
        for anchor in self._anchors.list_for_segment(segment_id):
            if anchor.last_seq < before_seq - 1:
                continue
            if anchor.last_seq != before_seq - 1:
                continue
            if self.verify_anchor(anchor):
                return anchor
        return None

    def verify_anchor(self, anchor: WormAnchor) -> bool:
        """Return whether the anchor's signature authenticates its contents.

        Raises:
            EvidenceIntegrityError: If the signing key cannot be resolved. An
                unverifiable anchor is not the same finding as a forged one.
        """
        try:
            return self._signer.verify(
                anchor.signing_payload(),
                anchor.root_signature,
                key_id=anchor.signer_key_id,
            )
        except SigningUnavailableError as exc:
            log_error(_logger, exc, message="anchor signing key unavailable")
            raise EvidenceIntegrityError(
                "anchor cannot be verified; its signing key is unavailable",
                anchor_id=anchor.anchor_id,
                signer_key_id=anchor.signer_key_id,
            ) from exc

    def prove_inclusion(
        self, segment_id: str, *, leaves: Sequence[SegmentLeaf], seq: int
    ) -> Tuple[MerkleProof, bytes]:
        """Build an inclusion proof for one record against its sealed root.

        This is the capability retention would otherwise destroy: given a record
        that has since been purged, plus this proof, an auditor can show it
        belonged to the sealed set.

        Args:
            segment_id: Segment the record belonged to.
            leaves: The sealed leaf sequence, in order.
            seq: Sequence number of the record to prove.

        Returns:
            The proof and the leaf value it proves.

        Raises:
            EvidenceIntegrityError: If ``seq`` is not in the sealed set.
        """
        ordered = sorted(leaves, key=lambda leaf: leaf.seq)
        index = next((i for i, leaf in enumerate(ordered) if leaf.seq == seq), -1)
        if index < 0:
            raise EvidenceIntegrityError(
                "sequence number is not in the sealed set",
                segment_id=segment_id,
                seq=seq,
            )
        proof = build_inclusion_proof([leaf.record_hmac for leaf in ordered], index)
        return proof, ordered[index].record_hmac

    @staticmethod
    def _require_contiguous(segment_id: str, leaves: Sequence[SegmentLeaf]) -> None:
        """Raise if the leaf sequence has a gap.

        A root computed over a set with a hole would verify happily, and would
        silently attest that the missing record never existed.
        """
        expected = leaves[0].seq
        for leaf in leaves:
            if leaf.seq != expected:
                raise EvidenceIntegrityError(
                    "cannot seal a segment with a gap in its sequence",
                    segment_id=segment_id,
                    expected_seq=expected,
                    found_seq=leaf.seq,
                )
            expected += 1


def _unsigned_anchor(
    *,
    anchor_id: str,
    segment_id: str,
    tenant_id: str,
    root: bytes,
    tree_size: int,
    first_seq: int,
    last_seq: int,
    sealed_at: float,
) -> WormAnchor:
    """Build an anchor with a placeholder signature, to compute its payload.

    The signature is not part of what is signed, so a placeholder is sound; it is
    replaced before the anchor is stored.
    """
    return WormAnchor(
        anchor_id=anchor_id,
        segment_id=segment_id,
        tenant_id=tenant_id,
        merkle_root=root,
        tree_size=tree_size,
        first_seq=first_seq,
        last_seq=last_seq,
        sealed_at=sealed_at,
        root_signature=b"\x00" * 32,
        signer_key_id="pending",
    )
