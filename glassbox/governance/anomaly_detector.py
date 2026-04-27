"""
GlassBox Framework - Anomaly Detector (Welford's Algorithm Optimization)
========================================================================

Performance optimization implementing Welford's online algorithm to compute
mean and standard deviation in O(1) time instead of O(n).

Impact:
  - Before: stats.std recalculates from entire window (O(n), 50-100 iterations)
  - After:  O(1) incremental update on each value addition
  - Benefit: ~95% faster stats calculation for 50-100 item windows

Reference:
  Welford, B. P. (1962). "Note on a method for calculating corrected sums of 
  squares and products." Technometrics 4(3):419–420.

Author: Mohammed Akbar Ansari
"""

import hashlib
import math
import threading
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional, Tuple


# Numeric fields monitored per decision type
MONITORED_FIELDS: Dict[str, List[str]] = {
    "procurement": ["amount"],
    "pricing":     ["new_price", "previous_price"],
    "financial":   ["amount"],
    "inventory":   ["quantity"],
    "clinical":    ["dose_mg", "dosage_mg"],
    "trading":     ["quantity", "notional_value"],
    "content":     ["confidence"],           # generative AI confidence score
    "legal":       ["contract_value"],       # contract authority amounts
    "logistics":   [],
    "it_ops":      [],
    "hr":          [],
    "custom":      [],
}

CATEGORICAL_FIELDS: Dict[str, List[str]] = {
    "procurement": ["supplier_id", "category", "urgency"],
    "pricing":     ["product_id", "reason"],
    "financial":   ["destination_account", "reference", "payment_method"],
    "inventory":   ["product_id", "warehouse_id", "supplier_id"],
    "logistics":   ["origin", "destination"],
    "it_ops":      ["action", "target", "service_id"],
    "hr":          ["action", "employee_id"],
    "clinical":    ["drug_name", "drug_class"],
    "trading":     ["symbol"],
    "content":     ["topic", "content_category"],
    "legal":       ["action"],
    "custom":      [],
}


class RollingStatsWelford:
    """
    Maintains rolling mean and standard deviation using Welford's algorithm.
    
    Time complexity:
    - add(value): O(1)
    - mean/std properties: O(1)
    - z_score: O(1)
    
    Before (O(n) on each property access):
        mean:    sum(_values) / len(_values)
        std:     sqrt(sum((v - mean)^2 for v in _values) / (n-1))
        z_score: (value - mean) / std  (triggers 2 O(n) operations)
    
    After (O(1) using Welford):
        All statistics updated incrementally as values are added.
        Window eviction handled with moment adjustments.
    """

    def __init__(self, window_size: int = 50):
        self.window_size = window_size
        self._values: deque = deque(maxlen=window_size)
        
        # Welford state (all private)
        self._n = 0          # Total values added (ever)
        self._mean = 0.0     # Running mean
        self._M2 = 0.0       # Sum of squared differences (for variance)
        self._count = 0      # Current count in window
        self._sum = 0.0      # Sum of window values (for eviction tracking)

    def add(self, value: float):
        """Add value to rolling window, updating Welford state in O(1)."""
        value = float(value)
        
        # If window is at max and we're about to overflow, remove oldest value
        if len(self._values) == self.window_size:
            old_value = self._values[0]
            self._adjust_remove(old_value)
        
        # Add new value
        self._values.append(value)
        self._adjust_add(value)

    def _adjust_add(self, value: float):
        """Add value to Welford running stats (online algorithm).
        
        Welford's method for computing mean and variance incrementally:
        - delta  = value - old_mean
        - new_mean = old_mean + delta / n   (where n is window size after adding)
        - M2 = M2 + delta * (value - new_mean)
        
        Key fix: Use self._count (effective window size) not self._n (total ever added).
        This maintains O(1) mean updates even as window slides and values are evicted.
        """
        self._count += 1
        self._sum += value
        
        # Welford delta method: mean update based on window size
        delta = value - self._mean
        self._mean += delta / self._count  # Use count (current window size), not _n
        delta2 = value - self._mean
        self._M2 += delta * delta2

    def _adjust_remove(self, value: float):
        """Remove value from Welford stats using O(1) incremental formula."""
        if self._count <= 1:
            self._count = 0
            self._mean = 0.0
            self._M2 = 0.0
            return

        old_mean = self._mean
        self._count -= 1
        self._sum -= value

        # Reverse Welford update: derive new mean, then update M2 accordingly.
        # new_mean = (old_mean * (count+1) - value) / count
        self._mean = (old_mean * (self._count + 1) - value) / self._count
        delta_old = value - old_mean
        delta_new = value - self._mean
        self._M2 = max(0.0, self._M2 - delta_old * delta_new)

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def mean(self) -> Optional[float]:
        if self.count == 0:
            return None
        return self._mean

    @property
    def std(self) -> Optional[float]:
        """Sample standard deviation using Welford's M2."""
        if self.count < 2:
            return None
        variance = self._M2 / (self.count - 1)
        return math.sqrt(max(0, variance))  # Guard against float precision errors

    def z_score(self, value: float) -> Optional[float]:
        m = self.mean
        s = self.std
        if m is None or s is None or s == 0:
            return None
        return (value - m) / s

    def summary(self) -> Dict[str, Any]:
        return {
            "count": self.count,
            "mean":  round(self.mean, 4) if self.mean is not None else None,
            "std":   round(self.std, 4)  if self.std  is not None else None,
        }


