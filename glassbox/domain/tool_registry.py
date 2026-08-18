"""Governed tool registry (GB-013).

Closes fundamental problem F6 at its measured worst case: v1's
``_TOOL_TYPE_MAP`` mapped 13 tool names to a ``DecisionType``; anything else
fell through to ``CUSTOM``, scored a flat 25, and auto-executed --
``wipe_production_database`` was one such unmapped tool, and it ran.

A :class:`ToolDefinition` is the deny-by-default alternative: a tool must be
registered under its exact name *and* definition digest before it can be
called at all. An unregistered tool, or one whose definition no longer
matches the digest it was registered under (the definition changed -- a rug
pull), is refused with ``DenialReason.TOOL_NOT_GOVERNED``, never silently
downgraded to a generic, low-risk default.

Reuses :class:`~glassbox.domain.catalogue.ActionDefinition` for the governed
shape (consequence, exposure derivation), so a tool call is evaluated by the
exact same, already-audited machinery as any other governed action -- a tool
registry entry *is* an action definition, addressed by tool identity instead
of action name.

Detecting and quarantining a definition change specifically, rather than just
refusing it, is GB-014's rug-pull handling; this module only records which
digest is currently the governed one.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.catalogue import ActionDefinition
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    canonical_bytes,
    require_identifier,
    require_non_negative,
    require_sha256_hex,
)

__all__ = ["ToolDefinition", "ToolRegistryBundle"]


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    """The governed shape of one tool, pinned to its exact definition.

    Attributes:
        tool_name: Exact, registered tool identifier.
        definition_sha256: Hex digest of the approved tool definition (its
            description and schema). A presented digest that does not match
            this one is not a variant of the same tool -- it is ungoverned.
        action: The consequence class and exposure derivation this tool's
            calls are evaluated against, identical in shape to a governed
            action (GB-010).
    """

    tool_name: str
    definition_sha256: str
    action: ActionDefinition

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", require_identifier(self.tool_name, field="tool_name"))
        object.__setattr__(
            self,
            "definition_sha256",
            require_sha256_hex(self.definition_sha256, field="definition_sha256"),
        )
        if not isinstance(self.action, ActionDefinition):
            raise DomainValidationError(
                "action must be an ActionDefinition",
                field="action",
                offending_type=type(self.action).__name__,
            )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation for bundle hashing."""
        return {
            "tool_name": self.tool_name,
            "definition_sha256": self.definition_sha256,
            "action": dict(self.action.as_evidence()),
        }


@dataclass(frozen=True, slots=True)
class ToolRegistryBundle:
    """A versioned, attributable set of governed tool definitions.

    Attributes:
        bundle_id: Identifier of this registry version.
        tenant_id: Owning tenant.
        version: Monotonic version.
        definitions: Every governed tool, keyed by name.
    """

    bundle_id: str
    tenant_id: str
    version: int
    definitions: Tuple[ToolDefinition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", require_identifier(self.bundle_id, field="bundle_id"))
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError("version must be an integer", field="version")
        require_non_negative(self.version, field="version")
        if not isinstance(self.definitions, tuple):
            object.__setattr__(self, "definitions", tuple(self.definitions or ()))
        seen = set()
        for definition in self.definitions:
            if not isinstance(definition, ToolDefinition):
                raise DomainValidationError(
                    "definitions must contain ToolDefinition instances",
                    field="definitions",
                    offending_type=type(definition).__name__,
                )
            if definition.tool_name in seen:
                raise DomainValidationError(
                    "duplicate tool definition", field="definitions", tool_name=definition.tool_name
                )
            seen.add(definition.tool_name)

    def resolve(self, tool_name: str) -> Optional[ToolDefinition]:
        """Return the definition for ``tool_name``, or ``None`` if ungoverned."""
        for definition in self.definitions:
            if definition.tool_name == tool_name:
                return definition
        return None

    def digest(self) -> str:
        """Return the SHA-256 digest of this bundle's content."""
        payload = {
            "bundle_id": self.bundle_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "definitions": [dict(item.as_evidence()) for item in self.definitions],
        }
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()
