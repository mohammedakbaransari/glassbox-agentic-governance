"""
GlassBox Framework — Bounded Queue with Backpressure (v1.1.0)
==============================================================
Prevents request accumulation and OOM under load spikes by:
  1. Bounded queue with configurable max size (default 10K requests)
  2. Admission control: reject new requests when queue full
  3. Fair queueing: FIFO with per-agent fairness
  4. Backpressure signals: indicate to client to retry later
  5. Metrics: queue depth, rejection rate, wait time histograms

Backpressure Scenario: 10K → 50K req/sec spike
  1. Pipeline processes ~1K req/sec (1000ms → 1 req each)
  2. Requests accumulate in queue (FIFO: 10K → 11K → 20K → ...)
  3. Memory grows unbounded (potential OOM)
  4. With backpressure: reject new requests after 10K queued
  5. Client gets 429 Backpressure status → implements retry-with-jitter
  6. Load spreads over time → queue drains → accepts new requests

Response Headers (Backpressure):
  - Retry-After: 2 seconds (directive to client)
  - X-Queue-Depth: 10234 / 10000 (current load)
  - X-Request-Wait-Time: 2.5s (mean wait time in queue)

Architecture:
  - Per-agent fairness queue (prevent one agent starving others)
  - Global backpressure threshold
  - Metrics: latency percentiles (p50/p95/p99)
  - Circuit breaker integration: skip backpressure if overloaded

Performance:
  - Queue operation: O(1) enqueue/dequeue (deque)
  - Membership check: O(1) set lookup
  - Fairness enforcement: O(agents) round-robin

Author: Mohammed Akbar Ansari
"""

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from glassbox.governance.logging_manager import get_logger
from glassbox.governance.models import DecisionRequest

log = get_logger("backpressure")


@dataclass
class QueuedRequest:
    """Request in bounded queue."""
    decision_id: str
    agent_id: str
    request: DecisionRequest
    enqueued_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    processed_at: Optional[datetime] = None

    def wait_time_ms(self) -> float:
        """Time spent in queue (milliseconds)."""
        end_time = self.processed_at or datetime.now(timezone.utc)
        delta = (end_time - self.enqueued_at).total_seconds()
        return delta * 1000


@dataclass
class BackpressureMetrics:
    """Request queueing and backpressure metrics."""
    total_enqueued: int = 0
    total_dequeued: int = 0
    total_rejected: int = 0
    current_queue_depth: int = 0
    peak_queue_depth: int = 0
    mean_wait_time_ms: float = 0.0
    p95_wait_time_ms: float = 0.0
    p99_wait_time_ms: float = 0.0
    rejection_rate_pct: float = 0.0

    def __repr__(self) -> str:
        return (
            f"BackpressureMetrics(depth={self.current_queue_depth}, "
            f"peak={self.peak_queue_depth}, rejections={self.total_rejected}, "
            f"mean_wait={self.mean_wait_time_ms:.2f}ms)"
        )


