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

## 4a. Error Path Scenarios

While the happy path shows a decision flowing through all stages, real systems encounter failures. Here are key error scenarios and how GlassBox handles them:

### Scenario 1: Security Violation (Early Exit — Pre-Stage 0)

```
Request with SQL injection payload
      │
      ▼ SECURITY PRE-CHECK
    [PayloadSanitizer detects: " OR 1=1' in field]
      │
      ▼ BLOCKED
┌────────────────────────────────────┐
│ response.final_status = BLOCKED    │
│ response.flaw detected: security   │
│ policy_violations[0] = "SECURITY-001: SQL injection pattern detected" │
│ response.risk_score = 100 (critical) │
└────────────────────────────────────┘
      │
      ▼ Event: security.violation
      │ (alert SIEM immediately)
      │
   LOGGED (immutable audit record)
  (No downstream system sees this)
```

**Outcome:** Malicious payload blocked before reaching policy engine. No risk of downstream system compromise.

---

### Scenario 2: Policy Violation (Fail-Fast Exit at Stage 5)

```
Valid request but violates policy
      │
      ▼ STAGES 0–4 pass
      │ (contract OK, schema OK, velocity OK, anomaly OK)
      │
      ▼ STAGE 5: POLICY ENFORCEMENT
    [PolicyEngine evaluates: amount=$750,000 > limit=$500,000]
      │
      ▼ BLOCKED
┌────────────────────────────────────────┐
│ response.final_status = BLOCKED        │
│ policy_violations[0] = "[PROC-001] Amount $750K exceeds $500K approval limit" │
│ response.risk_score = 92 (very high)   │
│ response.disposition = BLOCK           │
└────────────────────────────────────────┘
      │
      ▼ Event: policy.violated
      │ Event: decision.blocked
      │
   LOGGED + ESCALATED
  (Alert compliance team)
```

**Outcome:** Invalid decision blocked with minimal latency. Compliance evidence recorded.

---

### Scenario 3: Anomaly Detection Trip (Advisory Block at Stage 4)

```
Statistically unusual request
      │
      ▼ STAGES 0–3 pass
      │
      ▼ STAGE 4: ANOMALY DETECTION
    [AnomalyDetector: agent_x avg_amount=$10K, this request=$500K]
    [Z-score: 9.8 (threshold 3.0)]
      │
      ▼ ANOMALY DETECTED
┌────────────────────────────────────────┐
│ response.final_status = BLOCKED        │
│ anomaly_fields = ["amount"]            │
│ anomaly_detector_message = "Amount deviates 9.8σ from baseline" │
│ response.risk_score = 85 (high)        │
└────────────────────────────────────────┘
      │
      ▼ Event: anomaly.detected
      │ Suggest: manual review
      │
   LOGGED + ALERT
  (Optional: escalate to human)
```

**Outcome:** Unusual pattern detected early, blocking cascade from undetected bugs. Can configure `anomaly_enabled=False` for permissive mode.

---

### Scenario 4: Velocity Breaker Trip (Rate Limit Block at Stage 3)

```
Agent exceeded rate limits
      │
      ▼ STAGES 0–2 pass
      │
      ▼ STAGE 3: VELOCITY BREAKER
    [Agent "procurement_ai" sent 101 decisions in 60 seconds]
    [Limit: 100/60sec — BREACHED]
      │
      ▼ CIRCUIT BREAKER TRIPS
┌────────────────────────────────────────┐
│ response.final_status = BLOCKED        │
│ circuit_breaker_triggered = true       │
│ message = "Agent procurement_ai rate limit exceeded" │
│ cooldown_until = 60 seconds            │
│ response.disposition = BLOCK           │
└────────────────────────────────────────┘
      │
      ▼ Event: circuit_breaker.tripped
      │ (retry after cooldown)
      │
   LOGGED
  (Subsequent requests blocked until cooldown expires)
```

**Outcome:** Runaway agent stopped. Protects downstream systems and database. Cooldown prevents repeated violations.

---

