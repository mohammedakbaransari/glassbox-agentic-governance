"""Agent mandates and tool grants (GB-002, WS-3 foundation).

A mandate is the answer to a question v1 could not ask: *what is this agent
allowed to do at all?* v1 governed transactions described by agents; it had no
concept of an agent's scope of authority, so an agent could attempt anything and
only a policy rule -- evaluating fields the agent itself supplied -- stood in the
way.

The mandate is evaluated **before** policy. It is a coarse, cheap, deny-by-default
ceiling on actions, resources, consequence class, exposure and tools. Policy then
refines within that ceiling. An agent cannot be granted authority by policy that
its mandate does not already contain.

Enforcement of the resolved verdict is GB-015; this module supplies the value
objects and the pure decision logic.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, FrozenSet, Mapping, Optional, Tuple

from glassbox.domain.action import ConsequenceClass, Exposure, ProposedAction
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    require_identifier,
    require_non_empty,
    require_sha256_hex,
    require_timestamp,
)

__all__ = [
    "MandateDenialReason",
    "MandateVerdict",
    "ToolGrant",
    "ActionResourceGrant",
    "Mandate",
]


class MandateDenialReason(Enum):
    """Why a mandate refused an action. Recorded verbatim in evidence."""

    NOT_YET_VALID = "mandate_not_yet_valid"
    EXPIRED = "mandate_expired"
    REVOKED = "mandate_revoked"
    WRONG_TENANT = "mandate_wrong_tenant"
    WRONG_AGENT = "mandate_wrong_agent"
    ACTION_NOT_GRANTED = "action_not_granted"
    RESOURCE_NOT_GRANTED = "resource_not_granted"
    ACTION_RESOURCE_PAIR_NOT_GRANTED = "action_resource_pair_not_granted"
    CONSEQUENCE_EXCEEDS_CEILING = "consequence_exceeds_ceiling"
    EXPOSURE_EXCEEDS_CEILING = "exposure_exceeds_ceiling"
    TOOL_NOT_GRANTED = "tool_not_granted"
    TOOL_DEFINITION_CHANGED = "tool_definition_changed"


@dataclass(frozen=True, slots=True)
class MandateVerdict:
    """The result of checking a proposed action against a mandate.

    Constructed only via :meth:`permit` and :meth:`refuse` so that a permitted
    verdict cannot be produced by accident -- for example by a default-constructed
    instance in a mocked test.
    """

    permitted: bool
    reasons: Tuple[MandateDenialReason, ...] = ()
    detail: str = ""
    mandate_version: Optional[int] = None

    def __post_init__(self) -> None:
        if not isinstance(self.permitted, bool):
            raise DomainValidationError(
                "permitted must be a bool",
                field="permitted",
                offending_type=type(self.permitted).__name__,
            )
        if not isinstance(self.reasons, tuple):
            object.__setattr__(self, "reasons", tuple(self.reasons or ()))
        for index, reason in enumerate(self.reasons):
            if not isinstance(reason, MandateDenialReason):
                raise DomainValidationError(
                    "reasons must contain MandateDenialReason members",
                    field=f"reasons[{index}]",
                    offending_type=type(reason).__name__,
                )
        if self.permitted and self.reasons:
            raise DomainValidationError(
                "a permitted verdict must not carry denial reasons", field="reasons"
            )
        if not self.permitted and not self.reasons:
            raise DomainValidationError("a refusal must state at least one reason", field="reasons")

    @classmethod
    def permit(cls, *, mandate_version: int) -> "MandateVerdict":
        """Build a permitting verdict tied to a specific mandate version."""
        return cls(permitted=True, mandate_version=mandate_version)

    @classmethod
    def refuse(
        cls,
        *reasons: MandateDenialReason,
        detail: str = "",
        mandate_version: Optional[int] = None,
    ) -> "MandateVerdict":
        """Build a refusing verdict with one or more machine-readable reasons."""
        if not reasons:
            raise DomainValidationError("a refusal must state at least one reason", field="reasons")
        return cls(
            permitted=False,
            reasons=tuple(reasons),
            detail=detail,
            mandate_version=mandate_version,
        )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in evidence."""
        return {
            "permitted": self.permitted,
            "reasons": [reason.value for reason in self.reasons],
            "detail": self.detail,
            "mandate_version": self.mandate_version,
        }


