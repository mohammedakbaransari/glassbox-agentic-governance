"""
GlassBox Framework — Enterprise Governance Pipeline  (v1.2.0)
=============================================================

``EnterpriseGovernancePipeline`` composes the six enterprise security modules
into the core ``GovernancePipeline`` so that a single class wires everything
together for production deployments.

What it adds on top of the base pipeline:

  1. **RequestContext propagation** — if an HTTP ``RequestContext`` is active in
     the current thread (set by the API layer), its ``tenant_id``, ``user_id``,
     and ``correlation_id`` are automatically copied into ``DecisionContext``
     before the pipeline runs.

  2. **RBAC access control** — if ``access_control`` is provided, every request
     is checked for the ``"decisions/submit"`` permission before the pipeline
     executes.  Rejected requests are returned as ``BLOCKED`` responses without
     touching the audit store.

  3. **Hash-chain audit** — if ``hash_audit`` is provided, every processed
     decision is written to the tamper-evident audit at the end of the pipeline,
     in addition to any standard ``AuditLogger`` provided.

Usage::

    from glassbox.governance.enterprise_pipeline import EnterpriseGovernancePipeline
    from glassbox.governance.access_control import AccessControl
    from glassbox.governance.advanced_audit import TamperEvidentAuditLogger

    ac = AccessControl()
    ac.register_role(...)
    ac.register_user(...)

    hash_audit = TamperEvidentAuditLogger(db_path="audit.db")

    pipeline = EnterpriseGovernancePipeline(
        access_control=ac,
        hash_audit=hash_audit,
    )

    response = pipeline.process(request)

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from glassbox.governance.logging_manager import get_logger
from glassbox.governance.models import (
    DecisionContext,
    DecisionRequest,
    DecisionResponse,
    FinalStatus,
)
from glassbox.governance.pipeline import GovernancePipeline

log = get_logger("enterprise_pipeline")


class EnterpriseGovernancePipeline(GovernancePipeline):
    """
    ``GovernancePipeline`` with enterprise security modules wired in.

    All constructor keyword arguments are forwarded to ``GovernancePipeline``.
    The enterprise-specific arguments below are consumed before forwarding.

    Args:
        access_control: Optional :class:`~glassbox.governance.access_control.AccessControl`
            instance.  When supplied, every ``process()`` call checks that the
            requesting user holds the ``decisions/submit`` permission.
        hash_audit: Optional :class:`~glassbox.governance.advanced_audit.TamperEvidentAuditLogger`
            instance.  When supplied, a hash-chained record is written for every
            completed (EXECUTED, BLOCKED, or PENDING_REVIEW) decision.
        **kwargs: Forwarded to :class:`~glassbox.governance.pipeline.GovernancePipeline`.
    """

    def __init__(
        self,
        access_control=None,  # AccessControl | None
        hash_audit=None,      # TamperEvidentAuditLogger | None
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._access_control = access_control
        self._hash_audit = hash_audit

    # ── Override process() ────────────────────────────────────────────────────

    def process(
        self,
        request: DecisionRequest,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> DecisionResponse:
        """
        Enterprise pipeline: propagate request context, check RBAC, then run
        the governance pipeline, then write a hash-chained audit record.
        """
        request = self._apply_request_context(request)

        if self._access_control:
            user_id = self._get_user_id(request)
            if user_id and not self._access_control.has_permission(
                user_id, "decisions", "submit"
            ):
                log.warning(
                    "Enterprise pipeline: access denied for user %s on request %s",
                    user_id, request.request_id,
                )
                return self._build_blocked_response(
                    request,
                    f"Access denied: user '{user_id}' lacks decisions/submit permission",
                )

        response = super().process(request, request_metadata)

        if self._hash_audit and response.audit_record:
            self._write_hash_audit(request, response)

        return response

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_request_context(self, request: DecisionRequest) -> DecisionRequest:
        """Propagate active RequestContext into DecisionContext if not already set."""
        try:
            from glassbox.governance.request_context import RequestContext
            rc = RequestContext.get_current()

            # Auto-created default contexts have no user_id; skip propagation.
            if rc.user_id is None and rc.tenant_id is None:
                return request

            # Build or merge into the request's DecisionContext
            if request.context is None:
                new_ctx = rc.to_decision_context()
            else:
                # Merge: request fields take precedence; RC fills missing ones
                existing_meta = dict(request.context.metadata or {})
                if rc.tenant_id and "tenant_id" not in existing_meta:
                    existing_meta["tenant_id"] = rc.tenant_id
                if rc.user_id and "user_id" not in existing_meta:
                    existing_meta["user_id"] = rc.user_id
                if rc.correlation_id and "correlation_id" not in existing_meta:
                    existing_meta["correlation_id"] = rc.correlation_id
                new_ctx = DecisionContext(
                    session_id=request.context.session_id or rc.request_id,
                    environment=request.context.environment,
                    source_system=request.context.source_system,
                    metadata=existing_meta,
                )

            import dataclasses
            return dataclasses.replace(request, context=new_ctx)

        except Exception:
            # Context propagation is best-effort; never break the decision path
            return request

    @staticmethod
    def _get_user_id(request: DecisionRequest) -> Optional[str]:
        """Extract user_id from DecisionContext.metadata."""
        if request.context and request.context.metadata:
            return request.context.metadata.get("user_id")
        return None

    def _build_blocked_response(
        self, request: DecisionRequest, message: str
    ) -> DecisionResponse:
        """Return a BLOCKED response without running the pipeline."""
        from glassbox.governance.models import (
            AuditRecord,
            CircuitBreakerResult,
            PolicyEvaluation,
            PolicyResult,
        )
        import uuid
        from datetime import datetime, timezone

        decision_id = str(uuid.uuid4())
        record = AuditRecord(
            decision_id=decision_id,
            request_id=request.request_id,
            agent_id=request.agent_id,
            decision_type=request.decision_type,
            payload=request.payload,
            context=request.context,
            final_status=FinalStatus.BLOCKED,
            policy_result=PolicyResult(
                passed=False,
                violations=[message],
                evaluated_policies=[
                    PolicyEvaluation(
                        policy_id="ENTERPRISE-RBAC",
                        policy_name="Enterprise Access Control",
                        result="fail",
                        message=message,
                    )
                ],
            ),
            circuit_breaker_result=CircuitBreakerResult(triggered=False),
            timestamp=datetime.now(timezone.utc),
        )
        return DecisionResponse(
            decision_id=decision_id,
            final_status=FinalStatus.BLOCKED,
            policy_violations=[message],
            circuit_breaker_triggered=False,
            message=message,
            audit_record=record,
        )

    def _write_hash_audit(
        self, request: DecisionRequest, response: DecisionResponse
    ) -> None:
        """Write a tamper-evident audit record via the hash-chain logger."""
        try:
            user_id = self._get_user_id(request) or "unknown"
            self._hash_audit.log_action(
                user_id=user_id,
                action="decision_processed",
                resource_type="decision",
                resource_id=response.decision_id,
                result=response.final_status.value,
                context={
                    "agent_id": request.agent_id,
                    "decision_type": request.decision_type.value
                    if hasattr(request.decision_type, "value")
                    else str(request.decision_type),
                    "risk_score": getattr(response, "risk_score", None),
                    "tenant_id": (request.context.metadata or {}).get("tenant_id")
                    if request.context
                    else None,
                },
            )
        except Exception as exc:
            log.warning(
                "Enterprise pipeline: hash-chain audit write failed: %s", exc
            )
