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
from glassbox.store.database      import GlassBoxDatabase
from glassbox.events.event_bus    import EventBus

db       = GlassBoxDatabase("/var/lib/glassbox/glassbox.db")
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
GET  /decisions              List audit records (paginated, requires audit repository)
GET  /decisions/<id>         Get specific audit record
POST /decisions/<id>/replay  Replay historical decision
GET  /stats                  Aggregate governance statistics
GET  /agents/<id>/velocity   Agent circuit breaker status
GET  /policies               List registered policies
GET  /health                 K8s-compatible health check
```

See [API/endpoint_reference.md](../API/endpoint_reference.md) for full reference.

Production note: configure persistent audit storage before relying on `GET /decisions`.
If no `audit_repo` is configured, the API returns `503 Service Unavailable` for list reads.

---

## Production Deployment Checklist

### Pre-Deployment Validation

- [ ] **Code & Tests**: All 551 tests passing — `python3 -m pytest tests/` (or `unittest`)
- [ ] **Dependencies**: All optional dependencies installed (`pip install flask pyyaml`)
- [ ] **Python Version**: Verify correct version — `python3 --version` (must be 3.9–3.12)
- [ ] **Performance Baseline**: Run benchmarks — `python3 -m glassbox.benchmarks.run_benchmarks`
- [ ] **Security Scan**: Run OWASP dependency checker — `pip-audit`

### Configuration

- [ ] **Log Level**: Set `GLASSBOX_LOG_LEVEL=WARNING` (reduce noise, improve performance)
- [ ] **Log Directory**: Point `GLASSBOX_LOG_DIR` to persistent volume (not ephemeral `/tmp`)
- [ ] **Database**: Use `GlassBoxDatabase("/var/lib/glassbox/glassbox.db")` — not `:memory:`
- [ ] **SQLite Journal WAL**: `GlassBoxDatabase` enables SQLite journal WAL mode internally; if you use `DatabaseFactory.create("sqlite", ...)`, keep `enable_wal=True`
- [ ] **Governance WAL**: If you need finalize-path recovery records, configure `glassbox.governance.write_ahead_log.WriteAheadLog` separately from SQLite journal WAL mode
- [ ] **WAL Recovery**: Enable `recover_wal_on_startup=True` if this instance should replay unfinished finalize-time work on startup
- [ ] **Async Writes**: Set `async_audit_writes=True` for throughput >1,000 decisions/sec
- [ ] **Environment**: Set `environment="production"` in pipeline configuration

### Governance Setup

- [ ] **AgentContracts**: Register all production agents — define permitted types, max amounts, delegation limits
- [ ] **Custom Policies**: Register organisation-specific policies before launch
- [ ] **Ecosystem Limits**: Configure `EcosystemBreakerConfig` for fleet-wide rate limiting
- [ ] **Compliance**: Initialize `ComplianceCatalogue` for required frameworks (EU AI Act, NIST, etc.)

### Operational Integration

- [ ] **Workflow Engine**: Enable SLA monitoring — `WorkflowEngine(monitor_sla=True, default_sla_minutes=120)`
- [ ] **Event Bus**: Subscribe handlers for critical events (`decision.blocked`, `security.violation`, `circuit_breaker.tripped`)
- [ ] **Alerting**: Configure webhook/SIEM integration for security violations and anomalies
- [ ] **Logging**: Configure log aggregation (ELK Stack, Datadog, Splunk)
- [ ] **Monitoring**: Prometheus metrics scraping and Grafana dashboards configured

### Database & Backup

- [ ] **Backup Strategy**: Automated daily backups of SQLite database — see [Database Backup Strategy](#database-backup-strategy)
- [ ] **Recovery Testing**: Tested cold-start and restore procedures
- [ ] **Audit Trail**: Verified audit records are immutable and queryable
- [ ] **Retention Policy**: Set audit record retention (recommend: 7 years for compliance)

### Infrastructure

- [ ] **High Availability**: Load balancer in front of API instances (minimum 2 replicas)
- [ ] **Health Probes**: Kubernetes readiness + liveness probes configured (if K8s)
- [ ] **Resource Limits**: CPU/memory requests and limits set per pod/container
- [ ] **HTTPS**: SSL/TLS enabled (TLS 1.3 preferred)
- [ ] **Network**: API only accessible from authorised networks (VPN/private subnet)

### Security

- [ ] **API Authentication**: API key or OAuth2 configured — see [API.md — Authentication](API.md#authentication--security)
- [ ] **CORS**: Origins restricted to trusted domains
- [ ] **Input Validation**: `PayloadSanitizer` enabled (security stage runs first)
- [ ] **Secrets**: No credentials in configuration files — use environment variables or secrets manager
- [ ] **Audit Logging**: Disabled `include_payload=True` for PII-sensitive deployments

---

## Capacity Planning

### Decision Volume Estimation

Starting point: Estimate your decision volume (decisions/second, decisions/day, decisions/month)

| Decision Volume | Recommended Setup | Database | Notes |
|---|---|---|---|
| **0–1,000/day** | Single VM (2 CPU, 4GB RAM) | SQLite (`:memory:` OK) | Development/staging |
| **1K–10K/day** | Single VM (4 CPU, 8GB RAM) | SQLite via `GlassBoxDatabase` | Production pilot |
| **10K–100K/day** | Single instance (8 CPU, 16GB RAM) + persistent storage | SQLite file, daily backups | Small production |
| **100K–1M/day** | Multiple API instances (2–4 pods) + load balancer | PostgreSQL cluster | Medium production |
| **1M–10M/day** | Kubernetes cluster (16+ pods) + database cluster | PostgreSQL, read replicas | Large enterprise |
| **>10M/day** | Databricks/Spark cluster for batch + online serving tier | Delta Lake + PostgreSQL | Hyperscale |

### Resource Requirements

**Single API Instance:**
```
CPU:    1–2 cores (1 core = ~3,000–5,000 decisions/sec)
Memory: 256MB base + 100MB per concurrent connection
Disk:   2GB SQLite database (1M audit records)
         500MB JSONL logs (1 week retention)
