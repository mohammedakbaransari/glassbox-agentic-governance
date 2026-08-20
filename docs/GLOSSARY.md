# GlassBox Glossary

Technical terms and concepts used throughout the GlassBox framework.

GlassBox is a hexagonal-architecture governance kernel built around
`DecisionService`: `glassbox/domain` (pure value objects and rules),
`glassbox/ports` (Protocol interfaces), `glassbox/app` (orchestration), and
`glassbox/adapters` (inbound/outbound implementations). Two modules outside
this layering survive by exception, not by inertia:
`glassbox/workflow/workflow_engine.py` and `glassbox/store/repository.py`
(trimmed to its `WorkflowRepository`/`SQLiteWorkflowRepository` classes) —
together they are the sanctioned implementation reached through
`glassbox.ports.workflow.WorkflowGateway`, used by `ApprovalService`. See
[ARCHITECTURE.md](ARCHITECTURE.md).

---

## Action & Resource

**`ProposedAction`**
The concrete action an agent wishes to perform: an action name, a target resource, a consequence class, an exposure, and an idempotency key. Submitted to `DecisionService` for authorization.
- See: [domain/action.py](../glassbox/domain/action.py)

**`ResourceRef`**
Identifies the resource a `ProposedAction` targets (kind, id, tenant).
- See: [domain/action.py](../glassbox/domain/action.py)

**`Exposure` / `BlastRadius` / `ConsequenceClass`**
The quantified potential impact of an action (monetary value, record count, blast radius tier) and how reversible it is (`ADVISORY` → `IRREVERSIBLE`). Compared against a mandate's ceiling and a risk consequence floor.
- See: [domain/action.py](../glassbox/domain/action.py)

**Action Catalogue**
The registry of actions GlassBox knows how to govern, including their parameter schema and exposure rules. An action absent from the catalogue is denied by default (`DenialReason.ACTION_NOT_GOVERNED`).
- See: [domain/catalogue.py](../glassbox/domain/catalogue.py)

**Tool Registry**
The registry of tools an agent may call, keyed by a definition digest. A tool whose definition has changed since it was registered is denied (`DenialReason.TOOL_DEFINITION_CHANGED`), closing the "tool rug-pull" threat.
- See: [domain/tool_registry.py](../glassbox/domain/tool_registry.py)

---

## Authorization

**`AuthorizationRequest`**
The input to a decision: a `ProposedAction`, the identity presenting it, and any supporting context.
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`AuthorizationDecision`**
The output of a decision: an effect (`ALLOW` / `DENY` / `ALLOW_WITH_OBLIGATIONS` / `REQUIRE_APPROVAL`), a rationale (always populated), and (when denied) one or more `DenialReason` values.
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`Obligation`**
A condition attached to an `ALLOW` that must be discharged for the action to be considered complete (e.g., a required follow-up notification or approval).
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`Approval` / `ApprovalState`**
A durable, trackable approval record for a decision that requires human sign-off (`PENDING` → `IN_REVIEW` → `APPROVED`/`REJECTED`/`REVOKED`/`EXPIRED`).
- See: [domain/decision.py](../glassbox/domain/decision.py)

**`ApprovalService`**
The application service that turns `ApprovalState` into an operable lifecycle: approve, reject, escalate, expire. Never dispatches an effect itself; never imports a concrete workflow engine, only `WorkflowGateway`.
- See: [app/approval_service.py](../glassbox/app/approval_service.py)

**`WorkflowGateway`**
The port `ApprovalService` depends on. Satisfied structurally (no adapter shim needed) by `glassbox.workflow.workflow_engine.WorkflowEngine`, backed by `glassbox.store.repository.SQLiteWorkflowRepository`.
- See: [ports/workflow.py](../glassbox/ports/workflow.py)

**Risk-threshold gating**
An opt-in control (`RiskConfig.enforce_threshold`/`deny_level`) that denies a decision (`DenialReason.RISK_THRESHOLD_EXCEEDED`) when its computed `RiskScore` exceeds a configured band. Off by default: risk scoring is otherwise pure observability, never silently gating.
- See: [app/decision_service.py](../glassbox/app/decision_service.py)

**`DecisionService`**
The application-layer orchestrator: identity → mandate → policy → risk → limits → baseline → evidence → dispatch → outcome. Evaluates an `AuthorizationRequest` and, if allowed, dispatches it.
- See: [app/decision_service.py](../glassbox/app/decision_service.py)

---

## Identity, Mandates & Tenancy

**`RawCredential`**
An unverified credential presented by a caller (type, material, presentation time), the starting point for identity resolution.
- See: [domain/identity.py](../glassbox/domain/identity.py)

