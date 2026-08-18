"""The decision service (GB-008, GB-009, GB-010, GB-012, GB-013).

Rewires the ordering v1 got backwards. ``_stage_disposition`` in
``pipeline.py`` invoked the executor at stage 11 and ``_finalize`` wrote the
audit record at stage 12, where ``_persist_record`` swallowed every exception --
so the side effect could happen, and silently leave no trace of itself.

:meth:`DecisionService.decide_and_dispatch` enforces a single order that cannot
be bypassed by a caller, because each step's *output* is what the next step
needs:

    catalogue -> identity -> assertion check -> mandate -> policy -> risk
        -> limits -> baseline
        -> append_intent (durable receipt)
        -> dispatch (requires the receipt)
        -> append_outcome

Dispatch is reached from exactly one place, and only after ``append_intent`` has
returned. If evidence cannot be made durable, this method raises and the
dispatcher is never called -- proven, not merely arranged, by
``test_evidence_failure_never_reaches_dispatch``.

**Catalogue resolution runs first, ahead of identity, because it needs no
principal (GB-010).** :meth:`decide_and_dispatch_for_request` resolves an action
name against the governed catalogue before anything else -- an action that
turns out not to be governed, or whose attestations are not satisfied, is
evidenced as a denial with the most conservative possible placeholder action
(:attr:`DenialReason.ACTION_NOT_GOVERNED` / :attr:`DenialReason.ATTESTATION_NOT_SATISFIED`),
and every later stage is skipped.

**Identity is verified, then its transport-layer assertions are checked, and
both halves are evidenced separately (GB-009).** A malformed or untrusted
credential (``IdentityVerifier.verify`` failing) has no verified principal to
attribute a record to, so it is raised, not evidenced -- there is nothing yet to
name as the actor. Once a principal exists, a caller-supplied assertion (an
``X-Tenant-ID``-style header) that contradicts it is a **spoofing attempt**, not
a credential failure, and it is deliberately evidenced: :attr:`DenialReason.IDENTITY_UNVERIFIED`.
This is what closes v1's F1 defect at the API boundary -- a header could select
any tenant, and nothing recorded the attempt.

**Short-circuiting.** Once a stage denies, later stages that would *consume*
state (limits, baseline) are skipped rather than run and discarded -- consuming
an agent's rate budget for an action that was never going to happen would be its
own bug. Every skip is recorded as a :class:`~glassbox.domain.decision.StageOutcome`
(invariant I9); nothing is silently absent. Risk is the one exception: it is
always computed (or, if the engine is unavailable, conservatively estimated at
the action's consequence floor) because
:class:`~glassbox.domain.evidence.IntentRecord` requires a
:class:`~glassbox.domain.risk.RiskScore` unconditionally, and an already-denied
action's assessed risk is itself audit-worthy evidence.

**The action catalogue closes the remaining half of F1 (GB-010).** A caller may
either supply an already-built :class:`~glassbox.domain.action.ProposedAction`
directly to :meth:`decide_and_dispatch` -- a trusted, server-side construction
path, used internally and by GB-012's replay -- or, for real external callers,
name an action and supply transactional parameters to
:meth:`decide_and_dispatch_for_request`, which derives ``consequence`` and
``exposure`` from the governed catalogue and resolves any required
attestations from a system of record, never from the request itself. An action
absent from the catalogue, or a required attestation that cannot be resolved to
``True``, is refused and evidenced before any other stage runs.

**Replay is structurally incapable of dispatching (GB-012).**
:meth:`DecisionService.replay` re-evaluates a historical decision's principal
and action against the current mandate/policy/risk/limits/baseline wiring, so a
policy or risk-model change can be assessed against real historical cases. It
never calls the dispatcher -- an effect-worthy outcome is recorded as
:attr:`~glassbox.domain.decision.ExecutionStatus.REPLAYED`, not dispatched, on
a *replay* evidence record that never overwrites the original. v1's
``decision_replay.replay_one`` called the live pipeline directly, so replaying
a decision could re-execute its side effect; here there is no argument, no
configuration and no wiring choice that makes this code path dispatch.

**The tool registry runs first, ahead of even the action catalogue (GB-013).**
:meth:`decide_and_dispatch_for_tool_call` resolves a tool name and its
presented definition digest against the governed tool registry before
anything else -- an unregistered tool, or one whose definition no longer
matches what was registered, is refused with
:attr:`DenialReason.TOOL_NOT_GOVERNED` and every later stage, including
catalogue resolution, is skipped. This closes v1's F6 defect, where an
unmapped tool fell through to a flat, low-risk default and auto-executed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, List, Mapping, Optional, Tuple

from glassbox.app.composition import GovernanceRuntime
from glassbox.app.observability import bind_context, get_logger, log_error
from glassbox.app.telemetry import build_governance_metrics, traced_operation
from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.catalogue import ActionDefinition
from glassbox.domain.decision import (
    AuthorizationDecision,
    AuthorizationRequest,
    DecisionEffect,
    DenialReason,
    ExecutionOutcome,
    ExecutionStatus,
    StageOutcome,
    StageStatus,
)
from glassbox.domain.errors import (
    ActionNotGovernedError,
    AttestationUnavailableError,
    BaselineStoreUnavailable,
    CatalogueBundleUnavailableError,
    DispatchError,
    DomainValidationError,
    EvidenceWriteError,
    IdentityError,
    KillSwitchUnavailableError,
    LimitStoreUnavailable,
    MandateError,
    PolicyBundleUnavailableError,
    RiskModelUnavailableError,
    SigningUnavailableError,
    ToolNotGovernedError,
    ToolQuarantinedError,
    ToolRegistryUnavailableError,
)
from glassbox.domain.evidence import (
    EvidenceReceipt,
    IntentRecord,
    ModelProvenance,
    OutcomeRecord,
)
from glassbox.domain.identity import RawCredential, VerifiedPrincipal
from glassbox.domain.limits import LimitKey, LimitScope, LimitVerdict, Window
from glassbox.domain.mandate import Mandate
from glassbox.domain.prompt_injection import scan as scan_for_prompt_injection
from glassbox.domain.risk import RiskInputs, RiskScore
from glassbox.ports.baseline import BaselineKey, BaselineScope

__all__ = ["DecisionOutcome", "DecisionService", "diff_outcomes"]

_logger = get_logger("decision_service")

#: Module-level, like ``_logger`` -- not per-instance state. ``DecisionService``
#: is deliberately stateless (GB-027: its only slot is ``_runtime``), and a
#: telemetry instrument carries no per-decision or per-tenant data of its own,
#: so it belongs at the same scope logging already uses, not on the instance.
_metrics = build_governance_metrics()

#: Window used to bucket evidence into rotating segments. Plain arithmetic on
#: the injected clock's epoch seconds, deliberately avoiding ``time``/``datetime``
#: imports, which are banned in this layer (invariant I6: the clock is the only
#: notion of "now", and it must stay the only one).
_SEGMENT_ROTATION_SECONDS = 86_400

#: Window used for the monetary-exposure baseline check. GB-021/GB-022 will make
#: this a governed, per-metric setting; a fixed 30-day window is a reasonable
#: interim default and is exercised by the tests exactly as configured.
_BASELINE_WINDOW_SECONDS = 30 * _SEGMENT_ROTATION_SECONDS

#: Model version recorded when the risk engine could not be reached. Never a
#: real assessment -- the value is pinned to the action's consequence floor, and
#: the version string makes that unambiguous to anyone reading the evidence.
_RISK_UNAVAILABLE_MODEL_VERSION = "risk-engine-unavailable"

#: Placeholder consequence used only when the catalogue could not confirm what
#: an action actually is -- either it is not governed, or the catalogue itself
#: is unreachable. The most severe class and the widest blast radius, so that a
#: gap in the catalogue can never be mistaken for something benign (invariant
#: I4). Never dispatched: the catalogue stage denies before any later stage runs.
_UNGOVERNED_CONSEQUENCE = ConsequenceClass.IRREVERSIBLE
_UNGOVERNED_EXPOSURE = Exposure(blast_radius=BlastRadius.GLOBAL)


@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """What a caller receives from :meth:`DecisionService.decide_and_dispatch`.

    Attributes:
        decision_id: Correlation id for this decision.
        decision: The final authorization outcome.
        receipt: Proof the intent record is durable. Always present: evidence is
            written for denials too, since a denial is exactly the kind of event
            an auditor needs to see.
        execution: What happened after the decision. ``PENDING_APPROVAL`` for a
            require-approval decision or an unmet blocking obligation;
            ``DENIED`` for a denial; the dispatcher's outcome for an allow.
    """

    decision_id: str
    decision: AuthorizationDecision
    receipt: EvidenceReceipt
    execution: ExecutionOutcome


class DecisionService:
    """Orchestrates one governed decision end to end.

    Args:
        runtime: A fully composed, port-conforming object graph, normally from
            :func:`~glassbox.app.composition.build_runtime`.
    """

    __slots__ = ("_runtime",)

    def __init__(self, runtime: GovernanceRuntime) -> None:
        self._runtime = runtime

    def decide_and_dispatch(
        self,
        credential: RawCredential,
        action: ProposedAction,
        *,
        trace_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        provenance: Optional[ModelProvenance] = None,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> DecisionOutcome:
        """Verify, decide, evidence and (if allowed) dispatch one action.

        Args:
            credential: The caller's unverified credential.
            action: The server-derived action to evaluate.
            trace_id: Distributed trace id. Minted if omitted.
            decision_id: Correlation id. Minted if omitted.
            causation_id: Id of the decision that caused this one, if any.
            provenance: Model, prompt and context provenance for the record.
            asserted_tenant_id: A transport-layer tenant claim (e.g. a header) to
                be checked against the verified principal, never trusted on its
                own (invariant I2).
            asserted_subject: The transport-layer subject claim, checked the
                same way.

        Returns:
            The outcome. ``receipt`` is always populated: this method does not
            return without durable evidence having been written.

        Raises:
            glassbox.domain.errors.IdentityError: If the credential itself is
                invalid. No evidence is written -- there is no verified
                principal yet to attribute it to. A contradicting *assertion*
                (``asserted_tenant_id``/``asserted_subject``) is different: it is
                evidenced as a denial rather than raised (GB-009), because by
                that point a principal exists and the mismatch is itself a
                security event worth recording.
            glassbox.domain.errors.DomainValidationError: If ``action`` targets a
                tenant other than the credential's. This indicates a caller
                defect, not a governance denial, and is raised rather than
                evidenced.
            glassbox.domain.errors.EvidenceWriteError: If evidence cannot be made
                durable. **The dispatcher is never called on this path.**
            glassbox.domain.errors.SigningUnavailableError: Likewise, from the
                signer specifically.
        """
        runtime = self._runtime
        now = runtime.clock.now()
        decision_id = decision_id or _new_id("decision")
        trace_id = trace_id or _new_id("trace")
        provenance = provenance or ModelProvenance()

        with bind_context(decision_id=decision_id, trace_id=trace_id):
            principal = runtime.identity_verifier.verify(credential, now=now)
            principal.require_valid_at(now)

            with bind_context(tenant_id=principal.tenant_id, agent_ref=principal.agent_ref):
                if principal.tenant_id != action.tenant_id:
                    raise DomainValidationError(
                        "action targets a different tenant than the credential",
                        field="action",
                        principal_tenant=principal.tenant_id,
                        resource_tenant=action.tenant_id,
                    )
                return self._evaluate(
                    principal=principal,
                    action=action,
                    decision_id=decision_id,
                    trace_id=trace_id,
                    causation_id=causation_id,
                    provenance=provenance,
                    asserted_tenant_id=asserted_tenant_id,
                    asserted_subject=asserted_subject,
                    now=now,
                    catalogue_stage=_skipped(
                        "catalogue",
                        "action was supplied pre-built; catalogue resolution not applicable",
                    ),
                    catalogue_denial=None,
                    tool_registry_stage=_skipped(
                        "tool_registry",
                        "action was supplied pre-built; tool registry resolution not applicable",
                    ),
                    tool_registry_denial=None,
                )

    def decide_and_dispatch_for_request(
        self,
        credential: RawCredential,
        *,
        action_name: str,
        resource: ResourceRef,
        parameters: Mapping[str, Any],
        idempotency_key: str,
        trace_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        provenance: Optional[ModelProvenance] = None,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> DecisionOutcome:
        """Resolve a raw action request through the governed catalogue, then decide.

        This is the entry point real external callers should use (GB-010). Unlike
        :meth:`decide_and_dispatch`, the caller never supplies ``consequence`` or
        ``exposure`` directly -- those fields do not exist on this signature at
        all. They are derived from the catalogue entry for ``action_name``, and
        any attestation the entry requires is resolved from a system of record,
        never from ``parameters``. This closes F1's other half: v1 read facts
        like ``ctr_filed`` and ``change_window_approved`` out of the same request
        body a rule then trusted.

        Args:
            credential: The caller's unverified credential.
            action_name: Catalogue name of the action, e.g.
                ``payments.wire_transfer``.
            resource: The tenant-scoped target of the action.
            parameters: Caller-supplied transactional facts (amounts,
                destinations). Never a source of ``consequence``, ``exposure``,
                or attestation answers -- the catalogue and the attestation
                provider are the only sources for those.
            idempotency_key: Caller-stable key used for at-most-once dispatch.
            trace_id: Distributed trace id. Minted if omitted.
            decision_id: Correlation id. Minted if omitted.
            causation_id: Id of the decision that caused this one, if any.
            provenance: Model, prompt and context provenance for the record.
            asserted_tenant_id: A transport-layer tenant claim, checked against
                the verified principal (invariant I2).
            asserted_subject: The transport-layer subject claim, checked the
                same way.

        Returns:
            The outcome. ``receipt`` is always populated, including when the
            action turns out not to be governed or a required attestation could
            not be satisfied -- both are evidenced denials, not exceptions.

        Raises:
            glassbox.domain.errors.IdentityError: If the credential itself is
                invalid. See :meth:`decide_and_dispatch`.
            glassbox.domain.errors.DomainValidationError: If ``resource`` targets
                a tenant other than the credential's, or if ``action_name`` /
                ``parameters`` are not even structurally well-formed (not a
                governance denial -- there is no principal or governed action
                yet to evidence against, only a malformed request).
            glassbox.domain.errors.EvidenceWriteError: If evidence cannot be made
                durable. **The dispatcher is never called on this path.**
        """
        runtime = self._runtime
        now = runtime.clock.now()
        decision_id = decision_id or _new_id("decision")
        trace_id = trace_id or _new_id("trace")
        provenance = provenance or ModelProvenance()

        with bind_context(decision_id=decision_id, trace_id=trace_id):
            action, catalogue_stage, catalogue_denial = self._check_catalogue(
                tenant_id=resource.tenant_id,
                action_name=action_name,
                resource=resource,
                parameters=parameters,
                idempotency_key=idempotency_key,
                now=now,
            )

            principal = runtime.identity_verifier.verify(credential, now=now)
            principal.require_valid_at(now)

            with bind_context(tenant_id=principal.tenant_id, agent_ref=principal.agent_ref):
                if principal.tenant_id != resource.tenant_id:
                    raise DomainValidationError(
                        "action targets a different tenant than the credential",
                        field="resource",
                        principal_tenant=principal.tenant_id,
                        resource_tenant=resource.tenant_id,
                    )
                return self._evaluate(
                    principal=principal,
                    action=action,
                    decision_id=decision_id,
                    trace_id=trace_id,
                    causation_id=causation_id,
                    provenance=provenance,
                    asserted_tenant_id=asserted_tenant_id,
                    asserted_subject=asserted_subject,
                    now=now,
                    catalogue_stage=catalogue_stage,
                    catalogue_denial=catalogue_denial,
                    tool_registry_stage=_skipped(
                        "tool_registry", "action resolved via the action catalogue, not a tool call"
                    ),
                    tool_registry_denial=None,
                )

    def decide_and_dispatch_for_tool_call(
        self,
        credential: RawCredential,
        *,
        tool_name: str,
        definition_sha256: str,
        resource: ResourceRef,
        parameters: Mapping[str, Any],
        idempotency_key: str,
        trace_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        provenance: Optional[ModelProvenance] = None,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> DecisionOutcome:
        """Resolve an MCP-style tool call through the governed tool registry (GB-013).

        Closes fundamental problem F6: v1's ``_TOOL_TYPE_MAP`` mapped 13 tool
        names, and anything else fell through to a flat, low-risk default that
        auto-executed -- including, in the measured review,
        ``wipe_production_database``. Here an unregistered tool, or one whose
        definition digest does not match what was registered
        (:attr:`DenialReason.TOOL_NOT_GOVERNED`), is refused and evidenced
        before any other stage runs, the same way an ungoverned action is
        (GB-010).

        Args:
            credential: The caller's unverified credential.
            tool_name: Exact, registered tool identifier.
            definition_sha256: Hex digest of the tool definition being invoked
                (its description and schema) -- never trusted on its own; it
                must match the digest the tool was registered under.
            resource: The tenant-scoped target of the tool call.
            parameters: Caller-supplied transactional facts, fed to the
                registered tool's exposure derivation. Never a source of
                ``consequence`` or ``exposure`` directly.
            idempotency_key: Caller-stable key used for at-most-once dispatch.
            trace_id: Distributed trace id. Minted if omitted.
            decision_id: Correlation id. Minted if omitted.
            causation_id: Id of the decision that caused this one, if any.
            provenance: Model, prompt and context provenance for the record.
            asserted_tenant_id: A transport-layer tenant claim, checked against
                the verified principal (invariant I2).
            asserted_subject: The transport-layer subject claim, checked the
                same way.

        Returns:
            The outcome. ``receipt`` is always populated, including when the
            tool is ungoverned -- that is an evidenced denial, not an exception.

        Raises:
            glassbox.domain.errors.IdentityError: If the credential itself is
                invalid. See :meth:`decide_and_dispatch`.
            glassbox.domain.errors.DomainValidationError: If ``resource`` targets
                a tenant other than the credential's.
            glassbox.domain.errors.EvidenceWriteError: If evidence cannot be made
                durable. **The dispatcher is never called on this path.**
        """
        runtime = self._runtime
        now = runtime.clock.now()
        decision_id = decision_id or _new_id("decision")
        trace_id = trace_id or _new_id("trace")
        provenance = provenance or ModelProvenance()

        with bind_context(decision_id=decision_id, trace_id=trace_id):
            action, tool_registry_stage, tool_registry_denial = self._check_tool_registry(
                tenant_id=resource.tenant_id,
                tool_name=tool_name,
                definition_sha256=definition_sha256,
                resource=resource,
                parameters=parameters,
                idempotency_key=idempotency_key,
            )

            principal = runtime.identity_verifier.verify(credential, now=now)
            principal.require_valid_at(now)

            with bind_context(tenant_id=principal.tenant_id, agent_ref=principal.agent_ref):
                if principal.tenant_id != resource.tenant_id:
                    raise DomainValidationError(
                        "action targets a different tenant than the credential",
                        field="resource",
                        principal_tenant=principal.tenant_id,
                        resource_tenant=resource.tenant_id,
                    )
                return self._evaluate(
                    principal=principal,
                    action=action,
                    decision_id=decision_id,
                    trace_id=trace_id,
                    causation_id=causation_id,
                    provenance=provenance,
                    asserted_tenant_id=asserted_tenant_id,
                    asserted_subject=asserted_subject,
                    now=now,
                    catalogue_stage=_skipped(
                        "catalogue", "action resolved via tool registry, not the action catalogue"
                    ),
                    catalogue_denial=None,
                    tool_registry_stage=tool_registry_stage,
                    tool_registry_denial=tool_registry_denial,
                )

    def replay(
        self,
        principal: VerifiedPrincipal,
        action: ProposedAction,
        *,
        trace_id: Optional[str] = None,
        decision_id: Optional[str] = None,
        causation_id: Optional[str] = None,
        provenance: Optional[ModelProvenance] = None,
        now: Optional[float] = None,
    ) -> DecisionOutcome:
        """Re-evaluate a historical decision's inputs without dispatching (GB-012).

        v1's ``decision_replay.replay_one`` called the live ``pipeline.process()``
        directly, so replaying a decision could re-execute its side effect. This
        method never calls the dispatcher: an effect-worthy outcome is recorded
        as :attr:`~glassbox.domain.decision.ExecutionStatus.REPLAYED`, not
        dispatched, regardless of what the re-evaluated decision's effect is.
        This is a structural property of this code path, not a configuration a
        caller could get wrong -- there is no argument that makes it dispatch.

        ``principal`` and ``action`` are the historical inputs, reconstructed by
        the caller from evidence (:class:`~glassbox.domain.evidence.IntentRecord`).
        Identity is not re-verified and no transport-layer assertion is checked --
        replay evaluates mandate, policy, risk, limits and baseline against
        whatever the *current* wiring resolves, which is the point: comparing the
        replayed decision against the original is how a policy or risk-model
        change is assessed for its effect on a real historical case.

        Args:
            principal: The verified principal from the original decision.
            action: The server-derived action from the original decision.
            trace_id: Distributed trace id. Minted if omitted.
            decision_id: Correlation id for the replay record. Minted if
                omitted -- **never** the original decision's id; the two must
                remain distinguishable in evidence.
            causation_id: Defaults to nothing; callers replaying a specific
                historical decision should pass its ``decision_id`` here so the
                replay is traceable back to what it replays.
            provenance: Model, prompt and context provenance for the record.
            now: Evaluation time. Defaults to the runtime clock; replay against a
                historical ``now`` is supported by passing it explicitly.

        Returns:
            The outcome of the replay. ``execution.status`` is always
            :attr:`~glassbox.domain.decision.ExecutionStatus.REPLAYED` for an
            outcome that would otherwise have dispatched, and ``DENIED`` /
            ``PENDING_APPROVAL`` exactly as :meth:`decide_and_dispatch` would
            report them -- only the effect-worthy case changes, because that is
            the only case a live run would have dispatched.

        Raises:
            glassbox.domain.errors.EvidenceWriteError: If the replay's own
                evidence cannot be made durable. The replay record is real
                evidence, distinct from and never overwriting the original.
        """
        runtime = self._runtime
        now = now if now is not None else runtime.clock.now()
        decision_id = decision_id or _new_id("replay")
        trace_id = trace_id or _new_id("trace")
        provenance = provenance or ModelProvenance()

        with bind_context(decision_id=decision_id, trace_id=trace_id):
            with bind_context(tenant_id=principal.tenant_id, agent_ref=principal.agent_ref):
                return self._evaluate(
                    principal=principal,
                    action=action,
                    decision_id=decision_id,
                    trace_id=trace_id,
                    causation_id=causation_id,
                    provenance=provenance,
                    asserted_tenant_id="",
                    asserted_subject="",
                    now=now,
                    catalogue_stage=_skipped(
                        "catalogue", "replay: action reconstructed from historical evidence"
                    ),
                    catalogue_denial=None,
                    tool_registry_stage=_skipped(
                        "tool_registry", "replay: action reconstructed from historical evidence"
                    ),
                    tool_registry_denial=None,
                    suppress_dispatch=True,
                )

    # ----------------------------------------------------------------- #
    # Orchestration
    # ----------------------------------------------------------------- #

    @traced_operation("decision")
    def _evaluate(
        self,
        *,
        principal: VerifiedPrincipal,
        action: ProposedAction,
        tool_registry_stage: StageOutcome,
        tool_registry_denial: Optional[AuthorizationDecision],
        catalogue_stage: StageOutcome,
        catalogue_denial: Optional[AuthorizationDecision],
        decision_id: str,
        trace_id: str,
        causation_id: Optional[str],
        provenance: ModelProvenance,
        asserted_tenant_id: str,
        asserted_subject: str,
        now: float,
        suppress_dispatch: bool = False,
    ) -> DecisionOutcome:
        """Run every stage, write evidence, and dispatch if permitted."""
        runtime = self._runtime
        stages: List[StageOutcome] = [tool_registry_stage]
        limit_verdicts: List[LimitVerdict] = []
        limit_key: Optional[LimitKey] = None
        decision: Optional[AuthorizationDecision] = tool_registry_denial

        if decision is None:
            decision = catalogue_denial
            stages.append(catalogue_stage)
        else:
            stages.append(_skipped("catalogue", "action already denied by tool registry"))

        if decision is None:
            identity_stage, identity_denial = self._check_identity_assertion(
                principal, asserted_tenant_id, asserted_subject
            )
            if identity_denial is not None:
                decision = identity_denial
        else:
            identity_stage = _skipped("identity", "action already denied before identity check")
        stages.append(identity_stage)

        if decision is None:
            kill_switch_stage, kill_switch_denial = self._check_kill_switch(
                principal.tenant_id, action, now
            )
            if kill_switch_denial is not None:
                decision = kill_switch_denial
        else:
            kill_switch_stage = _skipped(
                "kill_switch", "action already denied before identity check"
            )
        stages.append(kill_switch_stage)

        if decision is None:
            mandate_stage, mandate_denial = self._check_mandate(principal, action, now)
            if mandate_denial is not None:
                decision = mandate_denial
        else:
            mandate_stage = _skipped("mandate", "action already denied")
        stages.append(mandate_stage)

        if decision is None:
            request = AuthorizationRequest(
                decision_id=decision_id, principal=principal, action=action, evaluated_at=now
            )
            decision, policy_stage = self._check_policy(request)
        else:
            policy_stage = _skipped("policy", "action already denied by mandate")
        stages.append(policy_stage)

        # Risk is never skipped: IntentRecord requires a score unconditionally,
        # and an already-denied action's assessed risk is itself worth recording.
        risk_score, risk_stage, risk_forced_denial = self._compute_risk(action, now)
        stages.append(risk_stage)
        if risk_forced_denial is not None and decision.effect is not DecisionEffect.DENY:
            decision = risk_forced_denial

        if decision.effect is not DecisionEffect.DENY:
            limit_key = self._limit_key_for(principal, action)
            verdict, limits_stage, limits_denial = self._check_limits(
                limit_key, action, decision_id, now
            )
            if verdict is not None:
                limit_verdicts.append(verdict)
            if limits_denial is not None:
                decision = limits_denial
        else:
            limits_stage = _skipped("limits", "action already denied")
        stages.append(limits_stage)

        if decision.effect is not DecisionEffect.DENY:
            baseline_stage, baseline_denial = self._check_baseline(principal, action, now)
            if baseline_denial is not None:
                decision = baseline_denial
                self._release_limit_budget(limit_key, limit_verdicts, decision_id)
        else:
            baseline_stage = _skipped("baseline", "action already denied")
        stages.append(baseline_stage)

        if decision.effect is DecisionEffect.DENY:
            for reason in decision.reasons:
                _metrics.record_denial(reason.value)
                if reason is DenialReason.DEPENDENCY_UNAVAILABLE:
                    _metrics.fail_closed_total.add(1, {"consequence": action.consequence.value})
                elif reason is DenialReason.LIMIT_EXCEEDED:
                    _metrics.limit_rejections_total.add(1)

        record = IntentRecord(
            decision_id=decision_id,
            segment_id=_segment_id_for(runtime, principal.tenant_id, now),
            tenant_id=principal.tenant_id,
            created_at=now,
            principal=principal,
            action=action,
            decision=decision,
            risk=risk_score,
            trace_id=trace_id,
            limits=tuple(limit_verdicts),
            stages=tuple(stages),
            provenance=provenance,
            causation_id=causation_id,
        )

        try:
            write_started_at = runtime.clock.now()
            receipt = runtime.evidence_store.append_intent(record)
            _metrics.evidence_write_latency_ms.record(
                (runtime.clock.now() - write_started_at) * 1000.0
            )
        except (EvidenceWriteError, SigningUnavailableError) as exc:
            # Nothing was dispatched and nothing will be: the caller sees the
            # failure directly. Releasing consumed budget is best-effort -- the
            # primary failure is what must reach the caller, not a secondary one.
            log_error(
                _logger, exc, message="evidence could not be made durable; refusing to proceed"
            )
            self._release_limit_budget(limit_key, limit_verdicts, decision_id, best_effort=True)
            raise

        execution = self._dispatch_if_permitted(
            action, decision, receipt, now, suppress_dispatch=suppress_dispatch
        )

        try:
            runtime.evidence_store.append_outcome(
                receipt, OutcomeRecord(decision_id=decision_id, outcome=execution)
            )
        except EvidenceWriteError as exc:
            # Off the critical path by design: a failure here is retried and
            # alerted, and must never retroactively authorise or forbid an
            # effect that has already happened.
            log_error(_logger, exc, message="outcome could not be recorded")

        return DecisionOutcome(
            decision_id=decision_id, decision=decision, receipt=receipt, execution=execution
        )

    def _dispatch_if_permitted(
        self,
        action: ProposedAction,
        decision: AuthorizationDecision,
        receipt: EvidenceReceipt,
        now: float,
        *,
        suppress_dispatch: bool = False,
    ) -> ExecutionOutcome:
        """Dispatch only an unconditional allow; everything else is recorded, not executed."""
        if decision.effect is DecisionEffect.DENY:
            return ExecutionOutcome(status=ExecutionStatus.DENIED, completed_at=now)
        if decision.effect is DecisionEffect.REQUIRE_APPROVAL or decision.blocking_obligations:
            # Obligation discharge is not yet built (tracked for a later card);
            # until it is, an unmet blocking obligation is treated exactly like
            # a pending approval -- dispatch withheld, nothing lost.
            return ExecutionOutcome(status=ExecutionStatus.PENDING_APPROVAL, completed_at=now)
        if suppress_dispatch:
            # GB-012: the one branch that would otherwise dispatch. The
            # dispatcher is never called -- not called-and-ignored, never
            # called at all -- which is what makes it structurally unreachable
            # during replay rather than merely unlikely to be reached.
            return ExecutionOutcome(status=ExecutionStatus.REPLAYED, completed_at=now)

        runtime = self._runtime
        try:
            return runtime.dispatcher.dispatch(
                action, receipt, timeout_s=runtime.config.dispatch.default_timeout_s, now=now
            )
        except DispatchError as exc:
            log_error(_logger, exc, message="dispatch failed")
            return ExecutionOutcome(
                status=ExecutionStatus.FAILED, completed_at=now, error_class=type(exc).__name__
            )

    # ----------------------------------------------------------------- #
    # Stages
    # ----------------------------------------------------------------- #

    def _check_tool_registry(
        self,
        *,
        tenant_id: str,
        tool_name: str,
        definition_sha256: str,
        resource: ResourceRef,
        parameters: Mapping[str, Any],
        idempotency_key: str,
    ) -> Tuple[ProposedAction, StageOutcome, Optional[AuthorizationDecision]]:
        """Resolve a tool call through the governed tool registry (GB-013).

        Runs before identity, like the action catalogue: resolution depends
        only on the tenant, tool name and presented digest, never on the
        caller's principal.
        """
        runtime = self._runtime
        try:
            tool_definition = runtime.tool_registry.resolve(tenant_id, tool_name, definition_sha256)
        except ToolRegistryUnavailableError as exc:
            log_error(_logger, exc, message="tool registry unavailable")
            return (
                self._ungoverned_placeholder(resource, tool_name, parameters, idempotency_key),
                StageOutcome(
                    stage="tool_registry", status=StageStatus.FAILED, reason="registry unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="tool registry unavailable"
                ),
            )
        except ToolQuarantinedError as exc:
            log_error(_logger, exc, message="tool is quarantined pending re-approval")
            return (
                self._ungoverned_placeholder(resource, tool_name, parameters, idempotency_key),
                StageOutcome(stage="tool_registry", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.TOOL_DEFINITION_CHANGED,
                    rationale=f"{tool_name!r} definition changed and awaits re-approval",
                ),
            )
        except ToolNotGovernedError as exc:
            log_error(_logger, exc, message="tool is not in the governed registry")
            return (
                self._ungoverned_placeholder(resource, tool_name, parameters, idempotency_key),
                StageOutcome(stage="tool_registry", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.TOOL_NOT_GOVERNED,
                    rationale=f"{tool_name!r} is not a governed tool at the presented definition",
                ),
            )

        definition = tool_definition.action
        action = ProposedAction(
            action=definition.action,
            resource=resource,
            consequence=definition.consequence,
            exposure=definition.exposure_rule.extract(parameters),
            idempotency_key=idempotency_key,
            parameters=tuple(sorted(parameters.items())),
        )
        return action, StageOutcome(stage="tool_registry", status=StageStatus.EXECUTED), None

    def _check_catalogue(
        self,
        *,
        tenant_id: str,
        action_name: str,
        resource: ResourceRef,
        parameters: Mapping[str, Any],
        idempotency_key: str,
        now: float,
    ) -> Tuple[ProposedAction, StageOutcome, Optional[AuthorizationDecision]]:
        """Resolve ``action_name`` through the governed catalogue (GB-010).

        Runs before identity is even checked: catalogue resolution depends only
        on the tenant and the action name, never on the caller's principal, so
        there is no reason to withhold it pending identity -- and every outcome,
        including "not governed", needs a :class:`ProposedAction` to attribute
        evidence to.

        Returns:
            A tuple of the resolved (or placeholder) action, its stage outcome,
            and a denial if the action could not be governed or a required
            attestation was not satisfied.
        """
        runtime = self._runtime
        try:
            definition = runtime.action_catalogue.resolve(tenant_id, action_name)
        except CatalogueBundleUnavailableError as exc:
            log_error(_logger, exc, message="action catalogue unavailable")
            return (
                self._ungoverned_placeholder(resource, action_name, parameters, idempotency_key),
                StageOutcome(
                    stage="catalogue", status=StageStatus.FAILED, reason="catalogue unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="action catalogue unavailable"
                ),
            )
        except ActionNotGovernedError as exc:
            log_error(_logger, exc, message="action is not in the governed catalogue")
            return (
                self._ungoverned_placeholder(resource, action_name, parameters, idempotency_key),
                StageOutcome(stage="catalogue", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.ACTION_NOT_GOVERNED,
                    rationale=f"{action_name!r} has no entry in the governed action catalogue",
                ),
            )

        action = ProposedAction(
            action=definition.action,
            resource=resource,
            consequence=definition.consequence,
            exposure=definition.exposure_rule.extract(parameters),
            idempotency_key=idempotency_key,
            parameters=tuple(sorted(parameters.items())),
        )

        violations = definition.validate_parameters(parameters)
        if violations:
            raise DomainValidationError(
                "parameters do not satisfy the action's governed schema (GB-029)",
                field="parameters",
                action=action_name,
                violations="; ".join(violations),
            )

        flagged_fields = _scan_untrusted_text_fields(definition, parameters)
        if flagged_fields:
            return (
                action,
                StageOutcome(stage="catalogue", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.PROMPT_INJECTION_DETECTED,
                    rationale=(
                        "untrusted text field(s) "
                        f"{', '.join(flagged_fields)} matched an injection pattern"
                    ),
                ),
            )

        unsatisfied = self._first_unsatisfied_attestation(tenant_id, resource, definition, now=now)
        if unsatisfied is not None:
            return (
                action,
                StageOutcome(stage="catalogue", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.ATTESTATION_NOT_SATISFIED,
                    rationale=f"required attestation {unsatisfied!r} was not satisfied",
                ),
            )
        return action, StageOutcome(stage="catalogue", status=StageStatus.EXECUTED), None

    def _first_unsatisfied_attestation(
        self, tenant_id: str, resource: ResourceRef, definition: ActionDefinition, *, now: float
    ) -> Optional[str]:
        """Return the name of the first required attestation that did not hold.

        An attestation that cannot be resolved is indistinguishable, to the
        caller, from one that resolved to ``False``: both fail closed. This is
        the direct fix for v1 accepting ``ctr_filed: true`` as a self-asserted
        field in the same request a rule then trusted.
        """
        runtime = self._runtime
        for name in definition.required_attestations:
            try:
                satisfied = runtime.attestation_provider.resolve(tenant_id, resource, name, now=now)
            except AttestationUnavailableError as exc:
                log_error(_logger, exc, message="attestation could not be resolved")
                return name
            if not satisfied:
                return name
        return None

    @staticmethod
    def _ungoverned_placeholder(
        resource: ResourceRef,
        action_name: str,
        parameters: Mapping[str, Any],
        idempotency_key: str,
    ) -> ProposedAction:
        """Build the most conservative action possible when nothing is known.

        Never dispatched -- the catalogue stage's denial short-circuits every
        later stage -- but :class:`~glassbox.domain.evidence.IntentRecord`
        requires a well-formed :class:`ProposedAction` regardless, so the denial
        itself can be evidenced.
        """
        return ProposedAction(
            action=action_name,
            resource=resource,
            consequence=_UNGOVERNED_CONSEQUENCE,
            exposure=_UNGOVERNED_EXPOSURE,
            idempotency_key=idempotency_key,
            parameters=tuple(sorted(parameters.items())),
        )

    def _check_identity_assertion(
        self, principal: VerifiedPrincipal, asserted_tenant_id: str, asserted_subject: str
    ) -> Tuple[StageOutcome, Optional[AuthorizationDecision]]:
        """Check a transport-layer identity claim against the verified principal.

        Regression for F1: v1 copied ``X-Tenant-ID`` / ``X-User-ID`` headers
        verbatim into the request context, so any holder of the single shared API
        key could act as any tenant or user. Here the header is, at most, a claim
        to be *checked*; a mismatch is refused and evidenced as a denial rather
        than silently overridden or merely raised past the audit trail.
        """
        try:
            self._runtime.identity_verifier.assert_matches_assertion(
                principal,
                asserted_tenant_id=asserted_tenant_id,
                asserted_subject=asserted_subject,
            )
        except IdentityError as exc:
            log_error(_logger, exc, message="asserted identity contradicts the verified principal")
            return (
                StageOutcome(
                    stage="identity", status=StageStatus.FAILED, reason="asserted identity mismatch"
                ),
                AuthorizationDecision.deny(
                    DenialReason.IDENTITY_UNVERIFIED,
                    rationale="asserted identity does not match the verified credential",
                ),
            )
        return StageOutcome(stage="identity", status=StageStatus.EXECUTED), None

    def _check_kill_switch(
        self, tenant_id: str, action: ProposedAction, now: float
    ) -> Tuple[StageOutcome, Optional[AuthorizationDecision]]:
        """Tenant or global emergency stop, checked before any mandate (GB-016).

        An engaged switch denies every non-advisory action outright, regardless
        of what any individual agent's mandate would otherwise permit -- v1 had
        no equivalent of this at all.
        """
        del now  # unused: the kill switch has no time-varying state
        if action.consequence.may_degrade_on_dependency_failure:
            return _skipped("kill_switch", "action is advisory"), None
        runtime = self._runtime
        try:
            engaged = (
                runtime.kill_switch.is_globally_engaged()
                or runtime.kill_switch.is_tenant_engaged(tenant_id)
            )
        except KillSwitchUnavailableError as exc:
            log_error(_logger, exc, message="kill switch unavailable")
            return (
                StageOutcome(
                    stage="kill_switch", status=StageStatus.FAILED, reason="switch unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="kill switch unavailable"
                ),
            )
        if engaged:
            return (
                StageOutcome(stage="kill_switch", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.KILL_SWITCH_ENGAGED, rationale="emergency stop is engaged"
                ),
            )
        return StageOutcome(stage="kill_switch", status=StageStatus.EXECUTED), None

    def _check_mandate(
        self, principal: VerifiedPrincipal, action: ProposedAction, now: float
    ) -> Tuple[StageOutcome, Optional[AuthorizationDecision]]:
        """Coarse, deny-by-default ceiling, evaluated before policy.

        Consults the fast revocation deny list (GB-016) for an agent that has
        an active mandate: an operator's emergency revocation must take effect
        on the very next decision, not wait for a full mandate row to be
        rewritten. An agent with *no* mandate at all is ``MANDATE_MISSING``
        regardless of the deny list -- ``is_revoked`` treats an unknown agent as
        revoked too (it cannot prove otherwise), which would otherwise mask the
        more specific reason.
        """
        runtime = self._runtime
        try:
            mandate: Optional[Mandate] = runtime.mandate_store.get(
                principal.tenant_id, principal.agent_ref, now=now
            )
        except MandateError as exc:
            if action.consequence.may_degrade_on_dependency_failure:
                return (
                    _skipped("mandate", "mandate store unavailable; action is advisory"),
                    None,
                )
            log_error(_logger, exc, message="mandate store unavailable")
            return (
                StageOutcome(
                    stage="mandate", status=StageStatus.FAILED, reason="store unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="mandate store unavailable"
                ),
            )

        if mandate is None:
            return (
                StageOutcome(stage="mandate", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.MANDATE_MISSING, rationale="no active mandate for this agent"
                ),
            )

        try:
            revoked = runtime.mandate_store.is_revoked(
                principal.tenant_id, principal.agent_ref, now=now
            )
        except MandateError as exc:
            if action.consequence.may_degrade_on_dependency_failure:
                return (
                    _skipped("mandate", "mandate store unavailable; action is advisory"),
                    None,
                )
            log_error(_logger, exc, message="mandate store unavailable")
            return (
                StageOutcome(
                    stage="mandate", status=StageStatus.FAILED, reason="store unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="mandate store unavailable"
                ),
            )
        if revoked:
            return (
                StageOutcome(stage="mandate", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.MANDATE_REVOKED, rationale="agent's authority has been revoked"
                ),
            )

        verdict = mandate.permits(action, now=now)
        if not verdict.permitted:
            return (
                StageOutcome(stage="mandate", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.MANDATE_EXCEEDED,
                    rationale=verdict.detail or "mandate does not permit this action",
                ),
            )
        return StageOutcome(stage="mandate", status=StageStatus.EXECUTED), None

    def _check_policy(
        self, request: AuthorizationRequest
    ) -> Tuple[AuthorizationDecision, StageOutcome]:
        """Deny-by-default evaluation. Never degrades, regardless of consequence."""
        try:
            decision = self._runtime.policy_decision_point.decide(request)
        except PolicyBundleUnavailableError as exc:
            log_error(_logger, exc, message="policy bundle unavailable")
            return (
                AuthorizationDecision.deny(
                    DenialReason.POLICY_BUNDLE_UNAVAILABLE, rationale="no active policy bundle"
                ),
                StageOutcome(
                    stage="policy", status=StageStatus.FAILED, reason="bundle unavailable"
                ),
            )
        return decision, StageOutcome(stage="policy", status=StageStatus.EXECUTED)

    def _compute_risk(
        self, action: ProposedAction, now: float
    ) -> Tuple[RiskScore, StageOutcome, Optional[AuthorizationDecision]]:
        """Score risk, or conservatively estimate it if the engine is unreachable."""
        inputs = RiskInputs(
            consequence=action.consequence, exposure=action.exposure, evaluated_at=now
        )
        try:
            score = self._runtime.risk_engine.score(inputs)
            return score, StageOutcome(stage="risk", status=StageStatus.EXECUTED), None
        except RiskModelUnavailableError as exc:
            log_error(_logger, exc, message="risk engine unavailable")
            # Never a real assessment: pinned to the consequence floor, so an
            # outage can never be mistaken for a benign score.
            placeholder = RiskScore(
                value=0.0, model_version=_RISK_UNAVAILABLE_MODEL_VERSION, inputs=inputs
            ).with_consequence_floor()
            denial = AuthorizationDecision.deny(
                DenialReason.DEPENDENCY_UNAVAILABLE, rationale="risk engine unavailable"
            )
            return (
                placeholder,
                StageOutcome(stage="risk", status=StageStatus.FAILED, reason="engine unavailable"),
                denial,
            )

    def _check_limits(
        self, key: LimitKey, action: ProposedAction, decision_id: str, now: float
    ) -> Tuple[Optional[LimitVerdict], StageOutcome, Optional[AuthorizationDecision]]:
        """Atomic check-and-consume against the agent's velocity budget."""
        try:
            verdict = self._runtime.limit_store.try_consume(
                key, cost=1.0, decision_id=decision_id, now=now
            )
        except LimitStoreUnavailable as exc:
            if action.consequence.may_degrade_on_dependency_failure:
                return (
                    None,
                    _skipped("limits", "limit store unavailable; action is advisory"),
                    None,
                )
            log_error(_logger, exc, message="limit store unavailable")
            return (
                None,
                StageOutcome(stage="limits", status=StageStatus.FAILED, reason="store unavailable"),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="limit store unavailable"
                ),
            )

        if not verdict.admitted:
            return (
                verdict,
                StageOutcome(stage="limits", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.LIMIT_EXCEEDED,
                    rationale=f"velocity limit exceeded: {verdict.observed}/{verdict.limit}",
                ),
            )
        return verdict, StageOutcome(stage="limits", status=StageStatus.EXECUTED), None

    def _check_baseline(
        self, principal: VerifiedPrincipal, action: ProposedAction, now: float
    ) -> Tuple[StageOutcome, Optional[AuthorizationDecision]]:
        """Compare monetary exposure against the agent's behavioural baseline."""
        if action.exposure.monetary is None:
            return _skipped("baseline", "action carries no quantifiable exposure to baseline"), None

        runtime = self._runtime
        key = BaselineKey(
            tenant_id=principal.tenant_id,
            scope=BaselineScope.AGENT,
            subject=principal.agent_ref,
            metric="exposure_monetary",
            window=Window(_BASELINE_WINDOW_SECONDS),
        )
        try:
            verdict = runtime.baseline_store.evaluate(
                key,
                action.exposure.monetary,
                peer_group=action.action,
                threshold=runtime.config.baseline.anomaly_threshold,
                now=now,
            )
        except BaselineStoreUnavailable as exc:
            if action.consequence.may_degrade_on_dependency_failure:
                return _skipped("baseline", "baseline store unavailable; action is advisory"), None
            log_error(_logger, exc, message="baseline store unavailable")
            return (
                StageOutcome(
                    stage="baseline", status=StageStatus.FAILED, reason="store unavailable"
                ),
                AuthorizationDecision.deny(
                    DenialReason.DEPENDENCY_UNAVAILABLE, rationale="baseline store unavailable"
                ),
            )

        if verdict.anomalous:
            return (
                StageOutcome(stage="baseline", status=StageStatus.EXECUTED),
                AuthorizationDecision.deny(
                    DenialReason.BASELINE_ANOMALY,
                    rationale=f"observation deviates {verdict.z_score} sigma from baseline",
                ),
            )
        runtime.baseline_store.observe(key, action.exposure.monetary, now=now)
        return StageOutcome(stage="baseline", status=StageStatus.EXECUTED), None

    # ----------------------------------------------------------------- #
    # Helpers
    # ----------------------------------------------------------------- #

    def _limit_key_for(self, principal: VerifiedPrincipal, action: ProposedAction) -> LimitKey:
        """Build the velocity counter key for one agent and action."""
        return LimitKey(
            tenant_id=principal.tenant_id,
            scope=LimitScope.AGENT,
            subject=principal.agent_ref,
            window=Window(self._runtime.config.limits.default_window_seconds),
            action=action.action,
        )

    def _release_limit_budget(
        self,
        key: Optional[LimitKey],
        verdicts: List[LimitVerdict],
        decision_id: str,
        *,
        best_effort: bool = False,
    ) -> None:
        """Return budget consumed by a decision that will not execute.

        Args:
            key: The counter that was consumed, if any.
            verdicts: Limit verdicts recorded so far; only an admitted one holds
                budget worth returning.
            decision_id: Correlation id used to identify the reservation.
            best_effort: When ``True``, a release failure is logged but not
                raised, because it is secondary to a failure already in flight
                (invariant I5 is about never *silently* swallowing -- this is
                logged, not silent).
        """
        if key is None or not verdicts or not verdicts[-1].admitted:
            return
        try:
            self._runtime.limit_store.release(key, decision_id=decision_id)
        except LimitStoreUnavailable as exc:
            if not best_effort:
                raise
            log_error(_logger, exc, message="could not release limit budget")


