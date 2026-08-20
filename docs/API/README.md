# HTTP API Documentation

GlassBox exposes one HTTP surface: the hexagonal runtime in
`glassbox/adapters/inbound/http/app.py`. A process entry point composes one
`GovernanceRuntime`, passes it to `create_app(runtime)`, and serves these
routes:

- `GET /healthz`
- `POST /v2/actions/{action_name}`
- `POST /v2/tools/{tool_name}`
- `POST /v2/replay`
- `GET /v2/approvals`
- `GET /v2/approvals/{decision_id}`
- `POST /v2/approvals/{decision_id}/approve`
- `POST /v2/approvals/{decision_id}/reject`
- `POST /v2/approvals/{decision_id}/escalate`
- `POST /v2/approvals/{decision_id}/revoke`

Identity verification, tenant assertion checks, policy evaluation, evidence
persistence, and dispatch ordering are enforced by `DecisionService`, not by
optional transport middleware. A cheap, in-process admission-control guard
(`HttpAdmissionController`) runs before identity verification, rejecting a
request-rate burst with `429` before any governance work is spent on it.

See [v2_endpoint_reference.md](v2_endpoint_reference.md) for the full
contract and [../../glassbox/adapters/inbound/http/README.md](../../glassbox/adapters/inbound/http/README.md)
for composition guidance.

## Earlier HTTP Surface

An earlier synchronous `GovernancePipeline` HTTP API (`glassbox/api/`,
17 routes including `/decisions`, `/metrics`, `/events/stream`) existed
during development. It has been physically deleted from this repository
(GB-040), not merely deprecated — there is nothing to route requests to any
more. The historical [TypeScript SDK](../../sdk/typescript/README.md) that
targeted it is kept only as a reference for organizations still operating an
old deployment elsewhere.

## Compatibility Policy

- This document describes only behavior implemented by the current
	`DecisionService` path — there is no other path.
- A capability is not a production guarantee unless it is also represented in
	[../CLAIMS.md](../CLAIMS.md) or explicitly described as deployment-specific.