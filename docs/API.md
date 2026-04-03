# GlassBox — REST API Reference

**v1.0.0 | Start:** `python3 -m glassbox.api.app` → `http://localhost:8000`

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

## HTTP Status Codes

| Code | Meaning |
|---|---|
| 200 | Success |
| 400 | Missing required fields |
| 404 | Decision ID not found |
| 415 | Invalid content type |
| 422 | Invalid decision type or agent ID |
| 500 | Internal server error |

---

*GlassBox v1.0.0 · Apache 2.0 · Mohammed Akbar Ansari*
