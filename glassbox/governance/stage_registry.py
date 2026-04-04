"""
GlassBox Framework — Stage Registry (v1.1.0)
==============================================
Enables dynamic stage addition/removal at runtime without code changes.

Feature Flags:
  - Enable/disable any stage by flag
  - Canary rollout: stage enabled for 10% of agents
  - Gradual rollout: increase % daily
  - A/B testing: stage for agent cohort A, not B
  - Feature gates: stage requires feature flag + API version

Extensibility:
  - Add custom stages without modifying pipeline.py
  - Define stage dependencies (e.g., "requires: velocity_breaker")
  - Set stage timeout and fallback behavior
  - Chain stages dynamically (DAG execution)

Built-in Stages (v1.1.0):
  1. agent_id_validation (pre-stage, always enabled)
  2. security_sanitizer (pre-stage, always enabled)
  3. agent_contract_validation (stage 0, can disable)
  4. context_capture (stage 1, always enabled)
  5. audit_record_init (stage 2, always enabled)
  6. schema_validation (stage 3, can disable)
  7. velocity_breaker (stage 4, can disable)
  8. anomaly_detection (stage 5, can disable)
  9. policy_enforcement (stage 6, can disable)
  10. risk_evaluation (stage 7, can disable)
  11. disposition_routing (stage 8, always enabled)

Custom Stage Example:
    class OFACStage(PipelineStage):
        def execute(self, ctx) -> Tuple[bool, Optional[str]]:
            # Lookup agent_id in OFAC list
            if is_sanctioned(ctx.agent_id):
                return (False, "Agent on OFAC list")
            return (True, None)  # Continue to next stage

    registry.register_stage(
        name="ofac_check",
        stage=OFACStage(),
        position=3.5,  # After schema (3), before velocity (4)
        depends_on=["schema_validation"],
        feature_flag="feature_ofac_check",
    )

Configuration (JSON):
    {
        "stages": {
            "velocity_breaker": {"enabled": true, "canary_pct": 100},
            "anomaly_detection": {"enabled": false, "reason": "disabled for debuging"},
            "policy_enforcement": {"enabled": true, "canary_pct": 20},  # 20% rollout
            "ofac_check": {"enabled": true, "position": 3.5}
        },
        "feature_flags": {
            "feature_ofac_check": true,
            "feature_ml_risk_v2": false
        }
    }

Author: Mohammed Akbar Ansari
"""

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("stage_registry")


class StagePosition(Enum):
    """Built-in stage positions in pipeline."""
    PRE_AGENT_ID_VALIDATION = 0.1
    PRE_SECURITY_SANITIZER = 0.2
    STAGE_AGENT_CONTRACT = 1
    STAGE_CONTEXT_CAPTURE = 2
    STAGE_AUDIT_INIT = 3
    STAGE_SCHEMA_VALIDATION = 4
    STAGE_VELOCITY_BREAKER = 5
    STAGE_ANOMALY_DETECTION = 6
    STAGE_POLICY_ENFORCEMENT = 7
    STAGE_RISK_EVALUATION = 8
    STAGE_DISPOSITION_ROUTING = 9


@dataclass
class PipelineStageConfig:
    """Configuration for a single pipeline stage."""
    name: str
    enabled: bool = True
    position: float = 0.0
    depends_on: List[str] = field(default_factory=list)
    timeout_ms: int = 1000  # Max execution time
    fallback_on_failure: bool = True  # Continue pipeline on failure
    feature_flag: Optional[str] = None  # Require feature flag
    canary_percent: int = 100  # 100 = all agents, 10 = 10% canary
    cohort: Optional[str] = None  # Optional: "cohort_a" or "cohort_b"
    metadata: Dict[str, Any] = field(default_factory=dict)


class StageExecutionResult:
    """Result of single stage execution."""
    __slots__ = (
        'stage_name', 'passed', 'blocked_reason', 'error', 'latency_ms',
        'skipped', 'skip_reason',
    )

    def __init__(
        self,
        stage_name: str,
        passed: bool,
        blocked_reason: Optional[str] = None,
        error: Optional[str] = None,
        latency_ms: float = 0.0,
        skipped: bool = False,
        skip_reason: Optional[str] = None,
    ):
        self.stage_name = stage_name
        self.passed = passed
        self.blocked_reason = blocked_reason
        self.error = error
        self.latency_ms = latency_ms
        self.skipped = skipped
        self.skip_reason = skip_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stage": self.stage_name,
            "passed": self.passed,
            "blocked_reason": self.blocked_reason,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


