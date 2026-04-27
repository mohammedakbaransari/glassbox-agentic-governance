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

    new_policy = Policy(policy_id="STRICT-001", policy_name="Strict Procurement Limit",
                        decision_types=[DecisionType.PROCUREMENT], rule=strict_limit)

    result = sim.simulate_policy(new_policy, lookback_hours=24*7)
    print(result.summary_text)
    # "Simulating STRICT-001 against 847 historical decisions:
    #  +143 newly blocked (16.9%) | 0 newly unblocked | 704 unchanged"
    # "Most affected agents: procurement_bot_eu (42), supply_chain_ai (38)"

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import concurrent.futures
import copy
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional, Tuple

from glassbox.security.sanitizer import validate_agent_id
from glassbox.governance.models import (
    AuditRecord, CircuitBreakerResult, DecisionContext, DecisionRequest, DecisionResponse,
    DecisionType, Disposition, FinalStatus, PolicyEvaluation, PolicyResult,
    RiskLevel, RiskResult,
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

    def simulate(
        self,
        request: DecisionRequest,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Dry-run a single decision request without persisting audit state."""
        pipeline = self._resolve_pipeline()
        if pipeline is None:
            raise ValueError("Request simulation requires a GovernancePipeline source.")

        with self._lock:
            prepared = pipeline._prepare_request(request)
            if isinstance(prepared.decision_type, str):
                prepared = DecisionRequest(
                    agent_id=prepared.agent_id,
                    decision_type=DecisionType(prepared.decision_type.lower()),
                    payload=prepared.payload,
                    context=prepared.context,
                    request_id=prepared.request_id,
                )

            active_stages = pipeline._resolve_stage_plan(prepared.agent_id, request_metadata)

            def _stage_enabled(stage_name: str) -> bool:
                if active_stages is None:
                    return True
                return stage_name in active_stages

            try:
                tenant_id = pipeline._tenant_id_from_request(prepared, request_metadata)
            except ValueError as exc:
                return self._blocked_simulation(prepared, str(exc), "TENANT-001")

            access_denial = pipeline._authorize_request(prepared)
            if access_denial:
                return self._blocked_simulation(prepared, access_denial, "ENTERPRISE-RBAC")

            ok, err = validate_agent_id(prepared.agent_id)
            if not ok:
                return self._blocked_simulation(prepared, err or "Invalid agent_id", "SECURITY-001")

            sec_report = pipeline.sanitizer.check(prepared.payload, agent_id=prepared.agent_id)
            if sec_report.blocked:
                detail = "; ".join(
                    f"{finding.category}@{finding.field_path}"
                    for finding in sec_report.findings
                    if finding.severity in ("critical", "high")
                ) or "Security violation"
                return self._blocked_simulation(prepared, f"Security violation: {detail}", "SECURITY-001")

            clean_payload = copy.deepcopy(sec_report.clean_payload or prepared.payload)

            contract = None
            if _stage_enabled("agent_contract_validation"):
                if tenant_id:
                    contract = pipeline.get_tenant_contract(tenant_id, prepared.agent_id)
                if contract is None:
                    contract = pipeline.get_contract(prepared.agent_id)
            if contract:
                violation = pipeline._check_contract(prepared, contract)
                if violation:
                    return self._blocked_simulation(prepared, violation, "CONTRACT-001")

            context = pipeline.context_capture.enrich(prepared, request_metadata)
            record = AuditRecord(
                agent_id=prepared.agent_id,
                decision_type=prepared.decision_type,
                payload=clean_payload,
                context=context,
                contract_validated=(contract is not None),
            )

            schema_ok, schema_error = True, None
            if _stage_enabled("schema_validation"):
                schema_ok, schema_error = pipeline.schema_validator.validate(
                    prepared.decision_type,
                    clean_payload,
                )
            if not schema_ok:
                blocked = pipeline._blocked_early(record, schema_error or "Schema error", "SCHEMA-001")
                return self._response_to_dict(prepared, blocked)

            policy_result = PolicyResult(passed=True)
            if _stage_enabled("policy_enforcement"):
                policy_result = pipeline.policy_engine.evaluate(
                    decision_type=prepared.decision_type,
                    payload=clean_payload,
                    context=context,
                )
            record.policy_result = policy_result

            if _stage_enabled("risk_evaluation"):
                risk_result = pipeline.risk_evaluator.evaluate(
                    decision_type=prepared.decision_type,
                    payload=clean_payload,
                    context=context,
                    policy_result=policy_result,
                )
            elif policy_result.passed:
                risk_result = RiskResult(
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    disposition=Disposition.AUTO_EXECUTE,
                    factors=[],
                )
            else:
                risk_result = RiskResult(
                    risk_score=100.0,
                    risk_level=RiskLevel.CRITICAL,
                    disposition=Disposition.BLOCK,
                    factors=[],
                )
            record.risk_result = risk_result
            record.circuit_breaker_result = CircuitBreakerResult(triggered=False)

            if risk_result.disposition == Disposition.BLOCK:
                final_status = FinalStatus.BLOCKED
            elif risk_result.disposition == Disposition.HUMAN_REVIEW:
                final_status = FinalStatus.PENDING_REVIEW
            else:
                final_status = FinalStatus.EXECUTED
            record.final_status = final_status

            response = DecisionResponse(
                decision_id=record.decision_id,
                request_id=prepared.request_id,
                final_status=final_status,
                risk_level=risk_result.risk_level,
                risk_score=risk_result.risk_score,
                disposition=risk_result.disposition,
                policy_violations=policy_result.violations,
                policy_warnings=policy_result.warnings,
                circuit_breaker_triggered=False,
                ecosystem_breaker=False,
                message="Simulated decision evaluated without persistence.",
                audit_record=record,
            )
            return self._response_to_dict(prepared, response)

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

        audit_repo = getattr(self._source, "audit_repo", None)
        if audit_repo is not None and hasattr(audit_repo, "query"):
            query_kwargs: Dict[str, Any] = {
                "agent_id": agent_filter,
                "decision_type": type_filter,
                "limit": max_records,
                "offset": 0,
            }
            if lookback_hours > 0:
                cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
                query_kwargs["from_ts"] = cutoff.isoformat()
            try:
                repo_records = audit_repo.query(**query_kwargs)
            except TypeError:
                repo_records = audit_repo.query(limit=max_records, offset=0)
            if repo_records:
                return [self._coerce_record(record) for record in repo_records]

        # Support both GovernancePipeline and AuditLogger as source
        logger = getattr(self._source, "audit_logger", self._source)
        if not hasattr(logger, "get_all"):
            return []

        records = logger.get_all()

        # Filter by lookback window
        if lookback_hours > 0:
            cutoff = datetime.now(timezone.utc) - timedelta(hours=lookback_hours)
            records = [r for r in records if self._parse_ts(r.timestamp) >= cutoff]

        if agent_filter:
            records = [r for r in records if r.agent_id == agent_filter]
        if type_filter:
            records = [r for r in records
                       if r.decision_type.value == type_filter
                       or str(r.decision_type) == type_filter]

        return records[:max_records]

    def _resolve_pipeline(self):
        if self._source is None:
            return None
        if hasattr(self._source, "process") and hasattr(self._source, "policy_engine"):
            return self._source
        return None

    def _blocked_simulation(
        self,
        request: DecisionRequest,
        message: str,
        policy_id: str,
    ) -> Dict[str, Any]:
        context = request.context or DecisionContext()
        record = AuditRecord(
            agent_id=request.agent_id,
            decision_type=request.decision_type,
            payload=request.payload,
            context=context,
        )
        response = DecisionResponse(
            decision_id=record.decision_id,
            request_id=request.request_id,
            final_status=FinalStatus.BLOCKED,
            disposition=Disposition.BLOCK,
            policy_violations=[f"[{policy_id}] {message}"],
            message=f"Blocked: {message}",
            audit_record=record,
        )
        return self._response_to_dict(request, response)

    def _response_to_dict(
        self,
        request: DecisionRequest,
        response: DecisionResponse,
    ) -> Dict[str, Any]:
        blocking_policy = None
        if response.policy_violations:
            first_violation = response.policy_violations[0]
            if first_violation.startswith("[") and "]" in first_violation:
                blocking_policy = first_violation[1:first_violation.index("]")]

        return {
            "simulation": True,
            "predicted_decision_id": response.decision_id,
            "request_id": request.request_id,
            "agent_id": request.agent_id,
            "decision_type": request.decision_type.value,
            "predicted_status": response.final_status.value,
            "predicted_disposition": response.disposition.value if response.disposition else None,
            "blocking_policy": blocking_policy,
            "risk_score": response.risk_score,
            "risk_level": response.risk_level.value if response.risk_level else None,
            "policy_violations": list(response.policy_violations or []),
            "policy_warnings": list(response.policy_warnings or []),
            "note": "This is a simulated decision - no audit record was created.",
        }

    def _coerce_record(self, record: Any) -> Any:
        if hasattr(record, "decision_id"):
            return record
        if not isinstance(record, dict):
            return record

        decision_type = record.get("decision_type")
        try:
            decision_type = DecisionType(decision_type)
        except Exception:
            pass

        final_status = record.get("final_status")
        try:
            final_status = FinalStatus(final_status) if final_status else None
        except Exception:
            final_status = None

        context_data = record.get("context") or {}
        context = self._coerce_context(context_data)

        return SimpleNamespace(
            decision_id=record.get("decision_id", ""),
            agent_id=record.get("agent_id", ""),
            decision_type=decision_type,
            final_status=final_status,
            payload=record.get("payload") or {},
            context=context,
            timestamp=record.get("timestamp", datetime.now(timezone.utc).isoformat()),
        )

    def _coerce_context(self, context: Any) -> DecisionContext:
        if isinstance(context, DecisionContext):
            return context
        if not isinstance(context, dict):
            return DecisionContext()
        return DecisionContext(
            session_id=context.get("session_id") or context.get("request_id") or DecisionContext().session_id,
            environment=context.get("environment", "production"),
            source_system=context.get("source_system", "unknown"),
            user_override=bool(context.get("user_override", False)),
            confidence=float(context.get("confidence", 1.0)),
            agent_chain=list(context.get("agent_chain") or []),
            metadata=dict(context.get("metadata") or {}),
            currency=context.get("currency", "USD"),
            jurisdiction=context.get("jurisdiction", "US"),
            patient_id=context.get("patient_id"),
            account_type=context.get("account_type", "unknown"),
        )

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

        dt = record.decision_type
        dt_value = dt.value if hasattr(dt, 'value') else str(dt)
        return SimulationOutcome(
            decision_id      = record.decision_id,
            agent_id         = record.agent_id,
            decision_type    = dt_value,
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
