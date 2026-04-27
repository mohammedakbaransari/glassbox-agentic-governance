"""
GlassBox — Decision Explanation API  (v1.2.0)
=============================================
Generates plain-language explanations for every governed decision.

EU AI Act Article 13 requires AI systems to provide sufficient transparency
for operators to interpret output. GlassBox implements this through the
DecisionExplainer, which translates governance outcomes into clear,
human-readable explanations.

Three explanation levels:
  BRIEF   — one sentence summary
  STANDARD— paragraph with all factors
  DETAILED— full breakdown with regulatory references

Usage:
    from glassbox.governance.explainer import DecisionExplainer

    explainer = DecisionExplainer()
    response  = pipeline.process(request)
    explanation = explainer.explain(response)
    print(explanation.summary)        # "Blocked: spending limit exceeded"
    print(explanation.full_text)      # Full explanation paragraph
    print(explanation.regulatory_ref) # "EU AI Act Art. 13; NIST AI RMF GOVERN"

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from glassbox.governance.models import DecisionResponse, RiskFactor


@dataclass
class DecisionExplanation:
    """Structured explanation of a governance decision."""
    decision_id:     str
    outcome:         str           # "EXECUTED" | "HUMAN_REVIEW" | "BLOCKED"
    summary:         str           # One-sentence summary
    full_text:       str           # Full paragraph explanation
    why_blocked:     List[str]     = field(default_factory=list)   # Specific block reasons
    risk_breakdown:  List[str]     = field(default_factory=list)   # Risk factor contributions
    warnings:        List[str]     = field(default_factory=list)   # Non-blocking concerns
    regulatory_refs: List[str]     = field(default_factory=list)   # Regulatory framework references
    recommended_actions: List[str] = field(default_factory=list)   # What to do next

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id":        self.decision_id,
            "outcome":            self.outcome,
            "summary":            self.summary,
            "full_text":          self.full_text,
            "why_blocked":        self.why_blocked,
            "risk_breakdown":     self.risk_breakdown,
            "warnings":           self.warnings,
            "regulatory_refs":    self.regulatory_refs,
            "recommended_actions":self.recommended_actions,
        }


class DecisionExplainer:
    """
    Translates governance outcomes into plain-language explanations.

    Designed for:
      - EU AI Act Article 13 transparency requirements
      - Human reviewers in the WorkflowEngine queue
      - API consumers who need machine-readable explanations
      - Audit documentation for regulators

    Thread-safe: all methods are stateless over their inputs.
    """

    # Maps policy IDs to plain-language descriptions and regulatory refs
    _POLICY_LABELS = {
        "PROC-001": ("Procurement spending limit exceeded",
                     ["Provide a contract_id for amounts above $500,000"],
                     ["FAR 6.301", "ISO 20400:2017"]),
        "PROC-002": ("Supplier not on approved vendor registry",
                     ["Verify supplier eligibility and initiate vendor qualification"],
                     ["ISO 9001 Supplier Approval", "FAR 9.4"]),
        "PROC-003": ("High-risk category requires category approval",
                     ["Obtain category_approval_ref from the category management team"],
                     ["Export Administration Regulations", "EU Dual-Use Regulation"]),
        "PROC-004": ("Sole-source procurement requires justification",
                     ["Provide sole_source_justification documenting why competitive bidding is not feasible"],
                     ["FAR 6.302", "UK PCR 2015 Reg.32"]),
        "PROC-005": ("High-value procurement requires an audit trail reference",
                 ["Provide audit_ref for procurements above the configured threshold"],
                 ["SOX Record Retention Controls", "ISO 9001 Audit Evidence"]),
        "PROC-006": ("Sanctioned country or debarred supplier",
                     ["Do not proceed — OFAC/UN sanctions apply. Contact compliance team."],
                     ["OFAC SDN List", "UN Security Council Resolutions", "31 CFR Part 501"]),
        "PRICE-001": ("Price change exceeds 30% single-decision limit",
                      ["Reduce price change to under 30% or obtain pricing_approval_ref"],
                      ["EU Unfair Commercial Practices Directive", "Consumer Protection Act"]),
        "PRICE-002": ("New price is below floor price",
                      ["Set new_price above the configured floor_price to prevent margin erosion"],
                      ["Internal Pricing Policy", "Competition Law (margin squeeze)"]),
        "PRICE-003": ("New price exceeds the approved ceiling price",
                  ["Reduce new_price below ceiling_price or update the approved price ceiling"],
                  ["Internal Pricing Policy", "Consumer Protection Act"]),
        "PRICE-004": ("Price exceeds competitor benchmark tolerance",
                  ["Review competitor_avg_price and obtain approval for the variance if justified"],
                  ["Internal Pricing Governance", "Competition Law"]),
        "FIN-001":   ("Financial transfer exceeds $1M single-transaction limit",
                      ["Split into multiple authorised transactions or obtain executive_approval_ref"],
                      ["BSA 31 CFR 1010", "PSD2 Art.74", "MiFID II Art.17"]),
        "FIN-002":   ("Daily transfer velocity limit exceeded",
                      ["Defer to next business day or obtain treasury approval for elevated daily limit"],
                      ["FINRA Rule 4210", "Basel III Operational Risk"]),
        "FIN-003":   ("Counterparty concentration risk",
                      ["Provide destination_account or counterparty_id for traceability"],
                      ["Basel III Large Exposures Rule", "CRR Art.395"]),
        "FIN-004":   ("Currency transaction at BSA reporting threshold",
                      ["Set ctr_filed=True after filing FinCEN Form 104 Currency Transaction Report"],
                      ["Bank Secrecy Act 31 CFR 1010.311", "FinCEN CTR requirements"]),
        "FIN-005":   ("Potential transaction structuring detected",
                      ["Review transaction pattern — if legitimate, add structuring_review_ref"],
                      ["BSA 31 CFR 1010.314 (Structuring)", "AML compliance team escalation"]),
        "IT-OPS-002": ("Destructive IT action requires change window approval",
                       ["Obtain change_window_approved=True from Change Advisory Board"],
                       ["ITIL Change Management", "NIST SP 800-53 CM-3", "SOC 2 CC8.1"]),
        "IT-OPS-003": ("Critical service changes require explicit approval",
                   ["Set requires_approval=True and record the approving authority for critical services"],
                   ["NIST SP 800-53 CM-3", "ITIL Change Management"]),
        "IT-OPS-004": ("Major IT changes require a change log reference",
                   ["Provide change_id for major operational changes before execution"],
                   ["SOC 2 CC8.1", "ISO 27001 A.12.1.2"]),
        "LOG-001":   ("Fleet budget threshold reached or exceeded",
                  ["Reduce spend or obtain fleet budget approval before proceeding"],
                  ["Internal Budget Controls", "COSO ERM Framework"]),
        "HR-001":    ("Salary adjustment exceeds threshold requiring HR approval",
                      ["Obtain approval_ref from HR Director or People Operations"],
                      ["Internal Compensation Policy", "FLSA", "Equal Pay Act"]),
        "HR-002":    ("Promotion requires both manager and HR approval",
                  ["Collect manager_approval and hr_approval before finalizing the promotion"],
                  ["Internal Promotion Policy", "Equal Employment Governance"]),
        "HR-003":    ("Privileged access provisioning requires security clearance",
                  ["Complete security review before granting ADMIN, SUPER, or ROOT access"],
                  ["ISO 27001 A.9", "NIST SP 800-53 AC-2"]),
        "AI-001":    ("AI model confidence below governance threshold",
                      ["Retry with a higher-confidence model or route to human review"],
                      ["NIST AI RMF MEASURE 2.5", "EU AI Act Art. 9 Risk Management"]),
        "SECURITY-001": ("User override not permitted in production environment",
                         ["Remove user_override=True flag — production decisions require proper policy compliance"],
                         ["SOX IT General Controls", "ISO 27001 A.12.1.2"]),
        "COMPLIANCE-001": ("Potential PII exposure detected in selected fields",
                  ["Remove or mask sensitive fields before release or processing"],
                  ["GDPR Art. 5", "HIPAA Privacy Rule", "CCPA 1798.100"]),
        "COMPLIANCE-002": ("Data residency region is not approved",
                  ["Move data to an approved region or update the approved residency policy"],
                  ["GDPR Data Transfer Controls", "Data Residency Policy"]),
        "COMPLIANCE-003": ("Regulatory breach notification has not been completed",
                  ["Notify the relevant authority within the required reporting window"],
                  ["GDPR Art. 33", "Incident Response Policy"]),
        "AGG-001":   ("Fleet aggregate budget limit approached or exceeded",
                      ["Defer procurement or obtain fleet_budget_override from Finance"],
                      ["Internal Budget Controls", "COSO ERM Framework"]),
        "CLIN-001":  ("Controlled substance requires prescriber authorisation",
                      ["Provide prescriber_dea_number and verify schedule compliance"],
                      ["21 CFR Part 1306", "DEA Controlled Substances Act", "UK MDA 1971"]),
        "CLIN-002":  ("Dosage exceeds patient-specific safety limit",
                      ["Review max_dose_mg and patient_weight_kg — consult prescribing physician"],
                      ["FDA Drug Label Guidance", "BNF Dosage Guidelines", "Joint Commission"]),
        "TRADE-001": ("Trading position notional exceeds limit",
                      ["Reduce order size or obtain position_limit_override from Risk Management"],
                      ["MiFID II Art.17", "FINRA Rule 4210", "Basel III Market Risk"]),
        "TRADE-002": ("Order quantity is a potential fat-finger error",
                      ["Confirm order qty vs avg_daily_qty — reduce or confirm with fat_finger_confirmed=True"],
                      ["FINRA Rule 5310 Best Execution", "MiFID II Art.27"]),
        "GEN-001":   ("Generated content contains detected PII",
                      ["Remove or mask PII before serving output", "Implement differential privacy"],
                      ["GDPR Art. 5(1)(f)", "CCPA 1798.100", "HIPAA Privacy Rule 45 CFR 164.514"]),
        "GEN-002":   ("Automated decision affecting EU individual lacks required disclosure",
                      ["Set human_review_available=True or gdpr_art22_disclosed=True"],
                      ["GDPR Art. 22 Automated Decision-Making", "EU AI Act Art. 13"]),
        "LEGAL-001": ("Contract value exceeds AI authority limit",
                      ["Provide authority_ref or approval_ref from an authorised legal approver"],
                      ["Contract Approval Policy", "SOX Delegation of Authority Controls"]),
        "LEGAL-002": ("Legal hold applies to the requested action",
                      ["Preserve records and obtain legal_hold_release_ref before proceeding"],
                      ["FRCP Rule 37(e)", "Legal Hold Policy", "eDiscovery Preservation Duty"]),
        "RISK-001":  ("Aggregated component risk exceeds the configured threshold",
                  ["Reduce the contributing component_risks or raise risk_threshold with approval"],
                  ["NIST AI RMF MANAGE", "Enterprise Risk Management Policy"]),
    }

    # Maps risk factors to plain-language descriptions
    _FACTOR_LABELS = {
        "transaction_size":    "transaction financial magnitude",
        "missing_contract":    "absence of contract documentation",
        "urgency":             "urgency classification",
        "ai_confidence":       "AI model confidence level",
        "agent_chain_depth":   "multi-agent delegation chain length",
        "time_of_day":         "after-hours timing",
        "environment":         "production environment weighting",
        "price_change_pct":    "price change percentage",
        "floor_price_breach":  "floor price proximity",
        "financial_magnitude": "financial transfer magnitude",
        "missing_reference":   "missing reference fields",
        "destructive_action":  "destructive action type",
        "no_change_window":    "absence of change window approval",
        "quantity_magnitude":  "order quantity magnitude",
        "action_type_risk":    "action type risk profile",
    }

    def explain(self, response: "DecisionResponse", level: str = "STANDARD") -> DecisionExplanation:
        """
        Generate a plain-language explanation for a governance decision.

        Args:
            response: The DecisionResponse from pipeline.process()
            level:    "BRIEF" | "STANDARD" | "DETAILED"

        Returns:
            DecisionExplanation with all explanation components
        """
        outcome      = response.final_status.value.upper() if response.final_status else "UNKNOWN"
        decision_id  = response.decision_id
        violations   = response.policy_violations or []
        warnings_raw = response.policy_warnings or []

        # ── Build why_blocked list ────────────────────────────────────────────
        why_blocked       = []
        recommended_acts  = []
        regulatory_refs   = []

        for v in violations:
            policy_id = self._extract_policy_id(v)
            if policy_id and policy_id in self._POLICY_LABELS:
                label, recs, refs = self._POLICY_LABELS[policy_id]
                why_blocked.append(label)
                recommended_acts.extend(recs)
                regulatory_refs.extend(refs)
            else:
                why_blocked.append(v)

        # ── Build risk breakdown ──────────────────────────────────────────────
        risk_breakdown = []
        if response.audit_record and response.audit_record.risk_result:
            rr = response.audit_record.risk_result
            if rr.factors:
                # Sort by weighted contribution descending
                sorted_factors = sorted(
                    rr.factors,
                    key=lambda f: f.score * f.weight,
                    reverse=True
                )
                for f in sorted_factors[:4]:  # top 4 factors
                    contribution = int(f.score * f.weight)
                    if contribution > 0:
                        label = self._FACTOR_LABELS.get(f.factor, f.factor.replace("_", " "))
                        risk_breakdown.append(
                            f"{label.capitalize()} contributed {contribution} risk points "
                            f"(score {int(f.score)}, weight {f.weight:.0%})"
                        )

        # ── Build warnings list ───────────────────────────────────────────────
        warnings = []
        for w in warnings_raw:
            pid = self._extract_policy_id(w)
            if pid and pid in self._POLICY_LABELS:
                warnings.append(self._POLICY_LABELS[pid][0])
            else:
                warnings.append(w)

        # ── Add regulatory refs from risk level ───────────────────────────────
        risk_score = response.risk_score or 0
        if risk_score > 70:
            regulatory_refs.append("NIST AI RMF MANAGE (high-risk decision)")
        if outcome == "BLOCKED":
            regulatory_refs.append("EU AI Act Art. 9 (Risk Management)")
            regulatory_refs.append("EU AI Act Art. 12 (Record-Keeping)")
        if outcome == "HUMAN_REVIEW":
            regulatory_refs.append("EU AI Act Art. 14 (Human Oversight)")
        regulatory_refs = list(dict.fromkeys(regulatory_refs))  # deduplicate

        # ── Compose summary ───────────────────────────────────────────────────
        if outcome == "BLOCKED":
            if why_blocked:
                summary = f"Decision BLOCKED: {why_blocked[0].lower()}."
            else:
                summary = f"Decision BLOCKED with risk score {risk_score:.0f}/100."
        elif outcome == "PENDING_REVIEW":
            summary = f"Decision routed to human review (risk score {risk_score:.0f}/100, {len(warnings)} advisory warnings)."
        elif outcome == "EXECUTED":
            summary = f"Decision approved and executed (risk score {risk_score:.0f}/100, {len(warnings)} advisory notes)."
        else:
            summary = f"Decision outcome: {outcome}."

        # ── Compose full text ─────────────────────────────────────────────────
        paragraphs = [summary]

        if why_blocked:
            block_detail = "; ".join(why_blocked)
            paragraphs.append(f"The governance pipeline blocked this decision because: {block_detail}.")

        if risk_breakdown and level in ("STANDARD", "DETAILED"):
            rb = "; ".join(risk_breakdown)
            paragraphs.append(f"Risk scoring: composite score {risk_score:.0f}/100. "
                              f"Contributing factors: {rb}.")

        if warnings and level in ("STANDARD", "DETAILED"):
            warn_text = "; ".join(warnings)
            paragraphs.append(f"Advisory warnings (non-blocking): {warn_text}.")

        if recommended_acts and level in ("STANDARD", "DETAILED"):
            acts = " ".join(f"({i+1}) {a}" for i, a in enumerate(recommended_acts[:3]))
            paragraphs.append(f"Recommended actions to resolve: {acts}")

        full_text = " ".join(paragraphs)

        return DecisionExplanation(
            decision_id=decision_id,
            outcome=outcome,
            summary=summary,
            full_text=full_text,
            why_blocked=why_blocked,
            risk_breakdown=risk_breakdown,
            warnings=warnings,
            regulatory_refs=regulatory_refs,
            recommended_actions=recommended_acts,
        )

    # Regex matching policy IDs like PROC-001, FIN-002, IT-OPS-003, SECURITY-001, etc.
    _POLICY_ID_RE = re.compile(r'^([A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+)[:\s\]]')

    @staticmethod
    def _extract_policy_id(message: str) -> Optional[str]:
        """Extract policy ID from formats: '[PROC-001] ...' or 'PROC-001: ...'."""
        if not message:
            return None
        # Bracket format: "[PROC-001] ..."
        if message.startswith("[") and "]" in message:
            return message[1:message.index("]")]
        # Colon format: "PROC-001: ..."
        colon = message.find(": ")
        if colon > 0:
            candidate = message[:colon]
            # Validate it looks like a policy ID (uppercase, digits, hyphens)
            if re.match(r'^[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+$', candidate):
                return candidate
        return None

    def explain_dict(self, response: "DecisionResponse", level: str = "STANDARD") -> Dict[str, Any]:
        """Convenience method: returns explanation as a plain dict."""
        return self.explain(response, level).to_dict()


# Module-level singleton
_default_explainer = DecisionExplainer()


def explain(response: "DecisionResponse", level: str = "STANDARD") -> DecisionExplanation:
    """Module-level convenience function for explaining a governance decision."""
    return _default_explainer.explain(response, level)
