# glassbox/api — Flask REST API

The `api` package provides a production-ready REST API for GlassBox governance operations.

| Module | Role |
|---|---|
| `app.py` | Flask application, 12 REST endpoints, security headers, error handling |

---

## Quick Start

```bash
# Install optional dependency
pip install flask

# Start API server → http://localhost:8000
python3 -m glassbox.api.app
```

---

## Endpoints

| Method | Path | Purpose | Example |
|--------|------|---------|---------|
| **POST** | `/decisions` | Submit a decision for governance | `{"agent_id": "my_agent", "decision_type": "procurement", ...}` |
| **GET** | `/decisions` | List audit records (paginated) | `?agent_id=my_agent&status=blocked&limit=50&offset=0` |
| **GET** | `/decisions/{id}` | Get specific audit record | `/decisions/a1b2c3d4-e5f6-...` |
| **POST** | `/decisions/{id}/replay` | Replay historical decision | `POST /decisions/{id}/replay` |
| **POST** | `/decisions/batch` | Submit multiple decisions | `{"decisions": [...]}`  (max 499) |
| **GET** | `/events/stream` | Real-time event stream (SSE) | `GET /events/stream` (continuous) |
| **GET** | `/stats` | Aggregate governance statistics | `/stats` |
| **GET** | `/stats/agents` | Per-agent statistics | `/stats/agents?agent_id=my_agent` |
| **GET** | `/agents/{id}/velocity` | Circuit breaker status | `/agents/my_agent/velocity` |
| **GET** | `/policies` | List registered policies | `/policies` |
| **GET** | `/contracts` | List registered agent contracts | `/contracts` |
| **GET** | `/ecosystem` | Ecosystem circuit breaker status | `/ecosystem` |
| **POST** | `/decisions/simulate` | Dry-run policy simulation | `POST /decisions/simulate` |
| **GET** | `/agents/{id}/anomaly` | Anomaly detection baseline | `/agents/my_agent/anomaly` |
| **GET** | `/health` | Full health check | `/health` |
| **GET** | `/ready` | Kubernetes readiness probe | `/ready` |

For full documentation, see [docs/API/endpoint_reference.md](../../docs/API/endpoint_reference.md).

---

## Configuration

### Environment Variables

```bash
export GLASSBOX_API_HOST="0.0.0.0"           # Listen address
export GLASSBOX_API_PORT="8000"              # Port number
export GLASSBOX_API_DEBUG="false"            # Debug mode (never in production)
export GLASSBOX_API_MAX_PAYLOAD_BYTES="1048576"  # 1MB payload limit
export GLASSBOX_API_TIMEOUT_SECONDS="30"    # Request timeout
```

### Flask Configuration

```python
from glassbox.api.app import create_app

app = create_app(
    pipeline=my_pipeline,
    config={
        "DEBUG": False,
        "JSON_SORT_KEYS": False,
        "PROPAGATE_EXCEPTIONS": False,
        "MAX_CONTENT_LENGTH": 10_485_760,  # 10MB
    }
)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, threaded=True)
```

---

## Security & Authentication

### API Key Authentication (Recommended)

Add API key validation to `app.py`:

```python
from functools import wraps
from flask import request, jsonify
import os

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != os.environ.get("GLASSBOX_API_KEY"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/decisions", methods=["POST"])
@require_api_key
def submit_decision():
    ...
```

### CORS Configuration

```python
from flask_cors import CORS

CORS(
    app,
    origins=["https://dashboard.company.com"],
    methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"]
)
```

### Security Headers

```python
@app.after_request
def set_security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['X-XSS-Protection'] = '1; mode=block'
    response.headers['Strict-Transport-Security'] = 'max-age=31536000; includeSubDomains'
    response.headers['Content-Security-Policy'] = "default-src 'self'"
    return response
```

---

## Custom Error Handlers

Register custom error handlers for specific exceptions:

```python
from glassbox.governance.models import GovernanceBlockedError, SecurityViolationError

@app.errorhandler(GovernanceBlockedError)
def handle_blocked(error):
    return jsonify({
        "error": "governance_blocked",
        "policy_violations": error.violations,
        "reason": str(error)
    }), 403

@app.errorhandler(SecurityViolationError)
def handle_security(error):
    return jsonify({
        "error": "security_violation",
        "reason": str(error)
    }), 400

@app.errorhandler(404)
def handle_not_found(error):
    return jsonify({
        "error": "not_found",
        "message": "Resource not found"
    }), 404

@app.errorhandler(500)
def handle_internal_error(error):
    return jsonify({
        "error": "internal_error",
        "request_id": request.headers.get("X-Request-ID", "unknown"),
        "message": "An unexpected error occurred"
    }), 500
```

---

## Adding New Endpoints

### Example: Custom Compliance Report Endpoint

