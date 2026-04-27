"""
GlassBox — Industry Use Case Examples  (v1.0.0)
================================================
Comprehensive, runnable examples demonstrating GlassBox governance
across 18 enterprise industry verticals.

Every example shows:
  - The governance problem specific to that industry
  - The policy controls that address it
  - Live decisions with outcomes (EXECUTED / BLOCKED / HUMAN_REVIEW)
  - Compliance framework alignment

Usage:
    python3 examples/industry_examples.py              # run all
    python3 examples/industry_examples.py --list       # list examples
    python3 examples/industry_examples.py --id 3       # run one

Author: Mohammed Akbar Ansari — Independent Researcher
"""

import os, sys, argparse
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
os.environ.setdefault("GLASSBOX_LOG_LEVEL", "CRITICAL")

from glassbox.governance.pipeline    import GovernancePipeline
from glassbox.governance.policy_engine import Policy, PolicyEngine, FleetBudgetPolicy
from glassbox.governance.models      import (
    AgentContract, DecisionContext, DecisionRequest,
    DecisionType, FinalStatus, PolicyEvaluation,
)


# ─── helpers ─────────────────────────────────────────────────────────────────

def _pipeline(**kw) -> GovernancePipeline:
    return GovernancePipeline(echo=False, **kw)

def _req(agent, dtype, payload, conf=0.92, env="production", chain=None):
    ctx = DecisionContext(
        confidence=conf, environment=env,
        agent_chain=chain or [], source_system="example",
    )
    return DecisionRequest(agent_id=agent, decision_type=dtype,
                           payload=payload, context=ctx)

def _hdr(title, subtitle=""):
    width = 70
    print()
    print("=" * width)
    print(f"  {title}")
    if subtitle:
        print(f"  {subtitle}")
    print("=" * width)

def _show(response, label=""):
    icon = {"executed": "✓", "blocked": "✗", "pending_review": "⏳"}.get(
        response.final_status.value if response.final_status else "", "?")
    tag = f"  [{label}]" if label else ""
    risk_str = f"{response.risk_score:.1f}" if response.risk_score is not None else "n/a"
    lat_str  = f"{response.pipeline_latency_ms:.2f}ms" if response.pipeline_latency_ms else ""
    print(f"  {icon}{tag} {response.final_status.value.upper():15s}  risk={risk_str}  {lat_str}")
    for v in response.policy_violations:
        print(f"    ↳ {v}")
    for w in (response.policy_warnings or [])[:1]:
        print(f"    ⚠ {w}")

def _section(text):
    print(f"\n  ── {text}")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 1 — Financial Services: Algorithmic Trading
# ══════════════════════════════════════════════════════════════════════════════

def example_01_financial_trading():
    _hdr("EXAMPLE 1 — Financial Services: Algorithmic Trading",
         "MiFID II order governance · position limits · confidence floors")

    pipeline = _pipeline()
    pipeline.register_contract(AgentContract(
        agent_id="fx_algo", permitted_types=[DecisionType.FINANCIAL],
        max_amount=500_000, delegation_allowed=False,
    ))

    _section("Normal FX order — should execute")
    _show(pipeline.process(_req("fx_algo", DecisionType.FINANCIAL,
        {"amount": 250_000, "destination_account": "BROKER-001",
         "reference": "FX-TRD-001", "currency_pair": "GBPUSD"})), "normal order")

    _section("Order above agent authority — should block")
    _show(pipeline.process(_req("fx_algo", DecisionType.FINANCIAL,
        {"amount": 1_500_000, "destination_account": "BROKER-001",
         "reference": "FX-TRD-002"})), "over authority")

    _section("Order with low model confidence — should block (AI-001)")
    _show(pipeline.process(_req("fx_algo", DecisionType.FINANCIAL,
        {"amount": 100_000, "destination_account": "BROKER-001",
         "reference": "FX-TRD-003"}, conf=0.15)), "low confidence")

    _section("Fleet budget approaching limit (5 agents × $180K = $900K of $1M budget)")
    fleet = FleetBudgetPolicy(budget=1_000_000, warn_threshold=0.80)
    fleet_pipeline = _pipeline()
    fleet_pipeline.policy_engine.register(fleet.as_policy())
    # Simulate 5 regional agents each spending $180K
    for i in range(5):
        r = fleet_pipeline.process(_req(f"regional_{i}", DecisionType.FINANCIAL,
            {"amount": 180_000, "destination_account": f"BROKER-{i:03d}",
             "reference": f"REG-{i:03d}"}))
        fleet.record_execution(180_000 if r.final_status == FinalStatus.EXECUTED else 0)
    _show(fleet_pipeline.process(_req("regional_5", DecisionType.FINANCIAL,
        {"amount": 180_000, "destination_account": "BROKER-005",
         "reference": "REG-005"})), "fleet budget near limit")

    print("\n  Compliance: FIN-001 (transfer limit) · AI-001 (confidence) ·"
          " AGG-001 (fleet budget)")
    print("  Regulatory: MiFID II Article 17 (algorithmic trading controls)")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 2 — Healthcare: Clinical AI Governance
# ══════════════════════════════════════════════════════════════════════════════

def example_02_healthcare():
    _hdr("EXAMPLE 2 — Healthcare: Clinical AI Prescription Governance",
         "FDA 21 CFR Part 11 · dosage controls · controlled substance oversight")

    pipeline = _pipeline()

    # Custom clinical policies
    def controlled_substance_policy(payload, ctx):
        CONTROLLED = {"morphine","oxycodone","fentanyl","ketamine",
                      "alprazolam","methylphenidate","amphetamine"}
        drug = str(payload.get("drug_name", "")).lower()
        if any(c in drug for c in CONTROLLED) and not payload.get("physician_cosign_id"):
            return PolicyEvaluation("CLIN-001", "Controlled Substance", "fail",
                f"[CLIN-001] '{drug}' requires physician_cosign_id")
        return PolicyEvaluation("CLIN-001", "Controlled Substance", "pass", "OK")

    def dosage_limit_policy(payload, ctx):
        prescribed = float(payload.get("dose_mg", 0))
        max_dose   = float(payload.get("max_dose_mg", float("inf")))
        if prescribed > max_dose:
            return PolicyEvaluation("CLIN-002", "Dosage Safety Limit", "fail",
                f"[CLIN-002] Prescribed dose {prescribed}mg exceeds "
                f"patient maximum {max_dose}mg")
        return PolicyEvaluation("CLIN-002", "Dosage Safety Limit", "pass", "OK")

    def allergy_check_policy(payload, ctx):
        drug       = str(payload.get("drug_name", "")).lower()
        allergies  = [a.lower() for a in payload.get("patient_allergies", [])]
        for allergen in allergies:
            if allergen in drug or drug in allergen:
                return PolicyEvaluation("CLIN-003", "Allergy Check", "fail",
                    f"[CLIN-003] Patient has documented allergy to '{allergen}'")
        return PolicyEvaluation("CLIN-003", "Allergy Check", "pass", "OK")

    for p, types in [
        (controlled_substance_policy, [DecisionType.CUSTOM]),
        (dosage_limit_policy,         [DecisionType.CUSTOM]),
        (allergy_check_policy,        [DecisionType.CUSTOM]),
    ]:
        pipeline.policy_engine.register(Policy(
            policy_id=p.__name__, policy_name=p.__name__, decision_types=types, rule=p))

    _section("Standard antibiotic prescription — should execute")
    _show(pipeline.process(_req("clinical_ai", DecisionType.CUSTOM,
        {"drug_name": "amoxicillin", "dose_mg": 500, "max_dose_mg": 1000,
         "patient_allergies": ["penicillin-alternative"], "indication": "sinusitis"},
        conf=0.94)), "amoxicillin 500mg")

    _section("Controlled substance without physician co-sign — should block")
    _show(pipeline.process(_req("clinical_ai", DecisionType.CUSTOM,
        {"drug_name": "oxycodone", "dose_mg": 5, "max_dose_mg": 20,
         "indication": "post-surgical pain"},
        conf=0.88)), "oxycodone no cosign")

    _section("Dosage 10× above safe maximum — should block")
    _show(pipeline.process(_req("clinical_ai", DecisionType.CUSTOM,
        {"drug_name": "metformin", "dose_mg": 8500, "max_dose_mg": 850,
         "physician_cosign_id": "DR-001", "indication": "T2DM"},
        conf=0.72)), "10x overdose")

    _section("Allergy conflict detected — should block")
    _show(pipeline.process(_req("clinical_ai", DecisionType.CUSTOM,
        {"drug_name": "penicillin", "dose_mg": 500, "max_dose_mg": 2000,
         "patient_allergies": ["penicillin"], "physician_cosign_id": "DR-001",
         "indication": "wound infection"},
        conf=0.91)), "allergy conflict")

    _section("Correctly co-signed controlled substance — should execute")
    _show(pipeline.process(_req("clinical_ai", DecisionType.CUSTOM,
        {"drug_name": "morphine", "dose_mg": 2, "max_dose_mg": 10,
         "physician_cosign_id": "DR-042", "patient_allergies": [],
         "indication": "palliative pain management"},
        conf=0.93)), "valid morphine with cosign")

    print("\n  Compliance: EUAI.A9 (risk management) · EUAI.A12 (audit trail)")
    print("  Regulatory: FDA 21 CFR Part 11 · Joint Commission patient safety")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 3 — Global Supply Chain & Procurement Governance