class CategoricalTracker:
    """
    Tracks distribution of categorical (string) field values per agent.
    Flags values that are new or extremely rare — catches novel suppliers,
    unusual action types, unexpected product categories entering the system.
    """
    def __init__(self, min_samples: int = 20):
        self.min_samples = min_samples
        self._counts: Dict[str, int] = {}
        self._total:  int = 0

    def update(self, value: str) -> None:
        self._counts[value] = self._counts.get(value, 0) + 1
        self._total += 1

    def is_anomalous(self, value: str) -> Tuple[bool, str]:
        """Returns (anomalous, reason) — never-seen values are anomalous after warmup."""
        if self._total < self.min_samples:
            return False, ""
        if value not in self._counts:
            return True, f"value '{value}' never observed in {self._total} prior decisions"
        freq = self._counts[value] / self._total
        if freq < 0.01:
            return True, f"value '{value}' seen in only {freq:.1%} of prior decisions"
        return False, ""

    def summary(self) -> Dict[str, Any]:
        return {
            "total": self._total,
            "unique_values": len(self._counts),
            "top_values": sorted(self._counts.items(), key=lambda x: -x[1])[:5],
        }


class AnomalyDetectorOptimized:
    """
    Z-score anomaly detection with Welford's algorithm for O(1) stats.
    
    Tracks rolling per-agent/decision_type/field statistics.
    Flags decisions where any numeric field has |z-score| > threshold.

    Performance:
    - check() time: O(fields_monitored) instead of O(fields * window_size)
    - For typical case (5 fields, 50-item window): ~95% faster

    Starts learning immediately. Anomaly detection activates after
    `min_samples` observations per field per agent.
    """

    def __init__(
        self,
        z_threshold:       float = 3.0,
        min_samples:       int   = 10,
        window_size:       int   = 50,
        track_categorical: bool  = True,
        lock_pool_size:    int   = 16,
    ):
        self.z_threshold       = z_threshold
        self.min_samples       = min_samples
        self._window_size      = window_size
        self.track_categorical = track_categorical
        # Numeric field stats: key (agent_id, decision_type, field) -> RollingStatsWelford
        self._stats: Dict[Tuple, RollingStatsWelford] = defaultdict(
            lambda: RollingStatsWelford(window_size=window_size)
        )
        # Categorical field stats: key (agent_id, decision_type, field) -> CategoricalTracker
        self._cat_stats: Dict[Tuple, CategoricalTracker] = defaultdict(
            lambda: CategoricalTracker(min_samples=max(min_samples * 2, 20))
        )
        # Lock-pool: hash (agent_id, decision_type) to one of N partitions.
        # This reduces contention ~16x compared to a single RLock for systems
        # with many concurrent agents across multiple decision types.
        self._lock_pool_size = max(1, lock_pool_size)
        self._lock_pool = [threading.RLock() for _ in range(self._lock_pool_size)]

    def _key(self, agent_id: str, decision_type: str, field: str) -> Tuple:
        return (agent_id, decision_type, field)

    def _get_lock(self, agent_id: str, decision_type: str) -> threading.RLock:
        """Hash (agent_id, decision_type) to a partition lock."""
        h = hashlib.md5(f"{agent_id}:{decision_type}".encode()).digest()
        idx = int.from_bytes(h[:2], "big") % self._lock_pool_size
        return self._lock_pool[idx]

    def check(
        self,
        agent_id: str,
        decision_type: str,
        payload: Dict[str, Any],
    ) -> Tuple[bool, float, List[str]]:
        """
        Check payload fields for statistical anomalies.

        Returns:
            (is_anomalous: bool, max_abs_z_score: float, anomalous_field_descriptions: List[str])
        
        Performance: O(fields_monitored) with Welford's algorithm.
        """
        fields = MONITORED_FIELDS.get(decision_type, [])
        categorical_fields = CATEGORICAL_FIELDS.get(decision_type, [])
        max_z = 0.0
        anomalous = []

        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                value = payload.get(fname)
                if value is None or not isinstance(value, (int, float)):
                    continue

                key = self._key(agent_id, decision_type, fname)
                stats = self._stats[key]

                # Check: enough baseline data?
                is_anomalous = False
                if stats.count >= self.min_samples:
                    z = stats.z_score(float(value))
                    if z is not None:
                        abs_z = abs(z)
                        if abs_z > max_z:
                            max_z = abs_z
                        if abs_z > self.z_threshold:
                            is_anomalous = True
                            anomalous.append(
                                f"{fname}={value} (z={z:.2f}, baseline_mean={stats.mean:.2f}, "
                                f"baseline_std={stats.std:.2f})"
                            )

                # Only add to baseline if NOT anomalous: prevent adversarial drift
                # where repeated out-of-distribution values shift the baseline.
                if not is_anomalous:
                    stats.add(float(value))

            if self.track_categorical:
                for fname in categorical_fields:
                    value = payload.get(fname)
                    if value is None:
                        continue
                    if not isinstance(value, str):
                        value = str(value)
                    value = value.strip()
                    if not value:
                        continue

                    key = self._key(agent_id, decision_type, fname)
                    tracker = self._cat_stats[key]
                    is_cat_anomalous, reason = tracker.is_anomalous(value)
                    if is_cat_anomalous:
                        anomalous.append(f"{fname}='{value}' ({reason})")
                    else:
                        tracker.update(value)

        return len(anomalous) > 0, round(max_z, 3), anomalous

    def update_only(
        self,
        agent_id: str,
        decision_type: str,
        payload: Dict[str, Any],
    ):
        """Update rolling stats without anomaly detection (used in replays)."""
        fields = MONITORED_FIELDS.get(decision_type, [])
        categorical_fields = CATEGORICAL_FIELDS.get(decision_type, [])
        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                value = payload.get(fname)
                if value is not None and isinstance(value, (int, float)):
                    key = self._key(agent_id, decision_type, fname)
                    self._stats[key].add(float(value))
            if self.track_categorical:
                for fname in categorical_fields:
                    value = payload.get(fname)
                    if value is None:
                        continue
                    if not isinstance(value, str):
                        value = str(value)
                    value = value.strip()
                    if not value:
                        continue
                    key = self._key(agent_id, decision_type, fname)
                    self._cat_stats[key].update(value)

    def get_agent_stats(self, agent_id: str, decision_type: str) -> Dict[str, Any]:
        """Return current rolling stats for a specific agent/decision_type. Thread-safe."""
        fields = MONITORED_FIELDS.get(decision_type, [])
        categorical_fields = CATEGORICAL_FIELDS.get(decision_type, [])
        result = {}
        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                key = self._key(agent_id, decision_type, fname)
                stats = self._stats.get(key)
                if stats:
                    result[fname] = stats.summary()
            if self.track_categorical:
                categorical = {}
                for fname in categorical_fields:
                    key = self._key(agent_id, decision_type, fname)
                    tracker = self._cat_stats.get(key)
                    if tracker and tracker._total > 0:
                        categorical[fname] = tracker.summary()
                if categorical:
                    result["_categorical"] = categorical
        return result

    def inject_baseline(
        self,
        agent_id: str,
        decision_type: str,
        field: str,
        historical_values: List[float],
    ):
        """
        Pre-seed the rolling stats with historical data.
        Useful for production deployments where historical data is available.
        Thread-safe: protected by the same lock-pool as check() and update_only().
        """
        key = self._key(agent_id, decision_type, field)
        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for v in historical_values:
                self._stats[key].add(v)

    def reset_agent(self, agent_id: str, decision_type: str = None) -> None:
        """Reset rolling baselines for an agent (or specific type)."""
        # Must acquire all locks when resetting by agent (different partition per decision_type).
        for lock in self._lock_pool:
            with lock:
                keys_to_del = [
                    k for k in self._stats
                    if k[0] == agent_id and (decision_type is None or k[1] == decision_type)
                ]
                for k in keys_to_del:
                    del self._stats[k]
                cat_keys_to_del = [
                    k for k in self._cat_stats
                    if k[0] == agent_id and (decision_type is None or k[1] == decision_type)
                ]
                for k in cat_keys_to_del:
                    del self._cat_stats[k]


