# GlassBox — Architecture Reference

**v1.0.0 | Mohammed Akbar Ansari | Independent Researcher | Navi Mumbai, India**

---

## 1. Overview

GlassBox is a **Runtime Decision Governance Framework** for autonomous AI systems.
It implements the *decision-semantic layer* — the missing tier between AI agents and
enterprise execution systems. Every AI-generated operational decision passes through
GlassBox before it reaches any downstream system.

```
AI Agent
   │
   ▼
┌──────────────────────────────────────────────────────────────────┐
│                    GlassBox Framework                            │
│                                                                  │
│  Security → Contract → Schema → Velocity → Anomaly              │
│     → Policy → Risk → Disposition → Audit                       │
│                                                                  │
│  ┌──────────┐  ┌───────────┐  ┌──────────────┐  ┌──────────┐  │
│  │ Policy   │  │ Audit     │  │  Workflow    │  │  Event   │  │
│  │ Store    │  │ Repository│  │  Engine      │  │  Bus     │  │
│  │(SQLite)  │  │ (SQLite)  │  │  (SQLite)    │  │ (async)  │  │
│  └──────────┘  └───────────┘  └──────────────┘  └──────────┘  │
└──────────────────────────────────────────────────────────────────┘
   │           │               │
   ▼           ▼               ▼
EXECUTE    BLOCK          HUMAN_REVIEW
   │                           │
   ▼                           ▼
Enterprise System         Workflow Queue
(ERP, CRM, Trading)       (approval UI)
```

---

## 2. Layer Architecture

GlassBox is a three-tier framework:

```
┌────────────────────────────────────────────────────────────────┐
│  Tier 3 — Integration Layer                                    │
│  REST API · PySpark Adapter · Platform Adapters · Event Bus    │
├────────────────────────────────────────────────────────────────┤
│  Tier 2 — Application Layer                                    │
│  GovernancePipeline · WorkflowEngine · RulesLoader             │
│  DecisionReplay · RetryExecutor                                │
├────────────────────────────────────────────────────────────────┤
│  Tier 1 — Core Framework                                       │
│  PolicyEngine · RiskEvaluator · AnomalyDetector                │
│  VelocityBreaker · SchemaValidator · SecuritySanitizer         │
│  AuditLogger · PolicyRepository · AuditRepository             │
│  WorkflowRepository · EventBus · ExecutionTrace                │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. Component Map

```
glassbox/
├── governance/              Core pipeline and domain logic
│   ├── pipeline.py          GovernancePipeline — 9-stage orchestrator
│   ├── models.py            All domain models (DecisionRequest, AuditRecord, …)
│   ├── policy_engine.py     PolicyEngine — thread-safe registry + evaluator
│   ├── risk_evaluator.py    RiskEvaluator — weighted composite scoring (0–100)
│   ├── anomaly_detector.py  AnomalyDetector — Z-score rolling baselines
│   ├── velocity_breaker.py  VelocityBreaker — per-agent + ecosystem rate limits
│   ├── schema_validator.py  SchemaValidator — payload structure validation
│   ├── audit_logger.py      AuditLogger — in-memory ring buffer + JSONL files
│   ├── decision_replay.py   DecisionReplay — sync + async + parallel replay
│   ├── retry_policy.py      RetryExecutor — sync + async retry with backoff
│   ├── context_capture.py   ContextCapture — platform-safe metadata enrichment
│   ├── logging_manager.py   GlassBoxLogger — JSON/text, rotating, GLASSBOX_LOG_LEVEL
│   └── execution_trace.py   ExecutionTrace — per-stage pipeline trace (opt-in)
│
├── store/                   Repository pattern — pluggable storage backends
│   └── repository.py        PolicyRepository, AuditRepository, WorkflowRepository
│                            InMemory + SQLite implementations, RepositoryFactory
│
├── events/                  Domain event system
│   └── event_bus.py         EventBus, 8 domain events, async handlers, webhooks
│
├── rules/                   Declarative rules engine
│   └── rules_engine.py      RuleCondition, DeclarativeRule, RulesLoader
│                            YAML/JSON → Policy compilation, 12 operators
│
├── workflow/                Approval workflow engine
│   └── workflow_engine.py   WorkflowEngine, WorkflowInstance, SLA monitoring
│                            States: pending → in_review → approved/rejected
│
├── security/                Input sanitisation and injection prevention
│   └── sanitizer.py         PayloadSanitizer — SQL, SSTI, XSS, path traversal
│                            validate_agent_id() — log injection prevention
│
├── adapters/                Platform integration adapters
│   ├── platforms.py         DatabricksAdapter, KubernetesAdapter, FabricAdapter
│   │                        BaseAdapter, auto_detect_adapter()
│   └── spark.py             GlassBoxSparkAdapter — UDF, mapPartitions, Streaming
│
├── api/                     REST API
│   └── app.py               Flask — 12 endpoints, security headers, UUID validation
│
├── scenarios/               Industry scenario demonstrations (8 built-in)
│   └── run_scenarios.py
│
├── benchmarks/              Performance benchmark suite
│   └── run_benchmarks.py
│
tests/
├── test_glassbox.py         Core test suite — 172 tests, 27 classes
├── test_load_stress_security.py  Load/stress/security — 60 tests, 12 classes
└── test_framework.py        Framework components — 66 tests, 11 classes

