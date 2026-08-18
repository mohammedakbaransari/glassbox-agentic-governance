"""Tests for segment sealing, WORM anchoring and retention (GB-007).

Success criterion **S3**: purging within retention keeps the chain verifiable.

The review measured the opposite in v1 -- ``verify_before_purge: true`` followed
by ``verify_after_purging_oldest: false`` -- which put an organisation in the
position of choosing between honouring its retention policy and keeping its
evidence defensible.

Also covered: the Merkle construction (RFC 6962, with the two failure modes that
bite hand-rolled trees), the write-once property of anchor storage, and the
ordering guarantee that nothing is deleted before its attestation is durable.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path
from typing import Any, List, Tuple

import pytest

from glassbox.adapters.outbound.memory.evidence import InMemoryEvidenceStore
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.worm import (
    FilesystemWormAnchorStore,
    InMemoryWormAnchorStore,
    anchor_from_json,
    anchor_to_json,
)
from glassbox.app.sealer import SegmentSealer
from glassbox.domain.errors import (
    DomainValidationError,
    EvidenceIntegrityError,
    EvidenceWriteError,
)
from glassbox.domain.evidence import IntegrityStatus, WormAnchor
from glassbox.domain.merkle import (
    EMPTY_ROOT,
    LEAF_PREFIX,
    NODE_PREFIX,
    MerkleProof,
    build_inclusion_proof,
    leaf_hash,
    merkle_root,
    node_hash,
)
from glassbox.ports.retention import SegmentLeaf
from glassbox.ports.worm import WormAnchorStore
from tests.conformance_evidence import SEGMENT
from tests.test_domain import NOW, make_intent

# --------------------------------------------------------------------------- #
# Merkle primitives
# --------------------------------------------------------------------------- #


class TestMerkleConstruction:
    """RFC 6962, chosen for the two properties hand-rolled trees usually miss."""

    def test_the_empty_tree_has_a_defined_root(self) -> None:
        assert merkle_root([]) == EMPTY_ROOT == hashlib.sha256(b"").digest()

    def test_a_single_leaf_root_is_the_leaf_hash(self) -> None:
        assert merkle_root([b"a"]) == hashlib.sha256(LEAF_PREFIX + b"a").digest()

    def test_leaves_and_nodes_are_domain_separated(self) -> None:
        """Without distinct prefixes an internal node can be passed off as a leaf.

        That is the classic second-preimage attack on Merkle trees: an attacker
        produces a valid inclusion proof for data that was never recorded.
        """
        assert LEAF_PREFIX != NODE_PREFIX
        assert leaf_hash(b"x") != hashlib.sha256(b"x").digest()
        assert node_hash(b"a" * 32, b"b" * 32) != hashlib.sha256(b"a" * 32 + b"b" * 32).digest()

    def test_an_internal_node_cannot_be_presented_as_a_leaf(self) -> None:
        leaves = [b"a" * 32, b"b" * 32]
        root = merkle_root(leaves)
        internal = node_hash(leaf_hash(leaves[0]), leaf_hash(leaves[1]))
        assert root == internal
        assert merkle_root([internal]) != root

    def test_reordering_changes_the_root(self) -> None:
        """Evidence order is part of what is attested to."""
        assert merkle_root([b"a", b"b"]) != merkle_root([b"b", b"a"])

    def test_odd_leaf_counts_do_not_collide(self) -> None:
        """Bitcoin's duplicate-last rule lets two leaf sets share a root (CVE-2012-2459)."""
        three = merkle_root([b"a", b"b", b"c"])
        duplicated = merkle_root([b"a", b"b", b"c", b"c"])
        assert three != duplicated

    def test_the_root_is_deterministic(self) -> None:
        leaves = [f"leaf-{index}".encode() for index in range(37)]
        assert merkle_root(leaves) == merkle_root(leaves)

    def test_non_bytes_leaves_are_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            merkle_root(["not bytes"])  # type: ignore[list-item]


