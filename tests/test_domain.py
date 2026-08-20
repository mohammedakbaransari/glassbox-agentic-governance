"""Unit tests for the GlassBox domain layer (GB-002).

Every test in this module is a regression test for a measured v1 defect or an
enforcement test for one of the non-negotiable invariants. Where a test locks in
a specific defect fix, the docstring names it.

The domain layer is pure, so these tests need no fixtures, no database, no clock
and no network. That is itself a property worth preserving: if a future change
makes a test here need a fixture, the change has broken the layering.
"""

from __future__ import annotations

import dataclasses
import importlib
import inspect
import math
import pathlib
from typing import Any, Dict, List

import pytest

import glassbox.domain
import glassbox.domain.errors
from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.decision import (
    Approval,
    ApprovalState,
    AuthorizationDecision,
    AuthorizationRequest,
    DecisionEffect,
    DenialReason,
    ExecutionOutcome,
    ExecutionStatus,
    Obligation,
    ObligationKind,
    StageOutcome,
    StageStatus,
)
from glassbox.domain.errors import (
    DelegationError,
    DomainValidationError,
    GlassBoxError,
    LimitStoreUnavailable,
)
from glassbox.domain.evidence import (
    GENESIS_PREV_HASH,
    EvidenceReceipt,
    EvidenceSegment,
    IntegrityReport,
    IntegrityStatus,
    IntentRecord,
    ModelProvenance,
    OutcomeRecord,
)
from glassbox.domain.identity import (
    CredentialType,
    DelegationChain,
    DelegationHop,
    RawCredential,
    SubjectType,
    VerifiedPrincipal,
)
from glassbox.domain.limits import LimitKey, LimitScope, LimitVerdict, Window
from glassbox.domain.mandate import (
    ActionResourceGrant,
    Mandate,
    MandateDenialReason,
    MandateVerdict,
    ToolGrant,
)
from glassbox.domain.risk import (
    CONSEQUENCE_FLOORS,
    RISK_BANDS,
    RiskFactor,
    RiskInputs,
    RiskLevel,
    RiskScore,
)
from glassbox.domain.serialization import (
    canonical_bytes,
    canonical_json,
    freeze_mapping,
    require_identifier,
    require_timestamp,
)
from glassbox.ports.baseline import BaselineKey, BaselineScope

# --------------------------------------------------------------------------- #
# Shared builders (plain functions, not fixtures: the domain needs no setup)
# --------------------------------------------------------------------------- #

NOW = 1_760_000_000.0
HOUR = 3600.0
DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def make_principal(**overrides: Any) -> VerifiedPrincipal:
    """Build a valid principal with sane defaults."""
    kwargs: Dict[str, Any] = {
        "agent_ref": "agent.treasury-bot",
        "agent_instance_id": "instance-01",
        "tenant_id": "acme",
        "credential_type": CredentialType.SPIFFE,
        "credential_id": "spiffe://acme/agent/treasury-bot",
        "issued_at": NOW - HOUR,
        "expires_at": NOW + HOUR,
    }
    kwargs.update(overrides)
    return VerifiedPrincipal(**kwargs)


def make_action(**overrides: Any) -> ProposedAction:
    """Build a valid proposed action with sane defaults."""
    kwargs: Dict[str, Any] = {
        "action": "payments.wire_transfer",
        "resource": ResourceRef(kind="account", id="ACC-1", tenant_id="acme"),
        "consequence": ConsequenceClass.IRREVERSIBLE,
        "exposure": Exposure(blast_radius=BlastRadius.SINGLE, monetary=1000.0),
        "idempotency_key": "idem-001",
    }
    kwargs.update(overrides)
    return ProposedAction(**kwargs)


def make_risk(score: float = 10.0, **inputs_overrides: Any) -> RiskScore:
    """Build a risk score over a valid input set."""
    inputs_kwargs: Dict[str, Any] = {
        "consequence": ConsequenceClass.IRREVERSIBLE,
        "exposure": Exposure(monetary=50_000_000.0),
        "evaluated_at": NOW,
    }
    inputs_kwargs.update(inputs_overrides)
    return RiskScore(value=score, model_version="risk-v2.0.0", inputs=RiskInputs(**inputs_kwargs))


def make_allow_decision() -> AuthorizationDecision:
    """Build a minimal valid allow decision."""
    return AuthorizationDecision.allow(
        rationale="within mandate and policy",
        policy_bundle_id="bundle.acme",
        policy_bundle_sha256=DIGEST_A,
    )


def make_intent(**overrides: Any) -> IntentRecord:
    """Build a valid intent record."""
    kwargs: Dict[str, Any] = {
        "decision_id": "decision-0001",
        "segment_id": "seg-2026-08",
        "tenant_id": "acme",
        "created_at": NOW,
        "principal": make_principal(),
        "action": make_action(),
        "decision": make_allow_decision(),
        "risk": make_risk(),
        "trace_id": "trace-abc",
    }
    kwargs.update(overrides)
    return IntentRecord(**kwargs)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #


class TestErrors:
    """Structured context replaces logging in a pure domain layer."""

    def test_context_is_structured_and_serialisable(self) -> None:
        error = LimitStoreUnavailable("redis unreachable", key="glassbox|limit|acme", attempts=3)
        assert error.context == {"key": "glassbox|limit|acme", "attempts": "3"}
        assert error.as_dict()["code"] == "limit_store_unavailable"
        assert error.as_dict()["error_class"] == "LimitStoreUnavailable"

    def test_every_domain_error_derives_from_the_base(self) -> None:
        assert issubclass(LimitStoreUnavailable, GlassBoxError)
        assert issubclass(DomainValidationError, GlassBoxError)

    def test_validation_error_is_also_a_value_error(self) -> None:
        """Boundary code that already catches ValueError keeps working."""
        assert issubclass(DomainValidationError, ValueError)


# --------------------------------------------------------------------------- #
# Canonical serialisation
# --------------------------------------------------------------------------- #


class TestCanonicalSerialization:
    """The evidence hash chain is only as reproducible as this function."""

    def test_key_order_does_not_affect_output(self) -> None:
        first = canonical_json({"b": 1, "a": 2})
        second = canonical_json({"a": 2, "b": 1})
        assert first == second == '{"a":2,"b":1}'

    def test_nested_key_order_does_not_affect_output(self) -> None:
        assert canonical_json({"outer": {"z": 1, "a": 2}}) == canonical_json(
            {"outer": {"a": 2, "z": 1}}
        )

    def test_enums_serialise_to_their_value(self) -> None:
        assert canonical_json({"c": ConsequenceClass.IRREVERSIBLE}) == '{"c":"irreversible"}'

    def test_tuples_and_lists_are_equivalent(self) -> None:
        assert canonical_json({"x": (1, 2)}) == canonical_json({"x": [1, 2]})

    def test_frozensets_are_sorted_deterministically(self) -> None:
        assert canonical_json({"s": frozenset({"b", "a"})}) == canonical_json(
            {"s": frozenset({"a", "b"})}
        )

    def test_unicode_is_preserved_not_escaped(self) -> None:
        """A vendor named 'Grupo Água & Café Ltda' must round-trip unharmed.

        v1's sanitizer flagged this exact string as a unicode anomaly.
        """
        payload = canonical_json({"vendor": "Grupo Água & Café Ltda"})
        assert "Água" in payload

    @pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
    def test_non_finite_floats_are_rejected(self, value: float) -> None:
        with pytest.raises(DomainValidationError):
            canonical_json({"x": value})

    def test_raw_bytes_are_rejected(self) -> None:
        """Bytes have no canonical JSON form; the caller must encode explicitly."""
        with pytest.raises(DomainValidationError):
            canonical_json({"x": b"\x00\x01"})

    def test_unsupported_types_are_rejected_not_stringified(self) -> None:
        """str(obj) would embed a memory address and destroy reproducibility."""

        class Opaque:
            pass

        with pytest.raises(DomainValidationError) as excinfo:
            canonical_json({"x": Opaque()})
        assert excinfo.value.context["offending_type"] == "Opaque"

    def test_non_string_keys_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            canonical_json({"outer": {1: "a"}})

    def test_canonical_bytes_is_utf8_of_canonical_json(self) -> None:
        payload = {"a": 1, "vendor": "Café"}
        assert canonical_bytes(payload) == canonical_json(payload).encode("utf-8")

    def test_freeze_mapping_is_hashable_and_order_stable(self) -> None:
        frozen = freeze_mapping({"b": 1, "a": {"n": [1, 2]}}, field="p")
        assert hash(frozen) == hash(freeze_mapping({"a": {"n": [1, 2]}, "b": 1}, field="p"))
        assert frozen[0][0] == "a"


