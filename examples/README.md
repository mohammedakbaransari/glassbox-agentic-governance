# Examples

## `quick_start_v2.py`

A minimal, runnable, verified end-to-end walk through the v2 decision path:
identity → mandate → policy → risk → limits → baseline → evidence →
dispatch, using the in-memory reference adapter set (no database, Redis, or
KMS required).

```bash
pip install -e .
python examples/quick_start_v2.py
```

This is a **development-only configuration** — no durability, no distributed
state, no managed key custody. See [docs/DEPLOYMENT/guide.md](../docs/DEPLOYMENT/guide.md)
for a production adapter set (Postgres, Redis, a managed KMS key, and durable
WORM anchoring), and [docs/USER/quick_start.md](../docs/USER/quick_start.md)
for the same walkthrough with narrative explanation.

## Further Reading

- [docs/USER/quick_start.md](../docs/USER/quick_start.md) — the annotated version of this example
- [docs/USER/use_cases.md](../docs/USER/use_cases.md) — governance patterns by industry
- [glassbox/app/README.md](../glassbox/app/README.md) — the orchestration layer this example exercises
- [docs/API/v2_endpoint_reference.md](../docs/API/v2_endpoint_reference.md) — the same flow over HTTP
