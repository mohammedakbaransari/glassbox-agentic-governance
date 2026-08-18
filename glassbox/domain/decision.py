"""Authorization requests, decisions, obligations and stage outcomes (GB-002).

Two v1 defects are made structurally impossible here.

**Deny by default (invariant I4).** ``AuthorizationDecision`` cannot be built with
a bare constructor call that happens to allow: :attr:`DecisionEffect.DENY` is the
default effect, an allowing decision must cite the policy bundle digest that
authorised it, and every denial must state at least one machine-readable
:class:`DenialReason`. v1's ``_authorize_request`` returned *allow* when the
access-control component was ``None`` or when the user id was missing.

**Explicit skipping (invariant I9).** v1 skipped the contract stage entirely when
no contract was registered, so a missing control was indistinguishable from a
passing one. :class:`StageOutcome` forces every stage to declare
``EXECUTED``/``SKIPPED``/``FAILED`` with a reason, and the whole list is written
to the ``skipped_stages`` evidence column.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.action import ProposedAction
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.identity import VerifiedPrincipal
from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_empty,
    require_non_negative,
    require_sha256_hex,
    require_timestamp,
)

__all__ = [
    "DecisionEffect",
    "DenialReason",
    "ObligationKind",
    "Obligation",
    "AuthorizationRequest",
    "AuthorizationDecision",
    "StageStatus",
    "StageOutcome",
    "ExecutionStatus",
    "ExecutionOutcome",
]


class DecisionEffect(Enum):
    """What the control plane decided to do with a proposed action."""

    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"
    ALLOW = "allow"

    @property
    def permits_dispatch(self) -> bool:
        """Whether this effect alone authorises immediate dispatch."""
        return self is DecisionEffect.ALLOW


class DenialReason(Enum):
    """Controlled vocabulary for refusals.

    A closed vocabulary means denials can be counted, alerted on and reported
    against controls. Free-text reasons cannot.
    """

    IDENTITY_UNVERIFIED = "identity_unverified"
    CREDENTIAL_EXPIRED = "credential_expired"
    DELEGATION_INVALID = "delegation_invalid"
    ACTION_NOT_GOVERNED = "action_not_governed"
    ATTESTATION_NOT_SATISFIED = "attestation_not_satisfied"
    MANDATE_MISSING = "mandate_missing"
    MANDATE_REVOKED = "mandate_revoked"
    MANDATE_EXCEEDED = "mandate_exceeded"
    TOOL_NOT_GOVERNED = "tool_not_governed"
    TOOL_DEFINITION_CHANGED = "tool_definition_changed"
    POLICY_DENIED = "policy_denied"
    POLICY_BUNDLE_UNAVAILABLE = "policy_bundle_unavailable"
    RISK_THRESHOLD_EXCEEDED = "risk_threshold_exceeded"
    LIMIT_EXCEEDED = "limit_exceeded"
    BASELINE_ANOMALY = "baseline_anomaly"
    DEPENDENCY_UNAVAILABLE = "dependency_unavailable"
    MANDATORY_STAGE_SKIPPED = "mandatory_stage_skipped"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    KILL_SWITCH_ENGAGED = "kill_switch_engaged"
    PARAMETERS_INVALID = "parameters_invalid"
    PROMPT_INJECTION_DETECTED = "prompt_injection_detected"


class ObligationKind(Enum):
    """Categories of condition attached to an allowing decision."""

    NOTIFY = "notify"
    REDACT = "redact"
    RATE_CAP = "rate_cap"
    DUAL_CONTROL = "dual_control"
    RECORD_JUSTIFICATION = "record_justification"
    POST_EXECUTION_REVIEW = "post_execution_review"


@dataclass(frozen=True, slots=True)
class Obligation:
    """A condition attached to an allowing decision.

    Attributes:
        kind: What kind of condition this is.
        obligation_id: Stable identifier for tracking discharge.
        parameters: Structured parameters for the enforcing component.
        blocking: Whether the obligation must be discharged *before* dispatch.
            A blocking obligation that cannot be discharged turns the decision
            into a denial; it never degrades into a warning.
    """

    kind: ObligationKind
    obligation_id: str
    blocking: bool = False
    parameters: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ObligationKind):
            raise DomainValidationError(
                "kind must be an ObligationKind",
                field="kind",
                offending_type=type(self.kind).__name__,
            )
        object.__setattr__(
            self, "obligation_id", require_identifier(self.obligation_id, field="obligation_id")
        )
        if not isinstance(self.blocking, bool):
            raise DomainValidationError(
                "blocking must be a bool",
                field="blocking",
                offending_type=type(self.blocking).__name__,
            )
        if isinstance(self.parameters, Mapping):
            object.__setattr__(
                self, "parameters", freeze_mapping(self.parameters, field="parameters")
            )
        elif not isinstance(self.parameters, tuple):
            raise DomainValidationError(
                "parameters must be a mapping or a tuple of pairs",
                field="parameters",
                offending_type=type(self.parameters).__name__,
            )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in ``obligations``."""
        return {
            "kind": self.kind.value,
            "obligation_id": self.obligation_id,
            "blocking": self.blocking,
            "parameters": {key: value for key, value in self.parameters},
        }


