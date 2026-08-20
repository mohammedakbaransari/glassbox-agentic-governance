"""Tests for the Postgres evidence store (GB-005).

Three tiers, because each proves something the others cannot.

**Fake-backed conformance.** :class:`FakeConnectionProvider` implements the eight
statements the store issues, semantically. That is tractable precisely because
the statement set is small and fixed, and it lets the shared
``EvidenceStoreConformance`` suite run against the real store class with no
server -- so the Postgres adapter and the in-memory reference are held to one
specification, which is what stops them drifting the way v1's local and Redis
anomaly stores did.

**Transaction-shape tests.** The fake records every statement in order, so the
ordering guarantee this card exists for -- lock the segment, then read the head,
then insert, then advance, all inside one transaction that commits before the
receipt is returned -- is asserted directly rather than hoped for.

**Integration tests.** Gated behind ``GLASSBOX_POSTGRES_DSN``. Only a real server
can prove that the append-only trigger rejects an ``UPDATE``, that the unique
index rejects a forked chain, and that ``FOR UPDATE`` actually serialises
concurrent appenders. CI provides the service container under GB-035.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.postgres.driver import DriverUnavailableError
from glassbox.adapters.outbound.postgres.evidence import PostgresEvidenceStore
from glassbox.adapters.outbound.postgres.schema import (
    MIGRATIONS,
    RETENTION_PURGE_GUC,
    SCHEMA_VERSION,
    apply_migrations,
)
from glassbox.domain.errors import EvidenceWriteError
from glassbox.domain.evidence import GENESIS_PREV_HASH, IntegrityStatus
from tests.conformance_evidence import SEGMENT, EvidenceStoreConformance
from tests.test_domain import NOW, make_action, make_intent

# --------------------------------------------------------------------------- #
# A semantic fake for the eight statements the store issues
# --------------------------------------------------------------------------- #


class FakeCursor:
    """Executes the store's known statements against in-memory dictionaries.

    Matching is by a distinctive fragment of each statement, so a change to the
    SQL that the fake does not understand raises rather than silently returning
    an empty result -- which would turn a broken query into a passing test.
    """

    def __init__(self, database: "FakeDatabase") -> None:
        self._db = database
        self._rows: List[Tuple[Any, ...]] = []

    def execute(self, sql: str, params: Sequence[Any] = ()) -> None:
        normalised = " ".join(sql.split())
        self._db.log.append((normalised, tuple(params)))
        if self._db.fail_next:
            self._db.fail_next = False
            raise RuntimeError("simulated database failure")

        for fragment, handler in (
            ("INSERT INTO evidence_segment", self._insert_segment),
            ("SELECT retention_sealed_last_seq", self._select_retention_seal),
            ("FROM evidence_segment WHERE segment_id = %s FOR UPDATE", self._lock_segment),
            ("FROM evidence_intent WHERE decision_id = %s", self._select_by_decision),
            ("INSERT INTO evidence_intent", self._insert_intent),
            ("UPDATE evidence_segment SET last_seq", self._advance_segment),
            ("INSERT INTO evidence_outcome", self._insert_outcome),
            ("purged_before_seq, sealed_at, merkle_root", self._select_segment_state),
            ("retention_sealed_at, retention_sealed_first_seq", self._select_segment_full),
            ("retention_sealed_at = to_timestamp", self._mark_sealed),
            ("SELECT seq, record_hmac", self._select_leaves),
            ("ORDER BY seq ASC", self._select_chain),
            ("ORDER BY seq DESC", self._select_anchor),
            ("DELETE FROM evidence_intent", self._delete_purged),
            ("UPDATE evidence_segment SET first_seq", self._advance_purge_marker),
            ("UPDATE evidence_segment SET purged_before_seq", self._seal_segment),
            ("UPDATE evidence_intent SET record", self._tamper),
            ("SET LOCAL", self._noop),
            ("ALTER TABLE", self._noop),
            ("SELECT set_config", self._noop),
        ):
            if fragment in normalised:
                self._rows = handler(tuple(params))
                return
        raise AssertionError(f"FakeCursor does not implement this statement: {normalised}")

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> List[Tuple[Any, ...]]:
        return list(self._rows)

    # -- handlers ------------------------------------------------------ #

    def _noop(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        return []

    def _insert_segment(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment_id, tenant_id, genesis = params
        self._db.segments.setdefault(
            segment_id,
            {
                "tenant_id": tenant_id,
                "opened_at": datetime.fromtimestamp(NOW, tz=timezone.utc),
                "first_seq": 0,
                "last_seq": -1,
                "last_hash": bytes(genesis),
                "purged_before_seq": 0,
                "sealed_at": None,
                "merkle_root": None,
                "worm_anchor_id": None,
                "retention_sealed_at": None,
                "retention_sealed_first_seq": None,
                "retention_sealed_last_seq": None,
                "retention_merkle_root": None,
                "retention_seal_signature": None,
                "retention_worm_anchor_id": None,
                "retention_worm_locator": None,
                "retention_last_leaf_hmac": None,
            },
        )
        return []

    def _lock_segment(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment = self._db.segments.get(params[0])
        if segment is None:
            return []
        self._db.locks_taken.append(params[0])
        return [
            (
                segment["last_seq"],
                segment["last_hash"],
                segment["purged_before_seq"],
                segment["sealed_at"],
            )
        ]

    def _select_by_decision(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        key = self._db.by_decision.get(params[0])
        if key is None:
            return []
        row = self._db.intents[key]
        return [
            (
                row["segment_id"],
                row["seq"],
                row["record_hmac"],
                row["signer_key_id"],
                row["created_at"],
                row["decision_id"],
            )
        ]

    def _insert_intent(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        row = dict(zip(_INSERT_COLUMNS, params))
        key = (row["segment_id"], row["seq"])
        if key in self._db.intents:
            raise RuntimeError("duplicate key value violates unique constraint")
        if row["decision_id"] in self._db.by_decision:
            raise RuntimeError("duplicate key value violates ux_evidence_intent_decision")
        self._db.intents[key] = row
        self._db.by_decision[row["decision_id"]] = key
        return []

    def _advance_segment(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        last_seq, last_hash, segment_id = params
        segment = self._db.segments[segment_id]
        segment["last_seq"] = last_seq
        segment["last_hash"] = bytes(last_hash)
        return []

    def _insert_outcome(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        self._db.outcomes.setdefault(params[0], params)
        return []

    def _select_segment_state(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment = self._db.segments.get(params[0])
        if segment is None:
            return []
        return [
            (
                segment["purged_before_seq"],
                segment["sealed_at"],
                segment["merkle_root"],
                segment["worm_anchor_id"],
                segment["retention_sealed_last_seq"],
                segment["retention_worm_anchor_id"],
                segment["retention_last_leaf_hmac"],
            )
        ]

    def _select_chain(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        rows = [
            row for (segment_id, _seq), row in self._db.intents.items() if segment_id == params[0]
        ]
        rows.sort(key=lambda row: row["seq"])
        return [
            (
                row["seq"],
                row["record"],
                row["prev_hash"],
                row["record_hmac"],
                row["signer_key_id"],
                row["decision_id"],
                row["tenant_id"],
                row["action"],
                row["consequence_class"],
                row["risk_score"],
            )
            for row in rows
        ]

    def _select_anchor(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment_id, before_seq = params
        candidates = [
            row
            for (stored_segment, _seq), row in self._db.intents.items()
            if stored_segment == segment_id and row["seq"] < before_seq
        ]
        if not candidates:
            return []
        candidates.sort(key=lambda row: row["seq"], reverse=True)
        return [(candidates[0]["record_hmac"],)]

    def _delete_purged(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment_id, before_seq = params
        doomed = [
            key
            for key, row in self._db.intents.items()
            if key[0] == segment_id and row["seq"] < before_seq
        ]
        for key in doomed:
            self._db.by_decision.pop(self._db.intents[key]["decision_id"], None)
            del self._db.intents[key]
        return [(key[1],) for key in doomed]

    def _seal_segment(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        purged_before, anchor, first_seq, segment_id = params
        segment = self._db.segments[segment_id]
        segment["purged_before_seq"] = purged_before
        segment["merkle_root"] = bytes(anchor)
        segment["sealed_at"] = segment["sealed_at"] or NOW
        return []

    def _tamper(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        payload, segment_id, seq = params
        self._db.intents[(segment_id, seq)]["record"] = payload
        return []

    # -- EvidenceRetentionStore (GB-007) port-conformant handlers ------- #

    def _select_segment_full(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment = self._db.segments.get(params[0])
        if segment is None:
            return []
        return [
            (
                segment["tenant_id"],
                segment["opened_at"],
                segment["first_seq"],
                segment["purged_before_seq"],
                segment["retention_sealed_at"],
                segment["retention_sealed_first_seq"],
                segment["retention_sealed_last_seq"],
                segment["retention_merkle_root"],
                segment["retention_seal_signature"],
                segment["retention_worm_anchor_id"],
            )
        ]

    def _select_leaves(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment_id = params[0]
        before_seq = params[1] if len(params) > 1 else None
        rows = [
            row
            for (stored_segment, _seq), row in self._db.intents.items()
            if stored_segment == segment_id and (before_seq is None or row["seq"] < before_seq)
        ]
        rows.sort(key=lambda row: row["seq"])
        return [(row["seq"], row["record_hmac"]) for row in rows]

    def _mark_sealed(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        (
            sealed_at,
            sealed_first_seq,
            sealed_last_seq,
            merkle_root,
            seal_signature,
            worm_anchor_id,
            locator,
            segment_id,
            _leaf_lookup_last_seq,
            _leaf_lookup_segment_id,
        ) = params
        segment = self._db.segments[segment_id]
        segment["retention_sealed_at"] = datetime.fromtimestamp(sealed_at, tz=timezone.utc)
        segment["retention_sealed_first_seq"] = sealed_first_seq
        segment["retention_sealed_last_seq"] = sealed_last_seq
        segment["retention_merkle_root"] = bytes(merkle_root)
        segment["retention_seal_signature"] = bytes(seal_signature)
        segment["retention_worm_anchor_id"] = worm_anchor_id
        segment["retention_worm_locator"] = locator
        leaf_row = self._db.intents.get((segment_id, sealed_last_seq))
        segment["retention_last_leaf_hmac"] = leaf_row["record_hmac"] if leaf_row else None
        return []

    def _select_retention_seal(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        segment = self._db.segments.get(params[0])
        if segment is None:
            return []
        return [(segment["retention_sealed_last_seq"], segment["retention_last_leaf_hmac"])]

    def _advance_purge_marker(self, params: Tuple[Any, ...]) -> List[Tuple[Any, ...]]:
        before_seq_a, before_seq_b, segment_id = params
        segment = self._db.segments[segment_id]
        segment["purged_before_seq"] = before_seq_a
        segment["first_seq"] = before_seq_b
        return []


#: Column order of ``_INSERT_INTENT``, used to rebuild a row from its parameters.
_INSERT_COLUMNS = (
    "segment_id",
    "seq",
    "decision_id",
    "tenant_id",
    "created_at",
    "agent_ref",
    "agent_instance_id",
    "delegating_subject",
    "credential_type",
    "credential_id",
    "action",
    "resource_kind",
    "resource_id",
    "consequence_class",
    "idempotency_key",
    "policy_bundle_id",
    "policy_bundle_sha256",
    "decision_effect",
    "risk_model_ver",
    "risk_score",
    "risk_level",
    "trace_id",
    "causation_id",
    "record",
    "prev_hash",
    "record_hmac",
    "signer_key_id",
)


class FakeDatabase:
    """Shared state behind :class:`FakeConnectionProvider`."""

    def __init__(self) -> None:
        self.segments: Dict[str, Dict[str, Any]] = {}
        self.intents: Dict[Tuple[str, int], Dict[str, Any]] = {}
        self.by_decision: Dict[str, Tuple[str, int]] = {}
        self.outcomes: Dict[str, Any] = {}
        self.log: List[Tuple[str, Tuple[Any, ...]]] = []
        self.locks_taken: List[str] = []
        self.fail_next = False
        self.available = True
        self.commits = 0
        self.rollbacks = 0


class FakeConnectionProvider:
    """A :class:`ConnectionProvider` backed by :class:`FakeDatabase`.

    A single re-entrant lock stands in for ``SELECT ... FOR UPDATE``: it gives the
    same serialisation the real store relies on, so the concurrency conformance
    test is meaningful here too.
    """

    def __init__(self, database: Optional[FakeDatabase] = None) -> None:
        self.db = database or FakeDatabase()
        self._lock = threading.RLock()

    @contextmanager
    def transaction(self) -> Iterator[FakeCursor]:
        if not self.db.available:
            raise DriverUnavailableError("simulated outage", adapter="FakeConnectionProvider")
        with self._lock:
            cursor = FakeCursor(self.db)
            self.db.log.append(("BEGIN", ()))
            try:
                yield cursor
            except Exception:
                self.db.log.append(("ROLLBACK", ()))
                self.db.rollbacks += 1
                raise
            self.db.log.append(("COMMIT", ()))
            self.db.commits += 1

    def close(self) -> None:
        return None


def fake_store(**kwargs: Any) -> PostgresEvidenceStore:
    """Build a store over a fresh fake database."""
    return PostgresEvidenceStore(
        FakeConnectionProvider(),
        LocalMacSigner(key_id="test.key", key=b"\x11" * 32),
        allow_test_tampering=True,
        **kwargs,
    )


def _statements(store: PostgresEvidenceStore) -> List[str]:
    """Return the recorded statement labels for a store's fake database."""
    return [sql for sql, _params in store._provider.db.log]  # type: ignore[attr-defined]


