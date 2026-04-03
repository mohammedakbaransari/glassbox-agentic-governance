# glassbox/orchestration — Agent Orchestration Layer

The `orchestration` package coordinates chains and graphs of AI agents with governance at every node.

| Module | Role |
|---|---|
| `orchestrator.py` | `AgentOrchestrator`, `AgentNode`, `NodeResult`, `OrchestrationResult` |

**Patterns:**
- `run_chain()` — linear sequence, abort on first block
- `run_graph()` — DAG with parallel fan-out (ThreadPoolExecutor)
- `run_saga()` — distributed with compensation/rollback

```python
orch = AgentOrchestrator(pipeline)
result = orch.run_chain([node_a, node_b, node_c])
# If node_b is blocked, node_c never runs

# Async variants
result = await orch.run_chain_async(nodes)
result = await orch.run_graph_async(nodes)
```

See [../../docs/USECASES.md](../../docs/USECASES.md) Pattern 6 for full chain-of-agents example.
