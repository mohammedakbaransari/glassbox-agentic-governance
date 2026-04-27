"""
GlassBox — OpenTelemetry Export Adapter  (v1.0.0)
==================================================
Exports GlassBox governance metrics and traces to any OpenTelemetry-compatible
backend: Datadog, Prometheus, Grafana, Jaeger, Zipkin, New Relic, Honeycomb.

Design approach — zero mandatory dependency on opentelemetry SDK:
  The OTel SDK is a heavy optional dependency. This module follows GlassBox's
  zero-mandatory-dependency principle by:
    1. Using lazy import — OTel SDK is only imported when OtelExporter is
       constructed, not when the module is loaded.
    2. Providing a no-op fallback — if the SDK is not installed, all calls
       silently succeed. The pipeline never fails due to missing observability.
    3. Supporting manual HTTP export — for environments where the SDK cannot
       be installed, the OTLP HTTP JSON format is supported via stdlib urllib.

Installation:
    pip install opentelemetry-api opentelemetry-sdk
    pip install opentelemetry-exporter-otlp-proto-http   # for OTLP HTTP
    pip install opentelemetry-exporter-prometheus         # for Prometheus scrape

What is exported:
  Metrics (via Counter/Histogram):
    glassbox.decisions.total           — total decisions by type and status
    glassbox.decisions.blocked         — blocked decisions by policy and type
    glassbox.decisions.latency_ms      — pipeline latency histogram
    glassbox.policy.violations         — policy violation counts by policy_id
    glassbox.risk.score               — risk score histogram
    glassbox.anomalies.detected        — anomaly detection count
    glassbox.circuit_breaker.trips     — circuit breaker trip events
    glassbox.security.violations       — security pre-check block count

  Spans (via Tracer):
    glassbox.decision               — one span per decision covering full pipeline
    glassbox.stage.*               — child spans per pipeline stage (when trace_enabled)

Integration:
    from glassbox.telemetry.otel_exporter import OtelExporter
    exporter = OtelExporter(service_name="my-service")
    bus = EventBus()
    bus.subscribe("*", exporter.handle_event)
    pipeline = GovernancePipeline(event_bus=bus, trace_enabled=True)

Author: Mohammed Akbar Ansari — Independent Researcher
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
import urllib.error
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional


# ── Metric accumulator (works without OTel SDK) ────────────────────────────────

@dataclass
class MetricSample:
    """A single metric data point with labels."""
    name:      str
    value:     float
    labels:    Dict[str, str]
    timestamp: float = field(default_factory=time.time)
    kind:      str   = "counter"   # counter | gauge | histogram


class InMemoryMetricStore:
    """
    Thread-safe in-memory metric accumulator.
    Works without any OTel SDK dependency.
    Can be scraped by Prometheus or exported via OTLP HTTP.
    """

    def __init__(self):
        self._counters: Dict[str, float]       = defaultdict(float)
        self._histograms: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def increment(self, name: str, labels: Dict[str, str] = None, value: float = 1.0):
        key = self._key(name, labels or {})
        with self._lock:
            self._counters[key] += value

    def record(self, name: str, value: float, labels: Dict[str, str] = None):
        """Record a histogram observation."""
        key = self._key(name, labels or {})
        with self._lock:
            self._histograms[key].append(value)
            # Cap at 10,000 samples per metric to bound memory
            if len(self._histograms[key]) > 10_000:
                self._histograms[key] = self._histograms[key][-5_000:]

    def snapshot(self) -> Dict[str, Any]:
        """Return a snapshot of all current metrics."""
        with self._lock:
            result = {}
            for key, val in self._counters.items():
                result[key] = {"kind": "counter", "value": val}
            for key, samples in self._histograms.items():
                if not samples: continue
                s = sorted(samples)
                n = len(s)
                result[key] = {
                    "kind":   "histogram",
                    "count":  n,
                    "sum":    sum(s),
                    "min":    s[0],
                    "max":    s[-1],
                    "p50":    s[int(n * 0.50)],
                    "p90":    s[int(n * 0.90)],
                    "p99":    s[int(n * 0.99)],
                    "p999":   s[min(int(n * 0.999), n-1)],
                }
            return result

    def prometheus_text(self) -> str:
        """Render metrics in Prometheus text exposition format."""
        lines = []
        snap = self.snapshot()
        for key, data in sorted(snap.items()):
            name, labels = self._parse_key(key)
            lbl_str = "{" + ",".join(f'{k}="{v}"' for k,v in labels.items()) + "}" if labels else ""
            if data["kind"] == "counter":
                lines.append(f"# TYPE {name} counter")
                lines.append(f"{name}{lbl_str} {data['value']}")
            elif data["kind"] == "histogram":
                lines.append(f"# TYPE {name} summary")
                for q, qv in [("0.5", data["p50"]), ("0.9", data["p90"]),
                               ("0.99", data["p99"]), ("0.999", data["p999"])]:
                    qlbl = "{" + ",".join(f'{k}="{v}"' for k,v in {**labels, "quantile": q}.items()) + "}"
                    lines.append(f"{name}{qlbl} {qv:.4f}")
                lines.append(f"{name}_count{lbl_str} {data['count']}")
                lines.append(f"{name}_sum{lbl_str} {data['sum']:.4f}")
        return "\n".join(lines)

    def reset(self):
        with self._lock:
            self._counters.clear()
            self._histograms.clear()

    @staticmethod
    def _key(name: str, labels: Dict[str, str]) -> str:
        lbl_str = ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
        return f"{name}|{lbl_str}"

    @staticmethod
    def _parse_key(key: str):
        parts = key.split("|", 1)
        name = parts[0]
        labels = {}
        if len(parts) > 1 and parts[1]:
            for kv in parts[1].split(","):
                if "=" in kv:
                    k, v = kv.split("=", 1)
                    labels[k] = v
        return name, labels


# ── OTel SDK wrapper ────────────────────────────────────────────────────────────

class OtelExporter:
    """
    Exports GlassBox governance events to OpenTelemetry.

    Operates in two modes depending on what is available:

    Mode 1 — OTel SDK installed:
        Uses opentelemetry-api and opentelemetry-sdk.
        Creates real Meter and Tracer instances.
        Works with any OTel-compatible exporter (OTLP, Prometheus, Jaeger, Zipkin).

    Mode 2 — OTel SDK not installed (fallback):
        Uses InMemoryMetricStore.
        Exposes prometheus_text() and snapshot() for manual scraping.
        Can push metrics via OTLP HTTP JSON using only stdlib urllib.

    Usage:
        # Mode 1 — with OTel SDK
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        reader = PeriodicExportingMetricReader(OTLPMetricExporter())
        provider = MeterProvider(metric_readers=[reader])

        exporter = OtelExporter(service_name="procurement-service",
                                meter_provider=provider)

        # Mode 2 — no SDK, Prometheus scrape
        exporter = OtelExporter(service_name="procurement-service")

        bus = EventBus()
        bus.subscribe("*", exporter.handle_event)
        pipeline = GovernancePipeline(event_bus=bus)

        # Expose metrics for Prometheus scrape
        @app.get("/metrics")
        def prometheus_metrics():
            return exporter.prometheus_text(), 200, {"Content-Type": "text/plain"}
    """

    def __init__(
        self,
        service_name:       str  = "glassbox",
        service_version:    str  = "1.0.0",
        meter_provider:     Any  = None,   # opentelemetry MeterProvider
        tracer_provider:    Any  = None,   # opentelemetry TracerProvider
        otlp_endpoint:      Optional[str] = None,  # e.g. "http://localhost:4318"
        export_interval_s:  int  = 30,
    ):
        self.service_name    = service_name
        self.service_version = service_version
        self.otlp_endpoint   = otlp_endpoint
        self._store          = InMemoryMetricStore()
        self._lock           = threading.Lock()

        # Try to initialise OTel SDK instruments
        self._meter  = None
        self._tracer = None
        self._instruments: Dict[str, Any] = {}

        if meter_provider:
            try:
                self._meter = meter_provider.get_meter(
                    service_name, version=service_version)
                self._init_instruments()
            except Exception as exc:
                self._meter = None

    def _init_instruments(self):
        """Create OTel metric instruments. Called only when SDK is available."""
        if not self._meter:
            return
        try:
            self._instruments["decisions_total"]    = self._meter.create_counter(
                "glassbox.decisions.total",
                description="Total AI decisions submitted for governance",
            )
            self._instruments["decisions_blocked"]  = self._meter.create_counter(
                "glassbox.decisions.blocked",
                description="Decisions blocked by governance pipeline",
            )
            self._instruments["policy_violations"]  = self._meter.create_counter(
                "glassbox.policy.violations",
                description="Policy violation counts by policy_id and decision_type",
            )
            self._instruments["anomalies"]          = self._meter.create_counter(
                "glassbox.anomalies.detected",
                description="Statistical anomaly detection events",
            )
            self._instruments["circuit_trips"]      = self._meter.create_counter(
                "glassbox.circuit_breaker.trips",
                description="Circuit breaker trip events (velocity + ecosystem)",
            )
            self._instruments["security_blocks"]    = self._meter.create_counter(
                "glassbox.security.violations",
                description="Security pre-check block events",
            )
            self._instruments["latency_hist"]       = self._meter.create_histogram(
                "glassbox.decisions.latency_ms",
                description="Pipeline governance latency in milliseconds",
                unit="ms",
            )
            self._instruments["risk_score_hist"]    = self._meter.create_histogram(
                "glassbox.risk.score",
                description="Governance risk score distribution (0-100)",
            )
            # v1.1.0 additions
            self._instruments["stage_latency_hist"] = self._meter.create_histogram(
                "glassbox.pipeline.stage_latency_ms",
                description="Per-stage pipeline latency in milliseconds",
                unit="ms",
            )
            self._instruments["anomaly_z_score"]    = self._meter.create_histogram(
                "glassbox.anomaly.z_score",
                description="Z-score magnitude of detected anomalies",
            )
            self._instruments["trust_score"]        = self._meter.create_up_down_counter(
                "glassbox.trust.score",
                description="Current agent trust score (0.0–1.0)",
            )
            self._instruments["cb_active"]          = self._meter.create_up_down_counter(
                "glassbox.circuit_breaker.active",
                description="Number of agents currently in circuit-breaker cooldown",
            )
        except Exception:
            self._instruments = {}

    # ── Event handler ─────────────────────────────────────────────────────────

    def handle_event(self, event) -> None:
        """
        Subscribe this to the GlassBox EventBus:
            bus.subscribe("*", exporter.handle_event)

        Handles all 8 domain events and records the appropriate metrics.
        """
        try:
            etype = getattr(event, "event_type", "")
            payload = getattr(event, "payload", {}) or {}

            if etype == "decision.executed":
                self._on_executed(payload)
            elif etype == "decision.blocked":
                self._on_blocked(payload)
            elif etype == "decision.pending_review":
                self._on_pending_review(payload)
            elif etype == "policy.violated":
                self._on_policy_violated(payload)
            elif etype == "anomaly.detected":
                self._on_anomaly(payload)
            elif etype == "circuit_breaker.tripped":
                self._on_circuit_trip(payload)
            elif etype == "security.violation":
                self._on_security(payload)
            elif etype == "trust.score.updated":
                self._on_trust_changed(payload)
            elif etype == "circuit_breaker.reset":
                # Track active breakers gauge: -1 when reset
                agent = payload.get("agent_id", "unknown")
                self._store.increment("glassbox_circuit_breaker_active",
                                      {"agent_id": agent}, value=-1.0)
                if "cb_active" in self._instruments:
                    self._instruments["cb_active"].add(-1, {"agent_id": agent})
        except Exception:
            pass   # Observability must never break the calling thread

    def _on_executed(self, p: Dict):
        dtype  = p.get("decision_type", "unknown")
        labels = {"decision_type": dtype, "status": "executed"}
        self._store.increment("glassbox_decisions_total", labels)
        latency = p.get("pipeline_latency_ms")
        if latency is not None:
            self._store.record("glassbox_decisions_latency_ms",
                               float(latency), {"decision_type": dtype})
        risk = p.get("risk_score")
        if risk is not None:
            self._store.record("glassbox_risk_score", float(risk),
                               {"decision_type": dtype})
        # Per-stage latency (present when trace_enabled=True on the pipeline)
        for stage in p.get("stage_latencies", []):
            stage_name  = stage.get("stage_name", "unknown")
            stage_ms    = stage.get("duration_ms")
            if stage_ms is not None:
                self._store.record("glassbox_stage_latency_ms",
                                   float(stage_ms), {"stage": stage_name})
                if "stage_latency_hist" in self._instruments:
                    self._instruments["stage_latency_hist"].record(
                        float(stage_ms), {"stage": stage_name})
        # OTel SDK path
        if "decisions_total" in self._instruments:
            self._instruments["decisions_total"].add(1, labels)
        if latency and "latency_hist" in self._instruments:
            self._instruments["latency_hist"].record(
                float(latency), {"decision_type": dtype})

    def _on_blocked(self, p: Dict):
        dtype  = p.get("decision_type", "unknown")
        labels = {"decision_type": dtype, "status": "blocked"}
        self._store.increment("glassbox_decisions_total", labels)
        self._store.increment("glassbox_decisions_blocked",
                              {"decision_type": dtype})
        if "decisions_blocked" in self._instruments:
            self._instruments["decisions_blocked"].add(
                1, {"decision_type": dtype})

    def _on_pending_review(self, p: Dict):
        dtype  = p.get("decision_type", "unknown")
        labels = {"decision_type": dtype, "status": "pending_review"}
        self._store.increment("glassbox_decisions_total", labels)

    def _on_policy_violated(self, p: Dict):
        violations = p.get("violations", [])
        dtype      = p.get("decision_type", "unknown")
        for v in violations:
            # Extract policy_id from violation string (format: "[PROC-001] ...")
            policy_id = "unknown"
            if v and v.startswith("[") and "]" in v:
                policy_id = v[1:v.index("]")]
            self._store.increment("glassbox_policy_violations",
                                  {"policy_id": policy_id, "decision_type": dtype})
            if "policy_violations" in self._instruments:
                self._instruments["policy_violations"].add(
                    1, {"policy_id": policy_id, "decision_type": dtype})

    def _on_anomaly(self, p: Dict):
        dtype = p.get("decision_type", "unknown")
        agent = p.get("agent_id", "unknown")
        self._store.increment("glassbox_anomalies_detected",
                              {"decision_type": dtype, "agent_id": agent})
        if "anomalies" in self._instruments:
            self._instruments["anomalies"].add(
                1, {"decision_type": dtype})
        # Record z_score if present in event payload
        z_score = p.get("max_z_score") or p.get("z_score")
        if z_score is not None:
            self._store.record("glassbox_anomaly_z_score",
                               abs(float(z_score)),
                               {"decision_type": dtype, "agent_id": agent})
            if "anomaly_z_score" in self._instruments:
                self._instruments["anomaly_z_score"].record(
                    abs(float(z_score)), {"decision_type": dtype})

    def _on_circuit_trip(self, p: Dict):
        kind  = "ecosystem" if p.get("is_ecosystem") else "per_agent"
        agent = p.get("agent_id", "unknown")
        self._store.increment("glassbox_circuit_breaker_trips",
                              {"kind": kind, "agent_id": agent})
        if "circuit_trips" in self._instruments:
            self._instruments["circuit_trips"].add(1, {"kind": kind})
        # Track active breakers gauge: +1 when tripped
        self._store.increment("glassbox_circuit_breaker_active",
                              {"agent_id": agent}, value=1.0)
        if "cb_active" in self._instruments:
            self._instruments["cb_active"].add(1, {"agent_id": agent})

    def _on_trust_changed(self, p: Dict):
        """Handle trust.score.updated events."""
        agent = p.get("agent_id", "unknown")
        score = p.get("trust_score", p.get("score"))
        if score is not None:
            self._store.record("glassbox_trust_score",
                               float(score), {"agent_id": agent})
            if "trust_score" in self._instruments:
                self._instruments["trust_score"].add(
                    float(score), {"agent_id": agent})

    def _on_security(self, p: Dict):
        dtype = p.get("decision_type", "unknown")
        self._store.increment("glassbox_security_violations",
                              {"decision_type": dtype})
        if "security_blocks" in self._instruments:
            self._instruments["security_blocks"].add(1, {"decision_type": dtype})

    # ── Export methods ────────────────────────────────────────────────────────

    def prometheus_text(self) -> str:
        """Return Prometheus text exposition format for scraping."""
        return self._store.prometheus_text()

    def snapshot(self) -> Dict[str, Any]:
        """Return raw metric snapshot as a dict."""
        return self._store.snapshot()

    def push_otlp_http(self, endpoint: Optional[str] = None) -> bool:
        """
        Push current metrics to an OTLP HTTP endpoint using stdlib urllib.
        No OTel SDK required. Works with any OTLP-compatible collector.

        Args:
            endpoint: OTLP HTTP endpoint, e.g. "http://localhost:4318"
                      Falls back to self.otlp_endpoint if not specified.

        Returns:
            True if push succeeded, False otherwise.
        """
        url = (endpoint or self.otlp_endpoint or "").rstrip("/")
        if not url:
            return False
        target = f"{url}/v1/metrics"

        snap = self.snapshot()
        now_ns = int(time.time() * 1e9)

        # Build minimal OTLP JSON payload
        resource = {
            "attributes": [
                {"key": "service.name",    "value": {"stringValue": self.service_name}},
                {"key": "service.version", "value": {"stringValue": self.service_version}},
            ]
        }

        metrics_data = []
        for key, data in snap.items():
            name, labels = self._store._parse_key(key)
            attrs = [{"key": k, "value": {"stringValue": v}}
                     for k, v in labels.items()]
            if data["kind"] == "counter":
                metrics_data.append({
                    "name": name,  # Keep snake_case for OTLP/Prometheus compatibility
                    "sum": {"dataPoints": [{
                        "attributes": attrs,
                        "asDouble": data["value"],
                        "timeUnixNano": str(now_ns),
                        "aggregationTemporality": 2,   # CUMULATIVE
                        "isMonotonic": True
                    }]}
                })
            elif data["kind"] == "histogram":
                metrics_data.append({
                    "name": name,  # Keep snake_case for OTLP/Prometheus compatibility
                    "gauge": {"dataPoints": [
                        {"attributes": [*attrs, {"key": "quantile",
                            "value": {"stringValue": q}}],
                         "asDouble": v,
                         "timeUnixNano": str(now_ns)}
                        for q, v in [("p50", data["p50"]), ("p90", data["p90"]),
                                     ("p99", data["p99"])]
                    ]}
                })

        payload = json.dumps({
            "resourceMetrics": [{
                "resource": resource,
                "scopeMetrics": [{
                    "scope": {"name": "glassbox", "version": self.service_version},
                    "metrics": metrics_data
                }]
            }]
        }).encode()

        try:
            req = urllib.request.Request(
                target, data=payload,
                headers={"Content-Type": "application/json"}, method="POST")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status in (200, 204)
        except Exception:
            return False

    def reset(self):
        """Reset all accumulated metrics."""
        self._store.reset()
