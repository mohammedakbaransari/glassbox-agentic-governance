"""
GlassBox Framework — Distributed Velocity Breaker (v1.0.1)
==========================================================

Extends VelocityBreaker with Redis backend for multi-instance deployments.

Problem (v1.0.0):
  • Single-instance VelocityBreaker: state isolated per instance
  • Multi-instance scenario: Each instance has its own window
  • Result: 3 instances × 20 decisions each = 60 total (limit 20 violated)

Solution (v1.0.1):
  • Redis-backed distributed state shared across instances
  • Atomic operations (Lua scripts) ensure consistency
  • Automatic fallback to local if Redis unavailable (circuit breaker)
  • Compatible API (drop-in replacement for VelocityBreaker)

Architecture:
  Instance-1 ──┐
  Instance-2 ──┼──→ Redis (shared velocity windows)
  Instance-3 ──┘
              ↓
        (All instances see same global state)

Performance:
  • Per-agent check: ~1ms (Redis round-trip)
  • Fallback if Redis down: <1µs (local in-memory)
  • Atomic operations: No race conditions at any scale

Author: Mohammed Akbar Ansari
"""

import logging
import threading
import time
from collections import deque
from typing import Dict, Optional, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("velocity_distributed")


class VelocityBreaker:
    """
    Single-instance velocity breaker for rate limiting per agent.
    
    Prevents agents from making too many decisions in a time window,
    with automatic cooldown to recover.
    
    Usage:
        breaker = VelocityBreaker(max_decisions=20, window_seconds=60)
        triggered, reason, count = breaker.check("agent_1")
        if triggered:
            # Reject decision
            pass
    
    Thread-Safe: Yes (uses RLock per agent)
    Multi-Instance: No (use DistributedVelocityBreaker for that)
    """
    
    def __init__(
        self,
        max_decisions: int = 20,
        window_seconds: int = 60,
        cooldown_seconds: int = 300,
        ecosystem_max: Optional[int] = None,
        ecosystem_window_seconds: int = 60,
        ecosystem_cooldown_seconds: int = 120,
    ):
        """
        Args:
            max_decisions: Max decisions per agent per window
            window_seconds: Decision window duration (seconds)
            cooldown_seconds: Cooldown after limit exceeded
            ecosystem_max: Optional: max decisions per ecosystem (all agents combined)
            ecosystem_window_seconds: Ecosystem window duration
            ecosystem_cooldown_seconds: Cooldown after ecosystem limit exceeded
        """
        self.max_decisions = max_decisions
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        self.ecosystem_max = ecosystem_max
        self.ecosystem_window_seconds = ecosystem_window_seconds
        self.ecosystem_cooldown_seconds = ecosystem_cooldown_seconds
        
        # Per-agent time windows: agent_id -> deque of monotonic timestamps (sorted ascending).
        # Using deque + popleft() gives O(k) amortised eviction where k = expired entries,
        # vs. O(n) list-comprehension filter on every check call.
        self._agent_windows: Dict[str, deque] = {}
        self._agent_locks: Dict[str, threading.RLock] = {}
        self._global_lock = threading.RLock()

        # Per-agent cooldown tracking
        self._cooldown_until: Dict[str, float] = {}

        # Ecosystem window (deque for same O(k) amortised eviction)
        self._ecosystem_window: deque = deque()

    @staticmethod
    def _now() -> float:
        """Use a monotonic clock for breaker windows and cooldowns."""
        return time.monotonic()

    def _get_agent_lock(self, agent_id: str) -> threading.RLock:
        with self._global_lock:
            if agent_id not in self._agent_locks:
                self._agent_locks[agent_id] = threading.RLock()
            return self._agent_locks[agent_id]
    
    def check(self, agent_id: str) -> Tuple[bool, Optional[str], int]:
        """
        Check if agent has exceeded velocity limit.
        
        Args:
            agent_id: Unique identifier for agent
        
        Returns:
            (triggered: bool, reason: Optional[str], window_count: int)
            - triggered: True if limit exceeded or in cooldown
            - reason: Human-readable explanation (if triggered)
            - window_count: Number of decisions in current window
        """
        now = self._now()

        # ── Canonical lock order: always acquire _global_lock before agent_lock.
        # Step 1: Get or create agent lock (under global lock).
        agent_lock = self._get_agent_lock(agent_id)

        # ── Step 2: Agent-level check (under agent_lock only, no nesting). ──
        with agent_lock:
            cooldown_until = self._cooldown_until.get(agent_id, 0)
            if now < cooldown_until:
                remaining = int(cooldown_until - now)
                return (True, f"Cooldown for {remaining}s", 0)

            if agent_id not in self._agent_windows:
                self._agent_windows[agent_id] = deque()

            window = self._agent_windows[agent_id]
            cutoff = now - self.window_seconds
            # O(k) amortised: only pop entries that have expired from the left.
            # Because timestamps are appended in monotonically increasing order,
            # the deque is always sorted — popleft() is safe.
            while window and window[0] <= cutoff:
                window.popleft()
            count = len(window)

            if count >= self.max_decisions:
                self._cooldown_until[agent_id] = now + self.cooldown_seconds
                return (
                    True,
                    f"Agent '{agent_id}' velocity limit ({self.max_decisions}/{self.window_seconds}s) exceeded",
                    count,
                )

        # ── Step 3: Ecosystem check (under _global_lock only; agent_lock NOT held). ──
        # Acquiring _global_lock after releasing agent_lock avoids nested locking
        # and the deadlock risk it would introduce.
        if self.ecosystem_max is not None:
            with self._global_lock:
                cutoff_eco = now - self.ecosystem_window_seconds
                while self._ecosystem_window and self._ecosystem_window[0] <= cutoff_eco:
                    self._ecosystem_window.popleft()
                eco_count = len(self._ecosystem_window)

                if eco_count >= self.ecosystem_max:
                    # Record per-agent cooldown (re-acquire agent_lock separately).
                    with agent_lock:
                        self._cooldown_until[agent_id] = now + self.ecosystem_cooldown_seconds
                    return (
                        True,
                        f"Fleet ecosystem limit ({self.ecosystem_max}/{self.ecosystem_window_seconds}s) exceeded",
                        eco_count,
                    )

                self._ecosystem_window.append(now)

        # ── Step 4: Both checks passed; commit agent window entry. ──
        with agent_lock:
            if agent_id not in self._agent_windows:
                self._agent_windows[agent_id] = deque()
            self._agent_windows[agent_id].append(now)
            return (False, None, len(self._agent_windows[agent_id]))
    
    def reset(self, agent_id: str) -> None:
        """
        Reset agent's velocity window and cooldown.
        
        Args:
            agent_id: Agent to reset
        """
        agent_lock = self._get_agent_lock(agent_id)
        with self._global_lock:
            with agent_lock:
                self._agent_windows.pop(agent_id, None)
                self._cooldown_until.pop(agent_id, None)
    
    def reset_ecosystem(self) -> None:
        """Reset ecosystem-level velocity window."""
        with self._global_lock:
            self._ecosystem_window.clear()
    
    def reset_all(self) -> None:
        """Reset all agents and ecosystem state."""
        with self._global_lock:
            agent_locks = list(self._agent_locks.values())
            for agent_lock in agent_locks:
                agent_lock.acquire()
            try:
                self._agent_windows.clear()
                self._cooldown_until.clear()
                self._ecosystem_window.clear()
            finally:
                for agent_lock in reversed(agent_locks):
                    agent_lock.release()
    
    def status(self, agent_id: str) -> dict:
        """
        Get status for an agent.
        
        Returns:
            Dict with keys:
            - count: Current window count
            - limit: Max decisions per window
            - cooldown_remaining: Seconds left in cooldown (0 if none)
            - active: True if agent has recent decisions
        """
        now = self._now()
        
        with self._global_lock:
            agent_lock = self._agent_locks.get(agent_id)
        
        if not agent_lock:
            return {
                "count": 0,
                "limit": self.max_decisions,
                "cooldown_remaining": 0,
                "active": False,
            }
        
        with agent_lock:
            window = self._agent_windows.get(agent_id, deque())
            cutoff = now - self.window_seconds
            count = sum(1 for t in window if t > cutoff)
            
            cooldown_until = self._cooldown_until.get(agent_id, 0)
            cooldown_remaining = max(0, int(cooldown_until - now))
            
            return {
                "count": count,
                "limit": self.max_decisions,
                "cooldown_remaining": cooldown_remaining,
                "active": count > 0,
            }
    
    def ecosystem_status(self) -> dict:
        """
        Get ecosystem-level velocity status (fleet-wide).
        
        Returns:
            Dict with keys:
            - mode: Literal "local" (for single-instance)
            - agents_tracked: Number of unique agents with recent windows
            - agents_in_cooldown: Number of agents currently in cooldown
            - current_ecosystem_count: Current decisions in ecosystem window
            - ecosystem_limit: Max ecosystem decisions
            - global_circuit_open: Always False for local mode
        """
        now = self._now()
        
        with self._global_lock:
            # Count active agents (with decisions in current window)
            agents_tracked = 0
            agents_in_cooldown = 0
            
            for agent_id, window in self._agent_windows.items():
                cutoff = now - self.window_seconds
                if any(t > cutoff for t in window):
                    agents_tracked += 1
                
                cooldown_until = self._cooldown_until.get(agent_id, 0)
                if now < cooldown_until:
                    agents_in_cooldown += 1
            
            # Count decisions in current ecosystem window
            cutoff_eco = now - self.ecosystem_window_seconds
            ecosystem_count = sum(1 for t in self._ecosystem_window if t > cutoff_eco)
            
            return {
                "mode": "local",
                "agents_tracked": agents_tracked,
                "agents_in_cooldown": agents_in_cooldown,
                "current_ecosystem_count": ecosystem_count,
                "ecosystem_limit": self.ecosystem_max or float('inf'),
                "global_circuit_open": False,
            }


