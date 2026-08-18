"""Batch pre-authorisation over Spark (GB-032).

**Confirmed defect this replaces.** ``adapters/spark.py`` (v1)
``GlassBoxSparkAdapter.__init__`` builds ``self._driver_pipeline =
_build_pipeline(...)`` -- a ``GovernancePipeline`` -- and ``_govern_via_udf``
closes over that instance inside a function handed to
``pyspark.sql.functions.udf``. ``GovernancePipeline`` holds ``RLock``,
``ThreadPoolExecutor``, ``Queue`` and ``WeakSet``, none of which cloudpickle can
serialise; the module's own "driver-side" comment is wrong, and
``_govern_via_map_partitions`` merely rebuilds an equally stateful pipeline once
per partition instead of once per driver.

This module never constructs a ``GovernancePipeline``, a ``Dispatcher``, a lock
or a thread pool anywhere. The only cluster-side input is an immutable,
already-signed :class:`~glassbox.domain.policy_bundle.PolicyBundle` (or
:class:`~glassbox.domain.policy_bundle.SignedPolicyBundle`), broadcast once to
every executor. Every executor-side callable here is a pure function of
``(row, bundle) -> verdict`` -- the property :func:`tests.test_spark_serializable`
enforces by literally cloudpickling each one.

This does **not** dispatch. A batch run produces pre-authorisation verdicts,
written downstream as Delta intents (GB-030/031); the one component allowed to
cause an effect is a live, single-process ``DecisionService``/``Dispatcher``,
never a Spark executor.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, Iterator, Mapping, Optional

from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.policy_bundle import PolicyBundle

__all__ = ["row_to_action", "evaluate_row", "evaluate_partition", "preauthorise_dataframe"]


def row_to_action(row: Mapping[str, Any]) -> ProposedAction:
    """Reconstruct the :class:`ProposedAction` a Bronze/Silver row represents.

    Pure: every field comes from the row itself, never from cluster-local state.
    """
    return ProposedAction(
        action=row["action"],
        resource=ResourceRef(
            kind=row["resource_kind"], id=row["resource_id"], tenant_id=row["tenant_id"]
        ),
        consequence=ConsequenceClass(row["consequence_class"]),
        exposure=Exposure(
            blast_radius=BlastRadius(row.get("blast_radius", "single")),
            monetary=row.get("exposure_monetary"),
            records=row.get("exposure_records"),
        ),
        idempotency_key=row["idempotency_key"],
    )


def evaluate_row(row: Mapping[str, Any], bundle: PolicyBundle) -> Dict[str, Any]:
    """Evaluate one row against a broadcast, immutable policy bundle.

    Returns the original row's fields plus the verdict. A row this function
    cannot even reconstruct into a valid :class:`ProposedAction` is recorded as
    a deny with the matched rule left ``None`` -- symmetric with
    :class:`~glassbox.domain.policy_bundle.PolicyBundle.matching_rule`'s own
    deny-by-default when nothing matches.
    """
    try:
        action = row_to_action(row)
    except (KeyError, ValueError, TypeError) as exc:
        return {
            **dict(row),
            "policy_effect": "deny",
            "matched_rule": None,
            "policy_bundle_id": bundle.bundle_id,
            "policy_bundle_sha256": bundle.digest(),
            "preauth_error": f"{type(exc).__name__}: {exc}",
        }

    rule = bundle.matching_rule(action)
    return {
        **dict(row),
        "policy_effect": rule.effect.value if rule is not None else "deny",
        "matched_rule": rule.name if rule is not None else None,
        "policy_bundle_id": bundle.bundle_id,
        "policy_bundle_sha256": bundle.digest(),
        "preauth_error": None,
    }


def evaluate_partition(
    rows: Iterable[Mapping[str, Any]], bundle: PolicyBundle
) -> Iterator[Dict[str, Any]]:
    """``mapPartitions`` entry point -- still a pure function of ``(rows, bundle)``.

    No pipeline, no evidence store, no dispatcher is constructed here or
    anywhere this function calls into; every row is independent, so the
    function does not even hold state *across* rows within one partition.
    """
    for row in rows:
        yield evaluate_row(row, bundle)


def preauthorise_dataframe(df: Any, bundle: PolicyBundle, *, num_partitions: Optional[int] = None):
    """Run batch pre-authorisation over a Spark DataFrame of proposed actions.

    Args:
        df: A Spark DataFrame whose rows are shaped like
            :func:`row_to_action`'s input (``action``, ``resource_kind``,
            ``resource_id``, ``tenant_id``, ``consequence_class``,
            ``idempotency_key``, and optionally ``blast_radius``,
            ``exposure_monetary``, ``exposure_records``).
        bundle: An immutable, already-signed policy bundle. Broadcast once, not
            reconstructed per partition.
        num_partitions: Optional repartition before evaluation, for tests that
            want a specific partition count.

    Returns:
        A Spark DataFrame of dicts (rows) with ``policy_effect``,
        ``matched_rule``, ``policy_bundle_id`` and ``policy_bundle_sha256``
        columns appended. Never dispatched -- writing to Delta and dispatching
        an ``ALLOW`` verdict happens downstream, off-cluster.
    """
    spark = df.sparkSession
    broadcast_bundle = spark.sparkContext.broadcast(bundle)
    source = df.repartition(num_partitions) if num_partitions else df

    def _partition_fn(rows: Iterable[Any]) -> Iterator[Dict[str, Any]]:
        return evaluate_partition((row.asDict() for row in rows), broadcast_bundle.value)

    return spark.createDataFrame(source.rdd.mapPartitions(_partition_fn))
