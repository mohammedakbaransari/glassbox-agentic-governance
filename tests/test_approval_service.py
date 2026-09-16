"""Tests for the approval workflow service (Workstream D).

Covers the part of the plan's Workstream D exit criteria that
``tests/test_decision_service.py`` does not: the reviewer lifecycle beyond
creation (approve/reject/escalate/expire/revoke), that approval never causes
dispatch, and that replay never creates a real, operational workflow row.
"""

from __future__ import annotations

from typing import Any

import pytest

from glassbox.app.approval_service import ApprovalRecord, ApprovalService
from glassbox.domain.decision import ApprovalState, AuthorizationDecision, ExecutionStatus
from glassbox.domain.errors import ApprovalGatewayUnavailableError, ApprovalNotFoundError
from glassbox.store.repository import SQLiteWorkflowRepository
from glassbox.workflow.workflow_engine import WorkflowEngine
from tests.test_decision_service import ACTION_NAME, Runtime, action, credential, mandate


class RequireApprovalPdp:
    """A stub PDP that always routes to human review."""

    def decide(self, request: Any) -> Any:
        return AuthorizationDecision.require_approval(
            rationale="dual control required",
            policy_bundle_id="b",
            policy_bundle_sha256="0" * 64,
        )

    def active_bundle_digest(self, tenant_id: str) -> str:
        return "0" * 64


@pytest.fixture
def rt_with_workflow() -> Runtime:
    rt = Runtime()
    rt.runtime.mandate_store.put(mandate())
    rt.register_handler()
    rt.seed_baseline()
    engine = WorkflowEngine(repository=SQLiteWorkflowRepository(":memory:"))
    rt.runtime = rt.runtime.with_workflow_engine(engine)
    rt.service._runtime = rt.runtime  # type: ignore[attr-defined]
    object.__setattr__(rt.runtime, "policy_decision_point", RequireApprovalPdp())
    return rt


class TestApprovalGatewayWiring:
    def test_conforms_to_the_workflow_gateway_port(self) -> None:
        from glassbox.ports.workflow import WorkflowGateway

        assert isinstance(WorkflowEngine(), WorkflowGateway)

    def test_with_workflow_engine_rejects_a_non_conforming_object(self) -> None:
        from glassbox.app.errors import CompositionError

        rt = Runtime()
        with pytest.raises(CompositionError):
            rt.runtime.with_workflow_engine(object())  # type: ignore[arg-type]

    def test_operating_with_no_engine_wired_raises(self) -> None:
        rt = Runtime()
        service = ApprovalService(rt.runtime)
        with pytest.raises(ApprovalGatewayUnavailableError):
            service.approve("decision-1", actor="reviewer@example.com")


class TestApprovalLifecycle:
    def test_a_require_approval_decision_is_visible_in_the_pending_queue(
        self, rt_with_workflow: Runtime
    ) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        service = ApprovalService(rt_with_workflow.runtime)

        pending = service.list_pending()
        assert any(record.decision_id == outcome.decision_id for record in pending)

        status = service.get_status(outcome.decision_id)
        assert status is not None
        assert status.state is ApprovalState.PENDING

    def test_approving_transitions_to_approved_and_never_dispatches(
        self, rt_with_workflow: Runtime
    ) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        service = ApprovalService(rt_with_workflow.runtime)

        record = service.approve(outcome.decision_id, actor="reviewer@example.com", notes="ok")

        assert isinstance(record, ApprovalRecord)
        assert record.state is ApprovalState.APPROVED
        assert rt_with_workflow.dispatched == [], "approval must never auto-discharge dispatch"

    def test_rejecting_transitions_to_rejected(self, rt_with_workflow: Runtime) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        service = ApprovalService(rt_with_workflow.runtime)

        record = service.reject(outcome.decision_id, actor="reviewer@example.com", notes="no")

        assert record.state is ApprovalState.REJECTED
        assert rt_with_workflow.dispatched == []

    def test_escalating_reassigns_and_stays_under_review(self, rt_with_workflow: Runtime) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        service = ApprovalService(rt_with_workflow.runtime)

        record = service.escalate(
            outcome.decision_id,
            actor="reviewer@example.com",
            escalate_to="senior-reviewer",
            notes="over my authority",
        )

        assert record.state is ApprovalState.IN_REVIEW
        assert record.escalate_to == "senior-reviewer"

    def test_revoking_a_still_pending_request_is_terminal(self, rt_with_workflow: Runtime) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        service = ApprovalService(rt_with_workflow.runtime)

        record = service.revoke(outcome.decision_id, actor="system", notes="mandate revoked")

        assert record.state is ApprovalState.REVOKED

    def test_expire_overdue_moves_a_breached_workflow_to_expired(
        self, rt_with_workflow: Runtime
    ) -> None:
        engine = rt_with_workflow.runtime.workflow_engine
        assert engine is not None
        # sla_minutes=0 with backdated creation makes the workflow immediately
        # breached without needing to sleep in a test.
        workflow = engine.create_from_decision(
            decision_id="decision-expiry-1",
            agent_id="agent.treasury-bot",
            decision_type=ACTION_NAME,
            risk_score=10.0,
            violations=[],
            sla_minutes=0,
        )
        import datetime as dt

        workflow.created_at = (
            dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)
        ).isoformat()
        engine.repo.update(workflow)

        service = ApprovalService(rt_with_workflow.runtime)
        expired = service.expire_overdue()

        assert any(record.decision_id == "decision-expiry-1" for record in expired)
        status = service.get_status("decision-expiry-1")
        assert status is not None
        assert status.state is ApprovalState.EXPIRED

    def test_unknown_decision_raises_not_found(self, rt_with_workflow: Runtime) -> None:
        service = ApprovalService(rt_with_workflow.runtime)
        with pytest.raises(ApprovalNotFoundError):
            service.approve("no-such-decision", actor="reviewer@example.com")


