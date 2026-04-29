# GlassBox: Runtime Decision Governance for Autonomous AI Systems

[![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache%202.0-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-1.2.0-blue)](pyproject.toml)

GlassBox is a Python framework that evaluates AI-generated operational decisions before execution. It validates requests, enforces policy, scores risk, routes outcomes, and records audit evidence.

## What it provides

- Runtime policy enforcement for agent decisions
- Risk scoring and anomaly detection
- Velocity/fleet breakers and contract checks
- Immutable/tamper-evident audit support
- Orchestration patterns (chain, DAG, saga)
- Optional API, Spark, Redis, OPA, and MCP integrations

## Install

```bash
pip install -e .
# Optional extras:
# pip install -e .[api,yaml,crypto,redis,spark,authoring]
```

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models import DecisionRequest, DecisionType

pipeline = GovernancePipeline()
result = pipeline.process(
    DecisionRequest(
        agent_id="procurement_agent",
        decision_type=DecisionType.PROCUREMENT,
        payload={"amount": 750000, "category": "semiconductors"},
    )
)

print(result.final_status)
print(result.risk_score)
print(result.policy_violations)
```

## Run tests

```bash
# Full suite
python -m pytest tests -q

# Coverage
python -m pytest tests --cov=glassbox --cov-report=term-missing

# Batch harness
python scripts/run_test_batches.py
```

## Run examples

```bash
python examples/industry_examples.py --list
python examples/industry_examples.py
python -m glassbox.scenarios.run_scenarios
```

## API

```bash
pip install -e .[api]
python -m glassbox.api.app
```

Primary API documentation: [docs/API/endpoint_reference.md](docs/API/endpoint_reference.md)

## Project layout

- `glassbox/governance/`: core governance pipeline and controls
- `glassbox/api/`: Flask API app and middleware
- `glassbox/orchestration/`: chain/DAG/saga orchestrator
- `glassbox/compliance/`: compliance catalogue and reporting helpers
- `glassbox/rules/`: declarative rules loading/hot reload
- `glassbox/store/`: persistence abstractions and repositories
- `tests/`: unit and integration tests
- `docs/`: user, developer, API, security, deployment, and feature docs

## Documentation map

- [docs/README.md](docs/README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/USER/quick_start.md](docs/USER/quick_start.md)
- [docs/DEVELOPMENT/implementation_guide.md](docs/DEVELOPMENT/implementation_guide.md)
- [docs/DEPLOYMENT/README.md](docs/DEPLOYMENT/README.md)
- [docs/SECURITY/README.md](docs/SECURITY/README.md)

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache 2.0 (see [LICENSE](LICENSE)).