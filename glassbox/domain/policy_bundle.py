"""Declarative, signed policy bundles (GB-018, GB-019, GB-020).

Replaces fundamental problem F5: v1 evaluated 35 Python callables
(``policy_engine.py:1443``), each capable of running arbitrary code, so
containing a misbehaving or slow one needed a 32-worker timeout pool
(``_timeout_executor``) that abandoned timed-out threads
(``policy_engine.py:462``). ``PolicyStatus`` (``models.py:64``) was declared and
never used, so nothing distinguished a draft bundle from an active one.

A :class:`PolicyRule` here is data: an action-name glob, a resource-name glob,
optional consequence and monetary ceilings, and an effect. Matching a rule
against an action is pure comparison -- there is no way to express arbitrary
code in this schema, so there is nothing for a thread pool to contain (GB-020:
the timeout executor is not ported forward because there is no longer
anything it would need to bound).

:class:`PolicyBundle` is versioned and content-addressed exactly like the
action catalogue (GB-010) and the tool registry (GB-013). Signing it is a
:mod:`glassbox.ports` concern (:class:`~glassbox.ports.keys.MacSigner`); this
module only defines the payload that gets signed.

:func:`detect_conflicts` (GB-019) replaces v1's substring match on rule names
(``policy_engine.py:370``, looking for ``"block"``/``"allow"`` in a rule's own
name) with structural overlap analysis over the declarative conditions
themselves -- it finds contradictory rules regardless of what they are named.
"""

from __future__ import annotations

import fnmatch
import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.action import ConsequenceClass, ProposedAction
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    canonical_bytes,
    require_identifier,
    require_non_empty,
    require_non_negative,
    require_timestamp,
)

__all__ = [
    "RuleEffect",
    "PolicyRule",
    "PolicyBundle",
    "SignedPolicyBundle",
    "RuleConflict",
    "detect_conflicts",
    "MAX_RULES_PER_BUNDLE",
]

#: A pathological bundle is refused at construction, not discovered at runtime
#: by a slow decision (GB-020). There is no code path in this module capable of
#: running long regardless -- this is a sanity ceiling, not a timeout.
MAX_RULES_PER_BUNDLE = 10_000


class RuleEffect(Enum):
    """What a matching rule decides."""

    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass(frozen=True, slots=True)
class PolicyRule:
    """One declarative condition-effect pair.

    Attributes:
        name: Stable, unique-within-bundle identifier.
        effect: What this rule decides when it matches.
        action_pattern: Shell-style glob over the action name.
        resource_pattern: Shell-style glob over ``kind/id``.
        max_consequence: The rule applies only up to this consequence class;
            ``None`` applies at every consequence.
        max_monetary: The rule applies only when exposure is known and at or
            below this ceiling; ``None`` applies at every (or no) exposure.
        priority: Lower values are evaluated first. The first matching rule
            wins -- deny-by-default if none match.
        rationale: Human-readable explanation recorded on the decision.
    """

    name: str
    effect: RuleEffect
    action_pattern: str = "*"
    resource_pattern: str = "*"
    max_consequence: Optional[ConsequenceClass] = None
    max_monetary: Optional[float] = None
    priority: int = 100
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, field="name"))
        if not isinstance(self.effect, RuleEffect):
            raise DomainValidationError(
                "effect must be a RuleEffect",
                field="effect",
                offending_type=type(self.effect).__name__,
            )
        object.__setattr__(
            self, "action_pattern", require_non_empty(self.action_pattern, field="action_pattern")
        )
        object.__setattr__(
            self,
            "resource_pattern",
            require_non_empty(self.resource_pattern, field="resource_pattern"),
        )
        if self.max_consequence is not None and not isinstance(
            self.max_consequence, ConsequenceClass
        ):
            raise DomainValidationError(
                "max_consequence must be a ConsequenceClass",
                field="max_consequence",
                offending_type=type(self.max_consequence).__name__,
            )
        if self.max_monetary is not None:
            object.__setattr__(
                self, "max_monetary", require_non_negative(self.max_monetary, field="max_monetary")
            )
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise DomainValidationError(
                "priority must be an integer",
                field="priority",
                offending_type=type(self.priority).__name__,
            )

    def matches(self, action: ProposedAction) -> bool:
        """Return whether ``action`` falls within this rule's declared conditions."""
        if not fnmatch.fnmatchcase(action.action, self.action_pattern):
            return False
        resource_name = f"{action.resource.kind}/{action.resource.id}"
        if not fnmatch.fnmatchcase(resource_name, self.resource_pattern):
            return False
        if self.max_consequence is not None and action.consequence > self.max_consequence:
            return False
        if self.max_monetary is not None:
            if action.exposure.monetary is None or action.exposure.monetary > self.max_monetary:
                return False
        return True

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation for bundle hashing."""
        return {
            "name": self.name,
            "effect": self.effect.value,
            "action_pattern": self.action_pattern,
            "resource_pattern": self.resource_pattern,
            "max_consequence": self.max_consequence.value if self.max_consequence else None,
            "max_monetary": self.max_monetary,
            "priority": self.priority,
        }


@dataclass(frozen=True, slots=True)
class PolicyBundle:
    """A versioned, attributable, content-addressed set of policy rules.

    Attributes:
        bundle_id: Identifier of this policy version.
        tenant_id: Owning tenant.
        version: Monotonic version.
        created_at: Epoch seconds at which this bundle was built.
        rules: Every rule, evaluated in ascending ``priority`` order.
    """

    bundle_id: str
    tenant_id: str
    version: int
    created_at: float
    rules: Tuple[PolicyRule, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", require_identifier(self.bundle_id, field="bundle_id"))
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError("version must be an integer", field="version")
        require_non_negative(self.version, field="version")
        object.__setattr__(
            self, "created_at", require_timestamp(self.created_at, field="created_at")
        )
        if not isinstance(self.rules, tuple):
            object.__setattr__(self, "rules", tuple(self.rules or ()))
        if len(self.rules) > MAX_RULES_PER_BUNDLE:
            raise DomainValidationError(
                "bundle exceeds the maximum number of rules",
                field="rules",
                count=len(self.rules),
                maximum=MAX_RULES_PER_BUNDLE,
            )
        seen = set()
        for rule in self.rules:
            if not isinstance(rule, PolicyRule):
                raise DomainValidationError(
                    "rules must contain PolicyRule instances",
                    field="rules",
                    offending_type=type(rule).__name__,
                )
            if rule.name in seen:
                raise DomainValidationError("duplicate rule name", field="rules", name=rule.name)
            seen.add(rule.name)
        object.__setattr__(self, "rules", tuple(sorted(self.rules, key=lambda r: r.priority)))

    def matching_rule(self, action: ProposedAction) -> Optional[PolicyRule]:
        """Return the first, lowest-priority rule that matches ``action``.

        ``None`` means deny by default -- the rule set said nothing about this
        action, which is never treated as permission.
        """
        for rule in self.rules:
            if rule.matches(action):
                return rule
        return None

    def canonical_payload(self) -> bytes:
        """Return the exact bytes a signer must MAC.

        The same bytes :meth:`digest` hashes, so a bundle's digest and its
        signature always describe identical content.
        """
        return canonical_bytes(
            {
                "bundle_id": self.bundle_id,
                "tenant_id": self.tenant_id,
                "version": self.version,
                "created_at": self.created_at,
                "rules": [dict(rule.as_evidence()) for rule in self.rules],
            }
        )

    def digest(self) -> str:
        """Return the SHA-256 digest of this bundle's content."""
        return hashlib.sha256(self.canonical_payload()).hexdigest()


