# API Documentation

This section documents the HTTP interface implemented in `glassbox/api/app.py`.

## Start API Locally

```bash
pip install -e .[api]
python -m glassbox.api.app
```

Default bind is controlled by env vars in the app configuration path.

## Endpoint Surface

Implemented routes include:

- `GET /health`
- `GET /ready`
- `GET /metrics`
- `GET /openapi.json`
- `POST /decisions`
- `POST /decisions/simulate`
- `GET /decisions`
- `GET /decisions/{decision_id}`
- `POST /decisions/{decision_id}/replay`
- `POST /decisions/batch`
- `GET /stats`
- `GET /agents/{agent_id}/velocity`
- `GET /agents/{agent_id}/anomaly`
- `GET /policies`
- `GET /contracts`
- `GET /ecosystem`
- `GET /events/stream`

## Operational Behavior

- Built-in request-size limits are enforced.
- Built-in in-memory rate limiting is applied per-agent and per-IP.
- `openapi.json` exposes machine-readable route schema.
- `/metrics` exposes Prometheus text format without extra metrics dependencies.

## Security Integration Guidance

The default distribution is intentionally light on identity controls. For production:

- add authentication middleware (API key, JWT, gateway auth)
- enforce authorization checks per route/action
- run behind TLS-terminating reverse proxy/load balancer
- keep outer rate limits in your ingress/proxy tier

## Canonical Reference

- [endpoint_reference.md](endpoint_reference.md)
- [../../glassbox/api/README.md](../../glassbox/api/README.md)