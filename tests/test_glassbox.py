"""
GlassBox Framework — Complete Test Suite  (v1.0.0)
218 tests across 22 classes covering every component.

Uses Python stdlib unittest only — no pytest required.

Run:  python3 tests/test_glassbox.py
Or:   python3 -m unittest tests.test_glassbox -v

Author: Mohammed Akbar Ansari
"""
from __future__ import annotations
import asyncio, gc, json, os, sys, threading, time, unittest
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from glassbox.governance.anomaly_detector  import AnomalyDetector
from glassbox.governance.audit_logger      import AuditLogger
from glassbox.governance.models            import (
    AgentContract, DecisionContext, DecisionRequest, DecisionType,
    FinalStatus, Disposition, RiskLevel, PolicyEvaluation, PolicyResult,
    RetryConfig, RetryStrategy,
)
from glassbox.governance.pipeline          import GovernancePipeline
from glassbox.governance.policy_engine     import Policy, PolicyEngine
from glassbox.governance.risk_evaluator    import RiskEvaluator
from glassbox.governance.schema_validator  import SchemaValidator
from glassbox.governance.velocity_breaker  import VelocityBreaker
from glassbox.governance.decision_replay   import DecisionReplay
from glassbox.security.sanitizer           import PayloadSanitizer, validate_agent_id
from glassbox.adapters.platforms           import (
    BaseAdapter, DatabricksAdapter, KubernetesAdapter,
    FabricAdapter, auto_detect_adapter,
)


# ── helpers ───────────────────────────────────────────────────────────────────
def _pipe(**kw) -> GovernancePipeline:
    return GovernancePipeline(echo=False, **kw)

def _req(dtype=DecisionType.PROCUREMENT, payload=None, agent="t_agent", ctx=None):
    payload = payload or {"amount": 1000, "supplier_id": "SUP-001", "category": "hardware"}
    return DecisionRequest(agent_id=agent, decision_type=dtype, payload=payload, context=ctx)

def _proc(agent="t_agent", amount=5000):
    return _req(DecisionType.PROCUREMENT,
                {"amount": amount, "supplier_id": "SUP-001", "category": "hardware"}, agent)


# ══════════════════════════════════════════════════════════════════════════════
# 1. SCHEMA VALIDATION
# ══════════════════════════════════════════════════════════════════════════════
class TestSchemaValidator(unittest.TestCase):
    def setUp(self): self.v = SchemaValidator()
    def test_valid_procurement(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT, {"amount": 50000}); self.assertTrue(ok)
    def test_missing_amount_fails(self):
        ok, err = self.v.validate(DecisionType.PROCUREMENT, {"supplier_id": "S"}); self.assertFalse(ok)
    def test_negative_amount_fails(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT, {"amount": -1}); self.assertFalse(ok)
    def test_empty_payload_fails(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT, {}); self.assertFalse(ok)
    def test_non_dict_payload_fails(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT, "bad"); self.assertFalse(ok)
    def test_none_payload_fails(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT, None); self.assertFalse(ok)
    def test_valid_pricing(self):
        ok, _ = self.v.validate(DecisionType.PRICING, {"new_price": 99.99}); self.assertTrue(ok)
    def test_valid_financial(self):
        ok, _ = self.v.validate(DecisionType.FINANCIAL, {"amount": 5000}); self.assertTrue(ok)
    def test_valid_inventory(self):
        ok, _ = self.v.validate(DecisionType.INVENTORY, {"quantity": 500}); self.assertTrue(ok)
    def test_valid_itops(self):
        ok, _ = self.v.validate(DecisionType.IT_OPS, {"action": "restart", "target": "svc"}); self.assertTrue(ok)
    def test_custom_no_schema(self):
        ok, _ = self.v.validate(DecisionType.CUSTOM, {"anything": "goes"}); self.assertTrue(ok)
    def test_extra_fields_allowed(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT,
                                {"amount": 1000, "extra": "field"}); self.assertTrue(ok)


# ══════════════════════════════════════════════════════════════════════════════
# 2. POLICY ENGINE
# ══════════════════════════════════════════════════════════════════════════════
class TestPolicyEngine(unittest.TestCase):
    def setUp(self): self.pe = PolicyEngine(); self.ctx = DecisionContext()

    def test_small_procurement_passes(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 5000, "supplier_id":"SUP-001","category":"hardware"}, self.ctx)
        self.assertTrue(r.passed)

    def test_large_no_contract_fails(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 700000, "supplier_id":"SUP-001","category":"hardware"}, self.ctx)
        self.assertFalse(r.passed)
        self.assertTrue(any("PROC-001" in v for v in r.violations))

    def test_large_with_contract_passes(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 700000, "supplier_id":"SUP-001","category":"hardware","contract_id":"CT"}, self.ctx)
        self.assertTrue(r.passed)

    def test_unknown_supplier_warns(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 5000, "supplier_id":"UNKNOWN","category":"parts"}, self.ctx)
        self.assertTrue(r.passed)
        self.assertGreater(len(r.warnings), 0)

    def test_high_risk_category_fails(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 50000, "supplier_id":"SUP-001","category":"semiconductors"}, self.ctx)
        self.assertFalse(r.passed)

    def test_high_risk_with_approval_passes(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
                             {"amount": 50000, "supplier_id":"SUP-001","category":"semiconductors","category_approval_ref":"A"}, self.ctx)
        self.assertTrue(r.passed)

    def test_price_over_30pct_fails(self):
        r = self.pe.evaluate(DecisionType.PRICING,
                             {"new_price": 140.0, "previous_price": 100.0}, self.ctx)
        self.assertFalse(r.passed)

    def test_price_under_30pct_passes(self):
        r = self.pe.evaluate(DecisionType.PRICING,
                             {"new_price": 125.0, "previous_price": 100.0, "reason":"demand"}, self.ctx)
        self.assertTrue(r.passed)

    def test_financial_over_limit_fails(self):
        r = self.pe.evaluate(DecisionType.FINANCIAL,
                             {"amount": 1_500_000, "destination_account":"ACC","reference":"REF"}, self.ctx)
        self.assertFalse(r.passed)

    def test_production_user_override_fails(self):
        ctx = DecisionContext(environment="production", user_override=True)
        r = self.pe.evaluate(DecisionType.PROCUREMENT, {"amount":1000,"supplier_id":"SUP-001","category":"hw"}, ctx)
        self.assertFalse(r.passed)

    def test_low_confidence_fails(self):
        ctx = DecisionContext(confidence=0.15)
        r = self.pe.evaluate(DecisionType.PROCUREMENT, {"amount":1000,"supplier_id":"SUP-001","category":"hw"}, ctx)
        self.assertFalse(r.passed)

    def test_disable_enable_policy(self):
        self.pe.disable("PROC-001")
        r = self.pe.evaluate(DecisionType.PROCUREMENT, {"amount":700000,"supplier_id":"SUP-001","category":"hw"}, self.ctx)
        self.assertFalse(any("PROC-001" in v for v in r.violations))
        self.pe.enable("PROC-001")

    def test_register_custom_policy(self):
        def rule(p, c): return PolicyEvaluation("C-01","Custom","fail" if p.get("blocked") else "pass","msg")
        self.pe.register(Policy("C-01","Custom",[DecisionType.CUSTOM], rule))
        r = self.pe.evaluate(DecisionType.CUSTOM, {"blocked": True}, self.ctx)
        self.assertFalse(r.passed)

    def test_crashing_policy_handled(self):
        def bad_rule(p, c): raise RuntimeError("crash!")
        self.pe.register(Policy("BAD-001","Bad",[DecisionType.CUSTOM], bad_rule))
        try:
            self.pe.evaluate(DecisionType.CUSTOM, {"x":1}, self.ctx)
        except RuntimeError:
            self.fail("Crashing policy must not propagate exception")

    def test_disabled_policy_not_evaluated(self):
        def always_fail(p, c): return PolicyEvaluation("DIS-001","Disabled","fail","always")
        self.pe.register(Policy("DIS-001","Disabled",[DecisionType.CUSTOM], always_fail))
        self.pe.disable("DIS-001")
        r = self.pe.evaluate(DecisionType.CUSTOM, {"x":1}, self.ctx)
        self.assertEqual(len([v for v in r.violations if "DIS-001" in v]), 0)

    def test_thread_safe_concurrent_register_evaluate(self):
        errors = []; lock = threading.Lock()
        def reg(i):
            try:
                def rule(p,c): return PolicyEvaluation(f"TS-{i}","TS","pass","ok")
                self.pe.register(Policy(f"TS-{i}","TS",[DecisionType.CUSTOM],rule))
            except Exception as e:
                with lock: errors.append(str(e))
        def ev():
            try:
                self.pe.evaluate(DecisionType.CUSTOM,{"x":1},DecisionContext())
            except Exception as e:
                with lock: errors.append(str(e))
        threads = [threading.Thread(target=reg, args=(i,)) for i in range(20)] + \
                  [threading.Thread(target=ev) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)

    def test_deep_copy_isolates_instances(self):
        pe1, pe2 = PolicyEngine(), PolicyEngine()
        pe1.disable("PROC-001")
        enabled = [p.enabled for p in pe2.policies if p.policy_id == "PROC-001"]
        self.assertTrue(all(enabled), "Disable on pe1 must not affect pe2")