# --------------------------------------------------------------------------- #
# Conformance
# --------------------------------------------------------------------------- #


class TestPostgresEvidenceConformance(EvidenceStoreConformance):
    """The Postgres store must satisfy the same specification as every other."""

    @pytest.fixture
    def store(self) -> PostgresEvidenceStore:
        return fake_store()


# --------------------------------------------------------------------------- #
# Transaction shape
# --------------------------------------------------------------------------- #


class TestTransactionShape:
    """The ordering guarantee this card exists for, asserted directly."""

    def test_append_runs_in_one_transaction_that_commits(self) -> None:
        store = fake_store()
        store.append_intent(make_intent())
        statements = _statements(store)
        assert statements[0] == "BEGIN"
        assert statements[-1] == "COMMIT"
        assert statements.count("BEGIN") == 1

    def test_the_tenant_context_is_set_before_any_other_statement(self) -> None:
        """GB-026b: RLS depends on this GUC being set before the first query that
        touches evidence_intent -- set it after, and the policy would apply to
        the wrong (or no) tenant for that statement."""
        store = fake_store()
        record = make_intent()
        store.append_intent(record)
        sql, params = store._provider.db.log[1]  # type: ignore[attr-defined]
        assert sql == "SELECT set_config(%s, %s, true)"
        assert params == ("glassbox.tenant_id", record.tenant_id)

    def test_the_segment_is_locked_before_the_chain_head_is_read(self) -> None:
        """Reading the head without the lock is how two writers fork a chain."""
        store = fake_store()
        store.append_intent(make_intent())
        statements = _statements(store)
        lock_index = next(i for i, sql in enumerate(statements) if "FOR UPDATE" in sql)
        insert_index = next(
            i for i, sql in enumerate(statements) if "INSERT INTO evidence_intent" in sql
        )
        assert lock_index < insert_index

    def test_the_chain_head_is_advanced_after_the_insert(self) -> None:
        store = fake_store()
        store.append_intent(make_intent())
        statements = _statements(store)
        insert_index = next(
            i for i, sql in enumerate(statements) if "INSERT INTO evidence_intent" in sql
        )
        advance_index = next(
            i for i, sql in enumerate(statements) if "UPDATE evidence_segment SET last_seq" in sql
        )
        assert insert_index < advance_index < statements.index("COMMIT")

    def test_the_receipt_is_returned_only_after_the_commit(self) -> None:
        """Invariant I1. Returning before the commit would let a caller dispatch
        on a write that a later rollback erases."""
        store = fake_store()
        store.append_intent(make_intent())
        assert store._provider.db.commits == 1  # type: ignore[attr-defined]
        assert store._provider.db.rollbacks == 0  # type: ignore[attr-defined]

    def test_a_failed_insert_rolls_back_and_raises(self) -> None:
        store = fake_store()
        store._provider.db.fail_next = True  # type: ignore[attr-defined]
        with pytest.raises(EvidenceWriteError):
            store.append_intent(make_intent())
        assert store._provider.db.rollbacks == 1  # type: ignore[attr-defined]
        assert store._provider.db.commits == 0  # type: ignore[attr-defined]

    def test_every_statement_binds_its_parameters(self) -> None:
        """String interpolation is how an append-only store stops being append-only."""
        store = fake_store()
        store.append_intent(make_intent())
        for sql, _params in store._provider.db.log:  # type: ignore[attr-defined]
            assert "'" not in sql, f"statement appears to interpolate a literal: {sql}"

    def test_the_genesis_link_is_thirty_two_zero_bytes(self) -> None:
        store = fake_store()
        store.append_intent(make_intent())
        row = next(iter(store._provider.db.intents.values()))  # type: ignore[attr-defined]
        assert row["prev_hash"] == GENESIS_PREV_HASH

    def test_each_record_links_to_its_predecessors_mac(self) -> None:
        store = fake_store()
        first = store.append_intent(make_intent(decision_id="decision-0000"))
        store.append_intent(make_intent(decision_id="decision-0001"))
        rows = sorted(
            store._provider.db.intents.values(),  # type: ignore[attr-defined]
            key=lambda row: row["seq"],
        )
        assert rows[1]["prev_hash"] == first.record_hmac


