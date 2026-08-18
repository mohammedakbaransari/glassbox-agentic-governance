"""Risk engine port (GB-002, WS-4)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.risk import RiskInputs, RiskScore

__all__ = ["RiskEngine"]


@runtime_checkable
class RiskEngine(Protocol):
    """Scores a decision's risk, deterministically and reproducibly.

    Conforming implementations must:

    * read no clock -- ``inputs.evaluated_at`` is the only notion of "now";
    * be **versioned** -- the returned score carries the model version so a
      replay can pin it;
    * be **monotonic** in exposure -- more at stake never lowers the score;
    * apply the consequence floor via
      :meth:`~glassbox.domain.risk.RiskScore.with_consequence_floor`, so an
      irreversible action can never be scored below its floor.

    v1 violated all four: a weighted mean that saturated, a cap of 27.5 on the
    financial contribution, an hour-of-day input, and two inconsistent banding
    tables.
    """

    @property
    def model_version(self) -> str:
        """Identifier of the scoring model, recorded on every evidence row."""
        ...

    def score(self, inputs: RiskInputs) -> RiskScore:
        """Score ``inputs`` and return a version-pinned result.

        Args:
            inputs: The complete, replayable input set.

        Returns:
            A score whose ``model_version`` equals :attr:`model_version` and
            whose consequence floor has already been applied.

        Raises:
            glassbox.domain.errors.RiskModelUnavailableError: If the model cannot
                be resolved. Callers must fail closed.
        """
        ...

    def score_with_model(self, inputs: RiskInputs, model_version: str) -> RiskScore:
        """Re-score ``inputs`` against a specific historical model version.

        Required by replay (GB-012): re-scoring a historical decision against
        today's model would answer a different question from the one the auditor
        asked.

        Raises:
            glassbox.domain.errors.RiskModelUnavailableError: If that version is
                not available.
        """
        ...