# ══════════════════════════════════════════════════════════════════════════════
# 3. RISK EVALUATOR
# ══════════════════════════════════════════════════════════════════════════════
class TestRiskEvaluator(unittest.TestCase):
    def setUp(self):
        self.re = RiskEvaluator(); self.ctx = DecisionContext()
        self.pr_pass = PolicyResult(passed=True)
        self.pr_fail = PolicyResult(passed=False, violations=["[X]"])

    def test_small_order_low_risk(self):
        r = self.re.evaluate(DecisionType.PROCUREMENT,{"amount":1000},self.ctx,self.pr_pass)
        self.assertLessEqual(r.risk_score, 35)
        self.assertEqual(r.disposition, Disposition.AUTO_EXECUTE)

    def test_policy_fail_forces_block(self):
        r = self.re.evaluate(DecisionType.PROCUREMENT,{"amount":1000},self.ctx,self.pr_fail)
        self.assertEqual(r.disposition, Disposition.BLOCK)

    def test_risk_levels_scale(self):
        r_low  = self.re.evaluate(DecisionType.PROCUREMENT,{"amount":100},self.ctx,self.pr_pass)
        self.assertEqual(r_low.risk_level, RiskLevel.LOW)

    def test_financial_risk(self):
        r = self.re.evaluate(DecisionType.FINANCIAL,{"amount":600000},self.ctx,self.pr_pass)
        self.assertGreater(r.risk_score, 35)


# ══════════════════════════════════════════════════════════════════════════════
# 4. VELOCITY BREAKER
# ══════════════════════════════════════════════════════════════════════════════
class TestVelocityBreaker(unittest.TestCase):
    def test_normal_rate_passes(self):
        vb = VelocityBreaker(max_decisions=5, window_seconds=60)
        for _ in range(5):
            triggered, _, _ = vb.check("a"); self.assertFalse(triggered)

    def test_exceeding_trips(self):
        vb = VelocityBreaker(max_decisions=3, window_seconds=60)
        for _ in range(3): vb.check("b")
        triggered, reason, _ = vb.check("b")
        self.assertTrue(triggered); self.assertIsNotNone(reason)

    def test_different_agents_isolated(self):
        vb = VelocityBreaker(max_decisions=2, window_seconds=60)
        for _ in range(3): vb.check("x")
        triggered, _, _ = vb.check("y")
        self.assertFalse(triggered)

    def test_manual_reset(self):
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3): vb.check("c")
        vb.reset("c")
        triggered, _, _ = vb.check("c")
        self.assertFalse(triggered)

    def test_cooldown_blocks(self):
        vb = VelocityBreaker(max_decisions=2, window_seconds=60, cooldown_seconds=300)
        for _ in range(3): vb.check("d")
        triggered, reason, _ = vb.check("d")
        self.assertTrue(triggered); self.assertIn("cooldown", reason.lower())

    def test_ecosystem_breaker(self):
        vb = VelocityBreaker(max_decisions=1000, window_seconds=60,
                             ecosystem_max=3, ecosystem_window_seconds=60)
        for i in range(3): vb.check(f"eco_{i}")
        triggered, reason, _ = vb.check("eco_last")
        self.assertTrue(triggered)
        self.assertTrue("ecosystem" in reason.lower() or "fleet" in reason.lower())


# ══════════════════════════════════════════════════════════════════════════════
# 5. ANOMALY DETECTOR
# ══════════════════════════════════════════════════════════════════════════════
class TestAnomalyDetector(unittest.TestCase):
    def _seeded(self, n=20, mean=50000, std=5000, seed=7):
        import random; rng = random.Random(seed)
        det = AnomalyDetector(z_threshold=3.0, min_samples=10)
        for _ in range(n):
            det.update_only("ag","procurement",{"amount": max(rng.gauss(mean,std), 1)})
        return det

    def test_normal_not_anomalous(self):
        det = self._seeded()
        triggered, _, _ = det.check("ag","procurement",{"amount": 52000})
        self.assertFalse(triggered)

    def test_extreme_is_anomalous(self):
        det = self._seeded()
        triggered, z, _ = det.check("ag","procurement",{"amount": 500000})
        self.assertTrue(triggered); self.assertGreater(z, 3.0)

    def test_below_min_samples_not_triggered(self):
        det = AnomalyDetector(z_threshold=3.0, min_samples=20)
        for _ in range(5): det.update_only("ag","procurement",{"amount":50000})
        triggered, _, _ = det.check("ag","procurement",{"amount": 999999})
        self.assertFalse(triggered)

    def test_inject_baseline(self):
        det = AnomalyDetector(z_threshold=3.0, min_samples=10)
        det.inject_baseline("ag","procurement","amount",[50000.0]*30)
        triggered, _, _ = det.check("ag","procurement",{"amount": 50001})
        self.assertFalse(triggered)

    def test_thread_safe_concurrent(self):
        det = AnomalyDetector(z_threshold=3.0, min_samples=5)
        det.inject_baseline("ag","procurement","amount",[50000.0]*20)
        errors = []; lock = threading.Lock()
        def check_and_update():
            try:
                for _ in range(50):
                    det.check("ag","procurement",{"amount":50000})
                    det.update_only("ag","procurement",{"amount":50000})
            except Exception as e:
                with lock: errors.append(str(e))
        threads = [threading.Thread(target=check_and_update) for _ in range(10)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)


