# Operations: SLOs and runbooks

Every metric named below is emitted by `glassbox.app.telemetry.GovernanceMetrics`
once a real backend is installed via `glassbox.adapters.outbound.otel.configure.configure_otel`.

## Published SLOs

| SLO | Target | Metric |
|---|---|---|
| Decision latency | p99 < 50 ms | `glassbox.stage.duration_ms` (sum across a decision's stages) |
| Evidence durability | 100% — no dispatch without a receipt | `glassbox.evidence.write_latency_ms` (presence proves the write happened; `DecisionService` never dispatches without one — see `tests/test_decision_service.py::TestEvidenceBeforeEffect`) |
| Fail-open events | 0 | `glassbox.dependency.fail_closed_total` should be the *only* dependency-outage signal; any denial reason other than `dependency_unavailable`/`limit_exceeded` on a dependency outage is a regression |

## Alerting guidance

| Alert | Condition | Metric |
|---|---|---|
| Decision latency SLO breach | p99(`glassbox.stage.duration_ms` summed per decision) > 50 ms over 5 min | `glassbox.stage.duration_ms` |
| Evidence write degraded | p99(`glassbox.evidence.write_latency_ms`) > 200 ms over 5 min | `glassbox.evidence.write_latency_ms` |
| Fail-closed spike | `glassbox.dependency.fail_closed_total` rate > baseline | `glassbox.dependency.fail_closed_total` |
| Denial rate spike | `glassbox.decisions.denied_total` rate > baseline, grouped by `reason` | `glassbox.decisions.denied_total` |
| Limit rejections spike | `glassbox.limits.rejected_total` rate > baseline | `glassbox.limits.rejected_total` |
| Mandatory stage skipped | `glassbox.stages.mandatory_skipped_total` > 0 | `glassbox.stages.mandatory_skipped_total` |

## Runbooks, one per dependency failure mode

### Postgres (evidence store) unavailable

**Symptom:** `glassbox.dependency.fail_closed_total` rising; decisions failing
with `DenialReason.DEPENDENCY_UNAVAILABLE` or `EvidenceWriteError` propagating
to callers (non-advisory actions only — see `ConsequenceClass.may_degrade_on_dependency_failure`).

**Expected behaviour:** every non-advisory action is denied; the dispatcher is
never called (`tests/test_decision_service.py::TestEvidenceBeforeEffect`).
Advisory actions may continue to be evaluated.

**Response:** restore Postgres connectivity. No decision made during the
outage needs replaying — a denial due to an unavailable dependency is a
correct, evidenced outcome, not data loss. Once restored, confirm the
`dispatch_ledger`/`evidence_intent` schema versions match
`glassbox.adapters.outbound.postgres.schema.SCHEMA_VERSION` before resuming
traffic that was previously paused.

### Redis (limits / baseline / mandate revocation) unavailable

**Symptom:** `glassbox.dependency.fail_closed_total` rising with
`DenialReason.DEPENDENCY_UNAVAILABLE` from the limits or baseline stage.

**Expected behaviour:** non-advisory actions deny (invariant I4 —
`LimitStoreUnavailable`/`BaselineStoreUnavailable` never fail open); advisory
actions degrade (skip, evidenced as `SKIPPED`, reason
`"... store unavailable; action is advisory"`).

**Response:** restore Redis. No manual reconciliation needed — limits and
baselines are external, atomic counters; a denied window during the outage is
not lost budget (the failed `try_consume` never subtracted anything).

### KMS (evidence signing key) unavailable

**Symptom:** `SigningUnavailableError` propagating from `append_intent`; no
decisions completing at all (identity and mandate stages still run, but the
evidence write — and therefore any outcome — cannot proceed).

**Expected behaviour:** the process never falls back to an unkeyed MAC. This
is total unavailability for effectful actions, by design — see
`glassbox/adapters/outbound/kms/signer.py`'s circuit-breaker and MAC-cache
trade-off documentation for the bounded-degradation options available before
declaring a hard outage.

**Response:** restore KMS connectivity or key availability. If the local MAC
cache and circuit breaker are configured, a short outage may be absorbed
without visible impact — check `glassbox.evidence.write_latency_ms` for
elevated (not failed) latency as the first signal before a hard failure.

### Dispatcher timeout storm

**Symptom:** `ExecutionStatus.INDETERMINATE` outcomes rising;
`glassbox.dispatch` spans (once tool-call/dispatch tracing lands) showing
elevated duration; `DispatchRefusedError` rising if the in-flight bound
(`max_in_flight`) is being hit.

**Expected behaviour:** a timeout is recorded as `INDETERMINATE`, never
`FAILED` — the effect's true state is unknown, and the dispatcher's admission
bound (`glassbox.adapters.outbound.postgres.dispatcher.PostgresDispatcher`)
refuses new work rather than queuing it unboundedly
(`tests/test_batch_admission_control.py`).

**Response:** investigate the downstream effect system. `INDETERMINATE`
outcomes require manual reconciliation against the downstream system of
record — GlassBox's own ledger (`dispatch_ledger`) proves whether *this*
process attempted the effect exactly once, but not whether the downstream
call actually completed.
