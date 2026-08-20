# Production Hardening Guide

Apply this checklist to the target environment. The `prod` runtime profile
enforces application safety settings, but it cannot configure the surrounding
platform.

## 1. Close the Bypass Paths

- Route every effectful agent, workflow, batch, and administrative path through
  GlassBox.
- Remove direct target-system credentials from agents where possible.
- Restrict dispatcher credentials to the minimum actions and resources.
- Use network policy to prevent direct access to effect systems.

## 2. Govern Identity

- Use a governed OIDC issuer/JWKS set or mTLS trust domain.
- Validate issuer, audience, signature, expiry, and key rotation.
- Give each agent instance a distinguishable workload identity.
- Revoke credentials independently of application deployment.
- Treat tenant and subject headers as assertions only.
- Disable shared API keys in production.

Never log bearer tokens or client certificate material.

## 3. Govern Actions and Tools

- Version and approve action catalogue bundles.
- Derive consequence and exposure from server-controlled definitions.
- Permit only schema-declared parameters.
- Resolve governance attestations from systems of record.
- Register tool definition digests and quarantine changed or compromised tools.
- Separate catalogue/tool administration from runtime identities.
- Tool **output**, not just tool registration, is re-scanned for prompt
  injection after dispatch (`glassbox.domain.prompt_injection.scan()`); a
  flagged result raises `ToolOutputQuarantinedError` and is never fed forward
  as trusted content, and only its digest — never the flagged content — is
  evidenced.

## 4. Enforce Authority and Policy

- Issue least-privilege, time-bounded mandates; use `ActionResourceGrant` to
  scope authority to a specific `(action, resource_kind, resource_id)` tuple
  where a blanket action grant is too broad.
- Define rapid revocation and kill-switch ownership.
- Require signed active policy bundles and deny when unavailable.
- Review risk thresholds, limit ceilings, and peer groups under change control.
  `RiskConfig.enforce_threshold`/`deny_level` is an opt-in, tested control
  (`DenialReason.RISK_THRESHOLD_EXCEEDED`) — off by default, since risk
  scoring is otherwise pure observability.
- Keep approval completion in an authenticated, auditable workflow:
  `glassbox.app.approval_service.ApprovalService`, backed by
  `WorkflowEngine.quorum_approve` for dual-control sign-off.

## 5. Protect Evidence and Keys

- Use PostgreSQL roles that prevent runtime UPDATE/DELETE of evidence.
- Apply row-level security and verify tenant context on every transaction.
- Require durable commit/fsync before returning an intent receipt.
- Keep MAC keys in managed KMS; separate key use from key administration.
- Record key identity/version with evidence and test historical verification.
- Seal segments and anchor roots to retention-locked WORM storage.
- Test legal hold, retention expiry, backup, restore, and independent verification.

Tamper evidence is not immutability. Independent key custody and WORM policy are
deployment properties. **Accepted gap:** only `evidence_intent` rows
participate in the MAC chain today; `evidence_outcome` rows are appended but
not yet chain-protected (see [CLAIMS.md](../CLAIMS.md)).

## 6. Harden Distributed State

- Use authenticated, encrypted Redis connectivity where supported.
- Configure HA/persistence appropriate to the required availability.
- Monitor latency, eviction, failover, replication, and memory.
- Bound subject cardinality and namespace all keys by tenant and scope; set
  `LimitsConfig.max_tenant_subjects` so one tenant's burst cannot grow
  without bound and trigger `maxmemory` eviction of another tenant's keys.
- Never fall back to local counters or baselines during outage.

## 7. Harden the HTTP Boundary

The v2 adapter verifies workload credentials through `IdentityVerifier` and
runs a cheap, in-process admission-control guard
(`glassbox.adapters.inbound.http.admission_control.HttpAdmissionController`)
before identity verification — a per-replica sliding-window budget, not a
replacement for platform-level rate limiting. Ingress still must provide:

- TLS with approved protocols/ciphers and certificate lifecycle;
- request body and header limits;
- connection, concurrency, and timeout limits;
- network allowlists/private connectivity where appropriate;
- platform-level rate limiting and denial-of-service protection;
- secure proxy handling with no trust in arbitrary forwarded headers.

The repository ships an application factory, not a hardened public server.

## 8. Protect Dispatch

- Use a durable dispatcher ledger shared by all replicas.
- Forward idempotency tokens to target systems that support them.
- Bound in-flight work and timeouts.
- Reconcile indeterminate outcomes against the target system of record.
- Do not use replay to retry effects.

## 9. Logging and Telemetry

- Redact credentials, certificates, secrets, and sensitive parameters.
- Bound metric label cardinality; avoid raw tenant/resource IDs where policy forbids them.
- Protect telemetry in transit and at rest.
- Alert on identity failures, assertion mismatches, tool changes, kill-switch
  state, policy/dependency failures, limit/baseline denials, evidence failures,
  and indeterminate dispatch.
- Restrict evidence and telemetry access by role and purpose.

## 10. Supply Chain and Runtime

- Install from verified build artifacts and retain an SBOM.
- Review dependency and secret scans; do not suppress findings without rationale.
- Run as a non-privileged identity on patched hosts/images.
- Use read-only filesystems and explicit writable paths where feasible.
- Protect configuration sources and prevent secrets from entering images or Git.
- Exercise rollback without changing historical evidence.

## Verification Checklist

- [ ] `RuntimeProfile.PROD` starts with no unsafe switches.
- [ ] Development adapters are rejected.
- [ ] Invalid identity and tenant assertions are denied.
- [ ] Unknown action/tool and changed tool digest are denied.
- [ ] Redis, policy, evidence, and KMS outages do not permit effects.
- [ ] Intent receipt exists before every dispatcher call.
- [ ] Repeated idempotency keys do not repeat effects.
- [ ] Replay generates no target-system traffic.
- [ ] Evidence modification is detected.
- [ ] Restore and historical verification have been exercised.

```bash
python -m pytest tests/test_adversarial_suite.py tests/test_decision_service.py -q
python -m pytest tests/test_stateless_tenancy.py tests/test_dispatcher_idempotency.py -q
python -m pytest tests/test_sealing.py tests/test_kms_signer.py -q
```