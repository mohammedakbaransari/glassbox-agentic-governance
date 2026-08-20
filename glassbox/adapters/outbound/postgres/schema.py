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

from datetime import datetime, timezone
from typing import Any, List, Mapping, Optional, Sequence, Tuple

from glassbox.adapters.outbound.postgres.driver import ConnectionProvider
from glassbox.domain.errors import EvidenceWriteError

__all__ = [
    "SCHEMA_VERSION",
    "MIGRATIONS",
    "SCHEMA_NOTES",
    "RETENTION_PURGE_GUC",
    "apply_migrations",
    "current_schema_version",
    "ensure_monthly_partitions",
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

_MIGRATION_0005 = """
-- Dedicated columns for the port-conformant retention path (GB-007,
-- glassbox.ports.retention.EvidenceRetentionStore). Namespaced with a
-- `retention_` prefix and kept fully separate from `sealed_at` /
-- `merkle_root` / `purged_before_seq` / `first_seq`, which the pre-existing
-- bespoke `PostgresEvidenceStore.seal_and_purge` reference method already
-- owns -- exactly as the in-memory reference store keeps its `_chain_anchor`
-- (bespoke) and `_worm_anchors` (port-based) dictionaries separate. The two
-- retention strategies must not be run against the same segment.
--
-- `retention_sealed_last_seq` is the authoritative marker of "what has been
-- sealed" for this path; a range is safe to purge only while
-- `retention_sealed_last_seq = before_seq - 1`.
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_sealed_at TIMESTAMPTZ;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_sealed_first_seq BIGINT;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_sealed_last_seq BIGINT;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_merkle_root BYTEA;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_seal_signature BYTEA;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_worm_anchor_id TEXT;
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_worm_locator TEXT;

CREATE INDEX IF NOT EXISTS ix_evidence_segment_unsealed
    ON evidence_segment (tenant_id, opened_at)
    WHERE retention_sealed_last_seq IS NULL;
"""

_MIGRATION_0006 = """
-- The chain-link anchor for the port-conformant retention path. After a
-- purge, the first surviving record's `prev_hash` still points at the MAC of
-- a now-deleted row; `verify()` needs that MAC to confirm the chain link
-- without the row itself. The bespoke seal_and_purge path already keeps this
-- in `merkle_root` (its own "anchor" is just the last purged record's MAC,
-- despite the column name); this is that same value for the retention_*
-- path, kept in its own column for the same separation-of-strategies reason
-- migration 5 exists.
ALTER TABLE evidence_segment ADD COLUMN IF NOT EXISTS retention_last_leaf_hmac BYTEA;
"""

_MIGRATION_0007 = """
-- Physical time-based partitioning of evidence_intent (review finding:
-- "Postgres evidence_intent/evidence_outcome tables have no DDL partitioning
-- by tenant/time"). Tenant-scoping is already real -- the tenant_id column,
-- its indexes, and per-tenant RLS (migration 4) -- so this migration adds
-- the TIME half: native declarative RANGE partitioning by month on
-- created_at, which is what makes retention (seal/purge whole old months)
-- and query performance actually scale as evidence grows without bound.
--
-- evidence_outcome is NOT partitioned by this migration: its idempotency
-- relies on `ON CONFLICT (decision_id) DO NOTHING` against a bare
-- `decision_id` PRIMARY KEY, and Postgres requires a partitioned table's
-- partition key to be part of every unique constraint. Its only timestamp
-- candidate for a partition key was `recorded_at`, a server-side `DEFAULT
-- now()` -- so a retried outcome write for the same decision_id would carry
-- a DIFFERENT `recorded_at` on each attempt, and adding it to the
-- uniqueness constraint would silently turn "idempotent" into "duplicate
-- per retry". Migration 9 does partition evidence_outcome, using
-- `completed_at` (caller-supplied, stable across retries) instead.
--
-- Postgres requires the partition key in every unique constraint on a
-- partitioned table, so PRIMARY KEY (segment_id, seq) becomes
-- (segment_id, seq, created_at), and the decision_id uniqueness index gains
-- created_at too. `created_at` is set once at append and never mutated
-- (append-only), so a decision_id can only ever be associated with the one
-- `created_at` it was actually recorded with -- this widens the literal
-- constraint but changes no real guarantee GB-033/invariant I1 rely on.
--
-- Existing rows are copied, not recreated, and the pre-partition table is
-- kept -- as `evidence_intent_pre_partition_backup` -- for an operator to
-- verify and drop manually. Nothing destructive happens automatically here,
-- mirroring this codebase's own seal-before-purge principle.
--
-- Index names get a `_v2` suffix: Postgres index names are unique per
-- SCHEMA (unlike constraint, trigger and policy names, which are unique per
-- TABLE), so reusing the original names would collide with the
-- still-existing indexes on the renamed backup table.

CREATE TABLE IF NOT EXISTS evidence_intent_partitioned (
    segment_id           TEXT        NOT NULL REFERENCES evidence_segment (segment_id),
    seq                  BIGINT      NOT NULL,
    decision_id          TEXT        NOT NULL,
    tenant_id            TEXT        NOT NULL,
    created_at           TIMESTAMPTZ NOT NULL,
    agent_ref            TEXT        NOT NULL,
    agent_instance_id    TEXT        NOT NULL,
    delegating_subject   TEXT,
    credential_type      TEXT        NOT NULL,
    credential_id        TEXT        NOT NULL,
    action               TEXT        NOT NULL,
    resource_kind        TEXT        NOT NULL,
    resource_id          TEXT        NOT NULL,
    consequence_class    TEXT        NOT NULL,
    idempotency_key      TEXT        NOT NULL,
    policy_bundle_id     TEXT,
    policy_bundle_sha256 TEXT,
    decision_effect      TEXT        NOT NULL,
    risk_model_ver       TEXT        NOT NULL,
    risk_score           NUMERIC     NOT NULL,
    risk_level           TEXT        NOT NULL,
    trace_id             TEXT        NOT NULL,
    causation_id         TEXT,
    record               JSONB       NOT NULL,
    prev_hash            BYTEA       NOT NULL,
    record_hmac          BYTEA       NOT NULL,
    signer_key_id        TEXT        NOT NULL,
    PRIMARY KEY (segment_id, seq, created_at),
    CONSTRAINT evidence_intent_partitioned_seq_non_negative CHECK (seq >= 0),
    CONSTRAINT evidence_intent_partitioned_hash_width CHECK (octet_length(prev_hash) = 32),
    CONSTRAINT evidence_intent_partitioned_mac_width CHECK (octet_length(record_hmac) >= 32)
) PARTITION BY RANGE (created_at);

-- Every row lands somewhere even before a month-specific partition exists
-- for it (see ensure_monthly_partitions) -- a migration or a burst of
-- traffic in an unprovisioned month never fails outright.
CREATE TABLE IF NOT EXISTS evidence_intent_default
    PARTITION OF evidence_intent_partitioned DEFAULT;

-- Indexes are created before the data copy: an unindexed bulk INSERT is
-- faster, but a fresh table's copy here is small enough in practice that the
-- ordering is chosen for correctness clarity, not performance.
CREATE UNIQUE INDEX IF NOT EXISTS ux_evidence_intent_decision_v2
    ON evidence_intent_partitioned (decision_id, created_at);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_tenant_time_v2
    ON evidence_intent_partitioned (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_agent_v2
    ON evidence_intent_partitioned (tenant_id, agent_ref, created_at DESC);
CREATE INDEX IF NOT EXISTS ix_evidence_intent_idempotency_v2
    ON evidence_intent_partitioned (tenant_id, idempotency_key);

-- Copied before RLS is enabled below: RLS's implicit WITH CHECK (no FOR/
-- WITH CHECK clause was given in migration 4, so USING doubles as WITH
-- CHECK) would otherwise reject every row whose tenant_id does not match
-- the single tenant_id the migration's own session happens to have set --
-- which for a bulk, cross-tenant copy is every tenant but one.
INSERT INTO evidence_intent_partitioned
SELECT segment_id, seq, decision_id, tenant_id, created_at, agent_ref, agent_instance_id,
       delegating_subject, credential_type, credential_id, action, resource_kind,
       resource_id, consequence_class, idempotency_key, policy_bundle_id,
       policy_bundle_sha256, decision_effect, risk_model_ver, risk_score, risk_level,
       trace_id, causation_id, record, prev_hash, record_hmac, signer_key_id
  FROM evidence_intent;

ALTER TABLE evidence_intent_partitioned ENABLE ROW LEVEL SECURITY;
ALTER TABLE evidence_intent_partitioned FORCE ROW LEVEL SECURITY;
CREATE POLICY evidence_intent_tenant_isolation ON evidence_intent_partitioned
    USING (tenant_id = current_setting('glassbox.tenant_id', true));

CREATE OR REPLACE FUNCTION glassbox_evidence_append_only() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE'
       AND coalesce(current_setting('glassbox.retention_purge', true), 'off') = 'on' THEN
        RETURN NULL;
    END IF;
    RAISE EXCEPTION
        'evidence_intent is append-only; % is not permitted', TG_OP
        USING ERRCODE = 'restrict_violation';
END;
$$;

CREATE TRIGGER evidence_intent_append_only
    BEFORE UPDATE OR DELETE OR TRUNCATE ON evidence_intent_partitioned
    FOR EACH STATEMENT EXECUTE FUNCTION glassbox_evidence_append_only();

REVOKE UPDATE, TRUNCATE ON evidence_intent_partitioned FROM PUBLIC;

ALTER TABLE evidence_intent RENAME TO evidence_intent_pre_partition_backup;
ALTER TABLE evidence_intent_partitioned RENAME TO evidence_intent;
"""

_MIGRATION_0008 = """
-- The backup table kept by migration 7 still carries the original FK to
-- evidence_segment, which blocks deleting a segment row (and, transitively,
-- the retention/sealing paths that depend on being able to) as long as ANY
-- historical decision for that segment_id exists in the frozen backup --
-- forever, since the backup is intentionally never pruned automatically.
-- The backup's own data integrity does not depend on this FK (it is a
-- frozen, read-only artifact once migration 7 finishes copying it), so it is
-- safe to drop here.
ALTER TABLE IF EXISTS evidence_intent_pre_partition_backup
    DROP CONSTRAINT IF EXISTS evidence_intent_segment_id_fkey;
"""

_MIGRATION_0009 = """
-- Physical time partitioning of evidence_outcome, completing the review
-- finding migration 7 only half-addressed. This one needed an idempotency
-- change first (see the comment migration 7 left on this table, and
-- glassbox/adapters/outbound/postgres/evidence.py's _UPSERT_OUTCOME): the
-- old `PRIMARY KEY (decision_id)` plus `ON CONFLICT (decision_id) DO
-- NOTHING` cannot survive partitioning as-is, because the partition key must
-- be in the uniqueness constraint, and this table's only timestamp,
-- `recorded_at`, is a server-side `DEFAULT now()` that would differ on every
-- retry of the same write.
--
-- The fix is `completed_at` instead: it is supplied by the caller
-- (ExecutionOutcome.completed_at) and describes *when the dispatched effect
-- itself finished* -- a historical fact about the world, not "when this
-- particular write attempt happened" -- so a legitimate retry of recording
-- the same outcome always carries the same value. `PRIMARY KEY
-- (decision_id, completed_at)` is therefore both partition-legal and no
-- less idempotent in practice than the original bare `decision_id` key.
-- The application-level ON CONFLICT target changes to match (see
-- _UPSERT_OUTCOME); this migration only prepares the schema for that.
--
-- No RLS and no FK on this table (unlike evidence_intent), so this
-- migration is the same copy-and-rename shape without those extra steps.

CREATE TABLE IF NOT EXISTS evidence_outcome_partitioned (
    decision_id   TEXT        NOT NULL,
    status        TEXT        NOT NULL,
    completed_at  TIMESTAMPTZ NOT NULL,
    result_digest TEXT,
    error_class   TEXT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (decision_id, completed_at)
) PARTITION BY RANGE (completed_at);

CREATE TABLE IF NOT EXISTS evidence_outcome_default
    PARTITION OF evidence_outcome_partitioned DEFAULT;

INSERT INTO evidence_outcome_partitioned
SELECT decision_id, status, completed_at, result_digest, error_class, recorded_at
  FROM evidence_outcome;

REVOKE UPDATE, TRUNCATE ON evidence_outcome_partitioned FROM PUBLIC;

ALTER TABLE evidence_outcome RENAME TO evidence_outcome_pre_partition_backup;
ALTER TABLE evidence_outcome_partitioned RENAME TO evidence_outcome;
"""

#: Ordered migrations. Append only; never edit a released entry.
MIGRATIONS: Tuple[Tuple[int, str, str], ...] = (
    (1, "evidence tables, indexes and constraints", _MIGRATION_0001),
    (2, "append-only enforcement", _MIGRATION_0002),
    (3, "dispatch ledger for cross-replica idempotency", _MIGRATION_0003),
    (4, "row-level security on evidence_intent", _MIGRATION_0004),
    (5, "port-conformant retention columns", _MIGRATION_0005),
    (6, "retention chain-link anchor column", _MIGRATION_0006),
    (7, "physical time partitioning of evidence_intent", _MIGRATION_0007),
    (8, "drop the backup table's FK so segment cleanup is not blocked", _MIGRATION_0008),
    (9, "physical time partitioning of evidence_outcome", _MIGRATION_0009),
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
                # Passing even an empty parameter tuple selects psycopg's
                # extended protocol, which rejects multi-statement SQL.
                cursor.execute(statements)
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

    if max(installed, applied[-1] if applied else installed) >= 7:
        # Migration 7 partitions evidence_intent by month; every call here
        # (startup, redeploy, or a periodic job) also tops up the partition
        # window, so the DEFAULT partition never becomes the long-term
        # catch-all for months nobody pre-provisioned.
        ensure_monthly_partitions(provider, table="evidence_intent")
    if max(installed, applied[-1] if applied else installed) >= 9:
        # Migration 9 does the same for evidence_outcome.
        ensure_monthly_partitions(provider, table="evidence_outcome")

    return applied


def _add_months(year: int, month: int, delta: int) -> Tuple[int, int]:
    """Return the ``(year, month)`` that is ``delta`` months from ``(year, month)``."""
    total = (year * 12 + (month - 1)) + delta
    return total // 12, total % 12 + 1


def ensure_monthly_partitions(
    provider: ConnectionProvider,
    *,
    table: str = "evidence_intent",
    now: Optional[datetime] = None,
    months_back: int = 1,
    months_ahead: int = 3,
) -> List[str]:
    """Create any missing monthly partitions of ``table`` for a window.

    Idempotent (``CREATE TABLE IF NOT EXISTS``) and safe to call repeatedly --
    on every process startup alongside :func:`apply_migrations`, or from a
    periodic job -- which is what keeps ``<table>_default`` from silently
    becoming the permanent catch-all for months nobody pre-provisioned.
    Identifiers are built with ``psycopg.sql.Identifier``, not string
    interpolation, even though ``partition_name`` here is derived only from
    integer arithmetic and never from caller-supplied data.

    Args:
        provider: Connection provider for the target database.
        table: Either ``"evidence_intent"`` (partitioned by ``created_at``,
            migration 7) or ``"evidence_outcome"`` (partitioned by
            ``completed_at``, migration 9). No other table is supported.
        now: Reference time the window is centred on. Defaults to the real
            wall clock -- adapters may read real time freely; only
            domain/ports/app may not (invariant I6).
        months_back: How many months before ``now``'s month to ensure exist.
        months_ahead: How many months after ``now``'s month to ensure exist.

    Returns:
        Names of every partition the window covers (already existing or newly
        created; ``CREATE TABLE IF NOT EXISTS`` makes the two indistinguishable
        from here, which is fine -- the caller only needs the window covered).

    Raises:
        EvidenceWriteError: If ``table`` is not one of the two supported
            names, is not yet partitioned (its migration not applied), or a
            partition could not be created.
    """
    from psycopg import sql

    _valid_tables = frozenset({"evidence_intent", "evidence_outcome"})
    if table not in _valid_tables:
        raise EvidenceWriteError(
            "unsupported table for partition maintenance",
            table=table,
            supported=", ".join(sorted(_valid_tables)),
        )

    reference = now or datetime.now(timezone.utc)
    year, month = reference.year, reference.month
    covered: List[str] = []
    try:
        with provider.transaction() as cursor:
            default_check = sql.SQL("SELECT to_regclass({name}) IS NOT NULL").format(
                name=sql.Literal(f"{table}_default")
            )
            cursor.execute(default_check)
            row = cursor.fetchone()
            if not row or not row[0]:
                raise EvidenceWriteError(
                    f"{table} is not partitioned yet; apply its migration first", table=table
                )
            for offset in range(-months_back, months_ahead + 1):
                partition_year, partition_month = _add_months(year, month, offset)
                next_year, next_month = _add_months(partition_year, partition_month, 1)
                partition_name = f"{table}_y{partition_year:04d}_m{partition_month:02d}"
                # `FOR VALUES FROM/TO` is a DDL partition-bound clause, not a
                # normal expression context: Postgres evaluates it at parse
                # time, so a bind parameter -- even cast with `::timestamptz`
                # -- fails with "could not determine data type of parameter
                # $1". `sql.Literal` embeds the value as a properly quoted
                # and escaped literal instead, which the grammar accepts, and
                # is exactly as safe as a bind parameter for the same reason:
                # the value is still never string-interpolated by hand.
                statement = sql.SQL(
                    "CREATE TABLE IF NOT EXISTS {partition} "
                    "PARTITION OF {table} FOR VALUES FROM ({start}) TO ({end})"
                ).format(
                    partition=sql.Identifier(partition_name),
                    table=sql.Identifier(table),
                    start=sql.Literal(f"{partition_year:04d}-{partition_month:02d}-01"),
                    end=sql.Literal(f"{next_year:04d}-{next_month:02d}-01"),
                )
                cursor.execute(statement)
                covered.append(partition_name)
    except EvidenceWriteError:
        raise
    except Exception as exc:
        raise EvidenceWriteError(
            "could not ensure monthly partitions",
            table=table,
            cause=type(exc).__name__,
            detail=str(exc),
        ) from exc
    return covered
