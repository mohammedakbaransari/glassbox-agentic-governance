# glassbox/events — Domain Event Bus

The `events` package provides the integration point for external systems.

| Module | Role |
|---|---|
| `event_bus.py` | `EventBus`, 8 domain event factories, async handlers, webhook handler |

**Domain events published:**
- `decision.executed` — decision approved and sent to downstream
- `decision.blocked` — decision blocked with violations
- `decision.pending_review` — decision routed to human review queue
- `policy.violated` — policy violations detected
- `circuit_breaker.tripped` — velocity or anomaly breaker fired
- `anomaly.detected` — statistical anomaly in payload
- `security.violation` — injection or malicious payload detected
- `workflow.sla_breached` — review SLA timer exceeded

```python
from glassbox.events.event_bus import EventBus, WebhookEventHandler

bus = EventBus()
bus.subscribe("decision.blocked", lambda e: alert_team(e.payload))
bus.subscribe("*", WebhookEventHandler("https://siem.company.com/glassbox"))
```
