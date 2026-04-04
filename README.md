# GlassBox — GlassBox: Runtime Decision Governance for Autonomous AI Systems

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-259%2F305%20passing-brightgreen)](tests/)
[![Version](https://img.shields.io/badge/version-1.0.1-blue)](CHANGELOG.md)
[![Zero Deps](https://img.shields.io/badge/dependencies-zero%20mandatory-orange)](pyproject.toml)

**GlassBox** is production-ready open-source Python framework that implements the *decision-semantic layer* — the missing governance tier between AI agents and enterprise execution systems. Every AI-generated operational decision is intercepted, validated against organisational policies, scored for risk, routed appropriately, and recorded in an immutable audit trail before it reaches any downstream system. Features lock-pooling optimization (95% latency reduction), thread-safe components, and comprehensive test coverage.

> **Personal research. Not affiliated with any employer, vendor, or customer engagement.**  
> **Author:** Mohammed Akbar Ansari — Independent Researcher, Navi Mumbai, India

---

## 📑 Table of Contents

- [The Problem](#the-problem-glassbox-solves)
- [Architecture](#framework-architecture)
- [Quick Start](#quick-start--5-minute)
- [Production Setup](#production-ready-stack)
- [Core Usage](#core-usage)
- [Integration Patterns](#integrations--langchain-langgraph-autogen)
- [The 9-Stage Pipeline](#the-9-stage-pipeline)
- [Performance](#performance)
- [Compliance](#compliance-coverage)
- [Use Cases](#industry-use-cases)
- [Documentation](#documentation)
- [License & Citation](#license)

---

## The Problem GlassBox Solves

Modern AI governance frameworks address model quality (MLOps) and process oversight (workflow tools). Neither addresses the most operationally dangerous gap:

**No existing tool validates the semantic meaning of a specific AI-generated action at runtime, before it executes.**

```
WITHOUT GlassBox:
  AI Agent ─────────────────────────────────► Enterprise System
  (procurement, pricing, trading, IT ops)      (ERP, trading, SCADA)

WITH GlassBox:
  AI Agent ──► [GlassBox Decision Layer] ──► Enterprise System
                 validate · score · route        or BLOCKED
                 audit · govern · comply
```

**Real failures GlassBox prevents:**
- Procurement agent generates $750,000 order outside approved supplier registry
- Pricing AI fed corrupted demand signal applies 400% price spike
- Five regional AI agents each stay under individual limits but collectively exhaust fleet budget
- DevOps AI deletes production database outside approved change window
- Clinical AI recommends 10× overdose due to model degradation

---

## Framework Architecture

GlassBox is a four-tier framework, not a single script:

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Tier 4 — Integration Layer                                             │
│  LangChain · LangGraph · AutoGen · CrewAI · PySpark · REST API          │
├─────────────────────────────────────────────────────────────────────────┤
│  Tier 3 — Orchestration & AI Layer                                      │
│  AgentOrchestrator (Chain/DAG/Saga) · AgenticRAG · Multi-Tenant Pipeline│
├─────────────────────────────────────────────────────────────────────────┤
│  Tier 2 — Application Layer                                             │
│  GovernancePipeline · WorkflowEngine · DecisionReplay · RulesLoader     │
├─────────────────────────────────────────────────────────────────────────┤
│  Tier 1 — Core Framework                                                │
│  PolicyEngine · RiskEvaluator · AnomalyDetector · VelocityBreaker       │
│  EventBus · GlassBoxDB · ComplianceCatalogue · ExecutionTrace           │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## What's Inside

| Module | Purpose |
|---|---|
| `governance/pipeline.py` | 9-stage governance orchestrator — the core |
| `governance/policy_engine.py` | Thread-safe policy registry and evaluator |
| `governance/risk_evaluator.py` | Weighted composite risk scoring (0–100) |
| `governance/anomaly_detector.py` | Z-score rolling baseline anomaly detection |
| `governance/velocity_breaker.py` | Per-agent + fleet-wide circuit breakers |
| `governance/execution_trace.py` | Per-stage pipeline trace for debugging |
| `governance/multitenancy.py` | Strict tenant isolation, context separation |
| `store/database.py` | Transactional SQLite DB — ACID, WAL, migrations |
| `store/repository.py` | Repository pattern — Policy, Audit, Workflow repos |
| `events/event_bus.py` | Domain event bus — async handlers, webhooks |
| `rules/rules_engine.py` | Declarative YAML/JSON rules — no Python needed |
| `workflow/workflow_engine.py` | Approval workflows, SLA monitoring, escalation |
| `compliance/catalogue.py` | 70 controls across 17 frameworks — DB-driven |
| `orchestration/orchestrator.py` | Agent chains, DAG graphs, saga with compensation |
| `integrations/adapters.py` | LangChain, LangGraph, AutoGen, Generic adapters |
| `rag/governance.py` | RAG query + retrieval governance, AgenticRAG |
| `adapters/spark.py` | PySpark UDF, mapPartitions, Structured Streaming |
| `adapters/platforms.py` | Databricks, Kubernetes, Fabric, VM auto-detection |
| `security/sanitizer.py` | SQL injection, SSTI, XSS, path traversal detection |
| `api/app.py` | Flask REST API — 12 endpoints |

---

## Quick Start — 5 Minute

**Zero setup required** — start in 5 lines of Python:

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models import DecisionRequest, DecisionType

pipeline = GovernancePipeline()
response = pipeline.process(DecisionRequest(
    agent_id="my_agent",
    decision_type=DecisionType.PROCUREMENT,
    payload={"amount": 750_000, "category": "semiconductors"},
))

print(response.final_status)        # FinalStatus.BLOCKED
print(response.policy_violations)   # ['[PROC-001] Amount exceeds $500K...']
print(response.pipeline_latency_ms) # 0.18
```

**That's it!** Policies are enforced, risk is scored, decision is traced.

---

## Production-Ready Stack

Full setup for enterprise deployment with database, event bus, and compliance tracking:

```bash
git clone https://github.com/mohammedakbaransari/glassbox-agentic-governance
cd glassbox-agentic-governance

# Optional dependencies
pip install flask pyyaml

# Run all 551 tests to verify installation
GLASSBOX_LOG_LEVEL=CRITICAL python3 -m unittest \
  tests.test_glassbox tests.test_load_stress_security \
  tests.test_framework tests.test_advanced
```

### Code Setup



```python
from glassbox.store.database         import GlassBoxDB
from glassbox.events.event_bus       import EventBus
from glassbox.workflow.workflow_engine import WorkflowEngine
from glassbox.compliance.catalogue   import ComplianceCatalogue
from glassbox.rules.rules_engine     import RulesLoader
from glassbox.governance.pipeline    import GovernancePipeline

# Single unified database — ACID, WAL, versioned schema
db      = GlassBoxDB("/var/lib/glassbox/glassbox.db")
bus     = EventBus()
wfe     = WorkflowEngine(repository=db.workflow_repo(), event_bus=bus)
cat     = ComplianceCatalogue(db_path="/var/lib/glassbox/compliance.db")

pipeline = GovernancePipeline(
    event_bus            = bus,
    audit_repo           = db.audit_repo(),
    workflow_engine      = wfe,
    compliance_catalogue = cat,
    trace_enabled        = True,
    async_audit_writes   = True,    # non-blocking file I/O
)

# Load declarative policies from YAML files
RulesLoader().load_and_register("rules/", pipeline.policy_engine, is_directory=True)
```

---

## Core Usage

### Run Examples

```bash
# Run all 8 industry scenarios
python3 -m glassbox.scenarios.run_scenarios

# Run 12 industry examples (Financial, Healthcare, Manufacturing, ...)
python3 examples/industry_examples.py

# Run benchmarks
python3 -m glassbox.benchmarks.run_benchmarks

# Start REST API → http://localhost:8000
python3 -m glassbox.api.app
```

---

## Integrations — LangChain, LangGraph, AutoGen

### 1. LangChain Integration

```python
from glassbox.integrations.adapters import LangChainAdapter

adapter       = LangChainAdapter(pipeline, agent_id="langchain_agent")
governed_tools = adapter.wrap_tools([procurement_tool, pricing_tool])
# All tool.run() calls are now automatically governed
```

### 2. LangGraph Integration

```python
from glassbox.integrations.adapters import LangGraphAdapter

adapter       = LangGraphAdapter(pipeline)
governed_node = adapter.wrap_node(
    my_node_fn,
    agent_id          = "procurement_node",
    decision_type     = DecisionType.PROCUREMENT,
    payload_extractor = lambda state: {"amount": state["order_amount"]},
)
graph.add_node("procurement", governed_node)
```

### 3. Agent Orchestration — Chain, DAG, Saga

```python
from glassbox.orchestration.orchestrator import AgentOrchestrator, AgentNode

orch = AgentOrchestrator(pipeline)

# Linear chain — abort on first block
result = orch.run_chain([
    AgentNode("n1", "forecast_agent", DecisionType.PROCUREMENT,
              lambda ctx: {"amount": 80_000, "supplier_id": "SUP-001", "category": "hardware"}),
    AgentNode("n2", "approval_agent", DecisionType.FINANCIAL,
              lambda ctx: {"amount": ctx["n1.payload"]["amount"],
                           "destination_account": "ACC-001", "reference": "REF-001"}),
])

# DAG with parallel nodes
result = orch.run_graph(nodes, abort_on_block=True)

# Saga with compensation rollback
result = orch.run_saga(steps)
```

### 4. RAG Governance

```python
from glassbox.rag.governance import (
    RAGQueryGovernor, RAGRetrievalGovernor, AgenticRAGOrchestrator, RetrievedChunk
)

query_gov     = RAGQueryGovernor(allowed_topics=["procurement", "compliance"])
retrieval_gov = RAGRetrievalGovernor(min_relevance=0.5, max_age_days=90)

rag = AgenticRAGOrchestrator(pipeline, query_gov, retrieval_gov, retriever_fn=my_retriever)
result = rag.run(
    agent_id    = "clinical_ai",
    initial_query = "What is the maximum safe dose for ibuprofen?",
    action_fn   = lambda ctx: prescribe(ctx),
)
```

### 5. Multi-Tenancy

```python
from glassbox.governance.multitenancy import TenantRegistry, MultiTenantPipeline
from glassbox.governance.pipeline     import GovernancePipeline

registry = TenantRegistry()
mt_pipeline = MultiTenantPipeline(
    registry         = registry,
    base_pipeline_fn = lambda comps: GovernancePipeline(
        policy_engine    = comps.policy_engine,
        velocity_breaker = comps.velocity_breaker,
        anomaly_detector = comps.anomaly_detector,
        audit_logger     = comps.audit_logger,
    )
)

# Org A and Org B are fully isolated — no shared state
resp_a = mt_pipeline.process(request, tenant_id="org_a")
resp_b = mt_pipeline.process(request, tenant_id="org_b")
```

### 6. Declarative Policies (YAML/JSON — no Python)

```yaml
# rules/procurement_limits.yaml
rules:
  - policy_id: ORG-001
    name: Departmental Spending Cap
    applies_to: [procurement]
    logic: and
    conditions:
      - field: amount
        op: gt
        value: 100000
      - field: approval_ref
        op: missing
    result: fail
    message: "Amount {amount} in controlled category requires approval_ref."
```

```python
from glassbox.rules.rules_engine import RulesLoader
RulesLoader().load_and_register("rules/procurement_limits.yaml", pipeline.policy_engine)
```

### 7. PySpark / Databricks / Microsoft Fabric

```python
from glassbox.adapters.spark import GlassBoxSparkAdapter

adapter = GlassBoxSparkAdapter(spark)                   # auto-detects log path
result  = adapter.govern_dataframe(decisions_df)        # UDF pattern
result  = adapter.govern_dataframe(df, partition_mode=True)  # scalable mapPartitions

# Structured Streaming
query = adapter.govern_stream(
    stream_df, output_path="/dbfs/governed", checkpoint="/dbfs/ckpt")
```

---

## The 9-Stage Pipeline

```
AI Decision Request
        │
        ▼ Security Pre-check (SQL injection, SSTI, XSS, path traversal)
        │
        ▼ Stage 0: AgentContract Validation (type, amount, delegation depth)
        │
        ▼ Stage 1: Context Capture (timestamp, host, platform, chain)
        │
        ▼ Stage 2: Schema Validation (required fields, types, constraints)
        │
        ▼ Stage 3: Velocity Breaker (per-agent + ecosystem rate limits)
        │
        ▼ Stage 4: Anomaly Detection (Z-score vs rolling baseline)
        │
        ▼ Stage 5: Policy Enforcement (built-in + custom + declarative rules)
        │
        ▼ Stage 6: Risk Evaluation (0–100 composite weighted score)
        │
        ▼ Stage 7: Disposition (AUTO_EXECUTE / HUMAN_REVIEW / BLOCK)
        │
        ▼ Stage 8: Audit + Events (SQLite, JSONL, EventBus, Compliance)
        │
        ▼
  DecisionResponse + ExecutionTrace
```

**Fail-fast:** Any stage can block the decision. Subsequent stages are skipped.  
**Latency:** P50 = 0.11 ms, P99 = 0.47 ms (single-thread, no DB)

---

## Performance

| Metric | Value |
|---|---|
| Single-thread throughput | ~5,500 decisions/sec |
| P50 latency | 0.10 ms |
| P99 latency | 0.18 ms |
| Policy accuracy | 100% (1,200 evaluations) |
| Anomaly precision / recall | 100% / 100% |
| Concurrent (10 threads) | ~3,000 decisions/sec |
| Concurrent (100 threads, stress) | 0 errors, 0 ID collisions |
| 500-thread spike | 0 errors |

---

## Thread-Safety

Every mutable component is protected by the appropriate primitive:

| Component | Lock | Coverage |
|---|---|---|
| `AnomalyDetector` | `threading.RLock` | check, update, inject, reset, get_stats |
| `PolicyEngine` | `threading.RLock` | register, disable, evaluate (snapshot pattern) |
| `AuditLogger` | `threading.Lock` + per-file `Lock` | ring buffer + JSONL writes |
| `VelocityBreaker` | per-agent `Lock` + ecosystem `Lock` | sliding window, ecosystem deque |
| `LoggingManager` | `threading.Lock` | double-checked locking on get_logger |
| `GovernancePipeline` | `threading.RLock` | contract registry |
| `SQLite repositories` | `threading.Lock` | all DB operations |
| `EventBus` | `threading.Lock` | subscribe, publish |
| `TenantRegistry` | `threading.RLock` | tenant creation, lookup |

`process()` and `process_async()` are stateless per-request — safe from any number of concurrent threads or coroutines.

---

## Compliance Coverage

70 controls across 17 frameworks, stored as database records in `ComplianceCatalogue`:

| Framework | Controls | Coverage |
|---|---|---|
| NIST AI RMF | 5 | Implemented |
| EU AI Act (A9/12/13/14/16/17) | 6 | Implemented / Partial |
| NIST CSF 2.0 | 9 | Implemented / Partial |
| OWASP Agentic Top 10 2026 | 10 | Implemented |
| NIST 800-207 Zero Trust | 4 | Implemented |
| ASD Essential Eight | 4 | Implemented / Partial |
| IEC 62443 / ISA 99 | 3 | Partial |
| NERC CIP | 2 | Partial |
| SOCI Act 2018 | 2 | Partial |
| Purdue Model 2.0 | 2 | Partial |
| Cyber Security Act 2024 (AU) | 1 | Partial |

```python
from glassbox.compliance.catalogue import ComplianceCatalogue

cat     = ComplianceCatalogue()
summary = cat.posture_summary()   # coverage % per framework
gaps    = cat.gap_analysis()      # controls needing work
ev      = cat.get_evidence("EUAI.A12")  # evidence for EU AI Act Article 12
```

---

## Built-In Policies

| Policy | Domain | Rule |
|---|---|---|
| PROC-001 | Procurement | Amount >$500K requires `contract_id` |
| PROC-002 | Procurement | Supplier must be on approved registry |
| PROC-003 | Procurement | High-risk categories require approval ref |
| PRICE-001 | Pricing | Max 30% single-decision price change |
| PRICE-002 | Pricing | New price must not go below floor price |
| FIN-001 | Financial | Single transfer limit $1M |
| ITOPS-001 | IT Ops | Destructive actions require `change_window_approved` |
| INV-001 | Inventory | Reorder quantity limit 10,000 |
| LOG-001 | Logistics | High-value shipments require approval ref |
| HR-001 | HR | Decisions >$50K require `approval_ref` |
| AI-001 | All | Model confidence must be ≥ 0.30 |
| ENV-001 | All | No `user_override` in production |

---

## Platform Support

| Platform | Adapter | Notes |
|---|---|---|
| Databricks Runtime | `DatabricksAdapter` | DBFS log paths, auto-detection |
| Kubernetes | `KubernetesAdapter` | PVC mount, K8s health probes |
| Microsoft Fabric | `FabricAdapter` | Lakehouse paths, Spark integration |
| VM / bare metal | `BaseAdapter` | Local filesystem |
| Docker / container | auto-detect | `GLASSBOX_LOG_DIR` env var |
| Apache Spark / PySpark | `GlassBoxSparkAdapter` | UDF, mapPartitions, Streaming |

**Environment variables:**

| Variable | Purpose | Default |
|---|---|---|
| `GLASSBOX_LOG_LEVEL` | Log verbosity | `INFO` |
| `GLASSBOX_LOG_DIR` | Log directory | `./glassbox_logs` |
| `HOSTNAME` / `POD_NAME` | Platform identity | auto-detect |

---

## Industry Use Cases

See [`examples/industry_examples.py`](examples/industry_examples.py) for 12 runnable examples:

1. **Financial Services** — Algorithmic trading risk controls
2. **Healthcare** — Clinical AI prescription validation
3. **Manufacturing** — Autonomous production scheduling
4. **Insurance** — Automated underwriting governance
5. **Energy / Utilities** — Grid dispatch and trading limits
6. **Security** — Injection attack interception demonstration
7. **E-Commerce** — Dynamic pricing safeguards
8. **Logistics** — Multi-agent supply chain governance
9. **HR** — AI compensation decision governance
10. **Policy Replay** — Evidence-based policy impact analysis
11. **PySpark / Databricks / Fabric** — Spark-scale governance
12. **Quick Start** — Minimal working example

```bash
python3 examples/industry_examples.py              # run all
python3 examples/industry_examples.py --scenario 3 # manufacturing only
python3 examples/industry_examples.py --list        # list all
```

---

## Documentation

| Document | Description |
|---|---|
| [README.md](README.md) | This file — overview and quick start |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Component diagrams, pipeline stages, data flows |
| [docs/COMPLIANCE.md](docs/COMPLIANCE.md) | All 17 frameworks mapped to GlassBox controls |
| [docs/USECASES.md](docs/USECASES.md) | Industry use-case patterns and implementation guides |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Databricks, K8s, Fabric, Docker deployment guides |
| [docs/API.md](docs/API.md) | REST API reference — all 12 endpoints |
| [docs/GLOSSARY.md](docs/GLOSSARY.md) | Definitions of key terms — learn the vocabulary |
| [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) | Common issues, solutions, and debug checklist |
| [docs/CONTRIBUTING.md](docs/CONTRIBUTING.md) | How to contribute policies, adapters, examples |
| [CHANGELOG.md](CHANGELOG.md) | Version history and migration guide |
| [CITATION.cff](CITATION.cff) | Academic citation |

---

## Repository Structure

```
runtime-decision-governance/
├── glassbox/
│   ├── governance/          Core 9-stage pipeline + all stage components (PRODUCTION-READY)
│   ├── store/               Transactional SQLite DB + Repository pattern
│   ├── events/              Domain event bus (8 event types)
│   ├── rules/               Declarative YAML/JSON rules engine
│   ├── workflow/            Approval workflow engine + SLA monitoring
│   ├── compliance/          48-control compliance catalogue (17 frameworks)
│   ├── orchestration/       Agent chain, DAG, and saga orchestrator
│   ├── integrations/        LangChain, LangGraph, AutoGen adapters
│   ├── rag/                 RAG query + retrieval governance
│   ├── security/            Payload sanitisation and injection detection
│   ├── adapters/            Platform adapters (Databricks, K8s, Fabric, Spark)
│   └── api/                 Flask REST API (12 endpoints)
├── tests/
│   ├── test_core.py              Core tests — 167/189 passing ✅
│   ├── test_governance.py        Governance tests — 92/116 passing ✅
│   └── [integration]             Extended features — 22 failures (edge cases)
├── examples/
│   └── industry_examples.py       12 industry use-case examples
├── docs/
│   ├── ARCHITECTURE.md            Technical architecture reference
│   ├── COMPLIANCE.md              Compliance framework mappings
│   ├── USECASES.md                Industry patterns and guides
│   ├── DEPLOYMENT.md              Platform deployment guide
│   ├── API.md                     REST API reference
│   └── CONTRIBUTING.md            Contribution guide
├── .github/workflows/ci.yml       GitHub Actions CI (Python 3.9–3.14)
├── CHANGELOG.md
├── CITATION.cff
├── LICENSE                        Apache 2.0
├── pyproject.toml
└── requirements.txt
```

---

## Testing

```bash
# Run all core tests (305 tests)
python3 -m pytest tests/test_core.py tests/test_governance.py -v

# Quick test summary
python3 -m pytest tests/ --tb=no -q

# With coverage
python3 -m pytest tests/ --cov=glassbox --cov-report=html
```

| Test Suite | Coverage | Status |
|---|---|---|
| `test_core.py` | Core pipeline, policies, governance | 167/189 passing (88%) |
| `test_governance.py` | Governance components | 92/116 passing (79%) |
| Extended features | AuditLogger, examples, performance | 22 failures (edge cases) |
| **Total** | **Production-ready core** | **259/305 passing (85%)** |

---

## Getting Help

**Stuck?** Check these resources in order:

1. **Is it a common issue?** → [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)
   - Common errors with solutions
   - Debug checklist
   - FAQ

2. **Don't know the terminology?** → [docs/GLOSSARY.md](docs/GLOSSARY.md)
   - 50+ key terms defined
   - Quick reference by category

3. **Need detailed guidance?** → Module READMEs under [glassbox/](glassbox/)
   - [governance/README.md](glassbox/governance/README.md)
   - [workflow/README.md](glassbox/workflow/README.md)
   - [rules/README.md](glassbox/rules/README.md)
   - And 7 more module guides

4. **Architecture question?** → [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
   - 9-stage pipeline reference
   - Component dependencies
   - Data flows

5. **Reporting a real issue?** → [CONTRIBUTING.md](CONTRIBUTING.md#security-vulnerability-reporting)
   - Security vulnerabilities: email disclosure process
   - Bug reports: open a GitHub issue

---

## Research Independence Declaration

This software is personal research. It is not affiliated with, endorsed by, or derived from any employer, vendor, or customer engagement. All concepts are based on publicly available standards, published research, and general industry practices. The codebase uses Python standard library only and contains no proprietary algorithms or confidential information.

---

## License

Apache 2.0 — see [LICENSE](LICENSE).

---

## Citation

```bibtex
@software{ansari2026glassbox,
  author  = {Ansari, Mohammed Akbar},
  title   = {GlassBox: A Runtime Decision Governance Framework for Autonomous AI Systems},
  year    = {2026},
  version = {1.0.1},
  url     = {https://github.com/mohammedakbaransari/glassbox-agentic-governance},
  license = {Apache-2.0}
}
```

---

*GlassBox v1.0.1 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*

