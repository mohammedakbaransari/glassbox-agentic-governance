"""Observability: tracing and metrics port (GB-034).

``glassbox.app`` has zero third-party dependencies -- the same discipline that
keeps ``glassbox.domain``/``glassbox.ports`` free of them, one layer further
out. This module therefore never imports ``opentelemetry`` (or any other
tracing/metrics library): it defines the *shape* every caller in ``app`` codes
against -- ``traced_stage``/``traced_operation`` and
:class:`GovernanceMetrics` -- plus a safe, fully-functional no-op
implementation built entirely from the standard library.

A real backend is installed by calling :func:`set_tracer`/:func:`set_meter`
from *outside* this layer -- normally
:mod:`glassbox.adapters.outbound.otel.configure`, an outbound adapter that is
free to import ``opentelemetry`` because that is exactly what
``adapters.outbound`` is for. Wiring it in is the process entry point's job
(the same place that assembles an :class:`~glassbox.app.composition.AdapterSet`
and hands it to :func:`~glassbox.app.composition.build_runtime`), never
``glassbox.app`` itself.
"""

from __future__ import annotations

import functools
from contextlib import contextmanager
from typing import Any, Callable, Iterator, Optional, Protocol, TypeVar, runtime_checkable

from glassbox.app.observability import current_context, get_logger

__all__ = [
    "Span",
    "Tracer",
    "Instrument",
    "Meter",
    "set_tracer",
    "set_meter",
    "get_tracer",
    "get_meter",
    "traced_stage",
    "traced_operation",
    "GovernanceMetrics",
    "build_governance_metrics",
]

_logger = get_logger("telemetry")

_F = TypeVar("_F", bound=Callable[..., Any])


@runtime_checkable
class Span(Protocol):
    """The minimum span surface :func:`traced_stage` needs."""

    def set_attribute(self, key: str, value: Any) -> None: ...

    def set_status(self, status: Any, description: Optional[str] = None) -> None: ...

    def record_exception(self, exception: BaseException) -> None: ...


@runtime_checkable
class Tracer(Protocol):
    """The minimum tracer surface :func:`traced_stage` needs."""

    def start_as_current_span(self, name: str) -> Any:
        """Return a context manager yielding a :class:`Span`."""
        ...


@runtime_checkable
class Instrument(Protocol):
    """A counter or histogram."""

    def add(self, amount: float, attributes: Optional[Any] = None) -> None: ...

    def record(self, amount: float, attributes: Optional[Any] = None) -> None: ...


@runtime_checkable
class Meter(Protocol):
    """The minimum meter surface :class:`GovernanceMetrics` needs."""

    def create_counter(self, name: str, *, unit: str = "", description: str = "") -> Instrument: ...

    def create_histogram(
        self, name: str, *, unit: str = "", description: str = ""
    ) -> Instrument: ...


class _NullSpan:
    """A span that does nothing. The default until a real tracer is installed."""

    def set_attribute(self, key: str, value: Any) -> None:
        return None

    def set_status(self, status: Any, description: Optional[str] = None) -> None:
        return None

    def record_exception(self, exception: BaseException) -> None:
        return None

    def __enter__(self) -> "_NullSpan":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        return None


class _NullTracer:
    """A tracer that produces only :class:`_NullSpan` instances."""

    @contextmanager
    def start_as_current_span(self, name: str) -> Iterator[_NullSpan]:
        yield _NullSpan()


class _NullInstrument:
    """A counter/histogram that discards every recording."""

    def add(self, amount: float, attributes: Optional[Any] = None) -> None:
        return None

    def record(self, amount: float, attributes: Optional[Any] = None) -> None:
        return None


class _NullMeter:
    """A meter that produces only :class:`_NullInstrument` instances."""

    def create_counter(
        self, name: str, *, unit: str = "", description: str = ""
    ) -> _NullInstrument:
        return _NullInstrument()

    def create_histogram(
        self, name: str, *, unit: str = "", description: str = ""
    ) -> _NullInstrument:
        return _NullInstrument()


_tracer: Tracer = _NullTracer()
_meter: Meter = _NullMeter()


def set_tracer(tracer: Tracer) -> None:
    """Install a real tracer. Called by an outbound adapter, never by ``app`` itself."""
    global _tracer
    _tracer = tracer
    _logger.info("tracer installed", extra={"tracer_type": type(tracer).__name__})


