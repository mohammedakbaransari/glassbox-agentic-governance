# Port Contracts

`glassbox.ports` defines runtime-checkable `Protocol` interfaces for every
external capability used by the current application layer. Ports express what
governance requires; adapters decide how a platform provides it.

## Runtime Ports

The composition root requires these fourteen ports for every runtime:

| Port | Responsibility | Reference adapter |
|---|---|---|
| `Clock` | Supply the only application notion of current time | memory |
| `IdentityVerifier` | Convert untrusted credentials into a verified principal | memory, identity/OIDC |
| `ActionCatalogue` | Resolve server-governed action definitions | memory |
| `AttestationProvider` | Resolve required system-of-record attestations | memory |
| `ToolRegistry` | Validate tool registration, digest, and quarantine state | memory |
| `MandateStore` | Resolve principal authority | memory |
| `KillSwitch` | Evaluate emergency stop state | memory |
| `PolicyDecisionPoint` | Evaluate active policy against an authorization request | memory |
| `RiskEngine` | Produce deterministic risk scores and model provenance | memory |
| `LimitStore` | Apply atomic scoped velocity limits | memory, Redis |
| `BaselineStore` | Evaluate and update behavioral baselines | memory, Redis |
| `MacSigner` | Sign and verify canonical evidence bytes | memory, KMS |
| `EvidenceStore` | Append intent/outcome records and return durable receipts | memory, PostgreSQL |
| `Dispatcher` | Execute an allowed effect at most once using a receipt | memory, PostgreSQL ledger |

Lifecycle services additionally use `EvidenceRetentionStore` and
`WormAnchorStore` for seal-before-retention and immutable-root anchoring.

## Implementing an Adapter

1. Read the port method contracts and domain types.
2. Implement the protocol under `glassbox/adapters/outbound/<technology>`.
3. Translate vendor errors into the structured domain/application error
   expected by the calling service.
4. Preserve atomicity and durability semantics; matching method names is not
   sufficient.
5. Add the adapter factory to a complete `AdapterSet` in the process entry point.
6. Run conformance, failure, concurrency, and layering tests.

All protocols are `@runtime_checkable`. `build_runtime` validates every supplied
component and reports all missing or non-conforming members together.

## Contract Semantics

Port implementations must preserve the behavioral contract documented in the
protocol. Examples include:

- `EvidenceStore.append_intent` returns only after the intent is durable;
- `LimitStore` performs atomic check-and-increment across replicas;
- `Dispatcher` treats idempotency as shared durable state, not a process cache;
- `MacSigner` does not expose production key material to the application;
- identity verification derives tenant and subject from verified credentials;
- unavailable safety dependencies fail closed according to the runtime profile.

## Verification

```bash
python -m pytest tests/test_ports.py tests/test_memory_adapters.py -q
python -m pytest tests/test_layering.py -q
```

Technology-specific integration tests are environment-gated. See
[testing guidance](../../docs/DEVELOPMENT/testing.md).

## Related Documentation

- [Architecture](../../docs/ARCHITECTURE.md)
- [Outbound adapters](../adapters/outbound/README.md)
- [Application composition](../app/README.md)