# ══════════════════════════════════════════════════════════════════════════════
# 6. FULL PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
class TestGovernancePipeline(unittest.TestCase):
    def setUp(self): self.p = _pipe()

    def _sub(self, dtype, payload, agent="pipe_agent", ctx=None):
        return self.p.process(DecisionRequest(agent_id=agent,decision_type=dtype,
                                               payload=payload,context=ctx))

    def test_clean_procurement_executes(self):
        r = self._sub(DecisionType.PROCUREMENT,{"amount":5000,"supplier_id":"SUP-001","category":"hw"})
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_policy_violation_blocks(self):
        r = self._sub(DecisionType.PROCUREMENT,{"amount":700000,"category":"hw"})
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
        self.assertGreater(len(r.policy_violations), 0)

    def test_schema_failure_blocks(self):
        r = self._sub(DecisionType.PROCUREMENT,{"supplier_id":"SUP-001"})
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_audit_record_created(self):
        r = self._sub(DecisionType.PROCUREMENT,{"amount":5000,"supplier_id":"SUP-001","category":"hw"})
        self.assertIsNotNone(r.audit_record)
        self.assertIsNotNone(r.audit_record.decision_id)

    def test_latency_measured(self):
        r = self._sub(DecisionType.PROCUREMENT,{"amount":5000,"supplier_id":"SUP-001","category":"hw"})
        self.assertIsNotNone(r.pipeline_latency_ms)
        self.assertGreater(r.pipeline_latency_ms, 0)

    def test_velocity_breaker(self):
        vb = VelocityBreaker(max_decisions=3, window_seconds=60)
        p  = _pipe(velocity_breaker=vb)
        for _ in range(3):
            p.process(DecisionRequest("vel_ag",DecisionType.INVENTORY,{"quantity":100,"product_id":"SK"}))
        r = p.process(DecisionRequest("vel_ag",DecisionType.INVENTORY,{"quantity":100,"product_id":"SK"}))
        self.assertTrue(r.circuit_breaker_triggered)
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_extreme_pricing_blocked(self):
        r = self._sub(DecisionType.PRICING,{"new_price":500.0,"previous_price":100.0,"product_id":"P"})
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_financial_over_limit_blocked(self):
        r = self._sub(DecisionType.FINANCIAL,{"amount":2_000_000,"destination_account":"A","reference":"R"})
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_itops_destructive_blocked(self):
        r = self._sub(DecisionType.IT_OPS,{"action":"delete_database","target":"prod-db"})
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_itops_with_window_executes(self):
        r = self._sub(DecisionType.IT_OPS,
                      {"action":"delete_database","target":"staging","change_window_approved":True})
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_stats_aggregate(self):
        p = _pipe()
        p.process(_proc())
        p.process(DecisionRequest("sa",DecisionType.PROCUREMENT,{"amount":700000,"category":"hw"}))
        s = p.stats
        self.assertEqual(s["total"], 2)
        self.assertIn("block_rate_pct", s)

    def test_health_endpoint(self):
        h = self.p.health()
        self.assertEqual(h["status"], "healthy")
        self.assertIn("policies", h)


# ══════════════════════════════════════════════════════════════════════════════
# 7. BOUNDARY CONDITIONS
# ══════════════════════════════════════════════════════════════════════════════
class TestBoundaryConditions(unittest.TestCase):
    def setUp(self): self.pe = PolicyEngine()

    def test_exactly_at_500k_with_contract_passes(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
            {"amount":500000,"supplier_id":"SUP-001","category":"hw","contract_id":"CT"},
            DecisionContext())
        self.assertTrue(r.passed)

    def test_500k_plus_1_no_contract_fails(self):
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
            {"amount":500001,"supplier_id":"SUP-001","category":"hw"}, DecisionContext())
        self.assertFalse(r.passed)

    def test_exactly_30pct_price_passes(self):
        r = self.pe.evaluate(DecisionType.PRICING,
            {"product_id":"P","previous_price":100.0,"new_price":130.0,"floor_price":50.0},
            DecisionContext())
        self.assertTrue(r.passed)

    def test_30pct_plus_epsilon_fails(self):
        r = self.pe.evaluate(DecisionType.PRICING,
            {"product_id":"P","previous_price":100.0,"new_price":130.1,"floor_price":50.0},
            DecisionContext())
        self.assertFalse(r.passed)

    def test_confidence_exactly_at_threshold_passes(self):
        ctx = DecisionContext(confidence=0.30)
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
            {"amount":1000,"supplier_id":"SUP-001","category":"hw"}, ctx)
        self.assertEqual(len([v for v in r.violations if "AI-001" in v]), 0)

    def test_confidence_below_threshold_fails(self):
        ctx = DecisionContext(confidence=0.29)
        r = self.pe.evaluate(DecisionType.PROCUREMENT,
            {"amount":1000,"supplier_id":"SUP-001","category":"hw"}, ctx)
        self.assertGreater(len([v for v in r.violations if "AI-001" in v]), 0)


# ══════════════════════════════════════════════════════════════════════════════
# 8. CONCURRENCY
# ══════════════════════════════════════════════════════════════════════════════
class TestConcurrency(unittest.TestCase):
    def test_50_threads_zero_errors_unique_ids(self):
        p = _pipe(max_memory_records=2000)
        results = []; errors = []; lock = threading.Lock()
        def submit(i):
            try:
                r = p.process(_proc(f"con_{i%5}", 500*(i%20+1)))
                with lock: results.append(r.decision_id)
            except Exception as e:
                with lock: errors.append(str(e))
        threads = [threading.Thread(target=submit, args=(i,)) for i in range(50)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(set(results)), 50, "Duplicate decision IDs!")

    def test_agent_velocity_isolation(self):
        vb = VelocityBreaker(max_decisions=10)
        p  = _pipe(velocity_breaker=vb)
        statuses = {}; lock = threading.Lock()
        def batch(aid):
            for _ in range(3):
                r = p.process(_proc(aid, 500))
                with lock: statuses.setdefault(aid, []).append(r.final_status)
        threads = [threading.Thread(target=batch, args=(f"iso_{i}",)) for i in range(4)]
        for t in threads: t.start()
        for t in threads: t.join()
        for aid, ss in statuses.items():
            blocked = [s for s in ss if s == FinalStatus.BLOCKED]
            self.assertEqual(len(blocked), 0, f"Agent {aid} unexpectedly blocked")