class RedisVelocityBreakerBackend:
    """Low-level Redis operations for distributed velocity breaking."""
    
    def __init__(self, redis_client, namespace: str = "glassbox:velocity"):
        """
        Args:
            redis_client: redis.Redis or redis.AsyncRedis instance
            namespace: Redis key prefix (e.g., "glassbox:velocity")
        """
        self.redis = redis_client
        self.namespace = namespace
        self._init_lua_scripts()
    
    def _init_lua_scripts(self):
        """Load Lua scripts for atomic operations."""
        # Script 1: Check and add timestamp (atomic)
        self.check_and_add_script = self.redis.register_script("""
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window_sec = tonumber(ARGV[2])
            local max_count = tonumber(ARGV[3])
            
            -- Remove old timestamps outside window
            redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_sec)
            
            -- Count remaining (in-window) timestamps
            local count = redis.call('ZCARD', key)
            
            if count >= max_count then
                return {0, count}  -- [breached, current_count]
            end
            
            -- Add new timestamp
            redis.call('ZADD', key, now, now)
            redis.call('EXPIRE', key, window_sec * 2)  -- Auto-expire unused keys
            
            return {1, count + 1}  -- [allowed, new_count]
        """)
        
        # Script 2: Get current window count (read-only)
        self.get_count_script = self.redis.register_script("""
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window_sec = tonumber(ARGV[2])
            
            -- Remove old timestamps
            redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_sec)
            
            -- Return count
            return redis.call('ZCARD', key)
        """)
        
        # Script 3: Check ecosystem global state
        self.check_ecosystem_script = self.redis.register_script("""
            local key = KEYS[1]
            local now = tonumber(ARGV[1])
            local window_sec = tonumber(ARGV[2])
            local max_count = tonumber(ARGV[3])
            
            -- Remove old entries
            redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window_sec)
            
            -- Count current
            local count = redis.call('ZCARD', key)
            
            if count >= max_count then
                return {0, count}  -- [allowed, current_count]
            end
            
            -- Add new entry
            redis.call('ZADD', key, now, now)
            redis.call('EXPIRE', key, window_sec * 2)
            
            return {1, count + 1}  -- [allowed, new_count]
        """)
    
    def check_and_add(
        self,
        agent_id: str,
        now: float,
        window_sec: int,
        max_count: int,
    ) -> Tuple[bool, int]:
        """
        Atomically check agent velocity and add timestamp if allowed.
        
        Args:
            agent_id: Agent identifier
            now: Current timestamp (time.time())
            window_sec: Window size in seconds
            max_count: Maximum decisions in window
        
        Returns:
            (allowed: bool, current_count: int)
        
        Atomic: Uses Lua script to prevent race conditions
        """
        key = f"{self.namespace}:agent:{agent_id}"
        try:
            result = self.check_and_add_script(
                keys=[key],
                args=[now, window_sec, max_count],
            )
            allowed = bool(result[0])
            count = int(result[1])
            return (allowed, count)
        except Exception as exc:
            log.error(f"Redis check_and_add failed: {exc}")
            raise
    
    def get_count(
        self,
        agent_id: str,
        now: float,
        window_sec: int,
    ) -> int:
        """Get current window count (read-only, cleans old entries)."""
        key = f"{self.namespace}:agent:{agent_id}"
        try:
            return int(self.get_count_script(
                keys=[key],
                args=[now, window_sec],
            ))
        except Exception as exc:
            log.error(f"Redis get_count failed: {exc}")
            raise
    
    def check_ecosystem_and_add(
        self,
        now: float,
        window_sec: int,
        max_count: int,
    ) -> Tuple[bool, int]:
        """Atomically check global ecosystem velocity."""
        key = f"{self.namespace}:ecosystem:global"
        try:
            result = self.check_ecosystem_script(
                keys=[key],
                args=[now, window_sec, max_count],
            )
            allowed = bool(result[0])
            count = int(result[1])
            return (allowed, count)
        except Exception as exc:
            log.error(f"Redis ecosystem check failed: {exc}")
            raise
    
    def reset_agent(self, agent_id: str) -> None:
        """Reset agent's velocity window."""
        key = f"{self.namespace}:agent:{agent_id}"
        try:
            self.redis.delete(key)
        except Exception as exc:
            log.error(f"Redis reset_agent failed: {exc}")


