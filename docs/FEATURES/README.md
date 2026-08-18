# Capability Index

Feature documentation describes current runtime behavior and its operational
boundary.

## Current Capabilities

- [Enterprise capability and maturity model](enterprise.md)
- [Distributed limits and behavioral baselines](velocity_breaker.md)
- [Architecture and decision lifecycle](../ARCHITECTURE.md)
- [Evidence-backed claims](../CLAIMS.md)

## Reading Maturity

| Label | Meaning |
|---|---|
| Verified | Code and focused tests exist; see `CLAIMS.md` |
| Adapter available | Integration code exists; deployment assurance remains environment-specific |
| Operator-owned | Required external platform/process capability |
| Legacy | Retained synchronous implementation, not the default for new work |

No maturity label is a compliance certification or production-readiness
attestation for a particular deployment.

## Paths

- Architects: [architecture](../ARCHITECTURE.md) -> [enterprise](enterprise.md)
- Operators: [deployment](../DEPLOYMENT/README.md) -> [distributed controls](velocity_breaker.md)
- Developers: [extension guide](../DEVELOPMENT/implementation_guide.md) -> [testing](../DEVELOPMENT/testing.md)