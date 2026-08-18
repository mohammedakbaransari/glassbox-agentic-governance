# Security Model

GlassBox is a governance enforcement component, not a complete security
platform. Its guarantees depend on all effectful paths using the component and
on correctly governed identity, storage, key management, policy, and network
services.

## Security Objectives

The current runtime is designed to:

- derive principal and tenant identity from verified credentials;
- prevent callers from selecting their own consequence, exposure, or policy facts;
- deny unknown or modified tools and actions;
- enforce mandate, kill switch, policy, risk, limits, and baselines before effect;
- persist signed intent evidence before dispatch;
- prevent replay from executing effects;
- preserve tenant context and durable idempotency across replicas;
- fail closed when required safety dependencies are unavailable.

Each product guarantee is mapped to code and tests in [CLAIMS.md](../CLAIMS.md).

## Trust Boundaries

```mermaid
flowchart LR
    Untrusted[Agent input, token,<br/>headers, parameters] --> Inbound[Inbound adapter]
    Inbound --> Service[DecisionService]
    Service --> Trust[Identity, catalogue,<br/>policy, mandate, tool registry]
    Service --> State[Limits, baselines,<br/>evidence, KMS]
    Service -->|receipt-gated| Dispatch[Dispatcher]
    Dispatch --> Target[Target system]
```

Untrusted inputs remain untrusted until the owning control verifies or derives
them. Transport headers may assert tenant or subject values but cannot establish
them.

## Threats and Controls

| Threat | Control |
|---|---|
| Caller lowers action risk | Consequence/exposure derived from governed catalogue |
| Tenant spoofing | Verified principal plus assertion/resource tenant checks |
| Tool rug pull | Registered definition digest and quarantine state |
| Excess authority | Time-bounded mandate and tool grants |
| Burst or distributed bypass | Atomic shared limit store and cooldown |
| Cold-start anomaly bypass | Peer-group baseline prior; no silent skip |
| Evidence loss before effect | Durable intent receipt required by dispatcher |
| Evidence forgery | Canonical keyed MAC chain, signer identity, segment verification |
| Duplicate side effect | Durable idempotency ledger and receipt validation |
| Replay causes effect | Structurally separate replay path with no dispatcher call |
| Dependency outage permits effect | Production safety switches and fail-closed errors |

## Responsibility Boundary

GlassBox does not by itself provide TLS termination, DDoS protection, identity
issuance, secrets management, database encryption, KMS administration, SIEM,
approval completion, host hardening, vulnerability response, backup/restore, or
disaster recovery. These are required platform and organizational controls.

## Security Validation

```bash
bandit -r glassbox --skip B101,B311 --severity-level medium --confidence-level medium
python -m pytest tests/test_adversarial_suite.py -q
python -m pytest tests/test_prompt_injection.py tests/test_oidc_identity.py -q
python -m pytest tests/test_hash_chain_tamper.py tests/test_sealing.py -q
```

Dependency and secret scanning also run in CI.

## Reporting Vulnerabilities

Do not open a public issue. Use the repository owner's private GitHub Security
Advisory workflow and include impact, affected component, reproduction steps,
and suggested remediation where possible. See [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Related Documentation

- [Production hardening](hardening.md)
- [Architecture boundaries](../ARCHITECTURE.md#security-boundaries)
- [Operations](../OPERATIONS/README.md)
- [Verified claims](../CLAIMS.md)