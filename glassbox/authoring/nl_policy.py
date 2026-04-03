"""
GlassBox — Natural Language Policy Authoring  (v1.0.0)
=======================================================
LLM-assisted translation of natural-language policy descriptions into
validated GlassBox YAML rule definitions.

This module lowers the barrier for compliance and legal teams who understand
the policy domain but are not YAML or Python developers. A compliance officer
can describe a rule in plain English; this module translates it into a
validated, production-ready YAML rule that can be loaded by RulesLoader.

Design approach:
  Uses the Anthropic Claude API (via the claude-sonnet-4-20250514 model) to
  perform the translation. The model is given:
    1. The complete GlassBox YAML rule schema with all 12 operators
    2. Examples of good rules for each decision type
    3. The natural-language description from the compliance officer
    4. Validation context (available decision types, known policy ID formats)

  The generated YAML is then:
    1. Parsed and validated by RulesLoader to catch schema errors
    2. Returned as both YAML text and as Policy objects ready to register
    3. Accompanied by an explanation in plain English of what was generated

Zero API dependency fallback:
  If the Anthropic SDK is not installed, the TemplateBasedGenerator provides
  a rule template populated from the natural-language description using
  simple pattern matching. This produces less accurate rules but still
  useful starting points that a developer can refine.

Usage:
    from glassbox.authoring.nl_policy import NLPolicyAuthor

    # With Claude API (recommended)
    author = NLPolicyAuthor(api_key="sk-ant-...")
    result = author.generate(
        description="Block any procurement over $200,000 that does not have both "
                    "a contract_id and an approval_ref from the category manager",
        decision_type="procurement",
        policy_id="ORG-001",
    )
    print(result.yaml_rule)       # Ready to save as a .yaml file
    print(result.explanation)     # Plain-English explanation of the rule
    print(result.validation_ok)   # True if RulesLoader can parse it
    if result.validation_ok:
        pipeline.policy_engine.register(*result.policies)

    # Preview without registering
    author.preview(description="...", decision_type="financial")

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import re
import textwrap
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

YAML_RULE_SCHEMA = """
GlassBox YAML Rule Schema (all fields):

rules:
  - policy_id:    string (required, e.g. "ORG-001", unique across all rules)
    name:         string (required, human-readable policy name)
    applies_to:   list of decision types (required)
                  choices: [procurement, pricing, financial, inventory,
                            logistics, it_ops, hr, custom]
    logic:        "and" | "or"  (default "and" — all conditions must match)
    conditions:
      - field: string (payload field name, dot-notation for nested, or ctx.* for context)
                ctx.confidence — model confidence (0.0-1.0)
                ctx.environment — "production"|"staging"|"development"
        op:    one of: gt, gte, lt, lte, eq, neq, in, not_in,
                       missing, present, contains, regex
        value: the comparison value (number, string, or list for in/not_in)
    result:   "fail" | "warn"  (fail = block decision, warn = warning only)
    message:  string (human-readable explanation, can reference {field_name})

Example rules:

  # Block procurement over $500K without contract_id
  - policy_id: ORG-001
    name: Large Procurement Requires Contract
    applies_to: [procurement]
    logic: and
    conditions:
      - field: amount
        op: gt
        value: 500000
      - field: contract_id
        op: missing
    result: fail
    message: "Procurement of ${amount} exceeds $500K limit and requires contract_id"

  # Warn on low AI confidence for financial decisions
  - policy_id: ORG-002
    name: Low Confidence Financial Warning
    applies_to: [financial]
    conditions:
      - field: ctx.confidence
        op: lt
        value: 0.7
    result: warn
    message: "Model confidence {ctx.confidence:.0%} is below 70% — manual verification recommended"

  # Block if supplier not in approved list
  - policy_id: ORG-003
    name: Approved Supplier Only
    applies_to: [procurement, inventory]
    conditions:
      - field: supplier_id
        op: not_in
        value: ["SUP-001", "SUP-002", "SUP-003"]
    result: fail
    message: "Supplier {supplier_id} is not on the approved vendor list"
