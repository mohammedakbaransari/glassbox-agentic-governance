"""Reference identity, policy and risk adapters (GB-003).

**Development only.** Each is the minimum implementation that is *honest* about
its port's contract, so that the decision service (GB-008) can be built and
tested before the real adapters land in GB-009, GB-018 and GB-021.

None of them cuts a corner that the review identified as a defect:

* :class:`DevIdentityVerifier` derives the tenant from the credential and
  **refuses** a transport header that disagrees with it. There is no shared API
  key.
* :class:`AllowListPolicyDecisionPoint` **denies by default** and cites the
  bundle digest that authorised every allow.
* :class:`ReferenceRiskEngine` is deterministic, reads no clock, aggregates
  **without saturating**, and applies the consequence floor.
"""

from __future__ import annotations

import hashlib
import threading
from typing import Dict, Iterable, Optional, Set

from glassbox.adapters.outbound.identity.assertions import check_assertion
from glassbox.app.config import GlassBoxConfig
from glassbox.domain.decision import (
    AuthorizationDecision,
    AuthorizationRequest,
    DenialReason,
)
from glassbox.domain.errors import (
    IdentityError,
    PolicyBundleUnavailableError,
    RiskModelUnavailableError,
)
from glassbox.domain.identity import (
    CredentialType,
    DelegationChain,
    RawCredential,
    VerifiedPrincipal,
)
from glassbox.domain.risk import MAX_RISK_SCORE, RiskInputs, RiskScore
from glassbox.ports.identity import IdentityVerifier
from glassbox.ports.policy import PolicyDecisionPoint
from glassbox.ports.risk import RiskEngine

__all__ = [
    "DevIdentityVerifier",
    "AllowListPolicyDecisionPoint",
    "ReferenceRiskEngine",
    "build_identity_verifier",
    "build_policy_decision_point",
    "build_risk_engine",
]

#: Development credential format: ``dev:<tenant>:<agent_ref>:<instance>``.
_DEV_CREDENTIAL_PARTS = 4

#: Version of the reference scoring model, recorded on every evidence row.
REFERENCE_RISK_MODEL_VERSION = "reference-v0.1.0"


