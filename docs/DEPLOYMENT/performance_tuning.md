# Performance and Capacity Engineering

Tune GlassBox only after preserving its safety invariants. Evidence durability,
signature verification, identity checks, fail-closed limits, and receipt-gated
dispatch are not optional performance knobs.

## Measure the Decision Path

Measure end-to-end latency and each external dependency under representative
allow, deny, approval, and failure traffic. Track percentiles, not averages.

| Segment | Primary drivers |
|---|---|
| Identity | JWKS cache, issuer latency, certificate validation |
| Catalogue/policy | Bundle size, cache validity, signature verification |
| Limits/baselines | Redis round trips, Lua execution, key cardinality |
| Evidence | PostgreSQL transaction latency, fsync, contention per segment |
| Signing | KMS latency and throttling |
| Dispatch | Target-system latency, timeout, idempotency lookup |
| Telemetry | Export batching, sampling, attribute cardinality |

Use OpenTelemetry at the outbound adapter and database/service-native telemetry.
Do not add timing calls directly to pure domain logic.

## Capacity Model

At steady state, each decision may require identity verification, catalogue and
policy reads, multiple Redis operations, a signing operation, an intent
transaction, optional dispatch, and an outcome transaction. Capacity planning
must therefore include dependency quotas and connection pools, not only Python
worker CPU.

Estimate concurrency with Little's Law:

$$
L = \lambda W
$$

where $L$ is in-flight work, $\lambda$ is arrival rate, and $W$ is mean service
time. Size pools from measured tail latency and bounded burst assumptions, then
load test denial and dependency-failure paths as well as success.

## Safe Tuning Levers

### Process and Worker Count

Increase workers only when PostgreSQL, Redis, KMS, and target-system quotas can
support the resulting concurrency. Keep one immutable runtime per process and
never use memory adapters to coordinate replicas.

### Database

- bound connection pools;
- keep intent transactions short;
- monitor segment lock contention and fsync latency;
- partition/retain evidence according to verified query and legal requirements;
- validate backup and restore throughput.

Do not trade durability for throughput by disabling required fsync behavior.

### Redis

- colocate where latency and failure-domain policy permit;
- monitor command latency, memory, eviction, replication, and failover;
- bound tenant/subject cardinality;
- retain atomic server-side admission semantics.

Never fall back to local counters during an outage.

### KMS and Identity

Use provider-supported connection reuse and bounded caches. Cache public JWKS
according to issuer semantics. Never cache an authorization decision or expose
signing key material to avoid a KMS call.

### Telemetry

Batch exports, sample traces according to risk, and bound labels. Preserve
denials, dependency failures, evidence failures, and dispatch failures at the
required audit level. Do not emit raw credentials or unrestricted parameters.

## Load-Test Scenarios

1. Typical allow mix at steady state.
2. Denial-heavy traffic with unknown actions and invalid mandates.
3. Burst to configured velocity ceilings.
4. High-cardinality tenants, agents, resources, and idempotency keys.
5. Slow KMS, Redis, PostgreSQL, identity, and target systems.
6. Dependency outage proving no fail-open behavior.
7. Replica restart during intent and dispatch transitions.
8. Replay workload proving zero effects.

Report p50/p95/p99 latency, throughput, denial reasons, pool saturation,
dependency errors, evidence commit latency, dispatch retries, and duplicate
effect count.

## Regression Gates

```bash
python -m pytest tests/test_performance.py -q
python -m pytest tests/test_multiprocess_limits.py -q
python -m pytest tests/test_dispatcher_idempotency.py -q
```

Environment-backed tests require the variables documented in
[deployment_reference.md](deployment_reference.md).

## Unsupported Advice Removed

Earlier documentation showed `engine.compile()`, `policy_tags`, `audit_filter`,
and per-call `schema_validator` examples. Those are not current runtime APIs and
must not be used as tuning guidance.