# ══════════════════════════════════════════════════════════════════════════════

def example_03_supply_chain():
    _hdr("EXAMPLE 3 — Global Supply Chain: Multi-Tier Procurement Governance",
         "4-tier chain · supplier debarment · export controls · aggregate limits")

    pipeline = _pipeline()

    # Supply chain specific policies
    def debarred_supplier_policy(payload, ctx):
        DEBARRED = {"SANCTIONED-CORP", "DEBARRED-01", "WATCHLIST-07",
                    "OFAC-BLOCKED", "EU-SANCTIONS-02"}
        sid = payload.get("supplier_id", "")
        if sid in DEBARRED:
            return PolicyEvaluation("SC-001", "Debarred Supplier Block", "fail",
                f"[SC-001] Supplier '{sid}' is on the debarment/sanctions list")
        return PolicyEvaluation("SC-001", "Debarred Supplier Block", "pass", "OK")

    def export_control_policy(payload, ctx):
        CONTROLLED_COUNTRIES = {"Iran", "North Korea", "Russia", "Cuba", "Syria"}
        CONTROLLED_ITEMS = {"semiconductor", "encryption", "military-grade",
                            "dual-use", "nuclear"}
        dest    = payload.get("destination_country", "")
        item    = str(payload.get("category", payload.get("item_description", ""))).lower()
        if dest in CONTROLLED_COUNTRIES:
            return PolicyEvaluation("SC-002", "Export Control", "fail",
                f"[SC-002] Shipment to '{dest}' requires export licence review")
        if any(c in item for c in CONTROLLED_ITEMS) and not payload.get("export_licence_ref"):
            return PolicyEvaluation("SC-002", "Export Control", "fail",
                f"[SC-002] Item category '{item}' requires export_licence_ref")
        return PolicyEvaluation("SC-002", "Export Control", "pass", "OK")

    def single_source_risk_policy(payload, ctx):
        # Warn if procurement is single-source for critical components
        if (payload.get("single_source", False) and
                float(payload.get("amount", 0)) > 100_000):
            return PolicyEvaluation("SC-003", "Single Source Risk", "warn",
                "[SC-003] Single-source procurement above $100K — consider dual sourcing")
        return PolicyEvaluation("SC-003", "Single Source Risk", "pass", "OK")

    def tier3_supplier_due_diligence(payload, ctx):
        # Tier-3 and deeper suppliers require enhanced due diligence
        tier = int(payload.get("supplier_tier", 1))
        if tier >= 3 and not payload.get("due_diligence_ref"):
            return PolicyEvaluation("SC-004", "Tier-3 Due Diligence", "fail",
                f"[SC-004] Tier-{tier} supplier requires due_diligence_ref")
        return PolicyEvaluation("SC-004", "Tier-3 Due Diligence", "pass", "OK")

    for p in [debarred_supplier_policy, export_control_policy,
              single_source_risk_policy, tier3_supplier_due_diligence]:
        pipeline.policy_engine.register(Policy(
            p.__name__, p.__name__, [DecisionType.PROCUREMENT], p))

    _section("Tier-1 supplier — standard order should execute")
    _show(pipeline.process(_req("procurement_ai", DecisionType.PROCUREMENT,
        {"amount": 75_000, "supplier_id": "SUP-001", "category": "automotive_parts",
         "supplier_tier": 1, "destination_country": "Germany", "contract_id": "CT-001"})),
        "tier-1 standard")

    _section("Sanctioned supplier — must be blocked immediately")
    _show(pipeline.process(_req("procurement_ai", DecisionType.PROCUREMENT,
        {"amount": 15_000, "supplier_id": "SANCTIONED-CORP",
         "category": "electronics", "supplier_tier": 2})),
        "sanctioned supplier")

    _section("Export controlled item to restricted country")
    _show(pipeline.process(_req("procurement_ai", DecisionType.PROCUREMENT,
        {"amount": 45_000, "supplier_id": "SUP-010", "category": "encryption",
         "destination_country": "Iran", "supplier_tier": 1})),
        "export violation")

    _section("Tier-3 supplier — no due diligence ref — should block")
    _show(pipeline.process(_req("procurement_ai", DecisionType.PROCUREMENT,
        {"amount": 250_000, "supplier_id": "TIER3-MFGR",
         "category": "components", "supplier_tier": 3, "contract_id": "CT-002"})),
        "tier-3 no DD")

    _section("Large single-source order — should warn")
    _show(pipeline.process(_req("procurement_ai", DecisionType.PROCUREMENT,
        {"amount": 450_000, "supplier_id": "SUP-003", "category": "rare_earth",
         "single_source": True, "contract_id": "CT-003",
         "supplier_tier": 1, "destination_country": "USA"})),
        "single source warning")

    # 4-tier agent chain: demand → selection → compliance → execution
    _section("4-tier agent chain with lineage tracking")
    from glassbox.orchestration.orchestrator import AgentOrchestrator, AgentNode
    orch = AgentOrchestrator(pipeline)
    nodes = [
        AgentNode("demand",     "demand_forecast_ai",  DecisionType.CUSTOM,
                  lambda ctx: {"description": "demand_forecast", "amount": 0}),
        AgentNode("selection",  "supplier_selection_ai", DecisionType.PROCUREMENT,
                  lambda ctx: {"amount": 200_000, "supplier_id": "SUP-002",
                               "category": "raw_materials", "supplier_tier": 1,
                               "contract_id": "CT-004", "destination_country": "UK"},
                  depends_on=["demand"]),
        AgentNode("compliance", "compliance_ai", DecisionType.PROCUREMENT,
                  lambda ctx: {"amount": 200_000, "supplier_id": "SUP-002",
                               "category": "raw_materials", "supplier_tier": 1,
                               "contract_id": "CT-004", "destination_country": "UK",
                               "due_diligence_ref": "DD-2024-001"},
                  depends_on=["selection"]),
    ]
    result = orch.run_graph(nodes)
    print(f"  Chain status: {result.status}  "
          f"nodes={list(result.node_results.keys())}")
    orch.shutdown()

    print("\n  Compliance: PROC-001/002/003 · SC-001/002/003/004")
    print("  Regulatory: OFAC sanctions · EU Export Controls · UK CSDDD")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 4 — Pharmaceutical: GxP & FDA 21 CFR Part 11
# ══════════════════════════════════════════════════════════════════════════════