**`DelegationChain` / `DelegationHop`**
The verified chain of principals that delegated authority to the agent making a request, used to bound how far delegated authority can be re-delegated.
- See: [domain/identity.py](../glassbox/domain/identity.py)

**`Mandate` / `MandateVerdict`**
The set of actions, resources, and exposure ceilings an agent is authorized to act on, and the result of checking a request against it.
- See: [domain/mandate.py](../glassbox/domain/mandate.py)

**`ActionResourceGrant`**
A resource-scoped mandate grant: authority for a specific `(action, resource_kind, resource_id)` tuple, not just an action name. Narrower than a blanket `allowed_actions` grant.
- See: [domain/mandate.py](../glassbox/domain/mandate.py)

**`ToolGrant`**
A specific grant of tool access issued to an agent under a mandate.
- See: [domain/mandate.py](../glassbox/domain/mandate.py)

**`Tenant` / `TenantStatus`**
A first-class, validated entity for an onboarded organization boundary (`PENDING` → `ACTIVE` → `SUSPENDED`/`OFFBOARDED`). Every domain record already carries a `tenant_id` string; `Tenant` gives that identifier a queryable home for an admin surface.
- See: [domain/tenancy.py](../glassbox/domain/tenancy.py)

**`AuditEvent`**
A read-model record of administrative/platform activity (tenant onboarding, mandate grants, approval resolution) — distinct from, and not a replacement for, the per-decision evidence chain.
- See: [domain/audit_event.py](../glassbox/domain/audit_event.py)

---

## Evidence & Integrity

**`EvidenceReceipt`**
Proof that an `IntentRecord` was made durable before any effect was dispatched: segment id, sequence number, and MAC.
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`IntentRecord` / `OutcomeRecord`**
The pre-effect and post-effect halves of one decision's evidence. `IntentRecord`s are MAC-chained (HMAC over the prior record's hash); `OutcomeRecord`s are appended but **not yet chain-protected** — see [CLAIMS.md](CLAIMS.md) for this accepted gap.
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`EvidenceSegment`**
A batch of evidence records sealed together under a single Merkle root for tamper-evident storage.
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`MerkleProof`**
A cryptographic proof that a specific evidence record is included in a sealed segment, without requiring the full segment to verify.
- See: [domain/merkle.py](../glassbox/domain/merkle.py)

**`IntegrityStatus`**
The verification state of a segment or record: `INTACT`, `TAMPERED`, or `UNVERIFIABLE` (never silently reported as intact when the signer is unreachable).
- See: [domain/evidence.py](../glassbox/domain/evidence.py)

**`SegmentSealer`**
Publishes a signed, write-once (WORM) anchor of a segment's Merkle root before its underlying records may be purged, so purged records remain provable.
- See: [app/sealer.py](../glassbox/app/sealer.py)

**`WormAnchorStore`**
The write-once storage port for sealed anchors. `InMemoryWormAnchorStore` and `FilesystemWormAnchorStore` are development/reference implementations; `S3WormAnchorStore` is the production-grade adapter, backed by S3 Object Lock in compliance mode.
- See: [adapters/outbound/worm.py](../glassbox/adapters/outbound/worm.py)

**Retention scheduler**
The background service that seals segments and purges records once their retention period has elapsed, so retention isn't an operator's manual responsibility.
- See: [app/retention_scheduler.py](../glassbox/app/retention_scheduler.py)

**Prompt-injection scanning**
`glassbox.domain.prompt_injection.scan()` inspects both inbound untrusted text fields and outbound tool-dispatch results (recursing through nested structures). A flagged tool result raises `ToolOutputQuarantinedError`: the underlying effect already ran, but its result is never fed forward as trusted content, and only its digest — never the flagged content — is evidenced.
- See: [domain/prompt_injection.py](../glassbox/domain/prompt_injection.py)

---

## Policy & Risk

**`PolicyBundle` / `PolicyRule`**
A versioned, content-addressed (SHA-256), signed set of policy rules evaluated against an `AuthorizationRequest`. Every non-deny decision cites the exact bundle digest it was evaluated against.
- See: [domain/policy_bundle.py](../glassbox/domain/policy_bundle.py)

**`RiskScore` / `RiskInputs` / `RiskFactor`**
A deterministic 0–100 risk score (no clock reads inside scoring) and the signals feeding it. Raised to a `ConsequenceClass`-specific floor so a large irreversible action is never scored "low risk" on factors alone.
- See: [domain/risk.py](../glassbox/domain/risk.py)

---

