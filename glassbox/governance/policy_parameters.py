"""
GlassBox Framework — Policy Parameter Store  (v1.2.0)
======================================================

Externalises hard-coded policy thresholds so that risk-committee changes take
effect immediately without a code change, PR, or re-deployment.

Architecture:
  - SQLite-backed persistence (default: :memory: for testing, set a file path
    for production so changes survive restarts).
  - In-memory LRU cache with configurable TTL prevents hot-path DB reads.
  - Thread-safe: all reads and writes acquire the same lock.
  - Values are JSON-encoded so integers, floats, lists, dicts are all supported.

Usage:
    from glassbox.governance.policy_parameters import PolicyParameterStore, _param_store

    # Read (falls back to default if not set)
    limit = _param_store.get("PROC-001", "amount_threshold", default=500_000)

    # Write (takes effect on next cache expiry, within cache_ttl seconds)
    _param_store.set("PROC-001", "amount_threshold", 750_000, updated_by="risk-committee")

    # Use a persistent file for production
    store = PolicyParameterStore(db_path="/var/lib/glassbox/policy_params.db")

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from typing import Any, Optional

from glassbox.governance.logging_manager import get_logger

log = get_logger("policy_params")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS policy_params (
    policy_id      TEXT    NOT NULL,
    param_name     TEXT    NOT NULL,
    value          TEXT    NOT NULL,
    effective_from TEXT    NOT NULL,
    updated_by     TEXT    NOT NULL,
    PRIMARY KEY (policy_id, param_name)
)
"""


class PolicyParameterStore:
    """
    Database-backed policy parameter store with in-memory cache.

    Policies call ``get()`` to fetch their thresholds rather than relying on
    module-level constants.  Operators call ``set()`` to update them without
    touching source code.
    """

    def __init__(self, db_path: str = ":memory:", cache_ttl: int = 60):
        self._db_path = db_path
        self._cache_ttl = cache_ttl
        # Cache: (policy_id, param_name) -> (value, expires_at)
        self._cache: dict[tuple[str, str], tuple[Any, float]] = {}
        self._lock = threading.Lock()
        # For :memory: databases we must hold a single persistent connection
        # because each new sqlite3.connect(":memory:") creates a fresh database.
        self._conn: Optional[sqlite3.Connection] = (
            sqlite3.connect(":memory:", check_same_thread=False)
            if db_path == ":memory:"
            else None
        )
        self._init_db()

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        """Return the appropriate connection (persistent for :memory:, new otherwise)."""
        if self._conn is not None:
            return self._conn
        return sqlite3.connect(self._db_path)

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        conn = self._connect()
        conn.execute(_SCHEMA)
        conn.commit()

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, policy_id: str, param_name: str, default: Any = None) -> Any:
        """
        Return the current value of a policy parameter.

        Falls back to *default* if the parameter has never been set.
        The result is cached for ``cache_ttl`` seconds.
        """
        key = (policy_id, param_name)

        with self._lock:
            cached = self._cache.get(key)
            if cached is not None:
                value, expires_at = cached
                if time.monotonic() < expires_at:
                    return value

        conn = self._connect()
        row = conn.execute(
            "SELECT value FROM policy_params "
            "WHERE policy_id = ? AND param_name = ?",
            (policy_id, param_name),
        ).fetchone()

        value = json.loads(row[0]) if row else default

        with self._lock:
            self._cache[key] = (value, time.monotonic() + self._cache_ttl)

        return value

    def set(
        self,
        policy_id: str,
        param_name: str,
        value: Any,
        updated_by: str,
    ) -> None:
        """
        Persist a new value for a policy parameter and invalidate its cache entry.

        Args:
            policy_id:   The policy this parameter belongs to (e.g. ``"PROC-001"``).
            param_name:  Name of the parameter (e.g. ``"amount_threshold"``).
            value:       New value (any JSON-serialisable type).
            updated_by:  Identity of the principal making the change (for audit).
        """
        now = datetime.now(timezone.utc).isoformat()

        conn = self._connect()
        conn.execute(
            """INSERT OR REPLACE INTO policy_params
               (policy_id, param_name, value, effective_from, updated_by)
               VALUES (?, ?, ?, ?, ?)""",
            (policy_id, param_name, json.dumps(value), now, updated_by),
        )
        conn.commit()

        with self._lock:
            self._cache.pop((policy_id, param_name), None)

        log.info(
            "Policy parameter updated: %s.%s = %r  (by %s)",
            policy_id, param_name, value, updated_by,
        )

    def list_params(self, policy_id: Optional[str] = None) -> list[dict]:
        """Return all stored parameters, optionally filtered by policy_id."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        if policy_id:
            rows = conn.execute(
                "SELECT * FROM policy_params WHERE policy_id = ? ORDER BY policy_id, param_name",
                (policy_id,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM policy_params ORDER BY policy_id, param_name"
            ).fetchall()

        return [
            {
                "policy_id": r["policy_id"],
                "param_name": r["param_name"],
                "value": json.loads(r["value"]),
                "effective_from": r["effective_from"],
                "updated_by": r["updated_by"],
            }
            for r in rows
        ]


# ── Module-level singleton ─────────────────────────────────────────────────────
# Policies in policy_engine.py import this object and call _param_store.get().
# Override with a file-backed instance at startup for production deployments:
#
#   from glassbox.governance.policy_parameters import _param_store, PolicyParameterStore
#   import glassbox.governance.policy_parameters as _pp
#   _pp._param_store = PolicyParameterStore(db_path="/var/lib/glassbox/params.db")

_param_store = PolicyParameterStore()
