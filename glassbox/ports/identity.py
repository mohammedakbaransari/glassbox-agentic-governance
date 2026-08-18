"""Identity verification port (GB-002, WS-1).

The single entry point through which an identity may enter the decision path.
Nothing else in the system is permitted to construct a
:class:`~glassbox.domain.identity.VerifiedPrincipal`.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.identity import RawCredential, VerifiedPrincipal

__all__ = ["IdentityVerifier"]


@runtime_checkable
class IdentityVerifier(Protocol):
    """Turns an unverified credential into a verified principal.

    Conforming adapters (SPIFFE/mTLS, OIDC) must:

    * validate the credential cryptographically before returning;
    * derive ``tenant_id``, ``agent_ref`` and ``delegating_subject`` **only** from
      verified claims, never from request headers or the request body;
    * raise rather than return a partially verified principal.
    """

    def verify(self, credential: RawCredential, *, now: float) -> VerifiedPrincipal:
        """Verify ``credential`` and return the principal it attests to.

        Args:
            credential: The unverified credential presented by the caller.
            now: Current time in POSIX epoch seconds, from the injected
                :class:`~glassbox.ports.clock.Clock`.

        Returns:
            The verified principal.

        Raises:
            glassbox.domain.errors.IdentityError: If the credential is malformed,
                untrusted, or fails signature verification.
            glassbox.domain.errors.CredentialExpiredError: If it is outside its
                validity window at ``now``.
            glassbox.domain.errors.DelegationError: If the delegation chain is
                unverifiable or widens authority.
        """
        ...

    def assert_matches_assertion(
        self,
        principal: VerifiedPrincipal,
        *,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> None:
        """Reject a caller-asserted identity that contradicts the principal.

        Transport headers such as ``X-Tenant-ID`` and ``X-User-ID`` may still be
        sent by clients. They are treated as *claims to be checked*, never as
        inputs: a mismatch is a spoofing attempt and must be refused rather than
        silently overridden, so that the attempt is visible in evidence.

        Args:
            principal: The verified principal.
            asserted_tenant_id: Tenant claimed by the transport, if any.
            asserted_subject: Subject claimed by the transport, if any.

        Raises:
            glassbox.domain.errors.IdentityError: If either assertion is present
                and disagrees with the verified principal.
        """
        ...
