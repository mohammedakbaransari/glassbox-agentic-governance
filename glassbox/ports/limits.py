"""Limit store port (GB-002, WS-5)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from glassbox.domain.limits import LimitKey, LimitVerdict, Window

__all__ = ["LimitStore"]


@runtime_checkable
class LimitStore(Protocol):
    """Atomic, externally-held velocity and volume counters.

    Conforming adapters must:

    * perform check-and-consume **atomically** -- a read followed by a separate
      write admits more than the limit under concurrency;
    * hold **all** state externally, including cooldown. v1 kept the tripped flag
      in a process-local dict while counting in Redis, so the effective cooldown
      collapsed to the window length;
    * use a **collision-free member** per admission
      (:meth:`~glassbox.domain.limits.LimitKey.member_for`). v1 used the
      timestamp as both score and member, so same-tick decisions collapsed and
      the window undercounted;
    * **raise, never fail open.** There is no verdict for "the store is down".
    """

    def try_consume(
        self, key: LimitKey, *, cost: float, decision_id: str, now: float
    ) -> LimitVerdict:
        """Atomically consume ``cost`` from the counter if budget remains.

        Args:
            key: The counter to consult.
            cost: Units to consume. Usually ``1.0``; monetary limits use amount.
            decision_id: Correlation id, used to build a unique window member.
            now: Current time in POSIX epoch seconds.

        Returns:
            A verdict stating whether the decision was admitted.

        Raises:
            glassbox.domain.errors.LimitStoreUnavailable: If the store cannot
                answer atomically. Callers **must** deny for any action whose
                consequence class is not ``ADVISORY``.
        """
        ...

    def cumulative(self, key: LimitKey, window: Window, *, now: float) -> float:
        """Return consumption within ``window`` without consuming budget.

        Read-only; used for reporting, aggregate exposure and approval context.

        Raises:
            glassbox.domain.errors.LimitStoreUnavailable: If the store is
                unreachable.
        """
        ...

    def release(self, key: LimitKey, *, decision_id: str) -> None:
        """Return budget consumed by a decision that never took effect.

        Called when dispatch is abandoned after a successful consume, so a denied
        or failed action does not permanently occupy an agent's budget.
        Implementations must be idempotent with respect to ``decision_id``.

        Raises:
            glassbox.domain.errors.LimitStoreUnavailable: If the store is
                unreachable. Release failure must be logged and retried; it never
                blocks a denial.
        """
        ...
