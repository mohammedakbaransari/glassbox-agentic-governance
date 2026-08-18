"""
GlassBox — PySpark / Spark Integration Adapter  (v1.0.0)
=========================================================
Integrates GlassBox governance with Apache Spark, PySpark,
Databricks Runtime, and Microsoft Fabric Spark.

Two integration patterns are provided:

Pattern 1 — mapPartitions (scalable, partition-local pipeline)
    Each Spark partition creates its own GovernancePipeline instance.
    Decisions are governed in parallel across the cluster.
    Best for: large batch governance jobs in Databricks / Fabric.
    (GB-040: this is now the only DataFrame governance path. The prior
    driver-side-UDF pattern closed over a GovernancePipeline inside a
    ``pyspark.sql.functions.udf``, which cloudpickle cannot serialise --
    that code path could never run on a real cluster and has been removed
    rather than fixed in place. See ``glassbox.adapters.outbound.spark.batch_preauth``
    for the v2 replacement.)

Pattern 2 — Structured Streaming (real-time, micro-batch governance)
    Govern a Spark Structured Streaming source in each micro-batch via
    foreachBatch(). Each batch runs the pipeline synchronously.
    Best for: real-time AI agent decision streams (Kafka, Delta, EventHub).

All patterns:
  - Zero mandatory dependencies beyond PySpark
  - Thread-safe: each executor/partition gets its own pipeline instance
  - Platform-safe: log paths auto-resolved for DBFS, Lakehouse, local
  - Policy-as-code: custom policies registered per batch/partition

Usage (quick start):
    from glassbox.adapters.spark import GlassBoxSparkAdapter
    adapter = GlassBoxSparkAdapter(spark)
    result_df = adapter.govern_dataframe(decisions_df)

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Any, Callable, Dict, Iterator, List, Optional

try:
    import pyspark as _pyspark  # noqa: F401
except ImportError:
    _pyspark = None

if TYPE_CHECKING:
    import pyspark

    from glassbox.governance.models import DecisionRequest

# ── Lazy PySpark imports (only required at runtime, not import time) ─────────


def _require_spark():
    """Raise a clear error if PySpark is not available."""
    try:
        import pyspark  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "PySpark is required for GlassBoxSparkAdapter. "
            "Install with: pip install pyspark  "
            "(already available on Databricks and Microsoft Fabric)"
        ) from exc


# ── Schema for governance result columns ─────────────────────────────────────

_RESULT_SCHEMA_STR = """
    decision_id     STRING,
    agent_id        STRING,
    decision_type   STRING,
    final_status    STRING,
    risk_score      DOUBLE,
    risk_level      STRING,
    policy_violations ARRAY<STRING>,
    policy_warnings   ARRAY<STRING>,
    blocked         BOOLEAN,
    circuit_breaker BOOLEAN,
    latency_ms      DOUBLE,
    message         STRING
