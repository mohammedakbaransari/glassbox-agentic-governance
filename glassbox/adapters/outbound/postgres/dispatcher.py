"""Durable, cross-replica dispatcher (GB-033).

:class:`~glassbox.adapters.outbound.memory.dispatch.InMemoryDispatcher` gets the
*control* behaviour right -- at-most-once, bounded concurrency, timeout as
``INDETERMINATE`` never ``FAILED`` -- but its idempotency ledger is a process-local
``dict``. That is correct for exactly one process and wrong the moment a second
replica exists: two replicas each holding an empty ledger both admit the same
``idempotency_key``, and v1's 500-task batch submission plus a same-process-only
notion of "already running" is precisely the shape of that gap.

This adapter claims the idempotency key in Postgres with a single
``INSERT ... ON CONFLICT DO NOTHING RETURNING`` before any effect handler runs.
Exactly one replica ever observes a successful claim for a given key; every other
replica -- including this same process retried after its own crash, and a
genuinely different replica -- observes the existing row and never re-executes.
A replica that loses the race waits (briefly, bounded by ``timeout_s``) for the
claimant to finish and returns its recorded outcome; if the claimant has not
finished by the deadline, the honest answer is ``INDETERMINATE``, not a second
attempt.
"""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Dict, Optional

from glassbox.adapters.outbound.postgres.driver import ConnectionProvider, DriverUnavailableError
from glassbox.domain.action import ProposedAction
from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
from glassbox.domain.errors import DispatchError, DispatchRefusedError
from glassbox.domain.evidence import EvidenceReceipt
from glassbox.domain.serialization import canonical_bytes
from glassbox.ports.dispatcher import Dispatcher

__all__ = ["PostgresDispatcher", "EffectHandler", "ReceiptCheck"]

#: A handler performs the real-world effect and returns a serialisable result.
EffectHandler = Callable[[ProposedAction], object]

#: Verifies that a receipt was genuinely issued by the evidence store.
ReceiptCheck = Callable[[EvidenceReceipt], bool]

_CLAIM = """
INSERT INTO dispatch_ledger (idempotency_key, decision_id, action, status)
VALUES (%s, %s, %s, 'claimed')
ON CONFLICT (idempotency_key) DO NOTHING
RETURNING idempotency_key
"""

_SELECT_LEDGER = """
SELECT status, completed_at, result_digest, error_class
  FROM dispatch_ledger
 WHERE idempotency_key = %s
"""

_COMPLETE = """
UPDATE dispatch_ledger
   SET status = %s, completed_at = %s, result_digest = %s, error_class = %s
 WHERE idempotency_key = %s
"""

_TERMINAL_STATUSES = frozenset({"executed", "failed", "indeterminate"})


