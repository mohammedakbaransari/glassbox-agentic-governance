# GlassBox Changelog

All notable changes documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.0] — 2026-04-03 — **Initial Public Release**

GlassBox v1.0.0 is the complete, production-ready release of the Runtime Decision Governance
Framework for Autonomous AI Systems — the decision-semantic layer that sits between autonomous
AI agents and enterprise execution systems.

### Framework at a Glance

| Metric | Value |
|---|---|
| Built-in policies | 24 across 10 domains |
| Decision types | 12 (procurement, pricing, financial, inventory, logistics, it_ops, hr, clinical, trading, content, legal, custom) |
| Compliance controls | 70 across 17 frameworks |
| Test suite | 551 tests across 6 suites and 39 test classes — all passing |
| Python versions | 3.9 · 3.10 · 3.11 · 3.12 |
| Mandatory dependencies | Zero (stdlib only) |
| P99 governance latency | < 0.2 ms |

---

### Core Governance Pipeline (9 stages)

- `GovernancePipeline` — synchronous, async-capable, event-driven orchestrator
- `AgentContract` — Stage 0: agent identity, permitted types, transaction authority, delegation depth
- `PayloadSanitizer` — Security pre-check: SQL injection (15+ patterns), SSTI, XSS, path traversal
- `SchemaValidator` — Stage 2: structural validation per decision type
- `VelocityBreaker` — Stage 3: per-agent + ecosystem fleet circuit breakers
- `AnomalyDetector` — Stage 4: Z-score numeric baseline + `CategoricalTracker` (string fields)
- `PolicyEngine` — Stage 5: 24 built-in + unlimited custom + declarative YAML/JSON rules
- `RiskEvaluator` — Stage 6: composite 0–100 risk scoring with domain-specific factor extractors
- Disposition — Stage 7: AUTO_EXECUTE (≤35) · HUMAN_REVIEW (≤70) · BLOCK
- `AuditLogger` — Stage 8: immutable append-only ring buffer + SQLite repository

---

### Built-in Policies (24)

**Original 13 (v1.0 baseline):**
`PROC-001` Procurement spending limit ($500K) ·
`PROC-002` Approved supplier registry ·
`PROC-003` High-risk category controls ·
`PRICE-001` 30% price change limit ·
`PRICE-002` Floor price enforcement ·
`FIN-001` Transfer limit ($1M) ·
`ITOPS-001` Change window for destructive IT actions ·
`INV-001` Inventory quantity limit ·
`LOG-001` High-value logistics approval ·
`HR-001` Salary adjustment threshold ·
`AI-001` Model confidence floor (≥0.30) ·
`ENV-001` Production user-override block ·
`AGG-001` Fleet aggregate spend budget (cross-agent)

**New in this release (11):**
`FIN-002` Daily transfer velocity limit ·
`FIN-003` Counterparty concentration / missing counterparty ·
`FIN-004` BSA Currency Transaction Report trigger (advisory) ·
`FIN-005` Structuring / round-number detection ·
`PROC-004` Sole-source justification (FAR 6.302) ·
`PROC-006` OFAC/UN sanctions check (sanctioned countries + debarred suppliers) ·
`CLIN-001` Controlled substance DEA authorisation (21 CFR Part 1306) ·
`CLIN-002` Dosage weight-based safety check ·
`TRADE-001` Trading position notional limit (MiFID II Art.17) ·
`TRADE-002` Fat-finger detection (qty vs avg daily) ·
`GEN-001` PII detection in AI-generated content (GDPR Art.5, CCPA, HIPAA) ·
`GEN-002` GDPR Article 22 automated decision disclosure

---

### New Decision Types (12 total, up from 8)

Added: `CLINICAL` · `TRADING` · `CONTENT` · `LEGAL`

---

### New Modules

**`glassbox/governance/currency.py`** — `CurrencyNormalizer`
30+ ISO 4217 currencies · `to_base()` · `configure_rates()` · `normalise_payload_amount()` ·
`currency` and `jurisdiction` fields added to `DecisionContext`

**`glassbox/governance/explainer.py`** — `DecisionExplainer`
Plain-language governance explanations · Three levels: BRIEF / STANDARD / DETAILED ·
Maps all 24 policy IDs to human-readable descriptions + regulatory references ·
EU AI Act Article 13 transparency compliance · `risk_explanation` and `explanation` fields
in `DecisionResponse`

**`glassbox/governance/simulator.py`** — `PolicySimulator`
Dry-run policy impact analysis before deployment · `simulate_policy()` · `simulate_policies()` ·
`compare_policies()` · `SimulationResult.summary_text` · Parallel execution via ThreadPoolExecutor ·
Answers: "If I activate this policy today, what would have happened to the last N decisions?"

**`glassbox/governance/trust.py`** — `AgentTrustScorer`
Decision-quality-based trust scoring (0–1000, 5 tiers) · Subscribes to EventBus ·
+5 executed · -20 blocked · -10 violation · -15 anomaly · -50 circuit trip ·
Time-based decay toward 600 at 1pt/hour · `reset_agent()` · `score_summary()`

