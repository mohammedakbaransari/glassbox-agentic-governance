"""Clock port (GB-002).

The only source of time in the rebuilt system.

v1 read ``datetime.now(timezone.utc).hour`` inside ``_procurement_factors``, so
the same decision replayed at a different hour produced a different risk score.
That makes an audit trail unreproducible, which makes it worthless. Routing every
time read through this port means a replay can pin the clock and obtain byte-
identical results (invariant I6).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["Clock"]


@runtime_checkable
class Clock(Protocol):
    """Supplies the current time to the decision path.

    Implementations must be monotonic in the weak sense that successive calls do
    not travel backwards by more than the platform's clock-adjustment tolerance.
    Adapters that need a strictly monotonic source for durations should expose it
    separately rather than lying here.
    """

    def now(self) -> float:
        """Return the current time as POSIX epoch seconds.

        Returns:
            A finite float in the range accepted by
            :func:`glassbox.domain.serialization.require_timestamp`.
        """
        ...
