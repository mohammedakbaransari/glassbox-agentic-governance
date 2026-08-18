"""The replay dispatcher (GB-012).

A :class:`Dispatcher` that is structurally incapable of causing an effect.
Replay must never re-invoke the executor -- v1's ``decision_replay.replay_one``
called the live ``pipeline.process()`` directly, so ``POST
/decisions/<id>/replay`` could re-execute a wire transfer.

Two independent guarantees back "never re-executes" here, deliberately not
just one:

* :meth:`DecisionService.replay` never calls ``dispatcher.dispatch`` at all --
  it records :attr:`~glassbox.domain.decision.ExecutionStatus.REPLAYED` for any
  effect-worthy decision without going through dispatch.
* even if a future bug reintroduced that call, :class:`NullDispatcher` raises
  immediately rather than performing anything or returning a plausible-looking
  outcome. It exists to fail loudly, not to be a working no-op dispatcher.
"""

from __future__ import annotations

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.action import ProposedAction
from glassbox.domain.decision import ExecutionOutcome
from glassbox.domain.evidence import EvidenceReceipt

__all__ = ["NullDispatcher", "build_null_dispatcher"]


class NullDispatcher:
    """Conforms to :class:`~glassbox.ports.dispatcher.Dispatcher`; never dispatches."""

    def dispatch(
        self,
        action: ProposedAction,
        receipt: EvidenceReceipt,
        *,
        timeout_s: float,
        now: float,
    ) -> ExecutionOutcome:
        """Never returns. Reaching this call is itself the defect being guarded against."""
        raise AssertionError(
            "NullDispatcher must never be invoked -- replay must not reach dispatch "
            f"(action={action.action!r}, decision_id={receipt.decision_id!r})"
        )


def build_null_dispatcher(config: GlassBoxConfig) -> NullDispatcher:
    """Factory used by a replay-only adapter set."""
    del config  # unused: NullDispatcher takes no configuration
    return NullDispatcher()
