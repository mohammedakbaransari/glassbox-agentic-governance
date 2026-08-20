# GlassBox — Claim-to-Test Traceability

Every capability claimed in GlassBox's documentation cites the exact code
that implements it and the exact test that proves it. A claim without both
is not documented here. `tests/test_claims_coverage.py` fails the build if a
citation stops resolving, so this file cannot silently drift from the code.

Claims here are about the code as it exists today. `glassbox/governance/`,
`glassbox/api/` and the other v1 packages that implemented an earlier
synchronous design have been physically deleted now that
`glassbox/app`/`glassbox/domain`/`glassbox/ports`/`glassbox/adapters` fully
supersede them (GB-040). `glassbox/store/` was kept, but trimmed to only
`WorkflowRepository`/`SQLiteWorkflowRepository` — the sanctioned
implementation behind `glassbox.ports.workflow.WorkflowGateway` — everything
else it used to hold was v1-only and was removed with the rest.

---

## Core guarantees

| # | Claim | Code | Test |
|---|---|---|---|
| 1 | Forging any evidence row causes verification to fail | [glassbox/adapters/outbound/memory/evidence.py:67](../glassbox/adapters/outbound/memory/evidence.py) `InMemoryEvidenceStore` (keyed MAC); [glassbox/adapters/outbound/postgres/evidence.py:170](../glassbox/adapters/outbound/postgres/evidence.py) `PostgresEvidenceStore` | `tests/test_adversarial_suite.py::TestThreat01AuditForgery`, `tests/test_memory_adapters.py::TestEvidenceIntegrity` |
| 2 | Killing the process between intent and effect loses no evidence | [glassbox/adapters/outbound/postgres/evidence.py:206](../glassbox/adapters/outbound/postgres/evidence.py) `append_intent` (`SELECT...FOR UPDATE` sequence allocation, durable before return) | `tests/test_adversarial_suite.py::TestThreat17WalOverwrite`, `tests/test_postgres_evidence.py::TestTransactionShape::test_the_segment_is_locked_before_the_chain_head_is_read` |
| 3 | Velocity limits are distributed, collision-free, and fail closed | [glassbox/adapters/outbound/redis/limits.py:145](../glassbox/adapters/outbound/redis/limits.py) `RedisLimitStore.try_consume` | `tests/test_multiprocess_limits.py` (real OS processes), `tests/test_adversarial_suite.py::TestThreat09VelocityEvasionViaOutage`, `TestThreat10VelocityUndercount` |
| 4 | Tenant identity is never optional and is enforced at the storage boundary too | [glassbox/app/decision_service.py](../glassbox/app/decision_service.py) (`principal.tenant_id` required, non-optional, on every path); [glassbox/adapters/outbound/postgres/schema.py](../glassbox/adapters/outbound/postgres/schema.py) (row-level security) | `tests/test_adversarial_suite.py::TestThreat04CrossTenantDataRead`, `TestThreat05CrossTenantPolicyLeak`, `tests/test_stateless_tenancy.py` |
| 5 | Every executor-side Spark callable is cloudpickle-serialisable | [glassbox/adapters/outbound/spark/batch_preauth.py](../glassbox/adapters/outbound/spark/batch_preauth.py) (pure, cloudpickle-safe batch pre-authorisation; no stateful object is ever closed over) | `tests/test_spark_serializable.py` |
| 6 | Delta Lake Bronze/Silver medallion is real, tested I/O | [glassbox/adapters/outbound/delta/{bronze,silver,cdc_consumer}.py](../glassbox/adapters/outbound/delta/) | `tests/test_delta_medallion.py` (11 tests, real Delta tables on disk) |
| 7 | `pip install -e .` succeeds from a clean checkout | [pyproject.toml](../pyproject.toml) (`setuptools.build_meta` backend) | CI job `build-and-install` (`.github/workflows/ci.yml`), `tests/test_packaging.py` |
| 8 | Test-suite size is not itself a readiness claim | — | Superseded by claim coverage: every row in this document, not a raw test count |
| 9 | Evidence is written before the effect, never after | [glassbox/app/decision_service.py:510](../glassbox/app/decision_service.py) `decide_and_dispatch` (evidence `append_intent` before `_dispatch_if_permitted`) | `tests/test_decision_service.py::TestEvidenceBeforeEffect` |
| 10 | Anomaly detection is not bypassed by a cold-start window | [glassbox/adapters/outbound/memory/governance_state.py:235](../glassbox/adapters/outbound/memory/governance_state.py) `InMemoryBaselineStore` (peer-group cold-start prior) | `tests/test_adversarial_suite.py::TestThreat08AnomalyEvasion`, `tests/test_memory_adapters.py::TestBaselineStore` |

