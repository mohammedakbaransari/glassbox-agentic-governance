"""
GlassBox — LlamaIndex & CrewAI Adapters  (v1.0.0)
==================================================
Extends glassbox/integrations/adapters.py with purpose-built adapters
for LlamaIndex and CrewAI, following the same transparent governance
wrapping pattern as LangChainAdapter and LangGraphAdapter.

LlamaIndexAdapter:
  Wraps LlamaIndex QueryEngine and BaseTool implementations.
  Every .query() and tool .call() passes through governance before executing.
  Works with VectorStoreIndex, SummaryIndex, KnowledgeGraphIndex, etc.

CrewAIAdapter:
  Wraps CrewAI Task.execute() and BaseTool._run() methods.
  Governs tool use within crew agents and the task execution lifecycle.
  Supports both synchronous and async crew execution.

Both adapters:
  - Zero mandatory dependency on the respective framework
    (imports are lazy — framework need not be installed to import this module)
  - Raise GovernanceBlockedError when a decision is blocked
    (the AI framework handles this through its native error path)
  - Support async variants natively
  - Thread-safe — no shared mutable state per-call

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import asyncio
import functools
import json
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

from glassbox.governance.models import (
    DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)
from glassbox.integrations.adapters import (
    GovernanceBlockedError, _infer_decision_type,
)

if TYPE_CHECKING:
    from glassbox.governance.pipeline import GovernancePipeline


# ══════════════════════════════════════════════════════════════════════════════
# LLAMAINDEX ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class LlamaIndexAdapter:
    """
    Wraps LlamaIndex QueryEngine and BaseTool implementations so that every
    retrieval query and tool invocation is governed by GlassBox.

    LlamaIndex introduces two governance points:
      1. Query-time governance: before the query engine retrieves and synthesises
         an answer, GlassBox evaluates whether the query should be permitted.
      2. Tool-call governance: when an agent invokes a LlamaIndex BaseTool,
         GlassBox evaluates the tool call before the tool runs.

    Usage:
        from glassbox.integrations.extended_adapters import LlamaIndexAdapter

        pipeline = GovernancePipeline()
        adapter  = LlamaIndexAdapter(pipeline, agent_id="llamaindex_agent")

        # Wrap a QueryEngine (index.as_query_engine())
        governed_engine = adapter.wrap_query_engine(query_engine)
        response = governed_engine.query("What is the maximum safe dose?")

        # Wrap a list of LlamaIndex tools
        governed_tools = adapter.wrap_tools([tool1, tool2])

    If governance blocks a query or tool call, GovernanceBlockedError is raised.
    LlamaIndex catches tool errors through its AgentRunner error handling.
    """

    def __init__(
        self,
        pipeline:          "GovernancePipeline",
        agent_id:          str = "llamaindex_agent",
        decision_type_map: Optional[Dict[str, DecisionType]] = None,
        confidence:        float = 1.0,
        query_decision_type: DecisionType = DecisionType.CUSTOM,
    ):
        self.pipeline           = pipeline
        self.agent_id           = agent_id
        self.decision_type_map  = decision_type_map or {}
        self.confidence         = confidence
        self.query_decision_type = query_decision_type

    def wrap_query_engine(self, engine) -> Any:
        """
        Wrap a LlamaIndex QueryEngine so every .query() call is governed.

        The governance payload includes the query string and query_engine type.
        Blocking the query prevents retrieval from occurring.
        """
        adapter = self

        original_query = engine.query

        @functools.wraps(original_query)
        def governed_query(query_str, **kwargs):
            adapter._govern_query(query_str)
            return original_query(query_str, **kwargs)

        @functools.wraps(original_query)
        async def governed_aquery(query_str, **kwargs):
            adapter._govern_query(query_str)
            if asyncio.iscoroutinefunction(original_query):
                return await original_query(query_str, **kwargs)
            return original_query(query_str, **kwargs)

        engine.query = governed_query
        if hasattr(engine, "aquery"):
            engine.aquery = governed_aquery

        return engine

    def wrap_tools(self, tools: List) -> List:
        """Wrap LlamaIndex BaseTool instances with governance."""
        return [self._wrap_tool(t) for t in tools]

    def _wrap_tool(self, tool) -> Any:
        adapter = self
        name    = getattr(tool, "metadata", None)
        if name:
            name = getattr(name, "name", str(tool.__class__.__name__))
        else:
            name = str(tool.__class__.__name__)

        original_call = getattr(tool, "__call__", None) or getattr(tool, "call", None)
        if original_call is None:
            return tool   # cannot wrap

        @functools.wraps(original_call)
        def governed_call(*args, **kwargs):
            input_val = args[0] if args else kwargs.get("input", str(kwargs))
            payload   = {"tool_name": name, "input": str(input_val)[:2000]}
            dtype     = adapter.decision_type_map.get(name) or _infer_decision_type(name)
            ctx       = DecisionContext(confidence=adapter.confidence,
                                        source_system="llamaindex")
            request   = DecisionRequest(
                agent_id=adapter.agent_id, decision_type=dtype,
                payload=payload, context=ctx,
            )
            response  = adapter.pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    name, response.policy_violations, response.decision_id)
            return original_call(*args, **kwargs)

        if hasattr(tool, "__call__"):
            tool.__call__ = governed_call
        else:
            tool.call = governed_call

        return tool

    def _govern_query(self, query_str: str):
        """Governance check for a query string before retrieval."""
        payload  = {"query": str(query_str)[:4096],
                    "engine_type": "llamaindex_query_engine"}
        ctx      = DecisionContext(confidence=self.confidence,
                                   source_system="llamaindex_query")
        request  = DecisionRequest(
            agent_id=self.agent_id, decision_type=self.query_decision_type,
            payload=payload, context=ctx,
        )
        response = self.pipeline.process(request)
        if response.final_status == FinalStatus.BLOCKED:
            raise GovernanceBlockedError(
                "query_engine", response.policy_violations, response.decision_id)

    async def wrap_query_engine_async(self, engine) -> Any:
        """Return an async-first governed query engine wrapper."""
        return self.wrap_query_engine(engine)


# ══════════════════════════════════════════════════════════════════════════════
# CREWAI ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class CrewAIAdapter:
    """
    Wraps CrewAI Task execution and BaseTool invocations with GlassBox governance.

    CrewAI governance operates at two levels:
      1. Task execution: before a Task's expected_output is generated, GlassBox
         evaluates whether the task's action is permitted given its description
         and assigned agent.
      2. Tool invocations: every tool._run() call from a CrewAI agent is
         governed before execution.

    Usage:
        from glassbox.integrations.extended_adapters import CrewAIAdapter

        pipeline = GovernancePipeline()
        adapter  = CrewAIAdapter(pipeline, agent_id="crew_agent")

        # Wrap tools for a CrewAI agent
        governed_tools = adapter.wrap_tools([search_tool, procurement_tool])

        # Create agent with governed tools
        agent = Agent(role="Procurement Specialist",
                      tools=governed_tools, ...)

        # Wrap a task to govern its execution
        governed_task = adapter.wrap_task(procurement_task)

    GovernanceBlockedError is raised when blocked.
    CrewAI handles tool errors through its ToolUsageErrorHandler.
    """

    def __init__(
        self,
        pipeline:          "GovernancePipeline",
        agent_id:          str = "crewai_agent",
        decision_type_map: Optional[Dict[str, DecisionType]] = None,
        confidence:        float = 1.0,
    ):
        self.pipeline          = pipeline
        self.agent_id          = agent_id
        self.decision_type_map = decision_type_map or {}
        self.confidence        = confidence

    def wrap_tools(self, tools: List) -> List:
        """Wrap CrewAI BaseTool instances with governance."""
        return [self._wrap_crewai_tool(t) for t in tools]

    def wrap_task(self, task) -> Any:
        """
        Wrap a CrewAI Task so its execute() method is governed.
        The task description and agent role are used to build the payload.
        """
        adapter = self

        original_execute = getattr(task, "execute", None)
        if original_execute is None:
            return task

        @functools.wraps(original_execute)
        def governed_execute(*args, **kwargs):
            description = getattr(task, "description", "")
            agent_role  = ""
            if hasattr(task, "agent") and task.agent:
                agent_role = getattr(task.agent, "role", "")
            payload = {
                "task_description": str(description)[:2000],
                "agent_role":       str(agent_role),
                "task_type":        "crewai_task",
            }
            dtype   = _infer_decision_type(description)
            ctx     = DecisionContext(confidence=adapter.confidence,
                                      source_system="crewai_task")
            request = DecisionRequest(
                agent_id=adapter.agent_id, decision_type=dtype,
                payload=payload, context=ctx,
            )
            response = adapter.pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    "crewai_task", response.policy_violations, response.decision_id)
            return original_execute(*args, **kwargs)

        task.execute = governed_execute
        return task

    def _wrap_crewai_tool(self, tool) -> Any:
        """Wrap a single CrewAI BaseTool's _run method."""
        adapter = self

        # CrewAI tools expose _run(tool_input: str)
        original_run = getattr(tool, "_run", None)
        if original_run is None:
            return tool

        tool_name = getattr(tool, "name", tool.__class__.__name__)

        @functools.wraps(original_run)
        def governed_run(tool_input: str = "", **kwargs):
            try:
                payload_data = json.loads(tool_input) if tool_input.startswith("{") else {}
            except (json.JSONDecodeError, AttributeError):
                payload_data = {}
            payload = {
                "tool_name":  tool_name,
                "tool_input": str(tool_input)[:2000],
                **payload_data,
            }
            dtype   = adapter.decision_type_map.get(tool_name) or _infer_decision_type(tool_name)
            ctx     = DecisionContext(confidence=adapter.confidence,
                                      source_system="crewai_tool")
            request = DecisionRequest(
                agent_id=adapter.agent_id, decision_type=dtype,
                payload=payload, context=ctx,
            )
            response = adapter.pipeline.process(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    tool_name, response.policy_violations, response.decision_id)
            return original_run(tool_input, **kwargs)

        @functools.wraps(original_run)
        async def governed_arun(tool_input: str = "", **kwargs):
            try:
                payload_data = json.loads(tool_input) if tool_input.startswith("{") else {}
            except (json.JSONDecodeError, AttributeError):
                payload_data = {}
            payload = {
                "tool_name":  tool_name,
                "tool_input": str(tool_input)[:2000],
                **payload_data,
            }
            dtype    = adapter.decision_type_map.get(tool_name) or _infer_decision_type(tool_name)
            ctx      = DecisionContext(confidence=adapter.confidence,
                                       source_system="crewai_tool_async")
            request  = DecisionRequest(
                agent_id=adapter.agent_id, decision_type=dtype,
                payload=payload, context=ctx,
            )
            response = await adapter.pipeline.process_async(request)
            if response.final_status == FinalStatus.BLOCKED:
                raise GovernanceBlockedError(
                    tool_name, response.policy_violations, response.decision_id)
            if asyncio.iscoroutinefunction(original_run):
                return await original_run(tool_input, **kwargs)
            return original_run(tool_input, **kwargs)

        tool._run  = governed_run
        if hasattr(tool, "_arun"):
            tool._arun = governed_arun

        return tool


