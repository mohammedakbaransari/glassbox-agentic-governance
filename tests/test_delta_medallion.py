"""Tests for the Delta evidence medallion (GB-030, GB-031).

Uses the real ``deltalake`` (delta-rs) library against a temp directory --
no fake, no mock, no JVM. This is a genuine integration test of the actual
storage engine, which is possible precisely because GB-030/031's design
deliberately avoids a Spark cluster dependency for Bronze/Silver ingestion.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pytest

deltalake = pytest.importorskip("deltalake")

from glassbox.adapters.outbound.delta.bronze import DeltaBronzeWriter
from glassbox.adapters.outbound.delta.cdc_consumer import CdcConsumer, InMemoryCheckpointStore
from glassbox.adapters.outbound.delta.rows import (
    intent_bronze_schema,
    intent_record_to_bronze_row,
    outcome_bronze_schema,
    outcome_record_to_bronze_row,
)
from glassbox.adapters.outbound.delta.silver import DeltaSilverMerger
from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
from glassbox.domain.evidence import OutcomeRecord
from tests.test_domain import NOW, make_intent


@pytest.fixture
def tmp_tables(tmp_path: Path) -> Dict[str, str]:
    return {
        "bronze_intent": str(tmp_path / "bronze_intent"),
        "bronze_outcome": str(tmp_path / "bronze_outcome"),
        "silver": str(tmp_path / "silver"),
    }


def _bronze_rows(n: int, *, tenant_id: str = "acme") -> List[Dict[str, Any]]:
    return [
        intent_record_to_bronze_row(
            make_intent(decision_id=f"decision-{i:04d}", tenant_id=tenant_id),
            seq=i,
            cdc_lsn=f"lsn-{i:06d}",
        )
        for i in range(n)
    ]


class TestDeltaBronzeWriter:
    def test_a_fresh_table_is_created_on_first_write(self, tmp_tables: Dict[str, str]) -> None:
        writer = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        inserted = writer.append_batch(_bronze_rows(3))
        assert inserted == 3
        assert writer.row_count() == 3

    def test_row_count_reconciles_exactly_with_the_source(self, tmp_tables: Dict[str, str]) -> None:
        """GB-030's acceptance criterion, verified directly."""
        writer = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        source_rows = _bronze_rows(50)
        writer.append_batch(source_rows)
        assert writer.row_count() == len(source_rows)

    def test_replaying_an_overlapping_batch_never_double_counts(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        """Exactly-once by decision_id: a CDC consumer that crashed after
        writing but before advancing its checkpoint will replay this batch."""
        writer = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        first_batch = _bronze_rows(5)
        writer.append_batch(first_batch)

        overlapping_batch = _bronze_rows(8)  # rows 0-4 are duplicates, 5-7 are new
        second_insert_count = writer.append_batch(overlapping_batch)

        assert second_insert_count == 3
        assert writer.row_count() == 8

    def test_appending_an_empty_batch_is_a_no_op(self, tmp_tables: Dict[str, str]) -> None:
        writer = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        assert writer.append_batch([]) == 0
        assert writer.row_count() == 0

    def test_row_count_of_a_table_that_does_not_exist_yet_is_zero(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        writer = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        assert writer.row_count() == 0


class _ListChangeEventSource:
    """A :class:`ChangeEventSource` over a plain in-memory list, for tests.

    Production is Postgres logical replication; this fake proves the
    consumer's checkpoint-then-Bronze-merge contract without a live server.
    """

    def __init__(self, events: List[Dict[str, Any]]) -> None:
        self._events = events

    def poll(self, *, since_checkpoint: Optional[str], limit: int) -> Iterable[Dict[str, Any]]:
        if since_checkpoint is None:
            start = 0
        else:
            start = next(
                (i + 1 for i, e in enumerate(self._events) if e["cdc_lsn"] == since_checkpoint),
                len(self._events),
            )
        return self._events[start : start + limit]


class TestCdcConsumer:
    def test_run_once_lands_one_batch_and_advances_the_checkpoint(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        events = _bronze_rows(10)
        source = _ListChangeEventSource(events)
        bronze = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        checkpoints = InMemoryCheckpointStore()
        consumer = CdcConsumer(source, bronze, checkpoints, stream="evidence_intent")

        inserted = consumer.run_once(batch_size=4)
        assert inserted == 4
        assert bronze.row_count() == 4
        assert checkpoints.get("evidence_intent") == "lsn-000003"

    def test_run_until_drained_lands_every_event_exactly_once(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        events = _bronze_rows(37)
        source = _ListChangeEventSource(events)
        bronze = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        checkpoints = InMemoryCheckpointStore()
        consumer = CdcConsumer(source, bronze, checkpoints, stream="evidence_intent")

        total_inserted = consumer.run_until_drained(batch_size=10)
        assert total_inserted == 37
        assert bronze.row_count() == 37

    def test_replaying_from_a_stale_checkpoint_reconciles_without_duplicating(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        """Simulates a consumer crash: the checkpoint is rolled back to an
        earlier position (as if the last advance was never durably recorded)
        and the same events are polled again."""
        events = _bronze_rows(20)
        source = _ListChangeEventSource(events)
        bronze = DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema())
        checkpoints = InMemoryCheckpointStore()
        consumer = CdcConsumer(source, bronze, checkpoints, stream="evidence_intent")
        consumer.run_until_drained(batch_size=5)
        assert bronze.row_count() == 20

        checkpoints.set("evidence_intent", "lsn-000009")  # rewind to an earlier point
        consumer.run_until_drained(batch_size=5)

        assert bronze.row_count() == 20, "replaying already-landed events must not duplicate rows"


class TestDeltaSilverMerger:
    def test_an_intent_with_no_outcome_yet_merges_as_intent_only(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema()).append_batch(
            _bronze_rows(3)
        )
        merger = DeltaSilverMerger(
            tmp_tables["bronze_intent"], tmp_tables["bronze_outcome"], tmp_tables["silver"]
        )
        stats = merger.merge()
        assert stats["inserted"] == 3
        assert stats["updated"] == 0
        rows = {row["decision_id"]: row for row in merger.read().to_pylist()}
        assert rows["decision-0000"].get("status") is None

    def test_a_later_outcome_updates_the_existing_silver_row_in_place(
        self, tmp_tables: Dict[str, str]
    ) -> None:
        DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema()).append_batch(
            _bronze_rows(1)
        )
        merger = DeltaSilverMerger(
            tmp_tables["bronze_intent"], tmp_tables["bronze_outcome"], tmp_tables["silver"]
        )
        merger.merge()
        assert merger.read().num_rows == 1

        outcome_row = outcome_record_to_bronze_row(
            OutcomeRecord(
                decision_id="decision-0000",
                outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
            ),
            tenant_id="acme",
            cdc_lsn="lsn-out-000000",
        )
        DeltaBronzeWriter(
            tmp_tables["bronze_outcome"], partition_by=(), schema=outcome_bronze_schema()
        ).append_batch([outcome_row])

        stats = merger.merge()
        assert stats["inserted"] == 0
        assert stats["updated"] == 1
        assert merger.read().num_rows == 1  # updated in place, not a second row

        rows = {row["decision_id"]: row for row in merger.read().to_pylist()}
        assert rows["decision-0000"]["status"] == "executed"

    def test_merging_twice_with_no_new_data_is_idempotent(self, tmp_tables: Dict[str, str]) -> None:
        DeltaBronzeWriter(tmp_tables["bronze_intent"], schema=intent_bronze_schema()).append_batch(
            _bronze_rows(5)
        )
        merger = DeltaSilverMerger(
            tmp_tables["bronze_intent"], tmp_tables["bronze_outcome"], tmp_tables["silver"]
        )
        merger.merge()
        stats = merger.merge()
        assert stats["inserted"] == 0
        assert stats["updated"] == 5  # same rows re-merged, matched and "updated" to themselves
        assert merger.read().num_rows == 5
