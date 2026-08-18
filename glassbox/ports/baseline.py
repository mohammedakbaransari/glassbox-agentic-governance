"""Behavioural baseline port (GB-002, WS-5).

Replaces v1's ``AnomalyDetector``, which had four defects this port is shaped to
prevent:

* unbounded per-agent state -- 20,000 agents produced 20,000 permanently retained
  stat objects, so :class:`BaselineKey` is designed for TTL-based external
  storage rather than a process dict;
* a cold-start bypass -- the first ten observations for a new agent were never
  flagged, so an attacker only needed a fresh agent id.
  :attr:`BaselineVerdict.used_peer_prior` makes the peer-group fallback explicit
  and auditable instead of silently skipping detection;
* divergent statistics -- the Redis path used exponential forgetting while the
  local path used a sliding window, so the two disagreed about what was
  anomalous. One documented model, one shared conformance suite;
* a latch -- once a Redis error occurred the store degraded to local mode
  permanently. Adapters must implement a circuit breaker that recovers.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Protocol, Sequence, runtime_checkable

from glassbox.domain.errors import DomainValidationError
from glassbox.domain.limits import Window
from glassbox.domain.serialization import (
    require_finite,
    require_identifier,
    require_non_negative,
    require_timestamp,
)

__all__ = ["BaselineScope", "BaselineKey", "Baseline", "BaselineVerdict", "BaselineStore"]


class BaselineScope(Enum):
    """What population a baseline describes."""

    AGENT = "agent"
    #: Aggregate over comparable agents; used as the cold-start prior.
    PEER_GROUP = "peer_group"
    TENANT = "tenant"


@dataclass(frozen=True, slots=True)
class BaselineKey:
    """Identity of one behavioural distribution in the external store."""

    tenant_id: str
    scope: BaselineScope
    subject: str
    metric: str
    window: Window

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if not isinstance(self.scope, BaselineScope):
            raise DomainValidationError(
                "scope must be a BaselineScope",
                field="scope",
                offending_type=type(self.scope).__name__,
            )
        object.__setattr__(self, "subject", require_identifier(self.subject, field="subject"))
        object.__setattr__(self, "metric", require_identifier(self.metric, field="metric"))
        if not isinstance(self.window, Window):
            raise DomainValidationError(
                "window must be a Window",
                field="window",
                offending_type=type(self.window).__name__,
            )

    def canonical_key(self) -> str:
        """Return the stable, backend-agnostic string identity."""
        return "|".join(
            (
                "glassbox",
                "baseline",
                self.tenant_id,
                self.scope.value,
                self.subject,
                self.metric,
                self.window.label,
            )
        )

    def peer_group_fallback(self, peer_group: str) -> "BaselineKey":
        """Return the peer-group key used when this subject has too little history."""
        return BaselineKey(
            tenant_id=self.tenant_id,
            scope=BaselineScope.PEER_GROUP,
            subject=peer_group,
            metric=self.metric,
            window=self.window,
        )

    def __str__(self) -> str:
        return self.canonical_key()


@dataclass(frozen=True, slots=True)
class Baseline:
    """A summarised behavioural distribution.

    Attributes:
        key: The distribution's identity.
        sample_count: Observations behind the summary.
        mean: Arithmetic mean of the metric.
        stddev: Standard deviation; ``0.0`` for a constant series.
        updated_at: Epoch seconds of the most recent observation.
    """

    key: BaselineKey
    sample_count: int
    mean: float
    stddev: float
    updated_at: float

    def __post_init__(self) -> None:
        if not isinstance(self.key, BaselineKey):
            raise DomainValidationError(
                "key must be a BaselineKey", field="key", offending_type=type(self.key).__name__
            )
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int):
            raise DomainValidationError(
                "sample_count must be an integer",
                field="sample_count",
                offending_type=type(self.sample_count).__name__,
            )
        if self.sample_count < 0:
            raise DomainValidationError(
                "sample_count must not be negative",
                field="sample_count",
                value=self.sample_count,
            )
        object.__setattr__(self, "mean", require_finite(self.mean, field="mean"))
        object.__setattr__(self, "stddev", require_non_negative(self.stddev, field="stddev"))
        object.__setattr__(
            self, "updated_at", require_timestamp(self.updated_at, field="updated_at")
        )

    def z_score(self, observation: float) -> float:
        """Return the standardised deviation of ``observation`` from the mean.

        A zero standard deviation yields ``0.0`` for an exact match and
        ``float('inf')`` otherwise, rather than raising: a constant series that
        suddenly changes is maximally anomalous, and silently returning ``0.0``
        would be the wrong answer.
        """
        value = require_finite(observation, field="observation")
        if self.stddev == 0.0:
            return 0.0 if value == self.mean else float("inf")
        return (value - self.mean) / self.stddev

    @classmethod
    def summarise(cls, key: "BaselineKey", samples: Sequence[float], *, now: float) -> "Baseline":
        """Build a :class:`Baseline` from raw samples.

        The **one** statistical model every :class:`BaselineStore` adapter must
        share (GB-022): a plain mean and population standard deviation over
        whatever samples the adapter retains. Both the in-memory reference and
        the Redis adapter call this same function, so "one documented model" is
        guaranteed by construction rather than by two implementations agreeing
        by convention -- which is exactly where v1 diverged (a sliding window
        locally, exponential forgetting in Redis).

        Args:
            samples: At least one raw observation. Order does not matter.
        """
        count = len(samples)
        mean = sum(samples) / count
        variance = sum((value - mean) ** 2 for value in samples) / count
        return cls(
            key=key, sample_count=count, mean=mean, stddev=math.sqrt(variance), updated_at=now
        )


@dataclass(frozen=True, slots=True)
class BaselineVerdict:
    """The result of comparing an observation against a baseline."""

    anomalous: bool
    key: BaselineKey
    observation: float
    z_score: float
    threshold: float
    sample_count: int
    #: True when the subject had insufficient history and a peer-group prior was
    #: used instead. Never silently skip detection.
    used_peer_prior: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.anomalous, bool):
            raise DomainValidationError(
                "anomalous must be a bool",
                field="anomalous",
                offending_type=type(self.anomalous).__name__,
            )
        if not isinstance(self.key, BaselineKey):
            raise DomainValidationError(
                "key must be a BaselineKey", field="key", offending_type=type(self.key).__name__
            )
        object.__setattr__(
            self, "observation", require_finite(self.observation, field="observation")
        )
        object.__setattr__(
            self, "threshold", require_non_negative(self.threshold, field="threshold")
        )
        if isinstance(self.sample_count, bool) or not isinstance(self.sample_count, int):
            raise DomainValidationError(
                "sample_count must be an integer",
                field="sample_count",
                offending_type=type(self.sample_count).__name__,
            )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``baseline_verdict`` payload."""
        return {
            "anomalous": self.anomalous,
            "key": self.key.canonical_key(),
            "observation": self.observation,
            "z_score": self.z_score if self.z_score != float("inf") else None,
            "threshold": self.threshold,
            "sample_count": self.sample_count,
            "used_peer_prior": self.used_peer_prior,
        }


