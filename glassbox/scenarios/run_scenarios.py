"""
GlassBox Framework — Industry Scenarios  (v1.0.0)
Eight complete, runnable governance demonstrations.

  1. Autonomous Procurement          — supply chain, ERP policy enforcement
  2. Dynamic Pricing Breach          — retail, 400% spike caught by anomaly + policy
  3. Runaway Agent Velocity          — circuit breaker trips at decision #6
  4. Multi-Agent Decision Chain      — 4-agent healthcare lineage tracking
  5. IT Operations Automation        — destructive actions blocked outside change windows
  6. Decision Replay                 — policy regression testing
  7. Delegated Decision Chain        — treasury FX, delegation depth risk amplification
  8. Cross-Agent Fleet Budget        — AGG-001 stateful aggregate budget enforcement

Author: Mohammed Akbar Ansari
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from glassbox.governance.pipeline       import GovernancePipeline
from glassbox.governance.models import (
    AgentContract, DecisionRequest, DecisionContext, DecisionType,
    FinalStatus, PolicyEvaluation, EcosystemBreakerConfig,
)
from glassbox.governance.policy_engine  import Policy, PolicyEngine
from glassbox.governance.velocity_breaker import VelocityBreaker
from glassbox.governance.decision_replay  import DecisionReplay

DIV = "=" * 70

def _hdr(title):
    print(f"\n{DIV}\n  SCENARIO: {title}\n{DIV}")

def _res(label, resp, indent=2):
    pad  = " " * indent
    icon = {"executed":"OK ","pending_review":">> ","blocked":"XX "}.get(
        resp.final_status.value,"?? ")
    risk = f"risk={resp.risk_score:.1f}" if resp.risk_score is not None else "risk=n/a"
    cd   = ""
    if resp.audit_record and resp.audit_record.context:
        d = len(resp.audit_record.context.agent_chain)
        if d: cd = f"  chain={d}"
    print(f"{pad}[{icon}] {label:<42} {resp.final_status.value:<16} {risk}{cd}  "
          f"({resp.pipeline_latency_ms:.2f}ms)")
    for v in resp.policy_violations:
        print(f"{pad}       VIOLATION: {v}")
    for w in resp.policy_warnings:
        print(f"{pad}       WARNING:   {w}")
    if resp.circuit_breaker_triggered:
        eco = " [ECOSYSTEM]" if resp.ecosystem_breaker else ""
        print(f"{pad}       CIRCUIT BREAKER{eco}: {resp.circuit_breaker_reason}")


# ── Scenario 1: Autonomous Procurement ───────────────────────────────────────

def scenario_procurement():
    _hdr("Autonomous Procurement System (Supply Chain / ERP)")
    print("""
  Context: An AI procurement agent generates purchase orders from
  inventory forecasts. GlassBox intercepts every decision before
  it reaches the ERP system, enforcing spending limits, supplier
  registry, and category controls.
