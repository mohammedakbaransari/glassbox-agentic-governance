# glassbox/workflow

> **Not v1 debt — the sanctioned `WorkflowGateway` backend.** This is one of
> two modules (alongside [`glassbox/store`](../store/README.md)) kept outside
> the v2 layering after the rest of the original synchronous implementation
> was physically deleted. `WorkflowEngine` is the real implementation reached
> through [`glassbox.ports.workflow.WorkflowGateway`](../ports/workflow.py),
> used by [`glassbox.app.approval_service.ApprovalService`](../app/approval_service.py).

Workflow management for decisions that require human review: creation,
approve/reject/escalate/expire/revoke transitions, quorum (dual-control)
approval, and SLA-breach detection.

## Key Modules

- `workflow_engine.py` — `WorkflowEngine`: workflow lifecycle, SLA handling, escalation paths

## Quick Start

`WorkflowEngine` is never imported by `glassbox.app`/`glassbox.domain`/
`glassbox.ports`/`glassbox.adapters.outbound` directly — only the composition
root (and tests) construct it, then hand it to the runtime through the port:

```python
from glassbox.store.repository import SQLiteWorkflowRepository
from glassbox.workflow.workflow_engine import WorkflowEngine

engine = WorkflowEngine(
    repository=SQLiteWorkflowRepository("./glassbox_workflows.db"),
    default_sla_minutes=60,
)
runtime = runtime.with_workflow_engine(engine)

# From here on, only glassbox.app.approval_service.ApprovalService operates it:
from glassbox.app.approval_service import ApprovalService

approvals = ApprovalService(runtime)
approvals.list_pending()
approvals.approve(decision_id, actor="reviewer@acme.com")
```

Dual-control (quorum) approval — two distinct approvers required before a
decision executes:

```python
engine.quorum_approve(workflow_id, actor="reviewer_a", min_approvers=2)
engine.quorum_approve(workflow_id, actor="reviewer_b", min_approvers=2)
# After reviewer_b: state becomes "approved"
```

## Operational Notes

- `create_from_decision(...)` is idempotent per `decision_id` — safe to call
  again after a crash-recovery replay.
- `ApprovalService` never dispatches an effect; approving a workflow only
  updates its own state. Obligation discharge on approval is out of scope by
  design, tracked separately.
- SLA monitoring (`monitor_sla=True`) runs a background thread inside this
  process; `expire_overdue()` can also be invoked from an external scheduler.
- There is no event-bus integration — `WorkflowEngine` has no `event_bus`
  parameter. Real-time notification on state transitions is the caller's
  responsibility (poll `list_pending()`/`list_sla_breached()`, or wire your
  own hook at the `ApprovalService` call sites).

## Testing

```bash
python -m pytest tests/test_approval_service.py tests/test_http_approvals.py tests/test_decision_service.py -q
```

## Related Docs

- [glassbox/ports/workflow.py](../ports/workflow.py) — the `WorkflowGateway` contract
- [glassbox/store/README.md](../store/README.md) — the persistence layer this engine uses
- [docs/FEATURES/enterprise.md](../../docs/FEATURES/enterprise.md)
- [docs/API/v2_endpoint_reference.md](../../docs/API/v2_endpoint_reference.md) — `/v2/approvals/*`
