# GlassBox Agentic Governance — Principal Architect Review (2026-09-16)

> **Historical architecture snapshot.** This report records findings from
> 2026-09-16 before the remediation work summarized below. Its body is
> intentionally preserved as review evidence and does not describe the current
> implementation. For current guarantees, use [CLAIMS.md](../CLAIMS.md); for
> current architecture, use [ARCHITECTURE.md](../ARCHITECTURE.md).

## Post-Review Remediation Status

The concrete P1/P2 findings from this review were subsequently addressed and
validated. The current state is:

| Review finding | Current status |
|---|---|
| Sensitive action parameters persisted in clear text | Closed: catalogue-declared `sensitive_parameter_fields` are redacted on the evidence-bound action; see claim 11 |
| Quorum state could survive a failed persistence attempt | Closed: unconditional cleanup plus TTL eviction; see claim 12 |
| Evidence maintenance had no deployable schedule or staleness signal | Closed: Kubernetes and systemd reference schedules plus stale-backlog warnings; see claim 13 |
| Outcome evidence was not chain-protected | Closed: independent outcome keyed-MAC chain and verification, migration 10; see claim 14 |
| Dispatch ledger lacked tenant scoping and RLS | Closed: tenant GUC propagation and migration 11; see claim 15 |
| Inbound adapters were outside architecture enforcement | Closed: import-linter and AST contracts cover the composition root; see claim 16 |
| No production-shaped PostgreSQL CDC source | Closed in code: wal2json polling source added; live wal2json verification remains environment-dependent; see claim 17 |
| Risk threshold described as disabled by default | Corrected: `RiskConfig.enforce_threshold` defaults to `True` and is required by the `prod` profile |

The remaining items in the report are historical observations or longer-term
design options, not an active remediation backlog.

---

## Original Review (Preserved Verbatim)

Statements below describe the repository as reviewed on 2026-09-16. They are
retained for traceability and must be read together with the remediation status
above.

**Original scope note:** This review covered the codebase as it existed on 2026-09-16: the v1 packages
(`api, authoring, benchmarks, compliance, events, governance, integrations, orchestration, rag,
rules, scenarios, security, telemetry, testing`) had been physically deleted. The live system was v2-only:
`glassbox/{domain,ports,app,adapters/{inbound,outbound},workflow,store}`. This review does not
repeat a class-by-class card for all ~120 classes; it scopes to the ~30 architecturally
load-bearing modules (evidence, dispatch, decision pipeline, identity/mandate, limits/baseline,
Postgres/Redis/Delta/WORM adapters, HTTP surface, workflow/approval) and gives package-level
verdicts for the rest, exactly as the prior 2026-08-20 review did. Every finding below is backed by
file:line evidence gathered by six parallel research passes over the actual source, cross-checked
by direct reads for the highest-risk claims (see citations).

---

## 1. Executive Summary

**Verdict: RETAIN AND EXTEND the v2 architecture. Do not redesign.** The governance pipeline
(`glassbox/app/decision_service.py`), the evidence/dispatch model, and the hexagonal
domain/ports/app/adapters layering are structurally sound, evidence-first, fail-closed by default,
and demonstrably closed most of the defects (F1–F6) that a prior v1 architecture had. The system is
a genuine **per-action authorization boundary** with cryptographically chained evidence, not a toy.

**Top strengths**
- Hexagonal layering is *mechanically* enforced twice (import-linter contract + AST-based
  `tests/test_layering.py`), not just documented — a rare and valuable property.
- Evidence is append-only (Postgres trigger + `REVOKE UPDATE/TRUNCATE`), tamper-evident (keyed
  HMAC via KMS, not a bare digest), sealed with RFC 6962 Merkle proofs before purge, and durable
  before dispatch (`decision_service.py` requires a receipt before `_dispatch_if_permitted`).
- Fail-closed is the default for every non-advisory stage (catalogue, identity, policy, evidence);
  only actions explicitly marked `may_degrade_on_dependency_failure=True` can skip a degraded
  dependency (kill switch, mandate, limits, baseline).
- Cross-replica idempotency (`PostgresDispatcher`, `INSERT ... ON CONFLICT`) and distributed rate
  limiting (`RedisLimitStore`, Lua atomic scripts) are real, tested against actual OS processes and
  a live Redis/Postgres, not just mocked.
- Replay is *structurally* incapable of mutating production state (suppressed dispatch flag +
  `NullDispatcher` that raises if ever called), proven by dedicated tests, not by convention.

**Top risks**
1. **In-memory adapters are unsafe for anything but single-process development** — this is
   correctly documented in-code, and is mechanically enforced, not just logged:
   `GlassBoxConfig.__post_init__` (`glassbox/app/config.py:518-544`) raises
   `ProfileViolationError` at construction time if the `PRODUCTION` profile is selected with any
   unsafe switch (`permits_unsafe_switches`) turned on or required infrastructure (Redis/Postgres
   URLs) missing — confirmed by direct read, this is fail-fast, not merely advisory. Residual risk
   is limited to an operator explicitly selecting `RuntimeProfile.DEV` in a real deployment, which
   is a deployment-configuration error outside the code's control.
2. **Outcome records are not chain-protected** (only intent records carry a keyed MAC chain) — an
   insider with DB access could forge an outcome undetected. Documented as an accepted gap in
   `docs/CLAIMS.md`, not fixed.
3. **Redis (limits/baseline) is a single shared instance with only key-prefix tenant isolation** —
   real noisy-neighbor risk under memory pressure/eviction.
4. **Retention/partition maintenance (`glassbox/adapters/inbound/cli/maintenance.py`) is a callable
   library, not a wired cron/CronJob** — if an operator forgets to schedule it, evidence and
   partitions grow unbounded.
5. **`WorkflowEngine._quorum_state`** is unbounded in-process state with a cleanup path that is
   skipped on any exception between quorum-reached and `repo.update()` succeeding — a slow, real
   memory leak under intermittent Postgres failures.
6. **MCP is entirely absent** from the codebase (zero references) — if the roadmap includes MCP
   tool ecosystems, today's `ToolRegistry` (name + SHA-256 digest only) has no publisher/version/URI
   concept and no per-hop mandate attenuation for tool-to-tool chaining.

**Architecture maturity:** Mid-to-high — this is a genuinely-designed hexagonal system with
mechanically enforced invariants, not an accumulation of features. **Production readiness:**
Conditional-yes for a single-tenant or trusted-multi-tenant deployment using only the
Postgres/Redis/KMS-backed adapters, with the P0/P1 items below closed first. **Next-generation
readiness:** Good foundation (clean ports, adapter substitutability, Delta/Spark already separated
correctly), but agent-versioning, MCP governance, and cross-tenant policy federation do not exist
yet and would need new domain concepts, not just more code in existing ones.

---

## 2. Project Goal Assessment

GlassBox's stated purpose (README.md, `docs/ARCHITECTURE.md`, `docs/CLAIMS.md`) is to be a
governance boundary for autonomous agent actions: verify identity, authorize under a mandate,
evaluate policy, score risk, enforce distributed rate limits, write evidence before any effect,
dispatch idempotently, and make every decision replayable and auditable.

