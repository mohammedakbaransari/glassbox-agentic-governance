# Domain Model

`glassbox.domain` defines the trusted vocabulary and invariants of runtime
decision governance. It is pure Python: no I/O, framework imports, environment
lookups, clocks, randomness, or third-party dependencies.

## Domain Areas

| Area | Principal types and responsibilities |
|---|---|
| Actions | `ProposedAction`, `ResourceRef`, `ConsequenceClass`, `Exposure`, `BlastRadius` |
| Catalogue | `ActionDefinition` and server-governed action metadata |
| Identity | `RawCredential`, `VerifiedPrincipal`, credential and delegation semantics |
| Decisions | authorization requests, effects, denial reasons, stage and execution outcomes, `Approval`/`ApprovalState` |
| Evidence | immutable intent/outcome records, receipts, model provenance, chain metadata |
| Risk | validated risk inputs and bounded risk scores; `RiskConfig.enforce_threshold`/`deny_level` gating |
| Limits | scopes, keys, windows, and atomic limit verdicts |
| Mandates | principal authority over actions and resources, including resource-scoped `ActionResourceGrant` |
| Tools | registered tool definitions, digests, and quarantine state |
| Policy bundles | signed bundle identity, lifecycle, and activation semantics |
| Tenancy | `Tenant`/`TenantStatus` \u2014 a first-class entity for the onboarded organization boundary |
| Audit events | `AuditEvent` \u2014 platform/administrative activity, distinct from per-decision evidence |
| Integrity | canonical serialization, Merkle proofs, and prompt-injection signals (scanned on inbound fields **and** outbound tool-dispatch results) |

## Core Invariants

- Tenant identity is mandatory and must be consistent across principal,
  resource, limits, and evidence.
- Callers name an action but do not define its consequence or exposure.
- Domain values validate at construction and are immutable where practical.
- Decision effects are explicit: allow, deny, or require approval.
- Denials carry machine-readable reasons and stage outcomes.
- Evidence serialization is deterministic; signatures and hashes are computed
  over canonical values.
- Replay is represented as an execution status, never as a dispatch request.

The executable mapping from guarantees to tests is maintained in
[docs/CLAIMS.md](../../docs/CLAIMS.md).

## Adding Domain Behavior

1. Put a rule here only when it is independent of transport and infrastructure.
2. Validate untrusted values at the type boundary.
3. Prefer explicit enums and immutable value objects to free-form dictionaries.
4. Keep time and external lookups behind ports.
5. Add deterministic unit tests and update the claims register when the change
   introduces or alters a guarantee.

## Dependency Rule

Domain modules may import the Python standard library and other domain modules.
They must not import `glassbox.app`, `glassbox.adapters`, legacy packages, or
third-party libraries. This is enforced by import-linter and AST tests.

## Verification

```bash
python -m pytest tests/test_domain.py tests/test_risk_determinism.py -q
python -m pytest tests/test_layering.py -q
```

## Related Documentation

- [Architecture](../../docs/ARCHITECTURE.md)
- [Application layer](../app/README.md)
- [Ports](../ports/README.md)
- [Glossary](../../docs/GLOSSARY.md)