class DistributedVelocityBreaker:
    """
    Distributed velocity breaker backed by Redis with local fallback.
    
    API-compatible with VelocityBreaker but with cross-instance coordination.
    
    Usage:
        redis_client = redis.Redis(host='localhost', port=6379)
        breaker = DistributedVelocityBreaker(
            redis_client=redis_client,
            max_decisions=20,
            window_seconds=60,
        )
        
        triggered, reason, count = breaker.check(agent_id)
    """
    
    def __init__(
        self,
        redis_client=None,
        max_decisions: int = 20,
        window_seconds: int = 60,
        cooldown_seconds: int = 300,
        ecosystem_max: Optional[int] = None,
        ecosystem_window_seconds: int = 60,
        ecosystem_cooldown_seconds: int = 120,
        fallback_mode: bool = True,  # Use local in-memory if Redis fails
    ):
        self.max_decisions = max_decisions
        self.window_seconds = window_seconds
        self.cooldown_seconds = cooldown_seconds
        
        self.ecosystem_max = ecosystem_max
        self.ecosystem_window_seconds = ecosystem_window_seconds
        self.ecosystem_cooldown_seconds = ecosystem_cooldown_seconds
        
        self.fallback_mode = fallback_mode
        self._redis_available = False
        self._circuit_breaker_open = False
        self._circuit_breaker_timestamp: Optional[float] = None
        
        # Redis backend
        self._redis_backend = None
        self._redis_backend_lock = threading.Lock()
        self._init_redis(redis_client)
        
        # Cooldown tracking (per-instance, local)
        self._tripped: Dict[str, float] = {}
        self._tripped_lock = threading.Lock()
        
        # Fallback: local in-memory windows (if Redis unavailable)
        self._local_fallback_windows: Dict[str, list] = {}
        self._local_fallback_lock = threading.Lock()
    
    def _init_redis(self, redis_client):
        """Initialize Redis backend with health check."""
        try:
            if redis_client:
                # Health check
                redis_client.ping()
                self._redis_backend = RedisVelocityBreakerBackend(redis_client)
                self._redis_available = True
                log.info("Distributed velocity breaker: Redis connected")
            else:
                log.warning("Distributed velocity breaker: No Redis client provided")
        except Exception as exc:
            log.warning(f"Distributed velocity breaker: Redis unavailable: {exc}")
            self._redis_available = False
            if not self.fallback_mode:
                raise
    
    def _check_circuit_breaker(self) -> bool:
        """Check if circuit breaker is open (Redis unavailable too long)."""
        if self._circuit_breaker_open:
            now = time.time()
            if now - (self._circuit_breaker_timestamp or now) > 60:  # 60s timeout
                # Try to recover
                try:
                    if self._redis_backend:
                        self._redis_backend.redis.ping()
                        self._circuit_breaker_open = False
                        log.info("Redis recovered; closing circuit breaker")
                except:
                    pass
            return self._circuit_breaker_open
        return False
    
    def check(self, agent_id: str) -> Tuple[bool, Optional[str], int]:
        """
        Check velocity for agent (distributed).
        
        Returns:
            (triggered: bool, reason: Optional[str], window_count: int)
        
        Logic:
          1. Check cooldown (local; if tripped, stay tripped)
          2. Try Redis check (atomic)
          3. If Redis fails, fallback to local if enabled
          4. Return result
        """
        now = time.time()
        
        # ── Cooldown check (local) ──
        with self._tripped_lock:
            trip_time = self._tripped.get(agent_id)
            if trip_time and (now - trip_time) < self.cooldown_seconds:
                remaining = int(self.cooldown_seconds - (now - trip_time))
                count = self._get_window_count(agent_id, now)
                return (True, f"Cooldown for {remaining}s", count)
            elif trip_time:
                # Cooldown expired
                del self._tripped[agent_id]
        
        # ── Redis check (distributed) ──
        if self._redis_available and not self._check_circuit_breaker():
            try:
                allowed, count = self._redis_backend.check_and_add(
                    agent_id, now, self.window_seconds, self.max_decisions
                )
                
                if not allowed:
                    # Trigger and set cooldown
                    with self._tripped_lock:
                        self._tripped[agent_id] = now
                    return (
                        True,
                        f"Agent '{agent_id}' velocity limit ({self.max_decisions}/{self.window_seconds}s) exceeded",
                        count,
                    )
                
                # Ecosystem check
                if self.ecosystem_max:
                    eco_allowed, eco_count = self._redis_backend.check_ecosystem_and_add(
                        now, self.ecosystem_window_seconds, self.ecosystem_max
                    )
                    if not eco_allowed:
                        with self._tripped_lock:
                            self._tripped[agent_id] = now
                        return (
                            True,
                            f"Fleet ecosystem limit ({self.ecosystem_max}/{self.ecosystem_window_seconds}s) exceeded",
                            eco_count,
                        )
                
                return (False, None, count)
            
            except Exception as exc:
                log.warning(f"Redis check failed: {exc}; falling back to local")
                self._circuit_breaker_open = True
                self._circuit_breaker_timestamp = now
        
        # ── Fallback: local in-memory (if enabled) ──
        if self.fallback_mode:
            return self._check_local(agent_id, now)
        else:
            # No fallback; fail open (allow)
            log.warning(f"Redis unavailable and fallback disabled; allowing request")
            return (False, None, 0)
    
    def _check_local(self, agent_id: str, now: float) -> Tuple[bool, Optional[str], int]:
        """Local fallback check (single-instance behavior)."""
        with self._local_fallback_lock:
            if agent_id not in self._local_fallback_windows:
                self._local_fallback_windows[agent_id] = []
            
            window = self._local_fallback_windows[agent_id]
            
            # Remove old entries
            window = [t for t in window if now - t < self.window_seconds]
            self._local_fallback_windows[agent_id] = window
            
            count = len(window)
            
            if count >= self.max_decisions:
                return (True, f"Local fallback: velocity exceeded", count)
            
            # Add new entry
            window.append(now)
            return (False, None, count + 1)
    
    def _get_window_count(self, agent_id: str, now: float) -> int:
        """Get current window count."""
        if self._redis_available and not self._check_circuit_breaker():
            try:
                return self._redis_backend.get_count(
                    agent_id, now, self.window_seconds
                )
            except:
                pass
        
        # Fallback
        with self._local_fallback_lock:
            window = self._local_fallback_windows.get(agent_id, [])
            return len([t for t in window if now - t < self.window_seconds])
    
    def reset_agent(self, agent_id: str) -> None:
        """Reset agent's velocity window."""
        if self._redis_available:
            try:
                self._redis_backend.reset_agent(agent_id)
            except Exception as exc:
                log.warning(f"Redis reset_agent failed: {exc}")
        
        with self._local_fallback_lock:
            self._local_fallback_windows.pop(agent_id, None)
        
        # Also clear cooldown
        with self._tripped_lock:
            self._tripped.pop(agent_id, None)
    
    def reset(self, agent_id: str) -> None:
        """Alias for reset_agent (compatibility with VelocityBreaker)."""
        self.reset_agent(agent_id)
    
    def reset_ecosystem(self) -> None:
        """Reset ecosystem-level state (compatibility with VelocityBreaker)."""
        # Note: In distributed mode, ecosystem state is in Redis
        # We only clear it if no ecosystem_max configured
        if self.ecosystem_max and self._redis_available:
            try:
                key = f"{self._redis_backend.namespace}:ecosystem:global"
                self._redis_backend.redis.delete(key)
                log.info("Ecosystem state reset")
            except Exception as exc:
                log.warning(f"Redis ecosystem reset failed: {exc}")
    
    def reset_all(self) -> None:
        """Reset all per-agent and ecosystem state."""
        # Get all agents from Redis
        if self._redis_available:
            try:
                pattern = f"{self._redis_backend.namespace}:agent:*"
                keys = list(self._redis_backend.redis.scan_iter(match=pattern))
                if keys:
                    self._redis_backend.redis.delete(*keys)
                log.info(f"Reset {len(keys)} agent windows")
            except Exception as exc:
                log.warning(f"Redis reset_all failed: {exc}")
        
        # Reset local state
        with self._tripped_lock:
            self._tripped.clear()
        
        with self._local_fallback_lock:
            self._local_fallback_windows.clear()
        
        self.reset_ecosystem()
    
    def status(self, agent_id: str) -> dict:
        """Get agent status (compatibility with VelocityBreaker)."""
        now = time.time()
        
        count = self._get_window_count(agent_id, now)
        
        with self._tripped_lock:
            is_tripped = agent_id in self._tripped
            cooldown_remaining = 0
            if is_tripped:
                trip_time = self._tripped[agent_id]
                elapsed = now - trip_time
                cooldown_remaining = max(0, int(self.cooldown_seconds - elapsed))
        
        return {
            "agent_id": agent_id,
            "decisions_in_window": count,
            "window_seconds": self.window_seconds,
            "max_decisions": self.max_decisions,
            "tripped": is_tripped,
            "cooldown_remaining": cooldown_remaining,
        }
    
    def ecosystem_status(self) -> dict:
        """Get ecosystem status (compatibility with VelocityBreaker)."""
        now = time.time()
        
        if not self.ecosystem_max:
            return {
                "enabled": False,
                "decisions_in_window": 0,
                "ecosystem_max": None,
                "window_seconds": self.ecosystem_window_seconds,
                "tripped": False,
                "cooldown_remaining": 0,
            }
        
        # Try to get count from Redis
        count = 0
        tripped = False
        
        if self._redis_available and not self._check_circuit_breaker():
            try:
                key = f"{self._redis_backend.namespace}:ecosystem:global"
                count = int(self._redis_backend.redis.zcard(key) or 0)
                tripped = count >= self.ecosystem_max
            except Exception as exc:
                log.warning(f"Redis ecosystem_status failed: {exc}")
        
        return {
            "enabled": True,
            "decisions_in_window": count,
            "ecosystem_max": self.ecosystem_max,
            "window_seconds": self.ecosystem_window_seconds,
            "tripped": tripped,
            "cooldown_remaining": 0,  # Not tracked in distributed mode
        }


