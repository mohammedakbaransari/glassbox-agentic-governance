"""Mandate store port (GB-002, WS-3)."""

from __future__ import annotations

from typing import Optional, Protocol, runtime_checkable

from glassbox.domain.mandate import Mandate

__all__ = ["MandateStore"]


@runtime_checkable
class MandateStore(Protocol):
    """Resolves an agent's approved scope of authority.

    ``tenant_id`` is a required positional parameter on every method. It has no
    default and it is not ``Optional``: v1's
    ``SQLiteAuditRepository.query(tenant_id=None)`` silently omitted the tenant
    predicate and returned every tenant's rows, and the only durable fix is to
    make the unscoped call impossible to write.
    """

    def get(self, tenant_id: str, agent_ref: str, *, now: float) -> Optional[Mandate]:
        """Return the active mandate for an agent, or ``None`` if there is none.

        ``None`` means *deny* (invariant I4); it must never be interpreted as
        unrestricted authority.

        Args:
            tenant_id: Tenant from the verified principal.
            agent_ref: Agent from the verified principal.
            now: Current time in POSIX epoch seconds.

        Returns:
            The active, unrevoked mandate, or ``None``.

        Raises:
            glassbox.domain.errors.MandateError: If the store cannot answer
                authoritatively. Callers must fail closed.
        """
        ...

    def is_revoked(self, tenant_id: str, agent_ref: str, *, now: float) -> bool:
        """Return whether the agent's authority has been revoked.

        Separated from :meth:`get` so revocation can be served from a fast,
        low-latency deny list that propagates in under a second, while the full
        mandate comes from the durable store (GB-016).

        Raises:
            glassbox.domain.errors.MandateError: If revocation state cannot be
                determined. Callers must treat an unanswerable check as revoked.
        """
        ...
