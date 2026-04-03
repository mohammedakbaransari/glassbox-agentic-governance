# GlassBox — Industry Use-Case Patterns

**v1.0.0 | Mohammed Akbar Ansari | Independent Researcher**

This document describes how GlassBox applies to real enterprise AI governance scenarios. Each pattern includes the business context, the failure mode GlassBox prevents, and an implementation guide.

Run all examples: `python3 examples/industry_examples.py`

---

## Pattern 1 — Financial Services: Algorithmic Trading Controls

**Business context:** Quantitative hedge fund with AI agents generating FX orders, treasury transfers, and settlement instructions connected to Bloomberg, Reuters, and prime broker APIs.

**Failure mode without GlassBox:** A corrupted feed causes the algo to generate a $1.5M currency swap — above the $1M per-ticket limit. The order executes before any human sees it.

**GlassBox controls:**
- `FIN-001`: Single transfer limit $1M
- `AgentContract(max_amount=500_000)` for FX algo
- `AI-001`: Block orders with confidence < 0.30 (degraded model)

```python
pipeline.register_contract(AgentContract(
    agent_id="fx_algo",
    permitted_types=[DecisionType.FINANCIAL],
    max_amount=500_000,
))
```

**Compliance:** NIST CSF 2.0 PR.AA-01, EU AI Act Art. 9, OWASP A04

---

## Pattern 2 — Healthcare: Clinical Prescription Validation

**Business context:** Hospital EHR connected to AI clinical decision support system generating prescription recommendations. AI model has variable confidence depending on patient data completeness.

**Failure mode without GlassBox:** Model degradation causes AI to recommend 10× overdose of controlled substance. The prescription reaches the pharmacy system.

**GlassBox controls:**
- Custom `CLIN-001`: Controlled substances require `physician_cosign_id`
- Custom `CLIN-002`: Dose must not exceed `max_dose_mg`
- `AI-001`: Block if model confidence < 0.30

```python
def controlled_substance_policy(payload, ctx):
    CONTROLLED = {"morphine","oxycodone","fentanyl","ketamine"}
    drug = payload.get("drug_name","").lower()
    if any(c in drug for c in CONTROLLED) and not payload.get("physician_cosign_id"):
        return PolicyEvaluation("CLIN-001","Controlled Substance","fail",
            f"Controlled substance '{drug}' requires physician_cosign_id")
    return PolicyEvaluation("CLIN-001","Controlled Substance","pass","OK")
```

**Compliance:** EU AI Act Art. 9/14, NIST AI RMF MANAGE

---

## Pattern 3 — Manufacturing: Smart Factory Production Scheduling

**Business context:** Automotive plant AI scheduling agents manage production runs, raw material procurement, and maintenance windows. Demand surge causes scheduling agent to order 3× normal materials from unapproved spot-market supplier.

**GlassBox controls:**
- `PROC-002`: Supplier must be on approved registry (warning for spot market)
- Custom `MFG-001`: Production units cannot exceed shift capacity
- Custom `MFG-002`: Line shutdown requires `maintenance_window_id`
- `ITOPS-001`: Destructive operations require change window

**Compliance:** IEC 62443 SR 2.1, NERC CIP-007 (for plant OT), Purdue L3-L4

---

## Pattern 4 — Insurance: Automated Underwriting

**Business context:** Commercial property insurer using AI for underwriting decisions. AI approves policies up to $5M automatically; larger policies route to human review.

**GlassBox controls:**
- Custom `UW-001`: Auto-approve limit $5M; block above $20M
- Custom `UW-002`: Risk class must be in valid classification set
- `AI-001`: Require confidence ≥ 0.30

**WorkflowEngine integration:**
Decisions with `HUMAN_REVIEW` disposition automatically create workflow instances:
```python
wfe = WorkflowEngine(default_sla_minutes=120)  # 2-hour SLA
pipeline = GovernancePipeline(workflow_engine=wfe)
# Large policies get WorkflowInstance created automatically
pending = wfe.list_pending()
wfe.approve(wf_id, actor="senior_underwriter@co.com", notes="Verified")
```

---

## Pattern 5 — Energy / Utilities: Grid Dispatch Governance

**Business context:** Regional utility with AI managing grid dispatch, renewable trading, and demand response. Critical grid operations require dual authorisation from operator and supervisor.