class DevIdentityVerifier:
    """Verifies a structured development credential.

    The credential is ``dev:<tenant>:<agent_ref>:<instance>``. It is not signed,
    which is why this adapter is development-only. What it demonstrates is the
    property the real verifier must keep: **tenancy is read out of the credential
    and nowhere else**, so a request header cannot select a tenant.

    Args:
        validity_seconds: How long an issued principal remains valid.
        reject_mismatched_assertions: When ``True``, a transport assertion that
            disagrees with the verified principal is refused rather than ignored,
            so the spoofing attempt surfaces in evidence.
    """

    __slots__ = ("_validity_seconds", "_reject_mismatched")

    def __init__(
        self, *, validity_seconds: float = 3600.0, reject_mismatched_assertions: bool = True
    ) -> None:
        self._validity_seconds = validity_seconds
        self._reject_mismatched = reject_mismatched_assertions

    def verify(self, credential: RawCredential, *, now: float) -> VerifiedPrincipal:
        """Verify ``credential`` and return the principal it attests to.

        Raises:
            IdentityError: If the credential is not a development credential or
                is malformed. A partially verified principal is never returned.
        """
        if not isinstance(credential, RawCredential):
            raise IdentityError(
                "verify requires a RawCredential",
                offending_type=type(credential).__name__,
            )
        parts = credential.material.split(":")
        if len(parts) != _DEV_CREDENTIAL_PARTS or parts[0] != "dev":
            raise IdentityError(
                "credential is not a recognised development credential",
                credential_type=credential.credential_type.value,
                adapter="DevIdentityVerifier",
            )

        _, tenant_id, agent_ref, instance_id = parts
        try:
            return VerifiedPrincipal(
                agent_ref=agent_ref,
                agent_instance_id=instance_id,
                tenant_id=tenant_id,
                credential_type=CredentialType.OIDC,
                credential_id=f"dev/{tenant_id}/{agent_ref}",
                issued_at=now,
                expires_at=now + self._validity_seconds,
                delegation_chain=DelegationChain(),
            )
        except Exception as exc:
            raise IdentityError(
                "credential claims did not produce a valid principal",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def assert_matches_assertion(
        self,
        principal: VerifiedPrincipal,
        *,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> None:
        """Reject a caller-asserted identity that contradicts the principal.

        Raises:
            IdentityError: On any mismatch, when configured to reject.
        """
        if not self._reject_mismatched:
            return
        check_assertion(
            principal, asserted_tenant_id=asserted_tenant_id, asserted_subject=asserted_subject
        )


class AllowListPolicyDecisionPoint:
    """Deny-by-default evaluation against an explicit allow list.

    A stand-in for the signed declarative bundle of GB-018. It keeps the two
    properties that matter: nothing is permitted unless a rule says so, and every
    allow names the bundle digest that authorised it.

    Args:
        allowed: ``tenant_id -> {action names}``. An action absent from the set,
            or a tenant absent from the mapping, is denied.
        bundle_id: Identifier reported on every decision.
    """

    __slots__ = ("_lock", "_allowed", "_bundle_id", "_available")

    def __init__(
        self,
        allowed: Optional[Dict[str, Iterable[str]]] = None,
        *,
        bundle_id: str = "reference.allowlist",
    ) -> None:
        self._lock = threading.RLock()
        self._allowed: Dict[str, Set[str]] = {
            tenant: set(actions) for tenant, actions in (allowed or {}).items()
        }
        self._bundle_id = bundle_id
        self._available = True

    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return the authorization decision for ``request``.

        Raises:
            PolicyBundleUnavailableError: If no bundle is loaded. The caller must
                fail closed rather than proceed without policy.
        """
        digest = self.active_bundle_digest(request.tenant_id)
        with self._lock:
            permitted = self._allowed.get(request.tenant_id, set())

        if request.action.action in permitted:
            return AuthorizationDecision.allow(
                rationale=f"action {request.action.action!r} is on the tenant allow list",
                policy_bundle_id=self._bundle_id,
                policy_bundle_sha256=digest,
                matched_rules=(f"allowlist.{request.action.action}",),
            )
        return AuthorizationDecision.deny(
            DenialReason.POLICY_DENIED,
            rationale=(
                f"action {request.action.action!r} is not on the allow list for tenant "
                f"{request.tenant_id!r}"
            ),
            policy_bundle_id=self._bundle_id,
            policy_bundle_sha256=digest,
        )

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the digest of the bundle currently active for a tenant."""
        if not self._available:
            raise PolicyBundleUnavailableError(
                "no active policy bundle",
                tenant_id=tenant_id,
                adapter="AllowListPolicyDecisionPoint",
            )
        with self._lock:
            material = "|".join(sorted(self._allowed.get(tenant_id, ())))
        return hashlib.sha256(f"{self._bundle_id}|{tenant_id}|{material}".encode()).hexdigest()

    def allow(self, tenant_id: str, *actions: str) -> None:
        """Add actions to a tenant's allow list."""
        with self._lock:
            self._allowed.setdefault(tenant_id, set()).update(actions)

    def set_available(self, available: bool) -> None:
        """Simulate an unavailable bundle registry, for fail-closed tests."""
        self._available = available


class ReferenceRiskEngine:
    """Deterministic, non-saturating aggregation with a consequence floor.

    The aggregation is ``max(factor scores)`` escalated by the number of factors
    that are themselves elevated. Two properties follow, and GB-021 must keep
    both when it replaces this with a calibrated model:

    * **non-saturating** -- a benign factor can never dilute a severe one, which
      is how v1's weighted mean scored a $50M irreversible transfer at 27.5;
    * **monotonic** -- adding a factor never lowers the score.

    Args:
        model_version: Identifier recorded on every evidence row.
        escalation_per_elevated_factor: Points added for each factor beyond the
            highest that also scores above ``escalation_floor``.
    """

    __slots__ = ("_model_version", "_escalation", "_escalation_floor", "_available")

    def __init__(
        self,
        *,
        model_version: str = REFERENCE_RISK_MODEL_VERSION,
        escalation_per_elevated_factor: float = 5.0,
        escalation_floor: float = 25.0,
    ) -> None:
        self._model_version = model_version
        self._escalation = escalation_per_elevated_factor
        self._escalation_floor = escalation_floor
        self._available = True

    @property
    def model_version(self) -> str:
        """Identifier of the scoring model."""
        return self._model_version

    def score(self, inputs: RiskInputs) -> RiskScore:
        """Score ``inputs`` and return a version-pinned, floored result."""
        return self._score(inputs, self._model_version)

    def score_with_model(self, inputs: RiskInputs, model_version: str) -> RiskScore:
        """Re-score ``inputs`` against a specific historical model version.

        Raises:
            RiskModelUnavailableError: If that version is not available.
        """
        if model_version != self._model_version:
            raise RiskModelUnavailableError(
                "historical risk model is not available in the reference engine",
                requested=model_version,
                available=self._model_version,
            )
        return self._score(inputs, model_version)

    def _score(self, inputs: RiskInputs, model_version: str) -> RiskScore:
        if not self._available:
            raise RiskModelUnavailableError(
                "risk model is unavailable", model_version=model_version
            )
        if not inputs.factors:
            raw = 0.0
        else:
            scores = sorted((factor.score for factor in inputs.factors), reverse=True)
            elevated = sum(1 for value in scores[1:] if value >= self._escalation_floor)
            raw = min(MAX_RISK_SCORE, scores[0] + elevated * self._escalation)
        return RiskScore(
            value=raw, model_version=model_version, inputs=inputs
        ).with_consequence_floor()

    def set_available(self, available: bool) -> None:
        """Simulate an unavailable model, for fail-closed tests."""
        self._available = available


# --------------------------------------------------------------------------- #
# Factories
# --------------------------------------------------------------------------- #


def build_identity_verifier(config: GlassBoxConfig) -> IdentityVerifier:
    """Factory used by the adapter set."""
    return DevIdentityVerifier(
        reject_mismatched_assertions=config.identity.reject_mismatched_assertions
    )


def build_policy_decision_point(config: GlassBoxConfig) -> PolicyDecisionPoint:
    """Factory used by the adapter set."""
    return AllowListPolicyDecisionPoint()


def build_risk_engine(config: GlassBoxConfig) -> RiskEngine:
    """Factory used by the adapter set."""
    return ReferenceRiskEngine()
