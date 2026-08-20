"""
GlassBox Framework — Workflow Repository  (v1.0.0)
====================================================
Persistence for approval-workflow instances, steps, and SLA tracking.

This module used to also hold ``PolicyRepository`` and ``AuditRepository``
implementations for the legacy ``glassbox.governance`` pipeline. That pipeline
and its policy/audit repositories were physically removed once the v2
hexagonal architecture (``glassbox.domain``/``ports``/``app``/
``adapters.outbound``) fully replaced them. ``WorkflowRepository`` and
``SQLiteWorkflowRepository`` were kept: they remain the real, sanctioned
implementation reached by v2 through
:class:`~glassbox.ports.workflow.WorkflowGateway` (see that port's docstring),
satisfied structurally by
:class:`~glassbox.workflow.workflow_engine.WorkflowEngine` with no adapter
shim required.

Design principles:
  - Repository pattern: domain objects never know how they are persisted
  - Thread-safe: the SQLite implementation uses ``threading.Lock`` internally
  - Zero mandatory deps: SQLite is Python stdlib, no third-party ORM

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════


class WorkflowStep:
    """A single step in a decision approval workflow."""

    def __init__(
        self,
        step_id: str,
        workflow_id: str,
        step_type: str,  # "review", "approve", "reject", "escalate", "notify"
        actor: Optional[str] = None,
        notes: str = "",
        outcome: str = "pending",  # pending|approved|rejected|escalated
    ):
        self.step_id = step_id
        self.workflow_id = workflow_id
        self.step_type = step_type
        self.actor = actor
        self.notes = notes
        self.outcome = outcome
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            k: getattr(self, k)
            for k in [
                "step_id",
                "workflow_id",
                "step_type",
                "actor",
                "notes",
                "outcome",
                "created_at",
                "completed_at",
            ]
        }


class WorkflowInstance:
    """A decision pending human review, with full lifecycle tracking."""

    STATES = {"pending", "in_review", "approved", "rejected", "escalated", "timed_out", "expired", "revoked"}

    def __init__(
        self,
        workflow_id: str,
        decision_id: str,
        agent_id: str,
        decision_type: str,
        risk_score: float,
        violations: List[str],
        sla_minutes: int = 60,
        assigned_to: Optional[str] = None,
        escalate_to: Optional[str] = None,
    ):
        self.workflow_id = workflow_id
        self.decision_id = decision_id
        self.agent_id = agent_id
        self.decision_type = decision_type
        self.risk_score = risk_score
        self.violations = violations
        self.sla_minutes = sla_minutes
        self.assigned_to = assigned_to
        self.escalate_to = escalate_to
        self.state = "pending"
        self.steps: List[WorkflowStep] = []
        self.approval_actors: List[str] = []  # quorum tracking (v1.1)
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.updated_at = self.created_at
        self.resolved_at: Optional[str] = None

    def is_sla_breached(self) -> bool:
        from datetime import timedelta

        created = datetime.fromisoformat(self.created_at)
        return (datetime.now(timezone.utc) - created).total_seconds() > self.sla_minutes * 60

    def add_step(self, step: WorkflowStep) -> None:
        self.steps.append(step)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        # Auto-advance state
        if step.outcome == "approved":
            self.state = "approved"
            self.resolved_at = step.completed_at or self.updated_at
        elif step.outcome == "rejected":
            self.state = "rejected"
            self.resolved_at = step.completed_at or self.updated_at
        elif step.outcome == "escalated":
            self.state = "escalated"
        elif step.outcome == "expired":
            self.state = "expired"
            self.resolved_at = step.completed_at or self.updated_at
        elif step.outcome == "revoked":
            self.state = "revoked"
            self.resolved_at = step.completed_at or self.updated_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id": self.workflow_id,
            "decision_id": self.decision_id,
            "agent_id": self.agent_id,
            "decision_type": self.decision_type,
            "risk_score": self.risk_score,
            "violations": self.violations,
            "sla_minutes": self.sla_minutes,
            "assigned_to": self.assigned_to,
            "escalate_to": self.escalate_to,
            "state": self.state,
            "steps": [s.to_dict() for s in self.steps],
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolved_at": self.resolved_at,
            "sla_breached": self.is_sla_breached(),
        }


class WorkflowRepository(ABC):
    @abstractmethod
    def create(self, instance: WorkflowInstance) -> None: ...
    @abstractmethod
    def get(self, workflow_id: str) -> Optional[WorkflowInstance]: ...
    @abstractmethod
    def get_by_decision(self, decision_id: str) -> Optional[WorkflowInstance]: ...
    @abstractmethod
    def update(self, instance: WorkflowInstance) -> None: ...
    @abstractmethod
    def list_pending(self) -> List[WorkflowInstance]: ...
    @abstractmethod
    def list_sla_breached(self) -> List[WorkflowInstance]: ...


class SQLiteWorkflowRepository(WorkflowRepository):
    """SQLite-backed workflow repository with SLA breach detection."""

    def __init__(self, db_path: str = "glassbox_workflows.db"):
        self.db_path = db_path
        self._lock = threading.Lock()
        self._shared_conn: Optional[sqlite3.Connection] = None
        if db_path == ":memory:":
            self._shared_conn = sqlite3.connect(":memory:", check_same_thread=False)
            self._shared_conn.row_factory = sqlite3.Row
        self._init_schema()

    @contextmanager
    def _conn(self):
        if self._shared_conn is not None:
            try:
                yield self._shared_conn
                self._shared_conn.commit()
            except Exception:
                self._shared_conn.rollback()
                raise
            return
        conn = sqlite3.connect(self.db_path, check_same_thread=False)
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def close(self) -> None:
        if self._shared_conn is not None:
            try:
                self._shared_conn.close()
            finally:
                self._shared_conn = None

    def __del__(self):
        self.close()

    def _init_schema(self) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS workflows (
                        workflow_id   TEXT PRIMARY KEY,
                        decision_id   TEXT NOT NULL UNIQUE,
                        agent_id      TEXT NOT NULL,
                        decision_type TEXT NOT NULL,
                        risk_score    REAL,
                        state         TEXT NOT NULL DEFAULT 'pending',
                        sla_minutes   INTEGER DEFAULT 60,
                        assigned_to   TEXT,
                        escalate_to   TEXT,
                        created_at    TEXT NOT NULL,
                        updated_at    TEXT NOT NULL,
                        resolved_at   TEXT,
                        full_json     TEXT NOT NULL
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_decision ON workflows(decision_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_state    ON workflows(state)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_wf_ts       ON workflows(created_at)")

    def create(self, instance: WorkflowInstance) -> None:
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO workflows
                    (workflow_id, decision_id, agent_id, decision_type, risk_score,
                     state, sla_minutes, assigned_to, escalate_to,
                     created_at, updated_at, resolved_at, full_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        instance.workflow_id,
                        instance.decision_id,
                        instance.agent_id,
                        instance.decision_type,
                        instance.risk_score,
                        instance.state,
                        instance.sla_minutes,
                        instance.assigned_to,
                        instance.escalate_to,
                        instance.created_at,
                        instance.updated_at,
                        instance.resolved_at,
                        json.dumps(instance.to_dict(), default=str),
                    ),
                )

    def get(self, workflow_id: str) -> Optional[WorkflowInstance]:
        return self._load_by("workflow_id", workflow_id)

    def get_by_decision(self, decision_id: str) -> Optional[WorkflowInstance]:
        return self._load_by("decision_id", decision_id)

    def _load_by(self, col: str, val: str) -> Optional[WorkflowInstance]:
        # Defence in depth: `col` is only ever passed as a hardcoded literal by
        # the two callers above, but this allowlist keeps that true even if a
        # future caller passes something else.
        if col not in ("workflow_id", "decision_id"):
            raise ValueError(f"Invalid column for _load_by: {col!r}")
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    # `col` is validated against an allowlist above.
                    f"SELECT full_json FROM workflows WHERE {col}=?",  # nosec B608
                    (val,),
                ).fetchone()
                return self._from_json(row["full_json"]) if row else None

    def update(self, instance: WorkflowInstance) -> None:
        instance.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                conn.execute(
                    """
                    UPDATE workflows SET state=?, assigned_to=?, updated_at=?,
                    resolved_at=?, full_json=? WHERE workflow_id=?
                """,
                    (
                        instance.state,
                        instance.assigned_to,
                        instance.updated_at,
                        instance.resolved_at,
                        json.dumps(instance.to_dict(), default=str),
                        instance.workflow_id,
                    ),
                )

    def list_pending(self) -> List[WorkflowInstance]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT full_json FROM workflows WHERE state IN ('pending','in_review') "
                    "ORDER BY created_at"
                ).fetchall()
                return [self._from_json(r["full_json"]) for r in rows]

    def list_sla_breached(self) -> List[WorkflowInstance]:
        return [w for w in self.list_pending() if w.is_sla_breached()]

    def _from_json(self, raw: str) -> WorkflowInstance:
        d = json.loads(raw)
        inst = WorkflowInstance(
            workflow_id=d["workflow_id"],
            decision_id=d["decision_id"],
            agent_id=d["agent_id"],
            decision_type=d["decision_type"],
            risk_score=d.get("risk_score", 0.0),
            violations=d.get("violations", []),
            sla_minutes=d.get("sla_minutes", 60),
            assigned_to=d.get("assigned_to"),
            escalate_to=d.get("escalate_to"),
        )
        inst.state = d.get("state", "pending")
        inst.created_at = d.get("created_at", inst.created_at)
        inst.updated_at = d.get("updated_at", inst.updated_at)
        inst.resolved_at = d.get("resolved_at")
        inst.steps = [
            WorkflowStep(
                **{
                    k: s[k]
                    for k in ["step_id", "workflow_id", "step_type", "actor", "notes", "outcome"]
                }
            )
            for s in d.get("steps", [])
        ]
        return inst
