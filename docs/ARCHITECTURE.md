# GlassBox — Architecture Overview

**v1.1.0 | Mohammed Akbar Ansari | Independent Researcher**

> Full architecture reference: [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md)

---

## What GlassBox Is

GlassBox is a **Runtime Decision Governance Framework** for autonomous AI systems.
It implements the *decision-semantic layer* — the missing tier between AI agents and
enterprise execution systems. Every AI-generated operational decision passes through
GlassBox before it reaches any downstream system.

```
AI Agent  ──►  GlassBox (9-stage governance)  ──►  Enterprise System
                    │                                      │
               BLOCK / REVIEW                         EXECUTE
                    │
             Audit Trail + Events
```

---

## Layer Architecture

```
┌──────────────────────────────────────────────────────┐
│  Tier 3 — REST API & Integration Layer               │
│  Flask API · Platform Adapters · Event Bus · SSE     │
├──────────────────────────────────────────────────────┤
│  Tier 2 — Application Layer                          │
│  GovernancePipeline · WorkflowEngine · RulesLoader   │
│  DecisionReplay · RetryExecutor · PolicySimulator    │
├──────────────────────────────────────────────────────┤
│  Tier 1 — Core Framework                             │
│  PolicyEngine · RiskEvaluator · AnomalyDetector      │
│  VelocityBreaker · SchemaValidator · Sanitizer       │
│  AuditLogger · SQLite Repositories · EventBus        │
└──────────────────────────────────────────────────────┘
```

---

## The 9-Stage Pipeline

Every `DecisionRequest` passes through these stages in order. Any stage may
**block** execution, short-circuiting all later stages.

| # | Stage | Module | Blocks On |
|---|-------|--------|-----------|
| Pre | Security Pre-check | `security/sanitizer.py` | SQL/XSS/injection in payload or `agent_id` |
| 0 | AgentContract Validation | `governance/pipeline.py` | Unauthorised `decision_type`, amount limit exceeded |
| 1 | Context Capture | `governance/context_capture.py` | — (enrichment only) |
| 2 | AuditRecord Init | `governance/pipeline.py` | — (record creation) |
| 3 | Schema Validation | `governance/schema_validator.py` | Missing/wrong-type required fields |
| 4 | Velocity Breaker | `governance/velocity_breaker.py` | Per-agent > 100 req/min; ecosystem limit |
| 5 | Anomaly Detection | `governance/anomaly_detector.py` | Z-score > 3σ after min\_samples |
| 6 | Policy Enforcement | `governance/policy_engine.py` | Any registered policy returns `fail` |
| 7 | Risk Evaluation | `governance/risk_evaluator.py` | Composite risk score routing |
| 8 | Disposition + Finalise | `governance/pipeline.py` | Audit + event emission |

### Disposition Thresholds (Stages 7–8)

| Risk Score | Disposition | Action |
|---|---|---|
| ≤ 35 | `AUTO_EXECUTE` | Execute immediately |
| 36–70 | `HUMAN_REVIEW` | Route to WorkflowEngine |
| > 70 | `BLOCK` | Rejected, emit `decision.blocked` event |

---

## Component Map

