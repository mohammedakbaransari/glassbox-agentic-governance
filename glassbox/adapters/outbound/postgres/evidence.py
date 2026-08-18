"""Postgres evidence store (GB-005).

The card that closes fundamental problems **F2** (effect precedes evidence) and
**F3** (evidence is forgeable and retention-fragile).

Each property below is a direct answer to a measured v1 defect.

``append_intent``
    Runs one transaction: lock the segment row, check idempotency, insert, then
    advance the chain head. It returns **only after the commit**, so possession
    of an :class:`~glassbox.domain.evidence.EvidenceReceipt` proves durability.
    v1 executed the side effect at stage 11 and wrote the audit record at stage
    12, where ``_persist_record`` caught every exception and continued.

Sequence allocation
    ``seq`` and ``prev_hash`` are both read from the locked segment row, so two
    concurrent writers cannot fork the chain. v1 derived ``entry_id`` from
    ``MAX(entry_id)+1`` in process memory; two replicas each produced
    ``entry_id: 0`` and one silently overwrote the other's decision.

The MAC
    Keyed, via :class:`~glassbox.ports.keys.MacSigner`, over the canonical bytes
    of ``{seq, prev_hash, record}``. v1 used an unkeyed SHA-256 over the record
    alone, so a rewritten row re-verified as intact.

Failure
    Nothing is swallowed. Every driver error becomes an
    :class:`~glassbox.domain.errors.EvidenceWriteError`, and the caller must not
    dispatch.

Retention
    Purging a sealed prefix leaves the chain verifiable and reports
    :attr:`~glassbox.domain.evidence.IntegrityStatus.SEALED_PURGED`. v1's
    ``purge_old_records`` permanently broke ``verify_hash_chain``.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from glassbox.adapters.outbound.postgres.driver import ConnectionProvider
from glassbox.adapters.outbound.postgres.schema import RETENTION_PURGE_GUC, TENANT_CONTEXT_GUC
from glassbox.domain.errors import (
    EvidenceIntegrityError,
    EvidenceWriteError,
    SigningUnavailableError,
)
from glassbox.domain.evidence import (
    GENESIS_PREV_HASH,
    EvidenceReceipt,
    IntegrityReport,
    IntegrityStatus,
    IntentRecord,
    OutcomeRecord,
)
from glassbox.domain.serialization import canonical_bytes
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.keys import MacSigner

__all__ = ["PostgresEvidenceStore"]

#: Sets the transaction-scoped RLS GUC (GB-026b). `set_config` binds both the
#: name and the value as parameters, unlike `SET LOCAL name = value`, which
#: cannot bind either -- consistent with "every statement binds its parameters".
_SET_TENANT_CONTEXT = "SELECT set_config(%s, %s, true)"

_SELECT_SEGMENT_FOR_UPDATE = """
SELECT last_seq, last_hash, purged_before_seq, sealed_at
  FROM evidence_segment
 WHERE segment_id = %s
   FOR UPDATE
"""

_INSERT_SEGMENT = """
INSERT INTO evidence_segment (segment_id, tenant_id, last_hash)
VALUES (%s, %s, %s)
ON CONFLICT (segment_id) DO NOTHING
"""

_SELECT_RECEIPT_BY_DECISION = """
SELECT segment_id, seq, record_hmac, signer_key_id, created_at, decision_id
  FROM evidence_intent
 WHERE decision_id = %s
"""

_INSERT_INTENT = """
INSERT INTO evidence_intent (
    segment_id, seq, decision_id, tenant_id, created_at,
    agent_ref, agent_instance_id, delegating_subject, credential_type, credential_id,
    action, resource_kind, resource_id, consequence_class, idempotency_key,
    policy_bundle_id, policy_bundle_sha256, decision_effect,
    risk_model_ver, risk_score, risk_level,
    trace_id, causation_id,
    record, prev_hash, record_hmac, signer_key_id
) VALUES (
    %s, %s, %s, %s, to_timestamp(%s),
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s,
    %s, %s,
    %s, %s, %s, %s
)
"""

_ADVANCE_SEGMENT = """
UPDATE evidence_segment
   SET last_seq = %s, last_hash = %s
 WHERE segment_id = %s
"""

_UPSERT_OUTCOME = """
INSERT INTO evidence_outcome (decision_id, status, completed_at, result_digest, error_class)
VALUES (%s, %s, to_timestamp(%s), %s, %s)
ON CONFLICT (decision_id) DO NOTHING
"""

_SELECT_SEGMENT_STATE = """
SELECT purged_before_seq, sealed_at, merkle_root, worm_anchor_id
  FROM evidence_segment
 WHERE segment_id = %s
