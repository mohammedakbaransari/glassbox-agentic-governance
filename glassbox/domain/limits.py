"""Rate and volume limit value objects (GB-002, WS-5 foundation).

The v1 velocity breaker had four defects that these types are shaped to prevent:

* it stored counters in a process-local ``dict``, so N replicas enforced N times
  the configured limit -- :class:`LimitKey` is therefore a pure, canonical,
  externally-storable key with no object identity in it;
* its Redis script used the timestamp as both the sorted-set score *and* member,
  so two decisions in the same clock tick collapsed into one and the window
  undercounted -- :meth:`LimitKey.member_for` derives a collision-free member;
* its cooldown state was kept locally while counting happened in Redis, so the
  effective cooldown decayed to the window length -- :class:`LimitVerdict` carries
  ``cooldown_until`` so the store owns that state;
* it failed open when Redis was unavailable -- there is deliberately no
  "unavailable" verdict here. The store raises
  :class:`~glassbox.domain.errors.LimitStoreUnavailable` and the caller decides,
  by consequence class, whether to deny.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional

from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    require_identifier,
    require_non_negative,
    require_timestamp,
)

__all__ = [
    "LimitScope",
    "Window",
    "LimitKey",
    "LimitVerdict",
]


class LimitScope(Enum):
    """What a limit is counted against."""

    AGENT = "agent"
    TENANT = "tenant"
    RESOURCE = "resource"
    DELEGATING_SUBJECT = "delegating_subject"
    GLOBAL = "global"


@dataclass(frozen=True, slots=True)
class Window:
    """A fixed-length sliding time window, in whole seconds.

    Whole seconds only: sub-second windows invite clock-skew disputes between
    replicas, and no governance limit in practice needs finer granularity.
    """

    seconds: int

    def __post_init__(self) -> None:
        if isinstance(self.seconds, bool) or not isinstance(self.seconds, int):
            raise DomainValidationError(
                "window length must be an integer number of seconds",
                field="seconds",
                offending_type=type(self.seconds).__name__,
            )
        if self.seconds <= 0:
            raise DomainValidationError(
                "window length must be positive", field="seconds", value=self.seconds
            )
        if self.seconds > 31_536_000:
            raise DomainValidationError(
                "window length must not exceed one year",
                field="seconds",
                value=self.seconds,
            )

    def start_of(self, now: float) -> float:
        """Return the inclusive lower bound of the window ending at ``now``."""
        return require_timestamp(now, field="now") - float(self.seconds)

    @property
    def label(self) -> str:
        """Compact, stable label used inside canonical keys."""
        return f"{self.seconds}s"

    def __str__(self) -> str:
        return self.label


@dataclass(frozen=True, slots=True)
class LimitKey:
    """Canonical identity of one counter in the external limit store.

    The key is a pure function of its fields. It contains no object references
    and no process-local state, which is what allows every replica to agree on
    the same counter.

    Attributes:
        tenant_id: Owning tenant. Always present -- there are no global-only keys.
        scope: What the counter is grouped by.
        subject: The concrete value of that grouping, e.g. the agent ref.
        action: Action name the limit applies to, or ``"*"`` for all actions.
        window: Length of the sliding window.
    """

    tenant_id: str
    scope: LimitScope
    subject: str
    window: Window
    action: str = "*"

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if not isinstance(self.scope, LimitScope):
            raise DomainValidationError(
                "scope must be a LimitScope",
                field="scope",
                offending_type=type(self.scope).__name__,
            )
        object.__setattr__(self, "subject", require_identifier(self.subject, field="subject"))
        if not isinstance(self.window, Window):
            raise DomainValidationError(
                "window must be a Window",
                field="window",
                offending_type=type(self.window).__name__,
            )
        if self.action != "*":
            object.__setattr__(self, "action", require_identifier(self.action, field="action"))

    def canonical_key(self) -> str:
        """Return the stable string identity of this counter.

        Backend-agnostic by design: the Redis adapter prefixes it, a SQL adapter
        indexes on it, and tests compare it directly.
        """
        return "|".join(
            (
                "glassbox",
                "limit",
                self.tenant_id,
                self.scope.value,
                self.subject,
                self.action,
                self.window.label,
            )
        )

    @staticmethod
    def member_for(decision_id: str, now: float) -> str:
        """Return a collision-free sorted-set member for one admission.

        v1 used the timestamp alone as the member, so two decisions arriving in
        the same clock tick overwrote one another and the window undercounted.
        Binding the member to the decision id makes every admission distinct.
        """
        require_identifier(decision_id, field="decision_id")
        moment = require_timestamp(now, field="now")
        return f"{moment!r}:{decision_id}"

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in evidence."""
        return {
            "tenant_id": self.tenant_id,
            "scope": self.scope.value,
            "subject": self.subject,
            "action": self.action,
            "window_seconds": self.window.seconds,
            "key": self.canonical_key(),
        }

    def __str__(self) -> str:
        return self.canonical_key()