**Failure mode:** Cyberattack injects malicious grid dispatch command attempting to trip a critical 345kV transmission line without dual authorisation.

**GlassBox controls:**
- Custom `GRID-001`: Critical grid operations require `operator_auth_code` AND `supervisor_auth_code`
- Custom `GRID-002`: Energy trading position limit $2M per trade
- `ITOPS-001`: Change window for all infrastructure changes

**Security layer:** The malicious command payload is checked for SQL injection and script patterns before reaching Stage 0.

**Compliance:** NERC CIP-007/010, IEC 62443 SR 1.1/2.1, AEMO AESCSF

---

## Pattern 6 — Multi-Agent Chain: Treasury Operations

**Business context:** 4-agent treasury workflow: `demand_agent` → `selection_agent` → `approval_agent` → `execution_agent`. Each agent's output feeds the next.

**Chain governance with AgentOrchestrator:**

```python
orch = AgentOrchestrator(pipeline)
nodes = [
    AgentNode("forecast", "demand_agent",    DecisionType.PROCUREMENT,
              lambda ctx: {"amount": forecast_amount(ctx), "category": "raw_materials"}),
    AgentNode("select",   "selection_agent", DecisionType.PROCUREMENT,
              lambda ctx: {"amount": ctx["forecast.payload"]["amount"],
                           "supplier_id": select_supplier(ctx)}),
    AgentNode("approve",  "approval_agent",  DecisionType.FINANCIAL,
              lambda ctx: {"amount": ctx["select.payload"]["amount"],
                           "reference": "TRY-REF-001"}),
    AgentNode("execute",  "execution_agent", DecisionType.FINANCIAL,
              lambda ctx: {"amount": ctx["approve.payload"]["amount"],
                           "destination_account": "ACC-TREASURY"}),
]
result = orch.run_chain(nodes, abort_on_block=True)
# If approval_agent is blocked, execution_agent never runs
```

**Chain abort propagation:** If any node is blocked, subsequent nodes are skipped. The `OrchestrationResult` shows which node caused the abort and why.

**Compliance:** NIST CSF 2.0 PR.AA-01, Zero Trust ZTA.TE-02

---

## Pattern 7 — LangChain Integration

**Business context:** LangChain agent with tools for procurement, pricing, and financial operations. Every tool call must be governed.

```python
from langchain.tools import Tool
from glassbox.integrations.adapters import LangChainAdapter

pipeline = GovernancePipeline()
adapter  = LangChainAdapter(pipeline, agent_id="lc_procurement_agent",
                            decision_type_map={"place_order": DecisionType.PROCUREMENT})

# Wrap existing tools — governance is transparent
governed_tools = adapter.wrap_tools([
    Tool(name="place_order", func=erp_place_order, description="..."),
    Tool(name="update_price", func=catalog_update_price, description="..."),
    Tool(name="transfer_funds", func=treasury_transfer, description="..."),
])

# Tool.run() calls are now governed — blocked calls raise GovernanceBlockedError
# LangChain catches this and includes it in the agent's observation
```

---

## Pattern 8 — LangGraph Workflow with Governance

**Business context:** LangGraph state machine for procurement approval with nodes: validate → enrich → approve → execute.

```python
from langgraph.graph import StateGraph
from glassbox.integrations.adapters import LangGraphAdapter

adapter = LangGraphAdapter(pipeline)

# Govern the execute node — validates state before committing
governed_execute = adapter.wrap_node(
    execute_fn,
    agent_id          = "procurement_execute",
    decision_type     = DecisionType.PROCUREMENT,
    payload_extractor = lambda state: {
        "amount":      state["order"]["total"],
        "supplier_id": state["order"]["supplier"],
        "category":    state["order"]["category"],
    },
)

graph = StateGraph(ProcurementState)
graph.add_node("execute", governed_execute)   # governance transparent to graph
```

---

## Pattern 9 — Agentic RAG Governance

**Business context:** Clinical AI using RAG to retrieve drug interaction data before making prescription recommendations. RAG retrieval from knowledge base must be governed.

