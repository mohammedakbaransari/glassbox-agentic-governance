"""Merkle tree primitives for segment sealing (GB-007).

Pure, deterministic, and standard. The construction is **RFC 6962** (Certificate
Transparency), chosen deliberately over the more commonly copied Bitcoin variant
for two reasons that matter to an evidence chain.

**Second-preimage resistance.** Leaves are hashed as ``H(0x00 || leaf)`` and
internal nodes as ``H(0x01 || left || right)``. Without those distinct prefixes an
attacker can present an *internal node* as if it were a leaf, and produce a valid
inclusion proof for data that was never recorded.

**No odd-node duplication flaw.** Bitcoin's tree duplicates the last node when a
level has an odd count, which lets two different leaf sets produce the same root
(CVE-2012-2459). RFC 6962 splits at the largest power of two instead, so the root
is an injective function of the leaf sequence.

The tree is what makes retention and integrity compatible. Sealing a segment
publishes a signed root; purging rows afterwards leaves the root and its
inclusion proofs valid, so an auditor holding a purged record can still prove it
was part of the sealed set. v1's ``purge_old_records`` simply broke
``verify_hash_chain`` and offered nothing in its place.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import List, Sequence, Tuple

from glassbox.domain.errors import DomainValidationError

__all__ = [
    "LEAF_PREFIX",
    "NODE_PREFIX",
    "EMPTY_ROOT",
    "leaf_hash",
    "node_hash",
    "merkle_root",
    "MerkleProof",
    "build_inclusion_proof",
]

#: Distinguishes a leaf from an internal node. Omitting this is the classic
#: second-preimage vulnerability in hand-rolled Merkle trees.
LEAF_PREFIX = b"\x00"

#: Distinguishes an internal node from a leaf.
NODE_PREFIX = b"\x01"

#: Root of the empty tree, per RFC 6962: the hash of the empty string.
EMPTY_ROOT = hashlib.sha256(b"").digest()


def leaf_hash(leaf: bytes) -> bytes:
    """Return the hash of a leaf.

    Raises:
        DomainValidationError: If ``leaf`` is not bytes.
    """
    if not isinstance(leaf, (bytes, bytearray)):
        raise DomainValidationError(
            "merkle leaves must be bytes", field="leaf", offending_type=type(leaf).__name__
        )
    return hashlib.sha256(LEAF_PREFIX + bytes(leaf)).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    """Return the hash of an internal node joining two subtree hashes."""
    return hashlib.sha256(NODE_PREFIX + bytes(left) + bytes(right)).digest()


def _largest_power_of_two_below(count: int) -> int:
    """Return the largest power of two strictly less than ``count``.

    The RFC 6962 split point. ``count`` is always at least 2 when this is called.
    """
    return 1 << (count - 1).bit_length() - 1


def _root_of_hashes(hashes: Sequence[bytes]) -> bytes:
    """Compute the root over already-hashed leaves."""
    count = len(hashes)
    if count == 0:
        return EMPTY_ROOT
    if count == 1:
        return hashes[0]
    split = _largest_power_of_two_below(count)
    return node_hash(_root_of_hashes(hashes[:split]), _root_of_hashes(hashes[split:]))


def merkle_root(leaves: Sequence[bytes]) -> bytes:
    """Return the Merkle root over ``leaves``, in order.

    The root is a function of the *sequence*, not the set: re-ordering evidence
    changes the root, which is the point.

    Args:
        leaves: Leaf payloads, normally each record's MAC.

    Returns:
        A 32-byte root. :data:`EMPTY_ROOT` for an empty sequence.
    """
    return _root_of_hashes([leaf_hash(leaf) for leaf in leaves])


@dataclass(frozen=True, slots=True)
class MerkleProof:
    """An inclusion proof for one leaf against a sealed root.

    Holding this plus the record itself lets an auditor prove the record was in
    the sealed set, **after the row has been purged for retention**. That is the
    capability the whole card exists to provide.

    Attributes:
        index: Position of the leaf in the sealed sequence.
        tree_size: Number of leaves the root was computed over.
        path: Sibling hashes from the leaf upwards, with a flag saying whether
            each sibling sits on the left.
    """

    index: int
    tree_size: int
    path: Tuple[Tuple[bytes, bool], ...] = ()

    def __post_init__(self) -> None:
        if isinstance(self.index, bool) or not isinstance(self.index, int):
            raise DomainValidationError(
                "index must be an integer", field="index", offending_type=type(self.index).__name__
            )
        if isinstance(self.tree_size, bool) or not isinstance(self.tree_size, int):
            raise DomainValidationError("tree_size must be an integer", field="tree_size")
        if self.tree_size < 1:
            raise DomainValidationError(
                "tree_size must be positive", field="tree_size", value=self.tree_size
            )
        if not 0 <= self.index < self.tree_size:
            raise DomainValidationError(
                "index is outside the tree",
                field="index",
                index=self.index,
                tree_size=self.tree_size,
            )
        for position, (sibling, _is_left) in enumerate(self.path):
            if not isinstance(sibling, (bytes, bytearray)) or len(sibling) != 32:
                raise DomainValidationError(
                    "proof path entries must be 32-byte hashes",
                    field=f"path[{position}]",
                )

    def verify(self, leaf: bytes, root: bytes) -> bool:
        """Return whether ``leaf`` is at :attr:`index` in the tree with ``root``.

        Recomputes the root from the leaf and the sibling path; there is no
        shortcut that trusts any part of the proof.
        """
        computed = leaf_hash(leaf)
        for sibling, sibling_on_left in self.path:
            computed = (
                node_hash(sibling, computed) if sibling_on_left else node_hash(computed, sibling)
            )
        return computed == bytes(root)

    def as_evidence(self) -> dict:
        """Return a canonical, JSON-safe representation for export to an auditor."""
        return {
            "index": self.index,
            "tree_size": self.tree_size,
            "path": [
                {"sibling": sibling.hex(), "sibling_on_left": sibling_on_left}
                for sibling, sibling_on_left in self.path
            ],
        }


def build_inclusion_proof(leaves: Sequence[bytes], index: int) -> MerkleProof:
    """Build the inclusion proof for ``leaves[index]``.

    Args:
        leaves: The full leaf sequence the root was computed over.
        index: Position of the leaf to prove.

    Returns:
        A proof that verifies against :func:`merkle_root` of the same sequence.

    Raises:
        DomainValidationError: If ``leaves`` is empty or ``index`` is outside it.
    """
    if not leaves:
        raise DomainValidationError("cannot prove inclusion in an empty tree", field="leaves")
    if not 0 <= index < len(leaves):
        raise DomainValidationError(
            "index is outside the leaf sequence",
            field="index",
            index=index,
            leaf_count=len(leaves),
        )

    hashes = [leaf_hash(leaf) for leaf in leaves]
    path: List[Tuple[bytes, bool]] = []

    def descend(subtree: Sequence[bytes], target: int) -> None:
        """Walk down to the target leaf, recording each sibling subtree root."""
        if len(subtree) <= 1:
            return
        split = _largest_power_of_two_below(len(subtree))
        if target < split:
            path.append((_root_of_hashes(subtree[split:]), False))
            descend(subtree[:split], target)
        else:
            path.append((_root_of_hashes(subtree[:split]), True))
            descend(subtree[split:], target - split)

    descend(hashes, index)
    # Recorded top-down; verification walks leaf-upwards.
    path.reverse()
    return MerkleProof(index=index, tree_size=len(leaves), path=tuple(path))