@dataclass(frozen=True, slots=True)
class LimitVerdict:
    """The atomic result of a check-and-consume against one counter.

    There is intentionally no ``UNKNOWN`` or ``UNAVAILABLE`` state. A store that
    cannot answer authoritatively raises, and the caller fails closed for any
    non-advisory action. Encoding "unavailable" as a verdict is what made the v1
    breaker admit everything during an outage.

    Attributes:
        admitted: Whether this decision was allowed to consume budget.
        key: The counter that was consulted.
        limit: Configured ceiling for the window.
        observed: Consumption after this call (including it, if admitted).
        evaluated_at: Epoch seconds at which the store evaluated the window.
        retry_after_s: Seconds until budget is expected to free up.
        cooldown_until: Epoch seconds until which the breaker stays tripped.
    """

    admitted: bool
    key: LimitKey
    limit: float
    observed: float
    evaluated_at: float
    retry_after_s: Optional[float] = None
    cooldown_until: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.admitted, bool):
            raise DomainValidationError(
                "admitted must be a bool",
                field="admitted",
                offending_type=type(self.admitted).__name__,
            )
        if not isinstance(self.key, LimitKey):
            raise DomainValidationError(
                "key must be a LimitKey", field="key", offending_type=type(self.key).__name__
            )
        object.__setattr__(self, "limit", require_non_negative(self.limit, field="limit"))
        object.__setattr__(self, "observed", require_non_negative(self.observed, field="observed"))
        object.__setattr__(
            self, "evaluated_at", require_timestamp(self.evaluated_at, field="evaluated_at")
        )
        if self.retry_after_s is not None:
            object.__setattr__(
                self,
                "retry_after_s",
                require_non_negative(self.retry_after_s, field="retry_after_s"),
            )
        if self.cooldown_until is not None:
            object.__setattr__(
                self,
                "cooldown_until",
                require_timestamp(self.cooldown_until, field="cooldown_until"),
            )
        if self.admitted and self.observed > self.limit:
            raise DomainValidationError(
                "an admitted verdict cannot report consumption above the limit",
                field="observed",
                observed=self.observed,
                limit=self.limit,
            )

    @property
    def remaining(self) -> float:
        """Budget left in the window; never negative."""
        return max(0.0, self.limit - self.observed)

    def is_in_cooldown(self, now: float) -> bool:
        """Return whether the breaker is still tripped at ``now``."""
        moment = require_timestamp(now, field="now")
        return self.cooldown_until is not None and moment < self.cooldown_until

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``limits_verdict`` payload."""
        return {
            "admitted": self.admitted,
            "key": dict(self.key.as_evidence()),
            "limit": self.limit,
            "observed": self.observed,
            "remaining": self.remaining,
            "evaluated_at": self.evaluated_at,
            "retry_after_s": self.retry_after_s,
            "cooldown_until": self.cooldown_until,
        }
