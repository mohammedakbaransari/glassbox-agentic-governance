"""GB-029: a legitimate business corpus has a zero false-positive rate.

``security/sanitizer.py`` (v1) ran SQL- and script-injection regexes over every
payload string; the measured false positives included "Create purchase order
for Q3 and update the supplier record" (matched an UPDATE-statement pattern),
"Delete stale cache entries after deploy" (matched a DELETE-statement pattern),
a hex account number ``0xA1B2C3D4E5F6``, and "Grupo \u00c1gua & Caf\u00e9 Ltda"
(a unicode anomaly).

GB-029 replaces that with an allow-list schema per action
(:meth:`ActionDefinition.validate_parameters`) plus parameterised persistence
(asserted separately in ``test_no_string_formatted_sql.py``). Neither layer
inspects string *content* for suspicious patterns, so this corpus -- all of it
containing text that the v1 regex WAF blocked -- must pass every one of these
checks cleanly.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

import pytest

from glassbox.domain.action import BlastRadius, ConsequenceClass
from glassbox.domain.catalogue import ActionDefinition, ExposureRule, ParameterField, ParameterType

#: The exact measured false positives from the architecture review, plus a
#: broader sample of ordinary business language covering the same categories
#: the regex WAF misfired on: SQL keywords in prose, hex-looking identifiers,
#: non-ASCII names, punctuation-heavy addresses, and shell-looking phrasing.
LEGITIMATE_BUSINESS_CORPUS: Tuple[str, ...] = (
    "Create purchase order for Q3 and update the supplier record",
    "Delete stale cache entries after deploy",
    "0xA1B2C3D4E5F6",
    "Grupo \u00c1gua & Caf\u00e9 Ltda",
    "Select the preferred vendor from the approved list",
    "Insert the new employee into the onboarding queue",
    "Drop off the signed contract at the front desk",
    "Union Pacific Railroad invoice #48213",
    "Execute the quarterly compliance review by Friday",
    "The system administrator will process the request",
    "Alter the delivery schedule to account for the holiday",
    "Truncate the report to the first ten line items",
    "Please expedite; waiting for sleep schedule confirmation",
    "Café René & Fils, S.A. de C.V.",
    "Sch\u00f6n & Partner GmbH -- Rechnung Nr. 00231",
    "Contact: jos\u00e9.p\u00e9rez@example.com re: purchase order",
    "Benchmark pricing against last quarter's supplier rates",
    "or equivalent substitute part is acceptable",
    "and update the shipping address on file",
    "convert the invoice total to the local currency",
)


def _wire_transfer_definition(**overrides: Any) -> ActionDefinition:
    fields = overrides.pop(
        "parameter_schema",
        (
            ParameterField(name="amount", type=ParameterType.NUMBER, required=True),
            ParameterField(name="memo", type=ParameterType.STRING, max_length=500),
            ParameterField(name="destination", type=ParameterType.STRING, max_length=200),
        ),
    )
    return ActionDefinition(
        action="payments.wire_transfer",
        consequence=ConsequenceClass.COMPENSABLE,
        exposure_rule=ExposureRule(blast_radius=BlastRadius.SINGLE, monetary_field="amount"),
        parameter_schema=fields,
        **overrides,
    )


class TestLegitimateBusinessCorpusHasNoFalsePositives:
    """None of these must be rejected -- the whole point of GB-029."""

    @pytest.mark.parametrize("memo", LEGITIMATE_BUSINESS_CORPUS)
    def test_the_memo_field_is_accepted(self, memo: str) -> None:
        definition = _wire_transfer_definition()
        parameters: Mapping[str, Any] = {"amount": 101.0, "memo": memo}
        assert definition.validate_parameters(parameters) == ()

    def test_the_entire_corpus_passes_in_one_batch(self) -> None:
        """A false-positive rate of exactly zero across the whole corpus."""
        definition = _wire_transfer_definition()
        false_positives = [
            memo
            for memo in LEGITIMATE_BUSINESS_CORPUS
            if definition.validate_parameters({"amount": 101.0, "memo": memo})
        ]
        assert false_positives == [], f"unexpected false positives: {false_positives}"

    def test_unicode_names_are_never_treated_as_anomalies(self) -> None:
        """Regression: 'Grupo \u00c1gua & Caf\u00e9 Ltda' registered as a unicode
        anomaly under the old regex WAF. A schema has no notion of 'anomalous
        unicode' at all -- it checks type and length, nothing else."""
        definition = _wire_transfer_definition()
        assert (
            definition.validate_parameters(
                {"amount": 1.0, "destination": "Grupo \u00c1gua & Caf\u00e9 Ltda"}
            )
            == ()
        )


class TestSchemaStillRejectsStructurallyInvalidInput:
    """The allow-list is not toothless: it rejects shape violations, just never
    content patterns."""

    def test_an_unmapped_parameter_is_rejected(self) -> None:
        definition = _wire_transfer_definition()
        violations = definition.validate_parameters({"amount": 1.0, "unexpected_field": "x"})
        assert any("unexpected_field" in v for v in violations)

    def test_a_missing_required_parameter_is_rejected(self) -> None:
        definition = _wire_transfer_definition()
        violations = definition.validate_parameters({"memo": "no amount supplied"})
        assert any("amount" in v for v in violations)

    def test_a_wrong_typed_parameter_is_rejected(self) -> None:
        definition = _wire_transfer_definition()
        violations = definition.validate_parameters({"amount": "not a number"})
        assert any("amount" in v for v in violations)

    def test_an_oversized_string_is_rejected(self) -> None:
        definition = _wire_transfer_definition()
        violations = definition.validate_parameters({"amount": 1.0, "memo": "x" * 501})
        assert any("memo" in v for v in violations)

    def test_an_action_with_no_schema_validates_nothing(self) -> None:
        """An action not yet migrated to a schema is not silently blocked."""
        definition = ActionDefinition(action="legacy.action", consequence=ConsequenceClass.ADVISORY)
        assert (
            definition.validate_parameters({"anything": "goes", "amount": "not even numeric"}) == ()
        )
