# GlassBox: Runtime Decision Governance for Autonomous AI Systems

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Python 3.13](https://img.shields.io/badge/python-3.13-blue)](https://www.python.org)

GlassBox evaluates AI-generated operational decisions before they take
effect. It verifies the caller's identity, resolves the action against a
governed catalogue, scores risk, enforces policy and velocity limits,
records tamper-evident evidence, and only then permits the effect to run.

## Documentation integrity

Every capability documented here is backed by a specific piece of code and a
passing test — see [docs/CLAIMS.md](docs/CLAIMS.md) for the exact mapping,
including what is explicitly not yet built. A CI check
(`tests/test_claims_coverage.py`) fails the build if a citation stops
resolving.

## What it provides

- Identity-verified, tenant-scoped decision evaluation — no caller-asserted
  tenant or user identity is ever trusted
- A governed action catalogue with schema-validated parameters, server-derived
  risk scoring, and policy enforcement
- Distributed, fail-closed velocity limits and cold-start-resistant anomaly
  detection
- Append-only, keyed-MAC evidence with retention that never breaks
  verifiability
- At-most-once, idempotent dispatch with pure replay — replay never
  re-executes a side effect
- An HTTP surface, a PostgreSQL evidence/limits backend, Redis-backed
  limits, KMS-backed signing, and OpenTelemetry tracing/metrics
- 97 compliance controls mapped across 24 regulatory frameworks
- Zero mandatory runtime dependencies

## Install

```bash
pip install -e .
# Optional extras, install only what you need:
# pip install -e .[api,postgres,redis,kms,otel,delta,spark,yaml,crypto,authoring]
```

## Quick start

```python
from glassbox.adapters.outbound.memory import (
  memory_adapter_set,
  wire_memory_adapter_set,
)
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.app.decision_service import DecisionService

# Development only: no durability, distributed state, or managed key custody.
runtime = wire_memory_adapter_set(
  build_runtime(GlassBoxConfig(profile=RuntimeProfile.DEV), memory_adapter_set())
)
decisions = DecisionService(runtime)
print(runtime.describe())
```

GlassBox denies by default. Before an action can execute, register its governed
definition, mandate, policy permission, baseline, and dispatcher. The
[complete quick start](docs/USER/quick_start.md) is executed from Markdown as
part of documentation validation and demonstrates the full path.

## Run tests

```bash
python -m pytest tests -q
python -m pytest tests --cov=glassbox --cov-report=term-missing
```

## Project layout

- `glassbox/domain/` — pure value objects and business rules; no I/O, no
  third-party dependencies
- `glassbox/ports/` — the interfaces (`Protocol`s) the domain and
  application layer depend on
- `glassbox/app/` — orchestration: `DecisionService`, the composition root,
  configuration, and observability; depends only on `domain` and `ports`
- `glassbox/adapters/outbound/` — concrete infrastructure implementations
  (PostgreSQL, Redis, KMS, identity verification, OpenTelemetry, Delta Lake,
  Spark, and an in-memory reference set for local development)
- `glassbox/adapters/inbound/http/` — the HTTP entry point onto
  `DecisionService`
- `glassbox/compliance/` — the compliance control catalogue and reporting
- `glassbox/governance/`, `glassbox/store/`, `glassbox/api/` — the original
  synchronous implementation, retained for the components documented as
  preserved in `docs/ARCHITECTURE.md`; new work targets the layers above
- `tests/` — unit, contract/conformance, property-based, adversarial, and
  multi-process integration tests
- `docs/` — architecture, deployment, security, and API documentation

## Documentation map

- [docs/README.md](docs/README.md) — documentation index
- [docs/CLAIMS.md](docs/CLAIMS.md) — every claim, its code, and its test
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — system architecture
- [docs/USER/quick_start.md](docs/USER/quick_start.md) — getting started
- [docs/DEVELOPMENT/implementation_guide.md](docs/DEVELOPMENT/implementation_guide.md) — extending GlassBox
- [docs/DEPLOYMENT/README.md](docs/DEPLOYMENT/README.md) — running it in production
- [docs/SECURITY/README.md](docs/SECURITY/README.md) — security posture and hardening

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache 2.0 (see [LICENSE](LICENSE)).
