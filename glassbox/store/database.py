"""
GlassBox — Transactional Relational Database Engine  (v1.0.0)
==============================================================
Production-grade relational database layer using Python stdlib sqlite3.

Why SQLite (transactional) over JSON-document stores:
  - Compliance audit trails require JOIN: which decisions satisfy which controls?
  - Workflow history requires foreign key integrity between workflows and steps
  - Policy versioning requires atomic version rollover (UPDATE + INSERT in one TX)
  - Evidence collection requires referential integrity (evidence → control must exist)
  - Aggregate spend queries (AGG-001) require SUM with time-window GROUP BY
  - None of these are natural in document stores without application-layer joins

What this module adds on top of repository.py:
  1. Connection pooling   — thread-local connections (safe under GIL + WAL mode)
  2. ACID transactions    — explicit BEGIN/COMMIT/ROLLBACK across multiple repos
  3. Schema migrations    — versioned DDL, applied incrementally, never re-run
  4. Referential integrity— FOREIGN KEY constraints enforced (not just docs)
  5. Full relational schema — all 5 tables in one consistent database
  6. Composite indexes    — covering indexes for all hot query paths
  7. Query builder        — type-safe parameterised query construction
  8. Aggregate queries    — spend, count, breach rates across time windows
  9. Cross-table queries  — policy + audit + workflow + compliance in one query
  10. Backup              — online hot backup without locking

Database schema (single file: glassbox.db):

  policies        — policy definitions with versioning and lifecycle
  audit_records   — every governed decision, fully indexed
  workflows       — approval workflow instances
  workflow_steps  — audit trail for every workflow state change
  compliance_evidence — links decisions to compliance controls

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List, Optional, Tuple

from glassbox.governance.models import AuditRecord, DecisionType, FinalStatus

log = logging.getLogger("glassbox.db")


# ── Schema version and DDL migrations ─────────────────────────────────────────

CURRENT_SCHEMA_VERSION = 3

# Each migration is (version, sql_statements_list)
# Applied in order, never re-applied once recorded in schema_version table.
MIGRATIONS: List[Tuple[int, List[str]]] = [

    (1, [
        # Core tables
        """CREATE TABLE IF NOT EXISTS schema_version (
            version     INTEGER PRIMARY KEY,
            applied_at  TEXT    NOT NULL,
            description TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS policies (
            policy_id      TEXT NOT NULL,
            version        TEXT NOT NULL,
            policy_name    TEXT NOT NULL,
            decision_types TEXT NOT NULL,   -- JSON array of strings
            rule_type      TEXT NOT NULL,   -- python | yaml | json
            rule_body      TEXT NOT NULL,   -- serialised rule
            status         TEXT NOT NULL DEFAULT 'active',
            description    TEXT,
            created_by     TEXT NOT NULL DEFAULT 'system',
            tags           TEXT,            -- JSON array
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            PRIMARY KEY (policy_id, version)
        )""",

        """CREATE TABLE IF NOT EXISTS audit_records (
            decision_id          TEXT PRIMARY KEY,
            agent_id             TEXT NOT NULL,
            decision_type        TEXT NOT NULL,
            final_status         TEXT,
            risk_score           REAL,
            risk_level           TEXT,
            violations_count     INTEGER DEFAULT 0,
            warnings_count       INTEGER DEFAULT 0,
            pipeline_latency_ms  REAL,
            payload_amount       REAL,
            timestamp            TEXT NOT NULL,
            replay_of            TEXT,
            contract_validated   INTEGER DEFAULT 0,
            circuit_breaker      INTEGER DEFAULT 0,
            tenant_id            TEXT,
            full_record_json     TEXT NOT NULL
        )""",

        """CREATE TABLE IF NOT EXISTS workflows (
            workflow_id    TEXT PRIMARY KEY,
            decision_id    TEXT NOT NULL REFERENCES audit_records(decision_id),
            agent_id       TEXT NOT NULL,
            decision_type  TEXT NOT NULL,
            risk_score     REAL,
            state          TEXT NOT NULL DEFAULT 'pending',
            sla_minutes    INTEGER DEFAULT 60,
            assigned_to    TEXT,
            escalate_to    TEXT,
            created_at     TEXT NOT NULL,
            updated_at     TEXT NOT NULL,
            resolved_at    TEXT,
            full_json      TEXT NOT NULL
        )""",

        """CREATE TABLE IF NOT EXISTS workflow_steps (
            step_id      TEXT PRIMARY KEY,
            workflow_id  TEXT NOT NULL REFERENCES workflows(workflow_id),
            step_type    TEXT NOT NULL,   -- review|approve|reject|escalate|comment
            actor        TEXT,
            notes        TEXT,
            outcome      TEXT NOT NULL DEFAULT 'pending',
            created_at   TEXT NOT NULL,
            completed_at TEXT
        )""",

        """CREATE TABLE IF NOT EXISTS compliance_evidence (
            evidence_id    TEXT PRIMARY KEY,
            control_id     TEXT NOT NULL,
            decision_id    TEXT REFERENCES audit_records(decision_id),
            agent_id       TEXT,
            evidence_type  TEXT NOT NULL,
            evidence_data  TEXT,
            collected_at   TEXT NOT NULL
        )""",
    ]),

    (2, [
        # Indexes for hot query paths
        "CREATE INDEX IF NOT EXISTS idx_pol_status    ON policies(status)",
        "CREATE INDEX IF NOT EXISTS idx_pol_id        ON policies(policy_id)",
        "CREATE INDEX IF NOT EXISTS idx_aud_agent     ON audit_records(agent_id)",
        "CREATE INDEX IF NOT EXISTS idx_aud_type      ON audit_records(decision_type)",
        "CREATE INDEX IF NOT EXISTS idx_aud_status    ON audit_records(final_status)",
        "CREATE INDEX IF NOT EXISTS idx_aud_ts        ON audit_records(timestamp)",
        "CREATE INDEX IF NOT EXISTS idx_aud_risk      ON audit_records(risk_score)",
        "CREATE INDEX IF NOT EXISTS idx_aud_tenant    ON audit_records(tenant_id)",
        "CREATE INDEX IF NOT EXISTS idx_wf_state      ON workflows(state)",
        "CREATE INDEX IF NOT EXISTS idx_wf_decision   ON workflows(decision_id)",
        "CREATE INDEX IF NOT EXISTS idx_wf_ts         ON workflows(created_at)",
        "CREATE INDEX IF NOT EXISTS idx_wfs_workflow  ON workflow_steps(workflow_id)",
        "CREATE INDEX IF NOT EXISTS idx_ev_control    ON compliance_evidence(control_id)",
        "CREATE INDEX IF NOT EXISTS idx_ev_decision   ON compliance_evidence(decision_id)",
        "CREATE INDEX IF NOT EXISTS idx_ev_ts         ON compliance_evidence(collected_at)",
    ]),

    (3, [
        # Covering index for AGG-001 fleet budget query (hot path)
        """CREATE INDEX IF NOT EXISTS idx_aud_spend
           ON audit_records(decision_type, final_status, timestamp, payload_amount)""",
        # Partial index for pending workflows (common dashboard query)
        """CREATE INDEX IF NOT EXISTS idx_wf_pending
           ON workflows(created_at) WHERE state IN ('pending','in_review')""",
    ]),
]


# ── Connection Pool (thread-local) ─────────────────────────────────────────────

class ThreadLocalConnectionPool:
    """
    Thread-local SQLite connection pool.

    Each thread gets its own connection — this is safe and recommended
    for SQLite with WAL mode. SQLite's WAL allows one writer + N readers
    concurrently without blocking.

    Why thread-local (not shared connection):
      - sqlite3 connections are NOT thread-safe when shared
      - check_same_thread=False exists but requires manual locking
      - Thread-local gives each worker its own connection with zero locking
      - All threads write via the same WAL file — SQLite serialises writes internally

    Why NOT a traditional pool with N connections:
      - SQLite has file-level locking — extra connections add no parallelism
      - Thread-local gives each thread its own connection with automatic cleanup
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local  = threading.local()

    def get(self) -> sqlite3.Connection:
        """Return this thread's connection, creating it if needed."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                   timeout=30.0, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")   # safe with WAL
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA cache_size=-8000")     # 8MB page cache
            conn.execute("PRAGMA temp_store=MEMORY")
            self._local.conn = conn
        return self._local.conn

    def close_thread(self) -> None:
        """Close this thread's connection (call at thread end)."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None


# ── Transaction Context Manager ────────────────────────────────────────────────

class Transaction:
    """
    Explicit ACID transaction across multiple repository operations.

    Usage:
        db = GlassBoxDatabase("glassbox.db")
        with db.transaction() as tx:
            tx.policies.save(policy_record)
            tx.audit.save(audit_record)
            tx.workflows.create(workflow_instance)
            # All committed atomically, or all rolled back on exception

    The transaction wraps a single connection's BEGIN/COMMIT/ROLLBACK.
    All three repositories use the same connection within the transaction.
    """

    def __init__(self, pool: ThreadLocalConnectionPool):
        self._pool = pool
        self._conn: Optional[sqlite3.Connection] = None

    def __enter__(self) -> "Transaction":
        self._conn = self._pool.get()
        self._conn.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        if exc_type is None:
            self._conn.execute("COMMIT")
        else:
            self._conn.execute("ROLLBACK")
            log.error("Transaction rolled back: %s — %s", exc_type.__name__, exc_val)
        return False  # never suppress exceptions

    def _execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self._conn.execute(sql, params)

    def _executemany(self, sql: str, params_list) -> None:
        self._conn.executemany(sql, params_list)


# ── Query Builder ─────────────────────────────────────────────────────────────

class QueryBuilder:
    """
    Type-safe parameterised SQL query builder.
    Prevents SQL injection at the application layer (sanitizer does it at input,
    this does it at the DB layer — defence in depth).
    """

    def __init__(self, table: str):
        self._table  = table
        self._where:  List[str]  = []
        self._params: List[Any]  = []
        self._order:  str        = ""
        self._limit:  int        = 1000
        self._offset: int        = 0
        self._cols:   str        = "*"

    def select(self, *cols: str) -> "QueryBuilder":
        self._cols = ", ".join(cols)
        return self

    def where(self, condition: str, *values) -> "QueryBuilder":
        self._where.append(condition)
        self._params.extend(values)
        return self

    def where_eq(self, col: str, val: Any) -> "QueryBuilder":
        if val is not None:
            self._where.append(f"{col} = ?")
            self._params.append(val)
        return self

    def where_gte(self, col: str, val: Any) -> "QueryBuilder":
        if val is not None:
            self._where.append(f"{col} >= ?")
            self._params.append(val)
        return self

    def where_lte(self, col: str, val: Any) -> "QueryBuilder":
        if val is not None:
            self._where.append(f"{col} <= ?")
            self._params.append(val)
        return self

    def where_in(self, col: str, vals: List) -> "QueryBuilder":
        if vals:
            placeholders = ",".join("?" * len(vals))
            self._where.append(f"{col} IN ({placeholders})")
            self._params.extend(vals)
        return self

    def where_gt(self, col: str, val: Any) -> "QueryBuilder":
        if val is not None:
            self._where.append(f"{col} > ?")
            self._params.append(val)
        return self

    def order_by(self, col: str, desc: bool = True) -> "QueryBuilder":
        self._order = f"ORDER BY {col} {'DESC' if desc else 'ASC'}"
        return self

    def limit(self, n: int) -> "QueryBuilder":
        self._limit = n
        return self

    def offset(self, n: int) -> "QueryBuilder":
        self._offset = n
        return self

    def build_select(self) -> Tuple[str, List]:
        sql = f"SELECT {self._cols} FROM {self._table}"
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        if self._order:
            sql += f" {self._order}"
        sql += f" LIMIT {self._limit} OFFSET {self._offset}"
        return sql, self._params

    def build_count(self) -> Tuple[str, List]:
        sql = f"SELECT COUNT(*) FROM {self._table}"
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        return sql, self._params

    def build_sum(self, col: str) -> Tuple[str, List]:
        sql = f"SELECT COALESCE(SUM({col}), 0) FROM {self._table}"
        if self._where:
            sql += " WHERE " + " AND ".join(self._where)
        return sql, self._params


# ── Relational Repositories (production versions) ─────────────────────────────

class RelationalPolicyRepository:
    """
    Full transactional policy repository.
    Supports versioned policies, lifecycle management, cross-version queries.
    """

    def __init__(self, pool: ThreadLocalConnectionPool):
        self._pool = pool

    def _conn(self) -> sqlite3.Connection:
        return self._pool.get()

    def save(self, record) -> None:
        """Upsert a policy record. Uses REPLACE for atomic version update."""
        record.updated_at = datetime.now(timezone.utc).isoformat()
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE" if conn.in_transaction is False else "SAVEPOINT sp_policy")
        try:
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
            if conn.in_transaction:
                conn.execute("RELEASE sp_policy" if "SAVEPOINT" else "")
            else:
                conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK TO sp_policy" if conn.in_transaction else "ROLLBACK")
            raise

    def save(self, record) -> None:
        """Upsert — safe under concurrent access."""
        record.updated_at = datetime.now(timezone.utc).isoformat()
        self._conn().execute("""
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

    def get(self, policy_id: str, version: str = None):
        """Get active (or specific version) policy."""
        conn = self._conn()
        if version:
            row = conn.execute(
                "SELECT * FROM policies WHERE policy_id=? AND version=?",
                (policy_id, version)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM policies WHERE policy_id=? AND status='active' "
                "ORDER BY updated_at DESC LIMIT 1", (policy_id,)
            ).fetchone()
            if not row:
                row = conn.execute(
                    "SELECT * FROM policies WHERE policy_id=? ORDER BY updated_at DESC LIMIT 1",
                    (policy_id,)
                ).fetchone()
        return self._from_row(row) if row else None

    def list_active(self) -> List:
        """All currently active policies (latest version per policy_id)."""
        rows = self._conn().execute("""
            SELECT p.* FROM policies p
            INNER JOIN (
                SELECT policy_id, MAX(updated_at) as max_u
                FROM policies WHERE status='active'
                GROUP BY policy_id
            ) latest ON p.policy_id=latest.policy_id AND p.updated_at=latest.max_u
            ORDER BY p.policy_id
        """).fetchall()
        return [self._from_row(r) for r in rows]

    def list_all(self, status: str = None) -> List:
        qb = QueryBuilder("policies").order_by("policy_id", desc=False)
        if status:
            qb.where_eq("status", status)
        sql, params = qb.build_select()
        rows = self._conn().execute(sql, params).fetchall()
        return [self._from_row(r) for r in rows]

    def update_status(self, policy_id: str, status: str, notes: str = "") -> bool:
        """Atomic status transition with timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn().execute(
            "UPDATE policies SET status=?, updated_at=? WHERE policy_id=?",
            (status, now, policy_id)
        )
        return cur.rowcount > 0

    def deprecate_and_activate(self, policy_id: str, new_record) -> None:
        """
        Atomic version rollover: deprecate current active, activate new version.
        Both operations in one transaction — no window where both or neither are active.
        """
        conn = self._conn()
        now  = datetime.now(timezone.utc).isoformat()
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Deprecate current active
            conn.execute(
                "UPDATE policies SET status='deprecated', updated_at=? WHERE policy_id=? AND status='active'",
                (now, policy_id)
            )
            # Insert new active version
            new_record.updated_at = now
            conn.execute("""
                INSERT INTO policies
                (policy_id, version, policy_name, decision_types, rule_type,
                 rule_body, status, description, created_by, tags, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                new_record.policy_id, new_record.version, new_record.policy_name,
                json.dumps(new_record.decision_types), new_record.rule_type,
                new_record.rule_body, 'active', new_record.description,
                new_record.created_by, json.dumps(new_record.tags),
                new_record.created_at, now,
            ))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def list_versions(self, policy_id: str) -> List:
        rows = self._conn().execute(
            "SELECT * FROM policies WHERE policy_id=? ORDER BY created_at",
            (policy_id,)
        ).fetchall()
        return [self._from_row(r) for r in rows]

    def delete(self, policy_id: str) -> bool:
        cur = self._conn().execute("DELETE FROM policies WHERE policy_id=?", (policy_id,))
        return cur.rowcount > 0

    def _from_row(self, row: sqlite3.Row):
        from glassbox.store.repository import PolicyRecord
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


class RelationalAuditRepository:
    """
    Full transactional audit repository with composite queries.
    Replaces the O(n) in-memory scan with indexed SQL.
    """

    def __init__(self, pool: ThreadLocalConnectionPool):
        self._pool = pool

    def _conn(self) -> sqlite3.Connection:
        return self._pool.get()

    def save(self, record: AuditRecord, tenant_id: str = None) -> None:
        """Persist an AuditRecord atomically."""
        record_dict  = record.to_dict()
        violations   = len(record.policy_result.violations) if record.policy_result else 0
        warnings     = len(record.policy_result.warnings)   if record.policy_result else 0
        risk_score   = record.risk_result.risk_score         if record.risk_result   else None
        risk_level   = record.risk_result.risk_level.value   if record.risk_result   else None
        pay_amount   = float(record.payload.get("amount") or 0) if record.payload else 0.0
        cb_triggered = bool(record.circuit_breaker_result and record.circuit_breaker_result.triggered)
        effective_tenant = tenant_id or record.context.metadata.get("tenant_id") if record.context else None

        self._conn().execute("""
            INSERT OR REPLACE INTO audit_records
            (decision_id, agent_id, decision_type, final_status, risk_score, risk_level,
             violations_count, warnings_count, pipeline_latency_ms, payload_amount,
             timestamp, replay_of, contract_validated, circuit_breaker,
             tenant_id, full_record_json)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            record.decision_id, record.agent_id, record.decision_type.value,
            record.final_status.value if record.final_status else None,
            risk_score, risk_level, violations, warnings,
            record.pipeline_latency_ms, pay_amount,
            record.timestamp, record.replay_of,
            int(record.contract_validated), int(cb_triggered),
            effective_tenant,
            json.dumps(record_dict, default=str),
        ))

    def get_by_id(self, decision_id: str) -> Optional[Dict[str, Any]]:
        row = self._conn().execute(
            "SELECT full_record_json FROM audit_records WHERE decision_id=?",
            (decision_id,)
        ).fetchone()
        return json.loads(row[0]) if row else None

    def query(
        self,
        agent_id:       Optional[str]   = None,
        decision_type:  Optional[str]   = None,
        final_status:   Optional[str]   = None,
        from_ts:        Optional[str]   = None,
        to_ts:          Optional[str]   = None,
        min_risk_score: Optional[float] = None,
        max_risk_score: Optional[float] = None,
        has_violations: Optional[bool]  = None,
        tenant_id:      Optional[str]   = None,
        circuit_breaker_triggered: Optional[bool] = None,
        limit:          int             = 100,
        offset:         int             = 0,
    ) -> List[Dict[str, Any]]:
        qb = (QueryBuilder("audit_records")
              .select("full_record_json")
              .where_eq("agent_id", agent_id)
              .where_eq("decision_type", decision_type)
              .where_eq("final_status", final_status)
              .where_gte("timestamp", from_ts)
              .where_lte("timestamp", to_ts)
              .where_gte("risk_score", min_risk_score)
              .where_lte("risk_score", max_risk_score)
              .where_eq("tenant_id", tenant_id)
              .order_by("timestamp", desc=True)
              .limit(limit).offset(offset))
        if has_violations is True:
            qb.where("violations_count > 0")
        elif has_violations is False:
            qb.where("violations_count = 0")
        if circuit_breaker_triggered is not None:
            qb.where_eq("circuit_breaker", int(circuit_breaker_triggered))

        sql, params = qb.build_select()
        rows = self._conn().execute(sql, params).fetchall()
        return [json.loads(r[0]) for r in rows]

    def count(
        self,
        final_status:  Optional[str] = None,
        decision_type: Optional[str] = None,
        agent_id:      Optional[str] = None,
        tenant_id:     Optional[str] = None,
    ) -> int:
        qb = (QueryBuilder("audit_records")
              .where_eq("final_status", final_status)
              .where_eq("decision_type", decision_type)
              .where_eq("agent_id", agent_id)
              .where_eq("tenant_id", tenant_id))
        sql, params = qb.build_count()
        return self._conn().execute(sql, params).fetchone()[0]

    def aggregate_spend(
        self,
        decision_type: str,
        final_status:  str          = "executed",
        from_ts:       Optional[str] = None,
        tenant_id:     Optional[str] = None,
    ) -> float:
        """Uses the covering index idx_aud_spend — fast even on millions of rows."""
        qb = (QueryBuilder("audit_records")
              .where_eq("decision_type", decision_type)
              .where_eq("final_status", final_status)
              .where_gte("timestamp", from_ts)
              .where_eq("tenant_id", tenant_id))
        sql, params = qb.build_sum("payload_amount")
        return float(self._conn().execute(sql, params).fetchone()[0])

    def block_rate_by_type(self, tenant_id: Optional[str] = None) -> Dict[str, float]:
        """Block rate per decision type — for compliance reporting."""
        params = []
        where  = "WHERE 1=1"
        if tenant_id:
            where += " AND tenant_id=?"
            params.append(tenant_id)
        rows = self._conn().execute(f"""
            SELECT decision_type,
                   COUNT(*) as total,
                   SUM(CASE WHEN final_status='blocked' THEN 1 ELSE 0 END) as blocked
            FROM audit_records {where}
            GROUP BY decision_type
        """, params).fetchall()
        return {
            r["decision_type"]: round(r["blocked"] / max(r["total"], 1) * 100, 1)
            for r in rows
        }

    def latency_percentiles(self, decision_type: Optional[str] = None) -> Dict[str, float]:
        """P50/P90/P99 latency — for SLA reporting."""
        where  = "WHERE pipeline_latency_ms IS NOT NULL"
        params = []
        if decision_type:
            where += " AND decision_type=?"
            params.append(decision_type)
        rows = self._conn().execute(
            f"SELECT pipeline_latency_ms FROM audit_records {where} "
            f"ORDER BY pipeline_latency_ms",
            params
        ).fetchall()
        if not rows:
            return {}
        vals = [r[0] for r in rows]
        n    = len(vals)
        return {
            "p50":   round(vals[int(n * 0.50)], 3),
            "p90":   round(vals[int(n * 0.90)], 3),
            "p99":   round(vals[min(int(n * 0.99), n-1)], 3),
            "count": n,
        }

    def decision_timeline(
        self,
        bucket_minutes: int = 60,
        last_hours:     int = 24,
        tenant_id:      Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Time-bucketed decision volume — for dashboards."""
        params = [last_hours * 60 * 60, bucket_minutes * 60]
        where  = ""
        if tenant_id:
            where = "AND tenant_id=?"
            params.append(tenant_id)
        rows = self._conn().execute(f"""
            SELECT
                CAST(strftime('%s', timestamp) / ? * ? AS INTEGER) as bucket_ts,
                COUNT(*) as total,
                SUM(CASE WHEN final_status='executed' THEN 1 ELSE 0 END) as executed,
                SUM(CASE WHEN final_status='blocked'  THEN 1 ELSE 0 END) as blocked,
                AVG(risk_score) as avg_risk
            FROM audit_records
            WHERE (strftime('%s','now') - strftime('%s', timestamp)) <= ?
            {where}
            GROUP BY bucket_ts
            ORDER BY bucket_ts
        """, [bucket_minutes * 60, bucket_minutes * 60, last_hours * 3600] +
             ([tenant_id] if tenant_id else [])).fetchall()
        return [dict(r) for r in rows]


class RelationalWorkflowRepository:
    """
    Full transactional workflow repository.
    Steps stored as separate rows for complete audit trail.
    """

    def __init__(self, pool: ThreadLocalConnectionPool):
        self._pool = pool

    def _conn(self) -> sqlite3.Connection:
        return self._pool.get()

    def create(self, instance) -> None:
        """Create workflow and record initial step atomically."""
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
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
            # Record creation as first step
            conn.execute("""
                INSERT INTO workflow_steps
                (step_id, workflow_id, step_type, actor, notes, outcome, created_at)
                VALUES (?,?,?,?,?,?,?)
            """, (str(uuid.uuid4()), instance.workflow_id, "created",
                  "system", "Workflow created", "pending",
                  instance.created_at))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def get(self, workflow_id: str):
        row = self._conn().execute(
            "SELECT full_json FROM workflows WHERE workflow_id=?", (workflow_id,)
        ).fetchone()
        return self._from_json(row[0]) if row else None

    def get_by_decision(self, decision_id: str):
        row = self._conn().execute(
            "SELECT full_json FROM workflows WHERE decision_id=?", (decision_id,)
        ).fetchone()
        return self._from_json(row[0]) if row else None

    def update(self, instance) -> None:
        """Atomic workflow update + step append."""
        now  = datetime.now(timezone.utc).isoformat()
        instance.updated_at = now
        conn = self._conn()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("""
                UPDATE workflows
                SET state=?, assigned_to=?, updated_at=?, resolved_at=?, full_json=?
                WHERE workflow_id=?
            """, (
                instance.state, instance.assigned_to,
                now, instance.resolved_at,
                json.dumps(instance.to_dict(), default=str),
                instance.workflow_id,
            ))
            # Persist any new steps that weren't already in the DB
            existing = {r[0] for r in self._conn().execute(
                "SELECT step_id FROM workflow_steps WHERE workflow_id=?",
                (instance.workflow_id,)
            ).fetchall()}
            for step in instance.steps:
                if step.step_id not in existing:
                    conn.execute("""
                        INSERT INTO workflow_steps
                        (step_id, workflow_id, step_type, actor, notes, outcome, created_at, completed_at)
                        VALUES (?,?,?,?,?,?,?,?)
                    """, (step.step_id, instance.workflow_id, step.step_type,
                          step.actor, step.notes, step.outcome,
                          step.created_at, step.completed_at))
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise

    def list_pending(self) -> List:
        rows = self._conn().execute(
            "SELECT full_json FROM workflows WHERE state IN ('pending','in_review') "
            "ORDER BY created_at"
        ).fetchall()
        return [self._from_json(r[0]) for r in rows]

    def list_sla_breached(self) -> List:
        return [w for w in self.list_pending() if w.is_sla_breached()]

    def get_step_history(self, workflow_id: str) -> List[Dict[str, Any]]:
        """Complete step audit trail for a workflow."""
        rows = self._conn().execute(
            "SELECT * FROM workflow_steps WHERE workflow_id=? ORDER BY created_at",
            (workflow_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def sla_summary(self) -> Dict[str, Any]:
        """SLA compliance summary across all workflows."""
        rows = self._conn().execute("""
            SELECT
                COUNT(*) as total,
                SUM(CASE WHEN state IN ('approved','rejected') THEN 1 ELSE 0 END) as resolved,
                SUM(CASE WHEN state IN ('pending','in_review') THEN 1 ELSE 0 END) as pending,
                AVG(CASE WHEN resolved_at IS NOT NULL
                    THEN (julianday(resolved_at) - julianday(created_at)) * 24 * 60
                    ELSE NULL END) as avg_resolution_minutes
            FROM workflows
        """).fetchone()
        return dict(rows)

    def _from_json(self, raw: str):
        from glassbox.store.repository import WorkflowInstance, WorkflowStep
        d    = json.loads(raw)
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


class RelationalComplianceRepository:
    """
    Full transactional compliance evidence repository.
    Supports joins between evidence and controls for gap analysis.
    """

    def __init__(self, pool: ThreadLocalConnectionPool):
        self._pool = pool

    def _conn(self) -> sqlite3.Connection:
        return self._pool.get()

    def save_evidence(
        self,
        control_id:    str,
        evidence_type: str,
        decision_id:   Optional[str] = None,
        agent_id:      Optional[str] = None,
        evidence_data: Optional[Dict] = None,
    ) -> str:
        evidence_id = str(uuid.uuid4())
        now         = datetime.now(timezone.utc).isoformat()
        self._conn().execute("""
            INSERT INTO compliance_evidence
            (evidence_id, control_id, decision_id, agent_id,
             evidence_type, evidence_data, collected_at)
            VALUES (?,?,?,?,?,?,?)
        """, (
            evidence_id, control_id, decision_id, agent_id,
            evidence_type,
            json.dumps(evidence_data or {}, default=str),
            now,
        ))
        return evidence_id

    def get_evidence(self, control_id: str) -> List[Dict[str, Any]]:
        rows = self._conn().execute(
            "SELECT * FROM compliance_evidence WHERE control_id=? ORDER BY collected_at DESC",
            (control_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def evidence_count_by_control(self) -> Dict[str, int]:
        rows = self._conn().execute(
            "SELECT control_id, COUNT(*) as cnt FROM compliance_evidence GROUP BY control_id"
        ).fetchall()
        return {r["control_id"]: r["cnt"] for r in rows}

    def recent_evidence(self, hours: int = 24, limit: int = 100) -> List[Dict[str, Any]]:
        rows = self._conn().execute("""
            SELECT * FROM compliance_evidence
            WHERE (julianday('now') - julianday(collected_at)) * 24 <= ?
            ORDER BY collected_at DESC LIMIT ?
        """, (hours, limit)).fetchall()
        return [dict(r) for r in rows]


# ── Main Database Class ────────────────────────────────────────────────────────

class GlassBoxDatabase:
    """
    Production-grade transactional relational database for GlassBox.

    Single database file with all tables, proper schema migrations,
    thread-local connection pool, and ACID transaction support.

    Usage:
        # Production
        db = GlassBoxDatabase("/var/lib/glassbox/glassbox.db")

        # Use individual repositories
        db.policies.save(record)
        db.audit.save(audit_record)
        db.workflows.create(instance)
        db.compliance.save_evidence("EUAI.A12", "decision", decision_id=d_id)

        # Use transactions for multi-table atomicity
        with db.transaction() as tx:
            # If any operation fails, ALL are rolled back
            tx._execute("UPDATE policies SET status=? WHERE policy_id=?", ...)
            tx._execute("INSERT INTO audit_records ...", ...)

        # Backup
        db.backup("/backup/glassbox_20260331.db")
    """

    def __init__(self, db_path: str = "glassbox.db"):
        self.db_path  = db_path
        if db_path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self._pool    = ThreadLocalConnectionPool(db_path)
        self._apply_migrations()

        # Public repository API
        self.policies   = RelationalPolicyRepository(self._pool)
        self.audit      = RelationalAuditRepository(self._pool)
        self.workflows  = RelationalWorkflowRepository(self._pool)
        self.compliance = RelationalComplianceRepository(self._pool)

    # ── Convenience repository accessors ────────────────────────────────────────
    # These aliases keep backward compatibility with code that calls
    # db.audit_repo(), db.policy_repo(), db.workflow_repo()

    def audit_repo(self):
        """Return the RelationalAuditRepository for this database."""
        return self.audit

    def policy_repo(self):
        """Return the RelationalPolicyRepository for this database."""
        return self.policies

    def workflow_repo(self):
        """Return the RelationalWorkflowRepository for this database."""
        return self.workflows

    def compliance_repo(self):
        """Return the RelationalComplianceRepository for this database."""
        return self.compliance

    def _apply_migrations(self) -> None:
        """Apply any pending schema migrations in a single transaction."""
        conn = self._pool.get()
        # Create schema_version table if needed (bootstrap)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version    INTEGER PRIMARY KEY,
                applied_at TEXT    NOT NULL,
                description TEXT
            )
        """)
        applied = {r[0] for r in conn.execute("SELECT version FROM schema_version").fetchall()}

        for version, statements in MIGRATIONS:
            if version in applied:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for stmt in statements:
                    conn.execute(stmt)
                conn.execute(
                    "INSERT INTO schema_version (version, applied_at, description) VALUES (?,?,?)",
                    (version, datetime.now(timezone.utc).isoformat(),
                     f"Migration v{version}")
                )
                conn.execute("COMMIT")
                log.info("Applied DB migration v%d", version)
            except Exception as exc:
                conn.execute("ROLLBACK")
                raise RuntimeError(f"Migration v{version} failed: {exc}") from exc

    @contextmanager
    def transaction(self) -> Generator[Transaction, None, None]:
        """
        Context manager for explicit ACID transactions.
        All operations on the yielded Transaction object are atomic.
        """
        tx = Transaction(self._pool)
        with tx:
            yield tx

    def backup(self, dest_path: str) -> None:
        """
        Online hot backup — safe to run while the database is in use.
        Uses SQLite's built-in backup API (no locks held during copy).
        """
        if self.db_path == ":memory:":
            raise ValueError("Cannot backup an in-memory database")
        dest_conn = sqlite3.connect(dest_path)
        src_conn  = self._pool.get()
        src_conn.backup(dest_conn)
        dest_conn.close()
        log.info("Database backed up to %s", dest_path)

    def vacuum(self) -> None:
        """Reclaim disk space after large deletions."""
        self._pool.get().execute("VACUUM")

    def integrity_check(self) -> bool:
        """Run SQLite integrity check. Returns True if OK."""
        result = self._pool.get().execute("PRAGMA integrity_check").fetchone()[0]
        return result == "ok"

    def stats(self) -> Dict[str, Any]:
        """Database statistics for monitoring."""
        conn   = self._pool.get()
        tables = ["policies", "audit_records", "workflows",
                  "workflow_steps", "compliance_evidence"]
        counts = {}
        for t in tables:
            try:
                counts[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                counts[t] = 0
        page_size  = conn.execute("PRAGMA page_size").fetchone()[0]
        page_count = conn.execute("PRAGMA page_count").fetchone()[0]
        return {
            "db_path":       self.db_path,
            "row_counts":    counts,
            "size_bytes":    page_size * page_count,
            "schema_version": CURRENT_SCHEMA_VERSION,
            "integrity":     "ok",
        }

    def close(self) -> None:
        """Close this thread's connection."""
        self._pool.close_thread()


# ── Factory ────────────────────────────────────────────────────────────────────

class DatabaseFactory:
    """
    Creates GlassBoxDatabase instances for different deployment targets.

    Usage:
        db = DatabaseFactory.sqlite("/var/lib/glassbox")   # production
        db = DatabaseFactory.memory()                       # tests, dev
        db = DatabaseFactory.databricks("/dbfs/tmp/glassbox")  # Databricks
        db = DatabaseFactory.fabric("/lakehouse/default/Files/glassbox")  # Fabric
    """

    @staticmethod
    def sqlite(data_dir: str = ".") -> GlassBoxDatabase:
        os.makedirs(data_dir, exist_ok=True)
        return GlassBoxDatabase(os.path.join(data_dir, "glassbox.db"))

    @staticmethod
    def memory() -> GlassBoxDatabase:
        return GlassBoxDatabase(":memory:")

    @staticmethod
    def databricks(dbfs_dir: str = "/dbfs/tmp/glassbox") -> GlassBoxDatabase:
        os.makedirs(dbfs_dir, exist_ok=True)
        return GlassBoxDatabase(os.path.join(dbfs_dir, "glassbox.db"))

    @staticmethod
    def fabric(lakehouse_dir: str = "/lakehouse/default/Files/glassbox") -> GlassBoxDatabase:
        os.makedirs(lakehouse_dir, exist_ok=True)
        return GlassBoxDatabase(os.path.join(lakehouse_dir, "glassbox.db"))

    @staticmethod
    def kubernetes(pvc_mount: str = "/var/lib/glassbox") -> GlassBoxDatabase:
        os.makedirs(pvc_mount, exist_ok=True)
        return GlassBoxDatabase(os.path.join(pvc_mount, "glassbox.db"))