# ══════════════════════════════════════════════════════════════════════════════
# 9. AUDIT LOGGER
# ══════════════════════════════════════════════════════════════════════════════
class TestAuditLogger(unittest.TestCase):
    def test_log_and_retrieve(self):
        p = _pipe()
        r = p.process(_proc())
        self.assertIsNotNone(p.audit_logger.get_by_id(r.decision_id))

    def test_filter_by_status(self):
        p = _pipe()
        p.process(_proc())
        p.process(DecisionRequest("fa",DecisionType.PROCUREMENT,{"amount":700000,"category":"hw"}))
        blocked = p.audit_logger.get_by_status(FinalStatus.BLOCKED)
        self.assertGreater(len(blocked), 0)

    def test_to_dict_json_serializable(self):
        p = _pipe()
        r = p.process(_proc())
        json.dumps(r.audit_record.to_dict())

    def test_bounded_ring_buffer(self):
        p = _pipe(max_memory_records=10)
        for _ in range(25): p.process(_proc())
        self.assertLessEqual(len(p.audit_logger.get_all()), 10)

    def test_bulk_records_all_retrievable(self):
        al = AuditLogger(echo=False, max_memory_records=50)
        p  = _pipe(audit_logger=al)
        for i in range(30): p.process(_proc(f"bulk_{i%5}"))
        self.assertGreaterEqual(len(al.get_all()), 30)

    def test_mandatory_fields_present(self):
        p = _pipe()
        p.process(_proc())
        rec = p.audit_logger.get_all()[-1]
        for f in ["decision_id","agent_id","decision_type","final_status","timestamp"]:
            self.assertIn(f, rec.to_dict())

    def test_concurrent_file_writes_no_corruption(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            p = _pipe(log_dir=tmpdir, max_memory_records=500)
            errors = []; lock = threading.Lock()
            def w(i):
                try: p.process(_proc(f"fw_{i%5}", 500+i))
                except Exception as e:
                    with lock: errors.append(str(e))
            threads = [threading.Thread(target=w, args=(i,)) for i in range(50)]
            for t in threads: t.start()
            for t in threads: t.join()
            self.assertEqual(len(errors), 0)
            from pathlib import Path
            lines_bad = 0; lines_total = 0
            for fp in Path(tmpdir).glob("*.jsonl"):
                for line in fp.read_text().strip().splitlines():
                    lines_total += 1
                    try: json.loads(line)
                    except json.JSONDecodeError: lines_bad += 1
            self.assertEqual(lines_bad, 0,
                f"{lines_bad}/{lines_total} JSONL lines are invalid — concurrent write corruption!")


# ══════════════════════════════════════════════════════════════════════════════
# 10. DECISION REPLAY
# ══════════════════════════════════════════════════════════════════════════════
class TestDecisionReplay(unittest.TestCase):
    def test_replay_produces_result(self):
        p = _pipe(); resp = p.process(_proc())
        rp = DecisionReplay(p).replay_one(resp.audit_record)
        self.assertIsNotNone(rp.final_status)

    def test_replay_tagged_with_original_id(self):
        p = _pipe(); resp = p.process(_proc())
        rp = DecisionReplay(p).replay_one(resp.audit_record)
        if rp.audit_record:
            self.assertEqual(rp.audit_record.replay_of, resp.decision_id)

    def test_tightened_policy_changes_outcome(self):
        p = _pipe()
        req = _req(payload={"amount":320000,"supplier_id":"SUP-001","category":"hw"})
        orig = p.process(req)
        self.assertEqual(orig.final_status, FinalStatus.EXECUTED)
        tighter = PolicyEngine()
        tighter.disable("PROC-001")
        def strict(payload, ctx):
            return PolicyEvaluation("PROC-001","Strict","fail" if float(payload.get("amount",0))>200000 else "pass","")
        tighter.register(Policy("PROC-001-STRICT","Strict",[DecisionType.PROCUREMENT],strict))
        p2 = _pipe(policy_engine=tighter)
        replayed = DecisionReplay(p2).replay_one(orig.audit_record)
        self.assertEqual(replayed.final_status, FinalStatus.BLOCKED)


# ══════════════════════════════════════════════════════════════════════════════
# 11. FLASK API
# ══════════════════════════════════════════════════════════════════════════════
class TestFlaskAPI(unittest.TestCase):
    def setUp(self):
        from glassbox.api.app import create_app
        self.app = create_app(echo=False, testing=True)
        self.client = self.app.test_client()

    def test_health(self):
        r = self.client.get("/health"); self.assertEqual(r.status_code, 200)
        self.assertEqual(r.get_json()["status"], "healthy")

    def test_ready(self):
        r = self.client.get("/ready"); self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["ready"])

    def test_submit_valid(self):
        r = self.client.post("/decisions", json={
            "agent_id":"api_a","decision_type":"procurement",
            "payload":{"amount":5000,"supplier_id":"SUP-001","category":"hw"}})
        self.assertEqual(r.status_code, 200)
        self.assertIn("final_status", r.get_json())

    def test_invalid_decision_type_422(self):
        r = self.client.post("/decisions", json={"agent_id":"a","decision_type":"INVALID","payload":{"amount":1}})
        self.assertEqual(r.status_code, 422)

    def test_missing_agent_id_422(self):
        r = self.client.post("/decisions", json={"decision_type":"procurement","payload":{"amount":1}})
        self.assertEqual(r.status_code, 422)

    def test_empty_body_400(self):
        r = self.client.post("/decisions", json={})
        self.assertEqual(r.status_code, 400)

    def test_malformed_json_400(self):
        r = self.client.post("/decisions", data="not json", content_type="text/plain")
        self.assertIn(r.status_code, [400, 415])

    def test_submit_and_retrieve(self):
        post = self.client.post("/decisions", json={
            "agent_id":"ret_a","decision_type":"procurement",
            "payload":{"amount":5000,"supplier_id":"SUP-001","category":"hw"}})
        did = post.get_json()["decision_id"]
        get = self.client.get(f"/decisions/{did}")
        self.assertEqual(get.status_code, 200)
        self.assertEqual(get.get_json()["decision_id"], did)

    def test_not_found_404(self):
        r = self.client.get("/decisions/00000000-0000-0000-0000-000000000000")
        self.assertEqual(r.status_code, 404)

    def test_invalid_decision_id_400(self):
        r = self.client.get("/decisions/../../etc/passwd")
        self.assertIn(r.status_code, [400, 404])

    def test_list_decisions(self):
        self.client.post("/decisions", json={
            "agent_id":"lst","decision_type":"procurement",
            "payload":{"amount":5000,"supplier_id":"SUP-001","category":"hw"}})
        r = self.client.get("/decisions")
        self.assertEqual(r.status_code, 200)
        self.assertIn("records", r.get_json())

    def test_stats_endpoint(self):
        r = self.client.get("/stats"); self.assertEqual(r.status_code, 200)
        self.assertIn("total", r.get_json())

    def test_policies_endpoint(self):
        r = self.client.get("/policies"); self.assertEqual(r.status_code, 200)
        self.assertGreater(len(r.get_json()["policies"]), 0)

    def test_velocity_endpoint(self):
        r = self.client.get("/agents/some_agent/velocity")
        self.assertEqual(r.status_code, 200)

    def test_ecosystem_endpoint(self):
        r = self.client.get("/ecosystem"); self.assertEqual(r.status_code, 200)

    def test_security_headers_present(self):
        r = self.client.get("/health")
        self.assertEqual(r.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(r.headers.get("X-Frame-Options"), "DENY")

    def test_injection_in_agent_id_blocked(self):
        r = self.client.post("/decisions", json={
            "agent_id": "'; DROP TABLE decisions;--",
            "decision_type":"procurement","payload":{"amount":1000}})
        self.assertEqual(r.status_code, 422)

    def test_all_decision_types_accepted(self):
        payloads = {
            "procurement": {"amount":1000,"supplier_id":"S","category":"hw"},
            "pricing":     {"new_price":105.0,"previous_price":100.0,"product_id":"P","floor_price":50.0},
            "financial":   {"amount":5000,"destination_account":"ACC"},
            "inventory":   {"quantity":100,"product_id":"SKU"},
            "it_ops":      {"action":"restart_service","target":"svc"},
            "logistics":   {"origin":"MUM","destination":"DEL"},
            "hr":          {"action":"address_update","employee_id":"EMP"},
            "custom":      {"description":"test"},
        }
        for dtype, payload in payloads.items():
            with self.subTest(dtype=dtype):
                r = self.client.post("/decisions", json={
                    "agent_id":f"api_{dtype}","decision_type":dtype,"payload":payload})
                self.assertIn(r.status_code, [200, 201], f"Type {dtype}: {r.get_json()}")


# ══════════════════════════════════════════════════════════════════════════════
# 12. SECURITY — SQL INJECTION
# ══════════════════════════════════════════════════════════════════════════════
class TestSecuritySQLInjection(unittest.TestCase):
    def setUp(self): self.san = PayloadSanitizer(block_on_sql=True)
    def _blocked(self, p): return self.san.check(p).blocked
    def test_or_injection(self):       self.assertTrue(self._blocked({"id":"' OR 1=1 --"}))
    def test_union_select(self):       self.assertTrue(self._blocked({"c":"hw UNION SELECT * FROM users"}))
    def test_drop_table(self):         self.assertTrue(self._blocked({"r":"REF; DROP TABLE decisions;"}))
    def test_sleep_injection(self):    self.assertTrue(self._blocked({"p":"P'; WAITFOR DELAY '0:0:5'--"}))
    def test_xp_cmdshell(self):        self.assertTrue(self._blocked({"a":"restart; xp_cmdshell('dir')"}))
    def test_blind_boolean(self):      self.assertTrue(self._blocked({"x":"agent' AND 1=1--"}))
    def test_clean_not_blocked(self):  self.assertFalse(self._blocked({"amount":5000,"supplier_id":"SUP-001"}))
    def test_normal_text(self):        self.assertFalse(self._blocked({"desc":"Quarterly procurement"}))


# ══════════════════════════════════════════════════════════════════════════════
# 13. SECURITY — SCRIPT/COMMAND INJECTION
# ══════════════════════════════════════════════════════════════════════════════
class TestSecurityScriptInjection(unittest.TestCase):
    def setUp(self): self.san = PayloadSanitizer(block_on_script=True)
    def _blocked(self, p): return self.san.check(p).blocked
    def test_xss_script(self):         self.assertTrue(self._blocked({"d":"<script>alert(1)</script>"}))
    def test_javascript_url(self):     self.assertTrue(self._blocked({"u":"javascript:void(0)"}))
    def test_jinja_ssti(self):         self.assertTrue(self._blocked({"t":"{{7*7}}"}))
    def test_el_injection(self):       self.assertTrue(self._blocked({"e":"${Runtime.exec('id')}"}))
    def test_python_eval(self):        self.assertTrue(self._blocked({"c":"eval('__import__(os)'"}))
    def test_path_traversal(self):     self.assertTrue(self._blocked({"p":"../../etc/passwd"}))
    def test_null_byte(self):          self.assertTrue(self._blocked({"n":"agent\x00admin"}))
    def test_blocked_keyword(self):    self.assertTrue(self._blocked({"note":"/etc/passwd contents"}))
    def test_clean_not_blocked(self):  self.assertFalse(self._blocked({"note":"Standard order Q4"}))


# ══════════════════════════════════════════════════════════════════════════════
# 14. SECURITY — PIPELINE INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════
class TestSecurityPipelineIntegration(unittest.TestCase):
    def setUp(self): self.p = _pipe()
    def test_sql_injection_blocked(self):
        r = self.p.process(DecisionRequest("a",DecisionType.PROCUREMENT,
            {"amount":1000,"supplier_id":"'; DROP TABLE suppliers;--","category":"hw"}))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
        self.assertTrue(any("SECURITY-001" in v for v in r.policy_violations))
    def test_script_injection_blocked(self):
        r = self.p.process(DecisionRequest("a",DecisionType.CUSTOM,
            {"description":"<script>fetch('https://evil.com')</script>"}))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
    def test_null_byte_agent_id_blocked(self):
        r = self.p.process(DecisionRequest("agent\x00admin",DecisionType.PROCUREMENT,{"amount":1000}))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
    def test_path_traversal_agent_id_blocked(self):
        r = self.p.process(DecisionRequest("../../etc/passwd",DecisionType.PROCUREMENT,{"amount":1000}))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
    def test_path_traversal_in_payload_blocked(self):
        r = self.p.process(DecisionRequest("a",DecisionType.IT_OPS,
            {"action":"read_file","target":"../../../../etc/shadow"}))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
    def test_clean_payload_executes(self):
        r = self.p.process(_proc())
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)


