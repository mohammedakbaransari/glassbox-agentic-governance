"""Evidence schema and migrations (GB-005).

Implements §6.1 of the implementation plan, with two deliberate changes from the
DDL sketched there. Both are noted in :data:`SCHEMA_NOTES` and repeated here.

**1. Mutation raises; it does not silently do nothing.**
The plan used ``CREATE RULE ... DO INSTEAD NOTHING``. That makes an ``UPDATE``
*succeed* while changing nothing, so a caller -- or an attacker probing what the
store permits -- gets a success response for an operation that did not happen.
A statement-level trigger that raises is strictly better: the attempt fails
loudly, is visible in the Postgres log, and is testable. ``REVOKE`` is kept as
the first line of defence, so the guarantee does not rest on a single mechanism.

**2. Chain state lives on the segment row, and the segment row is the lock.**
The plan allocated ``seq`` from a per-segment Postgres sequence. That needs DDL
per segment, and a sequence does not serialise the *chain*: two transactions
could take sequence values 4 and 5 and then compute ``prev_hash`` from the same
predecessor. A hash chain is inherently serial, so ``append_intent`` takes
``SELECT ... FOR UPDATE`` on the segment row and reads ``last_seq``/``last_hash``
from it. Different segments still proceed fully in parallel, which is where the
horizontal scaling comes from -- one segment per tenant per period.

``UNIQUE (segment_id, seq)`` is retained as a backstop: if the locking is ever
wrong, the database refuses the write rather than accepting a forked chain.
"""

from __future__ import annotations

from typing import Any, List, Mapping, Sequence, Tuple

from glassbox.adapters.outbound.postgres.driver import ConnectionProvider
from glassbox.domain.errors import EvidenceWriteError

__all__ = [
    "SCHEMA_VERSION",
    "MIGRATIONS",
    "SCHEMA_NOTES",
    "RETENTION_PURGE_GUC",
    "apply_migrations",
    "current_schema_version",
]

#: Session setting that a retention job must set before it may delete rows from a
#: sealed segment. Nothing else in the system sets it, so an accidental ``DELETE``
#: from application code fails.
RETENTION_PURGE_GUC = "glassbox.retention_purge"

SCHEMA_NOTES = (
    "evidence_intent is append-only: UPDATE and TRUNCATE always raise; DELETE "
    f"raises unless {RETENTION_PURGE_GUC} is set, which only the sealed-segment "
    "retention job (GB-007) does.",
    "The authoritative record is the `record` JSONB column. The MAC covers it "
    "together with `seq` and `prev_hash`, so mutation, deletion and re-ordering "
    "are all detectable.",
    "Denormalised columns exist for querying and are cross-checked against the "
    "JSONB during verification, so editing one of them is also detected.",
)