class TestValidationHelpers:
    """Identifier and timestamp guards keep unsafe values out of keys and evidence."""

    @pytest.mark.parametrize("value", ["a b", "-leading", "", "  ", "x" * 257, "semi;colon"])
    def test_unsafe_identifiers_are_rejected(self, value: str) -> None:
        with pytest.raises(DomainValidationError):
            require_identifier(value, field="test")

    @pytest.mark.parametrize("value", ["agent.treasury-bot", "spiffe://acme/agent/x", "ACC_1", "a"])
    def test_safe_identifiers_are_accepted(self, value: str) -> None:
        assert require_identifier(value, field="test") == value

    def test_millisecond_timestamps_are_rejected(self) -> None:
        """A unit error caught here is a unit error kept out of the evidence chain."""
        with pytest.raises(DomainValidationError):
            require_timestamp(NOW * 1000.0, field="now")

    def test_negative_timestamps_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            require_timestamp(-1.0, field="now")


# --------------------------------------------------------------------------- #
# Identity
# --------------------------------------------------------------------------- #


class TestRawCredential:
    """Secret material must never reach a log line."""

    def test_repr_redacts_material(self) -> None:
        credential = RawCredential(
            credential_type=CredentialType.OIDC, material="super-secret-jwt", presented_at=NOW
        )
        assert "super-secret-jwt" not in repr(credential)
        assert "super-secret-jwt" not in str(credential)
        assert "redacted" in repr(credential)

    def test_empty_material_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            RawCredential(credential_type=CredentialType.OIDC, material="  ", presented_at=NOW)


class TestDelegationChain:
    """Attenuation is enforced at construction, not deferred to a policy rule."""

    @staticmethod
    def _hop(subject: str, capabilities: Any, expires_at: float = NOW + HOUR) -> DelegationHop:
        return DelegationHop(
            subject=subject,
            subject_type=SubjectType.AGENT,
            capabilities=frozenset(capabilities),
            issued_at=NOW - HOUR,
            expires_at=expires_at,
        )

    def test_attenuating_chain_is_accepted(self) -> None:
        chain = DelegationChain.of(
            [
                self._hop("alice", {"pay", "read", "admin"}),
                self._hop("agent.a", {"pay", "read"}),
                self._hop("agent.b", {"read"}),
            ]
        )
        assert chain.depth == 3
        assert chain.effective_capabilities() == frozenset({"read"})

    def test_widening_chain_is_rejected(self) -> None:
        """A hop cannot hold a capability its delegator lacks."""
        with pytest.raises(DelegationError) as excinfo:
            DelegationChain.of(
                [self._hop("alice", {"read"}), self._hop("agent.a", {"read", "admin"})]
            )
        assert excinfo.value.context["widened_capabilities"] == "['admin']"

    def test_chain_cannot_outlive_its_delegator(self) -> None:
        with pytest.raises(DelegationError):
            DelegationChain.of(
                [
                    self._hop("alice", {"read"}, expires_at=NOW + HOUR),
                    self._hop("agent.a", {"read"}, expires_at=NOW + 2 * HOUR),
                ]
            )

    def test_empty_chain_grants_nothing(self) -> None:
        chain = DelegationChain()
        assert chain.is_empty
        assert chain.effective_capabilities() == frozenset()
        assert chain.is_valid_at(NOW) is False

    def test_expired_hop_invalidates_the_chain(self) -> None:
        chain = DelegationChain.of([self._hop("alice", {"read"})])
        assert chain.is_valid_at(NOW) is True
        assert chain.is_valid_at(NOW + 2 * HOUR) is False


class TestVerifiedPrincipal:
    """Tenancy comes from the credential, never from a header."""

    def test_tenant_is_a_field_of_the_verified_principal(self) -> None:
        principal = make_principal(tenant_id="acme")
        assert principal.owns("acme") is True
        assert principal.owns("evilcorp") is False

    def test_principal_is_immutable(self) -> None:
        principal = make_principal()
        with pytest.raises(Exception):
            principal.tenant_id = "evilcorp"  # type: ignore[misc]

    def test_expiry_window_is_enforced(self) -> None:
        principal = make_principal()
        assert principal.is_expired(NOW) is False
        assert principal.is_expired(NOW + 2 * HOUR) is True
        principal.require_valid_at(NOW)
        with pytest.raises(GlassBoxError):
            principal.require_valid_at(NOW + 2 * HOUR)

    def test_expires_at_must_follow_issued_at(self) -> None:
        with pytest.raises(DomainValidationError):
            make_principal(issued_at=NOW, expires_at=NOW)

    def test_capability_check_denies_by_default(self) -> None:
        """No presented delegation means no delegated authority (invariant I4)."""
        assert make_principal().has_capability("pay") is False

    def test_chain_leaf_must_be_the_acting_agent(self) -> None:
        chain = DelegationChain.of(
            [
                DelegationHop(
                    subject="someone.else",
                    subject_type=SubjectType.AGENT,
                    capabilities=frozenset({"pay"}),
                    issued_at=NOW - HOUR,
                    expires_at=NOW + HOUR,
                )
            ]
        )
        with pytest.raises(DelegationError):
            make_principal(delegation_chain=chain)

    def test_chain_root_must_be_the_delegating_subject(self) -> None:
        chain = DelegationChain.of(
            [
                DelegationHop(
                    subject="mallory",
                    subject_type=SubjectType.HUMAN,
                    capabilities=frozenset({"pay"}),
                    issued_at=NOW - HOUR,
                    expires_at=NOW + HOUR,
                ),
                DelegationHop(
                    subject="agent.treasury-bot",
                    subject_type=SubjectType.AGENT,
                    capabilities=frozenset({"pay"}),
                    issued_at=NOW - HOUR,
                    expires_at=NOW + HOUR,
                ),
            ]
        )
        with pytest.raises(DelegationError):
            make_principal(delegating_subject="alice", delegation_chain=chain)

    def test_evidence_payload_is_canonically_serialisable(self) -> None:
        payload = make_principal(claims={"env": "prod"}).as_evidence()
        assert canonical_bytes(dict(payload))
        assert payload["tenant_id"] == "acme"