class TestFailClosed:
    """An unreachable database denies; it never lets an effect through."""

    def test_an_outage_raises_rather_than_returning_a_receipt(self) -> None:
        store = fake_store()
        store._provider.db.available = False  # type: ignore[attr-defined]
        with pytest.raises(EvidenceWriteError):
            store.append_intent(make_intent())

    def test_a_sealed_segment_refuses_further_appends(self) -> None:
        store = fake_store()
        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        store.seal_and_purge(SEGMENT, before_seq=2)
        with pytest.raises(EvidenceWriteError):
            store.append_intent(make_intent(decision_id="decision-9999"))

    def test_a_store_without_a_signer_cannot_be_built(self) -> None:
        with pytest.raises(EvidenceWriteError):
            PostgresEvidenceStore(FakeConnectionProvider(), None)  # type: ignore[arg-type]

    def test_a_store_without_a_provider_cannot_be_built(self) -> None:
        with pytest.raises(EvidenceWriteError):
            PostgresEvidenceStore(None, LocalMacSigner())  # type: ignore[arg-type]

    def test_test_tampering_is_refused_unless_explicitly_enabled(self) -> None:
        """The hook must be impossible to reach in a deployed process."""
        store = PostgresEvidenceStore(
            FakeConnectionProvider(), LocalMacSigner(key_id="k", key=b"\x11" * 32)
        )
        store.append_intent(make_intent())
        with pytest.raises(EvidenceWriteError):
            store.tamper_for_test(SEGMENT, 0, make_intent())


