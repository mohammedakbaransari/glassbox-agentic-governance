"""Tests proving GB-021's acceptance criteria against the existing engine.

The non-saturating, monotonic aggregation and the consequence floor were
delivered structurally in GB-002/GB-003 (see
``ReferenceRiskEngine``/``RiskScore.with_consequence_floor``). This file is
the formal, named regression suite the plan calls for
(``test_risk_determinism.py``): determinism, monotonicity, and the headline
finding this rebuild exists to fix -- a $50M irreversible transfer must never
score anything but ``critical``.
"""

from __future__ import annotations

from glassbox.adapters.outbound.memory.decisioning import ReferenceRiskEngine
from glassbox.domain.action import ConsequenceClass, Exposure
from glassbox.domain.risk import RiskFactor, RiskInputs, RiskLevel

NOW = 1_760_000_000.0


def _inputs(*, consequence: ConsequenceClass, monetary: float, factors=()) -> RiskInputs:
    return RiskInputs(
        consequence=consequence,
        exposure=Exposure(monetary=monetary),
        evaluated_at=NOW,
        factors=factors,
    )


class TestDeterminism:
    def test_identical_inputs_score_identically(self) -> None:
        engine = ReferenceRiskEngine()
        inputs = _inputs(
            consequence=ConsequenceClass.COMPENSABLE,
            monetary=5_000.0,
            factors=(RiskFactor(name="velocity", score=40.0, rationale="elevated"),),
        )
        first = engine.score(inputs)
        second = engine.score(inputs)
        assert first.value == second.value
        assert first.level == second.level

    def test_score_with_model_reproduces_a_pinned_version(self) -> None:
        engine = ReferenceRiskEngine()
        inputs = _inputs(consequence=ConsequenceClass.REVERSIBLE, monetary=10.0)
        live = engine.score(inputs)
        replayed = engine.score_with_model(inputs, live.model_version)
        assert replayed.value == live.value


class TestMonotonicity:
    def test_adding_a_factor_never_lowers_the_score(self) -> None:
        engine = ReferenceRiskEngine()
        base = _inputs(
            consequence=ConsequenceClass.COMPENSABLE,
            monetary=1_000.0,
            factors=(RiskFactor(name="a", score=30.0, rationale="r"),),
        )
        more = _inputs(
            consequence=ConsequenceClass.COMPENSABLE,
            monetary=1_000.0,
            factors=(
                RiskFactor(name="a", score=30.0, rationale="r"),
                RiskFactor(name="b", score=40.0, rationale="r"),
            ),
        )
        assert engine.score(more).value >= engine.score(base).value

    def test_a_higher_factor_score_never_lowers_the_result(self) -> None:
        engine = ReferenceRiskEngine()
        low = _inputs(
            consequence=ConsequenceClass.REVERSIBLE,
            monetary=1.0,
            factors=(RiskFactor(name="a", score=10.0, rationale="r"),),
        )
        high = _inputs(
            consequence=ConsequenceClass.REVERSIBLE,
            monetary=1.0,
            factors=(RiskFactor(name="a", score=90.0, rationale="r"),),
        )
        assert engine.score(high).value >= engine.score(low).value

    def test_a_benign_factor_never_dilutes_a_severe_one(self) -> None:
        """Regression for v1's weighted mean: a saturating average let a benign
        factor pull a severe one down. Non-saturating aggregation cannot."""
        engine = ReferenceRiskEngine()
        severe_alone = _inputs(
            consequence=ConsequenceClass.IRREVERSIBLE,
            monetary=50_000_000.0,
            factors=(RiskFactor(name="amount", score=95.0, rationale="large transfer"),),
        )
        severe_plus_benign = _inputs(
            consequence=ConsequenceClass.IRREVERSIBLE,
            monetary=50_000_000.0,
            factors=(
                RiskFactor(name="amount", score=95.0, rationale="large transfer"),
                RiskFactor(name="tenure", score=1.0, rationale="long-tenured agent"),
            ),
        )
        assert engine.score(severe_plus_benign).value >= engine.score(severe_alone).value - 0.001


class TestGoldenCorpus:
    """Fixed, named cases a future model change must not silently regress."""

    def test_a_fifty_million_irreversible_transfer_scores_critical(self) -> None:
        """The measured v1 finding this rebuild exists to fix: $50M scored 27.5/medium."""
        engine = ReferenceRiskEngine()
        inputs = _inputs(
            consequence=ConsequenceClass.IRREVERSIBLE,
            monetary=50_000_000.0,
            factors=(
                RiskFactor(
                    name="amount_vs_peer_group", score=95.0, rationale="far above peer norm"
                ),
            ),
        )
        score = engine.score(inputs)
        assert score.level is RiskLevel.CRITICAL

    def test_a_trivial_advisory_action_scores_low(self) -> None:
        engine = ReferenceRiskEngine()
        inputs = _inputs(consequence=ConsequenceClass.ADVISORY, monetary=1.0)
        assert engine.score(inputs).level is RiskLevel.LOW

    def test_an_irreversible_action_never_scores_below_its_floor_even_with_no_factors(
        self,
    ) -> None:
        engine = ReferenceRiskEngine()
        inputs = _inputs(consequence=ConsequenceClass.IRREVERSIBLE, monetary=1.0, factors=())
        assert engine.score(inputs).level >= RiskLevel.HIGH
