"""Risk scoring value objects (GB-002, WS-4 foundation).

Three v1 defects are fixed structurally here; the scoring engine that consumes
these types is GB-021.

1. **One banding table.** v1 had ``_score_to_level`` banding at 25/50/75 and
   ``_score_to_disposition`` banding at 35/70, so the level shown to a human and
   the level acted on by the pipeline could disagree. :data:`RISK_BANDS` is the
   only banding table in the system and :meth:`RiskLevel.from_score` is the only
   way to derive a level.

2. **A consequence floor.** v1 computed a weighted mean whose weights summed to
   1.0, which saturates: a $50,000,000 irreversible transfer scored 27.5 /
   "medium". :meth:`RiskScore.with_consequence_floor` raises a score to the
   minimum permitted for its consequence class and records that it did so, so
   the floor is visible in evidence rather than hidden in a formula.

3. **Determinism.** v1 read ``datetime.now().hour`` inside scoring, so replaying
   a decision produced a different score and the audit trail could not be
   reproduced. Nothing in this module reads a clock; ``evaluated_at`` is supplied
   by the caller from the injected :class:`~glassbox.ports.clock.Clock`
   (invariant I6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional, Tuple

from glassbox.domain.action import ConsequenceClass, Exposure
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    freeze_mapping,
    require_identifier,
    require_non_empty,
    require_timestamp,
)

__all__ = [
    "RiskLevel",
    "RiskFactor",
    "RiskInputs",
    "RiskScore",
    "RISK_BANDS",
    "CONSEQUENCE_FLOORS",
    "MIN_RISK_SCORE",
    "MAX_RISK_SCORE",
]

#: Risk scores are expressed on a fixed 0-100 scale.
MIN_RISK_SCORE = 0.0
MAX_RISK_SCORE = 100.0


class RiskLevel(Enum):
    """Ordered risk bands. The only vocabulary used for dispositioning."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"

    @property
    def severity(self) -> int:
        """Ordinal severity, ``0`` (low) to ``3`` (critical)."""
        return _RISK_SEVERITY[self]

    @classmethod
    def from_score(cls, score: float) -> "RiskLevel":
        """Map a 0-100 score onto a band using the single system-wide table.

        Args:
            score: A finite score within ``[0, 100]``.

        Returns:
            The band whose lower bound is the greatest bound not exceeding
            ``score``.

        Raises:
            DomainValidationError: If ``score`` is out of range or not finite.
        """
        value = _require_score(score, field="score")
        selected = RiskLevel.LOW
        for lower_bound, level in RISK_BANDS:
            if value >= lower_bound:
                selected = level
        return selected

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity < other.severity

    def __le__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity <= other.severity

    def __gt__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity > other.severity

    def __ge__(self, other: object) -> bool:
        if not isinstance(other, RiskLevel):
            return NotImplemented
        return self.severity >= other.severity


_RISK_SEVERITY: Mapping[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}

#: The single banding table: ``(inclusive lower bound, level)`` ascending.
#: Changing these numbers changes every disposition in the system, which is the
#: point -- there is exactly one place to change them.
RISK_BANDS: Tuple[Tuple[float, RiskLevel], ...] = (
    (0.0, RiskLevel.LOW),
    (25.0, RiskLevel.MEDIUM),
    (50.0, RiskLevel.HIGH),
    (75.0, RiskLevel.CRITICAL),
)

#: Minimum band an action may be scored at, given its consequence class.
#: An irreversible action is never "low risk" no matter how benign its factors
#: look, because the cost of being wrong is unbounded.
CONSEQUENCE_FLOORS: Mapping[ConsequenceClass, RiskLevel] = {
    ConsequenceClass.ADVISORY: RiskLevel.LOW,
    ConsequenceClass.REVERSIBLE: RiskLevel.LOW,
    ConsequenceClass.COMPENSABLE: RiskLevel.MEDIUM,
    ConsequenceClass.IRREVERSIBLE: RiskLevel.HIGH,
}


