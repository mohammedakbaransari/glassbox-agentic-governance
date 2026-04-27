"""
GlassBox — Edge-Case Test Suite  (O1 coverage)
===============================================
Targets the three subsystems identified in the technical review as having
the lowest coverage and highest incident probability:

  1. WriteAheadLog  — crash-recovery, partial-commit replay, DB persistence
  2. Multi-Tenancy  — quota enforcement, invalid IDs, LRU eviction, isolation
  3. AdvancedAudit  — hash-chain integrity, tamper detection, genesis sentinel

All tests use stdlib only (no external dependencies).  Each test is
independent: no shared state across tests.
"""

import threading
import time
import uuid
import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_audit_record(agent_id: str = "test-agent", decision_type: str = "procurement"):
    from glassbox.governance.models import (
        AuditRecord, DecisionContext, DecisionType, FinalStatus,
    )
    ctx = DecisionContext(environment="test", source_system="pytest")
    rec = AuditRecord(
        agent_id=agent_id,
        decision_type=DecisionType(decision_type),
        payload={"amount": 1000.0},
        context=ctx,
    )
    rec.final_status = FinalStatus.EXECUTED
    return rec


# ===========================================================================
# 1. WriteAheadLog
# ===========================================================================

class TestWriteAheadLogInMemory:
    """In-memory WAL (no db_path) — covers the cache-only code paths."""

    def setup_method(self):
        from glassbox.governance.write_ahead_log import WriteAheadLog
        self.wal = WriteAheadLog()  # in-memory

    def test_begin_returns_pending_entry(self):
        from glassbox.governance.write_ahead_log import WALEntryState
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        assert entry.state == WALEntryState.PENDING
        assert entry.entry_id == 0

    def test_mark_side_effect_transitions_to_in_progress(self):
        from glassbox.governance.write_ahead_log import WALEntryState
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        self.wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
        e2 = self.wal.get_entry(entry.entry_id)
        assert e2.state == WALEntryState.IN_PROGRESS
        assert e2.side_effects["audit_saved"]["success"] is True

    def test_commit_marks_committed(self):
        from glassbox.governance.write_ahead_log import WALEntryState
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        self.wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
        self.wal.commit(entry.entry_id)
        e2 = self.wal.get_entry(entry.entry_id)
        assert e2.state == WALEntryState.COMMITTED

    def test_rollback_marks_rolled_back(self):
        from glassbox.governance.write_ahead_log import WALEntryState
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        self.wal.rollback(entry.entry_id, reason="simulated failure")
        e2 = self.wal.get_entry(entry.entry_id)
        assert e2.state == WALEntryState.ROLLED_BACK
        assert "simulated failure" in (e2.error_message or "")

    def test_get_pending_returns_only_unfinished(self):
        from glassbox.governance.write_ahead_log import WALEntryState
        rec1 = _make_audit_record("a1")
        rec2 = _make_audit_record("a2")
        e1   = self.wal.begin_transaction(rec1.decision_id, rec1)
        e2   = self.wal.begin_transaction(rec2.decision_id, rec2)
        self.wal.commit(e1.entry_id)
        pending = self.wal.get_pending_entries()
        ids = {e.entry_id for e in pending}
        assert e1.entry_id not in ids
        assert e2.entry_id in ids

    def test_partial_side_effects_stay_pending(self):
        """Only audit_saved marked; workflow_created still pending."""
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        self.wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)
        # Do NOT mark workflow_created or commit
        pending = self.wal.get_pending_entries()
        assert any(e.entry_id == entry.entry_id for e in pending)

    def test_failed_side_effect_recorded_with_error(self):
        rec   = _make_audit_record()
        entry = self.wal.begin_transaction(rec.decision_id, rec)
        self.wal.mark_side_effect(
            entry.entry_id, "repo_saved", success=False,
            error_msg="DB connection refused",
        )
        e2 = self.wal.get_entry(entry.entry_id)
        assert e2.side_effects["repo_saved"]["success"] is False
        assert "DB connection refused" in e2.side_effects["repo_saved"]["error"]

    def test_sequential_entry_ids_increment(self):
        rec1 = _make_audit_record("a1")
        rec2 = _make_audit_record("a2")
        e1   = self.wal.begin_transaction(rec1.decision_id, rec1)
        e2   = self.wal.begin_transaction(rec2.decision_id, rec2)
        assert e2.entry_id == e1.entry_id + 1

    def test_stats_reflect_state_counts(self):
        rec1 = _make_audit_record("a1")
        rec2 = _make_audit_record("a2")
        e1   = self.wal.begin_transaction(rec1.decision_id, rec1)
        e2   = self.wal.begin_transaction(rec2.decision_id, rec2)
        self.wal.commit(e1.entry_id)
        stats = self.wal.stats()
        assert stats["backend"] == "memory-only"
        assert stats["state_counts"].get("COMMITTED", 0) >= 1

    def test_mark_nonexistent_entry_is_safe(self):
        """mark_side_effect on unknown entry_id must not raise."""
        self.wal.mark_side_effect(9999, "audit_saved", success=True)  # no exception

    def test_concurrent_begin_no_id_collision(self):
        """100 concurrent begin_transaction calls must produce distinct entry_ids."""
        results = []
        lock    = threading.Lock()

        def _begin():
            rec   = _make_audit_record()
            entry = self.wal.begin_transaction(rec.decision_id, rec)
            with lock:
                results.append(entry.entry_id)

        threads = [threading.Thread(target=_begin) for _ in range(100)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(set(results)) == 100, "Duplicate entry_ids under concurrency"


class TestWriteAheadLogSQLite:
    """SQLite-backed WAL — persistence, recovery, and checkpoint paths."""

    def setup_method(self, method):
        import tempfile, os
        self._tmpdir = tempfile.mkdtemp()
        self._db     = os.path.join(self._tmpdir, "wal_test.db")

    def teardown_method(self, method):
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _new_wal(self):
        from glassbox.governance.write_ahead_log import WriteAheadLog
        return WriteAheadLog(db_path=self._db)

    def test_committed_entry_not_in_pending_after_restart(self):
        wal1 = self._new_wal()
        rec  = _make_audit_record()
        entry = wal1.begin_transaction(rec.decision_id, rec)
        wal1.mark_side_effect(entry.entry_id, "audit_saved", success=True)
        wal1.commit(entry.entry_id)

        # Simulate restart by creating a new WAL instance on same DB
        wal2 = self._new_wal()
        pending = wal2.get_pending_entries()
        ids = {e.entry_id for e in pending}
        assert entry.entry_id not in ids

    def test_pending_entry_survives_restart(self):
        wal1  = self._new_wal()
        rec   = _make_audit_record()
        entry = wal1.begin_transaction(rec.decision_id, rec)
        wal1.mark_side_effect(entry.entry_id, "audit_saved", success=True)
        # NOT committed — simulates crash

        wal2    = self._new_wal()
        pending = wal2.get_pending_entries()
        ids     = {e.entry_id for e in pending}
        assert entry.entry_id in ids

    def test_partial_side_effects_preserved_on_restart(self):
        wal1  = self._new_wal()
        rec   = _make_audit_record()
        entry = wal1.begin_transaction(rec.decision_id, rec)
        wal1.mark_side_effect(entry.entry_id, "audit_saved",      success=True)
        wal1.mark_side_effect(entry.entry_id, "workflow_created",  success=False,
                              error_msg="timeout")
        # Crash here — not committed

        wal2    = self._new_wal()
        pending = wal2.get_pending_entries()
        match   = next(e for e in pending if e.entry_id == entry.entry_id)
        assert match.side_effects["audit_saved"]["success"] is True
        assert match.side_effects["workflow_created"]["success"] is False

    def test_stats_returns_sqlite_backend(self):
        wal   = self._new_wal()
        stats = wal.stats()
        assert stats["backend"] == "sqlite3"

    def test_checkpoint_created_at_interval(self):
        from glassbox.governance.write_ahead_log import WriteAheadLog
        import sqlite3
        wal = WriteAheadLog(db_path=self._db, checkpoint_interval=3)
        for _ in range(3):
            rec   = _make_audit_record()
            entry = wal.begin_transaction(rec.decision_id, rec)
            wal.commit(entry.entry_id)
        with sqlite3.connect(self._db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM wal_checkpoints"
            ).fetchone()[0]
        assert count >= 1


# ===========================================================================
# 2. Multi-Tenancy
# ===========================================================================

class TestTenantRegistry:

    def _registry(self, **kw):
        from glassbox.governance.multitenancy import TenantRegistry
        return TenantRegistry(**kw)

    def test_get_creates_tenant_on_first_access(self):
        reg   = self._registry()
        comps = reg.get("tenant-01")
        assert comps is not None
        assert "tenant-01" in reg.list_tenants()

    def test_get_returns_same_instance_on_second_access(self):
        reg    = self._registry()
        comps1 = reg.get("tenant-01")
        comps2 = reg.get("tenant-01")
        assert comps1 is comps2

    def test_two_tenants_have_distinct_components(self):
        reg = self._registry()
        a   = reg.get("tenant-aa")
        b   = reg.get("tenant-bb")
        assert a.policy_engine    is not b.policy_engine
        assert a.velocity_breaker is not b.velocity_breaker
        assert a.anomaly_detector is not b.anomaly_detector
        assert a.audit_logger     is not b.audit_logger

    def test_quota_raises_on_overflow(self):
        reg = self._registry(max_tenants=2)
        reg.get("t-01")
        reg.get("t-02")
        with pytest.raises(RuntimeError, match="Tenant quota"):
            reg.get("t-03")

    def test_evict_inactive_frees_slot(self):
        reg = self._registry(max_tenants=2)
        reg.get("t-01")
        # Force last-access time into the past
        reg._tenant_last_access["t-01"] = time.time() - 7200
        evicted = reg.evict_inactive(inactive_after_sec=3600)
        assert evicted == 1
        # Slot is now free
        reg.get("t-02")
        reg.get("t-03")  # would have raised before eviction

    def test_evict_active_tenant_not_removed(self):
        reg = self._registry()
        reg.get("active-01")
        evicted = reg.evict_inactive(inactive_after_sec=3600)
        assert evicted == 0
        assert "active-01" in reg.list_tenants()

    # ── Invalid tenant ID patterns (CRITICAL-4 coverage) ─────────────────────

    @pytest.mark.parametrize("bad_id", [
        "",               # empty
        "ab",             # too short (< 3 chars, default pattern is 3-64)
        "a" * 200,        # exceeds 128-char hard cap
        "tenant\x00id",   # null byte
        "../traversal",   # path traversal
        "tenant/sub",     # path separator
        "tenant\\sub",    # Windows separator
    ])
    def test_invalid_tenant_id_raises(self, bad_id):
        reg = self._registry()
        with pytest.raises((ValueError, Exception)):
            reg.get(bad_id)

    def test_status_returns_utilisation(self):
        reg = self._registry(max_tenants=10)
        reg.get("t-01")
        reg.get("t-02")
        status = reg.status()
        assert status["total_tenants"] == 2
        assert status["max_tenants"]   == 10
        assert status["utilization_pct"] == 20.0

    def test_remove_tenant_frees_slot(self):
        reg = self._registry(max_tenants=2)
        reg.get("t-01")
        reg.get("t-02")
        reg.remove_tenant("t-01")
        reg.get("t-03")  # should succeed

    def test_concurrent_get_same_tenant_returns_one_instance(self):
        """Race between two threads creating the same tenant must not create two."""
        reg     = self._registry()
        results = []
        lock    = threading.Lock()

        def _get():
            comps = reg.get("shared-tenant")
            with lock:
                results.append(id(comps.policy_engine))

        threads = [threading.Thread(target=_get) for _ in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(set(results)) == 1, "Multiple PolicyEngine instances created"

    def test_context_isolation_validator(self):
        from glassbox.governance.multitenancy import ContextIsolationValidator, TenantRegistry
        reg = TenantRegistry()
        reg.get("org-a")
        reg.get("org-b")
        validator = ContextIsolationValidator(reg)
        report    = validator.check_isolation(["org-a", "org-b"])
        assert report["all_isolated"] is True
        assert len(report["issues"]) == 0


class TestMultiTenantPipeline:

    def _build(self):
        from glassbox.governance.multitenancy import TenantRegistry, MultiTenantPipeline
        from glassbox.governance.pipeline import GovernancePipeline
        registry = TenantRegistry()
        pipeline = MultiTenantPipeline(
            registry=registry,
            base_pipeline_fn=lambda comps: GovernancePipeline(
                policy_engine=comps.policy_engine,
                velocity_breaker=comps.velocity_breaker,
                anomaly_detector=comps.anomaly_detector,
                audit_logger=comps.audit_logger,
            ),
        )
        return pipeline

    def _request(self, agent_id: str = "agent-1"):
        from glassbox.governance.models import DecisionRequest, DecisionContext, DecisionType
        return DecisionRequest(
            agent_id=agent_id,
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": 100.0, "supplier_id": "SUPP-001"},
            context=DecisionContext(environment="test"),
        )

    def test_decisions_routed_to_correct_tenant(self):
        from glassbox.governance.models import FinalStatus
        pipeline = self._build()
        r1 = pipeline.process(self._request("agent-a"), tenant_id="tenant-001")
        r2 = pipeline.process(self._request("agent-b"), tenant_id="tenant-002")
        assert r1.final_status in list(FinalStatus)
        assert r2.final_status in list(FinalStatus)

    def test_velocity_limits_not_shared_across_tenants(self):
        """Exhausting tenant-001's velocity does NOT affect tenant-002."""
        from glassbox.governance.multitenancy import TenantRegistry, MultiTenantPipeline
        from glassbox.governance.pipeline import GovernancePipeline
        from glassbox.governance.velocity_breaker import VelocityBreaker

        registry = TenantRegistry()

        def _build_pipeline(comps):
            # Very low limit so we can trigger it quickly
            vb = VelocityBreaker(max_decisions=2, window_seconds=60)
            return GovernancePipeline(
                policy_engine=comps.policy_engine,
                velocity_breaker=vb,
                anomaly_detector=comps.anomaly_detector,
                audit_logger=comps.audit_logger,
            )

        pipeline = MultiTenantPipeline(registry=registry, base_pipeline_fn=_build_pipeline)
        req = self._request("shared-agent")

        # Exhaust tenant-001's velocity
        for _ in range(3):
            pipeline.process(req, tenant_id="ten-001")

        # tenant-002 should still pass (its own velocity counter is untouched)
        r = pipeline.process(req, tenant_id="ten-002")
        assert not r.circuit_breaker_triggered

    def test_original_request_not_mutated(self):
        """process() must not mutate the caller's request object."""
        from glassbox.governance.models import DecisionRequest, DecisionType
        pipeline = self._build()
        req      = self._request()
        original_meta = dict(req.context.metadata) if req.context else {}
        pipeline.process(req, tenant_id="ten-001")
        if req.context:
            assert req.context.metadata == original_meta


# ===========================================================================
# 3. Advanced Audit (TamperEvidentAuditLogger)
# ===========================================================================

class TestTamperEvidentAuditLogger:

    def _logger(self, enable_hash_chain: bool = True):
        from glassbox.governance.advanced_audit import TamperEvidentAuditLogger
        return TamperEvidentAuditLogger(
            db_path=":memory:",
            enable_hash_chain=enable_hash_chain,
            crypto_manager=None,
        )

    # ── Basic operations ──────────────────────────────────────────────────────

    def test_log_action_returns_record_with_hash(self):
        logger = self._logger()
        rec    = logger.log_action("user1", "create", "policy", "pol-001", "success")
        assert rec.id    >= 1
        assert rec.record_hash
        assert len(rec.record_hash) == 64  # SHA-256 hex

    def test_hash_is_deterministic(self):
        from glassbox.governance.advanced_audit import AuditRecord
        from datetime import datetime, timezone
        rec = AuditRecord(
            id=1,
            timestamp=datetime(2024, 1, 1, tzinfo=timezone.utc),
            user_id="u1", action="a", resource_type="rt",
            resource_id="ri", result="success",
            previous_hash="0" * 64,
        )
        h1 = rec.compute_hash()
        h2 = rec.compute_hash()
        assert h1 == h2

    def test_empty_logger_verifies_clean(self):
        logger = self._logger()
        assert logger.verify_hash_chain() is True

    def test_single_record_chain_valid(self):
        logger = self._logger()
        logger.log_action("u1", "login",  "session", "s1", "success")
        assert logger.verify_hash_chain() is True

    def test_multiple_records_chain_valid(self):
        logger = self._logger()
        for i in range(5):
            logger.log_action("u1", f"action_{i}", "resource", f"r{i}", "success")
        assert logger.verify_hash_chain() is True

    # ── Genesis sentinel ──────────────────────────────────────────────────────

    def test_first_record_references_genesis_sentinel(self):
        from glassbox.governance.advanced_audit import GENESIS_SENTINEL
        logger = self._logger()
        rec    = logger.log_action("u1", "act", "res", "rid", "success")
        assert rec.previous_hash == GENESIS_SENTINEL

    def test_second_record_references_first_hash(self):
        logger = self._logger()
        r1 = logger.log_action("u1", "a1", "res", "r1", "success")
        r2 = logger.log_action("u1", "a2", "res", "r2", "success")
        assert r2.previous_hash == r1.record_hash

    # ── Tamper detection ─────────────────────────────────────────────────────

    def test_tampered_hash_detected(self):
        """Directly modifying a stored hash must break chain verification."""
        logger = self._logger()
        logger.log_action("u1", "a1", "res", "r1", "success")
        logger.log_action("u1", "a2", "res", "r2", "success")

        # Corrupt the stored hash of record 1 directly
        with logger._get_connection() as conn:
            conn.execute(
                "UPDATE audit_records SET record_hash = ? WHERE id = 1",
                ("deadbeef" * 8,),  # 64-char fake hash
            )
            conn.commit()

        assert logger.verify_hash_chain() is False

    def test_tampered_content_detected(self):
        """Changing the action field on a stored record must break the chain."""
        logger = self._logger()
        logger.log_action("u1", "login", "session", "s1", "success")

        with logger._get_connection() as conn:
            conn.execute(
                "UPDATE audit_records SET action = 'INJECTED' WHERE id = 1",
            )
            conn.commit()

        assert logger.verify_hash_chain() is False

    def test_inserted_record_breaks_chain(self):
        """An attacker inserting a record in the middle breaks subsequent links."""
        logger = self._logger()
        r1 = logger.log_action("u1", "a1", "res", "r1", "success")
        r2 = logger.log_action("u1", "a2", "res", "r2", "success")

        # Insert a fake record between r1 and r2 (id=1.5 is not possible, so
        # instead corrupt r2's previous_hash to point to a fake hash)
        with logger._get_connection() as conn:
            conn.execute(
                "UPDATE audit_records SET previous_hash = ? WHERE id = ?",
                ("0" * 64, r2.id),
            )
            conn.commit()

        assert logger.verify_hash_chain() is False

    # ── Search ────────────────────────────────────────────────────────────────

    def test_search_by_user_id(self):
        logger = self._logger()
        logger.log_action("alice", "create", "policy", "p1", "success")
        logger.log_action("bob",   "update", "policy", "p2", "success")
        results = logger.search(user_id="alice")
        assert len(results) == 1
        assert results[0].user_id == "alice"

    def test_search_by_wildcard_action(self):
        logger = self._logger()
        logger.log_action("u1", "policy_create", "policy", "p1", "success")
        logger.log_action("u1", "policy_update", "policy", "p2", "success")
        logger.log_action("u1", "user_login",    "session","s1", "success")
        results = logger.search(action="policy_*")
        assert len(results) == 2

    def test_search_returns_empty_on_no_match(self):
        logger  = self._logger()
        results = logger.search(user_id="ghost")
        assert results == []

    # ── Stats and export ──────────────────────────────────────────────────────

    def test_get_stats(self):
        logger = self._logger()
        logger.log_action("u1", "a1", "res", "r1", "success")
        logger.log_action("u1", "a2", "res", "r2", "failure")
        stats  = logger.get_stats()
        assert stats["total_records"] == 2
        assert stats["hash_chain_enabled"] is True

    def test_export_json(self):
        import json
        logger = self._logger()
        logger.log_action("u1", "a1", "res", "r1", "success")
        exported = logger.export_records(format="json")
        data     = json.loads(exported)
        assert len(data) == 1
        assert data[0]["action"] == "a1"

    def test_export_csv_contains_header(self):
        logger = self._logger()
        logger.log_action("u1", "a1", "res", "r1", "success")
        csv_out = logger.export_records(format="csv")
        assert "user_id" in csv_out
        assert "action"  in csv_out

    def test_purge_old_records(self):
        """Records older than retention period are deleted."""
        from datetime import timedelta
        logger = self._logger()
        logger.log_action("u1", "a1", "res", "r1", "success")

        # Force timestamp into the distant past
        with logger._get_connection() as conn:
            conn.execute(
                "UPDATE audit_records SET timestamp = '2000-01-01T00:00:00+00:00'"
            )
            conn.commit()

        deleted = logger.purge_old_records(days=1)
        assert deleted == 1
        stats = logger.get_stats()
        assert stats["total_records"] == 0

    # ── Hash-chain disabled mode ───────────────────────────────────────────────

    def test_no_chain_verify_returns_true(self):
        logger = self._logger(enable_hash_chain=False)
        logger.log_action("u1", "a1", "res", "r1", "success")
        assert logger.verify_hash_chain() is True

    def test_no_chain_previous_hash_is_none(self):
        logger = self._logger(enable_hash_chain=False)
        rec    = logger.log_action("u1", "a1", "res", "r1", "success")
        assert rec.previous_hash is None

    # ── Thread-safety ─────────────────────────────────────────────────────────

    def test_concurrent_log_action_no_corruption(self):
        logger  = self._logger()
        errors  = []
        lock    = threading.Lock()

        def _log(i):
            try:
                logger.log_action(f"user{i}", "action", "res", f"r{i}", "success")
            except Exception as exc:
                with lock:
                    errors.append(str(exc))

        threads = [threading.Thread(target=_log, args=(i,)) for i in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert errors == [], f"Concurrent log_action errors: {errors}"
        stats = logger.get_stats()
        assert stats["total_records"] == 50


# ===========================================================================
# 4. Policy Engine — O5, O6, O7 regressions
# ===========================================================================

class TestPolicyEngineO5ExceptionHandling:
    """Policy exceptions must be sanitized — no internal detail in violation message."""

    def _engine_with_bad_policy(self):
        from glassbox.governance.policy_engine import Policy, PolicyEngine
        from glassbox.governance.models import DecisionType, PolicyEvaluation

        def _exploding_rule(payload, ctx):
            raise RuntimeError("internal DB connection string: postgres://user:pass@host/db")

        policy = Policy(
            policy_id="BAD-001",
            policy_name="Exploding Policy",
            decision_types=[DecisionType.CUSTOM],
            rule=_exploding_rule,
        )
        engine = PolicyEngine(policies=[policy])
        return engine

    def test_exception_does_not_leak_internal_detail(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        engine  = self._engine_with_bad_policy()
        ctx     = DecisionContext(environment="test")
        result  = engine.evaluate(DecisionType.CUSTOM, {"key": "val"}, ctx)
        # Must fail (fail-closed)
        assert result.passed is False
        # Internal detail must NOT be in the violation message
        for v in result.violations:
            assert "postgres://" not in v
            assert "DB connection string" not in v
        # Sanitized message present
        assert any("audit log" in v.lower() for v in result.violations)

    def test_exception_result_still_fails_closed(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        engine = self._engine_with_bad_policy()
        ctx    = DecisionContext(environment="test")
        result = engine.evaluate(DecisionType.CUSTOM, {}, ctx)
        assert result.passed is False


class TestPolicyEngineO6DynamicSanctions:
    """Sanctions list must be runtime-configurable via PolicyParameterStore."""

    def test_default_sanctioned_country_blocked(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        from glassbox.governance.policy_engine import PolicyEngine
        engine = PolicyEngine()
        ctx    = DecisionContext(environment="test")
        result = engine.evaluate(
            DecisionType.PROCUREMENT,
            {"amount": 1000, "supplier_id": "SUP-X", "supplier_country": "KP"},
            ctx,
        )
        # KP (North Korea) is in default sanctioned list
        assert result.passed is False

    def test_runtime_overriding_sanctions_list(self):
        """Adding a new country at runtime takes effect on the next evaluation."""
        from glassbox.governance.models import DecisionType, DecisionContext
        from glassbox.governance.policy_engine import PolicyEngine
        from glassbox.governance.policy_parameters import _param_store

        # Add a test-only country code
        _param_store.set("PROC-006", "sanctioned_countries", ["KP", "IR", "XX"], updated_by="test")
        try:
            engine = PolicyEngine()
            ctx    = DecisionContext(environment="test")
            result = engine.evaluate(
                DecisionType.PROCUREMENT,
                {"amount": 100, "supplier_id": "S1", "supplier_country": "XX"},
                ctx,
            )
            assert result.passed is False
        finally:
            # Clean up — remove the override so other tests aren't affected
            _param_store.set("PROC-006", "sanctioned_countries", None, updated_by="test")


class TestPolicyEngineO7ClinicalTrading:
    """CLINICAL and TRADING must be covered by SECURITY-001 and AI-001."""

    def _engine(self):
        from glassbox.governance.policy_engine import PolicyEngine
        return PolicyEngine()

    def test_security001_fires_on_clinical_production_override(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        engine = self._engine()
        ctx    = DecisionContext(environment="production", user_override=True)
        result = engine.evaluate(
            DecisionType.CLINICAL,
            {"drug_name": "morphine", "dose_mg": 10},
            ctx,
        )
        assert result.passed is False
        assert any("SECURITY-001" in v for v in result.violations)

    def test_ai001_fires_on_trading_low_confidence(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        engine = self._engine()
        ctx    = DecisionContext(environment="test", confidence=0.10)  # < 0.30
        result = engine.evaluate(
            DecisionType.TRADING,
            {"symbol": "AAPL", "quantity": 100},
            ctx,
        )
        assert result.passed is False
        assert any("AI-001" in v for v in result.violations)

    def test_security001_fires_on_trading_production_override(self):
        from glassbox.governance.models import DecisionType, DecisionContext
        engine = self._engine()
        ctx    = DecisionContext(environment="production", user_override=True)
        result = engine.evaluate(
            DecisionType.TRADING,
            {"symbol": "TSLA", "notional": 50000},
            ctx,
        )
        assert result.passed is False
        assert any("SECURITY-001" in v for v in result.violations)


# ===========================================================================
# 5. WorkflowEngine O8 — Idempotency
# ===========================================================================

class TestWorkflowEngineIdempotency:

    def _engine(self):
        from glassbox.workflow.workflow_engine import WorkflowEngine
        return WorkflowEngine(monitor_sla=False)

    def test_duplicate_create_returns_same_workflow(self):
        engine      = self._engine()
        decision_id = str(uuid.uuid4())
        wf1 = engine.create_from_decision(
            decision_id=decision_id, agent_id="a1",
            decision_type="financial", risk_score=55.0, violations=[],
        )
        wf2 = engine.create_from_decision(
            decision_id=decision_id, agent_id="a1",
            decision_type="financial", risk_score=55.0, violations=[],
        )
        assert wf1.workflow_id == wf2.workflow_id

    def test_no_duplicate_in_pending_list(self):
        engine      = self._engine()
        decision_id = str(uuid.uuid4())
        engine.create_from_decision(
            decision_id=decision_id, agent_id="a1",
            decision_type="financial", risk_score=55.0, violations=[],
        )
        engine.create_from_decision(
            decision_id=decision_id, agent_id="a1",
            decision_type="financial", risk_score=55.0, violations=[],
        )
        pending = engine.list_pending()
        matches = [w for w in pending if w.decision_id == decision_id]
        assert len(matches) == 1

    def test_concurrent_creates_idempotent(self):
        """50 threads calling create_from_decision with the same decision_id."""
        engine      = self._engine()
        decision_id = str(uuid.uuid4())
        workflow_ids = []
        lock         = threading.Lock()

        def _create():
            wf = engine.create_from_decision(
                decision_id=decision_id, agent_id="a1",
                decision_type="procurement", risk_score=45.0, violations=[],
            )
            with lock:
                workflow_ids.append(wf.workflow_id)

        threads = [threading.Thread(target=_create) for _ in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()

        assert len(set(workflow_ids)) == 1, "Multiple workflow IDs for same decision"


# ===========================================================================
# 6. Stage Latency — O2 regression
# ===========================================================================

class TestStageLantencyTracking:

    def test_pipeline_health_includes_stage_latency(self):
        from glassbox.governance.pipeline import GovernancePipeline
        from glassbox.governance.models import DecisionRequest, DecisionType
        pipeline = GovernancePipeline(environment="test", trace_enabled=False)
        req = DecisionRequest(
            agent_id="test-agent",
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": 100.0, "supplier_id": "SUPP-01"},
        )
        pipeline.process(req)
        h = pipeline.health()
        assert "stage_latency_p50_ms" in h
        assert "stage_latency_p99_ms" in h
        # After at least one request, schema validation should be populated
        p50 = h["stage_latency_p50_ms"]
        assert isinstance(p50, dict)
        assert len(p50) > 0

    def test_stage_latency_stats_returns_percentiles(self):
        from glassbox.governance.pipeline import GovernancePipeline
        from glassbox.governance.models import DecisionRequest, DecisionType
        pipeline = GovernancePipeline(environment="test")
        req = DecisionRequest(
            agent_id="perf-agent",
            decision_type=DecisionType.FINANCIAL,
            payload={"amount": 500.0, "destination_account": "ACC-1"},
        )
        for _ in range(10):
            pipeline.process(req)
        stats = pipeline.stage_latency_stats()
        assert len(stats) > 0
        for stage, vals in stats.items():
            assert "p50_ms" in vals
            assert "p99_ms" in vals
            assert vals["p99_ms"] >= vals["p50_ms"]
            assert vals["samples"] >= 10

    def test_stage_registry_latency_populated(self):
        from glassbox.governance.stage_registry import StageRegistry, StageExecutionResult
        registry = StageRegistry()
        for ms in [1.0, 2.0, 3.0, 4.0, 5.0]:
            registry.record_execution(
                "custom_stage",
                StageExecutionResult("custom_stage", passed=True, latency_ms=ms),
            )
        latency = registry.get_stage_latency_stats()
        assert "custom_stage" in latency
        assert latency["custom_stage"]["p50_ms"] > 0
        assert latency["custom_stage"]["samples"] == 5
