"""CDC consumer: lands change events into Delta Bronze, exactly once (GB-030).

The consumer is deliberately decoupled from *how* change events arrive.
Production wiring is Postgres logical replication (a
:class:`ChangeEventSource` backed by ``psycopg2``'s replication protocol,
lazy-imported so the dependency stays optional); tests use a plain iterable.
Neither the consumer nor :class:`~glassbox.adapters.outbound.delta.bronze.DeltaBronzeWriter`
cares which one it is talking to -- the exactly-once guarantee lives entirely
in the Bronze merge, not in this class holding a "have I seen this" cache.
"""

from __future__ import annotations

import threading
from typing import Any, Dict, Iterable, List, Optional, Protocol, Tuple, runtime_checkable

from glassbox.adapters.outbound.delta.bronze import DeltaBronzeWriter

__all__ = ["ChangeEventSource", "CheckpointStore", "InMemoryCheckpointStore", "CdcConsumer"]


@runtime_checkable
class ChangeEventSource(Protocol):
    """Yields change events since a checkpoint.

    An event is a plain ``dict`` shaped like
    :func:`~glassbox.adapters.outbound.delta.rows.intent_record_to_bronze_row`'s
    output, with a ``cdc_lsn`` field the consumer treats as an opaque, totally
    ordered cursor -- never parsed or compared by this module, only carried
    through to the next call's ``since_checkpoint``.
    """

    def poll(self, *, since_checkpoint: Optional[str], limit: int) -> Iterable[Dict[str, Any]]:
        """Return up to ``limit`` events strictly after ``since_checkpoint``."""
        ...


@runtime_checkable
class CheckpointStore(Protocol):
    """Durable cursor position per named stream."""

    def get(self, stream: str) -> Optional[str]: ...

    def set(self, stream: str, checkpoint: str) -> None: ...


class InMemoryCheckpointStore:
    """Reference checkpoint store. Development and tests only -- a restart
    loses the cursor, which is safe here only because Bronze's own exactly-once
    merge tolerates replaying events the consumer has already landed."""

    __slots__ = ("_lock", "_checkpoints")

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checkpoints: Dict[str, str] = {}

    def get(self, stream: str) -> Optional[str]:
        with self._lock:
            return self._checkpoints.get(stream)

    def set(self, stream: str, checkpoint: str) -> None:
        with self._lock:
            self._checkpoints[stream] = checkpoint


class CdcConsumer:
    """Consumes change events from one stream and lands them in Delta Bronze.

    Args:
        source: Where change events come from.
        bronze: The Bronze table to append into.
        checkpoints: Durable cursor storage.
        stream: Name of the logical stream (e.g. ``"evidence_intent"``),
            scoping the checkpoint so multiple Bronze tables can share one
            :class:`CheckpointStore`.
    """

    __slots__ = ("_source", "_bronze", "_checkpoints", "_stream")

    def __init__(
        self,
        source: ChangeEventSource,
        bronze: DeltaBronzeWriter,
        checkpoints: CheckpointStore,
        *,
        stream: str,
    ) -> None:
        self._source = source
        self._bronze = bronze
        self._checkpoints = checkpoints
        self._stream = stream

    def run_once(self, *, batch_size: int = 1_000) -> int:
        """Poll one batch and land it in Bronze.

        Returns:
            The number of rows Bronze actually inserted (never double-counts a
            replayed batch -- see
            :meth:`~glassbox.adapters.outbound.delta.bronze.DeltaBronzeWriter.append_batch`).

        The checkpoint is only advanced **after** the Bronze write succeeds: a
        crash between the two leaves the same batch to be polled and landed
        again next run, which Bronze's merge makes safe. Advancing the
        checkpoint first, by contrast, would let a crash lose a batch forever --
        the ordering here is the same "durable before advancing" discipline the
        evidence store itself uses (GB-005), applied to a cursor instead of a
        chain.
        """
        _events_seen, inserted = self._poll_and_land(batch_size=batch_size)
        return inserted

    def run_until_drained(self, *, batch_size: int = 1_000, max_batches: int = 10_000) -> int:
        """Repeatedly poll until the source has nothing left to offer.

        Returns:
            Total rows inserted across every batch. A batch that was entirely
            deduplicated (0 inserted, because every row's ``decision_id`` was
            already landed) still advances the checkpoint and does not stop the
            loop -- only an empty poll does.
        """
        total = 0
        for _ in range(max_batches):
            events_seen, inserted = self._poll_and_land(batch_size=batch_size)
            if events_seen == 0:
                break
            total += inserted
        return total

    def _poll_and_land(self, *, batch_size: int) -> Tuple[int, int]:
        """Poll one batch, land it, advance the checkpoint. Returns (events_seen, inserted)."""
        checkpoint = self._checkpoints.get(self._stream)
        events: List[Dict[str, Any]] = list(
            self._source.poll(since_checkpoint=checkpoint, limit=batch_size)
        )
        if not events:
            return 0, 0
        inserted = self._bronze.append_batch(events)
        last_checkpoint = events[-1].get("cdc_lsn")
        if last_checkpoint is not None:
            self._checkpoints.set(self._stream, last_checkpoint)
        return len(events), inserted
