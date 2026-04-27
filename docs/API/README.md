# GlassBox — API Reference

The `docs/API/` directory contains the REST API documentation.

---

## Contents

- **[endpoint_reference.md](endpoint_reference.md)** — Complete REST API reference:
  authentication, all 15 endpoints, request/response formats, status codes, error handling, rate limiting

---

## Quick Start

```bash
# Install Flask (only dependency for the API)
pip install flask

# Start the server
python3 -m glassbox.api.app
# → http://localhost:8000
```

Test immediately:

```bash
curl -X POST http://localhost:8000/decisions \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"test_agent","decision_type":"procurement","payload":{"amount":5000}}'

curl http://localhost:8000/health
```

---

## All Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/decisions` | Submit a decision for governance |
| `GET` | `/decisions` | List audit records (paginated, requires audit repository) |
| `GET` | `/decisions/{id}` | Get a specific audit record |
| `POST` | `/decisions/{id}/replay` | Replay a historical decision |
| `POST` | `/decisions/simulate` | Dry-run simulation (no audit write) |
| `POST` | `/decisions/batch` | Submit up to 499 decisions in parallel |
| `GET` | `/events/stream` | Real-time SSE event stream |
| `GET` | `/stats` | Aggregate governance statistics |
| `GET` | `/agents/{id}/velocity` | Circuit breaker status for an agent |
| `GET` | `/agents/{id}/anomaly` | Anomaly detection baseline for an agent |
| `GET` | `/policies` | List registered governance policies |
| `GET` | `/contracts` | List registered agent contracts |
| `GET` | `/ecosystem` | Ecosystem circuit breaker status |
| `GET` | `/health` | Full health check |
| `GET` | `/ready` | Kubernetes readiness probe |

---

## Authentication

The default server has **no authentication**. For production, add an API key check:

```python
from functools import wraps
from flask import request, jsonify
import os

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        key = request.headers.get("X-API-Key")
        if not key or key != os.environ.get("GLASSBOX_API_KEY"):
            return jsonify({"error": "unauthorized"}), 401
        return f(*args, **kwargs)
    return decorated
```

See [endpoint_reference.md](endpoint_reference.md#authentication--security) for CORS, HTTPS, and security headers.

---

## Rate Limits (default)

| Scope | Limit |
|---|---|
| Per IP address | 500 requests / minute |
| Per `agent_id` | 100 requests / minute |

Both enforced in `glassbox/api/app.py` using a sharded in-memory sliding-window counter.

`GET /decisions` is available only when the pipeline is configured with persistent
audit storage. Without an `audit_repo`, the API returns `503 Service Unavailable`.

---

## Payload Size Limit

Default: **8 KB** per request.  
Configured via `_MAX_BODY_BYTES` in `glassbox/api/app.py`.

---

## Error Format

All errors return:

```json
{
  "error": "human_readable_code",
  "status": 422,
  "request_id": "a1b2c3d4"
}
```

---

## Related Documentation

- [endpoint_reference.md](endpoint_reference.md) — Full endpoint reference
- [../GLOSSARY.md](../GLOSSARY.md) — API term definitions
- [../ARCHITECTURE.md](../ARCHITECTURE.md) — 9-stage pipeline behind every API call
- [../DEPLOYMENT.md](../DEPLOYMENT.md) — Deploying to production
- [../USER/troubleshooting.md](../USER/troubleshooting.md) — Common issues
- [../../glassbox/api/README.md](../../glassbox/api/README.md) — Module-level implementation notes
