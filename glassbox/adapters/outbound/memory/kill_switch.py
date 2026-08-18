"""In-memory kill switch (GB-003, reference for GB-016).

**Development only.** State lives in one process and does not propagate to
other replicas -- the durable requirement (sub-second propagation across every
replica) is what a Redis-backed implementation must provide. What must be
preserved: fail closed when the switch state cannot be determined, and a
global stop always takes precedence over an unset tenant stop.
"""

from __future__ import annotations

import threading
from typing import Set

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import KillSwitchUnavailableError
from glassbox.ports.kill_switch import KillSwitch

__all__ = ["InMemoryKillSwitch", "build_kill_switch"]


class InMemoryKillSwitch:
    """Tenant and global emergency stops held in process memory."""

    __slots__ = ("_lock", "_tenants", "_global", "_available")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._tenants: Set[str] = set()
        self._global = False
        self._available = True

    def is_tenant_engaged(self, tenant_id: str) -> bool:
        """Return whether the emergency stop is engaged for ``tenant_id``."""
        self._require_available()
        with self._lock:
            return tenant_id in self._tenants

    def is_globally_engaged(self) -> bool:
        """Return whether the system-wide emergency stop is engaged."""
        self._require_available()
        with self._lock:
            return self._global

    def engage_tenant(self, tenant_id: str) -> None:
        """Trip the stop for one tenant."""
        with self._lock:
            self._tenants.add(tenant_id)

    def disengage_tenant(self, tenant_id: str) -> None:
        """Release the stop for one tenant."""
        with self._lock:
            self._tenants.discard(tenant_id)

    def engage_globally(self) -> None:
        """Trip the system-wide stop."""
        with self._lock:
            self._global = True

    def disengage_globally(self) -> None:
        """Release the system-wide stop."""
        with self._lock:
            self._global = False

    def _require_available(self) -> None:
        if not self._available:
            raise KillSwitchUnavailableError(
                "kill switch is unreachable", adapter="InMemoryKillSwitch"
            )

    def set_available(self, available: bool) -> None:
        """Simulate an outage, for fail-closed tests."""
        self._available = available


def build_kill_switch(config: GlassBoxConfig) -> KillSwitch:
    """Factory used by the adapter set. Both stops start disengaged."""
    del config  # unused: the reference switch starts in the disengaged state
    return InMemoryKillSwitch()