## Distributed State

**Limit / `LimitStore`**
An atomic, distributed admission counter (window, ceiling, cooldown) shared across replicas. `RedisLimitStore` also bounds one tenant's own distinct-subject footprint (`max_tenant_subjects`) so one tenant's burst cannot trigger Redis `maxmemory` eviction of another tenant's keys.
- See: [ports/limits.py](../glassbox/ports/limits.py), [adapters/outbound/redis/limits.py](../glassbox/adapters/outbound/redis/limits.py)

**Baseline / `BaselineStore`**
Historical per-subject statistics (mean, stddev) used for z-score anomaly detection, independent of the hard `LimitStore` ceiling.
- See: [ports/baseline.py](../glassbox/ports/baseline.py)

**HTTP admission control**
A cheap, in-process, per-replica request-rate guard (`HttpAdmissionController`) applied before identity verification — protects one replica's own CPU/IO budget from a request burst, distinct from the distributed `LimitStore`.
- See: [adapters/inbound/http/admission_control.py](../glassbox/adapters/inbound/http/admission_control.py)

---

## Replay

**Replay**
Re-evaluating a past decision's identity/mandate/policy/risk/limits/baseline stages against the *current* configuration, without dispatching, to prove a policy or risk-model change is a deliberate improvement rather than a silent regression.
- See: [app/decision_service.py](../glassbox/app/decision_service.py) (`replay()`, `diff_outcomes()`)

---

## Runtime & Composition

**Port**
A `Protocol` interface describing a capability the application layer depends on (e.g., an identity resolver, a policy source, an evidence store), independent of any concrete implementation.
- See: [ports/](../glassbox/ports/)

**Adapter**
A concrete implementation of a port. Inbound adapters expose the system (e.g., the HTTP entry point); outbound adapters back it with infrastructure (e.g., Postgres, Redis, KMS, S3).
- See: [adapters/inbound/http/app.py](../glassbox/adapters/inbound/http/app.py), [adapters/outbound](../glassbox/adapters/outbound/)

**Composition Root**
`build_runtime(config, adapter_set)` — the single place adapters are wired to ports to produce a `GovernanceRuntime`. `glassbox.app` never imports a concrete adapter directly.
- See: [app/composition.py](../glassbox/app/composition.py)

**`GlassBoxConfig` / `RuntimeProfile`**
Runtime configuration and the deployment profile it targets (`dev` / `production`). A `dev_only` adapter set cannot be wired into a `production` profile (`ProfileViolationError`).
- See: [app/config.py](../glassbox/app/config.py)

**Idempotency**
Property where repeating the same decision with identical `idempotency_key` inputs always produces the same durable evidence, never a duplicate dispatch.
- See: [API/v2_endpoint_reference.md](API/v2_endpoint_reference.md)

**Zero Mandatory Dependencies**
Core GlassBox uses only the Python standard library; no external package requirements. Optional extras (`api`, `postgres`, `redis`, `kms`, `worm`, `delta`, `spark`, `otel`, `authoring`) add specific adapters.
- See: [README.md](../README.md), [pyproject.toml](../pyproject.toml)

---

## Quick Reference

- **Action & Resource:** `ProposedAction`, `ResourceRef`, `Exposure`, `ConsequenceClass`, Action Catalogue, Tool Registry
- **Authorization:** `AuthorizationRequest`, `AuthorizationDecision`, `Obligation`, `Approval`, `ApprovalService`, `WorkflowGateway`, risk-threshold gating, `DecisionService`
- **Identity, Mandates & Tenancy:** `RawCredential`, `DelegationChain`, `Mandate`, `ActionResourceGrant`, `ToolGrant`, `Tenant`, `AuditEvent`
- **Evidence & Integrity:** `EvidenceReceipt`, `IntentRecord`, `OutcomeRecord`, `EvidenceSegment`, `MerkleProof`, `IntegrityStatus`, `SegmentSealer`, `WormAnchorStore`, retention scheduler, prompt-injection scanning
- **Policy & Risk:** `PolicyBundle`, `PolicyRule`, `RiskScore`, `RiskInputs`, `RiskFactor`
- **Distributed State:** `LimitStore`, `BaselineStore`, HTTP admission control
- **Runtime & Composition:** Port, Adapter, Composition Root, `GlassBoxConfig`, `RuntimeProfile`, Idempotency

See also: [API/v2_endpoint_reference.md](API/v2_endpoint_reference.md), [ARCHITECTURE.md](ARCHITECTURE.md), [CLAIMS.md](CLAIMS.md), [USER/use_cases.md](USER/use_cases.md)
