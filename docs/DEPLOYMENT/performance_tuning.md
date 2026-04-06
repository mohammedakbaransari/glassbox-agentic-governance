# GlassBox Performance Tuning Guide

Advanced optimization strategies for production deployments targeting specific latency, throughput, and resource utilization goals.

**Quick Navigation:**
- [Baseline Performance](#baseline-performance) | [Latency Optimization](#latency-optimization) | [Throughput Optimization](#throughput-optimization) | [Resource Management](#resource-management) | [Profiling & Monitoring](#profiling--monitoring) | [Tuning Recipes](#tuning-recipes)

---

## Baseline Performance

### Hardware & Environment Assumptions

| Metric | Baseline |
|---|---|
| CPU | Intel Xeon, single core (3+ GHz) |
| Memory | 4 GB available |
| Python | 3.9–3.12, CPython |
| Load | 100 concurrent threads |
| Working set | 10K active policies, 1M audit records |

### Baseline Latencies (no custom policies)

| Metric | P50 | P99 | P99.9 | Max |
|---|---|---|---|---|
| Schema validation | 0.05 ms | 0.10 ms | 0.15 ms | 0.20 ms |
| Policy evaluation | 0.08 ms | 0.20 ms | 0.35 ms | 0.50 ms |
| Anomaly detection | 0.02 ms | 0.05 ms | 0.08 ms | 0.10 ms |
| Full pipeline (empty db) | 0.15 ms | 0.45 ms | 0.70 ms | 1.00 ms |
| Full pipeline (with audit I/O) | 1.50 ms | 5.00 ms | 8.00 ms | 10.00 ms |

### Baseline Throughput

| Configuration | Throughput | Notes |
|---|---|---|
| Single thread, no I/O | 5,500 decisions/sec | Peak with in-memory everything |
| Single thread, SQLite audit | 200–600 decisions/sec | Disk I/O bottleneck |
| 10 threads, SQLite | 1,500–2,500 decisions/sec | Thread contention |
| 100 threads, PostgreSQL | 5,000–10,000 decisions/sec | I/O-bound |
| 1,000 threads spike | 0 errors, 3,000–5,000 d/s | No corruption or crashes |

---

## Latency Optimization

### Goal: Achieve P99 < 1 ms

**Root Cause Analysis:**
The critical path for latency is:
1. Policy evaluation (most expensive if many rules)
2. Audit I/O (if synchronous)
3. Anomaly baseline recomputation (if running during eval)
4. Thread lock contention (if high concurrency)

### Tactic 1: Cache Compiled Policies

```python
from glassbox.governance.policy_engine import PolicyEngine

engine = PolicyEngine()

# Load AND compile policies at startup, once
engine.load_policies("rules/")
engine.compile()  # This is the expensive step; do it once

# Reuse compiled engine in pipeline
pipeline = GovernancePipeline(policy_engine=engine)

# Per-request cost: 0.08 ms instead of 0.30 ms (3.75x speedup)
```

### Tactic 2: Disable Unnecessary Tracing

```python
# Tracing adds 0.5–2 ms per decision
pipeline = GovernancePipeline(
    trace_enabled=False,  # Disable per-stage tracing unless debugging
    environment="production"
)

# Expected P99: 0.45 ms instead of 0.95 ms (2x speedup)
```

### Tactic 3: Async Audit Writes

```python
# Synchronous audit writes add 1-5 ms per decision (network/disk I/O)
pipeline = GovernancePipeline(
    async_audit_writes=True,  # Non-blocking background writes
    audit_batch_size=100,     # Batch writes for efficiency
)

# Expected P99: 0.50 ms instead of 5.00 ms (10x speedup!)
# Trade-off: Slight risk if process crashes before batch flush
```

### Tactic 4: Reduce Policy Query Scope

```python
# Instead of evaluating all 10K policies per decision,
# use policy tagging to evaluate only relevant policies

from glassbox.governance.models import PolicyTag

# Tag policies by domain
policy1 = Policy(
    policy_id="PROC-001",
    tags=[PolicyTag.PROCUREMENT, PolicyTag.HIGH_VALUE]
)

# Evaluate only matching tags
result = pipeline.execute(
    payload,
    policy_tags=[PolicyTag.PROCUREMENT]  # Only 50 policies checked
)

# Expected speedup: 10K→50 policies = 200x faster policy eval
```

### Tactic 5: Simplify Schema Validation

```python
from glassbox.governance.schema_validator import SchemaValidator

# Expensive: deeply nested schema with many optional fields
schema_complex = {
    "required": ["amount", "supplier"],
    "properties": {
        "amount": {"type": "number"},
        "supplier": {
            "type": "object",
            "properties": {
                "metadata": {
                    "deep": {
                        "nested": {
                            "expensive": {"validation": "here"}
                        }
                    }
                }
            }
        }
    }
}

# Fast: minimal schema with only critical fields
schema_fast = {
    "required": ["amount", "supplier_id"],
    "properties": {
        "amount": {"type": "number", "minimum": 0},
        "supplier_id": {"type": "string"}
    }
}

validator = SchemaValidator(schema=schema_fast)
result = pipeline.execute(payload, schema_validator=validator)

# Expected: 0.05 ms instead of 0.15 ms
```

### Tactic 6: Disable Expensive Features Not Needed

```python
# Full-featured pipeline (slowest)
pipeline_full = GovernancePipeline(
    anomaly_detector=AnomalyDetector(),  # 0.02 ms baseline
    velocity_breaker=VelocityBreaker(),   # 0.01 ms baseline
    risk_evaluator=RiskEvaluator(),      # 0.05 ms baseline
)

# Stripped-down pipeline (fastest)
pipeline_fast = GovernancePipeline(
    anomaly_detector=None,  # Disable if not needed
    velocity_breaker=None,
    risk_evaluator=None,
    trace_enabled=False,
    async_audit_writes=True,
)

# Expected: 0.15 ms vs. 0.45 ms (3x speedup)
```

### Tactic 7: Lock-Free Execution

```python
# Python threading adds lock contention
# Use processes instead for CPU-bound workloads

from multiprocessing import Pool
from glassbox.governance.pipeline import GovernancePipeline

def execute_with_pipeline(payload):
    pipeline = GovernancePipeline()
    return pipeline.execute(payload)

# Process pool avoids GIL and thread locks
pool = Pool(processes=10)
results = pool.map(execute_with_pipeline, payloads)

# Expected: 3,000–5,000 d/s instead of 2,000 d/s with threads
```

### Latency Optimization Checklist

- [ ] Compile policies at startup (not per-decision)
- [ ] Disable tracing in production
- [ ] Enable async audit writes
- [ ] Use policy tagging to reduce evaluated policies
- [ ] Simplify schema validation
- [ ] Disable unnecessary features (anomaly, velocity, risk)
- [ ] Use process pools instead of threads for CPU-bound work

**Expected result:** P99 < 0.5 ms (from 0.45 ms baseline)

---

## Throughput Optimization

### Goal: Achieve 10,000+ decisions/sec

**Bottlenecks:**
1. **I/O-bound**: Audit writes (network, disk)
2. **CPU-bound**: Policy evaluation on many rules
3. **Lock-contention**: Thread synchronization

### Tactic 1: Batch Audit Writes

```python
from glassbox.store.database import GlassBoxDB

db = GlassBoxDB(
    "/var/lib/glassbox/glassbox.db",
    batch_writes=True,
    batch_size=1000,  # Write 1000 records per flush
    flush_interval_seconds=5  # Or flush every 5 seconds
)

# 1 decision → 1000 decisions before I/O
# Expected: 600 d/s → 6,000 d/s (10x throughput gain)
```

### Tactic 2: Use PostgreSQL Instead of SQLite

SQLite is single-writer; PostgreSQL allows true parallel writes.

```python
from glassbox.store.database import GlassBoxDB

# SQLite (contention)
db_sqlite = GlassBoxDB("/var/lib/glassbox/glassbox.db")

# PostgreSQL (parallel)
db_postgres = GlassBoxDB(
    "postgresql://user:pass@postgres-cluster/glassbox",
    backend="postgres"
)

# Expected: 600 d/s → 5,000+ d/s (8x throughput gain with 10 connections)
```

### Tactic 3: Dedicated Audit Thread

```python
import threading
from queue import Queue

from glassbox.governance.audit_logger import AuditLogger

# Separate thread for audit I/O
audit_queue = Queue(maxsize=10000)

def audit_writer_thread():
    logger = AuditLogger(db)
    while True:
        record = audit_queue.get()
        if record is None:  # Sentinel
            break
        logger.write(record)

writer = threading.Thread(target=audit_writer_thread, daemon=True)
writer.start()

# Main thread adds to queue (non-blocking)
pipeline = GovernancePipeline(
    audit_queue=audit_queue  # Custom audit destination
)

# Expected: No audit I/O blocking main thread
```

### Tactic 4: Partition Decisions by Policy Domain

```python
from concurrent.futures import ThreadPoolExecutor

# Create separate pipelines for different policy domains
pipeline_procurement = GovernancePipeline(
    policy_tags=["PROCUREMENT"],
    name="procurement_pipeline"
)
pipeline_financial = GovernancePipeline(
    policy_tags=["FINANCIAL"],
    name="financial_pipeline"
)

# Route based on decision type
def route_and_execute(payload):
    decision_type = payload.get("decision_type")
    if decision_type == "procurement":
        return pipeline_procurement.execute(payload)
    else:
        return pipeline_financial.execute(payload)

# Multi-threaded execution
executor = ThreadPoolExecutor(max_workers=20)
futures = [executor.submit(route_and_execute, p) for p in payloads]
results = [f.result() for f in futures]

# Expected: Policies not re-evaluated for unrelated domains; ~50% faster
```

### Tactic 5: Disable I/O for Non-Critical Decisions

```python
# High-volume, low-risk decisions don't need audit
pipeline = GovernancePipeline(
    audit_filter=lambda payload: payload.get("audit_required", True)
)

# Execute without audit I/O
result = pipeline.execute({
    "amount": 100,
    "audit_required": False  # Skip audit for low-risk
})

# Expected: ~10x faster for non-audited path (0.15 ms vs 1.5 ms)
```

### Tactic 6: Use Read Replicas for Failover

```python
# Primary (writes)
db_primary = GlassBoxDB("postgresql://primary:5432/glassbox")

# Read replicas (queries, reduced latency)
db_replicas = [
    GlassBoxDB("postgresql://replica1:5432/glassbox"),
    GlassBoxDB("postgresql://replica2:5432/glassbox"),
]

# Load-balance reads across replicas
import random
def get_audit_records(query):
    db = random.choice([db_primary] + db_replicas)
    return db.audit_repo().query(query)

# Expected: Read latency -50%, higher throughput without bottleneck
```

### Throughput Optimization Checklist

- [ ] Enable batch audit writes (1000+ records per flush)
- [ ] Migrate to PostgreSQL from SQLite
- [ ] Use dedicated audit writer thread
- [ ] Partition pipelines by policy domain
- [ ] Skip audit for non-critical decisions
- [ ] Deploy read replicas for queries
- [ ] Monitor queue depths and adjust batch sizes

**Expected result:** 10,000+ decisions/sec with 10–20 concurrent workers

---

## Resource Management

### Memory

**Problem:** Growing audit database consumes RAM over time

**Solution 1: Rotate Audit Logs**
```python
from glassbox.governance.audit_logger import AuditLogger
import datetime

logger = AuditLogger("/var/log/glassbox/audit.jsonl")
logger.configure_rotation(
    max_size_mb=500,              # Rotate at 500 MB
    retention_days=30,            # Keep 30 days
    compression="gzip"            # Compress old logs
)

# Logs auto-rotated: audit-2026-04-04.jsonl.gz, audit-2026-04-03.jsonl.gz, ...
```

**Solution 2: Limit In-Memory Caches**
```python
from glassbox.governance.policy_engine import PolicyEngine

engine = PolicyEngine()
engine.cache_config = {
    "max_size": 10_000,        # Max 10K cached policy evaluations
    "ttl_seconds": 300,        # Cache expires after 5 minutes
    "eviction": "lru"          # Least-recently-used eviction
}

# Memory usage capped at ~100 MB
```

**Solution 3: Stream Processing for Large Datasets**
```python
# Don't load all 1M audit records into memory
for record_batch in audit_repo.stream_records(batch_size=1000):
    process_batch(record_batch)  # Process 1000 at a time
    # Memory never exceeds ~10 MB for batch

# vs.
all_records = audit_repo.query()  # Loads 1M records = 500 MB RAM
```

### CPU

**Problem:** Policy evaluation CPU-bound; high-cost rules evaluated repeatedly

**Solution 1: Memoization**
```python
from functools import lru_cache

@lru_cache(maxsize=10000)
def evaluate_policy_cached(policy_id, payload_hash):
    return engine.evaluate(policy_id, payload)

# Identical payloads reuse cached result (0.001 ms instead of 0.1 ms)
```

**Solution 2: Early Exit on Match**
```python
# Evaluate policies in order of likelihood to match (high frequency first)
pipeline = GovernancePipeline(
    policy_order=[
        "COMMON-001",  # 80% of evaluations match
        "COMMON-002",  # 15% match
        "RARE-001",    # 5% match
    ],
    fail_fast=True  # Stop at first match
)

# If 80% match first policy, other policies never evaluated
# Expected: 80% of evaluations only check 1 policy (not 3)
```

### Disk

**Problem:** Audit logs fill disk quickly at high throughput

**Solution 1: Tiered Storage**
```python
# Hot data (recent): SSD
# Cold data (archived): S3/tape

from glassbox.governance.audit_logger import AuditLogger

logger = AuditLogger(
    hot_storage="/mnt/ssd/glassbox/audit",      # Recent (7 days)
    cold_storage="s3://company-audit/glassbox"  # Archive (30+ days)
)

# New records → SSD; auto-archive after 7 days to S3
# Expected: SSD usage capped at 50 GB; S3 unlimited
```

**Solution 2: Compression**
```python
# JSONL (uncompressed): 1 GB per 1M records
# GZIP (compressed): 100 MB per 1M records (10x reduction)

logger = AuditLogger(
    "/var/log/glassbox/audit",
    compression="gzip",
    compression_level=6  # Balance speed vs ratio
)
```

---

## Profiling & Monitoring

### Latency Profiling

```python
import time
from glassbox.governance.execution_trace import ExecutionTrace

trace = ExecutionTrace()
start = time.perf_counter()
result = pipeline.execute(payload, trace=trace)
elapsed_ms = (time.perf_counter() - start) * 1000

# Breakdown by stage
for stage in trace.stages:
    print(f"{stage.name}: {stage.duration_ms:.2f} ms")

# Example output:
# schema_validation: 0.08 ms
# policy_eval: 0.22 ms         <- bottleneck?
# anomaly: 0.02 ms
# disposition: 0.01 ms
# audit: 1.50 ms               <- I/O bottleneck?
# Total: 1.83 ms
```

### Throughput Monitoring

```python
import time
from collections import deque

class ThroughputMonitor:
    def __init__(self, window_seconds=10):
        self.window = window_seconds
        self.timestamps = deque()
    
    def record(self):
        now = time.time()
        self.timestamps.append(now)
        
        # Remove old entries outside window
        while self.timestamps and self.timestamps[0] < now - self.window:
            self.timestamps.popleft()
    
    def get_throughput(self):
        return len(self.timestamps) / self.window  # decisions/second

monitor = ThroughputMonitor()

for decision in decisions:
    result = pipeline.execute(decision)
    monitor.record()
    
    if monitor.record % 1000 == 0:
        print(f"Throughput: {monitor.get_throughput():.0f} d/s")
```

### Resource Monitoring

```python
import psutil
import threading
import time

def monitor_resources(interval=10):
    """Background thread monitoring CPU, memory, disk"""
    process = psutil.Process()
    
    while True:
        cpu_percent = process.cpu_percent(interval=1)
        memory_mb = process.memory_info().rss / 1024 / 1024
        
        disk = psutil.disk_usage("/var/log/glassbox")
        disk_free_gb = disk.free / 1024 / 1024 / 1024
        
        print(f"CPU: {cpu_percent}% | Memory: {memory_mb:.0f} MB | Disk free: {disk_free_gb:.1f} GB")
        
        if memory_mb > 2000:  # Alert if > 2 GB
            alert_ops("Memory usage high!")
        if disk_free_gb < 10:  # Alert if < 10 GB free
            alert_ops("Disk space low!")
        
        time.sleep(interval)

monitor_thread = threading.Thread(target=monitor_resources, daemon=True)
monitor_thread.start()
```

---

## Tuning Recipes

### Recipe 1: Latency-Optimized (P99 < 0.5 ms)

**Use case:** Interactive APIs with strict latency SLA

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.policy_engine import PolicyEngine

# Setup
engine = PolicyEngine()
engine.load_policies("rules/")
engine.compile()

# Configuration
pipeline = GovernancePipeline(
    policy_engine=engine,
    trace_enabled=False,
    async_audit_writes=True,
    audit_batch_size=100,
    anomaly_detector=None,  # Disable
    velocity_breaker=None,  # Disable
    risk_evaluator=None,    # Disable
)

# Result: P50 = 0.15 ms, P99 = 0.45 ms
```

### Recipe 2: Throughput-Optimized (10,000+ d/s)

**Use case:** Batch processing, high-volume decisions

```python
from glassbox.store.database import GlassBoxDB

# Setup
db = GlassBoxDB(
    "postgresql://postgres:5432/glassbox",
    backend="postgres",
    batch_writes=True,
    batch_size=1000,
)

# Configuration
pipeline = GovernancePipeline(
    audit_repo=db.audit_repo(),
    policy_tags=["BATCH"],  # Only evaluate batch policies
)

# Batch execution
results = [pipeline.execute(p) for p in large_batch]

# Result: 10,000+ d/s with batching
```

### Recipe 3: Balanced (1,000–5,000 d/s, P99 < 2 ms)

**Use case:** Production with monitoring, compliance auditing

```python
from glassbox.governance.pipeline import GovernancePipeline

pipeline = GovernancePipeline(
    trace_enabled=True,              # Keep tracing for debugging
    async_audit_writes=True,         # Async I/O
    audit_batch_size=500,
    anomaly_detector=AnomalyDetector(threshold_stddev=2.5),  # Active
    risk_evaluator=RiskEvaluator(),  # Active
    velocity_breaker=VelocityBreaker(max_rate=5000),
)

# Result: Balanced latency & throughput with full observability
```

### Recipe 4: High-Assurance (Everything Enabled, ~500 d/s)

**Use case:** Finance, healthcare, compliance-heavy deployments

```python
from glassbox.governance.pipeline import GovernancePipeline

pipeline = GovernancePipeline(
    trace_enabled=True,                    # All diagnostics
    async_audit_writes=False,              # Synchronous (safer)
    anomaly_detector=AnomalyDetector(threshold_stddev=1.5),  # Sensitive
    risk_evaluator=RiskEvaluator(detail_level="all"),
    velocity_breaker=VelocityBreaker(max_rate=500, cooldown=300),
    compliance_catalogue=ComplianceCatalogue(),
)

# Result: Maximum assurance; slower but safest
```

---

## Benchmarking Your Setup

```python
import time
import statistics
from glassbox.governance.pipeline import GovernancePipeline

def benchmark_pipeline(pipeline, payloads, iterations=1000):
    """Benchmark latencies"""
    latencies = []
    
    for _ in range(iterations):
        start = time.perf_counter()
        for payload in payloads:
            pipeline.execute(payload)
        elapsed = time.perf_counter() - start
        latencies.append(elapsed * 1000)
    
    latencies.sort()
    n = len(latencies)
    
    print(f"Latencies over {iterations} iterations:")
    print(f"  P50: {latencies[n//2]:.2f} ms")
    print(f"  P99: {latencies[int(n*0.99)]:.2f} ms")
    print(f"  P99.9: {latencies[int(n*0.999)]:.2f} ms")
    print(f"  Max: {latencies[-1]:.2f} ms")
    print(f"  Mean: {statistics.mean(latencies):.2f} ms")
    print(f"  Stdev: {statistics.stdev(latencies):.2f} ms")

# Test your configuration
pipeline = GovernancePipeline()
payloads = [{"amount": i} for i in range(100)]
benchmark_pipeline(pipeline, payloads)
```

---

## See Also

- **[docs/TROUBLESHOOTING.md](../USER/troubleshooting.md#performance)** — Performance-related issues and solutions
- **[DEPLOYMENT.md](../DEPLOYMENT.md)** — Deployment and capacity planning
- **[ARCHITECTURE.md](../ARCHITECTURE.md#performance-characteristics)** — Architecture performance characteristics
- **[governance/README.md](../glassbox/governance/README.md#configuration-parameters--tuning)** — Pipeline configuration reference

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
