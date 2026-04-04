# glassbox/orchestration — Agent Orchestration Layer

The `orchestration` package coordinates chains and graphs of AI agents with governance at every node.

| Module | Role |
|---|---|
| `orchestrator.py` | `AgentOrchestrator`, `AgentNode`, `NodeResult`, `OrchestrationResult` |

**Patterns:**
- `run_chain()` — linear sequence, abort on first block
- `run_graph()` — DAG with parallel fan-out (ThreadPoolExecutor)
- `run_saga()` — distributed with compensation/rollback

---

## Quick Start

```python
from glassbox.orchestration.orchestrator import AgentOrchestrator, AgentNode
from glassbox.governance.pipeline import GovernancePipeline

pipeline = GovernancePipeline()
orch = AgentOrchestrator(pipeline)

# Define agent nodes
node_a = AgentNode(
    name="credit_check_agent",
    fn=lambda state: {"credit_score": 750}
)
node_b = AgentNode(
    name="risk_assessment_agent",
    fn=lambda state: {"risk_level": "low"}
)
node_c = AgentNode(
    name="approval_agent",
    fn=lambda state: {"approved": state["credit_score"] > 700}
)

# Run as linear chain
result = orch.run_chain([node_a, node_b, node_c])
print(f"Chain result: {result.final_state}")
print(f"Blocked nodes: {result.blocked_nodes}")
```

---

## Orchestration Patterns

### Pattern 1: Linear Chain (Sequential)

```python
# Agents run sequentially; if any is blocked, chain stops
result = orch.run_chain([node_a, node_b, node_c])

if result.status == "blocked":
    print(f"Chain stopped at {result.blocked_nodes[0]}")
else:
    print(f"Chain completed: {result.final_state}")

# Use when: order matters, later nodes depend on earlier results
# Example: KYC → Credit Check → Fraud Detection → Final Approval
```

### Pattern 2: Parallel Graph (DAG)

```python
# Nodes run in parallel where possible
nodes = [
    node_credit_check,
    node_identity_verify,
    node_aml_check,      # All three run concurrently
]

result = orch.run_graph(nodes, max_workers=3)
print(f"Parallel results: {result.final_state}")

# Use when: nodes are independent
# Example: Credit check, identity verification, AML check (all parallelizable)
```

### Pattern 3: SAGA Pattern (Distributed Transactions)

```python
# Run with compensation (rollback) on failure
saga_nodes = [
    AgentNode(
        name="reserve_inventory",
        fn=reserve_inventory_action,
        compensate=release_inventory_action
    ),
    AgentNode(
        name="charge_payment",
        fn=charge_payment_action,
        compensate=refund_payment_action
    ),
    AgentNode(
        name="ship_order",
        fn=ship_order_action,
        compensate=cancel_shipment_action
    ),
]

result = orch.run_saga(saga_nodes)

if result.status == "compensated":
    print(f"Rollback executed: {result.compensated_nodes}")
else:
    print(f"Saga completed: {result.final_state}")

# Use when: multiple steps need atomicity with fallback
# Example: E-commerce order (reserve → pay → ship; rollback if any block)
```

### Pattern 4: Conditional Branching

```python
# Next node depends on previous node's result
def credit_decision_node():
    if state["credit_score"] < 600:
        return AgentNode("decline", fn=lambda s: {"decision": "decline"})
    else:
        return AgentNode("approve", fn=lambda s: {"decision": "approve"})

# Implement with conditional output
node_credit = AgentNode(
    name="credit_evaluator",
    fn=lambda s: {"credit_score": 750, "next_node": "approve"}
)

result = orch.run_chain([node_credit])
next_node = determine_next_node(result.final_state)
```

---

## Performance Characteristics

| Operation | Latency | Throughput | Notes |
|-----------|---------|-----------|-------|
| run_chain() | 10–100 ms | 10–100 chains/sec | 3–5 sequential nodes |
| run_graph() | 5–50 ms | 20–200 graphs/sec | Parallel execution |
| run_saga() | 50–500 ms | 2–20 sagas/sec | Includes compensation logic |
| Node execution | 1–10 ms | — | Depends on agent work |

**Scaling:**
```python
# For high throughput, use graph pattern with worker threads
orch = AgentOrchestrator(pipeline, max_workers=10)

# For distributed systems, consider async variants
result = await orch.run_graph_async(nodes)
```