@runtime_checkable
class BaselineStore(Protocol):
    """Externally-held behavioural baselines with a single statistical model."""

    def get(self, key: BaselineKey, *, now: float) -> Optional[Baseline]:
        """Return the current baseline, or ``None`` when none exists yet.

        Raises:
            glassbox.domain.errors.BaselineStoreUnavailable: If unreachable.
        """
        ...

    def evaluate(
        self,
        key: BaselineKey,
        observation: float,
        *,
        peer_group: str,
        threshold: float,
        now: float,
    ) -> BaselineVerdict:
        """Compare ``observation`` against the baseline, or a peer-group prior.

        Implementations must fall back to ``key.peer_group_fallback(peer_group)``
        when the subject has too few samples, and set ``used_peer_prior``. They
        must never return ``anomalous=False`` merely because history is short.

        Raises:
            glassbox.domain.errors.BaselineStoreUnavailable: If unreachable.
                Callers fail closed for non-advisory actions.
        """
        ...

    def observe(self, key: BaselineKey, observation: float, *, now: float) -> None:
        """Record an observation, updating the distribution.

        Implementations must bound retention with a TTL derived from
        ``key.window`` so that per-agent state cannot grow without limit.

        Raises:
            glassbox.domain.errors.BaselineStoreUnavailable: If unreachable.
        """
        ...
