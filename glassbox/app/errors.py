"""Application-layer errors (GB-003).

Startup failures, not decision failures. They derive from
:class:`~glassbox.domain.errors.GlassBoxError` so that the composition root and
the decision path share one structured reporting shape, and so that a process
supervisor sees the same ``code``/``context`` contract everywhere.

All three are fatal by design. A misconfigured or partially wired governance
system must refuse to start rather than come up in a degraded state -- a
governance service that is running but not governing is the worst possible
outcome, because it manufactures exactly the false assurance the review found.
"""

from __future__ import annotations

from typing import Any, Sequence

from glassbox.domain.errors import GlassBoxError

__all__ = [
    "ConfigurationError",
    "CompositionError",
    "ProfileViolationError",
]


class ConfigurationError(GlassBoxError):
    """Configuration is missing, malformed, or unsafe for the selected profile.

    Carries **every** violation rather than the first, so an operator fixes the
    deployment in one pass instead of discovering problems one restart at a time.
    """

    code = "configuration_invalid"

    def __init__(self, message: str, /, *, violations: Sequence[str] = (), **context: Any) -> None:
        super().__init__(message, violations="; ".join(violations) or "none", **context)
        self.violations: tuple = tuple(violations)


class CompositionError(GlassBoxError):
    """A component could not be built, or does not satisfy its port.

    Raised at startup by :func:`glassbox.app.composition.build_runtime` when an
    adapter factory fails or returns an object that does not conform to the
    protocol it was registered against.
    """

    code = "composition_failed"

    def __init__(self, message: str, /, *, failures: Sequence[str] = (), **context: Any) -> None:
        super().__init__(message, failures="; ".join(failures) or "none", **context)
        self.failures: tuple = tuple(failures)


class ProfileViolationError(ConfigurationError):
    """A development-only component or unsafe switch was used in production.

    This is the guard that stops an in-memory evidence store, an unkeyed signer
    or a fail-open limit store from reaching a production deployment by way of a
    stale environment variable.
    """

    code = "profile_violation"