class TestDenormalisedColumnIntegrity:
    """Editing an indexed column is detected, not just editing the signed record."""

    def test_a_rewritten_indexed_column_breaks_verification(self) -> None:
        store = fake_store()
        store.append_intent(make_intent())
        row = next(iter(store._provider.db.intents.values()))  # type: ignore[attr-defined]
        row["risk_score"] = 0.0
        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.BROKEN
        assert "indexed column disagrees" in report.detail

    def test_the_check_can_be_disabled_for_backends_without_denormalisation(self) -> None:
        store = PostgresEvidenceStore(
            FakeConnectionProvider(),
            LocalMacSigner(key_id="k", key=b"\x11" * 32),
            verify_denormalised_columns=False,
        )
        store.append_intent(make_intent())
        row = next(iter(store._provider.db.intents.values()))  # type: ignore[attr-defined]
        row["tenant_id"] = "evilcorp"
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.INTACT


class TestPurgeWithoutSeal:
    """Rows removed from an unsealed segment leave nothing attesting to them."""

    def test_an_unsealed_purge_is_unverifiable_not_intact(self) -> None:
        store = fake_store()
        for index in range(4):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        database = store._provider.db  # type: ignore[attr-defined]
        doomed = [key for key in database.intents if key[1] < 2]
        for key in doomed:
            database.by_decision.pop(database.intents[key]["decision_id"], None)
            del database.intents[key]
        database.segments[SEGMENT]["purged_before_seq"] = 2

        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.UNVERIFIABLE
        assert report.is_acceptable is False


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #


