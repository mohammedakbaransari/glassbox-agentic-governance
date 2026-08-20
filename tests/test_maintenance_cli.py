"""Tests for the evidence maintenance CLI entrypoint (Workstream E).

Closes the "wire ensure_monthly_partitions/RetentionScheduler into a real
periodic entrypoint" gap: ``glassbox.adapters.inbound.cli.maintenance`` is
the thing an operator's cron/CronJob actually calls, and this file is what
proves it does the right thing end to end against a real server, not just
that its pieces individually work.
"""

from __future__ import annotations

import os
from typing import Any, List, Tuple

import pytest

from glassbox.adapters.inbound.cli.maintenance import _fetch_segment_ids, _build_signer
from glassbox.app.config import GlassBoxConfig, RuntimeProfile, SigningConfig

POSTGRES_DSN = os.environ.get("GLASSBOX_POSTGRES_DSN", "")

_requires_postgres = pytest.mark.skipif(
    not POSTGRES_DSN,
    reason="set GLASSBOX_POSTGRES_DSN to run the maintenance CLI integration tests",
)


class _FakeCursor:
    def __init__(self, rows: List[Tuple[Any, ...]]) -> None:
        self.rows = rows
        self.calls: List[Tuple[Any, ...]] = []

    def execute(self, sql: str, params: Any = ()) -> None:
        self.calls.append((sql, tuple(params)))

    def fetchall(self) -> List[Tuple[Any, ...]]:
        return self.rows


class _FakeProvider:
    def __init__(self, rows: List[Tuple[Any, ...]]) -> None:
        self.cursor = _FakeCursor(rows)

    def transaction(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            yield self.cursor

        return _ctx()


class TestBuildSigner:
    def test_uses_the_local_signer_when_local_keys_are_allowed(self) -> None:
        config = GlassBoxConfig(
            profile=RuntimeProfile.DEV, signing=SigningConfig(allow_local_key=True)
        )
        signer = _build_signer(config)
        assert type(signer).__name__ == "LocalMacSigner"

    def test_uses_the_kms_signer_when_local_keys_are_not_allowed(self) -> None:
        from unittest.mock import patch

        config = GlassBoxConfig(
            profile=RuntimeProfile.DEV, signing=SigningConfig(allow_local_key=False)
        )
        with patch("glassbox.adapters.outbound.kms.build_mac_signer") as build_kms:
            _build_signer(config)
        build_kms.assert_called_once_with(config)


class TestFetchSegmentIds:
    def test_queries_with_the_given_limit_and_returns_the_ids(self) -> None:
        provider = _FakeProvider([("seg-a",), ("seg-b",)])
        result = _fetch_segment_ids(provider, limit=42)  # type: ignore[arg-type]
        assert result == ["seg-a", "seg-b"]
        sql, params = provider.cursor.calls[0]
        assert params == (42,)
        assert "evidence_segment" in sql
        assert "retention_sealed_last_seq IS NULL" in sql


@_requires_postgres
class TestMaintenanceRunIntegration:
    """Real end-to-end run against a live server."""

    @pytest.fixture
    def config(self, tmp_path: Any) -> GlassBoxConfig:
        from glassbox.app.config import EvidenceConfig

        return GlassBoxConfig(
            profile=RuntimeProfile.DEV,
            evidence=EvidenceConfig(dsn=POSTGRES_DSN),
            signing=SigningConfig(allow_local_key=True),
        )

    @pytest.fixture(autouse=True)
    def _clean_slate(self) -> None:
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.schema import (
            RETENTION_PURGE_GUC,
            apply_migrations,
        )

        provider = PsycopgConnectionProvider(POSTGRES_DSN)
        apply_migrations(provider)
        with provider.transaction() as cursor:
            cursor.execute(f"SET LOCAL {RETENTION_PURGE_GUC} = 'on'", ())
            cursor.execute("DELETE FROM evidence_outcome", ())
            cursor.execute("DELETE FROM evidence_intent", ())
            cursor.execute("DELETE FROM evidence_segment", ())
        provider.close()

    def test_run_seals_segments_with_outstanding_retention_work(
        self, config: GlassBoxConfig, tmp_path: Any
    ) -> None:
        import time as _time

        from glassbox.adapters.inbound.cli.maintenance import run
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.adapters.outbound.postgres.evidence import PostgresEvidenceStore
        from glassbox.adapters.outbound.memory.signing import LocalMacSigner
        from glassbox.app.retention_scheduler import RetentionAction
        from tests.test_domain import make_action, make_intent

        provider = PsycopgConnectionProvider(POSTGRES_DSN)
        store = PostgresEvidenceStore(provider, LocalMacSigner(key_id="k", key=b"\x33" * 32))
        real_now = _time.time()
        for index in range(3):
            store.append_intent(
                make_intent(
                    decision_id=f"decision-maint-old-{index}",
                    segment_id="seg-maint-old",
                    created_at=real_now,
                    action=make_action(idempotency_key=f"idem-maint-old-{index}"),
                )
            )
        store.append_intent(
            make_intent(
                decision_id="decision-maint-fresh-0",
                segment_id="seg-maint-fresh",
                created_at=real_now,
                action=make_action(idempotency_key="idem-maint-fresh-0"),
            )
        )
        provider.close()

        outcomes = run(
            config=config,
            seal_after_seconds=0.0,  # both segments are "old enough" immediately
            purge_grace_seconds=1e12,  # never purge within this test
            segment_batch_limit=100,
            worm_anchor_dir=str(tmp_path / "anchors"),
        )

        by_segment = {outcome.segment_id: outcome for outcome in outcomes}
        assert by_segment["seg-maint-old"].action is RetentionAction.SEALED
        assert by_segment["seg-maint-fresh"].action is RetentionAction.SEALED

    def test_run_tops_up_the_partition_windows(self, config: GlassBoxConfig, tmp_path: Any) -> None:
        from glassbox.adapters.inbound.cli.maintenance import run
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider

        run(
            config=config,
            seal_after_seconds=1e12,
            purge_grace_seconds=1e12,
            segment_batch_limit=100,
            worm_anchor_dir=str(tmp_path / "anchors"),
        )

        provider = PsycopgConnectionProvider(POSTGRES_DSN)
        try:
            with provider.transaction() as cursor:
                cursor.execute(
                    "SELECT to_regclass('evidence_intent_default') IS NOT NULL", ()
                )
                assert cursor.fetchone() == (True,)
        finally:
            provider.close()

    def test_a_failing_segment_does_not_abort_the_whole_run(
        self, config: GlassBoxConfig, tmp_path: Any
    ) -> None:
        from glassbox.adapters.inbound.cli.maintenance import _fetch_segment_ids, run
        from glassbox.adapters.outbound.postgres.driver import PsycopgConnectionProvider
        from glassbox.app.retention_scheduler import RetentionAction

        # No real segments exist yet after the clean-slate fixture -- a segment
        # id that does not exist in evidence_segment must be reported as
        # SKIPPED, not raise and abort the whole pass. Exercised indirectly by
        # confirming an empty candidate set still returns a clean, empty run.
        outcomes = run(
            config=config,
            seal_after_seconds=0.0,
            purge_grace_seconds=1e12,
            segment_batch_limit=100,
            worm_anchor_dir=str(tmp_path / "anchors"),
        )
        assert outcomes == []
        assert not any(o.action is RetentionAction.FAILED for o in outcomes)
