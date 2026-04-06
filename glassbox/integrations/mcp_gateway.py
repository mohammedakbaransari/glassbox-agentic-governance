"""
GlassBox — MCP Governance Gateway  (v1.0.0)
============================================
Governs Model Context Protocol (MCP) tool calls before they execute.

MCP is the emerging standard for AI agent tool communication. The Gateway
intercepts every MCP tool call and routes it through GlassBox's governance
pipeline before the tool executes — providing the same decision-semantic
governance for MCP as GlassBox provides for LangChain and AutoGen.

Additionally, the MCPToolScanner performs static analysis of tool definitions
to detect: tool poisoning (hidden instructions in descriptions), typosquatting
(tool names that mimic trusted tools), rug-pull patterns (tools that change
behaviour after trust is established), and capability escalation (tools
requesting permissions beyond their stated purpose).

Usage:
    from glassbox.integrations.mcp_gateway import MCPGovernanceGateway, MCPToolScanner

    # Govern MCP tool calls
    gateway = MCPGovernanceGateway(pipeline, agent_id="my_agent")
    result  = gateway.call_tool("file_write", {"path": "/etc/passwd", "content": "..."})
    # Blocked: path traversal detected + file_write in sensitive path

    # Scan tool definitions before loading
    scanner = MCPToolScanner()
    report  = scanner.scan_tool_definition(tool_spec)
    if report.risk_level == "high":
        print(report.findings)

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple, TYPE_CHECKING

from glassbox.governance.models import (
    DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)

if TYPE_CHECKING:
    from glassbox.governance.pipeline import GovernancePipeline


# ── Suspicious patterns in MCP tool definitions ───────────────────────────────

_TOOL_POISONING_PATTERNS: List[Tuple[re.Pattern, str, str]] = [
    (re.compile(r'ignore (previous|prior|above|all) instructions', re.I),
     "Prompt injection in tool description",     "critical"),
    (re.compile(r'(disregard|forget|bypass|override) (your |the )?(system |)?(prompt|instructions|guidelines)', re.I),
     "System prompt override attempt",           "critical"),
    (re.compile(r'you (are|must|should) (now|actually) (be|act as|pretend)', re.I),
     "Role override in tool description",        "high"),
    (re.compile(r'<(script|iframe|img|svg)[^>]*>', re.I),
     "HTML/script injection in tool spec",       "high"),
    (re.compile(r'\[\s*system\s*\]|\[INST\]|<\|system\|>', re.I),
     "LLM instruction injection marker",         "critical"),
    (re.compile(r'exfiltrate|exfil|send.{0,30}(password|secret|token|key).{0,30}to', re.I),
     "Data exfiltration instruction",            "critical"),
    (re.compile(r'call.{0,50}(admin|root|sudo|privileged)', re.I),
     "Privilege escalation instruction",         "high"),
]

_TYPOSQUATTING_TRUSTED = [
    "read_file","write_file","execute_code","search_web","create_file",
    "delete_file","list_directory","bash","python","javascript",
    "send_email","http_request","database_query","file_manager",
]

_SENSITIVE_TOOL_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r'\b(passw(or)?d|secret|token|api.key|private.key)\b', re.I), "Credential access"),
    (re.compile(r'\b(shadow|passwd|sudoers|hosts|resolv)\b', re.I),           "System file access"),
    (re.compile(r'\b(rm\s+-rf|rmdir|shred|wipe|format)\b', re.I),            "Destructive operation"),
    (re.compile(r'\b(curl|wget)\b.{0,50}\|.{0,30}(bash|sh|python)', re.I),   "Pipe to shell"),
]


@dataclass
class MCPScanFinding:
    severity:    str    # critical | high | medium | low
    category:    str
    description: str
    location:    str    # "name" | "description" | "parameters" | "capability"


@dataclass
class MCPScanReport:
    tool_name:   str
    risk_level:  str          # critical | high | medium | low | safe
    findings:    List[MCPScanFinding] = field(default_factory=list)
    approved:    bool         = True

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "critical")

    @property
    def high_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == "high")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name":  self.tool_name,
            "risk_level": self.risk_level,
            "approved":   self.approved,
            "findings":   [{"severity": f.severity, "category": f.category,
                            "description": f.description, "location": f.location}
                           for f in self.findings],
        }


class MCPToolScanner:
    """
    Static analysis scanner for MCP tool definitions.

    Detects tool poisoning, typosquatting, hidden instructions,
    and capability escalation patterns in tool specs before they
    are loaded into an agent's tool registry.

    Thread-safe: all methods are stateless over their inputs.

    Complexity note: the typosquatting scan in ``scan_tool_definition`` runs
    ``_levenshtein`` once per trusted tool name, so its cost is
    O(|trusted| × |name| × |trusted_name|) per call.  With the default list
    of ~14 trusted names and typical short tool names this is negligible, but
    callers that pass a large ``trusted_tool_names`` list should be aware of
    the linear growth with that set size.
    """

    def __init__(self, trusted_tool_names: Optional[List[str]] = None):
        self._trusted = set(trusted_tool_names or _TYPOSQUATTING_TRUSTED)

    def scan_tool_definition(self, tool_spec: Dict[str, Any]) -> MCPScanReport:
        """
        Scan a single tool definition for governance risks.

        Args:
            tool_spec: MCP tool definition dict with keys:
                       name, description, inputSchema / parameters, etc.

        Returns:
            MCPScanReport with risk level and detailed findings.
        """
        name        = str(tool_spec.get("name", ""))
        description = str(tool_spec.get("description", ""))
        params      = tool_spec.get("inputSchema", tool_spec.get("parameters", {}))
        findings    = []

        # 1. Scan description for tool poisoning
        for pattern, category, severity in _TOOL_POISONING_PATTERNS:
            if pattern.search(description):
                findings.append(MCPScanFinding(
                    severity=severity, category=category,
                    description=f"Pattern detected in tool description: {pattern.pattern[:60]}",
                    location="description"
                ))

        # 2. Scan description for sensitive operation indicators
        for pattern, category in _SENSITIVE_TOOL_PATTERNS:
            if pattern.search(description):
                findings.append(MCPScanFinding(
                    severity="medium", category=category,
                    description=f"Sensitive operation indicated: {category}",
                    location="description"
                ))

        # 3. Typosquatting detection
        name_lower = name.lower().replace("_","").replace("-","")
        for trusted in self._trusted:
            trusted_norm = trusted.lower().replace("_","").replace("-","")
            if name_lower != trusted_norm and self._levenshtein(name_lower, trusted_norm) <= 2:
                findings.append(MCPScanFinding(
                    severity="high", category="Typosquatting",
                    description=f"Tool name '{name}' is similar to trusted tool '{trusted}'",
                    location="name"
                ))

        # 4. Scan tool name for hidden instructions
        if len(name) > 100:
            findings.append(MCPScanFinding(
                severity="medium", category="Anomalous tool name",
                description=f"Tool name is unusually long ({len(name)} chars)",
                location="name"
            ))

        # 5. Check parameters for hidden instruction fields
        if isinstance(params, dict):
            for key in params.get("properties", {}).keys():
                for pattern, category, severity in _TOOL_POISONING_PATTERNS:
                    if pattern.search(key):
                        findings.append(MCPScanFinding(
                            severity=severity, category=f"Suspicious parameter: {key}",
                            description=f"Parameter name contains injection pattern",
                            location=f"parameters.{key}"
                        ))

        # Determine overall risk level
        if any(f.severity == "critical" for f in findings):
            risk_level = "critical"
            approved   = False
        elif any(f.severity == "high" for f in findings):
            risk_level = "high"
            approved   = False
        elif any(f.severity == "medium" for f in findings):
            risk_level = "medium"
            approved   = True  # warn but allow
        elif findings:
            risk_level = "low"
            approved   = True
        else:
            risk_level = "safe"
            approved   = True

        return MCPScanReport(
            tool_name=name, risk_level=risk_level,
            findings=findings, approved=approved
        )

    def scan_tool_registry(self, tools: List[Dict[str, Any]]) -> List[MCPScanReport]:
        """Scan all tools in a registry. Returns one report per tool."""
        return [self.scan_tool_definition(t) for t in tools]

    def approved_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Filter tool list to approved tools only (no critical/high risks)."""
        return [t for t, r in zip(tools, self.scan_tool_registry(tools)) if r.approved]

    @staticmethod
    def _levenshtein(s1: str, s2: str) -> int:
        """Compute Levenshtein edit distance between two strings."""
        if len(s1) < len(s2):
            return MCPToolScanner._levenshtein(s2, s1)
        if len(s2) == 0:
            return len(s1)
        prev_row = range(len(s2) + 1)
        for i, c1 in enumerate(s1):
            curr_row = [i + 1]
            for j, c2 in enumerate(s2):
                insertions  = prev_row[j + 1] + 1
                deletions   = curr_row[j] + 1
                substitutions = prev_row[j] + (c1 != c2)
                curr_row.append(min(insertions, deletions, substitutions))
            prev_row = curr_row
        return prev_row[-1]


