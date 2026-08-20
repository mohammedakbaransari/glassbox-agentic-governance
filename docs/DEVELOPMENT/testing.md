# Testing Strategy

GlassBox tests governance behavior at multiple boundaries. Test depth scales
with the consequence of the contract: pure domain rules use fast unit tests;
durability, atomicity, tenancy, and idempotency use conformance and real-service
integration tests.

## Local Baseline

```bash
pip install -e .[dev]
python -m pytest tests -q
```

Coverage is enforced at 80 percent for the current v2 architecture (`domain`,
`ports`, `app`, and inbound/outbound adapters). Retained legacy compatibility
packages are tested but reported separately from this release gate:

```bash
python -m pytest tests \
  --cov=glassbox.domain --cov=glassbox.ports --cov=glassbox.app \
  --cov=glassbox.adapters.inbound --cov=glassbox.adapters.outbound \
  --cov-report=term-missing --cov-fail-under=80
```

## Test Layers

| Layer | Purpose | Examples |
|---|---|---|
| Domain unit | Deterministic values, validation, risk, and serialization | `test_domain.py`, `test_risk_determinism.py` |
| Port conformance | One behavioral contract across adapter implementations | `test_ports.py`, `conformance_*.py` |
| Application | Ordering, short-circuiting, evidence-before-effect, replay | `test_app.py`, `test_decision_service.py` |
| Architecture | Dependency direction, purity, banned constructs | `test_layering.py` |
| Adversarial | Threat-specific fail-closed behavior | `test_adversarial_suite.py` |
| Integration | PostgreSQL, Redis, KMS, Delta, HTTP, and multiprocess behavior | Technology-specific test modules |
| Claims | Documentation citations resolve to executable tests | `test_claims_coverage.py` |
| Packaging | Wheel/editable install and metadata contracts | `test_packaging.py` |

## Focused Commands

```bash
lint-imports
python -m pytest tests/test_layering.py -q
python -m pytest tests/test_decision_service.py tests/test_http_app.py -q
python -m pytest tests/test_adversarial_suite.py -q
python -m pytest tests/test_claims_coverage.py -q
```

## External Services

Real-service tests skip unless their environment is explicitly configured:

| Service | Environment variable | Representative tests |
|---|---|---|
| PostgreSQL | `GLASSBOX_POSTGRES_DSN` | Evidence, row-level security, dispatcher ledger |
| Redis | `GLASSBOX_REDIS_URL` | Limits, baselines, per-tenant quota, multiprocess atomicity |
| Local Spark job | `GLASSBOX_SPARK_LOCAL_JOB=1` | Optional Spark execution path |
| Build/install | `GLASSBOX_RUN_BUILD_TESTS=1` | Isolated package installation |
| P99 benchmark | `GLASSBOX_RUN_BENCHMARKS=1` | `tests/test_performance_benchmarks.py` (not run by default; environment-sensitive) |

Use isolated non-production resources. Integration tests may create schemas,
keys, or records.

## Quality Gates

CI runs formatting, import order, pylint, mypy, strict domain/port typing,
import-linter, ruff, Bandit, dependency audit, a package build/install check on
Python 3.13 (the single supported version), the full test suite, examples,
benchmarks, property tests, and environment-backed integration jobs.

Run the relevant focused check immediately after an edit, then run the full
suite before merging a cross-cutting change.

## Writing Tests

- Assert observable invariants, not merely the absence of exceptions.
- Use the injected `Clock`; do not depend on wall-clock sleeps.
- Test denial and dependency-failure paths as first-class behavior.
- For concurrency, assert a meaningful shared-state invariant such as unique
  sequence numbers or a bounded total.
- Keep unit tests independent of optional services.
- Add conformance cases when changing a port contract.
- Update [CLAIMS.md](../CLAIMS.md) when a public guarantee changes.

## Batch Harness

`scripts/run_test_batches.py` executes `tests/batch_manifest.json` and can
produce artifacts for scheduled runs. It is an orchestration convenience, not a
substitute for the ordinary pytest suite.

```bash
python scripts/run_test_batches.py
```

## Related Documentation

- [Contributing](../../CONTRIBUTING.md)
- [Architecture](../ARCHITECTURE.md)
- [Verified claims](../CLAIMS.md)