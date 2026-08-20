# Operations and Runbooks

Operational targets must be set from measured deployment behavior. The
repository does not publish a universal latency or availability SLA. It does
publish invariants that must remain true under load and failure.

## Invariant Indicators

| Indicator | Required condition |
|---|---|
| Dispatch without durable intent | Zero |
| Replay-triggered effects | Zero |
| Cross-tenant state/evidence access | Zero |
| Duplicate effects for one idempotency key | Zero |
| Mandatory stage silently absent | Zero |
| Safety dependency outage that permits an effect | Zero |

Available telemetry includes decision counts/denials, stage duration, evidence
write latency, dependency fail-closed counts, limit rejections, and mandatory
stage skips when a real backend is installed through the OpenTelemetry adapter.
Metric availability and export are deployment-specific.

## Alert Classes

- identity verification and assertion mismatch spikes;
- policy/catalogue/tool/mandate dependency failures;
- Redis limit or baseline errors and denial spikes;
- evidence commit or KMS signing failures;
- mandatory stage skips;
- dispatcher saturation, timeout, and indeterminate outcomes;
- evidence verification failures;
- kill-switch state changes;
- p95/p99 latency or error rates outside measured SLOs.

## PostgreSQL or Evidence Failure

**Expected:** no effect dispatches without a receipt.

1. Pause or shed incoming effectful traffic if denial volume threatens callers.
2. Restore database connectivity and verify schema/tenant policy state.
3. Confirm evidence chain integrity and sequence allocation.
4. Re-submit business requests with their original idempotency keys where the
   business owner decides retry is appropriate.
5. Do not insert, update, or delete historical evidence as a repair.

**Evidence lifecycle (retention scheduler / maintenance CLI):** evidence
tables are monthly-partitioned (`evidence_intent`, `evidence_outcome`).
`glassbox.app.retention_scheduler` seals a segment (WORM anchor) before
purging it; `glassbox/adapters/inbound/cli/maintenance.py` is the operator
entry point for manual partition/retention maintenance. Never purge a segment
whose anchor write has not been confirmed durable.

## Redis Failure

**Expected:** non-advisory effects deny; no local permissive fallback.

1. Verify connectivity, authentication, failover, memory, and eviction.
2. Confirm all replicas use the same endpoint and key namespace.
3. Restore service and observe cooldown/baseline recovery.
4. Treat denied requests as not admitted; retry only through the normal boundary.

**Per-tenant quota exhaustion:** if `LimitsConfig.max_tenant_subjects` is set
and a tenant's own distinct-subject count exceeds it, the oldest subjects for
that tenant are evicted (their counters reset), not another tenant's. A
tenant reporting unexpectedly-reset counters likely means this bound is set
too low for its real cardinality — raise the bound, don't disable it.

## KMS or Signing Failure

**Expected:** intent append fails and dispatch does not occur.

1. Check key state, policy, quota, region/endpoint, and network path.
2. Confirm signer key ID has not drifted from configuration.
3. Restore signing and verify a new record plus historical records.
4. Never switch to a local readable key in production.

## Identity or Policy Failure

**Expected:** no principal or authorization is guessed.

1. Check issuer/JWKS, certificate trust, bundle registry, signatures, and activation.
2. Distinguish invalid data from dependency unavailability in structured errors.
3. Restore governed data; do not bypass verification with request headers or
   an emergency allow-all policy.

## Dispatcher Timeout or Indeterminate Outcome

The target effect may have completed even when the response was lost.

1. Stop blind retries.
2. Query the durable dispatch ledger and target system using the idempotency key.
3. Reconcile the true state with an authorized operator.
4. Record remediation without rewriting original evidence.

## Kill Switch

Treat activation as a security/operations event. Confirm scope, owner, reason,
and expected duration. Keep evidence and identity paths available so denied
attempts remain attributable. Require authorized, auditable deactivation.

## Tool Output Quarantined

**Expected:** `execution.status` is `FAILED` with `error_class ==
"ToolOutputQuarantinedError"`; `decision.effect` remains whatever it was
(usually `ALLOW` — the action was correctly authorised, the *result* is what
was flagged).

1. This is not a governance denial — do not treat it as a policy bug.
2. Retrieve the matched pattern context from the raised error (never from
   evidence: the flagged content itself is never persisted, only its digest).
3. Decide, out of band, whether the tool/endpoint that produced the flagged
   content needs to be reviewed, patched, or removed from the registry.
4. Do not retry the same call expecting a different governance outcome — the
   underlying effect already ran once.

## HTTP Admission Control Rejected (429)

**Expected:** the request never reached `DecisionService`; no evidence exists
for it.

1. Confirm this is `HttpAdmissionController` (per-process, pre-identity) and
   not a `LimitStore` denial (distributed, post-identity, evidenced) — check
   whether an `IntentRecord` exists for the caller's idempotency key.
2. A sustained 429 rate from one client key usually indicates a genuine
   retry storm or a misconfigured caller, not an outage.
3. Adjust `max_requests`/`window_seconds` per replica capacity, not by
   removing the guard.

## Approval Awaiting Review

**Expected:** `execution.status` is `PENDING_APPROVAL`; no effect has dispatched.

1. Query `ApprovalService.list_pending()` / `list_sla_breached()` for the
   review queue and SLA state.
2. Resolution is always an explicit `approve`/`reject`/`escalate`/`revoke`
   call — there is no automatic timeout-to-approve.
3. `expire_overdue()` (scheduled externally) transitions a still-pending,
   SLA-breached workflow to `expired`; it does not approve or execute it.

## Evidence Verification Failure

1. Preserve the affected segment and surrounding storage snapshots.
2. Restrict access; do not attempt in-place repair.
3. Verify signer key metadata and WORM anchor independently.
4. Determine whether corruption, wrong key/version, software defect, or
   unauthorized modification caused the failure.
5. Escalate under the incident and legal/compliance process.

## Routine Exercises

- PostgreSQL backup/restore and chain verification
- Redis failover with fail-closed behavior
- KMS outage and key rotation
- identity key rotation/revocation
- policy/catalogue rollback
- kill-switch activation/deactivation
- duplicate request and indeterminate dispatch reconciliation
- replay with proof of zero target effects

## Related Documentation

- [Troubleshooting](../USER/troubleshooting.md)
- [Deployment](../DEPLOYMENT/README.md)
- [Performance](../DEPLOYMENT/performance_tuning.md)
- [Security hardening](../SECURITY/hardening.md)