# Deployment Workflow

Use this workflow to turn an application integration into an environment that
can support the `prod` assurance profile.

## 1. Establish the Enforcement Boundary

Inventory every agent, workflow, tool gateway, batch process, and administrative
path capable of causing the governed side effect. Route all of them through one
of the current application methods or the v2 HTTP adapter. Remove or constrain
direct credentials to target systems.

## 2. Build and Verify the Package

```bash
python -m venv .venv
python -m pip install --upgrade pip
pip install -e .[dev]
python -m pytest tests -q
```

For release artifacts, build and install the wheel in an isolated environment:

```bash
python -m build --sdist --wheel --outdir dist
```

CI verifies wheel and editable installation on Python 3.13.

## 3. Provision External Capabilities

Provision and govern:

- PostgreSQL for append-only evidence, tenant isolation, and dispatch ledger;
- Redis for distributed limits and baselines;
- OIDC/JWKS or mTLS identity trust;
- managed KMS key for evidence MAC operations;
- signed policy and action-catalogue storage;
- immutable/WORM anchor storage;
- effect-system credentials and durable idempotency;
- telemetry collector and protected log storage.

Use `docker compose up -d` only for local integration work.

## 4. Construct Production Configuration

Prefer a secret-aware configuration provider that produces a nested mapping or
environment variables consumed by `GlassBoxConfig.from_env()`. Do not log raw
DSNs, tokens, certificates, or key material.

```python
from glassbox.app.config import GlassBoxConfig

config = GlassBoxConfig.from_env()
```

Unknown `GLASSBOX_*` variables and malformed booleans fail validation. See the
[configuration reference](deployment_reference.md).

## 5. Assemble the Adapter Set

The process entry point selects factories for all fourteen required ports and
passes them to `build_runtime`. Keep this wiring outside `glassbox.app`.

```python
from glassbox.app.composition import build_runtime

runtime = build_runtime(config, production_adapter_set)
```

`production_adapter_set` is deployment-specific. Mark it `dev_only=False` only
after its storage, key custody, distribution, and failure properties have been
validated. Protocol shape alone does not establish production assurance.

## 6. Create and Serve the HTTP App

```python
from glassbox.adapters.inbound.http.app import create_app

app = create_app(runtime)
```

Serve the factory result with an organization-approved WSGI server. Configure
worker count only after validating database/Redis pools and dispatcher capacity.
At ingress enforce TLS, body limits, timeouts, connection limits, and network
policy. The repository does not ship a default server command.

## 7. Validate Before Traffic

1. Check `/healthz` for the expected profile and component types.
2. Run a governed no-effect or sandbox action for each decision effect.
3. Prove identity/tenant mismatch is denied and evidenced.
4. Interrupt evidence/KMS/Redis dependencies and prove fail-closed behavior.
5. Reuse an idempotency key and prove the effect is not repeated.
6. Verify an evidence segment and its signer key identity.
7. Exercise replay and prove no target-system call occurs.

## 8. Roll Out Gradually

Use a limited tenant/action cohort, monitor denial reasons and dependency
failures, then expand. Do not run a permissive shadow path that can dispatch.
Shadow evaluation must remain side-effect-free.

## 9. Operate and Recover

- Back up and restore PostgreSQL under measured recovery objectives.
- Preserve policy, catalogue, mandate, tool-registry, and key-version history.
- Treat Redis loss according to the fail-closed runbook.
- Rotate signing keys without losing historical verification metadata.
- Roll back application code and governed bundles independently.
- Never repair evidence by updating or deleting historical rows.

See [operations](../OPERATIONS/README.md) and
[security hardening](../SECURITY/hardening.md).