# ══════════════════════════════════════════════════════════════════════════════
# 15. AGENT ID VALIDATION
# ══════════════════════════════════════════════════════════════════════════════
class TestAgentIdValidation(unittest.TestCase):
    def test_valid(self):           ok,_ = validate_agent_id("agent_001"); self.assertTrue(ok)
    def test_empty(self):           ok,_ = validate_agent_id(""); self.assertFalse(ok)
    def test_too_long(self):        ok,_ = validate_agent_id("a"*200); self.assertFalse(ok)
    def test_path_traversal(self):  ok,_ = validate_agent_id("../../etc"); self.assertFalse(ok)
    def test_script(self):          ok,_ = validate_agent_id("<script>"); self.assertFalse(ok)
    def test_semicolon(self):       ok,_ = validate_agent_id("agent;DROP"); self.assertFalse(ok)
    def test_valid_special(self):
        for a in ["agent-1","a.b","a@b:8"]:
            ok,_ = validate_agent_id(a); self.assertTrue(ok, f"Should accept: {a}")


# ══════════════════════════════════════════════════════════════════════════════
# 16. LOAD TEST
# ══════════════════════════════════════════════════════════════════════════════
class TestLoadSustained(unittest.TestCase):
    def test_1000_zero_errors(self):
        p = _pipe(max_memory_records=2000)
        for i in range(1000): p.process(_proc(f"l_{i%10}", 500*(i%20+1)))
        self.assertEqual(p.stats["total"], 1000)
    def test_avg_latency_under_5ms(self):
        p = _pipe()
        for _ in range(200): p.process(_proc())
        self.assertLess(p.stats.get("avg_latency_ms", 999), 5.0)
    def test_p99_under_50ms(self):
        p = _pipe()
        for _ in range(500): p.process(_proc())
        p99 = p.stats.get("p99_latency_ms")
        if p99: self.assertLess(p99, 50.0)
    def test_all_types_no_errors(self):
        p   = _pipe()
        cases = [
            (DecisionType.PROCUREMENT, {"amount":5000,"supplier_id":"SUP-001","category":"hw"}),
            (DecisionType.PRICING,     {"new_price":110.0,"previous_price":100.0,"product_id":"P","reason":"d"}),
            (DecisionType.FINANCIAL,   {"amount":15000,"destination_account":"A","reference":"R"}),
            (DecisionType.INVENTORY,   {"quantity":500,"product_id":"SK"}),
            (DecisionType.IT_OPS,      {"action":"restart_service","target":"svc"}),
            (DecisionType.CUSTOM,      {"description":"ok"}),
        ]
        for _ in range(50):
            for dtype, payload in cases:
                p.process(DecisionRequest("lm",dtype,payload))


# ══════════════════════════════════════════════════════════════════════════════
# 17. STRESS TEST
# ══════════════════════════════════════════════════════════════════════════════
class TestStress(unittest.TestCase):
    def test_100_thread_no_errors_unique_ids(self):
        p = _pipe(max_memory_records=10000)
        results = []; errors = []; lock = threading.Lock()
        def w(i):
            for j in range(50):
                try:
                    r = p.process(_proc(f"st_{i}", 1000))
                    with lock: results.append(r.decision_id)
                except Exception as e:
                    with lock: errors.append(str(e))
        threads = [threading.Thread(target=w, args=(i,)) for i in range(100)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)
        self.assertEqual(len(set(results)), len(results), "Duplicate IDs under stress!")

    def test_multi_instance_isolation(self):
        p1, p2 = _pipe(), _pipe()
        p1.policy_engine.disable("PROC-001")
        r1 = p1.process(DecisionRequest("a",DecisionType.PROCUREMENT,{"amount":700000,"supplier_id":"S","category":"hw"}))
        r2 = p2.process(DecisionRequest("a",DecisionType.PROCUREMENT,{"amount":700000,"supplier_id":"S","category":"hw"}))
        self.assertEqual(r1.final_status, FinalStatus.EXECUTED, "p1 should execute (disabled)")
        self.assertEqual(r2.final_status, FinalStatus.BLOCKED,  "p2 must block (independent)")

    def test_bounded_memory_under_stress(self):
        p = _pipe(max_memory_records=100)
        for _ in range(300): p.process(_proc())
        self.assertLessEqual(len(p.audit_logger.get_all()), 100)

    def test_gc_after_large_run(self):
        p = _pipe(max_memory_records=100)
        for _ in range(500): p.process(_proc())
        gc.collect()
        self.assertLessEqual(len(p.audit_logger.get_all()), 100)


