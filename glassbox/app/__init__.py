"""GlassBox application layer (GB-003).

Orchestration and wiring. This layer depends on :mod:`glassbox.domain` and
:mod:`glassbox.ports` only -- never on a concrete adapter. It is where the
decisions the pure layers deliberately refuse to make are taken:

* **how a failure is reported** -- the domain raises a structured error, this
  layer renders it into a log record, a metric and an evidence field
  (invariant I11);
* **which adapter is used** -- the composition root wires ports to
  implementations supplied by the process entry point;
* **whether the deployment is safe to start** -- configuration validation refuses
  a production profile with any safety switch turned off.

The dependency rule is ``domain <- ports <- app <- adapters`` and it never
reverses. ``tests/test_layering.py`` fails the build if this package imports
``glassbox.adapters``.
"""

from __future__ import annotations

from glassbox.app.composition import (
    REQUIRED_COMPONENTS,
    AdapterSet,
    ComponentFactory,
    GovernanceRuntime,
    build_runtime,
)
from glassbox.app.config import (
    ENV_PREFIX,
    SAFETY_SWITCHES,
    BaselineConfig,
    DispatchConfig,
    EvidenceConfig,
    GlassBoxConfig,
    IdentityConfig,
    LimitsConfig,
    ObservabilityConfig,
    PolicyConfig,
    RuntimeProfile,
    SigningConfig,
)
from glassbox.app.decision_service import DecisionOutcome, DecisionService
from glassbox.app.errors import CompositionError, ConfigurationError, ProfileViolationError
from glassbox.app.observability import (
    DEV_PROFILE_BANNER,
    CorrelationContext,
    bind_context,
    configure_logging,
    current_context,
    get_logger,
    log_error,
    log_startup,
)

__all__ = [
    # composition
    "REQUIRED_COMPONENTS",
    "AdapterSet",
    "ComponentFactory",
    "GovernanceRuntime",
    "build_runtime",
    # config
    "ENV_PREFIX",
    "SAFETY_SWITCHES",
    "BaselineConfig",
    "DispatchConfig",
    "EvidenceConfig",
    "GlassBoxConfig",
    "IdentityConfig",
    "LimitsConfig",
    "ObservabilityConfig",
    "PolicyConfig",
    "RuntimeProfile",
    "SigningConfig",
    # errors
    "CompositionError",
    "ConfigurationError",
    "ProfileViolationError",
    # decision service
    "DecisionOutcome",
    "DecisionService",
    # observability
    "DEV_PROFILE_BANNER",
    "CorrelationContext",
    "bind_context",
    "configure_logging",
    "current_context",
    "get_logger",
    "log_error",
    "log_startup",
]
