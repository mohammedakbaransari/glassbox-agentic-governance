"""
GlassBox — OPA Rego Policy Adapter  (v1.0.0)
=============================================
Integrates Open Policy Agent (OPA) Rego policies with GlassBox.

Large enterprises — especially in financial services, healthcare, and
government — use OPA as their centralised policy engine. This adapter
lets those organisations write GlassBox policies in OPA Rego rather than
Python, maintaining a single policy authoring standard across their stack.

Two modes:
  1. OPA HTTP server mode (default): sends decisions to a running OPA
     server via HTTP. Works with any OPA deployment.
  2. Bundle evaluation mode: evaluates a Rego bundle file locally using
     the OPA CLI (requires opa binary on PATH). Useful for CI/CD.

Usage:
    # Mode 1: OPA HTTP server
    from glassbox.integrations.opa_adapter import OPARegoAdapter

    adapter = OPARegoAdapter(
        opa_url    = "http://localhost:8181",
        policy_path = "glassbox/procurement",
        rule_name  = "deny",
    )
    pipeline = GovernancePipeline()
    pipeline.policy_engine.register(adapter.as_policy(
        policy_id="OPA-PROC-001",
        policy_name="OPA Procurement Policy",
        decision_types=[DecisionType.PROCUREMENT],
    ))

    # Mode 2: OPA bundle file (no server needed)
    adapter = OPARegoAdapter.from_bundle("/path/to/bundle.tar.gz")

Example Rego policy (procurement.rego):
    package glassbox.procurement

    import future.keywords.if
    import future.keywords.in

    default deny = false

    deny if {
        input.payload.amount > 500000
        not input.payload.contract_id
    }

    violation[msg] if {
        deny
        msg := sprintf("Amount %v exceeds $500K limit without contract_id", [input.payload.amount])
    }

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
import subprocess
import tempfile
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.models import DecisionContext, DecisionType, PolicyEvaluation
from glassbox.governance.policy_engine import Policy

if TYPE_CHECKING:
    pass


class OPARegoAdapter:
    """
    Evaluates OPA Rego policies and returns GlassBox PolicyEvaluation results.

    Translates a GlassBox (payload, context) call into the OPA input document:
        {
          "payload": {...},       <- decision payload
          "context": {            <- DecisionContext fields
            "confidence": 0.9,
            "environment": "production",
            "agent_chain": [...],
            "currency": "USD",
            "jurisdiction": "US"
          }
        }

    The OPA policy must return a "deny" boolean and optionally a "violation"
    set of strings. GlassBox uses "deny" for fail/pass and "violation" for
    the violation message.
    """

    def __init__(
        self,
        opa_url:      Optional[str] = None,   # e.g. "http://localhost:8181"
        policy_path:  str           = "glassbox/policy",
        rule_name:    str           = "deny",
        timeout_s:    float         = 1.0,    # HTTP timeout — governance cannot block on OPA
        fallback:     str           = "pass", # "pass" or "fail" if OPA unreachable
    ):
        self._opa_url     = opa_url.rstrip("/") if opa_url else None
        self._policy_path = policy_path.strip("/")
        self._rule        = rule_name
        self._timeout     = timeout_s
        self._fallback    = fallback
        self._lock        = threading.Lock()
        self._failure_count = 0

    @classmethod
    def from_bundle(cls, bundle_path: str, **kwargs) -> "OPARegoAdapter":
        """Create an adapter that evaluates a local Rego bundle file using the OPA CLI."""
        adapter = cls(**kwargs)
        adapter._bundle_path = bundle_path
        return adapter

    def evaluate(self, payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
        """
        Evaluate the OPA policy against a decision payload and context.
        This method matches the Policy.rule signature.
        """
        input_doc = self._build_input(payload, ctx)

        if self._opa_url:
            return self._evaluate_http(input_doc)
        elif hasattr(self, "_bundle_path"):
            return self._evaluate_cli(input_doc)
        else:
            # No OPA configured — pass through (used in testing)
            return PolicyEvaluation("OPA", "OPA Policy", "pass",
                                    "OPA not configured — evaluation skipped")

    def as_policy(
        self,
        policy_id:      str,
        policy_name:    str,
        decision_types: List[DecisionType],
    ) -> Policy:
        """Return a GlassBox Policy object backed by this OPA adapter."""
        return Policy(
            policy_id=policy_id,
            policy_name=policy_name,
            decision_types=decision_types,
            rule=self.evaluate,
            description=f"OPA Rego policy: {self._policy_path}/{self._rule}",
        )

    # ── Private evaluation methods ────────────────────────────────────────────

    def _build_input(self, payload: Dict, ctx: DecisionContext) -> Dict:
        return {
            "payload": payload,
            "context": {
                "confidence":   ctx.confidence,
                "environment":  ctx.environment,
                "agent_chain":  ctx.agent_chain,
                "user_override":ctx.user_override,
                "currency":     getattr(ctx, "currency", "USD"),
                "jurisdiction": getattr(ctx, "jurisdiction", "US"),
                "source_system":ctx.source_system,
            }
        }

    def _evaluate_http(self, input_doc: Dict) -> PolicyEvaluation:
        """Call the OPA HTTP API: POST /v1/data/{policy_path}"""
        url = f"{self._opa_url}/v1/data/{self._policy_path}"
        body = json.dumps({"input": input_doc}).encode()

        try:
            req  = urllib.request.Request(
                url, data=body,
                headers={"Content-Type": "application/json"},
                method="POST"
            )
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                result = json.loads(resp.read().decode())

            result_data = result.get("result", {})
            deny        = bool(result_data.get(self._rule, False))
            violations  = list(result_data.get("violation", []))

            with self._lock:
                self._failure_count = 0   # reset on success

            if deny:
                msg = violations[0] if violations else f"OPA policy '{self._rule}' denied this decision"
                return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "fail",
                                        f"[OPA] {msg}")
            return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "pass", "OPA: allowed")

        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            with self._lock:
                self._failure_count += 1
            # Apply fallback policy when OPA is unreachable
            if self._fallback == "fail":
                return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "fail",
                    f"[OPA] Unreachable (fail-closed): {exc}")
            return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "warn",
                f"[OPA] Unreachable (fail-open) — evaluation skipped: {exc}")

    def _evaluate_cli(self, input_doc: Dict) -> PolicyEvaluation:
        """Evaluate using the local OPA CLI binary."""
        try:
            with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
                json.dump({"input": input_doc}, f)
                input_file = f.name

            result = subprocess.run(
                ["opa", "eval",
                 "--data", self._bundle_path,
                 "--input", input_file,
                 f"data.{self._policy_path}.{self._rule}",
                 "--format", "json"],
                capture_output=True, text=True, timeout=self._timeout
            )
            if result.returncode != 0:
                raise RuntimeError(f"OPA CLI error: {result.stderr}")

            data = json.loads(result.stdout)
            deny = bool(data.get("result", [{}])[0].get("expressions", [{}])[0].get("value", False))

            if deny:
                return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "fail",
                    f"[OPA] Policy '{self._rule}' denied this decision (CLI evaluation)")
            return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "pass", "OPA: allowed")

        except Exception as exc:
            if self._fallback == "fail":
                return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "fail",
                    f"[OPA] CLI evaluation failed (fail-closed): {exc}")
            return PolicyEvaluation("OPA", f"OPA:{self._policy_path}", "warn",
                f"[OPA] CLI evaluation failed (fail-open): {exc}")

    @property
    def failure_count(self) -> int:
        """Number of consecutive OPA evaluation failures (for monitoring)."""
        with self._lock:
            return self._failure_count

    def health_check(self) -> Dict[str, Any]:
        """Ping the OPA server and return health status."""
        if not self._opa_url:
            return {"status": "no_server", "mode": "cli_or_passthrough"}
        try:
            url = f"{self._opa_url}/health"
            with urllib.request.urlopen(url, timeout=2.0) as resp:
                return {"status": "ok", "url": self._opa_url,
                        "http_status": resp.status}
        except Exception as e:
            return {"status": "error", "url": self._opa_url, "error": str(e)}