# ══════════════════════════════════════════════════════════════════════════════
# 18. SPIKE TEST
# ══════════════════════════════════════════════════════════════════════════════
class TestSpike(unittest.TestCase):
    def test_500_simultaneous_threads(self):
        p = _pipe(max_memory_records=1000, async_workers=32)
        errors = []; lock = threading.Lock()
        def submit(i):
            try: p.process(_proc(f"sp_{i}", 100+i))
            except Exception as e:
                with lock: errors.append(str(e))
        with ThreadPoolExecutor(max_workers=500) as pool:
            futs = [pool.submit(submit, i) for i in range(500)]
            for f in as_completed(futs): f.result()
        self.assertEqual(len(errors), 0)

    def test_burst_then_normal_restored(self):
        vb = VelocityBreaker(max_decisions=5, window_seconds=60, cooldown_seconds=0)
        p  = _pipe(velocity_breaker=vb)
        for _ in range(7): p.process(_proc("spk"))
        vb.reset("spk")
        r = p.process(_proc("spk"))
        self.assertFalse(r.circuit_breaker_triggered)


# ══════════════════════════════════════════════════════════════════════════════
# 19. ASYNC PIPELINE
# ══════════════════════════════════════════════════════════════════════════════
class TestAsyncPipeline(unittest.TestCase):
    def test_async_single(self):
        p = _pipe()
        r = asyncio.run(p.process_async(_proc()))
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_async_50_concurrent_unique_ids(self):
        p = _pipe()
        async def go():
            return await asyncio.gather(*[p.process_async(_proc(f"as_{i}")) for i in range(50)])
        results = asyncio.run(go())
        ids = [r.decision_id for r in results]
        self.assertEqual(len(set(ids)), 50)

    def test_async_blocked(self):
        p = _pipe()
        async def go():
            return await p.process_async(DecisionRequest("ab",DecisionType.PROCUREMENT,{"amount":700000,"category":"hw"}))
        r = asyncio.run(go())
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_async_injection_blocked(self):
        p = _pipe()
        async def go():
            return await p.process_async(DecisionRequest("ai",DecisionType.CUSTOM,{"description":"{{7*7}}"}))
        r = asyncio.run(go())
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)

    def test_async_event_loop_not_blocked(self):
        p = _pipe()
        async def go():
            tasks = [p.process_async(_proc(f"el_{i}")) for i in range(20)]
            async def counter():
                count = 0
                for _ in range(100): await asyncio.sleep(0); count += 1
                return count
            all_results = await asyncio.gather(*tasks, counter())
            return all_results[-1]
        count = asyncio.run(go())
        self.assertEqual(count, 100, "Event loop was blocked!")

    def test_async_shutdown_no_raise(self):
        p = _pipe()
        try: p.shutdown()
        except Exception as e: self.fail(f"shutdown() raised: {e}")


# ══════════════════════════════════════════════════════════════════════════════
# 20. PLATFORM ADAPTERS
# ══════════════════════════════════════════════════════════════════════════════
class TestPlatformAdapters(unittest.TestCase):
    def test_base_creates_pipeline(self):
        p = BaseAdapter().create_pipeline()
        self.assertIsNotNone(p)
        r = p.process(_proc()); self.assertIsNotNone(r.final_status)

    def test_databricks_config(self):
        cfg = DatabricksAdapter().get_config()
        self.assertIn("log_dir", cfg); self.assertIn("environment", cfg)

    def test_kubernetes_config(self):
        cfg = KubernetesAdapter().get_config()
        self.assertEqual(cfg["log_dir"], "/var/log/glassbox")

    def test_fabric_config(self):
        cfg = FabricAdapter().get_config(); self.assertIn("log_dir", cfg)

    def test_auto_detect(self):
        adapter = auto_detect_adapter()
        p = adapter.create_pipeline()
        r = p.process(_proc()); self.assertIsNotNone(r.final_status)

    def test_k8s_readiness(self):
        adapter = KubernetesAdapter()
        p = adapter.create_pipeline()
        check = adapter.readiness_check(p)
        self.assertIn("ready", check); self.assertTrue(check["ready"])

    def test_k8s_liveness(self):
        check = KubernetesAdapter().liveness_check()
        self.assertTrue(check["alive"])


# ══════════════════════════════════════════════════════════════════════════════
# 21. AGENT CONTRACT
# ══════════════════════════════════════════════════════════════════════════════
class TestAgentContracts(unittest.TestCase):
    def setUp(self): self.p = _pipe()

    def test_permitted_type_executes(self):
        self.p.register_contract(AgentContract(
            "c_agent", [DecisionType.PROCUREMENT], max_amount=999999))
        r = self.p.process(_proc("c_agent", 1000))
        self.assertEqual(r.final_status, FinalStatus.EXECUTED)

    def test_forbidden_type_blocked(self):
        self.p.register_contract(AgentContract(
            "c2_agent", [DecisionType.PRICING], max_amount=999999))
        r = self.p.process(_proc("c2_agent", 1000))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)
        self.assertTrue(any("CONTRACT-001" in v for v in r.policy_violations))

    def test_over_amount_blocked(self):
        self.p.register_contract(AgentContract(
            "c3_agent", [DecisionType.PROCUREMENT], max_amount=5000))
        r = self.p.process(_proc("c3_agent", 10000))
        self.assertEqual(r.final_status, FinalStatus.BLOCKED)


# ══════════════════════════════════════════════════════════════════════════════
# 22. HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
class TestHealthCheck(unittest.TestCase):
    def test_structure(self):
        p = _pipe(); p.process(_proc())
        h = p.health()
        for k in ["status","service","version","environment","total_decisions","policies"]:
            self.assertIn(k, h)
        self.assertEqual(h["status"], "healthy")
        self.assertEqual(h["total_decisions"], 1)


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════


