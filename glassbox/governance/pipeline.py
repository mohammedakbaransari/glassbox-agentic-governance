"""
GlassBox Framework — Governance Pipeline  (v1.0.0)
===================================================
The central 12-step orchestrator (2 security pre-checks + 9 governance stages + finalize) — now a proper framework component.

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

import atexit
import asyncio
import concurrent.futures
import contextlib
import copy
import dataclasses
import threading
import time
import weakref
from collections import deque
from typing import Any, Callable, Dict, List, Optional

from glassbox.governance.anomaly_detector  import AnomalyDetector
from glassbox.governance.audit_logger      import AuditLogger
from glassbox.governance.context_capture   import ContextCapture
from glassbox.governance.event_dispatcher  import ResilientEventDispatcher
from glassbox.governance.execution_trace   import ExecutionTrace, StageTimer
from glassbox.governance.logging_manager   import get_logger, get_contextual_logger
from glassbox.governance.models import (
    AgentContract, AuditRecord, CircuitBreakerResult,
    DecisionContext, DecisionRequest, DecisionResponse,
    DecisionType, Disposition, EcosystemBreakerConfig, ExecutionResult,
    FinalStatus, LogConfig, PolicyEvaluation, PolicyResult, RiskLevel, RiskResult,
    RetryConfig, RetryStrategy,
)
from glassbox.governance.stage_registry import PipelineStageConfig, StagePosition, StageRegistry
from glassbox.governance.threadpool_config import default_async_workers
from glassbox.governance.policy_engine     import PolicyEngine
from glassbox.governance.retry_policy      import RetryExecutor
from glassbox.governance.risk_evaluator    import RiskEvaluator
from glassbox.governance.schema_validator  import SchemaValidator
from glassbox.governance.velocity_breaker  import VelocityBreaker
from glassbox.governance.write_ahead_log   import WriteAheadLog
from glassbox.security.sanitizer           import (
    PayloadSanitizer, SecurityReport, validate_agent_id,
)

log = get_logger("pipeline")
_ctx_log = get_contextual_logger("pipeline")

# ── P2-B Fix: Module-level atexit handler to prevent resource leak ────────────────
# Problem: Each GovernancePipeline() instantiation called atexit.register(self.shutdown),
#          creating hundreds of handlers in tests causing slow process exit.
# Solution: Track pipelines in a WeakSet, register single module-level handler.

_active_pipelines = weakref.WeakSet()


def _shutdown_all_pipelines() -> None:
    """
    Module-level atexit handler that shuts down all active pipelines.
    
    Uses WeakSet so pipelines that are garbage-collected are automatically
    removed without manual cleanup. This prevents resource leaks and ensures
    graceful shutdown of remaining active pipelines.
    """
    # Create a snapshot of active pipelines (WeakSet may change during iteration)
    pipelines = list(_active_pipelines)
    
    for pipeline in pipelines:
        try:
            if hasattr(pipeline, 'shutdown'):
                pipeline.shutdown()
        except Exception as exc:
            log.error(
                f"Pipeline shutdown failed during exit: {exc}",
                extra={"component": "pipeline", "event": "exit_shutdown_failed"},
                exc_info=True,
            )


# Register the module-level handler ONCE (not per-instance)
atexit.register(_shutdown_all_pipelines)


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
        access_control:       Optional[Any] = None,
        hash_audit:           Optional[Any] = None,
        wal:                  Optional[WriteAheadLog] = None,
        stage_registry:       Optional[StageRegistry] = None,
        trace_enabled:        bool          = False,
        async_audit_writes:   bool          = False,
        strict_audit_persistence: bool      = False,
        side_effect_mode:     Optional[str] = None,

        # Config
        environment:      str  = "production",
        log_dir:          Optional[str] = None,
        echo:             bool = False,
        max_memory_records: int = 100_000,
        async_workers:    Optional[int] = None,
        recover_wal_on_startup: bool = False,
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
        self.access_control       = access_control
        self.hash_audit           = hash_audit
        self.wal                  = wal
        self.stage_registry       = stage_registry
        self.trace_enabled        = trace_enabled
        self.async_audit_writes   = async_audit_writes
        self.strict_audit_persistence = strict_audit_persistence
        self.recover_wal_on_startup = recover_wal_on_startup
        if self.async_audit_writes and self.audit_repo and getattr(self.audit_logger, "repository", None) is None:
            self.audit_logger.repository = self.audit_repo
        resolved_side_effect_mode = (side_effect_mode or "").strip().lower()
        if not resolved_side_effect_mode:
            resolved_side_effect_mode = (
                "strict" if environment.strip().lower() == "production" else "best_effort"
            )
        self.side_effect_mode = resolved_side_effect_mode
        if self.side_effect_mode not in {"best_effort", "strict"}:
            raise ValueError(
                "side_effect_mode must be either 'best_effort' or 'strict'"
            )
        if self.strict_audit_persistence:
            self.side_effect_mode = "strict"
        self._ensure_stage_registry_defaults()

        # Resilient event dispatcher (v1.0.1 - CRITICAL-1 fix)
        self._event_dispatcher = ResilientEventDispatcher(
            event_bus=event_bus,
            fallback_log_fn=lambda msg: log.warning(msg),
            max_failures=10,
            failure_timeout_sec=60,
        ) if event_bus else None

        # O2: Aggregate per-stage latency tracker for built-in stages.
        # Maps stage_name -> list[float ms], capped at _STAGE_LATENCY_WINDOW.
        self._stage_latencies: Dict[str, list] = {}
        self._stage_latency_lock = threading.Lock()
        self._STAGE_LATENCY_WINDOW = 1000

        # Agent contract registry
        self._contracts: Dict[str, AgentContract] = {}
        self._contracts_lock = threading.RLock()

        # Thread pool for async dispatch
        resolved_async_workers = async_workers if async_workers is not None else default_async_workers()
        self.shared_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=resolved_async_workers,
            thread_name_prefix="glassbox-async",
        )
        self._thread_pool = self.shared_executor
        self._shutdown_lock = threading.Lock()
        self._shutdown_complete = False

        if self.wal and self.recover_wal_on_startup:
            self.recover_pending_wal_entries()

        # P2-B Fix: Track this pipeline in module-level WeakSet for coordinated shutdown
        # Instead of registering per-instance atexit handlers (which causes resource leaks),
        # add to WeakSet and let module-level handler manage cleanup.
        _active_pipelines.add(self)

        log.info("GovernancePipeline initialised", extra={
            "component":      "pipeline",
            "event":          "init",
            "environment":    environment,
            "policies":       len(self.policy_engine.policies),
            "event_bus":      event_bus is not None,
            "audit_repo":     audit_repo is not None,
            "workflow_engine":workflow_engine is not None,
            "access_control": access_control is not None,
            "hash_audit":    hash_audit is not None,
            "trace_enabled":  trace_enabled,
            "strict_audit_persistence": strict_audit_persistence,
            "side_effect_mode": self.side_effect_mode,
            "stage_registry": stage_registry is not None,
            "async_workers": resolved_async_workers,
            "recover_wal_on_startup": recover_wal_on_startup,
        })

    # ── Contract Registry ──────────────────────────────────────────────────

    def register_contract(self, contract: AgentContract) -> None:
        with self._contracts_lock:
            self._contracts[contract.agent_id] = contract

    def register_tenant_contract(self, tenant_id: str, contract: AgentContract) -> None:
        with self._contracts_lock:
            self._contracts[f"{tenant_id}::{contract.agent_id}"] = contract

    def get_contract(self, agent_id: str) -> Optional[AgentContract]:
        with self._contracts_lock:
            return self._contracts.get(agent_id)

    def get_tenant_contract(self, tenant_id: str, agent_id: str) -> Optional[AgentContract]:
        with self._contracts_lock:
            return self._contracts.get(f"{tenant_id}::{agent_id}")

    def list_contracts(self) -> List[Dict]:
        with self._contracts_lock:
            return [c.to_dict() for c in self._contracts.values()]

    def _ensure_stage_registry_defaults(self) -> None:
        """Register built-in stage configs when an external registry is provided."""
        if not self.stage_registry:
            return
        defaults = {
            "agent_contract_validation": StagePosition.STAGE_AGENT_CONTRACT.value,
            "context_capture": StagePosition.STAGE_CONTEXT_CAPTURE.value,
            "audit_record_init": StagePosition.STAGE_AUDIT_INIT.value,
            "schema_validation": StagePosition.STAGE_SCHEMA_VALIDATION.value,
            "velocity_breaker": StagePosition.STAGE_VELOCITY_BREAKER.value,
            "anomaly_detection": StagePosition.STAGE_ANOMALY_DETECTION.value,
            "policy_enforcement": StagePosition.STAGE_POLICY_ENFORCEMENT.value,
            "risk_evaluation": StagePosition.STAGE_RISK_EVALUATION.value,
            "disposition_routing": StagePosition.STAGE_DISPOSITION_ROUTING.value,
        }
        existing: set[str] = set()
        lock = getattr(self.stage_registry, "_stages_lock", None)
        stages = getattr(self.stage_registry, "_stages", None)
        if lock is not None and stages is not None:
            with lock:
                existing = set(stages.keys())
        else:
            existing = {cfg.name for cfg in self.stage_registry.get_execution_plan("__bootstrap__")}
        for name, pos in defaults.items():
            if name in existing:
                continue
            self.stage_registry.register_stage(
                name=name,
                config=PipelineStageConfig(name=name, enabled=True, position=float(pos)),
            )

    def _resolve_stage_plan(
        self,
        agent_id: str,
        request_metadata: Optional[Dict[str, Any]],
    ) -> Optional[set[str]]:
        if not self.stage_registry:
            return None
        plan = self.stage_registry.get_execution_plan(
            agent_id=agent_id,
            request_metadata=request_metadata or {},
        )
        names = {cfg.name for cfg in plan}
        return names if names else None

    def _resolve_runtime_stage_plan(
        self,
        agent_id: str,
        request_metadata: Optional[Dict[str, Any]],
    ) -> List[tuple[PipelineStageConfig, Any]]:
        if not self.stage_registry:
            return []
        return self.stage_registry.get_runtime_plan(
            agent_id=agent_id,
            request_metadata=request_metadata or {},
        )

    def _execute_registered_stage(
        self,
        config: PipelineStageConfig,
        stage_impl: Any,
        runtime_context: Dict[str, Any],
        completed_stages: set[str],
    ) -> Any:
        from glassbox.governance.stage_registry import StageExecutionResult

        missing_dependencies = [dep for dep in config.depends_on if dep not in completed_stages]
        if missing_dependencies:
            reason = (
                f"Stage '{config.name}' dependencies not satisfied: {', '.join(missing_dependencies)}"
            )
            if config.fallback_on_failure:
                return StageExecutionResult(
                    config.name,
                    passed=True,
                    skipped=True,
                    skip_reason=reason,
                )
            return StageExecutionResult(config.name, passed=False, error=reason)

        if stage_impl is None:
            return StageExecutionResult(
                config.name,
                passed=True,
                skipped=True,
                skip_reason="No stage implementation registered",
            )

        started = time.perf_counter()
        try:
            # Reuse the pipeline's shared executor rather than spawning a new
            # ThreadPoolExecutor per stage call (which created/destroyed a thread
            # pool on every request — severe overhead at production throughput).
            future = self.shared_executor.submit(stage_impl.execute, runtime_context)
            passed, blocked_reason = future.result(timeout=max(config.timeout_ms, 1) / 1000.0)
            return StageExecutionResult(
                config.name,
                passed=bool(passed),
                blocked_reason=blocked_reason,
                latency_ms=round((time.perf_counter() - started) * 1000, 3),
            )
        except concurrent.futures.TimeoutError:
            error = f"Stage '{config.name}' exceeded timeout of {config.timeout_ms}ms"
        except Exception as exc:
            error = f"Stage '{config.name}' failed: {exc}"

        latency_ms = round((time.perf_counter() - started) * 1000, 3)
        if config.fallback_on_failure:
            return StageExecutionResult(
                config.name,
                passed=True,
                error=error,
                latency_ms=latency_ms,
                skipped=True,
                skip_reason=error,
            )
        return StageExecutionResult(
            config.name,
            passed=False,
            error=error,
            latency_ms=latency_ms,
        )

    def _block_from_registered_stage(
        self,
        request: DecisionRequest,
        request_metadata: Optional[Dict[str, Any]],
        runtime_context: Dict[str, Any],
        t_start: float,
        trace: Optional[ExecutionTrace],
        result: Any,
    ) -> DecisionResponse:
        record = runtime_context.get("audit_record")
        if record is None:
            record = self._init_record(
                request,
                request_metadata,
                payload=runtime_context.get("payload") or request.payload,
            )
            runtime_context["audit_record"] = record
        message = result.blocked_reason or result.error or f"Stage '{result.stage_name}' blocked execution"
        policy_id = f"STAGE-{result.stage_name.upper().replace('-', '_')}"
        response = self._blocked_early(record, message, policy_id)
        return self._finalize(record, t_start, response, trace)

    def _rebuild_audit_record(self, payload: Dict[str, Any]) -> Optional[AuditRecord]:
        try:
            decision_id = payload.get("decision_id")
            decision_type = payload.get("decision_type")
            if not decision_id or not decision_type:
                return None

            context_data = payload.get("context") or {}
            context = DecisionContext(
                session_id=context_data.get("session_id") or context_data.get("request_id") or context_data.get("correlation_id") or "wal-recovery",
                environment=context_data.get("environment", "production"),
                source_system=context_data.get("source_system", "unknown"),
                user_override=bool(context_data.get("user_override", False)),
                confidence=float(context_data.get("confidence", 1.0) or 1.0),
                agent_chain=list(context_data.get("agent_chain") or []),
                metadata=dict(context_data.get("metadata") or {}),
                currency=context_data.get("currency", "USD"),
                jurisdiction=context_data.get("jurisdiction", "US"),
                patient_id=context_data.get("patient_id"),
                account_type=context_data.get("account_type", "unknown"),
            )
            record = AuditRecord(
                agent_id=str(payload.get("agent_id") or "unknown"),
                decision_type=DecisionType(str(decision_type)),
                payload=dict(payload.get("payload") or {}),
                context=context,
                decision_id=str(decision_id),
                timestamp=str(payload.get("timestamp") or ""),
            )
            record.contract_validated = bool(payload.get("contract_validated", False))
            record.pipeline_latency_ms = payload.get("pipeline_latency_ms")
            record.replay_of = payload.get("replay_of")

            final_status = payload.get("final_status")
            if final_status:
                record.final_status = FinalStatus(str(final_status))

            policy_result = payload.get("policy_result") or {}
            if policy_result:
                record.policy_result = PolicyResult(
                    passed=bool(policy_result.get("passed", True)),
                    evaluated_policies=[],
                    violations=list(policy_result.get("violations") or []),
                    warnings=list(policy_result.get("warnings") or []),
                )

            risk_result = payload.get("risk_result") or {}
            if risk_result:
                record.risk_result = RiskResult(
                    risk_score=float(risk_result.get("risk_score") or 0.0),
                    risk_level=RiskLevel(str(risk_result.get("risk_level") or RiskLevel.LOW.value)),
                    disposition=Disposition(str(risk_result.get("disposition") or Disposition.AUTO_EXECUTE.value)),
                    factors=[],
                )

            circuit_breaker = payload.get("circuit_breaker_result") or {}
            if circuit_breaker:
                record.circuit_breaker_result = CircuitBreakerResult(
                    triggered=bool(circuit_breaker.get("triggered", False)),
                    breaker_name=circuit_breaker.get("breaker_name"),
                    reason=circuit_breaker.get("reason"),
                    velocity_count=circuit_breaker.get("velocity_count"),
                    anomaly_score=circuit_breaker.get("anomaly_score"),
                    anomalous_fields=list(circuit_breaker.get("anomalous_fields") or []),
                    is_ecosystem=bool(circuit_breaker.get("is_ecosystem", False)),
                )
            return record
        except Exception as exc:
            log.error("Failed to rebuild audit record from WAL payload: %s", exc, exc_info=True)
            return None

    def recover_pending_wal_entries(self) -> int:
        """Replay incomplete WAL side effects on startup."""
        if not self.wal:
            return 0

        recovered = 0
        for entry in self.wal.get_pending_entries():
            payload = self.wal.deserialize_audit_record_json(entry.audit_record_json)
            record = self._rebuild_audit_record(payload)
            if record is None:
                continue

            side_effects = entry.side_effects or {}
            if not side_effects.get("audit_saved", {}).get("success"):
                self.audit_logger.log(record)
                self.wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)

            if self.audit_repo and not side_effects.get("repo_saved", {}).get("success"):
                self.audit_repo.save(record)
                self.wal.mark_side_effect(entry.entry_id, "repo_saved", success=True)

            if (
                self.workflow_engine
                and record.final_status == FinalStatus.PENDING_REVIEW
                and not side_effects.get("workflow_created", {}).get("success")
            ):
                policy_result = record.policy_result or PolicyResult(passed=True)
                risk_result = record.risk_result or RiskResult(
                    risk_score=0.0,
                    risk_level=RiskLevel.LOW,
                    disposition=Disposition.HUMAN_REVIEW,
                    factors=[],
                )
                self.workflow_engine.create_from_decision(
                    decision_id=record.decision_id,
                    agent_id=record.agent_id,
                    decision_type=record.decision_type.value,
                    risk_score=risk_result.risk_score,
                    violations=policy_result.violations,
                    warnings=policy_result.warnings,
                )
                self.wal.mark_side_effect(entry.entry_id, "workflow_created", success=True)

            if self.event_bus and not side_effects.get("events_emitted", {}).get("success"):
                response = DecisionResponse(
                    decision_id=record.decision_id,
                    final_status=record.final_status or FinalStatus.BLOCKED,
                    risk_level=record.risk_result.risk_level if record.risk_result else None,
                    risk_score=record.risk_result.risk_score if record.risk_result else None,
                    disposition=record.risk_result.disposition if record.risk_result else None,
                    policy_violations=record.policy_result.violations if record.policy_result else [],
                    policy_warnings=record.policy_result.warnings if record.policy_result else [],
                    audit_record=record,
                )
                self._emit_decision_event(record, response)
                self.wal.mark_side_effect(entry.entry_id, "events_emitted", success=True)

            self.wal.commit(entry.entry_id)
            recovered += 1

        if recovered:
            log.info("Recovered %d WAL entr%s on startup", recovered, "y" if recovered == 1 else "ies")
        return recovered

    def _tenant_id_from_request(
        self,
        request: DecisionRequest,
        request_metadata: Optional[Dict[str, Any]],
    ) -> Optional[str]:
        context_tenant = None
        metadata_tenant = None
        if request.context and isinstance(request.context.metadata, dict):
            context_tenant = request.context.metadata.get("tenant_id")
        if request_metadata and isinstance(request_metadata, dict):
            metadata_tenant = request_metadata.get("tenant_id")
        if context_tenant and metadata_tenant and context_tenant != metadata_tenant:
            raise ValueError(
                f"Tenant mismatch between context ({context_tenant}) and metadata ({metadata_tenant})"
            )
        tenant_id = context_tenant or metadata_tenant
        if tenant_id is None:
            return None
        tenant_id = str(tenant_id).strip()
        if not tenant_id:
            return None
        return tenant_id

    @staticmethod
    def _scoped_agent_id(agent_id: str, tenant_id: Optional[str]) -> str:
        if tenant_id:
            return f"{tenant_id}::{agent_id}"
        return agent_id

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
            self.shared_executor,
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
        request = self._prepare_request(request)
        if isinstance(request.decision_type, str):
            try:
                request.decision_type = DecisionType(request.decision_type.lower())
            except ValueError as exc:
                raise ValueError(
                    f"Invalid decision_type '{request.decision_type}'"
                ) from exc
        t_start = time.perf_counter()
        trace   = ExecutionTrace(request.request_id) if self.trace_enabled else None

        # Bind correlation context for this call's duration so all log lines emitted
        # during policy/risk/anomaly stages carry agent_id and decision_type.
        _pipeline_ctx = _ctx_log.bind(
            agent_id=request.agent_id,
            decision_type=request.decision_type.value,
        )
        _pipeline_ctx.__enter__()
        try:
            return self._execute_pipeline(request, request_metadata, t_start, trace)
        finally:
            _pipeline_ctx.__exit__(None, None, None)

    def _execute_pipeline(
        self,
        request:          DecisionRequest,
        request_metadata: Optional[Dict[str, Any]],
        t_start:          float,
        trace,
    ) -> DecisionResponse:
        runtime_stage_plan = self._resolve_runtime_stage_plan(request.agent_id, request_metadata)
        active_stages = {config.name for config, _ in runtime_stage_plan} if runtime_stage_plan else None
        pending_custom_stages = deque(
            (config, stage_impl)
            for config, stage_impl in runtime_stage_plan
            if stage_impl is not None
        )
        completed_stage_names: set[str] = set()
        runtime_context: Dict[str, Any] = {
            "request": request,
            "request_metadata": request_metadata or {},
            "payload": None,
            "context": None,
            "audit_record": None,
            "response": None,
            "tenant_id": None,
        }

        def _stage_enabled(stage_name: str) -> bool:
            if active_stages is None:
                return True
            return stage_name in active_stages

        def _run_registered_stages(up_to_position: float) -> Optional[DecisionResponse]:
            while pending_custom_stages and pending_custom_stages[0][0].position <= up_to_position:
                config, stage_impl = pending_custom_stages.popleft()
                result = self._execute_registered_stage(
                    config,
                    stage_impl,
                    runtime_context,
                    completed_stage_names,
                )
                if self.stage_registry:
                    self.stage_registry.record_execution(config.name, result)
                if result.skipped:
                    continue
                if not result.passed:
                    return self._block_from_registered_stage(
                        request,
                        request_metadata,
                        runtime_context,
                        t_start,
                        trace,
                        result,
                    )
                completed_stage_names.add(config.name)
            return None

        try:
            tenant_id = self._tenant_id_from_request(request, request_metadata)
            runtime_context["tenant_id"] = tenant_id
        except ValueError as exc:
            ctx = request.context or DecisionContext()
            record = AuditRecord(
                agent_id=request.agent_id,
                decision_type=request.decision_type,
                payload={},
                context=ctx,
            )
            with self._timed_call(trace, 0, "TenantIsolationValidation") as t:
                t.outcome = "blocked"
                t.detail = str(exc)
            resp = self._blocked_early(record, str(exc), "TENANT-001")
            return self._finalize(record, t_start, resp, trace)

        access_denial = self._authorize_request(request)
        if access_denial:
            record = self._init_record(request, request_metadata, copy.deepcopy(request.payload))
            with self._timed_call(trace, 0, "AccessControl", {"agent_id": request.agent_id}) as t:
                t.outcome = "blocked"
                t.detail = access_denial
            resp = self._blocked_early(record, access_denial, "ENTERPRISE-RBAC")
            return self._finalize(record, t_start, resp, trace)

        scoped_agent_id = self._scoped_agent_id(request.agent_id, tenant_id)

        id_ok, id_err = validate_agent_id(request.agent_id)
        if not id_ok:
            dummy_ctx = request.context or DecisionContext()
            record = AuditRecord(
                agent_id="__invalid__",
                decision_type=request.decision_type,
                payload={},
                context=dummy_ctx,
            )
            with self._timed_call(trace, 0, "AgentIDValidation", {"agent_id": request.agent_id}) as t:
                t.outcome = "blocked"
                t.detail = id_err or "Invalid agent_id"
            log.warning(f"Agent ID validation failed for '{request.agent_id}': {id_err}")
            resp = self._blocked_early(record, id_err or "Invalid agent_id", "SECURITY-001")
            return self._finalize(record, t_start, resp, trace)

        sec_report: SecurityReport = self.sanitizer.check(
            request.payload, agent_id=request.agent_id,
        )
        if sec_report.blocked:
            ctx = request.context or DecisionContext()
            record = AuditRecord(
                agent_id=request.agent_id,
                decision_type=request.decision_type,
                payload={},
                context=ctx,
            )
            detail = "; ".join(
                f"{f.category}@{f.field_path}" for f in sec_report.findings
                if f.severity in ("critical", "high")
            )
            with self._timed_call(trace, 0, "SecuritySanitizer") as t:
                t.outcome = "blocked"
                t.detail = detail
            self._emit_security_event(
                request.agent_id,
                request.decision_type.value,
                [f.detail for f in sec_report.findings],
            )
            log.warning(f"Payload security block for agent '{request.agent_id}': {detail}")
            resp = self._blocked_early(record, f"Security violation: {detail}", "SECURITY-001")
            return self._finalize(record, t_start, resp, trace)

        clean_payload = copy.deepcopy(sec_report.clean_payload or request.payload)
        runtime_context["payload"] = clean_payload

        staged_response = _run_registered_stages(StagePosition.STAGE_AGENT_CONTRACT.value)
        if staged_response is not None:
            return staged_response

        contract = None
        if _stage_enabled("agent_contract_validation"):
            if tenant_id:
                contract = self.get_tenant_contract(tenant_id, request.agent_id)
            if contract is None:
                contract = self.get_contract(request.agent_id)
        if contract:
            viol = self._check_contract(request, contract)
            if viol:
                record = self._init_record(request, request_metadata, clean_payload)
                record.contract_validated = True
                with self._timed_call(trace, 0, "AgentContract", {"agent_id": request.agent_id}) as t:
                    t.outcome = "blocked"
                    t.detail = viol
                log.warning(f"Agent contract violation for agent '{request.agent_id}': {viol}")
                resp = self._blocked_early(record, viol, "CONTRACT-001")
                return self._finalize(record, t_start, resp, trace)
        completed_stage_names.add("agent_contract_validation")

        staged_response = _run_registered_stages(StagePosition.STAGE_CONTEXT_CAPTURE.value)
        if staged_response is not None:
            return staged_response

        context = self.context_capture.enrich(request, request_metadata)
        runtime_context["context"] = context
        completed_stage_names.add("context_capture")

        staged_response = _run_registered_stages(StagePosition.STAGE_AUDIT_INIT.value)
        if staged_response is not None:
            return staged_response

        record = AuditRecord(
            agent_id=request.agent_id,
            decision_type=request.decision_type,
            payload=clean_payload,
            context=context,
            contract_validated=(contract is not None),
        )
        runtime_context["audit_record"] = record
        completed_stage_names.add("audit_record_init")

        staged_response = _run_registered_stages(StagePosition.STAGE_SCHEMA_VALIDATION.value)
        if staged_response is not None:
            return staged_response
        clean_payload = runtime_context.get("payload") or clean_payload
        record.payload = clean_payload

        if _stage_enabled("schema_validation"):
            with self._timed_call(trace, 3, "SchemaValidation", {"decision_type": request.decision_type.value}) as t:
                schema_ok, schema_error = self.schema_validator.validate(
                    request.decision_type,
                    clean_payload,
                )
                t.outcome = "passed" if schema_ok else "blocked"
                t.detail = schema_error or ""
                t.output_summary = {"valid": schema_ok}
        else:
            schema_ok, schema_error = True, None
        if not schema_ok:
            resp = self._blocked_early(record, schema_error or "Schema error", "SCHEMA-001")
            return self._finalize(record, t_start, resp, trace)
        completed_stage_names.add("schema_validation")

        staged_response = _run_registered_stages(StagePosition.STAGE_VELOCITY_BREAKER.value)
        if staged_response is not None:
            return staged_response

        # ── Stages 4 + 5: Circuit Breakers (Velocity + Anomaly) ──────────────
        cb_triggered, cb_name, cb_reason, is_eco, vel_count, anom_score, anom_fields = (
            self._stage_circuit_breakers(
                scoped_agent_id, request, record, clean_payload, trace, active_stages,
            )
        )
        completed_stage_names.add("velocity_breaker")
        completed_stage_names.add("anomaly_detection")

        record.circuit_breaker_result = CircuitBreakerResult(
            triggered=cb_triggered,
            breaker_name=cb_name,
            reason=cb_reason,
            velocity_count=vel_count if cb_triggered and "velocity" in (cb_name or "") else None,
            anomaly_score=anom_score if cb_triggered and "anomaly" in (cb_name or "") else None,
            anomalous_fields=anom_fields,
            is_ecosystem=is_eco,
        )
        if cb_triggered:
            record.policy_result = PolicyResult(passed=True)
            record.final_status = FinalStatus.BLOCKED
            if cb_name == "anomaly_detector" and self._event_dispatcher:
                self._emit_anomaly_event(
                    record.decision_id, request.agent_id,
                    request.decision_type.value, anom_fields, anom_score,
                )
            elif self._event_dispatcher:
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

        staged_response = _run_registered_stages(StagePosition.STAGE_POLICY_ENFORCEMENT.value)
        if staged_response is not None:
            return staged_response

        # ── Stages 6 + 7: Policy Enforcement + Risk Evaluation ───────────────
        policy_result, risk_result = self._stage_policy_and_risk(
            request, record, clean_payload, context, trace, active_stages,
        )
        record.policy_result = policy_result
        record.risk_result = risk_result
        runtime_context["policy_result"] = policy_result
        runtime_context["risk_result"] = risk_result
        completed_stage_names.add("policy_enforcement")
        completed_stage_names.add("risk_evaluation")

        if not policy_result.passed and self._event_dispatcher:
            self._emit_policy_event(record.decision_id, request.agent_id, policy_result.violations, policy_result.warnings)

        staged_response = _run_registered_stages(StagePosition.STAGE_DISPOSITION_ROUTING.value)
        if staged_response is not None:
            return staged_response

        # ── Stage 8: Disposition Routing ──────────────────────────────────────
        final_status = self._stage_disposition(request, record, policy_result, risk_result, trace)
        record.final_status = final_status
        completed_stage_names.add("disposition_routing")

        messages = {
            FinalStatus.EXECUTED: "Decision approved and executed.",
            FinalStatus.PENDING_REVIEW: f"Queued for human review (risk={risk_result.risk_score}).",
            FinalStatus.BLOCKED: (
                "Blocked. " + (
                    f"Violations: {policy_result.violations}"
                    if policy_result.violations
                    else f"Risk score {risk_result.risk_score} exceeds block threshold."
                )
            ),
        }

        _risk_expl = None
        if risk_result and risk_result.factors:
            top = sorted(risk_result.factors, key=lambda f: f.score * f.weight, reverse=True)[:3]
            parts = [
                f"{f.factor.replace('_',' ')} ({int(f.score * f.weight)}pts)"
                for f in top if f.score * f.weight > 0
            ]
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
            retry_attempts=(record.execution_result.attempts - 1 if record.execution_result else 0),
            audit_record=record,
            risk_explanation=_risk_expl,
            explanation=_expl,
        )
        runtime_context["response"] = resp

        staged_response = _run_registered_stages(float("inf"))
        if staged_response is not None:
            return staged_response
        return self._finalize(record, t_start, resp, trace)

    # ── Named stage helpers (extracted from _execute_pipeline) ───────────────
    # Each method handles exactly one governance stage, making the stage
    # individually testable and keeping _execute_pipeline at a readable length.

    def _stage_circuit_breakers(
        self,
        scoped_agent_id: str,
        request: DecisionRequest,
        record: AuditRecord,
        clean_payload: Dict[str, Any],
        trace,
        active_stages,
    ):
        """
        Stage: Velocity Breaker + Anomaly Detection + Circuit Breaker result.

        Returns (cb_triggered, cb_name, cb_reason, is_eco, vel_count,
                 anom_score, anom_fields, optional_early_response)
        """
        def _stage_enabled(name):
            return active_stages is None or name in active_stages

        vel_triggered, vel_reason, vel_count = False, None, 0
        if _stage_enabled("velocity_breaker"):
            with self._timed_call(trace, 4, "VelocityBreaker", {"agent_id": request.agent_id}) as t:
                vel_triggered, vel_reason, vel_count = self.velocity_breaker.check(scoped_agent_id)
                t.outcome = "blocked" if vel_triggered else "passed"
                t.detail = vel_reason or ""
                t.output_summary = {"count": vel_count, "triggered": vel_triggered}

        anom_triggered, anom_score, anom_fields = False, 0.0, []
        if _stage_enabled("anomaly_detection"):
            with self._timed_call(trace, 5, "AnomalyDetection", {"agent_id": request.agent_id, "decision_type": request.decision_type.value}) as t:
                anom_triggered, anom_score, anom_fields = self.anomaly_detector.check(
                    agent_id=scoped_agent_id,
                    decision_type=request.decision_type.value,
                    payload=clean_payload,
                )
                t.outcome = "blocked" if anom_triggered else "passed"
                t.detail = "; ".join(anom_fields) if anom_triggered else ""
                t.output_summary = {"z_score": anom_score, "anomalous": anom_triggered}

        cb_triggered = vel_triggered or anom_triggered
        if vel_triggered:
            is_eco = "ecosystem" in (vel_reason or "").lower()
            cb_name, cb_reason = ("ecosystem_breaker" if is_eco else "velocity_breaker"), vel_reason
        elif anom_triggered:
            is_eco = False
            cb_name = "anomaly_detector"
            cb_reason = f"Anomalous fields: {'; '.join(anom_fields)}"
        else:
            is_eco, cb_name, cb_reason = False, None, None

        return cb_triggered, cb_name, cb_reason, is_eco, vel_count, anom_score, anom_fields

    def _stage_policy_and_risk(
        self,
        request: DecisionRequest,
        record: AuditRecord,
        clean_payload: Dict[str, Any],
        context,
        trace,
        active_stages,
    ):
        """
        Stage: Policy Enforcement + Risk Evaluation.

        Returns (policy_result, risk_result).
        """
        def _stage_enabled(name):
            return active_stages is None or name in active_stages

        if _stage_enabled("policy_enforcement"):
            with self._timed_call(trace, 6, "PolicyEnforcement", {"decision_type": request.decision_type.value}) as t:
                policy_result = self.policy_engine.evaluate(
                    decision_type=request.decision_type,
                    payload=clean_payload,
                    context=context,
                )
                t.outcome = "passed" if policy_result.passed else "blocked"
                t.detail = "; ".join(policy_result.violations[:3])
                t.output_summary = {
                    "passed": policy_result.passed,
                    "violations": len(policy_result.violations),
                    "warnings": len(policy_result.warnings),
                }
        else:
            policy_result = PolicyResult(passed=True)

        if _stage_enabled("risk_evaluation"):
            with self._timed_call(trace, 7, "RiskEvaluation") as t:
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
            risk_result = RiskResult(
                risk_score=0.0 if policy_result.passed else 100.0,
                risk_level=RiskLevel.LOW if policy_result.passed else RiskLevel.CRITICAL,
                disposition=Disposition.AUTO_EXECUTE if policy_result.passed else Disposition.BLOCK,
                factors=[],
            )

        return policy_result, risk_result

    def _stage_disposition(
        self,
        request: DecisionRequest,
        record: AuditRecord,
        policy_result: PolicyResult,
        risk_result: RiskResult,
        trace,
    ) -> "FinalStatus":
        """
        Stage: Disposition Routing + optional executor invocation.

        Returns the resolved FinalStatus.
        """
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
                    log.warning(
                        "Executor failed after %d attempts: %s",
                        exec_result.attempts,
                        exec_result.error,
                        extra={"component": "pipeline", "event": "executor_failed",
                               "decision_id": record.decision_id},
                    )

        with self._timed_call(trace, 8, "Disposition") as t:
            t.outcome = final_status.value
            t.output_summary = {
                "final_status": final_status.value,
                "risk_score": risk_result.risk_score,
            }

        return final_status

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

    def _prepare_request(self, request: DecisionRequest) -> DecisionRequest:
        return self._apply_request_context(request)

    def _apply_request_context(self, request: DecisionRequest) -> DecisionRequest:
        """Merge active RequestContext into the decision context once at pipeline entry."""
        try:
            from glassbox.governance.request_context import RequestContext

            rc = RequestContext.get_current()
            if rc.user_id is None and rc.tenant_id is None and rc.correlation_id is None:
                return request

            if request.context is None:
                return dataclasses.replace(request, context=rc.to_decision_context())

            existing_meta = dict(request.context.metadata or {})
            if rc.tenant_id and "tenant_id" not in existing_meta:
                existing_meta["tenant_id"] = rc.tenant_id
            if rc.user_id and "user_id" not in existing_meta:
                existing_meta["user_id"] = rc.user_id
            if rc.correlation_id and "correlation_id" not in existing_meta:
                existing_meta["correlation_id"] = rc.correlation_id

            merged_context = dataclasses.replace(
                request.context,
                session_id=request.context.session_id or rc.request_id,
                metadata=existing_meta,
            )
            return dataclasses.replace(request, context=merged_context)
        except Exception:
            return request

    @staticmethod
    def _context_metadata(context: Optional[DecisionContext]) -> Dict[str, Any]:
        if context and isinstance(context.metadata, dict):
            return context.metadata
        return {}

    def _get_request_user_id(self, request: DecisionRequest) -> Optional[str]:
        user_id = self._context_metadata(request.context).get("user_id")
        if user_id is None:
            return None
        user_id = str(user_id).strip()
        return user_id or None

    def _authorize_request(self, request: DecisionRequest) -> Optional[str]:
        if not self.access_control:
            return None
        user_id = self._get_request_user_id(request)
        if not user_id:
            return None
        if self.access_control.has_permission(user_id, "decisions", "submit"):
            return None
        return f"Access denied: user '{user_id}' lacks decisions/submit permission"

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

    @contextlib.contextmanager
    def _timed_call(
        self,
        trace:      Optional[Any],
        stage_num:  int,
        stage_name: str,
        metadata:   Optional[Dict[str, Any]] = None,
    ):
        """
        Context manager that wraps StageTimer when trace is active; no-op otherwise.

        Eliminates the duplicate ``if trace: with StageTimer(...) as t: ... else: ...``
        pattern that appeared in every pipeline stage.

        Usage::

            with self._timed_call(trace, 3, "SchemaValidation", meta) as t:
                ok, err = self.schema_validator.validate(...)
                t.outcome = "passed" if ok else "blocked"
                t.output_summary = {"valid": ok}
        """
        _t0 = time.perf_counter()
        if trace is not None:
            with StageTimer(trace, stage_num, stage_name, metadata or {}) as t:
                yield t
        else:
            class _NoopTimer:
                # Instance attributes — NOT class-level mutables — so concurrent
                # callers each get their own dict and cannot cross-contaminate.
                def __init__(self):
                    self.outcome: str = ""
                    self.detail: str = ""
                    self.output_summary: Dict[str, Any] = {}
            yield _NoopTimer()
        # O2: Record elapsed time for this stage in the aggregate tracker.
        _elapsed_ms = (time.perf_counter() - _t0) * 1000
        with self._stage_latency_lock:
            samples = self._stage_latencies.setdefault(stage_name, [])
            samples.append(_elapsed_ms)
            if len(samples) > self._STAGE_LATENCY_WINDOW:
                self._stage_latencies[stage_name] = samples[self._STAGE_LATENCY_WINDOW // 2:]

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

        # WAL: log intent before side effects (crash-recovery guarantee)
        _wal_entry = None
        if self.wal:
            try:
                _wal_entry = self.wal.begin_transaction(record.decision_id, record)
            except Exception as exc:
                log.warning("WAL begin_transaction failed (non-fatal): %s", exc)

        # In-memory audit log — sync or async.
        # Errors always raise in strict mode; in best_effort mode a failed
        # in-memory write still raises because there is no backup persistence
        # path — losing the in-memory record silently would break audit completeness.
        try:
            if self.async_audit_writes:
                self.audit_logger.log_async(record)
            else:
                self.audit_logger.log(record)
            if _wal_entry:
                self.wal.mark_side_effect(_wal_entry.entry_id, "audit_saved", success=True)
        except Exception as exc:
            if _wal_entry:
                self.wal.mark_side_effect(_wal_entry.entry_id, "audit_saved",
                                          success=False, error_msg=str(exc))
            self._handle_side_effect_failure(
                side_effect="audit_logger.log_async" if self.async_audit_writes else "audit_logger.log",
                exc=exc,
                decision_id=record.decision_id,
                force_raise=self.strict_audit_persistence or self.side_effect_mode == "strict",
            )

        # SQLite audit repo (opt-in)
        if self.audit_repo and not self.async_audit_writes:
            try:
                self.audit_repo.save(record)
                if _wal_entry:
                    self.wal.mark_side_effect(_wal_entry.entry_id, "repo_saved", success=True)
            except Exception as exc:
                if _wal_entry:
                    self.wal.mark_side_effect(_wal_entry.entry_id, "repo_saved",
                                              success=False, error_msg=str(exc))
                self._handle_side_effect_failure(
                    side_effect="audit_repo.save",
                    exc=exc,
                    decision_id=record.decision_id,
                    force_raise=self.strict_audit_persistence or self.side_effect_mode == "strict",
                )

        if record.final_status == FinalStatus.PENDING_REVIEW and self.workflow_engine:
            try:
                self.workflow_engine.create_from_decision(
                    decision_id=record.decision_id,
                    agent_id=record.agent_id,
                    decision_type=record.decision_type.value,
                    risk_score=record.risk_result.risk_score if record.risk_result else 0.0,
                    violations=record.policy_result.violations if record.policy_result else [],
                    warnings=record.policy_result.warnings if record.policy_result else [],
                )
                if _wal_entry:
                    self.wal.mark_side_effect(_wal_entry.entry_id, "workflow_created", success=True)
            except Exception as exc:
                if _wal_entry:
                    self.wal.mark_side_effect(
                        _wal_entry.entry_id,
                        "workflow_created",
                        success=False,
                        error_msg=str(exc),
                    )
                self._handle_side_effect_failure(
                    side_effect="workflow_engine.create_from_decision",
                    exc=exc,
                    decision_id=record.decision_id,
                    force_raise=self.side_effect_mode == "strict",
                )

        if self.hash_audit and record.final_status:
            try:
                self._write_hash_audit(record, response)
            except Exception as exc:
                self._handle_side_effect_failure(
                    side_effect="hash_audit.log_action",
                    exc=exc,
                    decision_id=record.decision_id,
                )

        # Compliance evidence auto-collection (opt-in)
        if self.compliance_catalogue and record.final_status:
            try:
                self._collect_compliance_evidence(record)
            except Exception as exc:
                self._handle_side_effect_failure(
                    side_effect="compliance_catalogue.record_evidence",
                    exc=exc,
                    decision_id=record.decision_id,
                )

        # Domain events (opt-in)
        if self.event_bus and record.final_status:
            try:
                self._emit_decision_event(record, response)
                if _wal_entry:
                    self.wal.mark_side_effect(_wal_entry.entry_id, "events_emitted", success=True)
            except Exception as exc:
                if _wal_entry:
                    self.wal.mark_side_effect(_wal_entry.entry_id, "events_emitted",
                                              success=False, error_msg=str(exc))
                self._handle_side_effect_failure(
                    side_effect="event_bus.publish",
                    exc=exc,
                    decision_id=record.decision_id,
                )

        # WAL commit — all side effects completed successfully
        if _wal_entry:
            try:
                self.wal.commit(_wal_entry.entry_id)
            except Exception as exc:
                log.warning("WAL commit failed (non-fatal): %s", exc)

        return response

    def _write_hash_audit(
        self,
        record: AuditRecord,
        response: DecisionResponse,
    ) -> None:
        metadata = self._context_metadata(record.context)
        user_id = metadata.get("user_id") or "unknown"
        self.hash_audit.log_action(
            user_id=str(user_id),
            action="decision_processed",
            resource_type="decision",
            resource_id=response.decision_id,
            result=response.final_status.value,
            context={
                "agent_id": record.agent_id,
                "decision_type": record.decision_type.value
                if hasattr(record.decision_type, "value")
                else str(record.decision_type),
                "risk_score": getattr(response, "risk_score", None),
                "tenant_id": metadata.get("tenant_id"),
                "request_id": record.context.session_id if record.context else None,
            },
        )

    def _handle_side_effect_failure(
        self,
        side_effect: str,
        exc: Exception,
        decision_id: Optional[str] = None,
        force_raise: bool = False,
    ) -> None:
        log.error(
            "Side-effect failure in %s: %s",
            side_effect,
            exc,
            extra={
                "component": "pipeline",
                "event": "side_effect_failure",
                "side_effect": side_effect,
                "decision_id": decision_id,
            },
            exc_info=True,
        )
        if force_raise or self.side_effect_mode == "strict":
            raise RuntimeError(
                f"Side-effect failure ({side_effect}) for decision {decision_id}"
            ) from exc

    # ── Event emission helpers ────────────────────────────────────────────────

    def _collect_compliance_evidence(self, record) -> None:
        """Auto-collect compliance evidence from governed decisions."""
        if not self.compliance_catalogue:
            return

        try:
            # Maps decision outcome / type → control IDs that are evidenced by each governed decision.
            # Covers all 24 compliance frameworks in the catalogue.
            _MAP = {
                # Every governed decision evidences audit, logging, monitoring, ZTA verification
                # controls and general AI risk management regardless of type or outcome.
                "all": [
                    "AIRM.MG.02",      # NIST AI RMF — AI decision audit trail
                    "EUAI.A12",        # EU AI Act — Record-keeping
                    "EUAI.A13",        # EU AI Act — Transparency (ExecutionTrace present)
                    "EUAI.A15",        # EU AI Act — Accuracy/Robustness (AI-001 confidence guard)
                    "CSF2.DE.CM-01",   # NIST CSF 2.0 — Continuous monitoring
                    "E8.ML2.03",       # ASD E8 — Audit logging
                    "IEC62443.SR6.1",  # IEC 62443 — Audit log accessibility
                    "ZTA.TE-01",       # ZTA — Never trust, always verify (per-decision governance)
                    "ZTA.PE-01",       # ZTA — Dynamic policy evaluation
                    "ISO27K.A8.15",    # ISO 27001 — Logging
                    "ISO27K.A8.16",    # ISO 27001 — Monitoring activities
                    "ISO42K.9.1",      # ISO/IEC 42001 — Monitoring, measurement, analysis
                    "SOC2.CC7.2",      # SOC 2 — System monitoring
                    "800-53.AU-2",     # NIST 800-53 — Event logging
                    "800-53.AU-9",     # NIST 800-53 — Protection of audit information
                    "FDA11.11.10e",    # FDA 21 CFR Part 11 — Audit trails (electronic records)
                    "FFIEC.D1.CC",     # FFIEC CAT — Cyber risk identification
                    "DORA.Art6",       # DORA — ICT risk management framework
                ],
                # Auto-execute decisions: risk treatment, ZTA least-privilege, human oversight
                "executed": [
                    "AIRM.MG.01",      # NIST AI RMF — AI risk treatment
                    "EUAI.A14",        # EU AI Act — Human oversight (decision auto-approved)
                    "ZTA.TE-02",       # ZTA — Least privilege (within AgentContract limits)
                    "COL.SB205.8",     # Colorado AI Act — Risk management policy
                    "HIPAA.164.308a1", # HIPAA — Security management process
                    "DORA.Art24",      # DORA — Resilience testing evidence (successful execution)
                    "ISO42K.10.1",     # ISO/IEC 42001 — Continual improvement
                ],
                # Blocked decisions: access control, anomaly detection, security posture
                "blocked": [
                    "CSF2.PR.AA-01",   # NIST CSF 2.0 — Identity management (agent blocked)
                    "OWASP.A03",       # OWASP — Excessive agency prevention
                    "OWASP.A08",       # OWASP — Weak authentication / authorisation
                    "EUAI.A9",         # EU AI Act — Risk management system (risk enforcement)
                    "AIRM.ME.01",      # NIST AI RMF — AI risk measurement
                    "ZTA.TE-03",       # ZTA — Assume breach posture
                    "SOC2.CC6.1",      # SOC 2 — Logical access security
                    "PCI4.6.3",        # PCI DSS — Security event detection
                    "800-53.RA-3",     # NIST 800-53 — Risk assessment
                    "FFIEC.D3.CY",     # FFIEC CAT — Cybersecurity controls
                ],
                # Pending-review decisions: human oversight and workflow controls
                "pending_review": [
                    "EUAI.A14",        # EU AI Act — Human oversight
                    "COL.SB205.9",     # Colorado AI Act — Human review mechanism
                    "COL.SB205.10",    # Colorado AI Act — Disclosure of AI use
                    "FDA11.11.50",     # FDA — Signature manifestations (reviewer identity)
                    "MASTRM.5",        # MAS TRM — Access control (reviewer authority)
                    "CPS234.15",       # APRA CPS 234 — Information security controls
                    "FFIEC.D2.TI",     # FFIEC CAT — Threat intelligence (human-in-loop)
                    "GDPR.A22",        # GDPR — Automated decision-making (human review path)
                ],
                # Procurement decisions: supplier controls, sanctions, supply chain
                "procurement": [
                    "OWASP.A09",       # OWASP — Supply chain risk
                    "CSF2.ID.AM-01",   # NIST CSF 2.0 — Asset management
                    "DORA.Art28",      # DORA — Third-party ICT risk management
                    "FFIEC.D4.EX",     # FFIEC CAT — External dependency management
                    "MASTRM.13",       # MAS TRM — Outsourcing risk
                    "SOC2.CC8.1",      # SOC 2 — Change management controls
                ],
                # Financial decisions: AML, wire limits, transaction controls
                "financial": [
                    "AIRM.ME.01",      # NIST AI RMF — AI risk measurement
                    "DORA.Art6",       # DORA — ICT risk management
                    "FFIEC.D3.CY",     # FFIEC CAT — Cybersecurity controls
                    "PCI4.10.3",       # PCI DSS — Audit log protection
                    "SOC2.CC9.1",      # SOC 2 — Risk mitigation activities
                ],
                # IT operations decisions: change management, maintenance windows
                "it_ops": [
                    "NERC.CIP007",     # NERC CIP — Systems security management
                    "NERC.CIP010",     # NERC CIP — Configuration change management
                    "IEC62443.SR2.1",  # IEC 62443 — Authorisation enforcement
                    "PURDUE.L3-L4",    # Purdue Model 2.0 — Zone separation
                    "SOC2.CC8.1",      # SOC 2 — Change management controls
                    "800-53.CM-3",     # NIST 800-53 — Configuration change control
                    "ISO27K.A5.36",    # ISO 27001 — Compliance with policies
                ],
                # Clinical decisions: healthcare AI, dosage safety, PHI controls
                "clinical": [
                    "HIPAA.164.308a1", # HIPAA — Security management process
                    "HIPAA.164.308a3", # HIPAA — Workforce security
                    "HIPAA.164.312b",  # HIPAA — Audit controls
                    "FDA11.11.10d",    # FDA — System access limited to authorised individuals
                    "FDA11.11.10e",    # FDA — Audit trails
                ],
                # Trading decisions: position limits, fat-finger, market risk
                "trading": [
                    "AIRM.ME.01",      # NIST AI RMF — Risk measurement
                    "DORA.Art6",       # DORA — ICT risk management
                    "FFIEC.D3.CY",     # FFIEC CAT — Cybersecurity controls
                    "SOC2.CC9.1",      # SOC 2 — Risk mitigation
                ],
                # Content decisions: generative AI output, GDPR Art.22
                "content": [
                    "GDPR.A5",         # GDPR — Data minimisation / PII
                    "GDPR.A22",        # GDPR — Automated decision-making
                    "EUAI.A13",        # EU AI Act — Transparency
                    "OWASP.A02",       # OWASP — Insecure output handling
                    "OWASP.A06",       # OWASP — Sensitive data exposure
                    "COL.SB205.10",    # Colorado AI Act — Disclosure of AI use
                ],
                # Legal decisions: contract authority, legal hold, e-discovery
                "legal": [
                    "ISO27K.A5.1",     # ISO 27001 — Policies for information security
                    "ISO27K.A5.2",     # ISO 27001 — Roles and responsibilities
                    "SOC2.CC8.1",      # SOC 2 — Change management controls
                    "800-53.CM-3",     # NIST 800-53 — Configuration change control
                ],
                # HR decisions: workforce, access provisioning, salary
                "hr": [
                    "HIPAA.164.308a3", # HIPAA — Workforce security
                    "ISO27K.A5.2",     # ISO 27001 — Roles and responsibilities
                    "FDA11.11.10d",    # FDA — Access controls
                    "MASTRM.5",        # MAS TRM — Access control
                    "CPS234.15",       # APRA CPS 234 — Information security controls
                ],
                # Custom decisions: general compliance posture
                "custom": [
                    "ISO42K.6.1",      # ISO/IEC 42001 — Actions to address AI risks
                    "AIRM.GV.01",      # NIST AI RMF — AI risk management policies
                ],
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

    def stage_latency_stats(self) -> Dict[str, Dict[str, float]]:
        """
        Per-stage P50 / P99 latency breakdown across all observed executions.

        Returns:
            {stage_name: {"p50_ms": float, "p99_ms": float, "samples": int}}

        Merges built-in pipeline stage timings with any custom-stage timings
        recorded by the StageRegistry (if one is attached).
        """
        def _pct(sorted_s: list, p: float) -> float:
            if not sorted_s:
                return 0.0
            idx = (len(sorted_s) - 1) * p / 100.0
            lo, frac = int(idx), idx % 1
            hi = min(lo + 1, len(sorted_s) - 1)
            return sorted_s[lo] * (1 - frac) + sorted_s[hi] * frac

        result: Dict[str, Dict[str, float]] = {}

        # Built-in stages (tracked by _timed_call)
        with self._stage_latency_lock:
            snapshot = {k: list(v) for k, v in self._stage_latencies.items()}
        for stage_name, samples in snapshot.items():
            if samples:
                s = sorted(samples)
                result[stage_name] = {
                    "p50_ms":  round(_pct(s, 50), 3),
                    "p99_ms":  round(_pct(s, 99), 3),
                    "samples": len(s),
                }

        # Custom stages from StageRegistry (if attached)
        if self.stage_registry and hasattr(self.stage_registry, "get_stage_latency_stats"):
            for stage_name, stats in self.stage_registry.get_stage_latency_stats().items():
                if stage_name not in result:
                    result[stage_name] = stats

        return result

    @property
    def stats(self) -> Dict[str, Any]:
        stats = dict(self.audit_logger.summary_stats() or {})
        if "block_rate_pct" not in stats:
            total = float(stats.get("total", 0) or 0)
            blocked = float((stats.get("status_breakdown") or {}).get("blocked", 0) or 0)
            stats["block_rate_pct"] = (blocked / total * 100.0) if total > 0 else 0.0
        return stats

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
            "version":          __import__("glassbox").__version__,
            "environment":      self.environment,
            "total_decisions":  stats.get("total", 0),
            "block_rate_pct":   stats.get("block_rate_pct", 0),
            "avg_latency_ms":   stats.get("avg_latency_ms"),
            "p99_latency_ms":   stats.get("p99_latency_ms"),
            "audit_persisted":  stats.get("persisted", 0),
            "audit_failed":     stats.get("failed", 0),
            "audit_async_queue_depth": stats.get("async_queue_depth", 0),
            "audit_async_queue_capacity": stats.get("async_queue_capacity", 0),
            "audit_async_worker_alive": stats.get("async_worker_alive", False),
            "policies":         len(self.policy_engine.policies),
            "contracts":        len(self.list_contracts()),
            # O2: Per-stage latency breakdown for production debugging.
            "stage_latency_p50_ms": {k: v["p50_ms"] for k, v in self.stage_latency_stats().items()},
            "stage_latency_p99_ms": {k: v["p99_ms"] for k, v in self.stage_latency_stats().items()},
            "event_bus":             self.event_bus is not None,
            "audit_repo":            self.audit_repo is not None,
            "workflow_engine":       self.workflow_engine is not None,
            "compliance_catalogue":  self.compliance_catalogue is not None,
            "access_control":        self.access_control is not None,
            "hash_audit":            self.hash_audit is not None,
            "stage_registry":        self.stage_registry is not None,
            "trace_enabled":         self.trace_enabled,
            "async_audit_writes":    self.async_audit_writes,
            "side_effect_mode":      self.side_effect_mode,
        }

    def shutdown(self, timeout: float = None) -> None:
        """Gracefully shutdown pipeline (v1.0.1 - CRITICAL-5: lifecycle management).
        
        Args:
            timeout: Retained for compatibility with callers that pass an atexit budget.
        """
        with self._shutdown_lock:
            if self._shutdown_complete:
                self.shared_executor = None
                self._thread_pool = None
                return

            _active_pipelines.discard(self)

            # [v1.0.1 CRITICAL FIX] Shutdown audit logger background write executor
            # Don't log during shutdown—logging system may be closed during atexit
            if self.audit_logger and hasattr(self.audit_logger, "shutdown"):
                try:
                    self.audit_logger.shutdown()
                except Exception as exc:
                    log.debug("Audit logger shutdown failed: %s", exc, exc_info=True)

            if self.shared_executor is not None:
                try:
                    self.shared_executor.shutdown(wait=True)
                except Exception as exc:
                    log.debug("Pipeline thread pool shutdown failed: %s", exc, exc_info=True)
                finally:
                    self.shared_executor = None
                    self._thread_pool = None

            try:
                if self.workflow_engine and hasattr(self.workflow_engine, "shutdown"):
                    self.workflow_engine.shutdown()
                elif self.workflow_engine and hasattr(self.workflow_engine, "stop_monitor"):
                    self.workflow_engine.stop_monitor()
            except Exception as exc:
                log.debug("Workflow monitor shutdown failed: %s", exc, exc_info=True)

            try:
                if self.audit_repo and hasattr(self.audit_repo, "close"):
                    self.audit_repo.close()
            except Exception as exc:
                log.debug("Audit repository shutdown failed: %s", exc, exc_info=True)

            try:
                if self.wal and hasattr(self.wal, "shutdown"):
                    self.wal.shutdown()
            except Exception as exc:
                log.debug("WAL shutdown failed: %s", exc, exc_info=True)

            try:
                if self.event_bus and hasattr(self.event_bus, "shutdown"):
                    self.event_bus.shutdown()
            except Exception as exc:
                log.debug("Event bus shutdown failed: %s", exc, exc_info=True)

            self._shutdown_complete = True

    def __enter__(self):
        """Context manager entry (v1.0.1 - CRITICAL-5)."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - ensures cleanup."""
        self.shutdown()
        return False
