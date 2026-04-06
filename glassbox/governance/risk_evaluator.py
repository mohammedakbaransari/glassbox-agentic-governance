"""
GlassBox Framework - Risk Evaluator
Computes a composite weighted risk score (0-100) per decision and determines
the governance disposition: auto_execute, human_review, or block.

Author: Mohammed Akbar Ansari
"""

from typing import Any, Dict, List, Optional

from glassbox.governance.models import (
    DecisionContext, DecisionType,
    Disposition, PolicyResult,
    RiskFactor, RiskLevel, RiskResult,
)


DEFAULT_THRESHOLDS = {
    "auto_execute_max": 35,   # score <= 35  -> execute automatically
    "human_review_max": 70,   # score <= 70  -> queue for human review
    # score > 70              -> block
}


# ---------------------------------------------------------------------------
# Risk factor extractors per decision type
# ---------------------------------------------------------------------------

def _procurement_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    amount = float(payload.get("amount", 0))

    if   amount > 1_000_000: size_score = 45
    elif amount >   500_000: size_score = 30
    elif amount >   100_000: size_score = 18
    elif amount >    50_000: size_score = 10
    else:                    size_score =  5
    factors = [RiskFactor("transaction_size", size_score, 0.40)]

    contract_score = 35 if (amount > 100_000 and not payload.get("contract_id")) else 0
    factors.append(RiskFactor("missing_contract", contract_score, 0.30))

    urgency_map = {"critical": 30, "high": 20, "medium": 10, "low": 0}
    urgency_score = urgency_map.get(str(payload.get("urgency", "low")).lower(), 5)
    factors.append(RiskFactor("urgency", urgency_score, 0.15))

    confidence = ctx.confidence
    conf_score = max(0, int((1.0 - confidence) * 30))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    chain_score = min(len(ctx.agent_chain) * 5, 20)
    factors.append(RiskFactor("agent_chain_depth", chain_score, 0.05))

    # Time-of-day risk: after-hours decisions carry higher operational risk
    try:
        from datetime import datetime, timezone
        hour = datetime.now(timezone.utc).hour
        off_hours = hour < 6 or hour >= 22
        time_score = 20 if off_hours else 0
    except Exception:
        time_score = 0
    factors.append(RiskFactor("time_of_day", time_score, 0.05))

    # Environment weight: production decisions carry higher risk
    env_score = 20 if ctx.environment == "production" else 0
    factors.append(RiskFactor("environment", env_score, 0.05))

    return factors


def _pricing_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    new_price  = float(payload.get("new_price", 0))
    prev_price = float(payload.get("previous_price", 0))

    if prev_price > 0:
        pct = abs(new_price - prev_price) / prev_price * 100
        if   pct > 40: pct_score = 60
        elif pct > 25: pct_score = 40
        elif pct > 15: pct_score = 25
        elif pct > 5:  pct_score = 12
        else:          pct_score =  5
    else:
        pct_score = 20
    factors = [RiskFactor("price_change_pct", pct_score, 0.45)]

    if   new_price > 100_000: abs_score = 35
    elif new_price >  10_000: abs_score = 20
    elif new_price >   1_000: abs_score = 10
    else:                     abs_score =  3
    factors.append(RiskFactor("absolute_price", abs_score, 0.30))

    reason_score = 20 if not payload.get("reason") else 0
    factors.append(RiskFactor("missing_reason", reason_score, 0.15))

    conf_score = max(0, int((1.0 - ctx.confidence) * 25))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


def _financial_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    amount = float(payload.get("amount", 0))

    if   amount > 500_000: amt_score = 55
    elif amount > 200_000: amt_score = 40
    elif amount > 100_000: amt_score = 28
    elif amount >  50_000: amt_score = 15
    else:                  amt_score =  5
    factors = [RiskFactor("transfer_amount", amt_score, 0.50)]

    dest_score = 35 if not payload.get("destination_account") else 0
    factors.append(RiskFactor("missing_destination", dest_score, 0.25))

    ref_score = 25 if not payload.get("reference") else 0
    factors.append(RiskFactor("missing_reference", ref_score, 0.15))

    conf_score = max(0, int((1.0 - ctx.confidence) * 30))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


def _inventory_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    quantity = int(payload.get("quantity", 0))

    if   quantity > 50_000: qty_score = 55
    elif quantity > 10_000: qty_score = 35
    elif quantity >  5_000: qty_score = 20
    elif quantity >  1_000: qty_score = 10
    else:                   qty_score =  5
    factors = [RiskFactor("order_quantity", qty_score, 0.55)]

    missing_supplier = 25 if not payload.get("supplier_id") else 0
    factors.append(RiskFactor("missing_supplier", missing_supplier, 0.30))

    conf_score = max(0, int((1.0 - ctx.confidence) * 20))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.15))

    return factors


