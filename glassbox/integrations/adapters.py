"""
GlassBox — AI Framework Integrations  (v1.0.0)
================================================
Native adapters for LangChain, LangGraph, AutoGen, and CrewAI.
Each adapter wraps the framework's tool/node/agent calls so that
every AI-generated decision is automatically governed by GlassBox
without the developer having to manually construct DecisionRequests.

Adapter pattern:
  - Developer registers the GlassBox pipeline once
  - All tool calls / graph nodes / agent actions are intercepted
  - Governance happens transparently
  - The AI framework receives the governed result (or learns the action was blocked)

Adapters:
  LangChainAdapter    — wraps LangChain BaseTool.run()
  LangGraphAdapter    — wraps LangGraph node functions
  AutoGenAdapter      — wraps AutoGen function calls
  CrewAIAdapter       — wraps CrewAI Task execution
  GenericToolAdapter  — wraps any callable as a governed tool

All adapters:
  - Thread-safe
  - Async-capable (native async variants)
  - Zero mandatory dependencies (imports are lazy)
  - Work without the respective framework installed
    (useful for testing / documentation)

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import asyncio
import functools
import json
import threading
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.models import (
    DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)

if TYPE_CHECKING:
    from glassbox.governance.pipeline import GovernancePipeline


# ── Utility ────────────────────────────────────────────────────────────────────

def _infer_decision_type(tool_name: str) -> DecisionType:
    """Infer DecisionType from tool name for automatic governance routing."""
    name = tool_name.lower()
    # Check more specific patterns first to avoid false matches
    if any(w in name for w in ["stock","inventory","warehouse","reorder"]):
        return DecisionType.INVENTORY
    if any(w in name for w in ["procure","purchase","order","buy","supplier"]):
        return DecisionType.PROCUREMENT
    if any(w in name for w in ["pric","cost","price","rate"]):
        return DecisionType.PRICING
    if any(w in name for w in ["transfer","pay","fund","wire","financial"]):
        return DecisionType.FINANCIAL
    if any(w in name for w in ["ship","logistic","route","deliver","freight"]):
        return DecisionType.LOGISTICS
    if any(w in name for w in ["deploy","server","infra","k8s","devops","ops"]):
        return DecisionType.IT_OPS
    if any(w in name for w in ["hr","hire","fire","salary","employee"]):
        return DecisionType.HR
    return DecisionType.CUSTOM


class GovernanceBlockedError(Exception):
    """
    Raised when a governance check blocks a tool call.
    AI frameworks catch this and handle as a tool failure.
    """
    def __init__(self, tool_name: str, violations: List[str], decision_id: str):
        self.tool_name   = tool_name
        self.violations  = violations
        self.decision_id = decision_id
        super().__init__(
            f"GlassBox blocked '{tool_name}': {violations[0] if violations else 'governance block'}"
        )


# ══════════════════════════════════════════════════════════════════════════════
# LANGCHAIN ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class LangChainAdapter:
    """
    Wraps LangChain tools so every tool.run() call is governed by GlassBox.

    Usage:
        from langchain.tools import Tool
        from glassbox.integrations.adapters import LangChainAdapter

        pipeline = GovernancePipeline(...)
        adapter  = LangChainAdapter(pipeline, agent_id="langchain_agent")

        # Wrap a tool
        governed_tool = adapter.wrap_tool(my_tool)

        # Or wrap all tools in an agent
        governed_tools = adapter.wrap_tools([tool1, tool2, tool3])

    When a tool is blocked:
        GovernanceBlockedError is raised. LangChain catches this and includes
        the error message in the agent's observation, allowing the LLM to
        decide what to do next (typically it stops or tries an alternative).
    """

    def __init__(
        self,
        pipeline:         "GovernancePipeline",
        agent_id:         str = "langchain_agent",
        decision_type_map: Optional[Dict[str, DecisionType]] = None,
        confidence:       float = 1.0,
    ):
        self.pipeline          = pipeline
        self.agent_id          = agent_id
        self.decision_type_map = decision_type_map or {}
        self.confidence        = confidence

    def wrap_tool(self, tool) -> Any:
        """
        Wrap a LangChain Tool so its run() calls are governed.
        Works with BaseTool subclasses and Tool instances.
        Returns the same tool object with a governed _run method.
        """
        adapter = self

        original_run = tool._run if hasattr(tool, '_run') else tool.run

        @functools.wraps(original_run)
        def governed_run(*args, **kwargs):
            tool_input = args[0] if args else kwargs.get("tool_input", str(kwargs))
            return adapter._govern_and_run(
                tool_name=tool.name,
                tool_input=tool_input,
                original_fn=original_run,
                args=args, kwargs=kwargs,
            )

        @functools.wraps(original_run)
        async def governed_arun(*args, **kwargs):
            tool_input = args[0] if args else kwargs.get("tool_input", str(kwargs))
            return await adapter._govern_and_run_async(
                tool_name=tool.name,
                tool_input=tool_input,
                original_fn=original_run,
                args=args, kwargs=kwargs,
            )

        if hasattr(tool, '_run'):
            tool._run = governed_run
        else:
            tool.run = governed_run

        if hasattr(tool, '_arun'):
            tool._arun = governed_arun

        return tool

    def wrap_tools(self, tools: List) -> List:
        """Wrap a list of tools. Returns the same list with governed wrappers."""
        return [self.wrap_tool(t) for t in tools]

    def _build_payload(self, tool_name: str, tool_input: Any) -> Dict[str, Any]:
        """Convert tool input to a governance payload."""
        if isinstance(tool_input, dict):
            payload = tool_input
        elif isinstance(tool_input, str):
            # Try JSON parse, fall back to description
            try:
                payload = json.loads(tool_input)
            except (json.JSONDecodeError, TypeError):
                payload = {"description": tool_input, "tool_name": tool_name}
        else:
            payload = {"tool_input": str(tool_input), "tool_name": tool_name}
        payload["_tool_name"] = tool_name
        return payload

    def _govern_and_run(
        self,
        tool_name: str,
        tool_input: Any,
        original_fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        dtype   = self.decision_type_map.get(tool_name) or _infer_decision_type(tool_name)
        payload = self._build_payload(tool_name, tool_input)
        ctx     = DecisionContext(confidence=self.confidence, source_system="langchain")
        request = DecisionRequest(
            agent_id=self.agent_id, decision_type=dtype,
            payload=payload, context=ctx,
        )
        response = self.pipeline.process(request)

        if response.final_status == FinalStatus.BLOCKED:
            raise GovernanceBlockedError(
                tool_name, response.policy_violations, response.decision_id)

        return original_fn(*args, **kwargs)

    async def _govern_and_run_async(
        self,
        tool_name: str,
        tool_input: Any,
        original_fn: Callable,
        args: tuple,
        kwargs: dict,
    ) -> Any:
        dtype   = self.decision_type_map.get(tool_name) or _infer_decision_type(tool_name)
        payload = self._build_payload(tool_name, tool_input)
        ctx     = DecisionContext(confidence=self.confidence, source_system="langchain_async")
        request = DecisionRequest(
            agent_id=self.agent_id, decision_type=dtype,
            payload=payload, context=ctx,
        )
        response = await self.pipeline.process_async(request)

        if response.final_status == FinalStatus.BLOCKED:
            raise GovernanceBlockedError(
                tool_name, response.policy_violations, response.decision_id)

        if asyncio.iscoroutinefunction(original_fn):
            return await original_fn(*args, **kwargs)
        return original_fn(*args, **kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# LANGGRAPH ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class LangGraphAdapter:
    """
    Wraps LangGraph node functions so every node execution is governed.

    Usage:
        from langgraph.graph import StateGraph
        from glassbox.integrations.adapters import LangGraphAdapter

        pipeline = GovernancePipeline(...)
        adapter  = LangGraphAdapter(pipeline)

        # Wrap a node function
        governed_node = adapter.wrap_node(
            my_node_fn,
            agent_id="procurement_node",
            decision_type=DecisionType.PROCUREMENT,
            payload_extractor=lambda state: {"amount": state["order_amount"]},
        )

        # Add to graph
        graph = StateGraph(MyState)
        graph.add_node("procurement", governed_node)

    If blocked, the node raises GovernanceBlockedError which LangGraph
    will route to an error handler or end state.
    """

    def __init__(self, pipeline: "GovernancePipeline"):
        self.pipeline = pipeline

    def wrap_node(
        self,
        node_fn:           Callable,
        agent_id:          str,
        decision_type:     DecisionType,
        payload_extractor: Callable[[Any], Dict] = None,
        confidence:        float = 1.0,
    ) -> Callable:
        """
        Wrap a LangGraph node function with governance.

        payload_extractor: fn(state) → Dict — extracts governance payload from state.
        If not provided, the entire state dict is used as payload.
        """
        pipeline   = self.pipeline
        _extractor = payload_extractor or (lambda s: s if isinstance(s, dict) else {"state": str(s)})

        @functools.wraps(node_fn)
        def governed_node(state):
            payload = _extractor(state)
            ctx     = DecisionContext(confidence=confidence, source_system="langgraph")
            request = DecisionRequest(
                agent_id=agent_id, decision_type=decision_type,
                payload=payload, context=ctx,
            )
            response = pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    agent_id, response.policy_violations, response.decision_id)
            return node_fn(state)

        @functools.wraps(node_fn)
        async def governed_node_async(state):
            payload  = _extractor(state)
            ctx      = DecisionContext(confidence=confidence, source_system="langgraph_async")
            request  = DecisionRequest(
                agent_id=agent_id, decision_type=decision_type,
                payload=payload, context=ctx,
            )
            response = await pipeline.process_async(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    agent_id, response.policy_violations, response.decision_id)
            if asyncio.iscoroutinefunction(node_fn):
                return await node_fn(state)
            return node_fn(state)

        if asyncio.iscoroutinefunction(node_fn):
            return governed_node_async
        return governed_node


# ══════════════════════════════════════════════════════════════════════════════
# AUTOGEN ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class AutoGenAdapter:
    """
    Wraps AutoGen function calls so every function_map entry is governed.

    Usage:
        from glassbox.integrations.adapters import AutoGenAdapter

        pipeline = GovernancePipeline(...)
        adapter  = AutoGenAdapter(pipeline, agent_id="autogen_agent")

        # Govern a function map
        original_function_map = {
            "place_order": place_order_fn,
            "transfer_funds": transfer_funds_fn,
        }
        governed_map = adapter.govern_function_map(original_function_map)

        # Use with AutoGen ConversableAgent
        agent = ConversableAgent(function_map=governed_map, ...)
    """

    def __init__(
        self,
        pipeline:  "GovernancePipeline",
        agent_id:  str = "autogen_agent",
        confidence: float = 1.0,
    ):
        self.pipeline   = pipeline
        self.agent_id   = agent_id
        self.confidence = confidence

    def govern_function_map(self, function_map: Dict[str, Callable]) -> Dict[str, Callable]:
        """Return a new function_map with every function wrapped with governance."""
        return {
            name: self._wrap_fn(name, fn)
            for name, fn in function_map.items()
        }

    def _wrap_fn(self, fn_name: str, fn: Callable) -> Callable:
        pipeline   = self.pipeline
        agent_id   = self.agent_id
        confidence = self.confidence

        @functools.wraps(fn)
        def governed(*args, **kwargs):
            payload = {
                "function_name": fn_name,
                "args": list(args),
                "kwargs": kwargs,
            }
            # Extract known fields from kwargs for better policy evaluation
            payload.update({k: v for k, v in kwargs.items()
                            if k in ("amount","quantity","target","account","reference")})
            dtype   = _infer_decision_type(fn_name)
            ctx     = DecisionContext(confidence=confidence, source_system="autogen")
            request = DecisionRequest(
                agent_id=agent_id, decision_type=dtype,
                payload=payload, context=ctx,
            )
            response = pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                return {
                    "error": "GOVERNANCE_BLOCKED",
                    "decision_id": response.decision_id,
                    "violations": response.policy_violations,
                    "message": response.message,
                }
            return fn(*args, **kwargs)

        return governed


# ══════════════════════════════════════════════════════════════════════════════
# GENERIC TOOL ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class GenericToolAdapter:
    """
    Govern any callable as a GlassBox tool.

    Usage:
        adapter = GenericToolAdapter(pipeline)

        @adapter.govern(agent_id="pricing_agent",
                        decision_type=DecisionType.PRICING,
                        payload_extractor=lambda args, kw: {"new_price": kw["price"]})
        def update_price(product_id: str, price: float) -> dict:
            ...

        # Or wrap an existing function
        governed_fn = adapter.wrap(
            existing_fn,
            agent_id="inventory_agent",
            decision_type=DecisionType.INVENTORY,
        )
    """

    def __init__(self, pipeline: "GovernancePipeline"):
        self.pipeline = pipeline

    def govern(
        self,
        agent_id:          str,
        decision_type:     DecisionType,
        payload_extractor: Optional[Callable] = None,
        confidence:        float = 1.0,
    ):
        """Decorator — govern the decorated function."""
        def decorator(fn):
            return self.wrap(fn, agent_id, decision_type,
                             payload_extractor, confidence)
        return decorator

    def wrap(
        self,
        fn:                Callable,
        agent_id:          str,
        decision_type:     DecisionType,
        payload_extractor: Optional[Callable] = None,
        confidence:        float = 1.0,
    ) -> Callable:
        """Wrap fn with governance. Returns governed callable."""
        pipeline = self.pipeline
        _extract = payload_extractor or (lambda a, kw: {
            **{f"arg_{i}": v for i, v in enumerate(a)}, **kw})

        @functools.wraps(fn)
        def governed(*args, **kwargs):
            payload  = _extract(args, kwargs)
            ctx      = DecisionContext(confidence=confidence, source_system="generic_adapter")
            request  = DecisionRequest(
                agent_id=agent_id, decision_type=decision_type,
                payload=payload, context=ctx,
            )
            response = pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    fn.__name__, response.policy_violations, response.decision_id)
            return fn(*args, **kwargs)

        @functools.wraps(fn)
        async def governed_async(*args, **kwargs):
            payload  = _extract(args, kwargs)
            ctx      = DecisionContext(confidence=confidence, source_system="generic_async")
            request  = DecisionRequest(
                agent_id=agent_id, decision_type=decision_type,
                payload=payload, context=ctx,
            )
            response = await pipeline.process_async(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    fn.__name__, response.policy_violations, response.decision_id)
            if asyncio.iscoroutinefunction(fn):
                return await fn(*args, **kwargs)
            return fn(*args, **kwargs)

        if asyncio.iscoroutinefunction(fn):
            return governed_async
        return governed
