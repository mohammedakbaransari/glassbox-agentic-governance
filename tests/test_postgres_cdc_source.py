"""Tests for the production Postgres logical-replication CDC source.

wal_level=logical and the wal2json plugin are not available on this
project's dev sandbox (see glassbox/adapters/outbound/postgres/cdc_source.py's
module docstring), so :class:`PostgresLogicalReplicationSource` is verified
here against a fake cursor that reproduces wal2json's documented output shape
-- not against a real replication slot.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from glassbox.adapters.outbound.delta.cdc_consumer import ChangeEventSource
from glassbox.adapters.outbound.delta.rows import (
    intent_evidence_dict_to_bronze_row,
    intent_record_to_bronze_row,
    outcome_evidence_dict_to_bronze_row,
    outcome_record_to_bronze_row,
)
from glassbox.adapters.outbound.postgres.cdc_source import PostgresLogicalReplicationSource
from glassbox.adapters.outbound.postgres.driver import DriverUnavailableError
from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
from glassbox.domain.evidence import OutcomeRecord
from tests.test_domain import NOW, make_intent

# --------------------------------------------------------------------------- #
# Flatten functions: pure, grounded in a real .as_evidence() dict shape
# --------------------------------------------------------------------------- #


class TestIntentEvidenceDictToBronzeRow:
    def test_matches_the_live_object_flattener_field_for_field(self) -> None:
        record = make_intent()
        expected = intent_record_to_bronze_row(record, seq=3, cdc_lsn="lsn-1")
        actual = intent_evidence_dict_to_bronze_row(
            dict(record.as_evidence()), seq=3, cdc_lsn="lsn-1"
        )
        assert actual == expected

    def test_record_json_round_trips_the_input_mapping(self) -> None:
        record = make_intent()
        evidence = dict(record.as_evidence())
        row = intent_evidence_dict_to_bronze_row(evidence, seq=0, cdc_lsn="lsn-1")
        assert json.loads(row["record_json"]) == evidence


class TestOutcomeEvidenceDictToBronzeRow:
    def test_matches_the_live_object_flattener_field_for_field(self) -> None:
        record = OutcomeRecord(
            decision_id="decision-0001",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        expected = outcome_record_to_bronze_row(record, tenant_id="acme", cdc_lsn="lsn-1")
        actual = outcome_evidence_dict_to_bronze_row(
            dict(record.as_evidence()), tenant_id="acme", cdc_lsn="lsn-1"
        )
        assert actual == expected

    def test_tenant_id_may_be_none(self) -> None:
        record = OutcomeRecord(
            decision_id="decision-0001",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        row = outcome_evidence_dict_to_bronze_row(
            dict(record.as_evidence()), tenant_id=None, cdc_lsn="lsn-1"
        )
        assert row["tenant_id"] is None


# --------------------------------------------------------------------------- #
# A fake wal2json-speaking cursor/provider
# --------------------------------------------------------------------------- #


class _FakeCursor:
    def __init__(self, db: "_FakeDb") -> None:
        self._db = db
        self._rows: List[Tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        normalised = " ".join(sql.split())
        if self._db.raise_on_execute is not None:
            raise self._db.raise_on_execute
        if "SELECT 1 FROM pg_replication_slots" in normalised:
            (slot_name,) = params
            self._rows = [(1,)] if slot_name in self._db.slots else []
        elif "pg_create_logical_replication_slot" in normalised:
            (slot_name,) = params
            self._db.slots.add(slot_name)
            self._rows = []
        elif "pg_logical_slot_get_changes" in normalised:
            slot_name, limit = params
            assert slot_name in self._db.slots, "slot must be created before changes are read"
            self._rows = [
                (change["lsn"], change["xid"], json.dumps(change["data"]))
                for change in self._db.pending[:limit]
            ]
        else:
            raise AssertionError(f"unexpected statement: {normalised}")

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Tuple[Any, ...]]:
        return list(self._rows)


class _FakeDb:
    def __init__(self) -> None:
        self.slots: set = set()
        self.pending: List[Dict[str, Any]] = []
        self.raise_on_execute: Optional[Exception] = None


class _FakeProvider:
    def __init__(self, db: Optional[_FakeDb] = None) -> None:
        self.db = db or _FakeDb()

    @contextmanager
    def transaction(self) -> Iterator[_FakeCursor]:
        yield _FakeCursor(self.db)

    def close(self) -> None:
        return None


def _wal2json_change(
    *, lsn: str, xid: int, table: str, action: str, columns: Dict[str, Tuple[str, Any]]
) -> Dict[str, Any]:
    return {
        "lsn": lsn,
        "xid": xid,
        "data": {
            "action": action,
            "schema": "public",
            "table": table,
            "columns": [
                {"name": name, "type": type_, "value": value}
                for name, (type_, value) in columns.items()
            ],
        },
    }


def _intent_change(*, lsn: str = "0/100", seq: int = 0) -> Dict[str, Any]:
    record = make_intent()
    evidence_json = json.dumps(dict(record.as_evidence()))
    return _wal2json_change(
        lsn=lsn,
        xid=1,
        table="evidence_intent",
        action="I",
        columns={
            "decision_id": ("text", record.decision_id),
            "seq": ("bigint", seq),
            "record": ("jsonb", evidence_json),
        },
    )


# --------------------------------------------------------------------------- #
# PostgresLogicalReplicationSource
# --------------------------------------------------------------------------- #


class TestPostgresLogicalReplicationSource:
    def test_conforms_to_the_change_event_source_port(self) -> None:
        source = PostgresLogicalReplicationSource(_FakeProvider(), slot_name="s", stream="intent")
        assert isinstance(source, ChangeEventSource)

    def test_rejects_an_unknown_stream_name(self) -> None:
        with pytest.raises(ValueError):
            PostgresLogicalReplicationSource(_FakeProvider(), slot_name="s", stream="bogus")

    def test_the_slot_is_created_lazily_on_first_poll(self) -> None:
        provider = _FakeProvider()
        source = PostgresLogicalReplicationSource(provider, slot_name="gb_030", stream="intent")
        assert "gb_030" not in provider.db.slots
        source.poll(since_checkpoint=None, limit=10)
        assert "gb_030" in provider.db.slots

    def test_the_slot_is_not_recreated_once_it_exists(self) -> None:
        provider = _FakeProvider()
        provider.db.slots.add("gb_030")
        source = PostgresLogicalReplicationSource(provider, slot_name="gb_030", stream="intent")
        source.poll(since_checkpoint=None, limit=10)  # would assert-fail on a bad CREATE path
        assert provider.db.slots == {"gb_030"}

    def test_an_insert_on_the_intent_table_yields_a_bronze_shaped_row(self) -> None:
        provider = _FakeProvider()
        change = _intent_change(lsn="0/100", seq=7)
        provider.db.pending = [change]
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="intent")

        events = list(source.poll(since_checkpoint=None, limit=10))

        assert len(events) == 1
        assert events[0]["seq"] == 7
        assert events[0]["decision_id"] == "decision-0001"
        assert events[0]["cdc_lsn"] == str((0x0 << 32) | 0x100)

    def test_a_non_insert_action_is_skipped(self) -> None:
        provider = _FakeProvider()
        change = _wal2json_change(lsn="0/1", xid=1, table="evidence_intent", action="U", columns={})
        provider.db.pending = [change]
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="intent")
        assert list(source.poll(since_checkpoint=None, limit=10)) == []

    def test_a_change_on_a_different_table_is_skipped(self) -> None:
        provider = _FakeProvider()
        change = _wal2json_change(
            lsn="0/1", xid=1, table="some_other_table", action="I", columns={}
        )
        provider.db.pending = [change]
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="intent")
        assert list(source.poll(since_checkpoint=None, limit=10)) == []

    def test_since_checkpoint_excludes_already_seen_lsns(self) -> None:
        provider = _FakeProvider()
        change = _intent_change(lsn="0/100", seq=1)
        provider.db.pending = [change]
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="intent")

        floor = str((0x0 << 32) | 0x100)
        events = list(source.poll(since_checkpoint=floor, limit=10))

        assert events == []

    def test_outcome_stream_produces_outcome_shaped_rows_with_null_tenant(self) -> None:
        provider = _FakeProvider()
        outcome = OutcomeRecord(
            decision_id="decision-0001",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        change = _wal2json_change(
            lsn="0/1",
            xid=1,
            table="evidence_outcome",
            action="I",
            columns={
                "decision_id": ("text", "decision-0001"),
                "record": ("jsonb", json.dumps(dict(outcome.as_evidence()))),
            },
        )
        provider.db.pending = [change]
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="outcome")

        events = list(source.poll(since_checkpoint=None, limit=10))

        assert len(events) == 1
        assert events[0]["decision_id"] == "decision-0001"
        assert events[0]["tenant_id"] is None

    def test_a_read_failure_raises_driver_unavailable(self) -> None:
        provider = _FakeProvider()
        provider.db.slots.add("s")
        provider.db.raise_on_execute = RuntimeError("connection reset")
        source = PostgresLogicalReplicationSource(provider, slot_name="s", stream="intent")
        with pytest.raises(DriverUnavailableError):
            source.poll(since_checkpoint=None, limit=10)
