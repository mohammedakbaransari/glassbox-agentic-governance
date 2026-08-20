"""Redis-backed distributed baseline store (GB-022).

Completes the piece GB-002's reference adapter marked ``dev_only``. Two v1
defects are fixed here, not carried forward:

* **Divergent statistics.** v1's Redis path used exponential forgetting while
  its local path used a sliding window, so the two disagreed about what was
  anomalous depending on where the code ran. This adapter and
  :class:`~glassbox.adapters.outbound.memory.governance_state.InMemoryBaselineStore`
  both retain a fixed-size window of the most recent observations and both
  summarise it with :meth:`~glassbox.ports.baseline.Baseline.summarise` -- the
  same function, not two implementations of the same idea.
* **A circuit-breaker latch.** v1's ``_store_ok`` flag, once tripped, never
  reset, so one transient Redis blip degraded the deployment permanently. The
  breaker here opens after consecutive failures and **half-opens** to probe
  recovery, exactly like the KMS signer's (GB-006).
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable, Optional

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import BaselineStoreUnavailable
from glassbox.ports.baseline import Baseline, BaselineKey, BaselineVerdict

__all__ = ["RedisBaselineStore", "build_baseline_store"]

#: Extra seconds of Redis-side TTL beyond nothing else -- a baseline key is
#: reclaimed if its subject stops being observed, the Redis analogue of the
#: in-memory adapter's subject eviction.
_DEFAULT_TTL_SECONDS = 30 * 86_400


class _CircuitBreaker:
    """Fails fast while Redis is down, and probes for recovery.

    A small, self-contained analogue of the KMS signer's breaker (GB-006):
    the same shape (consecutive-failure threshold, half-open probe), but
    raising :class:`BaselineStoreUnavailable` rather than a KMS-specific error,
    since this breaker is not shared code between the two adapters.
    """

    def __init__(
        self,
        *,
        failure_threshold: int = 5,
        reset_after_s: float = 10.0,
        monotonic: Optional[Callable[[], float]] = None,
    ) -> None:
        self._threshold = failure_threshold
        self._reset_after = reset_after_s
        self._monotonic = monotonic or time.monotonic
        self._lock = threading.RLock()
        self._failures = 0
        self._opened_at: Optional[float] = None

    def before_call(self) -> None:
        with self._lock:
            if self._opened_at is not None and not self._ready_to_probe():
                raise BaselineStoreUnavailable(
                    "baseline store circuit is open; failing closed",
                    consecutive_failures=self._failures,
                )

    def _ready_to_probe(self) -> bool:
        if self._opened_at is None:
            return True
        return self._monotonic() - self._opened_at >= self._reset_after

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._opened_at = None

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._threshold:
                self._opened_at = self._monotonic()


class RedisBaselineStore:
    """A :class:`~glassbox.ports.baseline.BaselineStore` shared across replicas.

    Args:
        client: A ``redis.Redis``-compatible client.
        min_samples: Observations required before a subject's own baseline is
            used instead of its peer-group prior.
        max_samples: Sliding-window size, identical in meaning to the in-memory
            reference's ``max_samples``.
        ttl_seconds: Redis-side expiry applied after every write.
        key_prefix: Namespaces every Redis key.
    """

    __slots__ = (
        "_client",
        "_min_samples",
        "_max_samples",
        "_ttl_seconds",
        "_key_prefix",
        "_breaker",
    )

    def __init__(
        self,
        client: Any,
        *,
        min_samples: int = 30,
        max_samples: int = 512,
        ttl_seconds: int = _DEFAULT_TTL_SECONDS,
        key_prefix: str = "",
    ) -> None:
        self._client = client
        self._min_samples = min_samples
        self._max_samples = max_samples
        self._ttl_seconds = ttl_seconds
        self._key_prefix = key_prefix
        self._breaker = _CircuitBreaker()

    def get(self, key: BaselineKey, *, now: float) -> Optional[Baseline]:
        """Return the current baseline, or ``None`` when none exists yet."""
        samples = self._read_samples(key)
        if not samples:
            return None
        return Baseline.summarise(key, samples, now=now)

    def evaluate(
        self,
        key: BaselineKey,
        observation: float,
        *,
        peer_group: str,
        threshold: float,
        now: float,
    ) -> BaselineVerdict:
        """Compare ``observation`` against the baseline, or a peer-group prior."""
        samples = self._read_samples(key)
        used_peer_prior = False
        effective_key = key

        if len(samples) < self._min_samples:
            used_peer_prior = True
            effective_key = key.peer_group_fallback(peer_group)
            samples = self._read_samples(effective_key)

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

        baseline = Baseline.summarise(effective_key, samples, now=now)
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
        del now  # unused: Redis TTL, not a stored timestamp, governs retention
        self._breaker.before_call()
        redis_key = self._redis_key(key)
        try:
            pipeline = self._client.pipeline()
            pipeline.lpush(redis_key, repr(float(observation)))
            pipeline.ltrim(redis_key, 0, self._max_samples - 1)
            pipeline.expire(redis_key, self._ttl_seconds)
            pipeline.execute()
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            self._breaker.record_failure()
            raise BaselineStoreUnavailable(
                "redis baseline store is unreachable", key=key.canonical_key()
            ) from exc
        self._breaker.record_success()

    def _read_samples(self, key: BaselineKey) -> "tuple[float, ...]":
        self._breaker.before_call()
        try:
            raw = self._client.lrange(self._redis_key(key), 0, -1)
        except Exception as exc:  # noqa: BLE001 -- any backend failure fails closed
            self._breaker.record_failure()
            raise BaselineStoreUnavailable(
                "redis baseline store is unreachable", key=key.canonical_key()
            ) from exc
        self._breaker.record_success()
        return tuple(float(value) for value in raw)

    def _redis_key(self, key: BaselineKey) -> str:
        """Use a tenant hash tag for cluster-safe fan-out across replicas.

        This preserves a single canonical identity for the baseline while
        guaranteeing that all keys for one tenant are co-located in Redis Cluster,
        preventing noisy-neighbor eviction across tenant namespaces.
        """
        return f"{self._key_prefix}{{{key.tenant_id}}}:{key.canonical_key()}"


def build_baseline_store(config: GlassBoxConfig) -> RedisBaselineStore:
    """Factory used by a durable adapter set.

    Connects through Redis Sentinel when ``config.baseline.sentinel_hosts`` is
    set, mirroring :func:`~glassbox.adapters.outbound.redis.limits.build_limit_store`
    -- the same opt-in HA mechanism, applied to the baseline store's connection.
    """
    import redis  # local import: `redis` is an optional extra

    baseline = config.baseline
    if baseline.sentinel_hosts:
        from redis.sentinel import Sentinel

        sentinel = Sentinel(
            list(baseline.sentinel_hosts),
            socket_timeout=baseline.sentinel_socket_timeout_s,
        )
        client = sentinel.master_for(baseline.sentinel_service_name, decode_responses=True)
    else:
        client = redis.Redis.from_url(baseline.url, decode_responses=True)
    return RedisBaselineStore(client, min_samples=config.baseline.min_samples)
