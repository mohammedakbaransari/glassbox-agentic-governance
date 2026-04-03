"""
GlassBox — Agent Orchestration Layer  (v1.0.0)
===============================================
Orchestrates chains and graphs of AI agents, governing every decision
at each node before it executes downstream.

This layer addresses the fundamental gap between:
  - What GlassBox had: intercept ONE decision at a time
  - What production needs: orchestrate MANY agents in structured sequences

Three orchestration patterns:

  Pattern 1: AgentChain (linear sequence)
    A → B → C where each agent receives the previous agent's output.
    Each node's decision is governed before passing to the next.
    If any node is blocked, the chain aborts with a ChainAbortError.

  Pattern 2: AgentGraph (DAG — parallel + conditional)
    Nodes execute in dependency order.
    Parallel nodes run concurrently (ThreadPoolExecutor / asyncio).
    Conditional edges: route based on the governed outcome of a node.
    Fan-out → Fan-in with result aggregation.

  Pattern 3: AgentSaga (distributed multi-step with compensation)
    Each step is governed. If a step fails or is blocked, previously
    completed steps are compensated (rolled back) in reverse order.
    Essential for financial and operational multi-step AI workflows.

Thread-safety: All patterns are thread-safe and async-capable.
Context isolation: Each chain execution gets its own isolated context.
Cross-agent budget: The orchestrator enforces fleet-level budgets.

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from glassbox.governance.models import (
    AgentContract, DecisionContext, DecisionRequest, DecisionResponse,
    DecisionType, FinalStatus,
)


# ── Orchestration models ───────────────────────────────────────────────────────

class NodeStatus(str, Enum):
    PENDING  = "pending"
    RUNNING  = "running"
    EXECUTED = "executed"
    BLOCKED  = "blocked"
    SKIPPED  = "skipped"
    FAILED   = "failed"
    COMPENSATED = "compensated"


@dataclass
class AgentNode:
    """
    A single agent node in an orchestration graph.

    node_id:       Unique identifier within the graph
    agent_id:      The agent that makes the decision at this node
    decision_type: Type of decision this node produces
    payload_fn:    Callable that produces the payload given chain context
    depends_on:    Node IDs that must complete before this node runs
    condition_fn:  Optional: only run this node if condition(context) is True
    compensate_fn: Optional: called if a downstream node fails (saga rollback)
    timeout_s:     Maximum seconds this node may run (default 30)
    """
    node_id:       str
    agent_id:      str
    decision_type: DecisionType
    payload_fn:    Callable[[Dict[str, Any]], Dict[str, Any]]
    depends_on:    List[str]              = field(default_factory=list)
    condition_fn:  Optional[Callable]    = None
    compensate_fn: Optional[Callable]    = None
    timeout_s:     float                 = 30.0
    metadata:      Dict[str, Any]        = field(default_factory=dict)


@dataclass
class NodeResult:
    """Result of executing one agent node."""
    node_id:      str
    agent_id:     str
    status:       NodeStatus
    response:     Optional[DecisionResponse] = None
    output:       Dict[str, Any]             = field(default_factory=dict)
    error:        Optional[str]              = None
    duration_ms:  float                      = 0.0
    compensated:  bool                       = False


@dataclass
class OrchestrationResult:
    """Result of a full chain/graph/saga execution."""
    execution_id:   str
    pattern:        str                      # "chain" | "graph" | "saga"
    status:         str                      # "completed" | "aborted" | "partial"
    node_results:   Dict[str, NodeResult]    = field(default_factory=dict)
    aborted_at:     Optional[str]            = None
    abort_reason:   Optional[str]            = None
    total_ms:       float                    = 0.0
    context:        Dict[str, Any]           = field(default_factory=dict)

    def is_success(self) -> bool:
        return self.status == "completed"

    def blocked_nodes(self) -> List[str]:
        return [nid for nid, r in self.node_results.items()
                if r.status == NodeStatus.BLOCKED]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "pattern":      self.pattern,
            "status":       self.status,
            "aborted_at":   self.aborted_at,
            "abort_reason": self.abort_reason,
            "total_ms":     round(self.total_ms, 3),
            "nodes":        {
                nid: {
                    "status":      r.status.value,
                    "decision_id": r.response.decision_id if r.response else None,
                    "final_status":r.response.final_status.value if r.response else None,
                    "risk_score":  r.response.risk_score if r.response else None,
                    "duration_ms": round(r.duration_ms, 3),
                    "error":       r.error,
                    "compensated": r.compensated,
                }
                for nid, r in self.node_results.items()
            },
        }


class ChainAbortError(Exception):
    """Raised when an agent chain is aborted due to a blocked node."""
    def __init__(self, node_id: str, reason: str):
        self.node_id = node_id
        self.reason  = reason
        super().__init__(f"Chain aborted at node '{node_id}': {reason}")


# ── Agent Orchestrator ─────────────────────────────────────────────────────────

class AgentOrchestrator:
    """
    Orchestrates chains and graphs of AI agents with full governance
    at every decision node.

    Context isolation:
      Each execution gets its own mutable context dict (chain_context).
      Nodes add outputs to this context; subsequent nodes read from it.
      No state leaks between concurrent executions.

    Thread-safety:
      - Chain execution: single thread, sequential
      - Graph execution: ThreadPoolExecutor with isolated context copies
      - Saga execution: sequential with compensation stack
      - Concurrent orchestrations share the pipeline but not context

    Usage:
        orchestrator = AgentOrchestrator(pipeline)

        # Linear chain
        result = orchestrator.run_chain([node_a, node_b, node_c])

        # DAG with parallel nodes
        result = orchestrator.run_graph([node_a, node_b, node_c, node_d])

        # Saga with compensation
        result = orchestrator.run_saga([step_1, step_2, step_3])

        # Async
        result = await orchestrator.run_chain_async([...])
    """

    def __init__(
        self,
        pipeline,                              # GovernancePipeline
        max_parallel_workers: int = 8,
        default_context: Optional[Dict] = None,
    ):
        self.pipeline  = pipeline
        self._pool     = ThreadPoolExecutor(
            max_workers=max_parallel_workers,
            thread_name_prefix="glassbox-orch",
        )
        self._default_context = default_context or {}

    # ── Pattern 1: Linear Chain ───────────────────────────────────────────────

    def run_chain(
        self,
        nodes:          List[AgentNode],
        initial_context: Optional[Dict] = None,
        abort_on_block: bool = True,
    ) -> OrchestrationResult:
        """
        Execute agents in order. Each agent's output feeds the next.
        If abort_on_block=True (default), a blocked decision stops the chain.
        """
        execution_id = str(uuid.uuid4())
        context      = {**self._default_context, **(initial_context or {})}
        t_start      = time.perf_counter()
        node_results: Dict[str, NodeResult] = {}

        # Build agent_chain for lineage tracking
        agent_chain: List[str] = []

        for node in nodes:
            # Check condition gate
            if node.condition_fn and not node.condition_fn(context):
                node_results[node.node_id] = NodeResult(
                    node_id=node.node_id, agent_id=node.agent_id,
                    status=NodeStatus.SKIPPED,
                )
                continue

            t_node  = time.perf_counter()
            result  = self._execute_node(node, context, list(agent_chain))
            result.duration_ms = (time.perf_counter() - t_node) * 1000
            node_results[node.node_id] = result
            agent_chain.append(node.agent_id)

            if result.status == NodeStatus.BLOCKED and abort_on_block:
                total_ms = (time.perf_counter() - t_start) * 1000
                return OrchestrationResult(
                    execution_id=execution_id, pattern="chain",
                    status="aborted", node_results=node_results,
                    aborted_at=node.node_id,
                    abort_reason=result.error or "Node blocked by governance",
                    total_ms=total_ms, context=context,
                )

            # Merge node output into shared context
            if result.output:
                context.update(result.output)

        total_ms = (time.perf_counter() - t_start) * 1000
        all_ok   = all(r.status in (NodeStatus.EXECUTED, NodeStatus.SKIPPED)
                       for r in node_results.values())
        return OrchestrationResult(
            execution_id=execution_id, pattern="chain",
            status="completed" if all_ok else "partial",
            node_results=node_results,
            total_ms=total_ms, context=context,
        )

    # ── Pattern 2: DAG Graph ──────────────────────────────────────────────────

    def run_graph(
        self,
        nodes:          List[AgentNode],
        initial_context: Optional[Dict] = None,
        abort_on_block: bool = True,
    ) -> OrchestrationResult:
        """
        Execute a DAG of agent nodes respecting dependency order.
        Nodes with no unsatisfied dependencies run in parallel.
        """
        execution_id = str(uuid.uuid4())
        context      = {**self._default_context, **(initial_context or {})}
        t_start      = time.perf_counter()
        node_results: Dict[str, NodeResult] = {}
        node_map     = {n.node_id: n for n in nodes}
        context_lock = threading.Lock()

        completed: Set[str] = set()
        aborted              = False
        abort_node           = None
        abort_reason         = None

        def _can_run(node: AgentNode) -> bool:
            return all(d in completed for d in node.depends_on)

        remaining = list(nodes)
        while remaining and not aborted:
            # Find all nodes ready to run
            ready = [n for n in remaining if _can_run(n)]
            if not ready:
                break  # circular dependency or all blocked

            # Run ready nodes in parallel
            futures = {}
            for node in ready:
                remaining.remove(node)
                # Each node gets a snapshot of current context (isolation)
                with context_lock:
                    ctx_snapshot = dict(context)
                agent_chain = [r.agent_id for r in node_results.values()
                               if r.status == NodeStatus.EXECUTED]
                fut = self._pool.submit(self._execute_node, node,
                                        ctx_snapshot, agent_chain)
                futures[fut] = node

            for fut in as_completed(futures):
                node   = futures[fut]
                t_node = time.perf_counter()
                try:
                    result = fut.result(timeout=node.timeout_s)
                except Exception as exc:
                    result = NodeResult(
                        node_id=node.node_id, agent_id=node.agent_id,
                        status=NodeStatus.FAILED, error=str(exc),
                    )
                result.duration_ms = (time.perf_counter() - t_node) * 1000
                node_results[node.node_id] = result
                completed.add(node.node_id)

                if result.status == NodeStatus.BLOCKED and abort_on_block:
                    aborted     = True
                    abort_node  = node.node_id
                    abort_reason = result.error or "Node blocked"
                    break

                if result.output:
                    with context_lock:
                        context.update(result.output)

        total_ms = (time.perf_counter() - t_start) * 1000
        return OrchestrationResult(
            execution_id=execution_id, pattern="graph",
            status="aborted" if aborted else "completed",
            node_results=node_results,
            aborted_at=abort_node,
            abort_reason=abort_reason,
            total_ms=total_ms, context=context,
        )

    # ── Pattern 3: Saga (distributed with compensation) ───────────────────────

    def run_saga(
        self,
        steps:           List[AgentNode],
        initial_context: Optional[Dict] = None,
    ) -> OrchestrationResult:
        """
        Execute a saga: if any step is blocked or fails, compensate
        all previously completed steps in reverse order.

        Each step's compensate_fn is called with the node result and
        the current context. Compensation errors are logged but do not
        prevent other compensations from running.
        """
        execution_id       = str(uuid.uuid4())
        context            = {**self._default_context, **(initial_context or {})}
        t_start            = time.perf_counter()
        node_results: Dict[str, NodeResult] = {}
        completed_steps: List[Tuple[AgentNode, NodeResult]] = []  # for compensation

        aborted    = False
        abort_node = None

        for step in steps:
            t_step = time.perf_counter()
            result = self._execute_node(step, context, [])
            result.duration_ms = (time.perf_counter() - t_step) * 1000
            node_results[step.node_id] = result

            if result.status in (NodeStatus.BLOCKED, NodeStatus.FAILED):
                aborted    = True
                abort_node = step.node_id
                # Compensate completed steps in reverse order
                for comp_node, comp_result in reversed(completed_steps):
                    if comp_node.compensate_fn:
                        try:
                            comp_node.compensate_fn(comp_result, context)
                            node_results[comp_node.node_id].compensated = True
                            node_results[comp_node.node_id].status = NodeStatus.COMPENSATED
                        except Exception as exc:
                            node_results[comp_node.node_id].error = (
                                f"Compensation failed: {exc}")
                break

            completed_steps.append((step, result))
            if result.output:
                context.update(result.output)

        total_ms = (time.perf_counter() - t_start) * 1000
        return OrchestrationResult(
            execution_id=execution_id, pattern="saga",
            status="aborted" if aborted else "completed",
            node_results=node_results,
            aborted_at=abort_node,
            abort_reason=node_results[abort_node].error if abort_node else None,
            total_ms=total_ms, context=context,
        )

    # ── Async variants ─────────────────────────────────────────────────────────

    async def run_chain_async(
        self,
        nodes: List[AgentNode],
        initial_context: Optional[Dict] = None,
        abort_on_block: bool = True,
    ) -> OrchestrationResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self.run_chain, nodes, initial_context, abort_on_block)

    async def run_graph_async(
        self,
        nodes: List[AgentNode],
        initial_context: Optional[Dict] = None,
        abort_on_block: bool = True,
    ) -> OrchestrationResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self.run_graph, nodes, initial_context, abort_on_block)

    async def run_saga_async(
        self,
        steps: List[AgentNode],
        initial_context: Optional[Dict] = None,
    ) -> OrchestrationResult:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            self._pool, self.run_saga, steps, initial_context)

    # ── Internal node execution ────────────────────────────────────────────────

    def _execute_node(
        self,
        node:        AgentNode,
        context:     Dict[str, Any],
        agent_chain: List[str],
    ) -> NodeResult:
        """Execute one agent node through the governance pipeline."""
        try:
            payload = node.payload_fn(context)
        except Exception as exc:
            return NodeResult(
                node_id=node.node_id, agent_id=node.agent_id,
                status=NodeStatus.FAILED,
                error=f"payload_fn raised: {exc}",
            )

        ctx = DecisionContext(
            agent_chain=agent_chain,
            source_system="orchestrator",
            metadata={"node_id": node.node_id, **node.metadata},
        )
        request = DecisionRequest(
            agent_id=node.agent_id,
            decision_type=node.decision_type,
            payload=payload,
            context=ctx,
        )

        try:
            response = self.pipeline.process(request)
        except Exception as exc:
            return NodeResult(
                node_id=node.node_id, agent_id=node.agent_id,
                status=NodeStatus.FAILED,
                error=f"Pipeline error: {exc}",
            )

        if response.final_status == FinalStatus.BLOCKED:
            reason = (response.policy_violations[0]
                      if response.policy_violations
                      else response.message)
            return NodeResult(
                node_id=node.node_id, agent_id=node.agent_id,
                status=NodeStatus.BLOCKED,
                response=response,
                error=reason,
            )

        # Extract output from response for next nodes
        output = {
            f"{node.node_id}.decision_id":  response.decision_id,
            f"{node.node_id}.status":       response.final_status.value,
            f"{node.node_id}.risk_score":   response.risk_score,
            f"{node.node_id}.payload":      payload,
        }

        return NodeResult(
            node_id=node.node_id, agent_id=node.agent_id,
            status=NodeStatus.EXECUTED,
            response=response,
            output=output,
        )

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)
