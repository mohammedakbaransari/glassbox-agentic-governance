# glassbox/api - Flask REST API

`glassbox.api.app` exposes governance operations over HTTP.

## Start

```bash
pip install -e .[api]
python -m glassbox.api.app
```

Default URL: `http://localhost:8000`

## Implemented endpoints

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

For request/response details, see [docs/API/endpoint_reference.md](../../docs/API/endpoint_reference.md).

## Configuration notes

Important env vars:

- `GLASSBOX_API_HOST`
- `GLASSBOX_API_PORT`
- `GLASSBOX_API_MAX_PAYLOAD_BYTES`
- `GLASSBOX_LOG_LEVEL`

The app includes built-in rate limiting and request-size enforcement.

## Production guidance

- Run behind a reverse proxy (TLS + outer rate limiting).
- Add authentication/authorization middleware for your environment.
- Persist audit data via configured repository/database.