class BoundedQueue:
    """
    Thread-safe bounded queue with backpressure and fairness.

    Usage:
        bq = BoundedQueue(max_size=10_000, fairness_enabled=True)

        # Enqueue request
        is_accepted, wait_time = bq.try_enqueue(decision_id, agent_id, request)
        if not is_accepted:
            return HTTP 429 Backpressure

        # Dequeue for processing (blocking)
        queued_req = bq.dequeue_with_fairness(timeout_sec=60)
        # ... process ...
        queued_req.processed_at = datetime.now(timezone.utc)

        # Metrics
        print(bq.metrics())
    """

    def __init__(
        self,
        max_size: int = 10_000,
        fairness_enabled: bool = True,
        backpressure_threshold_pct: float = 0.9,  # Reject when 90% full
        rejection_strategy: str = "immediate",  # immediate | wait_jitter
    ):
        self.max_size = max_size
        self.fairness_enabled = fairness_enabled
        self.backpressure_threshold_pct = backpressure_threshold_pct
        self.rejection_strategy = rejection_strategy

        # Global depth counter — O(1) alternative to len(deque) across per-agent paths
        self._global_depth: int = 0

        # Per-agent queues (for fairness round-robin)
        self._agent_queues: Dict[str, deque] = {}

        # Thread safety
        self._lock = threading.RLock()
        self._not_empty = threading.Condition(self._lock)

        # Metrics tracking
        self._total_enqueued = 0
        self._total_dequeued = 0
        self._total_rejected = 0
        self._peak_depth = 0
        self._wait_times: deque = deque(maxlen=1000)  # Rolling window

        # Fairness: track which agent was last serviced
        self._last_agent_index = 0

        log.info(
            "BoundedQueue initialized: max_size=%d, fairness=%s, backpressure_threshold=%.0f%%",
            max_size, fairness_enabled, backpressure_threshold_pct * 100,
        )

    def try_enqueue(
        self,
        decision_id: str,
        agent_id: str,
        request: DecisionRequest,
    ) -> tuple[bool, Optional[float]]:
        """
        Attempt to enqueue request.

        Returns:
            (accepted: bool, estimated_wait_time_ms: Optional[float])
            - If accepted: (True, estimated_wait_ms)
            - If rejected: (False, None)
        """
        with self._lock:
            current_depth = self._global_depth

            # Check backpressure threshold
            threshold = int(self.max_size * self.backpressure_threshold_pct)
            if current_depth >= threshold:
                self._total_rejected += 1
                est_wait = self._estimate_wait_time(current_depth)

                log.warning(
                    "BoundedQueue: BACKPRESSURE triggered (depth=%d/%d, "
                    "rejections=%d, est_wait=%.1fms)",
                    current_depth, self.max_size, self._total_rejected, est_wait,
                )
                return (False, est_wait)

            # Check hard max (safety valve)
            if current_depth >= self.max_size:
                self._total_rejected += 1
                log.error(
                    "BoundedQueue: HARD MAX reached (depth=%d/%d)",
                    current_depth, self.max_size,
                )
                return (False, None)

            # Enqueue
            queued_req = QueuedRequest(
                decision_id=decision_id,
                agent_id=agent_id,
                request=request,
            )

            self._global_depth += 1
            self._total_enqueued += 1

            # Update peak depth
            if self._global_depth > self._peak_depth:
                self._peak_depth = self._global_depth

            # Update per-agent queue (for fairness)
            if self.fairness_enabled:
                if agent_id not in self._agent_queues:
                    self._agent_queues[agent_id] = deque()
                self._agent_queues[agent_id].append(queued_req)
            else:
                # Non-fairness path still needs a deque for dequeue_with_fairness fallback
                self._agent_queues.setdefault("_global", deque()).append(queued_req)

            # Signal waiting consumers
            self._not_empty.notify_all()

            est_wait = self._estimate_wait_time(self._global_depth)
            log.debug(
                "BoundedQueue: enqueued decision_id=%s, agent_id=%s, depth=%d/%d, "
                "est_wait=%.1fms",
                decision_id, agent_id, self._global_depth, self.max_size, est_wait,
            )

            return (True, est_wait)

    def dequeue_with_fairness(
        self,
        timeout_sec: float = 60.0,
    ) -> Optional[QueuedRequest]:
        """
        Dequeue next request with optional fairness (round-robin by agent).

        Returns:
            QueuedRequest if available within timeout, else None.
        """
        with self._not_empty:
            # Wait for queue to have items
            deadline = time.time() + timeout_sec
            while self._global_depth == 0:
                remaining = deadline - time.time()
                if remaining <= 0:
                    log.debug("BoundedQueue: dequeue timeout after %.1fs", timeout_sec)
                    return None
                self._not_empty.wait(timeout=remaining)

            queued_req = self._dequeue_fair()
            if queued_req is None:
                return None

            self._global_depth -= 1
            self._total_dequeued += 1

            # Mark processing start
            queued_req.processed_at = datetime.now(timezone.utc)
            wait_time = queued_req.wait_time_ms()
            self._wait_times.append(wait_time)

            log.debug(
                "BoundedQueue: dequeued decision_id=%s, agent_id=%s, wait_time=%.1fms, "
                "depth=%d",
                queued_req.decision_id, queued_req.agent_id, wait_time,
                self._global_depth,
            )

            return queued_req

    def _dequeue_fair(self) -> Optional[QueuedRequest]:
        """Round-robin dequeue by agent (prevents starvation). All operations O(1)."""
        if not self._agent_queues:
            return None

        agent_ids = list(self._agent_queues.keys())
        attempts = 0

        while attempts < len(agent_ids):
            self._last_agent_index = (self._last_agent_index + 1) % len(agent_ids)
            agent_id = agent_ids[self._last_agent_index]

            agent_q = self._agent_queues[agent_id]
            if agent_q:
                queued_req = agent_q.popleft()   # O(1) — no global remove needed

                if not agent_q:
                    del self._agent_queues[agent_id]

                return queued_req

            attempts += 1

        return None

    def _estimate_wait_time(self, queue_depth: int) -> float:
        """
        Estimate time for request to be processed (milliseconds).
        Assumes ~1000ms per request (1 request/second throughput).
        """
        estimated_requests_ahead = queue_depth
        assumed_throughput = 1.0  # requests per second
        estimated_wait_sec = estimated_requests_ahead / assumed_throughput
        return estimated_wait_sec * 1000.0

    def depth(self) -> int:
        """Current queue depth."""
        with self._lock:
            return self._global_depth

    def metrics(self) -> BackpressureMetrics:
        """Return current metrics snapshot."""
        with self._lock:
            current_depth = self._global_depth

            # Compute wait time percentiles
            wait_times_list = sorted(self._wait_times)
            mean_wait = sum(wait_times_list) / len(wait_times_list) if wait_times_list else 0
            p95_wait = (
                wait_times_list[int(0.95 * len(wait_times_list))]
                if wait_times_list else 0
            )
            p99_wait = (
                wait_times_list[int(0.99 * len(wait_times_list))]
                if wait_times_list else 0
            )

            # Rejection rate
            total_attempts = self._total_enqueued + self._total_rejected
            rejection_rate = (
                (self._total_rejected / total_attempts * 100)
                if total_attempts > 0 else 0
            )

            return BackpressureMetrics(
                total_enqueued=self._total_enqueued,
                total_dequeued=self._total_dequeued,
                total_rejected=self._total_rejected,
                current_queue_depth=current_depth,
                peak_queue_depth=self._peak_depth,
                mean_wait_time_ms=mean_wait,
                p95_wait_time_ms=p95_wait,
                p99_wait_time_ms=p99_wait,
                rejection_rate_pct=rejection_rate,
            )

    def clear(self) -> None:
        """Clear all queued requests (destructive — for testing/reset)."""
        with self._lock:
            self._agent_queues.clear()
            self._global_depth = 0
            log.info("BoundedQueue: cleared all requests")

    def shutdown(self) -> None:
        """Graceful shutdown."""
        self.clear()
        log.info("BoundedQueue: shutdown complete")