def example_04_pharmaceutical():
    _hdr("EXAMPLE 4 — Pharmaceutical: GxP Manufacturing & Clinical Trials",
         "FDA 21 CFR Part 11 · batch release · deviation controls · audit trail")

    pipeline = _pipeline()

    def gxp_batch_release_policy(payload, ctx):
        """All batch release decisions require QA approval and certificate of analysis."""
        if payload.get("decision_subtype") == "batch_release":
            if not payload.get("qa_approval_id"):
                return PolicyEvaluation("GXP-001", "QA Batch Release Approval", "fail",
                    "[GXP-001] Batch release requires qa_approval_id (21 CFR 211.192)")
            if not payload.get("certificate_of_analysis_ref"):
                return PolicyEvaluation("GXP-001", "QA Batch Release Approval", "fail",
                    "[GXP-001] Batch release requires certificate_of_analysis_ref")
        return PolicyEvaluation("GXP-001", "QA Batch Release Approval", "pass", "OK")

    def gxp_deviation_policy(payload, ctx):
        """Critical deviations in manufacturing require CAPA before proceeding."""
        if payload.get("open_critical_deviations", 0) > 0:
            return PolicyEvaluation("GXP-002", "Open Critical Deviations", "fail",
                f"[GXP-002] Batch has {payload['open_critical_deviations']} "
                f"open critical deviation(s) — CAPA required before release")
        return PolicyEvaluation("GXP-002", "Open Critical Deviations", "pass", "OK")

    def clinical_trial_data_integrity(payload, ctx):
        """Clinical trial data modifications require audit trail compliance."""
        if (payload.get("data_type") == "clinical_trial" and
                not payload.get("audit_trail_locked", False)):
            return PolicyEvaluation("GXP-003", "Clinical Data Integrity", "fail",
                "[GXP-003] Clinical trial data requires audit trail lock "
                "(21 CFR Part 11.10(e))")
        return PolicyEvaluation("GXP-003", "Clinical Data Integrity", "pass", "OK")

    for p in [gxp_batch_release_policy, gxp_deviation_policy,
              clinical_trial_data_integrity]:
        pipeline.policy_engine.register(Policy(
            p.__name__, p.__name__, [DecisionType.CUSTOM], p))

    _section("Valid batch release with full QA package — should execute")
    _show(pipeline.process(_req("pharma_ai", DecisionType.CUSTOM,
        {"decision_subtype": "batch_release", "batch_id": "BATCH-2024-0042",
         "product": "aspirin_100mg", "quantity_units": 50_000,
         "qa_approval_id": "QA-APPR-7891",
         "certificate_of_analysis_ref": "COA-2024-0042",
         "open_critical_deviations": 0})), "valid batch")

    _section("Batch release — open critical deviations — must block")
    _show(pipeline.process(_req("pharma_ai", DecisionType.CUSTOM,
        {"decision_subtype": "batch_release", "batch_id": "BATCH-2024-0043",
         "qa_approval_id": "QA-APPR-7892",
         "certificate_of_analysis_ref": "COA-2024-0043",
         "open_critical_deviations": 2})), "open deviations")

    _section("Batch release — missing QA approval — must block")
    _show(pipeline.process(_req("pharma_ai", DecisionType.CUSTOM,
        {"decision_subtype": "batch_release", "batch_id": "BATCH-2024-0044",
         "certificate_of_analysis_ref": "COA-2024-0044",
         "open_critical_deviations": 0})), "missing QA approval")

    _section("Clinical trial data — audit trail not locked — must block")
    _show(pipeline.process(_req("clinical_data_ai", DecisionType.CUSTOM,
        {"data_type": "clinical_trial", "trial_id": "CT-PHASE3-007",
         "modification_type": "efficacy_endpoint",
         "audit_trail_locked": False})), "unlocked audit trail")

    print("\n  Compliance: GXP-001/002/003 · EUAI.A12 (audit trail)")
    print("  Regulatory: FDA 21 CFR Part 11 · EU GMP Annex 11 · ICH E6(R3)")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 5 — Retail Banking: Credit & Payments Governance
# ══════════════════════════════════════════════════════════════════════════════

def example_05_retail_banking():
    _hdr("EXAMPLE 5 — Retail Banking: AI Credit & Payments Governance",
         "PSD2 · GDPR · Basel III · responsible lending · AML controls")

    pipeline = _pipeline()

    def aml_screening_policy(payload, ctx):
        """AML: high-value transactions to flagged jurisdictions require SAR review."""
        HIGH_RISK_JURISDICTIONS = {"Cayman Islands", "Panama", "Vanuatu",
                                   "Malta (shell)", "Seychelles"}
        amount = float(payload.get("amount", 0))
        dest   = payload.get("destination_country", "")
        if amount > 10_000 and dest in HIGH_RISK_JURISDICTIONS:
            return PolicyEvaluation("AML-001", "AML High-Risk Jurisdiction", "fail",
                f"[AML-001] ${amount:,.0f} to '{dest}' requires SAR review "
                f"(FATF recommendation 16)")
        return PolicyEvaluation("AML-001", "AML High-Risk Jurisdiction", "pass", "OK")

    def responsible_lending_policy(payload, ctx):
        """Responsible lending: AI credit decisions must include affordability assessment."""
        if payload.get("product_type") in ("personal_loan", "credit_card", "mortgage"):
            if not payload.get("affordability_assessment_ref"):
                return PolicyEvaluation("BANK-001", "Responsible Lending", "fail",
                    "[BANK-001] Credit decision requires affordability_assessment_ref "
                    "(FCA Consumer Duty)")
            dti = float(payload.get("debt_to_income_ratio", 0))
            if dti > 0.50:
                return PolicyEvaluation("BANK-001", "Responsible Lending", "fail",
                    f"[BANK-001] Debt-to-income ratio {dti:.0%} exceeds 50% limit")
        return PolicyEvaluation("BANK-001", "Responsible Lending", "pass", "OK")

    def psd2_strong_auth_policy(payload, ctx):
        """PSD2 SCA: payments above €30 require strong customer authentication."""
        amount = float(payload.get("amount", 0))
        if amount > 30 and not payload.get("sca_completed", False):
            return PolicyEvaluation("PSD2-001", "Strong Customer Authentication", "fail",
                f"[PSD2-001] Payment of €{amount:.0f} requires SCA (PSD2 Article 97)")
        return PolicyEvaluation("PSD2-001", "Strong Customer Authentication", "pass", "OK")

    for p in [aml_screening_policy, responsible_lending_policy, psd2_strong_auth_policy]:
        pipeline.policy_engine.register(Policy(p.__name__, p.__name__,
            [DecisionType.FINANCIAL, DecisionType.CUSTOM], p))

    _section("Standard domestic payment with SCA — should execute")
    _show(pipeline.process(_req("banking_ai", DecisionType.FINANCIAL,
        {"amount": 500, "destination_account": "ACC-UK-001",
         "destination_country": "United Kingdom",
         "sca_completed": True, "reference": "PAY-001"})), "domestic SCA payment")

    _section("Payment to high-risk jurisdiction — AML block")
    _show(pipeline.process(_req("banking_ai", DecisionType.FINANCIAL,
        {"amount": 25_000, "destination_account": "ACC-CAYMAN-001",
         "destination_country": "Cayman Islands",
         "sca_completed": True, "reference": "PAY-002"})), "AML jurisdiction block")

    _section("Payment without SCA — PSD2 block")
    _show(pipeline.process(_req("banking_ai", DecisionType.FINANCIAL,
        {"amount": 150, "destination_account": "ACC-EU-003",
         "destination_country": "France",
         "sca_completed": False, "reference": "PAY-003"})), "missing SCA")

    _section("Personal loan — high DTI — responsible lending block")
    _show(pipeline.process(_req("credit_ai", DecisionType.CUSTOM,
        {"product_type": "personal_loan", "amount": 20_000,
         "debt_to_income_ratio": 0.58,
         "affordability_assessment_ref": "AFA-2024-001",
         "applicant_id": "CUST-12345"})), "high DTI loan block")

    _section("Mortgage with compliant DTI — should execute")
    _show(pipeline.process(_req("credit_ai", DecisionType.CUSTOM,
        {"product_type": "mortgage", "amount": 350_000,
         "debt_to_income_ratio": 0.32,
         "affordability_assessment_ref": "AFA-2024-002",
         "sca_completed": True,
         "applicant_id": "CUST-12346"})), "valid mortgage")

    print("\n  Compliance: AML-001 · BANK-001 · PSD2-001 · FIN-001")
    print("  Regulatory: PSD2 · FCA Consumer Duty · FATF R16 · Basel III")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 6 — Energy: Grid Dispatch & NERC CIP Governance
# ══════════════════════════════════════════════════════════════════════════════

