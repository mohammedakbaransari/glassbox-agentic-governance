"""Agent identity and delegation (GB-002, WS-1).

This module exists to close the *trust-model inversion* (fundamental problem F1).
In v1 the tenant and the acting user arrived as ``X-Tenant-ID`` / ``X-User-ID``
headers and were copied verbatim into the request context, so any holder of the
single shared API key could act as any tenant and any user.

Here, a :class:`VerifiedPrincipal` is the *only* carrier of identity in the
decision path, and every governance-relevant identity attribute -- above all
``tenant_id`` -- is a field of that principal. A principal is only ever produced
by an :class:`~glassbox.ports.identity.IdentityVerifier` from a credential that
was cryptographically validated. Nothing in this module can construct a
principal from a request header.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, FrozenSet, Iterable, Mapping, Optional, Tuple

from glassbox.domain.errors import (
    CredentialExpiredError,
    DelegationError,
    DomainValidationError,
)
from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_empty,
    require_timestamp,
)

__all__ = [
    "CredentialType",
    "SubjectType",
    "RawCredential",
    "DelegationHop",
    "DelegationChain",
    "VerifiedPrincipal",
]


class CredentialType(Enum):
    """Supported workload-credential formats.

    A shared bearer API key is deliberately absent: it cannot identify a tenant,
    an agent or a delegating human, and it was the root of several v1 threats.
    """

    SPIFFE = "spiffe"
    OIDC = "oidc"
    MTLS = "mtls"


class SubjectType(Enum):
    """What kind of actor occupies a hop in a delegation chain."""

    HUMAN = "human"
    AGENT = "agent"
    SERVICE = "service"


@dataclass(frozen=True, slots=True)
class RawCredential:
    """An unverified credential as presented by a caller.

    The ``material`` field holds secret bytes (a JWT, an SVID, a certificate
    chain). :meth:`__repr__` and :meth:`__str__` redact it so that an accidental
    ``logger.info("got %s", credential)`` cannot leak it into an audit log --
    which is exactly the sort of leak that governance systems must not create.

    Attributes:
        credential_type: The format of ``material``.
        material: The secret credential itself. Never logged, never serialised.
        presented_at: Epoch seconds at which the caller presented it.
    """

    credential_type: CredentialType
    material: str
    presented_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.credential_type, CredentialType):
            raise DomainValidationError(
                "unsupported credential type",
                field="credential_type",
                offending_type=type(self.credential_type).__name__,
            )
        require_non_empty(self.material, field="material")
        object.__setattr__(
            self, "presented_at", require_timestamp(self.presented_at, field="presented_at")
        )

    def __repr__(self) -> str:
        return (
            f"RawCredential(credential_type={self.credential_type.value!r}, "
            f"material='<redacted:{len(self.material)}chars>', "
            f"presented_at={self.presented_at!r})"
        )

    __str__ = __repr__


@dataclass(frozen=True, slots=True)
class DelegationHop:
    """One verified link in a chain of authority.

    Attributes:
        subject: Stable identifier of the actor at this hop.
        subject_type: Whether the actor is a human, an agent or a service.
        capabilities: The capability names this hop is permitted to exercise.
        issued_at: Epoch seconds at which this hop's authority began.
        expires_at: Epoch seconds at which it ends.
    """

    subject: str
    subject_type: SubjectType
    capabilities: FrozenSet[str]
    issued_at: float
    expires_at: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "subject", require_identifier(self.subject, field="subject"))
        if not isinstance(self.subject_type, SubjectType):
            raise DomainValidationError(
                "unsupported subject type",
                field="subject_type",
                offending_type=type(self.subject_type).__name__,
            )
        if not isinstance(self.capabilities, frozenset):
            object.__setattr__(self, "capabilities", frozenset(self.capabilities or ()))
        for capability in self.capabilities:
            require_identifier(capability, field="capabilities")
        object.__setattr__(self, "issued_at", require_timestamp(self.issued_at, field="issued_at"))
        object.__setattr__(
            self, "expires_at", require_timestamp(self.expires_at, field="expires_at")
        )
        if self.expires_at <= self.issued_at:
            raise DomainValidationError(
                "expires_at must be strictly after issued_at",
                field="expires_at",
                issued_at=self.issued_at,
                expires_at=self.expires_at,
            )

    def is_valid_at(self, now: float) -> bool:
        """Return whether this hop's authority is live at ``now`` (epoch seconds)."""
        return self.issued_at <= now < self.expires_at

    def as_evidence(self) -> Mapping[str, Any]:
        """Return a canonical, non-secret representation for the evidence record."""
        return {
            "subject": self.subject,
            "subject_type": self.subject_type.value,
            "capabilities": sorted(self.capabilities),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True, slots=True)
