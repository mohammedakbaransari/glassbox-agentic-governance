"""Evidence records, receipts and integrity reports (GB-002, WS-2 foundation).

This is the module that fixes fundamental problems **F2** (effect precedes
evidence) and **F3** (evidence is forgeable and retention-fragile).

Design decisions worth stating explicitly:

* **A receipt proves durability.** An :class:`EvidenceReceipt` is only ever
  returned by ``EvidenceStore.append_intent`` after a committed, fsynced write.
  The dispatcher requires one. There is no "pending" receipt, because a pending
  receipt would let a caller dispatch on an evidence write that had not landed --
  precisely what v1 did when it marked the WAL ``audit_saved=True`` while the
  audit record was still sitting in an in-memory queue.

* **The chain is keyed.** :attr:`IntentRecord.record_hmac` is a KMS HMAC, not a
  bare digest. :meth:`IntentRecord.chain_payload` produces the exact canonical
  bytes that are MAC-ed, so the adapter has no freedom to serialise differently
  and the verification path can reproduce them independently. v1's unkeyed
  SHA-256 chain re-verified as intact after a forged rewrite.

* **Retention and integrity are decoupled.** Records live inside a
  :class:`EvidenceSegment`; sealing a segment publishes a signed Merkle root to
  WORM storage. Purging rows within a sealed segment is therefore
  :attr:`IntegrityStatus.SEALED_PURGED` -- still provable -- rather than v1's
  outcome, where a lawful purge permanently broke chain verification.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.action import ProposedAction
from glassbox.domain.decision import (
    AuthorizationDecision,
    ExecutionOutcome,
    StageOutcome,
)
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.identity import VerifiedPrincipal
from glassbox.domain.limits import LimitVerdict
from glassbox.domain.risk import RiskScore
from glassbox.domain.serialization import (
    canonical_bytes,
    freeze_mapping,
    require_identifier,
    require_sha256_hex,
    require_timestamp,
)

__all__ = [
    "GENESIS_PREV_HASH",
    "IntegrityStatus",
    "ModelProvenance",
    "IntentRecord",
    "OutcomeRecord",
    "EvidenceReceipt",
    "EvidenceSegment",
    "IntegrityReport",
]

#: Chain seed for the first record of a segment: 32 zero bytes.
GENESIS_PREV_HASH: bytes = b"\x00" * 32

_MIN_MAC_LENGTH = 32


def _require_mac(value: Any, *, field_name: str) -> bytes:
    """Validate a message authentication code of at least 256 bits."""
    if not isinstance(value, (bytes, bytearray)):
        raise DomainValidationError(
            "expected raw MAC bytes", field=field_name, offending_type=type(value).__name__
        )
    if len(value) < _MIN_MAC_LENGTH:
        raise DomainValidationError(
            "MAC is shorter than 256 bits", field=field_name, length=len(value)
        )
    return bytes(value)


class IntegrityStatus(Enum):
    """Outcome of verifying an evidence segment."""

    #: Every record's MAC and chain link verified.
    INTACT = "intact"
    #: A record was altered, removed, or re-ordered.
    BROKEN = "broken"
    #: Records were lawfully purged, but the sealed Merkle root still verifies.
    SEALED_PURGED = "sealed_purged"
    #: Imported v1 history, or a segment whose signing key is unavailable.
    UNVERIFIABLE = "unverifiable"

    @property
    def is_acceptable(self) -> bool:
        """Whether an auditor may rely on this segment."""
        return self in (IntegrityStatus.INTACT, IntegrityStatus.SEALED_PURGED)


@dataclass(frozen=True, slots=True)
class ModelProvenance:
    """Which model, prompt and context produced the agent's proposal.

    Entirely absent from v1's ``AuditRecord``, which made it impossible to answer
    an auditor asking "which model version decided this, and on what context?".

    Attributes:
        model_id: Provider model identifier.
        model_version: Pinned model version or snapshot.
        prompt_sha256: Digest of the rendered prompt; never the prompt itself.
        context_digest: Digest of the retrieved context set.
        tools_invoked: Tool names invoked while forming the proposal.
        data_sources: Identifiers of the data sources consulted.
    """

    model_id: Optional[str] = None
    model_version: Optional[str] = None
    prompt_sha256: Optional[str] = None
    context_digest: Optional[str] = None
    tools_invoked: Tuple[str, ...] = ()
    data_sources: Tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.model_id is not None:
            object.__setattr__(
                self, "model_id", require_identifier(self.model_id, field="model_id")
            )
        if self.model_version is not None:
            object.__setattr__(
                self, "model_version", require_identifier(self.model_version, field="model_version")
            )
        if self.prompt_sha256 is not None:
            object.__setattr__(
                self,
                "prompt_sha256",
                require_sha256_hex(self.prompt_sha256, field="prompt_sha256"),
            )
        if self.context_digest is not None:
            object.__setattr__(
                self,
                "context_digest",
                require_sha256_hex(self.context_digest, field="context_digest"),
            )
        if not isinstance(self.tools_invoked, tuple):
            object.__setattr__(self, "tools_invoked", tuple(self.tools_invoked or ()))
        for tool in self.tools_invoked:
            require_identifier(tool, field="tools_invoked")
        if not isinstance(self.data_sources, tuple):
            object.__setattr__(self, "data_sources", tuple(self.data_sources or ()))
        for source in self.data_sources:
            require_identifier(source, field="data_sources")

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical agentic-provenance payload."""
        return {
            "model_id": self.model_id,
            "model_version": self.model_version,
            "prompt_sha256": self.prompt_sha256,
            "context_digest": self.context_digest,
            "tools_invoked": list(self.tools_invoked),
            "data_sources": list(self.data_sources),
        }