```python
from flask import Flask, request, jsonify
from glassbox.compliance.catalogue import ComplianceCatalogue

@app.route("/compliance/report", methods=["GET"])
def get_compliance_report():
    """Generate compliance posture report for specified framework."""
    framework = request.args.get("framework", "NIST AI RMF")
    cat = ComplianceCatalogue()
    
    posture = cat.posture_summary()
    framework_status = posture.get(framework, {})
    
    return jsonify({
        "framework": framework,
        "total": framework_status.get("total", 0),
        "implemented": framework_status.get("implemented", 0),
        "coverage_pct": framework_status.get("coverage_pct", 0.0),
    })
```

### Example: Custom Policy Deployment Endpoint

```python
@app.route("/policies/deploy", methods=["POST"])
@require_admin_role  # Add your auth check
def deploy_policy():
    """Deploy a new policy with automatic enable/disable."""
    data = request.get_json()
    policy_id = data.get("policy_id")
    policy_def = data.get("definition")
    enabled = data.get("enabled", True)
    
    try:
        custom_policy = Policy(
            policy_id=policy_id,
            policy_name=policy_def.get("name"),
            condition_fn=eval(policy_def.get("condition_code"))  # CAREFUL: eval is dangerous
        )
        pipeline.policy_engine.register(custom_policy, enabled=enabled)
        
        return jsonify({
            "policy_id": policy_id,
            "status": "deployed",
            "enabled": enabled
        }), 201
    except Exception as e:
        return jsonify({"error": "deployment_failed", "reason": str(e)}), 400
```

---

## Rate Limiting

### Reverse Proxy (Recommended)

Use nginx or cloud provider's rate limiting:

```nginx
limit_req_zone $binary_remote_addr zone=api:10m rate=100r/s;

server {
    location /decisions {
        limit_req zone=api burst=200 nodelay;
        proxy_pass http://glassbox_backend;
    }
}
```

### Application-Level Rate Limiting

```bash
pip install Flask-Limiter
```

```python
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["200 per day", "50 per hour"]
)

@app.route("/decisions", methods=["POST"])
@limiter.limit("100 per minute")
def submit_decision():
    ...
```

---

## Deployment

### Docker

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install flask --no-cache-dir

ENV GLASSBOX_LOG_LEVEL=INFO
ENV GLASSBOX_API_PORT=8000

HEALTHCHECK --interval=10s --timeout=3s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python3", "-m", "glassbox.api.app"]
```

### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: glassbox-api
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
        image: glassbox:1.0.0
        ports:
        - containerPort: 8000
        env:
        - name: GLASSBOX_LOG_LEVEL
          value: "WARNING"
        - name: GLASSBOX_DB_PATH
          value: "/mnt/data/glassbox.db"
        livenessProbe:
          httpGet:
            path: /health
            port: 8000
          initialDelaySeconds: 10
          periodSeconds: 15
        readinessProbe:
          httpGet:
            path: /health
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
          mountPath: /mnt/data
      volumes:
      - name: data
        persistentVolumeClaim:
          claimName: glassbox-pvc
```

---

## Monitoring & Debugging

### Enable Debug Logging

```bash
export GLASSBOX_LOG_LEVEL=DEBUG
python3 -m glassbox.api.app
```

### Prometheus Metrics (Optional)

```bash
pip install prometheus-client
```

```python
from prometheus_client import Counter, Histogram, generate_latest

decision_counter = Counter('glassbox_decisions_total', 'Total decisions')
latency_histogram = Histogram('glassbox_latency_ms', 'Decision latency (ms)')

@app.route("/metrics", methods=["GET"])
def metrics():
    return generate_latest()

@app.route("/decisions", methods=["POST"])
def submit_decision():
    start = time.time()
    # ... process decision ...
    decision_counter.inc()
    latency_histogram.observe((time.time() - start) * 1000)
```

### Request Tracing

```python
import uuid

@app.before_request
def add_request_id():
    request.id = request.headers.get("X-Request-ID", str(uuid.uuid4()))

@app.after_request
def add_response_id(response):
    response.headers["X-Request-ID"] = request.id
    return response
```

---

## Common Issues

### "ModuleNotFoundError: No module named 'flask'"

**Solution:**
```bash
pip install flask
```

### "Address already in use" Error

**Solution:** Change port or kill existing process
```bash
lsof -i :8000
kill -9 <PID>

# Or use different port
GLASSBOX_API_PORT=8001 python3 -m glassbox.api.app
```

### API Timeout on Large Payloads

**Solution:** Increase timeout
```python
app.config['MAX_CONTENT_LENGTH'] = 52_428_800  # 50MB
```

---

See [docs/API/endpoint_reference.md](../../docs/API/endpoint_reference.md) for comprehensive endpoint reference and [docs/DEPLOYMENT.md](../../docs/DEPLOYMENT.md) for production deployment practices.