class DelegationChain:
    """An ordered chain of authority, root first.

    The root hop is normally the human who authorised the work; subsequent hops
    are the agents and services acting on their behalf.

    The chain enforces **attenuation**: authority may only narrow as it is passed
    on. A hop that claims a capability its delegator does not hold is a privilege
    escalation, and constructing such a chain raises immediately rather than
    deferring the check to a policy rule that might not be configured.
    """

    hops: Tuple[DelegationHop, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.hops, tuple):
            object.__setattr__(self, "hops", tuple(self.hops or ()))
        for index, hop in enumerate(self.hops):
            if not isinstance(hop, DelegationHop):
                raise DomainValidationError(
                    "delegation chain may only contain DelegationHop instances",
                    field=f"hops[{index}]",
                    offending_type=type(hop).__name__,
                )
        self._require_attenuating()

    def _require_attenuating(self) -> None:
        """Raise if any hop widens the authority granted by its delegator."""
        for index in range(1, len(self.hops)):
            delegator = self.hops[index - 1]
            delegate = self.hops[index]
            widened = delegate.capabilities - delegator.capabilities
            if widened:
                raise DelegationError(
                    "delegation chain widens authority; attenuation is mandatory",
                    hop_index=index,
                    delegator=delegator.subject,
                    delegate=delegate.subject,
                    widened_capabilities=sorted(widened),
                )
            if delegate.expires_at > delegator.expires_at:
                raise DelegationError(
                    "delegation chain extends validity beyond its delegator",
                    hop_index=index,
                    delegator=delegator.subject,
                    delegate=delegate.subject,
                    delegator_expires_at=delegator.expires_at,
                    delegate_expires_at=delegate.expires_at,
                )

    @property
    def is_empty(self) -> bool:
        """Whether the chain contains no hops."""
        return not self.hops

    @property
    def root(self) -> Optional[DelegationHop]:
        """The originating hop, or ``None`` for an empty chain."""
        return self.hops[0] if self.hops else None

    @property
    def leaf(self) -> Optional[DelegationHop]:
        """The final hop -- the actor that will perform the action."""
        return self.hops[-1] if self.hops else None

    @property
    def depth(self) -> int:
        """Number of hops in the chain."""
        return len(self.hops)

    def effective_capabilities(self) -> FrozenSet[str]:
        """Capabilities available at the leaf.

        Because the chain is attenuating, this is the intersection of every hop,
        which equals the leaf's own set. It is computed as an intersection anyway
        so the property still holds if the attenuation rule is ever relaxed.
        """
        if not self.hops:
            return frozenset()
        effective = self.hops[0].capabilities
        for hop in self.hops[1:]:
            effective = effective & hop.capabilities
        return effective

    def is_valid_at(self, now: float) -> bool:
        """Return whether *every* hop is live at ``now``."""
        return bool(self.hops) and all(hop.is_valid_at(now) for hop in self.hops)

    def subjects(self) -> Tuple[str, ...]:
        """Return the ordered subject identifiers, root first."""
        return tuple(hop.subject for hop in self.hops)

    def as_evidence(self) -> Tuple[Mapping[str, Any], ...]:
        """Return a canonical representation for the evidence record."""
        return tuple(hop.as_evidence() for hop in self.hops)

    @classmethod
    def of(cls, hops: Iterable[DelegationHop]) -> "DelegationChain":
        """Build a chain from any iterable of hops."""
        return cls(tuple(hops))


