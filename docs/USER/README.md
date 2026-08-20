# User Documentation

Use these guides for the `DecisionService` runtime — the only implementation
in this repository. An earlier synchronous `GovernancePipeline` existed
during development; it has been physically deleted, not merely deprecated.

## Start by Role

| Role | Primary path |
|---|---|
| Application developer | [Quick start](quick_start.md) -> [v2 API](../API/v2_endpoint_reference.md) -> [extension guide](../DEVELOPMENT/implementation_guide.md) |
| AI/system architect | [Architecture](../ARCHITECTURE.md) -> [enterprise capabilities](../FEATURES/enterprise.md) -> [claims](../CLAIMS.md) |
| Platform/SRE | [Deployment](../DEPLOYMENT/README.md) -> [operations](../OPERATIONS/README.md) -> [troubleshooting](troubleshooting.md) |
| Security engineer | [Architecture](../ARCHITECTURE.md#security-boundaries) -> [security](../SECURITY/README.md) -> [hardening](../SECURITY/hardening.md) |
| Risk/compliance | [Use cases](use_cases.md) -> [compliance model](../COMPLIANCE/README.md) -> [control mappings](../COMPLIANCE/requirements.md) |

## Common Goals

| Goal | Document |
|---|---|
| Run a complete development decision | [quick_start.md](quick_start.md) |
| Understand allow, deny, and approval behavior | [../ARCHITECTURE.md](../ARCHITECTURE.md#decision-outcomes) |
| Govern tools or autonomous workflows | [use_cases.md](use_cases.md) |
| Diagnose a denial or dependency failure | [troubleshooting.md](troubleshooting.md) |
| Implement a port or adapter | [../DEVELOPMENT/implementation_guide.md](../DEVELOPMENT/implementation_guide.md) |
| Verify a product claim | [../CLAIMS.md](../CLAIMS.md) |

## Core Vocabulary

- A **principal** is a verified agent instance scoped to a tenant.
- A **governed action** has server-owned consequence, exposure, schema, and
  attestation requirements.
- A **mandate** is the principal's bounded, time-valid ceiling of authority.
- A **decision** is allow, deny, or require approval, with machine-readable
  reasons and stage outcomes.
- An **intent receipt** proves evidence was durable before dispatch.
- **Replay** re-evaluates historical inputs and cannot dispatch.

See the [glossary](../GLOSSARY.md) for the complete vocabulary.

## Reading Product Claims

Documentation distinguishes:

- **Implemented and verified:** backed by cited code and tests in `CLAIMS.md`.
- **Adapter available:** implementation exists, but production assurance depends
  on deployment configuration and external services.
- **Operator-owned:** required outside this repository, such as TLS ingress,
  identity governance, disaster recovery, and approval completion.

Framework mappings and examples are not certifications or legal advice.