# --------------------------------------------------------------------------- #
# Action, consequence and exposure
# --------------------------------------------------------------------------- #


class TestConsequenceClass:
    """The axis v1's 12-value DecisionType never had."""

    def test_classes_are_totally_ordered(self) -> None:
        assert (
            ConsequenceClass.ADVISORY
            < ConsequenceClass.REVERSIBLE
            < ConsequenceClass.COMPENSABLE
            < ConsequenceClass.IRREVERSIBLE
        )

    def test_only_advisory_actions_may_degrade_on_dependency_failure(self) -> None:
        """The direct fix for the v1 breaker admitting everything during an outage."""
        assert ConsequenceClass.ADVISORY.may_degrade_on_dependency_failure is True
        for consequence in (
            ConsequenceClass.REVERSIBLE,
            ConsequenceClass.COMPENSABLE,
            ConsequenceClass.IRREVERSIBLE,
        ):
            assert consequence.may_degrade_on_dependency_failure is False

    def test_only_advisory_actions_skip_prior_evidence(self) -> None:
        assert ConsequenceClass.ADVISORY.requires_prior_evidence is False
        assert ConsequenceClass.IRREVERSIBLE.requires_prior_evidence is True

    def test_comparison_with_a_foreign_type_is_not_implemented(self) -> None:
        with pytest.raises(TypeError):
            _ = ConsequenceClass.ADVISORY < 1  # type: ignore[operator]


class TestExposure:
    """An unknown magnitude is never a small magnitude."""

    def test_unknown_monetary_breaches_a_monetary_ceiling(self) -> None:
        unknown = Exposure(monetary=None)
        ceiling = Exposure(monetary=1000.0)
        assert unknown.exceeds(ceiling) is True

    def test_known_value_within_ceiling_does_not_breach(self) -> None:
        assert Exposure(monetary=999.0).exceeds(Exposure(monetary=1000.0)) is False

    def test_value_above_ceiling_breaches(self) -> None:
        assert Exposure(monetary=1001.0).exceeds(Exposure(monetary=1000.0)) is True

    def test_blast_radius_is_compared(self) -> None:
        wide = Exposure(blast_radius=BlastRadius.GLOBAL)
        narrow_ceiling = Exposure(blast_radius=BlastRadius.TENANT)
        assert wide.exceeds(narrow_ceiling) is True

    def test_unconstrained_ceiling_dimension_is_ignored(self) -> None:
        assert Exposure(monetary=10.0).exceeds(Exposure(records=5)) is True
        assert Exposure(monetary=10.0, records=1).exceeds(Exposure(records=5)) is False

    def test_negative_magnitudes_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            Exposure(monetary=-1.0)
        with pytest.raises(DomainValidationError):
            Exposure(records=-1)

    def test_boolean_record_count_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            Exposure(records=True)  # type: ignore[arg-type]


class TestProposedAction:
    """Consequence and exposure are server-derived, structured, and immutable."""

    def test_tenant_is_derived_from_the_resource(self) -> None:
        assert make_action().tenant_id == "acme"

    def test_parameters_are_frozen_and_the_action_is_hashable(self) -> None:
        action = make_action(parameters={"amount": 1000, "nested": {"a": [1, 2]}})
        assert isinstance(action.parameters, tuple)
        assert hash(action) == hash(
            make_action(parameters={"nested": {"a": [1, 2]}, "amount": 1000})
        )

    def test_mutating_the_source_mapping_does_not_affect_the_action(self) -> None:
        source = {"amount": 1000}
        action = make_action(parameters=source)
        source["amount"] = 999_999_999
        assert action.parameter("amount") == 1000

    def test_consequence_must_be_a_consequence_class(self) -> None:
        with pytest.raises(DomainValidationError):
            make_action(consequence="irreversible")  # type: ignore[arg-type]

    def test_exposure_must_be_an_exposure(self) -> None:
        with pytest.raises(DomainValidationError):
            make_action(exposure={"monetary": 1.0})  # type: ignore[arg-type]

    def test_evidence_payload_is_canonically_serialisable(self) -> None:
        assert canonical_bytes(dict(make_action(parameters={"amount": 1000}).as_evidence()))


# --------------------------------------------------------------------------- #
# Risk
# --------------------------------------------------------------------------- #


class TestRiskBanding:
    """One banding table, used by both the level and the disposition."""

    def test_bands_are_ascending_and_cover_every_level(self) -> None:
        bounds = [bound for bound, _ in RISK_BANDS]
        assert bounds == sorted(bounds)
        assert {level for _, level in RISK_BANDS} == set(RiskLevel)

    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0.0, RiskLevel.LOW),
            (24.999, RiskLevel.LOW),
            (25.0, RiskLevel.MEDIUM),
            (49.999, RiskLevel.MEDIUM),
            (50.0, RiskLevel.HIGH),
            (74.999, RiskLevel.HIGH),
            (75.0, RiskLevel.CRITICAL),
            (100.0, RiskLevel.CRITICAL),
        ],
    )
    def test_boundaries_are_inclusive_lower_bounds(self, score: float, expected: RiskLevel) -> None:
        assert RiskLevel.from_score(score) is expected

    @pytest.mark.parametrize("score", [-0.1, 100.1, float("nan"), float("inf")])
    def test_out_of_range_scores_are_rejected(self, score: float) -> None:
        with pytest.raises(DomainValidationError):
            RiskLevel.from_score(score)

    def test_levels_are_totally_ordered(self) -> None:
        assert RiskLevel.LOW < RiskLevel.MEDIUM < RiskLevel.HIGH < RiskLevel.CRITICAL


class TestRiskScore:
    """Regression tests for the saturating v1 risk model."""

    def test_fifty_million_irreversible_transfer_is_not_medium(self) -> None:
        """v1 scored this exact case 27.5 / 'medium'.

        The floor cannot make the aggregation correct on its own -- that is
        GB-021 -- but it makes the *outcome* correct for the class of action that
        matters most.
        """
        raw = make_risk(score=27.5)
        assert raw.level is RiskLevel.MEDIUM

        floored = raw.with_consequence_floor()
        assert floored.level is RiskLevel.HIGH
        assert floored.floor_applied is True
        assert floored.raw_value == 27.5

    def test_floor_is_idempotent(self) -> None:
        once = make_risk(score=10.0).with_consequence_floor()
        twice = once.with_consequence_floor()
        assert once == twice

    def test_floor_never_lowers_a_score(self) -> None:
        high = make_risk(score=95.0)
        assert high.with_consequence_floor() is high

    def test_advisory_actions_have_no_floor(self) -> None:
        advisory = make_risk(score=1.0, consequence=ConsequenceClass.ADVISORY)
        assert advisory.with_consequence_floor().level is RiskLevel.LOW

    def test_every_consequence_class_has_a_floor(self) -> None:
        assert set(CONSEQUENCE_FLOORS) == set(ConsequenceClass)

    def test_a_score_below_its_raw_value_is_rejected(self) -> None:
        inputs = RiskInputs(
            consequence=ConsequenceClass.ADVISORY, exposure=Exposure(), evaluated_at=NOW
        )
        with pytest.raises(DomainValidationError):
            RiskScore(value=10.0, model_version="risk-v2.0.0", inputs=inputs, raw_value=20.0)

    def test_model_version_is_mandatory_and_recorded(self) -> None:
        assert make_risk().as_evidence()["risk_model_ver"] == "risk-v2.0.0"

    def test_exceeds_compares_bands(self) -> None:
        assert make_risk(score=80.0).exceeds(RiskLevel.HIGH) is True
        assert make_risk(score=80.0).exceeds(RiskLevel.CRITICAL) is False