@dataclass(frozen=True, slots=True)
class IntentRecord:
    """The complete, pre-effect record of a governed decision.

    This is written *before* any side effect. Everything an auditor, a regulator
    or a replay needs to reconstruct the decision is here: who acted, under whose
    authority, on what resource, under which policy bundle digest and risk model
    version, with which limit verdict, and which controls did not run.

    Attributes:
        decision_id: Correlation id, unique across the system.
        segment_id: Evidence segment this record belongs to.
        tenant_id: Owning tenant. Derived from the principal, never a header.
        created_at: Epoch seconds at which the record was minted.
        principal: The verified acting identity.
        action: The server-derived action.
        decision: The authorization outcome.
        risk: The risk score, pinned to a model version.
        limits: Limit verdicts consulted, if any.
        stages: Outcome of every governance stage, including skips.
        provenance: Model, prompt and context provenance.
        trace_id: Distributed trace correlation id.
        causation_id: Id of the decision that caused this one, if any.
        attributes: Additional server-derived attributes.
    """

    decision_id: str
    segment_id: str
    tenant_id: str
    created_at: float
    principal: VerifiedPrincipal
    action: ProposedAction
    decision: AuthorizationDecision
    risk: RiskScore
    trace_id: str
    limits: Tuple[LimitVerdict, ...] = ()
    stages: Tuple[StageOutcome, ...] = ()
    provenance: ModelProvenance = field(default_factory=ModelProvenance)
    causation_id: Optional[str] = None
    attributes: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", require_identifier(self.decision_id, field="decision_id")
        )
        object.__setattr__(
            self, "segment_id", require_identifier(self.segment_id, field="segment_id")
        )
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(
            self, "created_at", require_timestamp(self.created_at, field="created_at")
        )
        object.__setattr__(self, "trace_id", require_identifier(self.trace_id, field="trace_id"))

        for name, expected in (
            ("principal", VerifiedPrincipal),
            ("action", ProposedAction),
            ("decision", AuthorizationDecision),
            ("risk", RiskScore),
            ("provenance", ModelProvenance),
        ):
            value = getattr(self, name)
            if not isinstance(value, expected):
                raise DomainValidationError(
                    f"{name} must be a {expected.__name__}",
                    field=name,
                    offending_type=type(value).__name__,
                )

        if not isinstance(self.limits, tuple):
            object.__setattr__(self, "limits", tuple(self.limits or ()))
        for index, verdict in enumerate(self.limits):
            if not isinstance(verdict, LimitVerdict):
                raise DomainValidationError(
                    "limits must contain LimitVerdict instances",
                    field=f"limits[{index}]",
                    offending_type=type(verdict).__name__,
                )
        if not isinstance(self.stages, tuple):
            object.__setattr__(self, "stages", tuple(self.stages or ()))
        for index, stage in enumerate(self.stages):
            if not isinstance(stage, StageOutcome):
                raise DomainValidationError(
                    "stages must contain StageOutcome instances",
                    field=f"stages[{index}]",
                    offending_type=type(stage).__name__,
                )
        if self.causation_id is not None:
            object.__setattr__(
                self, "causation_id", require_identifier(self.causation_id, field="causation_id")
            )
        if isinstance(self.attributes, Mapping):
            object.__setattr__(
                self, "attributes", freeze_mapping(self.attributes, field="attributes")
            )
        elif not isinstance(self.attributes, tuple):
            raise DomainValidationError(
                "attributes must be a mapping or a tuple of pairs",
                field="attributes",
                offending_type=type(self.attributes).__name__,
            )

        # Tenancy must agree across every carrier. A mismatch here would produce
        # an evidence row filed under one tenant describing another's action.
        if self.principal.tenant_id != self.tenant_id:
            raise DomainValidationError(
                "record tenant does not match the principal's tenant",
                field="tenant_id",
                record_tenant=self.tenant_id,
                principal_tenant=self.principal.tenant_id,
            )
        if self.action.tenant_id != self.tenant_id:
            raise DomainValidationError(
                "record tenant does not match the resource's tenant",
                field="tenant_id",
                record_tenant=self.tenant_id,
                resource_tenant=self.action.tenant_id,
            )

    @property
    def skipped_stages(self) -> Tuple[StageOutcome, ...]:
        """Stages that did not execute (invariant I9)."""
        return tuple(stage for stage in self.stages if stage.is_missing_control)

    @property
    def requires_prior_evidence(self) -> bool:
        """Whether this record must be durable before any effect (invariant I1)."""
        return self.action.requires_prior_evidence

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical column payload for ``evidence_intent``."""
        return {
            "decision_id": self.decision_id,
            "segment_id": self.segment_id,
            "tenant_id": self.tenant_id,
            "created_at": self.created_at,
            "identity": dict(self.principal.as_evidence()),
            "action": dict(self.action.as_evidence()),
            "policy_decision": dict(self.decision.as_evidence()),
            "risk": dict(self.risk.as_evidence()),
            "limits_verdict": [verdict.as_evidence() for verdict in self.limits],
            "stages": [stage.as_evidence() for stage in self.stages],
            "skipped_stages": [stage.as_evidence() for stage in self.skipped_stages],
            "provenance": dict(self.provenance.as_evidence()),
            "trace_id": self.trace_id,
            "causation_id": self.causation_id,
            "attributes": {key: value for key, value in self.attributes},
        }

    def chain_payload(self, *, seq: int, prev_hash: bytes) -> bytes:
        """Return the exact bytes to be MAC-ed for this record's chain link.

        Binding ``seq`` and ``prev_hash`` into the payload is what makes removal
        and re-ordering detectable, not just field mutation.

        Args:
            seq: Position of this record within its segment.
            prev_hash: MAC of the previous record, or :data:`GENESIS_PREV_HASH`.

        Returns:
            Canonical UTF-8 bytes.

        Raises:
            DomainValidationError: If ``seq`` is negative or ``prev_hash`` is not
                exactly 32 bytes.
        """
        if isinstance(seq, bool) or not isinstance(seq, int):
            raise DomainValidationError(
                "seq must be an integer", field="seq", offending_type=type(seq).__name__
            )
        if seq < 0:
            raise DomainValidationError("seq must not be negative", field="seq", value=seq)
        if not isinstance(prev_hash, (bytes, bytearray)):
            raise DomainValidationError(
                "prev_hash must be bytes",
                field="prev_hash",
                offending_type=type(prev_hash).__name__,
            )
        if len(prev_hash) != len(GENESIS_PREV_HASH):
            raise DomainValidationError(
                "prev_hash must be exactly 32 bytes",
                field="prev_hash",
                length=len(prev_hash),
            )
        return canonical_bytes(
            {
                "seq": seq,
                "prev_hash": bytes(prev_hash).hex(),
                "record": dict(self.as_evidence()),
            }
        )


@dataclass(frozen=True, slots=True)
class OutcomeRecord:
    """What happened after the intent was made durable.

    Written separately and later, keyed by ``decision_id``. Splitting intent from
    outcome is what allows the intent write to be on the critical path while the
    outcome write is not.
    """

    decision_id: str
    outcome: ExecutionOutcome

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", require_identifier(self.decision_id, field="decision_id")
        )
        if not isinstance(self.outcome, ExecutionOutcome):
            raise DomainValidationError(
                "outcome must be an ExecutionOutcome",
                field="outcome",
                offending_type=type(self.outcome).__name__,
            )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``evidence_outcome`` payload."""
        return {"decision_id": self.decision_id, **dict(self.outcome.as_evidence())}


