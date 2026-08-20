"""HTTP-layer admission control (Workstream B).

A coarse, per-process guard applied before identity verification or any
governance work. v1 ran the full 9-stage pipeline for every request that
reached the process, so a burst of traffic -- authenticated or not -- paid
the full mandate/policy/risk/limits/baseline cost before anything could
reject it. This module is a cheap first gate: reject once a client key has
exceeded its budget within a sliding window, before any of that work starts.

Deliberately not a replacement for :class:`~glassbox.ports.limits.LimitStore`,
which governs verified-identity actions *after* the pipeline runs and is
distributed across replicas. This guard is intentionally per-process and
in-memory -- it protects one replica's own CPU/IO budget, the same way a
reverse proxy's local rate limiter would, and needs no external dependency to
do it.

Lives under ``adapters.inbound.http``, not ``glassbox.app``: it uses
``threading`` for its lock, and the application layer is held to a stricter
stdlib allowlist that excludes concurrency primitives (orchestration decides
*what* happens; adapters decide *how* -- ``tests/test_layering.py``).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Dict, List

from glassbox.ports.clock import Clock

__all__ = ["AdmissionVerdict", "HttpAdmissionController"]


@dataclass(frozen=True, slots=True)
class AdmissionVerdict:
    """Whether one request is admitted, and how long to wait if not."""

    admitted: bool
    retry_after_s: float = 0.0


class HttpAdmissionController:
    """A fixed-window request-rate guard, keyed by an arbitrary client key.

    Args:
        clock: The only source of "now" (invariant I6).
        max_requests: Requests permitted per ``client_key`` within ``window_seconds``.
        window_seconds: The sliding window over which requests are counted.
        max_tracked_clients: Upper bound on distinct client keys held in
            memory at once. Oldest-touched keys are evicted first, so an
            attacker cycling client keys (e.g. spoofed source addresses)
            cannot grow this structure without bound -- the in-process
            analogue of the distributed stores' own eviction policies.
    """

    __slots__ = (
        "_clock",
        "_max_requests",
        "_window_seconds",
        "_max_tracked_clients",
        "_lock",
        "_buckets",
    )

    def __init__(
        self,
        *,
        clock: Clock,
        max_requests: int,
        window_seconds: float,
        max_tracked_clients: int = 50_000,
    ) -> None:
        if max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        self._clock = clock
        self._max_requests = max_requests
        self._window_seconds = window_seconds
        self._max_tracked_clients = max_tracked_clients
        self._lock = threading.Lock()
        # Insertion order doubles as touch-recency order: a bucket is
        # re-inserted (moved to the end) on every access via dict re-set.
        self._buckets: Dict[str, List[float]] = {}

    def check(self, client_key: str) -> AdmissionVerdict:
        """Record one attempt for ``client_key`` and report whether it is admitted."""
        now = self._clock.now()
        with self._lock:
            timestamps = self._buckets.pop(client_key, [])
            cutoff = now - self._window_seconds
            timestamps = [ts for ts in timestamps if ts > cutoff]

            if len(timestamps) >= self._max_requests:
                self._buckets[client_key] = timestamps
                self._evict_if_needed()
                retry_after = max(timestamps[0] + self._window_seconds - now, 0.0)
                return AdmissionVerdict(admitted=False, retry_after_s=retry_after)

            timestamps.append(now)
            self._buckets[client_key] = timestamps
            self._evict_if_needed()
            return AdmissionVerdict(admitted=True)

    def _evict_if_needed(self) -> None:
        """Drop the oldest-touched buckets once the tracked-client cap is hit.

        Must be called with ``self._lock`` held.
        """
        while len(self._buckets) > self._max_tracked_clients:
            oldest_key = next(iter(self._buckets))
            del self._buckets[oldest_key]