**`glassbox/integrations/mcp_gateway.py`** — `MCPGovernanceGateway` + `MCPToolScanner`
Governs Model Context Protocol tool calls through the governance pipeline ·
Static analysis: tool poisoning (7 patterns) · typosquatting (Levenshtein ≤2) ·
exfiltration instructions · privilege escalation · `approve_tool_registry()` ·
`call_tool()` · `call_tool_async()`

**`glassbox/integrations/opa_adapter.py`** — `OPARegoAdapter`
OPA Rego policy integration · HTTP server mode + CLI bundle mode ·
Translates GlassBox decisions to OPA input document ·
Configurable fallback: fail-open or fail-closed when OPA unreachable ·
`as_policy()` · `health_check()`

**`sdk/typescript/index.ts`** — TypeScript SDK
Zero-dependency typed client for Node.js 18+ and browsers ·
`GlassBoxClient` · `govern()` · `governSafe()` · `governBatch()` · `streamEvents()` ·
`GovernanceBlockedError` · Full enum and type definitions

---

### Extended Adapters

**`OpenAIAgentsAdapter`** — `@adapter.govern()` decorator + `wrap_functions()` ·
Sync and async · OpenAI Agents SDK tool governance

**`PydanticAIAdapter`** — `@adapter.govern()` + `wrap_tools()` ·
Pydantic model payload extraction · Sync and async

---

### API Enhancements

**`POST /decisions/batch`** — Bulk governance endpoint ·
Accepts up to 499 decisions per request · Parallel via ThreadPoolExecutor (max 16 workers) ·
Returns per-decision results + summary (total / executed / blocked / pending_review / latency_ms)

**`GET /events/stream`** — Server-Sent Events real-time governance stream ·
Publishes all domain events (decision.executed, decision.blocked, policy.violated,
anomaly.detected, circuit_breaker.tripped, security.violation) ·
15-second heartbeat · Automatic cleanup on client disconnect

---

### Workflow Engine Enhancements

**Quorum approval** — `approve(min_approvers=N)` · `quorum_approve(min_approvers=2)` ·
Thread-safe engine-level quorum state · Distinct-approver enforcement ·
Partial approval steps recorded in audit trail · Automatically transitions to approved
when quorum count is reached

---

### Compliance Catalogue

70 controls across 17 frameworks (up from 70 controls / 17 frameworks):

**New frameworks added:**
- **ISO 27001:2022** — 5 controls: policy management, roles, logging, monitoring, compliance review
- **SOC 2 Type II** — 4 controls: logical access, system monitoring, change management, risk mitigation
- **HIPAA** — 4 controls: security management, audit controls, minimum necessary, workforce security
- **ISO/IEC 42001:2023** — 4 controls: AI risk planning, impact assessment, performance evaluation, continual improvement
- **Colorado AI Act (SB 24-205)** — 3 controls: risk management, human review mechanism, transparency disclosure
- **PCI DSS v4.0** — 2 controls: audit log protection, security event detection

---

### Risk Evaluation Enhancements

- **Time-of-day factor** — After-hours decisions (UTC 22:00–06:00) add 20 risk points (weight 0.05)
- **Environment factor** — Production environment decisions add 20 risk points (weight 0.05)
- **`risk_explanation` field** — Plain-language factor breakdown in `DecisionResponse`
  e.g. "Risk 78/100: transaction size (18pts), missing contract (10pts), after-hours timing (1pt)"
- **`explanation` field** — Why-blocked text for EU AI Act Article 13 transparency

---

### Context Capture

`DecisionContext` new fields:
- `currency: str = "USD"` — ISO 4217 currency code (propagated through enrich())
- `jurisdiction: str = "US"` — ISO 3166-1 country code
- `patient_id: Optional[str]` — Healthcare context
- `account_type: str` — Financial context (retail/institutional)

---

### Test Suite

| Suite | Tests | Focus |
|---|---|---|
| test_glassbox.py | 189 | Core pipeline, 24 policies, schema, anomaly, velocity, audit, API, platform adapters |
| test_load_stress_security.py | 60 | Load, stress, 500-thread spike, injection detection |
| test_framework.py | 66 | SQLite repos, event bus, declarative rules, workflow lifecycle |
| test_advanced.py | 68 | Orchestration (chain/DAG/saga), RAG governance, multi-tenancy, compliance |
| test_v1_features.py | 52 | OTel, LlamaIndex, CrewAI, PolicyHotReloader, ComplianceReporter, NL authoring |
| test_v1_1_features.py | 116 | Currency, new policies, explainer, simulator, trust scorer, MCP gateway, OPA, quorum, batch API, SSE, new compliance frameworks |
| **TOTAL** | **551** | **All passing on Python 3.9–3.12** |

---

### Performance

| Metric | Value |
|---|---|
| Single-thread throughput | ~5,500 decisions/sec (full 9-stage pipeline) |
| P50 latency | 0.10 ms |
| P99 latency | 0.18 ms |
| Policy accuracy | 100% (24 policies × structured test corpus) |
| Anomaly precision/recall | 100% / 100% |
| 500-thread spike | 0 errors, 0 collisions |

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
*Not affiliated with any employer, vendor, or customer engagement.*
