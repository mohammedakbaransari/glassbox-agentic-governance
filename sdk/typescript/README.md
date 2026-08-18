# TypeScript SDK

The TypeScript client in this directory targets the legacy v1
`GovernancePipeline` HTTP API (`/decisions`, `/stats`, `/policies`, and related
routes). It does not currently implement the v2 `DecisionService` contract.

## Status

- Compatibility asset for existing v1 API deployments
- Zero runtime dependencies; requires native `fetch` (Node.js 18+ or a modern browser)
- Source-only package in this repository; no committed TypeScript compiler configuration
- Package publication and the registry name are not asserted by this repository

Do not install the package name from `package.json` unless your organization has
verified that registry artifact and its provenance.

## Usage from Source

The client accepts a base URL and optional headers. Configure the authentication
required by the v1 deployment explicitly.

```typescript
import { GlassBoxClient, DecisionType } from "./index";

const client = new GlassBoxClient({
  baseUrl: "https://glassbox.example.com",
  headers: { Authorization: `Bearer ${process.env.GLASSBOX_TOKEN}` },
});

const result = await client.governSafe({
  agent_id: "procurement-bot",
  decision_type: DecisionType.PROCUREMENT,
  payload: { amount: 75000, supplier_id: "SUP-001" },
});
```

## API Compatibility

The v1 and v2 request models are not interchangeable. v2 requires governed
action names, resource identity, idempotency keys, and verified principal
semantics. See [the current HTTP reference](../../docs/API/v2_endpoint_reference.md).

A future v2 SDK should use separate types and methods rather than silently
changing the behavior of this client.

## Repository Metadata Note

The `repository.url` in `package.json` and the package version should be checked
before any publication workflow. They are retained metadata, not a release
attestation.