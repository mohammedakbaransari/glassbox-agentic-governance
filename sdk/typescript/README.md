# TypeScript SDK

> **Historical, non-functional against this repository.** The TypeScript
> client in this directory targets the legacy v1 `GovernancePipeline` HTTP
> API (`/decisions`, `/stats`, `/policies`, and related routes). That server
> implementation (`glassbox/api/`) has been physically deleted from this
> repository; this client cannot be pointed at anything this repository now
> ships. It is kept only as a historical reference for organizations that
> still operate an old v1 deployment elsewhere. There is no v2 TypeScript SDK
> yet — integrate with the [v2 HTTP contract](../../docs/API/v2_endpoint_reference.md) directly.

## Status

- Historical reference only; not compatible with the current repository
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