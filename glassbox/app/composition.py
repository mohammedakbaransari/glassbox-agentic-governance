"""The composition root (GB-003).

One place builds the object graph. Nothing else constructs a collaborator.

This is the structural fix for v1's ``GovernancePipeline``, which took 25+
constructor parameters, built eight concrete collaborators itself, and could not
be tested without monkey-patching. Its dependency-inversion score was 1/5.

Three properties are enforced here, at startup, before a single decision is
served:

* **Conformance.** Every wired component is checked against its ``Protocol``.
  A partially implemented adapter fails the deployment rather than raising
  ``AttributeError`` on the first decision that happens to reach it.
* **Profile safety.** An adapter set marked ``dev_only`` -- in-memory evidence, a
  locally-held signing key, a single-process limit store -- cannot be wired into
  a production profile, however the environment is configured.
* **Completeness, reported once.** Every missing or non-conforming component is
  collected and reported together, so an operator sees the whole gap in one pass.

This module imports :mod:`glassbox.domain` and :mod:`glassbox.ports`. It does not
import :mod:`glassbox.adapters`, and ``tests/test_layering.py`` fails the build if
that ever changes. Adapter sets are supplied *to* the composition root by the
process entry point, which is the only place allowed to name a concrete adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Tuple

from glassbox.app.config import GlassBoxConfig
from glassbox.app.errors import CompositionError, ProfileViolationError
from glassbox.app.observability import configure_logging, get_logger, log_startup
from glassbox.domain.serialization import require_identifier
from glassbox.ports.attestation import AttestationProvider
from glassbox.ports.baseline import BaselineStore
from glassbox.ports.catalogue import ActionCatalogue
from glassbox.ports.clock import Clock
from glassbox.ports.dispatcher import Dispatcher
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.identity import IdentityVerifier
from glassbox.ports.keys import MacSigner
from glassbox.ports.kill_switch import KillSwitch
from glassbox.ports.limits import LimitStore
from glassbox.ports.mandate import MandateStore
from glassbox.ports.policy import PolicyDecisionPoint
from glassbox.ports.risk import RiskEngine
from glassbox.ports.tool_registry import ToolRegistry

__all__ = [
    "ComponentFactory",
    "AdapterSet",
    "GovernanceRuntime",
    "build_runtime",
    "REQUIRED_COMPONENTS",
]

#: A factory receives the validated configuration and returns one component.
ComponentFactory = Callable[[GlassBoxConfig], Any]

#: ``(attribute name, port protocol)`` for every component the runtime needs.
#: The tuple is the single source of truth: :class:`AdapterSet`,
#: :class:`GovernanceRuntime` and the conformance check all derive from it, so a
#: port added in a later wave cannot be wired in without being verified.
REQUIRED_COMPONENTS: Tuple[Tuple[str, type], ...] = (
    ("clock", Clock),
    ("identity_verifier", IdentityVerifier),
    ("action_catalogue", ActionCatalogue),
    ("attestation_provider", AttestationProvider),
    ("tool_registry", ToolRegistry),
    ("mandate_store", MandateStore),
    ("kill_switch", KillSwitch),
    ("policy_decision_point", PolicyDecisionPoint),
    ("risk_engine", RiskEngine),
    ("limit_store", LimitStore),
    ("baseline_store", BaselineStore),
    ("mac_signer", MacSigner),
    ("evidence_store", EvidenceStore),
    ("dispatcher", Dispatcher),
)


@dataclass(frozen=True, slots=True)
class AdapterSet:
    """A named, complete set of factories for every port.

    Attributes:
        name: Identifier recorded in the startup log, e.g. ``postgres-redis-kms``.
        dev_only: ``True`` when the set provides no assurance -- in-memory state,
            no durability, no managed key. Such a set is refused outright by the
            production profile.
        factories: One factory per entry in :data:`REQUIRED_COMPONENTS`.
    """

    name: str
    factories: Mapping[str, ComponentFactory]
    dev_only: bool = False

    def __post_init__(self) -> None:
        require_identifier(self.name, field="name")
        if not isinstance(self.dev_only, bool):
            raise CompositionError(
                "dev_only must be a bool",
                failures=[f"dev_only={self.dev_only!r}"],
                adapter_set=self.name,
            )
        if not isinstance(self.factories, Mapping):
            raise CompositionError(
                "factories must be a mapping",
                failures=[f"factories is {type(self.factories).__name__}"],
                adapter_set=self.name,
            )

        required = {name for name, _ in REQUIRED_COMPONENTS}
        missing = sorted(required - set(self.factories))
        unexpected = sorted(set(self.factories) - required)
        not_callable = sorted(
            name for name, factory in self.factories.items() if not callable(factory)
        )
        failures = (
            [f"missing factory: {name}" for name in missing]
            + [f"unknown factory: {name}" for name in unexpected]
            + [f"factory is not callable: {name}" for name in not_callable]
        )
        if failures:
            raise CompositionError(
                f"adapter set {self.name!r} is incomplete",
                failures=failures,
                adapter_set=self.name,
            )
        object.__setattr__(self, "factories", dict(self.factories))


@dataclass(frozen=True, slots=True)
class GovernanceRuntime:
    """The fully wired, verified object graph.

    Frozen, so a component cannot be swapped after startup. Every field is
    guaranteed to satisfy its port -- :func:`build_runtime` is the only supported
    way to obtain one, and it will not return a partially wired instance.
    """

    config: GlassBoxConfig
    adapter_set_name: str
    clock: Clock
    identity_verifier: IdentityVerifier
    action_catalogue: ActionCatalogue
    attestation_provider: AttestationProvider
    tool_registry: ToolRegistry
    mandate_store: MandateStore
    kill_switch: KillSwitch
    policy_decision_point: PolicyDecisionPoint
    risk_engine: RiskEngine
    limit_store: LimitStore
    baseline_store: BaselineStore
    mac_signer: MacSigner
    evidence_store: EvidenceStore
    dispatcher: Dispatcher

    def component(self, name: str) -> Any:
        """Return a wired component by name.

        Raises:
            CompositionError: If ``name`` is not a known component.
        """
        if name not in {declared for declared, _ in REQUIRED_COMPONENTS}:
            raise CompositionError("unknown component", failures=[name], component=name)
        return getattr(self, name)

    def describe(self) -> Dict[str, Any]:
        """Return a summary for the startup log and the health endpoint."""
        return {
            **self.config.describe(),
            "adapter_set": self.adapter_set_name,
            "components": {
                name: type(getattr(self, name)).__name__ for name, _ in REQUIRED_COMPONENTS
            },
        }


def build_runtime(config: GlassBoxConfig, adapters: AdapterSet) -> GovernanceRuntime:
    """Build and verify the object graph for one process.

    Order matters. The profile check runs before any factory, so a development
    adapter set never touches a production configuration -- not even long enough
    to open a connection.

    Args:
        config: Validated configuration. Construct it with
            :meth:`~glassbox.app.config.GlassBoxConfig.from_env` or
            :meth:`~glassbox.app.config.GlassBoxConfig.from_mapping`.
        adapters: A complete, named set of component factories.

    Returns:
        A frozen runtime whose every component satisfies its port.

    Raises:
        ProfileViolationError: If a ``dev_only`` adapter set is wired into a
            profile that provides assurance.
        CompositionError: If any factory raises, returns ``None``, or returns an
            object that does not satisfy its protocol. Every failure is reported
            together.
    """
    if not isinstance(config, GlassBoxConfig):
        raise CompositionError(
            "config must be a GlassBoxConfig",
            failures=[f"config is {type(config).__name__}"],
        )
    if not isinstance(adapters, AdapterSet):
        raise CompositionError(
            "adapters must be an AdapterSet",
            failures=[f"adapters is {type(adapters).__name__}"],
        )

    if adapters.dev_only and not config.profile.permits_dev_only_adapters:
        raise ProfileViolationError(
            "a development-only adapter set cannot serve a production profile",
            violations=[
                f"adapter set {adapters.name!r} is dev_only but the profile is "
                f"{config.profile.value!r}"
            ],
            adapter_set=adapters.name,
            profile=config.profile.value,
        )

    logger = configure_logging(config)

    built: Dict[str, Any] = {}
    failures: List[str] = []
    for name, protocol in REQUIRED_COMPONENTS:
        factory = adapters.factories[name]
        try:
            component = factory(config)
        except Exception as exc:  # noqa: BLE001 - re-raised as a structured failure
            failures.append(f"{name}: factory raised {type(exc).__name__}: {exc}")
            continue
        if component is None:
            failures.append(f"{name}: factory returned None")
            continue
        if not isinstance(component, protocol):
            failures.append(
                f"{name}: {type(component).__name__} does not satisfy {protocol.__name__} "
                f"(missing: {_missing_members(component, protocol)})"
            )
            continue
        built[name] = component

    if failures:
        raise CompositionError(
            f"runtime could not be composed from adapter set {adapters.name!r}",
            failures=failures,
            adapter_set=adapters.name,
            profile=config.profile.value,
        )

    runtime = GovernanceRuntime(config=config, adapter_set_name=adapters.name, **built)
    log_startup(logger, config, adapter_set=adapters.name)
    get_logger("composition").info(
        "runtime composed", extra={"components": runtime.describe()["components"]}
    )
    return runtime


def _missing_members(component: Any, protocol: type) -> str:
    """Return the protocol members ``component`` does not provide.

    ``isinstance`` against a runtime-checkable protocol reports only pass/fail;
    naming the gap turns a startup failure into an actionable one.
    """
    expected = {name for name in dir(protocol) if not name.startswith("_") and name not in {"mro"}}
    missing = sorted(name for name in expected if not hasattr(component, name))
    return ", ".join(missing) if missing else "none (signature mismatch)"