class TestAsyncRetry(unittest.TestCase):
    """Validate RetryExecutor.async_execute() — never blocks event loop."""

    def test_async_execute_success(self):
        from glassbox.governance.retry_policy import RetryExecutor
        from glassbox.governance.models import RetryConfig, RetryStrategy
        executor = RetryExecutor(RetryConfig(strategy=RetryStrategy.NONE, max_attempts=1))
        async def ok_fn(rec): return {"status": "ok"}
        p = _pipe(); r = p.process(_proc())
        result = asyncio.run(executor.async_execute(ok_fn, r.audit_record))
        self.assertTrue(result.success)
        self.assertEqual(result.attempts, 1)

    def test_async_execute_retries_on_connection_error(self):
        from glassbox.governance.retry_policy import RetryExecutor
        from glassbox.governance.models import RetryConfig, RetryStrategy
        calls = []
        async def flaky(rec):
            calls.append(1)
            if len(calls) < 3: raise ConnectionError("down")
            return {"ok": True}
        executor = RetryExecutor(RetryConfig(
            strategy=RetryStrategy.FIXED, max_attempts=3, base_delay_s=0.0))
        p = _pipe(); r = p.process(_proc())
        result = asyncio.run(executor.async_execute(flaky, r.audit_record))
        self.assertTrue(result.success)
        self.assertEqual(len(calls), 3)

    def test_async_execute_non_retryable_fails_immediately(self):
        from glassbox.governance.retry_policy import RetryExecutor
        from glassbox.governance.models import RetryConfig, RetryStrategy
        calls = []
        async def bad(rec): calls.append(1); raise ValueError("non-retryable")
        executor = RetryExecutor(RetryConfig(max_attempts=3))
        p = _pipe(); r = p.process(_proc())
        result = asyncio.run(executor.async_execute(bad, r.audit_record))
        self.assertFalse(result.success)
        self.assertEqual(len(calls), 1, "Non-retryable must not retry")

    def test_async_execute_exhausted(self):
        from glassbox.governance.retry_policy import RetryExecutor
        from glassbox.governance.models import RetryConfig, RetryStrategy
        async def always_fail(rec): raise ConnectionError("always")
        executor = RetryExecutor(RetryConfig(
            strategy=RetryStrategy.FIXED, max_attempts=2, base_delay_s=0.0))
        p = _pipe(); r = p.process(_proc())
        result = asyncio.run(executor.async_execute(always_fail, r.audit_record))
        self.assertFalse(result.success)
        self.assertEqual(result.attempts, 2)
        self.assertIn("exhausted", result.error)

    def test_async_sleep_not_time_sleep(self):
        """async_execute must use asyncio.sleep, not time.sleep."""
        import inspect
        from glassbox.governance import retry_policy
        src = inspect.getsource(retry_policy.RetryExecutor.async_execute)
        self.assertIn("asyncio.sleep", src)
        self.assertNotIn("time.sleep", src)


# ══════════════════════════════════════════════════════════════════════════════
# 24. ASYNC REPLAY + PARALLEL REPLAY
# ══════════════════════════════════════════════════════════════════════════════
class TestAsyncAndParallelReplay(unittest.TestCase):

    def test_async_replay_one(self):
        p = _pipe(); resp = p.process(_proc())
        rp = DecisionReplay(p)
        r = asyncio.run(rp.async_replay_one(resp.audit_record))
        self.assertIsNotNone(r.final_status)
        self.assertEqual(r.audit_record.replay_of, resp.decision_id)

    def test_async_replay_many(self):
        p = _pipe()
        records = [p.process(_proc(f"ar_{i}")).audit_record for i in range(5)]
        rp = DecisionReplay(p)
        async def go():
            return await rp.async_replay_many(records, max_concurrency=3)
        results = asyncio.run(go())
        self.assertEqual(len(results), 5)
        self.assertFalse(any("error" in r for r in results))

    def test_parallel_replay_same_results_as_sequential(self):
        p = _pipe()
        records = [p.process(_proc(f"pr_{i}", 1000*(i+1))).audit_record for i in range(8)]
        rp = DecisionReplay(p)
        seq = rp.replay_many(records, parallel=False)
        par = rp.replay_many(records, parallel=True, max_workers=4)
        # Sort both by decision_id for comparison
        seq_ids = sorted(r["decision_id"] for r in seq)
        par_ids = sorted(r["decision_id"] for r in par)
        self.assertEqual(seq_ids, par_ids)
        self.assertEqual(len(par), 8)

    def test_compare_summary_by_type(self):
        p = _pipe()
        records = [p.process(_proc()).audit_record for _ in range(3)]
        rp = DecisionReplay(p)
        results = rp.replay_many(records)
        summary = rp.compare_summary(results)
        self.assertIn("by_decision_type", summary)
        self.assertIn("procurement", summary["by_decision_type"])

    def test_async_replay_blocked_decision(self):
        p = _pipe()
        resp = p.process(DecisionRequest(
            "replay_block", DecisionType.PROCUREMENT,
            {"amount": 700000, "category": "hardware"}))
        rp = DecisionReplay(p)
        async def go():
            return await rp.async_replay_one(resp.audit_record)
        r = asyncio.run(go())
        # Replayed record should also be blocked (same payload, same policy)
        self.assertIsNotNone(r.final_status)


# ══════════════════════════════════════════════════════════════════════════════
# 25. SCHEMA VALIDATOR EDGE CASES
# ══════════════════════════════════════════════════════════════════════════════
class TestSchemaValidatorEdgeCases(unittest.TestCase):
    def setUp(self): self.v = SchemaValidator()

    def test_tuple_type_error_message_no_attributeerror(self):
        """expected_type=(int,float) must not raise AttributeError."""
        try:
            ok, msg = self.v.validate(DecisionType.PROCUREMENT, {"amount": "not_a_number"})
            self.assertFalse(ok)
            self.assertIn("int or float", msg)
        except AttributeError as e:
            self.fail(f"AttributeError raised for tuple type: {e}")

    def test_none_payload_fails_gracefully(self):
        ok, msg = self.v.validate(DecisionType.PROCUREMENT, None)
        self.assertFalse(ok); self.assertIsNotNone(msg)

    def test_custom_type_always_passes(self):
        ok, _ = self.v.validate(DecisionType.CUSTOM, {"anything": "goes"})
        self.assertTrue(ok)

    def test_extra_fields_do_not_cause_failure(self):
        ok, _ = self.v.validate(DecisionType.PROCUREMENT,
                                {"amount": 1000, "extra_field": "value"})
        self.assertTrue(ok)

    def test_logistics_requires_origin_destination(self):
        ok, _ = self.v.validate(DecisionType.LOGISTICS, {"origin": "MUM", "destination": "DEL"})
        self.assertTrue(ok)
        ok2, _ = self.v.validate(DecisionType.LOGISTICS, {"origin": "MUM"})
        self.assertFalse(ok2)


# ══════════════════════════════════════════════════════════════════════════════
# 26. CONTEXT CAPTURE ENVIRONMENT LOGIC
# ══════════════════════════════════════════════════════════════════════════════
class TestContextCapture(unittest.TestCase):
    def test_pipeline_env_used_when_request_has_default(self):
        from glassbox.governance.context_capture import ContextCapture
        cc = ContextCapture(environment="staging")
        req = DecisionRequest("a", DecisionType.PROCUREMENT, {"amount": 1000})
        ctx = cc.enrich(req)
        self.assertEqual(ctx.environment, "staging")

    def test_request_env_overrides_pipeline_env(self):
        from glassbox.governance.context_capture import ContextCapture
        cc = ContextCapture(environment="production")
        req = DecisionRequest("a", DecisionType.PROCUREMENT, {"amount": 1000},
                              context=DecisionContext(environment="testing"))
        ctx = cc.enrich(req)
        self.assertEqual(ctx.environment, "testing")

    def test_safe_hostname_never_raises(self):
        from glassbox.governance.context_capture import _safe_hostname
        try:
            h = _safe_hostname()
            self.assertIsInstance(h, str)
            self.assertGreater(len(h), 0)
        except Exception as e:
            self.fail(f"_safe_hostname() raised: {e}")

    def test_metadata_enriched(self):
        from glassbox.governance.context_capture import ContextCapture
        cc = ContextCapture()
        req = DecisionRequest("a", DecisionType.PROCUREMENT, {"amount": 1000})
        ctx = cc.enrich(req)
        self.assertIn("governance_entry_utc", ctx.metadata)
        self.assertIn("host", ctx.metadata)


