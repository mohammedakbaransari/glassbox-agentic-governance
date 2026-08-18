"""In-memory governance state (GB-003, reference for GB-011, GB-015, GB-022).

**Development only.** State lives in one process, so N replicas enforce N times
the configured limit. That is exactly the v1 defect, and it is why the production
profile requires ``limits.url`` and ``baseline.url`` and refuses this adapter set.

What the implementations here get *right*, and what the Redis adapters must
preserve:

* check-and-consume is **atomic** (one lock, one critical section) -- a read
  followed by a separate write admits more than the limit under concurrency;
* every admission gets a **collision-free member**, so two decisions in the same
  clock tick are counted twice. v1 used the timestamp as both the sorted-set
  score and its member, so they collapsed into one and the window undercounted;
* **cooldown lives in the store**, not in the caller. v1 kept the tripped flag
  process-locally while counting in Redis, so the effective cooldown decayed to
  the window length;
* an unavailable store **raises**; there is no permissive verdict;
* per-subject state is **bounded** and evicted. v1 retained 20,000 stat objects
  for 20,000 agents and never released one;
* a subject with too little history falls back to a **peer-group prior** instead
  of skipping detection -- v1's first ten observations were never flagged, so an
  attacker only needed a fresh agent id.
"""

from __future__ import annotations

import threading
from collections import OrderedDict, deque
from typing import Deque, Dict, List, Optional, Tuple

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import BaselineStoreUnavailable, LimitStoreUnavailable, MandateError
from glassbox.domain.limits import LimitKey, LimitVerdict, Window
from glassbox.domain.mandate import Mandate
from glassbox.ports.baseline import (
    Baseline,
    BaselineKey,
    BaselineStore,
    BaselineVerdict,
)
from glassbox.ports.limits import LimitStore
from glassbox.ports.mandate import MandateStore

__all__ = [
    "InMemoryLimitStore",
    "InMemoryBaselineStore",
    "InMemoryMandateStore",
    "build_limit_store",
    "build_baseline_store",
    "build_mandate_store",
]

#: Hard ceiling on tracked subjects. Cardinality is attacker-controlled -- an
#: agent id is whatever the caller says it is -- so it must be bounded.
DEFAULT_MAX_SUBJECTS = 10_000


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


