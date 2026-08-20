"""The approval service (GB-002, Workstream D).

Turns the domain's :class:`~glassbox.domain.decision.ApprovalState` from a
label on a decision record into an operable lifecycle: a reviewer can approve,
reject, escalate or the platform can expire an overdue request, and every
transition is durable because it goes through the workflow engine's own
persisted step history (`glassbox.store.repository.WorkflowRepository`) --
this service adds no second, competing source of truth.

Two things this service deliberately does **not** do, both load-bearing:

* **It never dispatches.** Approving a workflow updates the workflow's own
  state; it does not call :class:`~glassbox.ports.dispatcher.Dispatcher`.
  Obligation discharge on approval is explicitly out of scope (tracked
  separately) so that "approval cases are never auto-discharged" remains true
  by construction, not by convention.
* **It never imports a concrete workflow engine.** Only
  :class:`~glassbox.ports.workflow.WorkflowGateway`, satisfied structurally by
  whatever :meth:`~glassbox.app.composition.GovernanceRuntime.with_workflow_engine`
  was given.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from glassbox.app.composition import GovernanceRuntime
from glassbox.app.observability import get_logger, log_error
from glassbox.domain.decision import ApprovalState
from glassbox.domain.errors import (
    ApprovalGatewayUnavailableError,
    ApprovalNotFoundError,
    DomainValidationError,
)
from glassbox.domain.serialization import require_identifier, require_non_empty

__all__ = ["ApprovalRecord", "ApprovalService"]

_logger = get_logger("approval_service")

#: Workflow-engine state strings that map onto a terminal ApprovalState.
_TERMINAL_STATE_MAP = {
    "approved": ApprovalState.APPROVED,
    "rejected": ApprovalState.REJECTED,
    "expired": ApprovalState.EXPIRED,
    "revoked": ApprovalState.REVOKED,
}


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    """A read model of one decision's approval status, for API and CLI callers.

    Deliberately not the domain :class:`~glassbox.domain.decision.Approval`
    dataclass: that type's invariants are keyed to a single ``ProposedAction``
    reconstructed from evidence, which callers of this service do not hold.
    This is the operational view -- what the workflow engine actually knows --
    projected onto the same :class:`ApprovalState` vocabulary.
    """

    decision_id: str
    workflow_id: str
    state: ApprovalState
    assigned_to: Optional[str]
    escalate_to: Optional[str]
    sla_breached: bool
    step_count: int

    def as_evidence(self) -> dict:
        """Return the canonical representation for API responses and logs."""
        return {
            "decision_id": self.decision_id,
            "workflow_id": self.workflow_id,
            "state": self.state.value,
            "assigned_to": self.assigned_to,
            "escalate_to": self.escalate_to,
            "sla_breached": self.sla_breached,
            "step_count": self.step_count,
        }


class ApprovalService:
    """Operates the human-review lifecycle for decisions awaiting approval.

    Args:
        runtime: A composed :class:`~glassbox.app.composition.GovernanceRuntime`.
            Its ``workflow_engine`` must be attached via
            :meth:`~glassbox.app.composition.GovernanceRuntime.with_workflow_engine`
            before any method here is called with effect.
    """

    __slots__ = ("_runtime",)

    def __init__(self, runtime: GovernanceRuntime) -> None:
        self._runtime = runtime

    def approve(
        self, decision_id: str, *, actor: str, notes: str = "", min_approvers: int = 1
    ) -> ApprovalRecord:
        """Record an approval. Returns ``APPROVED`` only once quorum is reached.

        Raises:
            ApprovalNotFoundError: If no workflow exists for ``decision_id``.
            ApprovalGatewayUnavailableError: If no workflow engine is wired.
        """
        engine = self._require_engine()
        workflow = engine.get_by_decision(require_identifier(decision_id, field="decision_id"))
        if workflow is None:
            raise ApprovalNotFoundError(
                "no approval workflow exists for this decision", decision_id=decision_id
            )
        require_non_empty(actor, field="actor")
        updated = engine.approve(
            workflow.workflow_id, actor, notes, min_approvers=min_approvers
        )
        return self._project(updated or workflow)

    def reject(self, decision_id: str, *, actor: str, notes: str = "") -> ApprovalRecord:
        """Reject a pending decision. It will remain blocked, never dispatched.

        Raises:
            ApprovalNotFoundError: If no workflow exists for ``decision_id``.
            ApprovalGatewayUnavailableError: If no workflow engine is wired.
        """
        engine = self._require_engine()
        workflow = engine.get_by_decision(require_identifier(decision_id, field="decision_id"))
        if workflow is None:
            raise ApprovalNotFoundError(
                "no approval workflow exists for this decision", decision_id=decision_id
            )
        require_non_empty(actor, field="actor")
        updated = engine.reject(workflow.workflow_id, actor, notes)
        return self._project(updated or workflow)

    def escalate(
        self, decision_id: str, *, actor: str, escalate_to: str, notes: str = ""
    ) -> ApprovalRecord:
        """Escalate a pending decision to a senior reviewer.

        Raises:
            ApprovalNotFoundError: If no workflow exists for ``decision_id``.
            ApprovalGatewayUnavailableError: If no workflow engine is wired.
        """
        engine = self._require_engine()
        workflow = engine.get_by_decision(require_identifier(decision_id, field="decision_id"))
        if workflow is None:
            raise ApprovalNotFoundError(
                "no approval workflow exists for this decision", decision_id=decision_id
            )
        require_non_empty(actor, field="actor")
        require_identifier(escalate_to, field="escalate_to")
        updated = engine.escalate(workflow.workflow_id, actor, escalate_to, notes)
        return self._project(updated or workflow)

    def revoke(self, decision_id: str, *, actor: str, notes: str = "") -> ApprovalRecord:
        """Withdraw a still-pending approval request (not a reviewer rejection).

        Raises:
            ApprovalNotFoundError: If no workflow exists for ``decision_id``.
            ApprovalGatewayUnavailableError: If no workflow engine is wired.
        """
        engine = self._require_engine()
        workflow = engine.get_by_decision(require_identifier(decision_id, field="decision_id"))
        if workflow is None:
            raise ApprovalNotFoundError(
                "no approval workflow exists for this decision", decision_id=decision_id
            )
        require_non_empty(actor, field="actor")
        if not hasattr(engine, "revoke"):
            raise DomainValidationError(
                "the wired workflow engine does not support revocation", field="workflow_engine"
            )
        updated = engine.revoke(workflow.workflow_id, actor, notes)  # type: ignore[attr-defined]
        return self._project(updated or workflow)

    def expire_overdue(self, *, actor: str = "system") -> List[ApprovalRecord]:
        """Expire every SLA-breached, still-pending workflow.

        Intended to be called periodically (e.g. by a scheduler) rather than
        per-request; expiry is a fact about elapsed time, not something a
        caller should be able to force on a workflow that has not breached.
        """
        engine = self._require_engine()
        if not hasattr(engine, "expire_overdue"):
            expired = []
            for workflow in engine.list_sla_breached():
                if hasattr(engine, "expire"):
                    result = engine.expire(workflow.workflow_id, actor)  # type: ignore[attr-defined]
                    if result is not None:
                        expired.append(result)
        else:
            expired = engine.expire_overdue(actor)  # type: ignore[attr-defined]
        return [self._project(workflow) for workflow in expired]

    def get_status(self, decision_id: str) -> Optional[ApprovalRecord]:
        """Return the current approval status for a decision, or ``None``."""
        engine = self._require_engine()
        workflow = engine.get_by_decision(require_identifier(decision_id, field="decision_id"))
        if workflow is None:
            return None
        return self._project(workflow)

    def list_pending(self) -> List[ApprovalRecord]:
        """All decisions currently awaiting human review."""
        engine = self._require_engine()
        return [self._project(workflow) for workflow in engine.list_pending()]

    # ----------------------------------------------------------------- #
    # Internal
    # ----------------------------------------------------------------- #

    def _require_engine(self):
        engine = self._runtime.workflow_engine
        if engine is None:
            raise ApprovalGatewayUnavailableError(
                "no workflow engine is wired into this runtime; "
                "attach one via GovernanceRuntime.with_workflow_engine"
            )
        return engine

    def _project(self, workflow) -> ApprovalRecord:
        state = _TERMINAL_STATE_MAP.get(workflow.state)
        if state is None:
            # "pending", "in_review" and "escalated" are all still under review;
            # escalation reassigns the reviewer, it does not leave IN_REVIEW.
            state = ApprovalState.IN_REVIEW if workflow.state != "pending" else ApprovalState.PENDING
        try:
            sla_breached = bool(workflow.is_sla_breached())
        except Exception as exc:  # pragma: no cover - defensive
            log_error(_logger, exc, message="could not evaluate SLA breach state")
            sla_breached = False
        return ApprovalRecord(
            decision_id=workflow.decision_id,
            workflow_id=workflow.workflow_id,
            state=state,
            assigned_to=workflow.assigned_to,
            escalate_to=workflow.escalate_to,
            sla_breached=sla_breached,
            step_count=len(workflow.steps),
        )
