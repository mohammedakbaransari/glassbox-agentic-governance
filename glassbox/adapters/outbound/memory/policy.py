"""Declarative, signed policy decision point (GB-018).

Replaces :class:`~glassbox.adapters.outbound.memory.decisioning.AllowListPolicyDecisionPoint`,
the GB-003 stand-in, with an adapter that evaluates real
:class:`~glassbox.domain.policy_bundle.PolicyBundle` data. Evaluation itself
touches no I/O and runs no rule-defined code (GB-020): a bundle is verified
once, at load time, and every subsequent :meth:`decide` call is pure pattern
matching over already-validated data.
"""

from __future__ import annotations

import threading
from typing import Dict, Optional

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.decision import AuthorizationDecision, AuthorizationRequest, DenialReason
from glassbox.domain.errors import (
    PolicyBundleSignatureError,
    PolicyBundleUnavailableError,
    SigningUnavailableError,
)
from glassbox.domain.policy_bundle import PolicyBundle, RuleEffect, SignedPolicyBundle
from glassbox.ports.keys import MacSigner
from glassbox.ports.policy import PolicyDecisionPoint

__all__ = ["DeclarativePolicyDecisionPoint"]


class DeclarativePolicyDecisionPoint:
    """Deny-by-default evaluation against a signed, versioned rule bundle.

    Args:
        signer: Used to sign bundles loaded via :meth:`load_bundle`, and to
            verify bundles loaded via :meth:`load_signed_bundle`. The same
            signer that MACs evidence (GB-006) may be reused, or a distinct one
            scoped to policy publication -- either way, verification is
            mandatory before a bundle becomes active.
    """

    __slots__ = ("_signer", "_lock", "_bundles", "_available")

    def __init__(self, signer: MacSigner) -> None:
        self._signer = signer
        self._lock = threading.RLock()
        self._bundles: Dict[str, SignedPolicyBundle] = {}
        self._available = True

    def load_bundle(self, bundle: PolicyBundle) -> SignedPolicyBundle:
        """Sign ``bundle`` with the configured signer and activate it.

        For a trusted local publisher (tests, a single-process deployment). A
        bundle built and signed elsewhere should use :meth:`load_signed_bundle`
        instead, which verifies rather than trusts.

        Raises:
            glassbox.domain.errors.SigningUnavailableError: If the signer
                cannot be reached.
        """
        mac = self._signer.mac(bundle.canonical_payload())
        signed = SignedPolicyBundle(bundle=bundle, mac=mac, signer_key_id=self._signer.key_id)
        with self._lock:
            self._bundles[bundle.tenant_id] = signed
        return signed

    def load_signed_bundle(self, signed: SignedPolicyBundle) -> None:
        """Verify and activate an externally-signed bundle.

        Raises:
            glassbox.domain.errors.PolicyBundleSignatureError: If the signature
                does not verify. The bundle is never activated.
            glassbox.domain.errors.PolicyBundleUnavailableError: If the signer
                cannot resolve the key that supposedly signed it.
        """
        try:
            valid = self._signer.verify(
                signed.bundle.canonical_payload(), signed.mac, key_id=signed.signer_key_id
            )
        except SigningUnavailableError as exc:
            raise PolicyBundleUnavailableError(
                "policy bundle signer could not be verified",
                tenant_id=signed.bundle.tenant_id,
                bundle_id=signed.bundle.bundle_id,
            ) from exc
        if not valid:
            raise PolicyBundleSignatureError(
                "policy bundle failed signature verification",
                tenant_id=signed.bundle.tenant_id,
                bundle_id=signed.bundle.bundle_id,
            )
        with self._lock:
            self._bundles[signed.bundle.tenant_id] = signed

    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return the authorization decision for ``request``.

        Raises:
            glassbox.domain.errors.PolicyBundleUnavailableError: If no bundle
                is active for the tenant.
        """
        signed = self._active(request.tenant_id)
        bundle = signed.bundle
        digest = bundle.digest()
        rule = bundle.matching_rule(request.action)

        if rule is None:
            return AuthorizationDecision.deny(
                DenialReason.POLICY_DENIED,
                rationale="no rule in the active bundle matches this action; deny by default",
                policy_bundle_id=bundle.bundle_id,
                policy_bundle_sha256=digest,
            )
        if rule.effect is RuleEffect.DENY:
            return AuthorizationDecision.deny(
                DenialReason.POLICY_DENIED,
                rationale=rule.rationale or f"denied by rule {rule.name!r}",
                policy_bundle_id=bundle.bundle_id,
                policy_bundle_sha256=digest,
                matched_rules=(rule.name,),
            )
        if rule.effect is RuleEffect.REQUIRE_APPROVAL:
            return AuthorizationDecision.require_approval(
                rationale=rule.rationale or f"rule {rule.name!r} requires human approval",
                policy_bundle_id=bundle.bundle_id,
                policy_bundle_sha256=digest,
                matched_rules=(rule.name,),
            )
        return AuthorizationDecision.allow(
            rationale=rule.rationale or f"permitted by rule {rule.name!r}",
            policy_bundle_id=bundle.bundle_id,
            policy_bundle_sha256=digest,
            matched_rules=(rule.name,),
        )

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the digest of the bundle currently active for a tenant."""
        return self._active(tenant_id).bundle.digest()

    def _active(self, tenant_id: str) -> SignedPolicyBundle:
        with self._lock:
            if not self._available:
                raise PolicyBundleUnavailableError(
                    "policy decision point is unreachable",
                    tenant_id=tenant_id,
                    adapter="DeclarativePolicyDecisionPoint",
                )
            signed = self._bundles.get(tenant_id)
        if signed is None:
            raise PolicyBundleUnavailableError(
                "no active policy bundle for tenant",
                tenant_id=tenant_id,
                adapter="DeclarativePolicyDecisionPoint",
            )
        return signed

    def set_available(self, available: bool) -> None:
        """Simulate an unavailable bundle registry, for fail-closed tests."""
        with self._lock:
            self._available = available


def build_declarative_policy_decision_point(
    config: GlassBoxConfig, *, signer: MacSigner
) -> PolicyDecisionPoint:
    """Factory for a durable adapter set. No bundle is preloaded.

    Unlike ``build_policy_decision_point`` (the GB-003
    ``AllowListPolicyDecisionPoint`` stand-in wired by
    :func:`~glassbox.adapters.outbound.memory.memory_adapter_set` by default),
    this factory needs a signer and is opted into explicitly rather than
    replacing the default -- swapping every existing caller's policy adapter is
    a decision for a dedicated migration, not a side effect of adding one.
    """
    del config  # unused: the declarative PDP is populated by the caller
    return DeclarativePolicyDecisionPoint(signer)