### Scenario 5: Disposition → Human Review (Non-Error Path, Stage 7)

```
Decision is valid but risky → routes to human
      │
      ▼ STAGES 0–6 pass (no blocks, high risk score)
      │
      ▼ STAGE 7: DISPOSITION
    [Risk score = 72 (above HUMAN_REVIEW threshold 70)]
    [Disposition = HUMAN_REVIEW, not AUTO_EXECUTE or BLOCK]
      │
      ▼ CREATE WORKFLOW
┌────────────────────────────────────────┐
│ response.final_status = PENDING_REVIEW │
│ response.disposition = HUMAN_REVIEW    │
│ workflow_id = "wf-xyz-12345"           │
│ sla_expires_at = now + 120 minutes     │
└────────────────────────────────────────┘
      │
      ▼ Event: decision.pending_review
      │ (to approval queue UI)
      │
   LOGGED
  (Waits for human approval/rejection)
```

**Outcome:** High-risk decisions get human eyes without blocking. SLA tracking ensures timely review.

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

## 14. Component Dependencies

This matrix shows which components depend on which others:

| Component | Depends On | Used By | Purpose |
|-----------|-----------|---------|---------|
| **GovernancePipeline** | All stage components, repositories, event bus | REST API, orchestrators, adapters | Central orchestrator |
| **PolicyEngine** | — | GovernancePipeline, RiskEvaluator | Policy registry + evaluation |
| **RiskEvaluator** | PolicyEngine | GovernancePipeline | Composite risk scoring |
| **AnomalyDetector** | — | GovernancePipeline | Statistical anomaly detection |
| **VelocityBreaker** | — | GovernancePipeline | Rate limiting |
| **SchemaValidator** | — | GovernancePipeline | Payload schema validation |
| **PayloadSanitizer** | — | GovernancePipeline (pre-check) | Security threat detection |
| **AuditLogger** | — | GovernancePipeline | In-memory audit ring buffer |
| **AuditRepository** | — | GovernancePipeline, REST API | Persistent audit storage |
| **PolicyRepository** | — | PolicyEngine, GovernancePipeline | Policy persistence |
| **WorkflowRepository** | — | WorkflowEngine, REST API | Workflow state storage |
| **WorkflowEngine** | WorkflowRepository, EventBus | GovernancePipeline | Approval workflow orchestration |
| **EventBus** | — | All components | Event publishing + subscription |
| **RulesLoader** | PolicyEngine | (admin tools) | YAML/JSON rule compilation |
| **ContextCapture** | — | GovernancePipeline (stage 1) | Metadata enrichment |
| **ExecutionTrace** | — | GovernancePipeline (optional) | Per-stage debugging |
| **AgentContract** | — | GovernancePipeline (stage 0) | Agent authority validation |
| **MultiTenantPipeline** | GovernancePipeline, TenantRegistry | Multi-tenant deployments | Tenant isolation |
| **AgentOrchestrator** | GovernancePipeline | Multi-agent workflows | Chain/DAG/Saga orchestration |
| **RAGQueryGovernor** | — | AgenticRAGOrchestrator | Query validation + filtering |
| **RAGRetrievalGovernor** | — | AgenticRAGOrchestrator | Retrieved chunk validation |
| **AgenticRAGOrchestrator** | GovernancePipeline, RAG governors | RAG applications | RAG governance orchestrator |
| **GlassBoxSparkAdapter** | GovernancePipeline | PySpark/Databricks | DataFrame/Streaming governance |
| **LangChainAdapter** | GovernancePipeline | LangChain agents | Transparent tool governance |
| **LangGraphAdapter** | GovernancePipeline | LangGraph workflows | Node + state governance |
| **AutoGenAdapter** | GovernancePipeline | AutoGen agents | Function mapping governance |
| **REST API (app.py)** | GovernancePipeline, all repositories | HTTP clients | REST endpoint handler |

**Key insight:** `GovernancePipeline` is the hub — all components eventually feed into it or are used by it. This keeps coupling low and testability high.

---

## 15. Configuration Parameters & Tuning

