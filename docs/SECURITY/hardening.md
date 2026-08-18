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

## 4. Enforce Authority and Policy

- Issue least-privilege, time-bounded mandates.
- Define rapid revocation and kill-switch ownership.
- Require signed active policy bundles and deny when unavailable.
- Review risk thresholds, limit ceilings, and peer groups under change control.
- Keep approval completion in an authenticated, auditable external workflow.

## 5. Protect Evidence and Keys

- Use PostgreSQL roles that prevent runtime UPDATE/DELETE of evidence.
- Apply row-level security and verify tenant context on every transaction.
- Require durable commit/fsync before returning an intent receipt.
- Keep MAC keys in managed KMS; separate key use from key administration.
- Record key identity/version with evidence and test historical verification.
- Seal segments and anchor roots to retention-locked WORM storage.
- Test legal hold, retention expiry, backup, restore, and independent verification.

Tamper evidence is not immutability. Independent key custody and WORM policy are
deployment properties.

## 6. Harden Distributed State

- Use authenticated, encrypted Redis connectivity where supported.
- Configure HA/persistence appropriate to the required availability.
- Monitor latency, eviction, failover, replication, and memory.
- Bound subject cardinality and namespace all keys by tenant and scope.
- Never fall back to local counters or baselines during outage.

## 7. Harden the HTTP Boundary

The v2 adapter verifies workload credentials through `IdentityVerifier`; ingress
still must provide:

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
python -m pytest tests/test_hash_chain_tamper.py tests/test_kms_signer.py -q
```

## Legacy Boundary

`glassbox/security` and the v1 API contain compatibility controls with different
contracts. Do not copy their middleware, RBAC, sanitization, or encryption
examples into the current runtime without a new port/adaptor design and threat
review.