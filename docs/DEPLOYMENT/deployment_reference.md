# Runtime Configuration Reference

`GlassBoxConfig` accepts a nested mapping or strict environment variables. The
profile is mandatory; unknown sections, keys, and `GLASSBOX_*` variables are
rejected.

## Environment Naming

Use `GLASSBOX_<SECTION>_<FIELD>`. The profile is `GLASSBOX_PROFILE`.

```text
GLASSBOX_PROFILE=prod
GLASSBOX_EVIDENCE_DSN=...
GLASSBOX_LIMITS_FAIL_CLOSED=true
GLASSBOX_OBSERVABILITY_LOG_LEVEL=INFO
```

Accepted booleans are `true/false`, `1/0`, `yes/no`, and `on/off`
(case-insensitive). Other values fail validation.

## Settings

| Environment variable | Default | Production rule |
|---|---:|---|
| `GLASSBOX_PROFILE` | none | Required: `dev` or `prod` |
| `GLASSBOX_EVIDENCE_DSN` | empty | Required |
| `GLASSBOX_EVIDENCE_SEGMENT_PREFIX` | `seg` | Valid identifier |
| `GLASSBOX_EVIDENCE_SEAL_INTERVAL_SECONDS` | `3600` | Positive |
| `GLASSBOX_EVIDENCE_FSYNC_REQUIRED` | `true` | Must remain `true` |
| `GLASSBOX_EVIDENCE_WORM_ANCHOR_URI` | empty | Required |
| `GLASSBOX_LIMITS_URL` | empty | Required |
| `GLASSBOX_LIMITS_DEFAULT_WINDOW_SECONDS` | `60` | Positive |
| `GLASSBOX_LIMITS_FAIL_CLOSED` | `true` | Must remain `true` |
| `GLASSBOX_LIMITS_COOLDOWN_SECONDS` | `300` | Non-negative |
| `GLASSBOX_BASELINE_URL` | empty | Required |
| `GLASSBOX_BASELINE_ANOMALY_THRESHOLD` | `3.0` | Positive |
| `GLASSBOX_BASELINE_MIN_SAMPLES` | `30` | Non-negative |
| `GLASSBOX_BASELINE_PEER_GROUP_PRIOR_REQUIRED` | `true` | Must remain `true` |
| `GLASSBOX_SIGNING_KEY_ID` | empty | Required |
| `GLASSBOX_SIGNING_KMS_ENDPOINT` | empty | Deployment-specific |
| `GLASSBOX_SIGNING_ALLOW_LOCAL_KEY` | `false` | Must remain `false` |
| `GLASSBOX_IDENTITY_TRUST_DOMAIN` | empty | Configure for mTLS/SPIFFE use |
| `GLASSBOX_IDENTITY_OIDC_ISSUER` | empty | Configure for OIDC use |
| `GLASSBOX_IDENTITY_ALLOW_SHARED_API_KEY` | `false` | Must remain `false` |
| `GLASSBOX_IDENTITY_REJECT_MISMATCHED_ASSERTIONS` | `true` | Must remain `true` |
| `GLASSBOX_POLICY_BUNDLE_REGISTRY_DSN` | empty | Required |
| `GLASSBOX_POLICY_REQUIRE_SIGNATURE` | `true` | Must remain `true` |
| `GLASSBOX_POLICY_DENY_ON_BUNDLE_UNAVAILABLE` | `true` | Must remain `true` |
| `GLASSBOX_DISPATCH_DEFAULT_TIMEOUT_S` | `30.0` | Positive |
| `GLASSBOX_DISPATCH_MAX_IN_FLIGHT` | `64` | Positive |
| `GLASSBOX_DISPATCH_REQUIRE_EVIDENCE_RECEIPT` | `true` | Must remain `true` |
| `GLASSBOX_OBSERVABILITY_SERVICE_NAME` | `glassbox` | Valid identifier |
| `GLASSBOX_OBSERVABILITY_LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `GLASSBOX_OBSERVABILITY_JSON_LOGS` | `true` | Deployment choice |

`describe()` redacts connection values and reports only whether sensitive
settings are configured.

## Integration-Test Variables

These variables are test harness controls, not `GlassBoxConfig` fields:

| Variable | Purpose |
|---|---|
| `GLASSBOX_POSTGRES_DSN` | Enable PostgreSQL-backed tests |
| `GLASSBOX_REDIS_URL` | Enable Redis-backed tests |
| `GLASSBOX_SPARK_LOCAL_JOB` | Enable optional local Spark execution test |
| `GLASSBOX_RUN_BUILD_TESTS` | Enable isolated build/install tests |

Do not pass them to `GlassBoxConfig.from_env()` in the same environment without
filtering: unknown `GLASSBOX_*` keys are rejected by design.

## HTTP Routes

The current adapter exposes `/healthz`, `/v2/actions/<action_name>`,
`/v2/tools/<tool_name>`, and `/v2/replay`. See the
[v2 API reference](../API/v2_endpoint_reference.md). Routes such as `/health`,
`/decisions`, and `/metrics` belong to the separate legacy API.