def _skipped(stage: str, reason: str) -> StageOutcome:
    """Build a ``SKIPPED`` stage outcome (invariant I9: never silently absent)."""
    return StageOutcome(stage=stage, status=StageStatus.SKIPPED, reason=reason)


def _scan_untrusted_text_fields(
    definition: ActionDefinition, parameters: Mapping[str, Any]
) -> Tuple[str, ...]:
    """Scan only the fields the catalogue names as untrusted text (GB-029).

    A business field is never passed to
    :func:`~glassbox.security.prompt_injection.scan` -- only fields named in
    ``untrusted_text_fields`` are, which is what keeps a legitimate business
    corpus at a zero false-positive rate while still detecting injection
    content on the one class of field where it is a risk.
    """
    flagged = []
    for field_name in definition.untrusted_text_fields:
        value = parameters.get(field_name)
        if isinstance(value, str) and scan_for_prompt_injection(field_name, value).flagged:
            flagged.append(field_name)
    return tuple(flagged)


def _segment_id_for(runtime: GovernanceRuntime, tenant_id: str, now: float) -> str:
    """Bucket evidence into rotating, per-tenant segments.

    Plain integer division of epoch seconds -- no ``time``/``datetime`` import,
    which would violate invariant I6 in spirit even in the app layer.
    """
    period = int(now // _SEGMENT_ROTATION_SECONDS)
    return f"{runtime.config.evidence.segment_prefix}-{tenant_id}-{period}"


def _new_id(prefix: str) -> str:
    """Mint a correlation id. Uniqueness only; not part of any evidence hash."""
    return f"{prefix}-{uuid.uuid4().hex}"


def diff_outcomes(
    original: AuthorizationDecision, replayed: AuthorizationDecision
) -> Mapping[str, Any]:
    """Compare a historical decision against a replayed one (GB-012).

    Deliberately narrow: it reports whether the effect and the denial reasons
    changed, which is what "did this policy/risk change alter the outcome"
    needs. It does not diff rationale text or bundle digests, which change
    routinely without the decision itself changing.
    """
    return {
        "effect_changed": original.effect is not replayed.effect,
        "original_effect": original.effect.value,
        "replayed_effect": replayed.effect.value,
        "reasons_added": sorted(
            reason.value for reason in set(replayed.reasons) - set(original.reasons)
        ),
        "reasons_removed": sorted(
            reason.value for reason in set(original.reasons) - set(replayed.reasons)
        ),
    }
