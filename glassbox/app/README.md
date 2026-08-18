# Application Layer

`glassbox.app` coordinates governance without depending on infrastructure. It
contains the current runtime's composition root, decision service, validated
configuration, evidence sealing, logging, and telemetry abstractions.

## Responsibilities

- build one complete, protocol-conforming `GovernanceRuntime` per process;
- enforce runtime-profile safety before opening infrastructure connections;
- evaluate governed actions and tool calls in a fixed, fail-closed order;
- require durable intent evidence before dispatch;
- record execution outcomes without mutating original intent evidence;
- support non-dispatching replay against current controls.

The application layer imports only `glassbox.domain` and `glassbox.ports`.
Concrete adapters are selected by a process entry point and passed inward as an
`AdapterSet`.

## Key Modules

| Module | Purpose |
|---|---|
| `composition.py` | Defines `AdapterSet`, `GovernanceRuntime`, required components, and `build_runtime` |
| `config.py` | Parses strict configuration and enforces `DEV`/`PROD` safety profiles |
| `decision_service.py` | Orchestrates action, tool, and replay decisions |
| `sealer.py` | Seals evidence segments and coordinates retention eligibility |
| `observability.py` | Structured startup, request, and error logging |
| `telemetry.py` | Dependency-free tracing and metrics protocols with no-op defaults |

## Development Runtime

The in-memory adapter set is intentionally development-only. It has no durable
storage, distributed coordination, or independent key custody.

```python
from glassbox.adapters.outbound.memory import (
    memory_adapter_set,
    wire_memory_adapter_set,
)
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.app.decision_service import DecisionService

config = GlassBoxConfig(profile=RuntimeProfile.DEV)
runtime = wire_memory_adapter_set(build_runtime(config, memory_adapter_set()))
service = DecisionService(runtime)
```

Before evaluating an action, register its `ActionDefinition`, mandate, policy,
and dispatch handler in the selected adapters. See
`tests/test_decision_service.py` for a complete executable fixture.

## Decision Paths

- `decide_and_dispatch_for_request`: external action name plus transactional parameters
- `decide_and_dispatch_for_tool_call`: governed tool name plus registered definition digest
- `decide_and_dispatch`: trusted server-side `ProposedAction`
- `replay`: historical principal and action, with no dispatch capability

`DecisionService` is stateless apart from its immutable runtime reference. All
cross-request state belongs behind ports.

## Extending the Layer

Add business meaning to `domain`, external capability contracts to `ports`, and
vendor implementations to `adapters/outbound`. Add application code only when
the change coordinates multiple domain rules or ports.

Do not import Flask, Redis, database drivers, cloud SDKs, OpenTelemetry, Spark,
or other third-party infrastructure libraries here. The layering tests inspect
the whole package.

## Verification

```bash
python -m pytest tests/test_app.py tests/test_decision_service.py -q
python -m pytest tests/test_layering.py -q
```

## Related Documentation

- [Architecture](../../docs/ARCHITECTURE.md)
- [Domain model](../domain/README.md)
- [Ports](../ports/README.md)
- [Current HTTP API](../../docs/API/v2_endpoint_reference.md)
- [Verified claims](../../docs/CLAIMS.md)