"""Delta Bronze writer (GB-030).

Append-only landing zone for CDC'd evidence, exactly-once by ``decision_id``.
Deliberately not a Spark job: routing "append a CDC row" through a JVM cluster
reintroduces a heavy, stateful runtime onto the evidence path for logic this
simple, which is the opposite of what confining PySpark to genuine batch
analytics (GB-032) is meant to achieve. This module uses ``deltalake``
(delta-rs), which needs no JVM at all.

Exactly-once is a real ``MERGE ... WHEN NOT MATCHED INSERT``, keyed on
``decision_id`` -- not an application-level "have I seen this before" cache,
which is exactly the kind of process-local state this rebuild eliminates
elsewhere (GB-011, GB-022, GB-027). Two CDC consumers racing on the same batch,
or one consumer replaying a batch after a crash, both converge on the same
Bronze row count.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Sequence

__all__ = ["DeltaBronzeWriter"]


def _require_deltalake() -> None:
    try:
        import deltalake  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "Delta Bronze/Silver requires the 'delta' extra. "
            "Install with: pip install 'glassbox-governance[delta]'"
        ) from exc


class DeltaBronzeWriter:
    """Appends CDC'd rows to a Delta Bronze table, exactly once by ``decision_id``.

    Args:
        table_path: Filesystem or object-store URI for the Bronze table.
        partition_by: Partition columns. Defaults to
            :data:`~glassbox.adapters.outbound.delta.rows.BRONZE_INTENT_PARTITION_COLUMNS`.
        schema: An explicit ``pyarrow.Schema`` for incoming rows, normally
            :func:`~glassbox.adapters.outbound.delta.rows.intent_bronze_schema`
            or :func:`~glassbox.adapters.outbound.delta.rows.outcome_bronze_schema`.
            Strongly recommended: without it, a batch where every row has
            ``None`` for some optional column makes ``pyarrow`` infer that
            column as its ``null`` type, which neither Delta's writer nor a
            later join/merge can reliably handle.
    """

    __slots__ = ("_table_path", "_partition_by", "_schema")

    def __init__(
        self,
        table_path: str,
        *,
        partition_by: Sequence[str] = ("tenant_id",),
        schema: Optional[Any] = None,
    ) -> None:
        _require_deltalake()
        self._table_path = table_path
        self._partition_by = tuple(partition_by)
        self._schema = schema

    def append_batch(self, rows: Sequence[Dict[str, Any]], *, key: str = "decision_id") -> int:
        """Insert every row in ``rows`` not already present, keyed by ``key``.

        Returns:
            The number of rows actually inserted -- fewer than ``len(rows)``
            exactly when a row's key was already present, which is the
            reconciliation acceptance criterion: replaying an overlapping CDC
            batch (the normal recovery path after a consumer restart) must
            never double the Bronze row count.
        """
        if not rows:
            return 0
        import pyarrow as pa
        from deltalake import DeltaTable, write_deltalake
        from deltalake.exceptions import TableNotFoundError

        source = pa.Table.from_pylist(list(rows), schema=self._schema)
        try:
            table = DeltaTable(self._table_path)
        except TableNotFoundError:
            write_deltalake(
                self._table_path, source, mode="append", partition_by=list(self._partition_by)
            )
            return source.num_rows

        result = (
            table.merge(
                source=source,
                predicate=f"target.{key} = source.{key}",
                source_alias="source",
                target_alias="target",
            )
            .when_not_matched_insert_all()
            .execute()
        )
        return int(result["num_target_rows_inserted"])

    def row_count(self) -> int:
        """Return the current row count. Used by reconciliation checks."""
        from deltalake import DeltaTable
        from deltalake.exceptions import TableNotFoundError

        try:
            return DeltaTable(self._table_path).to_pyarrow_table().num_rows
        except TableNotFoundError:
            return 0
