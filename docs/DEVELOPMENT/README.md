# Development Guide

This section is for engineers extending the current `DecisionService` runtime.
The repository also retains the original `GovernancePipeline`; its detailed
reference is explicitly labeled [legacy](architecture.md).

## Start by Goal

| Goal | Read |
|---|---|
| Understand system boundaries and decision flow | [Architecture](../ARCHITECTURE.md) |
| Add actions, ports, adapters, or transports | [Implementation guide](implementation_guide.md) |
| Run focused and full validation | [Testing strategy](testing.md) |
| Integrate over HTTP | [v2 API reference](../API/v2_endpoint_reference.md) |
| Prepare an environment | [Deployment](../DEPLOYMENT/README.md) |
| Maintain an existing v1 integration | [GovernancePipeline reference](architecture.md) |

## Current Source Ownership

| Location | Ownership |
|---|---|
| `glassbox/domain` | Pure governance values and invariants |
| `glassbox/ports` | Technology-neutral external capability contracts |
| `glassbox/app` | Composition, configuration, and decision orchestration |
| `glassbox/adapters/inbound` | Transport translation |
| `glassbox/adapters/outbound` | Infrastructure implementations |

New governance behavior belongs in these layers. Legacy packages are preserved
for compatibility and are forbidden imports from rebuilt layers.

## Non-Negotiable Rules

1. Evidence intent is durable before an effect can be dispatched.
2. Identity and tenant context come from verified credentials, not request claims.
3. Action consequence and exposure come from the governed catalogue.
4. Distributed limits and idempotency use shared atomic state.
5. Replay cannot dispatch.
6. Domain code is deterministic and infrastructure-free.
7. Production configuration fails startup when assurance dependencies are absent.

These rules are executable in architecture, adversarial, conformance, and claim
tests; see [CLAIMS.md](../CLAIMS.md).

## Development Loop

```bash
pip install -e .[dev]
python -m pytest <focused-tests> -q
ruff check glassbox tests
mypy glassbox --ignore-missing-imports --no-error-summary
python -m pytest tests -q
```

Run `lint-imports` and `tests/test_layering.py` for any layer or import change.

## Documentation Contract

- Update the owning module README when its public contract changes.
- Update the API reference when transport behavior changes.
- Update architecture when responsibility or dependency direction changes.
- Update deployment/security docs when an operator-controlled assumption changes.
- Update `CLAIMS.md` when a guarantee or limitation changes.

Documentation must distinguish current, legacy, planned, and
deployment-specific behavior. Illustrative code must be labeled if it is not
executable against the repository.

## Related Documentation

- [Contributing](../../CONTRIBUTING.md)
- [Domain](../../glassbox/domain/README.md)
- [Ports](../../glassbox/ports/README.md)
- [Application](../../glassbox/app/README.md)
- [Adapters](../../glassbox/adapters/README.md)