@dataclass(frozen=True, slots=True)
class AuthorizationRequest:
    """The complete, server-derived input to a policy decision.

    Every field originates from verified credentials, the governed action
    catalogue or a trusted store. There is deliberately no channel here for
    caller-supplied ``confidence``, ``environment`` or ``agent_chain`` -- the three
    fields v1 accepted from the request body and then governed against
    (invariant I2).

    Attributes:
        decision_id: Correlation id for this decision, minted by the gateway.
        principal: The verified identity of the acting agent.
        action: The server-derived action under evaluation.
        evaluated_at: Epoch seconds from the injected clock.
        attributes: Additional server-derived attributes for policy evaluation.
    """

    decision_id: str
    principal: VerifiedPrincipal
    action: ProposedAction
    evaluated_at: float
    attributes: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "decision_id", require_identifier(self.decision_id, field="decision_id")
        )
        if not isinstance(self.principal, VerifiedPrincipal):
            raise DomainValidationError(
                "principal must be a VerifiedPrincipal produced by an IdentityVerifier",
                field="principal",
                offending_type=type(self.principal).__name__,
            )
        if not isinstance(self.action, ProposedAction):
            raise DomainValidationError(
                "action must be a ProposedAction",
                field="action",
                offending_type=type(self.action).__name__,
            )
        object.__setattr__(
            self, "evaluated_at", require_timestamp(self.evaluated_at, field="evaluated_at")
        )
        if isinstance(self.attributes, Mapping):
            object.__setattr__(
                self, "attributes", freeze_mapping(self.attributes, field="attributes")
            )
        elif not isinstance(self.attributes, tuple):
            raise DomainValidationError(
                "attributes must be a mapping or a tuple of pairs",
                field="attributes",
                offending_type=type(self.attributes).__name__,
            )
        if self.principal.tenant_id != self.action.tenant_id:
            raise DomainValidationError(
                "the principal's tenant and the resource's tenant must match",
                field="action",
                principal_tenant=self.principal.tenant_id,
                resource_tenant=self.action.tenant_id,
            )

    @property
    def tenant_id(self) -> str:
        """The single tenant this request belongs to."""
        return self.principal.tenant_id

    def attribute(self, name: str, default: Optional[Any] = None) -> Optional[Any]:
        """Return a server-derived attribute, or ``default`` when absent."""
        for key, value in self.attributes:
            if key == name:
                return value
        return default


