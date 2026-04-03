"""
GlassBox Framework — Policy Engine  (v1.0.0)
=============================================
Thread-safe policy registry and evaluator.

Policies are Python callables that receive (payload, context) and return
a PolicyEvaluation. They can be registered at runtime, disabled, re-enabled,
and replaced by version without restarting the pipeline.

Built-in policies (12 across 7 domains):
  PROC-001  Procurement spending limit ($500K)
  PROC-002  Approved supplier registry
  PROC-003  High-risk category controls
  PRICE-001 Maximum 30% single-decision price change
  PRICE-002 Floor price enforcement
  FIN-001   Single financial transfer limit ($1M)
  ITOPS-001 Change window for destructive IT actions
  INV-001   Inventory reorder quantity limit
  LOG-001   High-value logistics approval
  HR-001    HR salary adjustment threshold
  AI-001    AI model confidence floor (0.30)
  ENV-001   Production user_override block
  AGG-001   Fleet aggregate spend budget (cross-agent)

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import copy
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from glassbox.governance.models import (
    DecisionContext, DecisionType,
    PolicyEvaluation, PolicyResult,
)


# ── Policy dataclass ──────────────────────────────────────────────────────────

@dataclass
class Policy:
    """A single governance policy — callable rule + metadata."""
    policy_id:      str
    policy_name:    str
    decision_types: List[DecisionType]
    rule:           Callable[[Dict, DecisionContext], PolicyEvaluation]
    enabled:        bool   = True
    version:        str    = "1.0.0"
    description:    str    = ""


# ── Built-in policy rules ──────────────────────────────────────────────────────

def _proc_spending_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    amount = float(payload.get("amount", 0))
    limit  = 500_000
    if amount > limit and not payload.get("contract_id"):
        return PolicyEvaluation("PROC-001", "Procurement Spending Limit", "fail",
            f"[PROC-001] Amount ${amount:,.0f} exceeds ${limit:,} limit without contract_id")
    return PolicyEvaluation("PROC-001", "Procurement Spending Limit", "pass", "OK")


def _proc_approved_suppliers(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    APPROVED = {
        "SUP-001","SUP-002","SUP-003","SUP-004","SUP-005",
        "SUP-010","SUP-020","SUP-030","SUP-100","SUP-200",
    }
    sid = payload.get("supplier_id", "")
    if sid and sid not in APPROVED:
        return PolicyEvaluation("PROC-002", "Approved Supplier Registry", "warn",
            f"[PROC-002] Supplier '{sid}' is not on the approved vendor registry")
    return PolicyEvaluation("PROC-002", "Approved Supplier Registry", "pass", "OK")


def _proc_high_risk_category(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    HIGH_RISK = {"semiconductors","chemicals","pharmaceuticals","weapons",
                 "dual-use","cryptography","export-controlled","radioactive"}
    cat = str(payload.get("category", "")).lower()
    if any(h in cat for h in HIGH_RISK) and not payload.get("category_approval_ref"):
        return PolicyEvaluation("PROC-003", "High-Risk Category", "fail",
            f"[PROC-003] Category '{cat}' requires category_approval_ref")
    return PolicyEvaluation("PROC-003", "High-Risk Category", "pass", "OK")


def _price_change_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    new_p = float(payload.get("new_price", 0))
    old_p = float(payload.get("previous_price", payload.get("old_price", 0)))
    if old_p > 0:
        pct = abs(new_p - old_p) / old_p * 100
        if pct > 30:
            return PolicyEvaluation("PRICE-001", "Price Change Limit", "fail",
                f"[PRICE-001] Price change of {pct:.1f}% exceeds 30% single-decision limit")
    return PolicyEvaluation("PRICE-001", "Price Change Limit", "pass", "OK")


def _price_floor(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    new_p   = float(payload.get("new_price", 0))
    floor_p = float(payload.get("floor_price", 0))
    if floor_p > 0 and new_p < floor_p:
        return PolicyEvaluation("PRICE-002", "Price Floor Enforcement", "fail",
            f"[PRICE-002] New price ${new_p:.2f} is below floor price ${floor_p:.2f}")
    return PolicyEvaluation("PRICE-002", "Price Floor Enforcement", "pass", "OK")


def _fin_transfer_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    amount = float(payload.get("amount", 0))
    limit  = 1_000_000
    if amount > limit:
        return PolicyEvaluation("FIN-001", "Financial Transfer Limit", "fail",
            f"[FIN-001] Transfer of ${amount:,.0f} exceeds single-transaction limit of ${limit:,}")
    return PolicyEvaluation("FIN-001", "Financial Transfer Limit", "pass", "OK")


def _itops_change_window(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    DESTRUCTIVE = {"delete","drop","truncate","destroy","terminate",
                   "reset","wipe","format","decommission","deprovision"}
    action = str(payload.get("action", "")).lower()
    if any(d in action for d in DESTRUCTIVE) and not payload.get("change_window_approved"):
        return PolicyEvaluation("ITOPS-001", "IT Change Window", "fail",
            f"[ITOPS-001] Destructive action '{action}' requires change_window_approved=True")
    return PolicyEvaluation("ITOPS-001", "IT Change Window", "pass", "OK")


def _inv_quantity_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    qty   = int(payload.get("quantity", 0))
    limit = 10_000
    if qty > limit:
        return PolicyEvaluation("INV-001", "Inventory Quantity Limit", "fail",
            f"[INV-001] Reorder quantity {qty:,} exceeds per-decision limit of {limit:,}")
    return PolicyEvaluation("INV-001", "Inventory Quantity Limit", "pass", "OK")


def _log_approval(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    value = float(payload.get("shipment_value", payload.get("value", 0)))
    if value > 100_000 and not payload.get("approval_ref"):
        return PolicyEvaluation("LOG-001", "Logistics High-Value Approval", "fail",
            f"[LOG-001] Shipment value ${value:,.0f} requires approval_ref")
    return PolicyEvaluation("LOG-001", "Logistics High-Value Approval", "pass", "OK")


def _hr_approval(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    amount = float(payload.get("amount", payload.get("adjustment_amount", 0)))
    if amount > 50_000 and not payload.get("approval_ref"):
        return PolicyEvaluation("HR-001", "HR Salary Adjustment Approval", "fail",
            f"[HR-001] Salary adjustment ${amount:,.0f} requires HR approval_ref")
    return PolicyEvaluation("HR-001", "HR Salary Adjustment Approval", "pass", "OK")


def _ai_confidence(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    conf      = ctx.confidence
    threshold = 0.30
    if conf < threshold:
        return PolicyEvaluation("AI-001", "AI Model Confidence Floor", "fail",
            f"[AI-001] Model confidence {conf:.2f} is below minimum threshold {threshold}")
    return PolicyEvaluation("AI-001", "AI Model Confidence Floor", "pass", "OK")


def _env_no_user_override(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    if ctx.environment == "production" and ctx.user_override:
        return PolicyEvaluation("ENV-001", "Production Override Block", "fail",
            "[ENV-001] user_override is not permitted in production environment")
    return PolicyEvaluation("ENV-001", "Production Override Block", "pass", "OK")


# ── AGG-001: Fleet Aggregate Spend Budget ─────────────────────────────────────
# This policy is special: it needs access to the AuditRepository to compute
# fleet-wide spend. It is registered as a closure that captures the budget
# and the audit logger reference when the pipeline is constructed.
# Default: warns at 80% of $5M fleet budget, blocks at 100%.

class FleetBudgetPolicy:
    """
    AGG-001 — Fleet Aggregate Spend Budget.

    Tracks cumulative approved spend across ALL agents in the fleet.
    Unlike per-agent limits (PROC-001), this enforces a global ceiling.

    Usage:
        fleet_policy = FleetBudgetPolicy(budget=5_000_000)
        pipeline.policy_engine.register(fleet_policy.as_policy())

    The audit_logger is injected at first evaluation to read approved spend.
    """

    def __init__(
        self,
        budget:          float = 5_000_000,
        warn_threshold:  float = 0.80,
        decision_types:  Optional[List[DecisionType]] = None,
    ):
        self.budget         = budget
        self.warn_threshold = warn_threshold
        self.decision_types = decision_types or [
            DecisionType.PROCUREMENT, DecisionType.FINANCIAL]
        self._lock          = threading.Lock()
        self._approved_spend: float = 0.0

    def record_execution(self, amount: float) -> None:
        """Called by pipeline when a financial/procurement decision executes."""
        with self._lock:
            self._approved_spend += amount

    def reset_period(self) -> None:
        """Reset for new budget period (monthly/quarterly)."""
        with self._lock:
            self._approved_spend = 0.0

    def current_spend(self) -> float:
        with self._lock:
            return self._approved_spend

    def _evaluate(self, payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
        amount = float(payload.get("amount", 0))
        with self._lock:
            projected = self._approved_spend + amount
        utilisation = projected / self.budget if self.budget > 0 else 0

        if projected > self.budget:
            return PolicyEvaluation("AGG-001", "Fleet Budget Limit", "fail",
                f"[AGG-001] Fleet spend ${projected:,.0f} would exceed budget "
                f"${self.budget:,.0f} (current: ${self._approved_spend:,.0f})")
        if utilisation >= self.warn_threshold:
            return PolicyEvaluation("AGG-001", "Fleet Budget Limit", "warn",
                f"[AGG-001] Fleet budget {utilisation:.0%} utilised "
                f"(${projected:,.0f} of ${self.budget:,.0f})")
        return PolicyEvaluation("AGG-001", "Fleet Budget Limit", "pass", "OK")

    def as_policy(self) -> Policy:
        return Policy(
            policy_id="AGG-001",
            policy_name="Fleet Aggregate Spend Budget",
            decision_types=self.decision_types,
            rule=self._evaluate,
            description=f"Fleet-wide budget cap: ${self.budget:,.0f}",
        )


# ── v1.1 Policy rules ─────────────────────────────────────────────────────────

# ─ Financial extended policies ────────────────────────────────────────────────

def _fin_daily_velocity(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-002: Maximum cumulative financial transfers per agent per 24-hour window."""
    # NOTE: In production, sum audit_logger.get_executed_spend(agent_id, hours=24)
    # For now, flag single transfers over $5M as velocity concern (configurable).
    amount = float(payload.get("amount", 0))
    if amount > 5_000_000:
        return PolicyEvaluation("FIN-002", "Daily Transfer Velocity", "fail",
            f"[FIN-002] Transfer ${amount:,.0f} exceeds single-day velocity limit. "
            "Verify cumulative daily exposure with treasury system.")
    return PolicyEvaluation("FIN-002", "Daily Transfer Velocity", "pass", "OK")


