# Velocity Breaker Technical Details

Detailed operational notes for current velocity controls.

## Execution Point in Pipeline

Velocity checks are part of the governance path before final disposition and side effects. If limits trip, request processing is blocked early.

## Core Signals

- request frequency per agent in active window
- optional aggregate/fleet decision volume
- breaker trip state and cooldown behavior

## Data and Concurrency Considerations

- state is maintained with thread-safe patterns
- checks execute in hot path, so low-overhead structures matter
- distributed deployments can use shared backends for coordination where configured

## Failure and Degradation Patterns

Watch for:

- legitimate spikes triggering excessive blocks
- stale distributed state causing inconsistent behavior across replicas
- downstream retries multiplying request-rate pressure

Mitigations:

- align retry policies with breaker windows
- tune by agent class and workload profile
- instrument trip-rate and post-trip recovery timing

## Operational Dashboards

Track at minimum:

- breaker trip count by agent
- ecosystem trip count
- percent of blocked decisions caused by breaker vs policy
- mean time to recovery after trip

## Test Coverage Focus

- invariants under concurrent access
- regression scenarios around edge windows/cooldowns
- interaction with policy/risk flow when both can block

```bash
python -m pytest tests/test_velocity_breaker_invariants.py -q
python -m pytest tests/test_regression.py -q
```