""")
    p = GovernancePipeline(echo=False)
    cases = [
        ("Routine stationery order",
         {"amount":2400,   "supplier_id":"SUP-001","category":"stationery"}),
        ("Mid-range IT equipment with contract",
         {"amount":48000,  "supplier_id":"SUP-002","category":"hardware","contract_id":"CT-001"}),
        ("Large order WITH contract",
         {"amount":620000, "supplier_id":"SUP-100","category":"hardware","contract_id":"CT-099"}),
        ("Large order WITHOUT contract",
         {"amount":620000, "supplier_id":"SUP-003","category":"hardware"}),
        ("Exceeds single-order limit",
         {"amount":1500000,"supplier_id":"SUP-001","category":"servers"}),
        ("Unapproved supplier — warning",
         {"amount":18000,  "supplier_id":"UNKNOWN-VENDOR","category":"parts"}),
        ("High-risk category (semiconductors) — no approval",
         {"amount":95000,  "supplier_id":"SUP-002","category":"semiconductors"}),
        ("High-risk WITH category approval ref",
         {"amount":95000,  "supplier_id":"SUP-002","category":"semiconductors",
          "category_approval_ref":"CAT-APPR-2026-044"}),
        ("Low AI confidence (0.22) — blocked by AI-001",
         {"amount":35000,  "supplier_id":"SUP-001","category":"logistics"},
         DecisionContext(confidence=0.22)),
    ]
    for case in cases:
        label, payload = case[0], case[1]
        ctx = case[2] if len(case) > 2 else None
        _res(label, p.process(DecisionRequest("procurement_agent", DecisionType.PROCUREMENT, payload, ctx)))
    s = p.stats
    print(f"\n  Summary: {s['total']} decisions | executed={s['by_status'].get('executed',0)} | "
          f"blocked={s['by_status'].get('blocked',0)} | avg_latency={s['avg_latency_ms']}ms")


# ── Scenario 2: Dynamic Pricing Breach ───────────────────────────────────────

def scenario_pricing():
    _hdr("Dynamic Pricing Engine — Corrupted Signal Detection (Retail)")
    print("""
  Context: An AI pricing model updates product prices in real time.
  A corrupted demand signal generates extreme price spikes.
  GlassBox catches anomalous decisions before they reach the
  pricing system.
""")
    p = GovernancePipeline(echo=False)
    cases = [
        ("Normal 2% increase",        {"new_price":102.0, "previous_price":100.0,"product_id":"P-001","reason":"demand"}),
        ("5% markdown — clearance",   {"new_price":95.0,  "previous_price":100.0,"product_id":"P-002","reason":"clearance"}),
        ("12% — within limit",        {"new_price":112.0, "previous_price":100.0,"product_id":"P-003","reason":"cost_increase"}),
        ("18% — warning zone",        {"new_price":118.0, "previous_price":100.0,"product_id":"P-004"}),
        ("32% — policy breach",       {"new_price":132.0, "previous_price":100.0,"product_id":"P-005"}),
        ("400% — corrupted signal",   {"new_price":499.0, "previous_price":100.0,"product_id":"P-006"}),
        ("Price below floor",         {"new_price":15.0,  "previous_price":100.0,"product_id":"P-007","floor_price":20.0}),
        ("High-value large change",   {"new_price":85000.0,"previous_price":65000.0,"product_id":"LUX-001","reason":"market"}),
    ]
    for label, payload in cases:
        _res(label, p.process(DecisionRequest("pricing_engine", DecisionType.PRICING, payload)))
    s = p.stats
    print(f"\n  Summary: {s['total']} decisions | executed={s['by_status'].get('executed',0)} | "
          f"blocked={s['by_status'].get('blocked',0)}")


# ── Scenario 3: Runaway Agent Velocity ───────────────────────────────────────

def scenario_velocity():
    _hdr("Runaway Agent — Velocity Circuit Breaker")
    print("""
  Context: A demand-forecasting agent enters a feedback loop and
  generates inventory replenishment orders at extreme velocity.
  The velocity circuit breaker trips after 5 decisions in 60s.
""")
    vb = VelocityBreaker(max_decisions=5, window_seconds=60, cooldown_seconds=30)
    p  = GovernancePipeline(velocity_breaker=vb, echo=False)
    print("  Submitting 10 rapid inventory decisions:\n")
    for i in range(10):
        r = p.process(DecisionRequest("inventory_agent", DecisionType.INVENTORY,
            {"quantity":500+i*10, "product_id":f"SKU-{i:03d}", "warehouse_id":"WH-01"}))
        note = " <-- CIRCUIT BREAKER" if r.circuit_breaker_triggered else ""
        print(f"  Decision {i+1:02d}: {r.final_status.value:<16}{note}")
    s = p.stats
    print(f"\n  {s['by_status'].get('executed',0)} executed before trip | "
          f"{s['by_status'].get('blocked',0)} blocked after trip")


# ── Scenario 4: Multi-Agent Decision Chain ────────────────────────────────────

def scenario_multi_agent():
    _hdr("Multi-Agent Decision Chain (Healthcare Pharma Supply)")
    print("""
  Context: Four agents collaborate on a pharmaceutical procurement.
  Each adds itself to agent_chain as it delegates forward.
  GlassBox governs the final execution decision with full lineage.
