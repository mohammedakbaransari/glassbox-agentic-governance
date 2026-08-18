"""Clock adapters (GB-003).

The only place in the system permitted to read a wall clock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import List

from glassbox.app.config import GlassBoxConfig
from glassbox.ports.clock import Clock

__all__ = ["SystemClock", "FrozenClock", "build_clock"]


class SystemClock:
    """Reads the host wall clock.

    Production-safe. It is listed in the development adapter set only because the
    set must be complete; there is nothing dev-specific about it.
    """

    __slots__ = ()

    def now(self) -> float:
        """Return the current time as POSIX epoch seconds."""
        return time.time()


@dataclass
class FrozenClock:
    """A clock under test control.

    Used by replay (GB-012) and by tests that must pin ``now``. Determinism is an
    invariant, not a convenience: a decision that cannot be re-scored to the same
    value cannot be audited.
    """

    instant: float
    _reads: List[float] = field(default_factory=list, repr=False)

    def now(self) -> float:
        """Return the pinned instant, recording the read for assertions."""
        self._reads.append(self.instant)
        return self.instant

    def advance(self, seconds: float) -> float:
        """Move the clock forward and return the new instant."""
        self.instant += seconds
        return self.instant

    @property
    def read_count(self) -> int:
        """How many times the clock has been read."""
        return len(self._reads)


def build_clock(config: GlassBoxConfig) -> Clock:
    """Factory used by the adapter set."""
    return SystemClock()
