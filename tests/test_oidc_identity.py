"""Tests for OIDC identity verification (GB-009).

Uses real RSA and EC keys and real signatures throughout -- not a mocked
signature check -- so a passing suite proves the verifier actually validates
cryptography, not merely that it calls a function named ``verify``.
"""

from __future__ import annotations

import base64
import json
from typing import Any, Dict, Optional

import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.hazmat.primitives.asymmetric.ec import ECDSA
from cryptography.hazmat.primitives.asymmetric.padding import PKCS1v15
from cryptography.hazmat.primitives.hashes import SHA256

from glassbox.adapters.outbound.identity import (
    ClaimMapping,
    OidcIdentityVerifier,
    StaticJwksProvider,
    check_assertion,
    verify_compact_jws,
)
from glassbox.adapters.outbound.identity.jwt_verify import SUPPORTED_ALGORITHMS
from glassbox.domain.errors import CredentialExpiredError, IdentityError
from glassbox.domain.identity import CredentialType, RawCredential

NOW = 1_760_000_000.0
ISSUER = "https://issuer.example.com"
AUDIENCE = "glassbox-api"


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _sign_rs256(private_key: Any, header: Dict[str, Any], payload: Dict[str, Any]) -> str:
    signing_input = f"{_b64url(_json(header))}.{_b64url(_json(payload))}"
    signature = private_key.sign(signing_input.encode("ascii"), PKCS1v15(), SHA256())
    return f"{signing_input}.{_b64url(signature)}"


def _sign_es256(private_key: Any, header: Dict[str, Any], payload: Dict[str, Any]) -> str:
    from cryptography.hazmat.primitives.asymmetric.utils import (
        decode_dss_signature,
        encode_dss_signature,
    )

    signing_input = f"{_b64url(_json(header))}.{_b64url(_json(payload))}"
    der_signature = private_key.sign(signing_input.encode("ascii"), ECDSA(SHA256()))
    r, s = decode_dss_signature(der_signature)
    # JWS ES256 uses the fixed-width R||S encoding, not DER.
    raw_signature = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"{signing_input}.{_b64url(raw_signature)}"


def _json(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def rsa_keypair() -> Any:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private_key, private_key.public_key()


def ec_keypair() -> Any:
    private_key = ec.generate_private_key(ec.SECP256R1())
    return private_key, private_key.public_key()


def make_token(
    private_key: Any,
    *,
    algorithm: str = "RS256",
    kid: str = "key-1",
    issuer: str = ISSUER,
    audience: Any = AUDIENCE,
    tenant_id: str = "acme",
    sub: str = "agent.treasury-bot",
    exp: float = NOW + 3600.0,
    nbf: Optional[float] = None,
    iat: Optional[float] = NOW,
    jti: str = "token-0001",
    extra_claims: Optional[Dict[str, Any]] = None,
    extra_header: Optional[Dict[str, Any]] = None,
) -> str:
    header = {"alg": algorithm, "typ": "JWT", "kid": kid}
    header.update(extra_header or {})
    payload: Dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": sub,
        "tenant_id": tenant_id,
        "exp": exp,
        "jti": jti,
    }
    if iat is not None:
        payload["iat"] = iat
    if nbf is not None:
        payload["nbf"] = nbf
    payload.update(extra_claims or {})
    signer = _sign_rs256 if algorithm == "RS256" else _sign_es256
    return signer(private_key, header, payload)


def credential(token: str) -> RawCredential:
    return RawCredential(credential_type=CredentialType.OIDC, material=token, presented_at=NOW)


def verifier(public_key: Any, *, kid: str = "key-1", **overrides: Any) -> OidcIdentityVerifier:
    kwargs: Dict[str, Any] = dict(
        issuer=ISSUER, audience=AUDIENCE, jwks=StaticJwksProvider({kid: public_key})
    )
    kwargs.update(overrides)
    return OidcIdentityVerifier(**kwargs)


# --------------------------------------------------------------------------- #
# Low-level JWS verification
# --------------------------------------------------------------------------- #


