"""Tests for dispatcher admission control (GB-033).

v1's batch endpoint submitted up to 500 tasks into the pipeline's own shared
executor with no bound at all -- a trivial self-DoS, and one that could starve
every other stage sharing that pool. The dispatcher's ``max_in_flight`` is the
fix: once the bound is reached, a caller is refused immediately rather than
queued, so no batch -- of any size -- can starve the service.
"""

from __future__ import annotations

import threading
from typing import Any, List

import pytest

from glassbox.adapters.outbound.memory.evidence import InMemoryEvidenceStore
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.postgres.dispatcher import PostgresDispatcher
from glassbox.domain.decision import ExecutionStatus
from glassbox.domain.errors import DispatchRefusedError
from tests.test_dispatcher_idempotency import FakeLedgerProvider
from tests.test_domain import NOW, make_action, make_intent


def _store() -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(signer=LocalMacSigner(key_id="test.key", key=b"\x11" * 32))


class TestBatchAdmissionControl:
    def test_the_bound_is_enforced_regardless_of_batch_size(self) -> None:
        """500 distinct keys against a bound of 4 must refuse the excess, not
        queue it -- a queue of any size is still the same self-DoS surface."""
        store = _store()
        started = threading.Event()
        release = threading.Event()
        started_count: List[int] = []
        lock = threading.Lock()

        def slow(action: Any) -> Any:
            with lock:
                started_count.append(1)
                if len(started_count) == 4:
                    started.set()
            release.wait(timeout=5.0)
            return {"ok": True}

        dispatcher = PostgresDispatcher(
            FakeLedgerProvider(), max_in_flight=4, receipt_check=store.has_receipt
        )
        dispatcher.register("payments.wire_transfer", slow)

        receipts = [
            store.append_intent(make_intent(decision_id=f"decision-{i:04d}")) for i in range(500)
        ]
        actions = [make_action(idempotency_key=f"idem-{i:04d}") for i in range(500)]

        admitted: List[int] = []
        refused: List[int] = []
        result_lock = threading.Lock()

        def attempt(index: int) -> None:
            try:
                dispatcher.dispatch(actions[index], receipts[index], timeout_s=5.0, now=NOW)
                with result_lock:
                    admitted.append(index)
            except DispatchRefusedError:
                with result_lock:
                    refused.append(index)

        # Fill the bound first, then fire the rest concurrently -- every one of
        # the rest must be refused immediately, never queued behind the first 4.
        fill_threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4)]
        for t in fill_threads:
            t.start()
        assert started.wait(timeout=5.0), "the pool never reached its bound"

        overflow_threads = [threading.Thread(target=attempt, args=(i,)) for i in range(4, 500)]
        for t in overflow_threads:
            t.start()
        for t in overflow_threads:
            t.join(timeout=5.0)

        assert (
            len(refused) == 496
        ), f"expected all 496 overflow attempts refused, got {len(refused)}"
        assert len(admitted) == 0, "no overflow attempt should have been admitted yet"

        release.set()
        for t in fill_threads:
            t.join(timeout=5.0)
        assert len(admitted) == 4
        dispatcher.shutdown()

    def test_a_refused_dispatch_leaves_no_stuck_ledger_claim(self) -> None:
        """A caller refused for admission-control reasons must be free to retry
        the same idempotency key without it being permanently 'claimed'."""
        store = _store()
        dispatcher = PostgresDispatcher(
            FakeLedgerProvider(), max_in_flight=1, receipt_check=store.has_receipt
        )
        release = threading.Event()
        dispatcher.register(
            "payments.wire_transfer", lambda action: release.wait(timeout=5.0) or {"ok": True}
        )

        blocking_receipt = store.append_intent(make_intent(decision_id="decision-block"))
        blocking_action = make_action(idempotency_key="idem-block")
        t = threading.Thread(
            target=dispatcher.dispatch,
            args=(blocking_action, blocking_receipt),
            kwargs={"timeout_s": 5.0, "now": NOW},
        )
        t.start()

        other_receipt = store.append_intent(make_intent(decision_id="decision-other"))
        other_action = make_action(idempotency_key="idem-other")
        # Give the first call a moment to occupy the single in-flight slot.
        import time

        deadline = time.monotonic() + 2.0
        refused = False
        while time.monotonic() < deadline:
            try:
                dispatcher.dispatch(other_action, other_receipt, timeout_s=0.01, now=NOW)
            except DispatchRefusedError:
                refused = True
                break
        assert refused, "the second key should have been refused for admission, not executed"

        release.set()
        t.join(timeout=5.0)

        # The refused key's own claim was never taken, so it must run cleanly now.
        outcome = dispatcher.dispatch(other_action, other_receipt, timeout_s=5.0, now=NOW)
        assert outcome.status is ExecutionStatus.EXECUTED
        dispatcher.shutdown()