"""

_SELECT_CHAIN = """
SELECT seq, record, prev_hash, record_hmac, signer_key_id,
       decision_id, tenant_id, action, consequence_class, risk_score
  FROM evidence_intent
 WHERE segment_id = %s
 ORDER BY seq ASC
"""

_SELECT_ANCHOR_BEFORE = """
SELECT record_hmac
  FROM evidence_intent
 WHERE segment_id = %s AND seq < %s
 ORDER BY seq DESC
 LIMIT 1
"""

_DELETE_PURGED = """
DELETE FROM evidence_intent
 WHERE segment_id = %s AND seq < %s
RETURNING seq
"""

_SEAL_SEGMENT = """
UPDATE evidence_segment
   SET purged_before_seq = %s,
       merkle_root = %s,
       sealed_at = coalesce(sealed_at, now()),
       first_seq = %s
 WHERE segment_id = %s
"""

_DISABLE_APPEND_ONLY_TRIGGER = (
    "ALTER TABLE evidence_intent DISABLE TRIGGER evidence_intent_append_only"
)
_ENABLE_APPEND_ONLY_TRIGGER = (
    "ALTER TABLE evidence_intent ENABLE TRIGGER evidence_intent_append_only"
)

_TAMPER_RECORD = """
UPDATE evidence_intent
   SET record = %s
 WHERE segment_id = %s AND seq = %s
