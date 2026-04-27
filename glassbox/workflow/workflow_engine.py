"""
GlassBox Framework — Workflow Engine  (v1.0.0)
===============================================
Manages the full lifecycle of decisions that route to HUMAN_REVIEW.

A WorkflowEngine:
  1. Creates a WorkflowInstance when a decision is pending review
  2. Tracks the approval/rejection steps with full audit trail
  3. Monitors SLA timers and triggers escalation on breach
  4. Publishes domain events at each state transition
  5. Provides a query API for review queues and dashboards

State machine:
  pending → in_review → approved (decision executes)
                      → rejected (decision blocked)
                      → escalated (routed to escalate_to)
          → timed_out (SLA breached, auto-escalated)

Workflow instances are persisted via WorkflowRepository (SQLite by default).
SLA monitoring runs as a background thread (opt-in, configurable).

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.models import FinalStatus
from glassbox.governance.logging_manager import get_logger
from glassbox.store.repository import (
    WorkflowInstance, WorkflowStep, WorkflowRepository,
    SQLiteWorkflowRepository,
)

if TYPE_CHECKING:
    from glassbox.events.event_bus import EventBus

log = get_logger("workflow")


class WorkflowEngine:
    """
    Decision approval workflow engine.

    Usage:
        engine = WorkflowEngine()

        # Create from a pending review decision
        wf = engine.create_from_decision(response)

        # Reviewer approves
        engine.approve(wf.workflow_id, actor="analyst@company.com",
                       notes="Verified against contract CT-2026-001")

        # Reviewer rejects
        engine.reject(wf.workflow_id, actor="manager@company.com",
                      notes="Supplier not cleared for this category")

        # Dashboard: list pending
        pending = engine.list_pending()
        breached = engine.list_sla_breached()
    """

    def __init__(
        self,
        repository:         Optional[WorkflowRepository] = None,
        event_bus:          Optional["EventBus"]         = None,
        default_sla_minutes: int                         = 60,
        monitor_sla:        bool                         = False,
        monitor_interval_s: int                          = 60,
    ):
        self.repo                 = repository or SQLiteWorkflowRepository(":memory:")
        self.event_bus            = event_bus
        self.default_sla_minutes  = default_sla_minutes
        self._monitor_interval_s  = monitor_interval_s
        self._monitor_thread: Optional[threading.Thread] = None
        self._stop_monitor        = threading.Event()
        self._quorum_state: Dict[str, list] = {}   # {workflow_id: [actor, ...]}
        self._quorum_lock         = threading.Lock()

        if monitor_sla:
            self._start_sla_monitor()

    # ── Create ────────────────────────────────────────────────────────────────

    def create_from_decision(
        self,
        decision_id:   str,
        agent_id:      str,
        decision_type: str,
        risk_score:    float,
        violations:    List[str],
        warnings:      List[str]   = None,
        sla_minutes:   int         = None,
        assigned_to:   Optional[str] = None,
        escalate_to:   Optional[str] = None,
    ) -> WorkflowInstance:
        """
        Create a new workflow instance for a decision pending human review.

        Idempotent: if a workflow already exists for decision_id (e.g. after
        WAL crash-recovery replay), the existing instance is returned unchanged
        rather than creating a duplicate.
        """
        # O8: Idempotency guard — WAL recovery may call this more than once for
        # the same decision_id if the process crashed between creating the workflow
        # and marking the WAL side-effect as successful.
        existing = self.repo.get_by_decision(decision_id)
        if existing is not None:
            log.info(
                "Workflow already exists for decision %s (workflow_id=%s); returning existing",
                decision_id, existing.workflow_id,
                extra={
                    "component": "workflow_engine",
                    "event": "create_idempotent_skip",
                    "decision_id": decision_id,
                    "workflow_id": existing.workflow_id,
                },
            )
            return existing

        workflow_id = str(uuid.uuid4())
        instance = WorkflowInstance(
            workflow_id   = workflow_id,
            decision_id   = decision_id,
            agent_id      = agent_id,
            decision_type = decision_type,
            risk_score    = risk_score,
            violations    = violations + (warnings or []),
            sla_minutes   = sla_minutes or self.default_sla_minutes,
            assigned_to   = assigned_to,
            escalate_to   = escalate_to,
        )
        self.repo.create(instance)

        if self.event_bus:
            from glassbox.events.event_bus import DecisionPendingReview
            self.event_bus.publish(DecisionPendingReview(
                decision_id=decision_id, agent_id=agent_id,
                decision_type=decision_type, risk_score=risk_score,
                workflow_id=workflow_id,
            ))

        return instance

    # ── State transitions ──────────────────────────────────────────────────────

    def start_review(
        self,
        workflow_id: str,
        actor:       str,
        notes:       str = "",
    ) -> Optional[WorkflowInstance]:
        """Mark a workflow as in_review (reviewer has picked it up)."""
        return self._transition(workflow_id, "review", actor, notes, "in_review")

    def approve(
        self,
        workflow_id:  str,
        actor:        str,
        notes:        str = "",
        min_approvers: int = 1,
    ) -> Optional[WorkflowInstance]:
        """
        Approve a pending decision.

        Args:
            workflow_id:   Workflow to approve.
            actor:         Reviewer ID recording the approval.
            notes:         Optional approval notes.
            min_approvers: Minimum approvals required before decision executes.
                           Set >1 for quorum/dual-control approval (e.g. min_approvers=2).

        Returns:
            WorkflowInstance — status will be "approved" only when quorum is reached.
        """
        # Thread-safe quorum tracking stored in engine (survives repo.get() fetches)
        with self._quorum_lock:
            actors = self._quorum_state.setdefault(workflow_id, [])
            if actor not in actors:
                actors.append(actor)
            count = len(actors)

        inst = self._get(workflow_id)
        if not inst:
            return None
        inst.approval_actors = list(actors)

        if count >= min_approvers:
            # Quorum reached — transition to approved
            inst = self._transition(workflow_id, "approve", actor,
                                    f"Quorum reached ({count}/{min_approvers}). {notes}",
                                    "approved", step_outcome="approved")
            if inst:
                inst.approval_actors = list(actors)
                self.repo.update(inst)
            if inst and self.event_bus:
                from glassbox.events.event_bus import DecisionExecuted
                self.event_bus.publish(DecisionExecuted(
                    decision_id=inst.decision_id, agent_id=inst.agent_id,
                    decision_type=inst.decision_type, risk_score=inst.risk_score or 0.0,
                    latency_ms=0.0,
                ))
            with self._quorum_lock:
                self._quorum_state.pop(workflow_id, None)
        else:
            # Partial — record step, persist
            step = WorkflowStep(
                step_id=str(uuid.uuid4()),
                workflow_id=workflow_id, step_type="approve",
                actor=actor,
                notes=f"Partial approval ({count}/{min_approvers}). "
                      f"Needs {min_approvers - count} more approver(s). {notes}",
                outcome="partial_approval",
            )
            inst.add_step(step)
            self.repo.update(inst)

        return self.repo.get(workflow_id)

    def quorum_approve(
        self,
        workflow_id:   str,
        actor:         str,
        min_approvers: int = 2,
        notes:         str = "",
    ) -> Optional[WorkflowInstance]:
        """
        Convenience method for quorum/dual-control approval.
        Requires min_approvers distinct approvers before the decision executes.

        Example — dual approval (two different reviewers both must approve):
            engine.quorum_approve(wf_id, "reviewer_a", min_approvers=2)
            engine.quorum_approve(wf_id, "reviewer_b", min_approvers=2)
            # After reviewer_b: status becomes "approved"
        """
        return self.approve(workflow_id, actor, notes, min_approvers=min_approvers)

    def reject(
        self,
        workflow_id: str,
        actor:       str,
        notes:       str = "",
    ) -> Optional[WorkflowInstance]:
        """Reject a pending decision — it will be blocked."""
        inst = self._transition(workflow_id, "reject", actor, notes, "rejected",
                                step_outcome="rejected")
        if inst and self.event_bus:
            from glassbox.events.event_bus import DecisionBlocked
            self.event_bus.publish(DecisionBlocked(
                decision_id=inst.decision_id, agent_id=inst.agent_id,
                decision_type=inst.decision_type,
                violations=[f"Rejected by {actor}: {notes}"],
                risk_score=inst.risk_score,
            ))
        return inst

    def escalate(
        self,
        workflow_id:  str,
        actor:        str,
        escalate_to:  str,
        notes:        str = "",
    ) -> Optional[WorkflowInstance]:
        """Escalate to a senior reviewer."""
        inst = self._get(workflow_id)
        if not inst:
            return None
        inst.escalate_to = escalate_to
        step = WorkflowStep(
            step_id=str(uuid.uuid4()), workflow_id=workflow_id,
            step_type="escalate", actor=actor, notes=notes, outcome="escalated",
        )
        step.completed_at = datetime.now(timezone.utc).isoformat()
        inst.add_step(step)
        self.repo.update(inst)
        return inst

    def add_comment(
        self,
        workflow_id: str,
        actor:       str,
        notes:       str,
    ) -> Optional[WorkflowInstance]:
        """Add a review comment without changing workflow state."""
        inst = self._get(workflow_id)
        if not inst:
            return None
        step = WorkflowStep(
            step_id=str(uuid.uuid4()), workflow_id=workflow_id,
            step_type="comment", actor=actor, notes=notes, outcome="pending",
        )
        inst.steps.append(step)
        inst.updated_at = datetime.now(timezone.utc).isoformat()
        self.repo.update(inst)
        return inst

    # ── Query ──────────────────────────────────────────────────────────────────

    def get(self, workflow_id: str) -> Optional[WorkflowInstance]:
        return self.repo.get(workflow_id)

    def get_by_decision(self, decision_id: str) -> Optional[WorkflowInstance]:
        return self.repo.get_by_decision(decision_id)

    def list_pending(self) -> List[WorkflowInstance]:
        """All decisions waiting for human review."""
        return self.repo.list_pending()

    def list_sla_breached(self) -> List[WorkflowInstance]:
        """Pending decisions that have exceeded their SLA timer."""
        return self.repo.list_sla_breached()

    def queue_stats(self) -> Dict[str, Any]:
        """Snapshot of the current review queue."""
        pending  = self.list_pending()
        breached = [w for w in pending if w.is_sla_breached()]
        return {
            "total_pending":     len(pending),
            "sla_breached":      len(breached),
            "by_decision_type":  self._count_by(pending, "decision_type"),
            "by_assigned_to":    self._count_by(pending, "assigned_to"),
            "oldest_pending_minutes": self._oldest_minutes(pending),
        }

    # ── SLA monitoring background thread ─────────────────────────────────────

    def _start_sla_monitor(self) -> None:
        if self._monitor_thread and self._monitor_thread.is_alive():
            return
        self._stop_monitor.clear()
        self._monitor_thread = threading.Thread(
            target=self._sla_monitor_loop,
            name="glassbox-sla-monitor",
            daemon=True,
        )
        self._monitor_thread.start()

    def _sla_monitor_loop(self) -> None:
        while not self._stop_monitor.wait(self._monitor_interval_s):
            try:
                breached = self.list_sla_breached()
                for wf in breached:
                    elapsed = self._elapsed_minutes(wf)
                    if self.event_bus:
                        from glassbox.events.event_bus import SLABreached
                        self.event_bus.publish(SLABreached(
                            workflow_id=wf.workflow_id,
                            decision_id=wf.decision_id,
                            agent_id=wf.agent_id,
                            sla_minutes=wf.sla_minutes,
                            elapsed_minutes=elapsed,
                        ))
                    # Auto-escalate if escalate_to is configured
                    if wf.escalate_to and wf.state != "escalated":
                        self.escalate(
                            wf.workflow_id,
                            actor="system.sla-monitor",
                            escalate_to=wf.escalate_to,
                            notes=f"Auto-escalated: SLA of {wf.sla_minutes}min breached after {elapsed:.1f}min",
                        )
            except Exception:
                log.exception("SLA monitor iteration failed")

    def stop_monitor(self) -> None:
        self._stop_monitor.set()
        if self._monitor_thread:
            self._monitor_thread.join(timeout=5)
            self._monitor_thread = None

    def shutdown(self) -> None:
        """Stop background work and release repository resources."""
        self.stop_monitor()
        if hasattr(self.repo, "close"):
            self.repo.close()

    def close(self) -> None:
        """Alias for shutdown to support generic lifecycle cleanup."""
        self.shutdown()

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _get(self, workflow_id: str) -> Optional[WorkflowInstance]:
        return self.repo.get(workflow_id)

    def _transition(
        self,
        workflow_id:   str,
        step_type:     str,
        actor:         str,
        notes:         str,
        new_state:     str,
        step_outcome:  str = "pending",
    ) -> Optional[WorkflowInstance]:
        inst = self._get(workflow_id)
        if not inst:
            return None
        step = WorkflowStep(
            step_id=str(uuid.uuid4()), workflow_id=workflow_id,
            step_type=step_type, actor=actor, notes=notes, outcome=step_outcome,
        )
        step.completed_at = datetime.now(timezone.utc).isoformat()
        if new_state not in ("in_review",):
            inst.state = new_state  # add_step will set if approved/rejected/escalated
        inst.add_step(step)
        if new_state == "in_review":
            inst.state = "in_review"
        self.repo.update(inst)
        return inst

    def _count_by(self, workflows: List[WorkflowInstance], attr: str) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for w in workflows:
            key = getattr(w, attr, "unknown") or "unassigned"
            counts[key] = counts.get(key, 0) + 1
        return counts

    def _elapsed_minutes(self, wf: WorkflowInstance) -> float:
        created = datetime.fromisoformat(wf.created_at)
        return (datetime.now(timezone.utc) - created).total_seconds() / 60

    def _oldest_minutes(self, workflows: List[WorkflowInstance]) -> Optional[float]:
        if not workflows:
            return None
        return max(self._elapsed_minutes(w) for w in workflows)