Additional claims that hold: zero mandatory dependencies
(`dependencies = []` in [pyproject.toml](../pyproject.toml), asserted by
`tests/test_packaging.py`). See
[docs/COMPLIANCE/requirements.md](COMPLIANCE/requirements.md) for the
97-control, 24-framework crosswalk to the mechanisms in this document (an
engineering reference, not a certification, and not itself a code claim).

---

## Adversarial test coverage

Every row below is a permanent regression test in
`tests/test_adversarial_suite.py`, one class per numbered threat.

| # | Threat | How it is closed | Test |
|---|---|---|---|
| 1 | Audit forgery | Keyed MAC over the evidence chain | `TestThreat01AuditForgery` |
| 2 | Tenant impersonation | Header is an assertion checked against the verified principal | `TestThreat02TenantImpersonation` |
| 3 | User impersonation | Same assertion-vs-principal check, on subject | `TestThreat03UserImpersonation` |
| 4 | Cross-tenant data read | `tenant_id` never optional; row-level security as defence-in-depth | `TestThreat04CrossTenantDataRead` |
| 5 | Cross-tenant policy leak | Policy decision point keyed by tenant | `TestThreat05CrossTenantPolicyLeak` |
| 6 | Risk downgrade via caller input | No `confidence`/`environment` field exists on the governed signature | `TestThreat06RiskDowngrade` |
| 7 | Control bypass via self-asserted attestations | Attestations resolved from an `AttestationProvider`, never from parameters | `TestThreat07ControlBypass` |
| 8 | Anomaly evasion (cold start) | Peer-group prior | `TestThreat08AnomalyEvasion` |
| 9 | Velocity evasion via outage | Fails closed for non-advisory actions | `TestThreat09VelocityEvasionViaOutage` |
| 10 | Velocity undercount | Collision-free window member | `TestThreat10VelocityUndercount` |
| 11 | Unknown-tool execution | Deny-by-default tool registry | `TestThreat11UnknownToolExecution` |
| 12 | Replay-triggered re-execution | `NullDispatcher` raises if invoked | `TestThreat12ReplayTriggeredReExecution` |
| 13 | Threshold tampering | No unsigned mutation path on a `PolicyBundle` | `TestThreat13ThresholdTampering` |
| 14 | Self-DoS | Bounded `max_in_flight`, refused not queued | `TestThreat14SelfDos` |
| 15 | Memory exhaustion | `max_subjects` bound on every per-agent store | `TestThreat15MemoryExhaustion` |
| 16 | Evidence loss | `append_intent` raises, never swallowed | `TestThreat16EvidenceLoss` |
| 17 | WAL overwrite | Sequence allocated inside the append's critical section | `TestThreat17WalOverwrite` |
| 18 | Sanitizer availability DoS | Schema allow-list, never content pattern-matching | `TestThreat18SanitizerAvailabilityDos` |
| 19 | Key sprawl | No shared-bearer-key `CredentialType` | `TestThreat19KeySprawl` |
| 20 | FIPS host failure (`md5`) | No non-`usedforsecurity=False` `hashlib.md5` anywhere in the codebase | `TestThreat20FipsHostFailure` |

---

## Success criteria

| # | Criterion | Test |
|---|---|---|
| S1 | Forging any evidence row causes verification to fail | `tests/test_adversarial_suite.py::TestThreat01AuditForgery`, `tests/test_memory_adapters.py::TestEvidenceIntegrity` |
| S2 | Killing the process between intent and effect loses no evidence | `tests/test_postgres_evidence.py` (durable-before-return transaction shape) |
| S3 | Purging within retention keeps the chain verifiable | `tests/test_sealing.py` |
| S3a | A production-grade, object-lock-backed WORM anchor store exists (not just filesystem/in-memory), and its write-once guarantee holds under a losing write race | `tests/test_sealing.py::TestS3Anchors` |
| S4 | 3 replicas + real Redis never admit more than `max_decisions` | `tests/test_multiprocess_limits.py` |
| S5 | Redis unavailable ⇒ irreversible actions denied | `tests/test_adversarial_suite.py::TestThreat09VelocityEvasionViaOutage` |
| S6 | Spoofed `X-Tenant-ID` / `X-User-ID` is rejected | `tests/test_http_app.py`, `tests/test_adversarial_suite.py::TestThreat02TenantImpersonation` |
| S7 | Tenant A cannot read or be governed by tenant B | `tests/test_adversarial_suite.py::TestThreat04CrossTenantDataRead` |
| S8 | An unmapped tool is denied, not auto-executed | `tests/test_adversarial_suite.py::TestThreat11UnknownToolExecution` |
| S9 | Replay never invokes an executor | `tests/test_replay.py`, `tests/test_adversarial_suite.py::TestThreat12ReplayTriggeredReExecution` |
| S10 | `pip install .` and `pip install -e .` succeed | CI job `build-and-install`, `tests/test_packaging.py` |
| S11 | The same inputs produce the same risk score forever | `tests/test_risk_determinism.py` |