def example_06_energy_grid():
    _hdr("EXAMPLE 6 — Energy & Utilities: Grid Dispatch & Cybersecurity",
         "NERC CIP · dual authorisation · critical asset protection · cyberattack defence")

    pipeline = _pipeline()

    def critical_grid_operation_policy(payload, ctx):
        """NERC CIP-006/007: Critical BES operations require dual authorisation."""
        CRITICAL_ACTIONS = {"trip_breaker", "isolate_substation", "shed_load",
                            "emergency_redispatch", "frequency_correction"}
        action = str(payload.get("action_type", "")).lower().replace(" ", "_")
        if any(c in action for c in CRITICAL_ACTIONS):
            if not payload.get("operator_auth_code"):
                return PolicyEvaluation("GRID-001", "Dual Auth: Operator", "fail",
                    f"[GRID-001] Critical grid action '{action}' requires operator_auth_code")
            if not payload.get("supervisor_auth_code"):
                return PolicyEvaluation("GRID-001", "Dual Auth: Supervisor", "fail",
                    f"[GRID-001] Critical grid action '{action}' requires supervisor_auth_code")
        return PolicyEvaluation("GRID-001", "Critical Grid Operation", "pass", "OK")

    def renewable_dispatch_limit_policy(payload, ctx):
        """Renewable dispatch: curtailment above 20% requires grid stability review."""
        curtailment = float(payload.get("curtailment_pct", 0))
        if curtailment > 20 and not payload.get("stability_review_ref"):
            return PolicyEvaluation("GRID-002", "Renewable Curtailment Limit", "fail",
                f"[GRID-002] Curtailment of {curtailment:.0f}% requires stability_review_ref")
        return PolicyEvaluation("GRID-002", "Renewable Curtailment Limit", "pass", "OK")

    def energy_trading_position_policy(payload, ctx):
        """Energy trading: single trade position limit."""
        amount = float(payload.get("trade_value_usd", 0))
        if amount > 2_000_000 and not payload.get("risk_approval_ref"):
            return PolicyEvaluation("GRID-003", "Trading Position Limit", "fail",
                f"[GRID-003] Trade value ${amount:,.0f} exceeds $2M limit — "
                f"requires risk_approval_ref")
        return PolicyEvaluation("GRID-003", "Trading Position Limit", "pass", "OK")

    for p in [critical_grid_operation_policy, renewable_dispatch_limit_policy,
              energy_trading_position_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.IT_OPS, DecisionType.CUSTOM], rule=p))

    _section("Routine grid optimisation — should execute")
    _show(pipeline.process(_req("grid_ai", DecisionType.IT_OPS,
        {"action": "adjust_tap_changer", "action_type": "voltage_regulation",
         "target_substation": "SUB-NW-042", "change_window_approved": True})),
        "routine optimisation")

    _section("Critical breaker trip — no dual auth — must block")
    _show(pipeline.process(_req("grid_ai", DecisionType.IT_OPS,
        {"action": "trip_345kv_breaker", "action_type": "trip_breaker",
         "target_substation": "SUB-TRANSMISSION-007",
         "change_window_approved": True})),
        "breaker trip no auth")

    _section("Cyberattack: injected command — security pre-check blocks")
    _show(pipeline.process(_req("grid_ai", DecisionType.IT_OPS,
        {"action": "'; DROP TABLE grid_state; --",
         "action_type": "emergency_redispatch",
         "operator_auth_code": "OPS-123", "supervisor_auth_code": "SUP-456",
         "change_window_approved": True})),
        "SQL injection attack")

    _section("Dual-authorised critical operation — should execute")
    _show(pipeline.process(_req("grid_ai", DecisionType.IT_OPS,
        {"action": "isolate_faulty_feeder", "action_type": "isolate_substation",
         "target": "FEEDER-NW-12", "change_window_approved": True,
         "operator_auth_code": "OPS-CERT-7891",
         "supervisor_auth_code": "SUP-CERT-4523"})),
        "valid dual-auth operation")

    _section("Renewable curtailment above 20% — needs stability review")
    _show(pipeline.process(_req("renewable_ai", DecisionType.CUSTOM,
        {"action_type": "curtailment", "curtailment_pct": 35,
         "asset": "WIND-FARM-NE-003", "reason": "frequency_deviation"})),
        "excess curtailment")

    print("\n  Compliance: GRID-001/002/003 · ITOPS-001")
    print("  Regulatory: NERC CIP-006/007/010 · AEMO AESCSF · IEC 62443")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 7 — Manufacturing: Smart Factory & Industry 4.0
# ══════════════════════════════════════════════════════════════════════════════

def example_07_manufacturing():
    _hdr("EXAMPLE 7 — Manufacturing: Smart Factory Production Governance",
         "ISO 9001 · capacity limits · preventive maintenance · quality controls")

    pipeline = _pipeline()

    def production_capacity_policy(payload, ctx):
        """Cannot schedule production beyond shift capacity."""
        units       = int(payload.get("units_scheduled", 0))
        shift_cap   = int(payload.get("shift_capacity", 10_000))
        if units > shift_cap:
            return PolicyEvaluation("MFG-001", "Shift Capacity Limit", "fail",
                f"[MFG-001] {units:,} units exceeds shift capacity of {shift_cap:,}")
        return PolicyEvaluation("MFG-001", "Shift Capacity Limit", "pass", "OK")

    def maintenance_window_policy(payload, ctx):
        """Production scheduling requires no active maintenance lockouts."""
        if payload.get("maintenance_lockout", False):
            return PolicyEvaluation("MFG-002", "Maintenance Lockout", "fail",
                "[MFG-002] Equipment under maintenance lockout — "
                "production scheduling blocked")
        return PolicyEvaluation("MFG-002", "Maintenance Lockout", "pass", "OK")

    def quality_parameter_policy(payload, ctx):
        """Process parameters outside validated range trigger quality hold."""
        temp = float(payload.get("process_temp_celsius", 0))
        if temp != 0 and not (180 <= temp <= 220):
            return PolicyEvaluation("MFG-003", "Process Parameter Validation", "fail",
                f"[MFG-003] Process temperature {temp}°C outside validated range "
                f"180–220°C — quality hold required")
        return PolicyEvaluation("MFG-003", "Process Parameter Validation", "pass", "OK")

    for p in [production_capacity_policy, maintenance_window_policy,
              quality_parameter_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.CUSTOM, DecisionType.INVENTORY], rule=p))

    _section("Standard production run — should execute")
    _show(pipeline.process(_req("factory_ai", DecisionType.CUSTOM,
        {"action": "schedule_production", "product_id": "PROD-A100",
         "units_scheduled": 2_500, "shift_capacity": 8_000,
         "process_temp_celsius": 195, "maintenance_lockout": False})),
        "standard production run")

    _section("Over-capacity scheduling — should block")
    _show(pipeline.process(_req("factory_ai", DecisionType.CUSTOM,
        {"action": "schedule_production", "product_id": "PROD-A100",
         "units_scheduled": 12_000, "shift_capacity": 8_000,
         "process_temp_celsius": 195})),
        "over capacity")

    _section("Production on equipment under maintenance lockout — must block")
    _show(pipeline.process(_req("factory_ai", DecisionType.CUSTOM,
        {"action": "schedule_production", "product_id": "PROD-B200",
         "units_scheduled": 1_000, "shift_capacity": 8_000,
         "maintenance_lockout": True})),
        "maintenance lockout")

    _section("Process temperature out of validated range — quality hold")
    _show(pipeline.process(_req("factory_ai", DecisionType.CUSTOM,
        {"action": "schedule_production", "product_id": "PROD-A100",
         "units_scheduled": 2_000, "shift_capacity": 8_000,
         "process_temp_celsius": 240, "maintenance_lockout": False})),
        "out-of-range temperature")

    print("\n  Compliance: MFG-001/002/003 · ITOPS-001")
    print("  Regulatory: ISO 9001:2015 · IEC 62443 OT layer · Purdue L3-L4")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 8 — Insurance: Automated Claims Processing
# ══════════════════════════════════════════════════════════════════════════════

def example_08_insurance():
    _hdr("EXAMPLE 8 — Insurance: Automated Claims & Underwriting Governance",
         "Solvency II · fraud detection · reserve adequacy · senior oversight")

    pipeline = _pipeline()

    def large_claim_approval_policy(payload, ctx):
        amount = float(payload.get("claim_amount", 0))
        if amount > 500_000 and not payload.get("senior_adjuster_ref"):
            return PolicyEvaluation("INS-001", "Large Claim Approval", "fail",
                f"[INS-001] Claim of ${amount:,.0f} requires senior_adjuster_ref")
        return PolicyEvaluation("INS-001", "Large Claim Approval", "pass", "OK")

    def fraud_indicator_policy(payload, ctx):
        score = float(payload.get("fraud_score", 0))
        if score > 0.7:
            return PolicyEvaluation("INS-002", "Fraud Indicator", "fail",
                f"[INS-002] Fraud score {score:.2f} exceeds 0.70 threshold — "
                f"manual investigation required")
        elif score > 0.4:
            return PolicyEvaluation("INS-002", "Fraud Indicator", "warn",
                f"[INS-002] Elevated fraud score {score:.2f} — enhanced review recommended")
        return PolicyEvaluation("INS-002", "Fraud Indicator", "pass", "OK")

    def solvency_reserve_policy(payload, ctx):
        """Solvency II: ensure claims do not deplete reserves below SCR buffer."""
        exposure = float(payload.get("total_exposure_after_claim", 0))
        scr_buffer = float(payload.get("scr_buffer_pct", 100))
        if scr_buffer < 110:  # must maintain 110% SCR
            return PolicyEvaluation("INS-003", "Solvency Reserve", "fail",
                f"[INS-003] Post-claim SCR coverage {scr_buffer:.0f}% below "
                f"minimum 110% (Solvency II Article 101)")
        return PolicyEvaluation("INS-003", "Solvency Reserve", "pass", "OK")

    for p in [large_claim_approval_policy, fraud_indicator_policy,
              solvency_reserve_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.FINANCIAL, DecisionType.CUSTOM], rule=p))

    _section("Standard property claim — auto-settlement")
    _show(pipeline.process(_req("claims_ai", DecisionType.FINANCIAL,
        {"amount": 12_500, "claim_amount": 12_500, "claim_id": "CLM-2024-08432",
         "fraud_score": 0.12, "scr_buffer_pct": 145,
         "reference": "SETTLE-001"})), "standard claim")

    _section("High-value claim — needs senior adjuster")
    _show(pipeline.process(_req("claims_ai", DecisionType.FINANCIAL,
        {"amount": 750_000, "claim_amount": 750_000, "claim_id": "CLM-2024-08433",
         "fraud_score": 0.08, "scr_buffer_pct": 138})), "large claim no adjuster")

    _section("High fraud score — investigation required")
    _show(pipeline.process(_req("claims_ai", DecisionType.FINANCIAL,
        {"amount": 35_000, "claim_amount": 35_000, "claim_id": "CLM-2024-08434",
         "fraud_score": 0.82, "scr_buffer_pct": 155,
         "reference": "SETTLE-003"})), "high fraud score")

    print("\n  Compliance: INS-001/002/003 · FIN-001")
    print("  Regulatory: Solvency II · FCA PROD · Lloyd's minimum standards")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 9 — Logistics: Cold Chain & Hazmat Governance
