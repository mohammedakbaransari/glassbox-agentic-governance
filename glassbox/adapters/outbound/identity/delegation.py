"""Cryptographically verified delegation chains (GB-017).

Each hop in a :class:`~glassbox.domain.identity.DelegationChain` must be a
verifiable token, not a value the caller merely asserts -- otherwise nothing
stops an agent from presenting a chain that grants itself capabilities no one
actually delegated. :func:`verify_delegation_chain` verifies each hop's JWS
signature independently (the same primitives GB-009 built for the primary
credential) before handing the resulting hops to
:class:`~glassbox.domain.identity.DelegationChain`, whose constructor is what
actually rejects a widening or validity-extending hop (invariant already
enforced there since GB-002).
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from glassbox.adapters.outbound.identity.jwt_verify import verify_compact_jws
from glassbox.adapters.outbound.identity.oidc import JwksProvider
from glassbox.domain.errors import IdentityError
from glassbox.domain.identity import DelegationChain, DelegationHop, SubjectType

__all__ = ["verify_delegation_chain"]


def verify_delegation_chain(
    hop_tokens: Sequence[str], *, jwks: JwksProvider, now: float
) -> DelegationChain:
    """Verify every hop token and assemble the resulting chain, root first.

    Args:
        hop_tokens: Compact JWS tokens, root first, leaf (the acting agent)
            last. An empty sequence yields an empty chain.
        jwks: Resolves each hop's verification key by its own ``kid`` --
            different hops may be signed by different issuers.
        now: Evaluation time. Individual hop validity is enforced by
            :meth:`~glassbox.domain.identity.DelegationChain.is_valid_at`
            elsewhere; this function only verifies signatures and shape.

    Returns:
        A :class:`~glassbox.domain.identity.DelegationChain`. Its own
        constructor rejects a hop that widens authority or extends validity
        beyond its delegator -- that check cannot be bypassed by calling this
        function, because it happens inside the domain type itself.

    Raises:
        glassbox.domain.errors.IdentityError: If any hop token is malformed,
            uses an unpermitted algorithm, fails signature verification, or is
            missing a required claim.
        glassbox.domain.errors.DelegationError: If the chain widens authority,
            extends validity, or the leaf/root do not match what the domain
            constructor requires.
    """
    del now  # unused directly: validity-window enforcement lives on the chain
    hops = tuple(_verify_hop(token, jwks=jwks) for token in hop_tokens)
    return DelegationChain(hops)


def _verify_hop(token: str, *, jwks: JwksProvider) -> DelegationHop:
    header = _peek_header(token)
    key_id = str(header.get("kid", ""))
    if not key_id:
        raise IdentityError("delegation hop is missing 'kid'")
    public_key = jwks.get_key(key_id)
    verified = verify_compact_jws(token, public_key=public_key)
    claims = verified.claims

    subject = _require_claim(claims, "sub")
    subject_type_raw = _require_claim(claims, "subject_type")
    try:
        subject_type = SubjectType(subject_type_raw)
    except ValueError as exc:
        raise IdentityError(
            "delegation hop has an unsupported subject_type", value=str(subject_type_raw)
        ) from exc
    capabilities = claims.get("capabilities") or ()
    if not isinstance(capabilities, (list, tuple)):
        raise IdentityError("delegation hop 'capabilities' must be a list")

    try:
        return DelegationHop(
            subject=subject,
            subject_type=subject_type,
            capabilities=frozenset(str(item) for item in capabilities),
            issued_at=float(_require_claim(claims, "iat")),
            expires_at=float(_require_claim(claims, "exp")),
        )
    except IdentityError:
        raise
    except Exception as exc:
        raise IdentityError(
            "delegation hop claims did not produce a valid hop",
            cause=type(exc).__name__,
            detail=str(exc),
        ) from exc


def _require_claim(claims: Mapping[str, Any], name: str) -> Any:
    value = claims.get(name)
    if value in (None, ""):
        raise IdentityError("delegation hop is missing a required claim", claim=name)
    return value


def _peek_header(token: str) -> Mapping[str, Any]:
    """Decode only the header, to learn ``kid`` before fetching a key.

    Not a trust decision: the header is unverified until
    :func:`~glassbox.adapters.outbound.identity.jwt_verify.verify_compact_jws`
    checks the signature over it moments later.
    """
    import base64
    import json

    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("delegation hop is not a three-part compact JWS")
    padded = parts[0] + "=" * (-len(parts[0]) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(padded.encode("ascii")))
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdentityError(
            "delegation hop header could not be decoded", cause=type(exc).__name__
        ) from exc