class TestSchema:
    """The DDL must actually encode the guarantees the store claims."""

    @staticmethod
    def _ddl() -> str:
        return "\n".join(statements for _version, _description, statements in MIGRATIONS)

    def test_migrations_are_ordered_and_contiguous(self) -> None:
        versions = [version for version, _description, _sql in MIGRATIONS]
        assert versions == sorted(versions)
        assert versions == list(range(1, len(versions) + 1))
        assert versions[-1] == SCHEMA_VERSION

    def test_multi_statement_migrations_use_the_simple_execute_path(self) -> None:
        """psycopg rejects multi-command SQL when any params argument is supplied."""

        class MigrationCursor:
            def __init__(self) -> None:
                self.calls: List[Tuple[Any, ...]] = []
                self.last_sql = ""

            def execute(self, *args: Any) -> None:
                self.calls.append(args)
                self.last_sql = args[0]

            def fetchone(self) -> Optional[Tuple[Any, ...]]:
                if not isinstance(self.last_sql, str):
                    # A composed psycopg.sql query (e.g. ensure_monthly_partitions'
                    # partition-existence check) -- this fake only needs to confirm
                    # "yes, already partitioned" so the caller proceeds.
                    return (True,)
                if "evidence_intent_default" in self.last_sql:
                    return (True,)
                if "to_regclass" in self.last_sql:
                    return (False,)
                return None

            def fetchall(self) -> List[Tuple[Any, ...]]:
                return []

        class MigrationProvider:
            def __init__(self) -> None:
                self.cursor = MigrationCursor()

            @contextmanager
            def transaction(self) -> Iterator[MigrationCursor]:
                yield self.cursor

            def close(self) -> None:
                return None

        provider = MigrationProvider()
        assert apply_migrations(provider) == [version for version, _, _ in MIGRATIONS]
        for _version, _description, statements in MIGRATIONS:
            assert (statements,) in provider.cursor.calls

    def test_evidence_is_append_only_by_revoke_and_by_trigger(self) -> None:
        """Defence in depth: neither mechanism carries the guarantee alone."""
        ddl = self._ddl()
        assert "REVOKE UPDATE, TRUNCATE ON evidence_intent FROM PUBLIC" in ddl
        assert "BEFORE UPDATE OR DELETE OR TRUNCATE ON evidence_intent" in ddl
        assert "RAISE EXCEPTION" in ddl

    def test_deletion_requires_the_retention_setting(self) -> None:
        assert RETENTION_PURGE_GUC in self._ddl()

    def test_the_chain_cannot_fork(self) -> None:
        """The primary key is the backstop if the row lock is ever wrong."""
        assert "PRIMARY KEY (segment_id, seq)" in self._ddl()

    def test_a_decision_can_only_be_recorded_once(self) -> None:
        assert "ux_evidence_intent_decision" in self._ddl()

    def test_hash_widths_are_constrained(self) -> None:
        ddl = self._ddl()
        assert "octet_length(prev_hash) = 32" in ddl
        assert "octet_length(record_hmac) >= 32" in ddl

    def test_the_authoritative_record_column_exists(self) -> None:
        assert "record               JSONB       NOT NULL" in self._ddl()

    @pytest.mark.parametrize(
        "column",
        [
            "agent_ref",
            "delegating_subject",
            "credential_id",
            "consequence_class",
            "policy_bundle_sha256",
            "risk_model_ver",
            "trace_id",
            "causation_id",
            "signer_key_id",
        ],
    )
    def test_provenance_columns_v1_lacked_are_present(self, column: str) -> None:
        """v1's AuditRecord had none of these; an auditor could not answer with it."""
        assert column in self._ddl()

    def test_outcome_is_a_separate_table(self) -> None:
        """Splitting intent from outcome keeps the outcome write off the hot path."""
        assert "CREATE TABLE IF NOT EXISTS evidence_outcome" in self._ddl()


# --------------------------------------------------------------------------- #
# EvidenceRetentionStore (GB-007) -- port-conformant retention path
# --------------------------------------------------------------------------- #


class TestPostgresRetentionStore:
    """PostgresEvidenceStore must satisfy EvidenceRetentionStore end to end.

    Exercised against the SegmentSealer that a real deployment would use --
    the port is the seam, and the sealer is the only intended caller of it.
    """

    def _seeded_store(self, *, count: int = 5) -> Tuple[PostgresEvidenceStore, str]:
        store = fake_store()
        segment_id = "seg-retention-1"
        for index in range(count):
            store.append_intent(
                make_intent(
                    decision_id=f"decision-ret-{index}",
                    segment_id=segment_id,
                    action=make_action(idempotency_key=f"idem-ret-{index}"),
                )
            )
        return store, segment_id

    def test_conforms_to_the_evidence_retention_store_port(self) -> None:
        from glassbox.ports.retention import EvidenceRetentionStore

        assert isinstance(fake_store(), EvidenceRetentionStore)

    def test_segment_state_is_none_for_an_unknown_segment(self) -> None:
        store = fake_store()
        assert store.segment_state("no-such-segment") is None

    def test_segment_state_reports_tenant_and_unsealed_by_default(self) -> None:
        store, segment_id = self._seeded_store()
        state = store.segment_state(segment_id)
        assert state is not None
        assert state.tenant_id == "acme"
        assert state.sealed_at is None
        assert state.last_seq is None

    def test_segment_leaves_are_returned_in_sequence_order(self) -> None:
        store, segment_id = self._seeded_store(count=5)
        leaves = store.segment_leaves(segment_id)
        assert [leaf.seq for leaf in leaves] == [0, 1, 2, 3, 4]

    def test_segment_leaves_respects_before_seq(self) -> None:
        store, segment_id = self._seeded_store(count=5)
        leaves = store.segment_leaves(segment_id, before_seq=3)
        assert [leaf.seq for leaf in leaves] == [0, 1, 2]

    def test_seal_then_purge_through_the_real_sealer(self) -> None:
        from glassbox.adapters.outbound.worm import InMemoryWormAnchorStore
        from glassbox.app.sealer import SegmentSealer

        store, segment_id = self._seeded_store(count=5)
        signer = LocalMacSigner(key_id="test.key", key=b"\x11" * 32)
        sealer = SegmentSealer(retention=store, anchors=InMemoryWormAnchorStore(), signer=signer)

        seal_result = sealer.seal(segment_id, before_seq=3, now=NOW)
        assert seal_result.leaves_sealed == 3

        state = store.segment_state(segment_id)
        assert state is not None
        assert state.sealed_at == NOW
        assert state.last_seq == 2
        assert state.merkle_root == seal_result.anchor.merkle_root

        purge_result = sealer.purge(segment_id, before_seq=3)
        assert purge_result.purged == 3
        assert [leaf.seq for leaf in store.segment_leaves(segment_id)] == [3, 4]

    def test_purge_before_refuses_an_unanchored_range(self) -> None:
        store, segment_id = self._seeded_store(count=5)
        with pytest.raises(EvidenceWriteError):
            store.purge_before(segment_id, before_seq=3)

    def test_purge_before_never_touches_the_bespoke_seal_columns(self) -> None:
        """The two retention strategies must stay fully independent (schema.py
        migration 5): using the port-based path leaves the bespoke
        seal_and_purge columns untouched."""
        from glassbox.adapters.outbound.worm import InMemoryWormAnchorStore
        from glassbox.app.sealer import SegmentSealer

        store, segment_id = self._seeded_store(count=5)
        signer = LocalMacSigner(key_id="test.key", key=b"\x11" * 32)
        sealer = SegmentSealer(retention=store, anchors=InMemoryWormAnchorStore(), signer=signer)
        sealer.seal(segment_id, before_seq=3, now=NOW)
        sealer.purge(segment_id, before_seq=3)

        segment = store._provider.db.segments[segment_id]  # type: ignore[attr-defined]
        assert segment["merkle_root"] is None
        assert segment["sealed_at"] is None


