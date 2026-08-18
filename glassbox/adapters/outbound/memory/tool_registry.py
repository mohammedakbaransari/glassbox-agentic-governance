"""In-memory tool registry (GB-003, reference for GB-013, GB-014).

**Development only.** State lives in one process; a bundle loaded here does
not survive a restart or reach other replicas. What must be preserved by any
durable replacement: deny by default (no bundle, unknown tool, or a digest
mismatch are all refused), and an unreachable registry is handled identically
to "tool not found" -- the caller cannot distinguish an outage from a genuine
absence, and must not be able to.

**Rug-pull detection (GB-014).** The first digest ever seen for a tool name is
its approval. Loading a later bundle with a *different* digest for the same
name does not silently replace the approval -- it quarantines the tool, and
:meth:`InMemoryToolRegistry.resolve` refuses every call until an operator
explicitly calls :meth:`approve`. This is what makes rug-pull detection
enforceable rather than aspirational: v1's docstring claimed it but retained no
definition-history state at all.
"""

from __future__ import annotations

import threading
from typing import Dict, Set, Tuple

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import (
    ToolNotGovernedError,
    ToolQuarantinedError,
    ToolRegistryUnavailableError,
)
from glassbox.domain.tool_registry import ToolDefinition, ToolRegistryBundle
from glassbox.ports.tool_registry import ToolRegistry

__all__ = ["InMemoryToolRegistry", "build_tool_registry"]


class InMemoryToolRegistry:
    """Tool definitions held in process memory, keyed by tenant.

    No bundle is loaded by default: every ``resolve`` call raises
    :class:`~glassbox.domain.errors.ToolRegistryUnavailableError` until
    :meth:`load_bundle` is called.
    """

    __slots__ = ("_lock", "_bundles", "_available", "_approved_digests", "_quarantined")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundles: Dict[str, ToolRegistryBundle] = {}
        self._available = True
        self._approved_digests: Dict[Tuple[str, str], str] = {}
        self._quarantined: Set[Tuple[str, str]] = set()

    def load_bundle(self, bundle: ToolRegistryBundle) -> None:
        """Register ``bundle`` as the active registry for its tenant.

        A tool whose digest differs from the one previously approved for its
        name is quarantined rather than silently re-approved (GB-014); a tool
        seen for the first time is approved at whatever digest this bundle
        gives it.
        """
        with self._lock:
            for definition in bundle.definitions:
                key = (bundle.tenant_id, definition.tool_name)
                previous = self._approved_digests.get(key)
                if previous is None:
                    self._approved_digests[key] = definition.definition_sha256
                elif previous != definition.definition_sha256:
                    self._quarantined.add(key)
            self._bundles[bundle.tenant_id] = bundle

    def resolve(self, tenant_id: str, tool_name: str, definition_sha256: str) -> ToolDefinition:
        """Return the governed definition for ``tool_name`` at ``definition_sha256``.

        Raises:
            ToolRegistryUnavailableError: If no bundle is loaded for
                ``tenant_id`` or the registry is simulating an outage.
            ToolQuarantinedError: If the tool's definition changed since it was
                approved and has not been explicitly re-approved.
            ToolNotGovernedError: If the tool is unregistered, or registered
                under a different definition digest.
        """
        bundle = self._active_bundle(tenant_id)
        key = (tenant_id, tool_name)
        with self._lock:
            quarantined = key in self._quarantined
        if quarantined:
            raise ToolQuarantinedError(
                "tool definition changed and is pending re-approval",
                tenant_id=tenant_id,
                tool_name=tool_name,
            )
        definition = bundle.resolve(tool_name)
        if definition is None:
            raise ToolNotGovernedError(
                "tool is not in the governed registry",
                tenant_id=tenant_id,
                tool_name=tool_name,
                bundle_id=bundle.bundle_id,
            )
        presented = definition_sha256.lower()
        if definition.definition_sha256 != presented:
            raise ToolNotGovernedError(
                "tool definition digest does not match the registered one",
                tenant_id=tenant_id,
                tool_name=tool_name,
                registered_digest=definition.definition_sha256,
                presented_digest=presented,
            )
        return definition

    def approve(self, tenant_id: str, tool_name: str, definition_sha256: str) -> None:
        """Explicitly re-approve a quarantined tool at its new, reviewed digest."""
        with self._lock:
            key = (tenant_id, tool_name)
            self._quarantined.discard(key)
            self._approved_digests[key] = definition_sha256.lower()

    def is_quarantined(self, tenant_id: str, tool_name: str) -> bool:
        """Return whether ``tool_name`` is currently quarantined."""
        with self._lock:
            return (tenant_id, tool_name) in self._quarantined

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the SHA-256 digest of the bundle active for ``tenant_id``."""
        return self._active_bundle(tenant_id).digest()
        return self._active_bundle(tenant_id).digest()

    def _active_bundle(self, tenant_id: str) -> ToolRegistryBundle:
        with self._lock:
            if not self._available:
                raise ToolRegistryUnavailableError(
                    "tool registry is unreachable",
                    tenant_id=tenant_id,
                    adapter="InMemoryToolRegistry",
                )
            bundle = self._bundles.get(tenant_id)
        if bundle is None:
            raise ToolRegistryUnavailableError(
                "no active tool registry bundle for tenant",
                tenant_id=tenant_id,
                adapter="InMemoryToolRegistry",
            )
        return bundle

    def set_available(self, available: bool) -> None:
        """Simulate a registry outage, for fail-closed tests."""
        with self._lock:
            self._available = available


def build_tool_registry(config: GlassBoxConfig) -> ToolRegistry:
    """Factory used by the adapter set. No bundle is preloaded."""
    del config  # unused: the reference registry is populated by the caller
    return InMemoryToolRegistry()
