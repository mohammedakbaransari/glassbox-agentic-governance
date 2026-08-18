"""Delta evidence medallion: shared row shapes (GB-030, GB-031).

Bronze mirrors the Postgres ``evidence_intent`` / ``evidence_outcome`` columns
verbatim (see :mod:`glassbox.adapters.outbound.postgres.evidence`'s
``_insert_parameters``, the single source of truth for that column set), plus
CDC metadata. No transformation happens at ingestion -- that is Silver's job --
so a Bronze row count can be reconciled against Postgres for every tenant-day
without accounting for any reshaping in between.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from glassbox.domain.evidence import IntentRecord, OutcomeRecord

__all__ = [
    "BRONZE_INTENT_PARTITION_COLUMNS",
    "intent_bronze_schema",
    "outcome_bronze_schema",
    "intent_record_to_bronze_row",
    "outcome_record_to_bronze_row",
]

#: Bronze partitioning, matching the plan's §6.4: append-only, partitioned by
#: tenant and day.
BRONZE_INTENT_PARTITION_COLUMNS = ("tenant_id", "cdc_date")


def intent_bronze_schema() -> Any:
    """Explicit Arrow schema for intent Bronze rows.

    Required, not optional: a batch whose every row has ``None`` for an
    optional field (``delegating_subject``, ``causation_id``,
    ``policy_bundle_id`` on a denial, ...) makes ``pyarrow`` infer that
    column's type as Arrow's special ``null`` type when no schema is given --
    and a ``null``-typed column cannot be joined (Silver's merge) or reliably
    merged across batches with different all-null columns. Pinning the schema
    here is what keeps Bronze/Silver correct on exactly the batches most
    likely to appear in production, not just on illustrative test data.

    ``pyarrow`` is imported lazily, inside this function, so importing this
    module does not require the ``delta`` extra -- only calling into it does,
    the same convention ``_require_spark``/``PsycopgConnectionProvider`` use
    for their own optional third-party dependencies.
    """
    import pyarrow as pa

    return pa.schema(
        [
            ("decision_id", pa.string()),
            ("segment_id", pa.string()),
            ("seq", pa.int64()),
            ("tenant_id", pa.string()),
            ("cdc_date", pa.string()),
            ("created_at", pa.float64()),
            ("agent_ref", pa.string()),
            ("agent_instance_id", pa.string()),
            ("delegating_subject", pa.string()),
            ("credential_type", pa.string()),
            ("credential_id", pa.string()),
            ("action", pa.string()),
            ("resource_kind", pa.string()),
            ("resource_id", pa.string()),
            ("consequence_class", pa.string()),
            ("idempotency_key", pa.string()),
            ("policy_bundle_id", pa.string()),
            ("policy_bundle_sha256", pa.string()),
            ("decision_effect", pa.string()),
            ("reasons_csv", pa.string()),
            ("risk_model_ver", pa.string()),
            ("risk_score", pa.float64()),
            ("risk_level", pa.string()),
            ("exposure_monetary", pa.float64()),
            ("exposure_records", pa.int64()),
            ("blast_radius", pa.string()),
            ("trace_id", pa.string()),
            ("causation_id", pa.string()),
            ("record_json", pa.string()),
            ("cdc_lsn", pa.string()),
            ("cdc_operation", pa.string()),
        ]
    )


def outcome_bronze_schema() -> Any:
    """Explicit Arrow schema for outcome Bronze rows. See :func:`intent_bronze_schema`."""
    import pyarrow as pa

    return pa.schema(
        [
            ("decision_id", pa.string()),
            ("tenant_id", pa.string()),
            ("status", pa.string()),
            ("completed_at", pa.float64()),
            ("result_digest", pa.string()),
            ("error_class", pa.string()),
            ("cdc_lsn", pa.string()),
            ("cdc_operation", pa.string()),
        ]
    )


def intent_record_to_bronze_row(
    record: IntentRecord,
    *,
    seq: int,
    cdc_lsn: str,
    cdc_operation: str = "insert",
) -> Dict[str, Any]:
    """Flatten an :class:`IntentRecord` into a Bronze row.

    Args:
        record: The durable intent record, as returned from Postgres CDC.
        seq: The record's sequence number within its evidence segment.
        cdc_lsn: The Postgres WAL LSN (or equivalent outbox cursor) this row was
            read at -- the CDC consumer's exactly-once checkpoint, carried
            through so Bronze itself is auditable against the source WAL.
        cdc_operation: Always ``"insert"`` for ``evidence_intent`` -- the table
            is append-only at the source, so CDC never sees an update or delete
            for this stream.
    """
    action = record.action
    decision = record.decision
    risk = record.risk
    principal = record.principal
    return {
        "decision_id": record.decision_id,
        "segment_id": record.segment_id,
        "seq": seq,
        "tenant_id": record.tenant_id,
        "cdc_date": _date_partition(record.created_at),
        "created_at": record.created_at,
        "agent_ref": principal.agent_ref,
        "agent_instance_id": principal.agent_instance_id,
        "delegating_subject": principal.delegating_subject,
        "credential_type": principal.credential_type.value,
        "credential_id": principal.credential_id,
        "action": action.action,
        "resource_kind": action.resource.kind,
        "resource_id": action.resource.id,
        "consequence_class": action.consequence.value,
        "idempotency_key": action.idempotency_key,
        "policy_bundle_id": decision.policy_bundle_id,
        "policy_bundle_sha256": decision.policy_bundle_sha256,
        "decision_effect": decision.effect.value,
        # Comma-joined, not a native list: pyarrow's Acero join engine does not
        # support list-typed non-key columns, and Silver's merge joins this row
        # against the outcome side. The full structured value is still present
        # in `record_json` for anything that needs it as a real list.
        "reasons_csv": ",".join(reason.value for reason in decision.reasons),
        "risk_model_ver": risk.model_version,
        "risk_score": risk.value,
        "risk_level": risk.level.value,
        "exposure_monetary": action.exposure.monetary,
        "exposure_records": action.exposure.records,
        "blast_radius": action.exposure.blast_radius.value,
        "trace_id": record.trace_id,
        "causation_id": record.causation_id,
        "record_json": json.dumps(
            dict(record.as_evidence()), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        ),
        "cdc_lsn": cdc_lsn,
        "cdc_operation": cdc_operation,
    }


def outcome_record_to_bronze_row(
    record: OutcomeRecord, *, tenant_id: str, cdc_lsn: str, cdc_operation: str = "insert"
) -> Dict[str, Any]:
    """Flatten an :class:`OutcomeRecord` into a Bronze row.

    ``tenant_id`` is supplied by the caller (the CDC consumer, which already
    knows which segment/tenant this outcome's intent belongs to) because
    ``evidence_outcome`` itself carries no tenant column at the source.
    """
    outcome = record.outcome
    return {
        "decision_id": record.decision_id,
        "tenant_id": tenant_id,
        "status": outcome.status.value,
        "completed_at": outcome.completed_at,
        "result_digest": outcome.result_digest,
        "error_class": outcome.error_class,
        "cdc_lsn": cdc_lsn,
        "cdc_operation": cdc_operation,
    }


def _date_partition(epoch_seconds: float) -> str:
    """Return a ``YYYY-MM-DD`` partition value from epoch seconds.

    Plain integer arithmetic on UTC days -- no ``datetime`` import, consistent
    with invariant I6 (the clock is the only notion of "now"; this function
    merely buckets an already-known timestamp, it does not read one).
    """
    days = int(epoch_seconds // 86_400)
    return _civil_from_days(days)


def _civil_from_days(days: int) -> str:
    """Convert a day count since the Unix epoch to a ``YYYY-MM-DD`` string.

    Howard Hinnant's ``civil_from_days`` algorithm: pure integer arithmetic, no
    floating point, no calendar library, correct for the entire proleptic
    Gregorian calendar.
    """
    z = days + 719_468
    era = z // 146_097 if z >= 0 else (z - 146_096) // 146_097
    doe = z - era * 146_097
    yoe = (doe - doe // 1460 + doe // 36_524 - doe // 146_096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + 3 if mp < 10 else mp - 9
    y = y + 1 if m <= 2 else y
    return f"{y:04d}-{m:02d}-{d:02d}"