# ══════════════════════════════════════════════════════════════════════════════
# OPENAI AGENTS SDK ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class OpenAIAgentsAdapter:
    """
    Wraps OpenAI Agents SDK tool functions with GlassBox governance.

    The OpenAI Agents SDK exposes tools as decorated functions.
    This adapter wraps those functions so every tool invocation is
    governed before the function body executes.

    Usage:
        from glassbox.integrations.extended_adapters import OpenAIAgentsAdapter

        adapter = OpenAIAgentsAdapter(pipeline, agent_id="openai_agent")

        @adapter.govern(decision_type=DecisionType.PROCUREMENT)
        def place_order(supplier_id: str, amount: float) -> dict:
            ...  # governed before this runs

        # Also wrap existing function tools
        governed_tools = adapter.wrap_functions([existing_fn1, existing_fn2])
    """

    def __init__(
        self,
        pipeline:  "GovernancePipeline",
        agent_id:  str = "openai_agent",
        confidence: float = 1.0,
    ):
        self.pipeline   = pipeline
        self.agent_id   = agent_id
        self.confidence = confidence

    def govern(self, decision_type: DecisionType = DecisionType.CUSTOM):
        """Decorator: add governance to any function tool."""
        def decorator(fn):
            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                payload = {"function_name": fn.__name__, **kwargs}
                if args:
                    payload["args"] = list(args)
                ctx     = DecisionContext(confidence=self.confidence,
                                          source_system="openai_agents")
                request = DecisionRequest(
                    agent_id=self.agent_id, decision_type=decision_type,
                    payload=payload, context=ctx,
                )
                response = self.pipeline.process(request)
                if response.final_status == FinalStatus.BLOCKED:
                    raise GovernanceBlockedError(
                        fn.__name__, response.policy_violations, response.decision_id)
                return fn(*args, **kwargs)

            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                payload  = {"function_name": fn.__name__, **kwargs}
                ctx      = DecisionContext(confidence=self.confidence,
                                           source_system="openai_agents_async")
                request  = DecisionRequest(
                    agent_id=self.agent_id, decision_type=decision_type,
                    payload=payload, context=ctx,
                )
                response = await self.pipeline.process_async(request)
                if response.final_status == FinalStatus.BLOCKED:
                    raise GovernanceBlockedError(
                        fn.__name__, response.policy_violations, response.decision_id)
                import asyncio
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)

            import asyncio
            return async_wrapper if asyncio.iscoroutinefunction(fn) else wrapper
        return decorator

    def wrap_functions(self, functions: list) -> list:
        """Wrap a list of function tools with governance (CUSTOM decision type)."""
        return [self.govern()(fn) for fn in functions]


