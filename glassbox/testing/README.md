# Batch Test Harness

`glassbox.testing.batch_runner` loads `tests/batch_manifest.json`, schedules
compatible batches, runs pytest subprocesses, and writes JUnit, JSON, stdout,
stderr, and summary artifacts under `test-results` by default.

Use the repository wrapper:

```bash
python scripts/run_test_batches.py
```

Profiles marked `isolated`, `perf`, or `heavy-io` run sequentially; standard
batches may run in parallel. A batch timeout is a failure and is recorded in the
summary.

The harness complements, but does not replace:

```bash
python -m pytest tests -q
```

See the [testing strategy](../../docs/DEVELOPMENT/testing.md) for test layers,
service gates, and CI commands.