---

## Common Errors

### Error: "Chain interrupted; node blocked"

**Symptom:**
```python
result = orch.run_chain([node_a, node_b, node_c])
# result.status = "blocked"
# node_b was blocked; node_c never executed
```

**Cause:** One node produced a blocking decision; downstream nodes skipped

**Solution:**
```python
# Option 1: Review violation causing block
if result.status == "blocked":
    blocked_node = result.blocked_nodes[0]
    violations = result.node_results[blocked_node].violations
    print(f"Violations: {violations}")
    # Fix the upstream issue

# Option 2: Use graph pattern to run nodes in parallel
result = orch.run_graph([node_a, node_b, node_c])  # All run, block doesn't stop others

# Option 3: Design chain to allow block-then-continue
result = orch.run_chain([
    node_a,
    node_b_with_error_handler,  # Catches blocks, continues
    node_c
])
```

### Error: "SAGA compensation failed; transaction in limbo"

**Symptom:**
```
node_charge_payment blocked; attempting compensation...
Node refund_payment also blocked (payment system unavailable)
SAGA status: FAILED_COMPENSATE (transaction in inconsistent state)
```

**Cause:** Compensation action itself failed; system in unknown state

**Solution:**
```python
# Option 1: Implement idempotent compensation
def refund_payment_action(state):
    """Check if already refunded before retrying"""
    payment_id = state["payment_id"]
    if is_already_refunded(payment_id):
        return {"status": "already_refunded"}
    else:
        return submit_refund(payment_id)

# Option 2: Log for manual intervention
result = orch.run_saga(saga_nodes)
if result.status == "failed_compensate":
    alert_operations_team(f"SAGA compensation failed: {result}")
    # Manual reconciliation required

# Option 3: Use compensation timeout
saga_with_timeout = [
    AgentNode(
        name="charge_payment",
        fn=charge_action,
        compensate=refund_with_timeout,
        compensate_timeout_seconds=30
    )
]
```

### Error: "Deadlock: node_a waiting for node_b, node_b waiting for node_a"

**Symptom:**
```
Graph execution hung; no progress for 60 seconds
Threads waiting on each other's results
```

**Cause:** Circular dependencies in graph

**Solution:**
```python
# Option 1: Review node dependencies; remove cycles
# node_a depends on node_b output
# node_b depends on node_a output
# FIX: Make one node independent

# Option 2: Use chain pattern instead of graph
result = orch.run_chain([node_a, node_b])  # Linear; no deadlock risk

# Option 3: Add timeout to detect deadlocks
result = orch.run_graph(nodes, timeout_seconds=10)
if result.status == "timeout":
    print("Graph execution exceeded 10s; likely deadlock")
```

### Error: "State mutation: upstream node modified shared state"

**Symptom:**
```python
# node_a modifies shared state
state["user"]["credit_score"] = 999

# node_b sees corrupted state
print(state["user"]["credit_score"])  # Unexpected value
```

**Cause:** Nodes share mutable state; unintended mutations

**Solution:**
```python
# Option 1: Use immutable state passing
def node_a_safe(state):
    new_state = state.copy()  # Shallow copy
    new_state["credit_score"] = 750
    return new_state

# Option 2: Deep copy for nested structures
import copy
def node_b_safe(state):
    new_state = copy.deepcopy(state)
    new_state["details"]["verification"] = "done"
    return new_state

# Option 3: Use AgentNode with isolated state
node = AgentNode(
    name="isolated",
    fn=handler,
    isolated_state=True  # Each invocation gets fresh copy
)
```

---

## Multi-Tenant Orchestration

```python
# Route decisions through tenant-specific pipeline
orch = AgentOrchestrator(pipeline)

for tenant_id in ["tenant-a", "tenant-b"]:
    # Each tenant's nodes run independently
    tenant_nodes = get_nodes_for_tenant(tenant_id)
    result = orch.run_chain(tenant_nodes)
    save_result(tenant_id, result)
```

---

See [../../docs/ARCHITECTURE.md](../../docs/ARCHITECTURE.md#orchestration) for component details and [../../docs/USECASES.md](../../docs/USECASES.md) Pattern 6 for full chain-of-agents example.
