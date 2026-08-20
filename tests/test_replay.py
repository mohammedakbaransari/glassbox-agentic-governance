"""Tests for pure replay (GB-012).

v1's ``decision_replay.replay_one`` called the live ``pipeline.process()``
directly, so ``POST /decisions/<id>/replay`` could re-execute a side effect.
These tests prove the dispatcher is structurally unreachable during replay,
not merely unlikely to be reached.
"""

from __future__ import annotations

from typing import Any

import pytest

from glassbox.adapters.outbound.replay import NullDispatcher, build_null_dispatcher
from glassbox.app.decision_service import diff_outcomes
from glassbox.domain.action import ConsequenceClass
from glassbox.domain.decision import DecisionEffect, DenialReason, ExecutionStatus, StageStatus
from glassbox.domain.evidence import IntegrityStatus
from glassbox.ports.dispatcher import Dispatcher
from tests.test_decision_service import (
    ACTION_NAME,
    Runtime,
    _principal,
    action,
    credential,
    mandate,
)


class TestNullDispatcher:
    """The replay dispatcher conforms to the port but never performs an effect."""

    def test_it_conforms_to_the_dispatcher_port(self) -> None:
        assert isinstance(NullDispatcher(), Dispatcher)

    def test_it_raises_if_ever_invoked(self) -> None:
        from glassbox.domain.evidence import EvidenceReceipt
        from tests.test_domain import make_action

        dispatcher = NullDispatcher()
        receipt = EvidenceReceipt(
            decision_id="decision-1",
            segment_id="seg-1",
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="stub.key",
            persisted_at=1_760_000_000.0,
        )
        with pytest.raises(AssertionError):
            dispatcher.dispatch(make_action(), receipt, timeout_s=1.0, now=1_760_000_000.0)

    def test_the_factory_needs_no_configuration(self) -> None:
        from glassbox.app.config import GlassBoxConfig, RuntimeProfile

        dispatcher = build_null_dispatcher(GlassBoxConfig(profile=RuntimeProfile.DEV))
        assert isinstance(dispatcher, NullDispatcher)


@pytest.fixture
def rt() -> Runtime:
    return Runtime()


def _use_null_dispatcher(rt: Runtime) -> None:
    """Swap in the production replay dispatcher, as a real deployment would."""
    object.__setattr__(rt.runtime, "dispatcher", NullDispatcher())