examples/
└── industry_examples.py     12 industry use-case examples
```

---

## 4. Pipeline Stages — Detailed

The `GovernancePipeline` runs every decision through 9 ordered stages.
Stages are fail-fast: a block at any stage short-circuits all remaining stages.

```
DecisionRequest
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ SECURITY PRE-CHECK (before Stage 0)                         │
│  validate_agent_id() → rejects SQL/XSS/path-traversal       │
│  PayloadSanitizer.check() → scans for 25+ injection patterns│
│  Blocked → SECURITY-001 violation                           │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 0: AgentContract Validation                           │
│  Checks: permitted_types, max_amount, max_delegation_depth   │
│  Blocked → CONTRACT-001 violation                           │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 1: Context Capture                                    │
│  Enriches: timestamp, hostname, platform, agent_chain       │
│  Platform-safe: env-var precedence for hostname             │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 2: AuditRecord initialisation                         │
│  Creates the immutable audit record with enriched context   │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 3: Schema Validation                                  │
│  Required fields, type checks, min/max constraints          │
│  Blocked → SCHEMA-001 violation                             │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 4: Velocity Breaker                                   │
│  Per-agent: sliding window, cooldown, circuit breaker       │
│  Ecosystem: fleet-wide aggregate rate limit                 │
│  Blocked → VELOCITY-001 or ECOSYSTEM-001                    │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 5: Anomaly Detection                                  │
│  Z-score against per-agent rolling baseline                 │
│  Activates after min_samples (default: 10)                  │
│  Blocked → ANOMALY-001 with anomalous field descriptions    │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 6: Policy Enforcement                                 │
│  Evaluates all applicable registered policies               │
│  Built-in: 12 policies across 7 domains                     │
│  Custom: Python callables + YAML/JSON declarative rules     │
│  Blocked → policy violation list                            │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 7: Risk Evaluation                                    │
│  Composite weighted score 0–100                             │
│  Domain-specific factor extractors per decision type        │
│  Disposition: AUTO_EXECUTE ≤35 / HUMAN_REVIEW ≤70 / BLOCK  │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────────────────────────┐
│ STAGE 8: Disposition + Finalise                             │
│  AUTO_EXECUTE  → call executor (with retry), emit executed  │
│  HUMAN_REVIEW  → create WorkflowInstance, emit pending      │
│  BLOCK         → emit blocked event                         │
│  Audit: AuditLogger (in-memory) + AuditRepository (SQLite)  │
└─────────────────────────────────────────────────────────────┘
      │
      ▼
