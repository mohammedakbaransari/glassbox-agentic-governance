# GlassBox v1.1.0 — Complete User Guide

**Runtime Decision Governance for Autonomous AI Systems**

This guide takes you from zero to a fully running GlassBox instance. It covers
installation, your first decision, the REST API, policy authoring, agent
contracts, multi-tenancy, integrations, testing, and debugging.

---

## Table of Contents

1. [Prerequisites](#1-prerequisites)
2. [Installation](#2-installation)
3. [Verify Installation](#3-verify-installation)
4. [Five-Minute Quickstart](#4-five-minute-quickstart)
5. [Core Concepts](#5-core-concepts)
6. [Configuring Policies](#6-configuring-policies)
7. [Agent Contracts](#7-agent-contracts)
8. [Risk-Based Disposition](#8-risk-based-disposition)
9. [Velocity & Anomaly Limits](#9-velocity--anomaly-limits)
10. [Running the REST API](#10-running-the-rest-api)
11. [REST API — All Endpoints](#11-rest-api--all-endpoints)
12. [Multi-Tenancy](#12-multi-tenancy)
13. [LangChain / AutoGen Integration](#13-langchain--autogen-integration)
14. [MCP Gateway](#14-mcp-gateway)
15. [Execution Tracing & Debugging](#15-execution-tracing--debugging)
16. [Running Tests](#16-running-tests)
17. [Running the Industry Examples](#17-running-the-industry-examples)
18. [Environment Variables Reference](#18-environment-variables-reference)
19. [Where to Go Next](#19-where-to-go-next)

---

## 1. Prerequisites

| Requirement | Minimum | Notes |
|---|---|---|
| Python | 3.9 | 3.10–3.14 also supported |
| Operating system | Linux / macOS / Windows | All three tested in CI |
| Flask | 3.0+ | **Optional** — only for the REST API |
| PyYAML | 6.0+ | **Optional** — only for YAML rules files |
| PySpark | 3.3+ | **Optional** — only for Spark batch adapter |

GlassBox core has **zero mandatory dependencies**. A plain `pip install` with no
extras gives you the full governance engine using Python stdlib only.

---

## 2. Installation

### Option A — Install from source (recommended)

```bash
# 1. Clone the repository
git clone https://github.com/mohammedakbaransari/runtime-decision-governance.git
cd runtime-decision-governance

# 2. Create and activate a virtual environment
python -m venv .venv

# Linux / macOS
source .venv/bin/activate

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# 3a. Core library only (zero deps)
pip install -e .

# 3b. Core + REST API (adds Flask)
pip install -e ".[api]"

# 3c. Full development install (Flask + PyYAML + test tools)
pip install -e ".[dev]"
pip install pytest
```

### Option B — Install from PyPI

```bash
pip install glassbox-governance            # core only
pip install "glassbox-governance[api]"     # core + REST API
pip install "glassbox-governance[dev]"     # core + Flask + PyYAML
```

---

## 3. Verify Installation

```bash
python -c "from glassbox import __version__; print('GlassBox', __version__)"
# Expected: GlassBox 1.1.0

python -c "from glassbox.governance.pipeline import GovernancePipeline; print('OK')"
# Expected: OK
```

Run the built-in validation script:

```bash
python scripts/validate.py
```

---

## 4. Five-Minute Quickstart

The smallest working program — no configuration required:

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models   import (
    DecisionContext, DecisionRequest, DecisionType,
)

# 1. Create the pipeline (all defaults — safe out of the box)
pipeline = GovernancePipeline()

# 2. Build a decision request
request = DecisionRequest(
    agent_id      = "procurement_agent",
    decision_type = DecisionType.PROCUREMENT,
    payload       = {
        "amount"      : 45_000,
        "supplier_id" : "SUP-001",
        "description" : "Server hardware Q2",
    },
    context = DecisionContext(
        confidence  = 0.95,
        environment = "production",
    ),
)

# 3. Evaluate
response = pipeline.process(request)

print(f"Status  : {response.final_status.value}")   # executed / blocked / pending_review
print(f"Risk    : {response.risk_score}")            # 0–100
print(f"Latency : {response.pipeline_latency_ms} ms")
```

**Expected output:**

```
Status  : executed
Risk    : 12.0
Latency : 0.83 ms
```

### Triggering review and block thresholds

```python
# Amount in the review band (risk 35–70) -> pending_review
request.payload["amount"] = 150_000
response = pipeline.process(request)
print(response.final_status.value)   # pending_review

# Amount above block threshold (risk > 70) -> blocked
request.payload["amount"] = 800_000
response = pipeline.process(request)
print(response.final_status.value)   # blocked
print(response.message)
```

---

## 5. Core Concepts

### Decision Types

GlassBox ships with 12 built-in types, extensible via `CUSTOM`:

| Constant | Value | Typical use |
|---|---|---|
| `DecisionType.PROCUREMENT` | `"procurement"` | Purchase orders, supplier selection |
| `DecisionType.PRICING` | `"pricing"` | Price changes, discount approval |
| `DecisionType.FINANCIAL` | `"financial"` | Payments, transfers, FX orders |
| `DecisionType.INVENTORY` | `"inventory"` | Stock changes, write-offs |
| `DecisionType.LOGISTICS` | `"logistics"` | Routing, shipment, dispatch |
| `DecisionType.IT_OPS` | `"it_ops"` | Deployments, config changes |
| `DecisionType.HR` | `"hr"` | Staffing, compensation |
| `DecisionType.CLINICAL` | `"clinical"` | Prescriptions, dosage, procedures |
| `DecisionType.TRADING` | `"trading"` | FX orders, positions, hedges |
| `DecisionType.CONTENT` | `"content"` | AI-generated content (GDPR Art.22) |
| `DecisionType.LEGAL` | `"legal"` | Contracts, compliance filings |
| `DecisionType.CUSTOM` | `"custom"` | Any domain not listed above |

### The 9-Stage Pipeline

Every decision passes through these stages in order. The first failure at any
stage short-circuits the pipeline and returns immediately.

```
Stage 0a  AgentID Validation     — format check on agent_id string
Stage 0b  Security Sanitizer     — injection / XSS / prompt injection scan
Stage 0c  Agent Contract         — max_amount, permitted_types, delegation depth
Stage 1   Context Capture        — enrich request with runtime metadata
Stage 2   Audit Record Init      — create the AuditRecord to be persisted
Stage 3   Schema Validation      — payload structure per decision type
Stage 4   Velocity Breaker       — per-agent + ecosystem circuit breakers
Stage 5   Anomaly Detection      — z-score deviation from rolling baseline
Stage 6   Policy Enforcement     — every registered policy is evaluated
Stage 7   Risk Evaluation        — weighted composite score 0–100
Stage 8   Disposition            — EXECUTE / REVIEW / BLOCK routing
```

### Final Status Values

| Value | Meaning |
|---|---|
| `executed` | Passed all stages — action approved |
| `pending_review` | Risk score 35–70 — routed to human reviewers |
| `blocked` | Failed policy, contract, circuit breaker, or risk > 70 |

### DecisionContext Fields

```python
context = DecisionContext(
    confidence    = 0.92,             # AI model confidence 0.0–1.0
    environment   = "production",     # production / staging / development
    agent_chain   = ["parent_agent"], # delegation ancestry list
    source_system = "erp_system",     # originating platform name
    metadata      = {
        "region"      : "EU",
        "cost_centre" : "CC-42",
        "request_ip"  : "10.0.0.1",
    },
)
```

---

## 6. Configuring Policies

Policies are enforced at Stage 6. A failing policy either blocks the decision
or raises a warning, depending on the policy's `action` setting.

### List built-in policies

```python
from glassbox.governance.policy_engine import PolicyEngine

engine = PolicyEngine()
for p in engine.list_policies():
    print(f"{p.policy_id:12s}  {p.name}")
```

Sample built-ins include:
- `FIN-001` — Single transfer limit $1,000,000
- `AI-001`  — Block if model confidence < 0.30
- `IT-001`  — Production deployments only inside change window
- `HR-001`  — Compensation changes within approved band

### Register a custom policy via lambda

```python
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.models        import DecisionType

engine = PolicyEngine()

engine.register(Policy(
    policy_id      = "PROC-001",
    name           = "Approved supplier registry",
    description    = "Procurement must reference an approved supplier",
    decision_types = [DecisionType.PROCUREMENT],
    rule = lambda payload, ctx: (
        payload.get("supplier_id", "").startswith("SUP-"),
        "supplier_id must be in approved registry (prefix SUP-)"
    ),
))
```

### Register a custom policy via function

```python
def clinical_controlled_substance(payload, ctx):
    CONTROLLED = {"morphine", "oxycodone", "fentanyl", "ketamine"}
    drug = payload.get("drug_name", "").lower()
    if any(c in drug for c in CONTROLLED) and not payload.get("physician_cosign_id"):
        return False, f"Controlled substance '{drug}' requires physician_cosign_id"
    return True, "OK"

engine.register(Policy(
    policy_id      = "CLIN-001",
    name           = "Controlled substance co-sign",
    decision_types = [DecisionType.CLINICAL],
    rule           = clinical_controlled_substance,
))
```

### Wire a custom engine into the pipeline

```python
pipeline = GovernancePipeline(policy_engine=engine)
```

### Enable / disable policies at runtime

```python
engine.disable("FIN-001")   # temporarily suspend
engine.enable("FIN-001")    # re-enable
```

---

## 7. Agent Contracts

Agent contracts are pre-registered capability declarations enforced at Stage 0c,
before any policy evaluation. Violations are always hard blocks.

```python
from glassbox.governance.models import AgentContract, DecisionType

pipeline.register_contract(AgentContract(
    agent_id             = "procurement_agent",
    permitted_types      = [DecisionType.PROCUREMENT, DecisionType.INVENTORY],
    max_amount           = 250_000,   # hard $ ceiling per decision
    max_delegation_depth = 2,         # max hops in agent_chain
    delegation_allowed   = True,
))
```

If `procurement_agent` submits a `FINANCIAL` decision, or any decision above
$250,000, it is blocked at Stage 0c.

### List and remove contracts

```python
# List all
for c in pipeline.list_contracts():
    print(c.agent_id, c.max_amount, c.permitted_types)

# Remove
pipeline.remove_contract("procurement_agent")
```

---

## 8. Risk-Based Disposition

Every decision receives a composite risk score from 0 to 100. Score thresholds
map directly to the final disposition:

| Score range | Disposition | Final status |
|---|---|---|
| 0 – 35 | `AUTO_EXECUTE` | `executed` |
| 36 – 70 | `HUMAN_REVIEW` | `pending_review` |
| 71 – 100 | `BLOCK` | `blocked` |

Read the score and disposition from any response:

```python
response = pipeline.process(request)
print(response.risk_score)         # e.g. 42.5
print(response.risk_level.value)   # low / medium / high / critical
print(response.disposition.value)  # auto_execute / human_review / block
```

---

## 9. Velocity & Anomaly Limits

### Velocity Breaker — per-agent rate limiting

```python
from glassbox.governance.velocity_breaker import VelocityBreaker

breaker = VelocityBreaker(
    max_requests               = 100,  # maximum calls per window
    window_seconds             = 60,   # rolling window length
    cooldown_seconds           = 300,  # how long the breaker stays open
    ecosystem_limit            = 500,  # fleet-wide limit across all agents
    ecosystem_cooldown_seconds = 120,  # ecosystem cooldown period
)

pipeline = GovernancePipeline(velocity_breaker=breaker)
```

When an agent exceeds `max_requests` in `window_seconds` the circuit opens and
all subsequent decisions from that agent are blocked for `cooldown_seconds`.
The ecosystem limit triggers a fleet-wide block when the combined request rate
from all agents exceeds `ecosystem_limit`.

### Anomaly Detector — z-score outlier detection

```python
from glassbox.governance.anomaly_detector import AnomalyDetector

detector = AnomalyDetector(
    z_threshold = 3.5,   # higher = less sensitive
    min_samples = 30,    # baseline history required before scoring activates
)

pipeline = GovernancePipeline(anomaly_detector=detector)
```

The detector calculates a rolling statistical baseline per `(agent_id,
decision_type)` pair. Any numeric payload field whose value deviates more than
`z_threshold` standard deviations from the running mean triggers a block.

---

## 10. Running the REST API

### Install and start

```bash
pip install flask

# Default host 127.0.0.1, port 8000
python -m glassbox.api.app

# Custom host and port (via environment variables)
GLASSBOX_API_HOST=0.0.0.0 GLASSBOX_API_PORT=9000 python -m glassbox.api.app
```

### Health check — confirm the server is running

```bash
curl http://localhost:8000/health
# {"status": "healthy", "version": "1.1.0", "pipeline": "ready"}

curl http://localhost:8000/ready
# {"ready": true}
```

### Submit a single decision

```bash
curl -s -X POST http://localhost:8000/decisions \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "procurement_agent",
    "decision_type": "procurement",
    "payload": {"amount": 45000, "supplier_id": "SUP-001"},
    "context": {"confidence": 0.95, "environment": "production"}
  }' | python -m json.tool
```

**Expected response:**

```json
{
  "decision_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "final_status": "executed",
  "risk_score": 12.0,
  "risk_level": "low",
  "circuit_breaker_triggered": false,
  "pipeline_latency_ms": 1.23,
  "message": "Decision approved and executed."
}
```

### Query audit records

```bash
# Latest 10 records
curl "http://localhost:8000/decisions?limit=10&offset=0"

# Filter by agent and status
curl "http://localhost:8000/decisions?agent_id=procurement_agent&status=blocked"
```

### Retrieve a specific decision

```bash
curl http://localhost:8000/decisions/a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

### Replay a historical decision

```bash
curl -X POST http://localhost:8000/decisions/a1b2c3d4-.../replay
```

### Batch submission (up to 499 decisions)

```bash
curl -s -X POST http://localhost:8000/decisions/batch \
  -H "Content-Type: application/json" \
  -d '{
    "decisions": [
      {
        "agent_id": "agent_1",
        "decision_type": "pricing",
        "payload": {"amount": 1000},
        "context": {"confidence": 0.9}
      },
      {
        "agent_id": "agent_2",
        "decision_type": "financial",
        "payload": {"amount": 500000},
        "context": {"confidence": 0.85}
      }
    ]
  }'
```

### Dry-run simulation (no audit record written)

```bash
curl -s -X POST http://localhost:8000/decisions/simulate \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "test_agent",
    "decision_type": "procurement",
    "payload": {"amount": 999999},
    "context": {"confidence": 0.88}
  }'
```

### Real-time event stream (Server-Sent Events)

```bash
# Streams a continuous event feed — press Ctrl+C to stop
curl -N http://localhost:8000/events/stream

# Output format:
# data: {"event_type": "DecisionExecuted", "decision_id": "...", "agent_id": "..."}
```

### Governance statistics

```bash
curl http://localhost:8000/stats
curl "http://localhost:8000/stats/agents?agent_id=procurement_agent"
curl http://localhost:8000/agents/procurement_agent/velocity
curl http://localhost:8000/agents/procurement_agent/anomaly
curl http://localhost:8000/policies
curl http://localhost:8000/contracts
curl http://localhost:8000/ecosystem
```

---

## 11. REST API — All Endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/decisions` | Submit a single decision |
| `GET` | `/decisions` | List audit records (paginated, requires audit repository) |
| `GET` | `/decisions/{id}` | Retrieve a specific audit record |
| `POST` | `/decisions/{id}/replay` | Replay a historical decision |
| `POST` | `/decisions/batch` | Submit up to 499 decisions at once |
| `POST` | `/decisions/simulate` | Dry-run — no audit record persisted |
| `GET` | `/events/stream` | Real-time SSE event stream |
| `GET` | `/stats` | Aggregate governance statistics |
| `GET` | `/stats/agents` | Per-agent decision statistics |
| `GET` | `/agents/{id}/velocity` | Agent circuit-breaker status |
| `GET` | `/agents/{id}/anomaly` | Anomaly detection baseline data |
| `GET` | `/policies` | List all registered policies |
| `GET` | `/contracts` | List all registered agent contracts |
| `GET` | `/ecosystem` | Ecosystem breaker status |
| `GET` | `/health` | Full health check (pipeline + components) |
| `GET` | `/ready` | Kubernetes readiness probe |

Full request/response schemas: [../API/endpoint_reference.md](../API/endpoint_reference.md)

If persistent audit storage is not configured, `GET /decisions` returns `503 Service Unavailable`
instead of an empty success response.

---

## 12. Multi-Tenancy

Run one pipeline that enforces strict context isolation between tenants:

```python
from glassbox.governance.multitenancy  import MultiTenantPipeline, TenantRegistry
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.models        import DecisionType

# Build a separate PolicyEngine for each tenant
tenant_a_engine = PolicyEngine()

tenant_b_engine = PolicyEngine()
tenant_b_engine.register(Policy(
    policy_id      = "TENANT-B-CAP",
    name           = "Tenant B spending cap — $50k",
    decision_types = [DecisionType.PROCUREMENT],
    rule           = lambda p, c: (
        p.get("amount", 0) <= 50_000,
        "Tenant B cap: amount must be <= $50,000"
    ),
))

# Register tenants in the registry
registry = TenantRegistry(base_policies=[])
registry.register("tenant_a", policy_engine=tenant_a_engine)
registry.register("tenant_b", policy_engine=tenant_b_engine)

mt_pipeline = MultiTenantPipeline(registry=registry)

# tenant_id is stamped onto a deep-copy of the request — no cross-tenant leakage
response = mt_pipeline.process(request, tenant_id="tenant_b")
print(response.final_status.value)
```

### Evict inactive tenants

```python
# Remove tenants that have been idle for more than 1 hour
evicted = registry.evict_inactive(inactive_after_sec=3600)
print(f"Evicted {evicted} inactive tenants")
```

---

## 13. LangChain / AutoGen Integration

### LangChain

```python
from glassbox.integrations.adapters import LangChainGovernanceAdapter
from glassbox.governance.models     import DecisionType

adapter = LangChainGovernanceAdapter(
    pipeline      = pipeline,
    agent_id      = "langchain_agent",
    decision_type = DecisionType.CUSTOM,
    auto_block    = True,   # raise GlassBoxBlockedError on BLOCKED decisions
)

# Wrap any LangChain agent — governance is fully transparent
governed_agent = adapter.wrap(langchain_agent)
result = governed_agent.invoke({"input": "Buy 10,000 units of component X"})
```

### AutoGen

```python
from glassbox.integrations.adapters import AutoGenGovernanceAdapter

adapter       = AutoGenGovernanceAdapter(pipeline=pipeline, agent_id="autogen_agent")
governed_agent = adapter.wrap(autogen_agent)
```

### Generic adapter (works with any AI framework)

```python
from glassbox.integrations.adapters import GovernanceAdapter
from glassbox.governance.models     import DecisionType

adapter = GovernanceAdapter(
    pipeline      = pipeline,
    agent_id      = "my_agent",
    decision_type = DecisionType.FINANCIAL,
)

# Call this before every AI-generated action executes
response = adapter.evaluate(payload={"amount": 75_000, "account": "ACC-999"})
if response.final_status.value == "blocked":
    raise RuntimeError(f"Action blocked by governance: {response.message}")
```

---

## 14. MCP Gateway

GlassBox provides a governance gateway for Model Context Protocol (MCP) tool
calls, plus a static scanner that detects tool poisoning before tools are loaded.

### Scan tool definitions before loading

```python
from glassbox.integrations.mcp_gateway import MCPGovernanceGateway, MCPToolScanner

scanner = MCPToolScanner()
report  = scanner.scan_tool_definition({
    "name"        : "file_write",
    "description" : "Writes content to a file on disk",
    "inputSchema" : {
        "type"       : "object",
        "properties" : {"path": {"type": "string"}, "content": {"type": "string"}},
    },
})

print(f"Risk level : {report.risk_level}")       # low / medium / high / critical
print(f"Findings   : {len(report.findings)}")

if report.risk_level in ("high", "critical"):
    for f in report.findings:
        print(f"  [{f.severity.upper()}] {f.category}: {f.description}")
```

### Govern live MCP tool calls

```python
gateway = MCPGovernanceGateway(pipeline=pipeline, agent_id="mcp_agent")

result = gateway.call_tool("file_write", {"path": "/data/report.csv", "content": "..."})
print(result.status)   # executed / blocked
if result.status == "blocked":
    print(result.reason)
```

---

## 15. Execution Tracing & Debugging

Enable per-stage timing to identify which stage is slow or blocking decisions:

```python
pipeline = GovernancePipeline(trace_enabled=True)

response = pipeline.process(request)
trace    = response.execution_trace

print(f"Total stages: {len(trace.steps)}")
print()
for step in trace.steps:
    print(f"  Stage {step.stage_num:2d}  {step.stage_name:<24}"
          f"  {step.outcome:<10}  {step.duration_ms:.3f} ms")
```

**Sample output:**

```
Total stages: 9

  Stage  0  AgentIDValidation         passed      0.010 ms
  Stage  0  SecuritySanitizer         passed      0.095 ms
  Stage  0  AgentContract             passed      0.005 ms
  Stage  3  SchemaValidation          passed      0.120 ms
  Stage  4  VelocityBreaker           passed      0.033 ms
  Stage  5  AnomalyDetection          passed      0.210 ms
  Stage  6  PolicyEnforcement         passed      0.505 ms
  Stage  7  RiskEvaluation            passed      0.088 ms
  Stage  8  Disposition               executed    0.002 ms
```

### Structured logging

```bash
# Show all pipeline internals (per-stage detail)
export GLASSBOX_LOG_LEVEL=DEBUG
python your_script.py

# Standard informational output (default)
export GLASSBOX_LOG_LEVEL=INFO

# Suppress everything except errors (good for test runs)
export GLASSBOX_LOG_LEVEL=CRITICAL
```

### Inspect the audit record directly

```python
response = pipeline.process(request)
record   = response.audit_record

print(record.decision_id)
print(record.final_status.value)
print(record.policy_result.violations)     # list of policy violation messages
print(record.risk_result.risk_score)       # numeric score 0–100
print(record.circuit_breaker_result)       # None if not triggered
print(record.pipeline_latency_ms)
```

---

## 16. Running Tests

```bash
# Full test suite
python -m pytest tests/ -v

# Core and governance tests (fastest, covers 88% of the engine)
python -m pytest tests/test_core.py tests/test_governance.py -v

# Single file
python -m pytest tests/test_security.py -v

# With line-level coverage report
python -m pytest tests/ --cov=glassbox --cov-report=term-missing

# Quick pass/fail summary only
python -m pytest tests/ --tb=no -q
```

### Test suite breakdown

| File | What it covers | Passing |
|---|---|---|
| `tests/test_core.py` | Pipeline, policies, governance engine | 167 / 189 (88%) |
| `tests/test_governance.py` | All governance components | 92 / 116 (79%) |
| `tests/test_security.py` | Sanitizer, injection detection | varies |
| `tests/test_performance.py` | Latency and throughput benchmarks | varies |
| `tests/test_enterprise.py` | Multi-tenancy, access control | varies |
| `tests/test_integrations.py` | LangChain, AutoGen, MCP adapters | varies |
| **Total** | **Production-ready core** | **259 / 305 (85%)** |

### Suppress log noise during test runs

```bash
GLASSBOX_LOG_LEVEL=CRITICAL python -m pytest tests/ -q
```

---

## 17. Running the Industry Examples

18 end-to-end runnable examples across every major enterprise domain:

```bash
# List all available examples with descriptions
python examples/industry_examples.py --list

# Run all 18 examples sequentially
python examples/industry_examples.py

# Run a single example by ID (e.g. example 2 — healthcare)
python examples/industry_examples.py --id 2
```

### Domains covered

| ID | Industry | Decision type |
|---|---|---|
| 1 | Financial services — algorithmic trading | `FINANCIAL` |
| 2 | Healthcare — clinical prescription validation | `CLINICAL` |
| 3 | Manufacturing — production scheduling | `CUSTOM` |
| 4 | Insurance — underwriting automation | `CUSTOM` |
| 5 | Energy — grid dispatch authorisation | `CUSTOM` |
| 6 | Multi-agent chains, DAGs, sagas | Multiple |
| 7 | LangChain transparent governance | `CUSTOM` |
| 8 | LangGraph workflow governance | `CUSTOM` |
| 9 | RAG system governance | `CUSTOM` |
| 10 | Multi-tenant SaaS governance | Multiple |
| 11 | Policy replay and regression testing | Multiple |
| 12 | PySpark / Databricks batch governance | Multiple |

---

## 18. Environment Variables Reference

| Variable | Default | Description |
|---|---|---|
| `GLASSBOX_LOG_LEVEL` | `INFO` | Logging verbosity: `DEBUG` / `INFO` / `WARNING` / `ERROR` / `CRITICAL` |
| `GLASSBOX_API_HOST` | `127.0.0.1` | API server bind address |
| `GLASSBOX_API_PORT` | `8000` | API server port |
| `GLASSBOX_API_DEBUG` | `false` | Flask debug mode — **never** `true` in production |
| `GLASSBOX_API_MAX_PAYLOAD_BYTES` | `8192` | Maximum request body size in bytes |
| `GLASSBOX_API_TIMEOUT_SECONDS` | `30` | Per-request processing timeout |

---

## 19. Where to Go Next

| Goal | Document |
|---|---|
| Understand the 9-stage pipeline architecture | [../DEVELOPMENT/architecture.md](../DEVELOPMENT/architecture.md) |
| Implement a custom policy or adapter | [../DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md) |
| REST API — full request/response schemas | [../API/endpoint_reference.md](../API/endpoint_reference.md) |
| Deploy to Kubernetes / Docker / Databricks | [../DEPLOYMENT/guide.md](../DEPLOYMENT/guide.md) |
| Performance tuning and latency benchmarks | [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md) |
| Security hardening production checklist | [../SECURITY/hardening.md](../SECURITY/hardening.md) |
| Compliance mappings (17 frameworks) | [../COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md) |
| Real-world industry patterns | [use_cases.md](use_cases.md) |
| Common errors and solutions | [troubleshooting.md](troubleshooting.md) |
| Contribute policies, adapters, or examples | [../../CONTRIBUTING.md](../../CONTRIBUTING.md) |
| Full version history and migration notes | [../../CHANGELOG.md](../../CHANGELOG.md) |
