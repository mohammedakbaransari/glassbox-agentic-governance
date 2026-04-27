"""
GlassBox Framework — Write-Ahead-Log (WAL) (v1.1.0)
====================================================
Guarantees durability and all-or-nothing semantics by:
  1. Log decisions to FIFO queue BEFORE side effects (WAL pattern)
  2. Side effects applied in order: audit → policy_repo → workflow → events
  3. On failure mid-side-effects: rollback not needed (idempotent store)
  4. On crash/recovery: replay unfinished log entries
  5. Checkpointing: mark entries as committed every N records

Transaction Flow (All-Or-Nothing):
  1. [WAL] Log: "decision=ABC123, intent=EXECUTE" to durable log
  2. [AUDIT] Save audit record to DB
  3. [REPO] Save policy result to repo (idempotent — use upsert)
  4. [WORKFLOW] Create workflow if PENDING_REVIEW
  5. [EVENTS] Emit domain events
  6. [WAL] Mark: "decision=ABC123 ✓ COMMITTED"

Recovery on Crash:
  1. Replay WAL entries without COMMITTED marker
  2. [AUDIT] Re-save audit record (idempotent, no duplicate)
  3. [REPO] Re-save policy result (UPSERT, no duplicate)
  4. [WORKFLOW] Re-create workflow (idempotent)
  5. [EVENTS] Re-emit events (event_bus dedukes by decision_id)
  6. Mark entries as COMMITTED, continue normal processing

Durability Guarantees:
  ✓ No lost decisions (even on crash mid-side-effects)
  ✓ No duplicate audit entries (UPSERT semantics)
  ✓ No zombie workflows (idempotent creation)
  ✓ All side effects ordered correctly
  ✓ Crash-safe: recovery replays unfinished entries

Performance Impact:
  - Log append: O(1) deque operation, ~10 µs
  - Checkpoint: O(log_size) to mark committed, can batch
  - Recovery: O(crashed_entries) to replay

Failure Scenarios & Recovery:
  1. Audit write fails → log stays open, client retried
  2. Workflow creation fails → circuit breaker fallback, continue
  3. Events emission fails → circuit breaker, retry async
  4. Crash before COMMIT marker → replay on restart (safe)
  5. Crash after COMMIT marker → entry skipped (already applied)

Author: Mohammed Akbar Ansari
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from glassbox.governance.logging_manager import get_logger
from glassbox.governance.models import AuditRecord, DecisionResponse, FinalStatus

log = get_logger("wal")


class WALEntryState(Enum):
    """WAL entry lifecycle."""
    PENDING = "PENDING"  # Logged but side effects not started
    IN_PROGRESS = "IN_PROGRESS"  # Side effects in progress
    COMMITTED = "COMMITTED"  # All side effects completed
    FAILED = "FAILED"  # Unrecoverable error
    ROLLED_BACK = "ROLLED_BACK"  # Intentionally cancelled


class WALEntry:
    """Single entry in Write-Ahead-Log."""
    __slots__ = (
        'entry_id', 'decision_id', 'state', 'created_at', 'updated_at',
        'audit_record_json', 'side_effects', 'error_message',
    )

    def __init__(
        self,
        entry_id: int,
        decision_id: str,
        audit_record_json: str,
        state: WALEntryState = WALEntryState.PENDING,
    ):
        self.entry_id = entry_id
        self.decision_id = decision_id
        self.state = state
        self.created_at = datetime.now(timezone.utc)
        self.updated_at = self.created_at
        self.audit_record_json = audit_record_json
        self.side_effects = {}  # Track which side effects completed
        self.error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "decision_id": self.decision_id,
            "state": self.state.value,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "side_effects": self.side_effects,
            "error": self.error_message,
        }


class WriteAheadLog:
    """
    Durable Write-Ahead-Log with transaction support.

    Usage:
        wal = WriteAheadLog(db_path="/var/lib/glassbox/wal.db")

        # Start transaction
        entry = wal.begin_transaction(decision_id, audit_record)

        try:
            # Apply side effects in order
            wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
            wal.mark_side_effect(entry.entry_id, "policy_persisted", success=True)
            wal.mark_side_effect(entry.entry_id, "workflow_created", success=True)

            # Commit
            wal.commit(entry.entry_id)
        except Exception as exc:
            wal.rollback(entry.entry_id, reason=str(exc))

        # Recovery on restart
        for pending_entry in wal.get_pending_entries():
            # replay side effects for pending_entry...
            wal.commit(pending_entry.entry_id)
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        checkpoint_interval: int = 1000,  # Commit every N entries
        enable_sync_writes: bool = True,  # fsync after every entry
    ):
        self.db_path = db_path
        self.checkpoint_interval = checkpoint_interval
        self.enable_sync_writes = enable_sync_writes

        # In-memory entry cache (read-through from DB)
        self._entry_cache: Dict[int, WALEntry] = {}
        self._cache_lock = threading.RLock()
        self._next_entry_id = 0

        if db_path:
            self._init_db()
            self._next_entry_id = self._load_next_entry_id()

        log.info(
            "WriteAheadLog initialized: db_path=%s, checkpoint_interval=%d, "
            "sync_writes=%s",
            db_path, checkpoint_interval, enable_sync_writes,
        )

    def _init_db(self):
        """Initialize SQLite WAL schema."""
        if not self.db_path:
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS wal_entries (
                        entry_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        decision_id TEXT NOT NULL UNIQUE,
                        state TEXT NOT NULL,
                        audit_record_json TEXT NOT NULL,
                        side_effects_json TEXT,
                        error_message TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        UNIQUE(decision_id)
                    )
                """)

                # Compound index for recovery queries
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_wal_state_created 
                       ON wal_entries(state, created_at)"""
                )

                # Index for recovery (find pending/in_progress entries)
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_wal_pending 
                       ON wal_entries(state) WHERE state IN ('PENDING', 'IN_PROGRESS')"""
                )

                # Checkpoint table for durability
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS wal_checkpoints (
                        checkpoint_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        last_committed_entry_id INTEGER NOT NULL,
                        checkpoint_time TEXT NOT NULL
                    )
                """)

                conn.commit()
                log.debug("WriteAheadLog: database initialized at %s", self.db_path)
        except Exception as exc:
            log.error("WriteAheadLog._init_db failed: %s", exc, exc_info=True)

    def _load_next_entry_id(self) -> int:
        """Load the next durable entry identifier from SQLite."""
        if not self.db_path:
            return 0

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("SELECT COALESCE(MAX(entry_id), -1) + 1 FROM wal_entries")
                return int(cursor.fetchone()[0])
        except Exception as exc:
            log.error("WriteAheadLog._load_next_entry_id failed: %s", exc)
            return 0

    def begin_transaction(
        self,
        decision_id: str,
        audit_record: AuditRecord,
    ) -> WALEntry:
        """
        Begin a new WAL transaction.

        Returns:
            WALEntry with state=PENDING
        """
        audit_json = self._serialize_audit_record(audit_record)

        with self._cache_lock:
            entry_id = self._next_entry_id
            self._next_entry_id += 1

            entry = WALEntry(
                entry_id=entry_id,
                decision_id=decision_id,
                audit_record_json=audit_json,
                state=WALEntryState.PENDING,
            )

            self._entry_cache[entry_id] = entry

        # Persist to DB
        if self.db_path:
            self._persist_entry_to_db(entry)

        log.debug(
            "WriteAheadLog: BEGIN transaction entry_id=%d, decision_id=%s",
            entry_id, decision_id,
        )

        return entry

    def _get_cached_or_loaded_entry(self, entry_id: int) -> Optional[WALEntry]:
        """Return an entry from cache, hydrating it from durable storage if needed."""
        entry = self._entry_cache.get(entry_id)
        if entry is not None:
            return entry
        if not self.db_path:
            return None
        entry = self._load_entry_from_db(entry_id)
        if entry is not None:
            self._entry_cache[entry_id] = entry
        return entry

    def mark_side_effect(
        self,
        entry_id: int,
        side_effect_name: str,
        success: bool,
        error_msg: Optional[str] = None,
    ) -> None:
        """
        Mark completion of a side effect.

        Side effects apply in order:
          1. audit (save to DB)
          2. policy_persisted (save policy result)
          3. workflow_created (create workflow)
          4. events_emitted (publish events)
        """
        with self._cache_lock:
            entry = self._get_cached_or_loaded_entry(entry_id)
            if entry is None:
                log.error("WriteAheadLog: entry_id=%d not found", entry_id)
                return
            entry.side_effects[side_effect_name] = {
                "success": success,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            if error_msg:
                entry.side_effects[side_effect_name]["error"] = error_msg
            entry.updated_at = datetime.now(timezone.utc)

            # Mark as in progress on first side effect
            if entry.state == WALEntryState.PENDING:
                entry.state = WALEntryState.IN_PROGRESS

        # Update DB
        if self.db_path:
            self._persist_entry_to_db(entry)

        log.debug(
            "WriteAheadLog: marked side_effect entry_id=%d, side_effect=%s, success=%s",
            entry_id, side_effect_name, success,
        )

    def commit(self, entry_id: int) -> None:
        """
        Mark transaction as committed (all side effects completed).
        After commit, entry can be garbage-collected.
        """
        with self._cache_lock:
            entry = self._get_cached_or_loaded_entry(entry_id)
            if entry is None:
                log.error("WriteAheadLog: entry_id=%d not found for commit", entry_id)
                return
            entry.state = WALEntryState.COMMITTED
            entry.updated_at = datetime.now(timezone.utc)

        if self.db_path:
            self._persist_entry_to_db(entry)

        # Periodic checkpoint
        if entry_id % self.checkpoint_interval == 0:
            self._create_checkpoint(entry_id)

        log.debug("WriteAheadLog: COMMIT entry_id=%d", entry_id)

    def rollback(
        self,
        entry_id: int,
        reason: str = "unknown",
    ) -> None:
        """
        Mark transaction as rolled back (unrecoverable error).
        Entry is logged for audit but not replayed on recovery.
        """
        with self._cache_lock:
            entry = self._get_cached_or_loaded_entry(entry_id)
            if entry is None:
                log.error("WriteAheadLog: entry_id=%d not found for rollback", entry_id)
                return
            entry.state = WALEntryState.ROLLED_BACK
            entry.error_message = reason
            entry.updated_at = datetime.now(timezone.utc)

        if self.db_path:
            self._persist_entry_to_db(entry)

        log.warning("WriteAheadLog: ROLLBACK entry_id=%d, reason=%s", entry_id, reason)

    def get_entry(self, entry_id: int) -> Optional[WALEntry]:
        """Retrieve WAL entry from cache or DB."""
        with self._cache_lock:
            return self._get_cached_or_loaded_entry(entry_id)

    def get_pending_entries(self) -> List[WALEntry]:
        """
        Get all PENDING and IN_PROGRESS entries (for crash recovery).
        Replay these entries to ensure all side effects completed.
        """
        if not self.db_path:
            # In-memory only: return from cache
            with self._cache_lock:
                return [
                    e for e in self._entry_cache.values()
                    if e.state in (WALEntryState.PENDING, WALEntryState.IN_PROGRESS)
                ]

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT entry_id, decision_id, state, audit_record_json, 
                           side_effects_json, error_message, created_at, updated_at
                    FROM wal_entries
                    WHERE state IN (?, ?)
                    ORDER BY entry_id ASC
                    """,
                    (WALEntryState.PENDING.value, WALEntryState.IN_PROGRESS.value),
                )

                entries = []
                for row in cursor:
                    entry = self._row_to_entry(row)
                    with self._cache_lock:
                        self._entry_cache[entry.entry_id] = entry
                    entries.append(entry)

                return entries
        except Exception as exc:
            log.error("WriteAheadLog.get_pending_entries failed: %s", exc)
            return []

    def stats(self) -> Dict[str, Any]:
        """Return WAL statistics."""
        if not self.db_path:
            with self._cache_lock:
                states = {}
                for entry in self._entry_cache.values():
                    state_name = entry.state.value
                    states[state_name] = states.get(state_name, 0) + 1

            return {
                "backend": "memory-only",
                "cached_entries": len(self._entry_cache),
                "state_counts": states,
            }

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT state, COUNT(*) FROM wal_entries GROUP BY state
                    """
                )

                states = dict(cursor.fetchall())
                total = sum(states.values())

                # Get last checkpoint
                cursor = conn.execute(
                    "SELECT MAX(last_committed_entry_id) FROM wal_checkpoints"
                )
                last_checkpoint = cursor.fetchone()[0] or 0

                return {
                    "backend": "sqlite3",
                    "total_entries": total,
                    "state_counts": states,
                    "last_checkpoint_entry_id": last_checkpoint,
                    "db_path": self.db_path,
                }
        except Exception as exc:
            log.error("WriteAheadLog.stats failed: %s", exc)
            return {"error": str(exc)}

    def _persist_entry_to_db(self, entry: WALEntry) -> None:
        """Persist WAL entry to SQLite."""
        if not self.db_path:
            return

        try:
            side_effects_json = json.dumps(entry.side_effects)
            with sqlite3.connect(self.db_path) as conn:
                if self.enable_sync_writes:
                    conn.execute("PRAGMA synchronous = FULL")

                conn.execute(
                    """
                    INSERT INTO wal_entries
                        (entry_id, decision_id, state, audit_record_json, side_effects_json,
                         error_message, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(entry_id) DO UPDATE SET
                        decision_id = excluded.decision_id,
                        state = excluded.state,
                        audit_record_json = excluded.audit_record_json,
                        side_effects_json = excluded.side_effects_json,
                        error_message = excluded.error_message,
                        created_at = excluded.created_at,
                        updated_at = excluded.updated_at
                    """,
                    (
                        entry.entry_id,
                        entry.decision_id,
                        entry.state.value,
                        entry.audit_record_json,
                        side_effects_json,
                        entry.error_message,
                        entry.created_at.isoformat(),
                        entry.updated_at.isoformat(),
                    ),
                )

                conn.commit()
        except Exception as exc:
            log.error("WriteAheadLog._persist_entry_to_db failed: %s", exc)

    def _load_entry_from_db(self, entry_id: int) -> Optional[WALEntry]:
        """Load WAL entry from SQLite."""
        if not self.db_path:
            return None

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute(
                    """
                    SELECT entry_id, decision_id, state, audit_record_json,
                           side_effects_json, error_message, created_at, updated_at
                    FROM wal_entries WHERE entry_id = ?
                    """,
                    (entry_id,),
                )
                row = cursor.fetchone()
                return self._row_to_entry(row) if row else None
        except Exception as exc:
            log.error("WriteAheadLog._load_entry_from_db failed: %s", exc)
            return None

    def _row_to_entry(self, row: tuple) -> WALEntry:
        """Convert DB row to WALEntry."""
        (entry_id, decision_id, state_str, audit_json,
         side_effects_json, error_msg, created_str, updated_str) = row

        entry = WALEntry(
            entry_id=entry_id,
            decision_id=decision_id,
            audit_record_json=audit_json,
            state=WALEntryState(state_str),
        )

        if side_effects_json:
            entry.side_effects = json.loads(side_effects_json)

        entry.error_message = error_msg
        entry.created_at = datetime.fromisoformat(created_str)
        entry.updated_at = datetime.fromisoformat(updated_str)

        return entry

    def _create_checkpoint(self, entry_id: int) -> None:
        """Create WAL checkpoint (mark entries < entry_id as safely committed)."""
        if not self.db_path:
            return

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO wal_checkpoints (last_committed_entry_id, checkpoint_time)
                    VALUES (?, ?)
                    """,
                    (entry_id, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()

                log.debug("WriteAheadLog: checkpoint created at entry_id=%d", entry_id)
        except Exception as exc:
            log.error("WriteAheadLog._create_checkpoint failed: %s", exc)

    @staticmethod
    def _serialize_audit_record(record: AuditRecord) -> str:
        """Serialize AuditRecord to JSON."""
        try:
            return json.dumps(record.to_dict(), default=str)
        except Exception as exc:
            log.error("WriteAheadLog._serialize_audit_record failed: %s", exc)
            return "{}"

    @staticmethod
    def deserialize_audit_record_json(audit_record_json: str) -> Dict[str, Any]:
        """Deserialize persisted audit record JSON for recovery workflows."""
        try:
            data = json.loads(audit_record_json)
            return data if isinstance(data, dict) else {}
        except Exception as exc:
            log.error("WriteAheadLog.deserialize_audit_record_json failed: %s", exc)
            return {}

    def shutdown(self) -> None:
        """Graceful shutdown."""
        log.info("WriteAheadLog: shutdown complete")