```

**GovernancePipeline Memory Footprint:**
```
PolicyEngine:      ~10MB (1,000 policies)
AnomalyDetector:   ~5MB (100 agents, rolling baselines)
AuditLogger:       ~50MB (ring buffer, 50K records)
VelocityBreaker:   ~5MB (per-agent rate windows)
Base pipeline:     ~100MB
                   ───
Total (typical):   ~200MB per pipeline instance
```

### Scaling Strategy

**Phase 1: Single Instance (0–100K decisions/day)**
```
Setup: Single VM or container
Database: SQLite (local filesystem)
API: Flask single instance (or Gunicorn with 4 workers)
Monitoring: Basic health checks + local logs
```

**Phase 2: High Availability (100K–1M decisions/day)**
```
Setup: Docker Swarm or Kubernetes (2–4 replicas)
Database: PostgreSQL cluster (primary + 1–2 read replicas)
API: Load balancer (nginx/HAProxy) + Gunicorn workers
Monitoring: Prometheus + Grafana + centralized logging
Cache: Redis for policy/rule cache (optional, for <10ms latency)
```

**Phase 3: Hyperscale (>1M decisions/day)**
```
Setup: Kubernetes auto-scaling (HPA based on CPU/latency)
Database: PostgreSQL with connection pooling + read replicas
API: Horizontally scaled pods (50–100+)
Batch: Apache Spark or Databricks for high-volume batch governance
Stream: Kafka for event-driven async governance
Cache: Redis cluster for distributed caching
Observability: Full OpenTelemetry instrumentation
```

### Performance Tuning Parameters

| Parameter | Default | Range | Impact |
|-----------|---------|-------|--------|
| `anomaly_min_samples` | 10 | 5–100 | Activates anomaly detection earlier/later |
| `velocity_window_seconds` | 60 | 10–300 | Tighter/looser rate limiting |
| `policy_engine.cache_size` | 1000 | 100–10K | Policy evaluation cache hit rate |
| `async_audit_writes` | True | bool | Latency vs safety tradeoff |
| `trace_enabled` | False | bool | Debugging detail vs performance |
| `max_payload_bytes` | 1MB | 100KB–10MB | DoS prevention |
| `circle_breaker.cooldown_seconds` | 60 | 30–600 | Recovery speed after trip |

**Tuning for latency:** Disable `trace_enabled`, increase `cache_size`, enable `async_audit_writes`  
**Tuning for consistency:** Enable `trace_enabled`, enable `sync` auditing, lower `breach_window`

### Testing Capacity

Before production deployment, load test with expected volume:

```python
from glassbox.benchmarks.run_benchmarks import BenchmarkSuite
from concurrent.futures import ThreadPoolExecutor

