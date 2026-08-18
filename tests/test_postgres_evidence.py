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
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

import pytest

from glassbox.adapters.outbound.memory.signing import LocalMacSigner
from glassbox.adapters.outbound.postgres.driver import DriverUnavailableError
from glassbox.adapters.outbound.postgres.evidence import PostgresEvidenceStore
from glassbox.adapters.outbound.postgres.schema import (
    MIGRATIONS,
    RETENTION_PURGE_GUC,
    SCHEMA_VERSION,
)
from glassbox.domain.errors import EvidenceWriteError
from glassbox.domain.evidence import GENESIS_PREV_HASH, IntegrityStatus
from tests.conformance_evidence import SEGMENT, EvidenceStoreConformance
from tests.test_domain import NOW, make_intent

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
            ("FROM evidence_segment WHERE segment_id = %s FOR UPDATE", self._lock_segment),
            ("FROM evidence_intent WHERE decision_id = %s", self._select_by_decision),
            ("INSERT INTO evidence_intent", self._insert_intent),
            ("UPDATE evidence_segment SET last_seq", self._advance_segment),
            ("INSERT INTO evidence_outcome", self._insert_outcome),
            ("purged_before_seq, sealed_at, merkle_root", self._select_segment_state),
            ("ORDER BY seq ASC", self._select_chain),
            ("ORDER BY seq DESC", self._select_anchor),
            ("DELETE FROM evidence_intent", self._delete_purged),
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
                "last_seq": -1,
                "last_hash": bytes(genesis),
                "purged_before_seq": 0,
                "sealed_at": None,
                "merkle_root": None,
                "worm_anchor_id": None,
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