class TestInclusionProofs:
    """The capability retention would otherwise destroy."""

    @pytest.mark.parametrize("size", [1, 2, 3, 4, 5, 7, 8, 9, 16, 17, 64, 100])
    def test_every_leaf_can_be_proven(self, size: int) -> None:
        leaves = [f"leaf-{index}".encode() for index in range(size)]
        root = merkle_root(leaves)
        for index, leaf in enumerate(leaves):
            assert build_inclusion_proof(leaves, index).verify(leaf, root) is True

    @pytest.mark.parametrize("size", [1, 3, 8, 17])
    def test_a_forged_leaf_is_rejected(self, size: int) -> None:
        leaves = [f"leaf-{index}".encode() for index in range(size)]
        root = merkle_root(leaves)
        for index in range(size):
            assert build_inclusion_proof(leaves, index).verify(b"forged", root) is False

    def test_a_proof_does_not_verify_against_another_root(self) -> None:
        leaves = [b"a", b"b", b"c"]
        proof = build_inclusion_proof(leaves, 1)
        assert proof.verify(leaves[1], merkle_root([b"x", b"y", b"z"])) is False

    def test_a_proof_for_the_wrong_index_is_rejected(self) -> None:
        leaves = [b"a", b"b", b"c", b"d"]
        root = merkle_root(leaves)
        assert build_inclusion_proof(leaves, 1).verify(leaves[2], root) is False

    def test_a_tampered_path_is_rejected(self) -> None:
        leaves = [b"a", b"b", b"c", b"d"]
        root = merkle_root(leaves)
        proof = build_inclusion_proof(leaves, 0)
        tampered = MerkleProof(
            index=proof.index,
            tree_size=proof.tree_size,
            path=((b"\x00" * 32, proof.path[0][1]),) + proof.path[1:],
        )
        assert tampered.verify(leaves[0], root) is False

    def test_an_empty_tree_cannot_be_proven(self) -> None:
        with pytest.raises(DomainValidationError):
            build_inclusion_proof([], 0)

    def test_an_out_of_range_index_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            build_inclusion_proof([b"a"], 5)

    def test_a_proof_is_exportable_to_an_auditor(self) -> None:
        leaves = [b"a", b"b", b"c"]
        payload = build_inclusion_proof(leaves, 1).as_evidence()
        assert payload["index"] == 1 and payload["tree_size"] == 3
        assert all(isinstance(step["sibling"], str) for step in payload["path"])


# --------------------------------------------------------------------------- #
# Anchor storage
# --------------------------------------------------------------------------- #


def make_anchor(**overrides: Any) -> WormAnchor:
    """Build a valid anchor."""
    kwargs: dict = {
        "anchor_id": "seg-2026-08:0-4",
        "segment_id": SEGMENT,
        "tenant_id": "acme",
        "merkle_root": b"\x01" * 32,
        "tree_size": 5,
        "first_seq": 0,
        "last_seq": 4,
        "sealed_at": NOW,
        "root_signature": b"\x02" * 32,
        "signer_key_id": "test.key",
    }
    kwargs.update(overrides)
    return WormAnchor(**kwargs)


class WormAnchorStoreConformance:
    """Behaviour every anchor store must exhibit."""

    def test_an_anchor_can_be_stored_and_read_back(self, anchors: Any) -> None:
        anchor = make_anchor()
        locator = anchors.put(anchor)
        assert isinstance(locator, str) and locator
        assert anchors.get(anchor.anchor_id) == anchor

    def test_an_absent_anchor_is_none(self, anchors: Any) -> None:
        assert anchors.get("seg-missing:0-0") is None

    def test_storing_the_identical_anchor_twice_succeeds(self, anchors: Any) -> None:
        """A retried seal is not an attack."""
        anchor = make_anchor()
        first = anchors.put(anchor)
        assert anchors.put(anchor) == first

    def test_a_different_anchor_cannot_replace_an_existing_one(self, anchors: Any) -> None:
        """Storage that permits replacement offers no more than a mutable table."""
        anchors.put(make_anchor())
        with pytest.raises(EvidenceWriteError):
            anchors.put(make_anchor(merkle_root=b"\xff" * 32))

    def test_anchors_are_listed_for_a_segment_oldest_first(self, anchors: Any) -> None:
        anchors.put(make_anchor(anchor_id="s:5-9", first_seq=5, last_seq=9, tree_size=5))
        anchors.put(make_anchor(anchor_id="s:0-4", first_seq=0, last_seq=4, tree_size=5))
        listed = anchors.list_for_segment(SEGMENT)
        assert [anchor.first_seq for anchor in listed] == [0, 5]

    def test_another_segments_anchors_are_not_listed(self, anchors: Any) -> None:
        anchors.put(make_anchor())
        assert anchors.list_for_segment("seg-other") == []

    def test_a_non_anchor_is_refused(self, anchors: Any) -> None:
        with pytest.raises(EvidenceWriteError):
            anchors.put({"anchor_id": "x"})

    def test_the_store_satisfies_the_port(self, anchors: Any) -> None:
        assert isinstance(anchors, WormAnchorStore)


