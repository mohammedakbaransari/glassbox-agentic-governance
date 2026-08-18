"""In-memory action catalogue and attestation provider (GB-003, reference for GB-010).

**Development only.** Both adapters hold state in one process: a catalogue
bundle loaded here does not survive a restart and is not shared across
replicas. What must be preserved by any durable replacement:

* **deny by default** -- an action or attestation absent from the loaded state
  is refused, never guessed permissively (invariant I4);
* an **unavailable** dependency (no bundle loaded, no system of record to ask)
  is handled identically to an *unfavourable* answer -- the caller cannot tell
  them apart and must not be able to, or a network partition becomes a bypass.
"""

from __future__ import annotations

import threading
from typing import Dict, Tuple

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.action import ResourceRef
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition
from glassbox.domain.errors import (
    ActionNotGovernedError,
    AttestationUnavailableError,
    CatalogueBundleUnavailableError,
)
from glassbox.ports.attestation import AttestationProvider
from glassbox.ports.catalogue import ActionCatalogue

__all__ = [
    "InMemoryActionCatalogue",
    "InMemoryAttestationProvider",
    "build_action_catalogue",
    "build_attestation_provider",
]


class InMemoryActionCatalogue:
    """Action definitions held in process memory, keyed by tenant.

    No bundle is loaded by default: every ``resolve`` call raises
    :class:`~glassbox.domain.errors.CatalogueBundleUnavailableError` until
    :meth:`load_bundle` is called, matching the "deny until proven governed"
    posture the port requires.
    """

    __slots__ = ("_lock", "_bundles", "_available")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._bundles: Dict[str, ActionCatalogueBundle] = {}
        self._available = True

    def load_bundle(self, bundle: ActionCatalogueBundle) -> None:
        """Register ``bundle`` as the active catalogue for its tenant."""
        with self._lock:
            self._bundles[bundle.tenant_id] = bundle

    def resolve(self, tenant_id: str, action: str) -> ActionDefinition:
        """Return the governed definition for ``action``.

        Raises:
            CatalogueBundleUnavailableError: If no bundle is loaded for
                ``tenant_id`` or the store is simulating an outage.
            ActionNotGovernedError: If the active bundle has no entry for
                ``action``.
        """
        bundle = self._active_bundle(tenant_id)
        definition = bundle.resolve(action)
        if definition is None:
            raise ActionNotGovernedError(
                "action is not in the governed catalogue",
                tenant_id=tenant_id,
                action=action,
                bundle_id=bundle.bundle_id,
            )
        return definition

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the SHA-256 digest of the bundle active for ``tenant_id``."""
        return self._active_bundle(tenant_id).digest()

    def _active_bundle(self, tenant_id: str) -> ActionCatalogueBundle:
        with self._lock:
            if not self._available:
                raise CatalogueBundleUnavailableError(
                    "action catalogue is unreachable",
                    tenant_id=tenant_id,
                    adapter="InMemoryActionCatalogue",
                )
            bundle = self._bundles.get(tenant_id)
        if bundle is None:
            raise CatalogueBundleUnavailableError(
                "no active catalogue bundle for tenant",
                tenant_id=tenant_id,
                adapter="InMemoryActionCatalogue",
            )
        return bundle

    def set_available(self, available: bool) -> None:
        """Simulate a store outage, for fail-closed tests."""
        with self._lock:
            self._available = available


class InMemoryAttestationProvider:
    """Attestation facts held in process memory, simulating a system of record.

    A fact that was never recorded is unresolvable, not ``False``: callers
    receive :class:`~glassbox.domain.errors.AttestationUnavailableError` rather
    than a permissive or a silently-wrong negative answer.
    """

    __slots__ = ("_lock", "_facts", "_available")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._facts: Dict[Tuple[str, str, str], bool] = {}
        self._available = True

    def record(self, tenant_id: str, resource: ResourceRef, name: str, value: bool) -> None:
        """Register the answer a system of record would give for this fact."""
        if not isinstance(resource, ResourceRef):
            raise TypeError("resource must be a ResourceRef")
        with self._lock:
            self._facts[(tenant_id, resource.qualified_name, name)] = bool(value)

    def resolve(self, tenant_id: str, resource: ResourceRef, name: str, *, now: float) -> bool:
        """Return the recorded answer for this attestation.

        Raises:
            AttestationUnavailableError: If the store is simulating an outage or
                no answer was ever recorded for this ``(tenant_id, resource,
                name)`` triple.
        """
        del now  # unused: the reference adapter has no time-varying state
        with self._lock:
            if not self._available:
                raise AttestationUnavailableError(
                    "attestation provider is unreachable",
                    tenant_id=tenant_id,
                    resource=resource.qualified_name,
                    name=name,
                )
            value = self._facts.get((tenant_id, resource.qualified_name, name))
        if value is None:
            raise AttestationUnavailableError(
                "attestation was never recorded by the system of record",
                tenant_id=tenant_id,
                resource=resource.qualified_name,
                name=name,
            )
        return value

    def set_available(self, available: bool) -> None:
        """Simulate a store outage, for fail-closed tests."""
        with self._lock:
            self._available = available


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


def build_action_catalogue(config: GlassBoxConfig) -> ActionCatalogue:
    """Factory used by the adapter set. No bundle is preloaded."""
    del config  # unused: the reference catalogue is populated by the caller
    return InMemoryActionCatalogue()


def build_attestation_provider(config: GlassBoxConfig) -> AttestationProvider:
    """Factory used by the adapter set. No facts are preloaded."""
    del config  # unused: the reference provider is populated by the caller
    return InMemoryAttestationProvider()
