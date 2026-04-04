# GlassBox — REST API Reference

**v1.0.0 | Start:** `python3 -m glassbox.api.app` → `http://localhost:8000`

---

## Authentication & Security

### API Authentication

GlassBox API endpoints are currently **unauthenticated** in the default distribution. For production deployment, implement authentication:

```python
# Example: Add API key validation in Flask
from functools import wraps
from flask import request, jsonify

def require_api_key(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        api_key = request.headers.get("X-API-Key")
        if not api_key or api_key != os.environ.get("GLASSBOX_API_KEY"):
            return jsonify({"error": "unauthorized", "reason": "invalid or missing api_key"}), 401
        return f(*args, **kwargs)
    return decorated

@app.route("/decisions", methods=["POST"])
@require_api_key
def submit_decision():
    ...
```

### CORS & Origin Restrictions

GlassBox API includes CORS headers. Configure trusted origins in production:

```python
from flask_cors import CORS

CORS(
    app,
    origins=["https://dashboard.company.com", "https://admin.company.com"],
    methods=["GET", "POST"],
    allow_headers=["Content-Type", "X-API-Key"]
)
```

### Rate Limiting (Recommended)

No built-in rate limiting; use reverse proxy (nginx, Cloudflare):

```nginx
# nginx configuration
limit_req_zone $binary_remote_addr zone=api:10m rate=100r/s;
limit_req_status 429;

server {
    listen 80;
    location /decisions {
        limit_req zone=api burst=200 nodelay;
        proxy_pass http://glassbox_backend;
    }
}
```

### HTTPS Enforcement

Always use HTTPS in production:

```nginx
server {
    listen 443 ssl http2;
    ssl_protocols TLSv1.3 TLSv1.2;
    ssl_ciphers HIGH:!aNULL:!MD5;
    ssl_certificate /etc/ssl/certs/glassbox.crt;
    ssl_certificate_key /etc/ssl/private/glassbox.key;
    proxy_pass http://glassbox_backend;
}

# Redirect HTTP to HTTPS
server {
    listen 80;
    return 301 https://$server_name$request_uri;
}
```

### Security Headers

Configurate Flask app to emit security headers:

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

## Endpoints

### POST /decisions — Submit a decision

```bash
curl -X POST http://localhost:8000/decisions \
  -H "Content-Type: application/json" \
  -d '{"agent_id":"my_agent","decision_type":"procurement",
       "payload":{"amount":750000,"category":"semiconductors"}}'
```

**Request:**
```json
{
  "agent_id":      "procurement_agent",
  "decision_type": "procurement",
  "payload":       {"amount": 50000, "supplier_id": "SUP-001", "category": "hardware"},
  "context": {
    "confidence":    0.95,
    "environment":   "production",
    "agent_chain":   ["forecast_agent"],
    "source_system": "erp"
  }
}
```

**Response:**
```json
{
  "decision_id":       "a1b2c3d4-...",
  "final_status":      "executed",
  "risk_score":        8.5,
  "risk_level":        "low",
  "disposition":       "auto_execute",
  "policy_violations": [],
  "policy_warnings":   ["[PROC-001] Amount $50,000 — contract recommended"],
  "pipeline_latency_ms": 0.18,
  "circuit_breaker_triggered": false
}
```

**Decision types:** `procurement` `pricing` `financial` `inventory` `logistics` `it_ops` `hr` `custom`

---

### GET /decisions — List audit records

```
GET /decisions?agent_id=my_agent&status=blocked&limit=50&offset=0
```

---

### GET /decisions/{id} — Get specific record

```
GET /decisions/a1b2c3d4-e5f6-...
```

---

### POST /decisions/{id}/replay — Replay historical decision

```
POST /decisions/a1b2c3d4-e5f6-.../replay
```

---

### GET /stats — Governance statistics

```json
{
  "total": 1234,
  "by_status": {"executed": 980, "blocked": 200, "pending_review": 54},
  "block_rate_pct": 16.2,
  "avg_latency_ms": 0.14,
  "p99_latency_ms": 0.47
}
```

---

### GET /agents/{id}/velocity — Circuit breaker status

```json
{
  "agent_id": "procurement_agent",
  "decision_count": 45,
  "window_seconds": 60,
  "max_decisions": 100,
  "tripped": false
}
```

---

### GET /policies — List registered policies