@dataclass(frozen=True, slots=True)
class EvidenceReceipt:
    """Proof that an intent record is durable.

    Possession of a receipt is the dispatcher's precondition. The type therefore
    has no "unpersisted" variant: a store either returns one, or raises
    :class:`~glassbox.domain.errors.EvidenceWriteError`.

    Attributes:
        decision_id: The decision this receipt covers.
        segment_id: Segment the record was appended to.
        seq: Position within the segment, allocated by the store transaction.
        record_hmac: KMS MAC over :meth:`IntentRecord.chain_payload`.
        signer_key_id: Identifier of the key that produced the MAC.
        persisted_at: Epoch seconds at which the write committed.
    """

    decision_id: str
    segment_id: str
    seq: int
    record_hmac: bytes
    signer_key_id: str
    persisted_at: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", require_identifier(self.decision_id, field="decision_id")
        )
        object.__setattr__(
            self, "segment_id", require_identifier(self.segment_id, field="segment_id")
        )
        if isinstance(self.seq, bool) or not isinstance(self.seq, int):
            raise DomainValidationError(
                "seq must be an integer", field="seq", offending_type=type(self.seq).__name__
            )
        if self.seq < 0:
            raise DomainValidationError("seq must not be negative", field="seq", value=self.seq)
        object.__setattr__(
            self, "record_hmac", _require_mac(self.record_hmac, field_name="record_hmac")
        )
        object.__setattr__(
            self, "signer_key_id", require_identifier(self.signer_key_id, field="signer_key_id")
        )
        object.__setattr__(
            self, "persisted_at", require_timestamp(self.persisted_at, field="persisted_at")
        )

    @property
    def is_genesis(self) -> bool:
        """Whether this is the first record in its segment."""
        return self.seq == 0

    def as_evidence(self) -> Mapping[str, Any]:
        """Return a canonical, JSON-safe representation of the receipt."""
        return {
            "decision_id": self.decision_id,
            "segment_id": self.segment_id,
            "seq": self.seq,
            "record_hmac": self.record_hmac.hex(),
            "signer_key_id": self.signer_key_id,
            "persisted_at": self.persisted_at,
        }

    def __repr__(self) -> str:
        return (
            f"EvidenceReceipt(decision_id={self.decision_id!r}, segment_id={self.segment_id!r}, "
            f"seq={self.seq!r}, record_hmac=<{len(self.record_hmac)} bytes>, "
            f"signer_key_id={self.signer_key_id!r}, persisted_at={self.persisted_at!r})"
        )