class TestRiskInputs:
    """Inputs are stored verbatim so a replay can reproduce the score exactly."""

    def test_duplicate_factor_names_are_rejected(self) -> None:
        factor = RiskFactor(name="amount", score=10.0, rationale="small")
        with pytest.raises(DomainValidationError):
            RiskInputs(
                consequence=ConsequenceClass.ADVISORY,
                exposure=Exposure(),
                evaluated_at=NOW,
                factors=(factor, factor),
            )

    def test_factors_are_individually_explainable(self) -> None:
        factor = RiskFactor(
            name="amount_vs_peer_group",
            score=80.0,
            rationale="42x the peer-group median",
            detail={"median": 1000.0},
        )
        assert factor.level is RiskLevel.CRITICAL
        assert "peer-group" in factor.rationale

    def test_evidence_payload_is_canonically_serialisable(self) -> None:
        inputs = RiskInputs(
            consequence=ConsequenceClass.IRREVERSIBLE,
            exposure=Exposure(monetary=5.0),
            evaluated_at=NOW,
            factors=(RiskFactor(name="amount", score=10.0, rationale="small"),),
        )
        assert canonical_bytes(dict(inputs.as_evidence()))


# --------------------------------------------------------------------------- #
# Mandates
# --------------------------------------------------------------------------- #


def make_mandate(**overrides: Any) -> Mandate:
    """Build a permissive-but-bounded mandate."""
    kwargs: Dict[str, Any] = {
        "tenant_id": "acme",
        "agent_ref": "agent.treasury-bot",
        "version": 1,
        "max_consequence": ConsequenceClass.IRREVERSIBLE,
        "max_exposure": Exposure(blast_radius=BlastRadius.TENANT, monetary=10_000.0),
        "valid_from": NOW - HOUR,
        "allowed_actions": frozenset({"payments.*"}),
        "allowed_resources": frozenset({"account/*"}),
    }
    kwargs.update(overrides)
    return Mandate(**kwargs)


class TestMandate:
    """Coarse, deny-by-default authority evaluated before policy."""

    def test_action_within_mandate_is_permitted(self) -> None:
        verdict = make_mandate().permits(make_action(), now=NOW)
        assert verdict.permitted is True
        assert verdict.mandate_version == 1

    def test_empty_pattern_set_grants_nothing(self) -> None:
        """Absence of a grant is a denial, never a wildcard (invariant I4)."""
        verdict = Mandate(
            tenant_id="acme",
            agent_ref="agent.treasury-bot",
            version=1,
            max_consequence=ConsequenceClass.IRREVERSIBLE,
            max_exposure=Exposure(monetary=1e9),
            valid_from=NOW - HOUR,
        ).permits(make_action(), now=NOW)
        assert verdict.permitted is False
        assert MandateDenialReason.ACTION_NOT_GRANTED in verdict.reasons
        assert MandateDenialReason.RESOURCE_NOT_GRANTED in verdict.reasons

    def test_consequence_ceiling_is_enforced(self) -> None:
        verdict = make_mandate(max_consequence=ConsequenceClass.REVERSIBLE).permits(
            make_action(consequence=ConsequenceClass.IRREVERSIBLE), now=NOW
        )
        assert MandateDenialReason.CONSEQUENCE_EXCEEDS_CEILING in verdict.reasons

    def test_exposure_ceiling_is_enforced(self) -> None:
        verdict = make_mandate().permits(
            make_action(exposure=Exposure(monetary=50_000_000.0)), now=NOW
        )
        assert MandateDenialReason.EXPOSURE_EXCEEDS_CEILING in verdict.reasons

    def test_cross_tenant_action_is_refused(self) -> None:
        other = make_action(resource=ResourceRef(kind="account", id="ACC-1", tenant_id="evilcorp"))
        verdict = make_mandate().permits(other, now=NOW)
        assert MandateDenialReason.WRONG_TENANT in verdict.reasons

    def test_revocation_refuses_immediately(self) -> None:
        mandate = make_mandate(revoked_at=NOW - 1.0)
        assert mandate.is_active_at(NOW) is False
        assert MandateDenialReason.REVOKED in mandate.permits(make_action(), now=NOW).reasons

    def test_expiry_and_not_yet_valid_are_distinguished(self) -> None:
        mandate = make_mandate(valid_from=NOW, valid_until=NOW + HOUR)
        assert (
            MandateDenialReason.NOT_YET_VALID
            in mandate.permits(make_action(), now=NOW - 1.0).reasons
        )
        assert (
            MandateDenialReason.EXPIRED
            in mandate.permits(make_action(), now=NOW + 2 * HOUR).reasons
        )

    def test_all_failing_dimensions_are_reported(self) -> None:
        """An operator sees the whole gap, not one condition at a time."""
        verdict = make_mandate(
            max_consequence=ConsequenceClass.ADVISORY,
            allowed_actions=frozenset(),
        ).permits(make_action(exposure=Exposure(monetary=1e8)), now=NOW)
        assert MandateDenialReason.ACTION_NOT_GRANTED in verdict.reasons
        assert MandateDenialReason.CONSEQUENCE_EXCEEDS_CEILING in verdict.reasons
        assert MandateDenialReason.EXPOSURE_EXCEEDS_CEILING in verdict.reasons

    def test_version_must_be_positive(self) -> None:
        with pytest.raises(DomainValidationError):
            make_mandate(version=0)