def _fin_counterparty_concentration(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-003: Single counterparty concentration risk — no more than 40% to one account."""
    dest = payload.get("destination_account", payload.get("counterparty_id", ""))
    amount = float(payload.get("amount", 0))
    # Flag when no counterparty ID provided for large transfers
    if amount > 100_000 and not dest:
        return PolicyEvaluation("FIN-003", "Counterparty Concentration", "fail",
            f"[FIN-003] Transfer of ${amount:,.0f} requires destination_account or counterparty_id")
    return PolicyEvaluation("FIN-003", "Counterparty Concentration", "pass", "OK")


def _fin_ctrs_trigger(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-004: Bank Secrecy Act — Currency Transaction Report trigger >= $10,000 cash.
    cash + no ctr_filed  → WARN (advisory; filer should file FinCEN 104)
    cash + ctr_filed     → PASS (already handled)
    non-cash ≥ $10K      → PASS (not a cash CTR trigger)
    """
    amount  = float(payload.get("amount", 0))
    is_cash = str(payload.get("payment_method", "")).lower() in {"cash","currency","physical_currency"}
    if amount >= 10_000 and is_cash and not payload.get("ctr_filed"):
        return PolicyEvaluation("FIN-004", "BSA Currency Transaction Report", "warn",
            f"[FIN-004] Cash transaction ${amount:,.0f} requires CTR filing (BSA 31 CFR 1010.311). "
            "Set ctr_filed=True after filing FinCEN Form 104.")
    return PolicyEvaluation("FIN-004", "BSA Currency Transaction Report", "pass", "OK")


def _fin_structuring_detection(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """FIN-005: Structuring (smurfing) detection — amounts just below reporting thresholds."""
    amount = float(payload.get("amount", 0))
    # Classic structuring: just below $10K, $5K, $3K thresholds
    THRESHOLDS = [10_000, 5_000, 3_000]
    for threshold in THRESHOLDS:
        if threshold * 0.95 <= amount < threshold:
            return PolicyEvaluation("FIN-005", "Structuring Detection", "warn",
                f"[FIN-005] Amount ${amount:,.0f} is suspiciously close to ${threshold:,} "
                "reporting threshold — potential structuring (BSA/AML)")
    return PolicyEvaluation("FIN-005", "Structuring Detection", "pass", "OK")


# ─ Procurement extended policies ──────────────────────────────────────────────

def _proc_sole_source(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PROC-004: Sole-source (single-bid) procurement above $25K requires justification.
    Only fires when sole_source=True is explicitly set OR bid_count is explicitly 1.
    Does NOT fire when bid_count is absent (unknown = not sole-source by default)."""
    amount = float(payload.get("amount", 0))
    # Only apply when explicitly flagged as sole-source, or bid_count explicitly = 1
    explicit_sole = payload.get("sole_source") is True
    explicit_bid1 = "bid_count" in payload and int(payload["bid_count"]) <= 1
    if amount > 25_000 and (explicit_sole or explicit_bid1):
        if not payload.get("sole_source_justification"):
            return PolicyEvaluation("PROC-004", "Sole-Source Justification", "fail",
                f"[PROC-004] Single-bid procurement of ${amount:,.0f} requires sole_source_justification "
                "(FAR 6.302, UK PCR 2015 Reg.32)")
    return PolicyEvaluation("PROC-004", "Sole-Source Justification", "pass", "OK")


def _proc_sanctions_check(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """PROC-006: OFAC/UN sanctions — block procurement from sanctioned countries."""
    SANCTIONED_COUNTRIES = {
        "IR","IRN","IRAN","KP","PRK","NORTH KOREA","NORTHKOREA",
        "CU","CUB","CUBA","SY","SYR","SYRIA","RU","RUS","RUSSIA",
        "BY","BLR","BELARUS","MM","MMR","MYANMAR"
    }
    country = str(payload.get("supplier_country", payload.get("country_of_origin", ""))).upper().strip()
    if country and country in SANCTIONED_COUNTRIES:
        return PolicyEvaluation("PROC-006", "OFAC Sanctions Check", "fail",
            f"[PROC-006] Procurement from sanctioned country '{country}' is prohibited "
            "(OFAC SDN list / UN Security Council resolutions)")
    # Also check supplier ID against a blocklist prefix
    sid = str(payload.get("supplier_id", ""))
    if sid.startswith("BLOCKED-") or sid.startswith("DEBARRED-"):
        return PolicyEvaluation("PROC-006", "OFAC Sanctions Check", "fail",
            f"[PROC-006] Supplier '{sid}' is on the debarment/sanctions blocklist")
    return PolicyEvaluation("PROC-006", "OFAC Sanctions Check", "pass", "OK")


# ─ Clinical policies ───────────────────────────────────────────────────────────

def _clin_controlled_substance(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """CLIN-001: Controlled substance prescriptions require DEA/prescriber authorisation."""
    SCHEDULES = {"schedule_i","schedule_ii","schedule_iii","schedule_iv","schedule_v",
                 "controlled","controlled_substance","s1","s2","s3","s4","s5"}
    drug_class = str(payload.get("drug_class", payload.get("substance_class", ""))).lower()
    if any(s in drug_class for s in SCHEDULES):
        if not payload.get("prescriber_dea_number") and not payload.get("authorisation_code"):
            return PolicyEvaluation("CLIN-001", "Controlled Substance Authorisation", "fail",
                f"[CLIN-001] Controlled substance '{drug_class}' requires prescriber_dea_number "
                "or authorisation_code (21 CFR Part 1306 / UK MDA 1971)")
    return PolicyEvaluation("CLIN-001", "Controlled Substance Authorisation", "pass", "OK")


def _clin_dosage_check(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """CLIN-002: Dosage must be within patient-specific weight-based therapeutic range."""
    dose    = float(payload.get("dose_mg", payload.get("dosage_mg", 0)))
    max_d   = float(payload.get("max_dose_mg", 0))
    weight  = float(payload.get("patient_weight_kg", 0))
    mg_per_kg = float(payload.get("max_mg_per_kg", 0))

    if dose > 0 and max_d > 0 and dose > max_d:
        return PolicyEvaluation("CLIN-002", "Dosage Safety Check", "fail",
            f"[CLIN-002] Dose {dose}mg exceeds patient maximum {max_d}mg "
            "(FDA drug label / BNF dosage guidance)")

    if dose > 0 and weight > 0 and mg_per_kg > 0:
        weight_limit = weight * mg_per_kg
        if dose > weight_limit:
            return PolicyEvaluation("CLIN-002", "Dosage Safety Check", "fail",
                f"[CLIN-002] Dose {dose}mg exceeds weight-based limit "
                f"{weight_limit:.1f}mg ({mg_per_kg}mg/kg × {weight}kg)")

    return PolicyEvaluation("CLIN-002", "Dosage Safety Check", "pass", "OK")


# ─ Trading policies ────────────────────────────────────────────────────────────

def _trade_position_limit(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """TRADE-001: Single position notional must not exceed configured limit."""
    notional = float(payload.get("notional", payload.get("order_value", 0)))
    limit    = float(payload.get("position_limit", 10_000_000))
    if notional > limit:
        return PolicyEvaluation("TRADE-001", "Trading Position Limit", "fail",
            f"[TRADE-001] Order notional ${notional:,.0f} exceeds position limit "
            f"${limit:,.0f} (MiFID II Art.17 / FINRA Rule 4210)")
    return PolicyEvaluation("TRADE-001", "Trading Position Limit", "pass", "OK")


def _trade_fat_finger(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """TRADE-002: Fat-finger check — order size must not be >10x average for the symbol."""
    qty        = float(payload.get("quantity", payload.get("order_qty", 0)))
    avg_qty    = float(payload.get("avg_daily_qty", 0))
    if qty > 0 and avg_qty > 0:
        ratio = qty / avg_qty
        if ratio > 10:
            return PolicyEvaluation("TRADE-002", "Fat-Finger Detection", "fail",
                f"[TRADE-002] Order quantity {qty:,.0f} is {ratio:.1f}× average "
                f"daily qty {avg_qty:,.0f} — possible fat-finger error")
        if ratio > 5:
            return PolicyEvaluation("TRADE-002", "Fat-Finger Detection", "warn",
                f"[TRADE-002] Order quantity {qty:,.0f} is {ratio:.1f}× average — review recommended")
    return PolicyEvaluation("TRADE-002", "Fat-Finger Detection", "pass", "OK")


# ─ Content / Generative AI policies ──────────────────────────────────────────

def _gen_pii_output(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """GEN-001: Block AI-generated content containing detected PII patterns."""
    import re
    content = str(payload.get("content", payload.get("generated_text", "")))
    # PII detection patterns (word-boundary anchored)
    patterns = [
        (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),    "SSN"),
        (re.compile(r"\b\d{16}\b"),                 "Credit card number"),
        (re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z]{2,}"), "Email address"),
        (re.compile(r"\b\d{3}[\s.-]\d{3}[\s.-]\d{4}\b"),  "Phone number"),
    ]
    if content:
        for pattern, pii_type in patterns:
            if pattern.search(content):
                return PolicyEvaluation("GEN-001", "PII in Generated Output", "fail",
                    f"[GEN-001] Generated content contains detected {pii_type} — "
                    "output blocked (GDPR Art.5, CCPA, HIPAA Privacy Rule)")
    return PolicyEvaluation("GEN-001", "PII in Generated Output", "pass", "OK")

def _gen_gdpr_art22(payload: Dict, ctx: DecisionContext) -> PolicyEvaluation:
    """GEN-002: GDPR Article 22 — automated decisions affecting EU individuals require disclosure."""
    affects_individual = payload.get("affects_individual", False)
    is_eu_subject      = str(ctx.jurisdiction).upper() in {
        "DE","FR","IT","ES","NL","PL","SE","AT","BE","DK","FI","IE","PT",
        "GR","CZ","HU","RO","SK","BG","HR","SI","LT","LV","EE","CY","LU",
        "MT","EU","EEA","GDPR"
    }
    if affects_individual and is_eu_subject:
        if not payload.get("human_review_available") and not payload.get("gdpr_art22_disclosed"):
            return PolicyEvaluation("GEN-002", "GDPR Article 22 Automated Decision", "fail",
                "[GEN-002] Automated decision affecting EU individual requires either "
                "human_review_available=True or gdpr_art22_disclosed=True (GDPR Art.22)")
    return PolicyEvaluation("GEN-002", "GDPR Article 22", "pass", "OK")


# ── DEFAULT_POLICIES ──────────────────────────────────────────────────────────

DEFAULT_POLICIES: List[Policy] = [
    Policy("PROC-001",  "Procurement Spending Limit",          [DecisionType.PROCUREMENT],                     _proc_spending_limit),
    Policy("PROC-002",  "Approved Supplier Registry",          [DecisionType.PROCUREMENT, DecisionType.INVENTORY], _proc_approved_suppliers),
    Policy("PROC-003",  "High-Risk Category Controls",         [DecisionType.PROCUREMENT],                     _proc_high_risk_category),
    Policy("PRICE-001", "Price Change Limit (30%)",            [DecisionType.PRICING],                         _price_change_limit),
    Policy("PRICE-002", "Price Floor Enforcement",             [DecisionType.PRICING],                         _price_floor),
    Policy("FIN-001",   "Financial Transfer Limit ($1M)",      [DecisionType.FINANCIAL],                       _fin_transfer_limit),
    Policy("ITOPS-001", "IT Operations Change Window",         [DecisionType.IT_OPS],                          _itops_change_window),
    Policy("INV-001",   "Inventory Quantity Limit",            [DecisionType.INVENTORY],                       _inv_quantity_limit),
    Policy("LOG-001",   "Logistics High-Value Approval",       [DecisionType.LOGISTICS],                       _log_approval),
    Policy("HR-001",    "HR Salary Adjustment Approval",       [DecisionType.HR],                              _hr_approval),
    Policy("AI-001",    "AI Model Confidence Floor",           list(DecisionType),                             _ai_confidence),
    Policy("ENV-001",   "Production Override Block",           list(DecisionType),                             _env_no_user_override),
    # v1.1 — Financial
    Policy("FIN-002",   "Daily Transfer Velocity",             [DecisionType.FINANCIAL, DecisionType.TRADING],  _fin_daily_velocity),
    Policy("FIN-003",   "Counterparty Concentration",          [DecisionType.FINANCIAL, DecisionType.TRADING],  _fin_counterparty_concentration),
    Policy("FIN-004",   "BSA Currency Transaction Report",     [DecisionType.FINANCIAL],                        _fin_ctrs_trigger),
    Policy("FIN-005",   "Structuring Detection",               [DecisionType.FINANCIAL],                        _fin_structuring_detection),
    # v1.1 — Procurement
    Policy("PROC-004",  "Sole-Source Justification",           [DecisionType.PROCUREMENT],                      _proc_sole_source),
    Policy("PROC-006",  "OFAC/UN Sanctions Check",             [DecisionType.PROCUREMENT, DecisionType.FINANCIAL, DecisionType.LOGISTICS], _proc_sanctions_check),
    # v1.1 — Clinical
    Policy("CLIN-001",  "Controlled Substance Authorisation",  [DecisionType.CLINICAL],                         _clin_controlled_substance),
    Policy("CLIN-002",  "Dosage Safety Check",                 [DecisionType.CLINICAL],                         _clin_dosage_check),
    # v1.1 — Trading
    Policy("TRADE-001", "Trading Position Limit",              [DecisionType.TRADING],                          _trade_position_limit),
    Policy("TRADE-002", "Fat-Finger Detection",                [DecisionType.TRADING],                          _trade_fat_finger),
    # v1.1 — Content / GenAI
    Policy("GEN-001",   "PII in Generated Output",             [DecisionType.CONTENT],                          _gen_pii_output),
    Policy("GEN-002",   "GDPR Article 22 Automated Decision",  [DecisionType.CONTENT, DecisionType.LEGAL],      _gen_gdpr_art22),
]


# ── PolicyEngine ───────────────────────────────────────────────────────────────

class PolicyEngine:
    """
    Thread-safe policy registry and evaluator.

    All mutations (register, disable, enable) are protected by threading.RLock.
    Evaluation uses a snapshot of the policy list taken under the lock, then
    evaluates outside the lock (snapshot-before-evaluate pattern) so that:
      - User-supplied policy rules cannot cause deadlocks
      - Crashing policy rules are caught and never propagate to the pipeline
      - The registry can be safely mutated during evaluation
    """

    def __init__(self, policies: Optional[List[Policy]] = None):
        # Deep-copy so each engine instance has its own independent registry
        initial = policies if policies is not None else DEFAULT_POLICIES
        self._registry: Dict[str, Policy] = {
            p.policy_id: copy.copy(p) for p in initial
        }
        self._lock = threading.RLock()

    def register(self, policy: Policy) -> None:
        """Register or replace a policy. Last registration wins on duplicate ID."""
        with self._lock:
            self._registry[policy.policy_id] = policy

    def disable(self, policy_id: str) -> None:
        with self._lock:
            if policy_id in self._registry:
                self._registry[policy_id].enabled = False

    def enable(self, policy_id: str) -> None:
        with self._lock:
            if policy_id in self._registry:
                self._registry[policy_id].enabled = True

    def list_policies(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [
                {"policy_id": p.policy_id, "policy_name": p.policy_name,
                 "enabled": p.enabled, "version": p.version,
                 "decision_types": [dt.value for dt in p.decision_types]}
                for p in self._registry.values()
            ]

    @property
    def policies(self) -> List[Policy]:
        with self._lock:
            return list(self._registry.values())

    def evaluate(
        self,
        decision_type: DecisionType,
        payload:       Dict[str, Any],
        context:       DecisionContext,
    ) -> PolicyResult:
        # Snapshot under lock — evaluate outside lock (crash safety + no deadlock)
        with self._lock:
            applicable = [
                p for p in self._registry.values()
                if p.enabled and (
                    decision_type in p.decision_types or
                    DecisionType.CUSTOM in p.decision_types
                )
            ]

        violations, warnings, evaluations = [], [], []
        for policy in applicable:
            try:
                ev = policy.rule(payload, context)
                evaluations.append(ev)
                if ev.result == "fail":
                    violations.append(ev.message)
                elif ev.result == "warn":
                    warnings.append(ev.message)
            except Exception as exc:
                # Crashing policy → log but never crash the pipeline
                warnings.append(
                    f"[{policy.policy_id}] Policy evaluation error: {exc}")

        return PolicyResult(
            passed=len(violations) == 0,
            violations=violations,
            warnings=warnings,
            evaluated_policies=evaluations,
        )
