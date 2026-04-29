# Velocity Breaker Feature

Velocity breaker controls are designed to prevent unsafe bursts of autonomous decisions.

## Why It Exists

Without throughput guardrails, agents can generate a high volume of valid-looking decisions that still create systemic risk (budget depletion, operational overload, fraud-like patterns).

## Implementation

Primary implementation is in:

- `glassbox/governance/velocity_breaker.py`

Integrated into governance flow by:

- `glassbox/governance/pipeline.py`

## Runtime Behavior

- per-agent sliding-window limits
- optional ecosystem/fleet-level limits
- fail-fast blocking when limits are exceeded
- status introspection via API and pipeline helpers

## API Visibility

- `GET /agents/{agent_id}/velocity`
- `GET /ecosystem`

## Tuning Guidance

Start conservative and tune from telemetry:

- set limits by agent criticality and expected cadence
- separate batch/burst workloads from interactive workloads
- review false positives before raising thresholds globally

## Validation Commands

```bash
python -m pytest tests/test_governance.py -q
python -m pytest tests/test_velocity_breaker_invariants.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [velocity_breaker_details.md](velocity_breaker_details.md)
- [../API/endpoint_reference.md](../API/endpoint_reference.md)
- [../DEPLOYMENT/performance_tuning.md](../DEPLOYMENT/performance_tuning.md)