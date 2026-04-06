# GlassBox — Deployment Guide

**v1.1.0 | Mohammed Akbar Ansari | Independent Researcher**

> Detailed per-platform reference: [DEPLOYMENT/deployment_reference.md](DEPLOYMENT/deployment_reference.md)

---

## Requirements

| Requirement | Minimum | Recommended |
|---|---|---|
| Python | 3.9 | 3.11+ |
| RAM | 256 MB | 1 GB+ |
| Disk | 100 MB | 1 GB+ (audit logs) |
| OS | Any POSIX / Windows | Linux (production) |
| Dependencies | None (stdlib only) | `flask>=3.0.0` for REST API |

Install:

```bash
pip install glassbox-governance                  # core — no dependencies
pip install "glassbox-governance[api]"           # + Flask REST API
pip install "glassbox-governance[dev]"           # + Flask + PyYAML
```

---

## Quick Start — Embedded (no REST server)

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models   import DecisionRequest, DecisionType

pipeline = GovernancePipeline(environment="production")

req  = DecisionRequest(
    agent_id="my_agent",
    decision_type=DecisionType.PROCUREMENT,
    payload={"amount": 50000, "supplier_id": "SUP-001"},
)
resp = pipeline.process(req)
print(resp.final_status)   # FinalStatus.EXECUTED
```

---

## Quick Start — REST API Server

```bash
pip install flask
python3 -m glassbox.api.app         # → http://localhost:8000
```

Test the server:

```bash
curl -X POST http://localhost:8000/decisions \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"agent1","decision_type":"procurement","payload":{"amount":5000}}'

curl http://localhost:8000/health
curl http://localhost:8000/ready
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `GLASSBOX_ENV` | `production` | Environment label (`production`, `staging`, `dev`) |
| `GLASSBOX_LOG_LEVEL` | `WARNING` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `GLASSBOX_LOG_DIR` | `/var/log/glassbox` | Directory for JSONL audit log rotation |
| `GLASSBOX_DB_PATH` | `glassbox.db` | SQLite database file path |
| `GLASSBOX_API_HOST` | `0.0.0.0` | API listen address |
| `GLASSBOX_API_PORT` | `8000` | API port |
| `GLASSBOX_API_DEBUG` | `false` | Flask debug mode — **never enable in production** |

### Programmatic Configuration

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
    async_audit_writes = True,   # non-blocking audit I/O
)
```

---

## Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install flask --no-cache-dir

ENV GLASSBOX_LOG_LEVEL=INFO
ENV GLASSBOX_ENV=production
ENV GLASSBOX_DB_PATH=/data/glassbox.db

VOLUME ["/data", "/var/log/glassbox"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python3 -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

EXPOSE 8000
CMD ["python3", "-m", "glassbox.api.app"]
```

Build and run:

```bash
docker build -t glassbox:1.1.0 .
docker run -d \
  -p 8000:8000 \
  -v glassbox_data:/data \
  -v glassbox_logs:/var/log/glassbox \
  --name glassbox \
  glassbox:1.1.0
```

---

## Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: glassbox-api
  labels:
    app: glassbox-api
    version: "1.1.0"
spec:
  replicas: 3
  selector:
    matchLabels:
      app: glassbox-api
  template:
    metadata:
      labels:
        app: glassbox-api
    spec:
      containers:
      - name: api
        image: glassbox:1.1.0
        ports:
        - containerPort: 8000
        env:
        - name: GLASSBOX_ENV
          value: "production"
        - name: GLASSBOX_LOG_LEVEL
          value: "WARNING"
        - name: GLASSBOX_DB_PATH
          value: "/data/glassbox.db"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 15
        readinessProbe:
          httpGet:
            path: /ready
            port: 8000
          initialDelaySeconds: 5
          periodSeconds: 10
        resources:
          requests:
            cpu: "250m"
            memory: "256Mi"
          limits:
            cpu: "1000m"
            memory: "1Gi"
        volumeMounts:
        - name: data
          mountPath: /data
        - name: logs
          mountPath: /var/log/glassbox
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: glassbox-pvc
      - name: logs
        emptyDir: {}
```

---

## Platform Adapters

GlassBox automatically detects the deployment platform:

```python
from glassbox.adapters.platforms import auto_detect_adapter

adapter  = auto_detect_adapter()   # detects Databricks / K8s / Fabric / VM
pipeline = adapter.create_pipeline()
```

| Adapter | Platform | Notes |
|---|---|---|
| `DatabricksAdapter` | Databricks | Uses `/dbfs/tmp/glassbox/` paths, DBUTILS logging |
| `KubernetesAdapter` | Kubernetes | Pod metadata from `HOSTNAME` env var |
| `FabricAdapter` | Microsoft Fabric | OneLake-compatible paths |
| `DockerAdapter` | Docker / bare metal | Standard filesystem paths |

---

## Security Hardening

| Topic | Recommendation |
|---|---|
| **API authentication** | Add `X-API-Key` validation middleware (see [API/endpoint_reference.md](API/endpoint_reference.md#authentication--security)) |
| **TLS** | Terminate TLS at reverse proxy (nginx/Caddy); never expose port 8000 directly |
| **Rate limiting** | Default: 100 req/min per agent, 500 req/min per IP. Tune via nginx for additional protection |
| **Database file** | Restrict to `chmod 600 glassbox.db`; store outside web root |
| **Audit logs** | Store JSONL files on write-once storage if compliance requires tamper evidence |
| **Environment** | Never set `GLASSBOX_API_DEBUG=true` in production; leaks stack traces |
| **Secrets** | Pass secrets via environment variables or secret manager, not in `payload` |

---

## Production Checklist

Before going live:

- [ ] `GLASSBOX_API_DEBUG=false`
- [ ] TLS termination configured at reverse proxy
- [ ] API key authentication added to application
- [ ] Database file permissions: `chmod 600 glassbox.db`
- [ ] Log directory writable by service user
- [ ] Health check endpoints monitored (`/health`, `/ready`)
- [ ] Rate limits tuned to expected traffic
- [ ] Audit log rotation configured (default: 10 MB JSONL files)
- [ ] Payload size limit reviewed (`_MAX_BODY_BYTES` in `api/app.py`, default 8 KB)
- [ ] Alert on blocked decisions and security violations via event bus

---

## Monitoring

### Health Endpoints

| Endpoint | Purpose | K8s Probe |
|---|---|---|
| `GET /health` | Full health check with stats | Liveness |
| `GET /ready` | Readiness probe | Readiness |

### Key Metrics to Monitor

| Metric | Source | Alert Threshold |
|---|---|---|
| Block rate | `GET /stats` → `block_rate_pct` | > 20% in 5-minute window |
| P99 latency | `GET /stats` → `p99_latency_ms` | > 50 ms |
| Error rate | Application logs `log.error` | > 1% of requests |
| Velocity trips | `GET /agents/{id}/velocity` → `tripped` | Any trip |
| Security violations | Event bus `security.violation` event | Any occurrence |

---

## Further Reading

- **Platform-specific configs**: [DEPLOYMENT/deployment_reference.md](DEPLOYMENT/deployment_reference.md)
- **Performance tuning**: [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md)
- **Architecture**: [ARCHITECTURE.md](ARCHITECTURE.md)
- **API reference**: [API/endpoint_reference.md](API/endpoint_reference.md)
- **Troubleshooting**: [USER/troubleshooting.md](USER/troubleshooting.md)

---

*GlassBox v1.1.0 · Apache 2.0 · Mohammed Akbar Ansari*