@dataclass(frozen=True, slots=True)
class EvidenceSegment:
    """A bounded run of evidence records that can be sealed independently.

    Sealing publishes a signed Merkle root to WORM storage. Once sealed, records
    inside the segment may be purged for retention without destroying the proof
    that the segment was complete and unaltered at seal time -- the conflict that
    v1's ``purge_old_records`` could not resolve.
    """

    segment_id: str
    tenant_id: str
    opened_at: float
    first_seq: int = 0
    sealed_at: Optional[float] = None
    last_seq: Optional[int] = None
    merkle_root: Optional[bytes] = None
    seal_signature: Optional[bytes] = None
    worm_anchor_id: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "segment_id", require_identifier(self.segment_id, field="segment_id")
        )
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(self, "opened_at", require_timestamp(self.opened_at, field="opened_at"))
        if isinstance(self.first_seq, bool) or not isinstance(self.first_seq, int):
            raise DomainValidationError(
                "first_seq must be an integer",
                field="first_seq",
                offending_type=type(self.first_seq).__name__,
            )
        if self.first_seq < 0:
            raise DomainValidationError(
                "first_seq must not be negative", field="first_seq", value=self.first_seq
            )

        sealed_fields = (self.sealed_at, self.last_seq, self.merkle_root, self.seal_signature)
        if any(value is not None for value in sealed_fields) and not all(
            value is not None for value in sealed_fields
        ):
            raise DomainValidationError(
                "a sealed segment requires sealed_at, last_seq, merkle_root and seal_signature",
                field="sealed_at",
            )

        if self.sealed_at is not None:
            object.__setattr__(
                self, "sealed_at", require_timestamp(self.sealed_at, field="sealed_at")
            )
            if self.sealed_at < self.opened_at:
                raise DomainValidationError(
                    "sealed_at must not precede opened_at",
                    field="sealed_at",
                    opened_at=self.opened_at,
                    sealed_at=self.sealed_at,
                )
            assert self.last_seq is not None
            # Equality (`first_seq == last_seq + 1`) is the valid "fully purged"
            # case -- every sealed record has since been purged and none remain
            # live -- and matches the Postgres schema's own
            # `CHECK (last_seq >= first_seq - 1)` constraint exactly; only a
            # first_seq that has run *past* that point is a real defect.
            if self.last_seq < self.first_seq - 1:
                raise DomainValidationError(
                    "last_seq must not precede first_seq - 1",
                    field="last_seq",
                    first_seq=self.first_seq,
                    last_seq=self.last_seq,
                )
            object.__setattr__(
                self, "merkle_root", _require_mac(self.merkle_root, field_name="merkle_root")
            )
            if not isinstance(self.seal_signature, (bytes, bytearray)):
                raise DomainValidationError(
                    "seal_signature must be bytes",
                    field="seal_signature",
                    offending_type=type(self.seal_signature).__name__,
                )
            object.__setattr__(self, "seal_signature", bytes(self.seal_signature))
        if self.worm_anchor_id is not None:
            object.__setattr__(
                self,
                "worm_anchor_id",
                require_identifier(self.worm_anchor_id, field="worm_anchor_id"),
            )

    @property
    def is_sealed(self) -> bool:
        """Whether the segment has been sealed and can be purged safely."""
        return self.sealed_at is not None

    @property
    def is_anchored(self) -> bool:
        """Whether the sealed root has been written to WORM storage."""
        return self.is_sealed and self.worm_anchor_id is not None

    @property
    def record_count(self) -> Optional[int]:
        """Number of records covered by the seal, or ``None`` if open."""
        if self.last_seq is None:
            return None
        return self.last_seq - self.first_seq + 1

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``evidence_segment`` payload."""
        return {
            "segment_id": self.segment_id,
            "tenant_id": self.tenant_id,
            "opened_at": self.opened_at,
            "sealed_at": self.sealed_at,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "merkle_root": self.merkle_root.hex() if self.merkle_root else None,
            "worm_anchor_id": self.worm_anchor_id,
            "record_count": self.record_count,
        }


@dataclass(frozen=True, slots=True)
class WormAnchor:
    """A signed, write-once attestation that a segment prefix existed.

    Written to object-lock storage **before** any record is purged. Once it
    exists, deleting the rows it covers no longer destroys the ability to prove
    what they were: an auditor holding a purged record plus a
    :class:`~glassbox.domain.merkle.MerkleProof` can still show it belonged to
    this sealed set.

    The signature covers the segment, the sequence range and the seal time as
    well as the root. Signing the root alone would let a valid anchor be replayed
    onto a different segment or a different period.

    Attributes:
        anchor_id: Stable identifier, used as the storage key.
        segment_id: Segment the seal covers.
        tenant_id: Owning tenant.
        merkle_root: Root over the sealed records' MACs, in sequence order.
        tree_size: Number of records the root covers.
        first_seq: First sequence number covered.
        last_seq: Last sequence number covered.
        sealed_at: Epoch seconds at which the seal was taken.
        root_signature: MAC over :meth:`signing_payload`.
        signer_key_id: Key that produced the signature.
    """

    anchor_id: str
    segment_id: str
    tenant_id: str
    merkle_root: bytes
    tree_size: int
    first_seq: int
    last_seq: int
    sealed_at: float
    root_signature: bytes
    signer_key_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "anchor_id", require_identifier(self.anchor_id, field="anchor_id"))
        object.__setattr__(
            self, "segment_id", require_identifier(self.segment_id, field="segment_id")
        )
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(
            self, "merkle_root", _require_mac(self.merkle_root, field_name="merkle_root")
        )
        object.__setattr__(
            self, "root_signature", _require_mac(self.root_signature, field_name="root_signature")
        )
        object.__setattr__(
            self, "signer_key_id", require_identifier(self.signer_key_id, field="signer_key_id")
        )
        object.__setattr__(self, "sealed_at", require_timestamp(self.sealed_at, field="sealed_at"))
        for name in ("tree_size", "first_seq", "last_seq"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise DomainValidationError(f"{name} must be an integer", field=name)
            if value < 0:
                raise DomainValidationError(f"{name} must not be negative", field=name, value=value)
        if self.last_seq < self.first_seq:
            raise DomainValidationError(
                "last_seq must not precede first_seq",
                field="last_seq",
                first_seq=self.first_seq,
                last_seq=self.last_seq,
            )
        if self.tree_size != self.last_seq - self.first_seq + 1:
            raise DomainValidationError(
                "tree_size must match the covered sequence range",
                field="tree_size",
                tree_size=self.tree_size,
                first_seq=self.first_seq,
                last_seq=self.last_seq,
            )

    def signing_payload(self) -> bytes:
        """Return the canonical bytes the signature is computed over."""
        return canonical_bytes(
            {
                "anchor_id": self.anchor_id,
                "segment_id": self.segment_id,
                "tenant_id": self.tenant_id,
                "merkle_root": self.merkle_root.hex(),
                "tree_size": self.tree_size,
                "first_seq": self.first_seq,
                "last_seq": self.last_seq,
                "sealed_at": self.sealed_at,
            }
        )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return a canonical, JSON-safe representation."""
        return {
            "anchor_id": self.anchor_id,
            "segment_id": self.segment_id,
            "tenant_id": self.tenant_id,
            "merkle_root": self.merkle_root.hex(),
            "tree_size": self.tree_size,
            "first_seq": self.first_seq,
            "last_seq": self.last_seq,
            "sealed_at": self.sealed_at,
            "root_signature": self.root_signature.hex(),
            "signer_key_id": self.signer_key_id,
        }