class TestResourceScopedGrants:
    """Joint (action, resource) grants close the independent-set gap.

    Without ``resource_scoped_grants``, ``allowed_actions`` and
    ``allowed_resources`` are two independent sets: any granted action is
    implicitly permitted against any granted resource. This is the
    Workstream F fix that lets a mandate express "this action only, on that
    resource only" instead.
    """

    def test_no_scoped_grants_preserves_the_independent_set_behaviour(self) -> None:
        """Backward compatibility: an empty resource_scoped_grants changes nothing."""
        mandate = make_mandate()
        assert mandate.resource_scoped_grants == ()
        verdict = mandate.permits(make_action(), now=NOW)
        assert verdict.permitted is True

    def test_a_matching_pair_is_permitted(self) -> None:
        mandate = make_mandate(
            resource_scoped_grants=(
                ActionResourceGrant(
                    action_pattern="payments.wire_transfer", resource_pattern="account/ACC-1"
                ),
            )
        )
        verdict = mandate.permits(
            make_action(resource=ResourceRef(kind="account", id="ACC-1", tenant_id="acme")),
            now=NOW,
        )
        assert verdict.permitted is True

    def test_independently_granted_action_and_resource_are_not_enough_alone(self) -> None:
        """The whole point: being in both independent sets is not sufficient
        once a scoped grant exists -- the pair itself must be granted."""
        mandate = make_mandate(
            allowed_actions=frozenset({"payments.wire_transfer", "payments.refund"}),
            allowed_resources=frozenset({"account/ACC-1", "account/ACC-2"}),
            resource_scoped_grants=(
                ActionResourceGrant(
                    action_pattern="payments.wire_transfer", resource_pattern="account/ACC-1"
                ),
                ActionResourceGrant(
                    action_pattern="payments.refund", resource_pattern="account/ACC-2"
                ),
            ),
        )
        # Both independently granted, but this exact pair was never jointly granted.
        cross_action = make_action(
            action="payments.wire_transfer",
            resource=ResourceRef(kind="account", id="ACC-2", tenant_id="acme"),
        )
        verdict = mandate.permits(cross_action, now=NOW)
        assert verdict.permitted is False
        assert MandateDenialReason.ACTION_RESOURCE_PAIR_NOT_GRANTED in verdict.reasons

    def test_glob_patterns_still_work_within_a_scoped_grant(self) -> None:
        mandate = make_mandate(
            resource_scoped_grants=(
                ActionResourceGrant(action_pattern="payments.*", resource_pattern="account/ACC-*"),
            )
        )
        verdict = mandate.permits(
            make_action(resource=ResourceRef(kind="account", id="ACC-99", tenant_id="acme")),
            now=NOW,
        )
        assert verdict.permitted is True

    def test_as_evidence_includes_scoped_grants(self) -> None:
        mandate = make_mandate(
            resource_scoped_grants=(
                ActionResourceGrant(action_pattern="payments.*", resource_pattern="account/*"),
            )
        )
        evidence = mandate.as_evidence()
        assert evidence["resource_scoped_grants"] == [
            {"action_pattern": "payments.*", "resource_pattern": "account/*"}
        ]

    def test_non_grant_members_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            make_mandate(resource_scoped_grants=("not-a-grant",))

    def test_empty_patterns_are_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            ActionResourceGrant(action_pattern="", resource_pattern="account/*")
        with pytest.raises(DomainValidationError):
            ActionResourceGrant(action_pattern="payments.*", resource_pattern="")


class TestToolGrant:
    """Tool identity is the definition digest, not the name."""

    def test_matching_name_and_digest_is_granted(self) -> None:
        mandate = make_mandate(
            tool_grants=(ToolGrant(tool_name="wire.send", definition_sha256=DIGEST_A),)
        )
        assert mandate.permits_tool("wire.send", DIGEST_A).permitted is True

    def test_unknown_tool_is_refused(self) -> None:
        """v1 executed 'wipe_production_database' because it was unmapped."""
        verdict = make_mandate().permits_tool("wipe_production_database", DIGEST_A)
        assert verdict.permitted is False
        assert MandateDenialReason.TOOL_NOT_GRANTED in verdict.reasons

    def test_changed_definition_is_refused_as_a_rug_pull(self) -> None:
        mandate = make_mandate(
            tool_grants=(ToolGrant(tool_name="wire.send", definition_sha256=DIGEST_A),)
        )
        verdict = mandate.permits_tool("wire.send", DIGEST_B)
        assert MandateDenialReason.TOOL_DEFINITION_CHANGED in verdict.reasons

    def test_digest_must_be_hex_sha256(self) -> None:
        with pytest.raises(DomainValidationError):
            ToolGrant(tool_name="wire.send", definition_sha256="not-a-digest")

    def test_duplicate_grants_are_rejected(self) -> None:
        grant = ToolGrant(tool_name="wire.send", definition_sha256=DIGEST_A)
        with pytest.raises(DomainValidationError):
            make_mandate(tool_grants=(grant, grant))


class TestMandateVerdict:
    """A permitted verdict cannot be produced by accident."""

    def test_refusal_requires_a_reason(self) -> None:
        with pytest.raises(DomainValidationError):
            MandateVerdict(permitted=False)

    def test_permitted_verdict_cannot_carry_reasons(self) -> None:
        with pytest.raises(DomainValidationError):
            MandateVerdict(permitted=True, reasons=(MandateDenialReason.REVOKED,))


# --------------------------------------------------------------------------- #
# Limits
# --------------------------------------------------------------------------- #


class TestLimitKey:
    """Keys are pure functions of their fields, so replicas agree."""

    def test_canonical_key_is_stable_and_scoped(self) -> None:
        key = LimitKey(
            tenant_id="acme",
            scope=LimitScope.AGENT,
            subject="agent.treasury-bot",
            window=Window(60),
            action="payments.wire_transfer",
        )
        assert key.canonical_key() == (
            "glassbox|limit|acme|agent|agent.treasury-bot|payments.wire_transfer|60s"
        )
        assert (
            key.canonical_key()
            == LimitKey(
                tenant_id="acme",
                scope=LimitScope.AGENT,
                subject="agent.treasury-bot",
                window=Window(60),
                action="payments.wire_transfer",
            ).canonical_key()
        )

    def test_different_tenants_never_share_a_key(self) -> None:
        base = dict(scope=LimitScope.AGENT, subject="a", window=Window(60))
        assert (
            LimitKey(tenant_id="acme", **base).canonical_key()
            != LimitKey(tenant_id="evilcorp", **base).canonical_key()
        )

    def test_same_tick_admissions_get_distinct_members(self) -> None:
        """Regression: v1's `ZADD key now now` collapsed same-tick decisions."""
        first = LimitKey.member_for("decision-a", NOW)
        second = LimitKey.member_for("decision-b", NOW)
        assert first != second

    def test_window_must_be_a_positive_whole_number_of_seconds(self) -> None:
        with pytest.raises(DomainValidationError):
            Window(0)
        with pytest.raises(DomainValidationError):
            Window(-1)
        with pytest.raises(DomainValidationError):
            Window(1.5)  # type: ignore[arg-type]

    def test_window_start_is_derived_from_the_injected_clock(self) -> None:
        assert Window(60).start_of(NOW) == NOW - 60.0


class TestRedisTenantIsolation:
    """Tenant-aware key layout must keep per-tenant counters and baselines isolated."""

    def test_limit_store_uses_a_tenant_hash_tag_for_cluster_safe_scoping(self) -> None:
        from glassbox.adapters.outbound.redis import RedisLimitStore

        class DummyClient:
            def register_script(self, script):
                return script

        store = RedisLimitStore(DummyClient(), key_prefix="test:")
        key = LimitKey(
            tenant_id="acme",
            scope=LimitScope.AGENT,
            subject="agent.treasury-bot",
            window=Window(60),
            action="payments.wire_transfer",
        )
        assert store._keys(key) == [
            "test:{acme}:glassbox|limit|acme|agent|agent.treasury-bot|payments.wire_transfer|60s:w",
            "test:{acme}:glassbox|limit|acme|agent|agent.treasury-bot|payments.wire_transfer|60s:c",
            "test:{acme}:glassbox|limit|acme|agent|agent.treasury-bot|payments.wire_transfer|60s:cd",
        ]

    def test_baseline_store_uses_a_tenant_hash_tag_for_cluster_safe_scoping(self) -> None:
        from glassbox.adapters.outbound.redis import RedisBaselineStore

        class DummyClient:
            def register_script(self, script):
                return script

        store = RedisBaselineStore(DummyClient(), key_prefix="test:")
        key = BaselineKey(
            tenant_id="acme",
            scope=BaselineScope.AGENT,
            subject="agent.treasury-bot",
            metric="exposure_monetary",
            window=Window(60),
        )
        assert store._redis_key(key) == (
            "test:{acme}:glassbox|baseline|acme|agent|agent.treasury-bot|"
            "exposure_monetary|60s"
        )


