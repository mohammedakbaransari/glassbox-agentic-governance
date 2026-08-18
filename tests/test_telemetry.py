"""Tests for observability (GB-034).

``glassbox.app.telemetry`` must work correctly with zero third-party
dependencies installed (the no-op default) and, separately, must accept a
real backend installed from an outbound adapter without either side needing
to know about the other's concrete type.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import pytest

from glassbox.app.observability import bind_context
from glassbox.app.telemetry import (
    GovernanceMetrics,
    build_governance_metrics,
    get_meter,
    get_tracer,
    set_meter,
    set_tracer,
    traced_operation,
    traced_stage,
)


@pytest.fixture(autouse=True)
def _restore_default_backends():
    """Every test starts and ends with the no-op tracer/meter installed."""
    from glassbox.app import telemetry as telemetry_module

    default_tracer, default_meter = telemetry_module._tracer, telemetry_module._meter
    yield
    telemetry_module._tracer, telemetry_module._meter = default_tracer, default_meter


class _RecordingSpan:
    def __init__(self, name: str, sink: List[Dict[str, Any]]) -> None:
        self.name = name
        self.attributes: Dict[str, Any] = {}
        self.status: Any = None
        self.exceptions: List[BaseException] = []
        self._sink = sink

    def set_attribute(self, key: str, value: Any) -> None:
        self.attributes[key] = value

    def set_status(self, status: Any, description: Any = None) -> None:
        self.status = (status, description)

    def record_exception(self, exception: BaseException) -> None:
        self.exceptions.append(exception)

    def __enter__(self) -> "_RecordingSpan":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self._sink.append({"name": self.name, "attributes": dict(self.attributes)})


class _RecordingTracer:
    def __init__(self) -> None:
        self.spans: List[Dict[str, Any]] = []

    def start_as_current_span(self, name: str) -> _RecordingSpan:
        return _RecordingSpan(name, self.spans)


class _RecordingInstrument:
    def __init__(self) -> None:
        self.calls: List[Tuple[float, Any]] = []

    def add(self, amount: float, attributes: Any = None) -> None:
        self.calls.append((amount, attributes))

    def record(self, amount: float, attributes: Any = None) -> None:
        self.calls.append((amount, attributes))


class _RecordingMeter:
    def __init__(self) -> None:
        self.instruments: Dict[str, _RecordingInstrument] = {}

    def create_counter(
        self, name: str, *, unit: str = "", description: str = ""
    ) -> _RecordingInstrument:
        instrument = _RecordingInstrument()
        self.instruments[name] = instrument
        return instrument

    def create_histogram(
        self, name: str, *, unit: str = "", description: str = ""
    ) -> _RecordingInstrument:
        instrument = _RecordingInstrument()
        self.instruments[name] = instrument
        return instrument


class TestNoOpDefaults:
    """Zero cost, zero error, with nothing installed."""

    def test_traced_stage_works_with_no_backend_installed(self) -> None:
        with traced_stage("mandate"):
            pass  # must not raise

    def test_an_exception_inside_a_traced_stage_still_propagates(self) -> None:
        with pytest.raises(ValueError):
            with traced_stage("policy"):
                raise ValueError("boom")

    def test_governance_metrics_record_without_a_backend(self) -> None:
        metrics = build_governance_metrics()
        metrics.record_denial("mandate_missing")  # must not raise
        metrics.record_stage_duration("risk", 12.5, status="executed")


class TestInstallingARealBackend:
    """The seam an outbound adapter (glassbox.adapters.outbound.otel) uses."""

    def test_traced_stage_uses_the_installed_tracer(self) -> None:
        tracer = _RecordingTracer()
        set_tracer(tracer)
        with bind_context(decision_id="decision-1", trace_id="trace-1", tenant_id="acme"):
            with traced_stage("mandate"):
                pass
        assert len(tracer.spans) == 1
        span = tracer.spans[0]
        assert span["name"] == "glassbox.stage.mandate"
        assert span["attributes"]["glassbox.decision_id"] == "decision-1"
        assert span["attributes"]["glassbox.tenant_id"] == "acme"
        assert span["attributes"]["glassbox.stage"] == "mandate"

    def test_traced_operation_wraps_a_whole_function(self) -> None:
        tracer = _RecordingTracer()
        set_tracer(tracer)

        @traced_operation("decision")
        def decide(x: int) -> int:
            return x * 2

        assert decide(21) == 42
        assert tracer.spans[0]["name"] == "glassbox.stage.decision"

    def test_governance_metrics_record_against_the_installed_meter(self) -> None:
        meter = _RecordingMeter()
        metrics = build_governance_metrics(meter)
        metrics.record_denial("limit_exceeded")
        assert meter.instruments["glassbox.decisions.denied_total"].calls == [
            (1, {"reason": "limit_exceeded"})
        ]

    def test_get_tracer_and_get_meter_return_the_installed_backend(self) -> None:
        tracer = _RecordingTracer()
        meter = _RecordingMeter()
        set_tracer(tracer)
        set_meter(meter)
        assert get_tracer() is tracer
        assert get_meter() is meter


class TestOtelAdapterInstallsIntoTheAppLayerSeam:
    """The real backend, if the SDK is installed, wires in the same way."""

    def test_configure_otel_installs_a_real_tracer(self) -> None:
        otel_sdk = pytest.importorskip("opentelemetry.sdk.trace")
        del otel_sdk
        from glassbox.adapters.outbound.otel.configure import configure_otel
        from glassbox.app.config import GlassBoxConfig, RuntimeProfile

        installed = configure_otel(GlassBoxConfig(profile=RuntimeProfile.DEV))
        assert installed is True
        with traced_stage("mandate"):
            pass  # a real span is created and exported; must not raise