# --------------------------------------------------------------------------- #
# Integration (opt-in)
# --------------------------------------------------------------------------- #

POSTGRES_DSN = os.environ.get("GLASSBOX_POSTGRES_DSN", "")

_requires_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="set GLASSBOX_POSTGRES_DSN to run the Postgres integration tests",
)


@_requires_postgres
class TestPostgresIntegration:
    """Behaviour only a real server can prove."""

    @pytest.fixture
    def provider(self) -> Iterator[Any]:
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.schema import apply_migrations

        connection_provider = PsycopgConnectionProvider(POSTGRES_DSN)
        apply_migrations(connection_provider)
        with connection_provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_outcome", ())
            cursor.execute("DELETE FROM evidence_intent", ())
            cursor.execute("DELETE FROM evidence_segment", ())
        try:
            yield connection_provider
        finally:
            connection_provider.close()

    @pytest.fixture
    def store(self, provider: Any) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            provider,
            LocalMacSigner(key_id="integration.key", key=b"\x22" * 32),
            allow_test_tampering=True,
        )

    def test_migrations_are_idempotent(self, provider: Any) -> None:
        from glassbox.adapters.outbound.postgres.schema import (
            apply_migrations,
            current_schema_version,
        )

        assert apply_migrations(provider) == []
        assert current_schema_version(provider) == SCHEMA_VERSION

    def test_update_is_rejected_by_the_database_itself(
        self, store: PostgresEvidenceStore, provider: Any
    ) -> None:
        """The guarantee must not depend on the application behaving."""
        store.append_intent(make_intent())
        with pytest.raises(Exception):
            with provider.transaction() as cursor:
                cursor.execute("UPDATE evidence_intent SET tenant_id = 'evilcorp'", ())

    def test_delete_without_the_retention_setting_is_rejected(
        self, store: PostgresEvidenceStore, provider: Any
    ) -> None:
        store.append_intent(make_intent())
        with pytest.raises(Exception):
            with provider.transaction() as cursor:
                cursor.execute("DELETE FROM evidence_intent", ())

    def test_concurrent_appenders_are_serialised_by_the_row_lock(
        self, store: PostgresEvidenceStore
    ) -> None:
        receipts: List[Any] = []
        lock = threading.Lock()

        def append(index: int) -> None:
            receipt = store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
            with lock:
                receipts.append(receipt)

        with __import__("concurrent.futures", fromlist=["ThreadPoolExecutor"]).ThreadPoolExecutor(
            max_workers=8
        ) as pool:
            list(pool.map(append, range(50)))

        assert sorted(receipt.seq for receipt in receipts) == list(range(50))

    def test_a_forged_row_is_detected_on_a_real_server(self, store: PostgresEvidenceStore) -> None:
        from glassbox.domain.action import Exposure

        for index in range(3):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.verify(SEGMENT, now=NOW).status is IntegrityStatus.INTACT

        from tests.test_domain import make_action

        store.tamper_for_test(
            SEGMENT,
            1,
            make_intent(
                decision_id="decision-0001",
                action=make_action(exposure=Exposure(monetary=999_999_999.0)),
            ),
        )
        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.BROKEN
        assert report.first_broken_seq == 1

    def test_retention_purge_keeps_the_segment_verifiable(
        self, store: PostgresEvidenceStore
    ) -> None:
        for index in range(5):
            store.append_intent(make_intent(decision_id=f"decision-{index:04d}"))
        assert store.seal_and_purge(SEGMENT, before_seq=2) == 2
        report = store.verify(SEGMENT, now=NOW)
        assert report.status is IntegrityStatus.SEALED_PURGED
        assert report.is_acceptable is True