# ══════════════════════════════════════════════════════════════════════════════

def example_09_logistics():
    _hdr("EXAMPLE 9 — Logistics: Cold Chain & Hazardous Materials Governance",
         "ADR · temperature excursion · route compliance · carrier qualification")

    pipeline = _pipeline()

    def cold_chain_integrity_policy(payload, ctx):
        min_temp = float(payload.get("min_recorded_temp_celsius", 999))
        max_temp = float(payload.get("max_recorded_temp_celsius", -999))
        req_min  = float(payload.get("required_temp_min", -999))
        req_max  = float(payload.get("required_temp_max", 999))
        if min_temp < req_min - 2 or max_temp > req_max + 2:
            return PolicyEvaluation("LOG-002", "Cold Chain Integrity", "fail",
                f"[LOG-002] Temperature excursion detected: "
                f"recorded {min_temp:.1f}–{max_temp:.1f}°C "
                f"vs required {req_min:.0f}–{req_max:.0f}°C")
        return PolicyEvaluation("LOG-002", "Cold Chain Integrity", "pass", "OK")

    def hazmat_compliance_policy(payload, ctx):
        if payload.get("hazmat", False) or payload.get("dangerous_goods", False):
            if not payload.get("adr_licence_ref"):
                return PolicyEvaluation("LOG-003", "Hazmat ADR Compliance", "fail",
                    "[LOG-003] Dangerous goods shipment requires adr_licence_ref (ADR 2023)")
            if not payload.get("emergency_response_plan_ref"):
                return PolicyEvaluation("LOG-003", "Hazmat ADR Compliance", "fail",
                    "[LOG-003] Dangerous goods requires emergency_response_plan_ref")
        return PolicyEvaluation("LOG-003", "Hazmat ADR Compliance", "pass", "OK")

    def carrier_qualification_policy(payload, ctx):
        QUALIFIED_CARRIERS = {"DHL-MED", "FEDEX-PHARMA", "COLD-CHAIN-CO",
                              "UPS-HEALTHCARE", "THERMO-LOGISTICS"}
        carrier = payload.get("carrier_id", "")
        requires_qualified = payload.get("temperature_controlled", False) or \
                             payload.get("pharmaceutical", False)
        if requires_qualified and carrier not in QUALIFIED_CARRIERS:
            return PolicyEvaluation("LOG-004", "Carrier Qualification", "fail",
                f"[LOG-004] Carrier '{carrier}' not qualified for "
                f"temperature-controlled/pharmaceutical shipments")
        return PolicyEvaluation("LOG-004", "Carrier Qualification", "pass", "OK")

    for p in [cold_chain_integrity_policy, hazmat_compliance_policy,
              carrier_qualification_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.LOGISTICS], rule=p))

    _section("Standard pharmaceutical shipment — should execute")
    _show(pipeline.process(_req("logistics_ai", DecisionType.LOGISTICS,
        {"shipment_id": "SHIP-PH-001", "shipment_value": 45_000,
         "approval_ref": "APPR-LOG-001",
         "pharmaceutical": True, "temperature_controlled": True,
         "carrier_id": "DHL-MED",
         "min_recorded_temp_celsius": 2.1, "max_recorded_temp_celsius": 7.8,
         "required_temp_min": 2, "required_temp_max": 8})), "valid pharma ship")

    _section("Cold chain temperature excursion — product integrity compromised")
    _show(pipeline.process(_req("logistics_ai", DecisionType.LOGISTICS,
        {"shipment_id": "SHIP-PH-002", "shipment_value": 280_000,
         "approval_ref": "APPR-LOG-002",
         "pharmaceutical": True, "temperature_controlled": True,
         "carrier_id": "DHL-MED",
         "min_recorded_temp_celsius": 12.5, "max_recorded_temp_celsius": 18.2,
         "required_temp_min": 2, "required_temp_max": 8})), "temperature excursion")

    _section("Hazmat without ADR licence — must block")
    _show(pipeline.process(_req("logistics_ai", DecisionType.LOGISTICS,
        {"shipment_id": "SHIP-HZ-001", "shipment_value": 15_000,
         "approval_ref": "APPR-LOG-003",
         "hazmat": True, "dangerous_goods": True,
         "carrier_id": "GENERIC-CARRIER"})), "hazmat no ADR")

    print("\n  Compliance: LOG-001/002/003/004")
    print("  Regulatory: ADR 2023 · GDP Guidelines (EU) · WHO TRS 961")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 10 — Public Sector: Government AI Transparency
# ══════════════════════════════════════════════════════════════════════════════

def example_10_public_sector():
    _hdr("EXAMPLE 10 — Public Sector: Government AI Decision Governance",
         "EU AI Act high-risk · algorithmic transparency · appeals process · bias controls")

    pipeline = _pipeline()

    def welfare_decision_fairness_policy(payload, ctx):
        """High-risk AI: welfare decisions require human review and explainability."""
        if payload.get("decision_category") in ("benefit_eligibility",
                                                  "housing_allocation", "tax_assessment"):
            if not payload.get("explanation_generated", False):
                return PolicyEvaluation("GOV-001", "AI Decision Explainability", "fail",
                    "[GOV-001] High-risk AI decision requires explanation_generated=True "
                    "(EU AI Act Article 13)")
            if not payload.get("appeals_process_notified", False):
                return PolicyEvaluation("GOV-001", "AI Decision Explainability", "fail",
                    "[GOV-001] Citizen must be notified of right to appeal "
                    "(EU AI Act Article 14)")
        return PolicyEvaluation("GOV-001", "AI Decision Explainability", "pass", "OK")

    def demographic_proxy_policy(payload, ctx):
        """Prevent use of demographic proxies in automated decisions."""
        PROTECTED_PROXIES = {"postcode_deprivation_index",
                             "surname_ethnicity_score", "accent_region_code"}
        used_features = set(payload.get("model_features_used", []))
        flagged = used_features & PROTECTED_PROXIES
        if flagged:
            return PolicyEvaluation("GOV-002", "Demographic Proxy Prevention", "fail",
                f"[GOV-002] Decision used demographic proxy features: {flagged}")
        return PolicyEvaluation("GOV-002", "Demographic Proxy Prevention", "pass", "OK")

    def procurement_transparency_policy(payload, ctx):
        """Public procurement: AI-assisted awards above threshold require public notice."""
        amount = float(payload.get("contract_value", 0))
        if amount > 139_000 and not payload.get("ojeu_notice_ref"):  # EU threshold
            return PolicyEvaluation("GOV-003", "Public Procurement Transparency", "fail",
                f"[GOV-003] Contract value €{amount:,.0f} requires OJEU notice reference "
                f"(PCR 2015 Regulation 109)")
        return PolicyEvaluation("GOV-003", "Public Procurement Transparency", "pass", "OK")

    for p in [welfare_decision_fairness_policy, demographic_proxy_policy,
              procurement_transparency_policy]:
        pipeline.policy_engine.register(Policy(p.__name__, p.__name__,
            [DecisionType.CUSTOM, DecisionType.PROCUREMENT], p))

    _section("Benefit eligibility — full compliance — should execute")
    _show(pipeline.process(_req("govt_ai", DecisionType.CUSTOM,
        {"decision_category": "benefit_eligibility",
         "citizen_id": "ANON-HASH-7891", "decision_outcome": "approved",
         "explanation_generated": True, "appeals_process_notified": True,
         "model_features_used": ["income_band", "employment_status",
                                  "dependants_count"]})), "compliant welfare decision")

    _section("Decision without explanation — EU AI Act violation")
    _show(pipeline.process(_req("govt_ai", DecisionType.CUSTOM,
        {"decision_category": "housing_allocation",
         "citizen_id": "ANON-HASH-7892",
         "explanation_generated": False, "appeals_process_notified": True,
         "model_features_used": ["waiting_list_position"]})),
        "missing explanation")

    _section("Decision using demographic proxy features — bias block")
    _show(pipeline.process(_req("govt_ai", DecisionType.CUSTOM,
        {"decision_category": "benefit_eligibility",
         "citizen_id": "ANON-HASH-7893",
         "explanation_generated": True, "appeals_process_notified": True,
         "model_features_used": ["income_band", "surname_ethnicity_score",
                                  "postcode_deprivation_index"]})),
        "demographic proxy use")

    _section("Public contract — missing OJEU notice — transparency block")
    _show(pipeline.process(_req("govt_ai", DecisionType.PROCUREMENT,
        {"amount": 250_000, "contract_value": 250_000,
         "supplier_id": "VENDOR-001", "category": "IT_services",
         "contract_id": "CT-GOV-001"})), "OJEU notice missing")

    print("\n  Compliance: GOV-001/002/003 · EUAI.A13 · EUAI.A14")
    print("  Regulatory: EU AI Act · PCR 2015 · UK Equality Act 2010")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 11 — Retail: Dynamic Pricing & Inventory