DecisionResponse
(+ ExecutionTrace if trace_enabled=True)
```

---

## 5. Storage Architecture — Repository Pattern

All storage is abstracted behind repository interfaces. The pipeline and
workflow engine never depend on a concrete storage class — they depend on
the abstract interface. This makes it trivially easy to add new backends
(PostgreSQL, Elasticsearch, DynamoDB) without touching pipeline logic.

```
                    ┌─────────────────────┐
                    │  PolicyRepository   │  (interface)
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴──────────────┐
                 │                            │
    ┌────────────▼──────┐         ┌──────────▼──────────┐
    │  InMemoryPolicy   │         │  SQLitePolicy        │
    │  Repository       │         │  Repository          │
    │  (tests, dev)     │         │  (production)        │
    └───────────────────┘         └──────────────────────┘

                    ┌─────────────────────┐
                    │  AuditRepository    │  (interface)
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴──────────────┐
                 │                            │
    ┌────────────▼──────┐         ┌──────────▼──────────┐
    │  AuditLogger      │         │  SQLiteAudit         │
    │  (deque ring buf) │         │  Repository          │
    │  in-memory        │         │  (indexed, queryable)│
    └───────────────────┘         └──────────────────────┘

                    ┌─────────────────────┐
                    │  WorkflowRepository │  (interface)
                    └──────────┬──────────┘
                               │
                 ┌─────────────┴──────────────┐
                 │                            │
    ┌────────────▼──────┐         ┌──────────▼──────────┐
    │  In-memory via    │         │  SQLiteWorkflow      │
    │  :memory: SQLite  │         │  Repository          │
    │  (tests)          │         │  (production)        │
    └───────────────────┘         └──────────────────────┘
```

**Adding PostgreSQL backend:**

```python
class PostgreSQLAuditRepository(AuditRepository):
    def save(self, record): ...  # implement the 5 methods
    def get_by_id(self, id): ...
    def query(self, **filters): ...
    def aggregate_spend(self, ...): ...
    def count(self, **filters): ...

# Inject into pipeline — nothing else changes
pipeline = GovernancePipeline(audit_repo=PostgreSQLAuditRepository(...))
```

---

## 6. Event-Driven Architecture

```
                GovernancePipeline
                        │
        ┌───────────────┼───────────────┐
        │               │               │
        ▼               ▼               ▼
  decision.executed  decision.blocked  policy.violated
  decision.pending_review  anomaly.detected
  circuit_breaker.tripped  security.violation
  workflow.sla_breached
        │
        ▼
┌───────────────────────────────────────────────────────┐
│                     EventBus                          │
│  Thread-safe · async handlers · wildcard subscriptions│
└───────┬───────────────┬───────────────┬───────────────┘
        │               │               │
        ▼               ▼               ▼
LoggingHandler    WebhookHandler   CustomHandler
(structured logs) (HTTP POST)      (your code)
```

**Integration example:**

```python
from glassbox.events.event_bus import EventBus, DecisionBlocked

bus = EventBus()

# Alert on any block
bus.subscribe("decision.blocked",
    lambda e: send_slack_alert(e.payload["agent_id"], e.payload["violations"]))

# Webhook to external system
bus.subscribe("*", WebhookEventHandler("https://my-siem.company.com/glassbox"))

pipeline = GovernancePipeline(event_bus=bus)
```

---

## 7. Declarative Rules — Policy-as-Data

GlassBox supports two policy formats:

**Format 1 — Python callable (for complex logic):**
```python
def my_rule(payload, context):
    if payload.get("amount", 0) > 500_000:
        return PolicyEvaluation("MY-001", "My Policy", "fail", "Over limit")
    return PolicyEvaluation("MY-001", "My Policy", "pass", "OK")

