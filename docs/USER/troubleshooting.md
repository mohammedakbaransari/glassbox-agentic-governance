# GlassBox Troubleshooting Guide

Common issues, solutions, and debugging strategies across all GlassBox modules.

**Quick Navigation:**
- [Pipeline & Core](#pipeline--core) | [Governance](#governance) | [Compliance](#compliance) | [Storage & Workflow](#storage--workflow) | [Event Bus](#event-bus) | [Rules Engine](#rules-engine) | [Orchestration](#orchestration) | [RAG & Adapters](#rag--adapters) | [Security](#security) | [Performance](#performance) | [Deployment](#deployment)

---

## Pipeline & Core

### Issue: Decision always routes to PASS despite policy violations

**Symptoms:**
```
Payload matches policy rule with condition amount > 100000
Expected: disposition = BLOCK or REVIEW
Actual: disposition = PASS
```

**Root Causes:**
1. Policy not registered in policy engine
2. `fail_fast=False` enabled (logging violations but allowing all decisions)
3. Policy evaluation disabled in pipeline configuration

**Solutions:**

```python
# 1. Verify policy is registered
from glassbox.governance.policy_engine import PolicyEngine
engine = PolicyEngine()
policies = engine.list_policies()
print(f"Registered policies: {[p.policy_id for p in policies]}")

# 2. Enable fail-fast
pipeline = GovernancePipeline(
    fail_fast=True,  # Block on first policy breach
    environment="production"
)

# 3. Check pipeline initialization
pipeline = GovernancePipeline()
if not pipeline.policy_engine:
    raise RuntimeError("Policy engine not initialized")
```

**See also:** [ARCHITECTURE.md](../ARCHITECTURE.md#the-9-stage-pipeline), [governance/README.md](../../glassbox/governance/README.md)

---

### Issue: Pipeline timeout; decisions take > 1 second

**Symptoms:**
```
Decision execution: 5-10 seconds per request
Expected: < 100 ms P99 latency
Application becoming unresponsive
```

**Root Causes:**
1. Too many policies (100+ rules) evaluated serially
2. Anomaly detector baseline recalculation running during eval
3. Audit logging to slow I/O (network, disk)
4. Schema validation on very large payloads

**Solutions:**

```python
# 1. Profile pipeline stages
from glassbox.governance.execution_trace import ExecutionTrace

trace = ExecutionTrace()
result = pipeline.execute(payload, trace=trace)
trace.print_summary()  # Identifies slow stage

# 2. Disable expensive features for high-throughput
pipeline = GovernancePipeline(
    trace_enabled=False,            # Skip execution trace
    async_audit_writes=True,        # Async logging
    anomaly_detector=None,          # Disable if not needed
    environment="high_throughput"
)

# 3. Batch similar decisions for policy amortization
batch = [payload1, payload2, ..., payload100]
results = [pipeline.execute(p) for p in batch]
# Amortizes policy compilation across batch

# 4. Reduce schema validation scope
schema = {
    "required": ["user_id", "amount"],  # Only check critical fields
    # Skip optional nested validation
}
```

**See also:** [ARCHITECTURE.md](../ARCHITECTURE.md#performance-characteristics), [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

---

### Issue: "Pipeline not initialized" error

**Symptoms:**
```python
pipeline = GovernancePipeline()
result = pipeline.execute({"amount": 1000})
# AttributeError: 'NoneType' object has no attribute 'policy_engine'
```

**Root Causes:**
1. Pipeline initialization failed silently
2. Dependency missing (audit repo, policy engine)
3. Configuration file not loaded

**Solutions:**

```python
# 1. Explicit initialization with error handling
try:
    pipeline = GovernancePipeline(environment="production")
    pipeline.initialize()  # Explicit init
except RuntimeError as e:
    print(f"Pipeline init failed: {e}")
    # Fallback to safe defaults
    pipeline = GovernancePipeline(
        audit_repo=InMemoryRepository(),
        policy_engine=PolicyEngine()
    )

# 2. Verify core components
if pipeline.policy_engine is None:
    raise RuntimeError("Policy engine failed to initialize")
if pipeline.audit_repo is None:
    raise RuntimeError("Audit repository failed to initialize")

# 3. Check configuration
import logging
logging.basicConfig(level=logging.DEBUG)
pipeline = GovernancePipeline(environment="development")
# Logs will show initialization steps
```

---

## Governance

### Issue: Anomaly detection fires on legitimate spikes

**Symptoms:**
```
Normal activity: 100 decisions/min
Monday morning surge: 500 decisions/min
Alert: Anomaly detected, all decisions routed to review
User complaints: Valid decisions are delayed
```

**Root Causes:**
1. Baseline outdated (computed from limited historical data)
2. Threshold too sensitive (e.g., 1σ instead of 3σ)
3. Seasonality not accounted for (Monday spike expected)

**Solutions:**

```python
# 1. Retrain baseline with recent data
from glassbox.governance.anomaly_detector import AnomalyDetector

detector = AnomalyDetector()
detector.retrain_baseline(
    decisions=recent_decisions,  # Last 30 days
    percentiles=[25, 50, 75, 95, 99]
)

# 2. Adjust sensitivity threshold
detector = AnomalyDetector(
    threshold_stddev=3.0,  # 3σ = 99.7% of normal data (less sensitive)
    # default was 2.0 = 95%
)

# 3. Use time-aware baseline (seasonal)
detector.enable_temporal_baseline()
# Maintains separate baseline for:
# - Hour of day (morning vs night)
# - Day of week (Monday vs Saturday)
# - Holiday vs weekday
```

**See also:** [governance/README.md](../../glassbox/governance/README.md#common-errors), [ARCHITECTURE.md](../ARCHITECTURE.md#the-9-stage-pipeline)

---

### Issue: Risk evaluator returns 0 for all decisions

**Symptoms:**
```python
result = pipeline.execute(payload)
print(f"Risk score: {result.risk_score}")  # Always 0.0
Expected: Varied scores (0.0 – 1.0) based on decision riskiness
```

**Root Causes:**
1. Risk evaluator not configured with policy weights
2. No historical decision data for pattern matching
3. Risk evaluator disabled in pipeline

**Solutions:**

```python
# 1. Configure risk weighting
from glassbox.governance.risk_evaluator import RiskEvaluator

evaluator = RiskEvaluator(
    policy_breach_weight=0.5,      # 50% risk from breaches
    anomaly_flag_weight=0.3,       # 30% risk from anomalies
    historical_pattern_weight=0.2  # 20% risk from patterns
)

pipeline = GovernancePipeline(risk_evaluator=evaluator)

# 2. Seed historical data
from glassbox.governance.decision_replay import DecisionReplay
replay = DecisionReplay(audit_repo)
decision_history = replay.get_recent_decisions(limit=10000)
evaluator.fit(decision_history)  # Train on history

# 3. Verify risk evaluator is enabled
if not pipeline.risk_evaluator:
    print("Risk evaluator disabled; re-enable:")
    pipeline.risk_evaluator = RiskEvaluator()
```

---

### Issue: Velocity breaker trips unexpectedly

**Symptoms:**
```
Normal request rate: 500 decisions/sec
Sudden spike to 5000 req/sec (intended, e.g., batch job)
Velocity breaker trips; all new decisions blocked or escalated
Batch job fails
```

**Root Causes:**
1. Threshold set too low relative to actual peak load
2. Cooldown period too long; breaker won't reset
3. Unexpected traffic spike (bot attack or legitimate scaling)

**Solutions:**

```python
# 1. Adjust velocity threshold to safe levels
from glassbox.governance.velocity_breaker import VelocityBreaker

breaker = VelocityBreaker(
    max_decisions_per_second=10000,  # Was 1000; too low
    window_seconds=5,                 # Sampling window
    trip_action="review"              # Escalate, not block
)

# 2. Extend cooldown or use adaptive reset
breaker = VelocityBreaker(
    cooldown_seconds=300,  # 5 min before breaker resets
    adaptive_reset=True    # Reset cooldown based on sustained load
)

# 3. Disable for known batch jobs
@breaker.disable_for_batch()
def run_batch_governance(decisions):
    return [pipeline.execute(d) for d in decisions]

# 4. Monitor and tune
stats = breaker.get_statistics()
print(f"Trips today: {stats['trips']}, Avg rate: {stats['avg_req_per_sec']}")
```

---

## Compliance

### Issue: Control catalogue not loading; compliance violations unchecked

**Symptoms:**
```python
from glassbox.compliance.catalogue import ComplianceCatalogue
catalogue = ComplianceCatalogue()
controls = catalogue.list_active_controls()
# Returns: [] (empty)
Expected: Controls from SOC2, HIPAA, etc.
```

**Root Causes:**
1. Catalogue file not found or malformed
2. Control registry database not initialized
3. Active framework not set

**Solutions:**

```python
# 1. Load controls from file
from glassbox.compliance.catalogue import ComplianceCatalogue

catalogue = ComplianceCatalogue()
catalogue.load_from_file("/etc/glassbox/controls/soc2.yaml")
# OR
catalogue.load_from_directory("/etc/glassbox/controls/")

# 2. Initialize catalogue database
catalogue.initialize_database()

# 3. Enable framework
catalogue.enable_framework("SOC2", version="2024")
catalogue.enable_framework("HIPAA", version="164.308")

controls = catalogue.list_active_controls()
print(f"Active controls: {[c.control_id for c in controls]}")

# 4. Verify controls registered in pipeline
pipeline = GovernancePipeline(compliance_catalogue=catalogue)
result = pipeline.execute(payload)
print(f"Controls verified: {len(result.compliance_checks)}")
```

**See also:** [compliance/README.md](../../glassbox/compliance/README.md), [COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)

---

### Issue: Compliance report shows 0% coverage despite active controls

**Symptoms:**
```python
report = compliance_reporter.generate_report(start_date, end_date)
print(f"Control coverage: {report.coverage}%")  # 0%
Expected: 80%+ coverage showing which decisions verified controls
```

**Root Causes:**
1. Reporter not connected to audit trail
2. Control-to-policy mapping missing
3. Report date range has no decisions

**Solutions:**

```python
# 1. Verify audit trail is being populated
audit_repo = pipeline.audit_repo
records = audit_repo.query(limit=10)
print(f"Audit records: {len(records)}")

# 2. Map controls to policies
from glassbox.compliance.reporter import ComplianceReporter

reporter = ComplianceReporter(audit_repo=audit_repo)
reporter.map_control_to_policy(
    "SOC2-C1.2",  # Control ID
    "policy_vendor_assessment"  # Policy that satisfies control
)

# 3. Run report with correct date range
from datetime import datetime, timedelta
start = datetime.now() - timedelta(days=30)
end = datetime.now()
report = reporter.generate_report(start, end)
print(f"Report: {report.coverage}%")
```

---

## Storage & Workflow

### Issue: "Database is locked" when writing audit records

**Symptoms:**
```
sqlite3.OperationalError: database is locked
Multiple processes trying to write simultaneously
Decisions cannot complete
```

**Root Causes:**
1. SQLite WAL (Write-Ahead Logging) not enabled
2. Multiple processes contending on single database file
3. Long-running transaction blocking others

**Solutions:**

```python
# 1. Enable WAL mode
from glassbox.store.database import GlassBoxDB

db = GlassBoxDB(
    "/var/lib/glassbox/glassbox.db",
    enable_wal=True  # Enable Write-Ahead Logging
)

# 2. Use PostgreSQL for higher concurrency
db = GlassBoxDB(
    "postgresql://user:pass@localhost/glassbox",
    backend="postgres"
)

# 3. Keep transactions short
# BAD:
with db.transaction() as tx:
    time.sleep(10)  # Long-running operation
    # Other processes blocked!

# GOOD:
with db.transaction() as tx:
    tx.execute("INSERT INTO audit_records ...")
    # Commit immediately; fast
```

**See also:** [store/README.md](../glassbox/store/README.md#common-errors)

---

### Issue: Workflow SLA timer expired; decision still not reviewed

**Symptoms:**
```
Workflow created 2 hours ago
SLA limit: 1 hour
Status: pending (should be escalated)
SLA monitor thread crashed or not running
```

**Root Causes:**
1. SLA monitor thread crashed
2. SLA monitoring disabled
3. Escalation target not configured

**Solutions:**

```python
# 1. Restart SLA monitor
from glassbox.workflow.workflow_engine import WorkflowEngine

wfe = WorkflowEngine(default_sla_minutes=60, monitor_sla=True)
wfe.start_sla_monitor()

# 2. Manual escalation for overdue workflows
import time
for workflow in wfe.list_pending():
    age_minutes = (time.time() - workflow.created_at) / 60
    if age_minutes > workflow.sla_minutes:
        wfe.escalate(workflow.id, level=2, reason="SLA timeout")

# 3. Configure escalation target
wfe = WorkflowEngine(
    default_sla_minutes=60,
    escalation_target="senior_analyst@company.com",
    monitor_sla=True
)
```

**See also:** [workflow/README.md](../glassbox/workflow/README.md#common-errors)

---

## Event Bus

### Issue: Event subscribers not being called; handler exceptions lost

**Symptoms:**
```python
bus.subscribe("decision.blocked", my_handler)
bus.publish("decision.blocked", {"decision_id": "DEC-001"})
# my_handler never called
# If handler throws, error is silent; event lost
```

**Root Causes:**
1. Handler not registered (typo in event type)
2. Handler raised exception; event dropped
3. Async handler not awaited

**Solutions:**

```python
# 1. Verify handler registration
from glassbox.events.event_bus import EventBus

bus = EventBus()

def my_handler(event):
    print(f"Received: {event.type}")

bus.subscribe("decision.blocked", my_handler)

# Test: does handler get called?
bus.publish("decision.blocked", {})
# If not called: check event type and handler signature

# 2. Add error handling to subscribers
def safe_handler(event):
    try:
        risky_operation(event)
    except Exception as e:
        log_error(f"Handler failed; continuing: {e}")
        # Event processed even if handler fails

bus.subscribe("decision.blocked", safe_handler)

# 3. Use dead-letter handler
def dead_letter(event, error):
    log_to_dlq(event, error)
    alert_ops(f"Event dropped: {event.type}, Error: {error}")

bus.subscribe("decision.blocked", my_handler, on_failure=dead_letter)
```

**See also:** [events/README.md](../glassbox/events/README.md#common-errors)

---

## Rules Engine

### Issue: Rule won't match; "field not found" or type mismatch

**Symptoms:**
```yaml
conditions:
  - field: amount
    op: gt
    value: 100000
```

Result: Rule never matches any decision
Check: `amount` field exists and is numeric

**Root Causes:**
1. Field name mismatch (payload has `total_amount`, rule checks `amount`)
2. Type mismatch (payload has string "50000", rule expects number 50000)
3. Nested field syntax incorrect

**Solutions:**

```python
# 1. Debug payload structure
from glassbox.rules.rules_engine import RulesLoader

payload = {"total_amount": "50000"}  # Field name wrong

# Print available fields
print(f"Payload keys: {payload.keys()}")

# Check types
for key, val in payload.items():
    print(f"{key}: {type(val).__name__} = {val}")

# 2. Use correct field path
yaml_rule = """
conditions:
  - field: total_amount  # Correct field name
    op: gt
    value: 100000
"""

# 3. Ensure type correctness
yaml_rule = """
conditions:
  - field: amount
    op: gt
    value: 100000  # Number, not string
"""

# 4. For nested fields
payload = {
    "order": {
        "amount": 50000
    }
}

yaml_rule = """
conditions:
  - field: order.amount  # Nested field
    op: gt
    value: 100000
"""
```

**See also:** [rules/README.md](../glassbox/rules/README.md#common-errors)

---

### Issue: Hot reload not updating rules; stale policies used

**Symptoms:**
```
Edit rules/spending_limits.yaml
Expected: New rule active within 5 seconds
Actual: Old rule still enforced; no reload detected
```

**Root Causes:**
1. Hot reload watcher not started
2. File permissions prevent reading changes
3. Poll interval too long

**Solutions:**

```python
# 1. Start hot reload explicitly
from glassbox.rules.hot_reload import enable_hot_reload

enable_hot_reload(
    rules_directory="rules/",
    engine=pipeline.policy_engine,
    poll_interval_seconds=5  # Check every 5 seconds
)

# 2. Verify file is readable
import os
stat = os.stat("rules/spending_limits.yaml")
print(f"File permissions: {oct(stat.st_mode)}")
# Should be readable by process

# 3. Lower poll interval
enable_hot_reload(
    rules_directory="rules/",
    engine=pipeline.policy_engine,
    poll_interval_seconds=1  # Check every 1 second (more frequent)
)

# 4. Manual reload if watching fails
from glassbox.rules.rules_engine import RulesLoader
loader = RulesLoader()
loader.load_and_register("rules/", pipeline.policy_engine, is_directory=True)
```

---

## Orchestration

### Issue: Chain stops when node is blocked; downstream nodes never execute

**Symptoms:**
```python
result = orch.run_chain([node_a, node_b, node_c])
# node_b blocked; node_c never runs
# Expected: Despite node_b block, node_c might run (depends on design)
```

**Root Causes:**
1. Chain pattern inherently stops on block (by design)
2. Need graph pattern instead for independence

**Solutions:**

```python
# 1. Use graph pattern instead of chain
result = orch.run_graph([node_a, node_b, node_c])
# All nodes run in parallel; block doesn't stop others

# 2. Design chain to handle blocks
from glassbox.orchestration.orchestrator import AgentNode

node_b_with_fallback = AgentNode(
    name="b_safe",
    fn=lambda state: {
        **state,
        "b_result": try_b(state) or {"fallback": "default"}
    }
)

result = orch.run_chain([node_a, node_b_with_fallback, node_c])

# 3. Check error details
if result.status == "blocked":
    print(f"Blocked at: {result.blocked_nodes}")
    for node_name in result.blocked_nodes:
        violations = result.node_results[node_name].violations
        print(f"Violations: {violations}")
```

**See also:** [orchestration/README.md](../glassbox/orchestration/README.md#common-errors)

---

## RAG & Adapters

### Issue: Retrieved chunks too stale; governance blocks RAG decision

**Symptoms:**
```
Chunk timestamp: 30 days old
max_chunk_age_seconds: 86400 (1 day)
Chunk rejected during retrieval governance
LLM has no context; decision fails
```

**Root Causes:**
1. Knowledge base not updated recently
2. Freshness requirement too strict

**Solutions:**

```python
# 1. Refresh knowledge base
knowledge_base.refresh_from_source()
chunks = retriever.search(query)  # Refresh before RAG

# 2. Adjust freshness requirement
from glassbox.rag.governance import RAGRetrievalGovernor

retrieval_gov = RAGRetrievalGovernor(
    max_chunk_age_seconds=604800  # 7 days instead of 1 day
)

# 3. Monitor knowledge base staleness
stale_chunks = kb.query_older_than(days=1)
print(f"Stale chunks: {len(stale_chunks)}")
if len(stale_chunks) > threshold:
    alert_ops("Knowledge base requires refresh")
```

**See also:** [rag/README.md](../glassbox/rag/README.md#common-errors)

---

### Issue: Framework adapter not wrapping tools; governance not active

**Symptoms:**
```python
# Created adapter but forgot to wrap
tools = [tool1, tool2]
agent = initialize_agent(tools, llm)
# Tools NOT governed; decisions bypass governance
```

**Root Causes:**
1. Forgot to call `wrap_tools()` or `wrap_node()`
2. Wrong adapter type used

**Solutions:**

```python
# 1. Always wrap before use
from glassbox.integrations.adapters import LangChainAdapter
from glassbox.governance.pipeline import GovernancePipeline

pipeline = GovernancePipeline()
adapter = LangChainAdapter(pipeline, agent_id="my_agent")

# Wrap tools
governed_tools = adapter.wrap_tools(tools)

# Then use
agent = initialize_agent(governed_tools, llm)

# 2. For LangGraph, wrap nodes
from glassbox.integrations.adapters import LangGraphAdapter
adapter = LangGraphAdapter(pipeline)
governed_node = adapter.wrap_node(my_fn, agent_id="agent")

graph.add_node("step", governed_node)

# 3. Verify governance is active
result = agent.run("Transfer $1000")
if result.disposition != "PASS":
    print("Governance working!")
```

---

## Security

### Issue: SQL injection bypasses; malicious payload reaches pipeline

**Symptoms:**
```
Payload: {"user": "admin' OR 1=1--"}
Expected: Blocked by sanitizer before Stage 0
Actual: Reaches policy engine; may cause damage
```

**Root Causes:**
1. Sanitizer not called before pipeline
2. Sanitizer configuration disabled SQL injection detection
3. Homoglyph or Unicode-based bypass

**Solutions:**

```python
# 1. Always validate before pipeline
from glassbox.security.sanitizer import PayloadSanitizer
from glassbox.governance.pipeline import GovernancePipeline

sanitizer = PayloadSanitizer()
pipeline = GovernancePipeline()

# SAFE PATTERN:
def execute_with_governance(payload):
    report = sanitizer.validate(payload)
    if not report.is_safe:
        raise SecurityException(f"Payload rejected: {report.violations}")
    
    return pipeline.execute(payload)

# 2. Enable all detection
sanitizer = PayloadSanitizer(
    detect_sql_injection=True,
    detect_template_injection=True,
    detect_unicode_homoglyphs=True,
    detect_command_injection=True
)

# 3. Add custom rules
sanitizer.add_rule(
    field="user",
    pattern="^[a-zA-Z0-9_]+$",  # Alphanumeric only
    error_message="Invalid username format"
)
```

**See also:** [security/README.md](../glassbox/security/README.md#common-errors)

---

## Performance

### Issue: High P99 latency (99th percentile decisions slow)

**Symptoms:**
```
P50 latency: 50 ms
P99 latency: 2000 ms (40x slower!)
Some decisions fast, others very slow
```

**Root Causes:**
1. Policy engine re-compiling rules on each decision
2. Anomaly detector retraining mid-execution
3. Audit I/O blocking (network latency to audit storage)
4. GC pauses in Python process

**Solutions:**

```python
# 1. Compile policies once at startup
from glassbox.governance.policy_engine import PolicyEngine

engine = PolicyEngine()
engine.load_policies("rules/")
engine.compile()  # Pre-compile; one-time cost

pipeline = GovernancePipeline(policy_engine=engine)

# 2. Disable baseline retraining during execution
from glassbox.governance.anomaly_detector import AnomalyDetector

detector = AnomalyDetector()
detector.retrain_baseline_async()  # Background thread, not blocking

# 3. Use async audit writes
pipeline = GovernancePipeline(
    async_audit_writes=True  # Non-blocking audit logging
)

# 4. Sample for monitoring
import time
start = time.time()
result = pipeline.execute(payload)
latency_ms = (time.time() - start) * 1000

print(f"Latency: {latency_ms:.1f} ms")
# Track percentiles over time
```

**See also:** [DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)

---

## Deployment

### Issue: Multiple containers can't use same SQLite database; corruption

**Symptoms:**
```
Container 1 and 2 both access /var/lib/glassbox/glassbox.db
Intermittent "database is locked"
Possible data corruption
```

**Root Causes:**
1. SQLite not designed for multi-machine network access
2. NFS/network filesystem adds latency + locking issues

**Solutions:**

```python
# 1. Migrate to PostgreSQL for multi-container deployments
db = GlassBoxDB(
    "postgresql://user:pass@postgres-svc:5432/glassbox",
    backend="postgres"
)

# 2. Use single writer, multiple readers pattern
# - Only one container writes (primary)
# - Others read from read replicas

# 3. For development/testing, use in-memory
db = GlassBoxDB(":memory:")

# 4. For Kubernetes, use StatefulSet with PersistentVolume
# POD: glassbox-0 (single replica, persistent storage)
# Others: read-only replicas or cache-with-sync
```

**See also:** [DEPLOYMENT.md](../DEPLOYMENT.md#production-checklist)

---

### Issue: Secrets not loading from environment; hardcoded credentials needed

**Symptoms:**
```python
from glassbox.integrations.opa_adapter import OPAAdapter

adapter = OPAAdapter()
# Error: API key not found in environment
# Falls back to hardcoded credentials (security risk)
```

**Root Causes:**
1. Secrets environment variables not set
2. Secrets not mounted in container
3. Wrong env var name expected

**Solutions:**

```python
# 1. Set secrets before initialization
import os
os.environ["GLASSBOX_API_KEY"] = "sk-..."
os.environ["GLASSBOX_DB_PASSWORD"] = "..."

# 2. Use Kubernetes Secrets
# In deployment.yaml:
# env:
# - name: GLASSBOX_API_KEY
#   valueFrom:
#     secretKeyRef:
#       name: glassbox-secrets
#       key: api-key

# 3. Use cloud secrets manager
from glassbox.integrations.secrets import SecretsManager
secrets = SecretsManager(backend="aws-secrets-manager")
api_key = secrets.get("glassbox/api-key")

# 4. Verify secrets loaded
required_secrets = ["GLASSBOX_API_KEY", "GLASSBOX_DB_PASSWORD"]
for secret in required_secrets:
    if secret not in os.environ:
        raise RuntimeError(f"Missing required secret: {secret}")
```

---

## FAQ & Best Practices

### Q: How do I know if GlassBox is catching genuine issues vs false positives?

**A:** Enable debug logging and review execution traces.

```python
import logging
logging.basicConfig(level=logging.DEBUG)

result = pipeline.execute(payload, collect_trace=True)
trace = result.execution_trace

for stage in trace.stages:
    if stage.blocked:
        print(f"Stage {stage.name}: BLOCKED")
        print(f"  Reason: {stage.blocking_reason}")
        print(f"  Violations: {stage.violations}")
```

### Q: What's the recommended way to test policy changes before production?

**A:** Use the simulator with historical data.

```python
from glassbox.governance.simulator import Simulator

simulator = Simulator(audit_repo)
historical = simulator.get_recent_decisions(limit=1000)

# Test new policy on historical data
new_policy = load_policy("new_spending_limit.yaml")
simulator.add_policy(new_policy)

results = simulator.evaluate(historical)
print(f"Decisions affected: {len(results.changed_dispositions)}")
print(f"False positives: {len(results.newly_blocked)}")
```

### Q: How do I scale governance to handle 100K+ decisions/second?

**A:** Use async adapters, batch processing, and distributed deployments.

```python
# 1. High-throughput configuration
pipeline = GovernancePipeline(
    async_audit_writes=True,
    trace_enabled=False,
    anomaly_detector=None,  # Disable if not needed
    environment="high_throughput"
)

# 2. Use Spark adapters for batch
adapter = GlassBoxSparkAdapter(spark)
df_decisions = spark.read.parquet("/data/requests")
governed = adapter.govern_dataframe(df_decisions, partition_mode=True)

# 3. Deploy to Kubernetes with HPA
# - 10–100 replicas based on load
# - Shared PostgreSQL backend
# - Event stream (Kafka) for scale
```

---

## Debug Checklist

Before escalating an issue:

- [ ] Enable debug logging: `logging.basicConfig(level=logging.DEBUG)`
- [ ] Check configuration: `pipeline.policy_engine.list_policies()`
- [ ] Verify dependencies: `get_python_environment_details()`
- [ ] Test with simple payload: `{"amount": 100}`
- [ ] Review execution trace: `result.execution_trace.print()`
- [ ] Check audit logs: `audit_repo.query(limit=10)`
- [ ] Verify external services (database, Kafka, etc. are reachable)

---

## Getting Help

- **Module-specific issues**: See module [README.md](../glassbox) files
- **Architecture questions**: See [ARCHITECTURE.md](../ARCHITECTURE.md)
- **API documentation**: See [API/endpoint_reference.md](../API/endpoint_reference.md)
- **Definitions**: See [GLOSSARY.md](../GLOSSARY.md)
- **Reporting bugs**: See [CONTRIBUTING.md](../CONTRIBUTING.md#security-vulnerability-reporting)

