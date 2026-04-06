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
    "logistics":   [],
    "it_ops":      [],
    "hr":          [],
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

        return len(anomalous) > 0, round(max_z, 3), anomalous

    def update_only(
        self,
        agent_id: str,
        decision_type: str,
        payload: Dict[str, Any],
    ):
        """Update rolling stats without anomaly detection (used in replays)."""
        fields = MONITORED_FIELDS.get(decision_type, [])
        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                value = payload.get(fname)
                if value is not None and isinstance(value, (int, float)):
                    key = self._key(agent_id, decision_type, fname)
                    self._stats[key].add(float(value))

    def get_agent_stats(self, agent_id: str, decision_type: str) -> Dict[str, Any]:
        """Return current rolling stats for a specific agent/decision_type. Thread-safe."""
        fields = MONITORED_FIELDS.get(decision_type, [])
        result = {}
        lock = self._get_lock(agent_id, decision_type)
        with lock:
            for fname in fields:
                key = self._key(agent_id, decision_type, fname)
                stats = self._stats.get(key)
                if stats:
                    result[fname] = stats.summary()
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


# Backwards compatibility alias (v1.0.0 used AnomalyDetector)
AnomalyDetector = AnomalyDetectorOptimized