@dataclass(frozen=True, slots=True)
class VerifiedPrincipal:
    """The authenticated identity of an agent, as established by a verifier.

    Every field is derived from verified credential material. In particular
    ``tenant_id`` is **not** a request header: it is the tenancy claim that the
    credential itself carries, which is what makes cross-tenant impersonation
    structurally impossible rather than merely discouraged.

    Attributes:
        agent_ref: Stable logical identity of the agent (survives restarts).
        agent_instance_id: Identity of this running instance.
        tenant_id: Tenant the credential belongs to.
        delegating_subject: The human or service on whose behalf the agent acts.
        delegation_chain: The verified, attenuating chain of authority.
        credential_type: Format of the credential that was verified.
        credential_id: Non-secret identifier of that credential (jti, SVID path).
        issued_at: Epoch seconds at which the credential became valid.
        expires_at: Epoch seconds at which it stops being valid.
        claims: Additional non-secret verified claims, frozen for immutability.
    """

    agent_ref: str
    agent_instance_id: str
    tenant_id: str
    credential_type: CredentialType
    credential_id: str
    issued_at: float
    expires_at: float
    delegating_subject: Optional[str] = None
    delegation_chain: DelegationChain = field(default_factory=DelegationChain)
    claims: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "agent_ref", require_identifier(self.agent_ref, field="agent_ref"))
        object.__setattr__(
            self,
            "agent_instance_id",
            require_identifier(self.agent_instance_id, field="agent_instance_id"),
        )
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(
            self, "credential_id", require_identifier(self.credential_id, field="credential_id")
        )
        if not isinstance(self.credential_type, CredentialType):
            raise DomainValidationError(
                "unsupported credential type",
                field="credential_type",
                offending_type=type(self.credential_type).__name__,
            )
        object.__setattr__(self, "issued_at", require_timestamp(self.issued_at, field="issued_at"))
        object.__setattr__(
            self, "expires_at", require_timestamp(self.expires_at, field="expires_at")
        )
        if self.expires_at <= self.issued_at:
            raise DomainValidationError(
                "expires_at must be strictly after issued_at",
                field="expires_at",
                issued_at=self.issued_at,
                expires_at=self.expires_at,
            )
        if self.delegating_subject is not None:
            object.__setattr__(
                self,
                "delegating_subject",
                require_identifier(self.delegating_subject, field="delegating_subject"),
            )
        if not isinstance(self.delegation_chain, DelegationChain):
            raise DomainValidationError(
                "delegation_chain must be a DelegationChain",
                field="delegation_chain",
                offending_type=type(self.delegation_chain).__name__,
            )
        if isinstance(self.claims, Mapping):
            object.__setattr__(self, "claims", freeze_mapping(self.claims, field="claims"))
        elif not isinstance(self.claims, tuple):
            raise DomainValidationError(
                "claims must be a mapping or a tuple of pairs",
                field="claims",
                offending_type=type(self.claims).__name__,
            )
        self._require_chain_consistency()

    def _require_chain_consistency(self) -> None:
        """Raise if the delegation chain contradicts the principal's own identity."""
        chain = self.delegation_chain
        if chain.is_empty:
            return

        leaf = chain.leaf
        assert leaf is not None  # non-empty chain always has a leaf
        if leaf.subject != self.agent_ref:
            raise DelegationError(
                "the delegation chain leaf must be the acting agent",
                agent_ref=self.agent_ref,
                leaf_subject=leaf.subject,
            )

        root = chain.root
        assert root is not None
        if self.delegating_subject is not None and root.subject != self.delegating_subject:
            raise DelegationError(
                "the delegation chain root must be the delegating subject",
                delegating_subject=self.delegating_subject,
                root_subject=root.subject,
            )

    # ----------------------------------------------------------------- #
    # Behaviour
    # ----------------------------------------------------------------- #

    def is_expired(self, now: float) -> bool:
        """Return whether the credential is outside its validity window at ``now``."""
        return not self.issued_at <= require_timestamp(now, field="now") < self.expires_at

    def require_valid_at(self, now: float) -> None:
        """Raise if the principal is not usable at ``now``.

        Raises:
            CredentialExpiredError: If the credential window has passed or has
                not yet opened, or if any delegation hop is not live.
        """
        moment = require_timestamp(now, field="now")
        if self.is_expired(moment):
            raise CredentialExpiredError(
                "credential is outside its validity window",
                agent_ref=self.agent_ref,
                tenant_id=self.tenant_id,
                credential_id=self.credential_id,
                issued_at=self.issued_at,
                expires_at=self.expires_at,
                now=moment,
            )
        if not self.delegation_chain.is_empty and not self.delegation_chain.is_valid_at(moment):
            raise CredentialExpiredError(
                "a delegation hop is outside its validity window",
                agent_ref=self.agent_ref,
                tenant_id=self.tenant_id,
                chain_depth=self.delegation_chain.depth,
                now=moment,
            )

    def claim(self, name: str, default: Optional[Any] = None) -> Optional[Any]:
        """Return a verified claim value, or ``default`` when absent."""
        for key, value in self.claims:
            if key == name:
                return value
        return default

    def has_capability(self, capability: str) -> bool:
        """Return whether the effective delegated capabilities include ``capability``.

        An empty delegation chain means no delegated authority was presented, so
        this returns ``False`` -- deny by default (invariant I4).
        """
        return capability in self.delegation_chain.effective_capabilities()

    def owns(self, tenant_id: str) -> bool:
        """Return whether this principal belongs to ``tenant_id``."""
        return self.tenant_id == tenant_id

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the identity fields recorded on every evidence row."""
        return {
            "agent_ref": self.agent_ref,
            "agent_instance_id": self.agent_instance_id,
            "tenant_id": self.tenant_id,
            "delegating_subject": self.delegating_subject,
            "delegation_chain": list(self.delegation_chain.as_evidence()),
            "credential_type": self.credential_type.value,
            "credential_id": self.credential_id,
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
            "claims": {key: value for key, value in self.claims},
        }