class TestReplayNeverCreatesAWorkflow:
    """GB-012 extended to Workstream D: replay must stay free of side effects."""

    def test_replaying_a_require_approval_decision_does_not_create_a_workflow(
        self, rt_with_workflow: Runtime
    ) -> None:
        outcome = rt_with_workflow.service.decide_and_dispatch(credential(), action())
        assert outcome.decision.approval_id is not None

        engine = rt_with_workflow.runtime.workflow_engine
        assert engine is not None
        pending_before = len(engine.list_pending())

        principal = rt_with_workflow.runtime.identity_verifier.verify(credential(), now=0.0)
        replayed = rt_with_workflow.service.replay(principal, action())

        # REQUIRE_APPROVAL always reports PENDING_APPROVAL, replay or not (the
        # DecisionOutcome contract); what replay changes is that dispatch is
        # structurally unreachable and no real workflow row is created.
        assert replayed.execution.status is ExecutionStatus.PENDING_APPROVAL
        assert replayed.decision.approval_id is not None
        # A replay's synthetic approval id must never collide with a real,
        # persisted workflow -- and must create no new one.
        assert engine.get_by_decision(replayed.decision_id) is None
        assert len(engine.list_pending()) == pending_before


class TestQuorumApprovalMemoryLeak:
    """Regression for the 2026-09-16 review finding: a persist failure after
    quorum was reached used to leave ``WorkflowEngine._quorum_state`` holding
    an orphaned entry forever (an unbounded, if slow, in-process memory leak).
    """

    def _engine(self, **kwargs: Any) -> WorkflowEngine:
        return WorkflowEngine(repository=SQLiteWorkflowRepository(":memory:"), **kwargs)

    def _pending_workflow(self, engine: WorkflowEngine, decision_id: str) -> Any:
        return engine.create_from_decision(
            decision_id=decision_id,
            agent_id="agent.treasury-bot",
            decision_type=ACTION_NAME,
            risk_score=10.0,
            violations=[],
        )

    def test_quorum_is_reached_only_once_two_distinct_actors_approve(self) -> None:
        engine = self._engine()
        workflow = self._pending_workflow(engine, "decision-quorum-1")

        engine.approve(workflow.workflow_id, "reviewer_a", min_approvers=2)
        assert engine.repo.get(workflow.workflow_id).state == "pending"
        assert workflow.workflow_id in engine._quorum_state  # partial vote retained

        updated = engine.approve(workflow.workflow_id, "reviewer_b", min_approvers=2)
        assert updated is not None
        assert updated.state == "approved"
        assert workflow.workflow_id not in engine._quorum_state  # cleared on quorum

    def test_the_same_actor_approving_twice_does_not_inflate_the_quorum_count(self) -> None:
        engine = self._engine()
        workflow = self._pending_workflow(engine, "decision-quorum-2")

        engine.approve(workflow.workflow_id, "reviewer_a", min_approvers=2)
        engine.approve(workflow.workflow_id, "reviewer_a", min_approvers=2)

        assert engine.repo.get(workflow.workflow_id).state == "pending"

    def test_a_persist_failure_after_quorum_is_reached_still_clears_the_quorum_entry(
        self,
    ) -> None:
        engine = self._engine()
        workflow = self._pending_workflow(engine, "decision-quorum-3")
        engine.approve(workflow.workflow_id, "reviewer_a", min_approvers=2)

        def failing_update(instance: Any) -> None:
            raise RuntimeError("simulated transient persistence failure")

        original_update = engine.repo.update
        engine.repo.update = failing_update  # type: ignore[method-assign]
        try:
            with pytest.raises(RuntimeError):
                engine.approve(workflow.workflow_id, "reviewer_b", min_approvers=2)
        finally:
            engine.repo.update = original_update  # type: ignore[method-assign]

        assert (
            workflow.workflow_id not in engine._quorum_state
        ), "a failed persist must not orphan the in-memory quorum entry forever"

        # The vote count was lost with the entry (by design -- the in-memory
        # tracker is a fast-path cache, never the source of truth), so a fresh
        # approval starts the count over rather than silently re-approving.
        updated = engine.approve(workflow.workflow_id, "reviewer_b", min_approvers=2)
        assert updated is not None
        assert updated.state == "pending"

    def test_stale_quorum_entries_are_evicted_after_the_configured_ttl(self) -> None:
        engine = self._engine(quorum_entry_ttl_seconds=60.0)
        stale = self._pending_workflow(engine, "decision-quorum-stale")
        engine.approve(stale.workflow_id, "reviewer_a", min_approvers=2)
        assert stale.workflow_id in engine._quorum_state

        with engine._quorum_lock:
            engine._quorum_state[stale.workflow_id].first_seen_at -= 61.0

        # The sweep is lazy: it runs on the next approve() call, even one for
        # an unrelated workflow.
        fresh = self._pending_workflow(engine, "decision-quorum-fresh")
        engine.approve(fresh.workflow_id, "reviewer_x", min_approvers=2)

        assert stale.workflow_id not in engine._quorum_state
        assert fresh.workflow_id in engine._quorum_state
