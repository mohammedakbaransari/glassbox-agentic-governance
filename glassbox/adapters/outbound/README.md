# Outbound Adapters

Outbound adapters implement current port contracts using infrastructure or
external services. They translate technology-specific behavior into the stable
domain and application contracts consumed by `DecisionService`.

## Adapter Families

| Package | Capability | Maturity boundary |
|---|---|---|
| `memory` | Complete reference adapter set | Development only; rejected by `PROD` |
| `identity` | OIDC/JWKS verification and delegation assertions | Requires governed issuer and key configuration |
| `postgres` | Durable evidence, tenant context, and dispatch ledger | Integration tests require PostgreSQL |
| `redis` | Atomic distributed limits and behavioral baselines | Integration tests require Redis |
| `kms` | Managed MAC signing | Requires provider credentials and key policy |
| `otel` | OpenTelemetry tracer and meter installation | Optional dependency; export policy is deployment-owned |
| `delta` | Bronze/Silver evidence processing with Delta Lake | Analytical path, not the decision transaction |
| `spark` | Serializable batch preauthorization and control testing | Optional batch path |
| `replay.py` | Replay-oriented adapter helpers | Cannot dispatch effects |
| `worm.py` | Immutable anchor integration | Requires object-lock capable storage |
| `signing.py` | Signing support shared by adapter implementations | Key custody determines assurance |

No single production adapter set is selected inside `glassbox.app`. A process
entry point supplies factories for all fourteen required ports and marks the set
`dev_only=False` only when its deployment properties justify that claim.

## Engineering Rules

- Keep vendor types at the adapter boundary.
- Parameterize all SQL values; do not format untrusted values into statements.
- Preserve tenant context for every storage operation.
- Use atomic server-side operations for distributed limits and idempotency.
- Map dependency failures to explicit structured errors.
- Do not weaken a port contract to accommodate a vendor limitation.

## Verification

```bash
python -m pytest tests/test_memory_adapters.py tests/test_postgres_evidence.py -q
python -m pytest tests/test_redis_limits.py tests/test_redis_baseline.py -q
python -m pytest tests/test_kms_signer.py tests/test_layering.py -q
```

Tests that require live services skip unless their documented environment
variables are present.

## Related Documentation

- [Port contracts](../../ports/README.md)
- [Architecture](../../../docs/ARCHITECTURE.md)
- [Deployment](../../../docs/DEPLOYMENT/README.md)