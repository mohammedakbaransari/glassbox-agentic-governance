"""
GlassBox — Agent Behavioral Trust Scorer  (v1.0.0)
===================================================
Tracks the operational decision quality of each AI agent over time.

Unlike AGT's identity-based trust scoring (cryptographic credentials),
GlassBox's trust score is based on DECISION QUALITY — the actual
governance outcomes of every decision the agent has made:

  High trust agent: 90% of decisions auto-execute at low risk, rarely blocked,
                    no anomaly triggers, human reviews always approved.

  Low trust agent:  Frequent policy violations, high block rate, anomaly
                    triggers, circuit breaker trips, reviewer rejections.

Trust Score: 0–1000 (5 tiers)
  900–1000: TRUSTED         — reduced scrutiny, eligible for elevated limits
  700–899:  RELIABLE        — standard governance
  500–699:  MONITORED       — enhanced monitoring, lower auto-execute threshold
  200–499:  RESTRICTED      — HUMAN_REVIEW required for all decisions
  0–199:    SUSPENDED       — all decisions blocked pending review

The trust score decays toward the mean over time if the agent is inactive,
preventing artificially high scores from stale history.

Usage:
    from glassbox.governance.trust import AgentTrustScorer

    scorer  = AgentTrustScorer()
    bus     = EventBus()
    bus.subscribe("*", scorer.handle_event)
    pipeline = GovernancePipeline(event_bus=bus)

    # After some decisions:
    profile = scorer.get_profile("procurement_agent")
    print(f"Trust: {profile.score} ({profile.tier})")
    print(f"Block rate: {profile.block_rate:.1%}")
    print(f"Decisions: {profile.total_decisions}")

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ── Trust tier thresholds ─────────────────────────────────────────────────────

TRUST_TIERS = [
    (900, "TRUSTED",     "Agent consistently operates within policy. Eligible for elevated limits."),
    (700, "RELIABLE",    "Agent operates within policy with standard governance controls."),
    (500, "MONITORED",   "Agent shows elevated block/violation rate. Enhanced monitoring active."),
    (200, "RESTRICTED",  "Agent requires human review for all decisions."),
    (0,   "SUSPENDED",   "Agent has persistent governance failures. All decisions blocked pending review."),
]


@dataclass
class AgentTrustProfile:
    """Complete trust profile for one agent."""
    agent_id:           str
    score:              float        # 0–1000
    tier:               str          # TRUSTED | RELIABLE | MONITORED | RESTRICTED | SUSPENDED
    tier_description:   str
    total_decisions:    int          = 0
    executed_count:     int          = 0
    blocked_count:      int          = 0
    review_count:       int          = 0
    violation_count:    int          = 0
    anomaly_count:      int          = 0
    circuit_trips:      int          = 0
    reviewer_approvals: int          = 0
    reviewer_rejections:int          = 0
    last_activity:      Optional[str]= None
    score_history:      List[float]  = field(default_factory=list)

    @property
    def block_rate(self) -> float:
        return self.blocked_count / max(self.total_decisions, 1)

    @property
    def violation_rate(self) -> float:
        return self.violation_count / max(self.total_decisions, 1)

    @property
    def execute_rate(self) -> float:
        return self.executed_count / max(self.total_decisions, 1)

    @property
    def approval_rate(self) -> float:
        reviews = self.reviewer_approvals + self.reviewer_rejections
        return self.reviewer_approvals / max(reviews, 1)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id":            self.agent_id,
            "score":               round(self.score, 1),
            "tier":                self.tier,
            "tier_description":    self.tier_description,
            "total_decisions":     self.total_decisions,
            "executed_count":      self.executed_count,
            "blocked_count":       self.blocked_count,
            "review_count":        self.review_count,
            "violation_count":     self.violation_count,
            "anomaly_count":       self.anomaly_count,
            "circuit_trips":       self.circuit_trips,
            "reviewer_approvals":  self.reviewer_approvals,
            "reviewer_rejections": self.reviewer_rejections,
            "block_rate":          round(self.block_rate, 4),
            "execute_rate":        round(self.execute_rate, 4),
            "violation_rate":      round(self.violation_rate, 4),
            "last_activity":       self.last_activity,
        }


class _AgentStats:
    """Internal mutable statistics for one agent."""
    __slots__ = ["total","executed","blocked","review","violations",
                 "anomalies","trips","approvals","rejections","score","last_ts"]

    def __init__(self):
        self.total      = 0
        self.executed   = 0
        self.blocked    = 0
        self.review     = 0
        self.violations = 0
        self.anomalies  = 0
        self.trips      = 0
        self.approvals  = 0
        self.rejections = 0
        self.score      = 700.0   # Start at RELIABLE tier
        self.last_ts    = time.time()


class AgentTrustScorer:
    """
    Computes and maintains behavioral trust scores for AI agents.

    Scoring algorithm:
      Base score: 700 (RELIABLE)
      +5  per executed decision
      -20 per blocked decision
      -10 per policy violation
      -15 per anomaly detection trigger
      -50 per circuit breaker trip
      -30 per reviewer rejection
      +10 per reviewer approval

    Score is bounded 0–1000 and decays toward 600 at 1 point/hour of inactivity
    (so a dormant agent doesn't retain historical high scores indefinitely).

    Thread-safe: all updates use per-agent locks.
    """

    DECAY_RATE_PER_HOUR = 1.0   # points per hour of inactivity, toward 600
    DECAY_TARGET        = 600.0

    def __init__(self):
        self._agents: Dict[str, _AgentStats] = {}
        self._locks:  Dict[str, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def handle_event(self, event) -> None:
        """
        Subscribe this to the GlassBox EventBus:
            bus.subscribe("*", scorer.handle_event)
        """
        try:
            etype   = getattr(event, "event_type", "")
            payload = getattr(event, "payload", {}) or {}
            agent   = payload.get("agent_id", "")
            if not agent:
                return

            stats = self._get_stats(agent)
            lock  = self._get_lock(agent)

            with lock:
                stats.last_ts = time.time()
                if etype == "decision.executed":
                    stats.total    += 1
                    stats.executed += 1
                    stats.score    = min(1000, stats.score + 5)
                elif etype == "decision.blocked":
                    stats.total   += 1
                    stats.blocked += 1
                    stats.score   = max(0, stats.score - 20)
                elif etype == "decision.pending_review":
                    stats.total  += 1
                    stats.review += 1
                    stats.score  = max(0, stats.score - 5)
                elif etype == "policy.violated":
                    n_violations  = len(payload.get("violations", []))
                    stats.violations += n_violations
                    stats.score = max(0, stats.score - 10 * n_violations)
                elif etype == "anomaly.detected":
                    stats.anomalies += 1
                    stats.score = max(0, stats.score - 15)
                elif etype == "circuit_breaker.tripped":
                    stats.trips += 1
                    stats.score = max(0, stats.score - 50)
                # Workflow review outcomes (published by WorkflowEngine)
                elif etype == "workflow.approved":
                    stats.approvals += 1
                    stats.score = min(1000, stats.score + 10)
                elif etype == "workflow.rejected":
                    stats.rejections += 1
                    stats.score = max(0, stats.score - 30)
        except Exception:
            pass  # Trust scoring never breaks the calling thread

    def get_profile(self, agent_id: str) -> AgentTrustProfile:
        """Get the current trust profile for an agent."""
        stats = self._get_stats(agent_id)
        with self._get_lock(agent_id):
            score = self._apply_decay(stats)
            tier, desc = self._tier_for(score)
            return AgentTrustProfile(
                agent_id           = agent_id,
                score              = round(score, 1),
                tier               = tier,
                tier_description   = desc,
                total_decisions    = stats.total,
                executed_count     = stats.executed,
                blocked_count      = stats.blocked,
                review_count       = stats.review,
                violation_count    = stats.violations,
                anomaly_count      = stats.anomalies,
                circuit_trips      = stats.trips,
                reviewer_approvals = stats.approvals,
                reviewer_rejections= stats.rejections,
                last_activity      = datetime.fromtimestamp(
                    stats.last_ts, tz=timezone.utc).isoformat(),
            )

    def get_all_profiles(self) -> List[AgentTrustProfile]:
        """Return trust profiles for all agents seen so far."""
        with self._global_lock:
            agent_ids = list(self._agents.keys())
        return [self.get_profile(a) for a in agent_ids]

    def reset_agent(self, agent_id: str) -> None:
        """Reset an agent's trust score to the starting baseline (700)."""
        with self._get_lock(agent_id):
            stats       = self._get_stats(agent_id)
            stats.score = 700.0
            stats.total = stats.executed = stats.blocked = 0
            stats.review = stats.violations = stats.anomalies = 0
            stats.trips  = stats.approvals = stats.rejections = 0

    def score_summary(self) -> Dict[str, Any]:
        """Fleet-wide trust score summary."""
        profiles = self.get_all_profiles()
        if not profiles:
            return {"total_agents": 0, "tiers": {}, "average_score": 0}
        tier_counts: Dict[str, int] = {}
        for p in profiles:
            tier_counts[p.tier] = tier_counts.get(p.tier, 0) + 1
        avg = sum(p.score for p in profiles) / len(profiles)
        return {
            "total_agents":  len(profiles),
            "average_score": round(avg, 1),
            "tiers":         tier_counts,
            "lowest_score":  round(min(p.score for p in profiles), 1),
            "highest_score": round(max(p.score for p in profiles), 1),
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _get_stats(self, agent_id: str) -> _AgentStats:
        with self._global_lock:
            if agent_id not in self._agents:
                self._agents[agent_id] = _AgentStats()
                self._locks[agent_id]  = threading.Lock()
        return self._agents[agent_id]

    def _get_lock(self, agent_id: str) -> threading.Lock:
        self._get_stats(agent_id)   # ensure exists
        return self._locks[agent_id]

    def _apply_decay(self, stats: _AgentStats) -> float:
        """Apply time-based decay toward DECAY_TARGET."""
        hours_inactive = (time.time() - stats.last_ts) / 3600.0
        if hours_inactive < 1.0:
            return stats.score
        decay = hours_inactive * self.DECAY_RATE_PER_HOUR
        if stats.score > self.DECAY_TARGET:
            stats.score = max(self.DECAY_TARGET, stats.score - decay)
        elif stats.score < self.DECAY_TARGET:
            stats.score = min(self.DECAY_TARGET, stats.score + decay)
        return stats.score

    @staticmethod
    def _tier_for(score: float) -> tuple:
        for threshold, tier, desc in TRUST_TIERS:
            if score >= threshold:
                return tier, desc
        return TRUST_TIERS[-1][1], TRUST_TIERS[-1][2]
