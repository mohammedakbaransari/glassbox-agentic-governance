# Adapters

Adapters connect GlassBox runtime contracts to transports and infrastructure.

## Current Architecture

- [`inbound/http`](inbound/http/README.md): Flask transport for `DecisionService`
- [`outbound`](outbound/README.md): implementations for memory, identity,
  PostgreSQL, Redis, KMS, OpenTelemetry, Delta Lake, Spark, and WORM storage

Inbound adapters translate requests. Outbound adapters implement ports. Neither
location owns domain policy or risk semantics.

## Compatibility Modules

`platforms.py` and the top-level `spark.py` support the original synchronous
`GovernancePipeline`. They remain tested for compatibility. New v2 adapters
belong under `inbound` or `outbound` and must satisfy the enforced dependency
contracts.

## Verification

```bash
python -m pytest tests/test_layering.py tests/test_memory_adapters.py -q
```

## Related Documentation

- [Architecture](../../docs/ARCHITECTURE.md)
- [Ports](../ports/README.md)
- [Deployment](../../docs/DEPLOYMENT/README.md)