class TestInMemoryAnchors(WormAnchorStoreConformance):
    """The development reference."""

    @pytest.fixture
    def anchors(self) -> InMemoryWormAnchorStore:
        return InMemoryWormAnchorStore()


class TestFilesystemAnchors(WormAnchorStoreConformance):
    """The smallest genuinely write-once implementation."""

    @pytest.fixture
    def anchors(self, tmp_path: Path) -> FilesystemWormAnchorStore:
        return FilesystemWormAnchorStore(tmp_path / "anchors")

    def test_the_file_is_made_read_only(self, tmp_path: Path) -> None:
        store = FilesystemWormAnchorStore(tmp_path / "anchors")
        store.put(make_anchor())
        written = next((tmp_path / "anchors").glob("*.json"))
        assert not stat.S_IMODE(written.stat().st_mode) & stat.S_IWUSR

    def test_an_anchor_id_cannot_escape_the_directory(self, tmp_path: Path) -> None:
        """Anchor ids reach storage as filenames; traversal must be impossible."""
        store = FilesystemWormAnchorStore(tmp_path / "anchors")
        with pytest.raises((EvidenceWriteError, DomainValidationError)):
            store.put(make_anchor(anchor_id="../../escaped"))

    def test_a_corrupt_anchor_is_an_integrity_error_not_absence(self, tmp_path: Path) -> None:
        """Corruption and absence are different findings for an auditor."""
        store = FilesystemWormAnchorStore(tmp_path / "anchors")
        store.put(make_anchor())
        written = next((tmp_path / "anchors").glob("*.json"))
        os.chmod(written, stat.S_IRUSR | stat.S_IWUSR)
        written.write_text("{ not json", encoding="utf-8")
        with pytest.raises(EvidenceIntegrityError):
            store.get("seg-2026-08:0-4")


class TestAnchorSerialisation:
    """Anchors round-trip without losing a bit."""

    def test_round_trip_preserves_the_anchor(self) -> None:
        anchor = make_anchor()
        assert anchor_from_json(anchor_to_json(anchor)) == anchor

    def test_malformed_json_is_an_integrity_error(self) -> None:
        with pytest.raises(EvidenceIntegrityError):
            anchor_from_json("{ not json")


class TestAnchorValidation:
    """An anchor that does not describe a coherent range attests to nothing."""

    def test_the_tree_size_must_match_the_range(self) -> None:
        with pytest.raises(DomainValidationError):
            make_anchor(first_seq=0, last_seq=4, tree_size=99)

    def test_the_range_must_not_be_inverted(self) -> None:
        with pytest.raises(DomainValidationError):
            make_anchor(first_seq=9, last_seq=4, tree_size=5)

    def test_the_signature_covers_the_segment_and_range_not_just_the_root(self) -> None:
        """Signing the root alone would let an anchor be replayed onto another segment."""
        base = make_anchor()
        other_segment = make_anchor(segment_id="seg-other", anchor_id="seg-other:0-4")
        assert base.signing_payload() != other_segment.signing_payload()

        later = make_anchor(sealed_at=NOW + 1.0)
        assert base.signing_payload() != later.signing_payload()

    def test_the_signature_itself_is_not_part_of_what_is_signed(self) -> None:
        base = make_anchor()
        assert (
            base.signing_payload()
            == make_anchor(root_signature=b"\x09" * 32, signer_key_id="other.key").signing_payload()
        )


# --------------------------------------------------------------------------- #
# Sealing and retention
# --------------------------------------------------------------------------- #


def sealed_setup(record_count: int = 5) -> Tuple[InMemoryEvidenceStore, SegmentSealer, Any]:
    """Build a store with records, plus a sealer over in-memory anchors."""
    signer = LocalMacSigner(key_id="seal.key", key=b"\x33" * 32)
    store = InMemoryEvidenceStore(signer=signer)
    for index in range(record_count):
        store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
    anchors = InMemoryWormAnchorStore()
    return store, SegmentSealer(store, anchors, signer), anchors


