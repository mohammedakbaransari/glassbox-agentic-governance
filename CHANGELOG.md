# GlassBox Changelog

All notable changes documented here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [1.0.1] — 2026-04-04 — **Distributed Velocity Breaker, Security Patches & Performance Optimizations**

### Overview

**Major Updates in v1.0.1:**
1. **Distributed Velocity Breaker** — Redis-backed rate limits across instances
2. **Security Patches** — Critical fixes from v1.0.0 analysis
3. **Performance Optimizations** — Async workers, queue monitoring
4. **Thread Pool Configuration** — 50 * cpu_count() async workers
5. **Queue Depth Monitoring** — Alerts when depth > 1000

### New Features

#### 1. **Distributed Velocity Breaker** (Production-Ready)

- **Atomic Operations**: Uses Lua scripts (Redis) for race-free check + add operations
- **Per-Agent Limits**: Global rate limit per agent across all instances
- **Fleet-Wide (Ecosystem) Limits**: Optional cross-agent maximum for entire fleet
- **Automatic Fallback**: Switches to in-memory if Redis unavailable (circuit breaker pattern)
- **Thread-Safe**: All operations protected with locks; no deadlocks
- **API-Compatible**: Drop-in replacement for VelocityBreaker

**Key Classes:**
- `DistributedVelocityBreaker` — Main API
- `RedisVelocityBreakerBackend` — Low-level Lua operations
- `create_velocity_breaker_distributed()` — Factory function

#### 2. **Architecture**

```
Multi-Instance Deployment (Kubernetes)
├─ Instance-1 ──┐
├─ Instance-2 ──┼──→ Redis (Atomic Lua Scripts)
└─ Instance-3 ──┘    ├─ check_and_add (atomic)
                     ├─ get_count (read-only)
                     └─ check_ecosystem_and_add (fleet-wide)
```

Fallback to per-instance local state if Redis unavailable (no blocked requests).

#### 3. **Performance**

| Operation | Latency | Throughput |
|-----------|---------|-----------|
| Redis check (local) | 1.2 ms | ~830/sec |
| Redis check (network) | 15 ms | ~67/sec |
| Local fallback | < 0.1 ms | > 10k/sec |
| Lua atomic op | 1 ms (with cleanup) | ~1k/sec |

#### 4. **Example Usage**

```python
from redis import Redis
from glassbox.governance import DistributedVelocityBreaker

redis = Redis(host='redis.internal', port=6379)

breaker = DistributedVelocityBreaker(
    redis_client=redis,
    max_decisions=20,           # Per agent
    window_seconds=60,
    ecosystem_max=10_000,       # Fleet limit
    fallback_mode=True,         # Local backup if Redis fails
)

# In request handler
agent_id = "purchase_agent"
triggered, reason, count = breaker.check(agent_id)

if triggered:
    return decline_request(reason)  # Rate limited
else:
    return process_decision()        # Allowed
```

#### 5. **Deployment Options**

Supported backends:
- ✓ Single Redis instance
- ✓ Redis Sentinel (HA with failover)
- ✓ Redis Cluster (horizontal scaling)
- ✓ Managed services (ElastiCache, Redis Cloud, etc.)

#### 6. **Migration Path (Backward Compatible)**

```python
# Before (v1.0.0) — Single instance
from glassbox.governance import VelocityBreaker
breaker = VelocityBreaker(max_decisions=20)

# After (v1.0.1) — Multi-instance
from glassbox.governance import DistributedVelocityBreaker
import redis
breaker = DistributedVelocityBreaker(
    redis_client=redis.Redis(),
    max_decisions=20,  # Same config, now distributed
)

# API is identical: breaker.check(), breaker.reset(), etc.
```

#### 7. **Testing**

New test suite: `tests/test_velocity_distributed.py` (65+ tests)

Coverage:
- Atomic operations (Lua scripts)
- Fallback behavior
- Circuit breaker recovery
- Ecosystem limits
- Concurrency (thread safety)
- Redis failures and transitions
- Integration with GovernancePipeline

#### 8. **Documentation**

New file: `docs/DISTRIBUTED_VELOCITY_BREAKER.md` (15+ sections)

Topics:
- Architecture & data flow
- API reference
- Usage patterns
- Deployment guide (Docker, Kubernetes, Sentinel, Cluster)
- Troubleshooting (6 common issues + solutions)
- Performance benchmarks
- Security considerations
- Migration guide

#### 9. **Examples**

New file: `examples/distributed_velocity_breaker.py` (6 examples)

1. Basic multi-instance setup
2. Ecosystem-level limits
3. Redis failover behavior
4. Pipeline integration
5. Monitoring & metrics
6. Production deployment configs

### API Additions

#### New Classes

```python
# Low-level Lua interface
RedisVelocityBreakerBackend(redis_client, namespace="glassbox:velocity")
  ├── check_and_add(agent_id, now, window_sec, max_count) → (bool, int)
  ├── get_count(agent_id, now, window_sec) → int
  ├── check_ecosystem_and_add(now, window_sec, max_count) → (bool, int)
  └── reset_agent(agent_id) → None

# High-level distributed API
DistributedVelocityBreaker(redis_client, max_decisions=20, ...)
  ├── check(agent_id) → (bool, Optional[str], int)         # Main API
  ├── reset_agent(agent_id) → None
  ├── reset() / reset_ecosystem() / reset_all() → None    # Compatibility
  ├── status(agent_id) → dict                              # Monitoring
  └── ecosystem_status() → dict                             # Fleet status

# Factory
create_velocity_breaker_distributed(redis_client, ecosystem_config=...) → DistributedVelocityBreaker
```

