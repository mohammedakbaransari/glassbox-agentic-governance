# DecisionService HTTP API (v2)

This is the authoritative HTTP contract for the current GlassBox runtime. The
Flask adapter is implemented in `glassbox/adapters/inbound/http/app.py` and
delegates governance decisions to `glassbox.app.decision_service.DecisionService`.

## Runtime Contract

`create_app(runtime)` requires a complete, already-validated
`GovernanceRuntime`. The HTTP adapter does not construct infrastructure,
evaluate policy, derive risk, or select a tenant. Those responsibilities remain
behind application ports.

```mermaid
sequenceDiagram
    autonumber
    participant Client
    participant HTTP as Flask adapter
    participant Service as DecisionService
    participant Ports as Governed ports
    Client->>HTTP: Credential + action/tool request
    HTTP->>Service: Untrusted transport values
    Service->>Ports: Verify identity and evaluate controls
    Ports-->>Service: Decision + durable evidence receipt
    Service-->>HTTP: DecisionOutcome
    HTTP-->>Client: JSON + semantic HTTP status
```

## Authentication and Assertions

Live action and tool requests require one of these credential transports:

- `Authorization: Bearer <token>` creates an unverified OIDC credential.
- `X-Client-Cert: <certificate-material>` creates an unverified mTLS credential.

The configured `IdentityVerifier` must verify that material before a principal
exists. Optional `X-Tenant-ID` and `X-Subject-Id` headers are assertions only;
they cannot establish identity. If either contradicts the verified principal,
the request is denied and the spoofing attempt is evidenced.

`POST /v2/replay` accepts a historical principal and action directly. It does
not accept a live credential and is structurally incapable of dispatching an
effect.

## Endpoints

### `GET /healthz`

Returns the validated runtime profile, adapter-set name, and concrete component
types. A successful response means composition completed; it is not a deep
dependency probe.

### `POST /v2/actions/{action_name}`

Evaluates a catalogue-governed action and dispatches only after durable intent
evidence has been written.

```json
{
  "resource": {
    "kind": "purchase_order",
    "id": "po-4471",
    "tenant_id": "acme"
  },
  "parameters": {
    "amount": 750000,
    "category": "semiconductors"
  },
  "idempotency_key": "po-4471-create",
  "causation_id": "workflow-90210"
}
```

`resource`, `resource.kind`, `resource.id`, `resource.tenant_id`, and
`idempotency_key` are required non-empty strings. `parameters` defaults to an
empty object and `causation_id` is optional.

### `POST /v2/tools/{tool_name}`

Validates a tool name and definition digest before resolving its governed
action. The request shape is the action request plus a required digest:

```json
{
  "definition_sha256": "<registered-definition-digest>",
  "resource": {
    "kind": "repository",
    "id": "governance-service",
    "tenant_id": "acme"
  },
  "parameters": {
    "operation": "create_pull_request"
  },
  "idempotency_key": "tool-call-188"
}
```

### `POST /v2/replay`

Re-evaluates historical inputs against the currently wired controls without
calling the dispatcher or overwriting original evidence.

```json
{
  "principal": {
    "agent_ref": "agent.procurement-bot",
    "agent_instance_id": "instance-01",
    "tenant_id": "acme",
    "credential_type": "oidc",
    "credential_id": "credential-42",
    "issued_at": 1760000000,
    "expires_at": 1760003600
  },
  "action": {
    "action": "procurement.create_purchase_order",
    "resource": {
      "kind": "purchase_order",
      "id": "po-4471",
      "tenant_id": "acme"
    },
    "consequence": "irreversible",
    "exposure": {
      "blast_radius": "single",
      "monetary": 750000,
      "records": 1
    },
    "parameters": {
      "amount": 750000
    },
    "idempotency_key": "po-4471-create"
  }
}
```

## Response Semantics

Successful parsing returns a `DecisionOutcome` with four top-level fields:

- `decision_id`: stable identifier for the evaluated decision
- `decision`: effect, reasons, risk, obligations, and stage outcomes
- `receipt`: durable intent-evidence location and signer metadata
- `execution`: dispatch, approval, denial, or replay outcome

HTTP status codes are semantic:

| Status | Meaning |
|---|---|
| `200` | Evaluation completed; action executed, did not require dispatch, or was replayed |
| `202` | Evaluation requires approval; no effect was dispatched |
| `400` | Request shape or domain value is invalid |
| `401` | Credential is absent, invalid, expired, or delegation is invalid |
| `403` | Governance denied the action or tool call |
| `503` | Durable evidence or signing was unavailable; dispatch did not occur |
| `500` | An unmapped application error occurred |

Errors use the structured `GlassBoxError.as_dict()` representation. Clients
should branch on the error type or decision effect, not parse human-readable
messages.

## Production Boundary

The repository provides the Flask application factory, not a complete process
launcher or ingress tier. Production deployments must supply a composed runtime,
a WSGI server, TLS termination, request-size controls, network policy, and
secret management appropriate to the environment. The in-memory adapter set is
development-only and is rejected by the production runtime profile.

## Related Documentation

- [API overview](README.md)
- [Architecture](../ARCHITECTURE.md)
- [Security](../SECURITY/README.md)
- [Verified claims](../CLAIMS.md)