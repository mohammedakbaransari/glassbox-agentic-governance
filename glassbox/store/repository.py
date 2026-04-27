"""
GlassBox Framework — Repository Layer  (v1.0.0)
================================================
Abstract storage interfaces (Repository pattern) with two concrete
implementations:

  InMemoryRepository  — zero deps, fast, volatile (default; tests, dev)
  SQLiteRepository    — stdlib sqlite3, persistent, production-ready

The interface is identical. Swap from in-memory to SQLite by changing
one constructor argument. Swap to Postgres/MySQL by implementing the
interface — the pipeline never changes.

Three repositories are provided:

  PolicyRepository    — stores Policy definitions, versions, lifecycle
  AuditRepository     — persistent, queryable, indexed decision records
  WorkflowRepository  — approval workflow instances, steps, SLA tracking

Design principles:
  - Repository pattern: domain objects never know how they are persisted
  - Interface segregation: each repo has exactly the methods it needs
  - Thread-safe: all implementations use threading.Lock internally
  - Zero mandatory deps: SQLite is Python stdlib, no third-party ORM

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional

from glassbox.governance.models import (
    AuditRecord,
    CircuitBreakerResult,
    DecisionContext,
    DecisionType,
    Disposition,
    ExecutionResult,
    FinalStatus,
    PolicyEvaluation,
    PolicyResult,
    RiskFactor,
    RiskLevel,
    RiskResult,
)


# ══════════════════════════════════════════════════════════════════════════════
# POLICY REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class PolicyRecord:
    """
    Persisted policy definition. Separates the storage concern (PolicyRecord)
    from the execution concern (Policy in policy_engine.py).

    Lifecycle: draft → active → deprecated → archived
    """
    VALID_STATUSES = {"draft", "active", "deprecated", "archived"}

    def __init__(
        self,
        policy_id:      str,
        policy_name:    str,
        decision_types: List[str],
        rule_type:      str,              # "python" | "yaml" | "json"
        rule_body:      str,              # serialised rule definition
        version:        str    = "1.0",
        status:         str    = "active",
        description:    str    = "",
        created_by:     str    = "system",
        tags:           List[str] = None,
    ):
        self.policy_id      = policy_id
        self.policy_name    = policy_name
        self.decision_types = decision_types
        self.rule_type      = rule_type
        self.rule_body      = rule_body
        self.version        = version
        self.status         = status
        self.description    = description
        self.created_by     = created_by
        self.tags           = tags or []
        self.created_at     = datetime.now(timezone.utc).isoformat()
        self.updated_at     = self.created_at

    def to_dict(self) -> Dict[str, Any]:
        return {
            "policy_id":      self.policy_id,
            "policy_name":    self.policy_name,
            "decision_types": self.decision_types,
            "rule_type":      self.rule_type,
            "rule_body":      self.rule_body,
            "version":        self.version,
            "status":         self.status,
            "description":    self.description,
            "created_by":     self.created_by,
            "tags":           self.tags,
            "created_at":     self.created_at,
            "updated_at":     self.updated_at,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PolicyRecord":
        rec = cls(
            policy_id=d["policy_id"], policy_name=d["policy_name"],
            decision_types=d.get("decision_types", []),
            rule_type=d.get("rule_type", "python"),
            rule_body=d.get("rule_body", ""),
            version=d.get("version", "1.0"),
            status=d.get("status", "active"),
            description=d.get("description", ""),
            created_by=d.get("created_by", "system"),
            tags=d.get("tags", []),
        )
        rec.created_at = d.get("created_at", rec.created_at)
        rec.updated_at = d.get("updated_at", rec.updated_at)
        return rec


class PolicyRepository(ABC):
    """Abstract interface for policy storage."""

    @abstractmethod
    def save(self, record: PolicyRecord) -> None: ...

    @abstractmethod
    def get(self, policy_id: str) -> Optional[PolicyRecord]: ...

    @abstractmethod
    def list_all(self, status: str = None) -> List[PolicyRecord]: ...

    @abstractmethod
    def delete(self, policy_id: str) -> bool: ...

    @abstractmethod
    def update_status(self, policy_id: str, status: str) -> bool: ...

    @abstractmethod
    def list_versions(self, policy_id: str) -> List[PolicyRecord]: ...


class InMemoryPolicyRepository(PolicyRepository):
    """
    In-memory policy repository — fast, volatile, zero deps.
    Suitable for tests, development, and single-process deployments.
    """

    def __init__(self):
        self._store: Dict[str, List[PolicyRecord]] = defaultdict(list)  # id → version list
        self._lock = threading.Lock()

    def save(self, record: PolicyRecord) -> None:
        with self._lock:
            record.updated_at = datetime.now(timezone.utc).isoformat()
            versions = self._store[record.policy_id]
            # Check if same version already exists — update it
            for i, existing in enumerate(versions):
                if existing.version == record.version:
                    versions[i] = record
                    return
            versions.append(record)

    def get(self, policy_id: str) -> Optional[PolicyRecord]:
        with self._lock:
            versions = self._store.get(policy_id, [])
            # Return the latest active version, or the latest overall
            active = [v for v in versions if v.status == "active"]
            if active:
                return active[-1]
            return versions[-1] if versions else None

    def list_all(self, status: str = None) -> List[PolicyRecord]:
        with self._lock:
            result = []
            for versions in self._store.values():
                if not versions:
                    continue
                latest = versions[-1]
                if status is None or latest.status == status:
                    result.append(latest)
            return sorted(result, key=lambda r: r.created_at)

    def delete(self, policy_id: str) -> bool:
        with self._lock:
            if policy_id in self._store:
                del self._store[policy_id]
                return True
            return False

    def update_status(self, policy_id: str, status: str) -> bool:
        with self._lock:
            if policy_id not in self._store or not self._store[policy_id]:
                return False
            self._store[policy_id][-1].status = status
            self._store[policy_id][-1].updated_at = datetime.now(timezone.utc).isoformat()
            return True

    def list_versions(self, policy_id: str) -> List[PolicyRecord]:
        with self._lock:
            return list(self._store.get(policy_id, []))


class SQLitePolicyRepository(PolicyRepository):
    """
    SQLite-backed policy repository — persistent, zero extra deps.
    Production-ready for single-node and embedded deployments.
    Thread-safe via check_same_thread=False + threading.Lock.
    For :memory: databases, a single shared connection is used.
    """

    def __init__(self, db_path: str = "glassbox_policies.db"):
        self.db_path = db_path
        self._lock   = threading.Lock()
        self._shared_conn: sqlite3.Connection = None
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
            conn.execute("PRAGMA foreign_keys=ON")
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
                    CREATE TABLE IF NOT EXISTS policies (
                        policy_id      TEXT NOT NULL,
                        version        TEXT NOT NULL,
                        policy_name    TEXT NOT NULL,
                        decision_types TEXT NOT NULL,  -- JSON array
                        rule_type      TEXT NOT NULL,
                        rule_body      TEXT NOT NULL,
                        status         TEXT NOT NULL DEFAULT 'active',
                        description    TEXT,
                        created_by     TEXT,
                        tags           TEXT,           -- JSON array
                        created_at     TEXT NOT NULL,
                        updated_at     TEXT NOT NULL,
                        PRIMARY KEY (policy_id, version)
                    )
                """)
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policies_status ON policies(status)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policies_id ON policies(policy_id)")

    def _row_to_record(self, row: sqlite3.Row) -> PolicyRecord:
        rec = PolicyRecord(
            policy_id=row["policy_id"], policy_name=row["policy_name"],
            decision_types=json.loads(row["decision_types"]),
            rule_type=row["rule_type"], rule_body=row["rule_body"],
            version=row["version"], status=row["status"],
            description=row["description"] or "",
            created_by=row["created_by"] or "system",
            tags=json.loads(row["tags"] or "[]"),
        )
        rec.created_at = row["created_at"]
        rec.updated_at = row["updated_at"]
        return rec

    def save(self, record: PolicyRecord) -> None:
        record.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO policies
                    (policy_id, version, policy_name, decision_types, rule_type,
                     rule_body, status, description, created_by, tags, created_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    record.policy_id, record.version, record.policy_name,
                    json.dumps(record.decision_types), record.rule_type, record.rule_body,
                    record.status, record.description, record.created_by,
                    json.dumps(record.tags), record.created_at, record.updated_at,
                ))

    def get(self, policy_id: str) -> Optional[PolicyRecord]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    "SELECT * FROM policies WHERE policy_id=? AND status='active' "
                    "ORDER BY updated_at DESC LIMIT 1", (policy_id,)
                ).fetchone()
                if not row:
                    row = conn.execute(
                        "SELECT * FROM policies WHERE policy_id=? ORDER BY updated_at DESC LIMIT 1",
                        (policy_id,)
                    ).fetchone()
                return self._row_to_record(row) if row else None

    def list_all(self, status: str = None) -> List[PolicyRecord]:
        with self._lock:
            with self._conn() as conn:
                if status:
                    rows = conn.execute(
                        "SELECT * FROM policies WHERE status=? ORDER BY created_at", (status,)
                    ).fetchall()
                else:
                    rows = conn.execute(
                        "SELECT p1.* FROM policies p1 "
                        "INNER JOIN (SELECT policy_id, MAX(updated_at) as max_u FROM policies GROUP BY policy_id) p2 "
                        "ON p1.policy_id=p2.policy_id AND p1.updated_at=p2.max_u "
                        "ORDER BY p1.created_at"
                    ).fetchall()
                return [self._row_to_record(r) for r in rows]

    def delete(self, policy_id: str) -> bool:
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute("DELETE FROM policies WHERE policy_id=?", (policy_id,))
                return cur.rowcount > 0

    def update_status(self, policy_id: str, status: str) -> bool:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE policies SET status=?, updated_at=? WHERE policy_id=?",
                    (status, now, policy_id)
                )
                return cur.rowcount > 0

    def list_versions(self, policy_id: str) -> List[PolicyRecord]:
        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT * FROM policies WHERE policy_id=? ORDER BY created_at", (policy_id,)
                ).fetchall()
                return [self._row_to_record(r) for r in rows]


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class AuditRepository(ABC):
    """Abstract interface for persistent, queryable audit storage."""

    @abstractmethod
    def save(self, record: AuditRecord, tenant_id: Optional[str] = None) -> None: ...

    @abstractmethod
    def get_by_id(
        self,
        decision_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[AuditRecord]: ...

    @abstractmethod
    def query(
        self,
        agent_id:       Optional[str]          = None,
        decision_type:  Optional[str]          = None,
        final_status:   Optional[str]          = None,
        from_ts:        Optional[str]          = None,
        to_ts:          Optional[str]          = None,
        min_risk_score: Optional[float]        = None,
        has_violations: Optional[bool]         = None,
        tenant_id:      Optional[str]          = None,
        limit:          int                    = 100,
        offset:         int                    = 0,
    ) -> List[AuditRecord]: ...

    @abstractmethod
    def aggregate_spend(
        self,
        decision_type: str,
        final_status:  str   = "executed",
        from_ts:       Optional[str] = None,
    ) -> float: ...

    @abstractmethod
    def count(self, tenant_id: Optional[str] = None, **filters) -> int: ...


class SQLiteAuditRepository(AuditRepository):
    """
    SQLite-backed audit repository.
    Full query API: filter by agent, type, status, time range, risk score.
    Indexed for fast lookups. Replaces O(n) in-memory scan.
    For :memory: databases, a single shared connection is used.
    """

    def __init__(self, db_path: str = "glassbox_audit.db"):
        self.db_path = db_path
        self._lock   = threading.Lock()
        self._shared_conn: sqlite3.Connection = None
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
            conn.execute("PRAGMA synchronous=NORMAL")
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
                    CREATE TABLE IF NOT EXISTS audit_records (
                        decision_id         TEXT PRIMARY KEY,
                        agent_id            TEXT NOT NULL,
                        decision_type       TEXT NOT NULL,
                        final_status        TEXT,
                        risk_score          REAL,
                        risk_level          TEXT,
                        violations_count    INTEGER DEFAULT 0,
                        warnings_count      INTEGER DEFAULT 0,
                        pipeline_latency_ms REAL,
                        payload_amount      REAL,
                        timestamp           TEXT NOT NULL,
                        replay_of           TEXT,
                        contract_validated  INTEGER DEFAULT 0,
                        circuit_breaker     INTEGER DEFAULT 0,
                        tenant_id           TEXT,
                        full_record_json    TEXT NOT NULL
                    )
                """)
                cols = {
                    row[1] if not isinstance(row, sqlite3.Row) else row["name"]
                    for row in conn.execute("PRAGMA table_info(audit_records)").fetchall()
                }
                if "tenant_id" not in cols:
                    conn.execute("ALTER TABLE audit_records ADD COLUMN tenant_id TEXT")
                for idx in [
                    "CREATE INDEX IF NOT EXISTS idx_audit_agent   ON audit_records(agent_id)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_type    ON audit_records(decision_type)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_status  ON audit_records(final_status)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_ts      ON audit_records(timestamp)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_risk    ON audit_records(risk_score)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_replay  ON audit_records(replay_of)",
                    "CREATE INDEX IF NOT EXISTS idx_audit_tenant  ON audit_records(tenant_id)",
                ]:
                    conn.execute(idx)

    def save(self, record: AuditRecord, tenant_id: Optional[str] = None) -> None:
        record_dict   = record.to_dict()
        violations    = len(record.policy_result.violations) if record.policy_result else 0
        warnings      = len(record.policy_result.warnings)   if record.policy_result else 0
        risk_score    = record.risk_result.risk_score         if record.risk_result   else None
        risk_level    = record.risk_result.risk_level.value   if record.risk_result   else None
        payload_amount = float(record.payload.get("amount") or 0) if record.payload else 0.0
        effective_tenant = tenant_id
        if effective_tenant is None and record.context and isinstance(record.context.metadata, dict):
            effective_tenant = record.context.metadata.get("tenant_id")

        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO audit_records
                    (decision_id, agent_id, decision_type, final_status,
                     risk_score, risk_level, violations_count, warnings_count,
                     pipeline_latency_ms, payload_amount, timestamp, replay_of,
                     contract_validated, circuit_breaker, tenant_id, full_record_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    record.decision_id, record.agent_id, record.decision_type.value,
                    record.final_status.value if record.final_status else None,
                    risk_score, risk_level, violations, warnings,
                    record.pipeline_latency_ms, payload_amount,
                    record.timestamp, record.replay_of,
                    int(record.contract_validated),
                    int(record.circuit_breaker_result.triggered if record.circuit_breaker_result else False),
                    effective_tenant,
                    json.dumps(record_dict, default=str),
                ))

    @staticmethod
    def _deserialize_audit_record(raw: Dict[str, Any]) -> AuditRecord:
        """
        Convert persisted JSON payload back into a typed AuditRecord.

        The converter is intentionally tolerant: unknown enum values or malformed
        nested structures are preserved in a best-effort fallback shape rather
        than raising and breaking read paths.
        """
        data = dict(raw or {})

        decision_type = data.get("decision_type", DecisionType.CUSTOM.value)
        try:
            decision_type = DecisionType(decision_type)
        except Exception:
            decision_type = DecisionType.CUSTOM

        context_data = data.get("context")
        if isinstance(context_data, DecisionContext):
            context = context_data
        elif isinstance(context_data, dict):
            context = DecisionContext(**context_data)
        else:
            context = DecisionContext()

        record = AuditRecord(
            agent_id=data.get("agent_id", ""),
            decision_type=decision_type,
            payload=data.get("payload") or {},
            context=context,
            decision_id=data.get("decision_id") or str(uuid.uuid4()),
            timestamp=data.get("timestamp") or datetime.now(timezone.utc).isoformat(),
        )

        status_val = data.get("final_status")
        if status_val:
            try:
                record.final_status = FinalStatus(status_val)
            except Exception:
                record.final_status = None

        policy_data = data.get("policy_result")
        if isinstance(policy_data, dict):
            try:
                evaluations = []
                for ev in policy_data.get("evaluated_policies", []) or []:
                    if isinstance(ev, dict):
                        evaluations.append(PolicyEvaluation(**ev))
                record.policy_result = PolicyResult(
                    passed=bool(policy_data.get("passed", True)),
                    evaluated_policies=evaluations,
                    violations=list(policy_data.get("violations", []) or []),
                    warnings=list(policy_data.get("warnings", []) or []),
                )
            except Exception:
                record.policy_result = None

        risk_data = data.get("risk_result")
        if isinstance(risk_data, dict):
            try:
                factors = []
                for factor in risk_data.get("factors", []) or []:
                    if isinstance(factor, dict):
                        factors.append(RiskFactor(**factor))
                record.risk_result = RiskResult(
                    risk_score=float(risk_data.get("risk_score", 0.0)),
                    risk_level=RiskLevel(risk_data.get("risk_level", RiskLevel.LOW.value)),
                    disposition=Disposition(
                        risk_data.get("disposition", Disposition.AUTO_EXECUTE.value)
                    ),
                    factors=factors,
                )
            except Exception:
                record.risk_result = None

        cb_data = data.get("circuit_breaker_result")
        if isinstance(cb_data, dict):
            try:
                record.circuit_breaker_result = CircuitBreakerResult(**cb_data)
            except Exception:
                record.circuit_breaker_result = None

        exec_data = data.get("execution_result")
        if isinstance(exec_data, dict):
            try:
                record.execution_result = ExecutionResult(**exec_data)
            except Exception:
                record.execution_result = None

        record.reviewer = data.get("reviewer")
        record.review_outcome = data.get("review_outcome")
        record.review_timestamp = data.get("review_timestamp")
        record.replay_of = data.get("replay_of")
        record.pipeline_latency_ms = data.get("pipeline_latency_ms")
        record.contract_validated = bool(data.get("contract_validated", False))
        return record

    def get_by_id(
        self,
        decision_id: str,
        tenant_id: Optional[str] = None,
    ) -> Optional[AuditRecord]:
        with self._lock:
            with self._conn() as conn:
                if tenant_id is not None:
                    row = conn.execute(
                        "SELECT full_record_json FROM audit_records WHERE decision_id=? AND tenant_id=?",
                        (decision_id, tenant_id),
                    ).fetchone()
                else:
                    row = conn.execute(
                        "SELECT full_record_json FROM audit_records WHERE decision_id=?",
                        (decision_id,)
                    ).fetchone()
                if not row:
                    return None
                return self._deserialize_audit_record(json.loads(row["full_record_json"]))

    def query(
        self,
        agent_id:       Optional[str]   = None,
        decision_type:  Optional[str]   = None,
        final_status:   Optional[str]   = None,
        from_ts:        Optional[str]   = None,
        to_ts:          Optional[str]   = None,
        min_risk_score: Optional[float] = None,
        has_violations: Optional[bool]  = None,
        tenant_id:      Optional[str]   = None,
        limit:          int             = 100,
        offset:         int             = 0,
    ) -> List[AuditRecord]:
        where, params = [], []
        if tenant_id is not None:
            where.append("tenant_id = ?"); params.append(tenant_id)
        if agent_id:
            where.append("agent_id = ?"); params.append(agent_id)
        if decision_type:
            where.append("decision_type = ?"); params.append(decision_type)
        if final_status:
            where.append("final_status = ?"); params.append(final_status)
        if from_ts:
            where.append("timestamp >= ?"); params.append(from_ts)
        if to_ts:
            where.append("timestamp <= ?"); params.append(to_ts)
        if min_risk_score is not None:
            where.append("risk_score >= ?"); params.append(min_risk_score)
        if has_violations is True:
            where.append("violations_count > 0")
        elif has_violations is False:
            where.append("violations_count = 0")

        sql = "SELECT full_record_json FROM audit_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params += [limit, offset]

        with self._lock:
            with self._conn() as conn:
                rows = conn.execute(sql, params).fetchall()
                return [self._deserialize_audit_record(json.loads(r["full_record_json"])) for r in rows]

    def aggregate_spend(
        self,
        decision_type: str,
        final_status:  str          = "executed",
        from_ts:       Optional[str] = None,
    ) -> float:
        where  = "decision_type=? AND final_status=?"
        params: list = [decision_type, final_status]
        if from_ts:
            where += " AND timestamp >= ?"
            params.append(from_ts)
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    f"SELECT COALESCE(SUM(payload_amount),0) FROM audit_records WHERE {where}",
                    params,
                ).fetchone()
                return float(row[0])

    def count(self, tenant_id: Optional[str] = None, **filters) -> int:
        where, params = [], []
        if tenant_id is not None:
            where.append("tenant_id = ?"); params.append(tenant_id)
        for k, v in filters.items():
            if v is not None:
                where.append(f"{k} = ?"); params.append(v)
        sql = "SELECT COUNT(*) FROM audit_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        with self._lock:
            with self._conn() as conn:
                return conn.execute(sql, params).fetchone()[0]


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class WorkflowStep:
    """A single step in a decision approval workflow."""
    def __init__(
        self,
        step_id:     str,
        workflow_id: str,
        step_type:   str,          # "review", "approve", "reject", "escalate", "notify"
        actor:       Optional[str] = None,
        notes:       str           = "",
        outcome:     str           = "pending",   # pending|approved|rejected|escalated
    ):
        self.step_id     = step_id
        self.workflow_id = workflow_id
        self.step_type   = step_type
        self.actor       = actor
        self.notes       = notes
        self.outcome     = outcome
        self.created_at  = datetime.now(timezone.utc).isoformat()
        self.completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {k: getattr(self, k) for k in
                ["step_id","workflow_id","step_type","actor","notes",
                 "outcome","created_at","completed_at"]}


class WorkflowInstance:
    """A decision pending human review, with full lifecycle tracking."""
    STATES = {"pending", "in_review", "approved", "rejected", "escalated", "timed_out"}

    def __init__(
        self,
        workflow_id:     str,
        decision_id:     str,
        agent_id:        str,
        decision_type:   str,
        risk_score:      float,
        violations:      List[str],
        sla_minutes:     int           = 60,
        assigned_to:     Optional[str] = None,
        escalate_to:     Optional[str] = None,
    ):
        self.workflow_id   = workflow_id
        self.decision_id   = decision_id
        self.agent_id      = agent_id
        self.decision_type = decision_type
        self.risk_score    = risk_score
        self.violations    = violations
        self.sla_minutes   = sla_minutes
        self.assigned_to   = assigned_to
        self.escalate_to   = escalate_to
        self.state         = "pending"
        self.steps:    List[WorkflowStep] = []
        self.approval_actors: List[str]   = []   # quorum tracking (v1.1)
        self.created_at    = datetime.now(timezone.utc).isoformat()
        self.updated_at    = self.created_at
        self.resolved_at:  Optional[str] = None

    def is_sla_breached(self) -> bool:
        from datetime import timedelta
        created = datetime.fromisoformat(self.created_at)
        return (datetime.now(timezone.utc) - created).total_seconds() > self.sla_minutes * 60

    def add_step(self, step: WorkflowStep) -> None:
        self.steps.append(step)
        self.updated_at = datetime.now(timezone.utc).isoformat()
        # Auto-advance state
        if step.outcome == "approved":
            self.state = "approved"; self.resolved_at = step.completed_at or self.updated_at
        elif step.outcome == "rejected":
            self.state = "rejected"; self.resolved_at = step.completed_at or self.updated_at
        elif step.outcome == "escalated":
            self.state = "escalated"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "workflow_id":   self.workflow_id,
            "decision_id":   self.decision_id,
            "agent_id":      self.agent_id,
            "decision_type": self.decision_type,
            "risk_score":    self.risk_score,
            "violations":    self.violations,
            "sla_minutes":   self.sla_minutes,
            "assigned_to":   self.assigned_to,
            "escalate_to":   self.escalate_to,
            "state":         self.state,
            "steps":         [s.to_dict() for s in self.steps],
            "created_at":    self.created_at,
            "updated_at":    self.updated_at,
            "resolved_at":   self.resolved_at,
            "sla_breached":  self.is_sla_breached(),
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
        self._lock   = threading.Lock()
        self._shared_conn: sqlite3.Connection = None
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
                conn.execute("""
                    INSERT INTO workflows
                    (workflow_id, decision_id, agent_id, decision_type, risk_score,
                     state, sla_minutes, assigned_to, escalate_to,
                     created_at, updated_at, resolved_at, full_json)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    instance.workflow_id, instance.decision_id, instance.agent_id,
                    instance.decision_type, instance.risk_score, instance.state,
                    instance.sla_minutes, instance.assigned_to, instance.escalate_to,
                    instance.created_at, instance.updated_at, instance.resolved_at,
                    json.dumps(instance.to_dict(), default=str),
                ))

    def get(self, workflow_id: str) -> Optional[WorkflowInstance]:
        return self._load_by("workflow_id", workflow_id)

    def get_by_decision(self, decision_id: str) -> Optional[WorkflowInstance]:
        return self._load_by("decision_id", decision_id)

    def _load_by(self, col: str, val: str) -> Optional[WorkflowInstance]:
        with self._lock:
            with self._conn() as conn:
                row = conn.execute(
                    f"SELECT full_json FROM workflows WHERE {col}=?", (val,)
                ).fetchone()
                return self._from_json(row["full_json"]) if row else None

    def update(self, instance: WorkflowInstance) -> None:
        instance.updated_at = datetime.now(timezone.utc).isoformat()
        with self._lock:
            with self._conn() as conn:
                conn.execute("""
                    UPDATE workflows SET state=?, assigned_to=?, updated_at=?,
                    resolved_at=?, full_json=? WHERE workflow_id=?
                """, (
                    instance.state, instance.assigned_to, instance.updated_at,
                    instance.resolved_at,
                    json.dumps(instance.to_dict(), default=str),
                    instance.workflow_id,
                ))

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
            workflow_id=d["workflow_id"], decision_id=d["decision_id"],
            agent_id=d["agent_id"], decision_type=d["decision_type"],
            risk_score=d.get("risk_score", 0.0),
            violations=d.get("violations", []),
            sla_minutes=d.get("sla_minutes", 60),
            assigned_to=d.get("assigned_to"),
            escalate_to=d.get("escalate_to"),
        )
        inst.state       = d.get("state", "pending")
        inst.created_at  = d.get("created_at", inst.created_at)
        inst.updated_at  = d.get("updated_at", inst.updated_at)
        inst.resolved_at = d.get("resolved_at")
        inst.steps       = [
            WorkflowStep(**{k: s[k] for k in
                            ["step_id","workflow_id","step_type","actor","notes","outcome"]})
            for s in d.get("steps", [])
        ]
        return inst