@_requires_postgres
class TestPostgresRetentionStoreIntegration:
    """The EvidenceRetentionStore port (GB-007), proven against a real server.

    Mirrors TestPostgresRetentionStore's fake-backed assertions exactly, so a
    real-server run and a fake-backed run are checked against the same
    specification -- the two cannot silently drift apart.
    """

    @pytest.fixture
    def provider(self) -> Iterator[Any]:
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.schema import apply_migrations

        connection_provider = PsycopgConnectionProvider(POSTGRES_DSN)
        apply_migrations(connection_provider)
        with connection_provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_outcome", ())
            cursor.execute("DELETE FROM evidence_intent", ())
            cursor.execute("DELETE FROM evidence_segment", ())
        try:
            yield connection_provider
        finally:
            connection_provider.close()

    @pytest.fixture
    def store(self, provider: Any) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            provider,
            LocalMacSigner(key_id="integration.key", key=b"\x22" * 32),
            allow_test_tampering=True,
        )

    def _seed(self, store: PostgresEvidenceStore, *, segment_id: str, count: int = 5) -> None:
        for index in range(count):
            store.append_intent(
                make_intent(
                    decision_id=f"decision-ret-int-{index}",
                    segment_id=segment_id,
                    action=make_action(idempotency_key=f"idem-ret-int-{index}"),
                )
            )

    def test_conforms_to_the_port_on_a_real_connection(self, store: PostgresEvidenceStore) -> None:
        from glassbox.ports.retention import EvidenceRetentionStore

        assert isinstance(store, EvidenceRetentionStore)

    def test_segment_state_round_trips_through_real_sql(self, store: PostgresEvidenceStore) -> None:
        self._seed(store, segment_id=SEGMENT)
        state = store.segment_state(SEGMENT)
        assert state is not None
        assert state.tenant_id == "acme"
        assert state.sealed_at is None

    def test_segment_leaves_round_trip_in_order(self, store: PostgresEvidenceStore) -> None:
        self._seed(store, segment_id=SEGMENT, count=5)
        leaves = store.segment_leaves(SEGMENT)
        assert [leaf.seq for leaf in leaves] == [0, 1, 2, 3, 4]
        assert [leaf.seq for leaf in store.segment_leaves(SEGMENT, before_seq=3)] == [0, 1, 2]

    def test_seal_then_purge_through_the_real_sealer_on_a_real_server(
        self, store: PostgresEvidenceStore
    ) -> None:
        """Uses the real wall clock for ``now``, not the fixed ``NOW`` fixture
        constant: ``opened_at`` here comes from the server's own ``now()`` at
        insert time, and a sealed_at that precedes it is rightly rejected."""
        import time as _time

        from glassbox.adapters.outbound.worm import InMemoryWormAnchorStore
        from glassbox.app.sealer import SegmentSealer

        self._seed(store, segment_id=SEGMENT, count=5)
        real_now = _time.time()
        signer = LocalMacSigner(key_id="integration.key", key=b"\x22" * 32)
        sealer = SegmentSealer(retention=store, anchors=InMemoryWormAnchorStore(), signer=signer)

        seal_result = sealer.seal(SEGMENT, before_seq=3, now=real_now)
        assert seal_result.leaves_sealed == 3

        state = store.segment_state(SEGMENT)
        assert state is not None
        assert state.sealed_at == pytest.approx(real_now, abs=1e-3)
        assert state.last_seq == 2
        assert state.merkle_root == seal_result.anchor.merkle_root

        purge_result = sealer.purge(SEGMENT, before_seq=3)
        assert purge_result.purged == 3
        assert [leaf.seq for leaf in store.segment_leaves(SEGMENT)] == [3, 4]

        # A purged-and-sealed segment reports SEALED_PURGED (not INTACT), and
        # that status is itself the acceptable one (IntegrityStatus.is_acceptable).
        report = store.verify(SEGMENT, now=real_now)
        assert report.status is IntegrityStatus.SEALED_PURGED
        assert report.is_acceptable is True

    def test_purge_before_refuses_an_unanchored_range_on_a_real_server(
        self, store: PostgresEvidenceStore
    ) -> None:
        self._seed(store, segment_id=SEGMENT, count=5)
        with pytest.raises(EvidenceWriteError):
            store.purge_before(SEGMENT, before_seq=3)

    def test_the_two_retention_strategies_do_not_collide_on_a_real_server(
        self, store: PostgresEvidenceStore
    ) -> None:
        """schema.py migration 5's whole reason to exist, proven live."""
        import time as _time

        from glassbox.adapters.outbound.worm import InMemoryWormAnchorStore
        from glassbox.app.sealer import SegmentSealer

        self._seed(store, segment_id=SEGMENT, count=5)
        signer = LocalMacSigner(key_id="integration.key", key=b"\x22" * 32)
        sealer = SegmentSealer(retention=store, anchors=InMemoryWormAnchorStore(), signer=signer)
        sealer.seal(SEGMENT, before_seq=3, now=_time.time())
        sealer.purge(SEGMENT, before_seq=3)

        with store._provider.transaction() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT merkle_root, sealed_at FROM evidence_segment WHERE segment_id = %s",
                (SEGMENT,),
            )
            merkle_root, sealed_at = cursor.fetchone()
        assert merkle_root is None
        assert sealed_at is None


@_requires_postgres
class TestPostgresPartitioning:
    """Physical time partitioning of evidence_intent (migrations 7-8)."""

    @pytest.fixture
    def provider(self) -> Iterator[Any]:
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.schema import apply_migrations

        connection_provider = PsycopgConnectionProvider(POSTGRES_DSN)
        apply_migrations(connection_provider)
        with connection_provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_outcome", ())
            cursor.execute("DELETE FROM evidence_intent", ())
            cursor.execute("DELETE FROM evidence_segment", ())
        try:
            yield connection_provider
        finally:
            connection_provider.close()

    @pytest.fixture
    def store(self, provider: Any) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            provider, LocalMacSigner(key_id="integration.key", key=b"\x22" * 32)
        )

    def test_evidence_intent_is_a_partitioned_table(self, provider: Any) -> None:
        with provider.transaction() as cursor:
            cursor.execute(
                "SELECT relkind FROM pg_class WHERE relname = 'evidence_intent'", ()
            )
            (relkind,) = cursor.fetchone()
        assert relkind == "p"

    def test_ensure_monthly_partitions_creates_the_expected_window(self, provider: Any) -> None:
        from datetime import datetime, timezone

        from glassbox.adapters.outbound.postgres.schema import ensure_monthly_partitions

        covered = ensure_monthly_partitions(
            provider,
            now=datetime(2030, 3, 15, tzinfo=timezone.utc),
            months_back=1,
            months_ahead=2,
        )
        assert covered == [
            "evidence_intent_y2030_m02",
            "evidence_intent_y2030_m03",
            "evidence_intent_y2030_m04",
            "evidence_intent_y2030_m05",
        ]
        with provider.transaction() as cursor:
            for partition_name in covered:
                cursor.execute("SELECT to_regclass(%s) IS NOT NULL", (partition_name,))
                assert cursor.fetchone() == (True,)

    def test_a_row_inside_the_window_lands_in_its_month_partition(
        self, provider: Any, store: PostgresEvidenceStore
    ) -> None:
        import time as _time

        from glassbox.adapters.outbound.postgres.schema import ensure_monthly_partitions

        ensure_monthly_partitions(provider)
        real_now = _time.time()
        store.append_intent(
            make_intent(decision_id="decision-partition-1", created_at=real_now)
        )
        with provider.transaction() as cursor:
            cursor.execute(
                "SELECT tableoid::regclass::text FROM evidence_intent WHERE decision_id = %s",
                ("decision-partition-1",),
            )
            (table_name,) = cursor.fetchone()
        assert table_name != "evidence_intent_default"
        assert table_name.startswith("evidence_intent_y")

    def test_a_row_outside_the_window_falls_to_the_default_partition(
        self, store: PostgresEvidenceStore
    ) -> None:
        store.append_intent(make_intent(decision_id="decision-partition-2"))  # uses fixed NOW
        with store._provider.transaction() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT tableoid::regclass::text FROM evidence_intent WHERE decision_id = %s",
                ("decision-partition-2",),
            )
            (table_name,) = cursor.fetchone()
        assert table_name == "evidence_intent_default"

    def test_partition_pruning_excludes_irrelevant_partitions(self, provider: Any) -> None:
        from glassbox.adapters.outbound.postgres.schema import ensure_monthly_partitions

        ensure_monthly_partitions(provider)
        with provider.transaction() as cursor:
            cursor.execute(
                "EXPLAIN SELECT * FROM evidence_intent "
                "WHERE created_at > now() - interval '1 day'"
            )
            plan = "\n".join(row[0] for row in cursor.fetchall())
        assert "Subplans Removed" in plan

    def test_the_append_only_trigger_still_applies_after_partitioning(
        self, store: PostgresEvidenceStore
    ) -> None:
        store.append_intent(make_intent())
        with pytest.raises(Exception):
            with store._provider.transaction() as cursor:  # type: ignore[attr-defined]
                cursor.execute("UPDATE evidence_intent SET tenant_id = 'evilcorp'", ())

    def test_the_backup_table_no_longer_blocks_segment_deletion(self, provider: Any) -> None:
        """The whole point of migration 8: the frozen backup's FK must not
        prevent legitimate segment cleanup after partitioning."""
        with provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_segment", ())  # must not raise