| Goal | Architecture | Code | Tests | Status | Gap/Risk |
|---|---|---|---|---|---|
| Verified, non-spoofable identity & tenancy | `domain/identity.py`, `ports/identity.py` | `decision_service.py:276-295` verifies principal before evidence; `_check_identity_assertion` rejects header/credential mismatch (`decision_service.py:1025-1060`) | `test_adversarial_suite.py::TestThreat04/05` | **Fully implemented** | None material |
| Deny-by-default, versioned policy | `domain/policy_bundle.py` | `_check_policy` (`decision_service.py:1212-1230`), digest never advisory | `test_policy_bundle.py` | **Fully implemented** | No policy federation/hierarchy across tenants |
| Coarse authority ceiling (Mandate) independent of policy | `domain/mandate.py` | `_check_mandate` runs before policy; resource-scoped grants (`mandate.py` `ActionResourceGrant`) | `test_domain.py::TestResourceScopedGrants` | **Fully implemented** | Grants are additive/opt-in; legacy mandates without scoped grants keep the old (weaker) cross-product behavior |
| Risk annotation with optional gating | `domain/risk.py` | `_check_risk_threshold` (`decision_service.py:1256-1270`), gated by `RiskConfig.enforce_threshold` (default off) | `test_decision_service.py::TestRiskThresholdGating` | **Fully implemented (opt-in)** | Off by default; an operator who never enables it gets risk-as-evidence-only, contradicting a reader's assumption that "risk scoring" gates by default |
| Evidence before effect (F2) | `ports/evidence.py`, `app/decision_service.py` | Evidence append **precedes** `_dispatch_if_permitted`; evidence failure raises, dispatcher unreachable | `test_decision_service.py::TestEvidenceBeforeEffect` | **Fully implemented** | None material |
| Tamper-evident, keyed-MAC evidence chain (F3) | `domain/evidence.py`, `adapters/outbound/kms` | `record_hmac` via KMS HMAC, not bare SHA-256 | `test_adversarial_suite.py::TestThreat01`, `test_sealing.py` | **Fully implemented** | Applies to **intent** records only |
| Retention compatible with integrity (F4) | `app/sealer.py`, `domain/merkle.py` | Seal (Merkle root + WORM anchor) before purge; `SEALED_PURGED` status | `test_sealing.py` | **Fully implemented** | Purge/seal must be scheduled by an operator (§9) |
| Cross-replica idempotent dispatch (F5) | `ports/dispatcher.py` | `PostgresDispatcher` atomic claim via `INSERT...ON CONFLICT` | `test_dispatcher_idempotency.py` | **Fully implemented (Postgres only)** | `InMemoryDispatcher` is process-local; a misconfigured deployment loses this guarantee |
| Governed tool registry, no ungoverned tool execution (F6) | `domain/tool_registry.py` | Tool digest pinning; `TOOL_DEFINITION_CHANGED`/`TOOL_NOT_GOVERNED` denials | `test_adversarial_suite.py::TestThreat11` | **Fully implemented** | No MCP-level tool identity (publisher/version/URI) |
| Distributed, collision-free velocity limits | `domain/limits.py`, `adapters/outbound/redis/limits.py` | Lua atomic `try_consume`, hash-tag tenant keying | `test_multiprocess_limits.py` (real OS processes) | **Fully implemented (Redis only)** | `InMemoryLimitStore` explicitly documents "N replicas enforce N× the limit" |
| Tool-output re-scanning for indirect prompt injection | `domain/prompt_injection.py` | Both dispatchers scan `result` and raise `ToolOutputQuarantinedError` | `test_decision_service.py::TestToolOutputPromptInjectionScan` | **Fully implemented** | Regex/heuristic scanner; encoded/obfuscated payloads may bypass |
| Human approval workflow | `app/approval_service.py`, `workflow/workflow_engine.py` | Full PENDING→APPROVED/REJECTED/ESCALATED/REVOKED/EXPIRED lifecycle; replay never creates a workflow | `test_approval_service.py`, `test_replay.py` | **Fully implemented** | No automatic obligation discharge (approval never auto-dispatches — by design, but not everyone will expect that) |
| Tenant/AuditEvent first-class entities | `domain/tenancy.py`, `domain/audit_event.py` | Independent of the evidence chain | (unit tests exist per file listing) | **Implemented** | Separate from evidence chain by design; not cross-referenced from IntentRecord |
| MCP governance | — | **Zero references to "MCP" anywhere in `glassbox/`** | — | **Absent** | If MCP is on the roadmap, this is a from-scratch design task, not an extension |
| Physical Postgres partitioning + automated retention | `adapters/outbound/postgres/schema.py`, `app/retention_scheduler.py`, `adapters/inbound/cli/maintenance.py` | Both `evidence_intent`/`evidence_outcome` monthly-partitioned; CLI entrypoint exists | `test_postgres_evidence.py::TestPostgresPartitioning`, `test_maintenance_cli.py` | **Code complete, operationally incomplete** | No committed cron/CronJob/systemd-timer manifest invokes the CLI — a real operational gap, not a code gap |

---

## 3. Vision vs Implementation — Traceability

```
Vision: "Evidence must exist before any effect, unforgeable, and retention-compatible with integrity"
   ↓ Architecture: ports/evidence.py contract requires a durable receipt before dispatch is permitted
   ↓ Design: keyed HMAC chain (not bare hash) + RFC 6962 Merkle sealing before purge
   ↓ Implementation: glassbox/adapters/outbound/postgres/evidence.py (append_intent, verify, mark_sealed)
   ↓ Test: test_adversarial_suite.py::TestThreat01AuditForgery, test_sealing.py, test_postgres_evidence.py
   ↓ Evidence: live-Postgres-verified (per /memories/repo/glassbox-notes.md, 77/77 partitioning
     tests passed against a real server on 2026-08-20)
   → VERDICT: Vision fully realized for intent records. Outcome records are a documented,
     narrower gap (no MAC chain) — this is the single largest "vision vs implementation" delta
     in the whole system.
```

```
Vision: "Distributed rate limiting must be collision-free and fail closed"
   ↓ Architecture: ports/limits.py Protocol, two implementations (memory=dev, redis=prod)
   ↓ Design: atomic Lua CAS in Redis; canonical member keys to avoid collisions
   ↓ Implementation: adapters/outbound/redis/limits.py
   ↓ Test: test_multiprocess_limits.py — REAL OS processes (ProcessPoolExecutor), not threads
   ↓ Evidence: passes against live Redis (gated by GLASSBOX_REDIS_URL)
   → VERDICT: Fully realized, but the vision is silently violable if an operator wires the
     in-memory adapter into a multi-replica deployment. The config-profile guard
     (glassbox/app/config.py) is the only mechanical protection — verify it is fail-fast, not
     just logged, before relying on it (see Finding P1-1).
```

```
Vision: "Replay must never mutate production state and must be deterministic"
   ↓ Architecture: DecisionService.replay() takes the same pipeline path with suppress_dispatch=True
   ↓ Design: NullDispatcher raises AssertionError if ever invoked (defense in depth, not just a flag)
   ↓ Implementation: app/decision_service.py:replay(), adapters/outbound/replay.py
   ↓ Test: test_replay.py (distinct decision_id, ExecutionStatus.REPLAYED, no workflow created)
   → VERDICT: Fully realized — this is one of the strongest, most structurally-proven guarantees
     in the codebase.
```

```
Vision: "Governance must not be a monolith; agent, tool, policy, risk are separable concerns"
   ↓ Architecture: separate domain modules (mandate.py, policy_bundle.py, risk.py, tool_registry.py)
     each with its own port and adapter
   ↓ Implementation: DecisionService orchestrates but does not embed policy/risk logic itself
   → VERDICT: Mostly realized. DecisionService (app/decision_service.py) is nonetheless a large
     orchestrator (~1500+ lines across all stage-check methods) — see §21/§26 on whether this is
     becoming a god-object by line count even though responsibilities are cleanly delegated.
```

**Net traceability verdict:** Every major claim in `docs/CLAIMS.md` that the subagent research
checked resolves to real, cited, tested code — including several claims (resource-scoped mandates,
tool-output re-scanning, risk-threshold gating) that a prior review (2026-08-20, see repo memory)
had flagged as gaps. **These gaps have since been closed** — the codebase has measurably progressed
since the last full review. The only claim that remains an *accepted, documented* gap is outcome-record
chain protection.

---

## 4. Current Architecture Reconstruction

**Logical architecture** (hexagonal / ports-and-adapters):
```
glassbox/domain   — pure value objects & rules (Action, Decision, Mandate, Policy, Risk, Evidence,
                     Limits, ToolRegistry, Tenancy, AuditEvent, PromptInjection, Merkle, Serialization)
glassbox/ports    — Protocols (contracts) DecisionService depends on: IdentityVerifier, MandateStore,
                     PolicyDecisionPoint, RiskEngine, LimitStore, BaselineStore, EvidenceStore,
                     Dispatcher, ToolRegistry, ActionCatalogue, KillSwitch, WorkflowGateway, KeyManager
glassbox/app      — DecisionService (orchestrator), ApprovalService, RetentionScheduler, SegmentSealer,
                     composition.py (wiring), config.py, telemetry.py (stdlib-only cross-cutting facade)
glassbox/adapters/outbound — real implementations: postgres/, redis/, memory/ (dev-only), kms/, delta/,
                     spark/, otel/, identity/, worm.py, replay.py, signing.py
glassbox/adapters/inbound  — http/ (Flask app + admission control), cli/ (maintenance entrypoint)
glassbox/workflow, glassbox/store — sanctioned exception: real WorkflowEngine + SQLiteWorkflowRepository,
                     accessed only via the ports.workflow.WorkflowGateway Protocol (duck-typed, no
                     static import from app/adapters.outbound into these two packages)
```
Import direction is enforced twice: `pyproject.toml`'s `[tool.importlinter]` `layers` contract
(`domain <- ports <- app <- adapters.outbound`, `pyproject.toml:158-166`) and an independent AST
walk in `tests/test_layering.py`, with a dedicated `TestContractConsistency` proving the two never
drift apart (per testing-subagent research).