class InMemoryLimitStore:
    """Sliding-window counters with an in-store cooldown.

    Args:
        limits: ``canonical_key -> ceiling``. A key with no configured ceiling
            uses ``default_limit``.
        default_limit: Ceiling applied to unconfigured keys.
        cooldown_seconds: How long a breaker stays tripped after a rejection.
        max_subjects: Eviction ceiling on tracked windows.
    """

    __slots__ = (
        "_lock",
        "_windows",
        "_cooldowns",
        "_limits",
        "_default_limit",
        "_cooldown_seconds",
        "_max_subjects",
        "_available",
    )

    def __init__(
        self,
        limits: Optional[Dict[str, float]] = None,
        *,
        default_limit: float = 100.0,
        cooldown_seconds: float = 300.0,
        max_subjects: int = DEFAULT_MAX_SUBJECTS,
    ) -> None:
        self._lock = threading.RLock()
        self._windows: "OrderedDict[str, List[Tuple[float, str, float]]]" = OrderedDict()
        self._cooldowns: Dict[str, float] = {}
        self._limits = dict(limits or {})
        self._default_limit = default_limit
        self._cooldown_seconds = cooldown_seconds
        self._max_subjects = max_subjects
        self._available = True

    def try_consume(
        self, key: LimitKey, *, cost: float, decision_id: str, now: float
    ) -> LimitVerdict:
        """Atomically consume ``cost`` from the counter if budget remains.

        Raises:
            LimitStoreUnavailable: If the store is marked unavailable. Callers
                must deny for any non-advisory action.
        """
        self._require_available(key)
        canonical = key.canonical_key()
        member = LimitKey.member_for(decision_id, now)
        ceiling = self._limits.get(canonical, self._default_limit)

        with self._lock:
            cooldown_until = self._cooldowns.get(canonical)
            if cooldown_until is not None and now < cooldown_until:
                return LimitVerdict(
                    admitted=False,
                    key=key,
                    limit=ceiling,
                    observed=self._observed(canonical, key.window, now),
                    evaluated_at=now,
                    retry_after_s=cooldown_until - now,
                    cooldown_until=cooldown_until,
                )
            if cooldown_until is not None:
                del self._cooldowns[canonical]

            entries = self._windows.get(canonical)
            if entries is None:
                entries = []
                self._windows[canonical] = entries
                self._evict_if_needed()
            self._windows.move_to_end(canonical)

            horizon = key.window.start_of(now)
            entries[:] = [entry for entry in entries if entry[0] > horizon]
            # A repeated decision_id is the same admission, not a new one.
            if any(existing_member == member for _, existing_member, _ in entries):
                return LimitVerdict(
                    admitted=True,
                    key=key,
                    limit=ceiling,
                    observed=sum(entry[2] for entry in entries),
                    evaluated_at=now,
                )

            observed = sum(entry[2] for entry in entries)
            if observed + cost > ceiling:
                cooldown_until = now + self._cooldown_seconds
                self._cooldowns[canonical] = cooldown_until
                return LimitVerdict(
                    admitted=False,
                    key=key,
                    limit=ceiling,
                    observed=observed + cost,
                    evaluated_at=now,
                    retry_after_s=self._cooldown_seconds,
                    cooldown_until=cooldown_until,
                )

            entries.append((now, member, cost))
            return LimitVerdict(
                admitted=True,
                key=key,
                limit=ceiling,
                observed=observed + cost,
                evaluated_at=now,
            )

    def cumulative(self, key: LimitKey, window: Window, *, now: float) -> float:
        """Return consumption within ``window`` without consuming budget."""
        self._require_available(key)
        with self._lock:
            return self._observed(key.canonical_key(), window, now)

    def release(self, key: LimitKey, *, decision_id: str) -> None:
        """Return budget consumed by a decision that never took effect."""
        self._require_available(key)
        canonical = key.canonical_key()
        with self._lock:
            entries = self._windows.get(canonical)
            if not entries:
                return
            suffix = f":{decision_id}"
            entries[:] = [entry for entry in entries if not entry[1].endswith(suffix)]

    # ----------------------------------------------------------------- #
    # Internals and test hooks
    # ----------------------------------------------------------------- #

    def _observed(self, canonical: str, window: Window, now: float) -> float:
        horizon = window.start_of(now)
        return sum(
            cost for timestamp, _, cost in self._windows.get(canonical, ()) if timestamp > horizon
        )

    def _evict_if_needed(self) -> None:
        while len(self._windows) > self._max_subjects:
            evicted, _ = self._windows.popitem(last=False)
            self._cooldowns.pop(evicted, None)

    def _require_available(self, key: LimitKey) -> None:
        if not self._available:
            raise LimitStoreUnavailable(
                "limit store is unreachable",
                key=key.canonical_key(),
                adapter="InMemoryLimitStore",
            )

    def configure(self, key: LimitKey, ceiling: float) -> None:
        """Set the ceiling for one counter."""
        with self._lock:
            self._limits[key.canonical_key()] = ceiling

    def set_available(self, available: bool) -> None:
        """Simulate a store outage, for fail-closed tests."""
        self._available = available

    @property
    def tracked_subjects(self) -> int:
        """Number of counters currently held, for memory-bound assertions."""
        with self._lock:
            return len(self._windows)


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