engine.register(Policy("MY-001", "My Policy", [DecisionType.PROCUREMENT], my_rule))
```

**Format 2 — Declarative YAML (no Python required):**
```yaml
rules:
  - policy_id: ORG-001
    name: Departmental Spending Cap
    applies_to: [procurement]
    logic: and
    conditions:
      - field: amount
        op: gt
        value: 100000
      - field: department_code
        op: in
        value: [DEPT-A, DEPT-B]
      - field: approval_ref
        op: missing
    result: fail
    message: "Amount {amount} in controlled department requires approval_ref."

  - policy_id: ORG-002
    name: Low Confidence Warning
    applies_to: [procurement, financial, pricing]
    conditions:
      - field: ctx.confidence
        op: lt
        value: 0.6
    result: warn
    message: "Low AI confidence — manual verification recommended."
```

```python
loader = RulesLoader()
loader.load_and_register("rules/org_policies.yaml", pipeline.policy_engine)
```

**Supported operators:** `gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `in`, `not_in`,
`missing`, `present`, `contains`, `startswith`, `regex`

---

## 8. Workflow Engine — State Machine

```
                    ┌─────────┐
                    │ pending │ ◄── created by pipeline on HUMAN_REVIEW
                    └────┬────┘
                         │ start_review()
                         ▼
                   ┌───────────┐
                   │ in_review │ ◄── reviewer picks it up
                   └─────┬─────┘
                         │
           ┌─────────────┼──────────────┐
           │             │              │
    approve()        reject()      escalate()
           │             │              │
           ▼             ▼              ▼
       ┌────────┐  ┌──────────┐  ┌───────────┐
       │approved│  │ rejected │  │ escalated │
       └────────┘  └──────────┘  └───────────┘

SLA monitoring (background thread, opt-in):
  → timed_out if not resolved within sla_minutes
  → auto-escalate if escalate_to is set
```

---

## 9. Thread-Safety Model

Every mutable shared state in GlassBox is protected:

| Component | Lock type | Scope |
|---|---|---|
| `AnomalyDetector._stats` | `threading.RLock` | All reads and writes |
| `PolicyEngine._policies` | `threading.RLock` | register, disable, evaluate |
| `AuditLogger._records` | `threading.Lock` | append, snapshot |
| `AuditLogger._file_locks` | per-path `threading.Lock` | JSONL file writes |
| `VelocityBreaker._windows` | per-agent `threading.Lock` | sliding window |
| `VelocityBreaker._ecosystem` | `threading.Lock` | ecosystem deque |
| `GovernancePipeline._contracts` | `threading.RLock` | contract registry |
| `GlassBoxLogger._loggers` | `threading.Lock` | double-checked locking |
| `SQLite repositories` | `threading.Lock` | all DB operations |
| `EventBus._handlers` | `threading.Lock` | subscribe, publish |

The pipeline itself is stateless per-request — `process()` can be called
from any number of threads simultaneously.

---

## 10. Async Architecture

```
asyncio event loop
        │
        │  await pipeline.process_async(request)
        │
        ▼
┌───────────────────────────────────────────┐
│  ThreadPoolExecutor (glassbox-async)      │
│  max_workers=8 (configurable)             │
│                                           │
│  Worker thread:                           │
│    pipeline._run_pipeline()               │
│    (all 9 stages, fully synchronous)      │
└───────────────────────────────────────────┘
        │
        │  result returned to event loop
        ▼
DecisionResponse
```

This design means:
- The asyncio event loop is never blocked
- All existing synchronous code works unchanged in async contexts
- `RetryExecutor.async_execute()` uses `asyncio.sleep()` (not `time.sleep()`)
- `DecisionReplay.async_replay_many()` uses `asyncio.Semaphore` to cap concurrency

---

## 11. Platform Deployment Patterns

### Standard VM / Docker
```python
pipeline = GovernancePipeline(
    log_dir="/var/log/glassbox",
    environment="production",
)
```

### Kubernetes
```python
from glassbox.adapters.platforms import KubernetesAdapter
adapter  = KubernetesAdapter()
pipeline = adapter.create_pipeline()

# K8s health probes
app.get("/ready",  adapter.readiness_check(pipeline))
app.get("/alive",  adapter.liveness_check())
```

