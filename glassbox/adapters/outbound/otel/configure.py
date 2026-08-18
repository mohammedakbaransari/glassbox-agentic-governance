"""Configure the real OpenTelemetry SDK and install it into ``glassbox.app.telemetry`` (GB-034).

Called once, by the process entry point -- the same place that assembles an
:class:`~glassbox.app.composition.AdapterSet` and hands it to
:func:`~glassbox.app.composition.build_runtime`. ``glassbox.app`` cannot call
this itself: it has zero third-party dependencies, by the same discipline that
keeps :mod:`glassbox.domain`/:mod:`glassbox.ports` free of them one layer
further in, and ``opentelemetry`` is explicitly one of the packages
``tests/test_layering.py`` forbids the ``app`` layer from importing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from glassbox.app.observability import get_logger
from glassbox.app.telemetry import set_meter, set_tracer

if TYPE_CHECKING:
    from glassbox.app.config import GlassBoxConfig

__all__ = ["configure_otel"]

_logger = get_logger("otel")

#: Guards against re-creating a ``TracerProvider``/``BatchSpanProcessor`` (and
#: its background export thread) on every call. Telemetry is configured once,
#: at process startup -- calling this repeatedly (e.g. a caller that builds
#: more than one app instance in the same process) must not leak a background
#: thread per call.
_configured = False


def configure_otel(config: "GlassBoxConfig") -> bool:
    """Install a real ``TracerProvider``/``MeterProvider``.

    Never raises: a missing or misconfigured telemetry SDK is logged and
    leaves ``glassbox.app.telemetry``'s no-op default in place, rather than
    blocking process startup on an observability dependency that no decision
    ever needs to complete.

    Returns:
        Whether a real (non-no-op) provider was installed.
    """
    global _configured
    if _configured:
        return True

    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.resources import SERVICE_NAME, Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    except ImportError:
        _logger.warning(
            "opentelemetry is not installed; tracing and metrics remain no-ops",
            extra={"remedy": "pip install 'glassbox-governance[otel]'"},
        )
        return False

    resource = Resource.create({SERVICE_NAME: config.observability.service_name})

    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(tracer_provider)

    meter_provider = MeterProvider(resource=resource)
    metrics.set_meter_provider(meter_provider)

    set_tracer(trace.get_tracer("glassbox"))
    set_meter(metrics.get_meter("glassbox"))

    _configured = True
    _logger.info("telemetry configured", extra={"service_name": config.observability.service_name})
    return True