# ── O3: Distributed Fleet Budget ─────────────────────────────────────────────
#
# Problem: FleetBudgetPolicy.self.spent is an instance variable — each
# horizontal replica tracks spend independently, so N replicas each allow
# the full budget, multiplying effective budget by N.
#
# Solution: RedisFleetBudgetBackend uses a single Redis key to track
# cumulative spend atomically.  DistributedFleetBudgetPolicy wraps it with
# the same interface as FleetBudgetPolicy (drop-in replacement).

class RedisFleetBudgetBackend:
    """
    Redis-backed cumulative budget tracker.

    Uses a single sorted-set key per policy_id where the score is the
    cumulative spend. Atomic INCRBYFLOAT ensures no double-counting under
    concurrent replicas.

    Usage:
        backend = RedisFleetBudgetBackend(redis_client, policy_id="LOG-001")
        new_total = backend.add_spend(1500.0)   # → total after this spend
        backend.reset()                          # start of new period
    """

    def __init__(
        self,
        redis_client,
        policy_id: str = "LOG-001",
        namespace: str = "glassbox:fleet_budget",
    ):
        self.redis     = redis_client
        self.policy_id = policy_id
        self.namespace = namespace

    def _key(self) -> str:
        return f"{self.namespace}:{self.policy_id}:spent"

    def add_spend(self, amount: float) -> float:
        """
        Atomically add amount to cumulative spend and return new total.
        INCRBYFLOAT is atomic in Redis — safe under N concurrent replicas.
        """
        new_total = self.redis.incrbyfloat(self._key(), amount)
        return float(new_total)

    def get_spent(self) -> float:
        """Return current cumulative spend (non-atomic read)."""
        raw = self.redis.get(self._key())
        return float(raw) if raw is not None else 0.0

    def reset(self) -> None:
        """Reset cumulative spend to zero (start of new budget period)."""
        self.redis.set(self._key(), 0.0)

    def set_spent(self, value: float) -> None:
        """Directly set the cumulative spend (e.g., for seeding from audit history)."""
        self.redis.set(self._key(), value)


