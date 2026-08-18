"""Actions, resources, consequence and exposure (GB-002, WS-1).

The v1 taxonomy (``DecisionType``, 12 domain-oriented values) had no axis for
*how bad it is if this is wrong*. "Transfer $5", "transfer $5M" and "delete the
production database" all shared the same governance shape.

:class:`ConsequenceClass` adds that axis, and it is the single most load-bearing
concept in the rebuilt system:

* it decides whether a dependency outage may be tolerated (invariant I4);
* it sets a floor under the risk score, so a saturating average can no longer
  score a $50M irreversible wire as "medium";
* it selects the mandate ceiling an agent must hold.

Both :attr:`ProposedAction.consequence` and :attr:`ProposedAction.exposure` are
**server-derived** from the governed action catalogue (GB-010). They are never
read from a request body. Nothing here can be populated by a caller, because
constructing a :class:`ProposedAction` is a control-plane operation.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_negative,
)

__all__ = [
    "ConsequenceClass",
    "BlastRadius",
    "ResourceRef",
    "Exposure",
    "ProposedAction",
]


class ConsequenceClass(Enum):
    """How reversible the real-world effect of an action is.

    Ordered from least to most severe. Comparison operators are defined so that
    mandate ceilings and risk floors can be expressed directly, for example
    ``action.consequence > mandate.max_consequence``.
    """

    ADVISORY = "advisory"
    REVERSIBLE = "reversible"
    COMPENSABLE = "compensable"
    IRREVERSIBLE = "irreversible"

    @property
    def severity(self) -> int:
        """Ordinal severity, ``0`` (advisory) to ``3`` (irreversible)."""
        return _CONSEQUENCE_SEVERITY[self]

    @property
    def requires_prior_evidence(self) -> bool:
        """Whether durable evidence must exist before the effect (invariant I1).

        Advisory actions produce no external effect, so evidence may be written
        asynchronously without weakening any assurance claim.
        """
        return self is not ConsequenceClass.ADVISORY

    @property
    def may_degrade_on_dependency_failure(self) -> bool:
        """Whether a governance dependency outage may be tolerated.

        Only advisory actions may proceed when a limit or baseline store is
        unreachable. Everything else fails closed -- this is the direct fix for
        the v1 velocity breaker admitting all traffic during a Redis outage.
        """
        return self is ConsequenceClass.ADVISORY

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ConsequenceClass):
            return NotImplemented
        return self.severity < other.severity

    def __le__(self, other: object) -> bool:
        if not isinstance(other, ConsequenceClass):
            return NotImplemented
        return self.severity <= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, ConsequenceClass):
            return NotImplemented
        return self.severity > other.severity

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, ConsequenceClass):
            return NotImplemented
        return self.severity >= other.severity


_CONSEQUENCE_SEVERITY: Mapping[ConsequenceClass, int] = {
    ConsequenceClass.ADVISORY: 0,
    ConsequenceClass.REVERSIBLE: 1,
    ConsequenceClass.COMPENSABLE: 2,
    ConsequenceClass.IRREVERSIBLE: 3,
}


class BlastRadius(Enum):
    """How many principals or records a mistake would touch."""

    SINGLE = "single"
    GROUP = "group"
    TENANT = "tenant"
    GLOBAL = "global"

    @property
    def severity(self) -> int:
        """Ordinal severity, ``0`` (single) to ``3`` (global)."""
        return _BLAST_SEVERITY[self]

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, BlastRadius):
            return NotImplemented
        return self.severity < other.severity

    def __le__(self, other: object) -> bool:
        if not isinstance(other, BlastRadius):
            return NotImplemented
        return self.severity <= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, BlastRadius):
            return NotImplemented
        return self.severity > other.severity

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, BlastRadius):
            return NotImplemented
        return self.severity >= other.severity


_BLAST_SEVERITY: Mapping[BlastRadius, int] = {
    BlastRadius.SINGLE: 0,
    BlastRadius.GROUP: 1,
    BlastRadius.TENANT: 2,
    BlastRadius.GLOBAL: 3,
}


@dataclass(frozen=True, slots=True)
class ResourceRef:
    """A tenant-scoped reference to the thing an action operates on.

    Carrying ``tenant_id`` on the resource (and not only on the principal) makes
    cross-tenant action a comparison rather than an assumption.
    """

    kind: str
    id: str
    tenant_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "kind", require_identifier(self.kind, field="kind"))
        object.__setattr__(self, "id", require_identifier(self.id, field="id"))
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))

    @property
    def qualified_name(self) -> str:
        """Stable ``tenant/kind/id`` string for keys, logs and metrics labels."""
        return f"{self.tenant_id}/{self.kind}/{self.id}"

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the resource fields recorded on the evidence row."""
        return {"resource_kind": self.kind, "resource_id": self.id, "tenant_id": self.tenant_id}