def set_meter(meter: Meter) -> None:
    """Install a real meter. Called by an outbound adapter, never by ``app`` itself."""
    global _meter
    _meter = meter
    _logger.info("meter installed", extra={"meter_type": type(meter).__name__})


def get_tracer() -> Tracer:
    """Return the currently installed tracer (a no-op until one is set)."""
    return _tracer


def get_meter() -> Meter:
    """Return the currently installed meter (a no-op until one is set)."""
    return _meter


@contextmanager
def traced_stage(stage: str, *, tracer: Optional[Tracer] = None) -> Iterator[Any]:
    """Start a span for one governance stage, tagged with the bound correlation context.

    RED metrics (rate, errors, duration) fall out of standard span attributes;
    this context manager additionally sets the span's status to an error and
    records the exception before letting it propagate, so a stage's failure is
    visible on its own span rather than only on whatever caller eventually
    catches it. Safe to use whether or not a real tracer has been installed --
    against the default no-op tracer, every operation here is a cheap no-op.
    """
    active_tracer = tracer or get_tracer()
    context = current_context().as_dict()
    with active_tracer.start_as_current_span(f"glassbox.stage.{stage}") as span:
        for key, value in context.items():
            span.set_attribute(f"glassbox.{key}", value)
        span.set_attribute("glassbox.stage", stage)
        try:
            yield span
        except Exception as exc:  # noqa: BLE001 - recorded on the span, always re-raised
            span.set_status("error", str(exc))
            span.record_exception(exc)
            raise


def traced_operation(stage: str) -> Callable[[_F], _F]:
    """Decorator form of :func:`traced_stage`, for wrapping a whole function.

    Exists so a method's tested body can be instrumented by adding one line
    above its definition, rather than re-indenting the method to fit inside a
    ``with`` block -- the lower-risk choice for a method as load-bearing as
    :meth:`~glassbox.app.decision_service.DecisionService._evaluate`.
    """

    def decorator(func: _F) -> _F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            with traced_stage(stage):
                return func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


class GovernanceMetrics:
    """Pre-built instruments for the governance-specific metrics GB-034 calls for.

    One instance per process, built once by :func:`build_governance_metrics` and
    used from wherever a governance signal needs recording -- never constructed
    ad hoc per call, which is what keeps instrument identity (and therefore
    exported metric names) stable. Works identically, as a safe no-op, whether
    or not a real :class:`Meter` has been installed.
    """

    __slots__ = (
        "stage_duration_ms",
        "denials_total",
        "fail_closed_total",
        "evidence_write_latency_ms",
        "chain_verification_total",
        "limit_rejections_total",
        "mandatory_stage_skipped_total",
    )

    def __init__(self, meter: Meter) -> None:
        self.stage_duration_ms = meter.create_histogram(
            "glassbox.stage.duration_ms", unit="ms", description="Duration of one governance stage."
        )
        self.denials_total = meter.create_counter(
            "glassbox.decisions.denied_total", description="Denials, by reason."
        )
        self.fail_closed_total = meter.create_counter(
            "glassbox.dependency.fail_closed_total",
            description="Times a dependency outage caused a fail-closed denial.",
        )
        self.evidence_write_latency_ms = meter.create_histogram(
            "glassbox.evidence.write_latency_ms",
            unit="ms",
            description="Latency of append_intent, the durable-before-effect write.",
        )
        self.chain_verification_total = meter.create_counter(
            "glassbox.evidence.chain_verification_total",
            description="Evidence chain verification results, by status.",
        )
        self.limit_rejections_total = meter.create_counter(
            "glassbox.limits.rejected_total", description="Velocity/volume limit rejections."
        )
        self.mandatory_stage_skipped_total = meter.create_counter(
            "glassbox.stages.mandatory_skipped_total",
            description="A mandatory stage recorded SKIPPED rather than EXECUTED.",
        )

    def record_denial(self, reason: str) -> None:
        self.denials_total.add(1, {"reason": reason})

    def record_stage_duration(self, stage: str, duration_ms: float, *, status: str) -> None:
        self.stage_duration_ms.record(duration_ms, {"stage": stage, "status": status})


def build_governance_metrics(meter: Optional[Meter] = None) -> GovernanceMetrics:
    """Build the shared metrics instance, using the currently installed meter if none is given."""
    return GovernanceMetrics(meter or get_meter())