""")
    p = GovernancePipeline(echo=False)
    payload = {"amount":180000,"supplier_id":"SUP-001","category":"pharmaceuticals",
               "category_approval_ref":"PHARMA-2026-012"}
    r1 = p.process(DecisionRequest("exec_agent", DecisionType.PROCUREMENT, payload,
        DecisionContext(confidence=0.91, agent_chain=[], source_system="pharma_supply")))
    r2 = p.process(DecisionRequest("exec_agent", DecisionType.PROCUREMENT, payload,
        DecisionContext(confidence=0.91,
            agent_chain=["demand_forecast_agent","supplier_selection_agent",
                         "compliance_check_agent","procurement_execution_agent"],
            source_system="pharma_supply")))
    print(f"  Single agent:           {r1.final_status.value:<14} risk={r1.risk_score}")
    print(f"  4-agent chain (same):   {r2.final_status.value:<14} risk={r2.risk_score}")
    print(f"  Risk delta from chain:  +{r2.risk_score-r1.risk_score:.1f} points")
    if r2.audit_record:
        print(f"  Lineage captured:       {r2.audit_record.context.agent_chain}")


# ── Scenario 5: IT Operations Automation ─────────────────────────────────────

def scenario_itops():
    _hdr("IT Operations Automation — Change Window Enforcement (AIOps)")
    print("""
  Context: An AIOps platform auto-remediates infrastructure incidents.
  GlassBox enforces change window policies: destructive actions are
  blocked unless change_window_approved=True.
""")
    p = GovernancePipeline(echo=False)
    cases = [
        ("Auto-scale web tier (routine)",
         {"action":"scale_up","target":"web-tier-k8s","replicas":8}),
        ("Restart unhealthy service",
         {"action":"restart_service","target":"payment-processor","service_id":"PAY-003"}),
        ("Config patch — in change window",
         {"action":"update_config","target":"api-gateway","change_window_approved":True}),
        ("DELETE database — NO change window",
         {"action":"delete_database","target":"prod-orders-db"}),
        ("TERMINATE cluster — WITH window approval",
         {"action":"terminate_cluster","target":"staging-cluster","change_window_approved":True}),
        ("DESTROY storage — production, no approval",
         {"action":"destroy_volume","target":"prod-data-vol-01"}),
    ]
    for label, payload in cases:
        _res(label, p.process(DecisionRequest("aiops_agent", DecisionType.IT_OPS, payload)))


# ── Scenario 6: Decision Replay ───────────────────────────────────────────────

def scenario_replay():
    _hdr("Decision Replay — Policy Regression Testing")
    print("""
  Context: After tightening procurement policy (limit lowered from
  $500K to $200K), 5 historical decisions are replayed to identify
  which would have a different outcome under the new policy.