"""


def _build_pipeline(log_dir: Optional[str] = None, echo: bool = False):
    """Build a GovernancePipeline instance — safe to call inside executors."""
    import sys

    from glassbox.adapters.platforms import auto_detect_adapter

    # Ensure glassbox is importable inside the executor
    # On Databricks/Fabric this is handled automatically if the package is
    # installed on the cluster; on standalone Spark add it to sys.path if needed.
    from glassbox.governance.pipeline import GovernancePipeline

    adapter = auto_detect_adapter()
    cfg = adapter.get_config()
    if log_dir:
        cfg["log_dir"] = log_dir
    cfg["echo"] = echo
    return GovernancePipeline(**cfg)


def _row_to_response(row_dict: Dict[str, Any]) -> DecisionRequest:
    """
    Convert a Spark Row dict to a DecisionRequest and run governance.
    Returns a flat dict of governance results suitable for Spark schema.

    Expected row fields:
        agent_id        (str)   — required
        decision_type   (str)   — required, e.g. "procurement"
        payload_json    (str)   — required, JSON-encoded payload dict
        confidence      (float) — optional, default 1.0
        environment     (str)   — optional, default "production"
        agent_chain_json(str)   — optional, JSON-encoded list of agent IDs
    """
    from glassbox.governance.models import DecisionContext, DecisionRequest, DecisionType
    from glassbox.governance.pipeline import GovernancePipeline

    # Parse inputs
    agent_id = str(row_dict.get("agent_id", "unknown"))
    dtype_raw = str(row_dict.get("decision_type", "custom")).lower()
    payload_json = row_dict.get("payload_json", "{}")
    confidence = row_dict.get("confidence", 1.0)
    environment = str(row_dict.get("environment", "production"))
    chain_json = row_dict.get("agent_chain_json", "[]")

    try:
        payload = json.loads(payload_json) if isinstance(payload_json, str) else payload_json
    except (json.JSONDecodeError, TypeError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}

    try:
        agent_chain = json.loads(chain_json) if isinstance(chain_json, str) else []
    except (json.JSONDecodeError, TypeError):
        agent_chain = []
    if not isinstance(agent_chain, list):
        agent_chain = []

    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 1.0

    try:
        dtype = DecisionType(dtype_raw)
    except ValueError:
        dtype = DecisionType.CUSTOM

    ctx = DecisionContext(
        environment=environment,
        confidence=confidence,
        agent_chain=agent_chain,
        source_system="spark",
    )
    request = DecisionRequest(
        agent_id=agent_id,
        decision_type=dtype,
        payload=payload,
        context=ctx,
    )

    return request


def _spark_row_factory(**kwargs):
    from pyspark.sql.types import Row

    return Row(**kwargs)


def _process_partition_rows(
    rows: Iterator,
    *,
    log_dir: Optional[str],
    policies: List,
    build_pipeline: Callable = _build_pipeline,
    row_factory: Optional[Callable[..., Any]] = None,
) -> Iterator[Any]:
    """Govern a partition and always release its local pipeline."""
    pipeline = build_pipeline(log_dir=log_dir, echo=False)
    row_factory = row_factory or _spark_row_factory
    try:
        for policy in policies:
            pipeline.policy_engine.register(policy)

        for row in rows:
            row_dict = row.asDict()
            try:
                request = _row_to_response(row_dict)
                resp = pipeline.process(request)
                yield row_factory(
                    **{
                        **row_dict,
                        "decision_id": resp.decision_id,
                        "final_status": resp.final_status.value,
                        "risk_score": resp.risk_score,
                        "risk_level": resp.risk_level.value if resp.risk_level else None,
                        "policy_violations": resp.policy_violations,
                        "blocked": resp.final_status.value == "blocked",
                        "latency_ms": resp.pipeline_latency_ms,
                    }
                )
            except Exception as exc:
                yield row_factory(
                    **{
                        **row_dict,
                        "decision_id": None,
                        "final_status": "error",
                        "risk_score": None,
                        "risk_level": None,
                        "policy_violations": [str(exc)],
                        "blocked": True,
                        "latency_ms": None,
                    }
                )
    finally:
        pipeline.shutdown()


def _batch_is_empty(batch_df) -> bool:
    """Check emptiness without forcing a full count when Spark exposes better options."""
    is_empty = getattr(batch_df, "isEmpty", None)
    if callable(is_empty):
        return bool(is_empty())
    return batch_df.limit(1).count() == 0


def _write_governed_stream_batch(adapter, batch_df, output_path: str, output_format: str) -> bool:
    """Govern and write one micro-batch, skipping empty batches."""
    if _batch_is_empty(batch_df):
        return False

    governed = adapter.govern_dataframe(batch_df, partition_mode=True)
    (
        governed.write.format(output_format)
        .mode("append")
        .option("mergeSchema", "true")
        .save(output_path)
    )
    return True


class GlassBoxSparkAdapter:
    """
    Integrates GlassBox governance with Apache Spark / PySpark.

    Parameters
    ----------
    spark       : SparkSession (required)
    log_dir     : str, optional — path to write JSONL audit logs.
                  On Databricks: "/dbfs/tmp/glassbox/logs"
                  On Fabric:     "/lakehouse/default/Files/glassbox/logs"
                  On local:      "./glassbox_logs"
    echo        : bool — print governance summaries (development only)
    policies    : list of Policy objects to register on each pipeline instance

    Thread-safety
    -------------
    Each Spark executor/partition creates its own GovernancePipeline. There
    is no shared state across executors, and (GB-040) there is no driver-side
    pipeline either: ``_govern_via_udf`` used to build one GovernancePipeline
    in ``__init__`` and close over it inside a ``pyspark.sql.functions.udf``,
    which cloudpickle cannot serialise (it holds an ``RLock``,
    ``ThreadPoolExecutor``, ``Queue`` and ``WeakSet`` internally) -- that code
    path could not run on a real cluster. It has been removed rather than
    fixed in place; :meth:`govern_dataframe` now always uses the
    ``mapPartitions`` pattern, where every executor builds its own pipeline
    locally and nothing needs to be serialised across the wire. See
    ``glassbox.adapters.outbound.spark.batch_preauth`` for the v2 replacement,
    which is a set of pure, cloudpickle-safe functions (no pipeline object at
    all) proven serialisable by ``tests/test_spark_serializable.py``.
    """

    def __init__(
        self,
        spark,
        log_dir: Optional[str] = None,
        echo: bool = False,
        policies: Optional[List] = None,
    ):
        _require_spark()
        self.spark = spark
        self.log_dir = log_dir or self._resolve_log_dir()
        self.echo = echo
        self.policies = policies or []

    # ── Log path auto-resolution ──────────────────────────────────────────────

    def _resolve_log_dir(self) -> str:
        from glassbox.adapters.platforms import auto_detect_adapter

        return auto_detect_adapter()._log_dir() or "./glassbox_logs"

    def govern_dataframe(self, df, partition_mode: bool = True) -> "pyspark.sql.DataFrame":
        """
        Govern every row of a Spark DataFrame via ``mapPartitions``: every
        executor partition builds its own GovernancePipeline locally.

        Parameters
        ----------
        df              : Spark DataFrame with columns:
                          agent_id, decision_type, payload_json,
                          confidence (optional), environment (optional)
        partition_mode  : Removed in GB-040 (accepted for backward
                          compatibility; this method always uses
                          mapPartitions now, since the driver-side UDF path
                          it used to select could not run on a real cluster
                          -- see the class docstring).

        Returns
        -------
        DataFrame with original columns + governance result columns
        """
        return self._govern_via_map_partitions(df)

    # ── mapPartitions (executor-local, scalable) ──────────────────────────────

    def _govern_via_map_partitions(self, df) -> "pyspark.sql.DataFrame":
        """
        Parallel governance via mapPartitions.
        Each executor partition creates its own GovernancePipeline instance.
        """
        from pyspark.sql.types import (
            ArrayType,
            BooleanType,
            DoubleType,
            StringType,
            StructField,
            StructType,
        )

        out_schema = StructType(
            df.schema.fields
            + [
                StructField("decision_id", StringType(), True),
                StructField("final_status", StringType(), True),
                StructField("risk_score", DoubleType(), True),
                StructField("risk_level", StringType(), True),
                StructField("policy_violations", ArrayType(StringType()), True),
                StructField("blocked", BooleanType(), True),
                StructField("latency_ms", DoubleType(), True),
            ]
        )

        log_dir = self.log_dir
        policies = self.policies

        def process_partition(rows: Iterator):
            return _process_partition_rows(rows, log_dir=log_dir, policies=policies)

        return df.rdd.mapPartitions(process_partition).toDF(out_schema)

    # ── Pattern 3: Structured Streaming ──────────────────────────────────────

    def govern_stream(
        self,
        stream_df,
        output_path: str,
        checkpoint: str,
        output_format: str = "delta",
        trigger_secs: int = 10,
    ) -> "pyspark.sql.streaming.StreamingQuery":
        """
        Govern a Spark Structured Streaming DataFrame.

        Each micro-batch is governed in full before being written to output.
        Blocked decisions are written with final_status="blocked".

        Parameters
        ----------
        stream_df     : Streaming DataFrame
        output_path   : Destination path (Delta table, parquet, etc.)
        checkpoint    : Checkpoint location
        output_format : "delta" (Databricks/Fabric) or "parquet"
        trigger_secs  : Micro-batch interval in seconds
        """
        from pyspark.sql.streaming import DataStreamWriter

        adapter_ref = self

        def process_batch(batch_df, batch_id: int):
            _write_governed_stream_batch(adapter_ref, batch_df, output_path, output_format)

        return (
            stream_df.writeStream.foreachBatch(process_batch)
            .option("checkpointLocation", checkpoint)
            .trigger(processingTime=f"{trigger_secs} seconds")
            .start()
        )

    # ── Utility: create sample Spark DataFrame ────────────────────────────────

    def create_sample_decisions_df(self, n: int = 10) -> "pyspark.sql.DataFrame":
        """
        Create a sample Spark DataFrame of AI decisions for testing.
        Each row represents one AI-generated operational decision.
        """
        import json as _json

        records: List[Dict[str, Any]] = [
            # Procurement decisions
            {
                "agent_id": "procurement_agent",
                "decision_type": "procurement",
                "payload_json": _json.dumps(
                    {"amount": 5000, "supplier_id": "SUP-001", "category": "hardware"}
                ),
            },
            {
                "agent_id": "procurement_agent",
                "decision_type": "procurement",
                "payload_json": _json.dumps(
                    {"amount": 750000, "supplier_id": "UNKNOWN", "category": "semiconductors"}
                ),
            },
            # Pricing decisions
            {
                "agent_id": "pricing_agent",
                "decision_type": "pricing",
                "payload_json": _json.dumps(
                    {
                        "new_price": 110.0,
                        "previous_price": 100.0,
                        "product_id": "P1",
                        "reason": "demand",
                    }
                ),
            },
            {
                "agent_id": "pricing_agent",
                "decision_type": "pricing",
                "payload_json": _json.dumps(
                    {"new_price": 500.0, "previous_price": 100.0, "product_id": "P2"}
                ),
            },
            # Financial decisions
            {
                "agent_id": "treasury_agent",
                "decision_type": "financial",
                "payload_json": _json.dumps(
                    {"amount": 50000, "destination_account": "ACC-001", "reference": "REF-001"}
                ),
            },
            {
                "agent_id": "treasury_agent",
                "decision_type": "financial",
                "payload_json": _json.dumps(
                    {"amount": 2000000, "destination_account": "ACC-002", "reference": "REF-002"}
                ),
            },
            # IT operations
            {
                "agent_id": "devops_agent",
                "decision_type": "it_ops",
                "payload_json": _json.dumps({"action": "restart_service", "target": "api-gateway"}),
            },
            {
                "agent_id": "devops_agent",
                "decision_type": "it_ops",
                "payload_json": _json.dumps({"action": "delete_database", "target": "prod-db"}),
            },
            # Inventory decisions
            {
                "agent_id": "inventory_agent",
                "decision_type": "inventory",
                "payload_json": _json.dumps({"quantity": 500, "product_id": "SKU-001"}),
            },
            {
                "agent_id": "inventory_agent",
                "decision_type": "inventory",
                "payload_json": _json.dumps({"quantity": 50000, "product_id": "SKU-002"}),
            },
        ]
        # Repeat to reach n rows
        while len(records) < n:
            records += records
        records = records[:n]
        for r in records:
            r.setdefault("confidence", 0.95)
            r.setdefault("environment", "production")
            r.setdefault("agent_chain_json", "[]")
        return self.spark.createDataFrame(records)

    # ── Governance stats as Spark DataFrame ──────────────────────────────────

    def stats_as_dataframe(self) -> "pyspark.sql.DataFrame":
        """
        Not implemented: there is no driver-side pipeline left to report on.
        Each partition shuts down its own pipeline immediately after use
        (see ``_process_partition_rows``), so there is no single "driver-side"
        stats object left to report -- aggregate the ``latency_ms`` /
        ``final_status`` output columns from :meth:`govern_dataframe` instead.
        """
        raise NotImplementedError(
            "GlassBoxSparkAdapter.stats_as_dataframe() is not supported: there "
            "is no driver-side pipeline. Aggregate the output "
            "columns of govern_dataframe() instead."
        )

    def shutdown(self) -> None:
        """
        No-op. There is no driver-side pipeline to release:
        every partition builds and shuts down its own pipeline within
        ``_process_partition_rows``. Kept for backward compatibility so
        existing call sites do not need to change.
        """
        return None