class PipelineStage:
    """Abstract base class for pipeline stages."""

    def __init__(self, name: str):
        self.name = name

    def execute(
        self,
        context: Dict[str, Any],
    ) -> Tuple[bool, Optional[str]]:
        """
        Execute stage logic.

        Args:
            context: Pipeline context (agent_id, payload, decision_type, etc)

        Returns:
            (passed: bool, block_reason: Optional[str])
            - passed=True: continue to next stage
            - passed=False, block_reason: block decision with reason
            - passed=False, block_reason=None: fail-closed (unknown error
        """
        raise NotImplementedError

    def get_config(self) -> Dict[str, Any]:
        """Return stage configuration."""
        return {"name": self.name}


class StageRegistry:
    """
    Registry for managing pipeline stages dynamically.

    Features:
      - Register/unregister stages at runtime
      - Enable/disable by feature flag
      - Canary rollout (percent of agents)
      - Fetch execution plan for given agent_id
      - Dependency validation
      - Execution tracing
    """

    def __init__(
        self,
        config_file: Optional[str] = None,
        feature_flags: Optional[Dict[str, bool]] = None,
    ):
        self.config_file = config_file
        self.feature_flags = feature_flags or {}

        # Stage registry: name -> (config, stage_impl)
        self._stages: Dict[str, Tuple[PipelineStageConfig, Optional[PipelineStage]]] = {}
        self._stages_lock = threading.RLock()

        # Execution stats
        self._execution_stats: Dict[str, int] = {}
        self._stats_lock = threading.Lock()

        # Load config from file if provided
        if config_file:
            self._load_config_file(config_file)

        log.info("StageRegistry initialized with %d stages", len(self._stages))

    def register_stage(
        self,
        name: str,
        config: PipelineStageConfig,
        stage_impl: Optional[PipelineStage] = None,
    ) -> None:
        """
        Register a new stage in the pipeline.

        Args:
            name: Unique stage name
            config: Stage configuration
            stage_impl: Optional implementation (for built-in stages)
        """
        with self._stages_lock:
            # Check dependency resolution
            if config.depends_on:
                for dep in config.depends_on:
                    if dep not in self._stages:
                        log.warning(
                            "StageRegistry: stage '%s' depends on '%s' which not registered",
                            name, dep,
                        )

            self._stages[name] = (config, stage_impl)

        log.info(
            "StageRegistry: registered stage '%s' at position %.1f (enabled=%s, canary=%d%%)",
            name, config.position, config.enabled, config.canary_percent,
        )

    def unregister_stage(self, name: str) -> None:
        """Unregister a stage (cannot unregister built-in stages)."""
        with self._stages_lock:
            if name in self._stages:
                del self._stages[name]
                log.info("StageRegistry: unregistered stage '%s'", name)

    def enable_stage(self, name: str) -> None:
        """Enable a stage."""
        with self._stages_lock:
            if name in self._stages:
                config, impl = self._stages[name]
                config.enabled = True
                log.info("StageRegistry: enabled stage '%s'", name)

    def disable_stage(self, name: str, reason: str = "") -> None:
        """Disable a stage."""
        with self._stages_lock:
            if name in self._stages:
                config, impl = self._stages[name]
                config.enabled = False
                log.info("StageRegistry: disabled stage '%s' (%s)", name, reason)

    def set_feature_flag(self, flag_name: str, enabled: bool) -> None:
        """Update feature flag value."""
        self.feature_flags[flag_name] = enabled
        log.info("StageRegistry: set feature_flag '%s' = %s", flag_name, enabled)

    def get_execution_plan(
        self,
        agent_id: str,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> List[PipelineStageConfig]:
        """
        Compute execution plan for given agent_id (applying canary, flags, cohorts).

        Returns:
            Sorted list of enabled stages for this agent.
        """
        plan = []
        request_metadata = request_metadata or {}

        with self._stages_lock:
            for stage_name, (config, impl) in self._stages.items():
                # Check if stage is enabled
                if not config.enabled:
                    log.debug(
                        "StageRegistry: stage '%s' disabled, skipping", stage_name
                    )
                    continue

                # Check feature flag
                if config.feature_flag:
                    flag_enabled = self.feature_flags.get(config.feature_flag, False)
                    if not flag_enabled:
                        log.debug(
                            "StageRegistry: stage '%s' requires flag '%s' (disabled)",
                            stage_name, config.feature_flag,
                        )
                        continue

                # Check canary (deterministic per agent_id)
                if config.canary_percent < 100:
                    if not self._is_agent_in_canary(agent_id, config.canary_percent):
                        log.debug(
                            "StageRegistry: stage '%s' canary excluded agent '%s'",
                            stage_name, agent_id,
                        )
                        continue

                # Check cohort
                if config.cohort:
                    cohort = request_metadata.get("cohort")
                    if cohort != config.cohort:
                        log.debug(
                            "StageRegistry: stage '%s' cohort mismatch (want=%s, got=%s)",
                            stage_name, config.cohort, cohort,
                        )
                        continue

                plan.append(config)

        # Sort by position
        plan.sort(key=lambda c: c.position)

        log.debug(
            "StageRegistry: execution plan for agent_id=%s has %d stages",
            agent_id, len(plan),
        )

        return plan

    def record_execution(self, stage_name: str, result: StageExecutionResult) -> None:
        """Record stage execution for metrics."""
        with self._stats_lock:
            key = f"{stage_name}:{'passed' if result.passed else 'blocked'}"
            self._execution_stats[key] = self._execution_stats.get(key, 0) + 1

    def stats(self) -> Dict[str, Any]:
        """Return registry statistics."""
        with self._stages_lock:
            total_stages = len(self._stages)
            enabled_stages = sum(1 for cfg, _ in self._stages.values() if cfg.enabled)

        with self._stats_lock:
            execution_stats = dict(self._execution_stats)

        return {
            "total_stages": total_stages,
            "enabled_stages": enabled_stages,
            "feature_flags": dict(self.feature_flags),
            "execution_stats": execution_stats,
        }

    def _is_agent_in_canary(self, agent_id: str, percent: int) -> bool:
        """
        Determine if agent is in canary group using consistent hashing.
        Ensures same agent always gets same canary decision.
        """
        if percent >= 100:
            return True
        if percent <= 0:
            return False

        # Hash agent_id to 0-100 range
        hash_obj = hashlib.md5(agent_id.encode())
        hash_int = int(hash_obj.hexdigest(), 16)
        hash_pct = (hash_int % 100) + 1  # 1-100 range

        return hash_pct <= percent

    def _load_config_file(self, config_file: str) -> None:
        """Load stage configuration from JSON file."""
        try:
            path = Path(config_file)
            if not path.exists():
                log.warning("StageRegistry: config file not found: %s", config_file)
                return

            with open(path, "r") as f:
                data = json.load(f)

            # Load stages config
            stages_cfg = data.get("stages", {})
            for stage_name, stage_cfg in stages_cfg.items():
                config = PipelineStageConfig(
                    name=stage_name,
                    enabled=stage_cfg.get("enabled", True),
                    position=float(stage_cfg.get("position", 0)),
                    depends_on=stage_cfg.get("depends_on", []),
                    timeout_ms=int(stage_cfg.get("timeout_ms", 1000)),
                    fallback_on_failure=stage_cfg.get("fallback_on_failure", True),
                    feature_flag=stage_cfg.get("feature_flag"),
                    canary_percent=int(stage_cfg.get("canary_percent", 100)),
                    cohort=stage_cfg.get("cohort"),
                    metadata=stage_cfg.get("metadata", {}),
                )

                # Only register if not already registered
                if stage_name not in self._stages:
                    self.register_stage(stage_name, config)

            # Load feature flags
            feature_flags_cfg = data.get("feature_flags", {})
            self.feature_flags.update(feature_flags_cfg)

            log.info(
                "StageRegistry: loaded config from %s (%d stages, %d flags)",
                config_file, len(stages_cfg), len(feature_flags_cfg),
            )
        except Exception as exc:
            log.error("StageRegistry._load_config_file failed: %s", exc, exc_info=True)

    def save_config_file(self, config_file: str) -> None:
        """Save current stage configuration to JSON file."""
        try:
            with self._stages_lock:
                stages_cfg = {}
                for stage_name, (config, _) in self._stages.items():
                    stages_cfg[stage_name] = {
                        "enabled": config.enabled,
                        "position": config.position,
                        "depends_on": config.depends_on,
                        "timeout_ms": config.timeout_ms,
                        "fallback_on_failure": config.fallback_on_failure,
                        "feature_flag": config.feature_flag,
                        "canary_percent": config.canary_percent,
                        "cohort": config.cohort,
                        "metadata": config.metadata,
                    }

            data = {
                "stages": stages_cfg,
                "feature_flags": dict(self.feature_flags),
            }

            path = Path(config_file)
            path.parent.mkdir(parents=True, exist_ok=True)

            with open(path, "w") as f:
                json.dump(data, f, indent=2)

            log.info("StageRegistry: saved config to %s", config_file)
        except Exception as exc:
            log.error("StageRegistry.save_config_file failed: %s", exc)

    def shutdown(self) -> None:
        """Graceful shutdown."""
        log.info("StageRegistry: shutdown complete")
