# glassbox/compliance

> **Legacy compatibility module:** This catalogue integrates with the original
> synchronous pipeline. Current guarantees are tracked in `docs/CLAIMS.md`.

Compliance catalogue and evidence mapping helpers.

## Key Modules

- `catalogue.py`: compliance controls, posture summaries, gap analysis, evidence recording
- `reporter.py`: report assembly helpers

## Quick Start

```python
from glassbox.compliance.catalogue import ComplianceCatalogue

cat = ComplianceCatalogue()
print(cat.posture_summary())
```

## Operational Notes

- Wire catalogue into pipeline to record runtime evidence.
- Use posture and gap outputs for governance review cycles.
- Keep framework mappings reviewed as requirements evolve.

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_comprehensive.py -q
```

## Related Docs

- [docs/COMPLIANCE/requirements.md](../../docs/COMPLIANCE/requirements.md)
- [docs/COMPLIANCE/README.md](../../docs/COMPLIANCE/README.md)