# glassbox/governance

Core governance runtime for decision evaluation.

## Key Modules

- `pipeline.py`: `GovernancePipeline` orchestration
- `models.py`: request/response and audit dataclasses
- `policy_engine.py`: policy registration and evaluation
- `risk_evaluator.py`: risk scoring and disposition support
- `anomaly_detector.py`: anomaly checks
- `velocity_breaker.py`: per-agent and ecosystem velocity controls
- `stage_registry.py`: stage configuration and latency tracking
- `write_ahead_log.py`: finalize-phase recovery support

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.models import DecisionRequest, DecisionType

pipeline = GovernancePipeline()
resp = pipeline.process(
    DecisionRequest(
        agent_id="agent_1",
        decision_type=DecisionType.PROCUREMENT,
        payload={"amount": 50000, "supplier_id": "SUP-001"},
    )
)
print(resp.final_status, resp.risk_score)
```

## Operational Notes

- Pipeline health includes aggregate and per-stage latency signals.
- Async processing is available through `process_async()`.
- Optional integrations: event bus, audit repo, workflow engine, compliance catalogue.
- Stage behavior can be controlled with `StageRegistry` when provided.

## Testing

```bash
python -m pytest tests/test_governance.py -q
python -m pytest tests/test_core.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)
- [docs/DEVELOPMENT/architecture.md](../../docs/DEVELOPMENT/architecture.md)
- [docs/FEATURES/enterprise.md](../../docs/FEATURES/enterprise.md)