"""


@dataclass
class PolicyGenerationResult:
    """Result of a natural-language policy generation request."""
    description:   str
    policy_id:     str
    yaml_rule:     str
    explanation:   str
    validation_ok: bool
    validation_error: Optional[str] = None
    policies:      List[Any] = field(default_factory=list)  # Policy objects
    raw_response:  Optional[str] = None


class NLPolicyAuthor:
    """
    Translates natural-language policy descriptions into GlassBox YAML rules.

    Uses the Claude API when available; falls back to template-based generation
    if the API is not configured.

    Usage:
        author = NLPolicyAuthor(api_key="sk-ant-...")

        result = author.generate(
            description = "Any IT operations action that deletes or terminates "
                          "a production resource must have both change_window_approved "
                          "set to true and a supervisor_auth_code present",
            decision_type = "it_ops",
            policy_id     = "ITOPS-002",
        )

        if result.validation_ok:
            for policy in result.policies:
                pipeline.policy_engine.register(policy)
        else:
            print("Fix needed:", result.validation_error)
    """

    def __init__(
        self,
        api_key:    Optional[str] = None,
        model:      str           = "claude-sonnet-4-20250514",
        max_tokens: int           = 1200,
    ):
        self.api_key    = api_key
        self.model      = model
        self.max_tokens = max_tokens
        self._client    = None   # lazy init

    def generate(
        self,
        description:   str,
        decision_type: str  = "custom",
        policy_id:     Optional[str] = None,
        strict:        bool = True,
    ) -> PolicyGenerationResult:
        """
        Generate a GlassBox YAML rule from a natural-language description.

        Args:
            description:   Plain-English policy description from compliance team
            decision_type: Which decision type this applies to
            policy_id:     Desired policy ID (auto-generated if not provided)
            strict:        If True, return validation error rather than partial rule

        Returns:
            PolicyGenerationResult with yaml_rule, explanation, validation status
        """
        pid = policy_id or f"NL-{str(uuid.uuid4())[:8].upper()}"

        # Try Claude API first
        if self.api_key:
            return self._generate_with_claude(description, decision_type, pid)

        # Fall back to template-based generation
        return self._generate_from_template(description, decision_type, pid)

    def preview(
        self,
        description:   str,
        decision_type: str = "custom",
    ) -> str:
        """
        Generate a YAML rule preview without registering it.
        Returns the YAML string or an error message.
        """
        result = self.generate(description, decision_type)
        if result.validation_ok:
            return f"# Generated rule (validation passed)\n\n{result.yaml_rule}\n\n# Explanation:\n# {result.explanation}"
        return f"# Generation failed: {result.validation_error}\n\n{result.yaml_rule}"

    # ── Claude API path ────────────────────────────────────────────────────────

    def _generate_with_claude(
        self, description: str, decision_type: str, policy_id: str
    ) -> PolicyGenerationResult:
        """Use Claude to generate the YAML rule."""
        try:
            client = self._get_client()
            prompt = self._build_prompt(description, decision_type, policy_id)
            message = client.messages.create(
                model=self.model,
                max_tokens=self.max_tokens,
                messages=[{"role": "user", "content": prompt}],
                system=self._system_prompt(),
            )
            raw_text = message.content[0].text.strip()
            return self._parse_and_validate(raw_text, description, policy_id)
        except Exception as exc:
            # Fall back to template on API error
            result = self._generate_from_template(description, decision_type, policy_id)
            result.raw_response = f"Claude API error: {exc}. Used template fallback."
            return result

    def _get_client(self):
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(api_key=self.api_key)
            except ImportError:
                raise ImportError(
                    "anthropic package required for Claude-powered generation: "
                    "pip install anthropic"
                )
        return self._client

    def _system_prompt(self) -> str:
        return (
            "You are a GlassBox governance expert. Your job is to translate natural-language "
            "policy descriptions from compliance teams into valid GlassBox YAML rules.\n\n"
            "Rules:\n"
            "1. Respond with ONLY a YAML code block (```yaml ... ```) followed by a one-sentence "
            "plain-English explanation prefixed with 'EXPLANATION: '.\n"
            "2. Use only the operators defined in the schema: "
            "gt, gte, lt, lte, eq, neq, in, not_in, missing, present, contains, regex.\n"
            "3. Use ctx.confidence for AI model confidence, ctx.environment for environment.\n"
            "4. result must be 'fail' (to block) or 'warn' (advisory only).\n"
            "5. Do not invent operators or fields not in the schema.\n"
            "6. Keep the message field concise and include the relevant field value in braces."
        )

    def _build_prompt(self, description: str, decision_type: str, policy_id: str) -> str:
        return (
            f"Generate a GlassBox YAML rule for the following policy requirement:\n\n"
            f"Policy ID: {policy_id}\n"
            f"Decision type: {decision_type}\n"
            f"Requirement: {description}\n\n"
            f"Schema reference:\n{YAML_RULE_SCHEMA}\n\n"
            f"Respond with the YAML rule block and then 'EXPLANATION: <one sentence>'."
        )

    def _parse_and_validate(
        self, raw_text: str, description: str, policy_id: str
    ) -> PolicyGenerationResult:
        """Extract YAML from response and validate with RulesLoader."""
        # Extract YAML block
        yaml_match = re.search(r"```yaml\s*(.*?)\s*```", raw_text, re.DOTALL)
        if yaml_match:
            yaml_text = yaml_match.group(1).strip()
        else:
            # Try to find raw YAML (rules: ...)
            yaml_match = re.search(r"(rules:.*)", raw_text, re.DOTALL)
            yaml_text  = yaml_match.group(1).strip() if yaml_match else raw_text

        # Extract explanation
        exp_match   = re.search(r"EXPLANATION:\s*(.+?)(?:\n|$)", raw_text, re.IGNORECASE)
        explanation = exp_match.group(1).strip() if exp_match else ""

        # Validate
        validation_ok    = False
        validation_error = None
        policies         = []
        try:
            from glassbox.rules.rules_engine import RulesLoader
            loader   = RulesLoader()
            policies = loader.load_from_string(yaml_text)
            validation_ok = True
        except Exception as exc:
            validation_error = str(exc)

        return PolicyGenerationResult(
            description=description,
            policy_id=policy_id,
            yaml_rule=yaml_text,
            explanation=explanation,
            validation_ok=validation_ok,
            validation_error=validation_error,
            policies=policies,
            raw_response=raw_text,
        )

    # ── Template fallback ──────────────────────────────────────────────────────

    def _generate_from_template(
        self, description: str, decision_type: str, policy_id: str
    ) -> PolicyGenerationResult:
        """
        Template-based rule generation without LLM.
        Produces a starting-point rule using keyword detection.
        """
        desc_lower = description.lower()

        # Detect amount/threshold patterns
        amount_match = re.search(r"\$?([\d,]+(?:k|K|m|M)?)\b", description)
        amount_val   = 0
        if amount_match:
            raw = amount_match.group(1).replace(",", "")
            if raw.endswith(("k", "K")): amount_val = int(float(raw[:-1]) * 1000)
            elif raw.endswith(("m", "M")): amount_val = int(float(raw[:-1]) * 1_000_000)
            else:
                try: amount_val = int(raw)
                except ValueError: amount_val = 0

        # Detect result type
        result = "fail" if any(w in desc_lower for w in
                               ["block", "reject", "must not", "cannot", "require"]) else "warn"

        # Detect missing fields
        missing_fields = []
        for keyword, field_name in [
            ("contract", "contract_id"), ("approval", "approval_ref"),
            ("reference", "reference"), ("authorisation", "auth_code"),
            ("supervisor", "supervisor_auth_code")
        ]:
            if keyword in desc_lower:
                missing_fields.append(field_name)

        # Build conditions with correct YAML indentation (4-space indent under conditions:)
        cond_lines = []
        if amount_val > 0:
            cond_lines += ["    - field: amount", "      op: gt", f"      value: {amount_val}"]
        for mf in missing_fields[:2]:
            cond_lines += [f"    - field: {mf}", "      op: missing"]
        if not cond_lines:
            cond_lines = ["    - field: REPLACE_FIELD", "      op: REPLACE_OP", "      value: REPLACE_VALUE"]

        conditions_block = "\n".join(cond_lines)
        yaml_text = (
            f"rules:\n"
            f"  - policy_id: {policy_id}\n"
            f"    name: \"{description[:80]}\"\n"
            f"    applies_to: [{decision_type}]\n"
            f"    logic: and\n"
            f"    conditions:\n"
            f"{conditions_block}\n"
            f"    result: {result}\n"
            f"    message: \"Policy {policy_id}: {description[:100]}\"\n"
        )

        # Try to validate
        validation_ok    = False
        validation_error = None
        policies         = []
        try:
            from glassbox.rules.rules_engine import RulesLoader
            loader   = RulesLoader()
            policies = loader.load_from_string(yaml_text)
            validation_ok = True
        except Exception as exc:
            validation_error = str(exc)

        return PolicyGenerationResult(
            description=description,
            policy_id=policy_id,
            yaml_rule=yaml_text,
            explanation=(
                f"Template-generated rule (Claude API not configured). "
                f"Review and adjust field names and values before deploying."
            ),
            validation_ok=validation_ok,
            validation_error=validation_error,
            policies=policies,
        )
