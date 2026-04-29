# Implementation Guide (v1.2.0)

This guide explains how to extend GlassBox safely with production-oriented patterns.

## 1. Extend Policies

Policy extension path:

- implement logic via `PolicyEngine` registration
- keep policy functions deterministic and side-effect free
- return clear violation/warning messages
- avoid expensive I/O inside evaluation paths

Testing expectations:

- pass case
- fail case
- malformed payload case

## 2. Declarative Rules

Use `RulesLoader` for YAML/JSON rule definitions when you want non-code policy updates.

Recommended workflow:

1. author rule in staging
2. validate against replay/simulation samples
3. promote to production with rollout guardrails

## 3. Pipeline Composition

`GovernancePipeline` supports injection of core and optional integrations:

- policy engine, risk evaluator, anomaly detector, velocity breaker
- audit repository
- event bus
- workflow engine
- compliance catalogue
- stage registry

Use `trace_enabled=True` during debugging environments to inspect execution traces.

## 4. Multi-Tenant Setup

Use `MultiTenantPipeline` when tenant isolation requirements include:

- isolated policy/risk context
- separated breaker/anomaly baselines
- tenant-scoped configuration and contracts

## 5. Async and Throughput Considerations

- `process_async()` uses thread-pool dispatch to avoid blocking event loops
- tune async worker count for your environment
- monitor p99 latency and queue depth under burst traffic

## 6. API-Layer Extensions

When extending API behavior:

- keep schema aligned with `DecisionRequest` and `DecisionResponse`
- preserve deterministic error semantics
- document new routes in both API docs files
- add route tests for auth, validation, and rate-limit behavior

## 7. Test Harness Usage

`run_test_batches.py` supports:

- batch selection (`--batch`)
- tag filtering (`--tag`)
- scheduling (`--schedule`)
- plan preview and JSON export (`--plan*`)
- rerun failed batches (`--rerun-failed-*`)
- CI one-line summaries (`--ci-summary`, `--ci-analysis-summary`)

## 8. Change Safety Checklist

- [ ] tests updated and passing
- [ ] docs updated for behavior/API changes
- [ ] backward compatibility considered
- [ ] performance impact assessed
- [ ] security implications reviewed

## Related

- [architecture.md](architecture.md)
- [../API/endpoint_reference.md](../API/endpoint_reference.md)
- [../../CONTRIBUTING.md](../../CONTRIBUTING.md)