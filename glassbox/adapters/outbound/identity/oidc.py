"""OIDC/JWT identity verifier (GB-009).

Verifies a short-lived OIDC access or ID token and derives a
:class:`~glassbox.domain.identity.VerifiedPrincipal` from its claims -- never
from a request header, which is what let v1's ``X-Tenant-ID`` select any tenant.

Key resolution is injected as a :class:`JwksProvider` rather than fetched by
this class directly: fetching a JWKS document is I/O with its own caching,
retry and rotation concerns, and mixing it into the verifier would make the
verifier's own logic (claim validation, principal construction) untestable
without a network. A conforming provider normally wraps an HTTP client with a
cache in front of the issuer's ``/.well-known/jwks.json``.

Trust is anchored on **issuer and audience**, checked before anything else is
read from the claims: a token from the wrong issuer or for the wrong audience
must never reach the point of being asked "which tenant does this claim to
be?".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

from glassbox.adapters.outbound.identity.assertions import check_assertion
from glassbox.adapters.outbound.identity.jwt_verify import verify_compact_jws
from glassbox.domain.errors import CredentialExpiredError, DelegationError, IdentityError
from glassbox.domain.identity import (
    CredentialType,
    DelegationChain,
    RawCredential,
    VerifiedPrincipal,
)

__all__ = ["ClaimMapping", "JwksProvider", "OidcIdentityVerifier"]

#: Seconds of allowance for clock skew between the issuer and this process.
_CLOCK_SKEW_TOLERANCE_S = 60.0


@dataclass(frozen=True, slots=True)
class ClaimMapping:
    """Which token claims carry which principal fields.

    Every identity provider names things differently; this is the one place
    that knowledge lives; nothing else in the verifier assumes a claim name.

    Attributes:
        tenant_claim: Claim carrying the tenant identifier.
        agent_ref_claim: Claim carrying the stable logical agent identity.
        agent_instance_claim: Claim carrying this running instance's identity.
            Falls back to ``jti`` (the token id) when the claim is absent, since
            a fresh token is normally minted per instance.
        delegating_subject_claim: Claim carrying the human or service on whose
            behalf the agent acts. Optional.
    """

    tenant_claim: str = "tenant_id"
    agent_ref_claim: str = "sub"
    agent_instance_claim: str = "instance_id"
    delegating_subject_claim: str = "act"


class JwksProvider:
    """Resolves a verification key by ``kid``.

    Not instantiated directly: implement ``get_key`` against your own cache and
    HTTP client. Defined as a class (rather than a bare ``Protocol``) so it can
    carry a useful ``__repr__`` in error messages; duck typing still works for
    any object with a matching ``get_key`` method.
    """

    def get_key(self, key_id: str) -> Any:
        """Return the ``cryptography`` public key for ``key_id``.

        Raises:
            glassbox.domain.errors.IdentityError: If the key is unknown or the
                provider cannot be reached. Never returns a placeholder key.
        """
        raise NotImplementedError


class OidcIdentityVerifier:
    """Verifies OIDC tokens and derives principals from trusted claims only.

    Args:
        issuer: The only accepted ``iss`` value.
        audience: The only accepted ``aud`` value (or member, if ``aud`` is a list).
        jwks: Resolves verification keys by ``kid``.
        claims: Names of the claims carrying principal fields.
        reject_mismatched_assertions: Safety switch mirroring
            :attr:`~glassbox.app.config.IdentityConfig.reject_mismatched_assertions`.
    """

    __slots__ = ("_issuer", "_audience", "_jwks", "_claims", "_reject_mismatched")

    def __init__(
        self,
        *,
        issuer: str,
        audience: str,
        jwks: JwksProvider,
        claims: Optional[ClaimMapping] = None,
        reject_mismatched_assertions: bool = True,
    ) -> None:
        if not issuer or not audience:
            raise IdentityError("an OIDC verifier requires both issuer and audience")
        self._issuer = issuer
        self._audience = audience
        self._jwks = jwks
        self._claims = claims or ClaimMapping()
        self._reject_mismatched = reject_mismatched_assertions

    def verify(self, credential: RawCredential, *, now: float) -> VerifiedPrincipal:
        """Verify an OIDC token and return the principal it attests to.

        Raises:
            glassbox.domain.errors.IdentityError: If the token is malformed, its
                signature does not verify, or ``iss``/``aud`` do not match.
            glassbox.domain.errors.CredentialExpiredError: If ``exp``/``nbf``
                place ``now`` outside the token's validity window.
            glassbox.domain.errors.DelegationError: If an ``act`` claim is
                present but malformed.
        """
        if not isinstance(credential, RawCredential):
            raise IdentityError(
                "verify requires a RawCredential", offending_type=type(credential).__name__
            )
        if credential.credential_type is not CredentialType.OIDC:
            raise IdentityError(
                "OidcIdentityVerifier only accepts OIDC credentials",
                credential_type=credential.credential_type.value,
            )

        header = self._peek_header(credential.material)
        key_id = str(header.get("kid", ""))
        if not key_id:
            raise IdentityError("credential header is missing 'kid'")
        public_key = self._jwks.get_key(key_id)

        verified = verify_compact_jws(credential.material, public_key=public_key)
        claims = verified.claims

        self._require_issuer_and_audience(claims)
        self._require_temporal_validity(claims, now=now)

        tenant_id = _require_claim(claims, self._claims.tenant_claim)
        agent_ref = _require_claim(claims, self._claims.agent_ref_claim)
        agent_instance_id = str(
            claims.get(self._claims.agent_instance_claim) or claims.get("jti") or agent_ref
        )
        delegating_subject = claims.get(self._claims.delegating_subject_claim)

        try:
            return VerifiedPrincipal(
                agent_ref=agent_ref,
                agent_instance_id=agent_instance_id,
                tenant_id=tenant_id,
                credential_type=CredentialType.OIDC,
                credential_id=str(claims.get("jti", key_id)),
                issued_at=float(claims.get("iat", now)),
                expires_at=float(claims["exp"]),
                delegating_subject=str(delegating_subject) if delegating_subject else None,
                delegation_chain=DelegationChain(),
                claims=tuple(sorted((k, v) for k, v in claims.items() if isinstance(v, str))),
            )
        except (IdentityError, DelegationError):
            raise
        except Exception as exc:
            raise IdentityError(
                "verified claims did not produce a valid principal",
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
        """Reject a caller-asserted identity that contradicts the principal."""
        if not self._reject_mismatched:
            return
        check_assertion(
            principal, asserted_tenant_id=asserted_tenant_id, asserted_subject=asserted_subject
        )

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    @staticmethod
    def _peek_header(token: str) -> Mapping[str, Any]:
        """Decode only the header, to learn ``kid`` before fetching a key.

        This is not a trust decision: the header is unverified until
        :func:`verify_compact_jws` checks the signature over it moments later.
        """
        import base64
        import json

        parts = token.split(".")
        if len(parts) != 3:
            raise IdentityError("credential is not a three-part compact JWS")
        padded = parts[0] + "=" * (-len(parts[0]) % 4)
        try:
            return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (ValueError, UnicodeDecodeError) as exc:
            raise IdentityError(
                "credential header could not be decoded", cause=type(exc).__name__
            ) from exc

    def _require_issuer_and_audience(self, claims: Mapping[str, Any]) -> None:
        """Anchor trust before anything else is read from the claims."""
        if claims.get("iss") != self._issuer:
            raise IdentityError(
                "credential issuer is not trusted",
                expected=self._issuer,
                actual=str(claims.get("iss")),
            )
        audience = claims.get("aud")
        matches = audience == self._audience or (
            isinstance(audience, (list, tuple)) and self._audience in audience
        )
        if not matches:
            raise IdentityError(
                "credential audience does not match",
                expected=self._audience,
                actual=str(audience),
            )

    @staticmethod
    def _require_temporal_validity(claims: Mapping[str, Any], *, now: float) -> None:
        """Check exp/nbf with a small, explicit clock-skew tolerance."""
        expires_at = claims.get("exp")
        if expires_at is None:
            raise IdentityError("credential is missing 'exp'")
        if now > float(expires_at) + _CLOCK_SKEW_TOLERANCE_S:
            raise CredentialExpiredError(
                "credential has expired", expires_at=str(expires_at), now=str(now)
            )
        not_before = claims.get("nbf")
        if not_before is not None and now < float(not_before) - _CLOCK_SKEW_TOLERANCE_S:
            raise CredentialExpiredError(
                "credential is not yet valid", not_before=str(not_before), now=str(now)
            )


def _require_claim(claims: Mapping[str, Any], name: str) -> str:
    """Return a required claim as a non-empty string, or raise."""
    value = claims.get(name)
    if not value or not isinstance(value, str):
        raise IdentityError("credential is missing a required claim", claim=name)
    return value