@dataclass(frozen=True, slots=True)
class AuthorizationDecision:
    """The outcome of policy evaluation.

    Use :meth:`deny`, :meth:`allow` or :meth:`require_approval`. The default
    constructor produces a denial, so a partially-initialised or mocked decision
    fails safe.

    Attributes:
        effect: What was decided.
        reasons: Machine-readable denial reasons; empty for an allow.
        rationale: Human-readable explanation shown to approvers and auditors.
        policy_bundle_id: Bundle that produced the decision.
        policy_bundle_sha256: Digest of that bundle, recorded in evidence.
        matched_rules: Identifiers of the rules that fired.
        obligations: Conditions attached to an allow.
    """

    effect: DecisionEffect = DecisionEffect.DENY
    reasons: Tuple[DenialReason, ...] = ()
    rationale: str = "deny by default"
    policy_bundle_id: Optional[str] = None
    policy_bundle_sha256: Optional[str] = None
    matched_rules: Tuple[str, ...] = ()
    obligations: Tuple[Obligation, ...] = field(default=())

    def __post_init__(self) -> None:
        if not isinstance(self.effect, DecisionEffect):
            raise DomainValidationError(
                "effect must be a DecisionEffect",
                field="effect",
                offending_type=type(self.effect).__name__,
            )
        if not isinstance(self.reasons, tuple):
            object.__setattr__(self, "reasons", tuple(self.reasons or ()))
        for index, reason in enumerate(self.reasons):
            if not isinstance(reason, DenialReason):
                raise DomainValidationError(
                    "reasons must contain DenialReason members",
                    field=f"reasons[{index}]",
                    offending_type=type(reason).__name__,
                )
        require_non_empty(self.rationale, field="rationale")
        if not isinstance(self.matched_rules, tuple):
            object.__setattr__(self, "matched_rules", tuple(self.matched_rules or ()))
        for rule in self.matched_rules:
            require_identifier(rule, field="matched_rules")
        if not isinstance(self.obligations, tuple):
            object.__setattr__(self, "obligations", tuple(self.obligations or ()))
        for index, obligation in enumerate(self.obligations):
            if not isinstance(obligation, Obligation):
                raise DomainValidationError(
                    "obligations must contain Obligation instances",
                    field=f"obligations[{index}]",
                    offending_type=type(obligation).__name__,
                )

        if self.policy_bundle_id is not None:
            object.__setattr__(
                self,
                "policy_bundle_id",
                require_identifier(self.policy_bundle_id, field="policy_bundle_id"),
            )
        if self.policy_bundle_sha256 is not None:
            object.__setattr__(
                self,
                "policy_bundle_sha256",
                require_sha256_hex(self.policy_bundle_sha256, field="policy_bundle_sha256"),
            )

        if self.effect is DecisionEffect.DENY and not self.reasons:
            raise DomainValidationError(
                "a denial must state at least one machine-readable reason", field="reasons"
            )
        if self.effect is not DecisionEffect.DENY and self.reasons:
            raise DomainValidationError(
                "only a denial may carry denial reasons", field="reasons", effect=self.effect.value
            )
        if self.effect is not DecisionEffect.DENY:
            if self.policy_bundle_id is None or self.policy_bundle_sha256 is None:
                raise DomainValidationError(
                    "a non-denying decision must cite the policy bundle that authorised it",
                    field="policy_bundle_sha256",
                    effect=self.effect.value,
                )
        if self.obligations and self.effect is DecisionEffect.DENY:
            raise DomainValidationError("a denial cannot carry obligations", field="obligations")

    # ----------------------------------------------------------------- #
    # Factories
    # ----------------------------------------------------------------- #

    @classmethod
    def deny(
        cls,
        *reasons: DenialReason,
        rationale: str,
        policy_bundle_id: Optional[str] = None,
        policy_bundle_sha256: Optional[str] = None,
        matched_rules: Tuple[str, ...] = (),
    ) -> "AuthorizationDecision":
        """Build a denial. At least one reason is mandatory."""
        if not reasons:
            raise DomainValidationError(
                "a denial must state at least one machine-readable reason", field="reasons"
            )
        return cls(
            effect=DecisionEffect.DENY,
            reasons=tuple(reasons),
            rationale=rationale,
            policy_bundle_id=policy_bundle_id,
            policy_bundle_sha256=policy_bundle_sha256,
            matched_rules=tuple(matched_rules),
        )

    @classmethod
    def allow(
        cls,
        *,
        rationale: str,
        policy_bundle_id: str,
        policy_bundle_sha256: str,
        matched_rules: Tuple[str, ...] = (),
        obligations: Tuple[Obligation, ...] = (),
    ) -> "AuthorizationDecision":
        """Build an allow. The authorising bundle must be cited."""
        return cls(
            effect=DecisionEffect.ALLOW,
            rationale=rationale,
            policy_bundle_id=policy_bundle_id,
            policy_bundle_sha256=policy_bundle_sha256,
            matched_rules=tuple(matched_rules),
            obligations=tuple(obligations),
        )

    @classmethod
    def require_approval(
        cls,
        *,
        rationale: str,
        policy_bundle_id: str,
        policy_bundle_sha256: str,
        matched_rules: Tuple[str, ...] = (),
        obligations: Tuple[Obligation, ...] = (),
    ) -> "AuthorizationDecision":
        """Build a decision that routes the action to human approval."""
        return cls(
            effect=DecisionEffect.REQUIRE_APPROVAL,
            rationale=rationale,
            policy_bundle_id=policy_bundle_id,
            policy_bundle_sha256=policy_bundle_sha256,
            matched_rules=tuple(matched_rules),
            obligations=tuple(obligations),
        )

    # ----------------------------------------------------------------- #
    # Behaviour
    # ----------------------------------------------------------------- #

    @property
    def is_denied(self) -> bool:
        """Whether the action was refused outright."""
        return self.effect is DecisionEffect.DENY

    @property
    def blocking_obligations(self) -> Tuple[Obligation, ...]:
        """Obligations that must be discharged before any dispatch."""
        return tuple(obligation for obligation in self.obligations if obligation.blocking)

    def permits_dispatch(self) -> bool:
        """Whether dispatch may proceed once blocking obligations are discharged."""
        return self.effect.permits_dispatch

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``policy_decision`` payload."""
        return {
            "effect": self.effect.value,
            "reasons": [reason.value for reason in self.reasons],
            "rationale": self.rationale,
            "policy_bundle_id": self.policy_bundle_id,
            "policy_bundle_sha256": self.policy_bundle_sha256,
            "matched_rules": list(self.matched_rules),
            "obligations": [obligation.as_evidence() for obligation in self.obligations],
        }


class StageStatus(Enum):
    """Whether a governance stage ran, was skipped, or failed."""

    EXECUTED = "executed"
    SKIPPED = "skipped"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StageOutcome:
    """The recorded result of one governance stage (invariant I9).

    A skipped stage must state why. Silence is the failure mode this type exists
    to eliminate: v1 skipped the contract stage whenever no contract was
    registered, and the evidence looked identical to a stage that passed.
    """

    stage: str
    status: StageStatus
    duration_ms: float = 0.0
    reason: Optional[str] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage", require_identifier(self.stage, field="stage"))
        if not isinstance(self.status, StageStatus):
            raise DomainValidationError(
                "status must be a StageStatus",
                field="status",
                offending_type=type(self.status).__name__,
            )
        object.__setattr__(
            self, "duration_ms", require_non_negative(self.duration_ms, field="duration_ms")
        )
        if self.status is not StageStatus.EXECUTED:
            require_non_empty(self.reason, field="reason")

    @property
    def is_missing_control(self) -> bool:
        """Whether this outcome represents a control that did not run."""
        return self.status is not StageStatus.EXECUTED

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in ``skipped_stages``."""
        return {
            "stage": self.stage,
            "status": self.status.value,
            "duration_ms": self.duration_ms,
            "reason": self.reason,
        }


