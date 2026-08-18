"""Minimal, explicit JWS verification (GB-009).

Deliberately not a general-purpose JWT library. It supports exactly two
algorithms, chosen by an explicit allow-list rather than trusted from the
token's own ``alg`` header -- the classic "alg confusion" attack lets a token
choose ``none`` or switch a signature scheme to one the verifier will accept
using material that isn't a real signing key. Nothing here ever honours the
token's opinion of how it should be checked.

Built on ``cryptography`` alone (already an optional extra) rather than adding a
new JWT dependency: a compact JWS is base64url segments plus a signature, and
verifying one is a few lines once the library supplies the primitives.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Mapping

from glassbox.domain.errors import IdentityError

__all__ = ["SUPPORTED_ALGORITHMS", "VerifiedJws", "verify_compact_jws"]

#: The only algorithms this verifier will honour, regardless of what a token's
#: header claims. Anything else -- including ``none`` -- is refused.
SUPPORTED_ALGORITHMS = ("RS256", "ES256")


@dataclass(frozen=True, slots=True)
class VerifiedJws:
    """A JWS whose signature has been checked against a specific key.

    Attributes:
        header: The decoded JOSE header.
        claims: The decoded payload.
        key_id: The ``kid`` used to select the verification key.
    """

    header: Mapping[str, Any]
    claims: Mapping[str, Any]
    key_id: str


def _b64url_decode(segment: str) -> bytes:
    """Decode a base64url segment, restoring the padding a JWS omits."""
    padded = segment + "=" * (-len(segment) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise IdentityError("credential is not valid base64url", cause=type(exc).__name__) from exc


def _decode_json_segment(raw: bytes, *, part: str) -> Dict[str, Any]:
    """Decode a JWS segment as a JSON object."""
    try:
        decoded = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise IdentityError(
            f"credential {part} is not valid JSON", cause=type(exc).__name__
        ) from exc
    if not isinstance(decoded, dict):
        raise IdentityError(f"credential {part} must be a JSON object", part=part)
    return decoded


def verify_compact_jws(token: str, *, public_key: Any) -> VerifiedJws:
    """Verify a compact JWS and return its header and claims.

    Args:
        token: The ``header.payload.signature`` compact serialisation.
        public_key: A ``cryptography`` public key object matching the header's
            ``alg``: an RSA public key for ``RS256``, an EC public key for
            ``ES256``. Resolving *which* key from a ``kid`` is the caller's
            responsibility (normally a JWKS lookup); this function only verifies
            the signature against the key it is given.

    Returns:
        The verified header and claims. Callers still must check ``iss``,
        ``aud``, ``exp``/``nbf`` and any custom claims themselves -- this
        function proves only that the bytes were signed by ``public_key``.

    Raises:
        glassbox.domain.errors.IdentityError: If the token is malformed, uses an
            algorithm outside :data:`SUPPORTED_ALGORITHMS`, or the signature does
            not verify.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise IdentityError("credential is not a three-part compact JWS", part_count=len(parts))
    header_b64, payload_b64, signature_b64 = parts

    header = _decode_json_segment(_b64url_decode(header_b64), part="header")
    algorithm = header.get("alg")
    if algorithm not in SUPPORTED_ALGORITHMS:
        raise IdentityError(
            "credential algorithm is not permitted",
            algorithm=str(algorithm),
            permitted=",".join(SUPPORTED_ALGORITHMS),
        )

    signature = _b64url_decode(signature_b64)
    signing_input = f"{header_b64}.{payload_b64}".encode("ascii")
    _verify_signature(algorithm, public_key, signature, signing_input)

    claims = _decode_json_segment(_b64url_decode(payload_b64), part="payload")
    return VerifiedJws(header=header, claims=claims, key_id=str(header.get("kid", "")))


def _verify_signature(algorithm: str, public_key: Any, signature: bytes, message: bytes) -> None:
    """Verify ``signature`` over ``message``, translating any failure uniformly.

    ``cryptography`` raises ``InvalidSignature`` for a bad signature and a
    variety of ``ValueError``/type errors for a key of the wrong shape (an EC
    key handed to the RSA path, for instance). Both must fail the same way: an
    unverifiable credential, never a crash that skips verification.
    """
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa
        from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
    except ImportError as exc:
        raise IdentityError(
            "JWS verification requires the 'crypto' extra",
            remedy="pip install 'glassbox-governance[crypto]'",
        ) from exc

    try:
        if algorithm == "RS256":
            if not isinstance(public_key, rsa.RSAPublicKey):
                raise IdentityError("RS256 requires an RSA public key")
            public_key.verify(signature, message, padding.PKCS1v15(), hashes.SHA256())
        elif algorithm == "ES256":
            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise IdentityError("ES256 requires an EC public key")
            # JWS uses the fixed-width R||S encoding; cryptography's ECDSA verify
            # expects DER, so the wire format must be converted before checking.
            if len(signature) != 64:
                raise IdentityError(
                    "ES256 signature must be 64 bytes (R||S)", length=len(signature)
                )
            r = int.from_bytes(signature[:32], "big")
            s = int.from_bytes(signature[32:], "big")
            der_signature = encode_dss_signature(r, s)
            public_key.verify(der_signature, message, ec.ECDSA(hashes.SHA256()))
        else:  # pragma: no cover - unreachable, guarded by SUPPORTED_ALGORITHMS
            raise IdentityError("unsupported algorithm", algorithm=algorithm)
    except InvalidSignature as exc:
        raise IdentityError("credential signature does not verify") from exc
    except IdentityError:
        raise
    except (ValueError, TypeError) as exc:
        raise IdentityError(
            "credential signature could not be verified", cause=type(exc).__name__
        ) from exc