def _logistics_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for logistics/shipment decisions."""
    value = float(payload.get("shipment_value", 0) or payload.get("value", 0))

    if   value > 1_000_000: val_score = 55
    elif value >   500_000: val_score = 40
    elif value >   100_000: val_score = 25
    elif value >    10_000: val_score = 12
    else:                   val_score =  5
    factors = [RiskFactor("shipment_value", val_score, 0.50)]

    # Hazardous materials flag
    hazmat = payload.get("hazardous_materials", False) or payload.get("hazmat", False)
    hazmat_score = 40 if hazmat else 0
    factors.append(RiskFactor("hazardous_materials", hazmat_score, 0.30))

    # Missing approval ref for valuable shipments
    approval = payload.get("approval_ref") or payload.get("approval_reference")
    appr_score = 20 if (value > 100_000 and not approval) else 0
    factors.append(RiskFactor("missing_approval", appr_score, 0.20))

    return factors


def _hr_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for HR decisions (termination, salary, promotion)."""
    action = str(payload.get("action", "")).lower()

    # Action severity
    HIGH_IMPACT = {"terminate", "termination", "redundancy", "layoff", "dismissal"}
    MED_IMPACT  = {"salary", "compensation", "promotion", "demotion", "transfer"}
    if any(h in action for h in HIGH_IMPACT):
        action_score = 55
    elif any(m in action for m in MED_IMPACT):
        action_score = 25
    else:
        action_score = 10
    factors = [RiskFactor("action_severity", action_score, 0.55)]

    # Salary adjustment size
    amount = float(payload.get("amount", 0) or payload.get("adjustment_amount", 0))
    base   = float(payload.get("base_salary", 0) or payload.get("current_salary", 0))
    if base > 0 and amount > 0:
        pct = amount / base * 100
        adj_score = min(int(pct * 1.5), 50)
    else:
        adj_score = 10
    factors.append(RiskFactor("adjustment_size", adj_score, 0.30))

    conf_score = max(0, int((1.0 - ctx.confidence) * 20))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.15))

    return factors


def _itops_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    action = str(payload.get("action", "")).lower()
    DESTRUCTIVE = {"delete", "terminate", "destroy", "drop", "truncate", "reset"}
    action_score = 50 if any(d in action for d in DESTRUCTIVE) else 15
    factors = [RiskFactor("action_risk", action_score, 0.60)]

    in_window = payload.get("change_window_approved", False)
    window_score = 0 if in_window else 30
    factors.append(RiskFactor("change_window", window_score, 0.40))
    return factors


def _generic_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    return [RiskFactor("unclassified_decision", 25, 1.0)]


def _clinical_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for clinical/healthcare decisions (prescriptions, dosage, procedures)."""
    # Dosage-related risk: high-dose or off-label prescriptions are higher risk
    dosage_str = str(payload.get("dosage", "") or payload.get("dose", ""))
    dosage_val = float(payload.get("dosage_mg", 0) or payload.get("dose_mg", 0))
    if   dosage_val > 1000: dose_score = 60
    elif dosage_val > 500:  dose_score = 40
    elif dosage_val > 100:  dose_score = 20
    else:                   dose_score = 10 if dosage_val > 0 else 5
    factors = [RiskFactor("dosage_level", dose_score, 0.40)]

    # Contraindication / allergy flag
    contraindication = payload.get("contraindication_flag", False) or payload.get("allergy_flag", False)
    contra_score = 55 if contraindication else 0
    factors.append(RiskFactor("contraindication", contra_score, 0.35))

    # Missing prescriber authorisation for controlled substances
    controlled = payload.get("controlled_substance", False)
    auth_ref   = payload.get("prescriber_id") or payload.get("auth_ref")
    auth_score = 35 if (controlled and not auth_ref) else 0
    factors.append(RiskFactor("missing_prescriber_auth", auth_score, 0.15))

    conf_score = max(0, int((1.0 - ctx.confidence) * 30))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


def _trading_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for trading decisions (orders, positions, hedges)."""
    notional = float(payload.get("notional", 0) or payload.get("order_value", 0))
    if   notional > 10_000_000: notional_score = 65
    elif notional >  1_000_000: notional_score = 45
    elif notional >    100_000: notional_score = 25
    elif notional >     10_000: notional_score = 12
    else:                       notional_score =  5
    factors = [RiskFactor("notional_value", notional_score, 0.45)]

    # Large position concentration raises risk
    position_pct = float(payload.get("position_pct", 0) or payload.get("concentration_pct", 0))
    if   position_pct > 20: conc_score = 55
    elif position_pct > 10: conc_score = 30
    elif position_pct >  5: conc_score = 15
    else:                   conc_score =  5
    factors.append(RiskFactor("position_concentration", conc_score, 0.30))

    # Missing risk limit / mandate reference for large trades
    limit_ref = payload.get("risk_limit_ref") or payload.get("mandate_ref")
    limit_score = 30 if (notional > 100_000 and not limit_ref) else 0
    factors.append(RiskFactor("missing_limit_ref", limit_score, 0.15))

    conf_score = max(0, int((1.0 - ctx.confidence) * 25))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