class InMemoryBaselineStore:
    """Sliding-window behavioural baselines with a peer-group cold-start prior.

    One statistical model -- a sliding window of the most recent observations --
    is used for both the subject and the peer group. v1 used a sliding window
    locally and exponential forgetting in Redis, so the two disagreed about what
    was anomalous depending on where the code ran.
    """

    __slots__ = (
        "_lock",
        "_observations",
        "_min_samples",
        "_max_samples",
        "_max_subjects",
        "_available",
    )

    def __init__(
        self,
        *,
        min_samples: int = 30,
        max_samples: int = 512,
        max_subjects: int = DEFAULT_MAX_SUBJECTS,
    ) -> None:
        self._lock = threading.RLock()
        self._observations: "OrderedDict[str, Deque[float]]" = OrderedDict()
        self._min_samples = min_samples
        self._max_samples = max_samples
        self._max_subjects = max_subjects
        self._available = True

    def get(self, key: BaselineKey, *, now: float) -> Optional[Baseline]:
        """Return the current baseline, or ``None`` when none exists yet."""
        self._require_available(key)
        with self._lock:
            samples = self._observations.get(key.canonical_key())
            if not samples:
                return None
            return self._summarise(key, samples, now)

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

        A subject with fewer than ``min_samples`` observations is scored against
        its peer group and the verdict records ``used_peer_prior``. Detection is
        never skipped: with no prior at all the observation is treated as
        anomalous, because the system cannot show that it is normal.
        """
        self._require_available(key)
        with self._lock:
            samples = self._observations.get(key.canonical_key())
            used_peer_prior = False
            effective_key = key

            if samples is None or len(samples) < self._min_samples:
                used_peer_prior = True
                effective_key = key.peer_group_fallback(peer_group)
                samples = self._observations.get(effective_key.canonical_key())

            if not samples:
                return BaselineVerdict(
                    anomalous=True,
                    key=effective_key,
                    observation=observation,
                    z_score=float("inf"),
                    threshold=threshold,
                    sample_count=0,
                    used_peer_prior=used_peer_prior,
                )

            baseline = self._summarise(effective_key, samples, now)
            z_score = baseline.z_score(observation)
            return BaselineVerdict(
                anomalous=abs(z_score) > threshold,
                key=effective_key,
                observation=observation,
                z_score=z_score,
                threshold=threshold,
                sample_count=baseline.sample_count,
                used_peer_prior=used_peer_prior,
            )

    def observe(self, key: BaselineKey, observation: float, *, now: float) -> None:
        """Record an observation, updating the distribution."""
        self._require_available(key)
        canonical = key.canonical_key()
        with self._lock:
            samples = self._observations.get(canonical)
            if samples is None:
                samples = deque(maxlen=self._max_samples)
                self._observations[canonical] = samples
                while len(self._observations) > self._max_subjects:
                    self._observations.popitem(last=False)
            self._observations.move_to_end(canonical)
            samples.append(float(observation))

    # ----------------------------------------------------------------- #
    # Internals and test hooks
    # ----------------------------------------------------------------- #

    @staticmethod
    def _summarise(key: BaselineKey, samples: "Deque[float]", now: float) -> Baseline:
        return Baseline.summarise(key, tuple(samples), now=now)

    def _require_available(self, key: BaselineKey) -> None:
        if not self._available:
            raise BaselineStoreUnavailable(
                "baseline store is unreachable",
                key=key.canonical_key(),
                adapter="InMemoryBaselineStore",
            )

    def set_available(self, available: bool) -> None:
        """Simulate a store outage, for fail-closed tests."""
        self._available = available

    @property
    def tracked_subjects(self) -> int:
        """Number of distributions currently held, for memory-bound assertions."""
        with self._lock:
            return len(self._observations)


# --------------------------------------------------------------------------- #
# Mandates
# --------------------------------------------------------------------------- #


class InMemoryMandateStore:
    """Mandates held in process memory, keyed by ``(tenant_id, agent_ref)``.

    Note the shape of :meth:`get`: ``tenant_id`` is positional and required.
    v1's ``SQLiteAuditRepository.query(tenant_id=None)`` omitted the tenant
    predicate entirely when the argument was left at its default and returned
    every tenant's rows.
    """

    __slots__ = ("_lock", "_mandates", "_revoked", "_available")

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._mandates: Dict[Tuple[str, str], Mandate] = {}
        self._revoked: set = set()
        self._available = True

    def get(self, tenant_id: str, agent_ref: str, *, now: float) -> Optional[Mandate]:
        """Return the active mandate for an agent, or ``None`` if there is none."""
        self._require_available(tenant_id, agent_ref)
        with self._lock:
            mandate = self._mandates.get((tenant_id, agent_ref))
        if mandate is None or not mandate.is_active_at(now):
            return None
        return mandate

    def is_revoked(self, tenant_id: str, agent_ref: str, *, now: float) -> bool:
        """Return whether the agent's authority has been revoked."""
        self._require_available(tenant_id, agent_ref)
        with self._lock:
            if (tenant_id, agent_ref) in self._revoked:
                return True
            mandate = self._mandates.get((tenant_id, agent_ref))
        return mandate is None or mandate.is_revoked_at(now)

    def put(self, mandate: Mandate) -> None:
        """Register or replace a mandate."""
        with self._lock:
            self._mandates[(mandate.tenant_id, mandate.agent_ref)] = mandate
            self._revoked.discard((mandate.tenant_id, mandate.agent_ref))

    def revoke(self, tenant_id: str, agent_ref: str) -> None:
        """Add the agent to the fast deny list (GB-016 kill switch)."""
        with self._lock:
            self._revoked.add((tenant_id, agent_ref))

    def _require_available(self, tenant_id: str, agent_ref: str) -> None:
        if not self._available:
            raise MandateError(
                "mandate store is unreachable",
                tenant_id=tenant_id,
                agent_ref=agent_ref,
                adapter="InMemoryMandateStore",
            )

    def set_available(self, available: bool) -> None:
        """Simulate a store outage, for fail-closed tests."""
        self._available = available


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


def build_limit_store(config: GlassBoxConfig) -> LimitStore:
    """Factory used by the adapter set."""
    return InMemoryLimitStore(cooldown_seconds=float(config.limits.cooldown_seconds))


def build_baseline_store(config: GlassBoxConfig) -> BaselineStore:
    """Factory used by the adapter set."""
    return InMemoryBaselineStore(min_samples=config.baseline.min_samples)


def build_mandate_store(config: GlassBoxConfig) -> MandateStore:
    """Factory used by the adapter set."""
    return InMemoryMandateStore()