# ── O4: Redis-backed distributed Welford statistics ───────────────────────────
#
# Problem: AnomalyDetectorOptimized keeps Welford state in-process.  Under
# horizontal scaling, each replica builds its own baseline independently.
# Replica A may detect an anomaly that Replica B (which received the run-up
# traffic) never sees.
#
# Solution: RedisAnomalyStore persists the three Welford state variables
# (count, mean, M2) to Redis using a single HSET per (agent, type, field)
# key.  A Lua script performs the Welford update atomically so no two
# replicas can race on the same statistics.
#
# Drop-in usage:
#     store = RedisAnomalyStore(redis.Redis(), namespace="glassbox:anomaly")
#     detector = DistributedAnomalyDetector(z_threshold=3.0, store=store)

class RedisAnomalyStore:
    """
    Atomic Welford statistics storage backed by Redis.

    Key layout:
        {namespace}:{agent_id}:{decision_type}:{field}
          HASH fields:  count (int), mean (float), M2 (float)

    The Lua script that performs the Welford update is registered once per
    connection to avoid re-parsing overhead.
    """

    # Lua script — runs atomically on the Redis server.
    # Arguments: KEYS[1]=hash_key  ARGV[1]=new_value  ARGV[2]=max_count
    _LUA_WELFORD_UPDATE = """
local key    = KEYS[1]
local x      = tonumber(ARGV[1])
local maxn   = tonumber(ARGV[2])

local count  = tonumber(redis.call('HGET', key, 'count')  or '0')
local mean   = tonumber(redis.call('HGET', key, 'mean')   or '0')
local M2     = tonumber(redis.call('HGET', key, 'M2')     or '0')

-- Evict oldest conceptually: cap count so stats reflect at most maxn samples.
-- (True sliding-window eviction would require storing all values; this
-- Bayesian-forgetting approximation keeps the algorithm O(1) in Redis.)
if count >= maxn then
    -- Weight old mean down by 1/maxn each step (exponential forgetting)
    local alpha = 1.0 / maxn
    mean = mean * (1 - alpha) + x * alpha
    -- M2 shrinks proportionally to maintain approximate variance
    M2   = M2   * (1 - alpha)
    -- count stays at maxn so std-dev denominator remains stable
else
    count = count + 1
    local delta  = x - mean
    mean = mean + delta / count
    local delta2 = x - mean
    M2   = M2 + delta * delta2
end

redis.call('HMSET', key, 'count', count, 'mean', mean, 'M2', M2)
redis.call('EXPIRE', key, 86400)  -- TTL: 24 h (refresh on every update)

-- Return count, mean, M2 as an array so the caller can compute z-score.
return {tostring(count), tostring(mean), tostring(M2)}
"""

    def __init__(
        self,
        redis_client,
        namespace: str  = "glassbox:anomaly",
        window_size: int = 50,
    ):
        self.redis      = redis_client
        self.namespace  = namespace
        self.window_size = window_size
        self._script    = self.redis.register_script(self._LUA_WELFORD_UPDATE)

    def _key(self, agent_id: str, decision_type: str, field: str) -> str:
        return f"{self.namespace}:{agent_id}:{decision_type}:{field}"

    def update_and_get(
        self, agent_id: str, decision_type: str, field: str, value: float
    ) -> tuple:
        """
        Atomically update Welford stats and return (count, mean, M2).

        The Lua script runs server-side so concurrent replicas cannot corrupt
        each other's state.
        """
        key    = self._key(agent_id, decision_type, field)
        result = self._script(keys=[key], args=[value, self.window_size])
        count  = int(float(result[0]))
        mean   = float(result[1])
        M2     = float(result[2])
        return count, mean, M2

    def get(
        self, agent_id: str, decision_type: str, field: str
    ) -> tuple:
        """Return current (count, mean, M2) without updating."""
        key    = self._key(agent_id, decision_type, field)
        raw    = self.redis.hmget(key, "count", "mean", "M2")
        count  = int(float(raw[0])) if raw[0] else 0
        mean   = float(raw[1])      if raw[1] else 0.0
        M2     = float(raw[2])      if raw[2] else 0.0
        return count, mean, M2

    def reset(self, agent_id: str, decision_type: str = None) -> None:
        """Delete all stats keys for an agent (optionally scoped to decision_type)."""
        pattern = (
            f"{self.namespace}:{agent_id}:{decision_type}:*"
            if decision_type
            else f"{self.namespace}:{agent_id}:*"
        )
        keys = list(self.redis.scan_iter(match=pattern))
        if keys:
            self.redis.delete(*keys)


