"""Kill switch port (GB-016).

A tenant-level or global emergency stop, orthogonal to any individual agent's
mandate. Checked on every non-advisory decision, before mandate evaluation:
an engaged switch denies regardless of what any agent's mandate or the active
policy bundle would otherwise permit.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["KillSwitch"]


@runtime_checkable
class KillSwitch(Protocol):
    """Resolves whether a tenant-level or global emergency stop is engaged.

    Conforming adapters must:

    * **propagate fast.** v1's mandate revocation had no equivalent at all; this
      port exists specifically so an operator's stop takes effect on the very
      next decision, not the next deployment.
    * **fail closed.** An unreachable switch must be treated as engaged by the
      caller for any non-advisory action -- see
      :class:`~glassbox.domain.errors.KillSwitchUnavailableError`.
    """

    def is_tenant_engaged(self, tenant_id: str) -> bool:
        """Return whether the emergency stop is engaged for ``tenant_id``.

        Raises:
            glassbox.domain.errors.KillSwitchUnavailableError: If the switch
                state cannot be determined.
        """
        ...

    def is_globally_engaged(self) -> bool:
        """Return whether the system-wide emergency stop is engaged.

        Raises:
            glassbox.domain.errors.KillSwitchUnavailableError: If the switch
                state cannot be determined.
        """
        ...