class ExecutionStatus(Enum):
    """Terminal state of a governed action."""

    EXECUTED = "executed"
    FAILED = "failed"
    #: Dispatch timed out; whether the effect occurred is unknown.
    INDETERMINATE = "indeterminate"
    ABANDONED = "abandoned"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"
    #: Evaluated for replay only (GB-012). Dispatch was never attempted, so
    #: this is never uncertain the way ``INDETERMINATE`` is -- it is the
    #: deliberate, structural absence of any effect. Never set on the original
    #: decision being replayed, only on the replay's own record.
    REPLAYED = "replayed"

    @property
    def is_terminal(self) -> bool:
        """Whether no further outcome is expected."""
        return self is not ExecutionStatus.PENDING_APPROVAL


@dataclass(frozen=True, slots=True)
class ExecutionOutcome:
    """What actually happened after the decision was recorded.

    Attributes:
        status: Terminal state of the action.
        completed_at: Epoch seconds at which the outcome was known.
        result_digest: Hash of the result payload; never the payload itself.
        error_class: Exception class name when the action failed.
    """

    status: ExecutionStatus
    completed_at: float
    result_digest: Optional[str] = None
    error_class: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, ExecutionStatus):
            raise DomainValidationError(
                "status must be an ExecutionStatus",
                field="status",
                offending_type=type(self.status).__name__,
            )
        object.__setattr__(
            self, "completed_at", require_timestamp(self.completed_at, field="completed_at")
        )
        if self.result_digest is not None:
            object.__setattr__(
                self,
                "result_digest",
                require_sha256_hex(self.result_digest, field="result_digest"),
            )
        if self.error_class is not None:
            require_non_empty(self.error_class, field="error_class")
        if self.status is ExecutionStatus.FAILED and self.error_class is None:
            raise DomainValidationError(
                "a failed outcome must record its error class", field="error_class"
            )

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``evidence_outcome`` payload."""
        return {
            "status": self.status.value,
            "completed_at": self.completed_at,
            "result_digest": self.result_digest,
            "error_class": self.error_class,
        }
