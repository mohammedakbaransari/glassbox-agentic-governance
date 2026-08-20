"""Approval workflow gateway port (GB-002, Workstream D).

The decision service and the application layer must never import
``glassbox.workflow`` or ``glassbox.store`` directly -- both are legacy
packages banned from the rebuilt layers by ``tests/test_layering.py`` and the
import-linter contract in ``pyproject.toml``. This port is the seam: any
object that structurally satisfies :class:`WorkflowGateway` (duck typing via
``@runtime_checkable``) can be wired into a
:class:`~glassbox.app.composition.GovernanceRuntime` without the app layer
ever naming the concrete engine that implements it.

This is deliberately a thin, behavioural contract over the existing
``glassbox.workflow.workflow_engine.WorkflowEngine`` shape rather than a new
abstraction invented from scratch -- the engine and its SQLite-backed
repository already provide idempotent creation, state transitions and a
durable per-step audit trail; this port just gives the app layer a typed,
conformance-checkable way to depend on that behaviour.
"""

from __future__ import annotations

from typing import Any, List, Optional, Protocol, runtime_checkable

__all__ = ["WorkflowGateway"]


@runtime_checkable
class WorkflowGateway(Protocol):
    """Behavioural contract for an approval-workflow engine.

    Every method mirrors ``glassbox.workflow.workflow_engine.WorkflowEngine``
    exactly, so the reference engine satisfies this protocol with no adapter
    shim required.
    """

    def create_from_decision(
        self,
        decision_id: str,
        agent_id: str,
        decision_type: str,
        risk_score: float,
        violations: List[str],
        warnings: Optional[List[str]] = None,
        sla_minutes: Optional[int] = None,
        assigned_to: Optional[str] = None,
        escalate_to: Optional[str] = None,
    ) -> Any:
        """Create (idempotently) a workflow instance for a pending decision."""
        ...

    def get_by_decision(self, decision_id: str) -> Optional[Any]:
        """Return the workflow instance for a decision, or ``None``."""
        ...

    def approve(
        self, workflow_id: str, actor: str, notes: str = "", min_approvers: int = 1
    ) -> Optional[Any]:
        """Record an approval; returns the instance once quorum is reached."""
        ...

    def reject(self, workflow_id: str, actor: str, notes: str = "") -> Optional[Any]:
        """Reject a pending decision."""
        ...

    def escalate(
        self, workflow_id: str, actor: str, escalate_to: str, notes: str = ""
    ) -> Optional[Any]:
        """Escalate a pending decision to a senior reviewer."""
        ...

    def expire(self, workflow_id: str, actor: str = "system", notes: str = "") -> Optional[Any]:
        """Transition an SLA-breached workflow to its terminal expired state."""
        ...

    def list_pending(self) -> List[Any]:
        """All decisions currently awaiting human review."""
        ...

    def list_sla_breached(self) -> List[Any]:
        """Pending decisions whose SLA window has elapsed."""
        ...
