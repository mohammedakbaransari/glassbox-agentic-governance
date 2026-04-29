# glassbox/orchestration

Orchestration primitives for chain, graph, and saga-style governed execution.

## Key Modules

- `orchestrator.py`: `AgentOrchestrator`, `AgentNode`, execution result models

## Quick Start

```python
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.orchestration.orchestrator import AgentOrchestrator

pipeline = GovernancePipeline()
orch = AgentOrchestrator(pipeline)
```

## Operational Notes

- Use chain mode for strict sequential dependency flows.
- Use graph mode for parallelizable nodes.
- Use saga mode for multi-step operations requiring compensation semantics.

## Testing

```bash
python -m pytest tests/test_framework.py -q
python -m pytest tests/test_regression.py -q
```

## Related Docs

- [docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md)
- [docs/FEATURES/enterprise.md](../../docs/FEATURES/enterprise.md)