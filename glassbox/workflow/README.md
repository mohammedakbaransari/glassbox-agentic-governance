# glassbox/workflow — Approval Workflow Engine

The `workflow` package manages the lifecycle of decisions requiring human review.

| Module | Role |
|---|---|
| `workflow_engine.py` | `WorkflowEngine`, `WorkflowInstance`, `WorkflowStep`, SLA monitoring |

**Workflow states:** `pending` → `in_review` → `approved` / `rejected` / `escalated`

**Features:**
- SLA timer monitoring (background thread, opt-in)
- Auto-escalation on SLA breach
- Per-step audit trail
- Queue statistics and dashboard support

```python
wfe = WorkflowEngine(default_sla_minutes=60, monitor_sla=True)

# Created automatically by pipeline for HUMAN_REVIEW decisions
pending  = wfe.list_pending()
breached = wfe.list_sla_breached()

wfe.approve(wf_id, actor="analyst@co.com", notes="Verified against contract")
wfe.reject(wf_id,  actor="manager@co.com", notes="Supplier not cleared")
```