_MIGRATION_0001 = """
CREATE TABLE IF NOT EXISTS glassbox_schema_migration (
    version     INTEGER     PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    description TEXT        NOT NULL
);

-- One open chain. `last_seq` and `last_hash` are the chain head; the row is the
-- serialisation point for appends to this segment.
CREATE TABLE IF NOT EXISTS evidence_segment (
    segment_id        TEXT        PRIMARY KEY,
    tenant_id         TEXT        NOT NULL,
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    first_seq         BIGINT      NOT NULL DEFAULT 0,
    last_seq          BIGINT      NOT NULL DEFAULT -1,
    last_hash         BYTEA       NOT NULL,
    purged_before_seq BIGINT      NOT NULL DEFAULT 0,
    sealed_at         TIMESTAMPTZ,
    merkle_root       BYTEA,
    seal_signature    BYTEA,
    worm_anchor_id    TEXT,
    CONSTRAINT evidence_segment_seq_order CHECK (last_seq >= first_seq - 1),
    CONSTRAINT evidence_segment_purge_bound CHECK (purged_before_seq >= first_seq)
);

CREATE TABLE IF NOT EXISTS evidence_intent (
    segment_id           TEXT        NOT NULL REFERENCES evidence_segment (segment_id),
    seq                  BIGINT      NOT NULL,
    decision_id          TEXT        NOT NULL,
    tenant_id            TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL,

    -- identity (server-derived; never a request header)
    agent_ref            TEXT        NOT NULL,
    agent_instance_id    TEXT        NOT NULL,
    delegating_subject   TEXT,
    credential_type      TEXT        NOT NULL,
    credential_id        TEXT        NOT NULL,

    -- action
    action               TEXT        NOT NULL,
    resource_kind        TEXT        NOT NULL,
    resource_id          TEXT        NOT NULL,
    consequence_class    TEXT        NOT NULL,
    idempotency_key      TEXT        NOT NULL,

    -- governance provenance
    policy_bundle_id     TEXT,
    policy_bundle_sha256 TEXT,
    decision_effect      TEXT        NOT NULL,
    risk_model_ver       TEXT        NOT NULL,
    risk_score           NUMERIC     NOT NULL,
    risk_level           TEXT        NOT NULL,

    -- tracing
    trace_id             TEXT        NOT NULL,
    causation_id         TEXT,

    -- the authoritative canonical payload the MAC is computed over
    record               JSONB       NOT NULL,

    -- integrity
    prev_hash            BYTEA       NOT NULL,
    record_hmac          BYTEA       NOT NULL,
    signer_key_id        TEXT        NOT NULL,

    PRIMARY KEY (segment_id, seq),
    CONSTRAINT evidence_intent_seq_non_negative CHECK (seq >= 0),
    CONSTRAINT evidence_intent_hash_width CHECK (octet_length(prev_hash) = 32),
    CONSTRAINT evidence_intent_mac_width CHECK (octet_length(record_hmac) >= 32)
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_intent_decision
    ON evidence_intent (decision_id);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_tenant_time
    ON evidence_intent (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_agent
    ON evidence_intent (tenant_id, agent_ref, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_idempotency
    ON evidence_intent (tenant_id, idempotency_key);

-- Written later and separately, so the intent write stays on the critical path
-- alone. A missing row here means the outcome is unknown, which is a true and
-- useful statement; it never retroactively authorises the effect.
CREATE TABLE IF NOT EXISTS evidence_outcome (
    decision_id   TEXT        PRIMARY KEY,
    status        TEXT        NOT NULL,
    completed_at  TIMESTAMPTZ NOT NULL,
    result_digest TEXT,
    error_class   TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""

_MIGRATION_0002 = f"""
-- Defence in depth. REVOKE stops the ordinary path; the trigger stops a role
-- that was granted more than it should have been, and makes the attempt visible.
CREATE OR REPLACE FUNCTION glassbox_evidence_append_only() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('{RETENTION_PURGE_GUC}', true), 'off') = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION
        'evidence_intent is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

DROP TRIGGER IF EXISTS evidence_intent_append_only ON evidence_intent;
CREATE TRIGGER evidence_intent_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON evidence_intent
    FOR EACH STATEMENT EXECUTE FUNCTION glassbox_evidence_append_only();

REVOKE UPDATE, TRUNCATE ON evidence_intent FROM PUBLIC;
REVOKE UPDATE, TRUNCATE ON evidence_outcome FROM PUBLIC;
"""

_MIGRATION_0003 = """
-- The cross-replica idempotency ledger (GB-033). A row is claimed atomically by
-- exactly one replica via `INSERT ... ON CONFLICT DO NOTHING`; every other
-- replica -- including one that crashed mid-dispatch -- sees the same claim
-- and never re-executes the effect.
CREATE TABLE IF NOT EXISTS dispatch_ledger (
    idempotency_key TEXT        PRIMARY KEY,
    decision_id     TEXT        NOT NULL,
    action          TEXT        NOT NULL,
    status          TEXT        NOT NULL,
    claimed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- epoch seconds, not TIMESTAMPTZ: this is round-tripped through
    -- ExecutionOutcome.completed_at (a float) with no driver-side conversion.
    completed_at    DOUBLE PRECISION,
    result_digest   TEXT,
    error_class     TEXT,
    CONSTRAINT dispatch_ledger_status_valid CHECK (
        status IN ('claimed', 'executed', 'failed', 'indeterminate')
    )
);