@dataclass(frozen=True, slots=True)
class ToolGrant:
    """Permission for an agent to invoke one specific, pinned tool.

    ``definition_sha256`` pins the tool's *definition*, not just its name. A tool
    whose description or schema changes after approval is a different tool -- this
    is what makes rug-pull detection (GB-014) enforceable rather than aspirational.

    Attributes:
        tool_name: Registered tool identifier.
        definition_sha256: Hex digest of the approved tool definition.
        max_consequence: Highest consequence class this grant permits.
        requires_approval: Whether every invocation needs human sign-off.
    """

    tool_name: str
    definition_sha256: str
    max_consequence: ConsequenceClass = ConsequenceClass.REVERSIBLE
    requires_approval: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", require_identifier(self.tool_name, field="tool_name"))
        object.__setattr__(
            self,
            "definition_sha256",
            require_sha256_hex(self.definition_sha256, field="definition_sha256"),
        )
        if not isinstance(self.max_consequence, ConsequenceClass):
            raise DomainValidationError(
                "max_consequence must be a ConsequenceClass",
                field="max_consequence",
                offending_type=type(self.max_consequence).__name__,
            )

    def matches(self, tool_name: str, definition_sha256: str) -> bool:
        """Return whether this grant covers the given tool *and* its exact definition."""
        return self.tool_name == tool_name and self.definition_sha256 == definition_sha256.lower()

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in evidence."""
        return {
            "tool_name": self.tool_name,
            "definition_sha256": self.definition_sha256,
            "max_consequence": self.max_consequence.value,
            "requires_approval": self.requires_approval,
        }


@dataclass(frozen=True, slots=True)
class ActionResourceGrant:
    """A joint grant binding one action pattern to one resource pattern.

    ``allowed_actions`` and ``allowed_resources`` on :class:`Mandate` are two
    independent sets: any granted action is implicitly permitted against any
    granted resource, so there is no way to express "this action is only
    permitted against that specific resource" -- an agent authorised for
    ``payments.wire_transfer`` on ``account/ACC-1`` and, separately,
    ``payments.refund`` on ``account/ACC-2`` would also be authorised for
    ``payments.wire_transfer`` on ``account/ACC-2`` under the independent-set
    model, purely because both halves happen to be granted somewhere.

    ``resource_scoped_grants`` on :class:`Mandate` closes that gap: when a
    mandate declares at least one :class:`ActionResourceGrant`, an action is
    permitted only if some grant's ``action_pattern`` **and**
    ``resource_pattern`` both match the proposed action jointly. A mandate
    that declares no scoped grants is unaffected -- this is purely additive.

    Attributes:
        action_pattern: Shell-style glob matched against the action name.
        resource_pattern: Shell-style glob matched against ``kind/id``.
    """

    action_pattern: str
    resource_pattern: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "action_pattern",
            require_non_empty(self.action_pattern, field="action_pattern"),
        )
        object.__setattr__(
            self,
            "resource_pattern",
            require_non_empty(self.resource_pattern, field="resource_pattern"),
        )

    def matches(self, *, action: str, resource: str) -> bool:
        """Return whether this grant covers the given action/resource pair."""
        return fnmatch.fnmatchcase(action, self.action_pattern) and fnmatch.fnmatchcase(
            resource, self.resource_pattern
        )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in evidence."""
        return {"action_pattern": self.action_pattern, "resource_pattern": self.resource_pattern}