def _require_score(value: Any, *, field: str) -> float:
    """Validate that ``value`` is a finite score within ``[0, 100]``."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DomainValidationError(
            "score must be a real number", field=field, offending_type=type(value).__name__
        )
    number = float(value)
    if number != number or number in (float("inf"), float("-inf")):
        raise DomainValidationError("score must be finite", field=field, value=repr(value))
    if not MIN_RISK_SCORE <= number <= MAX_RISK_SCORE:
        raise DomainValidationError(
            "score is outside the 0-100 scale",
            field=field,
            value=number,
            minimum=MIN_RISK_SCORE,
            maximum=MAX_RISK_SCORE,
        )
    return number


def _lower_bound_for(level: RiskLevel) -> float:
    """Return the inclusive lower bound of ``level`` in :data:`RISK_BANDS`."""
    for lower_bound, band in RISK_BANDS:
        if band is level:
            return lower_bound
    raise DomainValidationError(  # pragma: no cover - unreachable while bands cover the enum
        "risk level has no band", field="level", level=level.value
    )


@dataclass(frozen=True, slots=True)
class RiskFactor:
    """One named, individually explainable contribution to a risk score.

    Factors are kept as first-class records rather than being collapsed into a
    single number, because the whole point of the product is that a human can be
    told *why*. The full factor list is written to ``risk_inputs`` in evidence.

    Attributes:
        name: Stable factor identifier, e.g. ``amount_vs_peer_group``.
        score: This factor's contribution on the 0-100 scale.
        rationale: Human-readable explanation, safe to show to an approver.
        detail: Optional structured supporting values.
    """

    name: str
    score: float
    rationale: str
    detail: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, field="name"))
        object.__setattr__(self, "score", _require_score(self.score, field="score"))
        object.__setattr__(self, "rationale", require_non_empty(self.rationale, field="rationale"))
        if isinstance(self.detail, Mapping):
            object.__setattr__(self, "detail", freeze_mapping(self.detail, field="detail"))
        elif not isinstance(self.detail, tuple):
            raise DomainValidationError(
                "detail must be a mapping or a tuple of pairs",
                field="detail",
                offending_type=type(self.detail).__name__,
            )

    @property
    def level(self) -> RiskLevel:
        """Band this individual factor falls into."""
        return RiskLevel.from_score(self.score)

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation stored in ``risk_inputs``."""
        return {
            "name": self.name,
            "score": self.score,
            "level": self.level.value,
            "rationale": self.rationale,
            "detail": {key: value for key, value in self.detail},
        }