```json
{
  "policies": [
    {"policy_id": "PROC-001", "policy_name": "Procurement Spending Limit",
     "enabled": true, "decision_types": ["procurement"]}
  ],
  "total": 12
}
```

---

### GET /health — Health check

```json
{
  "status": "healthy",
  "service": "GlassBox",
  "version": "1.0.0",
  "environment": "production",
  "total_decisions": 5432,
  "policies": 12,
  "event_bus": true,
  "audit_repo": true
}
```

---

## HTTP Status Codes & Error Responses

| Status | Scenario | Response | Example |
|--------|----------|----------|----------|
| **200** | Success | Standard response with decision outcome | `{"decision_id": "...", "final_status": "executed"}` |
| **400** | Invalid payload — missing required fields | `{"error": "invalid_request", "field": "agent_id", "reason": "required"}` | Missing `agent_id` in POST body |
| **400** | Invalid payload — malformed JSON | `{"error": "json_decode_error", "reason": "Expecting value..."}` | Syntax error in JSON |
| **401** | Authentication failed | `{"error": "unauthorized", "reason": "invalid_api_key"}` | Missing or invalid `X-API-Key` header |
| **403** | Permission denied | `{"error": "forbidden", "reason": "agent_id not authorized"}` | Agent not permitted for operation |
| **404** | Decision ID not found | `{"error": "not_found", "resource": "decision", "id": "xyz"}` | Query for non-existent decision ID |
| **409** | Conflict — duplicate submission | `{"error": "conflict", "reason": "decision_id already exists"}` | Idempotency key collision |
| **413** | Payload too large | `{"error": "payload_too_large", "max_bytes": 10485760}` | Payload exceeds 10MB limit |
| **415** | Invalid content type | `{"error": "unsupported_media_type", "expected": "application/json"}` | Missing `Content-Type: application/json` |
| **422** | Unprocessable entity — semantic error | `{"error": "invalid_decision_type", "got": "invalid_type", "valid_types": ["procurement", ...]}` | Unknown decision type |
| **429** | Rate limited | `{"error": "rate_limited", "retry_after_seconds": 60}` | Too many requests in time window |
| **500** | Internal server error | `{"error": "internal_error", "request_id": "req-123abc", "message": "Unexpected error"}` | Pipeline exception, database error |
| **503** | Service unavailable | `{"error": "service_unavailable", "retry_after_seconds": 30}` | Database connection lost, circuit breaker open |

### Error Response Structure

All errors follow this format:

```json
{
  "error": "error_code",
  "reason": "human-readable explanation",
  "request_id": "req-12345abc",
  "timestamp": "2026-04-03T10:30:00Z",
  "details": {
    "field": "optional_extra_context"
  }
}
```

### Idempotency & Retry Strategy

For safe retries, include an idempotency key:

```bash
curl -X POST http://localhost:8000/decisions \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: 550e8400-e29b-41d4-a716-446655440000" \
  -d '{"agent_id":"my_agent","decision_type":"procurement","payload":{...}}'
```

The same `Idempotency-Key` will always return the same response, even if called multiple times.

---

## Troubleshooting API Issues

**Common errors and solutions:**

| Error | Cause | Solution |
|---|---|---|
| `400 Bad Request` | Malformed JSON or missing required field | Check payload structure in endpoint table above |
| `401 Unauthorized` | Missing or invalid API key | Ensure `Authorization: Bearer {token}` header |
| `403 Forbidden` | Agent not authorized for decision type | Contact admin to grant permissions |
| `413 Payload Too Large` | Payload exceeds size limit (10 MB) | Reduce payload size or split into smaller decisions |
| `429 Too Many Requests` | Rate limit exceeded | Back off; retry after `Retry-After` seconds |
| `500 Internal Server Error` | Pipeline or database error | Check server logs; contact support |
| `503 Service Unavailable` | Server overloaded or restarting | Retry after 30–60 seconds |

For detailed troubleshooting, see [TROUBLESHOOTING.md](TROUBLESHOOTING.md#api--core).

---

## See Also

- **[GLOSSARY.md](GLOSSARY.md)** — Definitions of API terms (disposition, decision_type, payload, etc.)
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — Understanding the 9-stage pipeline behind each API call
- **[TROUBLESHOOTING.md](TROUBLESHOOTING.md)** — Common API issues and solutions
- **[DEPLOYMENT.md](DEPLOYMENT.md)** — Deploying the REST API to production
- **[glassbox/api/README.md](../glassbox/api/README.md)** — Module-level API documentation

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari*