""")
    original = GovernancePipeline(echo=False)
    payloads = [
        {"amount":45000,  "supplier_id":"SUP-001","category":"hardware"},
        {"amount":180000, "supplier_id":"SUP-002","category":"software","contract_id":"CT-001"},
        {"amount":320000, "supplier_id":"SUP-003","category":"services"},
        {"amount":480000, "supplier_id":"SUP-001","category":"hardware"},
        {"amount":620000, "supplier_id":"SUP-001","category":"hardware","contract_id":"CT-002"},
    ]
    records = []
    for pl in payloads:
        r = original.process(DecisionRequest("proc_agent", DecisionType.PROCUREMENT, pl))
        if r.audit_record: records.append(r.audit_record)
        print(f"  ORIGINAL  ${pl['amount']:>7,}  -> {r.final_status.value}")

    # Stricter engine
    def _strict(payload, ctx):
        amount = float(payload.get("amount",0))
        if amount > 200_000 and not payload.get("contract_id"):
            return PolicyEvaluation("PROC-001-STRICT","Strict Limit","fail",
                f"Amount ${amount:,.0f} exceeds new $200K limit without contract.")
        return PolicyEvaluation("PROC-001-STRICT","Strict Limit","pass","OK.")
    eng = PolicyEngine(); eng.disable("PROC-001")
    eng.register(Policy(policy_id="PROC-001-STRICT", policy_name="Strict Limit", decision_types=[DecisionType.PROCUREMENT], rule=_strict))
    rep_pipeline = GovernancePipeline(policy_engine=eng, echo=False)
    replay = DecisionReplay(rep_pipeline)

    print("\n  Replaying through updated policy (new $200K threshold):")
    results = replay.replay_many(records)
    for r in results:
        changed = " <-- OUTCOME CHANGED" if r.get("outcome_changed") else ""
        print(f"  REPLAYED  orig={r['original_status']:<14} new={r['replayed_status']:<14}{changed}")
    s = replay.compare_summary(results)
    print(f"\n  {s['outcomes_changed']} of {s['total_replayed']} decisions would change under new policy.")


# ── Scenario 7: Delegated Decision Chain ─────────────────────────────────────

def scenario_delegated_chain():
    _hdr("Delegated Decision Chain — Treasury FX (Banking)")
    print("""
  Context: A treasury system uses four specialist agents to process
  a $420,000 foreign exchange conversion. Each agent adds itself to
  the agent_chain as it delegates forward. Risk escalates with depth
  and as model confidence degrades through multi-step reasoning.
""")
    p = GovernancePipeline(echo=False)
    base_payload = {"amount":420000,"destination_account":"ACC-FX-001",
                    "reference":"FX-2026-001"}
    chains = [
        ("Direct settlement — no chain",          [],                                     0.96),
        ("Via treasury_agent (depth 1)",          ["treasury_agent"],                     0.93),
        ("Via treasury → fx_pricing (depth 2)",   ["treasury_agent","fx_pricing_agent"],  0.88),
        ("treasury → fx → compliance (depth 3)",  ["treasury_agent","fx_pricing_agent",
                                                   "compliance_agent"],                   0.81),
        ("4-level chain, degraded conf (depth 4)",["treasury_agent","fx_pricing_agent",
                                                   "compliance_agent","risk_agent"],      0.58),
    ]
    print("  Baseline — direct execution:")
    r0 = p.process(DecisionRequest("settlement_agent", DecisionType.FINANCIAL, base_payload,
        DecisionContext(confidence=0.96, agent_chain=[])))
    _res("Direct (no delegation)", r0, indent=4)

    print("\n  Delegated chain — same payload, same amount:")
    results = []
    for label, chain, conf in chains[1:]:
        r = p.process(DecisionRequest("settlement_agent", DecisionType.FINANCIAL, base_payload,
            DecisionContext(confidence=conf, agent_chain=chain)))
        _res(label, r, indent=4)
        results.append((len(chain), r.risk_score))

    print(f"\n  Risk amplification: direct={r0.risk_score:.1f} → "
          f"depth-4={results[-1][1]:.1f} (+{results[-1][1]-r0.risk_score:.1f})")
    print(f"\n  Over-limit amount through 3-level chain:")
    r_over = p.process(DecisionRequest("settlement_agent", DecisionType.FINANCIAL,
        {"amount":1_200_000,"destination_account":"ACC-FX-001","reference":"FX-2026-002"},
        DecisionContext(confidence=0.91,
            agent_chain=["treasury_agent","fx_pricing_agent","compliance_agent"])))
    _res("$1.2M — exceeds FIN-001 single-transfer limit", r_over, indent=4)

    if results:
        print(f"\n  Audit lineage (depth-4 decision):")
        print(f"    agent_chain = {chains[-1][1]}")


# ── Scenario 8: Cross-Agent Fleet Budget Exhaustion ───────────────────────────

def scenario_fleet_budget():
    _hdr("Cross-Agent Fleet Budget Exhaustion — Regional Procurement")
    print("""
  Context: Five regional procurement agents each operate within their
  individual PROC-001 limit ($500K). No single agent would be blocked.
  But their combined daily spend exhausts the fleet budget of $400K.

  GlassBox policy AGG-001 (stateful, reads audit history) detects
  this cross-agent constraint violation at the point of execution.
  This is the governance problem invisible to per-agent tooling.