# Simulate 5,000 decisions/sec for 60 seconds
suite = BenchmarkSuite(pipeline)
with ThreadPoolExecutor(max_workers=50) as executor:
    results = suite.stress_test(
        concurrency=50,
        decisions_per_thread=100,
        total_seconds=60
    )
    print(f"P99 latency: {results.p99_latency_ms}ms")
    print(f"Throughput: {results.throughput_per_sec}")
```

---

## Health Check Monitoring

### Prometheus Metrics Endpoint

Configure Prometheus scraping for `/metrics` endpoint (optional, requires `prometheus_client` library):

```bash
pip install prometheus-client
```

**Prometheus Scrape Configuration:**
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: 'glassbox_api'
    static_configs:
      - targets: ['localhost:8000']
    metrics_path: '/metrics'
    scrape_interval: 10s
```

**Key Metrics to Monitor:**
```
glassbox_decisions_total                 # Counter: total decisions processed
glassbox_decisions_executed              # Counter: executed decisions
glassbox_decisions_blocked               # Counter: blocked decisions
glassbox_decisions_pending_review        # Counter: pending human review
glassbox_decision_latency_ms             # Histogram: decision latency (ms)
glassbox_policy_violations_total         # Counter: policy violations
glassbox_anomalies_total                 # Counter: anomalies detected
glassbox_circuit_breaker_trips_total     # Counter: velocity breaker trips
glassbox_security_violations_total       # Counter: security violations
glassbox_audit_records_total             # Gauge: total audit records in database
```

### Grafana Dashboard Queries

**Example Grafana dashboard panels:**

```
# Decision throughput (decisions/sec)
rate(glassbox_decisions_total[1m])

# Block rate (%)
100 * (rate(glassbox_decisions_blocked[5m]) / rate(glassbox_decisions_total[5m]))

# API latency P99 (ms)
histogram_quantile(0.99, glassbox_decision_latency_ms)

# API latency P95 (ms)
histogram_quantile(0.95, glassbox_decision_latency_ms)

# Circuit breaker status
glassbox_circuit_breaker_trips_total

# Security violations (rate)
rate(glassbox_security_violations_total[5m])
```

### Application Health Endpoints

**Readiness Probe** (`/health/ready`): Can the service process decisions?
```json
{
  "status": "ready",
  "database": "connected",
  "policies": 24,
  "audit_repo": "initialized"
}
```

**Liveness Probe** (`/health/live`): Is the process still alive?
```json
{
  "status": "alive",
  "uptime_seconds": 3600,
  "last_decision_timestamp": "2026-04-03T10:30:15Z"
}
```

**Kubernetes Probe Configuration:**
```yaml
livenessProbe:
  httpGet:
    path: /health/live
    port: 8000
    scheme: HTTPS
  initialDelaySeconds: 10
  periodSeconds: 15
  timeoutSeconds: 5
  failureThreshold: 3

readinessProbe:
  httpGet:
    path: /health/ready
    port: 8000
    scheme: HTTPS
  initialDelaySeconds: 5
  periodSeconds: 10
  timeoutSeconds: 3
  failureThreshold: 2
```

---

## Database Backup Strategy

### SQLite Backup Procedures

**Automated Daily Backup (cron):**
```bash
#!/bin/bash
# /usr/local/bin/backup-glassbox.sh
BACKUP_DIR="/backup/glassbox"
DB_PATH="/var/lib/glassbox/glassbox.db"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR
sqlite3 $DB_PATH ".backup '$BACKUP_DIR/glassbox_$TIMESTAMP.db'"
gzip "$BACKUP_DIR/glassbox_$TIMESTAMP.db"

# Retain last 30 days
find $BACKUP_DIR -name "glassbox_*.db.gz" -mtime +30 -delete
```

**Cron Schedule:**
```bash
# Daily backup at 2 AM UTC
0 2 * * * /usr/local/bin/backup-glassbox.sh
```

**Restore from Backup:**
```bash
# Restore specific backup
sqlite3 /var/lib/glassbox/glassbox.db ".restore '/backup/glassbox/glassbox_20260403_020000.db'"

# Verify integrity
sqlite3 /var/lib/glassbox/glassbox.db "PRAGMA integrity_check;"
```

### PostgreSQL Backup (Enterprise)

For deployments using PostgreSQL backend:

```bash
#!/bin/bash
# PostgreSQL backup with WAL archiving
BACKUP_DIR="/backup/postgres"
DB_NAME="glassbox"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

mkdir -p $BACKUP_DIR

# Full backup
pg_dump -U glassbox -h localhost $DB_NAME | gzip > "$BACKUP_DIR/glassbox_$TIMESTAMP.sql.gz"

# Verify backup
gunzip -t "$BACKUP_DIR/glassbox_$TIMESTAMP.sql.gz"

# Point-in-time recovery support (requires WAL archiving)
# See: https://www.postgresql.org/docs/current/continuous-archiving.html
```

### S3 / Cloud Storage Backup

**Automated backup to AWS S3:**
```python
import boto3
import subprocess
from datetime import datetime

def backup_to_s3(db_path, bucket_name, prefix="glassbox-backups"):
    s3 = boto3.client('s3')
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_key = f"{prefix}/glassbox_{timestamp}.db.gz"
    
    # Create backup
    subprocess.run(f"sqlite3 {db_path} '.backup /tmp/glassbox_backup.db'", shell=True)
    subprocess.run("gzip /tmp/glassbox_backup.db", shell=True)
    
    # Upload to S3
    s3.upload_file("/tmp/glassbox_backup.db.gz", bucket_name, backup_key)
    
    # Cleanup
    subprocess.run("rm /tmp/glassbox_backup.db.gz", shell=True)
    
    print(f"Backup saved to s3://{bucket_name}/{backup_key}")

# Lambda function trigger (CloudWatch Events, 2 AM UTC daily)
def lambda_handler(event, context):
    backup_to_s3(
        db_path="/var/lib/glassbox/glassbox.db",
        bucket_name="company-glassbox-backups"
    )
```

---

## Troubleshooting

### "SQLite database is locked" Error

**Error Message:**
```
Error: database is locked
```

**Causes:**  
- Multiple processes writing simultaneously  
- Long-running transaction blocking others

**Solution:**
```python
# Relational storage path
from glassbox.store.database import GlassBoxDatabase

db = GlassBoxDatabase("/var/lib/glassbox/glassbox.db")

# Note: this enables SQLite journal WAL mode internally.
# If you also want governance finalize-path recovery records,
# configure glassbox.governance.write_ahead_log.WriteAheadLog on the pipeline.

# This allows concurrent reads while writes are buffered
```

### "Policy not being enforced" Error

**Symptom:** Policy registered but decisions not being blocked

**Diagnosis:**
```python
# Check if policy is enabled
engine = pipeline.policy_engine
policies = engine.list_policies()
print(f"Found {len(policies)} policies")

# Test policy directly
from glassbox.governance.models import DecisionRequest, DecisionType, DecisionContext

test_request = DecisionRequest(
    agent_id="test_agent",
    decision_type=DecisionType.PROCUREMENT,
    payload={"amount": 750_000}
)

results = engine.evaluate(test_request)
print(results)  # Should show policy violations
```

**Common Issues:**
- Policy is `disabled=True` — re-enable: `engine.enable("PROC-001")`
- Decision type doesn't match — check `policy.applies_to` list
- Custom policy has syntax error — check exception logs: `GLASSBOX_LOG_LEVEL=DEBUG`

### API Timeout with Large Payloads

**Error Message:**
```
504 Gateway Timeout / Request timeout after 30s
```

**Solution:**
```dockerfile
# Increase Flask timeout and upload size limits
ENV GLASSBOX_FLASK_TIMEOUT=60
ENV GLASSBOX_MAX_PAYLOAD_BYTES=52428800  # 50MB

# In app.py
app.config['MAX_CONTENT_LENGTH'] = 52_428_800  # 50MB
```

### Circuit Breaker Constantly Tripping

**Symptom:** `VELOCITY-001` violations even at low volume

**Diagnosis:**
```python
# Check velocity breaker state
breaker = pipeline.velocity_breaker
stats = breaker.get_stats("my_agent")
print(f"Decisions in window: {stats['decision_count']}")
print(f"Max allowed: {stats['max_decisions']}")
```

**Solution:**
```python
# Increase per-agent limit
breaker.configure_agent_limit(
    agent_id="my_agent",
    max_decisions=1000,
    window_seconds=60
)

# Or reset the breaker
breaker.reset_agent("my_agent")
```

### High Memory Usage

**Symptom:** Memory usage grows over time