# ══════════════════════════════════════════════════════════════════════════════
# PYDANTIC AI ADAPTER
# ══════════════════════════════════════════════════════════════════════════════

class PydanticAIAdapter:
    """
    Wraps PydanticAI tool functions with GlassBox governance.

    PydanticAI uses typed Pydantic models for tool inputs/outputs.
    This adapter intercepts tool calls and governs them before execution,
    extracting payload from the Pydantic model's dict representation.

    Usage:
        from glassbox.integrations.extended_adapters import PydanticAIAdapter

        adapter = PydanticAIAdapter(pipeline, agent_id="pydantic_agent")

        # Wrap a PydanticAI tool function
        @adapter.govern(decision_type=DecisionType.FINANCIAL)
        async def transfer_funds(model: TransferRequest) -> TransferResult:
            ...  # governed before this runs

        # Wrap a list of tools
        governed = adapter.wrap_tools([tool1, tool2])
    """

    def __init__(
        self,
        pipeline:  "GovernancePipeline",
        agent_id:  str   = "pydantic_agent",
        confidence: float = 1.0,
    ):
        self.pipeline   = pipeline
        self.agent_id   = agent_id
        self.confidence = confidence

    def govern(self, decision_type: DecisionType = DecisionType.CUSTOM):
        """Decorator: add governance to any PydanticAI tool function."""
        def decorator(fn):
            @functools.wraps(fn)
            async def async_wrapper(*args, **kwargs):
                # Extract payload from Pydantic model args or kwargs
                payload = {"function_name": fn.__name__}
                for arg in args:
                    if hasattr(arg, "model_dump"):
                        payload.update(arg.model_dump())
                    elif hasattr(arg, "dict"):
                        payload.update(arg.dict())
                payload.update({k: (v.model_dump() if hasattr(v, "model_dump") else v)
                                for k, v in kwargs.items()})

                ctx     = DecisionContext(confidence=self.confidence,
                                          source_system="pydanticai")
                request = DecisionRequest(
                    agent_id=self.agent_id, decision_type=decision_type,
                    payload=payload, context=ctx,
                )
                response = await self.pipeline.process_async(request)
                if response.final_status == FinalStatus.BLOCKED:
                    raise GovernanceBlockedError(
                        fn.__name__, response.policy_violations, response.decision_id)

                import asyncio
                if asyncio.iscoroutinefunction(fn):
                    return await fn(*args, **kwargs)
                return fn(*args, **kwargs)

            @functools.wraps(fn)
            def sync_wrapper(*args, **kwargs):
                payload = {"function_name": fn.__name__}
                for arg in args:
                    if hasattr(arg, "model_dump"):
                        payload.update(arg.model_dump())
                    elif hasattr(arg, "dict"):
                        payload.update(arg.dict())
                payload.update({k: (v.model_dump() if hasattr(v, "model_dump") else v)
                                for k, v in kwargs.items()})
                ctx     = DecisionContext(confidence=self.confidence,
                                          source_system="pydanticai_sync")
                request = DecisionRequest(
                    agent_id=self.agent_id, decision_type=decision_type,
                    payload=payload, context=ctx,
                )
                response = self.pipeline.process(request)
                if response.final_status == FinalStatus.BLOCKED:
                    raise GovernanceBlockedError(
                        fn.__name__, response.policy_violations, response.decision_id)
                return fn(*args, **kwargs)

            import asyncio
            return async_wrapper if asyncio.iscoroutinefunction(fn) else sync_wrapper
        return decorator

    def wrap_tools(self, tools: list) -> list:
        """Wrap a list of tool callables with governance."""
        return [self.govern()(t) for t in tools]