CREATE INDEX IF NOT EXISTS ix_dispatch_ledger_decision ON dispatch_ledger (decision_id);
"""

#: Session variable a transaction sets to the verified principal's tenant before
#: touching `evidence_intent` (GB-026b). Defence in depth, never the only
#: guarantee: `DecisionService` already makes cross-tenant reads structurally
#: unreachable in the code path (every method requires a `VerifiedPrincipal`),
#: but this stops a *future* adapter bug that issues a query without the
#: application-level tenant predicate from being able to see another tenant's
#: rows at all.
TENANT_CONTEXT_GUC = "glassbox.tenant_id"

_MIGRATION_0004 = f"""
-- RLS is a second, independent guarantee, not a replacement for the
-- application-level tenant predicate -- the same REVOKE-plus-trigger pattern
-- migration 2 uses for append-only enforcement.
ALTER TABLE evidence_intent ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_intent FORCE ROW LEVEL SECURITY;

CREATE POLICY evidence_intent_tenant_isolation ON evidence_intent
    USING (tenant_id = current_setting('{TENANT_CONTEXT_GUC}', true));
"""

#: Ordered migrations. Append only; never edit a released entry.
MIGRATIONS: Tuple[Tuple[int, str, str], ...] = (
    (1, "evidence tables, indexes and constraints", _MIGRATION_0001),
    (2, "append-only enforcement", _MIGRATION_0002),
    (3, "dispatch ledger for cross-replica idempotency", _MIGRATION_0003),
    (4, "row-level security on evidence_intent", _MIGRATION_0004),
)

#: Derived, never hand-maintained: a constant edited separately from the list it
#: describes drifts, and the drift is invisible until a deployment half-migrates.
SCHEMA_VERSION = MIGRATIONS[-1][0]


def current_schema_version(provider: ConnectionProvider) -> int:
    """Return the highest applied migration version, or ``0`` if none.

    Raises:
        EvidenceWriteError: If the version cannot be determined.
    """
    try:
        with provider.transaction() as cursor:
            cursor.execute(
                "SELECT to_regclass('glassbox_schema_migration') IS NOT NULL",
                (),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                return 0
            cursor.execute("SELECT coalesce(max(version), 0) FROM glassbox_schema_migration", ())
            row = cursor.fetchone()
            return int(row[0]) if row else 0
    except EvidenceWriteError:
        raise
    except Exception as exc:
        raise EvidenceWriteError(
            "could not read the evidence schema version",
            cause=type(exc).__name__,
            detail=str(exc),
        ) from exc


def apply_migrations(provider: ConnectionProvider) -> List[int]:
    """Apply every migration that has not yet been applied.

    Each migration runs in its own transaction together with the row that records
    it, so a partially applied schema is not representable.

    Args:
        provider: Connection provider for the target database.

    Returns:
        The versions applied by this call, in order.

    Raises:
        EvidenceWriteError: If a migration fails. The schema is left at the last
            successfully applied version.
    """
    applied: List[int] = []
    installed = current_schema_version(provider)
    for version, description, statements in MIGRATIONS:
        if version <= installed:
            continue
        try:
            with provider.transaction() as cursor:
                cursor.execute(statements, ())
                cursor.execute(
                    "INSERT INTO glassbox_schema_migration (version, description) "
                    "VALUES (%s, %s) ON CONFLICT (version) DO NOTHING",
                    (version, description),
                )
        except EvidenceWriteError:
            raise
        except Exception as exc:
            raise EvidenceWriteError(
                "evidence schema migration failed",
                version=version,
                description=description,
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc
        applied.append(version)
    return applied