**Physical/runtime architecture:** A single Python process serves HTTP via
`adapters/inbound/http/app.py` (Flask). All governance logic executes synchronously in-request; the
only asynchronous element is a bounded `ThreadPoolExecutor` inside the dispatcher adapters
(`max_in_flight` cap, default small). PySpark/Delta are **not** on the live request path — they run
as separate offline batch jobs (Bronze/Silver ingestion, Gold analytics), correctly decoupled per
§59 of the review brief.

**Data architecture:** Evidence and dispatch state live in Postgres (durable, RLS-tenant-isolated,
partitioned monthly). Rate limits and behavioral baselines live in Redis (fast, but a single shared
instance unless Sentinel/cluster is deployed). WORM anchors (sealed Merkle roots) live in filesystem
or S3 Object Lock storage. Delta Lake (via `deltalake`/delta-rs, no JVM) holds Bronze/Silver
medallion copies for analytics. SQLite (via `store/repository.py`) holds workflow/approval state.

**Security architecture:** Trust boundary is the HTTP edge — a request is untrusted until its
credential (OIDC bearer or mTLS) is cryptographically verified into a `VerifiedPrincipal`; no
subsequent stage trusts a header value without cross-checking it against that principal (closes
v1's transport-header-spoofing defect, F1). Admission control (rate limiting) at the HTTP layer runs
**before** identity verification and is in-process/per-replica only.

**Governance architecture:** Decisions are made by a strict, ordered pipeline inside
`DecisionService`: tool registry → catalogue → identity → identity-assertion cross-check → kill
switch → mandate → policy → risk (compute, then optional threshold) → limits → baseline → evidence
write → dispatch (if permitted). Every stage's outcome (executed/skipped/failed) is recorded, never
silently dropped.

**Observability architecture:** `glassbox/app/telemetry.py` defines a stdlib-only Protocol
(Tracer/Meter) with a no-op default; the real OpenTelemetry backend lives in
`adapters/outbound/otel/configure.py` and is wired from the inbound entrypoint — a clean example of
keeping `glassbox.app` free of third-party dependencies (mechanically enforced by
`test_app_only_imports_permitted_stdlib`/`FORBIDDEN_THIRD_PARTY` in `test_layering.py`).

**Documented vs implemented delta:** No material contradiction was found between
`docs/ARCHITECTURE.md`/`docs/CLAIMS.md` and the code for anything checked in this pass — a
significant, positive finding given how often that gap exists elsewhere. The one soft gap: nothing
in `docs/ARCHITECTURE.md` explicitly warns a reader that `RiskConfig.enforce_threshold` defaults to
**off**, so risk is evidence-only unless explicitly turned on — worth a documentation clarification
even though the code itself is correct and intentional.

---

## 5. Domain Model Assessment

| Entity | Identity | Lifecycle | Mutability | Versioning | Persistence | Concurrency | Gaps |
|---|---|---|---|---|---|---|---|
| Tenant | `tenant_id` | PENDING→ACTIVE→SUSPENDED/OFFBOARDED | Immutable value + `with_status()` | none | Read-model, adapter-dependent | RLS-isolated | Recently added (P3 item), not yet cross-referenced from evidence |
| Principal (VerifiedPrincipal) | `(tenant_id, agent_ref, agent_instance_id, credential_id)` | Lifetime of credential | Immutable, never persisted | credential `issued_at`/`expires_at` | Transient, reconstructed per request | Constructed fresh per request (no cross-request reuse) | No `AgentVersion` entity — see below |
| Mandate | `(tenant_id, agent_ref, version)` | valid_from → valid_until/revoked_at | Immutable, versioned rows | Monotonic version int | Durable adapter store | Fast, separate revocation check (sub-second propagation) | No re-delegation depth cap |
| Delegation Chain | Chain of `DelegationHop` | Bounded by each hop's expiry | Immutable, attenuation checked at construction | none | Embedded in Principal | Structurally cannot represent privilege escalation | — |
| Policy (PolicyBundle) | `(tenant_id, bundle_id, version, sha256)` | active → replaced | Immutable once signed | Version int + content digest | Adapter-loaded, in-memory PDP | Pure evaluation (no I/O in `decide()`) | No cross-tenant policy inheritance |
| Risk Score | none (transient) | Computed once per decision | Immutable | `model_version` pinned | Evidenced, never separate entity | Deterministic (no clock reads) | Threshold gating opt-in, off by default |
| Intent Record | `decision_id` | Created once, append-only | Immutable | Segment/seq monotonic | Durable before dispatch, keyed-MAC chained | Serializable within segment | — |
| Outcome Record | `decision_id` (FK) | Created after dispatch | Immutable | none | Appended off critical path | Independent write path | **Not chain-protected** (accepted gap) |
| Approval | `approval_id` | PENDING→IN_REVIEW→{APPROVED,REJECTED,REVOKED,EXPIRED} | Transition-based (new instance) | none | `WorkflowRepository`/SQLite | In-process quorum state, unbounded (see §9) | — |
| Evidence Segment | `segment_id` | Sealed once, purgeable after | Records immutable, sealing adds Merkle root | Records have seq | Adapter-specific + WORM anchor | Idempotent sealing | — |
| AuditEvent | `event_id` | One-time creation | Immutable | none | Separate read model | Independent of evidence chain | Not cross-referenced from IntentRecord |

**What GlassBox governs — the agent, the action, or the request payload?**

**Verdict: GlassBox governs the `ProposedAction`, bounded by a verified `Principal`'s mandate —
i.e., per-action authorization, not agent-process monitoring.** Strongest evidence:
`Mandate.permits()` gates on `action.consequence`/`action.exposure` (server-derived from the
catalogue, never agent-supplied) — `domain/mandate.py`; the same `agent_ref` can be granted
different action sets by resource via `ActionResourceGrant`; evidence records "principal requested
action X on resource Y, mandate ceiling was Z" — never "agent X was monitored." Corollary: GlassBox
does **not** track agent process health, model version rollout, or the agent's internal
reasoning/plan — those remain the agent framework's responsibility. This is an intentional and
correctly-scoped boundary, not an oversight, but it means marketing language like "agent governance"
should be read precisely as "the governance of an agent's individual proposed actions."

**Missing domain concepts** (ranked by architectural value):
1. **AgentVersion** — no first-class entity to gate a new model/agent-binary version pending
   re-certification; only `agent_ref` (stable) + `agent_instance_id` (per-restart) exist.
2. **Obligation discharge tracking** — `Obligation` is defined (`domain/decision.py`) but nothing
   verifies it was actually fulfilled; a blocking obligation becomes an indefinite `PENDING_APPROVAL`.
3. **Cumulative/aggregate consequence tracking across grants** — `ToolGrant.max_consequence` and
   policy ceilings are each evaluated independently per action; nothing detects an agent exercising
   many independently-permitted low-risk actions that aggregate into a high-risk outcome.
4. **Credential/key rotation lifecycle** as a first-class domain concept (currently adapter-only).
5. **Federated/hierarchical policy** across parent/child tenants.

---

## 6. Design Principles Assessment

| Principle | Where used | Evidence | Verdict |
|---|---|---|---|
| Hexagonal / ports-and-adapters | Whole codebase | `ports/*.py` Protocols, `adapters/outbound/*` implementations, `test_layering.py` enforcement | **Correctly and mechanically enforced** — a genuine strength, not aspirational |
| Dependency inversion | `app/decision_service.py` depends only on `ports.*` Protocols | Constructor takes a `Runtime` object of Protocol-typed fields | **Correct** |
| Fail-safe / fail-closed design | Every non-advisory stage | `_check_*` methods deny on `DEPENDENCY_UNAVAILABLE` unless `may_degrade_on_dependency_failure` | **Correct and consistently applied** — a genuinely rare, valuable property |
| Immutability | Domain value objects (frozen dataclasses) | `ProposedAction`, `IntentRecord`, `Mandate`, `PolicyBundle` all frozen | **Correct** — enables the deterministic-replay guarantee |
| Statelessness (of the service layer) | `DecisionService` itself | No instance-level request state; all state lives in injected adapters | **Correct**, verified by `test_stateless_tenancy.py` |
| Idempotency | Dispatch, outcome upsert | `ON CONFLICT` claims, canonical idempotency keys | **Correct for Postgres adapter**; **absent** for the in-memory dev adapter by explicit design |
| Zero-trust / least privilege | Identity-assertion cross-check, tenant-scoped RLS | `_check_identity_assertion` never trusts a header | **Correct** |
| Separation of concerns | mandate vs policy vs risk as separate modules/ports | Each has its own domain type, port, adapter | **Mostly correct** — see DecisionService size concern below |
| DRY | Shared fail-closed/skip pattern across 6+ `_check_*` stages | Repeated `if unavailable: deny-unless-advisory` shape | **Minor violation, justified**: the repetition is a deliberate, auditable per-stage contract, not accidental duplication — collapsing it into one generic helper would obscure which specific `DenialReason` each stage uses, which matters for audit clarity. Acceptable trade-off. |
| YAGNI vs premature abstraction | `ports.workflow.WorkflowGateway` as a thin Protocol over a pre-existing concrete `WorkflowEngine` | Explicit "kept, not v1 debt" decision, documented in the port's own docstring | **Correct, deliberate exception** — not over-engineering |
| KISS | `DecisionService` as a single orchestrator class | ~1,500+ lines, ~15 `_check_*`/private methods | **Borderline** — see §21 |

---

## 7. Runtime Execution Model

Synchronous, single-process-per-replica request handling. The only concurrency primitives on the
live path are: (a) a bounded `ThreadPoolExecutor` inside dispatcher adapters to run the effect
handler with a timeout, and (b) database-side atomicity (Postgres transactions, Redis Lua scripts)
for cross-replica correctness. No asyncio is used anywhere in the reviewed code. PySpark/Delta jobs
are entirely off the request path, invoked as separate batch processes.

**Latency amplification per decision** (worst case, all stages hit real backends): 1 tool-registry
lookup + 1 catalogue lookup + 1 identity verification (KMS/OIDC) + 1 mandate lookup (Redis/Postgres)
+ 1 policy evaluation (in-memory, no I/O once loaded) + 1 risk call (HTTP/gRPC, external) + 1 limits
check (Redis Lua round-trip) + 1 baseline check (Postgres or Redis) + 1 evidence append (Postgres
transaction + 1 KMS MAC call) + 1 dispatch (external effect call). That is **up to 8 external
network round-trips per decision** before the effect itself runs — a real, measurable latency
budget that should be load-tested (see §11).

---

## 8. Concurrency & Multi-Execution Assessment

**Verdict:** the codebase draws a **correct and clearly-documented line** between in-memory
development adapters (single-process only, explicitly marked as such in their own docstrings) and
production-grade adapters (Postgres/Redis, cross-replica correct, tested with real OS processes).
Within that line, locking discipline is good: no lock is ever held across an external I/O call or
KMS/Redis/Postgres round-trip in any adapter reviewed.

**Top concurrency risks (ranked):**

1. **[Mitigated by config, low residual risk] In-process idempotency ledger cannot survive
   multi-replica deployment.** `InMemoryDispatcher._outcomes` (`adapters/outbound/memory/dispatch.py`)
   is a process-local dict; two replicas each see "no prior entry" for the same `idempotency_key`
   and can both execute the same effect. The `PostgresDispatcher`
   (`adapters/outbound/postgres/dispatcher.py`, `INSERT ... ON CONFLICT`) is the correct, production
   adapter. **Verified by direct read:** `glassbox/app/config.py:518-544` raises
   `ProfileViolationError` at construction if `RuntimeProfile.PRODUCTION` is selected with an unsafe
   adapter wired in — this closes the P0 uncertainty the prior 2026-08-20 review had flagged as
   still needing verification.
2. **[Mitigated by config, low residual risk] In-process rate limiting is N× too permissive across
   replicas** — same class of issue as above, `InMemoryLimitStore` vs `RedisLimitStore`
   (`adapters/outbound/redis/limits.py`, Lua-atomic). Explicitly documented in the adapter's own
   docstring: "N replicas enforce N times the configured limit." Proven correct for Redis via
   `test_multiprocess_limits.py` using real `ProcessPoolExecutor` workers; production selection of
   the unsafe adapter is blocked at config-construction time by the same `ProfileViolationError`
   mechanism as risk #1.
3. **[HIGH] `WorkflowEngine._quorum_state` (glassbox/workflow/workflow_engine.py`) is unbounded,
   in-process state with a cleanup path that only runs on the success branch** of `approve()`. If
   `repo.update()` or `_transition()` raises (e.g., a transient Postgres error) after quorum is
   reached but before the state is popped, the entry is orphaned forever. Realistic growth: modest
   in absolute bytes (~100 bytes/entry) but genuinely unbounded over a long-lived process with
   intermittent DB failures — a real, if slow, memory leak, and also a correctness smell (a quorum
   that "loses" its vote count on a transient failure forces reviewers to re-approve from scratch).
4. **[MEDIUM] HTTP admission controller** (`adapters/inbound/http/admission_control.py`) performs
   LRU eviction *under its lock* when the tracked-client cap (default 50,000) is exceeded; an
   attacker cycling through many distinct client keys can force an eviction loop while holding the
   lock, causing a latency spike for all concurrent requests. Bounded (cannot exceed the configured
   cap), so this is a tail-latency risk, not a memory risk.
5. **[LOW] `InMemoryEvidenceStore.verify()`** holds its lock for the entire chain-verification loop
   over a segment; acceptable because this adapter is development-only and verification is not
   normally on a hot path.

**No deadlocks, no cross-lock nesting, and no lock held across network I/O were found in any
adapter reviewed** — a genuinely strong result for a codebase of this size.

---

## 9. Object Sharing and Locking Assessment

All in-memory adapters use a single `RLock`/`Lock` per instance guarding one cohesive piece of state
(bundles, windows, cooldowns, outcomes, quorum state, admission buckets). No object is shared
mutably across adapters; each adapter owns its own dict/set, and `DecisionService` never reaches
into an adapter's internals — it only calls Protocol methods. The most notable **unbounded**
structures, all explicitly or implicitly scoped to development-only adapters except one:

| Object | Bounded? | Production-relevant? |
|---|---|---|
| `InMemoryEvidenceStore._segments/_receipts/_outcomes` | No | No — dev-only, `PostgresEvidenceStore` is the real store |
| `InMemoryDispatcher._outcomes` | No | No — dev-only |
| `InMemoryKillSwitch._tenants` | No (but grows only on rare manual engage/disengage) | Dev-only in this form; production uses a Redis-backed equivalent per `ports/kill_switch.py` |
| `WorkflowEngine._quorum_state` | **No, and this IS the production workflow engine** | **Yes — this is the one genuinely production-relevant unbounded structure found** |
| `HttpAdmissionController._buckets` | Yes (hard cap 50k, LRU-evicted) | Yes, but correctly bounded |
| `KmsMacSigner._cache` | Yes (hard cap 4,096, LRU) | Yes, correctly bounded |

**Recommendation, scoped and concrete:** wrap `WorkflowEngine.approve()`'s quorum-state mutation and
the subsequent `repo.update()` call in a `try/finally` so the `_quorum_state.pop(...)` cleanup always
runs regardless of whether the transition/persist step succeeds, and add a periodic (or
lazy-on-access) TTL eviction as a second line of defense.

---

## 10. Memory Assessment

The only realistic **production** memory-growth risk identified is `WorkflowEngine._quorum_state`
(§8/§9 above; worst-case bound in the hundreds of MB over years of intermittent failures — not an
acute risk, but a genuine, fixable leak). All other unbounded structures live exclusively in
adapters explicitly documented and gated as development-only (`InMemoryEvidenceStore`,
`InMemoryDispatcher`, `InMemoryLimitStore`/`InMemoryBaselineStore` beyond their `max_subjects`
eviction bound). Production stores (Postgres, Redis with TTL/eviction policy, KMS cache with a hard
LRU cap) are all correctly bounded or externally managed.

No evidence was found of unbounded caches, retained request objects, or accidental object retention
via closures/callbacks in the reviewed hot-path code.

---

## 11. Performance Assessment

The decision pipeline is I/O-bound, not CPU-bound: per §7, a fully-wired production decision can
involve up to 8 external round-trips (identity/KMS, mandate store, risk engine, limits, baseline,
evidence append + KMS MAC, dispatch). None of these are individually unusual, but the **stacking**
means P99 latency is dominated by the slowest single dependency plus network overhead repeated
across stages — this should be load-tested end-to-end, not just per-adapter
(`tests/test_performance_benchmarks.py` exists but is opt-in, `GLASSBOX_RUN_BENCHMARKS=1`, and is
never run in CI per the testing-subagent findings). KMS is explicitly flagged as being on the
critical path with only a 5-failure circuit breaker before it starts denying everything — a
reasonable but not instantaneous safety margin.

---

## 12. Distributed Systems Assessment

Consistency model is intentionally mixed by design and correctly matched to each concern: **strong**
consistency (single-row lock + transaction) for evidence sequencing and dispatch-ledger claims;
**atomic-but-eventually-globally-consistent** for Redis-backed limits/baselines (correct within one
Redis deployment, but see the noisy-neighbor risk in §13); **durable, append-only, and immutable**
for the evidence chain via Postgres triggers + RLS + WORM anchoring. Idempotency is genuinely
proven cross-process for the Postgres dispatcher via real multi-process tests
(`test_multiprocess_limits.py`, `test_dispatcher_idempotency.py`), which is a materially stronger
claim than most systems make (and prove) at this stage of maturity.

---

## 13. Agentic AI Architecture Assessment

See §5's "governs the action, not the agent" verdict. Tool governance is real (digest pinning,
quarantine on definition drift) but shallow by MCP-ecosystem standards: no publisher/namespace,
semantic version, or nested-call attenuation. **MCP itself is entirely unimplemented** — zero
references anywhere in `glassbox/`. If MCP tool-chaining, tool marketplaces, or agent-to-agent
delegation with per-hop mandate narrowing are near-term roadmap items, they require new domain
concepts (a versioned `AgentVersion`, a tool-chain depth/attenuation model), not incremental
extensions of `ToolRegistry` as it exists today.

---

## 14. Authorization Assessment

**Authorization is correctly independent of risk scoring.** The decision junction
(`decision_service.py:679-693`, corroborated by direct read) shows risk-threshold denial is only
ever applied when the decision is not already a `DENY` from mandate/policy — risk can veto an
otherwise-allowed action but can never override a mandate or policy denial, and it is off by default
(`RiskConfig.enforce_threshold`). This is the correct architectural relationship: **mandate + policy
are authorization; risk is a gate on top of authorization, evidenced either way.** Identity is
cryptographically verified (OIDC/mTLS) before anything else runs; delegation chains are
attenuation-checked at construction (structurally impossible to build a privilege-escalating chain).
Resource-scoped mandate grants close the previous action/resource independent-set gap, but only for
mandates that opt into declaring `resource_scoped_grants` — legacy/undeclared mandates keep the
older, weaker cross-product semantics by default.

---

## 15. Policy Architecture Assessment

Policy is a distinct, content-addressed, signed bundle (`PolicyBundle`, digest + version), evaluated
by a pure, deny-by-default `PolicyDecisionPoint` with no I/O inside `decide()` and structural
conflict detection (`detect_conflicts()`) before activation. Mandate (coarse ceiling), policy (fine
rules), and risk (scored, optionally gating) are **kept as three distinct concepts with three
distinct ports** — the review brief's §28 warning against collapsing these into "one undifferentiated
rule engine" is satisfied. Missing: policy inheritance/federation across a tenant hierarchy, and no
explicit policy-simulation/what-if tooling was found (may exist and simply not have been in scope of
this pass — flagged for follow-up, not asserted absent).

---

## 16. Risk Architecture Assessment

Risk is a pinned-model-version, deterministic (no clock reads inside `score()`) evidence annotation
that can *optionally* gate via `RiskConfig.enforce_threshold` (default off). This design is
defensible — it lets an operator adopt risk scoring for observability before turning it into a hard
gate — but the **default-off** posture means a reader of `docs/CLAIMS.md`/`ARCHITECTURE.md` could
reasonably assume risk always gates when in fact most deployments, unless explicitly configured,
treat it as evidence-only. This should be called out explicitly in the architecture docs (a
documentation fix, not a code fix).

---

## 17. Security Assessment

Full threat table (attack path → control → residual risk), from the dedicated security research
pass, file:line-cited:

| Threat | Control | Residual Risk |
|---|---|---|
| Direct prompt injection | `decision_service.py:1509` scans only caller-declared `untrusted_text_fields`; flagged → deny | Business/other fields never scanned; correctness depends on the catalogue curator labeling fields correctly |
| Indirect injection (tool output) | Both dispatchers scan `result` post-execution, raise `ToolOutputQuarantinedError` (`memory/dispatch.py:131`, `postgres/dispatcher.py:170`) | Heuristic/regex scanner; sophisticated encoding may bypass |
| Tool rug-pull / definition poisoning | SHA-256 digest pin + quarantine on mismatch (`tool_registry.py:50-51`, `decision_service.py:920-924`) | Assumes hash collision resistance; supply-chain trust is a deployment responsibility |
| Excessive agency | `ToolGrant.max_consequence` + policy ceiling, evaluated per-action | **No cumulative-consequence tracking** across many independently-permitted actions |
| Confused deputy | `principal.tenant_id == action.tenant_id` checked at multiple stages | Depends on identity verifier correctly extracting tenant |
| Agent impersonation | OIDC `iss`/`aud` anchoring, `exp`/`nbf` with 60s skew, fast mandate-revocation check | Revocation is only for *known* agents; a freshly-forged token for a never-seen agent isn't caught until the next mandate lookup denies it for lack of a mandate (fail-closed, but not "impersonation-specific" detection) |
| Tenant breakout | Postgres RLS + `TENANT_CONTEXT_GUC` | Depends on correct non-superuser role configuration in deployment (a superuser test role would silently bypass RLS — a documented lesson from the prior session's migration work) |
| Policy/catalogue poisoning | Digest verification; unavailable → deny | No version pinning against a "replace, then quietly revert" attack window |
| Replay/duplicate execution | In-process dict (dev) / `INSERT...ON CONFLICT` (prod) | Dev adapter loses state on restart (acceptable, dev-only); Postgres path can leave a `claimed`-but-crashed row, correctly surfaced as `INDETERMINATE` on retry |
| Credential replay | `exp` validation | No single-use `jti` check; no client-IP token binding (standard OAuth-class limitation, not GlassBox-specific) |
| Audit tampering | Keyed HMAC chain (intent) + WORM anchor | **Outcome records not chain-protected** — the single most concrete, fixable audit-integrity gap |
| HTTP-layer rate-limit bypass | Per-process sliding window, pre-identity | Per-process only; N replicas ⇒ N× budget; spoofed/rotating client keys bypass entirely |
| Secret/PII leakage (evidence) | No automatic redaction of `ProposedAction.parameters` before write | **High residual risk**: parameters are stored **raw** in `evidence_intent`; mitigation depends entirely on the catalogue curator correctly and broadly labeling `untrusted_text_fields` |

**MCP verdict:** Absent, not merely under-integrated — zero code references anywhere.

**Data-at-rest verdict:** Tool output, prompts, and credential material are correctly digest-only or
never persisted. **Action parameters are the one place raw, potentially sensitive data is stored
without any automatic redaction** — this is the most actionable, concrete security finding in this
review (see Finding P1-2 below).

---

## 18. Compliance Assessment

Architectural alignment (not certification claims):
- **NIST AI RMF / ISO 42001-style governance:** decision provenance (who/what/why/policy
  version/risk version) is genuinely reconstructable — a strong alignment with "Govern/Map/Measure"
  functions.
- **ISO 27001 / SOC 2 (change management, access control, audit logging):** RLS + append-only +
  KMS-backed signing map well to logical access control and audit-trail controls; key rotation and
  retention scheduling are present but require an operator to actually run them (a control-design
  gap, not a control-implementation gap).
- **OWASP LLM/Agentic security guidance:** direct and indirect prompt-injection controls exist and
  are tested; excessive-agency and confused-deputy controls exist at the single-action level but not
  at the cumulative/multi-action level; MCP-specific guidance (tool poisoning across chained tools)
  has no corresponding control since MCP itself doesn't exist in this codebase yet.
- **GDPR-adjacent (data minimization):** action parameters stored raw in evidence without automatic
  redaction is the one area that would need explicit compensating controls (data classification at
  the catalogue layer) for any deployment handling regulated PII.

---

## 19. Audit and Evidence Assessment

The system can answer, from evidence alone: who acted (verified principal + delegation chain),
which tenant, which action/resource, which policy bundle digest, which risk model version, which
decision effect and matched rules, which human approval (full step history in
`WorkflowRepository`), what executed (`OutcomeRecord.status`/`error_class`), and whether it can be
replayed (yes, structurally, with a distinct decision_id). The **one incomplete answer**: "was the
outcome record itself tamper-evident?" — no, only intent records carry the keyed-MAC chain. This is
explicitly and honestly documented as an accepted gap in `docs/CLAIMS.md`, which is itself a
positive governance signal (the project does not overclaim).

**Immutability-claim precision check** (per the review brief's §35 requirement not to conflate
terms): the codebase's own documentation is precise — "append-only" (DB trigger + REVOKE), "tamper-
evident" (keyed MAC chain), "cryptographically authenticated" (KMS-backed HMAC, not local-key), and
"non-repudiable" (WORM anchor + Merkle proof) are each backed by a distinct, separately-tested
mechanism rather than being used interchangeably.

---

## 20. Data Architecture Assessment

Postgres holds durable, RLS-isolated, monthly-partitioned evidence and an exactly-once dispatch
ledger; Redis holds fast, atomic, but single-shared-instance rate-limit/baseline state; Delta Lake
(via delta-rs, no JVM) holds Bronze/Silver medallion copies for offline analytics; PySpark is
correctly reserved for optional Gold-layer batch analysis and is never on the request path. This
split is architecturally appropriate and matches the review brief's §59 guidance precisely. The
`dispatch_ledger` table notably has **no tenant_id column or RLS** — idempotency keys are
tenant-agnostic today, which is a real (if narrow) tenant-isolation gap worth closing given how
carefully RLS is applied everywhere else.

---

## 21. Code Quality Assessment

`DecisionService` (`glassbox/app/decision_service.py`) is the one class in the codebase that
approaches "god object by line count" — it owns roughly 15 private `_check_*`/stage methods across
~1,500+ lines. This is **not** a SRP violation in the classic sense (each stage delegates to a
distinct port/adapter and the class's only real job is orchestration + evidence assembly), but its
sheer size makes it the highest-effort file to safely modify, and it is the single most
consequential file in the whole system. It would benefit from extraction into smaller per-stage
orchestrator objects (e.g., a `StagePipeline` of ordered `Stage` objects) purely for
navigability/testability — **not** because the current design is incorrect. Elsewhere, code quality
is notably good: `glassbox.app` and `glassbox.domain`/`glassbox.ports` are held to a stricter
standard (no third-party imports, no I/O, no clock/random calls, mandatory docstrings/`__all__`) and
this is mechanically enforced, not just styled. Dead/orphaned artifacts: `glassbox/api/` and
`glassbox/governance/` are empty directories (only `__pycache__`) left over from the v1 deletion —
harmless but should be removed to avoid confusing future contributors.

---

## 22. Refactoring Opportunities (Prioritized)

| # | Current | Problem | Risk if unaddressed | Proposed | Migration | Complexity | Value |
|---|---|---|---|---|---|---|---|
| 1 | `DecisionService` as one ~1,500-line orchestrator | Hard to navigate/extend safely; every new stage grows one file | Medium (slower iteration, higher review burden) | Extract each `_check_*` into a small `Stage` object implementing a common Protocol, iterated by a thin pipeline runner | Additive: introduce `Stage` Protocol, move method bodies one at a time behind it, keep public API identical | Medium | Medium |
| 2 | `WorkflowEngine._quorum_state` cleanup only on success path | Unbounded leak + lost quorum votes on transient DB errors | Low-medium (slow leak, occasional reviewer friction) | `try/finally` around transition+persist; add lazy TTL eviction | Low-risk, localized | Low | Medium |
| 3 | `OutcomeRecord` has no MAC chain | Insider-forgeable outcomes | Medium (compliance/audit exposure) | Extend the keyed-MAC chain to outcomes (new `prev_hash`/`record_hmac`/`seq` columns, same pattern as intent) | Requires a new migration + adapter changes; must not weaken the existing idempotency semantics (`(decision_id, completed_at)` key) | High | High |
| 4 | `dispatch_ledger` has no `tenant_id`/RLS | Narrow tenant-isolation gap in an otherwise RLS-everywhere system | Low-medium | Add `tenant_id` column + RLS policy, mirroring `evidence_intent` | New migration; idempotency key must remain stable | Medium | Medium |
| 5 | Retention/partition maintenance is library-only | Silent unbounded growth if never scheduled | Medium (operational, not code) | Ship a reference CronJob/systemd-timer manifest alongside `adapters/inbound/cli/maintenance.py` | Deployment artifact, not application code | Low | High |
| 6 | Empty `glassbox/api/`, `glassbox/governance/` dirs | Confuses new contributors about what's real | Low | Delete the empty directories | Trivial | Low | Low |
| 7 | Action parameters stored raw in evidence | PII/secret leakage into the audit trail | High if handling regulated data | Add an explicit, catalogue-declared redaction/classification hook applied before `append_intent` | Additive; must not change the digest/replay contract for non-sensitive fields | Medium | Critical (for regulated deployments) |

---

## 23. Testing Assessment

Strong: `test_multiprocess_limits.py` and `test_dispatcher_idempotency.py` prove cross-replica
correctness against **real OS processes and (when gated) a real Redis/Postgres**, not mocks —
genuinely rare rigor. `test_replay.py` proves non-mutation structurally (a `NullDispatcher` that
raises if ever called). `test_layering.py` + import-linter double-enforce architectural boundaries.
`test_concurrency_invariant_lint.py` is a meta-test that rejects "spawned threads + asserted only
`errors == []`" as a test pattern — an unusually disciplined practice.

Weak spots / blind spots (with citations from the testing research pass):
- `glassbox.adapters.inbound` (HTTP, CLI) is **not** covered by the `layers` import-linter contract
  or by `test_layering.py`'s AST checks (`pyproject.toml:152-166` explicitly scopes the contract to
  `adapters.outbound` only) — nothing mechanically prevents an inbound adapter from accumulating
  business logic over time.
- Meta-tests prove *a* meaningful assertion exists, not that it is the *right* one — a concurrency
  test could assert `len(results) == N` without checking per-tenant correctness and still pass the
  lint.
- ~105 of ~319 tests are gated behind live infra env vars (`GLASSBOX_POSTGRES_DSN`,
  `GLASSBOX_REDIS_URL`), and coverage reporting only reflects the always-run subset — a normal PR
  run's 80%-floor coverage number does not include Postgres/Redis-specific code paths.
- Spark real-mode execution tests (`GLASSBOX_SPARK_LOCAL_JOB=1`) are never actually run in CI —
  static/AST serializability checks run, but no real Spark job has ever executed against this code
  in CI.
- No test exercises Postgres retention/purge under concurrent load (index/lock behavior under
  contention), only unit-level and gated-integration coverage.

---

## 24. Observability and Operations

Correlation is possible end-to-end (`decision_id`, `tenant_id`, `agent_ref`, `policy_bundle_id`,
`risk.model_version`, `approval_id` are all present on evidence and, where applicable, on workflow
records) — a genuine single-decision traceability story exists. Operationally, the biggest gap is
that `RetentionScheduler`/`ensure_monthly_partitions` and the CLI maintenance entrypoint exist and
are tested but have **no committed scheduling manifest** (cron/CronJob/systemd timer) — an operator
must remember to wire this, and nothing fails loudly if they don't (evidence just keeps
accumulating). Health/readiness (`/healthz`) exists on the HTTP surface; no explicit graceful
shutdown/drain behavior was reviewed in this pass (out of scope, flag for follow-up if relevant).

---

## 25. Architecture Debt Register

| Debt | Location | Root Cause | Impact | Severity | Recommended Action |
|---|---|---|---|---|---|
| Outcome records lack MAC chain | `domain/evidence.py`, Postgres schema | Deliberately deferred (schema/idempotency complexity) | Insider-forgeable outcomes | P1 | Extend keyed-MAC chain to outcomes (see §22 item 3) |
| Retention/partition maintenance unscheduled | `adapters/inbound/cli/maintenance.py` | Deployment-tooling scope boundary (correctly not committed as application code) | Unbounded evidence growth if forgotten | P1 | Ship a reference scheduling manifest; add a startup warning/metric if last-run timestamp is stale |
| `dispatch_ledger` has no tenant scoping/RLS | `adapters/outbound/postgres/schema.py` | Added later than the RLS-everywhere pattern was established | Narrow tenant-isolation gap | P2 | Add `tenant_id` + RLS |
| `WorkflowEngine._quorum_state` unbounded/leaky on exception | `workflow/workflow_engine.py` | Cleanup only wired on the success path | Slow memory leak + lost quorum votes | P2 | `try/finally` + TTL eviction |
| `glassbox.adapters.inbound` not layering-checked | `pyproject.toml`, `test_layering.py` | Historical scoping decision (inbound wasn't considered "a layer") | Silent business-logic creep into inbound adapters over time | P2 | Add an explicit forbidden-imports contract for inbound |
| Empty legacy directories on disk | `glassbox/api/`, `glassbox/governance/` | v1 physical-deletion pass removed files but not the now-empty directories | Contributor confusion, zero runtime risk | P3 | Delete |
| Action parameters stored raw in evidence | `app/decision_service.py` → `evidence.py` | No redaction layer was ever designed; relies entirely on catalogue curation of `untrusted_text_fields` | PII/secret persistence in the audit trail | P1 (context-dependent: critical for regulated data) | Add a catalogue-declared classification/redaction hook |
| No MCP support | Entire codebase | Not yet on the roadmap when v2 was built | Blocks MCP-ecosystem adoption | P3 (only if MCP is actually planned) | New domain design, not incremental |

---

## 26. Over-Engineering Review

Nothing found in this pass qualifies as over-engineering relative to its value. The
`workflow`/`store` "kept, not v1 debt" exception is a good example of a decision that could *look*
like accidental complexity (why keep two legacy-named packages?) but is in fact a deliberate,
documented, correctly-scoped reuse of working code behind a thin Protocol — exactly the right
judgment call, not premature abstraction. The dual layering enforcement (import-linter **and**
AST test) could look redundant, but the two mechanisms deliberately check the same rule via
independent means specifically so the tool-availability of import-linter is never a single point of
failure for the architecture gate — a justified, not redundant, choice.

---

## 27. Under-Engineering Review

- **Automatic redaction of action parameters** before they enter evidence — currently entirely
  absent; relies on operator/curator discipline.
- **Cumulative/aggregate risk and consequence tracking** across many independently-permitted
  actions by the same agent — each action is evaluated in isolation.
- **Cross-tenant policy hierarchy/federation** — flat, per-tenant only.
- **Agent/model versioning as a first-class, gate-able concept** — currently just an opaque
  `agent_ref`/`agent_instance_id` pair.
- **Operational scheduling of retention/partition maintenance** — code-complete, operationally
  unwired.

---

## 28. Next-Generation Readiness

| Capability | Current readiness | Architectural blocker | Required change |
|---|---|---|---|
| Millions of agent decisions/day | Good | KMS/identity-verification round-trip per decision | Batch/cache-friendly identity verification (e.g., short-lived local attestation cache with fast revocation checks, already partially present) |
| Thousands of tenants | Good | Single shared Redis instance for limits/baselines | Sentinel/cluster deployment (already wired, opt-in) or per-tenant-tier Redis sharding |
| Multi-region / active-active | Partial | Evidence/dispatch ledger are single-Postgres-primary by construction | Would need a cross-region consensus or per-region evidence chains with a reconciliation model — a genuine redesign, not incremental |
| Event-driven governance / streaming analytics | Partial | Delta/Spark already correctly separated for batch; no streaming (Kafka/Kinesis-style) ingestion path exists | Add a streaming CDC consumer beside the existing batch Bronze/Silver adapters |
| MCP ecosystems / tool marketplaces | Absent | No tool identity beyond a name+digest; no per-hop attenuation | New domain design (publisher/version/URI, chain-depth model) |
| Autonomous policy optimization / AI-assisted policy creation | Absent | Policy is hand-authored/signed bundles only | New subsystem, out of scope for incremental extension |
| Regulated/financial workloads | Conditional | Outcome-chain gap + raw-parameter storage are the two blockers | Close Findings P1-2 (redaction) and the outcome-chain gap first |

---

## 29. Recommended Target Architecture

**Verdict: extend, do not redesign.** The hexagonal ports/adapters skeleton, the evidence-first
pipeline, and the fail-closed defaults are exactly the right foundation for the next 2-3 years of
growth. Recommended target-state deltas, all additive to the current architecture:

- **Governance:** keep the single ordered `DecisionService` pipeline conceptually, but refactor its
  internals into composable `Stage` objects (see §22-1) purely for maintainability as more stages
  are added (obligation discharge, cumulative-risk tracking, MCP tool-chain attenuation).
- **Data:** extend the keyed-MAC chain to outcome records; add `tenant_id`+RLS to `dispatch_ledger`;
  ship a reference scheduling manifest for retention/partition maintenance.
- **Security:** add a catalogue-declared parameter-classification/redaction hook ahead of evidence
  write; add cumulative-consequence tracking per agent/window as a new, opt-in domain concept
  layered on top of (not replacing) per-action mandate/policy checks.
- **Multi-tenant:** move Redis to a per-tenant-tier sharding or Sentinel/cluster topology as tenant
  count grows; this is already supported by the existing adapter (opt-in Sentinel wiring), so it is
  a deployment change, not a code change, for most of the path there.
- **Agentic:** introduce `AgentVersion` as a first-class, gate-able domain concept before any MCP or
  multi-agent-framework work begins — this is the one truly new domain concept the current model is
  missing that everything else (tool registry, mandate, evidence) would need to reference.
- **Multi-region:** explicitly out of scope for incremental extension; treat as a distinct, later
  redesign decision once (and only if) genuinely required, given the current single-Postgres-primary
  evidence model.

---

## 30. Migration Roadmap

**P0 (verified closed, no action needed):**
- `glassbox/app/config.py`'s production-profile check **is confirmed fail-fast**
  (`ProfileViolationError` raised at construction, `config.py:518-544`) if in-memory
  dispatcher/limit-store adapters are wired under `RuntimeProfile.PRODUCTION` — verified by direct
  read during this review. No further action required.

**P1 (close before handling regulated/financial data or before a compliance audit):**
- Add automatic classification/redaction for action parameters before evidence write.
- Extend the keyed-MAC chain to outcome records.
- Ship a reference cron/CronJob/systemd-timer manifest for `adapters/inbound/cli/maintenance.py`,
  and add a staleness alert if it hasn't run recently.

**P2 (close within the next planning cycle):**
- Add `tenant_id` + RLS to `dispatch_ledger`.
- Fix `WorkflowEngine._quorum_state` leak (`try/finally` + TTL eviction).
- Add an explicit forbidden-imports contract for `glassbox.adapters.inbound` mirroring the one
  `adapters.outbound` already has.
- Run Spark real-mode tests in CI (`GLASSBOX_SPARK_LOCAL_JOB=1`) at least on a scheduled/nightly
  basis if not every PR.

**P3 (housekeeping / roadmap-dependent):**
- Delete empty `glassbox/api/`, `glassbox/governance/` directories.
- Document explicitly in `docs/ARCHITECTURE.md` that `RiskConfig.enforce_threshold` defaults off.
- If MCP support is genuinely on the roadmap, scope it as a new domain-design effort (AgentVersion +
  tool publisher/version/URI + chain attenuation), not an incremental `ToolRegistry` change.

---

## 31. Final Verdict

**Retain and extend.** GlassBox v2 is a well-architected, evidence-first governance boundary with
mechanically enforced layering, genuinely fail-closed defaults, and cross-replica correctness proven
against real infrastructure rather than mocks. It is measurably more mature than it was at the last
full review (2026-08-20): several previously-flagged gaps (resource-scoped mandates, tool-output
re-scanning, risk-threshold wiring, Tenant/AuditEvent entities, physical Postgres partitioning, S3
WORM, full v1 deletion) have since been closed and verified. The remaining gaps are narrow,
well-understood, and additive to fix — none require a redesign of the core pipeline, evidence model,
or layering. The most consequential open item for any deployment handling sensitive data is the lack
of automatic redaction of action parameters before they enter the immutable evidence trail.

---

## 32. Final Scorecard (0–10, evidence-based)

| Dimension | Score | Evidence |
|---|---|---|
| Business Problem | 8 | Clear, scoped problem (per-action agent authorization + evidence), not over-claimed |
| Vision | 8 | `docs/CLAIMS.md` claims are honest, including documented gaps |
| Domain Model | 7 | Rich authorization concepts; missing AgentVersion, obligation discharge, cumulative risk |
| Architecture | 8 | Hexagonal, mechanically enforced, correctly split sync/batch (Python/Spark/Delta) |
| Agentic AI | 6 | Governs actions well; no MCP, no cumulative-consequence tracking |
| Authorization | 8 | Mandate/policy/risk correctly separated and ordered; resource-scoped grants close a real gap |
| Policy | 7 | Signed, versioned, deny-by-default; no cross-tenant federation |
| Risk | 6 | Deterministic, evidenced; opt-in gating undocumented as such in architecture docs |
| Security | 7 | Strong injection/tenant/replay controls; raw-parameter storage is a real gap |
| Compliance | 6 | Good architectural alignment; redaction and outcome-chain gaps limit regulated-data readiness |
| Audit | 8 | Near-complete traceability; outcome-chain gap is the one honest exception |
| Data Architecture | 8 | Correct Postgres/Redis/Delta/Spark split; dispatch_ledger tenant-scoping gap |
| Concurrency | 8 | No deadlocks/lock-held-during-I/O found; dev/prod adapter boundary correctly drawn and documented |
| Thread Safety | 8 | Consistent single-lock-per-structure pattern, no observed nesting |
| Memory | 7 | One real production leak (`WorkflowEngine._quorum_state`); everything else bounded or dev-only |
| Performance | 6 | I/O-bound design is sound; up to 8 round-trips/decision not yet load-tested in CI |
| Scalability | 7 | Redis sharding/Sentinel already supported; multi-region is a genuine future redesign |
| Reliability | 7 | Fail-closed defaults are strong; unscheduled retention maintenance is an operational risk |
| Testing | 7 | Genuinely rigorous where it counts (multi-process, real infra); ~1/3 of suite is infra-gated |
| Observability | 7 | Strong per-decision traceability; no CI-verified Spark real-mode runs |
| Maintainability | 7 | Excellent layering discipline; `DecisionService` size is the one navigability concern |
| Developer Experience | 7 | Clear module READMEs, mechanically-enforced conventions, some empty legacy dirs remain |
| Operational Readiness | 6 | Retention/partition maintenance not wired to run automatically |
| Multi-Tenancy | 7 | RLS + hash-tag Redis isolation strong; `dispatch_ledger` and shared Redis are the two gaps |
| Multi-Region Readiness | 4 | Single-Postgres-primary evidence model; not designed for active-active yet |
| Next-Gen Readiness | 6 | Strong foundation; MCP/AgentVersion/cumulative-risk are new-design gaps, not extensions |

**Unweighted mean ≈ 7.0/10.** As instructed, this number does not replace the verdict above — the
architecture is sound; the score reflects genuine, scoped, closable gaps, not fundamental unsoundness.

---

## 33. Final Questions — Explicit Answers

1. **Is the fundamental problem worth solving?** Yes — verifiable, evidence-backed authorization for
   autonomous agent actions is a real and growing need.
2. **Is the current architecture solving the correct problem?** Yes — per-action authorization with
   durable evidence, not agent-process monitoring, is the right scope and is honestly represented as such.
3. **Is the domain model strong enough?** Mostly — missing AgentVersion, obligation discharge, and
   cumulative-consequence tracking are the notable gaps.
4. **Is the runtime execution model safe?** Yes for the production (Postgres/Redis) adapter set;
   the in-memory dev adapters are unsafe by design and explicitly documented as such.
5. **Is the code safe under concurrent execution?** Yes within a single process; cross-process
   safety is correctly delegated to Postgres/Redis atomicity, not attempted in Python.
6. **Are shared objects correctly isolated?** Yes — no cross-adapter mutable aliasing was found.
7. **Are locks correctly designed?** Yes — single lock per structure, never held across I/O.
8. **Are there memory growth risks?** One real one in production code (`WorkflowEngine._quorum_state`);
   the rest are dev-only adapters explicitly documented as unbounded.
9. **Are there hidden performance bottlenecks?** The up-to-8-round-trip decision path is not hidden
   (it's a direct consequence of the design) but is not yet load-tested in CI.
10. **Can the system scale horizontally?** Yes for the API/decision layer; Redis and Postgres are the
    two components that need explicit sharding/HA planning as scale grows.
11. **Can it safely operate across multiple tenants?** Yes, with the two narrow gaps noted
    (`dispatch_ledger` RLS, shared Redis noisy-neighbor risk).
12. **Can it operate across multiple processes?** Yes, for the Postgres/Redis-backed adapter set.
13. **Can it operate across multiple regions?** Not yet — single-Postgres-primary evidence chain is
    the blocker; this is a genuine future redesign question, not a bug.
14. **Is authorization truly independent from risk?** Yes, confirmed by direct code read.
15. **Is policy truly versioned and reproducible?** Yes — content-addressed digest, replay re-evaluates deterministically.
16. **Can every action be reconstructed from evidence?** Yes, for intent; outcome integrity is the one gap.
17. **Can every governance decision be replayed?** Yes, structurally proven non-mutating.
18. **Are security controls appropriate for autonomous agents?** Mostly — injection/tenant/replay
    controls are strong; cumulative-agency and MCP-chain controls do not exist yet.
19. **Is this Agent Governance or Decision Governance?** Precisely: **per-action Decision Governance
    of a verified agent's authority**, not agent-process governance — and the codebase never
    overclaims the former.
20. **Which parts should remain?** The entire hexagonal skeleton, the evidence-first pipeline, the
    fail-closed defaults, the dev/prod adapter separation.
21. **Which parts should be refactored?** `DecisionService`'s internals (extract stages);
    `WorkflowEngine`'s quorum-state cleanup.
22. **Which parts should be removed?** The two empty legacy directories (`glassbox/api`,
    `glassbox/governance`).
23. **Which parts should be redesigned?** None at the core-architecture level; only additive
    extensions are needed for the identified gaps.
24. **Five biggest risks:** (1) raw parameter storage in evidence, (2) outcome-chain gap, (3)
    unscheduled retention maintenance, (4) misconfiguring in-memory adapters into production, (5)
    shared-Redis noisy-neighbor risk at high tenant count.
25. **Five biggest opportunities:** (1) redaction hook, (2) outcome-chain extension, (3) AgentVersion
    domain concept, (4) cumulative-consequence tracking, (5) a real scheduling manifest for
    maintenance.
26. **What must be fixed before production?** Nothing at P0 — the production-profile fail-fast guard
    against in-memory adapters was verified by direct code read during this review.
27. **What must be fixed before enterprise/regulated adoption?** Parameter redaction + outcome-chain
    protection (P1).
28. **What should be built next?** AgentVersion domain concept, then (only if actually on the
    roadmap) MCP tool governance.
29. **What should NOT be built next?** Multi-region active-active support, autonomous policy
    optimization, or MCP support speculatively — none of these are justified by current evidence of
    need, and building them now would be premature relative to closing the P0/P1 items above.
30. **Would I, as Principal Architect, approve the current architecture?** **Yes, with the P0/P1
    remediation list above as a condition of production sign-off for any deployment handling
    regulated or financial data**; unconditional approval for internal/trusted-tenant deployments as-is.

---

## 34. The Most Important Architectural Question — Answered

> Can GlassBox guarantee that a specific autonomous action was proposed by the correct agent, on
> behalf of the correct principal and tenant, against the correct resource, evaluated using the
> correct policy and risk versions, explicitly authorized under the correct governance context,
> executed at the correct boundary, and subsequently reconstructed from trustworthy evidence — even
> under concurrent, multi-tenant, multi-process execution and component failure?

**PARTIALLY — with the boundary precisely identified.**

**YES**, with direct implementation evidence, for: agent/principal/tenant identity (cryptographically
verified, never trusted from headers — `decision_service.py:276-295`, `:1025-1060`); resource and
action identity (server-derived consequence/exposure, catalogue-resolved); policy and risk version
pinning (content-addressed digest + pinned `model_version`, both recorded on the intent record);
authorization context (mandate + policy + kill-switch, ordered and fail-closed); execution boundary
(evidence-before-dispatch, structurally enforced); and reconstruction from evidence (intent chain is
keyed-MAC, sequence-checked, Merkle-sealed, and independently verifiable even after purge) — **all
proven under real concurrent, multi-tenant, multi-process execution** when the production
(Postgres/Redis/KMS) adapter set is used, per `test_multiprocess_limits.py`,
`test_dispatcher_idempotency.py`, and the live-Postgres verification recorded in repo memory.

**The precise boundary where the guarantee weakens to PARTIAL:** the **outcome** half of "what was
executed" is not chain-protected the way the intent half is — an insider with database access could
alter an outcome record without the same tamper-evidence the intent enjoys. (The second candidate
boundary — whether production deployments could silently run on unsafe in-memory adapters — was
checked directly in this review and is **closed**: `glassbox/app/config.py` raises
`ProfileViolationError` at construction time if `RuntimeProfile.PRODUCTION` is selected with any
unsafe adapter/switch, so this is not a residual gap.) Close the outcome-chain gap and the answer
becomes an unqualified **YES**.
