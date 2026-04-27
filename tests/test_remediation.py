from __future__ import annotations

import os
import sys
import tempfile
import unittest

_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from glassbox.governance.models import (
    DecisionContext,
    DecisionRequest,
    DecisionType,
    Disposition,
    FinalStatus,
    PolicyResult,
    RiskLevel,
    RiskResult,
)
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.stage_registry import PipelineStage, PipelineStageConfig, StageRegistry
from glassbox.governance.threadpool_config import AsyncWorkQueue, QueueDepthMonitor
from glassbox.governance.write_ahead_log import WriteAheadLog
from glassbox.store.repository import SQLiteAuditRepository, SQLiteWorkflowRepository
from glassbox.workflow.workflow_engine import WorkflowEngine


class _BlockingStage(PipelineStage):
    def __init__(self):
        super().__init__("custom_guard")

    def execute(self, context):
        payload = context.get("payload") or {}
        if payload.get("block_custom_stage"):
            return False, "Custom stage blocked request"
        return True, None


class _RecordingAuditRepo:
    def __init__(self):
        self.records = []

    def save(self, record):
        self.records.append(record.decision_id)


class _RecordingWorkflowEngine:
    def __init__(self):
        self.calls = []

    def create_from_decision(self, **kwargs):
        self.calls.append(kwargs)
        return kwargs


class TestRemediationCoverage(unittest.TestCase):
    def test_stage_registry_executes_custom_stage_runtime(self):
        registry = StageRegistry()
        pipeline = GovernancePipeline(echo=False, stage_registry=registry)
        registry.register_stage(
            name="custom_guard",
            config=PipelineStageConfig(
                name="custom_guard",
                enabled=True,
                position=3.5,
                depends_on=["audit_record_init"],
                fallback_on_failure=False,
            ),
            stage_impl=_BlockingStage(),
        )

        response = pipeline.process(
            DecisionRequest(
                agent_id="stage-agent",
                decision_type=DecisionType.PROCUREMENT,
                payload={
                    "amount": 100,
                    "supplier_id": "SUP-001",
                    "category": "hardware",
                    "block_custom_stage": True,
                },
            )
        )

        self.assertEqual(response.final_status, FinalStatus.BLOCKED)
        self.assertTrue(any("Custom stage blocked request" in v for v in response.policy_violations))
        self.assertEqual(registry.stats()["execution_stats"].get("custom_guard:blocked"), 1)

    def test_async_queue_monitor_drains_completed_work(self):
        monitor = QueueDepthMonitor(max_depth_alert=10)
        queue = AsyncWorkQueue(queue_id="unit", monitor=monitor)
        try:
            future = queue.submit(lambda: 42)
            self.assertEqual(queue.get_result(future, timeout=2), 42)
            stats = monitor.get_stats("unit")
            self.assertEqual(stats["current_depth"], 0)
            self.assertEqual(stats["items_processed"], 1)
            self.assertIn("unit", monitor.get_all_stats())
        finally:
            queue.shutdown(wait=True)

    def test_wal_entry_id_round_trips_and_recovery_replays_missing_side_effects(self):
        fd, wal_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        pipeline = None
        recovery_pipeline = None
        try:
            wal = WriteAheadLog(db_path=wal_path)
            pipeline = GovernancePipeline(echo=False)
            record = pipeline.process(
                DecisionRequest(
                    agent_id="wal-agent",
                    decision_type=DecisionType.PROCUREMENT,
                    payload={"amount": 100, "supplier_id": "SUP-001", "category": "hardware"},
                )
            ).audit_record
            record.final_status = FinalStatus.PENDING_REVIEW
            record.policy_result = PolicyResult(passed=True, violations=[], warnings=["needs review"])
            record.risk_result = RiskResult(
                risk_score=55.0,
                risk_level=RiskLevel.MEDIUM,
                disposition=Disposition.HUMAN_REVIEW,
                factors=[],
            )

            entry = wal.begin_transaction(record.decision_id, record)
            wal.mark_side_effect(entry.entry_id, "audit_saved", success=True)

            replay_repo = _RecordingAuditRepo()
            workflow_engine = _RecordingWorkflowEngine()
            recovered_wal = WriteAheadLog(db_path=wal_path)
            recovery_pipeline = GovernancePipeline(
                echo=False,
                wal=recovered_wal,
                audit_repo=replay_repo,
                workflow_engine=workflow_engine,
                recover_wal_on_startup=True,
            )

            self.assertIsNotNone(recovered_wal.get_entry(entry.entry_id))
            self.assertEqual(recovered_wal.get_entry(entry.entry_id).decision_id, record.decision_id)
            self.assertEqual(recovered_wal.get_pending_entries(), [])
            self.assertEqual(replay_repo.records, [record.decision_id])
            self.assertEqual(len(workflow_engine.calls), 1)
            self.assertEqual(workflow_engine.calls[0]["decision_id"], record.decision_id)
            self.assertEqual(recovered_wal.stats()["state_counts"].get("COMMITTED"), 1)
        finally:
            if recovery_pipeline is not None:
                recovery_pipeline.shutdown()
            if pipeline is not None:
                pipeline.shutdown()
            try:
                os.remove(wal_path)
            except (FileNotFoundError, PermissionError):
                pass

    def test_pipeline_shutdown_closes_sqlite_backed_dependencies(self):
        audit_repo = SQLiteAuditRepository(":memory:")
        workflow_repo = SQLiteWorkflowRepository(":memory:")
        workflow_engine = WorkflowEngine(repository=workflow_repo)
        pipeline = GovernancePipeline(
            echo=False,
            audit_repo=audit_repo,
            workflow_engine=workflow_engine,
        )

        pipeline.shutdown()

        self.assertIsNone(audit_repo._shared_conn)
        self.assertIsNone(workflow_repo._shared_conn)


if __name__ == "__main__":
    unittest.main()
