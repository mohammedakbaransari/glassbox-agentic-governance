# DecisionService HTTP Adapter

This package exposes the current v2 HTTP surface. It translates Flask requests
into `DecisionService` calls and serializes outcomes; it does not construct a
runtime or implement governance policy.

## Routes

| Method and route | Purpose |
|---|---|
| `GET /healthz` | Describe the composed runtime |
| `POST /v2/actions/<action_name>` | Evaluate and conditionally dispatch a governed action |
| `POST /v2/tools/<tool_name>` | Validate a governed tool definition, then evaluate its action |
| `POST /v2/replay` | Re-evaluate historical inputs without dispatch |

The complete request, response, identity, and status-code contract is in the
[v2 endpoint reference](../../../../docs/API/v2_endpoint_reference.md).

## Composition

`create_app(runtime)` requires a fully composed `GovernanceRuntime`. The caller
is responsible for choosing a production-capable adapter set and a WSGI server.

```python
from glassbox.adapters.inbound.http.app import create_app

# runtime is created once by the process composition root.
app = create_app(runtime)
```

There is deliberately no module-level default runtime. Importing this package
cannot silently select in-memory state, a local key, or a tenant model.

## Trust Boundary

- Bearer tokens and client certificates are forwarded as untrusted credentials.
- The `IdentityVerifier` establishes the principal.
- Tenant and subject headers are assertions and cannot override that principal.
- Resource tenancy and all policy/risk decisions are enforced in the service.
- Replay accepts historical values but has no path to the dispatcher.

Production ingress remains responsible for TLS, request-size enforcement,
network controls, and platform-level denial-of-service protection.

## Verification

```bash
python -m pytest tests/test_http_app.py -q
```

The original `glassbox/api` package is a separate v1 compatibility API. Its
routes and authentication model do not apply here.