@dataclass(frozen=True, slots=True)
class RiskInputs:
    """The complete, replayable input set for a risk evaluation.

    Storing this verbatim in evidence is what makes a decision reproducible: a
    replay (GB-012) re-scores these exact inputs against the pinned model version
    and must obtain the identical score.

    Attributes:
        consequence: Server-derived consequence class of the action.
        exposure: Server-derived exposure of the action.
        factors: Individually explainable contributions.
        evaluated_at: Epoch seconds, supplied by the injected clock.
        context: Additional server-derived, non-caller-asserted signals.
    """

    consequence: ConsequenceClass
    exposure: Exposure
    evaluated_at: float
    factors: Tuple[RiskFactor, ...] = ()
    context: Tuple[Tuple[str, Any], ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.consequence, ConsequenceClass):
            raise DomainValidationError(
                "consequence must be a ConsequenceClass",
                field="consequence",
                offending_type=type(self.consequence).__name__,
            )
        if not isinstance(self.exposure, Exposure):
            raise DomainValidationError(
                "exposure must be an Exposure",
                field="exposure",
                offending_type=type(self.exposure).__name__,
            )
        object.__setattr__(
            self, "evaluated_at", require_timestamp(self.evaluated_at, field="evaluated_at")
        )
        if not isinstance(self.factors, tuple):
            object.__setattr__(self, "factors", tuple(self.factors or ()))
        seen = set()
        for index, factor in enumerate(self.factors):
            if not isinstance(factor, RiskFactor):
                raise DomainValidationError(
                    "factors must contain RiskFactor instances",
                    field=f"factors[{index}]",
                    offending_type=type(factor).__name__,
                )
            if factor.name in seen:
                raise DomainValidationError(
                    "duplicate risk factor name", field="factors", name=factor.name
                )
            seen.add(factor.name)
        if isinstance(self.context, Mapping):
            object.__setattr__(self, "context", freeze_mapping(self.context, field="context"))
        elif not isinstance(self.context, tuple):
            raise DomainValidationError(
                "context must be a mapping or a tuple of pairs",
                field="context",
                offending_type=type(self.context).__name__,
            )

    def factor(self, name: str) -> Optional[RiskFactor]:
        """Return the named factor, or ``None`` if it was not evaluated."""
        for candidate in self.factors:
            if candidate.name == name:
                return candidate
        return None

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical ``risk_inputs`` payload."""
        return {
            "consequence_class": self.consequence.value,
            "exposure": dict(self.exposure.as_evidence()),
            "evaluated_at": self.evaluated_at,
            "factors": [factor.as_evidence() for factor in self.factors],
            "context": {key: value for key, value in self.context},
        }


@dataclass(frozen=True, slots=True)
class RiskScore:
    """The outcome of a risk evaluation, pinned to a model version.

    Attributes:
        value: Final score on the 0-100 scale, after any floor was applied.
        model_version: Identifier of the scoring model that produced it.
        inputs: The exact inputs that were scored.
        raw_value: The score before a consequence floor was applied.
        floor_applied: Whether :meth:`with_consequence_floor` raised the score.
    """

    value: float
    model_version: str
    inputs: RiskInputs
    raw_value: float = field(default=-1.0)
    floor_applied: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _require_score(self.value, field="value"))
        object.__setattr__(
            self, "model_version", require_identifier(self.model_version, field="model_version")
        )
        if not isinstance(self.inputs, RiskInputs):
            raise DomainValidationError(
                "inputs must be a RiskInputs",
                field="inputs",
                offending_type=type(self.inputs).__name__,
            )
        if self.raw_value < 0:
            object.__setattr__(self, "raw_value", self.value)
        else:
            object.__setattr__(self, "raw_value", _require_score(self.raw_value, field="raw_value"))
        if self.value < self.raw_value:
            raise DomainValidationError(
                "a floor may only raise a score, never lower it",
                field="value",
                value=self.value,
                raw_value=self.raw_value,
            )

    @property
    def level(self) -> RiskLevel:
        """Band of the final score, from the single system-wide table."""
        return RiskLevel.from_score(self.value)

    @property
    def raw_level(self) -> RiskLevel:
        """Band of the score before any floor was applied."""
        return RiskLevel.from_score(self.raw_value)

    def with_consequence_floor(self) -> "RiskScore":
        """Return a score raised to the minimum permitted by its consequence class.

        Idempotent: applying it twice yields an equal score. When no floor is
        needed the same instance is returned.
        """
        floor_level = CONSEQUENCE_FLOORS[self.inputs.consequence]
        floor_value = _lower_bound_for(floor_level)
        if self.value >= floor_value:
            return self
        return RiskScore(
            value=floor_value,
            model_version=self.model_version,
            inputs=self.inputs,
            raw_value=self.raw_value,
            floor_applied=True,
        )

    def exceeds(self, threshold: RiskLevel) -> bool:
        """Return whether this score's band is strictly above ``threshold``."""
        if not isinstance(threshold, RiskLevel):
            raise DomainValidationError(
                "threshold must be a RiskLevel",
                field="threshold",
                offending_type=type(threshold).__name__,
            )
        return self.level > threshold

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical risk fields recorded on the evidence row."""
        return {
            "risk_score": self.value,
            "risk_level": self.level.value,
            "risk_raw_score": self.raw_value,
            "risk_raw_level": self.raw_level.value,
            "risk_floor_applied": self.floor_applied,
            "risk_model_ver": self.model_version,
            "risk_inputs": dict(self.inputs.as_evidence()),
        }