@dataclass(frozen=True, slots=True)
class RuleConflict:
    """Two rules whose declared conditions overlap but whose effects differ."""

    rule_a: str
    rule_b: str
    reason: str


def detect_conflicts(bundle: PolicyBundle) -> Tuple[RuleConflict, ...]:
    """Find rule pairs with overlapping scope but contradictory effects (GB-019).

    Replaces v1's name-substring conflict check
    (``policy_engine.py:370``, looking for ``"block"``/``"allow"`` in a rule's
    own name) with analysis over the rules' actual declared conditions, so a
    conflict is found regardless of how the rules are named.

    A bounded heuristic, not a full constraint solver: two glob patterns are
    treated as overlapping when neither's fixed (non-wildcard) prefix rules out
    the other -- sound for the prefix-glob patterns this schema supports
    (``payments.*`` vs ``payments.wire_transfer``), not a general regular
    language intersection. ``max_consequence``/``max_monetary`` are ceilings
    from a shared floor, so two such ranges always overlap and do not further
    narrow a pattern-level match.
    """
    conflicts = []
    rules = bundle.rules
    for i, rule_a in enumerate(rules):
        for rule_b in rules[i + 1 :]:
            if rule_a.effect is rule_b.effect:
                continue
            if _patterns_overlap(
                rule_a.action_pattern, rule_b.action_pattern
            ) and _patterns_overlap(rule_a.resource_pattern, rule_b.resource_pattern):
                conflicts.append(
                    RuleConflict(
                        rule_a=rule_a.name,
                        rule_b=rule_b.name,
                        reason=(
                            f"{rule_a.effect.value!r} ({rule_a.name}) and "
                            f"{rule_b.effect.value!r} ({rule_b.name}) overlap on "
                            f"action={rule_a.action_pattern!r}/{rule_b.action_pattern!r} "
                            f"resource={rule_a.resource_pattern!r}/{rule_b.resource_pattern!r}"
                        ),
                    )
                )
    return tuple(conflicts)


def _patterns_overlap(pattern_a: str, pattern_b: str) -> bool:
    """Return whether two glob patterns could both match some real value."""
    if "*" not in pattern_a and "*" not in pattern_b:
        return pattern_a == pattern_b
    prefix_a = pattern_a.split("*", 1)[0]
    prefix_b = pattern_b.split("*", 1)[0]
    return prefix_a.startswith(prefix_b) or prefix_b.startswith(prefix_a)


@dataclass(frozen=True, slots=True)
class SignedPolicyBundle:
    """A policy bundle plus the MAC that attests it has not been tampered with.

    Pure data: computing and verifying ``mac`` is a
    :class:`~glassbox.ports.keys.MacSigner` concern. Carrying the signature
    alongside the bundle it covers is what lets "an unsigned or tampered bundle
    is refused" be checked once, at load time, rather than trusted implicitly.
    """

    bundle: PolicyBundle
    mac: bytes
    signer_key_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.bundle, PolicyBundle):
            raise DomainValidationError(
                "bundle must be a PolicyBundle",
                field="bundle",
                offending_type=type(self.bundle).__name__,
            )
        if not isinstance(self.mac, (bytes, bytearray)) or len(self.mac) < 32:
            raise DomainValidationError(
                "mac must be at least 32 bytes",
                field="mac",
                offending_type=type(self.mac).__name__,
            )
        object.__setattr__(self, "mac", bytes(self.mac))
        object.__setattr__(
            self, "signer_key_id", require_identifier(self.signer_key_id, field="signer_key_id")
        )