@dataclass(frozen=True, slots=True)
class Exposure:
    """The magnitude of what is at stake if an action is wrong.

    Attributes:
        blast_radius: How widely a mistake would propagate.
        monetary: Value at risk in the tenant's reporting currency, if any.
        records: Number of records affected, if any.

    Unknown magnitudes are represented by ``None``, not by ``0``. A missing
    figure must never be treated as a small figure -- that is how a $50M wire
    ends up scored as low risk.
    """

    blast_radius: BlastRadius = BlastRadius.SINGLE
    monetary: Optional[float] = None
    records: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.blast_radius, BlastRadius):
            raise DomainValidationError(
                "unsupported blast radius",
                field="blast_radius",
                offending_type=type(self.blast_radius).__name__,
            )
        if self.monetary is not None:
            object.__setattr__(
                self, "monetary", require_non_negative(self.monetary, field="monetary")
            )
        if self.records is not None:
            if isinstance(self.records, bool) or not isinstance(self.records, int):
                raise DomainValidationError(
                    "records must be an integer",
                    field="records",
                    offending_type=type(self.records).__name__,
                )
            if self.records < 0:
                raise DomainValidationError(
                    "records must not be negative", field="records", value=self.records
                )

    @property
    def is_quantified(self) -> bool:
        """Whether any magnitude is known."""
        return self.monetary is not None or self.records is not None

    def exceeds(self, ceiling: "Exposure") -> bool:
        """Return whether this exposure breaches ``ceiling`` on any dimension.

        An unknown magnitude on *this* side is treated as breaching a ceiling
        that constrains that dimension: the system cannot prove it is within the
        limit, so it must not assume that it is (invariant I4).
        """
        if not isinstance(ceiling, Exposure):
            raise DomainValidationError(
                "ceiling must be an Exposure",
                field="ceiling",
                offending_type=type(ceiling).__name__,
            )
        if self.blast_radius > ceiling.blast_radius:
            return True
        if ceiling.monetary is not None:
            if self.monetary is None or self.monetary > ceiling.monetary:
                return True
        if ceiling.records is not None:
            if self.records is None or self.records > ceiling.records:
                return True
        return False

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the exposure fields recorded on the evidence row."""
        return {
            "blast_radius": self.blast_radius.value,
            "monetary": self.monetary,
            "records": self.records,
        }


@dataclass(frozen=True, slots=True)
class ProposedAction:
    """A concrete action an agent wishes to perform, as understood by the control plane.

    ``parameters`` is stored as a frozen tuple of sorted pairs so that the
    instance is genuinely immutable and hashable, and so that its canonical
    serialisation is stable across processes.

    Attributes:
        action: Catalogue name of the action, e.g. ``payments.wire_transfer``.
        resource: The tenant-scoped target of the action.
        consequence: Server-derived reversibility class.
        exposure: Server-derived magnitude at risk.
        idempotency_key: Caller-stable key used for at-most-once dispatch.
        parameters: Validated, schema-checked action parameters.
    """

    action: str
    resource: ResourceRef
    consequence: ConsequenceClass
    exposure: Exposure
    idempotency_key: str
    parameters: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", require_identifier(self.action, field="action"))
        if not isinstance(self.resource, ResourceRef):
            raise DomainValidationError(
                "resource must be a ResourceRef",
                field="resource",
                offending_type=type(self.resource).__name__,
            )
        if not isinstance(self.consequence, ConsequenceClass):
            raise DomainValidationError(
                "consequence must be a ConsequenceClass derived from the action catalogue",
                field="consequence",
                offending_type=type(self.consequence).__name__,
            )
        if not isinstance(self.exposure, Exposure):
            raise DomainValidationError(
                "exposure must be an Exposure derived from the action catalogue",
                field="exposure",
                offending_type=type(self.exposure).__name__,
            )
        object.__setattr__(
            self,
            "idempotency_key",
            require_identifier(self.idempotency_key, field="idempotency_key"),
        )
        if isinstance(self.parameters, Mapping):
            object.__setattr__(
                self, "parameters", freeze_mapping(self.parameters, field="parameters")
            )
        elif not isinstance(self.parameters, tuple):
            raise DomainValidationError(
                "parameters must be a mapping or a tuple of pairs",
                field="parameters",
                offending_type=type(self.parameters).__name__,
            )

    @property
    def tenant_id(self) -> str:
        """Tenant that owns the target resource."""
        return self.resource.tenant_id

    @property
    def requires_prior_evidence(self) -> bool:
        """Whether durable evidence must precede dispatch (invariant I1)."""
        return self.consequence.requires_prior_evidence

    def parameter(self, name: str, default: Optional[Any] = None) -> Optional[Any]:
        """Return a validated parameter value, or ``default`` when absent."""
        for key, value in self.parameters:
            if key == name:
                return value
        return default

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the action fields recorded on the evidence row."""
        return {
            "action": self.action,
            **self.resource.as_evidence(),
            "consequence_class": self.consequence.value,
            "exposure": dict(self.exposure.as_evidence()),
            "idempotency_key": self.idempotency_key,
            "parameters": {key: value for key, value in self.parameters},
        }
