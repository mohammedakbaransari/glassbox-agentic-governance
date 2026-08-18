"""Delta Silver: joined, typed, deduplicated intent+outcome (GB-031).

Silver merges Bronze intent and outcome rows on ``decision_id``, so a decision
that dispatched has one row carrying both its authorisation facts and its
terminal outcome -- Bronze alone always has them as two separate append-only
streams, because the source (`evidence_outcome`) is written later and
separately from `evidence_intent` by design (GB-005), to keep the intent write
alone on the critical path.

A decision whose outcome has not yet arrived merges as an intent-only Silver
row (``status`` absent); a later run's merge updates that same row in place
once the outcome lands -- ``when_matched_update_all()``, not a second insert.
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from glassbox.adapters.outbound.delta.bronze import _require_deltalake

__all__ = ["DeltaSilverMerger"]


def _outcome_only_columns() -> List[Tuple[str, Any]]:
    """Outcome-side columns absent from every intent row, with explicit types.

    Used to pad an intent-only batch so its Arrow schema always matches a
    batch that does carry a joined outcome, regardless of which kind of batch
    happens to be the very first one Silver ever sees.
    """
    import pyarrow as pa

    return [
        ("status", pa.string()),
        ("completed_at", pa.float64()),
        ("result_digest", pa.string()),
        ("error_class", pa.string()),
    ]


class DeltaSilverMerger:
    """Merges Bronze intent + outcome Delta tables into one Silver table.

    Args:
        intent_bronze_path: Bronze table of flattened
            :class:`~glassbox.domain.evidence.IntentRecord` rows.
        outcome_bronze_path: Bronze table of flattened
            :class:`~glassbox.domain.evidence.OutcomeRecord` rows.
        silver_path: Destination table.
        partition_by: Silver partition columns.
    """

    __slots__ = ("_intent_path", "_outcome_path", "_silver_path", "_partition_by")

    def __init__(
        self,
        intent_bronze_path: str,
        outcome_bronze_path: str,
        silver_path: str,
        *,
        partition_by: Sequence[str] = ("tenant_id",),
    ) -> None:
        _require_deltalake()
        self._intent_path = intent_bronze_path
        self._outcome_path = outcome_bronze_path
        self._silver_path = silver_path
        self._partition_by = tuple(partition_by)

    def merge(self) -> Dict[str, int]:
        """Read both Bronze tables, join on ``decision_id``, merge into Silver.

        Returns:
            ``{"inserted": n, "updated": n}`` -- the exact
            :meth:`DeltaTable.merge` counters, so a caller can assert on them
            directly rather than re-deriving them from a row count.
        """
        import pyarrow as pa
        from deltalake import DeltaTable, write_deltalake
        from deltalake.exceptions import TableNotFoundError

        intent_table = DeltaTable(self._intent_path).to_pyarrow_table()
        try:
            outcome_table = DeltaTable(self._outcome_path).to_pyarrow_table()
        except TableNotFoundError:
            outcome_table = None

        if outcome_table is not None and outcome_table.num_rows:
            # Bronze intent and outcome rows both carry a `tenant_id` column;
            # drop the outcome side's copy so the join does not produce a
            # duplicate, ambiguous `tenant_id` field in the merged row.
            outcome_table = outcome_table.drop(["tenant_id"])
            joined: pa.Table = intent_table.join(
                outcome_table, keys="decision_id", join_type="left outer", right_suffix="_outcome"
            )
        else:
            # No outcome rows exist yet (or ever, for a still-pending action).
            # The outcome columns must still be present -- with an explicit
            # type, never inferred -- so this batch's Arrow schema matches
            # every other batch's regardless of which one happened to run
            # first. Without this, the very first Silver write (when no
            # outcome has landed yet) would permanently fix Silver's schema to
            # "no outcome columns at all", and a later batch that does have an
            # outcome could not be merged into it.
            joined = intent_table
            for name, arrow_type in _outcome_only_columns():
                joined = joined.append_column(name, pa.nulls(joined.num_rows, type=arrow_type))

        try:
            silver = DeltaTable(self._silver_path)
        except TableNotFoundError:
            write_deltalake(
                self._silver_path, joined, mode="append", partition_by=list(self._partition_by)
            )
            return {"inserted": joined.num_rows, "updated": 0}

        result = (
            silver.merge(
                source=joined,
                predicate="target.decision_id = source.decision_id",
                source_alias="source",
                target_alias="target",
            )
            .when_matched_update_all()
            .when_not_matched_insert_all()
            .execute()
        )
        return {
            "inserted": int(result["num_target_rows_inserted"]),
            "updated": int(result["num_target_rows_updated"]),
        }

    def read(self) -> Any:
        """Return the current Silver table as a ``pyarrow.Table``."""
        from deltalake import DeltaTable

        return DeltaTable(self._silver_path).to_pyarrow_table()
