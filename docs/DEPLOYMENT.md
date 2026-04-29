# Deployment Overview

Top-level deployment guidance points to detailed runbooks in `docs/DEPLOYMENT/`.

## Recommended Reading Order

1. [DEPLOYMENT/README.md](DEPLOYMENT/README.md)
2. [DEPLOYMENT/guide.md](DEPLOYMENT/guide.md)
3. [DEPLOYMENT/deployment_reference.md](DEPLOYMENT/deployment_reference.md)
4. [DEPLOYMENT/performance_tuning.md](DEPLOYMENT/performance_tuning.md)

## Minimal Production Checklist

- Install package with required optional extras.
- Verify baseline tests pass.
- Start API and validate `/health` and `/ready`.
- Configure authentication and TLS at the edge.
- Configure persistent audit storage and retention.
- Wire logs/metrics/events into observability stack.

## Core Commands

```bash
python -m pytest tests -q
python scripts/run_test_batches.py
python -m glassbox.api.app
```

## Related References

- [API/endpoint_reference.md](API/endpoint_reference.md)
- [SECURITY/hardening.md](SECURITY/hardening.md)
- [ARCHITECTURE.md](ARCHITECTURE.md)