@dataclass(frozen=True, slots=True)
class IntegrityReport:
    """The result of verifying one evidence segment.

    ``first_broken_seq`` localises tampering rather than merely reporting that
    something, somewhere, is wrong.
    """

    segment_id: str
    status: IntegrityStatus
    records_checked: int
    verified_at: float
    first_broken_seq: Optional[int] = None
    detail: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "segment_id", require_identifier(self.segment_id, field="segment_id")
        )
        if not isinstance(self.status, IntegrityStatus):
            raise DomainValidationError(
                "status must be an IntegrityStatus",
                field="status",
                offending_type=type(self.status).__name__,
            )
        if isinstance(self.records_checked, bool) or not isinstance(self.records_checked, int):
            raise DomainValidationError(
                "records_checked must be an integer",
                field="records_checked",
                offending_type=type(self.records_checked).__name__,
            )
        if self.records_checked < 0:
            raise DomainValidationError(
                "records_checked must not be negative",
                field="records_checked",
                value=self.records_checked,
            )
        object.__setattr__(
            self, "verified_at", require_timestamp(self.verified_at, field="verified_at")
        )
        if self.first_broken_seq is not None:
            if isinstance(self.first_broken_seq, bool) or not isinstance(
                self.first_broken_seq, int
            ):
                raise DomainValidationError(
                    "first_broken_seq must be an integer",
                    field="first_broken_seq",
                    offending_type=type(self.first_broken_seq).__name__,
                )
            if self.first_broken_seq < 0:
                raise DomainValidationError(
                    "first_broken_seq must not be negative",
                    field="first_broken_seq",
                    value=self.first_broken_seq,
                )
        if self.status is IntegrityStatus.BROKEN and self.first_broken_seq is None:
            raise DomainValidationError(
                "a broken segment must localise the first failing record",
                field="first_broken_seq",
            )
        if self.status is not IntegrityStatus.BROKEN and self.first_broken_seq is not None:
            raise DomainValidationError(
                "only a broken segment may name a failing record",
                field="first_broken_seq",
                status=self.status.value,
            )

    @property
    def is_acceptable(self) -> bool:
        """Whether an auditor may rely on this segment."""
        return self.status.is_acceptable

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation for reporting."""
        return {
            "segment_id": self.segment_id,
            "status": self.status.value,
            "records_checked": self.records_checked,
            "verified_at": self.verified_at,
            "first_broken_seq": self.first_broken_seq,
            "detail": self.detail,
        }
