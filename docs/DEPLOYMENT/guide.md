# GlassBox — Deployment Step-by-Step Guide

**v1.1.0 | Mohammed Akbar Ansari | Independent Researcher**

This guide walks through deploying GlassBox v1.1.0 to production.

For a quick overview, see [../DEPLOYMENT.md](../DEPLOYMENT.md).
For platform-specific configurations, see [deployment_reference.md](deployment_reference.md).

---

## Pre-Deployment Checklist

### Week 1 — Regression Testing

Run the full test suite before any deployment:

```bash
# Full test suite
pytest tests/ -v --tb=short -q

# Core regression tests
pytest tests/test_regression.py -v

# Security tests
pytest tests/test_security.py -v

# Performance baseline
pytest tests/test_performance.py -v
```

All tests in `tests/` must pass before proceeding.

### Week 1 — Staging Validation

1. Install GlassBox:

   ```bash
   pip install "glassbox-governance[api]"
   # or from source:
   pip install -e ".[api]"
   ```

2. Configure environment:

   ```bash
   export GLASSBOX_ENV=staging
   export GLASSBOX_LOG_LEVEL=INFO
   export GLASSBOX_DB_PATH=/var/lib/glassbox/glassbox.db
   ```

3. Start staging server:

   ```bash
   python3 -m glassbox.api.app
   ```

4. Verify health:

   ```bash
   curl http://localhost:8000/health
   curl http://localhost:8000/ready
   ```

5. Run smoke test:

   ```bash
   curl -X POST http://localhost:8000/decisions \
     -H "Content-Type: application/json" \
     -d '{"agent_id":"smoke_test","decision_type":"procurement","payload":{"amount":1000}}'
   ```

---

## Week 2 — Canary Deployment (10% traffic)

1. Deploy staging build to production-canary instance
2. Route 10% of traffic to canary via load balancer
3. Monitor for 24–48 hours:
   - Block rate: `GET /stats` → `block_rate_pct` should stay < 20%
   - P99 latency: `GET /stats` → `p99_latency_ms` should stay < 10 ms
   - Error rate: check application logs for `ERROR` messages
4. If metrics are healthy, proceed to full rollout

---

## Week 3 — Full Production Rollout

1. Roll canary to 100% of traffic
2. Monitor production metrics for 48 hours
3. Keep previous version available for 1-week rollback window

---

## Deployment Targets

### Bare Metal / VM

```bash
# Install
pip install "glassbox-governance[api]"

# Create service user
useradd -r -s /bin/false glassbox

# Create directories
mkdir -p /var/lib/glassbox /var/log/glassbox
chown glassbox:glassbox /var/lib/glassbox /var/log/glassbox

# systemd service
cat > /etc/systemd/system/glassbox-api.service << 'EOF'
[Unit]
Description=GlassBox Governance API
After=network.target

[Service]
User=glassbox
Environment=GLASSBOX_ENV=production
Environment=GLASSBOX_DB_PATH=/var/lib/glassbox/glassbox.db
Environment=GLASSBOX_LOG_DIR=/var/log/glassbox
Environment=GLASSBOX_LOG_LEVEL=WARNING
ExecStart=/usr/local/bin/python3 -m glassbox.api.app
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable glassbox-api
systemctl start glassbox-api
```

### Docker

```bash
docker build -t glassbox:1.1.0 -f Dockerfile .
docker run -d \
  --name glassbox-api \
  -p 8000:8000 \
  -v glassbox_data:/data \
  -e GLASSBOX_ENV=production \
  -e GLASSBOX_LOG_LEVEL=WARNING \
  glassbox:1.1.0
```

See [deployment_reference.md](deployment_reference.md) for the full Dockerfile.

### Kubernetes

Quick apply:

```bash
kubectl apply -f k8s/glassbox-deployment.yaml
kubectl get pods -l app=glassbox-api
kubectl logs -l app=glassbox-api --tail=50
```

See [../DEPLOYMENT.md](../DEPLOYMENT.md#kubernetes) for the full Kubernetes manifest.

### Databricks

```python
from glassbox.adapters.platforms import DatabricksAdapter

adapter  = DatabricksAdapter(log_dir="/dbfs/tmp/glassbox")
pipeline = adapter.create_pipeline()
```

---

## Post-Deployment Verification

```bash
# Health check
curl https://your-api.internal/health

# Submit test decision
curl -X POST https://your-api.internal/decisions \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"deploy_verify","decision_type":"procurement","payload":{"amount":1000}}'

# Check governance stats
curl https://your-api.internal/stats
```

---

## Rollback Procedure

1. Route traffic back to previous version via load balancer
2. Database schema is backwards-compatible; no migration rollback needed
3. Verify health on previous version: `curl /health`
4. Investigate root cause before re-deploying

---

## See Also

- [../DEPLOYMENT.md](../DEPLOYMENT.md) — Deployment overview
- [deployment_reference.md](deployment_reference.md) — Platform-specific configurations
- [performance_tuning.md](performance_tuning.md) — Optimisation and benchmarking
- [../USER/troubleshooting.md](../USER/troubleshooting.md) — Troubleshooting

---

*GlassBox v1.1.0 · Apache 2.0 · Mohammed Akbar Ansari*