class PostgresDispatcher:
    """Executes registered effect handlers, at most once, across N replicas.

    Args:
        provider: Supplies cursors already inside a transaction, over the
            ``dispatch_ledger`` table (see
            :mod:`glassbox.adapters.outbound.postgres.schema`).
        max_in_flight: Worker pool size and **per-replica** admission bound. This
            is not the cross-replica idempotency guarantee -- that comes from the
            ledger claim -- it is what stops one replica's own unbounded batch
            submission, exactly the defect measured in v1's 500-task submission
            into the pipeline's shared executor.
        receipt_check: Called with every receipt. Returning ``False`` refuses the
            dispatch, so invariant I1 is checked against real evidence-store
            state rather than against argument shape.
        require_receipt: Safety switch mirroring
            :attr:`~glassbox.app.config.DispatchConfig.require_evidence_receipt`.
        poll_interval_s: How often a losing replica re-checks the ledger while
            waiting for the claimant to finish.
    """

    __slots__ = (
        "_provider",
        "_pool",
        "_lock",
        "_handlers",
        "_receipt_check",
        "_require_receipt",
        "_max_in_flight",
        "_in_flight",
        "_poll_interval_s",
    )

    def __init__(
        self,
        provider: ConnectionProvider,
        *,
        max_in_flight: int = 8,
        receipt_check: Optional[ReceiptCheck] = None,
        require_receipt: bool = True,
        poll_interval_s: float = 0.05,
    ) -> None:
        if provider is None:
            raise DispatchError("a Postgres dispatcher requires a connection provider")
        self._provider = provider
        self._pool = ThreadPoolExecutor(
            max_workers=max_in_flight, thread_name_prefix="glassbox-dispatch-pg"
        )
        self._lock = threading.RLock()
        self._handlers: Dict[str, EffectHandler] = {}
        self._receipt_check = receipt_check
        self._require_receipt = require_receipt
        self._max_in_flight = max_in_flight
        self._in_flight = 0
        self._poll_interval_s = poll_interval_s

    def dispatch(
        self,
        action: ProposedAction,
        receipt: EvidenceReceipt,
        *,
        timeout_s: float,
        now: float,
    ) -> ExecutionOutcome:
        """Execute ``action`` and return its terminal outcome.

        Raises:
            DispatchRefusedError: If the receipt is missing, malformed, not
                issued by the evidence store, if no handler is registered for
                the action, or if this replica's in-flight bound is exceeded.
        """
        self._require_valid_receipt(action, receipt)
        handler = self._handlers.get(action.action)
        if handler is None:
            raise DispatchRefusedError(
                "no handler is registered for this action",
                action=action.action,
                decision_id=receipt.decision_id,
            )

        with self._lock:
            if self._in_flight >= self._max_in_flight:
                raise DispatchRefusedError(
                    "dispatcher is at its in-flight bound",
                    action=action.action,
                    in_flight=self._in_flight,
                    max_in_flight=self._max_in_flight,
                )
            self._in_flight += 1

        try:
            claimed = self._claim(action, receipt)
            if not claimed:
                # Another replica -- or this key, retried -- got there first.
                # Never run the handler; wait for its terminal outcome instead.
                return self._await_existing(action.idempotency_key, timeout_s=timeout_s, now=now)

            future: Future = self._pool.submit(handler, action)
            try:
                result = future.result(timeout=timeout_s)
            except FuturesTimeout:
                outcome = ExecutionOutcome(
                    status=ExecutionStatus.INDETERMINATE,
                    completed_at=now + timeout_s,
                    error_class="DispatchTimeout",
                )
            except Exception as exc:  # noqa: BLE001 - recorded, never swallowed
                outcome = ExecutionOutcome(
                    status=ExecutionStatus.FAILED,
                    completed_at=now,
                    error_class=type(exc).__name__,
                )
            else:
                outcome = ExecutionOutcome(
                    status=ExecutionStatus.EXECUTED,
                    completed_at=now,
                    result_digest=_digest(result),
                )
            self._complete(action.idempotency_key, outcome)
            return outcome
        finally:
            with self._lock:
                self._in_flight -= 1

    # ----------------------------------------------------------------- #
    # Wiring
    # ----------------------------------------------------------------- #

    def register(self, action: str, handler: EffectHandler) -> None:
        """Register the handler that performs one action's effect."""
        with self._lock:
            self._handlers[action] = handler

    def set_receipt_check(self, check: ReceiptCheck) -> None:
        """Bind the receipt validator, normally the evidence store's own check."""
        self._receipt_check = check

    def shutdown(self) -> None:
        """Stop the worker pool."""
        self._pool.shutdown(wait=True)

    # ----------------------------------------------------------------- #
    # Ledger
    # ----------------------------------------------------------------- #

    def _claim(self, action: ProposedAction, receipt: EvidenceReceipt) -> bool:
        """Atomically claim ``idempotency_key``. Returns ``False`` if already claimed."""
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_CLAIM, (action.idempotency_key, receipt.decision_id, action.action))
                row = cursor.fetchone()
        except DriverUnavailableError:
            raise
        except Exception as exc:
            raise DriverUnavailableError(
                "could not claim the dispatch ledger", cause=type(exc).__name__, detail=str(exc)
            ) from exc
        return row is not None

    def _complete(self, idempotency_key: str, outcome: ExecutionOutcome) -> None:
        """Record the terminal outcome so every other replica stops waiting."""
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(
                    _COMPLETE,
                    (
                        outcome.status.value,
                        outcome.completed_at,
                        outcome.result_digest,
                        outcome.error_class,
                        idempotency_key,
                    ),
                )
        except DriverUnavailableError:
            # The effect already happened (or definitively didn't); a failure to
            # record it here must not be reported as the effect's own outcome,
            # and must not raise past a caller who already has a real outcome.
            pass

    def _await_existing(
        self, idempotency_key: str, *, timeout_s: float, now: float
    ) -> ExecutionOutcome:
        """Wait for the replica holding the claim to reach a terminal state."""
        deadline = time.monotonic() + timeout_s
        while True:
            with self._provider.transaction() as cursor:
                cursor.execute(_SELECT_LEDGER, (idempotency_key,))
                row = cursor.fetchone()
            if row is not None:
                status, completed_at, result_digest, error_class = row
                if status in _TERMINAL_STATUSES:
                    return ExecutionOutcome(
                        status=ExecutionStatus(status),
                        completed_at=completed_at if completed_at is not None else now,
                        result_digest=result_digest,
                        error_class=error_class,
                    )
            if time.monotonic() >= deadline:
                # Genuinely unknown: the claimant has not finished, and this
                # replica must not run the handler a second time to find out.
                return ExecutionOutcome(
                    status=ExecutionStatus.INDETERMINATE,
                    completed_at=now + timeout_s,
                    error_class="DispatchClaimPending",
                )
            time.sleep(self._poll_interval_s)

    def _require_valid_receipt(self, action: ProposedAction, receipt: EvidenceReceipt) -> None:
        """Enforce invariant I1 before any effect can occur."""
        if not self._require_receipt:
            return
        if not isinstance(receipt, EvidenceReceipt):
            raise DispatchRefusedError(
                "dispatch requires a durable evidence receipt",
                action=action.action,
                offending_type=type(receipt).__name__,
            )
        if self._receipt_check is not None and not self._receipt_check(receipt):
            raise DispatchRefusedError(
                "the evidence store did not issue this receipt",
                action=action.action,
                decision_id=receipt.decision_id,
            )


def _digest(result: object) -> str:
    """Return a SHA-256 digest of the result, never the result itself.

    Effect payloads routinely contain the very data the governed action touched;
    storing a digest keeps the ledger useful without turning it into a second
    copy of the sensitive data.
    """
    try:
        material = canonical_bytes({"result": result})
    except Exception:  # noqa: BLE001 - fall back to a stable repr for opaque results
        material = repr(result).encode("utf-8")
    return hashlib.sha256(material).hexdigest()
