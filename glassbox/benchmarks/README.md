# Benchmark Harness (Legacy Runtime)

`run_benchmarks.py` measures the retained v1 `GovernancePipeline`: throughput,
latency distributions, policy behavior, anomaly detection, concurrency, and
retry overhead.

```bash
python -m glassbox.benchmarks.run_benchmarks
```

Results are local microbenchmarks, not production capacity claims. Record Python,
OS, CPU, dependency versions, workload size, warm-up, configuration, and raw
output when comparing runs. The harness does not represent v2 external-service
latency for PostgreSQL, Redis, KMS, identity, or dispatch.

For current capacity methodology, see
[performance tuning](../../docs/DEPLOYMENT/performance_tuning.md).