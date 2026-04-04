"""
GlassBox Framework — Governance Pipeline  (v1.0.0)
===================================================
The central 9-stage orchestrator — now a proper framework component.

Framework integrations (all opt-in):
  event_bus       — publishes domain events at every state transition
  audit_repo      — persists decisions to SQLite (queryable, indexed)
  workflow_engine — creates approval workflows for HUMAN_REVIEW decisions
  trace_enabled   — records per-stage execution trace for debugging
  policy_store    — loads policies from PolicyRepository at startup

Async support:
  process_async() — never blocks the asyncio event loop (ThreadPoolExecutor)

Security:
  Every request passes through PayloadSanitizer before Stage 0.

Thread-safety:
  process() and process_async() are fully thread-safe.
  All shared state (velocity_breaker, anomaly_detector, audit_logger,
  contracts registry) uses internal locking.

Platform support:
  Databricks, Kubernetes, Microsoft Fabric, VM, Docker.
  Hostname resolution is platform-safe (env-var precedence).

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import asyncio
import atexit
import concurrent.futures
import copy
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from glassbox.governance.anomaly_detector  import AnomalyDetector
from glassbox.governance.audit_logger      import AuditLogger
from glassbox.governance.context_capture   import ContextCapture
from glassbox.governance.event_dispatcher  import ResilientEventDispatcher
from glassbox.governance.execution_trace   import ExecutionTrace, StageTimer
from glassbox.governance.logging_manager   import get_logger
from glassbox.governance.models import (
    AgentContract, AuditRecord, CircuitBreakerResult,
    DecisionContext, DecisionRequest, DecisionResponse,
    Disposition, EcosystemBreakerConfig, ExecutionResult,
    FinalStatus, LogConfig, PolicyEvaluation, PolicyResult,
    RetryConfig, RetryStrategy,
)
from glassbox.governance.policy_engine     import PolicyEngine
from glassbox.governance.retry_policy      import RetryExecutor
from glassbox.governance.risk_evaluator    import RiskEvaluator
from glassbox.governance.schema_validator  import SchemaValidator
from glassbox.governance.velocity_breaker  import VelocityBreaker
from glassbox.security.sanitizer           import (
    PayloadSanitizer, SecurityReport, validate_agent_id,
)

log = get_logger("pipeline")


class GovernancePipeline:
    """
    Thread-safe, async-capable, event-driven 9-stage governance pipeline.

    Minimal usage (no external dependencies):
        pipeline  = GovernancePipeline()
        response  = pipeline.process(request)

    Full framework usage:
        from glassbox.events.event_bus     import EventBus
        from glassbox.store.repository     import RepositoryFactory
        from glassbox.workflow.workflow_engine import WorkflowEngine

        repos     = RepositoryFactory.sqlite(db_dir="/var/lib/glassbox")
        bus       = EventBus()
        wf_engine = WorkflowEngine(repository=repos["workflow"], event_bus=bus)

        pipeline  = GovernancePipeline(
            event_bus       = bus,
            audit_repo      = repos["audit"],
            workflow_engine = wf_engine,
            trace_enabled   = True,
        )
        response  = pipeline.process(request)
        print(response.execution_trace.summary())
    """

    def __init__(
        self,
        # Core components (injectable)
        policy_engine:    Optional[PolicyEngine]    = None,
        risk_evaluator:   Optional[RiskEvaluator]   = None,
        velocity_breaker: Optional[VelocityBreaker] = None,
        anomaly_detector: Optional[AnomalyDetector] = None,
        schema_validator: Optional[SchemaValidator] = None,
        audit_logger:     Optional[AuditLogger]     = None,
        executor:         Optional[Callable[[AuditRecord], Dict[str, Any]]] = None,
        retry_config:     Optional[RetryConfig]     = None,
        sanitizer:        Optional[PayloadSanitizer] = None,
        ecosystem_config: Optional[EcosystemBreakerConfig] = None,

        # Framework integrations (opt-in)
        event_bus:            Optional[Any] = None,
        audit_repo:           Optional[Any] = None,
        workflow_engine:      Optional[Any] = None,
        compliance_catalogue: Optional[Any] = None,
        trace_enabled:        bool          = False,
        async_audit_writes:   bool          = False,

        # Config
        environment:      str  = "production",
        log_dir:          Optional[str] = None,
        echo:             bool = False,
        max_memory_records: int = 100_000,
        async_workers:    int  = 8,
    ):
        eco = ecosystem_config or EcosystemBreakerConfig()

        self.context_capture  = ContextCapture(environment=environment)
        self.policy_engine    = policy_engine   or PolicyEngine()
        self.risk_evaluator   = risk_evaluator  or RiskEvaluator()
        self.anomaly_detector = anomaly_detector or AnomalyDetector()
        self.schema_validator = schema_validator or SchemaValidator()
        self.sanitizer        = sanitizer or PayloadSanitizer()

        if velocity_breaker is None:
            self.velocity_breaker = VelocityBreaker(
                ecosystem_max=(eco.max_decisions if eco.enabled else None),
                ecosystem_window_seconds=eco.window_seconds,
            )
        else:
            self.velocity_breaker = velocity_breaker

        self.audit_logger = audit_logger or AuditLogger(
            log_dir=log_dir, echo=echo, max_memory_records=max_memory_records,
        )
        self.executor      = executor
        self.retry_exec    = RetryExecutor(config=retry_config or RetryConfig())
        self.environment   = environment

        # Framework integrations
        self.event_bus            = event_bus
        self.audit_repo           = audit_repo
        self.workflow_engine      = workflow_engine
        self.compliance_catalogue = compliance_catalogue
        self.trace_enabled        = trace_enabled
        self.async_audit_writes   = async_audit_writes

        # Resilient event dispatcher (v1.0.1 - CRITICAL-1 fix)
        self._event_dispatcher = ResilientEventDispatcher(
            event_bus=event_bus,
            fallback_log_fn=lambda msg: log.warning(msg),
            max_failures=10,
            failure_timeout_sec=60,
        ) if event_bus else None

        # Agent contract registry
        self._contracts: Dict[str, AgentContract] = {}
        self._contracts_lock = threading.RLock()

        # Thread pool for async dispatch
        self._thread_pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=async_workers,
            thread_name_prefix="glassbox-async",
        )

        # Register cleanup handlers (v1.0.1 - CRITICAL-5 fix)
        atexit.register(self.shutdown)

        log.info("GovernancePipeline initialised", extra={
            "component":      "pipeline",
            "event":          "init",
            "environment":    environment,
            "policies":       len(self.policy_engine.policies),
            "event_bus":      event_bus is not None,
            "audit_repo":     audit_repo is not None,
            "workflow_engine":workflow_engine is not None,
            "trace_enabled":  trace_enabled,
        })

    # ── Contract Registry ──────────────────────────────────────────────────

    def register_contract(self, contract: AgentContract) -> None:
        with self._contracts_lock:
            self._contracts[contract.agent_id] = contract

    def get_contract(self, agent_id: str) -> Optional[AgentContract]:
        with self._contracts_lock:
            return self._contracts.get(agent_id)

    def list_contracts(self) -> List[Dict]:
        with self._contracts_lock:
            return [c.to_dict() for c in self._contracts.values()]

    # ── Public API ─────────────────────────────────────────────────────────

    def process(
        self,
        request:          DecisionRequest,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> DecisionResponse:
        """
        Synchronous pipeline — thread-safe, suitable for any context.
        """
        return self._run_pipeline(request, request_metadata)

    async def process_async(
        self,
        request:          DecisionRequest,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> DecisionResponse:
        """
        Async-safe pipeline — runs in thread pool, never blocks event loop.
        Use with LangChain, AutoGen, FastAPI, CrewAI, Fabric notebooks.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._thread_pool,
            self._run_pipeline,
            request,
            request_metadata,
        )

    # ── Core pipeline ──────────────────────────────────────────────────────

    def _run_pipeline(
        self,
        request:          DecisionRequest,
        request_metadata: Optional[Dict[str, Any]],
    ) -> DecisionResponse:
        t_start = time.perf_counter()
        trace   = ExecutionTrace(request.request_id) if self.trace_enabled else None

        # ── Security pre-check: agent_id validation ────────────────────
        id_ok, id_err = validate_agent_id(request.agent_id)
        if not id_ok:
            dummy_ctx = request.context or DecisionContext()
            record = AuditRecord(agent_id="__invalid__",
                                 decision_type=request.decision_type,
                                 payload={}, context=dummy_ctx)
            if trace:
                with StageTimer(trace, 0, "AgentIDValidation",
                                {"agent_id": request.agent_id}) as t:
                    t.outcome = "blocked"
                    t.detail  = id_err or "Invalid agent_id"
            resp = self._blocked_early(record, id_err or "Invalid agent_id", "SECURITY-001")
            return self._finalize(record, t_start, resp, trace)

        # ── Security pre-check: payload sanitization ────────────────────
        sec_report: SecurityReport = self.sanitizer.check(
            request.payload, agent_id=request.agent_id,
        )
        if sec_report.blocked:
            ctx    = request.context or DecisionContext()
            record = AuditRecord(agent_id=request.agent_id,
                                 decision_type=request.decision_type,
                                 payload={}, context=ctx)
            detail = "; ".join(
                f"{f.category}@{f.field_path}" for f in sec_report.findings
                if f.severity in ("critical", "high")
            )
            if trace:
                with StageTimer(trace, 0, "SecuritySanitizer") as t:
                    t.outcome = "blocked"
                    t.detail  = detail
            self._emit_security_event(request.agent_id,
                                       request.decision_type.value,
                                       [f.detail for f in sec_report.findings])
            resp = self._blocked_early(record, f"Security violation: {detail}", "SECURITY-001")
            return self._finalize(record, t_start, resp, trace)

        # Deep copy sanitized payload to prevent post-sanitization injection (v1.0.1 - CRITICAL-6)
        clean_payload = copy.deepcopy(sec_report.clean_payload or request.payload)

        # ── Stage 0: AgentContract Validation ───────────────────────────
        contract = self.get_contract(request.agent_id)
        if contract:
            viol = self._check_contract(request, contract)
            if viol:
                record = self._init_record(request, request_metadata, clean_payload)
                record.contract_validated = True
                if trace:
                    with StageTimer(trace, 0, "AgentContract",
                                    {"agent_id": request.agent_id}) as t:
                        t.outcome = "blocked"
                        t.detail  = viol
                resp = self._blocked_early(record, viol, "CONTRACT-001")
                return self._finalize(record, t_start, resp, trace)

        # ── Stage 1: Context Capture ─────────────────────────────────────
        context = self.context_capture.enrich(request, request_metadata)

        # ── Stage 2: Initialise audit record ─────────────────────────────
        record = AuditRecord(
            agent_id=request.agent_id,
            decision_type=request.decision_type,
            payload=clean_payload,
            context=context,
            contract_validated=(contract is not None),
        )

        # ── Stage 3: Schema Validation ────────────────────────────────────
        if trace:
            with StageTimer(trace, 3, "SchemaValidation",
                            {"decision_type": request.decision_type.value}) as t:
                schema_ok, schema_error = self.schema_validator.validate(
                    request.decision_type, clean_payload)
                t.outcome = "passed" if schema_ok else "blocked"
                t.detail  = schema_error or ""
                t.output_summary = {"valid": schema_ok}
        else:
            schema_ok, schema_error = self.schema_validator.validate(
                request.decision_type, clean_payload)

        if not schema_ok:
            resp = self._blocked_early(record, schema_error or "Schema error", "SCHEMA-001")
            return self._finalize(record, t_start, resp, trace)

        # ── Stage 4: Velocity / Ecosystem Breaker ─────────────────────────
        if trace:
            with StageTimer(trace, 4, "VelocityBreaker",
                            {"agent_id": request.agent_id}) as t:
                vel_triggered, vel_reason, vel_count = self.velocity_breaker.check(
                    request.agent_id)
                t.outcome = "blocked" if vel_triggered else "passed"
                t.detail  = vel_reason or ""
                t.output_summary = {"count": vel_count, "triggered": vel_triggered}
        else:
            vel_triggered, vel_reason, vel_count = self.velocity_breaker.check(
                request.agent_id)

        # ── Stage 5: Anomaly Detection ─────────────────────────────────────
        if trace:
            with StageTimer(trace, 5, "AnomalyDetection",
                            {"agent_id": request.agent_id,
                             "decision_type": request.decision_type.value}) as t:
                anom_triggered, anom_score, anom_fields = self.anomaly_detector.check(
                    agent_id=request.agent_id,
                    decision_type=request.decision_type.value,
                    payload=clean_payload,
                )
                t.outcome = "blocked" if anom_triggered else "passed"
                t.detail  = "; ".join(anom_fields) if anom_triggered else ""
                t.output_summary = {"z_score": anom_score, "anomalous": anom_triggered}
        else:
            anom_triggered, anom_score, anom_fields = self.anomaly_detector.check(
                agent_id=request.agent_id,
                decision_type=request.decision_type.value,
                payload=clean_payload,
            )

        cb_triggered = vel_triggered or anom_triggered
        if vel_triggered:
            is_eco    = "ecosystem" in (vel_reason or "").lower()
            cb_name   = "ecosystem_breaker" if is_eco else "velocity_breaker"
            cb_reason = vel_reason
        elif anom_triggered:
            is_eco    = False
            cb_name   = "anomaly_detector"
            cb_reason = f"Anomalous fields: {'; '.join(anom_fields)}"
        else:
            is_eco, cb_name, cb_reason = False, None, None

        cb_result = CircuitBreakerResult(
            triggered=cb_triggered, breaker_name=cb_name, reason=cb_reason,
            velocity_count=vel_count if vel_triggered else None,
            anomaly_score=anom_score if anom_triggered else None,
            anomalous_fields=anom_fields, is_ecosystem=is_eco,
        )
        record.circuit_breaker_result = cb_result

        if cb_triggered:
            record.policy_result = PolicyResult(passed=True)
            record.final_status  = FinalStatus.BLOCKED
            if cb_name == "anomaly_detector" and self.event_bus:
                self._emit_anomaly_event(record.decision_id, request.agent_id,
                                          request.decision_type.value, anom_fields, anom_score)
            elif self.event_bus:
                self._emit_breaker_event(request.agent_id, cb_name or "", cb_reason or "", is_eco)
            resp = DecisionResponse(
                decision_id=record.decision_id,
                final_status=FinalStatus.BLOCKED,
                circuit_breaker_triggered=True,
                circuit_breaker_reason=cb_reason,
                ecosystem_breaker=is_eco,
                message=f"Blocked by circuit breaker: {cb_reason}",
                audit_record=record,
            )
            return self._finalize(record, t_start, resp, trace)

        # ── Stage 6: Policy Enforcement ────────────────────────────────────
        if trace:
            with StageTimer(trace, 6, "PolicyEnforcement",
                            {"decision_type": request.decision_type.value}) as t:
                policy_result = self.policy_engine.evaluate(
                    decision_type=request.decision_type,
                    payload=clean_payload,
                    context=context,
                )
                t.outcome = "passed" if policy_result.passed else "blocked"
                t.detail  = "; ".join(policy_result.violations[:3])
                t.output_summary = {
                    "passed": policy_result.passed,
                    "violations": len(policy_result.violations),
                    "warnings": len(policy_result.warnings),
                }
        else:
            policy_result = self.policy_engine.evaluate(
                decision_type=request.decision_type,
                payload=clean_payload,
                context=context,
            )
        record.policy_result = policy_result

        if not policy_result.passed and self.event_bus:
            self._emit_policy_event(record.decision_id, request.agent_id,
                                     policy_result.violations, policy_result.warnings)

        # ── Stage 7: Risk Evaluation ────────────────────────────────────────
        if trace:
            with StageTimer(trace, 7, "RiskEvaluation") as t:
                risk_result = self.risk_evaluator.evaluate(
                    decision_type=request.decision_type,
                    payload=clean_payload,
                    context=context,
                    policy_result=policy_result,
                )
                t.outcome = "passed" if risk_result.disposition.value != "block" else "blocked"
                t.output_summary = {
                    "risk_score": risk_result.risk_score,
                    "risk_level": risk_result.risk_level.value,
                    "disposition": risk_result.disposition.value,
                }
        else:
            risk_result = self.risk_evaluator.evaluate(
                decision_type=request.decision_type,
                payload=clean_payload,
                context=context,
                policy_result=policy_result,
            )
        record.risk_result = risk_result

        # ── Stage 8: Disposition ────────────────────────────────────────────
        if risk_result.disposition == Disposition.BLOCK:
            final_status = FinalStatus.BLOCKED
        elif risk_result.disposition == Disposition.HUMAN_REVIEW:
            final_status = FinalStatus.PENDING_REVIEW
        else:
            final_status = FinalStatus.EXECUTED
            if self.executor:
                exec_result = self.retry_exec.execute(self.executor, record)
                record.execution_result = exec_result
                if not exec_result.success:
                    log.warning("Executor failed after %d attempts: %s",
                                exec_result.attempts, exec_result.error,
                                extra={"component": "pipeline",
                                       "event": "executor_failed",
                                       "decision_id": record.decision_id})

        record.final_status = final_status

        # ── Workflow creation for PENDING_REVIEW ────────────────────────
        if final_status == FinalStatus.PENDING_REVIEW and self.workflow_engine:
            try:
                self.workflow_engine.create_from_decision(
                    decision_id   = record.decision_id,
                    agent_id      = record.agent_id,
                    decision_type = record.decision_type.value,
                    risk_score    = risk_result.risk_score,
                    violations    = policy_result.violations,
                    warnings      = policy_result.warnings,
                )
            except Exception as exc:
                log.warning("WorkflowEngine.create_from_decision failed: %s", exc)

        if trace:
            with StageTimer(trace, 8, "Disposition") as t:
                t.outcome = final_status.value
                t.output_summary = {
                    "final_status": final_status.value,
                    "risk_score": risk_result.risk_score,
                }

        messages = {
            FinalStatus.EXECUTED:       "Decision approved and executed.",
            FinalStatus.PENDING_REVIEW: f"Queued for human review (risk={risk_result.risk_score}).",
            FinalStatus.BLOCKED: (
                "Blocked. " + (
                    f"Violations: {policy_result.violations}"
                    if policy_result.violations
                    else f"Risk score {risk_result.risk_score} exceeds block threshold."
                )
            ),
        }

        # Risk explanation (EU AI Act Art.13 transparency)
        _risk_expl = None
        if risk_result and risk_result.factors:
            top = sorted(risk_result.factors, key=lambda f: f.score * f.weight, reverse=True)[:3]
            parts = [f"{f.factor.replace('_',' ')} ({int(f.score*f.weight)}pts)"
                     for f in top if f.score * f.weight > 0]
            if parts:
                _risk_expl = f"Risk {risk_result.risk_score:.0f}/100: {', '.join(parts)}"

        _expl = None
        if final_status == FinalStatus.BLOCKED:
            if policy_result.violations:
                _expl = "Blocked: " + "; ".join(policy_result.violations[:2])
            else:
                _expl = f"Blocked: risk score {risk_result.risk_score:.0f}/100 exceeded threshold"

        resp = DecisionResponse(
            decision_id=record.decision_id,
            final_status=final_status,
            risk_level=risk_result.risk_level,
            risk_score=risk_result.risk_score,
            disposition=risk_result.disposition,
            policy_violations=policy_result.violations,
            policy_warnings=policy_result.warnings,
            circuit_breaker_triggered=False,
            ecosystem_breaker=False,
            message=messages.get(final_status, "Decision processed."),
            retry_attempts=(
                record.execution_result.attempts - 1 if record.execution_result else 0
            ),
            audit_record=record,
            risk_explanation=_risk_expl,
            explanation=_expl,
        )
        return self._finalize(record, t_start, resp, trace)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _init_record(
        self,
        request:  DecisionRequest,
        metadata: Optional[Dict] = None,
        payload:  Optional[Dict] = None,
    ) -> AuditRecord:
        context = self.context_capture.enrich(request, metadata)
        return AuditRecord(
            agent_id=request.agent_id,
            decision_type=request.decision_type,
            payload=payload or request.payload,
            context=context,
        )

    def _check_contract(
        self,
        request:  DecisionRequest,
        contract: AgentContract,
    ) -> Optional[str]:
        if request.decision_type not in contract.permitted_types:
            return (f"Agent '{request.agent_id}' not authorised for "
                    f"'{request.decision_type.value}'. "
                    f"Permitted: {[t.value for t in contract.permitted_types]}.")
        amount = float(request.payload.get("amount") or 0)
        if amount > contract.max_amount:
            return (f"Amount ${amount:,.2f} exceeds agent contract limit "
                    f"${contract.max_amount:,.2f}.")
        chain_depth = len(request.context.agent_chain) if request.context else 0
        if chain_depth > contract.max_delegation_depth:
            return (f"Agent chain depth {chain_depth} exceeds contract "
                    f"limit {contract.max_delegation_depth}.")
        if not contract.delegation_allowed and chain_depth > 0:
            return (f"Agent '{request.agent_id}' contract prohibits delegation "
                    f"(chain depth {chain_depth}).")
        return None

    def _blocked_early(
        self,
        record:    AuditRecord,
        message:   str,
        policy_id: str,
    ) -> DecisionResponse:
        policy_result = PolicyResult(
            passed=False,
            violations=[f"[{policy_id}] {message}"],
            evaluated_policies=[PolicyEvaluation(
                policy_id=policy_id, policy_name="Pre-pipeline Validation",
                result="fail", message=message,
            )],
        )
        record.policy_result          = policy_result
        record.circuit_breaker_result = CircuitBreakerResult(triggered=False)
        record.final_status           = FinalStatus.BLOCKED
        return DecisionResponse(
            decision_id=record.decision_id,
            final_status=FinalStatus.BLOCKED,
            policy_violations=policy_result.violations,
            circuit_breaker_triggered=False,
            message=f"Blocked: {message}",
            audit_record=record,
        )

    def _finalize(
        self,
        record:   AuditRecord,
        t_start:  float,
        response: DecisionResponse,
        trace:    Optional[ExecutionTrace] = None,
    ) -> DecisionResponse:
        latency_ms = round((time.perf_counter() - t_start) * 1000, 3)
        record.pipeline_latency_ms   = latency_ms
        response.pipeline_latency_ms = latency_ms

        # Attach execution trace
        if trace:
            trace.finalise()
            response.execution_trace = trace

        # In-memory audit log — sync or async
        if self.async_audit_writes:
            self.audit_logger.log_async(record)
        else:
            self.audit_logger.log(record)

        # SQLite audit repo (opt-in)
        if self.audit_repo:
            try:
                self.audit_repo.save(record)
            except Exception as exc:
                log.warning("AuditRepository.save failed: %s", exc)

        # Compliance evidence auto-collection (opt-in)
        if self.compliance_catalogue and record.final_status:
            self._collect_compliance_evidence(record)

        # Domain events (opt-in)
        if self.event_bus and record.final_status:
            self._emit_decision_event(record, response)

        return response

    # ── Event emission helpers ────────────────────────────────────────────────

    def _collect_compliance_evidence(self, record) -> None:
        """Auto-collect compliance evidence from governed decisions."""
        if not self.compliance_catalogue:
            return

        try:
            _MAP = {
                "all":        ["AIRM.MG.02","EUAI.A12","CSF2.DE.CM-01","E8.ML2.03","IEC62443.SR6.1"],
                "executed":   ["AIRM.MG.01","EUAI.A14","ZTA.TE-01","ZTA.PE-01"],
                "blocked":    ["CSF2.PR.AA-01","OWASP.A03","OWASP.A08","EUAI.A9"],
                "procurement":["OWASP.A09","CSF2.ID.AM-01"],
                "financial":  ["AIRM.ME.01","NERC.CIP007"],
                "it_ops":     ["NERC.CIP007","NERC.CIP010","IEC62443.SR2.1","PURDUE.L3-L4"],
            }
            status = record.final_status.value if record.final_status else "unknown"
            dtype  = record.decision_type.value
            ev     = {"decision_id": record.decision_id, "final_status": status,
                      "decision_type": dtype,
                      "risk_score": record.risk_result.risk_score if record.risk_result else None}
            for ctrl in set(_MAP.get("all",[]) + _MAP.get(status,[]) + _MAP.get(dtype,[])):
                self.compliance_catalogue.record_evidence(
                    ctrl, "decision", decision_id=record.decision_id,
                    agent_id=record.agent_id, evidence_data=ev)
        except Exception as exc:
            log.error(
                f"Compliance evidence collection failed: {exc}",
                extra={
                    "component": "pipeline",
                    "event": "compliance_evidence_failed",
                    "decision_id": record.decision_id,
                    "error_type": type(exc).__name__,
                },
                exc_info=True,
            )

    def _emit_decision_event(self, record: AuditRecord, response: DecisionResponse) -> None:
        if not self._event_dispatcher:
            return

        from glassbox.events.event_bus import (
            DecisionExecuted, DecisionBlocked, DecisionPendingReview,
        )

        status = record.final_status
        if status == FinalStatus.EXECUTED:
            event = DecisionExecuted(
                decision_id=record.decision_id, agent_id=record.agent_id,
                decision_type=record.decision_type.value,
                risk_score=record.risk_result.risk_score if record.risk_result else 0.0,
                latency_ms=record.pipeline_latency_ms or 0.0,
            )
            self._event_dispatcher.publish(event, event_type="DecisionExecuted")
        elif status == FinalStatus.BLOCKED:
            event = DecisionBlocked(
                decision_id=record.decision_id, agent_id=record.agent_id,
                decision_type=record.decision_type.value,
                violations=response.policy_violations,
                risk_score=response.risk_score,
            )
            self._event_dispatcher.publish(event, event_type="DecisionBlocked")
        elif status == FinalStatus.PENDING_REVIEW:
            event = DecisionPendingReview(
                decision_id=record.decision_id, agent_id=record.agent_id,
                decision_type=record.decision_type.value,
                risk_score=response.risk_score or 0.0,
            )
            self._event_dispatcher.publish(event, event_type="DecisionPendingReview")

    def _emit_security_event(self, agent_id, dtype, findings) -> None:
        if not self._event_dispatcher:
            return
        from glassbox.events.event_bus import SecurityViolation
        event = SecurityViolation(agent_id, dtype, findings)
        self._event_dispatcher.publish(event, event_type="SecurityViolation")

    def _emit_anomaly_event(self, decision_id, agent_id, dtype, fields, z) -> None:
        if not self._event_dispatcher:
            return
        from glassbox.events.event_bus import AnomalyDetected
        event = AnomalyDetected(decision_id, agent_id, dtype, fields, z)
        self._event_dispatcher.publish(event, event_type="AnomalyDetected")

    def _emit_breaker_event(self, agent_id, name, reason, is_eco) -> None:
        if not self._event_dispatcher:
            return
        from glassbox.events.event_bus import CircuitBreakerTripped
        event = CircuitBreakerTripped(agent_id, name, reason, is_eco)
        self._event_dispatcher.publish(event, event_type="CircuitBreakerTripped")

    def _emit_policy_event(self, decision_id, agent_id, violations, warnings) -> None:
        if not self._event_dispatcher:
            return
        from glassbox.events.event_bus import PolicyViolated
        event = PolicyViolated(decision_id, agent_id, violations, warnings)
        self._event_dispatcher.publish(event, event_type="PolicyViolated")

    # ── Convenience ────────────────────────────────────────────────────────────

    @property
    def stats(self) -> Dict[str, Any]:
        return self.audit_logger.summary_stats()

    def velocity_status(self, agent_id: str) -> Dict:
        return self.velocity_breaker.status(agent_id)

    def ecosystem_status(self) -> Dict:
        return self.velocity_breaker.ecosystem_status()

    def anomaly_stats(self, agent_id: str, decision_type: str) -> Dict:
        return self.anomaly_detector.get_agent_stats(agent_id, decision_type)

    def health(self) -> Dict[str, Any]:
        stats = self.stats
        return {
            "status":           "healthy",
            "service":          "GlassBox",
            "version":          "1.0.0",
            "environment":      self.environment,
            "total_decisions":  stats.get("total", 0),
            "block_rate_pct":   stats.get("block_rate_pct", 0),
            "avg_latency_ms":   stats.get("avg_latency_ms"),
            "p99_latency_ms":   stats.get("p99_latency_ms"),
            "policies":         len(self.policy_engine.policies),
            "contracts":        len(self.list_contracts()),
            "event_bus":             self.event_bus is not None,
            "audit_repo":            self.audit_repo is not None,
            "workflow_engine":       self.workflow_engine is not None,
            "compliance_catalogue":  self.compliance_catalogue is not None,
            "trace_enabled":         self.trace_enabled,
            "async_audit_writes":    self.async_audit_writes,
        }

    def shutdown(self) -> None:
        """Gracefully shutdown pipeline (v1.0.1 - CRITICAL-5: lifecycle management)."""
        # [v1.0.1 CRITICAL FIX] Shutdown audit logger background write executor
        if self.audit_logger and hasattr(self.audit_logger, "shutdown"):
            try:
                self.audit_logger.shutdown()
            except Exception as exc:
                log.error(
                    f"AuditLogger shutdown failed: {exc}",
                    extra={"component": "pipeline", "event": "audit_shutdown_failed"},
                    exc_info=True,
                )
        
        if self._thread_pool is not None:
            try:
                self._thread_pool.shutdown(wait=True)
            except Exception as exc:
                log.error(
                    f"ThreadPoolExecutor shutdown failed: {exc}",
                    extra={"component": "pipeline", "event": "shutdown_failed"},
                    exc_info=True,
                )
            finally:
                self._thread_pool = None

        if self.workflow_engine and hasattr(self.workflow_engine, "stop_monitor"):
            try:
                self.workflow_engine.stop_monitor()
            except Exception as exc:
                log.error(
                    f"WorkflowEngine shutdown failed: {exc}",
                    extra={"component": "pipeline", "event": "workflow_shutdown_failed"},
                    exc_info=True,
                )

        if self.event_bus and hasattr(self.event_bus, "shutdown"):
            try:
                self.event_bus.shutdown()
            except Exception as exc:
                log.error(
                    f"EventBus shutdown failed: {exc}",
                    extra={"component": "pipeline", "event": "eventbus_shutdown_failed"},
                    exc_info=True,
                )

        log.info("GovernancePipeline shutdown complete",
                 extra={"component": "pipeline", "event": "shutdown"})

    def __enter__(self):
        """Context manager entry (v1.0.1 - CRITICAL-5)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        self.shutdown()
        return False
