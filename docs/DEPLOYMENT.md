# GlassBox — Deployment Guide

**v1.0.0 | Mohammed Akbar Ansari | Independent Researcher**

---

## Deployment Platforms

GlassBox runs on any platform with Python 3.9+. Platform adapters auto-detect the environment and configure log paths, hostnames, and Spark integration appropriately.

```python
from glassbox.adapters.platforms import auto_detect_adapter
adapter  = auto_detect_adapter()   # detects Databricks / K8s / Fabric / VM
pipeline = adapter.create_pipeline()
```

---

## VM / Bare Metal / Docker

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.store.database      import GlassBoxDB
from glassbox.events.event_bus    import EventBus

db       = GlassBoxDB("/var/lib/glassbox/glassbox.db")
bus      = EventBus()
pipeline = GovernancePipeline(
    event_bus          = bus,
    audit_repo         = db.audit_repo(),
    environment        = "production",
    log_dir            = "/var/log/glassbox",
    async_audit_writes = True,
)
```

**Dockerfile:**
```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install flask
ENV GLASSBOX_LOG_LEVEL=INFO
ENV GLASSBOX_LOG_DIR=/var/log/glassbox
CMD ["python3", "-m", "glassbox.api.app"]
```

---

## Kubernetes

```python
from glassbox.adapters.platforms import KubernetesAdapter

adapter  = KubernetesAdapter(log_dir="/var/log/glassbox")
pipeline = adapter.create_pipeline()
```

**K8s health probes (FastAPI/Flask):**
```python
@app.get("/health/ready")
def readiness():
    return pipeline.health()   # {"status": "healthy", "total_decisions": 1234}

@app.get("/health/live")
def liveness():
    return {"status": "ok"}
```

**K8s manifest snippet:**
```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
  initialDelaySeconds: 5

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
  initialDelaySeconds: 10
```

---

## Databricks

```python
# In a Databricks notebook cell
from glassbox.adapters.platforms import DatabricksAdapter
from glassbox.adapters.spark     import GlassBoxSparkAdapter

adapter  = DatabricksAdapter()
pipeline = adapter.create_pipeline()   # logs to /dbfs/tmp/glassbox/logs

# Govern a DataFrame
spark_adapter = GlassBoxSparkAdapter(spark)
result_df     = spark_adapter.govern_dataframe(decisions_df, partition_mode=True)
display(result_df)
```

**Install on cluster (init script):**
```bash
pip install /dbfs/FileStore/glassbox-governance-1.0.0-py3-none-any.whl
```

---

## Microsoft Fabric

```python
# In a Fabric notebook cell
from glassbox.adapters.platforms import FabricAdapter
from glassbox.adapters.spark     import GlassBoxSparkAdapter

adapter      = FabricAdapter()
pipeline     = adapter.create_pipeline()   # logs to Lakehouse Files

spark_adapter = GlassBoxSparkAdapter(spark)

# Govern incoming decisions
governed = spark_adapter.govern_dataframe(df, partition_mode=True)

# Write to Delta table
governed.write.format("delta").mode("append").saveAsTable("governed_decisions")
```

---

## Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `GLASSBOX_LOG_LEVEL` | Log verbosity (`DEBUG`/`INFO`/`WARNING`/`ERROR`/`CRITICAL`) | `INFO` |
| `GLASSBOX_LOG_DIR` | Log directory for JSONL audit files | `./glassbox_logs` |
| `GLASSBOX_DB_PATH` | Path to unified SQLite database | `./glassbox.db` |
| `HOSTNAME` | Override hostname in audit records | auto-detect |
| `POD_NAME` | K8s pod name for audit context | auto-detect |
| `K8S_NODE_NAME` | K8s node name | auto-detect |
| `DB_CLUSTER_ID` | Databricks cluster ID | auto-detect |

---

## REST API

```bash
# Start REST API
python3 -m glassbox.api.app   # → http://localhost:8000

# Endpoints
POST /decisions              Submit decision for governance
GET  /decisions              List audit records (paginated)
GET  /decisions/<id>         Get specific audit record
POST /decisions/<id>/replay  Replay historical decision
GET  /stats                  Aggregate governance statistics
GET  /agents/<id>/velocity   Agent circuit breaker status
GET  /policies               List registered policies
GET  /health                 K8s-compatible health check
```

See [docs/API.md](API.md) for full reference.

---

## Production Checklist

- [ ] Set `GLASSBOX_LOG_LEVEL=WARNING` (reduce noise)
- [ ] Set `GLASSBOX_LOG_DIR` to a persistent volume
- [ ] Use `GlassBoxDB` with a named file path (not `:memory:`)
- [ ] Set `async_audit_writes=True` for high-throughput deployments
- [ ] Configure `AgentContract` for each AI agent in production
- [ ] Register custom policies for your organisation's rules
- [ ] Set `EcosystemBreakerConfig` fleet-level limits
- [ ] Configure `WorkflowEngine(monitor_sla=True)` for human review SLA alerting
- [ ] Subscribe to `EventBus` for alerting integration (Slack, PagerDuty, SIEM)
- [ ] Set `ComplianceCatalogue` for your required frameworks
- [ ] Run `python3 -m glassbox.benchmarks.run_benchmarks` to baseline your environment

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher · Navi Mumbai, India*