class TestSealing:
    """Sealing publishes a signed attestation over the sealed set."""

    def test_sealing_produces_a_signed_anchor(self) -> None:
        store, sealer, anchors = sealed_setup()
        result = sealer.seal(SEGMENT, before_seq=3, now=NOW)
        assert result.leaves_sealed == 3
        assert result.anchor.first_seq == 0 and result.anchor.last_seq == 2
        assert sealer.verify_anchor(result.anchor) is True
        assert anchors.get(result.anchor.anchor_id) == result.anchor

    def test_the_root_covers_the_record_macs_in_order(self) -> None:
        store, sealer, _anchors = sealed_setup()
        leaves = store.segment_leaves(SEGMENT, before_seq=3)
        result = sealer.seal(SEGMENT, before_seq=3, now=NOW)
        assert result.anchor.merkle_root == merkle_root([leaf.record_hmac for leaf in leaves])

    def test_sealing_is_idempotent(self) -> None:
        """A retried seal must recompute the same root and be accepted."""
        _store, sealer, _anchors = sealed_setup()
        first = sealer.seal(SEGMENT, before_seq=3, now=NOW)
        second = sealer.seal(SEGMENT, before_seq=3, now=NOW)
        assert first.anchor == second.anchor

    def test_sealing_an_empty_range_is_refused(self) -> None:
        _store, sealer, _anchors = sealed_setup()
        with pytest.raises(EvidenceWriteError):
            sealer.seal(SEGMENT, before_seq=0, now=NOW)

    def test_sealing_an_unknown_segment_is_refused(self) -> None:
        _store, sealer, _anchors = sealed_setup()
        with pytest.raises(EvidenceWriteError):
            sealer.seal("seg-missing", before_seq=3, now=NOW)

    def test_a_tampered_anchor_does_not_verify(self) -> None:
        _store, sealer, _anchors = sealed_setup()
        result = sealer.seal(SEGMENT, before_seq=3, now=NOW)
        forged = WormAnchor(
            anchor_id=result.anchor.anchor_id,
            segment_id=result.anchor.segment_id,
            tenant_id=result.anchor.tenant_id,
            merkle_root=b"\xee" * 32,
            tree_size=result.anchor.tree_size,
            first_seq=result.anchor.first_seq,
            last_seq=result.anchor.last_seq,
            sealed_at=result.anchor.sealed_at,
            root_signature=result.anchor.root_signature,
            signer_key_id=result.anchor.signer_key_id,
        )
        assert sealer.verify_anchor(forged) is False

    def test_a_sealer_requires_all_three_collaborators(self) -> None:
        with pytest.raises(EvidenceWriteError):
            SegmentSealer(None, InMemoryWormAnchorStore(), LocalMacSigner())


class TestRetentionPreservesIntegrity:
    """Success criterion S3."""

    def test_purging_within_retention_keeps_the_chain_verifiable(self) -> None:
        """Regression for the exact v1 measurement.

        v1: ``verify_before_purge: true`` then ``verify_after_purging_oldest: false``.
        """
        store, sealer, _anchors = sealed_setup()
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.INTACT

        sealer.seal_and_purge(SEGMENT, before_seq=2, now=NOW)

        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.SEALED_PURGED
        assert report.is_acceptable is True

    def test_a_purged_record_can_still_be_proven_to_have_existed(self) -> None:
        """The capability retention would otherwise destroy.

        An auditor holding the purged record's MAC plus this proof can show it
        belonged to the sealed set, without the row still existing.
        """
        store, sealer, _anchors = sealed_setup()
        leaves = list(store.segment_leaves(SEGMENT, before_seq=2))
        result = sealer.seal(SEGMENT, before_seq=2, now=NOW)
        proof, leaf = sealer.prove_inclusion(SEGMENT, leaves=leaves, seq=1)

        sealer.purge(SEGMENT, before_seq=2)
        assert store.segment_size(SEGMENT) == 3

        assert proof.verify(leaf, result.anchor.merkle_root) is True

    def test_tampering_after_a_purge_is_still_detected(self) -> None:
        store, sealer, _anchors = sealed_setup()
        sealer.seal_and_purge(SEGMENT, before_seq=2, now=NOW)
        from glassbox.domain.action import Exposure
        from tests.test_domain import make_action

        store.tamper_for_test(
            SEGMENT,
            3,
            make_intent(
                decision_id="decision-0003",
                action=make_action(exposure=Exposure(monetary=1.0)),
            ),
        )
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.BROKEN

    def test_proving_a_sequence_outside_the_sealed_set_is_refused(self) -> None:
        store, sealer, _anchors = sealed_setup()
        leaves = list(store.segment_leaves(SEGMENT, before_seq=2))
        with pytest.raises(EvidenceIntegrityError):
            sealer.prove_inclusion(SEGMENT, leaves=leaves, seq=99)


