"""Tests for the governed action catalogue domain types (GB-010).

Pure domain: no I/O. These tests exercise construction validation, exposure
derivation from parameters, and bundle-level lookup/digest behaviour -- the
port and adapter layers built on top are exercised in
``tests/test_decision_service.py::TestActionCatalogueStage``.
"""

from __future__ import annotations

import pytest

from glassbox.domain.action import BlastRadius, ConsequenceClass, Exposure
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition, ExposureRule
from glassbox.domain.errors import DomainValidationError


def test_exposure_rule_extracts_monetary_and_records_fields() -> None:
    rule = ExposureRule(
        blast_radius=BlastRadius.TENANT, monetary_field="amount", records_field="row_count"
    )
    exposure = rule.extract({"amount": 42.5, "row_count": 7})
    assert exposure == Exposure(blast_radius=BlastRadius.TENANT, monetary=42.5, records=7)


def test_exposure_rule_yields_unknown_not_zero_when_a_field_is_absent() -> None:
    rule = ExposureRule(monetary_field="amount")
    exposure = rule.extract({})
    assert exposure.monetary is None
    assert not exposure.is_quantified


def test_exposure_rule_ignores_a_field_of_the_wrong_type() -> None:
    """A caller-supplied string or bool where a number is expected must never
    silently coerce into a magnitude -- that would let a forged field influence
    exposure by accident of Python's truthiness."""
    rule = ExposureRule(monetary_field="amount", records_field="row_count")
    exposure = rule.extract({"amount": "a lot", "row_count": True})
    assert exposure.monetary is None
    assert exposure.records is None


def test_exposure_rule_ignores_unconfigured_fields() -> None:
    """A rule with no monetary_field configured never reads one, however the
    parameters are shaped -- the catalogue, not the request, decides what is read."""
    rule = ExposureRule()
    exposure = rule.extract({"amount": 999_999.0})
    assert exposure.monetary is None


class TestActionDefinition:
    def test_rejects_a_non_consequence_class(self) -> None:
        with pytest.raises(DomainValidationError):
            ActionDefinition(action="a", consequence="irreversible")  # type: ignore[arg-type]

    def test_rejects_duplicate_required_attestations(self) -> None:
        with pytest.raises(DomainValidationError):
            ActionDefinition(
                action="a",
                consequence=ConsequenceClass.REVERSIBLE,
                required_attestations=("ctr_filed", "ctr_filed"),
            )

    def test_as_evidence_is_a_plain_serialisable_mapping(self) -> None:
        definition = ActionDefinition(
            action="payments.wire_transfer",
            consequence=ConsequenceClass.COMPENSABLE,
            exposure_rule=ExposureRule(monetary_field="amount"),
            required_attestations=("ctr_filed",),
        )
        assert definition.as_evidence() == {
            "action": "payments.wire_transfer",
            "consequence": "compensable",
            "exposure_rule": {
                "blast_radius": "single",
                "monetary_field": "amount",
                "records_field": None,
            },
            "required_attestations": ["ctr_filed"],
            "parameter_schema": [],
            "untrusted_text_fields": [],
        }


class TestActionCatalogueBundle:
    def test_resolve_returns_none_for_an_ungoverned_action(self) -> None:
        bundle = ActionCatalogueBundle(bundle_id="b", tenant_id="acme", version=1)
        assert bundle.resolve("payments.wire_transfer") is None

    def test_resolve_finds_the_matching_definition(self) -> None:
        definition = ActionDefinition(
            action="payments.wire_transfer", consequence=ConsequenceClass.REVERSIBLE
        )
        bundle = ActionCatalogueBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(definition,)
        )
        assert bundle.resolve("payments.wire_transfer") is definition

    def test_rejects_duplicate_action_definitions(self) -> None:
        one = ActionDefinition(action="a", consequence=ConsequenceClass.REVERSIBLE)
        two = ActionDefinition(action="a", consequence=ConsequenceClass.COMPENSABLE)
        with pytest.raises(DomainValidationError):
            ActionCatalogueBundle(
                bundle_id="b", tenant_id="acme", version=1, definitions=(one, two)
            )

    def test_digest_is_stable_for_equal_content(self) -> None:
        definition = ActionDefinition(action="a", consequence=ConsequenceClass.REVERSIBLE)
        first = ActionCatalogueBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(definition,)
        )
        second = ActionCatalogueBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(definition,)
        )
        assert first.digest() == second.digest()

    def test_digest_changes_when_a_definition_changes(self) -> None:
        base = ActionCatalogueBundle(
            bundle_id="b",
            tenant_id="acme",
            version=1,
            definitions=(ActionDefinition(action="a", consequence=ConsequenceClass.REVERSIBLE),),
        )
        changed = ActionCatalogueBundle(
            bundle_id="b",
            tenant_id="acme",
            version=1,
            definitions=(ActionDefinition(action="a", consequence=ConsequenceClass.IRREVERSIBLE),),
        )
        assert base.digest() != changed.digest()
