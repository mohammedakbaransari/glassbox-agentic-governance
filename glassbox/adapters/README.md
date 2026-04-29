# glassbox/adapters

Platform and Spark adapters for running governance in varied compute environments.

## Key Modules

- `platforms.py`: platform detection and adapter classes
- `spark.py`: Spark dataframe/stream governance helpers

## Quick Start

```python
from glassbox.adapters.platforms import auto_detect_adapter

adapter = auto_detect_adapter()
pipeline = adapter.create_pipeline()
```

## Operational Notes

- Use explicit adapter selection when environment detection is ambiguous.
- For Spark-scale governance, prefer partition-based processing patterns.
- Validate cluster/runtime resource sizing before high-volume runs.

## Testing

```bash
python -m pytest tests/test_integrations.py -q
python -m pytest tests/test_performance.py -q
```

## Related Docs

- [docs/DEPLOYMENT/guide.md](../../docs/DEPLOYMENT/guide.md)
- [docs/DEPLOYMENT/performance_tuning.md](../../docs/DEPLOYMENT/performance_tuning.md)