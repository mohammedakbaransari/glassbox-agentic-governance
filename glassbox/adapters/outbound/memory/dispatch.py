"""In-memory dispatcher (GB-003, reference implementation for GB-033).

**Development only** in the sense that the effects are whatever handlers a test
registers. The *control* behaviour, however, is exactly what the production
dispatcher must implement, and every rule below is a measured v1 defect.

* **A receipt is required, and it is checked against the store.** Passing the
  receipt is not enough; the dispatcher asks the evidence store whether it really
  issued it. v1 invoked the executor at stage 11 and wrote the audit record at
  stage 12, where it could and did fail silently.
* **At most once per idempotency key.** A repeated key returns the recorded
  outcome without re-executing.
* **Bounded concurrency.** A fixed worker pool with an admission bound. v1's
  batch endpoint submitted up to 500 tasks into the pipeline's own executor -- a
  trivial self-DoS that could also deadlock the stages sharing it.
* **A timeout is INDETERMINATE, never FAILED.** After a timeout the effect may or
  may not have occurred; recording a clean failure would put a false statement
  into an audit record.
"""

from __future__ import annotations

import hashlib
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from typing import Callable, Dict, Optional

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.action import ProposedAction
from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
from glassbox.domain.errors import DispatchRefusedError, ToolOutputQuarantinedError
from glassbox.domain.evidence import EvidenceReceipt
from glassbox.domain.prompt_injection import scan as scan_for_prompt_injection
from glassbox.domain.serialization import canonical_bytes
from glassbox.ports.dispatcher import Dispatcher

__all__ = ["InMemoryDispatcher", "EffectHandler", "build_dispatcher"]

#: A handler performs the real-world effect and returns a serialisable result.
EffectHandler = Callable[[ProposedAction], object]

#: Verifies that a receipt was genuinely issued by the evidence store.
ReceiptCheck = Callable[[EvidenceReceipt], bool]


class InMemoryDispatcher:
    """Executes registered effect handlers under governance constraints.

    Args:
        max_in_flight: Worker pool size and admission bound.
        receipt_check: Called with every receipt. Returning ``False`` refuses the
            dispatch. Wiring this to the evidence store is what makes invariant
            I1 checkable against real state rather than against argument shape.
        require_receipt: Safety switch mirroring
            :attr:`~glassbox.app.config.DispatchConfig.require_evidence_receipt`.
    """

    __slots__ = (
        "_pool",
        "_lock",
        "_handlers",
        "_outcomes",
        "_receipt_check",
        "_require_receipt",
        "_max_in_flight",
        "_in_flight",
    )

    def __init__(
        self,
        *,
        max_in_flight: int = 8,
        receipt_check: Optional[ReceiptCheck] = None,
        require_receipt: bool = True,
    ) -> None:
        self._pool = ThreadPoolExecutor(
            max_workers=max_in_flight, thread_name_prefix="glassbox-dispatch"
        )
        self._lock = threading.RLock()
        self._handlers: Dict[str, EffectHandler] = {}
        self._outcomes: Dict[str, ExecutionOutcome] = {}
        self._receipt_check = receipt_check
        self._require_receipt = require_receipt
        self._max_in_flight = max_in_flight
        self._in_flight = 0

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
                issued by the evidence store, if no handler is registered for the
                action, or if the in-flight bound is exceeded.
        """
        self._require_valid_receipt(action, receipt)

        with self._lock:
            recorded = self._outcomes.get(action.idempotency_key)
            if recorded is not None:
                return recorded
            handler = self._handlers.get(action.action)
            if handler is None:
                raise DispatchRefusedError(
                    "no handler is registered for this action",
                    action=action.action,
                    decision_id=receipt.decision_id,
                )
            if self._in_flight >= self._max_in_flight:
                raise DispatchRefusedError(
                    "dispatcher is at its in-flight bound",
                    action=action.action,
                    in_flight=self._in_flight,
                    max_in_flight=self._max_in_flight,
                )
            self._in_flight += 1
            # Reserve the key before releasing the lock so a concurrent duplicate
            # cannot start a second execution of the same effect.
            self._outcomes[action.idempotency_key] = _PENDING

        future: Future = self._pool.submit(handler, action)
        try:
            result = future.result(timeout=timeout_s)
            injection = scan_for_prompt_injection("tool_output", result)
            if injection.flagged:
                # Raised, not returned: the effect already ran (whether it was
                # authorised to was decided before dispatch), but the result
                # must never be reported as EXECUTED -- that would tell the
                # caller it is safe to feed forward as trusted content.
                raise ToolOutputQuarantinedError(
                    "tool output matched a prompt-injection pattern and was quarantined",
                    action=action.action,
                    matched_patterns=", ".join(injection.matched_patterns),
                )
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
        finally:
            with self._lock:
                self._in_flight -= 1

        with self._lock:
            self._outcomes[action.idempotency_key] = outcome
        return outcome

    # ----------------------------------------------------------------- #
    # Wiring and inspection
    # ----------------------------------------------------------------- #

    def register(self, action: str, handler: EffectHandler) -> None:
        """Register the handler that performs one action's effect."""
        with self._lock:
            self._handlers[action] = handler

    def set_receipt_check(self, check: ReceiptCheck) -> None:
        """Bind the receipt validator, normally the evidence store's own check."""
        self._receipt_check = check

    def outcome_for(self, idempotency_key: str) -> Optional[ExecutionOutcome]:
        """Return the recorded outcome for a key, if any."""
        with self._lock:
            recorded = self._outcomes.get(idempotency_key)
        return None if recorded is _PENDING else recorded

    def shutdown(self) -> None:
        """Stop the worker pool."""
        self._pool.shutdown(wait=True)

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


#: Sentinel marking a key whose execution has started but not finished.
_PENDING = ExecutionOutcome(status=ExecutionStatus.PENDING_APPROVAL, completed_at=0.0)


def _digest(result: object) -> str:
    """Return a SHA-256 digest of the result, never the result itself.

    Effect payloads routinely contain the very data the governed action touched;
    storing a digest keeps the evidence record useful without turning it into a
    second copy of the sensitive data.
    """
    try:
        material = canonical_bytes({"result": result})
    except Exception:  # noqa: BLE001 - fall back to a stable repr for opaque results
        material = repr(result).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def build_dispatcher(config: GlassBoxConfig) -> Dispatcher:
    """Factory used by the adapter set."""
    return InMemoryDispatcher(
        max_in_flight=config.dispatch.max_in_flight,
        require_receipt=config.dispatch.require_evidence_receipt,
    )