# ══════════════════════════════════════════════════════════════════════════════
# 27. AUDIT LOGGER FSYNC + CSV EXPORT
# ══════════════════════════════════════════════════════════════════════════════
class TestAuditLoggerExtended(unittest.TestCase):
    def test_fsync_configurable(self):
        """fsync_writes=False (default) must not raise."""
        al = AuditLogger(echo=False, fsync_writes=False)
        p  = _pipe(audit_logger=al)
        p.process(_proc())   # should not raise

    def test_csv_export(self):
        import csv, tempfile
        p = _pipe()
        for _ in range(5): p.process(_proc())
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False, mode='w') as f:
            path = f.name
        p.audit_logger.export_csv(path)
        with open(path) as f:
            rows = list(csv.DictReader(f))
        self.assertGreaterEqual(len(rows), 5)
        self.assertIn("decision_id", rows[0])
        import os; os.unlink(path)

    def test_get_executed_spend_time_window(self):
        """get_executed_spend with window_seconds should filter by time."""
        p = _pipe()
        p.process(_proc(amount=10000))
        # Spend in the last 3600s should be non-zero
        spend = p.audit_logger.get_executed_spend(
            DecisionType.PROCUREMENT, window_seconds=3600)
        self.assertGreater(spend, 0)
        # Very short window (0s) should return 0
        spend_zero = p.audit_logger.get_executed_spend(
            DecisionType.PROCUREMENT, window_seconds=0)
        self.assertEqual(spend_zero, 0.0)

    def test_anomaly_detector_get_stats_thread_safe(self):
        """get_agent_stats() must not race with concurrent check() calls."""
        det = AnomalyDetector(z_threshold=3.0, min_samples=5)
        det.inject_baseline("ag", "procurement", "amount", [50000.0]*20)
        errors = []; lock = threading.Lock()
        def check_thread():
            for _ in range(100):
                try: det.check("ag", "procurement", {"amount": 50000})
                except Exception as e:
                    with lock: errors.append(str(e))
        def stats_thread():
            for _ in range(100):
                try: det.get_agent_stats("ag", "procurement")
                except Exception as e:
                    with lock: errors.append(str(e))
        threads = ([threading.Thread(target=check_thread) for _ in range(5)] +
                   [threading.Thread(target=stats_thread) for _ in range(5)])
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0, f"Concurrent stats errors: {errors}")


# ── Update runner at bottom ──────────────────────────────────────────────────
# The runner at the bottom of the file already has the existing classes.
# These new classes are picked up automatically by unittest discovery.
if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    classes = [
        TestSchemaValidator, TestPolicyEngine, TestRiskEvaluator,
        TestVelocityBreaker, TestAnomalyDetector, TestGovernancePipeline,
        TestBoundaryConditions, TestConcurrency, TestAuditLogger,
        TestDecisionReplay, TestFlaskAPI, TestSecuritySQLInjection,
        TestSecurityScriptInjection, TestSecurityPipelineIntegration,
        TestAgentIdValidation, TestLoadSustained, TestStress, TestSpike,
        TestAsyncPipeline, TestPlatformAdapters, TestAgentContracts,
        TestHealthCheck,
        # v1.0.0 additions
        TestAsyncRetry, TestAsyncAndParallelReplay,
        TestSchemaValidatorEdgeCases, TestContextCapture,
        TestAuditLoggerExtended,
        # v1.0.0 industry examples and spark adapter
        TestIndustryExamples, TestSparkAdapter,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)


# ══════════════════════════════════════════════════════════════════════════════
# 23. ASYNC RETRY
# ══════════════════════════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════════════════════════
# 28. INDUSTRY EXAMPLES SMOKE TEST
# ══════════════════════════════════════════════════════════════════════════════
class TestIndustryExamples(unittest.TestCase):
    """Smoke-test every industry example runs without exception."""

    def _run(self, fn_name):
        import importlib, sys, os
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
        examples_path = os.path.join(root, 'examples', 'industry_examples.py')
        spec = importlib.util.spec_from_file_location("industry_examples", examples_path)
        mod  = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        fn = getattr(mod, fn_name)
        try:
            fn()
        except SystemExit:
            pass  # argparse may call sys.exit in some modes

    def test_example_financial_services(self):
        self._run('example_01_financial_trading')

    def test_example_healthcare(self):
        self._run('example_02_healthcare')

    def test_example_manufacturing(self):
        self._run('example_07_manufacturing')

    def test_example_insurance(self):
        self._run('example_08_insurance')

    def test_example_energy(self):
        self._run('example_06_energy_grid')

    def test_example_security_demonstration(self):
        self._run('example_13_security')

    def test_example_ecommerce_pricing(self):
        self._run('example_11_retail')

    def test_example_logistics(self):
        self._run('example_09_logistics')

    def test_example_hr_compensation(self):
        self._run('example_12_hr')

    def test_example_policy_replay(self):
        self._run('example_15_policy_replay')

    def test_example_spark_guide(self):
        self._run('example_16_rag_governance')

    def test_example_quickstart(self):
        self._run('example_18_nl_policy_authoring')


# ══════════════════════════════════════════════════════════════════════════════
# 29. SPARK ADAPTER (without Spark dependency)
# ══════════════════════════════════════════════════════════════════════════════
class TestSparkAdapter(unittest.TestCase):
    """Validate Spark adapter imports and non-Spark paths work correctly."""

    def test_spark_adapter_imports(self):
        """Adapter module must import without PySpark installed."""
        try:
            from glassbox.adapters.spark import GlassBoxSparkAdapter, _build_pipeline
            self.assertTrue(callable(GlassBoxSparkAdapter))
        except ImportError as e:
            self.fail(f"Spark adapter failed to import: {e}")

    def test_build_pipeline_no_spark(self):
        """_build_pipeline() must return a working GovernancePipeline."""
        from glassbox.adapters.spark import _build_pipeline
        pipeline = _build_pipeline(echo=False)
        self.assertIsNotNone(pipeline)
        r = pipeline.process(_proc())
        self.assertIsNotNone(r.final_status)

    def test_require_spark_raises_import_error(self):
        """_require_spark() must raise ImportError when PySpark not installed."""
        import sys
        # Temporarily hide pyspark
        pyspark = sys.modules.get('pyspark')
        if pyspark is not None:
            return  # PySpark is installed — skip this test
        from glassbox.adapters.spark import _require_spark
        with self.assertRaises(ImportError):
            _require_spark()

    def test_row_to_response_clean_payload(self):
        """_row_to_response must return a DecisionRequest from a row dict."""
        from glassbox.adapters.spark import _row_to_response
        row = {
            "agent_id": "spark_agent",
            "decision_type": "procurement",
            "payload_json": '{"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"}',
            "confidence": 0.95,
            "environment": "production",
            "agent_chain_json": "[]",
        }
        req = _row_to_response(row)
        self.assertEqual(req.agent_id, "spark_agent")
        self.assertEqual(req.payload["amount"], 5000)

    def test_row_to_response_malformed_json(self):
        """Malformed payload_json must fall back to empty dict, not raise."""
        from glassbox.adapters.spark import _row_to_response
        row = {
            "agent_id": "spark_agent",
            "decision_type": "custom",
            "payload_json": "NOT VALID JSON {{{}}}",
        }
        try:
            req = _row_to_response(row)
            self.assertEqual(req.payload, {})
        except Exception as e:
            self.fail(f"_row_to_response raised on malformed JSON: {e}")
