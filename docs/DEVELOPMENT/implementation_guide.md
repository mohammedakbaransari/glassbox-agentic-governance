# Extension Guide

Extend GlassBox at the narrowest owning boundary. Domain semantics belong in
the domain; external capabilities become ports; vendor code becomes an outbound
adapter; request translation becomes an inbound adapter; multi-port sequencing
belongs in the application layer.

## Add a Governed Action

An `ActionDefinition` fixes server-governed consequence, exposure derivation,
parameter schema, required attestations, and untrusted text fields.

```python
from glassbox.domain.action import BlastRadius, ConsequenceClass
from glassbox.domain.catalogue import (
    ActionDefinition,
    ExposureRule,
    ParameterField,
    ParameterType,
)

definition = ActionDefinition(
    action="procurement.create_purchase_order",
    consequence=ConsequenceClass.COMPENSABLE,
    exposure_rule=ExposureRule(
        blast_radius=BlastRadius.SINGLE,
        monetary_field="amount",
    ),
    parameter_schema=(
        ParameterField("amount", ParameterType.NUMBER, required=True),
        ParameterField("supplier_id", ParameterType.STRING, required=True),
        ParameterField("justification", ParameterType.STRING, max_length=4000),
    ),
    required_attestations=("supplier_active",),
    untrusted_text_fields=frozenset({"justification"}),
)
```

Add the definition to a versioned `ActionCatalogueBundle`, supply the named
attestations from an `AttestationProvider`, grant a bounded `Mandate`, configure
policy and limits, and register a dispatcher handler. Test both allow and denial
paths, including unknown parameters and unavailable attestations.

## Add a Domain Rule

Use `glassbox/domain` when the rule:

- is deterministic for explicit inputs;
- requires no database, network, environment, wall clock, or framework;
- belongs to the stable language of governance.

Validate values at construction, prefer immutable dataclasses and enums, and
provide canonical evidence serialization when the value is persisted.

## Add a Port

Create a small `@runtime_checkable Protocol` under `glassbox/ports` using domain
types. Document behavioral semantics, especially atomicity, durability,
idempotency, tenant isolation, and failure behavior.

Then:

1. add the port to `REQUIRED_COMPONENTS` only if every runtime needs it;
2. update `AdapterSet` factories and `GovernanceRuntime` through the composition
   root's single source of truth;
3. implement the reference memory adapter;
4. add shared conformance tests;
5. implement durable adapters;
6. update the ports and outbound-adapter READMEs.

## Add an Outbound Adapter

Place vendor-specific code under `glassbox/adapters/outbound/<technology>`.

- Keep SDK and driver types inside the adapter.
- Parameterize data values and enforce tenant context.
- Use server-side atomic operations for counters and ledgers.
- Translate dependency failures into structured errors expected by the service.
- Never return a permissive result because a safety dependency is unavailable.
- Add environment-gated integration tests against the real service.

An object that passes `isinstance(component, Protocol)` may still violate
behavioral semantics; conformance tests are mandatory.

## Add an Inbound Adapter

Inbound adapters extract untrusted transport values, call one application
method, and serialize outcomes. They must not:

- construct concrete outbound dependencies;
- accept caller-selected consequence, exposure, tenant, or policy verdicts;
- duplicate application control flow;
- turn replay into dispatch.

Use `glassbox/adapters/inbound/http/app.py` as the reference pattern.

## Add Policy Behavior

Policy refines authority within a mandate; it cannot expand the mandate.
Production policy bundles must be versioned, attributable, active, and
signature-verified according to configuration. Record bundle identity in
evidence and test unavailable/invalid bundles as fail-closed paths.

## Add Observability

The application layer exposes dependency-free telemetry protocols and no-op
defaults. Install concrete OpenTelemetry providers from an outbound adapter at
the process entry point. Do not import OpenTelemetry into `glassbox.app`.

Use bounded-cardinality attributes. Never emit credentials, raw tokens, client
certificates, signing material, or unrestricted action parameters.

## Review Checklist

- Owning layer is correct and dependency direction remains inward.
- Input trust and tenant derivation are explicit.
- Failure mode is fail closed where safety depends on the component.
- Dispatch still requires a durable intent receipt.
- Shared state is atomic across processes and replicas.
- Unit, conformance, adversarial, and integration coverage match the risk.
- Public docs and `CLAIMS.md` reflect the change.

## Verification

```bash
lint-imports
ruff check glassbox tests
mypy glassbox --ignore-missing-imports --no-error-summary
python -m pytest tests/test_layering.py -q
python -m pytest tests -q
```

See [testing.md](testing.md) for service-specific gates and environment variables.