class TestPurgeOrdering:
    """Nothing is deleted before its attestation is durable."""

    def test_purging_without_a_seal_is_refused_by_the_sealer(self) -> None:
        _store, sealer, _anchors = sealed_setup()
        with pytest.raises(EvidenceWriteError):
            sealer.purge(SEGMENT, before_seq=2)

    def test_purging_without_a_seal_is_refused_by_the_store_too(self) -> None:
        """The check is repeated at the last possible moment, not only in the caller."""
        store, _sealer, _anchors = sealed_setup()
        with pytest.raises(EvidenceWriteError):
            store.purge_before(SEGMENT, before_seq=2)

    def test_a_purge_beyond_the_sealed_range_is_refused(self) -> None:
        """Sealing 0-1 must not authorise deleting record 2."""
        _store, sealer, _anchors = sealed_setup()
        sealer.seal(SEGMENT, before_seq=2, now=NOW)
        with pytest.raises(EvidenceWriteError):
            sealer.purge(SEGMENT, before_seq=4)

    def test_no_records_are_removed_when_the_seal_fails(self) -> None:
        store, sealer, _anchors = sealed_setup()
        with pytest.raises(EvidenceWriteError):
            sealer.seal_and_purge("seg-missing", before_seq=2, now=NOW)
        assert store.segment_size(SEGMENT) == 5

    def test_a_gap_in_the_sequence_cannot_be_sealed(self) -> None:
        """A root over a set with a hole silently attests the missing record never existed."""
        store, sealer, _anchors = sealed_setup()
        leaves = [
            SegmentLeaf(seq=0, record_hmac=b"\x01" * 32),
            SegmentLeaf(seq=2, record_hmac=b"\x02" * 32),
        ]

        class GappedStore:
            def segment_state(self, segment_id: str) -> Any:
                return store.segment_state(segment_id)

            def segment_leaves(self, segment_id: str, *, before_seq: Any = None) -> Any:
                return leaves

            def mark_sealed(self, segment_id: str, anchor: Any, *, locator: str) -> None:
                store.mark_sealed(segment_id, anchor, locator=locator)

            def purge_before(self, segment_id: str, *, before_seq: int) -> int:
                return store.purge_before(segment_id, before_seq=before_seq)

        gapped = SegmentSealer(
            GappedStore(), InMemoryWormAnchorStore(), LocalMacSigner(key_id="k", key=b"\x33" * 32)
        )
        with pytest.raises(EvidenceIntegrityError):
            gapped.seal(SEGMENT, before_seq=3, now=NOW)


class TestRetentionPortShape:
    """Sealing is privileged and deliberately not on the decision path's port."""

    def test_the_evidence_port_offers_no_purge(self) -> None:
        """A decision service that *could* purge is one that can be made to."""
        from glassbox.ports.evidence import EvidenceStore

        surface = {name for name in dir(EvidenceStore) if not name.startswith("_")}
        assert surface == {"append_intent", "append_outcome", "verify"}

    def test_the_memory_store_satisfies_the_retention_port(self) -> None:
        from glassbox.ports.retention import EvidenceRetentionStore

        store, _sealer, _anchors = sealed_setup()
        assert isinstance(store, EvidenceRetentionStore)

    def test_leaves_expose_only_sequence_and_mac(self) -> None:
        """The sealer never reads record contents, so it learns nothing it need not."""
        assert set(SegmentLeaf.__dataclass_fields__) == {"seq", "record_hmac"}

    def test_a_short_mac_cannot_become_a_leaf(self) -> None:
        with pytest.raises(DomainValidationError):
            SegmentLeaf(seq=0, record_hmac=b"\x01" * 16)
