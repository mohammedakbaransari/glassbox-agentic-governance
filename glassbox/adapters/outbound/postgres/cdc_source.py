"""Production Postgres logical-replication CDC source (GB-030 follow-up).

Polls a logical replication slot through wal2json's SQL interface
(``pg_logical_slot_get_changes``) rather than the low-level streaming
replication protocol -- this fits :class:`~glassbox.adapters.outbound.delta.cdc_consumer.CdcConsumer`'s
own poll-based design exactly, and stays on the same
:class:`~glassbox.adapters.outbound.postgres.driver.ConnectionProvider`/
``Cursor`` seam every other Postgres adapter in this package uses, so no
special ``replication=`` connection mode or extra driver capability is
required.

Requires the server running with ``wal_level = logical`` and the
``wal2json`` output plugin installed. Neither is available on this
project's own dev sandbox (``wal_level = replica``, no ``wal2json``), so
this class is verified only against a fake cursor
(``tests/test_postgres_cdc_source.py``) -- a live deployment must confirm
the slot can actually be created and read before relying on it.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from glassbox.adapters.outbound.delta.rows import (
    intent_evidence_dict_to_bronze_row,
    outcome_evidence_dict_to_bronze_row,
)
from glassbox.adapters.outbound.postgres.driver import ConnectionProvider, DriverUnavailableError

__all__ = ["PostgresLogicalReplicationSource"]

_CHECK_SLOT_EXISTS = "SELECT 1 FROM pg_replication_slots WHERE slot_name = %s"
_CREATE_SLOT = "SELECT pg_create_logical_replication_slot(%s, 'wal2json')"
_GET_CHANGES = """
SELECT lsn, xid, data
  FROM pg_logical_slot_get_changes(%s, NULL, %s, 'format-version', '2', 'include-typmod', 'false')
"""

_INTENT_TABLE = "public.evidence_intent"
_OUTCOME_TABLE = "public.evidence_outcome"


class PostgresLogicalReplicationSource:
    """A :class:`~glassbox.adapters.outbound.delta.cdc_consumer.ChangeEventSource`
    backed by a real Postgres logical replication slot, for the
    ``evidence_intent``/``evidence_outcome`` streams specifically.

    Args:
        provider: Ordinary SQL connection provider -- wal2json changes are read
            via ``pg_logical_slot_get_changes``, not the binary replication
            protocol, so a plain connection is all this needs.
        slot_name: Logical replication slot name, created on first use if it
            does not already exist.
        stream: ``"intent"`` or ``"outcome"``, selecting both the source table
            filtered to and the Bronze row shape produced.
    """

    __slots__ = ("_provider", "_slot_name", "_stream", "_table", "_ensured")

    def __init__(self, provider: ConnectionProvider, *, slot_name: str, stream: str) -> None:
        if stream not in ("intent", "outcome"):
            raise ValueError(f"stream must be 'intent' or 'outcome', got {stream!r}")
        self._provider = provider
        self._slot_name = slot_name
        self._stream = stream
        self._table = _INTENT_TABLE if stream == "intent" else _OUTCOME_TABLE
        self._ensured = False

    def poll(self, *, since_checkpoint: Optional[str], limit: int) -> Iterable[Dict[str, Any]]:
        """Return up to ``limit`` Bronze-shaped events strictly after ``since_checkpoint``.

        The slot itself already tracks the read position server-side (each
        call consumes and advances it); ``since_checkpoint`` is honoured too,
        defensively, so a stale/duplicated checkpoint can never replay an
        already-yielded event even if the slot were ever recreated.

        Raises:
            DriverUnavailableError: If the slot cannot be created or read.
        """
        self._ensure_slot()
        floor = int(since_checkpoint) if since_checkpoint is not None else -1
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_GET_CHANGES, (self._slot_name, limit))
                rows = cursor.fetchall()
        except DriverUnavailableError:
            raise
        except Exception as exc:
            raise DriverUnavailableError(
                "could not read the logical replication slot",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc
        return list(self._events_from_rows(rows, floor=floor))

    def _ensure_slot(self) -> None:
        """Create the slot once, lazily -- idempotent across process restarts."""
        if self._ensured:
            return
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_CHECK_SLOT_EXISTS, (self._slot_name,))
                exists = cursor.fetchone() is not None
                if not exists:
                    cursor.execute(_CREATE_SLOT, (self._slot_name,))
        except DriverUnavailableError:
            raise
        except Exception as exc:
            raise DriverUnavailableError(
                "could not create the logical replication slot",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc
        self._ensured = True

    def _events_from_rows(self, rows: Iterable[Any], *, floor: int) -> Iterator[Dict[str, Any]]:
        for lsn, _xid, data in rows:
            lsn_value = _lsn_to_int(lsn)
            if lsn_value <= floor:
                continue
            change = json.loads(data) if isinstance(data, str) else data
            if change.get("action") != "I":
                continue
            if f"{change.get('schema')}.{change.get('table')}" != self._table:
                continue
            columns = {
                column["name"]: _decode_column(column) for column in change.get("columns", [])
            }
            cdc_lsn = str(lsn_value)
            if self._stream == "intent":
                yield intent_evidence_dict_to_bronze_row(
                    columns["record"], seq=columns["seq"], cdc_lsn=cdc_lsn
                )
            else:
                yield outcome_evidence_dict_to_bronze_row(
                    columns["record"], tenant_id=None, cdc_lsn=cdc_lsn
                )


def _decode_column(column: Mapping[str, Any]) -> Any:
    """wal2json emits ``jsonb``/``json`` column values as a JSON string."""
    value = column.get("value")
    if column.get("type") in ("jsonb", "json") and isinstance(value, str):
        return json.loads(value)
    return value


def _lsn_to_int(lsn: Any) -> int:
    """Postgres LSNs are ``XXXXXXXX/XXXXXXXX`` hex pairs; make them orderable."""
    high, low = str(lsn).split("/")
    return (int(high, 16) << 32) | int(low, 16)
