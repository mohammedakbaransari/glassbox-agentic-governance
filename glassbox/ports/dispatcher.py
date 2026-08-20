"""Dispatcher port (GB-002, WS-2).

The dispatcher is the only component permitted to cause a real-world effect, and
it may only do so when handed an :class:`~glassbox.domain.evidence.EvidenceReceipt`.
Making the receipt a required argument is how invariant **I1** is enforced by the
type system rather than by convention: there is no signature that dispatches
without one.

In v1, ``_stage_disposition`` invoked the executor at stage 11 and ``_finalize``
wrote the audit record at stage 12, where it could -- and did -- fail silently.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.action import ProposedAction
from glassbox.domain.decision import ExecutionOutcome
from glassbox.domain.evidence import EvidenceReceipt

__all__ = ["Dispatcher"]


@runtime_checkable
class Dispatcher(Protocol):
    """Performs the governed side effect, at most once."""

    def dispatch(
        self,
        action: ProposedAction,
        receipt: EvidenceReceipt,
        *,
        timeout_s: float,
        now: float,
    ) -> ExecutionOutcome:
        """Execute ``action`` and return its terminal outcome.

        Conforming adapters must:

        * **require the receipt.** Validate that it covers this action's
          decision, and raise
          :class:`~glassbox.domain.errors.DispatchRefusedError` if it does not.
        * be **at most once** with respect to ``action.idempotency_key``. A
          repeated key returns the recorded outcome without re-executing.
        * **bound concurrency.** No unbounded submission into a shared pool: v1's
          batch endpoint pushed up to 500 tasks into the pipeline's own executor,
          a trivial self-DoS.
        * **re-scan textual result content for prompt injection before
          reporting success.** A tool's result is exactly the content an
          indirect-injection attack uses to carry instructions to the agent's
          next reasoning step -- the input-side scan
          (:func:`glassbox.domain.prompt_injection.scan`) never sees it,
          because it does not exist until the effect has already run. A
          flagged result must be reported as
          :attr:`~glassbox.domain.decision.ExecutionStatus.FAILED` (with
          :class:`~glassbox.domain.errors.ToolOutputQuarantinedError` as the
          error class), never as a successful outcome the caller could feed
          forward as trusted content. The flagged content itself must never
          be evidenced -- only its digest, exactly as for any other result.
        * report a timeout as
          :attr:`~glassbox.domain.decision.ExecutionStatus.INDETERMINATE`, never
          as ``FAILED``. After a timeout the effect may or may not have occurred,
          and recording a clean failure would be a false statement in an audit
          record.

        Args:
            action: The authorised action.
            receipt: Proof that the intent record is durable.
            timeout_s: Wall-clock budget for the effect.
            now: Current time in POSIX epoch seconds.

        Returns:
            The terminal outcome, to be written via
            :meth:`~glassbox.ports.evidence.EvidenceStore.append_outcome`.

        Raises:
            glassbox.domain.errors.DispatchRefusedError: If the receipt is
                missing, malformed, or does not cover this action.
            glassbox.domain.errors.DispatchTimeoutError: If the adapter cannot
                determine an outcome within ``timeout_s``.
            glassbox.domain.errors.ToolOutputQuarantinedError: If the result
                matched a prompt-injection pattern. Conforming adapters may
                also report this as a ``FAILED`` outcome directly rather than
                raising; either way the caller must never receive
                ``EXECUTED`` for a flagged result.
            glassbox.domain.errors.DispatchError: For non-recoverable dispatch
                failures.
        """
        ...