```python
from glassbox.rag.governance import (
    RAGQueryGovernor, RAGRetrievalGovernor,
    AgenticRAGOrchestrator, ApprovedSourceRegistry
)

# Register approved document sources
registry = ApprovedSourceRegistry(approved_sources=["clinical_kb","approved_formulary"])
registry.block_source("unverified_web")  # block internet sources

query_gov     = RAGQueryGovernor(allowed_topics=["drug","dose","clinical"])
retrieval_gov = RAGRetrievalGovernor(
    source_registry = registry,
    min_relevance   = 0.5,
    max_age_days    = 365,  # medical docs must be < 1 year old
)

rag = AgenticRAGOrchestrator(pipeline, query_gov, retrieval_gov, retriever_fn=clinical_kb.search)
result = rag.run(
    agent_id    = "clinical_ai",
    initial_query = "Maximum safe dose morphine adult patient kidney impairment",
    action_fn   = lambda ctx: generate_prescription(ctx),
    action_decision_type = DecisionType.CUSTOM,
)
```

---

## Pattern 10 — Multi-Tenant SaaS

**Business context:** ISV offering GlassBox-powered AI governance as a service to multiple enterprises. Each enterprise must be completely isolated — separate policies, velocity counters, anomaly baselines, and audit records.

```python
from glassbox.governance.multitenancy import TenantRegistry, MultiTenantPipeline

registry = TenantRegistry(
    velocity_config={"max_decisions": 500, "window_seconds": 60},
)

# Register tenant-specific policies
registry.register_policy("acme_corp", acme_spending_policy)
registry.register_policy("globex",    globex_spending_policy)

mt_pipeline = MultiTenantPipeline(
    registry,
    base_pipeline_fn=lambda comps: GovernancePipeline(
        policy_engine    = comps.policy_engine,
        velocity_breaker = comps.velocity_breaker,
        anomaly_detector = comps.anomaly_detector,
    )
)

# Completely isolated — ACME's velocity does not affect Globex
resp_acme   = mt_pipeline.process(acme_request,  tenant_id="acme_corp")
resp_globex = mt_pipeline.process(globex_request, tenant_id="globex")
```

---

## Pattern 11 — Policy Replay / Regression Testing

**Business context:** Finance compliance team proposes tightening procurement limit from $500K to $200K. Before deploying, they replay last month's decisions to quantify impact.

```python
from glassbox.governance.decision_replay import DecisionReplay

# Get historical decisions from SQLite
historical = audit_repo.query(decision_type="procurement", final_status="executed")

# Build pipeline with proposed new policy
new_engine = PolicyEngine()
new_engine.disable("PROC-001")
new_engine.register(Policy("PROC-001-STRICT", "Strict Limit",
    [DecisionType.PROCUREMENT], lambda p,c: PolicyEvaluation(
        "PROC-001-STRICT","Strict","fail" if float(p.get("amount",0)) > 200_000
        and not p.get("contract_id") else "pass","...")))

replay_pipeline = GovernancePipeline(policy_engine=new_engine)
replay          = DecisionReplay(replay_pipeline)

# Parallel replay for speed
results = replay.replay_many(records_from_audit, parallel=True, max_workers=8)
summary = replay.compare_summary(results)
print(f"Impact: {summary['outcomes_changed']}/{summary['total_replayed']} decisions would change")
```

---

## Pattern 12 — PySpark Scale Governance (Databricks/Fabric)

**Business context:** Enterprise running batch AI decision governance across millions of records in a Databricks Lakehouse or Microsoft Fabric workspace.

```python
# On Databricks / Fabric — spark session auto-available
from glassbox.adapters.spark import GlassBoxSparkAdapter

adapter = GlassBoxSparkAdapter(spark)   # log path auto-detected for DBFS/Lakehouse

# Govern entire DataFrame — each row is one AI decision
decisions_df = spark.read.table("ai_agent_decisions")
governed_df  = adapter.govern_dataframe(decisions_df, partition_mode=True)

# Write governed decisions to Delta Lake
governed_df.write.format("delta").mode("append").saveAsTable("governed_decisions")

# Structured Streaming for real-time governance
stream = spark.readStream.format("delta").table("incoming_decisions")
query  = adapter.govern_stream(
    stream,
    output_path = "/lakehouse/default/Tables/governed_decisions",
    checkpoint  = "/lakehouse/default/checkpoints/glassbox",
    trigger_secs= 5,
)
```

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
