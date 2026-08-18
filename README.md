# GlassBox: Runtime Decision Governance for Autonomous AI Systems

[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](https://www.python.org)

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
from glassbox.adapters.outbound.memory import memory_adapter_set
from glassbox.app.composition import build_runtime
from glassbox.app.config import GlassBoxConfig, RuntimeProfile
from glassbox.app.decision_service import DecisionService
from glassbox.domain.action import ResourceRef
from glassbox.domain.identity import CredentialType, RawCredential

# The in-memory adapter set is for local development only: it provides no
# durability or tamper-evidence guarantees. Production uses the PostgreSQL,
# Redis and KMS adapters under glassbox/adapters/outbound instead.
runtime = build_runtime(GlassBoxConfig(profile=RuntimeProfile.DEV), memory_adapter_set())
decisions = DecisionService(runtime)

credential = RawCredential(
    credential_type=CredentialType.OIDC,
    material="dev:acme:agent.procurement-bot:instance-01",
)
resource = ResourceRef(kind="purchase_order", id="po-4471", tenant_id="acme")

outcome = decisions.decide_and_dispatch_for_request(
    credential,
    action_name="procurement.create_purchase_order",
    resource=resource,
    parameters={"amount": 750000, "category": "semiconductors"},
    idempotency_key="po-4471-create",
)

print(outcome.decision.effect)      # ALLOW / DENY / REQUIRE_APPROVAL
print(outcome.decision.rationale)
print(outcome.execution)
```

`procurement.create_purchase_order` must first be registered in the action
catalogue (consequence class, exposure rule, parameter schema) — see
`tests/test_decision_service.py` for a complete, runnable setup and
[docs/DEVELOPMENT/implementation_guide.md](docs/DEVELOPMENT/implementation_guide.md)
for how to register your own actions.

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
