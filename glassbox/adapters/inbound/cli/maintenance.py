"""Scheduled evidence maintenance entrypoint (Workstream E).

Wires the partition-window maintenance (``ensure_monthly_partitions``) and
the retention scheduler (seal/purge) into something a real deployment can
actually invoke periodically -- a Kubernetes ``CronJob``, a systemd timer,
plain ``cron`` -- closing the gap that both existed only as library code
with zero callers.

This deliberately composes its own small, retention-specific object graph
rather than building a full :class:`~glassbox.app.composition.GovernanceRuntime`:
sealing and purging are privileged, batch, out-of-band operations
(:mod:`glassbox.app.sealer`'s own docstring), kept off the request-serving
runtime on purpose so the two can be granted to different database roles
and run in different processes.

Usage::

    python -m glassbox.adapters.inbound.cli.maintenance

Configuration is entirely via ``GLASSBOX_*`` environment variables, the same
convention every other entrypoint uses (:meth:`GlassBoxConfig.from_env`).
Additional maintenance-specific settings:

``GLASSBOX_MAINTENANCE_SEAL_AFTER_SECONDS``
    How old an unsealed segment must be before it is sealed. Default 30 days.
``GLASSBOX_MAINTENANCE_PURGE_GRACE_SECONDS``
    How long after sealing before a segment is purged. Default 30 days.
``GLASSBOX_MAINTENANCE_SEGMENT_BATCH_LIMIT``
    Upper bound on segments considered in one run. Default 5000 -- so one
    invocation can never grow unbounded even against a very large, very
    active deployment; a slower-than-arrival maintenance cadence shows up as
    a growing backlog to alert on, not a single unbounded batch.
``GLASSBOX_MAINTENANCE_WORM_ANCHOR_DIR``
    Directory for sealed anchors when no KMS-backed signer is configured.
    Defaults to ``./glassbox-worm-anchors``.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Sequence

from glassbox.adapters.outbound.memory.clock import SystemClock
from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
from glassbox.adapters.outbound.postgres.evidence import PostgresEvidenceStore
from glassbox.adapters.outbound.worm import FilesystemWormAnchorStore
from glassbox.app.config import GlassBoxConfig
from glassbox.app.observability import get_logger, log_error
from glassbox.app.retention_scheduler import RetentionAction, RetentionOutcome, RetentionScheduler
from glassbox.app.sealer import SegmentSealer

__all__ = ["run", "main"]

_logger = get_logger("maintenance")

_SELECT_RETENTION_CANDIDATES = """
SELECT segment_id
  FROM evidence_segment
 WHERE retention_sealed_last_seq IS NULL
    OR purged_before_seq <= retention_sealed_last_seq
 ORDER BY opened_at ASC
 LIMIT %s
"""


def _fetch_segment_ids(provider: PsycopgConnectionProvider, *, limit: int) -> List[str]:
    """Return segment ids with outstanding retention work, oldest first.

    Bounded by ``limit`` so a single run can never scan an unbounded table;
    the scheduler itself decides per segment whether it is actually due yet.
    """
    with provider.transaction() as cursor:
        cursor.execute(_SELECT_RETENTION_CANDIDATES, (limit,))
        return [row[0] for row in cursor.fetchall()]


def _build_signer(config: GlassBoxConfig):
    """Match whatever signer the deployment's own evidence writes use.

    A retention job that signed seals with a *different* key than the one
    protecting live evidence would produce anchors nothing else could verify.
    """
    if config.signing.allow_local_key:
        from glassbox.adapters.outbound.memory.signing import build_mac_signer
    else:
        from glassbox.adapters.outbound.kms import build_mac_signer
    return build_mac_signer(config)


def run(
    *,
    config: GlassBoxConfig,
    seal_after_seconds: float,
    purge_grace_seconds: float,
    segment_batch_limit: int,
    worm_anchor_dir: str,
) -> Sequence[RetentionOutcome]:
    """Run one maintenance pass: top up partitions, then seal/purge eligible segments.

    Returns:
        Every retention action taken this pass (or attempted and failed), for
        the caller to log, alert on, or assert against in a test.

    Raises:
        glassbox.domain.errors.EvidenceWriteError: If the connection cannot be
            established, or partition maintenance itself fails. A single
            segment's seal/purge failure does NOT raise -- it is reported as
            :attr:`~glassbox.app.retention_scheduler.RetentionAction.FAILED`
            in the returned sequence, so one bad segment cannot abort the
            whole pass.
    """
    from glassbox.adapters.outbound.postgres.schema import apply_migrations

    provider = PsycopgConnectionProvider(config.evidence.dsn)
    try:
        # Also tops up both evidence_intent's and evidence_outcome's monthly
        # partition windows for any already-applied migration >= 7/9.
        apply_migrations(provider)

        store = PostgresEvidenceStore(provider, _build_signer(config))
        anchors = FilesystemWormAnchorStore(Path(worm_anchor_dir))
        sealer = SegmentSealer(retention=store, anchors=anchors, signer=_build_signer(config))
        scheduler = RetentionScheduler(
            retention=store,
            sealer=sealer,
            clock=SystemClock(),
            seal_after_seconds=seal_after_seconds,
            purge_grace_seconds=purge_grace_seconds,
        )

        segment_ids = _fetch_segment_ids(provider, limit=segment_batch_limit)
        outcomes = scheduler.run_once(segment_ids)
    finally:
        provider.close()

    for outcome in outcomes:
        if outcome.action in (RetentionAction.SEALED, RetentionAction.PURGED):
            _logger.info(
                "retention action taken",
                extra={"segment_id": outcome.segment_id, "action": outcome.action.value,
                       "detail": outcome.detail},
            )
        elif outcome.action is RetentionAction.FAILED:
            log_error(
                _logger,
                RuntimeError(outcome.detail),
                message=f"retention action failed for segment {outcome.segment_id!r}",
            )
    return outcomes


def main(argv: Sequence[str] = ()) -> int:
    """CLI entry point. Returns a process exit code; never raises to the shell.

    A single failed segment does not fail the run (see :func:`run`); only an
    inability to connect, migrate, or query at all does, and that is reported
    as a non-zero exit code so a scheduler (cron, a CronJob) surfaces it.
    """
    del argv  # no positional arguments; everything is environment-configured
    config = GlassBoxConfig.from_env()
    try:
        outcomes = run(
            config=config,
            seal_after_seconds=float(
                os.environ.get("GLASSBOX_MAINTENANCE_SEAL_AFTER_SECONDS", 30 * 86_400.0)
            ),
            purge_grace_seconds=float(
                os.environ.get("GLASSBOX_MAINTENANCE_PURGE_GRACE_SECONDS", 30 * 86_400.0)
            ),
            segment_batch_limit=int(
                os.environ.get("GLASSBOX_MAINTENANCE_SEGMENT_BATCH_LIMIT", 5_000)
            ),
            worm_anchor_dir=os.environ.get(
                "GLASSBOX_MAINTENANCE_WORM_ANCHOR_DIR", "./glassbox-worm-anchors"
            ),
        )
    except Exception as exc:  # noqa: BLE001 - top-level CLI boundary
        log_error(_logger, exc, message="evidence maintenance run failed")
        return 1

    failed = sum(1 for outcome in outcomes if outcome.action is RetentionAction.FAILED)
    _logger.info(
        "evidence maintenance run complete",
        extra={"segments_considered": len(outcomes), "failed": failed},
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