class TestLimitVerdict:
    """There is deliberately no 'store unavailable' verdict."""

    def test_admitted_verdict_cannot_exceed_the_limit(self) -> None:
        key = LimitKey(tenant_id="acme", scope=LimitScope.AGENT, subject="a", window=Window(60))
        with pytest.raises(DomainValidationError):
            LimitVerdict(admitted=True, key=key, limit=5.0, observed=6.0, evaluated_at=NOW)

    def test_remaining_is_never_negative(self) -> None:
        key = LimitKey(tenant_id="acme", scope=LimitScope.AGENT, subject="a", window=Window(60))
        verdict = LimitVerdict(admitted=False, key=key, limit=5.0, observed=9.0, evaluated_at=NOW)
        assert verdict.remaining == 0.0

    def test_cooldown_is_carried_by_the_verdict_not_the_process(self) -> None:
        """Regression: v1 kept `_tripped` locally while counting in Redis."""
        key = LimitKey(tenant_id="acme", scope=LimitScope.AGENT, subject="a", window=Window(60))
        verdict = LimitVerdict(
            admitted=False,
            key=key,
            limit=5.0,
            observed=9.0,
            evaluated_at=NOW,
            cooldown_until=NOW + 300.0,
        )
        assert verdict.is_in_cooldown(NOW + 10.0) is True
        assert verdict.is_in_cooldown(NOW + 400.0) is False


# --------------------------------------------------------------------------- #
# Decisions
# --------------------------------------------------------------------------- #


class TestAuthorizationDecision:
    """Deny by default, structurally (invariant I4)."""

    def test_default_construction_denies(self) -> None:
        """v1's _authorize_request allowed when access control was None."""
        with pytest.raises(DomainValidationError):
            AuthorizationDecision()

    def test_denial_requires_a_machine_readable_reason(self) -> None:
        with pytest.raises(DomainValidationError):
            AuthorizationDecision.deny(rationale="because")  # type: ignore[call-arg]

    def test_allow_must_cite_the_authorising_bundle(self) -> None:
        with pytest.raises(DomainValidationError):
            AuthorizationDecision(effect=DecisionEffect.ALLOW, rationale="ok", policy_bundle_id="b")

    def test_allow_records_bundle_identity(self) -> None:
        decision = make_allow_decision()
        assert decision.policy_bundle_sha256 == DIGEST_A
        assert decision.permits_dispatch() is True
        assert decision.is_denied is False

    def test_bundle_digest_must_be_sha256_hex(self) -> None:
        with pytest.raises(DomainValidationError):
            AuthorizationDecision.allow(
                rationale="ok", policy_bundle_id="b", policy_bundle_sha256="short"
            )

    def test_require_approval_does_not_permit_dispatch(self) -> None:
        decision = AuthorizationDecision.require_approval(
            rationale="dual control", policy_bundle_id="b", policy_bundle_sha256=DIGEST_A
        )
        assert decision.permits_dispatch() is False
        assert decision.is_denied is False

    def test_require_approval_tracks_approval_lifecycle(self) -> None:
        decision = AuthorizationDecision.require_approval(
            rationale="dual control",
            policy_bundle_id="b",
            policy_bundle_sha256=DIGEST_A,
            approval_id="approval-8",
            approval_state=ApprovalState.PENDING,
        )
        assert decision.approval_id == "approval-8"
        assert decision.approval_state is ApprovalState.PENDING
        assert decision.as_evidence()["approval_state"] == "pending"

    def test_approval_lifecycle_transitions_are_explicit(self) -> None:
        approval = Approval(
            approval_id="approval-9",
            decision_id="decision-9",
            tenant_id="acme",
            action=make_action(),
            requested_at=NOW,
            rationale="dual control required",
        )
        in_review = approval.transition(
            state=ApprovalState.IN_REVIEW,
            actor="analyst@example.com",
            notes="review started",
            reviewed_at=NOW + 30.0,
        )
        approved = in_review.transition(
            state=ApprovalState.APPROVED,
            actor="manager@example.com",
            notes="approved after review",
            reviewed_at=NOW + 60.0,
        )
        assert approval.state is ApprovalState.PENDING
        assert in_review.state is ApprovalState.IN_REVIEW
        assert approved.state is ApprovalState.APPROVED
        assert approved.is_terminal is True
        assert approved.as_evidence()["approval_id"] == "approval-9"

    def test_denial_cannot_carry_obligations(self) -> None:
        obligation = Obligation(kind=ObligationKind.NOTIFY, obligation_id="ob-1")
        with pytest.raises(DomainValidationError):
            AuthorizationDecision(
                effect=DecisionEffect.DENY,
                reasons=(DenialReason.POLICY_DENIED,),
                rationale="no",
                obligations=(obligation,),
            )

    def test_blocking_obligations_are_isolated(self) -> None:
        decision = AuthorizationDecision.allow(
            rationale="ok",
            policy_bundle_id="b",
            policy_bundle_sha256=DIGEST_A,
            obligations=(
                Obligation(kind=ObligationKind.NOTIFY, obligation_id="ob-1"),
                Obligation(kind=ObligationKind.DUAL_CONTROL, obligation_id="ob-2", blocking=True),
            ),
        )
        assert [ob.obligation_id for ob in decision.blocking_obligations] == ["ob-2"]

    def test_evidence_payload_is_canonically_serialisable(self) -> None:
        assert canonical_bytes(dict(make_allow_decision().as_evidence()))


class TestAuthorizationRequest:
    """No channel exists for caller-asserted governance inputs (invariant I2)."""

    def test_request_has_no_confidence_environment_or_agent_chain_fields(self) -> None:
        fields = set(AuthorizationRequest.__dataclass_fields__)
        assert not fields & {"confidence", "environment", "agent_chain"}

    def test_principal_and_resource_tenants_must_agree(self) -> None:
        with pytest.raises(DomainValidationError):
            AuthorizationRequest(
                decision_id="decision-1",
                principal=make_principal(tenant_id="acme"),
                action=make_action(
                    resource=ResourceRef(kind="account", id="ACC-1", tenant_id="evilcorp")
                ),
                evaluated_at=NOW,
            )

    def test_principal_must_be_a_verified_principal(self) -> None:
        with pytest.raises(DomainValidationError):
            AuthorizationRequest(
                decision_id="decision-1",
                principal={"tenant_id": "acme"},  # type: ignore[arg-type]
                action=make_action(),
                evaluated_at=NOW,
            )

    def test_tenant_is_read_from_the_principal(self) -> None:
        request = AuthorizationRequest(
            decision_id="decision-1",
            principal=make_principal(),
            action=make_action(),
            evaluated_at=NOW,
        )
        assert request.tenant_id == "acme"


class TestStageOutcome:
    """A control that did not run is never silent (invariant I9)."""

    def test_skipping_requires_a_reason(self) -> None:
        with pytest.raises(DomainValidationError):
            StageOutcome(stage="contract", status=StageStatus.SKIPPED)

    def test_failure_requires_a_reason(self) -> None:
        with pytest.raises(DomainValidationError):
            StageOutcome(stage="contract", status=StageStatus.FAILED)

    def test_skipped_stage_is_flagged_as_a_missing_control(self) -> None:
        outcome = StageOutcome(
            stage="contract", status=StageStatus.SKIPPED, reason="no contract registered"
        )
        assert outcome.is_missing_control is True

    def test_executed_stage_needs_no_reason(self) -> None:
        assert StageOutcome(stage="policy", status=StageStatus.EXECUTED).is_missing_control is False


