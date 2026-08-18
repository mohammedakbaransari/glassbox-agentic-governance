"""Policy decision point port (GB-002, WS-3)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.decision import AuthorizationDecision, AuthorizationRequest

__all__ = ["PolicyDecisionPoint"]


@runtime_checkable
class PolicyDecisionPoint(Protocol):
    """Evaluates a request against the active, signed policy bundle.

    Conforming adapters must be:

    * **pure** -- no I/O during evaluation; the bundle is loaded and verified
      beforehand, so evaluation cannot block on a network call;
    * **deterministic** -- identical requests against an identical bundle produce
      identical decisions forever, which is what makes replay meaningful;
    * **deny by default** -- absence of a matching allow rule is a denial;
    * **attributable** -- every non-denying decision cites ``policy_bundle_id``
      and ``policy_bundle_sha256``, which the domain type enforces.

    v1 evaluated 35 Python callables that could execute arbitrary code, needed a
    32-worker timeout pool to contain them, and had no bundle identity at all.
    """

    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        """Return the authorization decision for ``request``.

        Args:
            request: The server-derived request. Contains no caller-asserted
                governance inputs.

        Returns:
            An allow, require-approval or deny decision. Never ``None``.

        Raises:
            glassbox.domain.errors.PolicyBundleUnavailableError: If no ACTIVE,
                signature-verified bundle is loaded. Callers must fail closed.
            glassbox.domain.errors.PolicyBundleSignatureError: If the loaded
                bundle fails digest or signature verification.
        """
        ...

    def active_bundle_digest(self, tenant_id: str) -> str:
        """Return the SHA-256 digest of the bundle currently active for a tenant.

        Exposed separately so the decision service can record the digest even on
        paths that deny before policy evaluation runs.

        Raises:
            glassbox.domain.errors.PolicyBundleUnavailableError: If no bundle is
                active for the tenant.
        """
        ...