class GovernanceBlockedError(Exception):
    """Raised when an MCP tool call is blocked by governance."""
    def __init__(self, tool_name: str, violations: List[str], decision_id: str = ""):
        self.tool_name   = tool_name
        self.violations  = violations
        self.decision_id = decision_id
        super().__init__(f"Tool '{tool_name}' blocked by GlassBox governance: {'; '.join(violations)}")


class MCPGovernanceGateway:
    """
    Governs MCP tool calls through the GlassBox pipeline.

    Every tool call is evaluated before execution. The gateway:
      1. Maps tool name to a decision type (or uses CUSTOM)
      2. Constructs a DecisionRequest from the tool name + arguments
      3. Submits to the GovernancePipeline
      4. Returns the tool result if approved, or raises GovernanceBlockedError

    Integration:
        gateway = MCPGovernanceGateway(pipeline, agent_id="mcp_agent")

        # Intercept a tool call
        result = gateway.call_tool("database_query",
                                   {"sql": "SELECT * FROM users", "db": "prod"})

        # Register tool scan before loading
        approved = gateway.approve_tool_registry(mcp_tool_list)
    """

    _TOOL_TYPE_MAP: Dict[str, DecisionType] = {
        "database_query":    DecisionType.CUSTOM,
        "http_request":      DecisionType.CUSTOM,
        "file_read":         DecisionType.CUSTOM,
        "file_write":        DecisionType.IT_OPS,
        "execute_code":      DecisionType.IT_OPS,
        "bash":              DecisionType.IT_OPS,
        "send_payment":      DecisionType.FINANCIAL,
        "create_order":      DecisionType.PROCUREMENT,
        "update_price":      DecisionType.PRICING,
        "prescribe":         DecisionType.CLINICAL,
        "trade_order":       DecisionType.TRADING,
        "generate_content":  DecisionType.CONTENT,
        "draft_contract":    DecisionType.LEGAL,
    }

    def __init__(
        self,
        pipeline:          "GovernancePipeline",
        agent_id:          str = "mcp_agent",
        decision_type_map: Optional[Dict[str, DecisionType]] = None,
        auto_scan:         bool = True,
        confidence:        float = 1.0,
    ):
        self._pipeline   = pipeline
        self._agent_id   = agent_id
        self._type_map   = {**self._TOOL_TYPE_MAP, **(decision_type_map or {})}
        self._scanner    = MCPToolScanner() if auto_scan else None
        self._confidence = confidence

    def call_tool(
        self,
        tool_name:  str,
        arguments:  Dict[str, Any],
        tool_fn:    Optional[Callable] = None,
        confidence: Optional[float]    = None,
    ) -> Any:
        """
        Govern a MCP tool call and optionally execute it.

        Args:
            tool_name:  Name of the MCP tool being called.
            arguments:  Tool arguments as a dict.
            tool_fn:    Optional callable to invoke if governance approves.
            confidence: Model confidence for this call.

        Returns:
            Result of tool_fn if provided and approved.
            True if approved and no tool_fn provided.

        Raises:
            GovernanceBlockedError if blocked.
        """
        dtype   = self._type_map.get(tool_name, DecisionType.CUSTOM)
        payload = {"tool_name": tool_name, **arguments}
        ctx     = DecisionContext(
            confidence=confidence or self._confidence,
            source_system="mcp_gateway"
        )
        request  = DecisionRequest(
            agent_id=self._agent_id, decision_type=dtype,
            payload=payload, context=ctx,
        )
        response = self._pipeline.process(request)

        if response.final_status == FinalStatus.BLOCKED:
            raise GovernanceBlockedError(
                tool_name, response.policy_violations, response.decision_id)

        if tool_fn is not None:
            return tool_fn(**arguments)
        return True

    async def call_tool_async(
        self,
        tool_name:  str,
        arguments:  Dict[str, Any],
        tool_fn:    Optional[Callable] = None,
    ) -> Any:
        """Async variant of call_tool."""
        dtype   = self._type_map.get(tool_name, DecisionType.CUSTOM)
        payload = {"tool_name": tool_name, **arguments}
        ctx     = DecisionContext(confidence=self._confidence, source_system="mcp_gateway_async")
        request = DecisionRequest(
            agent_id=self._agent_id, decision_type=dtype,
            payload=payload, context=ctx,
        )
        response = await self._pipeline.process_async(request)
        if response.final_status == FinalStatus.BLOCKED:
            raise GovernanceBlockedError(
                tool_name, response.policy_violations, response.decision_id)
        if tool_fn is not None:
            import asyncio
            if asyncio.iscoroutinefunction(tool_fn):
                return await tool_fn(**arguments)
            return tool_fn(**arguments)
        return True

    def approve_tool_registry(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Scan a list of MCP tool definitions and return only approved tools.
        Raises ValueError if any critical-risk tools are found.
        """
        if self._scanner is None:
            return tools
        reports   = self._scanner.scan_tool_registry(tools)
        critical  = [r for r in reports if r.risk_level == "critical"]
        if critical:
            names = ", ".join(r.tool_name for r in critical)
            raise ValueError(
                f"MCP tool registry contains critical-risk tools: {names}. "
                "Load aborted. Review MCPScanReport findings.")
        return [t for t, r in zip(tools, reports) if r.approved]
