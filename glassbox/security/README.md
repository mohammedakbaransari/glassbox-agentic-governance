# glassbox/security

Payload safety checks and request sanitization helpers.

## Key Modules

- `sanitizer.py`: payload validation, security report generation, agent-id validation

## Quick Start

```python
from glassbox.security.sanitizer import PayloadSanitizer

sanitizer = PayloadSanitizer(max_payload_bytes=1_000_000)
report = sanitizer.validate({"input": "hello"})
print(report.is_safe)
```

## Operational Notes

- Run sanitizer checks before governance evaluation for untrusted input.
- Tune payload limits and field limits to fit workload characteristics.
- Treat repeated security violations as operational alerts.

## Testing

```bash
python -m pytest tests/test_security.py -q
python -m pytest tests/test_api.py -q
```

## Related Docs

- [docs/SECURITY/hardening.md](../../docs/SECURITY/hardening.md)
- [docs/USER/troubleshooting.md](../../docs/USER/troubleshooting.md)