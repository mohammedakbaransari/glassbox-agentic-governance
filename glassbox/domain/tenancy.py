"""First-class ``Tenant`` entity (P3 roadmap item, architecture review §5).

The domain model previously identified a tenant only by its ``tenant_id``
string, threaded through every record. That is sufficient for the decision
boundary but not for a platform with tenant onboarding, suspension, or an
admin UI -- none of which have anywhere to attach state today. ``Tenant`` is
that anchor: immutable, validated, and pure (no I/O, no clock reads).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_empty,
    require_timestamp,
)

__all__ = ["TenantStatus", "Tenant"]


class TenantStatus(Enum):
    """Lifecycle state of a tenant. Ordered by how much authority it grants."""

    PENDING = "pending"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    OFFBOARDED = "offboarded"

    @property
    def permits_new_decisions(self) -> bool:
        """Whether a tenant in this state may have new decisions evaluated."""
        return self is TenantStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Tenant:
    """An onboarded organisation boundary.

    Attributes:
        tenant_id: The identifier already threaded through every evidence
            record and mandate. This entity does not replace that string key;
            it gives it a validated, queryable home.
        display_name: Human-readable name, for an admin UI or audit report.
        status: Current lifecycle state.
        created_at: POSIX epoch seconds the tenant was onboarded.
        metadata: Arbitrary, canonically-serialisable key/value pairs (e.g.
            contract tier, region). Frozen to a sorted tuple of pairs so the
            entity stays hashable and immutable.
    """

    tenant_id: str
    display_name: str
    status: TenantStatus
    created_at: float
    metadata: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(
            self, "display_name", require_non_empty(self.display_name, field="display_name")
        )
        object.__setattr__(self, "created_at", require_timestamp(self.created_at, field="created_at"))
        if not isinstance(self.metadata, tuple):
            object.__setattr__(
                self,
                "metadata",
                freeze_mapping(self.metadata if isinstance(self.metadata, Mapping) else None, field="metadata"),
            )

    @classmethod
    def create(
        cls,
        tenant_id: str,
        display_name: str,
        *,
        created_at: float,
        status: TenantStatus = TenantStatus.PENDING,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> "Tenant":
        """Construct a tenant from a plain mapping, freezing ``metadata``."""
        return cls(
            tenant_id=tenant_id,
            display_name=display_name,
            status=status,
            created_at=created_at,
            metadata=freeze_mapping(metadata, field="metadata"),
        )

    def with_status(self, status: TenantStatus) -> "Tenant":
        """Return a copy transitioned to a new lifecycle state."""
        from dataclasses import replace

        return replace(self, status=status)

    def as_evidence(self) -> Mapping[str, Any]:
        """Canonical representation for API responses and audit reports."""
        return {
            "tenant_id": self.tenant_id,
            "display_name": self.display_name,
            "status": self.status.value,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