""")
    p = GovernancePipeline(echo=False)
    FLEET_BUDGET = 400_000.0

    def _agg_budget(payload, ctx, pipeline=p):
        """Fleet budget policy with explicit pipeline binding to avoid closure bug."""
        spent = pipeline.audit_logger.get_executed_spend(DecisionType.PROCUREMENT)
        proposed = float(payload.get("amount",0))
        projected = spent + proposed
        util = projected / FLEET_BUDGET * 100
        if projected > FLEET_BUDGET:
            return PolicyEvaluation("AGG-001","Fleet Budget Control","fail",
                f"Fleet budget exhausted. Spent:${spent:,.0f} + Proposed:${proposed:,.0f} "
                f"= ${projected:,.0f} exceeds ${FLEET_BUDGET:,.0f} limit.")
        if util >= 80:
            return PolicyEvaluation("AGG-001","Fleet Budget Control","warn",
                f"Fleet budget at {util:.0f}%: ${spent:,.0f} spent + ${proposed:,.0f} proposed.")
        return PolicyEvaluation("AGG-001","Fleet Budget Control","pass",
            f"Fleet budget at {util:.0f}%.")

    p.policy_engine.register(Policy(policy_id="AGG-001", policy_name="Fleet Procurement Budget",
        decision_types=[DecisionType.PROCUREMENT], rule=_agg_budget,
        description=f"Cross-agent fleet budget: ${FLEET_BUDGET:,.0f}/day"))

    print(f"  Fleet daily budget: ${FLEET_BUDGET:,.0f} across all regional agents\n")
    orders = [
        ("agent_north",   80_000, "hardware"),
        ("agent_south",   90_000, "components"),
        ("agent_east",    85_000, "tooling"),
        ("agent_west",    95_000, "hardware"),
        ("agent_central", 120_000,"hardware"),
    ]
    cumulative = 0.0
    for agent, amount, cat in orders:
        r = p.process(DecisionRequest(agent, DecisionType.PROCUREMENT,
            {"amount":amount,"supplier_id":"SUP-001","category":cat},
            DecisionContext(confidence=0.95)))
        if r.final_status == FinalStatus.EXECUTED: cumulative += amount
        note = ""
        if any("AGG-001" in v for v in r.policy_violations): note = "  ← FLEET BUDGET EXCEEDED"
        elif any("AGG-001" in w for w in r.policy_warnings):  note = "  ← budget warning"
        print(f"  {agent:<22} ${amount:>7,}  [{r.final_status.value.upper():<14}]{note}")

    s = p.stats
    print(f"\n  Total approved: ${cumulative:,.0f}  |  "
          f"Approved: {s['by_status'].get('executed',0)}  |  "
          f"Blocked: {s['by_status'].get('blocked',0)}")
    print(f"\n  Key insight: each individual order was under the $500K PROC-001 limit.")
    print(f"  Only fleet-level aggregate governance catches budget exhaustion.")


# ── Main runner ───────────────────────────────────────────────────────────────

def run_all():
    print(f"\n{DIV}")
    print("  GLASSBOX FRAMEWORK — INDUSTRY SCENARIO DEMONSTRATIONS  (v1.0.0)")
    print("  Runtime Decision Governance for Autonomous AI Systems")
    print("  Author: Mohammed Akbar Ansari")
    print(DIV)

    scenario_procurement()
    scenario_pricing()
    scenario_velocity()
    scenario_multi_agent()
    scenario_itops()
    scenario_replay()
    scenario_delegated_chain()
    scenario_fleet_budget()

    print(f"\n{DIV}\n  All 8 scenarios complete.\n{DIV}")


if __name__ == "__main__":
    run_all()
