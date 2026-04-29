# glassbox/events

Event publication and subscription utilities for governance outcomes.

## Key Modules

- `event_bus.py`: event types, handlers, subscription and publish primitives

## Quick Start

```python
from glassbox.events.event_bus import EventBus

bus = EventBus()


def on_blocked(event):
    print("blocked", event)


bus.subscribe("decision.blocked", on_blocked)
```

## Operational Notes

- Integrates with governance event dispatching in pipeline finalize flow.
- Use structured handlers for logging, alerting, and downstream integrations.
- Keep handler failure behavior explicit in production (retry, fallback, dead-letter strategy).

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_integrations.py -q
```

## Related Docs

- [docs/API/endpoint_reference.md](../../docs/API/endpoint_reference.md)
- [docs/DEVELOPMENT/architecture.md](../../docs/DEVELOPMENT/architecture.md)