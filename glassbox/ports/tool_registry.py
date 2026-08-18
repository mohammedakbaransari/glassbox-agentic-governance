"""Tool registry port (GB-013)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.tool_registry import ToolDefinition

__all__ = ["ToolRegistry"]


@runtime_checkable
class ToolRegistry(Protocol):
    """Resolves the governed shape of a tool, deny-by-default.

    Conforming adapters must:

    * be **deny by default** -- a tool absent from the active bundle, or
      presented with a definition digest that does not match the registered
      one, is ``ToolNotGovernedError``, never a permissive guess;
    * be **attributable** -- :meth:`active_bundle_digest` names exactly which
      registry version produced a resolution;
    * pin the *definition*, not just the name -- a changed description or
      schema is a different, ungoverned tool until re-registered (GB-014).
    """

    def resolve(self, tenant_id: str, tool_name: str, definition_sha256: str) -> ToolDefinition:
        """Return the governed definition for ``tool_name``.

        Raises:
            glassbox.domain.errors.ToolNotGovernedError: If no definition
                exists for this tool name, or ``definition_sha256`` does not
                match the registered digest. Callers must fail closed.
            glassbox.domain.errors.ToolRegistryUnavailableError: If no active
                bundle is loaded for the tenant.
        """
        ...

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the SHA-256 digest of the bundle active for a tenant.

        Raises:
            glassbox.domain.errors.ToolRegistryUnavailableError: If no bundle
                is active.
        """
        ...
