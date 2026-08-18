"""Tests for declarative, signed policy bundles (GB-018, GB-019, GB-020)."""

from __future__ import annotations

import threading

import pytest

from glassbox.adapters.outbound.memory.policy import DeclarativePolicyDecisionPoint
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.domain.action import ConsequenceClass, Exposure, ProposedAction, ResourceRef
from glassbox.domain.decision import (
    AuthorizationRequest,
    DecisionEffect,
    DenialReason,
)
from glassbox.domain.errors import (
    DomainValidationError,
    PolicyBundleSignatureError,
    PolicyBundleUnavailableError,
)
from glassbox.domain.policy_bundle import (
    MAX_RULES_PER_BUNDLE,
    PolicyBundle,
    PolicyRule,
    RuleEffect,
    SignedPolicyBundle,
    detect_conflicts,
)
from tests.test_domain import make_principal

TENANT = "acme"


def _action(
    *, action: str = "payments.wire_transfer", monetary: float = 100.0, resource_id: str = "ACC-1"
) -> ProposedAction:
    return ProposedAction(
        action=action,
        resource=ResourceRef(kind="account", id=resource_id, tenant_id=TENANT),
        consequence=ConsequenceClass.REVERSIBLE,
        exposure=Exposure(monetary=monetary),
        idempotency_key="idem-1",
    )


def _bundle(*rules: PolicyRule, version: int = 1) -> PolicyBundle:
    return PolicyBundle(
        bundle_id="bundle.v1", tenant_id=TENANT, version=version, created_at=0.0, rules=rules
    )


class TestPolicyRule:
    def test_matches_by_action_and_resource_glob(self) -> None:
        rule = PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW, action_pattern="payments.*")
        assert rule.matches(_action(action="payments.wire_transfer"))
        assert not rule.matches(_action(action="refunds.issue"))

    def test_a_monetary_ceiling_excludes_unknown_exposure(self) -> None:
        rule = PolicyRule(name="small-only", effect=RuleEffect.ALLOW, max_monetary=100.0)
        unknown = ProposedAction(
            action="payments.wire_transfer",
            resource=ResourceRef(kind="account", id="ACC-1", tenant_id=TENANT),
            consequence=ConsequenceClass.REVERSIBLE,
            exposure=Exposure(),
            idempotency_key="idem-1",
        )
        assert not rule.matches(unknown)

    def test_a_bundle_exceeding_the_rule_ceiling_is_refused(self) -> None:
        rules = tuple(
            PolicyRule(name=f"rule-{i}", effect=RuleEffect.ALLOW)
            for i in range(MAX_RULES_PER_BUNDLE + 1)
        )
        with pytest.raises(DomainValidationError):
            _bundle(*rules)

    def test_a_duplicate_rule_name_is_refused(self) -> None:
        rules = (
            PolicyRule(name="dup", effect=RuleEffect.ALLOW),
            PolicyRule(name="dup", effect=RuleEffect.DENY),
        )
        with pytest.raises(DomainValidationError):
            _bundle(*rules)


class TestPolicyBundle:
    def test_deny_by_default_when_no_rule_matches(self) -> None:
        bundle = _bundle(
            PolicyRule(name="only-refunds", effect=RuleEffect.ALLOW, action_pattern="refunds.*")
        )
        assert bundle.matching_rule(_action(action="payments.wire_transfer")) is None

    def test_lower_priority_rule_wins(self) -> None:
        deny_all = PolicyRule(name="deny-all", effect=RuleEffect.DENY, priority=200)
        allow_wires = PolicyRule(
            name="allow-wires", effect=RuleEffect.ALLOW, action_pattern="payments.*", priority=10
        )
        bundle = _bundle(deny_all, allow_wires)
        matched = bundle.matching_rule(_action())
        assert matched is not None and matched.name == "allow-wires"

    def test_digest_changes_when_a_rule_changes(self) -> None:
        first = _bundle(PolicyRule(name="r", effect=RuleEffect.ALLOW))
        second = _bundle(PolicyRule(name="r", effect=RuleEffect.DENY))
        assert first.digest() != second.digest()

    def test_max_rules_constant_is_a_real_ceiling(self) -> None:
        assert MAX_RULES_PER_BUNDLE > 0


class TestConflictDetection:
    """GB-019: overlapping scope, contradictory effect -- regardless of naming."""

    def test_an_overlapping_allow_and_deny_are_flagged(self) -> None:
        allow = PolicyRule(
            name="totally-fine-rule", effect=RuleEffect.ALLOW, action_pattern="payments.*"
        )
        deny = PolicyRule(
            name="also-totally-fine",
            effect=RuleEffect.DENY,
            action_pattern="payments.wire_transfer",
        )
        conflicts = detect_conflicts(_bundle(allow, deny))
        assert len(conflicts) == 1
        assert {conflicts[0].rule_a, conflicts[0].rule_b} == {
            "totally-fine-rule",
            "also-totally-fine",
        }

    def test_non_overlapping_actions_are_not_flagged(self) -> None:
        allow = PolicyRule(name="a", effect=RuleEffect.ALLOW, action_pattern="payments.*")
        deny = PolicyRule(name="b", effect=RuleEffect.DENY, action_pattern="refunds.*")
        assert detect_conflicts(_bundle(allow, deny)) == ()

    def test_same_effect_rules_are_never_flagged(self) -> None:
        one = PolicyRule(name="a", effect=RuleEffect.ALLOW, action_pattern="payments.*")
        two = PolicyRule(name="b", effect=RuleEffect.ALLOW, action_pattern="payments.wire_transfer")
        assert detect_conflicts(_bundle(one, two)) == ()


