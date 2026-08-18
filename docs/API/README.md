# HTTP API Documentation

GlassBox contains two HTTP surfaces. They solve the same governance problem but
use different runtimes and contracts. Choose the surface deliberately; routes
and request models are not interchangeable.

## Current API: DecisionService v2

New integrations use the hexagonal runtime exposed by
`glassbox/adapters/inbound/http/app.py`. A process entry point composes one
`GovernanceRuntime`, passes it to `create_app(runtime)`, and serves these routes:

- `GET /healthz`
- `POST /v2/actions/{action_name}`
- `POST /v2/tools/{tool_name}`
- `POST /v2/replay`

Identity verification, tenant assertion checks, policy evaluation, evidence
persistence, and dispatch ordering are enforced by `DecisionService`, not by
optional transport middleware.

See [v2_endpoint_reference.md](v2_endpoint_reference.md) for the contract and
[../../glassbox/adapters/inbound/http/README.md](../../glassbox/adapters/inbound/http/README.md)
for composition guidance.

## Legacy API: GovernancePipeline v1

The retained synchronous API in `glassbox/api/app.py` provides the original
17-route surface, including `/decisions`, `/metrics`, and `/events/stream`. It
remains tested for compatibility, but new integrations should not assume that
its routes or security model apply to v2.

See [endpoint_reference.md](endpoint_reference.md) and
[../../glassbox/api/README.md](../../glassbox/api/README.md).

## Compatibility Policy

- v2 documentation describes only behavior implemented by the current
	`DecisionService` path.
- v1 documentation is labeled **Legacy** and remains separate.
- A capability is not a production guarantee unless it is also represented in
	[../CLAIMS.md](../CLAIMS.md) or explicitly described as deployment-specific.