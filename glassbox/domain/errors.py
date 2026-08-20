"""Domain error hierarchy (GB-002).

The domain layer is pure: it performs no I/O and emits no log records. Instead of
logging, every failure raises an exception that carries a structured ``context``
mapping. The application layer is responsible for turning that context into a
structured log event, a metric and an evidence record.

This matters for invariant **I5** ("never swallow an exception on a governance
path"). A caller that catches :class:`GlassBoxError` always has enough structured
detail to record *why* the decision failed closed, without re-deriving it from a
formatted message string.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

__all__ = [
    "GlassBoxError",
    "DomainValidationError",
    "IdentityError",
    "CredentialExpiredError",
    "DelegationError",
    "MandateError",
    "MandateNotFoundError",
    "MandateRevokedError",
    "MandateExceededError",
    "PolicyError",
    "PolicyBundleUnavailableError",
    "PolicyBundleSignatureError",
    "CatalogueError",
    "ActionNotGovernedError",
    "CatalogueBundleUnavailableError",
    "AttestationUnavailableError",
    "KillSwitchUnavailableError",
    "ToolRegistryError",
    "ToolNotGovernedError",
    "ToolRegistryUnavailableError",
    "ToolQuarantinedError",
    "RiskError",
    "RiskModelUnavailableError",
    "LimitStoreUnavailable",
    "BaselineStoreUnavailable",
    "EvidenceError",
    "EvidenceWriteError",
    "EvidenceIntegrityError",
    "SigningUnavailableError",
    "DispatchError",
    "DispatchTimeoutError",
    "DispatchRefusedError",
    "ToolOutputQuarantinedError",
    "ApprovalError",
    "ApprovalNotFoundError",
    "ApprovalGatewayUnavailableError",
    "ApprovalTransitionError",
]


class GlassBoxError(Exception):
    """Base class for every GlassBox domain failure.

    Args:
        message: Human-readable summary. Must never contain secret material.
        context: Structured key/value detail for logging and evidence. Values
            are coerced to ``str`` so the mapping is always safe to serialise.
    """

    #: Stable machine-readable code used in evidence and metrics.
    code: str = "glassbox_error"

    def __init__(self, message: str, /, **context: Any) -> None:
        super().__init__(message)
        self.message: str = message
        self.context: Mapping[str, str] = {key: str(value) for key, value in context.items()}

    def as_dict(self) -> Dict[str, Any]:
        """Return a JSON-serialisable representation for logs and evidence."""
        return {
            "error_class": type(self).__name__,
            "code": self.code,
            "message": self.message,
            "context": dict(self.context),
        }

    def __repr__(self) -> str:
        return f"{type(self).__name__}(message={self.message!r}, context={dict(self.context)!r})"


# --------------------------------------------------------------------------- #
# Construction / validation
# --------------------------------------------------------------------------- #


class DomainValidationError(GlassBoxError, ValueError):
    """A domain value object was constructed with invalid state.

    Subclasses :class:`ValueError` so that existing ``except ValueError`` handlers
    at system boundaries continue to behave sensibly.
    """

    code = "domain_validation_failed"

    def __init__(self, message: str, /, *, field: Optional[str] = None, **context: Any) -> None:
        if field is not None:
            context["field"] = field
        super().__init__(message, **context)


# --------------------------------------------------------------------------- #
# Identity and delegation
# --------------------------------------------------------------------------- #


class IdentityError(GlassBoxError):
    """Credential verification failed. Never return an unverified principal."""

    code = "identity_verification_failed"


class CredentialExpiredError(IdentityError):
    """The presented credential is outside its validity window."""

    code = "credential_expired"


class DelegationError(IdentityError):
    """The delegation chain is malformed, unverifiable, or widens authority."""

    code = "delegation_invalid"


# --------------------------------------------------------------------------- #
# Mandates
# --------------------------------------------------------------------------- #


class MandateError(GlassBoxError):
    """Base class for mandate resolution and enforcement failures."""

    code = "mandate_error"


class MandateNotFoundError(MandateError):
    """No mandate exists for the agent. Deny by default (invariant I4)."""

    code = "mandate_not_found"


class MandateRevokedError(MandateError):
    """The mandate was revoked. The agent has no authority."""

    code = "mandate_revoked"


class MandateExceededError(MandateError):
    """The proposed action falls outside the agent's granted authority."""

    code = "mandate_exceeded"


# --------------------------------------------------------------------------- #
# Policy
# --------------------------------------------------------------------------- #


class PolicyError(GlassBoxError):
    """Base class for policy evaluation failures."""

    code = "policy_error"


class PolicyBundleUnavailableError(PolicyError):
    """No ACTIVE, signature-verified policy bundle could be loaded."""

    code = "policy_bundle_unavailable"


class PolicyBundleSignatureError(PolicyError):
    """A policy bundle failed signature or digest verification."""

    code = "policy_bundle_signature_invalid"


# --------------------------------------------------------------------------- #
# Action catalogue (GB-010)
# --------------------------------------------------------------------------- #


class CatalogueError(GlassBoxError):
    """Base class for action-catalogue failures."""

    code = "catalogue_error"


class ActionNotGovernedError(CatalogueError):
    """No catalogue entry exists for this action.

    Deny by default (invariant I4): an action absent from the governed catalogue
    is treated as the most severe class it could plausibly be, never as benign.
    """

    code = "action_not_governed"


class CatalogueBundleUnavailableError(CatalogueError):
    """No active, signed catalogue bundle could be loaded."""

    code = "catalogue_bundle_unavailable"


