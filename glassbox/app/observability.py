"""Structured logging and request context (GB-003).

This module is where the domain's "raise, never log" discipline (invariant I11)
is cashed in: a :class:`~glassbox.domain.errors.GlassBoxError` carries a stable
``code`` and a structured ``context``, and :func:`log_error` renders both into a
single machine-parsable record. Exactly one component decides how a failure is
reported, and it is this one.

It is also where request context lives, and it uses
:class:`contextvars.ContextVar` (invariant I10). v1 used ``threading.local()``,
so the tenant binding was silently lost the moment work crossed a
``ThreadPoolExecutor`` boundary -- which the pipeline did on every registered
stage. A ``ContextVar`` propagates across ``asyncio`` tasks and, via
``contextvars.copy_context()``, across worker threads.

The correlation fields are attached by a logging *filter* rather than passed at
every call site, so a log record cannot accidentally omit the tenant it belongs
to.
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional

from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import GlassBoxError

__all__ = [
    "CorrelationContext",
    "current_context",
    "bind_context",
    "StructuredFormatter",
    "CorrelationFilter",
    "configure_logging",
    "get_logger",
    "log_error",
    "log_startup",
    "DEV_PROFILE_BANNER",
]

#: Printed once at startup under the development profile. Deliberately hard to
#: miss: the review found that the worst outcome is a system believed to be
#: governing when it is not.
DEV_PROFILE_BANNER = (
    "GLASSBOX IS RUNNING IN THE DEVELOPMENT PROFILE. "
    "Evidence is not durable, not MAC-signed against a managed key, and not "
    "admissible. Limits are not enforced across replicas. "
    "THIS CONFIGURATION PROVIDES NO ASSURANCE AND MUST NOT BE USED IN PRODUCTION."
)

#: Attributes present on every :class:`logging.LogRecord`, used to separate the
#: caller's ``extra`` fields from the standard ones when serialising.
_STANDARD_RECORD_FIELDS = frozenset(
    vars(logging.LogRecord("", 0, "", 0, "", None, None)).keys()
) | {"message", "asctime", "taskName"}


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Identifiers that must appear on every log record for one decision.

    Attributes:
        decision_id: Correlation id of the decision being processed.
        trace_id: Distributed trace id.
        tenant_id: Tenant, derived from the verified principal -- never a header.
        agent_ref: Acting agent.
    """

    decision_id: Optional[str] = None
    trace_id: Optional[str] = None
    tenant_id: Optional[str] = None
    agent_ref: Optional[str] = None

    def as_dict(self) -> Dict[str, str]:
        """Return the populated fields only, so empty keys do not clutter logs."""
        return {
            name: value
            for name, value in (
                ("decision_id", self.decision_id),
                ("trace_id", self.trace_id),
                ("tenant_id", self.tenant_id),
                ("agent_ref", self.agent_ref),
            )
            if value is not None
        }

    def merged_with(self, other: "CorrelationContext") -> "CorrelationContext":
        """Return this context overlaid with the populated fields of ``other``."""
        return CorrelationContext(
            decision_id=other.decision_id or self.decision_id,
            trace_id=other.trace_id or self.trace_id,
            tenant_id=other.tenant_id or self.tenant_id,
            agent_ref=other.agent_ref or self.agent_ref,
        )


_CONTEXT: contextvars.ContextVar[CorrelationContext] = contextvars.ContextVar(
    "glassbox_correlation_context", default=CorrelationContext()
)


def current_context() -> CorrelationContext:
    """Return the correlation context bound to the current execution."""
    return _CONTEXT.get()


@contextmanager
def bind_context(
    *,
    decision_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    agent_ref: Optional[str] = None,
) -> Iterator[CorrelationContext]:
    """Bind correlation identifiers for the duration of the block.

    Nested binds merge rather than replace, so an inner block that only knows the
    ``decision_id`` does not erase the ``tenant_id`` an outer block established.

    To propagate across a worker thread, copy the context explicitly::

        ctx = contextvars.copy_context()
        pool.submit(ctx.run, work)

    Yields:
        The merged context that is in force inside the block.
    """
    merged = _CONTEXT.get().merged_with(
        CorrelationContext(
            decision_id=decision_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            agent_ref=agent_ref,
        )
    )
    token = _CONTEXT.set(merged)
    try:
        yield merged
    finally:
        _CONTEXT.reset(token)


class CorrelationFilter(logging.Filter):
    """Attaches the bound correlation context to every record.

    Implemented as a filter rather than as a call-site convention: a governance
    log line that omits its tenant is not much use, and relying on every author
    to remember guarantees some will not.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for name, value in current_context().as_dict().items():
            if not hasattr(record, name):
                setattr(record, name, value)
        return True


class StructuredFormatter(logging.Formatter):
    """Renders records as single-line JSON.

    Args:
        service_name: Emitted on every record so multi-service log streams can be
            separated.
    """

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD_RECORD_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_logging(config: GlassBoxConfig) -> logging.Logger:
    """Install the structured handler on the ``glassbox`` logger.

    Idempotent: repeated calls replace the handler rather than stacking, so a
    process that rebuilds its runtime does not emit duplicate records.

    Args:
        config: Validated runtime configuration.

    Returns:
        The configured package logger.
    """
    logger = logging.getLogger("glassbox")
    logger.setLevel(config.observability.log_level.upper())

    for existing in list(logger.handlers):
        if getattr(existing, "_glassbox_managed", False):
            logger.removeHandler(existing)

    handler: logging.Handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        StructuredFormatter(config.observability.service_name)
        if config.observability.json_logs
        else logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
        )
    )
    handler.addFilter(CorrelationFilter())
    handler._glassbox_managed = True  # type: ignore[attr-defined]
    logger.addHandler(handler)

    # The package owns its output; leaving propagation on double-logs whenever
    # the embedding application also configures the root logger.
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a child of the package logger."""
    return logging.getLogger(f"glassbox.{name}" if not name.startswith("glassbox") else name)


def log_error(
    logger: logging.Logger,
    error: BaseException,
    *,
    message: Optional[str] = None,
    level: int = logging.ERROR,
    **extra: Any,
) -> None:
    """Render an exception as one structured record.

    A :class:`~glassbox.domain.errors.GlassBoxError` contributes its ``code`` and
    its ``context`` as first-class fields. Any other exception is reported with
    its class name, so an unexpected failure is never less legible than an
    expected one.
    """
    payload: Dict[str, Any] = dict(extra)
    if isinstance(error, GlassBoxError):
        details = error.as_dict()
        payload["error_code"] = details["code"]
        payload["error_class"] = details["error_class"]
        payload.update({f"ctx_{key}": value for key, value in details["context"].items()})
        text = message or details["message"]
    else:
        payload["error_code"] = "unhandled_exception"
        payload["error_class"] = type(error).__name__
        text = message or str(error)
    logger.log(level, text, extra=payload)


def log_startup(logger: logging.Logger, config: GlassBoxConfig, **extra: Any) -> None:
    """Emit the startup record, and the banner when assurance is not provided."""
    logger.info("glassbox runtime starting", extra={**config.describe(), **extra})
    if not config.profile.provides_assurance:
        logger.warning(DEV_PROFILE_BANNER, extra={"profile": config.profile.value})
    unsafe = config.unsafe_switches()
    if unsafe:
        logger.warning(
            "safety switches are disabled",
            extra={"unsafe_switches": list(unsafe), "profile": config.profile.value},
        )
