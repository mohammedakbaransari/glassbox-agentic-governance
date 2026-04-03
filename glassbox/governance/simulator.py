"""
GlassBox — Policy Simulator / Dry-Run Mode  (v1.0.0)
=====================================================
Test the impact of a new or modified policy against historical decisions
BEFORE deploying it to production. Answers: "If I add/change this policy
today, what would have happened to the last N days of decisions?"

This is one of GlassBox's uniquely powerful capabilities. No other
governance tool provides pre-deployment policy impact analysis backed
by a real audit trail.

Use cases:
  1. New policy introduction: quantify operational disruption before going live
  2. Policy tightening: see which decisions would be newly blocked
  3. Policy relaxation: see which previously blocked decisions would now pass
  4. Regulatory change: assess impact of new regulatory requirement

Usage:
    from glassbox.governance.simulator import PolicySimulator, SimulationResult

    sim = PolicySimulator(pipeline)

    # Simulate a new strict policy
    def strict_limit(payload, ctx):
        if payload.get("amount", 0) > 200_000 and not payload.get("contract_id"):
            return PolicyEvaluation("STRICT-001", "Strict Limit", "fail",
                "[STRICT-001] Amount exceeds strict $200K limit")
        return PolicyEvaluation("STRICT-001", "Strict Limit", "pass", "OK")

    new_policy = Policy("STRICT-001", "Strict Procurement Limit",
                        [DecisionType.PROCUREMENT], strict_limit)

    result = sim.simulate_policy(new_policy, lookback_hours=24*7)
    print(result.summary_text)
    # "Simulating STRICT-001 against 847 historical decisions:
    #  +143 newly blocked (16.9%) | 0 newly unblocked | 704 unchanged"
    # "Most affected agents: procurement_bot_eu (42), supply_chain_ai (38)"

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from glassbox.governance.models import (
    DecisionContext, DecisionRequest, DecisionType,
    FinalStatus, PolicyEvaluation,
)


@dataclass
class SimulationOutcome:
    """Outcome of simulating one historical decision under a proposed policy."""
    decision_id:       str
    agent_id:          str
    decision_type:     str
    original_status:   str
    simulated_status:  str
    changed:           bool
    direction:         str   # "newly_blocked" | "newly_unblocked" | "unchanged"
    policy_fired:      bool
    policy_message:    str   = ""


@dataclass
class SimulationResult:
    """Aggregate result of a full policy simulation run."""
    policy_id:           str
    policy_name:         str
    total_decisions:     int
    newly_blocked:       int
    newly_unblocked:     int
    unchanged:           int
    block_rate_before:   float
    block_rate_simulated:float
    affected_agents:     Dict[str, int]   = field(default_factory=dict)
    affected_types:      Dict[str, int]   = field(default_factory=dict)
    outcomes:            List[SimulationOutcome] = field(default_factory=list)
    simulation_ms:       float            = 0.0

    @property
    def summary_text(self) -> str:
        lines = [
            f"Simulation: '{self.policy_name}' ({self.policy_id})",
            f"  Decisions analysed:    {self.total_decisions:,}",
            f"  Newly BLOCKED:         {self.newly_blocked:,} "
            f"({self.newly_blocked/max(self.total_decisions,1):.1%})",
            f"  Newly UNBLOCKED:       {self.newly_unblocked:,}",
            f"  Unchanged:             {self.unchanged:,}",
            f"  Block rate change:     {self.block_rate_before:.1%} → "
            f"{self.block_rate_simulated:.1%}",
        ]
        if self.affected_agents:
            top = sorted(self.affected_agents.items(), key=lambda x: -x[1])[:5]
            lines.append(f"  Most affected agents:  " +
                         ", ".join(f"{a} ({n})" for a, n in top))
        if self.affected_types:
            top = sorted(self.affected_types.items(), key=lambda x: -x[1])[:4]
            lines.append(f"  Affected by type:      " +
                         ", ".join(f"{t} ({n})" for t, n in top))
        lines.append(f"  Simulation time:       {self.simulation_ms:.0f}ms")
        return "\n".join(lines)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id":            self.policy_id,
            "policy_name":          self.policy_name,
            "total_decisions":      self.total_decisions,
            "newly_blocked":        self.newly_blocked,
            "newly_unblocked":      self.newly_unblocked,
            "unchanged":            self.unchanged,
            "block_rate_before":    round(self.block_rate_before, 4),
            "block_rate_simulated": round(self.block_rate_simulated, 4),
            "affected_agents":      self.affected_agents,
            "affected_types":       self.affected_types,
            "simulation_ms":        round(self.simulation_ms, 1),
        }


class PolicySimulator:
    """
    Dry-run simulation of proposed policies against historical audit records.

    The simulator replays historical decisions through a proposed policy rule
    (without going through the full pipeline) and measures the change in outcomes.
    This is purely read-only — no production state is modified.

    Thread-safe: all methods are safe to call from multiple threads.
    """

    def __init__(self, pipeline_or_audit_logger=None):
        """
        Args:
            pipeline_or_audit_logger: GovernancePipeline or AuditLogger instance.
                Used to retrieve historical audit records.
        """
        self._source = pipeline_or_audit_logger
        self._lock   = threading.Lock()

    def simulate_policy(
        self,
        policy,                       # Policy object with .rule, .policy_id, .policy_name
        lookback_hours:  int   = 168, # 7 days default
        agent_id_filter: Optional[str] = None,
        decision_type_filter: Optional[str] = None,
        max_records:     int   = 10_000,
        parallel:        bool  = True,
        max_workers:     int   = 4,
    ) -> SimulationResult:
        """
        Simulate a single proposed policy against historical decisions.

        Args:
            policy:               The Policy object to simulate.
            lookback_hours:       How many hours of history to replay.
            agent_id_filter:      Optional — only simulate for this agent.
            decision_type_filter: Optional — only simulate for this decision type.
            max_records:          Maximum historical records to process.
            parallel:             Use ThreadPoolExecutor for speed.
            max_workers:          Thread pool size.

        Returns:
            SimulationResult with full impact analysis.
        """
        t_start = time.perf_counter()

        records = self._fetch_records(
            lookback_hours, agent_id_filter, decision_type_filter, max_records
        )

        if parallel and len(records) > 50:
            outcomes = self._simulate_parallel(policy, records, max_workers)
        else:
            outcomes = self._simulate_sequential(policy, records)

        result = self._aggregate(policy, outcomes, time.perf_counter() - t_start)
        return result

    def simulate_policies(
        self,
        policies: List,
        lookback_hours: int = 168,
        max_records:    int = 10_000,
    ) -> List[SimulationResult]:
        """Simulate multiple proposed policies and return a result per policy."""
        records = self._fetch_records(lookback_hours, None, None, max_records)
        results = []
        for policy in policies:
            t_start  = time.perf_counter()
            outcomes = self._simulate_sequential(policy, records)
            results.append(self._aggregate(policy, outcomes, time.perf_counter() - t_start))
        return results

    def compare_policies(
        self,
        policy_a,
        policy_b,
        lookback_hours: int = 168,
        max_records:    int = 10_000,
    ) -> Dict[str, Any]:
        """
        Compare two policies head-to-head against the same historical records.
        Useful for choosing between a strict and a relaxed version of a rule.
        """
        results = self.simulate_policies([policy_a, policy_b], lookback_hours, max_records)
        return {
            "policy_a": results[0].to_dict() if results else {},
            "policy_b": results[1].to_dict() if len(results) > 1 else {},
            "recommendation": (
                f"Policy '{results[0].policy_name}' blocks "
                f"{results[0].newly_blocked} more decisions "
                f"({results[0].newly_blocked - results[1].newly_blocked:+d} vs policy_b)"
                if len(results) == 2 else "insufficient data"
            )
        }

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _fetch_records(
        self,
        lookback_hours: int,
        agent_filter:   Optional[str],
        type_filter:    Optional[str],
        max_records:    int,
    ) -> List[Any]:
        """Retrieve audit records from the source."""
        if self._source is None:
            return []

        # Support both GovernancePipeline and AuditLogger as source
        logger = getattr(self._source, "audit_logger", self._source)
        if not hasattr(logger, "get_all"):
            return []

        records = logger.get_all()

        # Filter by lookback window
        if lookback_hours > 0:
            from datetime import datetime, timezone, timedelta
            cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            records = [r for r in records
                       if self._parse_ts(r.timestamp) >= cutoff]

        if agent_filter:
            records = [r for r in records if r.agent_id == agent_filter]
        if type_filter:
            records = [r for r in records
                       if r.decision_type.value == type_filter
                       or str(r.decision_type) == type_filter]

        return records[:max_records]

    def _simulate_sequential(self, policy, records: List) -> List[SimulationOutcome]:
        return [self._evaluate_one(policy, r) for r in records]

    def _simulate_parallel(self, policy, records: List, max_workers: int) -> List[SimulationOutcome]:
        outcomes = [None] * len(records)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
            futures = {ex.submit(self._evaluate_one, policy, r): i
                       for i, r in enumerate(records)}
            for fut in concurrent.futures.as_completed(futures):
                idx = futures[fut]
                try:
                    outcomes[idx] = fut.result()
                except Exception:
                    pass
        return [o for o in outcomes if o is not None]

    def _evaluate_one(self, policy, record) -> SimulationOutcome:
        """Evaluate one historical record under the proposed policy."""
        original_status = (record.final_status.value
                           if record.final_status else "unknown")
        policy_fired    = False
        policy_message  = ""

        # Check if policy applies to this decision type
        applies = (DecisionType.CUSTOM in policy.decision_types or
                   record.decision_type in policy.decision_types)

        if applies:
            try:
                ctx = record.context or DecisionContext()
                ev  = policy.rule(record.payload or {}, ctx)
                if ev.result == "fail":
                    policy_fired   = True
                    policy_message = ev.message
            except Exception as e:
                policy_message = f"Policy evaluation error: {e}"

        # Determine simulated status
        was_blocked = original_status == "blocked"
        would_block = policy_fired

        if would_block and not was_blocked:
            direction       = "newly_blocked"
            simulated_status = "blocked"
        elif not would_block and was_blocked:
            direction        = "newly_unblocked"
            simulated_status = "executed"
        else:
            direction        = "unchanged"
            simulated_status = original_status

        return SimulationOutcome(
            decision_id      = record.decision_id,
            agent_id         = record.agent_id,
            decision_type    = record.decision_type.value,
            original_status  = original_status,
            simulated_status = simulated_status,
            changed          = direction != "unchanged",
            direction        = direction,
            policy_fired     = policy_fired,
            policy_message   = policy_message,
        )

    def _aggregate(self, policy, outcomes: List[SimulationOutcome], elapsed_s: float) -> SimulationResult:
        """Aggregate individual outcomes into a SimulationResult."""
        total          = len(outcomes)
        newly_blocked  = sum(1 for o in outcomes if o.direction == "newly_blocked")
        newly_unblocked= sum(1 for o in outcomes if o.direction == "newly_unblocked")
        unchanged      = total - newly_blocked - newly_unblocked

        originally_blocked = sum(1 for o in outcomes if o.original_status == "blocked")
        simulated_blocked  = sum(1 for o in outcomes
                                 if o.simulated_status == "blocked")

        # Count affected agents and types
        affected_agents: Dict[str, int] = {}
        affected_types:  Dict[str, int] = {}
        for o in outcomes:
            if o.changed:
                affected_agents[o.agent_id] = affected_agents.get(o.agent_id, 0) + 1
                affected_types[o.decision_type] = affected_types.get(o.decision_type, 0) + 1

        return SimulationResult(
            policy_id            = policy.policy_id,
            policy_name          = policy.policy_name,
            total_decisions      = total,
            newly_blocked        = newly_blocked,
            newly_unblocked      = newly_unblocked,
            unchanged            = unchanged,
            block_rate_before    = originally_blocked / max(total, 1),
            block_rate_simulated = simulated_blocked  / max(total, 1),
            affected_agents      = dict(sorted(affected_agents.items(), key=lambda x: -x[1])[:20]),
            affected_types       = affected_types,
            outcomes             = outcomes,
            simulation_ms        = elapsed_s * 1000,
        )

    @staticmethod
    def _parse_ts(ts_str: str):
        """Parse ISO timestamp string to datetime."""
        from datetime import datetime, timezone
        try:
            if ts_str.endswith("Z"):
                ts_str = ts_str[:-1] + "+00:00"
            return datetime.fromisoformat(ts_str)
        except Exception:
            return datetime.min.replace(tzinfo=timezone.utc)

    # ── Convenience: DecisionContext for import ────────────────────────────────

from glassbox.governance.models import DecisionContext  # noqa: E402