| Parameter | Component | Type | Default | Range | When to Modify |
|-----------|-----------|------|---------|-------|-----------------|
| `anomaly_min_samples` | AnomalyDetector | int | 10 | 5–100 | Lower = faster activation; Higher = fewer false positives |
| `anomaly_z_threshold` | AnomalyDetector | float | 3.0 | 1.5–5.0 | Tighter = fewer anomalies; Looser = catch more outliers |
| `velocity_window_seconds` | VelocityBreaker | int | 60 | 10–600 | Shorter = tighter rate limiting; Longer = more permissive |
| `max_decisions_per_window` | VelocityBreaker | int | 100 | 10–10K | Adjust per agent throughput needs |
| `velocity_cooldown_seconds` | VelocityBreaker | int | 60 | 10–600 | Shorter = faster recovery; Longer = stronger braking |
| `ecosystem_max_decisions` | VelocityBreaker | int | 10K | 1K–1M | Fleet-wide aggregate limit |
| `risk_threshold_execute` | RiskEvaluator | int | 35 | 0–50 | Scores ≤ this auto-execute immediately |
| `risk_threshold_review` | RiskEvaluator | int | 70 | 50–100 | Scores ≤ this route to HUMAN_REVIEW; above = BLOCK |
| `async_audit_writes` | AuditLogger | bool | True | bool | False = sync (safer) vs True = async (faster) |
| `trace_enabled` | ExecutionTrace | bool | False | bool | Enable for debugging; disable for performance |
| `max_payload_bytes` | PayloadSanitizer | int | 1M | 100K–50M | Small = DoS protection; Large = flexibility |
| `policy_engine_cache_size` | PolicyEngine | int | 1000 | 100–10K | Larger = more memory, lower latency |
| `audit_ring_buffer_size` | AuditLogger | int | 50K | 1K–1M | Memory vs coverage tradeoff |
| `default_sla_minutes` | WorkflowEngine | int | 120 | 10–1440 | Approval deadline for human review |
| `monitor_sla` | WorkflowEngine | bool | False | bool | Enable to auto-escalate on SLA breach |
| `log_level` | GlassBoxLogger | str | INFO | DEBUG/INFO/WARNING/ERROR/CRITICAL | Vebosity |
| `include_payload` | AuditLogger | bool | True | bool | False = PII protection (don't log sensitive data) |

### Tuning Strategies

**For Latency (sub-1ms target):**
```python
pipeline = GovernancePipeline(
    trace_enabled=False,           # disable per-stage tracing
    async_audit_writes=True,       # non-blocking I/O
    anomaly_detector=None,         # disable if optional
)
engine.policy_engine.cache_size = 10_000  # increase cache
```

**For Consistency (safety-first):**
```python
pipeline = GovernancePipeline(
    trace_enabled=True,            # detailed debugging
    async_audit_writes=False,      # synchronous audit (safer)
)
breaker.configure(
    max_decisions=50,              # aggressive rate limit
    window_seconds=10,             # tight window
)
```

**For Throughput (high volume):**
```python
pipeline = GovernancePipeline(
    async_audit_writes=True,
    environment="production",
)
breaker.configure(
    max_decisions=5_000,           # permissive
    window_seconds=60,
    cooldown_seconds=30,           # faster recovery
)
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

## See Also

- **[GLOSSARY.md](../GLOSSARY.md)** — Definitions of architectural terms (policy, disposition, anomaly, etc.)
- **[TROUBLESHOOTING.md](../USER/troubleshooting.md)** — Common architecture issues and solutions
- **[API/endpoint_reference.md](../API/endpoint_reference.md)** — REST API reference for remote governance
- **[DEPLOYMENT.md](../DEPLOYMENT.md)** — Running GlassBox on Databricks, Kubernetes, Fabric
- **Module READMEs** — [governance](../glassbox/governance/README.md), [rules](../glassbox/rules/README.md), [workflow](../glassbox/workflow/README.md), and 8 others

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
*Not affiliated with any employer, vendor, or customer engagement.*