class TestVerifyCompactJws:
    """Signature verification, isolated from claim semantics."""

    def test_a_genuine_rs256_signature_verifies(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, algorithm="RS256")
        verified = verify_compact_jws(token, public_key=public_key)
        assert verified.claims["tenant_id"] == "acme"

    def test_a_genuine_es256_signature_verifies(self) -> None:
        private_key, public_key = ec_keypair()
        token = make_token(private_key, algorithm="ES256")
        verified = verify_compact_jws(token, public_key=public_key)
        assert verified.claims["sub"] == "agent.treasury-bot"

    def test_a_tampered_payload_is_rejected(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key)
        header_b64, payload_b64, signature_b64 = token.split(".")
        tampered_payload = json.loads(base64.urlsafe_b64decode(payload_b64 + "=="))
        tampered_payload["tenant_id"] = "evilcorp"
        forged = f"{header_b64}.{_b64url(_json(tampered_payload))}.{signature_b64}"
        with pytest.raises(IdentityError):
            verify_compact_jws(forged, public_key=public_key)

    def test_a_signature_from_a_different_key_is_rejected(self) -> None:
        private_key, _public_key = rsa_keypair()
        _other_private, other_public = rsa_keypair()
        token = make_token(private_key)
        with pytest.raises(IdentityError):
            verify_compact_jws(token, public_key=other_public)

    def test_alg_none_is_never_honoured(self) -> None:
        """The classic alg-confusion attack: a token claims it needs no signature."""
        header = _b64url(_json({"alg": "none", "typ": "JWT"}))
        payload = _b64url(_json({"iss": ISSUER, "aud": AUDIENCE, "sub": "x", "exp": NOW + 60}))
        token = f"{header}.{payload}."
        _private_key, public_key = rsa_keypair()
        with pytest.raises(IdentityError) as excinfo:
            verify_compact_jws(token, public_key=public_key)
        assert "algorithm" in str(excinfo.value).lower()

    def test_an_algorithm_outside_the_allow_list_is_rejected(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, algorithm="RS256", extra_header={"alg": "HS256"})
        # Re-sign is irrelevant: the header alg is read before the signature is
        # even attempted, so a mismatched claimed algorithm is refused outright.
        header_b64 = token.split(".")[0]
        forged_header = _b64url(_json({"alg": "HS256", "typ": "JWT", "kid": "key-1"}))
        forged = f"{forged_header}.{token.split('.')[1]}.{token.split('.')[2]}"
        with pytest.raises(IdentityError):
            verify_compact_jws(forged, public_key=public_key)

    def test_an_rsa_key_cannot_verify_an_es256_token(self) -> None:
        """A key of the wrong shape must fail closed, not raise an unrelated crash."""
        private_key, _ = ec_keypair()
        token = make_token(private_key, algorithm="ES256")
        _rsa_private, rsa_public = rsa_keypair()
        with pytest.raises(IdentityError):
            verify_compact_jws(token, public_key=rsa_public)

    def test_a_malformed_token_is_rejected(self) -> None:
        with pytest.raises(IdentityError):
            verify_compact_jws("not-a-jws", public_key=None)

    def test_every_supported_algorithm_is_exercised_above(self) -> None:
        """Guards against a future algorithm being added without a matching test."""
        assert set(SUPPORTED_ALGORITHMS) == {"RS256", "ES256"}


# --------------------------------------------------------------------------- #
# OidcIdentityVerifier
# --------------------------------------------------------------------------- #