---

## Hardening of the earlier implementation

`glassbox/governance/`, `glassbox/security/`, `glassbox/api/`,
`glassbox/adapters/spark.py` and the other v1 packages this section used to
describe have been **physically deleted** (GB-040). Each mechanism this
table previously tracked (an unaudited RBAC impersonation path, an
unconditional retention `DELETE`, an `UPDATE`/`DELETE` inside a
tamper-evident log, a plain-text JSONL audit sink, a per-row Spark UDF, and
overly broad SQL-pattern sanitisation) was hardened — proven inert or
replaced by its v2 equivalent — before removal, and is preserved in git
history at the commit that performed this deletion, should the exact
before/after diff ever need to be re-examined. The v2 equivalents these
fixes pointed to remain active and covered: `glassbox.app.sealer.SegmentSealer`
(retention), `glassbox/adapters/outbound/spark/` (Spark), and the append-only
evidence store's database-level append-only trigger (tamper-evidence) — see
`tests/test_sealing.py`, `tests/test_spark_serializable.py`, and
`tests/test_postgres_evidence.py`.

`glassbox.workflow` and `glassbox.store` are the two exceptions kept in
place: they are not v1 debt but the sanctioned implementation behind
`glassbox.ports.workflow.WorkflowGateway`, reached only through that port by
the rebuilt layers (see the port's own docstring).

---

## What GlassBox does not do (yet)

Stated explicitly, rather than left to be assumed:

- **No UI.** GlassBox is a library and an HTTP surface, not an operator console.
- **No per-row PySpark governance in the decision path**, by design. Spark is
  confined to batch pre-authorisation and Gold-layer control-testing jobs
  ([glassbox/adapters/outbound/spark/](../glassbox/adapters/outbound/spark/)).
- **No automatic obligation discharge.** A `REQUIRE_APPROVAL` decision or an
  unmet blocking obligation is recorded as `PENDING_APPROVAL`; resolving it is
  an external workflow, not something `DecisionService` does itself.
- **No production Postgres logical-replication CDC source yet.** The CDC
  consumer is fully built and tested against a pluggable source; the
  production Postgres-replication-backed source
  (`psycopg2.extras.LogicalReplicationConnection`) is not yet implemented —
  see `glassbox/adapters/outbound/delta/cdc_consumer.py`'s `ChangeEventSource`
  protocol.
- **Outcome records are not MAC-chained.** `append_intent` participates in the
  signed hash chain; `append_outcome` (`evidence_outcome` rows) does not — an
  operator with direct database access could alter a recorded execution
  outcome without breaking the intent chain's integrity check. Accepted gap,
  not fixed: extending the chain to outcome records needs a schema migration
  (`prev_hash`/`record_hmac`/`seq` columns) and a dual-chain verification path
  that deserves its own dedicated, live-Postgres-verified pass rather than a
  bundled one.
- **No v2-native MCP tool-poisoning detection.** The legacy
  `glassbox.integrations.mcp_gateway` (`MCPToolScanner`/`MCPGovernanceGateway`)
  was physically deleted along with the rest of v1 (GB-040) and was not
  ported. Retiring it (not porting it) was the deliberate choice: the v2
  `glassbox.domain.prompt_injection` scanner plus tool-output re-scanning
  cover the same threat class (untrusted content driving a subsequent
  decision) generically, not just for MCP-shaped tool calls specifically.
- **This document is not exhaustive.** It covers the guarantees, threats, and
  success criteria with the highest cost if wrong. A full line-by-line audit
  of every document under `docs/` remains open work.

---

## Verification

`tests/test_claims_coverage.py` parses this file and asserts that every cited
`tests/test_*.py::TestClass::test_method` (or `tests/test_*.py` bare file
reference) actually exists, so a citation cannot silently rot as the test
suite evolves.
