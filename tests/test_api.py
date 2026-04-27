"""
GlassBox — Flask HTTP-Level API Tests  (T-3)

End-to-end tests using Flask's test client. These cover serialization,
auth enforcement, rate-limiting headers, body-size limits, CORS, the /v1/
prefix, the Prometheus /metrics endpoint, and the OpenAPI spec endpoint.

Run: python -m pytest tests/test_api.py -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from glassbox.api.app import create_app
from glassbox.governance.models import DecisionType
from glassbox.governance.pipeline import GovernancePipeline


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app(**kw):
    """Create a test Flask app with auth and tenant-scoping disabled."""
    pipeline = GovernancePipeline(echo=False, environment="testing")
    return create_app(pipeline=pipeline, testing=True, auth_required=False, **kw)


def _proc_body(agent="api-test-agent", amount=5000, dtype="procurement"):
    return {
        "agent_id": agent,
        "decision_type": dtype,
        "payload": {"amount": amount, "supplier_id": "SUP-001", "category": "hardware"},
    }


# ══════════════════════════════════════════════════════════════════════════════
# 1. HEALTH & READINESS
# ══════════════════════════════════════════════════════════════════════════════

class TestHealthReady(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_health_returns_200(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_health_body_has_status_healthy(self):
        resp = self.client.get("/health")
        data = resp.get_json()
        self.assertEqual(data["status"], "healthy")

    def test_health_version_is_not_hardcoded_old(self):
        resp = self.client.get("/health")
        data = resp.get_json()
        self.assertNotEqual(data.get("version"), "1.0.0", "version must not be the old hardcoded value")

    def test_ready_returns_200(self):
        resp = self.client.get("/ready")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json().get("ready"))

    def test_health_has_glassbox_version_header(self):
        resp = self.client.get("/health")
        self.assertIn("X-GlassBox-Version", resp.headers)

    def test_health_has_security_headers(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.headers.get("X-Content-Type-Options"), "nosniff")
        self.assertEqual(resp.headers.get("X-Frame-Options"), "DENY")


# ══════════════════════════════════════════════════════════════════════════════
# 2. SUBMIT DECISION (POST /decisions)
# ══════════════════════════════════════════════════════════════════════════════

class TestSubmitDecision(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def _post(self, body, content_type="application/json"):
        return self.client.post(
            "/decisions",
            data=json.dumps(body),
            content_type=content_type,
        )

    def test_valid_decision_returns_200(self):
        resp = self._post(_proc_body())
        self.assertEqual(resp.status_code, 200)

    def test_response_has_decision_id(self):
        resp = self._post(_proc_body())
        data = resp.get_json()
        self.assertIn("decision_id", data)

    def test_response_has_final_status(self):
        resp = self._post(_proc_body())
        data = resp.get_json()
        self.assertIn(data["final_status"], ["executed", "blocked", "pending_review"])

    def test_response_has_pipeline_latency(self):
        resp = self._post(_proc_body())
        data = resp.get_json()
        self.assertIsNotNone(data.get("pipeline_latency_ms"))

    def test_missing_agent_id_returns_422(self):
        body = _proc_body()
        del body["agent_id"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 422)

    def test_missing_decision_type_returns_422(self):
        body = _proc_body()
        del body["decision_type"]
        resp = self._post(body)
        self.assertEqual(resp.status_code, 422)

    def test_invalid_decision_type_returns_422(self):
        body = _proc_body()
        body["decision_type"] = "not_a_real_type"
        resp = self._post(body)
        self.assertEqual(resp.status_code, 422)

    def test_missing_payload_returns_422(self):
        body = {"agent_id": "a", "decision_type": "procurement", "payload": {}}
        resp = self._post(body)
        self.assertEqual(resp.status_code, 422)

    def test_user_override_in_body_returns_422(self):
        body = _proc_body()
        body["context"] = {"user_override": True}
        resp = self._post(body)
        self.assertEqual(resp.status_code, 422)
        self.assertIn("user_override", resp.get_json().get("error", ""))

    def test_non_json_body_returns_400(self):
        resp = self.client.post("/decisions", data="not-json", content_type="text/plain")
        self.assertEqual(resp.status_code, 400)

    def test_sql_injection_in_payload_is_blocked(self):
        body = _proc_body()
        body["payload"]["supplier_id"] = "' OR 1=1 --"
        resp = self._post(body)
        data = resp.get_json()
        # Pipeline should block, not crash
        self.assertIn(resp.status_code, [200])
        self.assertEqual(data["final_status"], "blocked")

    def test_response_has_no_implementation_details(self):
        resp = self._post(_proc_body())
        data = resp.get_json()
        # Should not leak "in-memory audit records" note in single-decision responses
        self.assertNotIn("in-memory", json.dumps(data))

    def test_all_decision_types_accepted(self):
        types = [t.value for t in DecisionType]
        for dtype in types:
            body = {
                "agent_id": "type-test-agent",
                "decision_type": dtype,
                "payload": {"amount": 100, "action": "test", "target": "svc",
                            "new_price": 10.0, "quantity": 1, "notional": 100},
            }
            resp = self._post(body)
            self.assertIn(resp.status_code, [200], f"Failed for decision_type={dtype}")


# ══════════════════════════════════════════════════════════════════════════════
# 3. BODY SIZE LIMIT
# ══════════════════════════════════════════════════════════════════════════════

class TestBodySizeLimit(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_oversized_single_decision_rejected(self):
        # Build a payload just over 8 KB
        body = _proc_body()
        body["payload"]["padding"] = "x" * (9 * 1024)
        resp = self.client.post(
            "/decisions",
            data=json.dumps(body),
            content_type="application/json",
            headers={"Content-Length": str(len(json.dumps(body)))},
        )
        # Flask rejects at 413 before our handler; or our handler catches it
        self.assertIn(resp.status_code, [413, 422])

    def test_batch_accepts_multiple_decisions(self):
        decisions = [_proc_body(agent=f"agent-{i}") for i in range(10)]
        resp = self.client.post(
            "/decisions/batch",
            data=json.dumps({"decisions": decisions}),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data["summary"]["total"], 10)


# ══════════════════════════════════════════════════════════════════════════════
# 4. AUTH ENFORCEMENT
# ══════════════════════════════════════════════════════════════════════════════

class TestAuthEnforcement(unittest.TestCase):
    def setUp(self):
        os.environ["GLASSBOX_API_KEY"] = "test-key-secret"
        pipeline    = GovernancePipeline(echo=False, environment="testing")
        self.app    = create_app(pipeline=pipeline, testing=False, auth_required=True)
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop("GLASSBOX_API_KEY", None)

    def test_unauthenticated_request_rejected(self):
        resp = self.client.post(
            "/decisions",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        self.assertIn(resp.status_code, [401, 403])

    def test_health_does_not_require_auth(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)

    def test_ready_does_not_require_auth(self):
        resp = self.client.get("/ready")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_does_not_require_auth(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# 5. SIMULATE ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

class TestSimulate(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_simulate_returns_200(self):
        resp = self.client.post(
            "/decisions/simulate",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_simulate_marks_as_simulation(self):
        resp = self.client.post(
            "/decisions/simulate",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        data = resp.get_json()
        self.assertTrue(data.get("simulation"))

    def test_simulate_has_note_about_no_audit(self):
        resp = self.client.post(
            "/decisions/simulate",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        data = resp.get_json()
        self.assertIn("simulated", data.get("note", "").lower())


# ══════════════════════════════════════════════════════════════════════════════
# 6. PROMETHEUS METRICS
# ══════════════════════════════════════════════════════════════════════════════

class TestMetricsEndpoint(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_metrics_returns_200(self):
        resp = self.client.get("/metrics")
        self.assertEqual(resp.status_code, 200)

    def test_metrics_content_type_is_plain_text(self):
        resp = self.client.get("/metrics")
        self.assertIn("text/plain", resp.content_type)

    def test_metrics_contains_glassbox_decisions_total(self):
        # Submit a decision first
        self.client.post("/decisions", data=json.dumps(_proc_body()), content_type="application/json")
        resp = self.client.get("/metrics")
        self.assertIn("glassbox_decisions_total", resp.data.decode())

    def test_metrics_contains_block_rate(self):
        resp = self.client.get("/metrics")
        self.assertIn("glassbox_block_rate_pct", resp.data.decode())

    def test_metrics_contains_policies_active(self):
        resp = self.client.get("/metrics")
        self.assertIn("glassbox_policies_active", resp.data.decode())

    def test_metrics_prometheus_format_has_help_and_type(self):
        resp = self.client.get("/metrics")
        text = resp.data.decode()
        self.assertIn("# HELP", text)
        self.assertIn("# TYPE", text)


# ══════════════════════════════════════════════════════════════════════════════
# 7. OPENAPI SPEC
# ══════════════════════════════════════════════════════════════════════════════

class TestOpenAPISpec(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_openapi_returns_200(self):
        resp = self.client.get("/openapi.json")
        self.assertEqual(resp.status_code, 200)

    def test_openapi_is_valid_json(self):
        resp = self.client.get("/openapi.json")
        data = resp.get_json()
        self.assertIsNotNone(data)

    def test_openapi_version_is_3(self):
        resp = self.client.get("/openapi.json")
        data = resp.get_json()
        self.assertTrue(data.get("openapi", "").startswith("3."))

    def test_openapi_has_decisions_path(self):
        resp = self.client.get("/openapi.json")
        data = resp.get_json()
        self.assertIn("/decisions", data.get("paths", {}))

    def test_openapi_info_title_present(self):
        resp = self.client.get("/openapi.json")
        data = resp.get_json()
        self.assertIn("title", data.get("info", {}))


# ══════════════════════════════════════════════════════════════════════════════
# 8. /v1/ BLUEPRINT PREFIX
# ══════════════════════════════════════════════════════════════════════════════

class TestV1Blueprint(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_v1_health_returns_200(self):
        resp = self.client.get("/v1/health")
        self.assertEqual(resp.status_code, 200)

    def test_v1_ready_returns_200(self):
        resp = self.client.get("/v1/ready")
        self.assertEqual(resp.status_code, 200)

    def test_v1_submit_decision_returns_200(self):
        resp = self.client.post(
            "/v1/decisions",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        self.assertEqual(resp.status_code, 200)

    def test_v1_policies_returns_200(self):
        resp = self.client.get("/v1/policies")
        self.assertEqual(resp.status_code, 200)

    def test_v1_ecosystem_returns_200(self):
        resp = self.client.get("/v1/ecosystem")
        self.assertEqual(resp.status_code, 200)

    def test_v1_contracts_returns_200(self):
        resp = self.client.get("/v1/contracts")
        self.assertEqual(resp.status_code, 200)

    def test_root_routes_still_work(self):
        """Backward compat: root routes must remain available."""
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# 9. POLICIES / CONTRACTS / ECOSYSTEM
# ══════════════════════════════════════════════════════════════════════════════

class TestPoliciesContractsEcosystem(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_policies_returns_200(self):
        resp = self.client.get("/policies")
        self.assertEqual(resp.status_code, 200)

    def test_policies_returns_list(self):
        resp = self.client.get("/policies")
        data = resp.get_json()
        self.assertIn("policies", data)
        self.assertIsInstance(data["policies"], list)

    def test_policies_not_empty(self):
        resp = self.client.get("/policies")
        data = resp.get_json()
        self.assertGreater(len(data["policies"]), 0)

    def test_contracts_returns_200(self):
        resp = self.client.get("/contracts")
        self.assertEqual(resp.status_code, 200)

    def test_ecosystem_returns_200(self):
        resp = self.client.get("/ecosystem")
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# 10. VELOCITY & ANOMALY ENDPOINTS
# ══════════════════════════════════════════════════════════════════════════════

class TestVelocityAnomalyEndpoints(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_velocity_returns_200(self):
        resp = self.client.get("/agents/test-agent/velocity")
        self.assertEqual(resp.status_code, 200)

    def test_velocity_invalid_agent_returns_400(self):
        resp = self.client.get("/agents/" + "x" * 200 + "/velocity")
        self.assertEqual(resp.status_code, 400)

    def test_anomaly_without_decision_type_returns_400(self):
        resp = self.client.get("/agents/test-agent/anomaly")
        self.assertEqual(resp.status_code, 400)

    def test_anomaly_with_decision_type_returns_200(self):
        resp = self.client.get("/agents/test-agent/anomaly?decision_type=procurement")
        self.assertEqual(resp.status_code, 200)


# ══════════════════════════════════════════════════════════════════════════════
# 11. BATCH ENDPOINT
# ══════════════════════════════════════════════════════════════════════════════

class TestBatchEndpoint(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def _batch(self, decisions, **extra):
        body = {"decisions": decisions, **extra}
        return self.client.post(
            "/decisions/batch",
            data=json.dumps(body),
            content_type="application/json",
        )

    def test_batch_single_decision_returns_200(self):
        resp = self._batch([_proc_body()])
        self.assertEqual(resp.status_code, 200)

    def test_batch_summary_totals_match(self):
        resp = self._batch([_proc_body(agent=f"batch-{i}") for i in range(5)])
        data = resp.get_json()
        self.assertEqual(data["summary"]["total"], 5)

    def test_batch_over_500_returns_400(self):
        resp = self._batch([_proc_body() for _ in range(501)])
        self.assertEqual(resp.status_code, 400)

    def test_batch_empty_list_returns_400(self):
        resp = self._batch([])
        self.assertEqual(resp.status_code, 400)

    def test_batch_invalid_entry_captured_as_error(self):
        decisions = [_proc_body(), {"agent_id": "", "decision_type": "procurement", "payload": {"amount": 1}}]
        resp = self._batch(decisions)
        data = resp.get_json()
        self.assertGreater(len(data["errors"]), 0)


# ══════════════════════════════════════════════════════════════════════════════
# 12. DECISION LOOKUP / REPLAY
# ══════════════════════════════════════════════════════════════════════════════

class TestDecisionLookup(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_get_nonexistent_decision_returns_404(self):
        resp = self.client.get("/decisions/00000000-0000-0000-0000-000000000000")
        self.assertEqual(resp.status_code, 404)

    def test_get_decision_invalid_uuid_returns_400(self):
        resp = self.client.get("/decisions/not-a-uuid")
        self.assertEqual(resp.status_code, 400)

    def test_replay_nonexistent_decision_returns_404(self):
        resp = self.client.post("/decisions/00000000-0000-0000-0000-000000000000/replay")
        self.assertEqual(resp.status_code, 404)

    def test_submit_then_lookup_succeeds(self):
        # Submit a decision
        post_resp = self.client.post(
            "/decisions",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        self.assertEqual(post_resp.status_code, 200)
        decision_id = post_resp.get_json()["decision_id"]

        # Look it up via in-memory logger
        get_resp = self.client.get(f"/decisions/{decision_id}")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.get_json()["decision_id"], decision_id)

    def test_submit_then_replay_succeeds(self):
        post_resp = self.client.post(
            "/decisions",
            data=json.dumps(_proc_body()),
            content_type="application/json",
        )
        decision_id = post_resp.get_json()["decision_id"]
        replay_resp = self.client.post(f"/decisions/{decision_id}/replay")
        self.assertEqual(replay_resp.status_code, 200)
        data = replay_resp.get_json()
        self.assertIn("original_id", data)
        self.assertIn("replayed", data)
        self.assertIn("outcome_changed", data)


# ══════════════════════════════════════════════════════════════════════════════
# 13. STATS
# ══════════════════════════════════════════════════════════════════════════════

class TestStats(unittest.TestCase):
    def setUp(self):
        self.app    = _make_app()
        self.client = self.app.test_client()

    def test_stats_returns_200(self):
        resp = self.client.get("/stats")
        self.assertEqual(resp.status_code, 200)

    def test_stats_has_total_field(self):
        resp = self.client.get("/stats")
        self.assertIn("total", resp.get_json())


# ══════════════════════════════════════════════════════════════════════════════
# 14. CORS HEADERS
# ══════════════════════════════════════════════════════════════════════════════

class TestCORSHeaders(unittest.TestCase):
    def setUp(self):
        os.environ["GLASSBOX_CORS_ORIGINS"] = "https://dashboard.example.com,https://admin.example.com"
        self.app    = _make_app()
        self.client = self.app.test_client()

    def tearDown(self):
        os.environ.pop("GLASSBOX_CORS_ORIGINS", None)

    def test_allowed_origin_gets_cors_header(self):
        resp = self.client.get(
            "/health",
            headers={"Origin": "https://dashboard.example.com"},
        )
        self.assertIn("Access-Control-Allow-Origin", resp.headers)

    def test_disallowed_origin_no_cors_header(self):
        resp = self.client.get(
            "/health",
            headers={"Origin": "https://evil.example.com"},
        )
        self.assertNotIn("Access-Control-Allow-Origin", resp.headers)

    def test_preflight_options_accepted(self):
        resp = self.client.options(
            "/decisions",
            headers={"Origin": "https://dashboard.example.com"},
        )
        self.assertIn(resp.status_code, [200, 204])


if __name__ == "__main__":
    unittest.main()
