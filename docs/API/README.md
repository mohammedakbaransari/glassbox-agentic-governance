# API Reference

This directory contains the GlassBox REST API documentation and endpoint references.

## 📖 Contents

### Endpoint Reference
- **[endpoint_reference.md](endpoint_reference.md)** - Complete REST API reference including:
  - Authentication & headers
  - Available endpoints
  - Request/response formats
  - Status codes
  - Error handling
  - Rate limiting

## 🚀 Quick Start

1. **Review authentication** - Check your API key setup
2. **Explore endpoints** - Browse available operations
3. **Test requests** - Use examples provided
4. **Handle errors** - Understand error responses

## 🔍 Categories

### Decision Management
- POST `/api/v1/decisions` - Create and evaluate decisions
- GET `/api/v1/decisions/{id}` - Retrieve decision details
- GET `/api/v1/decisions` - List decisions with filters

### Policy Management
- GET `/api/v1/policies` - List all policies
- POST `/api/v1/policies` - Register new policy
- PUT `/api/v1/policies/{id}` - Update policy configuration

### Audit & Compliance
- GET `/api/v1/audit` - Retrieve audit records
- GET `/api/v1/audit/{id}` - Get specific audit entry
- POST `/api/v1/audit/export` - Export audit logs

### Monitoring & Health
- GET `/api/v1/health` - System health check
- GET `/api/v1/metrics` - Performance metrics
- GET `/api/v1/status` - Current system status

## 🔐 Authentication

All API requests require authentication. Include:
```
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
```

## 📊 Rate Limiting

- **Free tier**: 100 requests/hour
- **Pro tier**: 10,000 requests/hour
- **Enterprise**: Custom limits

Rate limit headers included in all responses:
```
X-RateLimit-Limit: 100
X-RateLimit-Remaining: 42
X-RateLimit-Reset: 1617904800
```

## 🛠️ Common Tasks

| Task | Endpoint | Method |
|------|----------|--------|
| Create decision | `/api/v1/decisions` | POST |
| Get decision | `/api/v1/decisions/{id}` | GET |
| List policies | `/api/v1/policies` | GET |
| Export audit | `/api/v1/audit/export` | POST |
| Check health | `/api/v1/health` | GET |

## 📝 Examples

### Create a Decision
```bash
curl -X POST https://api.glassbox.io/v1/decisions \
  -H "Authorization: Bearer YOUR_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "agent-001",
    "decision_type": "financial",
    "payload": {"amount": 50000}
  }'
```

### Retrieve Decision
```bash
curl https://api.glassbox.io/v1/decisions/dec-12345 \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## ⚠️ Error Handling

All errors follow standard HTTP status codes:
- `400` - Bad Request (validation error)
- `401` - Unauthorized (missing/invalid auth)
- `403` - Forbidden (insufficient permissions)
- `404` - Not Found (resource doesn't exist)
- `429` - Rate Limited (too many requests)
- `500` - Server Error (internal issue)

## 📚 Related Documentation

- [User Guides](../USER/README.md) - Best practices
- [Development Guide](../DEVELOPMENT/architecture.md) - Extend the API
- [Security](../SECURITY/hardening.md) - Security best practices
- [Compliance](../COMPLIANCE/requirements.md) - Regulatory info

## 🤝 Support

For API issues:
1. Check [../USER/troubleshooting.md](../USER/troubleshooting.md)
2. Review error messages in response
3. Check [endpoint_reference.md](endpoint_reference.md) for correct usage
4. Contact support with request ID (from response headers)