```
glassbox/
├── governance/          Core pipeline domain logic
│   ├── pipeline.py      GovernancePipeline — 9-stage orchestrator
│   ├── models.py        DecisionRequest, AuditRecord, DecisionResponse, …
│   ├── policy_engine.py Thread-safe policy registry + evaluator (26 built-in policies)
│   ├── risk_evaluator.py Weighted composite scoring 0–100
│   ├── anomaly_detector.py Z-score rolling baselines per agent
│   ├── velocity_breaker.py Sliding-window circuit breaker
│   ├── schema_validator.py Payload structure validation
│   ├── audit_logger.py  In-memory ring buffer + JSONL file rotation
│   ├── decision_replay.py Sync + async + parallel replay
│   ├── simulator.py     Dry-run policy impact analysis
│   ├── encryption.py    AES-256 field-level encryption
│   ├── multitenancy.py  Tenant routing + quota enforcement
│   └── access_control.py RBAC with role inheritance
│
├── store/               Persistence layer
│   ├── database.py      GlassBoxDatabase — SQLite, schema migrations v1–v4
│   └── repository.py    PolicyRepository, AuditRepository, WorkflowRepository
│
├── security/            Input sanitisation
│   └── sanitizer.py     PayloadSanitizer — 25+ injection pattern detectors
│
├── rules/               Declarative rules engine
│   ├── rules_engine.py  YAML/JSON → Policy compilation, 12 operators
│   └── hot_reload.py    Live rule updates without restart
│
├── workflow/            Approval workflow
│   └── workflow_engine.py States: pending → in_review → approved/rejected
│
├── events/              Domain events
│   └── event_bus.py     8 event types, async handlers, webhooks, SSE
│
├── orchestration/       Multi-agent orchestration
│   └── orchestrator.py  Chain, DAG graph, Saga patterns
│
├── rag/                 RAG governance
│   └── governance.py    Query, retrieval, agentic loop governance
│
├── adapters/            Platform adapters
│   └── platforms.py     Databricks, Kubernetes, Fabric; auto_detect_adapter()
│
├── integrations/        AI framework adapters
│   └── adapters.py      LangChain, LangGraph, AutoGen, MCP Gateway, OPA
│
├── compliance/          Compliance catalogue
│   └── catalogue.py     70 controls across 17 frameworks
│
└── api/                 REST API
    └── app.py           Flask — 15 endpoints, rate limiting, security headers
```

---

## Built-in Policies (26 total)

Policies span 7 decision domains: Procurement, Financial, IT-Ops, HR, Pricing,
Logistics, and Clinical. Register custom policies with `pipeline.policy_engine.register()`.

See [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md) for the full policy reference.

---

## Data Flow: Decision Lifecycle

```
POST /decisions
      │
      ▼ JSON parse + rate-limit check
      │
      ▼ PayloadSanitizer.check(payload)        ─── BLOCK if injection found
      │
      ▼ Stage 0: AgentContract check            ─── BLOCK if type not permitted
      │
      ▼ Stage 1: ContextCapture enrich          (timestamp, hostname, platform)
      │
      ▼ Stage 3: SchemaValidator                ─── BLOCK if required field missing
      │
      ▼ Stage 4: VelocityBreaker                ─── BLOCK if rate exceeded
      │
      ▼ Stage 5: AnomalyDetector                ─── BLOCK if statistical outlier
      │
      ▼ Stage 6: PolicyEngine.evaluate()        ─── BLOCK if policy fails
      │
      ▼ Stage 7: RiskEvaluator.score()
      │
      ▼ Stage 8: Disposition routing
      │          ├─ AUTO_EXECUTE → response (executed)
      │          ├─ HUMAN_REVIEW → WorkflowInstance created
      │          └─ BLOCK        → response (blocked)
      │
      ▼ AuditLogger.append() + EventBus.publish()
      │
      ▼ DecisionResponse (JSON)
```

---

## Performance Characteristics

| Metric | Typical | P99 | Notes |
|---|---|---|---|
| Full pipeline latency | < 0.2 ms | < 0.5 ms | In-memory, no I/O |
| With SQLite audit write | < 2 ms | < 5 ms | WAL mode |
| Throughput (single thread) | 5,500 req/s | — | In-memory audit |
| Throughput (SQLite) | 200–600 req/s | — | Disk I/O bound |
| Throughput (10 threads) | 1,500–2,500 req/s | — | WAL concurrency |

See [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md) for tuning guide.

---

## Further Reading

- **Full architecture reference**: [DEVELOPMENT/architecture.md](DEVELOPMENT/architecture.md)
- **API endpoints**: [API/endpoint_reference.md](API/endpoint_reference.md)
- **Deployment guide**: [DEPLOYMENT.md](DEPLOYMENT.md)
- **Compliance controls**: [COMPLIANCE/requirements.md](COMPLIANCE/requirements.md)
- **Troubleshooting**: [USER/troubleshooting.md](USER/troubleshooting.md)
- **Glossary**: [GLOSSARY.md](GLOSSARY.md)

---

*GlassBox v1.1.0 · Apache 2.0 · Mohammed Akbar Ansari*
