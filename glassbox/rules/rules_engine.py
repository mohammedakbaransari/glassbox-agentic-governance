"""
GlassBox Framework — Declarative Rules Engine  (v1.0.0)
========================================================
Defines and evaluates governance rules using a declarative format
that non-developers can author, version, and deploy.

Two rule formats are supported:

  1. YAML/JSON Rules (declarative, no-code)
     Condition-action rules that governance teams write without Python.
     Example:
       policy_id: PROC-001
       name: Procurement Spending Limit
       applies_to: [procurement]
       conditions:
         - field: amount
           op: gt
           value: 500000
         - field: contract_id
           op: missing
       result: fail
       message: "Amount {amount} exceeds $500K limit without contract_id"

  2. Python Callable Rules (code, for complex logic)
     The existing Policy.rule callable pattern — fully supported.

The RulesEngine wraps both formats behind one consistent evaluate() API.
All loaded YAML rules are compiled to Policy objects and registered in the
PolicyEngine, so the pipeline never changes.

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from glassbox.governance.models import (
    DecisionContext, DecisionType, PolicyEvaluation,
)
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.logging_manager import get_logger

log = get_logger("rules_engine")


# ── Supported operators ───────────────────────────────────────────────────────

_OPS: Dict[str, Callable] = {
    "gt":          lambda v, t: float(v or 0) > float(t),
    "gte":         lambda v, t: float(v or 0) >= float(t),
    "lt":          lambda v, t: float(v or 0) < float(t),
    "lte":         lambda v, t: float(v or 0) <= float(t),
    "eq":          lambda v, t: str(v or "").strip().upper() == str(t).strip().upper(),
    "neq":         lambda v, t: str(v or "").strip().upper() != str(t).strip().upper(),
    "in":          lambda v, t: str(v or "").strip().upper() in [x.strip().upper() for x in (t if isinstance(t, list) else [t])],
    "not_in":      lambda v, t: str(v or "").strip().upper() not in [x.strip().upper() for x in (t if isinstance(t, list) else [t])],
    "missing":     lambda v, t: not v,
    "present":     lambda v, t: bool(v),
    "contains":    lambda v, t: str(t).lower() in str(v or "").lower(),
    "startswith":  lambda v, t: str(v or "").lower().startswith(str(t).lower()),
    "regex":       lambda v, t: bool(re.search(str(t), str(v or ""), re.IGNORECASE)),
}


# ── Rule definition ───────────────────────────────────────────────────────────

class RuleCondition:
    """
    A single condition in a declarative rule.

    field:    payload field name (dot-notation for nested: "address.city")
              or "ctx.confidence", "ctx.environment" for context fields
    op:       comparison operator (see _OPS above)
    value:    comparison target (not used for "missing"/"present")
    negate:   if True, invert the result
    """

    def __init__(self, field: str, op: str, value: Any = None, negate: bool = False):
        self.field  = field
        self.op     = op
        self.value  = value
        self.negate = negate
        if op not in _OPS:
            raise ValueError(f"Unknown operator '{op}'. Valid: {sorted(_OPS)}")

    def evaluate(self, payload: Dict[str, Any], ctx: DecisionContext) -> bool:
        # Resolve field value — support ctx.* for context fields
        if self.field.startswith("ctx."):
            attr = self.field[4:]
            val = getattr(ctx, attr, None)
        else:
            # Dot-notation for nested payload fields
            val = payload
            for part in self.field.split("."):
                if isinstance(val, dict):
                    val = val.get(part)
                else:
                    val = None
                    break

        result = _OPS[self.op](val, self.value)
        return (not result) if self.negate else result


class DeclarativeRule:
    """
    A declarative governance rule compiled from YAML/JSON.

    Conditions are joined by the logic operator ("and" or "or").
    When all conditions match (AND) or any condition matches (OR),
    the rule fires and returns the specified result.
    """

    def __init__(
        self,
        policy_id:     str,
        policy_name:   str,
        applies_to:    List[str],
        conditions:    List[RuleCondition],
        result:        str,               # "fail" | "warn" | "pass"
        message:       str,
        logic:         str = "and",      # "and" | "or"
        description:   str = "",
        version:       str = "1.0",
        enabled:       bool = True,
    ):
        self.policy_id   = policy_id
        self.policy_name = policy_name
        self.applies_to  = [DecisionType(t.lower()) for t in applies_to]
        self.conditions  = conditions
        self.result      = result
        self.message     = message
        self.logic       = logic
        self.description = description
        self.version     = version
        self.enabled     = enabled

    def evaluate(self, payload: Dict[str, Any], ctx: DecisionContext) -> PolicyEvaluation:
        if not self.conditions:
            return PolicyEvaluation(self.policy_id, self.policy_name, "pass", "No conditions")

        results = [c.evaluate(payload, ctx) for c in self.conditions]

        if self.logic == "or":
            fired = any(results)
        else:   # "and"
            fired = all(results)

        if fired:
            # Interpolate {field} tokens in message
            try:
                msg = self.message.format(**payload,
                                          confidence=ctx.confidence,
                                          environment=ctx.environment)
            except (KeyError, ValueError):
                msg = self.message
            # Prefix message with [policy_id] for consistent searchability
            prefixed = f"[{self.policy_id}] {msg}"
            return PolicyEvaluation(self.policy_id, self.policy_name, self.result, prefixed)

        return PolicyEvaluation(self.policy_id, self.policy_name, "pass",
                                f"{self.policy_name}: conditions not met")

    def to_policy(self) -> "Policy":
        """Convert to a Policy object compatible with PolicyEngine."""
        rule = self.evaluate  # bound method
        return Policy(
            policy_id=self.policy_id,
            policy_name=self.policy_name,
            decision_types=self.applies_to,
            rule=rule,
            enabled=self.enabled,
            description=self.description,
            version=self.version,
        )


# ── YAML/JSON Loader ──────────────────────────────────────────────────────────

def _parse_rule_dict(d: Dict[str, Any]) -> DeclarativeRule:
    """Parse one rule definition dict into a DeclarativeRule."""
    raw_conditions = d.get("conditions", [])
    conditions = []
    for c in raw_conditions:
        conditions.append(RuleCondition(
            field  = c["field"],
            op     = c["op"],
            value  = c.get("value"),
            negate = bool(c.get("negate", False)),
        ))

    return DeclarativeRule(
        policy_id   = d["policy_id"],
        policy_name = d.get("name", d["policy_id"]),
        applies_to  = d.get("applies_to", ["custom"]),
        conditions  = conditions,
        result      = d.get("result", "fail"),
        message     = d.get("message", "Policy condition matched."),
        logic       = d.get("logic", "and"),
        description = d.get("description", ""),
        version     = str(d.get("version", "1.0")),
        enabled     = bool(d.get("enabled", True)),
    )


class RulesLoader:
    """
    Loads declarative rules from YAML or JSON files and registers
    them in a PolicyEngine.

    File format (YAML or JSON):

    rules:
      - policy_id: CUSTOM-001
        name: My Custom Limit
        applies_to: [procurement, financial]
        logic: and          # "and" (all must match) | "or" (any must match)
        conditions:
          - field: amount
            op: gt
            value: 100000
          - field: approval_ref
            op: missing
        result: fail        # fail | warn | pass
        message: "Amount {amount} exceeds $100K and no approval_ref provided."
        description: Custom spending limit for pilot project
        version: "1.0"
        enabled: true

      - policy_id: CUSTOM-002
        name: Confidence Warning
        applies_to: [procurement]
        conditions:
          - field: ctx.confidence
            op: lt
            value: 0.6
        result: warn
        message: "Low model confidence ({confidence:.2f}) — verify decision."

    Usage:
        loader = RulesLoader()
        policies = loader.load_file("/etc/glassbox/rules.yaml")
        for p in policies:
            engine.register(p)

        # Or load a directory:
        policies = loader.load_directory("/etc/glassbox/rules/")
    """

    def __init__(self):
        self._lock = threading.Lock()

    def load_dict(self, data: Dict[str, Any]) -> List[Policy]:
        """Load rules from a parsed dict (already loaded from YAML/JSON)."""
        rules_data = data.get("rules", [data] if "policy_id" in data else [])
        policies = []
        for r in rules_data:
            try:
                rule = _parse_rule_dict(r)
                policies.append(rule.to_policy())
            except Exception as exc:
                log.error(
                    "RulesLoader: failed to parse rule %s: %s",
                    r.get("policy_id", "?"), exc
                )
        return policies

    def load_json_string(self, json_str: str) -> List[Policy]:
        """Load rules from a JSON string."""
        return self.load_dict(json.loads(json_str))

    def load_from_string(self, yaml_or_json: str) -> List[Policy]:
        """Alias for load_yaml_string — load rules from a YAML/JSON string."""
        if yaml_or_json.strip().startswith("{") or yaml_or_json.strip().startswith("["):
            return self.load_json_string(yaml_or_json)
        return self.load_yaml_string(yaml_or_json)

    def load(self, filepath: str) -> List[Policy]:
        """Load rules from a file path (alias for load_file)."""
        return self.load_file(filepath)

    def load_yaml_string(self, yaml_str: str) -> List[Policy]:
        """
        Load rules from a YAML string.
        Requires PyYAML (pip install pyyaml) — falls back to JSON if unavailable.
        """
        try:
            import yaml
            return self.load_dict(yaml.safe_load(yaml_str))
        except ImportError:
            # Fallback: try to parse as JSON
            return self.load_json_string(yaml_str)

    def load_file(self, path: str) -> List[Policy]:
        """Load rules from a YAML or JSON file."""
        p = Path(path)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() in (".yaml", ".yml"):
            return self.load_yaml_string(text)
        return self.load_json_string(text)

    def load_directory(self, directory: str, pattern: str = "*.yaml") -> List[Policy]:
        """Load all rule files matching pattern from a directory."""
        policies = []
        for path in sorted(Path(directory).glob(pattern)):
            policies.extend(self.load_file(str(path)))
        for path in sorted(Path(directory).glob("*.json")):
            policies.extend(self.load_file(str(path)))
        return policies

    def register_all(self, policies: List[Policy], engine: PolicyEngine) -> int:
        """Register a list of policies in a PolicyEngine. Returns count registered."""
        count = 0
        with self._lock:
            for policy in policies:
                engine.register(policy)
                count += 1
        return count

    def load_and_register(
        self,
        source: str,
        engine: PolicyEngine,
        is_directory: bool = False,
    ) -> int:
        """Load from file or directory and register in one call."""
        if is_directory:
            policies = self.load_directory(source)
        else:
            policies = self.load_file(source)
        return self.register_all(policies, engine)


# ── Built-in reference rule set (JSON) ───────────────────────────────────────

REFERENCE_RULES_JSON = json.dumps({
    "rules": [
        {
            "policy_id": "YAML-PROC-001",
            "name": "Procurement Declarative Limit",
            "applies_to": ["procurement"],
            "logic": "and",
            "conditions": [
                {"field": "amount", "op": "gt",      "value": 500000},
                {"field": "contract_id", "op": "missing"},
            ],
            "result": "fail",
            "message": "Amount {amount} exceeds $500K limit without contract_id.",
            "version": "1.0",
        },
        {
            "policy_id": "YAML-PRICE-001",
            "name": "Pricing Change Declarative",
            "applies_to": ["pricing"],
            "logic": "and",
            "conditions": [
                {"field": "new_price",      "op": "present"},
                {"field": "previous_price", "op": "present"},
            ],
            "result": "warn",
            "message": "Price change detected — verify against market data.",
            "version": "1.0",
        },
        {
            "policy_id": "YAML-CONF-001",
            "name": "Low Confidence Warning",
            "applies_to": ["procurement", "financial", "pricing"],
            "conditions": [
                {"field": "ctx.confidence", "op": "lt", "value": 0.5},
            ],
            "result": "warn",
            "message": "AI model confidence is low. Human verification recommended.",
            "version": "1.0",
        },
    ]
})