@dataclass(frozen=True, slots=True)
class Mandate:
    """An agent's approved, versioned, time-bounded scope of authority.

    Patterns in ``allowed_actions`` and ``allowed_resources`` use shell-style
    globbing (``payments.*``, ``account/*``). An empty pattern set grants nothing:
    absence of a grant is a denial, never a wildcard (invariant I4).

    Attributes:
        tenant_id: Tenant that owns the mandate.
        agent_ref: Agent the mandate is issued to.
        version: Monotonic version; every change creates a new row.
        allowed_actions: Glob patterns of permitted action names.
        allowed_resources: Glob patterns of permitted ``kind/id`` references.
        max_consequence: Highest consequence class the agent may cause.
        max_exposure: Per-action ceiling on magnitude at risk.
        tool_grants: Pinned tool permissions.
        valid_from: Epoch seconds at which authority begins.
        valid_until: Epoch seconds at which it ends, or ``None`` for open-ended.
        revoked_at: Epoch seconds of revocation, or ``None``.
        approved_by: Identifiers of the approvers who authorised this version.
        resource_scoped_grants: Optional joint (action, resource) grants. When
            non-empty, an action must match some grant's action pattern *and*
            resource pattern together, closing the gap where the independent
            ``allowed_actions``/``allowed_resources`` sets would otherwise
            implicitly cross-product every granted action with every granted
            resource. Empty by default; existing mandates are unaffected.
    """

    tenant_id: str
    agent_ref: str
    version: int
    max_consequence: ConsequenceClass
    max_exposure: Exposure
    valid_from: float
    allowed_actions: FrozenSet[str] = frozenset()
    allowed_resources: FrozenSet[str] = frozenset()
    tool_grants: Tuple[ToolGrant, ...] = ()
    valid_until: Optional[float] = None
    revoked_at: Optional[float] = None
    approved_by: Tuple[str, ...] = field(default=())
    resource_scoped_grants: Tuple[ActionResourceGrant, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        object.__setattr__(self, "agent_ref", require_identifier(self.agent_ref, field="agent_ref"))
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError(
                "version must be an integer",
                field="version",
                offending_type=type(self.version).__name__,
            )
        if self.version < 1:
            raise DomainValidationError("version must be >= 1", field="version", value=self.version)
        if not isinstance(self.max_consequence, ConsequenceClass):
            raise DomainValidationError(
                "max_consequence must be a ConsequenceClass",
                field="max_consequence",
                offending_type=type(self.max_consequence).__name__,
            )
        if not isinstance(self.max_exposure, Exposure):
            raise DomainValidationError(
                "max_exposure must be an Exposure",
                field="max_exposure",
                offending_type=type(self.max_exposure).__name__,
            )
        object.__setattr__(
            self, "valid_from", require_timestamp(self.valid_from, field="valid_from")
        )
        if self.valid_until is not None:
            object.__setattr__(
                self, "valid_until", require_timestamp(self.valid_until, field="valid_until")
            )
            if self.valid_until <= self.valid_from:
                raise DomainValidationError(
                    "valid_until must be strictly after valid_from",
                    field="valid_until",
                    valid_from=self.valid_from,
                    valid_until=self.valid_until,
                )
        if self.revoked_at is not None:
            object.__setattr__(
                self, "revoked_at", require_timestamp(self.revoked_at, field="revoked_at")
            )
        object.__setattr__(self, "allowed_actions", frozenset(self.allowed_actions or ()))
        object.__setattr__(self, "allowed_resources", frozenset(self.allowed_resources or ()))
        for pattern in self.allowed_actions:
            require_non_empty(pattern, field="allowed_actions")
        for pattern in self.allowed_resources:
            require_non_empty(pattern, field="allowed_resources")
        if not isinstance(self.tool_grants, tuple):
            object.__setattr__(self, "tool_grants", tuple(self.tool_grants or ()))
        granted_names = set()
        for index, grant in enumerate(self.tool_grants):
            if not isinstance(grant, ToolGrant):
                raise DomainValidationError(
                    "tool_grants must contain ToolGrant instances",
                    field=f"tool_grants[{index}]",
                    offending_type=type(grant).__name__,
                )
            if grant.tool_name in granted_names:
                raise DomainValidationError(
                    "duplicate tool grant", field="tool_grants", tool_name=grant.tool_name
                )
            granted_names.add(grant.tool_name)
        if not isinstance(self.approved_by, tuple):
            object.__setattr__(self, "approved_by", tuple(self.approved_by or ()))
        for approver in self.approved_by:
            require_identifier(approver, field="approved_by")
        if not isinstance(self.resource_scoped_grants, tuple):
            object.__setattr__(
                self, "resource_scoped_grants", tuple(self.resource_scoped_grants or ())
            )
        for index, grant in enumerate(self.resource_scoped_grants):
            if not isinstance(grant, ActionResourceGrant):
                raise DomainValidationError(
                    "resource_scoped_grants must contain ActionResourceGrant instances",
                    field=f"resource_scoped_grants[{index}]",
                    offending_type=type(grant).__name__,
                )

    # ----------------------------------------------------------------- #
    # Lifecycle
    # ----------------------------------------------------------------- #

    def is_revoked_at(self, now: float) -> bool:
        """Return whether the mandate is revoked as of ``now``."""
        moment = require_timestamp(now, field="now")
        return self.revoked_at is not None and moment >= self.revoked_at

    def is_active_at(self, now: float) -> bool:
        """Return whether the mandate is live and unrevoked at ``now``."""
        moment = require_timestamp(now, field="now")
        if self.is_revoked_at(moment):
            return False
        if moment < self.valid_from:
            return False
        return self.valid_until is None or moment < self.valid_until

    # ----------------------------------------------------------------- #
    # Enforcement
    # ----------------------------------------------------------------- #

    def permits(self, action: ProposedAction, *, now: float) -> MandateVerdict:
        """Check a proposed action against this mandate.

        Every failing dimension is reported, not just the first, so that an
        operator sees the whole gap rather than fixing one condition at a time.

        Args:
            action: The server-derived action to check.
            now: Epoch seconds from the injected clock.

        Returns:
            A permitting or refusing verdict.
        """
        if not isinstance(action, ProposedAction):
            raise DomainValidationError(
                "action must be a ProposedAction",
                field="action",
                offending_type=type(action).__name__,
            )
        moment = require_timestamp(now, field="now")
        reasons = []

        if self.is_revoked_at(moment):
            reasons.append(MandateDenialReason.REVOKED)
        elif moment < self.valid_from:
            reasons.append(MandateDenialReason.NOT_YET_VALID)
        elif self.valid_until is not None and moment >= self.valid_until:
            reasons.append(MandateDenialReason.EXPIRED)

        if action.tenant_id != self.tenant_id:
            reasons.append(MandateDenialReason.WRONG_TENANT)

        if not self._matches_any(self.allowed_actions, action.action):
            reasons.append(MandateDenialReason.ACTION_NOT_GRANTED)

        resource_name = f"{action.resource.kind}/{action.resource.id}"
        if not self._matches_any(self.allowed_resources, resource_name):
            reasons.append(MandateDenialReason.RESOURCE_NOT_GRANTED)

        if self.resource_scoped_grants and not any(
            grant.matches(action=action.action, resource=resource_name)
            for grant in self.resource_scoped_grants
        ):
            reasons.append(MandateDenialReason.ACTION_RESOURCE_PAIR_NOT_GRANTED)

        if action.consequence > self.max_consequence:
            reasons.append(MandateDenialReason.CONSEQUENCE_EXCEEDS_CEILING)

        if action.exposure.exceeds(self.max_exposure):
            reasons.append(MandateDenialReason.EXPOSURE_EXCEEDS_CEILING)

        if reasons:
            return MandateVerdict.refuse(
                *reasons,
                detail=(
                    f"action={action.action} resource={resource_name} "
                    f"consequence={action.consequence.value}"
                ),
                mandate_version=self.version,
            )
        return MandateVerdict.permit(mandate_version=self.version)

    def permits_tool(self, tool_name: str, definition_sha256: str) -> MandateVerdict:
        """Check whether a pinned tool invocation is granted.

        An unknown tool, or a known tool whose definition digest has changed, is
        refused. This is the mandate-side half of deny-by-default tool governance.
        """
        require_identifier(tool_name, field="tool_name")
        require_non_empty(definition_sha256, field="definition_sha256")

        by_name = [grant for grant in self.tool_grants if grant.tool_name == tool_name]
        if not by_name:
            return MandateVerdict.refuse(
                MandateDenialReason.TOOL_NOT_GRANTED,
                detail=f"tool={tool_name}",
                mandate_version=self.version,
            )
        if not any(grant.matches(tool_name, definition_sha256) for grant in by_name):
            return MandateVerdict.refuse(
                MandateDenialReason.TOOL_DEFINITION_CHANGED,
                detail=f"tool={tool_name} presented_digest={definition_sha256.lower()}",
                mandate_version=self.version,
            )
        return MandateVerdict.permit(mandate_version=self.version)

    def tool_grant(self, tool_name: str) -> Optional[ToolGrant]:
        """Return the grant for ``tool_name``, or ``None`` when not granted."""
        for grant in self.tool_grants:
            if grant.tool_name == tool_name:
                return grant
        return None

    @staticmethod
    def _matches_any(patterns: FrozenSet[str], value: str) -> bool:
        """Return whether ``value`` matches any glob pattern.

        An empty pattern set matches nothing.
        """
        return any(fnmatch.fnmatchcase(value, pattern) for pattern in patterns)

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in evidence."""
        return {
            "tenant_id": self.tenant_id,
            "agent_ref": self.agent_ref,
            "version": self.version,
            "allowed_actions": sorted(self.allowed_actions),
            "allowed_resources": sorted(self.allowed_resources),
            "resource_scoped_grants": [
                dict(grant.as_evidence()) for grant in self.resource_scoped_grants
            ],
            "max_consequence": self.max_consequence.value,
            "max_exposure": dict(self.max_exposure.as_evidence()),
            "tool_grants": [grant.as_evidence() for grant in self.tool_grants],
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "revoked_at": self.revoked_at,
            "approved_by": list(self.approved_by),
        }