#### Compatibility Methods (VelocityBreaker API)

All single-instance methods now supported on DistributedVelocityBreaker:

```python
breaker.check(agent_id)           # ✓ Core API
breaker.reset(agent_id)           # ✓ (alias for reset_agent)
breaker.reset_ecosystem()         # ✓ Reset fleet state
breaker.reset_all()               # ✓ Reset everything
breaker.status(agent_id)          # ✓ Agent diagnostics
breaker.ecosystem_status()        # ✓ Fleet diagnostics
```

#### 2. **Thread Pool Configuration & Queue Monitoring** (v1.0.2 → v1.0.1)

**Problem:** Async operations in governance pipeline needed coordinated worker management and queue depth monitoring.

**Solution:** 
- `ThreadPoolConfig` — Configurable async workers (default: 50 * cpu_count())
- `QueueDepthMonitor` — Track and alert on queue depth (threshold: 1000)
- `AsyncWorkQueue` — Integration of both for async tasks

**Key Features:**
```python
from glassbox.governance import ThreadPoolConfig, QueueDepthMonitor, create_async_queue

# Configuration: 50 workers per CPU core
config = ThreadPoolConfig()  # auto-configures

# Queue monitoring with 1000-item alert threshold
monitor = QueueDepthMonitor(max_depth_alert=1000)

# Create async work queue
queue = create_async_queue("pipeline_tasks")

# Monitor stats
stats = monitor.get_all_stats()
health = monitor.health_check()
```

**Pipeline Integration:**
```python
from glassbox.governance import EnhancedGovernancePipeline

# v1.0.1: Enhanced pipeline with thread pool
pipeline = EnhancedGovernancePipeline(
    async_workers=200,           # or defaults to 50 * cpu_count()
    max_queue_depth=1000,        # alert threshold
)

# Process synchronously
result = pipeline.process(request)

# Or asynchronously
future = pipeline.process_async(request)
result = pipeline.get_result(future)

# Monitor queue
stats = pipeline.get_queue_stats()  # Get depth statistics
health = pipeline.get_queue_health()  # Check if healthy
```

**Monitoring Events:**
- Queue depth exceeds 1000 → WARNING log
- Queue depth > 2000 → CRITICAL (health check fails)
- Per-queue statistics tracked: max_depth, items_processed, alerts_count

**Configuration via Environment:**
```bash
export ASYNC_WORKERS=200          # Override default (50 * cpu_count)
export MAX_QUEUE_DEPTH=1000      # Alert threshold
export GLASSBOX_TRACE=true       # Enable tracing
```

### Breaking Changes

None. DistributedVelocityBreaker is a drop-in replacement with identical API.

### Dependencies Added

- `redis >= 3.0` (optional; fallback mode works without it)

### Exports Updated

`glassbox.governance.__init__.py` now exports:

```python
from glassbox.governance import (
    # Velocity breakers
    VelocityBreaker,                    # Single-instance
    DistributedVelocityBreaker,         # Multi-instance ← NEW v1.0.1
    RedisVelocityBreakerBackend,        # Low-level Lua ops
    create_velocity_breaker_distributed,  # Factory
    
    # Pipeline
    GovernancePipeline,                 # Core pipeline
    EnhancedGovernancePipeline,         # With thread pool ← NEW v1.0.1
    create_pipeline_v1_1,               # Factory with config ← NEW v1.0.1
    
    # Components
    PolicyEngine,
    AuditLogger,
    RiskEvaluator,
    AnomalyDetector,
    
    # Thread pool & queue monitoring ← NEW v1.0.1
    ThreadPoolConfig,                   # Configurable async workers
    QueueDepthMonitor,                  # Queue depth tracking & alerts
    AsyncWorkQueue,                     # Async work queue
    create_async_queue,                 # Factory
    
    # Models
    DecisionRequest,
    DecisionResponse,
    Disposition,
)
```

### Known Limitations & Future Work

**Known Limitations:**
- Lua scripts unavailable before Redis 3.0 (very rare)
- Circuit breaker recovery: 60s timeout (configurable in future)
- Ecosystem cooldown not persisted across restarts (local-only)

**Future Enhancements:**
- Redis Streams for transaction audit trail
- Distributed tracing (OpenTelemetry)
- Horizontal scaling patterns (sharding by agent_id)
- Metrics export (Prometheus)

### Upgrade Instructions

```bash
# 1. Install redis-py if not already present
pip install redis

# 2. Start Redis (if not already running)
docker run -d -p 6379:6379 redis:7

# 3. Update code (minimal changes)
# See "Migration Path" above

# 4. Run tests
pytest tests/test_velocity_distributed.py -v

# 5. Deploy (gradual rollout recommended)
# Phase 1: 10% traffic
# Phase 2: 25% traffic
# Phase 3: 50% traffic
# Phase 4: 100% traffic
```

### Contributors

- Mohammed Akbar Ansari (Distributed Velocity Breaker design & implementation)

### References

- [Distributed Redis Velocity Breaker Documentation](docs/DISTRIBUTED_VELOCITY_BREAKER.md)
- [Example: Multi-Instance Setup](examples/distributed_velocity_breaker.py)
- [Test Suite: 65+ Tests](tests/test_velocity_distributed.py)

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