# ══════════════════════════════════════════════════════════════════════════════

def example_11_retail():
    _hdr("EXAMPLE 11 — Retail: AI Pricing Governance & Demand Sensing",
         "Price gouging prevention · competitor parity · promotional guardrails")

    pipeline = _pipeline()

    def price_gouging_policy(payload, ctx):
        """Prevent AI from exploiting demand surge events."""
        surge_factor = float(payload.get("demand_surge_factor", 1.0))
        pct_change   = float(payload.get("price_change_pct", 0))
        if surge_factor > 2.0 and pct_change > 15:
            return PolicyEvaluation("RTL-001", "Price Gouging Prevention", "fail",
                f"[RTL-001] Price increase of {pct_change:.0f}% during "
                f"{surge_factor:.1f}x demand surge constitutes price gouging")
        return PolicyEvaluation("RTL-001", "Price Gouging Prevention", "pass", "OK")

    def promotional_cannibalisation_policy(payload, ctx):
        """Prevent AI from discounting products already below cost."""
        new_price  = float(payload.get("new_price", 0))
        cost_price = float(payload.get("cost_price", 0))
        if cost_price > 0 and new_price < cost_price * 0.95:
            return PolicyEvaluation("RTL-002", "Below-Cost Promotion", "fail",
                f"[RTL-002] Proposed price ${new_price:.2f} is below 95% of "
                f"cost price ${cost_price:.2f}")
        return PolicyEvaluation("RTL-002", "Below-Cost Promotion", "pass", "OK")

    for p in [price_gouging_policy, promotional_cannibalisation_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.PRICING], rule=p))

    _section("Normal price optimisation — should execute")
    _show(pipeline.process(_req("pricing_ai", DecisionType.PRICING,
        {"product_id": "SKU-001", "new_price": 24.99, "previous_price": 22.99,
         "floor_price": 18.00, "cost_price": 15.00,
         "demand_surge_factor": 1.2, "price_change_pct": 8.7,
         "reason": "competitor match"})), "normal optimisation")

    _section("Price gouging during demand surge — must block")
    _show(pipeline.process(_req("pricing_ai", DecisionType.PRICING,
        {"product_id": "SKU-HAND-SANITISER", "new_price": 14.99,
         "previous_price": 2.99, "floor_price": 1.50, "cost_price": 1.20,
         "demand_surge_factor": 8.5, "price_change_pct": 400})),
        "price gouging surge")

    _section("Promotional price below cost — must block")
    _show(pipeline.process(_req("pricing_ai", DecisionType.PRICING,
        {"product_id": "SKU-LAPTOP", "new_price": 280.00,
         "previous_price": 399.00, "floor_price": 250.00, "cost_price": 320.00,
         "demand_surge_factor": 0.8, "price_change_pct": -29.8})),
        "below cost promotion")

    print("\n  Compliance: PRICE-001/002 · RTL-001/002")
    print("  Regulatory: UK CMA pricing guidance · EU UCTD · FTC pricing rules")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 12 — HR: AI Compensation & Workforce Decisions
# ══════════════════════════════════════════════════════════════════════════════

def example_12_hr():
    _hdr("EXAMPLE 12 — Human Resources: AI Compensation Governance",
         "Pay equity · discrimination prevention · legal compliance · approval thresholds")

    pipeline = _pipeline()

    def pay_equity_policy(payload, ctx):
        """Flag AI compensation decisions that may create pay equity gaps."""
        proposed_salary = float(payload.get("proposed_salary", 0))
        peer_median     = float(payload.get("peer_group_median_salary", 0))
        if peer_median > 0:
            ratio = proposed_salary / peer_median
            if ratio < 0.85:
                return PolicyEvaluation("HR-002", "Pay Equity Floor", "fail",
                    f"[HR-002] Proposed salary ${proposed_salary:,.0f} is "
                    f"{ratio:.0%} of peer median — below 85% equity threshold")
            if ratio > 1.30:
                return PolicyEvaluation("HR-002", "Pay Equity Ceiling", "warn",
                    f"[HR-002] Proposed salary {ratio:.0%} of peer median — "
                    f"requires pay equity justification")
        return PolicyEvaluation("HR-002", "Pay Equity", "pass", "OK")

    def termination_process_policy(payload, ctx):
        """AI-assisted termination recommendations require legal review."""
        if payload.get("action", "").lower() in ("terminate", "termination", "dismissal"):
            if not payload.get("legal_review_ref"):
                return PolicyEvaluation("HR-003", "Termination Legal Review", "fail",
                    "[HR-003] Termination decision requires legal_review_ref")
            if not payload.get("hr_business_partner_sign_off"):
                return PolicyEvaluation("HR-003", "Termination Legal Review", "fail",
                    "[HR-003] Termination requires hr_business_partner_sign_off=True")
        return PolicyEvaluation("HR-003", "Termination Legal Review", "pass", "OK")

    for p in [pay_equity_policy, termination_process_policy]:
        pipeline.policy_engine.register(Policy(policy_id=p.__name__, policy_name=p.__name__,
            decision_types=[DecisionType.HR], rule=p))

    _section("Standard salary adjustment within equity band — should execute")
    _show(pipeline.process(_req("hr_ai", DecisionType.HR,
        {"employee_id": "EMP-001", "action": "salary_adjustment",
         "amount": 5_000, "approval_ref": "HR-APPR-001",
         "proposed_salary": 72_000, "peer_group_median_salary": 68_000})),
        "standard increment")

    _section("Salary offer below pay equity floor — should block")
    _show(pipeline.process(_req("hr_ai", DecisionType.HR,
        {"employee_id": "EMP-002", "action": "new_hire_offer",
         "amount": 3_000, "approval_ref": "HR-APPR-002",
         "proposed_salary": 45_000, "peer_group_median_salary": 65_000})),
        "below equity floor")

    _section("Termination without legal review — must block")
    _show(pipeline.process(_req("hr_ai", DecisionType.HR,
        {"employee_id": "EMP-003", "action": "terminate",
         "amount": 0, "reason": "performance"})),
        "termination no review")

    print("\n  Compliance: HR-001/002/003")
    print("  Regulatory: Equality Act 2010 · EU Pay Transparency Directive · ACAS Code")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 13 — Security: Attack Detection & Injection Prevention
# ══════════════════════════════════════════════════════════════════════════════

