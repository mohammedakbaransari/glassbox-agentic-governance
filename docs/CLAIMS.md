# GlassBox — Claim-to-Test Traceability

Every capability claimed in GlassBox's documentation cites the exact code
that implements it and the exact test that proves it. A claim without both
is not documented here. `tests/test_claims_coverage.py` fails the build if a
citation stops resolving, so this file cannot silently drift from the code.

Claims here are about the code as it exists today. Some modules under
`glassbox/governance/`, `glassbox/store/` and `glassbox/api/` implement an
earlier synchronous design that is being superseded by
`glassbox/app`/`glassbox/domain`/`glassbox/ports`/`glassbox/adapters`; where
that distinction matters to a claim, it is called out explicitly rather than
assumed.

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

Additional claims that hold: 97 compliance controls across 24 frameworks
([glassbox/compliance/catalogue.py](../glassbox/compliance/catalogue.py),
asserted by its own test suite); zero mandatory dependencies
(`dependencies = []` in [pyproject.toml](../pyproject.toml), asserted by
`tests/test_packaging.py`).

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

The following mechanisms in `glassbox/governance/`, `glassbox/security/` and
`glassbox/adapters/spark.py` were removed or rebuilt because there was no
safe way to fix them in place. Each is nested inside a module that otherwise
remains in active use.

| # | Mechanism | What changed | Test |
|---|---|---|---|
| 1 | JSONL audit sink | [glassbox/governance/audit_logger.py](../glassbox/governance/audit_logger.py) `AuditLogger._append_jsonl` and its `log_dir`-driven file creation were removed; plain-text audit output is not tamper-evident. `log_dir`/`fsync_writes` remain accepted (inert) for backward compatibility | `tests/test_core.py::TestAuditLogger::test_concurrent_writes_no_corruption`, `tests/test_security.py::TestAuditLoggerFileSafety::test_concurrent_writes_produce_one_record_per_decision` |
| 2 | In-process RBAC | [glassbox/governance/access_control.py](../glassbox/governance/access_control.py) `AccessControl.impersonate()` (an unaudited, externally-unrevocable privilege-escalation mechanism) now raises `NotImplementedError`; the permission cache is bounded by `MAX_CACHE_ENTRIES` | `tests/test_enterprise.py::TestAccessControl::test_impersonation_removed`, `test_permission_cache_is_bounded` |
| 3 | Business-payload regex scanning | [glassbox/security/sanitizer.py](../glassbox/security/sanitizer.py) removed the bare `\b(select\|insert\|update\|...)\b` keyword pattern and the bare `0x[0-9a-fA-F]{4,}` hex pattern from `_SQL_PATTERNS` — both matched ordinary business text with no SQL syntax context. Patterns requiring real SQL syntax (statement separators, function calls, `UNION...SELECT`) remain | `tests/test_comprehensive.py::TestPayloadSanitizerEnhancements::test_ordinary_business_text_with_sql_verbs_not_blocked`, `test_hex_formatted_identifier_not_blocked` |
| 4 | Per-row Spark UDF | [glassbox/adapters/spark.py](../glassbox/adapters/spark.py) `GlassBoxSparkAdapter._govern_via_udf` and its driver-side pipeline were deleted entirely (cloudpickle cannot serialise a lock/thread-pool/queue held on a stateful object); `govern_dataframe()` always uses `mapPartitions`, where every executor builds its own state locally | `tests/test_core.py::TestSparkAdapter::test_govern_dataframe_always_uses_map_partitions`, `test_adapter_shutdown_is_a_noop` |
| 5 | Unconditional retention purge | [glassbox/governance/advanced_audit.py](../glassbox/governance/advanced_audit.py) `TamperEvidentAuditLogger.purge_old_records` used to run an unconditional `DELETE` that permanently broke `verify_hash_chain`; it now raises `NotImplementedError` pointing to `glassbox.app.sealer.SegmentSealer`, which seals a signed WORM anchor before purging | `tests/test_edge_cases.py::TestTamperEvidentAuditLogger::test_purge_old_records_removed` |
| 6 | `UPDATE`/`DELETE` inside a tamper-evident log | `log_action` used to `INSERT` a placeholder row then `UPDATE ... SET record_hash`; it now computes the hash before the row exists and issues a single `INSERT`. No code path in this class runs `UPDATE` or `DELETE` any more | `tests/test_enterprise.py::TestAdvancedAudit`, `tests/test_hash_chain_tamper.py::TestHashChainTampering` |

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
- **No S3-backed WORM anchor adapter yet.** `WormAnchorStore` has in-memory
  and filesystem implementations only; an S3 Object Lock adapter is not yet
  written (`docker-compose.yml`'s MinIO service exists for when one is).
- **No production Postgres logical-replication CDC source yet.** The CDC
  consumer is fully built and tested against a pluggable source; the
  production Postgres-replication-backed source
  (`psycopg2.extras.LogicalReplicationConnection`) is not yet implemented —
  see `glassbox/adapters/outbound/delta/cdc_consumer.py`'s `ChangeEventSource`
  protocol.
- **The earlier synchronous implementation is still present.**
  `glassbox/governance/`, `glassbox/store/`, `glassbox/api/app.py`,
  `glassbox/adapters/spark.py`, `glassbox/security/sanitizer.py` and related
  packages remain importable; no claim in this document is made about them
  beyond the hardening table above, and `tests/test_layering.py` prevents any
  module under `glassbox/app`, `glassbox/domain`, `glassbox/ports` or
  `glassbox/adapters/outbound` from depending on them. `process()` remains a
  live entry point on the original pipeline; it is not a shim over
  `DecisionService.decide()`.
- **This document is not exhaustive.** It covers the guarantees, threats, and
  success criteria with the highest cost if wrong. A full line-by-line audit
  of every document under `docs/` remains open work.

---

## Verification

`tests/test_claims_coverage.py` parses this file and asserts that every cited
`tests/test_*.py::TestClass::test_method` (or `tests/test_*.py` bare file
reference) actually exists, so a citation cannot silently rot as the test
suite evolves.
