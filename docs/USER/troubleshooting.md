# Troubleshooting

This guide covers the current `DecisionService` runtime. For
`GovernancePipeline` compatibility issues, use the
[legacy architecture reference](../DEVELOPMENT/architecture.md) and the
module README associated with the failing v1 component.

## Triage Order

Diagnose from the outside inward:

1. Confirm the process composed a complete runtime.
2. Confirm the request reached the intended v2 route.
3. Verify identity and tenant assertions.
4. Inspect the decision's stage outcomes and denial reasons.
5. Check the dependency behind the first failed stage.
6. Verify intent evidence exists before investigating dispatch.

Do not disable fail-closed controls to make a request pass. Reproduce in the
development profile with controlled adapters instead.

## Quick Diagnostic Matrix

| Symptom | Likely boundary | First check |
|---|---|---|
| Runtime will not start | Configuration/composition | Structured configuration or composition error |
| `401` from v2 HTTP | Identity | Credential transport and verifier configuration |
| `400` from v2 HTTP | Request/domain validation | Required resource and idempotency fields |
| `403` from v2 HTTP | Governance denial | Decision reasons and stage outcomes |
| `503` from v2 HTTP | Evidence/signing dependency | Evidence database and KMS availability |
| Approval remains pending | External workflow | Approval system; current request never dispatches |
| Duplicate effect concern | Dispatcher ledger | Idempotency key and durable ledger state |
| Different behavior across replicas | Distributed state | Redis/PostgreSQL adapter selection and tenant keys |

## Runtime Composition Fails

`build_runtime` reports all missing factories, factory exceptions, and protocol
conformance failures together. Check that configuration validates, the
`AdapterSet` covers every `REQUIRED_COMPONENTS` entry, no factory returns
`None`, every object satisfies its protocol, and production does not use a
`dev_only` set.

```bash
python -m pytest tests/test_app.py -q
```

## Action Is Not Governed

Typical reason: `action_not_governed` or a catalogue-unavailable error.
Verify that the active tenant bundle contains the exact action name, its schema
allows every supplied key, and required attestations resolve from the system of
record. Callers cannot supply consequence, exposure, or attestation verdicts.

For the memory adapter, no catalogue bundle is loaded by default. See the
[quick start](quick_start.md#run-a-governed-action).

## Identity or Tenant Check Fails

Live v2 requests accept `Authorization: Bearer <token>` for OIDC material or
`X-Client-Cert` for mTLS material. The transport only extracts a
`RawCredential`; the verifier must establish tenant, agent, and instance.

`X-Tenant-ID` and `X-Subject-Id` are assertions. If supplied, they must match
the verified principal. `resource.tenant_id` must also agree. A contradiction is
treated as a spoofing attempt and evidenced.

## Mandate Denies the Request

Check the mandate's tenant, agent, validity interval, revocation state, action
and resource patterns, consequence/exposure ceilings, and tool grant digest.
Policy cannot grant authority that the mandate does not contain.

## Policy Denies or Is Unavailable

Use the machine-readable reason and stage outcome to distinguish policy denial
from policy dependency failure. Verify active signed bundle identity, tenant,
activation state, signature requirements, and decision-point availability. Do
not infer v2 policy state from legacy Python policy objects.

## Baseline Anomaly on a New Agent

Cold-start detection deliberately does not skip evaluation. A subject with too
few samples uses a peer-group prior; with no prior, the observation is anomalous.

- Load an approved peer baseline before admitting a new agent.
- Confirm tenant, scope, subject, metric, and window keys.
- Inspect sample count, z-score, threshold, and `used_peer_prior`.
- Do not lower the threshold merely to bypass missing history.

## Limit Exceeded or Redis Unavailable

The current `LimitStore` contract is atomic and fail closed. Verify Redis
connectivity, identical keys/windows across replicas, unique decision IDs,
cooldown state, server time, and that no replica uses memory adapters.

An unavailable production store must not fall back silently to local memory;
that would multiply the limit by replica count.

```bash
python -m pytest tests/test_redis_limits.py tests/test_multiprocess_limits.py -q
```

Live tests require `GLASSBOX_REDIS_URL`.

## Evidence or Signing Fails

If intent evidence cannot be signed and durably appended, dispatch must not
occur. Inspect database transactions and tenant context, signing-key identity
and KMS policy, segment allocation and chain locking, and production safety
switches. Preserve the idempotency key and retry through governance after the
dependency recovers; never call the effect directly.

```bash
python -m pytest tests/test_postgres_evidence.py tests/test_kms_signer.py -q
python -m pytest tests/test_decision_service.py -q
```

## Dispatch Fails or Repeats

Verify that a valid intent receipt exists, then inspect the dispatcher and its
durable idempotency ledger. A process-local set is insufficient across replicas.
The effect system should also accept an idempotency token where possible.
Dispatch failures are recorded as outcomes and do not erase intent evidence.

Replay is not retry: `DecisionService.replay` never dispatches.

## HTTP Route Returns 404

| Runtime | Routes |
|---|---|
| DecisionService v2 | `/healthz`, `/v2/actions/...`, `/v2/tools/...`, `/v2/replay` |
| GovernancePipeline v1 | `/health`, `/decisions`, `/stats`, and the legacy route set |

The Flask factories are separate. Use the [API overview](../API/README.md) and
do not mix clients or payloads.

## Health Is Green but a Dependency Is Down

`GET /healthz` describes a runtime that composed successfully. It is not a deep
probe of PostgreSQL, Redis, KMS, OIDC, or WORM storage. Implement
deployment-specific readiness probes at the process/platform boundary.

## OpenTelemetry Shutdown Warning in Tests

Console export may attempt to write after pytest closes captured output:

```text
ValueError: I/O operation on closed file
```

If pytest already reported success, this is a non-failing exporter shutdown
warning. Prefer a test exporter or disable console export in tests. Do not
ignore telemetry errors during normal process life.

## Integration Test Is Skipped

- PostgreSQL: `GLASSBOX_POSTGRES_DSN`
- Redis: `GLASSBOX_REDIS_URL`
- local Spark job: `GLASSBOX_SPARK_LOCAL_JOB=1`
- build/install checks: `GLASSBOX_RUN_BUILD_TESTS=1`

See the [testing strategy](../DEVELOPMENT/testing.md).

## Collect Evidence for a Bug Report

Include the GlassBox and Python versions, runtime profile and adapter-set name,
route or service method, structured error or denial reasons, first failed stage,
whether an intent receipt exists, focused test output, and a redacted minimal
reproduction.

Use [CONTRIBUTING.md](../../CONTRIBUTING.md). Do not open a public issue for a
suspected vulnerability.

## Related Documentation

- [Architecture](../ARCHITECTURE.md)
- [Current HTTP API](../API/v2_endpoint_reference.md)
- [Operations](../OPERATIONS/README.md)
- [Security hardening](../SECURITY/hardening.md)
- [Verified claims](../CLAIMS.md)