@_requires_postgres
class TestPostgresOutcomePartitioning:
    """Physical time partitioning of evidence_outcome (migration 9)."""

    @pytest.fixture
    def provider(self) -> Iterator[Any]:
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.schema import apply_migrations

        connection_provider = PsycopgConnectionProvider(POSTGRES_DSN)
        apply_migrations(connection_provider)
        with connection_provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_outcome", ())
            cursor.execute("DELETE FROM evidence_intent", ())
            cursor.execute("DELETE FROM evidence_segment", ())
        try:
            yield connection_provider
        finally:
            connection_provider.close()

    @pytest.fixture
    def store(self, provider: Any) -> PostgresEvidenceStore:
        return PostgresEvidenceStore(
            provider, LocalMacSigner(key_id="integration.key", key=b"\x22" * 32)
        )

    def test_evidence_outcome_is_a_partitioned_table(self, provider: Any) -> None:
        with provider.transaction() as cursor:
            cursor.execute(
                "SELECT relkind FROM pg_class WHERE relname = 'evidence_outcome'", ()
            )
            (relkind,) = cursor.fetchone()
        assert relkind == "p"

    def test_an_outcome_lands_in_its_month_partition_by_completed_at(
        self, provider: Any, store: PostgresEvidenceStore
    ) -> None:
        import time as _time

        from glassbox.adapters.outbound.postgres.schema import ensure_monthly_partitions
        from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
        from glassbox.domain.evidence import OutcomeRecord

        ensure_monthly_partitions(provider, table="evidence_outcome")
        real_now = _time.time()
        receipt = store.append_intent(make_intent(decision_id="decision-outcome-1"))
        store.append_outcome(
            receipt,
            OutcomeRecord(
                decision_id="decision-outcome-1",
                outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=real_now),
            ),
        )
        with provider.transaction() as cursor:
            cursor.execute(
                "SELECT tableoid::regclass::text FROM evidence_outcome WHERE decision_id = %s",
                ("decision-outcome-1",),
            )
            (table_name,) = cursor.fetchone()
        assert table_name != "evidence_outcome_default"
        assert table_name.startswith("evidence_outcome_y")

    def test_retrying_the_same_outcome_is_still_idempotent(
        self, store: PostgresEvidenceStore
    ) -> None:
        """The whole point of switching the ON CONFLICT target: a retry that
        supplies the same (decision_id, completed_at) must still no-op, not
        raise and not duplicate."""
        from glassbox.domain.decision import ExecutionOutcome, ExecutionStatus
        from glassbox.domain.evidence import OutcomeRecord

        receipt = store.append_intent(make_intent(decision_id="decision-outcome-2"))
        record = OutcomeRecord(
            decision_id="decision-outcome-2",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=NOW),
        )
        store.append_outcome(receipt, record)
        store.append_outcome(receipt, record)  # retry: must not raise

        with store._provider.transaction() as cursor:  # type: ignore[attr-defined]
            cursor.execute(
                "SELECT count(*) FROM evidence_outcome WHERE decision_id = %s",
                ("decision-outcome-2",),
            )
            (count,) = cursor.fetchone()
        assert count == 1

    def test_partition_pruning_excludes_irrelevant_partitions(self, provider: Any) -> None:
        from glassbox.adapters.outbound.postgres.schema import ensure_monthly_partitions

        ensure_monthly_partitions(provider, table="evidence_outcome")
        with provider.transaction() as cursor:
            cursor.execute(
                "EXPLAIN SELECT * FROM evidence_outcome "
                "WHERE completed_at > now() - interval '1 day'"
            )
            plan = "\n".join(row[0] for row in cursor.fetchall())
        assert "Subplans Removed" in plan