"""


class PostgresEvidenceStore:
    """Append-only, MAC-chained evidence in Postgres.

    Args:
        provider: Supplies cursors already inside a transaction.
        signer: Produces the keyed MAC. Required -- there is no unkeyed mode.
        verify_denormalised_columns: When ``True`` (the default) verification
            also checks that the indexed columns still agree with the
            authoritative JSONB, so editing a queryable column is detected too.
        allow_test_tampering: Enables :meth:`tamper_for_test`. Never set in a
            deployed process.
    """

    __slots__ = ("_provider", "_signer", "_verify_columns", "_allow_tampering")

    def __init__(
        self,
        provider: ConnectionProvider,
        signer: MacSigner,
        *,
        verify_denormalised_columns: bool = True,
        allow_test_tampering: bool = False,
    ) -> None:
        if provider is None:
            raise EvidenceWriteError("a Postgres evidence store requires a connection provider")
        if signer is None:
            raise EvidenceWriteError("a Postgres evidence store requires a MAC signer")
        self._provider = provider
        self._signer = signer
        self._verify_columns = verify_denormalised_columns
        self._allow_tampering = allow_test_tampering

    # ----------------------------------------------------------------- #
    # EvidenceStore
    # ----------------------------------------------------------------- #

    def append_intent(self, record: IntentRecord) -> EvidenceReceipt:
        """Persist a pre-effect record and return proof that it is durable.

        Raises:
            EvidenceWriteError: If the record is invalid, the segment is sealed,
                or the write fails for any reason. The caller **must not**
                dispatch.
            SigningUnavailableError: If the MAC signer is unreachable. Writing
                unkeyed evidence is not an available fallback.
        """
        if not isinstance(record, IntentRecord):
            raise EvidenceWriteError(
                "append_intent requires an IntentRecord",
                offending_type=type(record).__name__,
            )

        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_SET_TENANT_CONTEXT, (TENANT_CONTEXT_GUC, record.tenant_id))
                cursor.execute(
                    _INSERT_SEGMENT,
                    (record.segment_id, record.tenant_id, GENESIS_PREV_HASH),
                )
                cursor.execute(_SELECT_SEGMENT_FOR_UPDATE, (record.segment_id,))
                segment = cursor.fetchone()
                if segment is None:
                    raise EvidenceWriteError(
                        "evidence segment could not be created or locked",
                        segment_id=record.segment_id,
                    )
                last_seq, last_hash, _purged_before, sealed_at = (
                    int(segment[0]),
                    bytes(segment[1]),
                    int(segment[2]),
                    segment[3],
                )
                if sealed_at is not None:
                    raise EvidenceWriteError(
                        "cannot append to a sealed segment",
                        segment_id=record.segment_id,
                    )

                cursor.execute(_SELECT_RECEIPT_BY_DECISION, (record.decision_id,))
                existing = cursor.fetchone()
                if existing is not None:
                    return _receipt_from_row(existing)

                seq = last_seq + 1
                payload = record.chain_payload(seq=seq, prev_hash=last_hash)
                mac = self._sign(payload, record)
                key_id = self._signer.key_id

                cursor.execute(
                    _INSERT_INTENT,
                    _insert_parameters(
                        record, seq=seq, prev_hash=last_hash, mac=mac, key_id=key_id
                    ),
                )
                cursor.execute(_ADVANCE_SEGMENT, (seq, mac, record.segment_id))

                return EvidenceReceipt(
                    decision_id=record.decision_id,
                    segment_id=record.segment_id,
                    seq=seq,
                    record_hmac=mac,
                    signer_key_id=key_id,
                    persisted_at=record.created_at,
                )
        except (EvidenceWriteError, SigningUnavailableError):
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "evidence could not be made durable; the caller must not dispatch",
                decision_id=record.decision_id,
                segment_id=record.segment_id,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def append_outcome(self, receipt: EvidenceReceipt, record: OutcomeRecord) -> None:
        """Record what happened after the intent was made durable.

        Raises:
            ValueError: If the receipt and the outcome describe different decisions.
            EvidenceWriteError: If no intent exists for the receipt, or the write
                fails.
        """
        if receipt.decision_id != record.decision_id:
            raise ValueError(
                "receipt and outcome describe different decisions: "
                f"{receipt.decision_id!r} != {record.decision_id!r}"
            )
        outcome = record.outcome
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_SELECT_RECEIPT_BY_DECISION, (receipt.decision_id,))
                if cursor.fetchone() is None:
                    raise EvidenceWriteError(
                        "no intent record exists for this receipt",
                        decision_id=receipt.decision_id,
                    )
                cursor.execute(
                    _UPSERT_OUTCOME,
                    (
                        record.decision_id,
                        outcome.status.value,
                        outcome.completed_at,
                        outcome.result_digest,
                        outcome.error_class,
                    ),
                )
        except EvidenceWriteError:
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "outcome could not be recorded",
                decision_id=record.decision_id,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def verify(self, segment_id: str, *, now: float) -> IntegrityReport:
        """Verify the MAC chain of one segment.

        Recomputes every payload from the stored fields; a cached digest is never
        trusted. Sequence continuity is checked, so deletion and re-ordering are
        detected as well as mutation.

        Raises:
            EvidenceIntegrityError: If verification could not be performed at all.
                A *failed* verification is a returned report, not an exception.
        """
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_SELECT_SEGMENT_STATE, (segment_id,))
                state = cursor.fetchone()
                if state is None:
                    return IntegrityReport(
                        segment_id=segment_id,
                        status=IntegrityStatus.UNVERIFIABLE,
                        records_checked=0,
                        verified_at=now,
                        detail="segment not found",
                    )
                purged_before = int(state[0])
                sealed_at = state[1]
                sealed_anchor = bytes(state[2]) if state[2] is not None else None
                worm_anchor_id = state[3]

                cursor.execute(_SELECT_CHAIN, (segment_id,))
                rows = cursor.fetchall()
        except Exception as exc:
            raise EvidenceIntegrityError(
                "evidence verification could not be performed",
                segment_id=segment_id,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

        return self._verify_rows(
            segment_id,
            rows,
            purged_before=purged_before,
            sealed=sealed_at is not None,
            sealed_anchor=sealed_anchor,
            anchored=worm_anchor_id is not None,
            now=now,
        )

    # ----------------------------------------------------------------- #
    # Retention (reference behaviour for GB-007)
    # ----------------------------------------------------------------- #

    def seal_and_purge(self, segment_id: str, *, before_seq: int) -> int:
        """Seal the segment prefix and purge it, keeping verification possible.

        The MAC of the last purged record is retained as the sealed anchor, so
        the surviving records still chain to something verifiable. Deletion is
        permitted only while the retention session setting is on, which nothing
        else in the system turns on.

        GB-007 replaces the anchor with a real Merkle root and writes it to WORM
        storage; the surrounding contract does not change.

        Args:
            segment_id: Segment to purge within.
            before_seq: Purge records whose ``seq`` is strictly below this.

        Returns:
            The number of records purged.

        Raises:
            EvidenceWriteError: If the purge fails. Nothing partial is committed.
        """
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_SELECT_SEGMENT_FOR_UPDATE, (segment_id,))
                if cursor.fetchone() is None:
                    return 0

                cursor.execute(_SELECT_ANCHOR_BEFORE, (segment_id, before_seq))
                anchor_row = cursor.fetchone()
                if anchor_row is None:
                    return 0
                anchor = bytes(anchor_row[0])

                cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
                cursor.execute(_DELETE_PURGED, (segment_id, before_seq))
                purged = cursor.fetchall()
                cursor.execute(_SEAL_SEGMENT, (before_seq, anchor, before_seq, segment_id))
                return len(purged)
        except EvidenceWriteError:
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "retention purge failed",
                segment_id=segment_id,
                before_seq=before_seq,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def tamper_for_test(self, segment_id: str, seq: int, replacement: IntentRecord) -> None:
        """Overwrite a stored record without updating its MAC.

        Test-only, and refused unless the store was constructed with
        ``allow_test_tampering=True``. It models an attacker who has already got
        past the database's append-only guards -- superuser, or direct file
        access -- and exists to prove the keyed chain still catches them. The
        guards themselves are asserted separately by the integration tests.

        Raises:
            EvidenceWriteError: If test tampering was not explicitly enabled.
        """
        if not self._allow_tampering:
            raise EvidenceWriteError(
                "test tampering is disabled; construct the store with allow_test_tampering=True",
                segment_id=segment_id,
            )
        payload = json.dumps(
            dict(replacement.as_evidence()),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        try:
            with self._provider.transaction() as cursor:
                cursor.execute(_DISABLE_APPEND_ONLY_TRIGGER, ())
                cursor.execute(_TAMPER_RECORD, (payload, segment_id, seq))
                cursor.execute(_ENABLE_APPEND_ONLY_TRIGGER, ())
        except Exception as exc:
            raise EvidenceWriteError(
                "test tampering failed",
                segment_id=segment_id,
                seq=seq,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    # ----------------------------------------------------------------- #
    # Internals
    # ----------------------------------------------------------------- #

    def _sign(self, payload: bytes, record: IntentRecord) -> bytes:
        """Compute the record MAC, refusing to degrade to an unkeyed digest."""
        try:
            return self._signer.mac(payload)
        except SigningUnavailableError:
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "MAC computation failed; refusing to write unkeyed evidence",
                decision_id=record.decision_id,
                cause=type(exc).__name__,
            ) from exc

    def _verify_rows(
        self,
        segment_id: str,
        rows: Sequence[Sequence[Any]],
        *,
        purged_before: int,
        sealed: bool,
        sealed_anchor: Optional[bytes],
        anchored: bool,
        now: float,
    ) -> IntegrityReport:
        """Walk the chain, checking continuity, linkage and authenticity."""
        expected_seq = purged_before
        expected_prev = GENESIS_PREV_HASH if purged_before == 0 else sealed_anchor
        checked = 0

        for row in rows:
            seq = int(row[0])
            record_payload = _as_mapping(row[1])
            prev_hash = bytes(row[2])
            mac = bytes(row[3])
            key_id = str(row[4])

            if seq != expected_seq:
                return _broken(
                    segment_id,
                    checked,
                    now,
                    seq,
                    f"sequence discontinuity: expected {expected_seq}",
                )
            if expected_prev is not None and prev_hash != expected_prev:
                return _broken(
                    segment_id, checked, now, seq, "chain link does not match the previous record"
                )
            if self._verify_columns:
                mismatch = _column_mismatch(row, record_payload)
                if mismatch:
                    return _broken(
                        segment_id,
                        checked,
                        now,
                        seq,
                        f"indexed column disagrees with the signed record: {mismatch}",
                    )

            payload = canonical_bytes(
                {"seq": seq, "prev_hash": prev_hash.hex(), "record": record_payload}
            )
            try:
                authentic = self._signer.verify(payload, mac, key_id=key_id)
            except SigningUnavailableError:
                return IntegrityReport(
                    segment_id=segment_id,
                    status=IntegrityStatus.UNVERIFIABLE,
                    records_checked=checked,
                    verified_at=now,
                    detail=f"signing key {key_id!r} is unavailable",
                )
            if not authentic:
                return _broken(
                    segment_id,
                    checked,
                    now,
                    seq,
                    "record MAC does not authenticate the stored fields",
                )

            expected_prev = mac
            expected_seq += 1
            checked += 1

        status = (
            IntegrityStatus.SEALED_PURGED
            if purged_before > 0 and sealed
            else IntegrityStatus.INTACT
        )
        detail = ""
        if status is IntegrityStatus.SEALED_PURGED:
            detail = (
                f"{purged_before} record(s) purged under retention; sealed root "
                f"{'anchored' if anchored else 'not yet anchored'}"
            )
        elif purged_before > 0:
            # Rows are gone but the segment was never sealed, so nothing attests
            # to what they contained. Saying "intact" would overstate the case.
            return IntegrityReport(
                segment_id=segment_id,
                status=IntegrityStatus.UNVERIFIABLE,
                records_checked=checked,
                verified_at=now,
                detail=f"{purged_before} record(s) purged from an unsealed segment",
            )
        return IntegrityReport(
            segment_id=segment_id,
            status=status,
            records_checked=checked,
            verified_at=now,
            detail=detail,
        )


# --------------------------------------------------------------------------- #
# Row helpers
# --------------------------------------------------------------------------- #


def _receipt_from_row(row: Sequence[Any]) -> EvidenceReceipt:
    """Rebuild the receipt for an already-appended decision."""
    created_at = row[4]
    return EvidenceReceipt(
        decision_id=_decision_id_from_row(row),
        segment_id=str(row[0]),
        seq=int(row[1]),
        record_hmac=bytes(row[2]),
        signer_key_id=str(row[3]),
        persisted_at=_as_epoch(created_at),
    )


def _decision_id_from_row(row: Sequence[Any]) -> str:
    """The idempotency SELECT is keyed by decision id, so carry it through."""
    return str(row[5]) if len(row) > 5 else ""


def _as_epoch(value: Any) -> float:
    """Coerce a database timestamp to POSIX epoch seconds."""
    if isinstance(value, (int, float)):
        return float(value)
    timestamp = getattr(value, "timestamp", None)
    if callable(timestamp):
        return float(timestamp())
    raise EvidenceWriteError(
        "unsupported timestamp type from the driver",
        offending_type=type(value).__name__,
    )


def _as_mapping(value: Any) -> Mapping[str, Any]:
    """Coerce a JSONB column to a mapping.

    Drivers differ: ``psycopg`` returns ``dict`` while some return the raw JSON
    text. Both are accepted; anything else is refused rather than guessed at,
    because a wrong guess here silently changes what the MAC covers.
    """
    if isinstance(value, Mapping):
        return value
    if isinstance(value, (str, bytes, bytearray)):
        return json.loads(value)
    raise EvidenceIntegrityError(
        "unsupported JSON column type from the driver",
        offending_type=type(value).__name__,
    )


def _column_mismatch(row: Sequence[Any], record: Mapping[str, Any]) -> str:
    """Return the first indexed column that disagrees with the signed record."""
    action_payload = record.get("action", {})
    risk_payload = record.get("risk", {})
    checks: Tuple[Tuple[str, Any, Any], ...] = (
        ("decision_id", str(row[5]), record.get("decision_id")),
        ("tenant_id", str(row[6]), record.get("tenant_id")),
        ("action", str(row[7]), action_payload.get("action")),
        ("consequence_class", str(row[8]), action_payload.get("consequence_class")),
        ("risk_score", float(row[9]), risk_payload.get("risk_score")),
    )
    for name, stored, signed in checks:
        if signed is None:
            continue
        if isinstance(stored, float) and float(stored) != float(signed):
            return name
        if not isinstance(stored, float) and stored != signed:
            return name
    return ""


def _broken(segment_id: str, checked: int, now: float, seq: int, detail: str) -> IntegrityReport:
    """Build a localised BROKEN report."""
    return IntegrityReport(
        segment_id=segment_id,
        status=IntegrityStatus.BROKEN,
        records_checked=checked,
        verified_at=now,
        first_broken_seq=seq,
        detail=detail,
    )


def _insert_parameters(
    record: IntentRecord, *, seq: int, prev_hash: bytes, mac: bytes, key_id: str
) -> Tuple[Any, ...]:
    """Flatten a record into the INSERT parameter tuple.

    The denormalised columns exist for querying; the ``record`` JSONB is what the
    MAC covers and what verification recomputes from.
    """
    evidence = dict(record.as_evidence())
    action = record.action
    decision = record.decision
    risk = record.risk
    principal = record.principal
    return (
        record.segment_id,
        seq,
        record.decision_id,
        record.tenant_id,
        record.created_at,
        principal.agent_ref,
        principal.agent_instance_id,
        principal.delegating_subject,
        principal.credential_type.value,
        principal.credential_id,
        action.action,
        action.resource.kind,
        action.resource.id,
        action.consequence.value,
        action.idempotency_key,
        decision.policy_bundle_id,
        decision.policy_bundle_sha256,
        decision.effect.value,
        risk.model_version,
        risk.value,
        risk.level.value,
        record.trace_id,
        record.causation_id,
        json.dumps(evidence, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
        prev_hash,
        mac,
        key_id,
    )
