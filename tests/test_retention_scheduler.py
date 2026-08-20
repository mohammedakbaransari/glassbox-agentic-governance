"""Tests for the automated retention scheduler (Workstream E).

Uses the in-memory evidence store (which already conforms to
``EvidenceRetentionStore``) and a frozen clock, so the seal/purge timing
policy is exercised deterministically with no real time passing.
"""

from __future__ import annotations

import pytest

from glassbox.adapters.outbound.memory.clock import FrozenClock
from glassbox.adapters.outbound.memory.evidence import InMemoryEvidenceStore
from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.worm import InMemoryWormAnchorStore
from glassbox.app.retention_scheduler import RetentionAction, RetentionScheduler
from glassbox.app.sealer import SegmentSealer
from glassbox.domain.errors import EvidenceWriteError
from tests.test_domain import make_action, make_intent

SEGMENT_ID = "seg-acme-19000"


def _store_with_records(*, count: int = 5, opened_at: float = 0.0) -> InMemoryEvidenceStore:
    store = InMemoryEvidenceStore(signer=LocalMacSigner(key_id="test.key", key=b"\x22" * 32))
    for index in range(count):
        store.append_intent(
            make_intent(
                decision_id=f"decision-sched-{index}",
                segment_id=SEGMENT_ID,
                created_at=opened_at,
                action=make_action(idempotency_key=f"idem-sched-{index}"),
            )
        )
    return store


def _scheduler(
    store: InMemoryEvidenceStore, clock: FrozenClock, **kwargs
) -> RetentionScheduler:
    sealer = SegmentSealer(
        retention=store,
        anchors=InMemoryWormAnchorStore(),
        signer=LocalMacSigner(key_id="test.key", key=b"\x22" * 32),
    )
    defaults = {"seal_after_seconds": 86_400.0, "purge_grace_seconds": 86_400.0}
    defaults.update(kwargs)
    return RetentionScheduler(retention=store, sealer=sealer, clock=clock, **defaults)


class TestConstruction:
    def test_rejects_negative_windows(self) -> None:
        store = _store_with_records()
        clock = FrozenClock(instant=0.0)
        sealer = SegmentSealer(
            retention=store,
            anchors=InMemoryWormAnchorStore(),
            signer=LocalMacSigner(key_id="test.key", key=b"\x22" * 32),
        )
        with pytest.raises(EvidenceWriteError):
            RetentionScheduler(
                retention=store,
                sealer=sealer,
                clock=clock,
                seal_after_seconds=-1.0,
                purge_grace_seconds=1.0,
            )


class TestSchedulerPolicy:
    def test_an_unknown_segment_is_skipped(self) -> None:
        store = _store_with_records()
        clock = FrozenClock(instant=0.0)
        scheduler = _scheduler(store, clock)
        outcomes = scheduler.run_once(["no-such-segment"])
        assert outcomes[0].action is RetentionAction.SKIPPED

    def test_a_young_segment_is_not_yet_sealed(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=100.0)  # far younger than seal_after_seconds
        scheduler = _scheduler(store, clock, seal_after_seconds=86_400.0)
        outcomes = scheduler.run_once([SEGMENT_ID])
        assert outcomes[0].action is RetentionAction.SKIPPED
        assert "not old enough" in outcomes[0].detail

    def test_an_old_unsealed_segment_is_sealed(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(store, clock, seal_after_seconds=86_400.0)
        outcomes = scheduler.run_once([SEGMENT_ID])
        assert outcomes[0].action is RetentionAction.SEALED

        state = store.segment_state(SEGMENT_ID)
        assert state is not None
        assert state.sealed_at == 200_000.0
        assert state.last_seq == 4

    def test_running_twice_does_not_reseal(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(store, clock, seal_after_seconds=86_400.0, purge_grace_seconds=1e9)
        scheduler.run_once([SEGMENT_ID])
        second_pass = scheduler.run_once([SEGMENT_ID])
        assert second_pass[0].action is RetentionAction.SKIPPED
        assert "grace period" in second_pass[0].detail

    def test_a_sealed_segment_within_the_grace_period_is_not_purged(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(
            store, clock, seal_after_seconds=86_400.0, purge_grace_seconds=86_400.0
        )
        scheduler.run_once([SEGMENT_ID])
        outcomes = scheduler.run_once([SEGMENT_ID])
        assert outcomes[0].action is RetentionAction.SKIPPED
        assert store.segment_size(SEGMENT_ID) == 5

    def test_a_sealed_segment_past_grace_is_purged(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(
            store, clock, seal_after_seconds=86_400.0, purge_grace_seconds=86_400.0
        )
        scheduler.run_once([SEGMENT_ID])
        clock.instant = 200_000.0 + 86_401.0
        outcomes = scheduler.run_once([SEGMENT_ID])
        assert outcomes[0].action is RetentionAction.PURGED
        assert store.segment_size(SEGMENT_ID) == 0

    def test_purging_twice_is_a_no_op_the_second_time(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(
            store, clock, seal_after_seconds=86_400.0, purge_grace_seconds=86_400.0
        )
        scheduler.run_once([SEGMENT_ID])
        clock.instant = 200_000.0 + 86_401.0
        scheduler.run_once([SEGMENT_ID])
        again = scheduler.run_once([SEGMENT_ID])
        assert again[0].action is RetentionAction.SKIPPED
        assert "already purged" in again[0].detail

    def test_verification_still_succeeds_after_a_full_seal_and_purge_cycle(self) -> None:
        """The whole point of GB-007: retention must not break auditability."""
        from glassbox.domain.evidence import IntegrityStatus

        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(
            store, clock, seal_after_seconds=86_400.0, purge_grace_seconds=86_400.0
        )
        scheduler.run_once([SEGMENT_ID])
        clock.instant = 200_000.0 + 86_401.0
        scheduler.run_once([SEGMENT_ID])

        report = store.verify(SEGMENT_ID, now=clock.instant)
        assert report.status is IntegrityStatus.SEALED_PURGED

    def test_one_failing_segment_does_not_stop_the_pass(self) -> None:
        store = _store_with_records(opened_at=0.0)
        clock = FrozenClock(instant=200_000.0)
        scheduler = _scheduler(store, clock, seal_after_seconds=86_400.0)

        outcomes = scheduler.run_once(["missing-segment-a", SEGMENT_ID, "missing-segment-b"])

        assert outcomes[0].action is RetentionAction.SKIPPED
        assert outcomes[1].action is RetentionAction.SEALED
        assert outcomes[2].action is RetentionAction.SKIPPED
