# glassbox/workflow

Workflow management for decisions routed to human review.

## Key Modules

- `workflow_engine.py`: workflow lifecycle, SLA handling, escalation paths

## Quick Start

```python
from glassbox.workflow.workflow_engine import WorkflowEngine

engine = WorkflowEngine(default_sla_minutes=60, monitor_sla=True)
pending = engine.list_pending()
for wf in pending:
    print(wf.workflow_id, wf.status)
```

## Operational Notes

- Use with `GovernancePipeline` to process `PENDING_REVIEW` outcomes.
- `create_from_decision(...)` is designed for idempotent workflow creation.
- SLA monitoring can be enabled or disabled per deployment needs.

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [docs/FEATURES/enterprise.md](../../docs/FEATURES/enterprise.md)
- [docs/DEPLOYMENT/guide.md](../../docs/DEPLOYMENT/guide.md)