def example_13_security():
    _hdr("EXAMPLE 13 — Security: OWASP Agentic Top 10 Attack Scenarios",
         "SQL injection · SSTI · prompt injection · path traversal · excessive agency")

    pipeline = _pipeline()

    attacks = [
        ("SQL Injection",       {"amount": 1000, "supplier_id": "'; DROP TABLE policies; --"}),
        ("SSTI Attack",          {"amount": 1000, "supplier_id": "SUP-001",
                                  "notes": "{{7*7}} {{ config.SECRET_KEY }}"}),
        ("XSS Injection",        {"amount": 1000, "supplier_id": "SUP-001",
                                  "description": "<script>alert('xss')</script>"}),
        ("Path Traversal",       {"amount": 1000, "supplier_id": "SUP-001",
                                  "file_ref": "../../etc/passwd"}),
        ("Command Injection",    {"amount": 1000, "supplier_id": "SUP-001",
                                  "action": "list; rm -rf /; echo pwned"}),
        ("Null Byte Injection",  {"amount": 1000, "supplier_id": "SUP-001\x00admin"}),
        ("Oversized Payload",    {f"field_{i}": "A" * 100 for i in range(100)}),
        ("Excessive Agency",     {"amount": 9_000_000, "supplier_id": "SUP-001",
                                  "category": "hardware"}),
    ]

    pipeline.register_contract(AgentContract(
        agent_id="sec_test_agent",
        permitted_types=[DecisionType.PROCUREMENT],
        max_amount=500_000,
    ))

    for attack_name, payload in attacks:
        r = pipeline.process(_req("sec_test_agent", DecisionType.PROCUREMENT,
            payload))
        icon = "✗" if r.final_status == FinalStatus.BLOCKED else "⚠ PASSED"
        msg = (r.policy_violations[0][:65] if r.policy_violations
               else r.message[:65] if r.message else "security pre-check")
        print(f"  {icon}  {attack_name:<25s}  {msg}")

    print("\n  Compliance: PayloadSanitizer (pre-pipeline) · AgentContract")
    print("  Standard: OWASP Agentic Top 10 A01-A10 (2026)")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 14 — Multi-Tenant: SaaS Governance Platform
# ══════════════════════════════════════════════════════════════════════════════

def example_14_multi_tenant():
    _hdr("EXAMPLE 14 — Multi-Tenant: SaaS AI Governance Platform",
         "Complete tenant isolation · custom policies per org · no data leakage")

    from glassbox.governance.multitenancy import TenantRegistry, MultiTenantPipeline

    registry = TenantRegistry()

    # Register tenant-specific policies
    def acme_spending_policy(payload, ctx):
        amount = float(payload.get("amount", 0))
        if amount > 100_000 and not payload.get("contract_id"):
            return PolicyEvaluation("ACME-001", "ACME Spending Policy", "fail",
                "[ACME-001] ACME Corp: procurement above $100K requires contract_id")
        return PolicyEvaluation("ACME-001", "ACME Spending Policy", "pass", "OK")

    def globex_spending_policy(payload, ctx):
        amount = float(payload.get("amount", 0))
        if amount > 250_000 and not payload.get("board_approval_ref"):
            return PolicyEvaluation("GLBX-001", "Globex Spending Policy", "fail",
                "[GLBX-001] Globex: amounts above $250K require board_approval_ref")
        return PolicyEvaluation("GLBX-001", "Globex Spending Policy", "pass", "OK")

    registry.register_policy("acme_corp",
        Policy("ACME-001", "ACME Policy", [DecisionType.PROCUREMENT], acme_spending_policy))
    registry.register_policy("globex",
        Policy("GLBX-001", "Globex Policy", [DecisionType.PROCUREMENT], globex_spending_policy))

    mt_pipeline = MultiTenantPipeline(
        registry=registry,
        base_pipeline_fn=lambda comps: GovernancePipeline(
            echo=False,
            policy_engine=comps.policy_engine,
            velocity_breaker=comps.velocity_breaker,
            anomaly_detector=comps.anomaly_detector,
        )
    )

    _section("ACME Corp — $120K without contract — ACME policy triggers")
    r = mt_pipeline.process(
        _req("acme_agent", DecisionType.PROCUREMENT,
             {"amount": 120_000, "supplier_id": "SUP-001", "category": "IT"}),
        tenant_id="acme_corp")
    _show(r, "ACME $120K no contract")

    _section("Globex — $120K without contract — ACME policy should NOT apply")
    r = mt_pipeline.process(
        _req("globex_agent", DecisionType.PROCUREMENT,
             {"amount": 120_000, "supplier_id": "SUP-001", "category": "IT",
              "contract_id": "CT-GLBX-001"}),
        tenant_id="globex")
    _show(r, "Globex $120K with contract")

    _section("Isolation verification")
    from glassbox.governance.multitenancy import ContextIsolationValidator
    validator = ContextIsolationValidator(registry)
    report    = validator.check_isolation(["acme_corp", "globex"])
    print(f"  Isolation verified: {report['all_isolated']}  "
          f"({report['tenants_checked']} tenants, "
          f"{len(report['issues'])} shared instances detected)")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 15 — Policy Replay: Impact Analysis Before Deployment
# ══════════════════════════════════════════════════════════════════════════════

def example_15_policy_replay():
    _hdr("EXAMPLE 15 — Policy Replay: Evidence-Based Policy Change Analysis",
         "Replaying decisions under proposed new policy before deployment")

    original_pipeline = _pipeline()
    requests = []

    # Build historical decision corpus
    test_amounts = [50_000, 100_000, 200_000, 350_000, 450_000, 600_000, 800_000]
    for amount in test_amounts:
        req = _req("hist_agent", DecisionType.PROCUREMENT,
                   {"amount": amount, "supplier_id": "SUP-001",
                    "category": "hardware", "contract_id": f"CT-{amount:06d}"})
        resp = original_pipeline.process(req)
        requests.append((resp, amount))

    _section("Original policy results (PROC-001: $500K limit)")
    executed = sum(1 for r, _ in requests if r.final_status == FinalStatus.EXECUTED)
    blocked  = sum(1 for r, _ in requests if r.final_status == FinalStatus.BLOCKED)
    print(f"  Executed: {executed}  Blocked: {blocked}  "
          f"Block rate: {blocked/(executed+blocked):.0%}")

    # Propose tighter policy: $200K limit
    tight_engine = PolicyEngine()
    tight_engine.disable("PROC-001")

    def strict_limit(payload, ctx):
        amount = float(payload.get("amount", 0))
        if amount > 200_000 and not payload.get("contract_id"):
            return PolicyEvaluation("PROC-001-STRICT", "Strict $200K Limit", "fail",
                f"[PROC-001-STRICT] Amount ${amount:,.0f} exceeds strict $200K limit")
        return PolicyEvaluation("PROC-001-STRICT", "Strict $200K Limit", "pass", "OK")

    tight_engine.register(Policy(policy_id="PROC-001-STRICT", policy_name="Strict Procurement Limit",
        decision_types=[DecisionType.PROCUREMENT], rule=strict_limit))

    replay_pipeline = GovernancePipeline(echo=False, policy_engine=tight_engine)
    from glassbox.governance.decision_replay import DecisionReplay

    replayer = DecisionReplay(replay_pipeline)
    records  = [r.audit_record for r, _ in requests if r.audit_record]
    results  = replayer.replay_many(records)
    summary  = replayer.compare_summary(results)

    _section("Proposed new policy (strict $200K limit) — replay results")
    print(f"  Total replayed:    {summary['total_replayed']}")
    print(f"  Outcomes changed:  {summary['outcomes_changed']}")
    print(f"  Would be blocked:  {sum(1 for r in results if r.get('replayed_status') == 'blocked')}")
    print(f"  Impact: {summary['outcomes_changed']} of {summary['total_replayed']} "
          f"decisions would have different outcomes")
    print("\n  → Finance team can quantify operational impact before deploying the change")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 16 — RAG Governance: Clinical Knowledge Base
# ══════════════════════════════════════════════════════════════════════════════

