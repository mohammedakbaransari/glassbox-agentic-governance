"""Tests for the durable, cross-replica dispatcher (GB-033).

:class:`~glassbox.adapters.outbound.memory.dispatch.InMemoryDispatcher` proves
the *control* behaviour with an in-process ledger. These tests prove the same
behaviour holds when the ledger is shared -- via a fake standing in for
Postgres -- across what are modelled here as two independent replica
instances, which is the scenario a single process can never exercise.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from glassbox.adapters.outbound.memory.evidence import InMemoryEvidenceStore
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.postgres.dispatcher import PostgresDispatcher
from glassbox.adapters.outbound.postgres.driver import DriverUnavailableError
from glassbox.domain.decision import ExecutionStatus
from glassbox.domain.errors import DispatchRefusedError
from glassbox.domain.evidence import EvidenceReceipt
from glassbox.ports.dispatcher import Dispatcher
from tests.test_domain import NOW, make_action, make_intent

# --------------------------------------------------------------------------- #
# A fake ledger standing in for the `dispatch_ledger` Postgres table
# --------------------------------------------------------------------------- #


class FakeLedger:
    """Shared state behind :class:`FakeLedgerProvider`.

    One lock around the whole read-check-write path is what makes
    ``INSERT ... ON CONFLICT DO NOTHING`` atomic in Postgres; the same lock here
    gives the fake the same guarantee, which is what this suite actually tests.
    """

    def __init__(self) -> None:
        self.rows: Dict[str, Dict[str, Any]] = {}
        self.lock = threading.RLock()
        self.available = True


class FakeLedgerCursor:
    def __init__(self, ledger: FakeLedger) -> None:
        self._ledger = ledger
        self._rows: List[Tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        normalised = " ".join(sql.split())
        if "INSERT INTO dispatch_ledger" in normalised:
            key, decision_id, action = params
            with self._ledger.lock:
                if key in self._ledger.rows:
                    self._rows = []
                else:
                    self._ledger.rows[key] = {
                        "status": "claimed",
                        "decision_id": decision_id,
                        "action": action,
                        "completed_at": None,
                        "result_digest": None,
                        "error_class": None,
                    }
                    self._rows = [(key,)]
        elif "SELECT status, completed_at" in normalised:
            (key,) = params
            with self._ledger.lock:
                row = self._ledger.rows.get(key)
            self._rows = (
                []
                if row is None
                else [
                    (row["status"], row["completed_at"], row["result_digest"], row["error_class"])
                ]
            )
        elif "UPDATE dispatch_ledger" in normalised:
            status, completed_at, result_digest, error_class, key = params
            with self._ledger.lock:
                if key in self._ledger.rows:
                    self._ledger.rows[key].update(
                        status=status,
                        completed_at=completed_at,
                        result_digest=result_digest,
                        error_class=error_class,
                    )
            self._rows = []
        else:
            raise AssertionError(
                f"FakeLedgerCursor does not implement this statement: {normalised}"
            )

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Tuple[Any, ...]]:
        return list(self._rows)


class FakeLedgerProvider:
    """A ``ConnectionProvider`` over :class:`FakeLedger`, sharable across
    multiple :class:`PostgresDispatcher` instances to model separate replicas."""

    def __init__(self, ledger: Optional[FakeLedger] = None) -> None:
        self.ledger = ledger or FakeLedger()

    @contextmanager
    def transaction(self) -> Iterator[FakeLedgerCursor]:
        if not self.ledger.available:
            raise DriverUnavailableError("simulated outage", adapter="FakeLedgerProvider")
        yield FakeLedgerCursor(self.ledger)

    def close(self) -> None:
        return None


def evidence_store() -> InMemoryEvidenceStore:
    return InMemoryEvidenceStore(signer=LocalMacSigner(key_id="test.key", key=b"\x11" * 32))


def dispatcher(provider: FakeLedgerProvider, store: InMemoryEvidenceStore, **kwargs: Any):
    return PostgresDispatcher(provider, receipt_check=store.has_receipt, **kwargs)


class TestPostgresDispatcher:
    """No effect without a receipt the evidence store actually issued."""

    def test_it_conforms_to_the_dispatcher_port(self) -> None:
        assert isinstance(dispatcher(FakeLedgerProvider(), evidence_store()), Dispatcher)

    def test_a_dispatch_backed_by_evidence_succeeds(self) -> None:
        store = evidence_store()
        d = dispatcher(FakeLedgerProvider(), store)
        d.register("payments.wire_transfer", lambda action: {"status": "sent"})
        receipt = store.append_intent(make_intent())
        outcome = d.dispatch(make_action(), receipt, timeout_s=5.0, now=NOW)
        assert outcome.status is ExecutionStatus.EXECUTED
        assert outcome.result_digest is not None

    def test_a_forged_receipt_is_refused(self) -> None:
        store = evidence_store()
        d = dispatcher(FakeLedgerProvider(), store)
        d.register("payments.wire_transfer", lambda action: {"status": "sent"})
        forged = EvidenceReceipt(
            decision_id="decision-0001",
            segment_id="seg-2026-08",
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="test.key",
            persisted_at=NOW,
        )
        with pytest.raises(DispatchRefusedError):
            d.dispatch(make_action(), forged, timeout_s=5.0, now=NOW)

    def test_an_unregistered_action_is_refused(self) -> None:
        store = evidence_store()
        d = dispatcher(FakeLedgerProvider(), store)
        receipt = store.append_intent(make_intent())
        with pytest.raises(DispatchRefusedError):
            d.dispatch(make_action(action="payments.unknown"), receipt, timeout_s=5.0, now=NOW)

    def test_a_failing_handler_records_its_error_class(self) -> None:
        store = evidence_store()
        d = dispatcher(FakeLedgerProvider(), store)

        def explode(action: Any) -> Any:
            raise KeyError("downstream rejected the transfer")

        d.register("payments.wire_transfer", explode)
        receipt = store.append_intent(make_intent())
        outcome = d.dispatch(make_action(), receipt, timeout_s=5.0, now=NOW)
        assert outcome.status is ExecutionStatus.FAILED
        assert outcome.error_class == "KeyError"

    def test_a_timeout_is_indeterminate_not_failed(self) -> None:
        store = evidence_store()
        d = dispatcher(FakeLedgerProvider(), store, max_in_flight=2)
        release = threading.Event()

        def slow(action: Any) -> Any:
            release.wait(timeout=5.0)
            return {"ok": True}

        d.register("payments.wire_transfer", slow)
        receipt = store.append_intent(make_intent())
        try:
            outcome = d.dispatch(make_action(), receipt, timeout_s=0.05, now=NOW)
            assert outcome.status is ExecutionStatus.INDETERMINATE
            assert outcome.error_class == "DispatchTimeout"
        finally:
            release.set()
            d.shutdown()


class TestCrossReplicaIdempotency:
    """The scenario a single process cannot exercise: two dispatcher instances
    sharing one ledger, modelling two replicas of the same service."""

    def test_two_replicas_never_both_execute_the_same_key(self) -> None:
        """Regression target: a process-local ledger (as in v1's batch endpoint
        and InMemoryDispatcher alone) would let each replica execute once."""
        store = evidence_store()
        ledger = FakeLedger()
        replica_a = dispatcher(FakeLedgerProvider(ledger), store)
        replica_b = dispatcher(FakeLedgerProvider(ledger), store)
        executions: List[int] = []
        lock = threading.Lock()

        def handler(action: Any) -> Any:
            with lock:
                executions.append(1)
            return {"ok": True}

        replica_a.register("payments.wire_transfer", handler)
        replica_b.register("payments.wire_transfer", handler)
        receipt = store.append_intent(make_intent())
        action = make_action()

        results: List[Any] = [None, None]

        def run(replica: PostgresDispatcher, index: int) -> None:
            results[index] = replica.dispatch(action, receipt, timeout_s=5.0, now=NOW)

        t1 = threading.Thread(target=run, args=(replica_a, 0))
        t2 = threading.Thread(target=run, args=(replica_b, 1))
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        assert len(executions) == 1, "both replicas executed the same idempotency key"
        assert results[0].status is ExecutionStatus.EXECUTED
        assert results[1].status is ExecutionStatus.EXECUTED
        assert results[0].result_digest == results[1].result_digest

    def test_a_losing_replica_returns_the_claimants_recorded_outcome_without_running_its_handler(
        self,
    ) -> None:
        store = evidence_store()
        ledger = FakeLedger()
        replica_a = dispatcher(FakeLedgerProvider(ledger), store)
        replica_b = dispatcher(FakeLedgerProvider(ledger), store)
        executions: List[str] = []

        replica_a.register(
            "payments.wire_transfer", lambda action: executions.append("a") or {"ok": True}
        )
        replica_b.register(
            "payments.wire_transfer", lambda action: executions.append("b") or {"ok": True}
        )
        receipt = store.append_intent(make_intent())
        action = make_action()

        first = replica_a.dispatch(action, receipt, timeout_s=5.0, now=NOW)
        second = replica_b.dispatch(action, receipt, timeout_s=5.0, now=NOW)

        assert executions == ["a"], "the losing replica must never run its own handler"
        assert second.status is ExecutionStatus.EXECUTED
        assert second.result_digest == first.result_digest

    def test_a_claim_still_pending_at_the_deadline_is_indeterminate(self) -> None:
        """A losing replica must not wait forever, and must not guess."""
        store = evidence_store()
        ledger = FakeLedger()
        replica_a = dispatcher(FakeLedgerProvider(ledger), store, poll_interval_s=0.01)
        replica_b = dispatcher(FakeLedgerProvider(ledger), store, poll_interval_s=0.01)
        release = threading.Event()

        replica_a.register("payments.wire_transfer", lambda action: release.wait(timeout=5.0))
        replica_b.register("payments.wire_transfer", lambda action: release.wait(timeout=5.0))
        receipt = store.append_intent(make_intent())
        action = make_action()

        t = threading.Thread(
            target=replica_a.dispatch, args=(action, receipt), kwargs={"timeout_s": 5.0, "now": NOW}
        )
        t.start()
        try:
            # Give replica_a time to win the claim before replica_b observes it.
            import time as _time

            _time.sleep(0.05)
            outcome = replica_b.dispatch(action, receipt, timeout_s=0.1, now=NOW)
            assert outcome.status is ExecutionStatus.INDETERMINATE
        finally:
            release.set()
            t.join(timeout=5.0)
