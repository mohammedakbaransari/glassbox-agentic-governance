"""First-class ``AuditEvent`` entity (P3 roadmap item, architecture review §5).

The evidence store already proves *decisions* (``IntentRecord``/``OutcomeRecord``,
MAC-chained, append-only). ``AuditEvent`` is a narrower, deliberately separate
concept: a read-model record of *administrative/platform* activity -- tenant
onboarding, mandate grants, policy bundle rotation, approval resolution -- the
kind of event a compliance dashboard or SIEM export needs, distinct from the
per-decision evidence chain and not a replacement for it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_empty,
    require_timestamp,
)

__all__ = ["AuditEvent"]


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """One administrative/platform event, independent of the evidence chain.

    Attributes:
        event_id: Unique identifier for this event.
        event_type: A short, stable name (e.g. ``"tenant.onboarded"``,
            ``"mandate.granted"``, ``"approval.resolved"``).
        occurred_at: POSIX epoch seconds the event happened.
        actor: Who or what caused it (a user id, service principal, or
            ``"system"``).
        tenant_id: Tenant this event pertains to, if any.
        subject_id: The entity affected (a mandate id, approval id, tenant
            id, ...), if any.
        detail: Arbitrary, canonically-serialisable key/value pairs.
    """

    event_id: str
    event_type: str
    occurred_at: float
    actor: str
    tenant_id: Optional[str] = None
    subject_id: Optional[str] = None
    detail: tuple = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", require_identifier(self.event_id, field="event_id"))
        object.__setattr__(
            self, "event_type", require_non_empty(self.event_type, field="event_type")
        )
        object.__setattr__(
            self, "occurred_at", require_timestamp(self.occurred_at, field="occurred_at")
        )
        object.__setattr__(self, "actor", require_non_empty(self.actor, field="actor"))
        if self.tenant_id is not None:
            object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if self.subject_id is not None:
            object.__setattr__(
                self, "subject_id", require_identifier(self.subject_id, field="subject_id")
            )
        if not isinstance(self.detail, tuple):
            object.__setattr__(
                self,
                "detail",
                freeze_mapping(self.detail if isinstance(self.detail, Mapping) else None, field="detail"),
            )

    @classmethod
    def create(
        cls,
        event_id: str,
        event_type: str,
        *,
        occurred_at: float,
        actor: str,
        tenant_id: Optional[str] = None,
        subject_id: Optional[str] = None,
        detail: Optional[Mapping[str, Any]] = None,
    ) -> "AuditEvent":
        """Construct an event from a plain mapping, freezing ``detail``."""
        return cls(
            event_id=event_id,
            event_type=event_type,
            occurred_at=occurred_at,
            actor=actor,
            tenant_id=tenant_id,
            subject_id=subject_id,
            detail=freeze_mapping(detail, field="detail"),
        )

    def as_evidence(self) -> Mapping[str, Any]:
        """Canonical representation for API responses and audit reports."""
        return {
            "event_id": self.event_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "actor": self.actor,
            "tenant_id": self.tenant_id,
            "subject_id": self.subject_id,
            "detail": dict(self.detail),
        }
