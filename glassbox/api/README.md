# glassbox/api - Flask REST API

> **Legacy compatibility module:** New integrations use the v2 adapter at
> `glassbox/adapters/inbound/http`. Do not mix route or identity contracts.

`glassbox.api.app` exposes governance operations over HTTP, built on the
original synchronous `GovernancePipeline`. The HTTP entry point onto the
current `DecisionService` is `glassbox/adapters/inbound/http/app.py`; see
[docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#0-current-architecture).

## Application Factory

Install the API extra and import `glassbox.api.app.create_app`. This compatibility
package does not define a `python -m` launcher; the deployment owns the WSGI
server, bind address, TLS, and process lifecycle.

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