"""
GlassBox Framework - Anomaly Detector
Detects statistically anomalous decision values by comparing incoming
decisions against a rolling baseline per agent/decision_type/field.
Uses Z-score analysis on numeric payload fields.

Author: Mohammed Akbar Ansari
"""

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


class RollingStats:
    """Maintains a rolling mean and sample standard deviation."""

    def __init__(self, window_size: int = 50):
        self.window_size = window_size
        self._values: deque = deque(maxlen=window_size)

    def add(self, value: float):
        self._values.append(value)

    @property
    def count(self) -> int:
        return len(self._values)

    @property
    def mean(self) -> Optional[float]:
        if not self._values:
            return None
        return sum(self._values) / len(self._values)

    @property
    def std(self) -> Optional[float]:
        if len(self._values) < 2:
            return None
        m = self.mean
        variance = sum((v - m) ** 2 for v in self._values) / (len(self._values) - 1)
        return math.sqrt(variance)

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


class AnomalyDetector:
    """
    Tracks rolling per-agent/decision_type/field statistics.
    Flags decisions where any numeric field has |z-score| > threshold.

    Starts learning immediately. Anomaly detection activates after
    `min_samples` observations per field per agent.
    """

    def __init__(
        self,
        z_threshold:       float = 3.0,
        min_samples:       int   = 10,
        window_size:       int   = 50,
        track_categorical: bool  = True,
    ):
        self.z_threshold       = z_threshold
        self.min_samples       = min_samples
        self._window_size      = window_size
        self.track_categorical = track_categorical
        # Numeric field stats: key (agent_id, decision_type, field) -> RollingStats
        self._stats: Dict[Tuple, RollingStats] = defaultdict(
            lambda: RollingStats(window_size=window_size)
        )
        # Categorical field stats: key (agent_id, decision_type, field) -> CategoricalTracker
        self._cat_stats: Dict[Tuple, CategoricalTracker] = defaultdict(
            lambda: CategoricalTracker(min_samples=max(min_samples * 2, 20))
        )
        # Thread-safe: RLock protects all dicts
        self._lock = threading.RLock()

    def _key(self, agent_id: str, decision_type: str, field: str) -> Tuple:
        return (agent_id, decision_type, field)

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
        """
        fields = MONITORED_FIELDS.get(decision_type, [])
        max_z = 0.0
        anomalous = []

        with self._lock:
            for fname in fields:
                value = payload.get(fname)
                if value is None or not isinstance(value, (int, float)):
                    continue

                key = self._key(agent_id, decision_type, fname)
                stats = self._stats[key]

                if stats.count >= self.min_samples:
                    z = stats.z_score(float(value))
                    if z is not None:
                        abs_z = abs(z)
                        if abs_z > max_z:
                            max_z = abs_z
                        if abs_z > self.z_threshold:
                            anomalous.append(
                                f"{fname}={value} (z={z:.2f}, baseline_mean={stats.mean:.2f}, "
                                f"baseline_std={stats.std:.2f})"
                            )

                # Always update the baseline (even on anomalous values - adaptive)
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
        with self._lock:
            for fname in fields:
                value = payload.get(fname)
                if value is not None and isinstance(value, (int, float)):
                    key = self._key(agent_id, decision_type, fname)
                    self._stats[key].add(float(value))

    def get_agent_stats(self, agent_id: str, decision_type: str) -> Dict[str, Any]:
        """Return current rolling stats for a specific agent/decision_type. Thread-safe."""
        fields = MONITORED_FIELDS.get(decision_type, [])
        result = {}
        with self._lock:
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
        Thread-safe: protected by the same RLock as check() and update_only().
        """
        key = self._key(agent_id, decision_type, field)
        with self._lock:
            for v in historical_values:
                self._stats[key].add(v)

    def reset_agent(self, agent_id: str, decision_type: str = None) -> None:
        """Reset rolling baselines for an agent (or specific type)."""
        with self._lock:
            keys_to_del = [
                k for k in self._stats
                if k[0] == agent_id and (decision_type is None or k[1] == decision_type)
            ]
            for k in keys_to_del:
                del self._stats[k]
