# glassbox/governance — Core Governance Engine

The `governance` package contains the 9-stage pipeline and all stage components.

| Module | Role |
|---|---|
| `pipeline.py` | `GovernancePipeline` — the central orchestrator |
| `models.py` | All dataclasses and enums (DecisionRequest, AuditRecord, …) |
| `policy_engine.py` | Thread-safe policy registry + evaluator |
| `risk_evaluator.py` | Weighted composite risk scoring (0–100) |
| `anomaly_detector.py` | Z-score rolling baseline anomaly detection |
| `velocity_breaker.py` | Per-agent + ecosystem circuit breakers |
| `schema_validator.py` | Payload structure validation per decision type |
| `audit_logger.py` | In-memory ring buffer + JSONL file persistence |
| `decision_replay.py` | Sync + async + parallel batch replay |
| `retry_policy.py` | Sync + async retry with configurable backoff |
| `context_capture.py` | Platform-safe metadata enrichment |
| `logging_manager.py` | Structured JSON/text logging, GLASSBOX_LOG_LEVEL |
| `execution_trace.py` | Per-stage timing and outcome trace (opt-in) |
| `multitenancy.py` | TenantRegistry + MultiTenantPipeline context isolation |

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for pipeline diagrams.

---

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models import DecisionRequest, DecisionType

pipeline = GovernancePipeline()
response = pipeline.process(DecisionRequest(
    agent_id="my_agent",
    decision_type=DecisionType.PROCUREMENT,
    payload={"amount": 50_000, "supplier_id": "SUP-001"}
))

print(f"Status: {response.final_status}")  # FinalStatus.EXECUTED
print(f"Latency: {response.pipeline_latency_ms}ms")
```

---

## Configuration Examples

### Strict Mode (Safety-First)

```python
pipeline = GovernancePipeline(
    trace_enabled=True,            # detailed per-stage info
    async_audit_writes=False,      # synchronous auditing
    environment="production"
)
```

### Permissive Mode (High-Throughput)

```python
pipeline = GovernancePipeline(
    trace_enabled=False,           # skip tracing for speed
    async_audit_writes=True,       # non-blocking audit
    anomaly_detector=None          # disable anomaly detection
)
```

### Full Production Setup

```python
from glassbox.store.database import GlassBoxDB
from glassbox.events.event_bus import EventBus
from glassbox.compliance.catalogue import ComplianceCatalogue

db = GlassBoxDB("/var/lib/glassbox/glassbox.db")
cat = ComplianceCatalogue()
bus = EventBus()

pipeline = GovernancePipeline(
    audit_repo=db.audit_repo(),
    compliance_catalogue=cat,
    event_bus=bus,
    trace_enabled=True,
    async_audit_writes=True
)
```

---

## Performance Characteristics

| Operation | Latency P50 | Latency P99 | Throughput | Notes |
|-----------|-------------|-------------|-----------|-------|
| Full 9-stage pipeline | 0.10 ms | 0.18 ms | 5,500 decisions/sec | Single thread, no DB |
| With SQLite audit | 0.15 ms | 0.25 ms | 4,000 decisions/sec | Includes DB write |
| Policy evaluation (all 24) | 0.05 ms | 0.12 ms | 8,000 evals/sec | Cached policies |
| Anomaly detection check | 0.02 ms | 0.05 ms | 20K checks/sec | Z-score baseline |
| Velocity breaker check | 0.03 ms | 0.08 ms | 15K checks/sec | Sliding window |
| Risk scoring | 0.04 ms | 0.10 ms | 10K scores/sec | All factor extractors |

**Key tuning levers:**
- `trace_enabled=False` saves ~0.02ms per decision
- `async_audit_writes=True` saves ~0.05ms per decision
- Disable `anomaly_detector` if not using saves ~0.02ms

---

## Common Errors

### Error: "Policy not registered"

**Symptom:**
```python
response = pipeline.process(request)
# But policy not triggered or error: "Policy FOO-001 not found"
```

**Diagnosis:**
```python
# Check registered policies
policies = pipeline.policy_engine.list_policies()
print([p.policy_id for p in policies])
```

**Solution:**
```python
# Register the policy
from glassbox.governance.models import Policy, PolicyEvaluation

def my_policy(payload, context):
    if payload.get("amount", 0) > 100_000:
        return PolicyEvaluation("FOO-001", "My Policy", "fail", "Over limit")
    return PolicyEvaluation("FOO-001", "My Policy", "pass", "OK")

pipeline.policy_engine.register(Policy(
    policy_id="FOO-001",
    policy_name="My Policy",
    applies_to=[DecisionType.PROCUREMENT],
    condition=my_policy
))
```

### Error: "Anomaly detector not detecting anomalies"

**Symptom:**
```python
# Even with outlier values, anomaly not triggered
response = pipeline.process(request)
# response.anomalies = []
```

**Cause:** Anomaly detector requires minimum baseline samples before activation.

**Diagnosis:**
```python
detector = pipeline.anomaly_detector
stats = detector.get_stats("my_agent")
print(f"Samples: {stats['sample_count']} (need {detector.min_samples})")
```

**Solution:**
```python
# Send at least 10 baseline decisions first
for i in range(10):
    pipeline.process(DecisionRequest(...))

# Now anomaly detection is active
response = pipeline.process(anomalous_request)  # Will trigger
```

### Error: "Thread-safety issues in policy engine"

**Symptom:**
```python
# Multiple threads calling register() or evaluate() simultaneously
# Errors: "dictionary changed during iteration"
```

**Solution:**
```python
# Register all policies before launching concurrent threads
pipeline.policy_engine.register(policy1)
pipeline.policy_engine.register(policy2)

# Then in thread pool:
with ThreadPoolExecutor(max_workers=10) as ex:
    futures = [ex.submit(pipeline.process, req) for req in requests]
    # Safe! All registrations done, only reads (evaluate) are concurrent
```

### Error: "Audit records growing unbounded"

**Symptom:**
```python
# Memory usage increases over time with no limit
```

**Solution:**
```python
# Reduce ring buffer size
pipeline.audit_logger.set_ring_buffer_size(10_000)  # was 50_000

# Or enable async writes so audit doesn't block:
pipeline.audit_logger.async_writes = True

# Or periodically prune old records:
pipeline.audit_repo.delete_older_than(days=30)
```

---

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md) for pipeline diagrams and [../../docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md) for production deployment patterns.