# ══════════════════════════════════════════════════════════════════════════════
# FACTORY — easy repository construction
# ══════════════════════════════════════════════════════════════════════════════

class RepositoryFactory:
    """
    Creates repository instances based on backend type.

    Usage:
        # In-memory (tests, dev)
        repos = RepositoryFactory.in_memory()

        # SQLite (production single-node)
        repos = RepositoryFactory.sqlite(db_dir="/var/lib/glassbox")

        # SQLite (isolated namespace; useful for test runs)
        repos = RepositoryFactory.sqlite(db_dir="/tmp", namespace="test_run_42")

    Returns a dict with keys: "policy", "audit", "workflow"
    """

    @staticmethod
    def in_memory() -> Dict[str, Any]:
        return {
            "policy":   InMemoryPolicyRepository(),
            "audit":    None,   # in-memory audit uses AuditLogger's deque
            "workflow": SQLiteWorkflowRepository(":memory:"),
        }

    @staticmethod
    def sqlite(
        db_dir: str = ".",
        namespace: Optional[str] = None,
        policy_db_path: Optional[str] = None,
        audit_db_path: Optional[str] = None,
        workflow_db_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        import os
        os.makedirs(db_dir, exist_ok=True)
        if namespace:
            safe_ns = "".join(ch for ch in str(namespace) if ch.isalnum() or ch in ("-", "_"))
            safe_ns = safe_ns or "default"
            policy_name = f"glassbox_policies_{safe_ns}.db"
            audit_name = f"glassbox_audit_{safe_ns}.db"
            workflow_name = f"glassbox_workflows_{safe_ns}.db"
        else:
            policy_name = "glassbox_policies.db"
            audit_name = "glassbox_audit.db"
            workflow_name = "glassbox_workflows.db"

        policy_path = policy_db_path or os.path.join(db_dir, policy_name)
        audit_path = audit_db_path or os.path.join(db_dir, audit_name)
        workflow_path = workflow_db_path or os.path.join(db_dir, workflow_name)
        return {
            "policy": SQLitePolicyRepository(policy_path),
            "audit": SQLiteAuditRepository(audit_path),
            "workflow": SQLiteWorkflowRepository(workflow_path),
        }


# Backward compatibility alias retained for older imports and tests.
class SQLiteRepository(SQLiteAuditRepository):
    pass