class TestOidcIdentityVerifier:
    """Trust is anchored on issuer and audience before any claim is read."""

    def test_a_genuine_token_produces_a_principal_from_claims_only(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, tenant_id="acme", sub="agent.treasury-bot")
        principal = verifier(public_key).verify(credential(token), now=NOW)
        assert principal.tenant_id == "acme"
        assert principal.agent_ref == "agent.treasury-bot"

    def test_wrong_issuer_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, issuer="https://attacker.example.com")
        with pytest.raises(IdentityError):
            verifier(public_key).verify(credential(token), now=NOW)

    def test_wrong_audience_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, audience="some-other-api")
        with pytest.raises(IdentityError):
            verifier(public_key).verify(credential(token), now=NOW)

    def test_audience_as_a_list_is_accepted_when_it_contains_the_expected_value(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, audience=["other-api", AUDIENCE])
        principal = verifier(public_key).verify(credential(token), now=NOW)
        assert principal.tenant_id == "acme"

    def test_an_expired_token_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, exp=NOW - 3600.0)
        with pytest.raises(CredentialExpiredError):
            verifier(public_key).verify(credential(token), now=NOW)

    def test_a_not_yet_valid_token_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, nbf=NOW + 3600.0)
        with pytest.raises(CredentialExpiredError):
            verifier(public_key).verify(credential(token), now=NOW)

    def test_small_clock_skew_is_tolerated(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, iat=NOW - 3600.0, exp=NOW - 10.0)
        principal = verifier(public_key).verify(credential(token), now=NOW)
        assert principal.tenant_id == "acme"

    def test_a_missing_tenant_claim_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, tenant_id="")
        with pytest.raises(IdentityError):
            verifier(public_key).verify(credential(token), now=NOW)

    def test_an_unknown_kid_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, kid="key-unknown")
        with pytest.raises(IdentityError):
            verifier(public_key, kid="key-1").verify(credential(token), now=NOW)

    def test_a_forged_tenant_claim_does_not_verify(self) -> None:
        """Regression for F1: a header cannot select a tenant, and neither can
        an unsigned claim -- only a signature the verifier trusts can."""
        private_key, public_key = rsa_keypair()
        attacker_private, _attacker_public = rsa_keypair()
        forged_token = make_token(attacker_private, tenant_id="evilcorp")
        with pytest.raises(IdentityError):
            verifier(public_key).verify(credential(forged_token), now=NOW)

    def test_a_delegating_subject_claim_is_captured(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, extra_claims={"act": "alice@acme.example"})
        principal = verifier(public_key).verify(credential(token), now=NOW)
        assert principal.delegating_subject == "alice@acme.example"

    def test_claim_mapping_can_be_customised_per_identity_provider(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key, extra_claims={"org_id": "acme-corp"}, tenant_id="acme-corp")
        custom = verifier(public_key, claims=ClaimMapping(tenant_claim="org_id")).verify(
            credential(token), now=NOW
        )
        assert custom.tenant_id == "acme-corp"

    def test_a_non_oidc_credential_is_refused(self) -> None:
        from glassbox.domain.identity import CredentialType

        wrong_type = RawCredential(
            credential_type=CredentialType.MTLS, material="irrelevant", presented_at=NOW
        )
        _private_key, public_key = rsa_keypair()
        with pytest.raises(IdentityError):
            verifier(public_key).verify(wrong_type, now=NOW)

    def test_a_non_credential_is_refused(self) -> None:
        _private_key, public_key = rsa_keypair()
        with pytest.raises(IdentityError):
            verifier(public_key).verify("not-a-credential", now=NOW)  # type: ignore[arg-type]

    def test_construction_requires_issuer_and_audience(self) -> None:
        _private_key, public_key = rsa_keypair()
        with pytest.raises(IdentityError):
            OidcIdentityVerifier(issuer="", audience=AUDIENCE, jwks=StaticJwksProvider({}))


class TestOidcAssertionChecking:
    """The same spoofing-detection semantics as the reference verifier."""

    def test_a_matching_assertion_is_accepted(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key)
        instance = verifier(public_key)
        principal = instance.verify(credential(token), now=NOW)
        instance.assert_matches_assertion(principal, asserted_tenant_id="acme")

    def test_a_spoofed_tenant_assertion_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key)
        instance = verifier(public_key)
        principal = instance.verify(credential(token), now=NOW)
        with pytest.raises(IdentityError):
            instance.assert_matches_assertion(principal, asserted_tenant_id="evilcorp")

    def test_the_switch_can_be_disabled(self) -> None:
        private_key, public_key = rsa_keypair()
        token = make_token(private_key)
        instance = verifier(public_key, reject_mismatched_assertions=False)
        principal = instance.verify(credential(token), now=NOW)
        instance.assert_matches_assertion(principal, asserted_tenant_id="evilcorp")


class TestSharedAssertionCheck:
    """One function; every verifier calls it, so the check cannot drift."""

    def test_empty_assertions_never_mismatch(self) -> None:
        from tests.test_domain import make_principal

        check_assertion(make_principal())

    def test_reused_by_the_development_verifier(self) -> None:
        from glassbox.adapters.outbound.memory.decisioning import DevIdentityVerifier

        source = __import__("inspect", fromlist=["getsource"]).getsource(
            DevIdentityVerifier.assert_matches_assertion
        )
        assert "check_assertion(" in source
