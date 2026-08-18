"""Action catalogue port (GB-010)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.catalogue import ActionDefinition

__all__ = ["ActionCatalogue"]


@runtime_checkable
class ActionCatalogue(Protocol):
    """Resolves the governed shape of an action, deny-by-default.

    Conforming adapters must:

    * be **deny by default** -- an action absent from the active bundle is
      ``ActionNotGovernedError``, never a permissive guess;
    * be **attributable** -- :meth:`active_bundle_digest` names exactly which
      catalogue version produced a resolution, recorded in evidence the same
      way a policy bundle digest is;
    * never derive ``consequence`` or ``exposure`` from anything the caller
      supplied outside the bundle's own :class:`~glassbox.domain.catalogue.ExposureRule`
      (invariant I2).
    """

    def resolve(self, tenant_id: str, action: str) -> ActionDefinition:
        """Return the governed definition for ``action``.

        Raises:
            glassbox.domain.errors.ActionNotGovernedError: If no definition
                exists for this action. Callers must fail closed.
            glassbox.domain.errors.CatalogueBundleUnavailableError: If no active
                bundle is loaded for the tenant.
        """
        ...

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the SHA-256 digest of the bundle active for a tenant.

        Raises:
            glassbox.domain.errors.CatalogueBundleUnavailableError: If no bundle
                is active.
        """
        ...