### Databricks / Microsoft Fabric (PySpark)
```python
from glassbox.adapters.spark import GlassBoxSparkAdapter
adapter = GlassBoxSparkAdapter(spark)

# Govern entire DataFrame
result_df = adapter.govern_dataframe(decisions_df)

# Structured Streaming
query = adapter.govern_stream(
    stream_df, output_path="/dbfs/governed", checkpoint="/dbfs/ckpt")
```

### Full production stack
```python
from glassbox.store.repository     import RepositoryFactory
from glassbox.events.event_bus     import EventBus, LoggingEventHandler
from glassbox.workflow.workflow_engine import WorkflowEngine
from glassbox.rules.rules_engine   import RulesLoader

repos     = RepositoryFactory.sqlite(db_dir="/var/lib/glassbox")
bus       = EventBus()
bus.subscribe("*", LoggingEventHandler().handle)
wf_engine = WorkflowEngine(repository=repos["workflow"], event_bus=bus,
                            monitor_sla=True, default_sla_minutes=60)

pipeline  = GovernancePipeline(
    event_bus=bus, audit_repo=repos["audit"],
    workflow_engine=wf_engine, trace_enabled=True,
)

# Load declarative policies from YAML files
RulesLoader().load_and_register("rules/", pipeline.policy_engine, is_directory=True)
```

---

## 12. Data Flow — Decision Lifecycle

```
t=0ms   AI Agent submits DecisionRequest
t=0.01  Security pre-check (agent_id + payload sanitization)
t=0.02  AgentContract checked (permitted types, limits)
t=0.05  Schema validated
t=0.07  Velocity window checked (per-agent + ecosystem)
t=0.10  Anomaly detection Z-score computed
t=0.15  All applicable policies evaluated
t=0.18  Risk score computed (0–100)
t=0.20  Disposition determined (execute/review/block)
t=0.22  AuditLogger.log() — in-memory ring buffer
t=0.23  AuditRepository.save() — SQLite (if configured)
t=0.24  EventBus.publish() — async, non-blocking
t=0.25  WorkflowEngine.create() — if HUMAN_REVIEW (async)
t=0.25  DecisionResponse returned to caller
```

Typical end-to-end latency: **P50 = 0.11ms, P99 = 0.47ms** (single-thread, no DB)

---

## 13. Security Model

```
Every request passes through three security checks before Stage 0:

1. agent_id validation
   Regex: ^[a-zA-Z0-9_\-\.@:]+$  (max 128 chars)
   Rejects: path traversal, SQL, script characters
   If blocked: SECURITY-001, no audit record with malicious data

2. Payload sanitization (PayloadSanitizer)
   SQL injection:    15+ patterns (OR 1=1, UNION SELECT, xp_cmdshell, …)
   Script injection: XSS, SSTI (Jinja/EL), command injection, eval()
   Path traversal:   ../ and ..\\ detection
   Null bytes:       \x00 rejection
   Blocked keywords: /etc/passwd, cmd.exe, powershell, …
   Size limits:      64KB max payload, depth 5, width 50 keys
   If blocked: SECURITY-001, malicious payload NOT logged

3. AgentContract (Stage 0)
   Restricts decision types, amounts, and delegation depth per agent
```

---

## 14. Extension Points

GlassBox is designed to be extended at every layer:

| Extension point | How |
|---|---|
| Custom policy | `engine.register(Policy(..., rule=my_fn))` |
| Declarative rule | YAML/JSON via `RulesLoader` |
| Custom risk factors | Override `RiskEvaluator` with custom extractors |
| Storage backend | Implement `PolicyRepository`, `AuditRepository`, `WorkflowRepository` |
| Event handler | `bus.subscribe("*", my_handler)` |
| Platform adapter | Subclass `BaseAdapter`, override `_log_dir()`, `_env_name()` |
| Pipeline stage | Subclass `GovernancePipeline`, override `_run_pipeline()` |
| Schema | Add entry to `SCHEMAS` dict in `schema_validator.py` |
| Decision type | Add to `DecisionType` enum and schema + risk factor extractor |

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
*Not affiliated with any employer, vendor, or customer engagement.*