class TestExecutionOutcome:
    """A timeout is indeterminate, not a clean failure."""

    def test_indeterminate_status_exists(self) -> None:
        outcome = ExecutionOutcome(status=ExecutionStatus.INDETERMINATE, completed_at=NOW)
        assert outcome.status.is_terminal is True

    def test_failure_must_record_its_error_class(self) -> None:
        with pytest.raises(DomainValidationError):
            ExecutionOutcome(status=ExecutionStatus.FAILED, completed_at=NOW)

    def test_result_digest_must_be_sha256_hex(self) -> None:
        with pytest.raises(DomainValidationError):
            ExecutionOutcome(
                status=ExecutionStatus.EXECUTED, completed_at=NOW, result_digest="raw-payload"
            )

    def test_pending_approval_is_not_terminal(self) -> None:
        assert ExecutionStatus.PENDING_APPROVAL.is_terminal is False


# --------------------------------------------------------------------------- #
# Evidence
# --------------------------------------------------------------------------- #


class TestIntentRecord:
    """The pre-effect record and its chain payload."""

    def test_tenant_must_agree_across_principal_action_and_record(self) -> None:
        with pytest.raises(DomainValidationError):
            make_intent(tenant_id="evilcorp")

    def test_skipped_stages_are_extracted_for_evidence(self) -> None:
        record = make_intent(
            stages=(
                StageOutcome(stage="policy", status=StageStatus.EXECUTED),
                StageOutcome(
                    stage="contract", status=StageStatus.SKIPPED, reason="none registered"
                ),
            )
        )
        assert [stage.stage for stage in record.skipped_stages] == ["contract"]
        assert len(record.as_evidence()["skipped_stages"]) == 1

    def test_record_carries_the_provenance_v1_never_captured(self) -> None:
        record = make_intent(
            provenance=ModelProvenance(
                model_id="claude-opus-5",
                model_version="2026-05-01",
                prompt_sha256=DIGEST_A,
                tools_invoked=("wire.send",),
                data_sources=("crm.customers",),
            )
        )
        provenance = record.as_evidence()["provenance"]
        assert provenance["model_id"] == "claude-opus-5"
        assert provenance["tools_invoked"] == ["wire.send"]

    def test_evidence_payload_is_canonically_serialisable(self) -> None:
        assert canonical_bytes(dict(make_intent().as_evidence()))


class TestChainPayload:
    """What makes forgery, deletion and re-ordering all detectable."""

    def test_payload_is_deterministic(self) -> None:
        first = make_intent().chain_payload(seq=0, prev_hash=GENESIS_PREV_HASH)
        second = make_intent().chain_payload(seq=0, prev_hash=GENESIS_PREV_HASH)
        assert first == second

    def test_mutating_any_field_changes_the_payload(self) -> None:
        """Regression: v1's forged record re-verified as intact."""
        original = make_intent().chain_payload(seq=0, prev_hash=GENESIS_PREV_HASH)
        forged = make_intent(
            action=make_action(exposure=Exposure(monetary=999_999_999.0))
        ).chain_payload(seq=0, prev_hash=GENESIS_PREV_HASH)
        assert original != forged

    def test_changing_position_changes_the_payload(self) -> None:
        """Binding seq into the payload is what detects re-ordering."""
        record = make_intent()
        assert record.chain_payload(seq=0, prev_hash=GENESIS_PREV_HASH) != record.chain_payload(
            seq=1, prev_hash=GENESIS_PREV_HASH
        )

    def test_changing_the_predecessor_changes_the_payload(self) -> None:
        """Binding prev_hash is what detects deletion of an earlier record."""
        record = make_intent()
        assert record.chain_payload(seq=1, prev_hash=GENESIS_PREV_HASH) != record.chain_payload(
            seq=1, prev_hash=b"\x01" * 32
        )

    def test_prev_hash_must_be_exactly_32_bytes(self) -> None:
        record = make_intent()
        with pytest.raises(DomainValidationError):
            record.chain_payload(seq=0, prev_hash=b"\x00" * 31)

    def test_negative_sequence_is_rejected(self) -> None:
        with pytest.raises(DomainValidationError):
            make_intent().chain_payload(seq=-1, prev_hash=GENESIS_PREV_HASH)


class TestEvidenceReceipt:
    """A receipt is proof of durability; there is no pending variant."""

    @staticmethod
    def _receipt(**overrides: Any) -> EvidenceReceipt:
        kwargs: Dict[str, Any] = {
            "decision_id": "decision-0001",
            "segment_id": "seg-2026-08",
            "seq": 0,
            "record_hmac": b"\x2a" * 32,
            "signer_key_id": "kms.evidence.v1",
            "persisted_at": NOW,
        }
        kwargs.update(overrides)
        return EvidenceReceipt(**kwargs)

    def test_mac_must_be_at_least_256_bits(self) -> None:
        """A short MAC signals a weakened or unkeyed digest."""
        with pytest.raises(DomainValidationError):
            self._receipt(record_hmac=b"\x2a" * 16)

    def test_mac_must_be_bytes_not_hex(self) -> None:
        with pytest.raises(DomainValidationError):
            self._receipt(record_hmac="2a" * 32)

    def test_signing_key_is_recorded_for_rotation(self) -> None:
        assert self._receipt().signer_key_id == "kms.evidence.v1"

    def test_repr_does_not_dump_mac_bytes(self) -> None:
        assert "\\x2a" not in repr(self._receipt())
        assert "32 bytes" in repr(self._receipt())

    def test_genesis_is_identified_by_sequence(self) -> None:
        assert self._receipt(seq=0).is_genesis is True
        assert self._receipt(seq=1).is_genesis is False


class TestEvidenceSegment:
    """Sealing is what decouples retention from integrity."""

    def test_open_segment_is_not_sealed_or_anchored(self) -> None:
        segment = EvidenceSegment(segment_id="seg-1", tenant_id="acme", opened_at=NOW)
        assert segment.is_sealed is False
        assert segment.is_anchored is False
        assert segment.record_count is None

    def test_sealing_is_all_or_nothing(self) -> None:
        with pytest.raises(DomainValidationError):
            EvidenceSegment(
                segment_id="seg-1", tenant_id="acme", opened_at=NOW, sealed_at=NOW + 1.0
            )

    def test_sealed_segment_reports_its_record_count(self) -> None:
        segment = EvidenceSegment(
            segment_id="seg-1",
            tenant_id="acme",
            opened_at=NOW,
            sealed_at=NOW + 1.0,
            first_seq=0,
            last_seq=9,
            merkle_root=b"\x01" * 32,
            seal_signature=b"\x02" * 64,
            worm_anchor_id="s3.object-lock.abc",
        )
        assert segment.record_count == 10
        assert segment.is_anchored is True

    def test_seal_cannot_precede_open(self) -> None:
        with pytest.raises(DomainValidationError):
            EvidenceSegment(
                segment_id="seg-1",
                tenant_id="acme",
                opened_at=NOW,
                sealed_at=NOW - 1.0,
                first_seq=0,
                last_seq=1,
                merkle_root=b"\x01" * 32,
                seal_signature=b"\x02" * 64,
            )