class AttestationUnavailableError(GlassBoxError):
    """A required attestation could not be resolved from its system of record.

    Never a default answer: v1 let an agent self-assert facts like
    ``ctr_filed`` or ``change_window_approved`` in its own request payload. An
    attestation that cannot be resolved authoritatively is treated exactly like
    one that resolved to ``False`` -- the control fails closed, not open.
    """

    code = "attestation_unavailable"


class KillSwitchUnavailableError(GlassBoxError):
    """A tenant or global emergency-stop state could not be determined.

    Callers must treat this identically to an engaged switch for any
    non-advisory action -- an unreachable kill switch is not a reason to
    proceed as if nothing were wrong.
    """

    code = "kill_switch_unavailable"


# --------------------------------------------------------------------------- #
# Tool registry (GB-013)
# --------------------------------------------------------------------------- #


class ToolRegistryError(GlassBoxError):
    """Base class for tool-registry failures."""

    code = "tool_registry_error"


class ToolNotGovernedError(ToolRegistryError):
    """No registry entry exists for this tool name and definition digest.

    Deny by default (invariant I4): an unregistered tool, or one whose
    definition digest no longer matches the registered one, is refused --
    never downgraded to a generic, low-risk default the way v1's unmapped
    tools fell through to ``DecisionType.CUSTOM``.
    """

    code = "tool_not_governed"


class ToolRegistryUnavailableError(ToolRegistryError):
    """No active tool registry bundle could be loaded."""

    code = "tool_registry_unavailable"


class ToolQuarantinedError(ToolRegistryError):
    """The tool's definition changed after approval and awaits re-approval.

    A rug pull (GB-014): a definition digest presented for a tool_name that
    differs from the digest last approved for it is never trusted implicitly.
    The tool is quarantined until an operator explicitly re-approves the new
    definition.
    """

    code = "tool_quarantined"


# --------------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------------- #


class RiskError(GlassBoxError):
    """Base class for risk evaluation failures."""

    code = "risk_error"


class RiskModelUnavailableError(RiskError):
    """The pinned risk model version could not be resolved."""

    code = "risk_model_unavailable"


# --------------------------------------------------------------------------- #
# External governance state
# --------------------------------------------------------------------------- #


class LimitStoreUnavailable(GlassBoxError):
    """The limit store could not be reached or did not answer atomically.

    Callers **must** fail closed for any action whose consequence class is not
    :attr:`~glassbox.domain.action.ConsequenceClass.ADVISORY`. Failing open here
    is the defect that made the v1 velocity breaker admit everything during a
    Redis outage.
    """

    code = "limit_store_unavailable"


class BaselineStoreUnavailable(GlassBoxError):
    """The behavioural baseline store could not be reached."""

    code = "baseline_store_unavailable"


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


class EvidenceError(GlassBoxError):
    """Base class for evidence capture and verification failures."""

    code = "evidence_error"


class EvidenceWriteError(EvidenceError):
    """Evidence could not be made durable.

    Invariant **I1**: when this is raised, no side effect may be dispatched.
    """

    code = "evidence_write_failed"


class EvidenceIntegrityError(EvidenceError):
    """An evidence segment failed integrity verification."""

    code = "evidence_integrity_failed"


class SigningUnavailableError(EvidenceError):
    """The MAC signer (KMS) is unavailable.

    Degrading to an *unkeyed* digest is forbidden: that is precisely the v1
    defect which allowed a forged record to re-verify as intact.
    """

    code = "signing_unavailable"


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


class DispatchError(GlassBoxError):
    """Base class for side-effect dispatch failures."""

    code = "dispatch_error"


class DispatchTimeoutError(DispatchError):
    """The dispatched action did not complete within its budget.

    The outcome is *indeterminate*: the effect may or may not have occurred. It
    must be recorded as such rather than as a clean failure.
    """

    code = "dispatch_timeout"


class DispatchRefusedError(DispatchError):
    """Dispatch was attempted without a valid, durable evidence receipt.

    This is the runtime guard for invariant **I1** and should never fire in a
    correctly wired system.
    """

    code = "dispatch_refused_no_evidence"


class ToolOutputQuarantinedError(DispatchError):
    """The dispatched effect ran, but its result matched a prompt-injection
    pattern and is quarantined -- never fed forward as trusted content.

    The effect itself is not undone: whether it was authorised to run was
    already decided by mandate/policy/risk before dispatch. This guards a
    separate, later risk -- indirect prompt injection, where a compromised or
    malicious tool result carries instructions meant for the agent's next
    reasoning step. The outcome is recorded as ``FAILED``, and -- like every
    other result -- only its digest is ever evidenced, never the flagged
    content itself.
    """

    code = "tool_output_quarantined"


# --------------------------------------------------------------------------- #
# Approval workflow (Workstream D)
# --------------------------------------------------------------------------- #


class ApprovalError(GlassBoxError):
    """Base class for approval-workflow failures raised by the app layer."""

    code = "approval_error"


class ApprovalNotFoundError(ApprovalError):
    """No approval workflow exists for the given decision."""

    code = "approval_not_found"


class ApprovalGatewayUnavailableError(ApprovalError):
    """No workflow gateway is wired into this runtime.

    Raised rather than silently no-oping: a caller invoking an approval
    transition on a runtime with no approval routing configured has a
    deployment defect, not a business outcome.
    """

    code = "approval_gateway_unavailable"


class ApprovalTransitionError(ApprovalError):
    """The requested transition is not valid for the approval's current state."""

    code = "approval_transition_invalid"