class TestReplayNeverDispatches:
    """The load-bearing guarantee: an effect-worthy replay never executes."""

    def test_a_replayed_allow_is_marked_replayed_not_executed(self, rt: Runtime) -> None:
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)
        _use_null_dispatcher(rt)

        outcome = rt.service.replay(principal, action(monetary=101.0))

        assert outcome.decision.effect is DecisionEffect.ALLOW
        assert outcome.execution.status is ExecutionStatus.REPLAYED

    def test_replaying_an_allow_against_a_null_dispatcher_never_raises(self, rt: Runtime) -> None:
        """If replay ever called the dispatcher, NullDispatcher would raise --
        this test passing is itself proof dispatch was never reached."""
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)
        _use_null_dispatcher(rt)

        rt.service.replay(principal, action(monetary=101.0, idempotency_key="idem-replay-1"))

    def test_a_denied_replay_is_denied_not_replayed(self, rt: Runtime) -> None:
        principal = _principal(rt)
        _use_null_dispatcher(rt)
        outcome = rt.service.replay(principal, action())
        assert outcome.decision.effect is DecisionEffect.DENY
        assert outcome.execution.status is ExecutionStatus.DENIED

    def test_replay_writes_its_own_evidence(self, rt: Runtime) -> None:
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)
        _use_null_dispatcher(rt)

        outcome = rt.service.replay(principal, action(monetary=101.0))
        assert outcome.receipt is not None
        report = rt.runtime.evidence_store.verify(outcome.receipt.segment_id, now=0.0)
        assert report.status is IntegrityStatus.INTACT

    def test_replay_never_shares_a_decision_id_with_the_original(self, rt: Runtime) -> None:
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)

        original = rt.service.decide_and_dispatch(
            credential(), action(monetary=101.0, idempotency_key="idem-original")
        )
        _use_null_dispatcher(rt)
        replayed = rt.service.replay(
            principal,
            action(monetary=101.0, idempotency_key="idem-original"),
            causation_id=original.decision_id,
        )
        assert replayed.decision_id != original.decision_id

    def test_the_catalogue_stage_is_recorded_as_skipped(self, rt: Runtime) -> None:
        principal = _principal(rt)
        _use_null_dispatcher(rt)
        outcome = rt.service.replay(principal, action())
        stored = rt.runtime.evidence_store._segments[  # type: ignore[attr-defined]
            outcome.receipt.segment_id
        ]
        record = next(
            item.record for item in stored if item.record.decision_id == outcome.decision_id
        )
        by_stage = {stage.stage: stage for stage in record.stages}
        assert by_stage["catalogue"].status is StageStatus.SKIPPED

    def test_a_replayed_require_approval_decision_stays_pending_and_creates_no_workflow(
        self, rt: Runtime
    ) -> None:
        """Workstream D extension of GB-012: replay must not create a real,
        operational approval workflow row even when the re-evaluated decision
        would route to human review."""
        from glassbox.domain.decision import AuthorizationDecision
        from glassbox.store.repository import SQLiteWorkflowRepository
        from glassbox.workflow.workflow_engine import WorkflowEngine

        class RequireApprovalPdp:
            def decide(self, request: Any) -> Any:
                return AuthorizationDecision.require_approval(
                    rationale="dual control required",
                    policy_bundle_id="b",
                    policy_bundle_sha256="0" * 64,
                )

            def active_bundle_digest(self, tenant_id: str) -> str:
                return "0" * 64

        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        engine = WorkflowEngine(repository=SQLiteWorkflowRepository(":memory:"))
        rt.runtime = rt.runtime.with_workflow_engine(engine)
        rt.service._runtime = rt.runtime  # type: ignore[attr-defined]
        object.__setattr__(rt.runtime, "policy_decision_point", RequireApprovalPdp())
        principal = _principal(rt)
        _use_null_dispatcher(rt)

        replayed = rt.service.replay(principal, action(monetary=101.0))

        assert replayed.decision.effect is DecisionEffect.REQUIRE_APPROVAL
        assert replayed.execution.status is ExecutionStatus.PENDING_APPROVAL
        assert replayed.decision.approval_id is not None
        assert engine.get_by_decision(replayed.decision_id) is None
        assert engine.list_pending() == []


class TestDiffOutcomes:
    def test_no_diff_when_the_effect_is_unchanged(self, rt: Runtime) -> None:
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)
        first = rt.service.decide_and_dispatch(credential(), action(monetary=101.0))
        _use_null_dispatcher(rt)
        replayed = rt.service.replay(principal, action(monetary=101.0, idempotency_key="idem-0002"))

        diff = diff_outcomes(first.decision, replayed.decision)
        assert diff["effect_changed"] is False
        assert diff["reasons_added"] == []
        assert diff["reasons_removed"] == []

    def test_a_diff_is_reported_when_the_mandate_narrows_before_replay(self, rt: Runtime) -> None:
        rt.allow(ACTION_NAME)
        rt.runtime.mandate_store.put(mandate())
        rt.seed_baseline()
        principal = _principal(rt)
        first = rt.service.decide_and_dispatch(credential(), action(monetary=101.0))

        rt.runtime.mandate_store.put(mandate(version=2, max_consequence=ConsequenceClass.ADVISORY))
        _use_null_dispatcher(rt)
        replayed = rt.service.replay(principal, action(monetary=101.0, idempotency_key="idem-0003"))

        diff = diff_outcomes(first.decision, replayed.decision)
        assert diff["effect_changed"] is True
        assert diff["original_effect"] == "allow"
        assert diff["replayed_effect"] == "deny"
        assert DenialReason.MANDATE_EXCEEDED.value in diff["reasons_added"]
