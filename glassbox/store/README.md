# glassbox/store

> **Not v1 debt — the sanctioned `WorkflowGateway` backend.** Everything else
> this package used to hold (`PolicyRepository`, `AuditRepository`,
> `database.py`, `database_abstraction.py`) was physically deleted along with
> the rest of the original synchronous implementation. This package survives
> for one reason: it is the real, tested persistence layer behind
> [`glassbox.ports.workflow.WorkflowGateway`](../ports/workflow.py) — see that
> port's docstring for why the seam exists at all.

`glassbox.app`, `glassbox.domain`, `glassbox.ports`, and
`glassbox.adapters.outbound` never import this package directly — that
boundary is mechanically enforced by `tests/test_layering.py` and the
import-linter contract in `pyproject.toml`. Only the composition root, tests,
and `glassbox.workflow.workflow_engine.WorkflowEngine` (which this package
backs) reach it.

## Key Modules

- `repository.py` — `WorkflowStep`, `WorkflowInstance`, `WorkflowRepository`
  (abstract), `SQLiteWorkflowRepository` (SQLite-backed, thread-safe)

## Quick Start

```python
from glassbox.store.repository import SQLiteWorkflowRepository
from glassbox.workflow.workflow_engine import WorkflowEngine

repository = SQLiteWorkflowRepository("./glassbox_workflows.db")
engine = WorkflowEngine(repository=repository)

# Wired into the v2 runtime through the port, not used directly by app code:
runtime = runtime.with_workflow_engine(engine)
```

See [`glassbox.app.approval_service.ApprovalService`](../app/approval_service.py)
for the application-layer service that operates this lifecycle, and
`/v2/approvals/*` in [docs/API/v2_endpoint_reference.md](../../docs/API/v2_endpoint_reference.md)
for the HTTP surface.

## Operational Notes

- `SQLiteWorkflowRepository(":memory:")` is the reference/test configuration;
  pass a file path for durable, production persistence.
- `create()` is idempotent per `decision_id` — a WAL crash-recovery replay
  that calls it twice does not create a duplicate workflow instance.

## Testing

```bash
python -m pytest tests/test_approval_service.py tests/test_http_approvals.py -q
```

## Related Docs

- [glassbox/ports/workflow.py](../ports/workflow.py) — the `WorkflowGateway` contract
- [glassbox/workflow/README.md](../workflow/README.md) — the engine this repository backs
- [docs/API/v2_endpoint_reference.md](../../docs/API/v2_endpoint_reference.md) — `/v2/approvals/*`