class TestDeclarativePolicyDecisionPoint:
    """GB-018: signed, versioned, and refuses tampered data."""

    def _request(self, action: ProposedAction) -> AuthorizationRequest:
        return AuthorizationRequest(
            decision_id="decision-1", principal=make_principal(), action=action, evaluated_at=0.0
        )

    def test_a_matching_allow_rule_cites_the_bundle(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        signed = pdp.load_bundle(
            _bundle(
                PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW, action_pattern="payments.*")
            )
        )
        decision = pdp.decide(self._request(_action()))
        assert decision.effect is DecisionEffect.ALLOW
        assert decision.policy_bundle_sha256 == signed.bundle.digest()
        assert decision.matched_rules == ("allow-wires",)

    def test_no_matching_rule_denies(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        pdp.load_bundle(_bundle())
        decision = pdp.decide(self._request(_action()))
        assert decision.effect is DecisionEffect.DENY
        assert DenialReason.POLICY_DENIED in decision.reasons

    def test_a_require_approval_rule_routes_for_approval(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        pdp.load_bundle(
            _bundle(PolicyRule(name="dual-control", effect=RuleEffect.REQUIRE_APPROVAL))
        )
        decision = pdp.decide(self._request(_action()))
        assert decision.effect is DecisionEffect.REQUIRE_APPROVAL

    def test_no_bundle_loaded_is_unavailable(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        with pytest.raises(PolicyBundleUnavailableError):
            pdp.decide(self._request(_action()))

    def test_a_tampered_bundle_is_refused(self) -> None:
        signer = LocalMacSigner(key_id="k", key=b"\x11" * 32)
        pdp = DeclarativePolicyDecisionPoint(signer)
        bundle = _bundle(PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW))
        mac = signer.mac(bundle.canonical_payload())
        tampered_bundle = PolicyBundle(
            bundle_id=bundle.bundle_id,
            tenant_id=bundle.tenant_id,
            version=bundle.version + 1,
            created_at=bundle.created_at,
            rules=bundle.rules,
        )
        forged = SignedPolicyBundle(bundle=tampered_bundle, mac=mac, signer_key_id=signer.key_id)
        with pytest.raises(PolicyBundleSignatureError):
            pdp.load_signed_bundle(forged)

    def test_an_unsigned_bundle_with_a_foreign_key_id_is_refused(self) -> None:
        signer = LocalMacSigner(key_id="k", key=b"\x11" * 32)
        pdp = DeclarativePolicyDecisionPoint(signer)
        bundle = _bundle(PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW))
        forged = SignedPolicyBundle(bundle=bundle, mac=b"\x00" * 32, signer_key_id="unknown-key")
        with pytest.raises((PolicyBundleSignatureError, PolicyBundleUnavailableError)):
            pdp.load_signed_bundle(forged)

    def test_a_correctly_signed_bundle_verifies_and_activates(self) -> None:
        signer = LocalMacSigner(key_id="k", key=b"\x11" * 32)
        publisher_pdp = DeclarativePolicyDecisionPoint(signer)
        bundle = _bundle(
            PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW, action_pattern="payments.*")
        )
        signed = publisher_pdp.load_bundle(bundle)

        consumer_pdp = DeclarativePolicyDecisionPoint(signer)
        consumer_pdp.load_signed_bundle(signed)
        decision = consumer_pdp.decide(self._request(_action()))
        assert decision.effect is DecisionEffect.ALLOW

    def test_an_unavailable_pdp_raises(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        pdp.load_bundle(_bundle(PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW)))
        pdp.set_available(False)
        with pytest.raises(PolicyBundleUnavailableError):
            pdp.decide(self._request(_action()))


class TestNoRuleTimeoutThreadLeak:
    """GB-020: nothing in the policy path needs a timeout thread pool to contain."""

    def test_policy_module_source_names_no_thread_pool(self) -> None:
        import inspect

        from glassbox.adapters.outbound.memory import policy as policy_module
        from glassbox.domain import policy_bundle as policy_bundle_module

        for module in (policy_module, policy_bundle_module):
            source = inspect.getsource(module)
            assert "ThreadPoolExecutor" not in source

    def test_evaluation_never_spawns_a_thread(self) -> None:
        pdp = DeclarativePolicyDecisionPoint(LocalMacSigner(key_id="k", key=b"\x11" * 32))
        pdp.load_bundle(_bundle(PolicyRule(name="allow-wires", effect=RuleEffect.ALLOW)))
        before = threading.active_count()
        pdp.decide(
            AuthorizationRequest(
                decision_id="decision-1",
                principal=make_principal(),
                action=_action(),
                evaluated_at=0.0,
            )
        )
        assert threading.active_count() == before