def example_16_rag_governance():
    _hdr("EXAMPLE 16 — RAG Governance: Clinical Knowledge Base Queries",
         "Query injection prevention · source validation · freshness controls")

    from glassbox.rag.governance import (
        RAGQueryGovernor, RAGRetrievalGovernor,
        ApprovedSourceRegistry, RetrievedChunk,
    )

    registry = ApprovedSourceRegistry(
        approved_sources=["approved_formulary", "clinical_guidelines_2024",
                          "bnf_online", "nice_guidance"]
    )
    registry.block_source("internet_forum")

    query_gov     = RAGQueryGovernor(allowed_topics=["drug", "dose", "clinical", "treatment"])
    retrieval_gov = RAGRetrievalGovernor(
        source_registry=registry, min_relevance=0.50, max_age_days=365)

    _section("Clinical drug query — approved source — should pass")
    q = query_gov.check("Maximum safe dose of paracetamol for adults", agent_id="clinical_ai")
    print(f"  Query allowed: {q.allowed}")

    _section("Injection attempt in query — should block")
    q = query_gov.check("'; DROP TABLE drug_interactions; -- dose", agent_id="clinical_ai")
    print(f"  Query allowed: {q.allowed}  reason: {q.blocked_reason[:60] if not q.allowed else 'N/A'}")

    _section("Retrieval governance — approved vs unapproved sources")
    chunks = [
        RetrievedChunk("c1", "Paracetamol max dose: 4g/24h in adults",
                       "approved_formulary", relevance_score=0.92),
        RetrievedChunk("c2", "someone said 10g is fine on reddit",
                       "internet_forum", relevance_score=0.71),
        RetrievedChunk("c3", "Paracetamol hepatotoxicity threshold 7.5g",
                       "clinical_guidelines_2024", relevance_score=0.88),
        RetrievedChunk("c4", "old guidelines from 1990",
                       "clinical_guidelines_2024", relevance_score=0.45),
    ]
    result = retrieval_gov.check(chunks)
    print(f"  Chunks in: {result.total_retrieved}  Passed: {result.passed_count}  "
          f"Blocked: {result.blocked_count}")
    for chunk, reason in result.blocked_chunks:
        print(f"    ✗ '{chunk.source}' — {reason}")


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 17 — OTel Telemetry: Governance Observability
# ══════════════════════════════════════════════════════════════════════════════

def example_17_observability():
    _hdr("EXAMPLE 17 — Observability: OpenTelemetry Governance Metrics",
         "Prometheus · Datadog · Grafana integration without OTel SDK")

    from glassbox.telemetry.otel_exporter import OtelExporter
    from glassbox.events.event_bus        import EventBus
    import time

    exporter = OtelExporter(service_name="glassbox-demo", service_version="1.0.0")
    bus      = EventBus(max_workers=2)
    bus.subscribe("*", exporter.handle_event)

    pipeline = _pipeline(event_bus=bus)

    _section("Running 20 decisions to generate telemetry")
    for i in range(10):
        pipeline.process(_req("telemetry_agent", DecisionType.PROCUREMENT,
            {"amount": 5_000 * (i + 1), "supplier_id": "SUP-001",
             "category": "hardware", "contract_id": f"CT-{i:03d}"}))
    # Some that will block
    for i in range(5):
        pipeline.process(_req("telemetry_agent", DecisionType.PROCUREMENT,
            {"amount": 600_000 + i * 50_000, "supplier_id": "UNKNOWN-VENDOR",
             "category": "semiconductors"}))

    time.sleep(0.1)  # allow async dispatch

    _section("Governance metrics snapshot")
    snap = exporter.snapshot()
    total_keys  = [k for k in snap if "decisions_total" in k]
    blocked_keys = [k for k in snap if "decisions_blocked" in k]
    latency_keys = [k for k in snap if "latency" in k]

    total_val   = sum(snap[k]["value"] for k in total_keys)
    blocked_val = sum(snap[k]["value"] for k in blocked_keys)
    print(f"  Total decisions measured:   {total_val:.0f}")
    print(f"  Blocked decisions:          {blocked_val:.0f}")
    if latency_keys:
        latency = snap[latency_keys[0]]
        print(f"  P99 latency:                {latency.get('p99', 0):.3f}ms")

    _section("Prometheus scrape endpoint preview (first 300 chars)")
    prom_text = exporter.prometheus_text()
    print(f"  {prom_text[:300].strip()}")
    print(f"  ... ({len(prom_text)} chars total — ready for /metrics endpoint)")

    bus.shutdown()


# ══════════════════════════════════════════════════════════════════════════════
# EXAMPLE 18 — NL Policy Authoring: Compliance Team Self-Service
# ══════════════════════════════════════════════════════════════════════════════

def example_18_nl_policy_authoring():
    _hdr("EXAMPLE 18 — NL Policy Authoring: Compliance Team Self-Service",
         "Plain-English policy → validated YAML → registered and tested in seconds")

    from glassbox.authoring.nl_policy import NLPolicyAuthor
    pipeline = _pipeline()
    author   = NLPolicyAuthor(api_key=None)  # template mode — no API key needed

    policies_to_author = [
        ("Block any procurement request that exceeds $200,000 and does not have "
         "a contract_id field present",
         "procurement", "ORG-001"),
        ("Block financial transfers above $50,000 that are missing a reference number",
         "financial", "ORG-002"),
        ("Block all logistics shipments above $100,000 that do not include an "
         "approval_ref",
         "logistics", "ORG-003"),
    ]

    for description, dtype, pid in policies_to_author:
        result = author.generate(description, dtype, pid)
        status = "✓ VALID" if result.validation_ok else "✗ INVALID"
        print(f"\n  Policy ID: {pid}  Status: {status}")
        print(f"  Description: '{description[:65]}...'")
        if result.validation_ok:
            print(f"  Generated YAML ({len(result.yaml_rule)} chars) — registering...")
            for p in result.policies:
                pipeline.policy_engine.register(p)

            # Test the registered policy
            test_r = pipeline.process(_req("test_agent", DecisionType.PROCUREMENT
                if dtype == "procurement" else
                (DecisionType.FINANCIAL if dtype == "financial" else DecisionType.LOGISTICS),
                {"amount": 999_999, "supplier_id": "SUP-001",
                 "category": "hardware", "contract_id": "CT-001"}
                if dtype != "logistics" else
                {"amount": 999_999, "shipment_value": 999_999,
                 "supplier_id": "SUP-001"}))
            print(f"  Policy test: {test_r.final_status.value}")
        else:
            print(f"  Error: {result.validation_error}")

    print(f"\n  With Claude API key: policies are generated by AI in <2 seconds")
    print(f"  Without API key: template generation provides a valid starting point")


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCHER
# ══════════════════════════════════════════════════════════════════════════════

EXAMPLES = {
    1:  ("Financial Services: Algorithmic Trading",           example_01_financial_trading),
    2:  ("Healthcare: Clinical AI Prescription Governance",   example_02_healthcare),
    3:  ("Global Supply Chain: Multi-Tier Procurement",       example_03_supply_chain),
    4:  ("Pharmaceutical: GxP & FDA 21 CFR Part 11",          example_04_pharmaceutical),
    5:  ("Retail Banking: Credit & Payments Governance",      example_05_retail_banking),
    6:  ("Energy: Grid Dispatch & NERC CIP",                  example_06_energy_grid),
    7:  ("Manufacturing: Smart Factory Production",           example_07_manufacturing),
    8:  ("Insurance: Automated Claims Processing",            example_08_insurance),
    9:  ("Logistics: Cold Chain & Hazmat Governance",         example_09_logistics),
    10: ("Public Sector: Government AI Transparency",         example_10_public_sector),
    11: ("Retail: Dynamic Pricing & Demand Sensing",          example_11_retail),
    12: ("HR: AI Compensation & Workforce Decisions",         example_12_hr),
    13: ("Security: OWASP Agentic Top 10 Attack Scenarios",   example_13_security),
    14: ("Multi-Tenant: SaaS Governance Platform",            example_14_multi_tenant),
    15: ("Policy Replay: Evidence-Based Impact Analysis",     example_15_policy_replay),
    16: ("RAG Governance: Clinical Knowledge Base",           example_16_rag_governance),
    17: ("Observability: OpenTelemetry Governance Metrics",   example_17_observability),
    18: ("NL Policy Authoring: Compliance Self-Service",      example_18_nl_policy_authoring),
}


def main():
    parser = argparse.ArgumentParser(description="GlassBox Industry Examples v1.0.0")
    parser.add_argument("--list", action="store_true", help="List all examples")
    parser.add_argument("--id", type=int, help="Run a specific example by ID")
    args = parser.parse_args()

    if args.list:
        print("\nGlassBox v1.0.0 — Industry Use Cases\n")
        for eid, (name, _) in EXAMPLES.items():
            print(f"  {eid:2d}. {name}")
        return

    if args.id:
        if args.id not in EXAMPLES:
            print(f"Unknown example ID: {args.id}")
            sys.exit(1)
        name, fn = EXAMPLES[args.id]
        print(f"\n  EXAMPLE {args.id} — {name}")
        fn()
        return

    print("\n" + "=" * 70)
    print("  GlassBox v1.0.0 — Runtime Decision Governance Framework")
    print("  18 Industry Use Cases  ·  Apache 2.0")
    print("  Mohammed Akbar Ansari — Independent Researcher Navi Mumbai")
    print("=" * 70)

    for eid, (name, fn) in EXAMPLES.items():
        try:
            fn()
        except Exception as exc:
            print(f"\n  [ERROR] Example {eid} failed: {exc}")

    print("\n" + "=" * 70)
    print("  All 18 examples complete.")
    print("=" * 70)


if __name__ == "__main__":
    main()