class TestIntegrityReport:
    """Retention no longer destroys verifiability."""

    def test_sealed_purged_is_acceptable_to_an_auditor(self) -> None:
        """Regression: v1's purge_old_records made verify_hash_chain return False."""
        report = IntegrityReport(
            segment_id="seg-1",
            status=IntegrityStatus.SEALED_PURGED,
            records_checked=0,
            verified_at=NOW,
            detail="purged under 7-year retention; merkle root anchored",
        )
        assert report.is_acceptable is True

    def test_broken_is_not_acceptable(self) -> None:
        report = IntegrityReport(
            segment_id="seg-1",
            status=IntegrityStatus.BROKEN,
            records_checked=10,
            verified_at=NOW,
            first_broken_seq=3,
        )
        assert report.is_acceptable is False

    def test_broken_must_localise_the_failure(self) -> None:
        with pytest.raises(DomainValidationError):
            IntegrityReport(
                segment_id="seg-1",
                status=IntegrityStatus.BROKEN,
                records_checked=10,
                verified_at=NOW,
            )

    def test_intact_must_not_name_a_failing_record(self) -> None:
        with pytest.raises(DomainValidationError):
            IntegrityReport(
                segment_id="seg-1",
                status=IntegrityStatus.INTACT,
                records_checked=10,
                verified_at=NOW,
                first_broken_seq=3,
            )

    def test_unverifiable_is_not_acceptable(self) -> None:
        """Imported v1 history is labelled honestly, not treated as intact."""
        report = IntegrityReport(
            segment_id="seg-v1",
            status=IntegrityStatus.UNVERIFIABLE,
            records_checked=0,
            verified_at=NOW,
            detail="v1_imported",
        )
        assert report.is_acceptable is False


class TestOutcomeRecord:
    """Outcome is a separate, later write keyed by decision id."""

    def test_outcome_carries_only_a_digest_of_the_result(self) -> None:
        record = OutcomeRecord(
            decision_id="decision-0001",
            outcome=ExecutionOutcome(
                status=ExecutionStatus.EXECUTED, completed_at=NOW, result_digest=DIGEST_A
            ),
        )
        payload = record.as_evidence()
        assert payload["result_digest"] == DIGEST_A
        assert canonical_bytes(dict(payload))


# --------------------------------------------------------------------------- #
# Cross-cutting invariants
# --------------------------------------------------------------------------- #


class TestDomainInvariants:
    """Properties that must hold across the whole layer."""

    @pytest.mark.parametrize(
        "factory",
        [
            make_principal,
            make_action,
            make_risk,
            make_allow_decision,
            make_intent,
            make_mandate,
        ],
    )
    def test_domain_objects_are_immutable(self, factory: Any) -> None:
        instance = factory()
        first_field = next(iter(type(instance).__dataclass_fields__))
        with pytest.raises(Exception):
            setattr(instance, first_field, "mutated")

    def test_every_evidence_payload_survives_canonical_serialisation(self) -> None:
        """If this fails, the hash chain cannot be computed for that record."""
        for payload in (
            make_principal().as_evidence(),
            make_action().as_evidence(),
            make_risk().as_evidence(),
            make_allow_decision().as_evidence(),
            make_mandate().as_evidence(),
            make_intent().as_evidence(),
        ):
            assert canonical_bytes(dict(payload))

    def test_no_domain_value_accepts_infinity(self) -> None:
        with pytest.raises(DomainValidationError):
            Exposure(monetary=math.inf)


class TestValueObjectContract:
    """Properties that must hold for *every* dataclass in the layer.

    These are discovered by walking the package rather than listed by hand, so a
    new value object added in a later wave is covered the moment it is written.
    """

    @staticmethod
    def _domain_dataclasses() -> List[type]:
        discovered: Dict[str, type] = {}
        package_root = pathlib.Path(glassbox.domain.__file__).resolve().parent
        for path in sorted(package_root.glob("*.py")):
            module = importlib.import_module(f"glassbox.domain.{path.stem}")
            for _, member in inspect.getmembers(module, inspect.isclass):
                if dataclasses.is_dataclass(member) and member.__module__.startswith(
                    "glassbox.domain"
                ):
                    discovered[f"{member.__module__}.{member.__name__}"] = member
        return list(discovered.values())

    def test_the_layer_actually_contains_dataclasses(self) -> None:
        assert len(self._domain_dataclasses()) >= 20

    def test_every_dataclass_is_frozen(self) -> None:
        """Frozen means a decision cannot be mutated while it is in flight."""
        offenders = [
            cls.__qualname__
            for cls in self._domain_dataclasses()
            if not cls.__dataclass_params__.frozen  # type: ignore[attr-defined]
        ]
        assert not offenders, f"mutable domain value objects: {offenders}"

    def test_every_dataclass_uses_slots(self) -> None:
        """Slots bound memory on the hot path and block attribute smuggling.

        Without ``__slots__`` an adapter can attach an arbitrary attribute to a
        governance object and carry it through the decision path unvalidated.
        """
        offenders = [
            cls.__qualname__ for cls in self._domain_dataclasses() if not hasattr(cls, "__slots__")
        ]
        assert not offenders, f"domain value objects without __slots__: {offenders}"

    def test_unknown_attributes_cannot_be_attached(self) -> None:
        principal = make_principal()
        # CPython 3.11's generated setter for frozen+slotted dataclasses raises
        # TypeError here; 3.12+ raises AttributeError. Both reject the mutation.
        with pytest.raises((AttributeError, TypeError)):
            principal.smuggled = "payload"  # type: ignore[attr-defined]
        assert not hasattr(principal, "smuggled")

    def test_instances_carry_no_instance_dict(self) -> None:
        assert not hasattr(make_action(), "__dict__")


class TestErrorContract:
    """Errors are the domain's only reporting channel, so they must be disciplined."""

    @staticmethod
    def _error_classes() -> List[type]:
        return [
            member
            for _, member in inspect.getmembers(glassbox.domain.errors, inspect.isclass)
            if issubclass(member, GlassBoxError)
        ]

    def test_every_error_declares_a_code(self) -> None:
        offenders = [
            cls.__name__
            for cls in self._error_classes()
            if cls is not GlassBoxError and cls.code == GlassBoxError.code
        ]
        assert not offenders, f"errors reusing the base code: {offenders}"

    def test_error_codes_are_unique(self) -> None:
        """A shared code would make two distinct failures indistinguishable in metrics."""
        codes = [cls.code for cls in self._error_classes()]
        duplicates = sorted({code for code in codes if codes.count(code) > 1})
        assert not duplicates, f"duplicate error codes: {duplicates}"

    def test_every_error_is_documented(self) -> None:
        offenders = [cls.__name__ for cls in self._error_classes() if not cls.__doc__]
        assert not offenders, f"undocumented errors: {offenders}"

    def test_context_values_are_always_strings(self) -> None:
        """Guarantees the context can be serialised into a log line or an evidence field."""
        error = LimitStoreUnavailable("down", attempts=3, ratio=0.5, flag=True)
        assert all(isinstance(value, str) for value in error.context.values())
        assert canonical_bytes(error.as_dict()["context"])
