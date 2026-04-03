"""
GlassBox — Multi-Tenancy & Context Isolation  (v1.0.0)
========================================================
Provides strict namespace isolation between tenants sharing one
GlassBox deployment.

Problem without multi-tenancy:
  If Organisation A and Organisation B share one GovernancePipeline:
  - Agent "pricing_agent" for Org A shares anomaly baselines with
    "pricing_agent" for Org B → Org A's behaviour poisons Org B's baseline
  - Velocity counters are shared → Org B can exhaust Org A's rate limit
  - AuditRepository records for both orgs are mixed together
  - Policies registered by Org A are applied to Org B's decisions

Solution — TenantContext:
  Every DecisionRequest carries a tenant_id.
  All stateful components (AnomalyDetector, VelocityBreaker, PolicyEngine,
  AuditLogger) are namespaced by tenant_id.
  A TenantRegistry owns per-tenant component instances.

Design:
  - Lazy tenant instantiation: tenant components created on first use
  - Thread-safe: RLock on tenant registry
  - Zero-copy: each tenant has its own AnomalyDetector, VelocityBreaker,
    and PolicyEngine instance — no shared mutable state whatsoever
  - Compatible with the existing GovernancePipeline API

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.anomaly_detector import AnomalyDetector
from glassbox.governance.audit_logger     import AuditLogger
from glassbox.governance.models           import (
    DecisionContext, DecisionRequest, DecisionResponse,
    DecisionType, FinalStatus,
)
from glassbox.governance.policy_engine    import Policy, PolicyEngine
from glassbox.governance.velocity_breaker import VelocityBreaker

if TYPE_CHECKING:
    from glassbox.governance.pipeline import GovernancePipeline


# ── Per-tenant component bundle ────────────────────────────────────────────────

class TenantComponents:
    """
    All stateful, mutable components for one tenant.
    Completely isolated — zero shared state with other tenants.
    """

    def __init__(
        self,
        tenant_id:         str,
        base_policies:     Optional[List[Policy]] = None,
        velocity_config:   Optional[Dict]         = None,
        anomaly_config:    Optional[Dict]         = None,
        log_dir:           Optional[str]          = None,
    ):
        self.tenant_id       = tenant_id
        vel_cfg              = velocity_config or {}
        anom_cfg             = anomaly_config  or {}

        # Each tenant gets its own isolated instances
        self.policy_engine    = PolicyEngine(policies=base_policies)
        self.velocity_breaker = VelocityBreaker(
            max_decisions        = vel_cfg.get("max_decisions", 100),
            window_seconds       = vel_cfg.get("window_seconds", 60),
            cooldown_seconds     = vel_cfg.get("cooldown_seconds", 300),
            ecosystem_max        = vel_cfg.get("ecosystem_max", 1000),
            ecosystem_window_seconds   = vel_cfg.get("ecosystem_window_seconds", 60),
            ecosystem_cooldown_seconds = vel_cfg.get("ecosystem_cooldown_seconds", 120),
        )
        self.anomaly_detector = AnomalyDetector(
            z_threshold  = anom_cfg.get("z_threshold", 3.0),
            min_samples  = anom_cfg.get("min_samples", 10),
            window_size  = anom_cfg.get("window_size", 50),
        )
        # Tenant-specific audit log directory
        tenant_log = f"{log_dir}/{tenant_id}" if log_dir else None
        self.audit_logger = AuditLogger(log_dir=tenant_log, echo=False)

    def register_policy(self, policy: Policy) -> None:
        """Register a policy for this tenant only."""
        self.policy_engine.register(policy)


# ── Tenant Registry ────────────────────────────────────────────────────────────

class TenantRegistry:
    """
    Thread-safe registry of per-tenant component bundles.

    Usage:
        registry = TenantRegistry(base_policies=DEFAULT_POLICIES)

        # Get (or lazily create) components for a tenant
        components = registry.get("org_a")
        components.policy_engine.evaluate(...)
        components.velocity_breaker.check("agent_id")

        # Register a tenant-specific policy
        registry.register_policy("org_a", my_custom_policy)

        # List active tenants
        tenants = registry.list_tenants()
    """

    def __init__(
        self,
        base_policies:   Optional[List[Policy]] = None,
        velocity_config: Optional[Dict]         = None,
        anomaly_config:  Optional[Dict]         = None,
        log_dir:         Optional[str]          = None,
    ):
        self._base_policies   = base_policies
        self._velocity_config = velocity_config
        self._anomaly_config  = anomaly_config
        self._log_dir         = log_dir
        self._tenants: Dict[str, TenantComponents] = {}
        self._lock    = threading.RLock()

    def get(self, tenant_id: str) -> TenantComponents:
        """Get tenant components, creating them if this is a new tenant."""
        # Fast path
        if tenant_id in self._tenants:
            return self._tenants[tenant_id]
        # Slow path: create under lock
        with self._lock:
            if tenant_id not in self._tenants:
                self._tenants[tenant_id] = TenantComponents(
                    tenant_id=tenant_id,
                    base_policies=self._base_policies,
                    velocity_config=self._velocity_config,
                    anomaly_config=self._anomaly_config,
                    log_dir=self._log_dir,
                )
            return self._tenants[tenant_id]

    def register_policy(self, tenant_id: str, policy: Policy) -> None:
        self.get(tenant_id).register_policy(policy)

    def list_tenants(self) -> List[str]:
        with self._lock:
            return list(self._tenants.keys())

    def remove_tenant(self, tenant_id: str) -> bool:
        with self._lock:
            if tenant_id in self._tenants:
                del self._tenants[tenant_id]
                return True
            return False

    def tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        comp = self.get(tenant_id)
        stats = comp.audit_logger.summary_stats()
        return {
            "tenant_id":  tenant_id,
            "decisions":  stats.get("total", 0),
            "block_rate": stats.get("block_rate_pct", 0),
            "policies":   len(comp.policy_engine.policies),
        }


# ── Multi-Tenant Pipeline ─────────────────────────────────────────────────────

class MultiTenantPipeline:
    """
    A governance pipeline with strict per-tenant isolation.

    Routes each request to the correct tenant's component set
    based on tenant_id in the request context metadata.

    Usage:
        pipeline = MultiTenantPipeline(
            registry=TenantRegistry(),
            base_pipeline_fn=lambda comps: GovernancePipeline(
                policy_engine=comps.policy_engine,
                velocity_breaker=comps.velocity_breaker,
                anomaly_detector=comps.anomaly_detector,
                audit_logger=comps.audit_logger,
            )
        )
        response = pipeline.process(request, tenant_id="org_a")
    """

    def __init__(
        self,
        registry:         TenantRegistry,
        base_pipeline_fn: Callable[[TenantComponents], "GovernancePipeline"],
    ):
        self.registry          = registry
        self._pipeline_fn      = base_pipeline_fn
        self._pipelines: Dict[str, "GovernancePipeline"] = {}
        self._lock = threading.RLock()

    def _get_pipeline(self, tenant_id: str) -> "GovernancePipeline":
        if tenant_id in self._pipelines:
            return self._pipelines[tenant_id]
        with self._lock:
            if tenant_id not in self._pipelines:
                comps = self.registry.get(tenant_id)
                self._pipelines[tenant_id] = self._pipeline_fn(comps)
            return self._pipelines[tenant_id]

    def process(
        self,
        request:   DecisionRequest,
        tenant_id: str,
    ) -> DecisionResponse:
        """
        Process a decision for a specific tenant.
        The tenant's policy engine, velocity breaker, and anomaly detector
        are used — completely isolated from other tenants.
        """
        # Stamp tenant_id into context metadata
        if request.context:
            request.context.metadata["tenant_id"] = tenant_id
        else:
            request.context = DecisionContext(
                metadata={"tenant_id": tenant_id})

        pipeline = self._get_pipeline(tenant_id)
        return pipeline.process(request)

    async def process_async(
        self,
        request:   DecisionRequest,
        tenant_id: str,
    ) -> DecisionResponse:
        if request.context:
            request.context.metadata["tenant_id"] = tenant_id
        pipeline = self._get_pipeline(tenant_id)
        return await pipeline.process_async(request)

    def register_policy(self, tenant_id: str, policy: Policy) -> None:
        self.registry.register_policy(tenant_id, policy)
        # Invalidate cached pipeline so it picks up new policy on next request
        with self._lock:
            if tenant_id in self._pipelines:
                self._pipelines[tenant_id].policy_engine.register(policy)

    def tenant_stats(self, tenant_id: str) -> Dict[str, Any]:
        return self.registry.tenant_stats(tenant_id)

    def list_tenants(self) -> List[str]:
        return self.registry.list_tenants()

    def health(self) -> Dict[str, Any]:
        tenants = self.list_tenants()
        return {
            "status":       "healthy",
            "active_tenants": len(tenants),
            "tenant_ids":   tenants,
        }


# ── Context Isolation Validator ────────────────────────────────────────────────

class ContextIsolationValidator:
    """
    Validates that no data leaks across tenant boundaries.

    Use in testing:
        validator = ContextIsolationValidator(registry)
        report    = validator.check_isolation(["org_a", "org_b"])
        assert report["all_isolated"]
    """

    def __init__(self, registry: TenantRegistry):
        self.registry = registry

    def check_isolation(self, tenant_ids: List[str]) -> Dict[str, Any]:
        """
        Verify that each tenant has its own component instances.
        Returns a report confirming (or denying) isolation.
        """
        issues  = []
        checked = {}

        for tid in tenant_ids:
            comps = self.registry.get(tid)
            checked[tid] = {
                "policy_engine_id":    id(comps.policy_engine),
                "velocity_breaker_id": id(comps.velocity_breaker),
                "anomaly_detector_id": id(comps.anomaly_detector),
                "audit_logger_id":     id(comps.audit_logger),
            }

        # Check no two tenants share the same component instance
        all_ids: Dict[str, List[str]] = {}
        for tid, comp_ids in checked.items():
            for comp_name, comp_id in comp_ids.items():
                key = f"{comp_name}:{comp_id}"
                all_ids.setdefault(key, []).append(tid)

        for key, tids in all_ids.items():
            if len(tids) > 1:
                issues.append(
                    f"Shared {key.split(':')[0]} instance between tenants: {tids}"
                )

        return {
            "all_isolated":  len(issues) == 0,
            "issues":        issues,
            "tenants_checked": len(tenant_ids),
            "component_map": checked,
        }
