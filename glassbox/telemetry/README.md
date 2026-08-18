# Telemetry Exporter (Legacy Runtime)

`otel_exporter.py` consumes v1 event-bus and `GovernancePipeline` telemetry. It
is retained for compatibility and has different metrics and wiring from the
current application telemetry.

New integrations use the dependency-free protocols in
`glassbox.app.telemetry` and install the real backend through
`glassbox.adapters.outbound.otel.configure` at the process entry point.

Telemetry does not carry governance assurance by itself. Bound cardinality,
redact sensitive data, protect transport/storage, and test exporter failure
behavior. See [operations](../../docs/OPERATIONS/README.md).