def _content_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for generative AI content governance (GDPR Art.22)."""
    # Personal data mention raises risk (PII in generated content)
    includes_pii = payload.get("contains_pii", False) or payload.get("pii_detected", False)
    pii_score = 50 if includes_pii else 5
    factors = [RiskFactor("pii_in_content", pii_score, 0.40)]

    # Sensitive topic: medical, legal, financial advice without disclaimer
    topic = str(payload.get("topic", "") or payload.get("content_category", "")).lower()
    HIGH_RISK_TOPICS = {"medical", "legal", "financial", "investment", "health"}
    disclaimer = payload.get("disclaimer_present", False)
    topic_score = 40 if (any(t in topic for t in HIGH_RISK_TOPICS) and not disclaimer) else 10
    factors.append(RiskFactor("sensitive_topic", topic_score, 0.30))

    # Automated decision flag: fully automated decisions affecting users (GDPR Art.22)
    automated = payload.get("fully_automated", False) or payload.get("no_human_review", False)
    auto_score = 35 if automated else 0
    factors.append(RiskFactor("fully_automated_decision", auto_score, 0.20))

    conf_score = max(0, int((1.0 - ctx.confidence) * 20))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


def _legal_factors(payload: Dict, ctx: DecisionContext) -> List[RiskFactor]:
    """Risk factors for legal AI decisions (contract analysis, e-discovery, filings)."""
    # Contract value or dispute amount raises risk
    value = float(payload.get("contract_value", 0) or payload.get("dispute_amount", 0))
    if   value > 10_000_000: val_score = 60
    elif value >  1_000_000: val_score = 40
    elif value >    100_000: val_score = 22
    elif value >     10_000: val_score = 10
    else:                    val_score =  5
    factors = [RiskFactor("contract_value", val_score, 0.40)]

    # Missing legal review / sign-off for binding actions
    action = str(payload.get("action", "")).lower()
    BINDING_ACTIONS = {"sign", "execute", "file", "submit", "conclude"}
    legal_review = payload.get("legal_review_ref") or payload.get("counsel_sign_off")
    binding_score = 45 if (any(a in action for a in BINDING_ACTIONS) and not legal_review) else 0
    factors.append(RiskFactor("missing_legal_review", binding_score, 0.35))

    # Jurisdiction risk: cross-border filings are inherently riskier
    cross_border = payload.get("cross_border", False) or payload.get("international", False)
    juris_score = 25 if cross_border else 0
    factors.append(RiskFactor("cross_border_jurisdiction", juris_score, 0.15))

    conf_score = max(0, int((1.0 - ctx.confidence) * 25))
    factors.append(RiskFactor("ai_confidence", conf_score, 0.10))

    return factors


FACTOR_EXTRACTORS = {
    DecisionType.PROCUREMENT: _procurement_factors,
    DecisionType.PRICING:     _pricing_factors,
    DecisionType.FINANCIAL:   _financial_factors,
    DecisionType.INVENTORY:   _inventory_factors,
    DecisionType.IT_OPS:      _itops_factors,
    DecisionType.LOGISTICS:   _logistics_factors,
    DecisionType.HR:          _hr_factors,
    DecisionType.CUSTOM:      _generic_factors,
    # v1.1 decision types — dedicated extractors replace the generic fallback.
    DecisionType.CLINICAL:    _clinical_factors,
    DecisionType.TRADING:     _trading_factors,
    DecisionType.CONTENT:     _content_factors,
    DecisionType.LEGAL:       _legal_factors,
}


# ---------------------------------------------------------------------------
# Risk Evaluator
# ---------------------------------------------------------------------------

class RiskEvaluator:
    """
    Computes a weighted composite risk score and determines the governance
    disposition for an AI-generated decision.
    """

    def __init__(self, thresholds: Optional[Dict[str, float]] = None):
        self.thresholds = thresholds or dict(DEFAULT_THRESHOLDS)

    def _score_to_level(self, score: float) -> RiskLevel:
        if   score <= 25: return RiskLevel.LOW
        elif score <= 50: return RiskLevel.MEDIUM
        elif score <= 75: return RiskLevel.HIGH
        else:             return RiskLevel.CRITICAL

    def _score_to_disposition(self, score: float, policy_result: PolicyResult) -> Disposition:
        if not policy_result.passed:
            return Disposition.BLOCK
        if score <= self.thresholds["auto_execute_max"]:
            return Disposition.AUTO_EXECUTE
        elif score <= self.thresholds["human_review_max"]:
            return Disposition.HUMAN_REVIEW
        return Disposition.BLOCK

    def evaluate(
        self,
        decision_type: DecisionType,
        payload: Dict[str, Any],
        context: DecisionContext,
        policy_result: PolicyResult,
    ) -> RiskResult:
        extractor = FACTOR_EXTRACTORS.get(decision_type, _generic_factors)
        factors   = extractor(payload, context)

        total_weight = sum(f.weight for f in factors)
        if total_weight == 0:
            composite = 0.0
        else:
            composite = sum(f.score * f.weight for f in factors) / total_weight

        composite    = min(max(round(composite, 2), 0.0), 100.0)
        risk_level   = self._score_to_level(composite)
        disposition  = self._score_to_disposition(composite, policy_result)

        return RiskResult(
            risk_score=composite,
            risk_level=risk_level,
            disposition=disposition,
            factors=factors,
        )
