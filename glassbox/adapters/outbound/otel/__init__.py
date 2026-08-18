"""Marker package: the real OpenTelemetry backend (GB-034).

Free to import ``opentelemetry`` -- that is exactly what ``adapters.outbound``
is for. ``glassbox.app.telemetry`` defines the shape every caller codes
against and a safe no-op default; this package installs a real implementation
into it.
"""

from __future__ import annotations
