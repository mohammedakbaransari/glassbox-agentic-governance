"""Attestation provider port (GB-010).

An :class:`AttestationProvider` resolves a governance-critical fact -- "was a
currency transaction report filed", "is this change inside an approved
maintenance window", "does this prescriber hold a valid DEA registration" --
from a system of record. It is the structural fix for the other half of F1:
these facts must never come from the caller's own request payload.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.action import ResourceRef

__all__ = ["AttestationProvider"]


@runtime_checkable
class AttestationProvider(Protocol):
    """Resolves named attestations against a system of record.

    Conforming adapters must:

    * **fail closed** -- raise
      :class:`~glassbox.domain.errors.AttestationUnavailableError` when the
      system of record cannot be reached or has no answer, rather than
      defaulting to ``True`` or ``False``. Callers treat "unavailable" and
      "resolved false" identically: deny.
    * never accept the answer as part of the same request that is being
      governed -- the caller's own claim is not evidence.
    """

    def resolve(self, tenant_id: str, resource: ResourceRef, name: str, *, now: float) -> bool:
        """Return whether the named attestation holds for ``resource``.

        Args:
            tenant_id: Owning tenant.
            resource: The resource the attestation concerns.
            name: Attestation name, e.g. ``"ctr_filed"``.
            now: Caller-supplied evaluation time (never read from the wall
                clock inside a port implementation -- invariant I6).

        Raises:
            glassbox.domain.errors.AttestationUnavailableError: If the
                attestation cannot be resolved authoritatively right now.
        """
        ...