class DistributedAnomalyDetector(AnomalyDetectorOptimized):
    """
    AnomalyDetector backed by a RedisAnomalyStore for cross-replica consensus.

    All replicas share the same Welford baselines stored in Redis.  The
    z-score check and anomaly decision logic remain local (no extra Redis
    round-trip for the check itself — only the update is remote).

    Falls back to in-process Welford statistics if the store raises.

    Usage:
        store    = RedisAnomalyStore(redis.Redis())
        detector = DistributedAnomalyDetector(z_threshold=3.0, store=store)
        pipeline = GovernancePipeline(anomaly_detector=detector)
    """

    def __init__(
        self,
        store: "RedisAnomalyStore",
        z_threshold:       float = 3.0,
        min_samples:       int   = 10,
        window_size:       int   = 50,
        track_categorical: bool  = True,
        fallback_mode:     bool  = True,
    ):
        super().__init__(
            z_threshold=z_threshold,
            min_samples=min_samples,
            window_size=window_size,
            track_categorical=track_categorical,
        )
        self._store        = store
        self._fallback_mode = fallback_mode
        self._store_ok     = True

    def _welford_update(
        self, agent_id: str, decision_type: str, field: str, value: float
    ) -> tuple:
        """
        Update Welford stats via Redis store; fall back to in-memory on error.

        Returns (count, mean, std_or_None).
        """
        if self._store_ok:
            try:
                count, mean, M2 = self._store.update_and_get(
                    agent_id, decision_type, field, value
                )
                std = math.sqrt(max(0.0, M2 / (count - 1))) if count >= 2 else None
                return count, mean, std
            except Exception as exc:
                import logging as _log
                _log.getLogger("glassbox.anomaly_detector").warning(
                    "RedisAnomalyStore update failed, falling back to local: %s", exc
                )
                self._store_ok = False

        # Local fallback — delegates to parent's _stats dict
        key   = self._key(agent_id, decision_type, field)
        lock  = self._get_lock(agent_id, decision_type)
        with lock:
            stats = self._stats[key]
            stats.add(value)
            return stats.count, (stats.mean or 0.0), stats.std

    def check(
        self,
        agent_id: str,
        decision_type: str,
        payload: dict,
    ) -> tuple:
        """
        Check for anomalies using distributed Welford baselines.

        Numeric-field z-scores use Redis-backed statistics so all replicas
        see the same baseline.  Categorical tracking remains in-process
        (Redis-backing categorical trackers is left as a future enhancement).
        """
        fields            = MONITORED_FIELDS.get(decision_type, [])
        categorical_fields = CATEGORICAL_FIELDS.get(decision_type, [])
        max_z     = 0.0
        anomalous: list   = []

        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                value = payload.get(fname)
                if value is None or not isinstance(value, (int, float)):
                    continue

                fval = float(value)
                count, mean, std = self._welford_update(
                    agent_id, decision_type, fname, fval
                )

                if count >= self.min_samples and std is not None and std > 0:
                    z     = (fval - mean) / std
                    abs_z = abs(z)
                    if abs_z > max_z:
                        max_z = abs_z
                    if abs_z > self.z_threshold:
                        anomalous.append(
                            f"{fname}={fval} (z={z:.2f}, mean={mean:.2f}, std={std:.2f})"
                        )

            if self.track_categorical:
                for fname in categorical_fields:
                    value = payload.get(fname)
                    if value is None:
                        continue
                    value = str(value).strip()
                    if not value:
                        continue
                    key     = self._key(agent_id, decision_type, fname)
                    tracker = self._cat_stats[key]
                    is_cat_anomalous, reason = tracker.is_anomalous(value)
                    if is_cat_anomalous:
                        anomalous.append(f"{fname}='{value}' ({reason})")
                    else:
                        tracker.update(value)

        return len(anomalous) > 0, round(max_z, 3), anomalous


class AnomalyDetector(AnomalyDetectorOptimized):
    """
    Public v1.0-compatible interface, backed by AnomalyDetectorOptimized.

    All v1.0 constructor parameters and method names are preserved exactly.
    Existing code using AnomalyDetector(z_threshold=..., min_samples=...) continues
    to work without modification.
    """

    def __init__(
        self,
        z_threshold: float = 3.0,
        min_samples: int = 10,
        **kwargs,
    ) -> None:
        """v1.0-compatible constructor — maps to AnomalyDetectorOptimized."""
        super().__init__(
            z_threshold=z_threshold,
            min_samples=min_samples,
            **kwargs,
        )

    def check(
        self,
        agent_id: str,
        decision_type: str,
        payload: Dict[str, Any],
    ) -> Tuple[bool, float, List[str]]:
        """v1.0 public signature — preserved."""
        return super().check(agent_id, decision_type, payload)

    def update_only(
        self,
        agent_id: str,
        decision_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """v1.0 public signature — preserved."""
        return super().update_only(agent_id, decision_type, payload)