**Common Causes:**  
- Audit logger ring buffer not rotating  
- Anomaly detector not evicting old baselines  
- Memory leak in custom policy

**Solution:**
```python
# Reduce audit ring buffer size
pipeline.audit_logger.set_ring_buffer_size(10_000)  # default 50K

# Clear old anomaly baselines (monthly)
pipeline.anomaly_detector.reset_agent("old_agent")

# Monitor memory
import tracemalloc
tracemalloc.start()
# ... run decisions ...
current, peak = tracemalloc.get_traced_memory()
print(f"Current: {current/1e6}MB; Peak: {peak/1e6}MB")
```

---

## Security Hardening

### Network Security

**Restrict API to Private Network:**
```nginx
server {
    listen 8000;
    
    # Only allow from private subnet
    allow 10.0.0.0/8;
    allow 172.16.0.0/12;
    deny all;
}
```

**Use HTTPS/TLS 1.3:**
```nginx
server {
    listen 443 ssl http2;
    ssl_protocols TLSv1.3 TLSv1.2;
    ssl_ciphers ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256;
    ssl_prefer_server_ciphers on;
}
```

**Enable HSTS:**
```nginx
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains; preload" always;
```

### Database Security

**File Permissions (SQLite):**
```bash
# Only glassbox user can read/write
chmod 600 /var/lib/glassbox/glassbox.db
chown glassbox:glassbox /var/lib/glassbox/glassbox.db

# Directory permissions
chmod 700 /var/lib/glassbox
```

**PostgreSQL Encrypted Connection:**
```python
db = PostgreSQLRepository(
    host="postgres.internal",
    port=5432,
    user="glassbox",
    password=os.environ["POSTGRES_PASSWORD"],
    require_ssl=True,  # Force SSL
    ssl_ca_cert="/etc/ssl/certs/ca.crt"
)
```

### Input Validation

**Payload Sanitization Enabled by Default:**
```python
# PayloadSanitizer runs as security pre-check (before all stages)
pipeline = GovernancePipeline(
    payload_sanitizer=PayloadSanitizer()  # enabled by default
)

# Scans for: SQL injection, SSTI, XSS, command injection, path traversal
```

### Secrets Management

**Use Environment Variables, Never Hardcode:**
```python
# ❌ Bad
from glassbox.store.database_abstraction import DatabaseFactory

db = DatabaseFactory.create(
  "postgresql",
  host="postgres.internal",
  database="glassbox",
  user="glassbox",
  password="postgres123",  # EXPOSED!
)

# ✅ Good
db = DatabaseFactory.create(
  "postgresql",
  host="postgres.internal",
  database="glassbox",
  user="glassbox",
  password=os.environ["GLASSBOX_DB_PASSWORD"],
)
```

**Kubernetes Secrets:**
```yaml
apiVersion: v1
kind: Secret
metadata:
  name: glassbox-secrets
type: Opaque
stringData:
  db-password: my-secure-password
  api-key: my-api-key
---
apiVersion: apps/v1
kind: Deployment
spec:
  containers:
  - env:
    - name: GLASSBOX_DB_PASSWORD
      valueFrom:
        secretKeyRef:
          name: glassbox-secrets
          key: db-password
```

### Audit Logging

**Disable Payload Logging for PII:**
```python
logger = AuditLogger(
    include_payload=False  # Don't log sensitive fields
)
```

**Encrypt Audit Logs at Rest:**
```bash
# Use file-level encryption (LUKS on Linux)
sudo cryptsetup luksFormat /dev/sdb
sudo cryptsetup luksOpen /dev/sdb glassbox_encrypted
sudo mkfs.ext4 /dev/mapper/glassbox_encrypted
sudo mount /dev/mapper/glassbox_encrypted /var/log/glassbox

# Logs now written to encrypted volume
```

---

## See Also

- **[TROUBLESHOOTING.md](../USER/troubleshooting.md#deployment)** — Common deployment issues and solutions
- **[GLOSSARY.md](../GLOSSARY.md)** — Definitions of deployment and infrastructure terms
- **[ARCHITECTURE.md](../ARCHITECTURE.md)** — Technical architecture for deployment decisions
- **[COMPLIANCE/requirements.md](../COMPLIANCE/requirements.md)** — Compliance requirements for production
- **[adapters/README.md](../glassbox/adapters/README.md)** — Platform-specific adapter documentation

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari · Independent Researcher ·  *
