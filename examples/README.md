# Examples

The examples demonstrate the retained `GovernancePipeline` compatibility API.
They are useful for exploring industry policy patterns, but they are not the
recommended integration template for the current `DecisionService` runtime.

## Industry Scenarios

`industry_examples.py` contains 18 runnable v1 scenarios spanning financial
services, healthcare, procurement, public sector, industrial operations, and
other regulated contexts.

```bash
python examples/industry_examples.py --list
python examples/industry_examples.py --id 3
python examples/industry_examples.py
```

The policy identifiers and framework labels in these scenarios are examples,
not certifications or evidence of organizational compliance. Use
[docs/CLAIMS.md](../docs/CLAIMS.md) for verified product claims and
[docs/COMPLIANCE/README.md](../docs/COMPLIANCE/README.md) for the control-mapping
method.

## Distributed Velocity Breaker

`distributed_velocity_breaker.py` demonstrates the legacy Redis-backed breaker
across threads and agent/fleet scopes.

```bash
pip install -e .[redis]
python examples/distributed_velocity_breaker.py
```

It requires Redis on `localhost:6379` and mutates test keys. Use an isolated
development instance. The current v2 distributed limit contract is represented
by `glassbox.ports.limits.LimitStore` and the Redis outbound adapter.

## Current Integration Pattern

For new code, start with the root [quick start](../README.md#quick-start), then
use [application-layer guidance](../glassbox/app/README.md) and the
[v2 HTTP contract](../docs/API/v2_endpoint_reference.md).