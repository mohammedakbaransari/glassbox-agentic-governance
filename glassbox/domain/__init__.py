"""GlassBox domain layer (GB-002).

Pure value objects and the rules that govern them. This package:

* performs **no I/O** -- no database, no network, no filesystem;
* reads **no clock**, no environment variable and no random source, so every
  computation is reproducible from its inputs (invariant I6);
* emits **no log records** -- failures raise
  :class:`~glassbox.domain.errors.GlassBoxError` subclasses carrying a structured
  ``context`` mapping that the application layer turns into a log event, a metric
  and an evidence field;
* imports **nothing** outside the standard library and its own modules, which is
  enforced by ``tests/test_layering.py``.

The dependency rule is ``domain <- ports <- app <- adapters`` and it never
reverses.

Every value object is ``@dataclass(frozen=True, slots=True)``. ``frozen`` makes
tampering with a decision in flight impossible; ``slots`` removes the per-instance
``__dict__``, which both bounds memory on the hot path and prevents an adapter
from smuggling extra attributes onto a governance object.

**This layer deliberately does not log.** A pure layer that emits log records has
a hidden dependency on logging configuration and becomes untestable without a
capture fixture. Instead, failures raise a
:class:`~glassbox.domain.errors.GlassBoxError` carrying a stable ``code`` and a
structured ``context`` mapping; the application layer is responsible for turning
that into a log event, a metric and an evidence field exactly once. The rule is
enforced by ``tests/test_layering.py``, which fails the build if ``logging`` is
imported here.
"""

from __future__ import annotations

from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.decision import (
    Approval,
    ApprovalState,
    AuthorizationDecision,
    AuthorizationRequest,
    DecisionEffect,
    DenialReason,
    ExecutionOutcome,
    ExecutionStatus,
    Obligation,
    ObligationKind,
    StageOutcome,
    StageStatus,
)
from glassbox.domain.errors import (
    BaselineStoreUnavailable,
    CredentialExpiredError,
    DelegationError,
    DispatchError,
    DispatchRefusedError,
    DispatchTimeoutError,
    DomainValidationError,
    EvidenceError,
    EvidenceIntegrityError,
    EvidenceWriteError,
    GlassBoxError,
    IdentityError,
    LimitStoreUnavailable,
    MandateError,
    MandateExceededError,
    MandateNotFoundError,
    MandateRevokedError,
    PolicyBundleSignatureError,
    PolicyBundleUnavailableError,
    PolicyError,
    RiskError,
    RiskModelUnavailableError,
    SigningUnavailableError,
)
from glassbox.domain.evidence import (
    GENESIS_PREV_HASH,
    EvidenceReceipt,
    EvidenceSegment,
    IntegrityReport,
    IntegrityStatus,
    IntentRecord,
    ModelProvenance,
    OutcomeRecord,
)
from glassbox.domain.identity import (
    CredentialType,
    DelegationChain,
    DelegationHop,
    RawCredential,
    SubjectType,
    VerifiedPrincipal,
)
from glassbox.domain.limits import LimitKey, LimitScope, LimitVerdict, Window
from glassbox.domain.mandate import Mandate, MandateDenialReason, MandateVerdict, ToolGrant
from glassbox.domain.audit_event import AuditEvent
from glassbox.domain.tenancy import Tenant, TenantStatus
from glassbox.domain.risk import (
    CONSEQUENCE_FLOORS,
    MAX_RISK_SCORE,
    MIN_RISK_SCORE,
    RISK_BANDS,
    RiskFactor,
    RiskInputs,
    RiskLevel,
    RiskScore,
)
from glassbox.domain.serialization import canonical_bytes, canonical_json, freeze_mapping

__all__ = [
    # action
    "BlastRadius",
    "ConsequenceClass",
    "Exposure",
    "ProposedAction",
    "ResourceRef",
    # audit event
    "AuditEvent",
    # tenancy
    "Tenant",
    "TenantStatus",
    # decision
    "Approval",
    "ApprovalState",
    "AuthorizationDecision",
    "AuthorizationRequest",
    "DecisionEffect",
    "DenialReason",
    "ExecutionOutcome",
    "ExecutionStatus",
    "Obligation",
    "ObligationKind",
    "StageOutcome",
    "StageStatus",
    # errors
    "BaselineStoreUnavailable",
    "CredentialExpiredError",
    "DelegationError",
    "DispatchError",
    "DispatchRefusedError",
    "DispatchTimeoutError",
    "DomainValidationError",
    "EvidenceError",
    "EvidenceIntegrityError",
    "EvidenceWriteError",
    "GlassBoxError",
    "IdentityError",
    "LimitStoreUnavailable",
    "MandateError",
    "MandateExceededError",
    "MandateNotFoundError",
    "MandateRevokedError",
    "PolicyBundleSignatureError",
    "PolicyBundleUnavailableError",
    "PolicyError",
    "RiskError",
    "RiskModelUnavailableError",
    "SigningUnavailableError",
    # evidence
    "GENESIS_PREV_HASH",
    "EvidenceReceipt",
    "EvidenceSegment",
    "IntegrityReport",
    "IntegrityStatus",
    "IntentRecord",
    "ModelProvenance",
    "OutcomeRecord",
    # identity
    "CredentialType",
    "DelegationChain",
    "DelegationHop",
    "RawCredential",
    "SubjectType",
    "VerifiedPrincipal",
    # limits
    "LimitKey",
    "LimitScope",
    "LimitVerdict",
    "Window",
    # mandate
    "Mandate",
    "MandateDenialReason",
    "MandateVerdict",
    "ToolGrant",
    # risk
    "CONSEQUENCE_FLOORS",
    "MAX_RISK_SCORE",
    "MIN_RISK_SCORE",
    "RISK_BANDS",
    "RiskFactor",
    "RiskInputs",
    "RiskLevel",
    "RiskScore",
    # serialization
    "canonical_bytes",
    "canonical_json",
    "freeze_mapping",
]
