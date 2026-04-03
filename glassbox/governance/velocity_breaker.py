"""
GlassBox Framework — Velocity Circuit Breaker  (v1.0.0)
Thread-safe sliding-window rate limiter per agent, plus an optional
ecosystem-level cross-agent aggregate breaker.

Per-agent breaker:   trips when one agent exceeds its individual limit.
Ecosystem breaker:   trips when ALL agents combined exceed the fleet limit.
                     This catches coordinated or cascading runaway conditions
                     that are invisible to per-agent checks alone.

Author: Mohammed Akbar Ansari
"""

import threading
import time
from collections import defaultdict, deque
from typing import Dict, Optional, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("velocity")


class VelocityBreaker:
    """
    Thread-safe sliding-window rate limiter.

    Per-agent lock: fine-grained locking per agent_id to maximise throughput
    under concurrent load while preventing race conditions.

    Ecosystem lock: a single shared lock for the global deque.
    """

    def __init__(
        self,
        max_decisions:    int  = 20,
        window_seconds:   int  = 60,
        cooldown_seconds: int  = 300,
        # Ecosystem / fleet-level breaker
        ecosystem_max:              Optional[int] = None,
        ecosystem_window_seconds:   int           = 60,
        ecosystem_cooldown_seconds: int           = 120,
    ):
        self.max_decisions    = max_decisions
        self.window_seconds   = window_seconds
        self.cooldown_seconds = cooldown_seconds

        # Ecosystem config
        self.ecosystem_max              = ecosystem_max
        self.ecosystem_window_seconds   = ecosystem_window_seconds
        self.ecosystem_cooldown_seconds = ecosystem_cooldown_seconds

        # Per-agent state
        self._windows: Dict[str, deque] = defaultdict(deque)
        self._tripped: Dict[str, float] = {}
        self._agent_locks: Dict[str, threading.Lock] = defaultdict(threading.Lock)
        self._agents_meta_lock = threading.Lock()  # protects _agent_locks creation

        # Ecosystem state
        self._ecosystem_timestamps: deque = deque()
        self._ecosystem_tripped:    Optional[float] = None
        self._ecosystem_lock = threading.Lock()

    # ── Per-agent logic ───────────────────────────────────────────────────────

    def _get_agent_lock(self, agent_id: str) -> threading.Lock:
        with self._agents_meta_lock:
            return self._agent_locks[agent_id]

    def _check_cooldown(self, agent_id: str, now: float) -> Optional[Tuple[bool, str, int]]:
        trip_time = self._tripped.get(agent_id)
        if trip_time is None:
            return None
        elapsed = now - trip_time
        if elapsed < self.cooldown_seconds:
            remaining = int(self.cooldown_seconds - elapsed)
            count = len(self._windows[agent_id])
            return (True,
                    f"Agent '{agent_id}' in cooldown for {remaining}s after velocity breach.",
                    count)
        # Cooldown expired — reset
        del self._tripped[agent_id]
        self._windows[agent_id].clear()
        return None

    def check(self, agent_id: str) -> Tuple[bool, Optional[str], int]:
        """
        Check velocity for a single agent.
        Returns (triggered, reason, window_count).
        Thread-safe: per-agent lock + ecosystem lock.
        """
        now = time.monotonic()

        # ── Per-agent check ───────────────────────────────────────────────
        with self._get_agent_lock(agent_id):
            cooldown_result = self._check_cooldown(agent_id, now)
            if cooldown_result:
                return cooldown_result

            window = self._windows[agent_id]
            cutoff = now - self.window_seconds
            while window and window[0] < cutoff:
                window.popleft()
            window.append(now)
            count = len(window)

            if count > self.max_decisions:
                self._tripped[agent_id] = now
                reason = (f"Agent '{agent_id}' submitted {count} decisions in "
                          f"{self.window_seconds}s (limit: {self.max_decisions}). "
                          f"Velocity circuit breaker tripped.")
                log.warning("VelocityBreaker tripped for agent=%s count=%d", agent_id, count)
                return True, reason, count

        # ── Ecosystem check ───────────────────────────────────────────────
        if self.ecosystem_max is not None:
            with self._ecosystem_lock:
                # Check ecosystem cooldown
                if self._ecosystem_tripped is not None:
                    elapsed = now - self._ecosystem_tripped
                    if elapsed < self.ecosystem_cooldown_seconds:
                        remaining = int(self.ecosystem_cooldown_seconds - elapsed)
                        total = len(self._ecosystem_timestamps)
                        return (True,
                                f"Ecosystem velocity limit: fleet in cooldown for {remaining}s.",
                                total)
                    else:
                        self._ecosystem_tripped = None
                        self._ecosystem_timestamps.clear()

                # Prune and add
                eco_cutoff = now - self.ecosystem_window_seconds
                while self._ecosystem_timestamps and self._ecosystem_timestamps[0] < eco_cutoff:
                    self._ecosystem_timestamps.popleft()
                self._ecosystem_timestamps.append(now)
                eco_count = len(self._ecosystem_timestamps)

                if eco_count > self.ecosystem_max:
                    self._ecosystem_tripped = now
                    reason = (f"Ecosystem limit: {eco_count} decisions in "
                              f"{self.ecosystem_window_seconds}s across all agents "
                              f"(fleet limit: {self.ecosystem_max}).")
                    log.warning("EcosystemBreaker tripped: count=%d", eco_count)
                    return True, reason, eco_count

        return False, None, count

    def reset(self, agent_id: str):
        """Reset the per-agent breaker (e.g. after investigation)."""
        with self._get_agent_lock(agent_id):
            self._tripped.pop(agent_id, None)
            self._windows[agent_id].clear()

    def reset_ecosystem(self):
        """Reset the ecosystem breaker."""
        with self._ecosystem_lock:
            self._ecosystem_tripped = None
            self._ecosystem_timestamps.clear()

    def reset_all(self):
        """Reset all per-agent and ecosystem state."""
        with self._agents_meta_lock:
            agent_ids = list(self._agent_locks.keys())
        for aid in agent_ids:
            self.reset(aid)
        self.reset_ecosystem()

    def status(self, agent_id: str) -> Dict:
        now = time.monotonic()
        with self._get_agent_lock(agent_id):
            window = self._windows.get(agent_id, deque())
            recent = sum(1 for t in window if t >= now - self.window_seconds)
            tripped = agent_id in self._tripped
            return {
                "agent_id":            agent_id,
                "decisions_in_window": recent,
                "window_seconds":      self.window_seconds,
                "max_decisions":       self.max_decisions,
                "tripped":             tripped,
                "cooldown_remaining":  (
                    max(0, int(self.cooldown_seconds - (now - self._tripped[agent_id])))
                    if tripped else 0
                ),
            }

    def ecosystem_status(self) -> Dict:
        now = time.monotonic()
        with self._ecosystem_lock:
            eco_cutoff = now - self.ecosystem_window_seconds
            recent = sum(1 for t in self._ecosystem_timestamps if t >= eco_cutoff)
            tripped = self._ecosystem_tripped is not None
            return {
                "enabled":              self.ecosystem_max is not None,
                "decisions_in_window":  recent,
                "ecosystem_max":        self.ecosystem_max,
                "window_seconds":       self.ecosystem_window_seconds,
                "tripped":              tripped,
                "cooldown_remaining":   (
                    max(0, int(self.ecosystem_cooldown_seconds - (now - self._ecosystem_tripped)))
                    if tripped else 0
                ),
            }

    def all_agent_statuses(self) -> Dict[str, Dict]:
        with self._agents_meta_lock:
            agents = list(self._agent_locks.keys())
        return {a: self.status(a) for a in agents}