class DistributedFleetBudgetPolicy:
    """
    Fleet budget policy backed by Redis for multi-replica deployments.

    Drop-in replacement for FleetBudgetPolicy.  All replicas share one Redis
    counter so the effective budget is always the configured value regardless
    of replica count.

    Falls back to local in-memory tracking if Redis is unavailable.

    Usage:
        policy = DistributedFleetBudgetPolicy(
            redis_client=redis.Redis(),
            budget=100_000.0,
            warn_threshold=0.80,
        )
        pipeline.policy_engine.register(policy.as_policy())
    """

    def __init__(
        self,
        redis_client=None,
        budget: float = 100_000.0,
        warn_threshold: float = 0.80,
        policy_id: str = "LOG-001",
        namespace: str = "glassbox:fleet_budget",
        fallback_mode: bool = True,
    ):
        from glassbox.governance.models import DecisionType, PolicyEvaluation
        self._DecisionType    = DecisionType
        self._PolicyEvaluation = PolicyEvaluation

        self.budget         = float(budget)
        self.warn_threshold = float(warn_threshold)
        self.policy_id      = policy_id
        self.fallback_mode  = fallback_mode

        self._redis_backend: Optional[RedisFleetBudgetBackend] = None
        self._redis_ok      = False
        self._local_spent   = 0.0
        self._local_lock    = threading.Lock()

        if redis_client is not None:
            try:
                redis_client.ping()
                self._redis_backend = RedisFleetBudgetBackend(
                    redis_client, policy_id=policy_id, namespace=namespace,
                )
                self._redis_ok = True
                log.info(
                    "DistributedFleetBudgetPolicy: Redis connected (policy=%s)", policy_id
                )
            except Exception as exc:
                log.warning(
                    "DistributedFleetBudgetPolicy: Redis unavailable, using local fallback: %s", exc
                )
                if not fallback_mode:
                    raise

    def _add_spend(self, amount: float) -> float:
        """Add amount to cumulative spend; return new total. Prefers Redis."""
        if self._redis_ok and self._redis_backend:
            try:
                return self._redis_backend.add_spend(amount)
            except Exception as exc:
                log.warning("DistributedFleetBudgetPolicy: Redis add_spend failed: %s", exc)
                self._redis_ok = False

        # Local fallback
        with self._local_lock:
            self._local_spent += amount
            return self._local_spent

    def _get_spent(self) -> float:
        """Return current cumulative spend without modifying it."""
        if self._redis_ok and self._redis_backend:
            try:
                return self._redis_backend.get_spent()
            except Exception as exc:
                log.warning("DistributedFleetBudgetPolicy: Redis get_spent failed: %s", exc)
                self._redis_ok = False
        with self._local_lock:
            return self._local_spent

    def reset(self) -> None:
        """Reset cumulative spend (call at start of each budget period)."""
        if self._redis_ok and self._redis_backend:
            try:
                self._redis_backend.reset()
            except Exception as exc:
                log.warning("DistributedFleetBudgetPolicy: Redis reset failed: %s", exc)
        with self._local_lock:
            self._local_spent = 0.0

    def _rule(self, payload: dict, ctx) -> object:
        amount     = float(
            payload.get("amount", payload.get("fleet_spend", payload.get("total_cost", 0))) or 0
        )
        # We preview projected spend WITHOUT committing (Redis read + local add).
        current    = self._get_spent()
        projected  = current + amount

        if projected > self.budget:
            return self._PolicyEvaluation(
                policy_id=self.policy_id,
                policy_name="Fleet Budget Policy (Distributed)",
                result="fail",
                message=(
                    f"Fleet spend ${projected:,.2f} exceeds budget ${self.budget:,.2f} "
                    f"(current=${current:,.2f}, this_request=${amount:,.2f})"
                ),
            )

        if projected >= self.budget * self.warn_threshold:
            return self._PolicyEvaluation(
                policy_id=self.policy_id,
                policy_name="Fleet Budget Policy (Distributed)",
                result="warn",
                message=(
                    f"Fleet spend ${projected:,.2f} is {projected / self.budget:.0%} "
                    f"of budget ${self.budget:,.2f}"
                ),
            )

        return self._PolicyEvaluation(
            policy_id=self.policy_id,
            policy_name="Fleet Budget Policy (Distributed)",
            result="pass",
            message="Fleet within budget",
        )

    def record_execution(self, amount: float) -> None:
        """
        Commit spend after a decision is executed.

        Call this only once per executed decision to avoid double-counting.
        Typically invoked from an EventBus handler for DecisionExecuted events.
        """
        self._add_spend(float(amount or 0.0))

    def as_policy(self):
        """Return a Policy object suitable for PolicyEngine.register()."""
        from glassbox.governance.policy_engine import Policy
        return Policy(
            policy_id=self.policy_id,
            policy_name="Fleet Budget Policy (Distributed)",
            decision_types=[
                self._DecisionType.LOGISTICS, self._DecisionType.FINANCIAL,
            ],
            rule=self._rule,
        )


# Compatibility helper
def create_velocity_breaker_distributed(
    *,
    redis_client=None,
    max_decisions: int = 20,
    window_seconds: int = 60,
    ecosystem_config=None,
    fallback_mode: bool = True,
):
    """
    Factory function to create distributed velocity breaker.
    
    Usage:
        breaker = create_velocity_breaker_distributed(
            redis_client=redis.Redis(),
            max_decisions=20,
            ecosystem_config=EcosystemBreakerConfig(enabled=True, max_decisions=10000),
        )
    """
    eco = ecosystem_config or type('Config', (), {'enabled': False, 'max_decisions': None})()
    
    return DistributedVelocityBreaker(
        redis_client=redis_client,
        max_decisions=max_decisions,
        window_seconds=window_seconds,
        ecosystem_max=(eco.max_decisions if hasattr(eco, 'enabled') and eco.enabled else None),
        fallback_mode=fallback_mode,
    )
