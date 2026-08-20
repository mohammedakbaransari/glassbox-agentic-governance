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
| `GET /v2/approvals` | List decisions currently awaiting human review |
| `GET /v2/approvals/<decision_id>` | Read one decision's approval status |
| `POST /v2/approvals/<decision_id>/approve` | Approve (supports dual-control quorum via `min_approvers`) |
| `POST /v2/approvals/<decision_id>/reject` | Reject |
| `POST /v2/approvals/<decision_id>/escalate` | Escalate to a senior reviewer |
| `POST /v2/approvals/<decision_id>/revoke` | Withdraw a still-pending request |

The complete request, response, identity, and status-code contract is in the
[v2 endpoint reference](../../../../docs/API/v2_endpoint_reference.md).

## Admission Control

`admission_control.py`'s `HttpAdmissionController` runs before identity
verification: a cheap, per-process, in-memory sliding-window guard keyed by an
arbitrary client key. It protects one replica's own CPU/IO budget from a
request burst — it is not a substitute for the distributed `LimitStore` that
governs verified-identity actions after the pipeline runs, and it is not a
replacement for platform-level rate limiting at the ingress. A rejected
request returns `429` with `Retry-After` and never reaches `DecisionService`.

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
python -m pytest tests/test_http_app.py tests/test_http_approvals.py tests/test_http_admission_control.py -q
```

There is no other HTTP surface in this repository. An earlier synchronous
`glassbox/api` package existed during development; it has been physically
deleted (GB-040), not merely deprecated.