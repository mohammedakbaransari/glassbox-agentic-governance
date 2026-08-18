"""Tests for cryptographically verified delegation chains (GB-017).

Real RSA signatures throughout, so a passing suite proves hop verification --
not merely that a function named ``verify`` was called.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from glassbox.adapters.outbound.identity import StaticJwksProvider, verify_delegation_chain
from glassbox.domain.errors import DelegationError, IdentityError
from glassbox.domain.identity import SubjectType
from tests.test_oidc_identity import _sign_rs256, rsa_keypair

NOW = 1_760_000_000.0


def _hop_token(
    private_key: Any,
    *,
    kid: str = "hop-key",
    sub: str,
    subject_type: str = "human",
    capabilities: Optional[list] = None,
    issued_at: float = NOW,
    expires_at: float = NOW + 3600.0,
) -> str:
    header: Dict[str, Any] = {"alg": "RS256", "kid": kid}
    payload: Dict[str, Any] = {
        "sub": sub,
        "subject_type": subject_type,
        "capabilities": capabilities if capabilities is not None else ["payments.wire_transfer"],
        "iat": issued_at,
        "exp": expires_at,
    }
    return _sign_rs256(private_key, header, payload)


class TestVerifyDelegationChain:
    def test_an_empty_sequence_yields_an_empty_chain(self) -> None:
        chain = verify_delegation_chain((), jwks=StaticJwksProvider({}), now=NOW)
        assert chain.is_empty

    def test_a_single_verified_hop_builds_a_chain(self) -> None:
        private_key, public_key = rsa_keypair()
        token = _hop_token(private_key, sub="agent.treasury-bot", subject_type="agent")
        chain = verify_delegation_chain(
            (token,), jwks=StaticJwksProvider({"hop-key": public_key}), now=NOW
        )
        assert chain.depth == 1
        assert chain.leaf is not None and chain.leaf.subject == "agent.treasury-bot"
        assert chain.leaf.subject_type is SubjectType.AGENT

    def test_an_attenuating_two_hop_chain_verifies(self) -> None:
        human_key, human_public = rsa_keypair()
        agent_key, agent_public = rsa_keypair()
        root = _hop_token(
            human_key,
            kid="human-key",
            sub="alice",
            subject_type="human",
            capabilities=["payments.wire_transfer", "payments.refund"],
        )
        leaf = _hop_token(
            agent_key,
            kid="agent-key",
            sub="agent.treasury-bot",
            subject_type="agent",
            capabilities=["payments.wire_transfer"],
        )
        chain = verify_delegation_chain(
            (root, leaf),
            jwks=StaticJwksProvider({"human-key": human_public, "agent-key": agent_public}),
            now=NOW,
        )
        assert chain.subjects() == ("alice", "agent.treasury-bot")
        assert chain.effective_capabilities() == frozenset({"payments.wire_transfer"})

    def test_a_widening_hop_is_rejected(self) -> None:
        """The domain constructor rejects this; verification cannot bypass it."""
        human_key, human_public = rsa_keypair()
        agent_key, agent_public = rsa_keypair()
        root = _hop_token(
            human_key, kid="human-key", sub="alice", capabilities=["payments.wire_transfer"]
        )
        leaf = _hop_token(
            agent_key,
            kid="agent-key",
            sub="agent.treasury-bot",
            subject_type="agent",
            capabilities=["payments.wire_transfer", "admin.delete_database"],
        )
        with pytest.raises(DelegationError):
            verify_delegation_chain(
                (root, leaf),
                jwks=StaticJwksProvider({"human-key": human_public, "agent-key": agent_public}),
                now=NOW,
            )

    def test_a_forged_hop_signature_is_rejected(self) -> None:
        _real_private, real_public = rsa_keypair()
        attacker_private, _attacker_public = rsa_keypair()
        token = _hop_token(attacker_private, sub="agent.treasury-bot")
        with pytest.raises(IdentityError):
            verify_delegation_chain(
                (token,), jwks=StaticJwksProvider({"hop-key": real_public}), now=NOW
            )

    def test_a_missing_capability_claim_defaults_to_no_capabilities(self) -> None:
        private_key, public_key = rsa_keypair()
        token = _hop_token(private_key, sub="agent.treasury-bot", capabilities=[])
        chain = verify_delegation_chain(
            (token,), jwks=StaticJwksProvider({"hop-key": public_key}), now=NOW
        )
        assert chain.leaf is not None and chain.leaf.capabilities == frozenset()

    def test_an_unknown_subject_type_is_refused(self) -> None:
        private_key, public_key = rsa_keypair()
        token = _hop_token(private_key, sub="agent.treasury-bot", subject_type="robot")
        with pytest.raises(IdentityError):
            verify_delegation_chain(
                (token,), jwks=StaticJwksProvider({"hop-key": public_key}), now=NOW
            )

    def test_a_missing_kid_is_refused(self) -> None:
        private_key, _public_key = rsa_keypair()
        header = {"alg": "RS256"}
        payload = {
            "sub": "agent.treasury-bot",
            "subject_type": "agent",
            "capabilities": [],
            "iat": NOW,
            "exp": NOW + 3600.0,
        }
        token = _sign_rs256(private_key, header, payload)
        with pytest.raises(IdentityError):
            verify_delegation_chain((token,), jwks=StaticJwksProvider({}), now=NOW)
