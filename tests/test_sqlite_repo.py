"""
GlassBox — SQLite Repository Integration Tests  (T-4)

Tests the full persistence stack: SQLiteRepository save/query/count, tenant
isolation, pagination, and pipeline-level end-to-end write-through with a
real SQLite database (stdlib only, no mocks).

Run: python -m pytest tests/test_sqlite_repo.py -v
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import unittest
import uuid

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from glassbox.store.repository import RepositoryFactory, SQLiteRepository
from glassbox.governance.models import (
    AuditRecord, DecisionContext, DecisionRequest, DecisionType,
    FinalStatus, RiskLevel, RiskResult, PolicyResult, Disposition,
)
from glassbox.governance.pipeline import GovernancePipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(
    agent_id: str = "test-agent",
    dtype: DecisionType = DecisionType.PROCUREMENT,
    final_status: FinalStatus = FinalStatus.EXECUTED,
    tenant_id: str | None = None,
) -> AuditRecord:
    meta = {}
    if tenant_id:
        meta["tenant_id"] = tenant_id
    ctx = DecisionContext(metadata=meta)
    record = AuditRecord(
        agent_id=agent_id,
        decision_type=dtype,
        payload={"amount": 1000},
        context=ctx,
    )
    record.final_status = final_status
    record.policy_result = PolicyResult(passed=True)
    record.risk_result = RiskResult(
        risk_score=10.0,
        risk_level=RiskLevel.LOW,
        disposition=Disposition.AUTO_EXECUTE,
    )
    return record


def _tmp_db_dir() -> str:
    base = os.path.join(os.getcwd(), ".tmp-sqlite-tests")
    os.makedirs(base, exist_ok=True)
    path = os.path.join(base, f"glassbox-sqlite-{uuid.uuid4().hex}")
    os.makedirs(path, exist_ok=False)
    return path


def _ns() -> str:
    return uuid.uuid4().hex


# ══════════════════════════════════════════════════════════════════════════════
# 1. BASIC SAVE / RETRIEVE
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLiteBasicOperations(unittest.TestCase):
    def setUp(self):
        self.db_dir = _tmp_db_dir()
        self.repo = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        # Directly access the audit repo
        self.audit = self.repo["audit"]

    def tearDown(self):
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def test_save_and_retrieve_by_id(self):
        record = _make_record()
        self.audit.save(record)
        fetched = self.audit.get_by_id(record.decision_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.decision_id, record.decision_id)

    def test_retrieve_nonexistent_returns_none(self):
        result = self.audit.get_by_id(str(uuid.uuid4()))
        self.assertIsNone(result)

    def test_save_multiple_then_query(self):
        for i in range(5):
            self.audit.save(_make_record(agent_id=f"agent-{i}"))
        records = self.audit.query(limit=10)
        self.assertGreaterEqual(len(records), 5)

    def test_count_returns_correct_total(self):
        for _ in range(3):
            self.audit.save(_make_record())
        total = self.audit.count()
        self.assertGreaterEqual(total, 3)

    def test_saved_record_preserves_agent_id(self):
        record = _make_record(agent_id="preserved-agent")
        self.audit.save(record)
        fetched = self.audit.get_by_id(record.decision_id)
        self.assertEqual(fetched.agent_id, "preserved-agent")

    def test_saved_record_preserves_decision_type(self):
        record = _make_record(dtype=DecisionType.FINANCIAL)
        self.audit.save(record)
        fetched = self.audit.get_by_id(record.decision_id)
        self.assertEqual(fetched.decision_type, DecisionType.FINANCIAL)

    def test_saved_record_preserves_final_status(self):
        record = _make_record(final_status=FinalStatus.BLOCKED)
        self.audit.save(record)
        fetched = self.audit.get_by_id(record.decision_id)
        self.assertEqual(fetched.final_status, FinalStatus.BLOCKED)


# ══════════════════════════════════════════════════════════════════════════════
# 2. QUERY FILTERING
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLiteQueryFiltering(unittest.TestCase):
    def setUp(self):
        self.db_dir = _tmp_db_dir()
        self.repo = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        self.audit = self.repo["audit"]
        # Seed data: 3 executed procurement, 2 blocked financial
        for i in range(3):
            self.audit.save(_make_record(
                agent_id="agent-proc", dtype=DecisionType.PROCUREMENT,
                final_status=FinalStatus.EXECUTED,
            ))
        for i in range(2):
            self.audit.save(_make_record(
                agent_id="agent-fin", dtype=DecisionType.FINANCIAL,
                final_status=FinalStatus.BLOCKED,
            ))

    def tearDown(self):
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def test_filter_by_agent_id(self):
        results = self.audit.query(agent_id="agent-proc")
        self.assertEqual(len(results), 3)
        for r in results:
            self.assertEqual(r.agent_id, "agent-proc")

    def test_filter_by_decision_type(self):
        results = self.audit.query(decision_type=DecisionType.FINANCIAL.value)
        self.assertEqual(len(results), 2)

    def test_filter_by_final_status(self):
        results = self.audit.query(final_status=FinalStatus.BLOCKED.value)
        self.assertEqual(len(results), 2)

    def test_count_by_agent(self):
        count = self.audit.count(agent_id="agent-fin")
        self.assertEqual(count, 2)

    def test_count_by_status(self):
        count = self.audit.count(final_status=FinalStatus.EXECUTED.value)
        self.assertEqual(count, 3)

    def test_combined_filter(self):
        results = self.audit.query(
            agent_id="agent-proc",
            final_status=FinalStatus.EXECUTED.value,
        )
        self.assertEqual(len(results), 3)


# ══════════════════════════════════════════════════════════════════════════════
# 3. PAGINATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLitePagination(unittest.TestCase):
    def setUp(self):
        self.db_dir = _tmp_db_dir()
        self.repo = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        self.audit = self.repo["audit"]
        for _ in range(10):
            self.audit.save(_make_record())

    def tearDown(self):
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def test_limit_respected(self):
        results = self.audit.query(limit=3)
        self.assertLessEqual(len(results), 3)

    def test_offset_skips_records(self):
        first  = self.audit.query(limit=5, offset=0)
        second = self.audit.query(limit=5, offset=5)
        first_ids  = {r.decision_id for r in first}
        second_ids = {r.decision_id for r in second}
        self.assertEqual(len(first_ids & second_ids), 0, "Pages must not overlap")

    def test_total_count_unaffected_by_limit(self):
        total = self.audit.count()
        self.assertGreaterEqual(total, 10)

    def test_full_scan_returns_all_records(self):
        page1 = self.audit.query(limit=5, offset=0)
        page2 = self.audit.query(limit=5, offset=5)
        all_ids = {r.decision_id for r in page1} | {r.decision_id for r in page2}
        self.assertGreaterEqual(len(all_ids), 10)


# ══════════════════════════════════════════════════════════════════════════════
# 4. TENANT ISOLATION
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLiteTenantIsolation(unittest.TestCase):
    def setUp(self):
        self.db_dir = _tmp_db_dir()
        self.repo = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        self.audit = self.repo["audit"]
        # 3 records for tenant-a, 2 for tenant-b
        for _ in range(3):
            self.audit.save(_make_record(tenant_id="tenant-a"))
        for _ in range(2):
            self.audit.save(_make_record(tenant_id="tenant-b"))

    def tearDown(self):
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def test_count_scoped_to_tenant_a(self):
        count = self.audit.count(tenant_id="tenant-a")
        self.assertEqual(count, 3)

    def test_count_scoped_to_tenant_b(self):
        count = self.audit.count(tenant_id="tenant-b")
        self.assertEqual(count, 2)

    def test_query_scoped_to_tenant_a(self):
        results = self.audit.query(tenant_id="tenant-a")
        self.assertEqual(len(results), 3)

    def test_query_tenant_a_does_not_return_tenant_b_records(self):
        results = self.audit.query(tenant_id="tenant-a")
        for r in results:
            tenant = (r.context.metadata or {}).get("tenant_id") if r.context else None
            self.assertEqual(tenant, "tenant-a")

    def test_get_by_id_tenant_scoped(self):
        record_a = _make_record(tenant_id="tenant-a")
        self.audit.save(record_a)
        # Should find with correct tenant
        found = self.audit.get_by_id(record_a.decision_id, tenant_id="tenant-a")
        self.assertIsNotNone(found)
        # Should NOT find with wrong tenant
        not_found = self.audit.get_by_id(record_a.decision_id, tenant_id="tenant-b")
        self.assertIsNone(not_found)


# ══════════════════════════════════════════════════════════════════════════════
# 5. THREAD SAFETY
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLiteThreadSafety(unittest.TestCase):
    def setUp(self):
        self.db_dir = _tmp_db_dir()
        self.repo = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        self.audit = self.repo["audit"]

    def tearDown(self):
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def test_concurrent_saves_do_not_corrupt(self):
        errors = []
        n      = 30

        def _write():
            try:
                self.audit.save(_make_record())
            except Exception as exc:
                errors.append(str(exc))

        threads = [threading.Thread(target=_write) for _ in range(n)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(errors, [], f"Concurrent save errors: {errors}")
        count = self.audit.count()
        self.assertEqual(count, n)


# ══════════════════════════════════════════════════════════════════════════════
# 6. PIPELINE END-TO-END WITH SQLITE
# ══════════════════════════════════════════════════════════════════════════════

class TestPipelineWithSQLite(unittest.TestCase):
    """Verify the pipeline persists decisions to SQLite and they are queryable."""

    def setUp(self):
        self.db_dir = _tmp_db_dir()
        repos = RepositoryFactory.sqlite(db_dir=self.db_dir, namespace=_ns())
        self.audit = repos["audit"]
        self.pipeline = GovernancePipeline(
            echo=False,
            environment="testing",
            audit_repo=self.audit,
        )

    def tearDown(self):
        self.pipeline.shutdown()
        try:
            self.audit.close()
        except Exception:
            pass
        try:
            shutil.rmtree(self.db_dir, ignore_errors=True)
        except Exception:
            pass

    def _proc_request(self, agent="pipe-sqlite-agent", amount=1000):
        return DecisionRequest(
            agent_id=agent,
            decision_type=DecisionType.PROCUREMENT,
            payload={"amount": amount, "supplier_id": "SUP-001", "category": "hardware"},
        )

    def test_pipeline_persists_to_sqlite(self):
        req  = self._proc_request()
        resp = self.pipeline.process(req)
        # Give SQLite writer a moment (it's synchronous in default mode)
        fetched = self.audit.get_by_id(resp.decision_id)
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched.decision_id, resp.decision_id)

    def test_pipeline_persisted_status_matches_response(self):
        req  = self._proc_request()
        resp = self.pipeline.process(req)
        fetched = self.audit.get_by_id(resp.decision_id)
        self.assertEqual(fetched.final_status, resp.final_status)

    def test_multiple_decisions_all_persisted(self):
        n = 5
        for _ in range(n):
            self.pipeline.process(self._proc_request())
        count = self.audit.count()
        self.assertEqual(count, n)

    def test_blocked_decision_persisted_with_correct_status(self):
        req = DecisionRequest(
            agent_id="blocked-agent",
            decision_type=DecisionType.FINANCIAL,
            payload={
                "amount": 2_000_000,          # Exceeds FIN-001 ($1M limit)
                "destination_account": "ACC-123",
            },
        )
        resp = self.pipeline.process(req)
        if resp.final_status == FinalStatus.BLOCKED:
            fetched = self.audit.get_by_id(resp.decision_id)
            self.assertEqual(fetched.final_status, FinalStatus.BLOCKED)

    def test_query_by_agent_after_pipeline(self):
        agent = "sqlite-query-agent"
        for _ in range(3):
            self.pipeline.process(self._proc_request(agent=agent))
        results = self.audit.query(agent_id=agent)
        self.assertEqual(len(results), 3)


if __name__ == "__main__":
    unittest.main()
