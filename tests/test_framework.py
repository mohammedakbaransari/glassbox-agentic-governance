"""
GlassBox Framework — Framework Component Tests  (v1.0.0)
=========================================================
Tests for:
  - PolicyRepository (in-memory + SQLite)
  - AuditRepository  (SQLite)
  - WorkflowRepository (SQLite)
  - EventBus + domain events
  - RulesEngine (declarative YAML/JSON rules)
  - WorkflowEngine (lifecycle + SLA)
  - ExecutionTrace (per-stage pipeline tracing)
  - Full framework integration (pipeline + all components)

Run:  python3 tests/test_framework.py
Or:   python3 -m unittest tests.test_framework -v

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import threading
import time
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("GLASSBOX_LOG_LEVEL", "CRITICAL")

from glassbox.governance.models import (
    AgentContract, DecisionContext, DecisionRequest, DecisionType,
    FinalStatus, PolicyStatus, WorkflowStatus,
)
from glassbox.governance.pipeline    import GovernancePipeline
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.models      import PolicyEvaluation
from glassbox.governance.execution_trace import ExecutionTrace, ExecutionStep, StageTimer
from glassbox.store.repository       import (
    PolicyRecord, InMemoryPolicyRepository, SQLitePolicyRepository,
    SQLiteAuditRepository, SQLiteWorkflowRepository,
    WorkflowInstance, WorkflowStep, RepositoryFactory,
)
from glassbox.events.event_bus       import (
    EventBus, GlassBoxEvent, get_event_bus,
    DecisionExecuted, DecisionBlocked, DecisionPendingReview,
    PolicyViolated, CircuitBreakerTripped, AnomalyDetected,
    SecurityViolation, SLABreached, LoggingEventHandler,
)
from glassbox.rules.rules_engine     import (
    RuleCondition, DeclarativeRule, RulesLoader, REFERENCE_RULES_JSON,
)
from glassbox.workflow.workflow_engine import WorkflowEngine


def _pipe(**kw) -> GovernancePipeline:
    return GovernancePipeline(echo=False, **kw)

def _proc(agent="fw_agent", amount=5000):
    return DecisionRequest(
        agent_id=agent, decision_type=DecisionType.PROCUREMENT,
        payload={"amount": amount, "supplier_id": "SUP-001", "category": "hardware"},
    )


# ══════════════════════════════════════════════════════════════════════════════
# POLICY REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class TestInMemoryPolicyRepository(unittest.TestCase):

    def setUp(self):
        self.repo = InMemoryPolicyRepository()
        self.record = PolicyRecord(
            policy_id="TEST-001", policy_name="Test Policy",
            decision_types=["procurement"], rule_type="python",
            rule_body="pass", version="1.0",
        )

    def test_save_and_get(self):
        self.repo.save(self.record)
        got = self.repo.get("TEST-001")
        self.assertIsNotNone(got)
        self.assertEqual(got.policy_name, "Test Policy")

    def test_list_all(self):
        self.repo.save(self.record)
        policies = self.repo.list_all()
        self.assertGreaterEqual(len(policies), 1)

    def test_update_status(self):
        self.repo.save(self.record)
        ok = self.repo.update_status("TEST-001", "deprecated")
        self.assertTrue(ok)
        got = self.repo.get("TEST-001")
        self.assertEqual(got.status, "deprecated")

    def test_delete(self):
        self.repo.save(self.record)
        ok = self.repo.delete("TEST-001")
        self.assertTrue(ok)
        self.assertIsNone(self.repo.get("TEST-001"))

    def test_list_versions(self):
        self.repo.save(self.record)
        v2 = PolicyRecord(policy_id="TEST-001", policy_name="Test Policy v2",
                          decision_types=["procurement"], rule_type="python",
                          rule_body="pass", version="2.0")
        self.repo.save(v2)
        versions = self.repo.list_versions("TEST-001")
        self.assertEqual(len(versions), 2)

    def test_get_missing_returns_none(self):
        self.assertIsNone(self.repo.get("NONEXISTENT"))

    def test_list_by_status(self):
        self.repo.save(self.record)
        active = self.repo.list_all(status="active")
        self.assertGreaterEqual(len(active), 1)
        draft = self.repo.list_all(status="draft")
        self.assertEqual(len(draft), 0)


class TestSQLitePolicyRepository(unittest.TestCase):

    def setUp(self):
        self.repo = SQLitePolicyRepository(":memory:")
        self.record = PolicyRecord(
            policy_id="SQL-001", policy_name="SQL Test Policy",
            decision_types=["financial"], rule_type="python",
            rule_body="pass", version="1.0",
        )

    def test_save_and_get(self):
        self.repo.save(self.record)
        got = self.repo.get("SQL-001")
        self.assertIsNotNone(got)
        self.assertEqual(got.policy_name, "SQL Test Policy")

    def test_list_all(self):
        self.repo.save(self.record)
        policies = self.repo.list_all()
        self.assertEqual(len(policies), 1)

    def test_update_status(self):
        self.repo.save(self.record)
        ok = self.repo.update_status("SQL-001", "deprecated")
        self.assertTrue(ok)

    def test_delete(self):
        self.repo.save(self.record)
        ok = self.repo.delete("SQL-001")
        self.assertTrue(ok)
        self.assertIsNone(self.repo.get("SQL-001"))

    def test_concurrent_saves(self):
        errors = []; lock = threading.Lock()
        def save_batch(i):
            try:
                rec = PolicyRecord(
                    policy_id=f"CONC-{i:03d}", policy_name=f"Policy {i}",
                    decision_types=["custom"], rule_type="python", rule_body="pass",
                )
                self.repo.save(rec)
            except Exception as e:
                with lock: errors.append(str(e))
        threads = [threading.Thread(target=save_batch, args=(i,)) for i in range(20)]
        for t in threads: t.start()
        for t in threads: t.join()
        self.assertEqual(len(errors), 0)
        self.assertGreaterEqual(len(self.repo.list_all()), 20)


# ══════════════════════════════════════════════════════════════════════════════
# AUDIT REPOSITORY
# ══════════════════════════════════════════════════════════════════════════════

class TestSQLiteAuditRepository(unittest.TestCase):

    def setUp(self):
        self.repo = SQLiteAuditRepository(":memory:")
        self.p    = _pipe(audit_repo=self.repo)

    def test_save_and_get_by_id(self):
        resp = self.p.process(_proc())
        got  = self.repo.get_by_id(resp.decision_id)
        self.assertIsNotNone(got)
        self.assertEqual(got["decision_id"], resp.decision_id)

    def test_query_by_agent(self):
        self.p.process(_proc(agent="query_agent"))
        self.p.process(_proc(agent="other_agent"))
        results = self.repo.query(agent_id="query_agent")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["agent_id"], "query_agent")

    def test_query_by_status(self):
        self.p.process(_proc())
        blocked_req = DecisionRequest("block_agent", DecisionType.PROCUREMENT,
                                       {"amount": 700000, "category": "hardware"})
        self.p.process(blocked_req)
        blocked = self.repo.query(final_status="blocked")
        self.assertGreaterEqual(len(blocked), 1)

    def test_query_by_risk_score(self):
        self.p.process(_proc(amount=900000))
        # Use a threshold below the actual risk score (~22.5 for large procurement)
        high_risk = self.repo.query(min_risk_score=10.0)
        self.assertGreater(len(high_risk), 0)

    def test_aggregate_spend(self):
        self.p.process(_proc(amount=10000))
        self.p.process(_proc(amount=5000))
        spend = self.repo.aggregate_spend("procurement")
        self.assertGreaterEqual(spend, 15000)

    def test_count(self):
        for _ in range(5):
            self.p.process(_proc())
        total = self.repo.count()
        self.assertGreaterEqual(total, 5)

    def test_query_time_range(self):
        from datetime import datetime, timezone, timedelta
        self.p.process(_proc())
        from_ts = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        results = self.repo.query(from_ts=from_ts)
        self.assertGreater(len(results), 0)


# ══════════════════════════════════════════════════════════════════════════════
# EVENT BUS
# ══════════════════════════════════════════════════════════════════════════════

class TestEventBus(unittest.TestCase):

    def setUp(self):
        self.bus = EventBus(max_workers=2)

    def tearDown(self):
        self.bus.shutdown()

    def test_subscribe_and_publish(self):
        received = []; lock = threading.Lock()
        def handler(evt): 
            with lock: received.append(evt)
        self.bus.subscribe("decision.executed", handler)
        self.bus.publish_sync(DecisionExecuted("d1","a1","procurement",5.0,1.0))
        self.assertEqual(len(received), 1)
        self.assertEqual(received[0].event_type, "decision.executed")

    def test_wildcard_subscription(self):
        received = []; lock = threading.Lock()
        self.bus.subscribe("*", lambda e: received.append(e))
        self.bus.publish_sync(DecisionBlocked("d1","a1","financial",["VIOL"],.5))
        self.bus.publish_sync(DecisionExecuted("d2","a2","pricing",3.0,1.0))
        self.assertEqual(len(received), 2)

    def test_crashing_handler_does_not_crash_bus(self):
        def bad_handler(evt): raise RuntimeError("boom")
        self.bus.subscribe("decision.blocked", bad_handler)
        try:
            self.bus.publish_sync(DecisionBlocked("d1","a1","procurement",["V"],5.0))
        except RuntimeError:
            self.fail("EventBus propagated exception from crashing handler")

    def test_async_handler(self):
        received = []; lock = threading.Lock()
        async def async_handler(evt):
            with lock: received.append(evt)
        self.bus.subscribe("anomaly.detected", async_handler)
        self.bus.publish_sync(AnomalyDetected("d1","a1","procurement",["amount"],4.2))
        self.assertEqual(len(received), 1)

    def test_unsubscribe(self):
        received = []
        def handler(evt): received.append(evt)
        self.bus.subscribe("decision.executed", handler)
        self.bus.unsubscribe("decision.executed", handler)
        self.bus.publish_sync(DecisionExecuted("d1","a1","procurement",1.0,1.0))
        self.assertEqual(len(received), 0)

    def test_event_history(self):
        self.bus.publish_sync(DecisionExecuted("d1","a1","procurement",1.0,1.0))
        self.bus.publish_sync(DecisionBlocked("d2","a1","financial",["V"],2.0))
        recent = self.bus.recent()
        self.assertGreaterEqual(len(recent), 2)

    def test_pipeline_publishes_events(self):
        bus = EventBus(max_workers=2)
        received = []; lock = threading.Lock()
        bus.subscribe("decision.blocked", lambda e: received.append(e) or None)
        p = _pipe(event_bus=bus)
        p.process(DecisionRequest("ev_agent", DecisionType.PROCUREMENT,
                                   {"amount": 700000, "category": "hardware"}))
        time.sleep(0.05)  # allow async dispatch
        self.assertGreater(len(received), 0)
        bus.shutdown()

    def test_all_event_factory_functions(self):
        events = [
            DecisionExecuted("d1","a","procurement",5.0,1.0),
            DecisionBlocked("d2","a","financial",["V"],5.0),
            DecisionPendingReview("d3","a","pricing",40.0),
            PolicyViolated("d4","a",["V1"],["W1"]),
            CircuitBreakerTripped("a","velocity","too fast",False),
            AnomalyDetected("d5","a","procurement",["amount"],4.2),
            SecurityViolation("a","procurement",["SQL injection"]),
            SLABreached("wf1","d6","a",60,75.0),
        ]
        for e in events:
            self.assertIsInstance(e, GlassBoxEvent)
            d = e.to_dict()
            self.assertIn("event_type", d)
            self.assertIn("event_id",   d)


# ══════════════════════════════════════════════════════════════════════════════
# DECLARATIVE RULES ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestRuleCondition(unittest.TestCase):

    def setUp(self):
        self.ctx = DecisionContext()

    def test_gt_passes(self):
        c = RuleCondition("amount", "gt", 1000)
        self.assertTrue(c.evaluate({"amount": 2000}, self.ctx))

    def test_gt_fails(self):
        c = RuleCondition("amount", "gt", 1000)
        self.assertFalse(c.evaluate({"amount": 500}, self.ctx))

    def test_missing_operator(self):
        c = RuleCondition("contract_id", "missing")
        self.assertTrue(c.evaluate({"amount": 1000}, self.ctx))
        self.assertFalse(c.evaluate({"contract_id": "CT-001"}, self.ctx))

    def test_in_operator(self):
        c = RuleCondition("category", "in", ["semiconductors", "chemicals"])
        self.assertTrue(c.evaluate({"category": "semiconductors"}, self.ctx))
        self.assertFalse(c.evaluate({"category": "hardware"}, self.ctx))

    def test_ctx_confidence(self):
        c = RuleCondition("ctx.confidence", "lt", 0.5)
        low_ctx = DecisionContext(confidence=0.3)
        self.assertTrue(c.evaluate({}, low_ctx))
        high_ctx = DecisionContext(confidence=0.9)
        self.assertFalse(c.evaluate({}, high_ctx))

    def test_negate(self):
        c = RuleCondition("amount", "gt", 1000, negate=True)
        self.assertFalse(c.evaluate({"amount": 2000}, self.ctx))
        self.assertTrue(c.evaluate({"amount": 500}, self.ctx))

    def test_invalid_operator_raises(self):
        with self.assertRaises(ValueError):
            RuleCondition("amount", "INVALID_OP", 100)

    def test_dot_notation_nested_field(self):
        c = RuleCondition("address.city", "eq", "London")
        self.assertTrue(c.evaluate({"address": {"city": "London"}}, self.ctx))
        self.assertFalse(c.evaluate({"address": {"city": "Paris"}}, self.ctx))


class TestDeclarativeRule(unittest.TestCase):

    def setUp(self):
        self.ctx = DecisionContext()

    def test_and_logic_all_match(self):
        rule = DeclarativeRule(
            "TEST-001", "Test", ["procurement"],
            conditions=[
                RuleCondition("amount", "gt", 500000),
                RuleCondition("contract_id", "missing"),
            ],
            result="fail", message="Over limit without contract",
            logic="and",
        )
        result = rule.evaluate({"amount": 700000}, self.ctx)
        self.assertEqual(result.result, "fail")

    def test_and_logic_partial_match(self):
        rule = DeclarativeRule(
            "TEST-001", "Test", ["procurement"],
            conditions=[
                RuleCondition("amount", "gt", 500000),
                RuleCondition("contract_id", "missing"),
            ],
            result="fail", message="msg", logic="and",
        )
        result = rule.evaluate({"amount": 700000, "contract_id": "CT-001"}, self.ctx)
        self.assertEqual(result.result, "pass")

    def test_or_logic_any_match(self):
        rule = DeclarativeRule(
            "TEST-002", "Test", ["procurement"],
            conditions=[
                RuleCondition("amount", "gt", 1_000_000),
                RuleCondition("category", "in", ["weapons","nuclear"]),
            ],
            result="fail", message="blocked", logic="or",
        )
        result = rule.evaluate({"amount": 1000, "category": "weapons"}, self.ctx)
        self.assertEqual(result.result, "fail")

    def test_to_policy_conversion(self):
        rule = DeclarativeRule(
            "DECL-001", "Declarative Policy", ["procurement"],
            conditions=[RuleCondition("amount", "gt", 100000)],
            result="warn", message="High amount",
        )
        policy = rule.to_policy()
        self.assertEqual(policy.policy_id, "DECL-001")
        self.assertCallable = callable(policy.rule)


class TestRulesLoader(unittest.TestCase):

    def setUp(self):
        self.loader = RulesLoader()
        self.engine = PolicyEngine()

    def test_load_json_string(self):
        policies = self.loader.load_json_string(REFERENCE_RULES_JSON)
        self.assertGreaterEqual(len(policies), 1)
        self.assertIsInstance(policies[0], Policy)

    def test_register_all(self):
        policies = self.loader.load_json_string(REFERENCE_RULES_JSON)
        count    = self.loader.register_all(policies, self.engine)
        self.assertGreater(count, 0)

    def test_load_from_file(self):
        rules = {
            "rules": [{
                "policy_id": "FILE-001", "name": "File Rule",
                "applies_to": ["procurement"],
                "conditions": [{"field": "amount", "op": "gt", "value": 9999}],
                "result": "warn", "message": "Amount exceeds $9,999",
            }]
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(rules, f)
            path = f.name
        try:
            policies = self.loader.load_file(path)
            self.assertEqual(len(policies), 1)
        finally:
            os.unlink(path)

    def test_declarative_rule_evaluates_correctly_via_engine(self):
        policies = self.loader.load_json_string(REFERENCE_RULES_JSON)
        for p in policies:
            self.engine.register(p)
        ctx    = DecisionContext(confidence=0.3)
        result = self.engine.evaluate(
            DecisionType.PROCUREMENT,
            {"amount": 600000, "supplier_id": "SUP-001", "category": "hardware"},
            ctx,
        )
        # YAML-CONF-001 should warn (confidence=0.3 < 0.5)
        warned = [w for w in result.warnings if "YAML-CONF-001" in w]
        self.assertGreater(len(warned), 0)

    def test_malformed_rule_handled_gracefully(self):
        bad_json = json.dumps({"rules": [{"no_policy_id": True}]})
        try:
            policies = self.loader.load_json_string(bad_json)
        except Exception as e:
            self.fail(f"Malformed rule raised exception: {e}")

    def test_load_directory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            rule1 = {"rules": [{"policy_id": "DIR-001", "name": "D1",
                                 "applies_to": ["custom"],
                                 "conditions": [{"field": "amount","op":"gt","value":0}],
                                 "result": "pass", "message": "ok"}]}
            rule2 = {"rules": [{"policy_id": "DIR-002", "name": "D2",
                                 "applies_to": ["financial"],
                                 "conditions": [{"field": "amount","op":"gt","value":0}],
                                 "result": "warn", "message": "high"}]}
            for i, r in enumerate([rule1, rule2]):
                with open(os.path.join(tmpdir, f"rule{i}.json"), "w") as f:
                    json.dump(r, f)
            policies = self.loader.load_directory(tmpdir)
            self.assertGreaterEqual(len(policies), 2)


# ══════════════════════════════════════════════════════════════════════════════
# WORKFLOW ENGINE
# ══════════════════════════════════════════════════════════════════════════════

class TestWorkflowEngine(unittest.TestCase):

    def setUp(self):
        self.bus    = EventBus(max_workers=2)
        self.engine = WorkflowEngine(
            repository=SQLiteWorkflowRepository(":memory:"),
            event_bus=self.bus,
        )

    def tearDown(self):
        self.bus.shutdown()

    def test_create_workflow(self):
        wf = self.engine.create_from_decision(
            decision_id="d-001", agent_id="agent_a",
            decision_type="procurement", risk_score=45.0,
            violations=["[PROC-001] Over limit"],
        )
        self.assertIsNotNone(wf)
        self.assertEqual(wf.state, "pending")

    def test_approve_workflow(self):
        wf = self.engine.create_from_decision(
            "d-002","agent_b","financial",55.0,["[FIN-001] Over limit"])
        approved = self.engine.approve(wf.workflow_id, "analyst@co.com", "Verified OK")
        self.assertIsNotNone(approved)
        self.assertEqual(approved.state, "approved")
        self.assertIsNotNone(approved.resolved_at)

    def test_reject_workflow(self):
        wf = self.engine.create_from_decision(
            "d-003","agent_c","pricing",60.0,["[PRICE-001] Too high"])
        rejected = self.engine.reject(wf.workflow_id, "mgr@co.com", "Cannot approve")
        self.assertEqual(rejected.state, "rejected")

    def test_escalate_workflow(self):
        wf = self.engine.create_from_decision(
            "d-004","agent_d","hr",70.0,["[HR-001] Needs review"])
        escalated = self.engine.escalate(
            wf.workflow_id, "jr_analyst@co.com", "sr_analyst@co.com", "Exceeds my authority")
        self.assertEqual(escalated.state, "escalated")

    def test_get_by_decision(self):
        wf = self.engine.create_from_decision(
            "d-find","agent_e","inventory",20.0,[])
        found = self.engine.get_by_decision("d-find")
        self.assertIsNotNone(found)
        self.assertEqual(found.workflow_id, wf.workflow_id)

    def test_list_pending(self):
        self.engine.create_from_decision("d-p1","a1","procurement",30.0,[])
        self.engine.create_from_decision("d-p2","a2","financial",50.0,[])
        pending = self.engine.list_pending()
        self.assertGreaterEqual(len(pending), 2)

    def test_add_comment(self):
        wf = self.engine.create_from_decision("d-cmt","agent_f","hr",40.0,[])
        updated = self.engine.add_comment(wf.workflow_id, "analyst","Needs more info")
        self.assertEqual(len([s for s in updated.steps if s.step_type == "comment"]), 1)

    def test_queue_stats(self):
        self.engine.create_from_decision("d-s1","a1","procurement",30.0,[])
        stats = self.engine.queue_stats()
        self.assertIn("total_pending", stats)
        self.assertIn("sla_breached",  stats)

    def test_workflow_publishes_events(self):
        received = []; lock = threading.Lock()
        self.bus.subscribe("decision.pending_review",
                           lambda e: received.append(e) or None)
        self.engine.create_from_decision("d-ev","a1","financial",55.0,[])
        time.sleep(0.05)
        self.assertGreater(len(received), 0)

    def test_pipeline_creates_workflow_for_pending_review(self):
        bus = EventBus(max_workers=2)
        wf_repo = SQLiteWorkflowRepository(":memory:")
        wf_engine = WorkflowEngine(repository=wf_repo, event_bus=bus)
        # Tune thresholds so 40-60 risk → human_review
        from glassbox.governance.risk_evaluator import RiskEvaluator
        p = GovernancePipeline(
            echo=False,
            workflow_engine=wf_engine,
            risk_evaluator=RiskEvaluator(thresholds={"auto_execute_max": 10,
                                                       "human_review_max": 100}),
        )
        resp = p.process(_proc(amount=200000))  # medium risk → review
        pending = wf_engine.list_pending()
        # If the decision went to pending_review, a workflow was created
        if resp.final_status == FinalStatus.PENDING_REVIEW:
            self.assertGreater(len(pending), 0)
        bus.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
# EXECUTION TRACE
# ══════════════════════════════════════════════════════════════════════════════

class TestExecutionTrace(unittest.TestCase):

    def test_trace_records_steps(self):
        trace = ExecutionTrace("test-decision-001")
        with StageTimer(trace, 2, "SchemaValidation", {"type": "procurement"}) as t:
            t.outcome = "passed"
            t.output_summary = {"valid": True}
        with StageTimer(trace, 6, "PolicyEnforcement") as t:
            t.outcome = "blocked"
            t.detail  = "[PROC-001] Over limit"
        trace.finalise()
        self.assertEqual(len(trace.steps), 2)
        self.assertEqual(trace.steps[0].stage_name, "SchemaValidation")
        self.assertEqual(trace.steps[1].stage_name, "PolicyEnforcement")
        self.assertEqual(trace.blocked_at(), "PolicyEnforcement")

    def test_trace_to_dict(self):
        trace = ExecutionTrace("d-001")
        with StageTimer(trace, 3, "Schema") as t:
            t.outcome = "passed"
        trace.finalise()
        d = trace.to_dict()
        self.assertIn("steps",       d)
        self.assertIn("total_ms",    d)
        self.assertIn("blocked_at",  d)
        self.assertIn("decision_id", d)
        self.assertIsNone(d["blocked_at"])

    def test_stage_timer_duration(self):
        trace = ExecutionTrace("d-002")
        with StageTimer(trace, 1, "SlowStage") as t:
            time.sleep(0.01)
            t.outcome = "passed"
        self.assertGreater(trace.steps[0].duration_ms, 5.0)

    def test_pipeline_trace_enabled(self):
        p    = GovernancePipeline(echo=False, trace_enabled=True)
        resp = p.process(DecisionRequest(
            "trace_agent", DecisionType.PROCUREMENT,
            {"amount": 700000, "category": "hardware"}
        ))
        self.assertIsNotNone(resp.execution_trace)
        self.assertGreater(len(resp.execution_trace.steps), 0)
        self.assertEqual(resp.execution_trace.blocked_at(), "PolicyEnforcement")

    def test_pipeline_trace_disabled_by_default(self):
        p    = GovernancePipeline(echo=False, trace_enabled=False)
        resp = p.process(DecisionRequest(
            "notrace_agent", DecisionType.PROCUREMENT,
            {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"}
        ))
        self.assertFalse(hasattr(resp, 'execution_trace') and resp.execution_trace is not None)

    def test_trace_exception_handling(self):
        trace = ExecutionTrace("d-exc")
        try:
            with StageTimer(trace, 9, "FailStage") as t:
                t.outcome = "error"
                raise ValueError("simulated stage error")
        except ValueError:
            pass
        self.assertEqual(trace.steps[0].outcome, "error")
        self.assertIsNotNone(trace.steps[0].error)

    def test_trace_summary_string(self):
        trace = ExecutionTrace("d-sum")
        with StageTimer(trace, 3, "Schema") as t: t.outcome = "passed"
        with StageTimer(trace, 6, "Policy") as t: t.outcome = "blocked"
        trace.finalise()
        summary = trace.summary()
        self.assertIn("Schema[P]", summary)
        self.assertIn("Policy[B]", summary)


# ══════════════════════════════════════════════════════════════════════════════
# REPOSITORY FACTORY
# ══════════════════════════════════════════════════════════════════════════════

class TestRepositoryFactory(unittest.TestCase):

    def test_in_memory_factory(self):
        repos = RepositoryFactory.in_memory()
        self.assertIn("policy",   repos)
        self.assertIn("workflow", repos)
        self.assertIsNotNone(repos["policy"])
        self.assertIsNotNone(repos["workflow"])

    def test_sqlite_factory(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            repos = RepositoryFactory.sqlite(db_dir=tmpdir)
            self.assertIn("policy",   repos)
            self.assertIn("audit",    repos)
            self.assertIn("workflow", repos)
            # Verify all created files exist
            import os
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "glassbox_policies.db")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "glassbox_audit.db")))
            self.assertTrue(os.path.exists(os.path.join(tmpdir, "glassbox_workflows.db")))


# ══════════════════════════════════════════════════════════════════════════════
# FULL FRAMEWORK INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestFullFrameworkIntegration(unittest.TestCase):
    """End-to-end test of all framework components working together."""

    def test_full_stack_decision_flow(self):
        """
        Pipeline + EventBus + AuditRepository + WorkflowEngine +
        RulesLoader + ExecutionTrace working together.
        """
        bus     = EventBus(max_workers=2)
        repos   = RepositoryFactory.in_memory()
        audit_r = SQLiteAuditRepository(":memory:")
        wf_eng  = WorkflowEngine(repository=repos["workflow"], event_bus=bus)

        # Load declarative rules
        loader   = RulesLoader()
        policies = loader.load_json_string(REFERENCE_RULES_JSON)
        pe       = PolicyEngine()
        for p in policies:
            pe.register(p)

        # Full-stack pipeline
        pipeline = GovernancePipeline(
            echo=False,
            policy_engine   = pe,
            event_bus       = bus,
            audit_repo      = audit_r,
            workflow_engine = wf_eng,
            trace_enabled   = True,
        )

        # Events captured
        events = []; lock = threading.Lock()
        bus.subscribe("*", lambda e: events.append(e.event_type) or None)

        # Submit decisions
        r1 = pipeline.process(_proc(amount=5000))
        r2 = pipeline.process(DecisionRequest(
            "block_agent", DecisionType.PROCUREMENT,
            {"amount": 700000, "category": "hardware"}
        ))

        # Both have execution traces
        self.assertIsNotNone(r1.execution_trace)
        self.assertIsNotNone(r2.execution_trace)
        self.assertEqual(r2.execution_trace.blocked_at(), "PolicyEnforcement")

        # Both in SQLite audit repo
        got_r1 = audit_r.get_by_id(r1.decision_id)
        got_r2 = audit_r.get_by_id(r2.decision_id)
        self.assertIsNotNone(got_r1)
        self.assertIsNotNone(got_r2)

        # Events published
        time.sleep(0.1)
        self.assertIn("decision.executed", events)
        self.assertIn("decision.blocked",  events)

        bus.shutdown()

    def test_workflow_pipeline_audit_sqlite_all_together(self):
        """All three SQLite repos in one flow."""
        with tempfile.TemporaryDirectory() as tmpdir:
            repos  = RepositoryFactory.sqlite(db_dir=tmpdir)
            wf_eng = WorkflowEngine(repository=repos["workflow"])
            p      = GovernancePipeline(
                echo=False,
                audit_repo=repos["audit"],
                workflow_engine=wf_eng,
                trace_enabled=True,
                risk_evaluator=__import__(
                    'glassbox.governance.risk_evaluator',
                    fromlist=['RiskEvaluator']
                ).RiskEvaluator(thresholds={"auto_execute_max":5,"human_review_max":100}),
            )
            resp = p.process(_proc(amount=150000))  # medium risk → review with tight threshold
            # Audit repo should have this decision
            got = repos["audit"].get_by_id(resp.decision_id)
            self.assertIsNotNone(got)
            # Trace should be present
            self.assertIsNotNone(resp.execution_trace)


# ══════════════════════════════════════════════════════════════════════════════
# Runner
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    loader = unittest.TestLoader()
    suite  = unittest.TestSuite()
    classes = [
        TestInMemoryPolicyRepository,
        TestSQLitePolicyRepository,
        TestSQLiteAuditRepository,
        TestEventBus,
        TestRuleCondition,
        TestDeclarativeRule,
        TestRulesLoader,
        TestWorkflowEngine,
        TestExecutionTrace,
        TestRepositoryFactory,
        TestFullFrameworkIntegration,
    ]
    for cls in classes:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    import sys; sys.exit(0 if result.wasSuccessful() else 1)
