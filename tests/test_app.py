"""Tests for the application layer: config, observability and composition (GB-003).

The composition root is where a governance system either refuses to start or
quietly comes up half-wired. These tests are weighted accordingly: most of them
assert that something is *refused*.
"""

from __future__ import annotations

import contextvars
import dataclasses
import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import pytest

from glassbox.app.composition import (
    REQUIRED_COMPONENTS,
    AdapterSet,
    GovernanceRuntime,
    build_runtime,
)
from glassbox.app.config import (
    ENV_PREFIX,
    SAFETY_SWITCHES,
    BaselineConfig,
    EvidenceConfig,
    GlassBoxConfig,
    IdentityConfig,
    LimitsConfig,
    PolicyConfig,
    RuntimeProfile,
    SigningConfig,
)
from glassbox.app.errors import CompositionError, ConfigurationError, ProfileViolationError
from glassbox.app.observability import (
    DEV_PROFILE_BANNER,
    CorrelationContext,
    CorrelationFilter,
    StructuredFormatter,
    bind_context,
    configure_logging,
    current_context,
    log_error,
    log_startup,
)
from glassbox.domain.errors import LimitStoreUnavailable

# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #

PROD_VALUES: Dict[str, Any] = {
    "profile": "prod",
    "evidence": {"dsn": "postgresql://evidence", "worm_anchor_uri": "s3://bucket?lock=compliance"},
    "limits": {"url": "redis://limits"},
    "baseline": {"url": "redis://baselines"},
    "signing": {"key_id": "kms.evidence.v1"},
    "policy": {"bundle_registry_dsn": "postgresql://policy"},
}


def dev_config(**overrides: Any) -> GlassBoxConfig:
    """Build a valid development configuration."""
    return GlassBoxConfig(profile=RuntimeProfile.DEV, **overrides)


def prod_config(**section_overrides: Any) -> GlassBoxConfig:
    """Build a valid production configuration, with optional section overrides."""
    values = {key: dict(value) for key, value in PROD_VALUES.items() if key != "profile"}
    for section, changes in section_overrides.items():
        values.setdefault(section, {}).update(changes)
    return GlassBoxConfig.from_mapping({"profile": "prod", **values})


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


class TestProfile:
    """A profile is a posture, not a convenience flag."""

    def test_only_production_provides_assurance(self) -> None:
        assert RuntimeProfile.PROD.provides_assurance is True
        assert RuntimeProfile.DEV.provides_assurance is False

    def test_only_development_permits_dev_adapters_and_unsafe_switches(self) -> None:
        assert RuntimeProfile.DEV.permits_dev_only_adapters is True
        assert RuntimeProfile.DEV.permits_unsafe_switches is True
        assert RuntimeProfile.PROD.permits_dev_only_adapters is False
        assert RuntimeProfile.PROD.permits_unsafe_switches is False


class TestConfigurationValidation:
    """Production refuses to start unless every control is in force."""

    def test_development_profile_needs_no_infrastructure(self) -> None:
        assert dev_config().profile is RuntimeProfile.DEV

    def test_production_lists_every_missing_requirement_at_once(self) -> None:
        """An operator fixes the deployment in one pass, not one restart per problem."""
        with pytest.raises(ProfileViolationError) as excinfo:
            GlassBoxConfig(profile=RuntimeProfile.PROD)
        violations = excinfo.value.violations
        assert len(violations) == 6
        assert any("evidence.dsn" in item for item in violations)
        assert any("worm_anchor_uri" in item for item in violations)
        assert any("signing.key_id" in item for item in violations)

    def test_fully_configured_production_starts(self) -> None:
        config = prod_config()
        assert config.profile is RuntimeProfile.PROD
        assert config.unsafe_switches() == ()

    @pytest.mark.parametrize(("path", "safe"), SAFETY_SWITCHES)
    def test_every_safety_switch_blocks_production_when_flipped(
        self, path: str, safe: bool
    ) -> None:
        """Each switch is individually load-bearing, not decorative."""
        section, _, key = path.partition(".")
        with pytest.raises(ProfileViolationError) as excinfo:
            prod_config(**{section: {key: not safe}})
        assert any(path in item for item in excinfo.value.violations)

    def test_safety_switches_default_to_the_safe_value(self) -> None:
        """A missing environment variable can only make a deployment safer."""
        config = dev_config()
        assert config.unsafe_switches() == ()

    def test_development_may_disable_a_switch_and_it_is_reported(self) -> None:
        config = dev_config(limits=LimitsConfig(fail_closed=False))
        assert config.unsafe_switches() == ("limits.fail_closed",)

    def test_every_declared_switch_path_resolves(self) -> None:
        """Guards against a renamed field silently disabling a check."""
        config = dev_config()
        for path, _ in SAFETY_SWITCHES:
            assert isinstance(config._lookup(path), bool)

    def test_shared_api_key_is_forbidden_in_production(self) -> None:
        """The v1 single bearer token cannot identify a tenant, agent or human."""
        with pytest.raises(ProfileViolationError):
            prod_config(identity={"allow_shared_api_key": True})

    def test_locally_held_signing_key_is_forbidden_in_production(self) -> None:
        """A key the writer can read makes the evidence chain forgeable."""
        with pytest.raises(ProfileViolationError):
            prod_config(signing={"allow_local_key": True})


class TestConfigurationLoading:
    """Loading is strict: nothing is guessed and nothing is ignored."""

    def test_profile_has_no_default(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_mapping({})
        assert "profile is required" in excinfo.value.violations[0]

    def test_unknown_profile_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            GlassBoxConfig.from_mapping({"profile": "staging"})

    def test_unknown_section_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_mapping({"profile": "dev", "nonsense": {}})
        assert "unknown section: nonsense" in excinfo.value.violations

    def test_unknown_key_is_rejected(self) -> None:
        """A silently ignored key is indistinguishable from a disabled control."""
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_mapping({"profile": "dev", "limits": {"fail_clsoed": True}})
        assert "unknown key: limits.fail_clsoed" in excinfo.value.violations

    @pytest.mark.parametrize("literal", ["true", "TRUE", "1", "yes", "on"])
    def test_true_literals_parse(self, literal: str) -> None:
        config = GlassBoxConfig.from_mapping({"profile": "dev", "limits": {"fail_closed": literal}})
        assert config.limits.fail_closed is True

    @pytest.mark.parametrize("literal", ["false", "0", "no", "off"])
    def test_false_literals_parse(self, literal: str) -> None:
        config = GlassBoxConfig.from_mapping({"profile": "dev", "limits": {"fail_closed": literal}})
        assert config.limits.fail_closed is False

    def test_unrecognised_boolean_is_an_error_not_a_falsy_value(self) -> None:
        """A typo in a safety switch must fail the deployment, not disable a control."""
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_mapping({"profile": "dev", "limits": {"fail_closed": "maybe"}})
        assert "fail_closed" in excinfo.value.violations[0]

    def test_non_numeric_integer_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            GlassBoxConfig.from_mapping({"profile": "dev", "limits": {"cooldown_seconds": "soon"}})

    def test_environment_loading_round_trips(self) -> None:
        config = GlassBoxConfig.from_env(
            {
                f"{ENV_PREFIX}PROFILE": "dev",
                f"{ENV_PREFIX}LIMITS_COOLDOWN_SECONDS": "45",
                f"{ENV_PREFIX}OBSERVABILITY_SERVICE_NAME": "glassbox-test",
                "UNRELATED_VARIABLE": "ignored",
            }
        )
        assert config.limits.cooldown_seconds == 45
        assert config.observability.service_name == "glassbox-test"

    def test_unknown_environment_variable_is_rejected(self) -> None:
        """A misspelled switch must not leave the control silently at its default.

        ``GLASSBOX_LIMITS_FAILCLOSED`` resolves to a known *section* but an
        unknown key, so it is reported as such rather than as an unknown variable.
        Either way it fails the deployment.
        """
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_env(
                {f"{ENV_PREFIX}PROFILE": "dev", f"{ENV_PREFIX}LIMITS_FAILCLOSED": "false"}
            )
        assert "unknown key: limits.failclosed" in excinfo.value.violations

    def test_unknown_environment_section_is_rejected(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            GlassBoxConfig.from_env(
                {f"{ENV_PREFIX}PROFILE": "dev", f"{ENV_PREFIX}TELEPATHY_ENABLED": "true"}
            )
        assert any("TELEPATHY_ENABLED" in item for item in excinfo.value.violations)

    def test_invalid_component_settings_are_rejected(self) -> None:
        with pytest.raises(ConfigurationError):
            EvidenceConfig(seal_interval_seconds=0)
        with pytest.raises(ConfigurationError):
            LimitsConfig(default_window_seconds=0)

    def test_sentinel_hosts_without_a_service_name_is_rejected(self) -> None:
        """GB-011 HA follow-up: Sentinel needs a master-name to discover."""
        with pytest.raises(ConfigurationError):
            LimitsConfig(sentinel_hosts=(("sentinel-1", 26379),))
        with pytest.raises(ConfigurationError):
            BaselineConfig(sentinel_hosts=(("sentinel-1", 26379),))

    def test_sentinel_hosts_with_a_service_name_is_accepted(self) -> None:
        limits = LimitsConfig(
            sentinel_hosts=(("sentinel-1", 26379), ("sentinel-2", 26379)),
            sentinel_service_name="glassbox-limits",
        )
        assert limits.sentinel_service_name == "glassbox-limits"
        baseline = BaselineConfig(
            sentinel_hosts=(("sentinel-1", 26379),), sentinel_service_name="glassbox-baseline"
        )
        assert baseline.sentinel_service_name == "glassbox-baseline"


class TestConfigurationDescription:
    """The startup summary must be safe to log."""

    def test_connection_strings_are_never_reported_by_value(self) -> None:
        """A DSN routinely carries a password."""
        described = json.dumps(prod_config().describe())
        assert "postgresql://evidence" not in described
        assert "redis://limits" not in described
        assert '"evidence_configured": true' in described

    def test_description_reports_assurance_and_unsafe_switches(self) -> None:
        described = dev_config(policy=PolicyConfig(require_signature=False)).describe()
        assert described["provides_assurance"] is False
        assert described["unsafe_switches"] == ["policy.require_signature"]

    def test_config_is_immutable(self) -> None:
        with pytest.raises(Exception):
            dev_config().profile = RuntimeProfile.PROD  # type: ignore[misc]


# --------------------------------------------------------------------------- #
# Observability
# --------------------------------------------------------------------------- #


def _capture(logger: logging.Logger) -> io.StringIO:
    """Replace the logger's handlers with a capturing structured handler."""
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(StructuredFormatter("glassbox-test"))
    handler.addFilter(CorrelationFilter())
    logger.handlers = [handler]
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    return stream


def _records(stream: io.StringIO) -> List[Dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


class TestCorrelationContext:
    """Invariant I10: context propagates, including across threads."""

    def test_context_is_empty_by_default(self) -> None:
        assert current_context().as_dict() == {}

    def test_bind_sets_and_resets(self) -> None:
        with bind_context(tenant_id="acme", decision_id="d-1"):
            assert current_context().tenant_id == "acme"
        assert current_context().tenant_id is None

    def test_nested_binds_merge_rather_than_replace(self) -> None:
        """An inner block that knows only the decision must not erase the tenant."""
        with bind_context(tenant_id="acme"):
            with bind_context(decision_id="d-1"):
                context = current_context()
                assert context.tenant_id == "acme"
                assert context.decision_id == "d-1"
            assert current_context().decision_id is None

    def test_context_survives_a_thread_pool_boundary(self) -> None:
        """Regression: v1 used threading.local and lost the tenant on every stage.

        The measured v1 behaviour was ``sync_tenant_bound: 'tenant_alpha'`` but
        ``async_tenant_bound: null``.
        """
        with ThreadPoolExecutor(max_workers=1) as pool:
            with bind_context(tenant_id="tenant_alpha"):
                sync_bound = current_context().tenant_id
                context = contextvars.copy_context()
                async_bound = pool.submit(context.run, lambda: current_context().tenant_id).result()
        assert sync_bound == "tenant_alpha"
        assert async_bound == "tenant_alpha"

    def test_merged_with_prefers_populated_fields(self) -> None:
        base = CorrelationContext(tenant_id="acme", agent_ref="agent.a")
        merged = base.merged_with(CorrelationContext(agent_ref="agent.b"))
        assert (merged.tenant_id, merged.agent_ref) == ("acme", "agent.b")


class TestStructuredLogging:
    """The domain raises; this layer renders. Exactly once, machine-readably."""

    def test_records_are_single_line_json(self) -> None:
        logger = logging.getLogger("glassbox.test.json")
        stream = _capture(logger)
        logger.info("hello", extra={"custom_field": 7})
        record = _records(stream)[0]
        assert record["message"] == "hello"
        assert record["custom_field"] == 7
        assert record["service"] == "glassbox-test"

    def test_correlation_fields_are_attached_without_a_call_site_change(self) -> None:
        """A governance log line that omits its tenant is not much use."""
        logger = logging.getLogger("glassbox.test.correlation")
        stream = _capture(logger)
        with bind_context(tenant_id="acme", decision_id="d-1"):
            logger.info("decision evaluated")
        record = _records(stream)[0]
        assert record["tenant_id"] == "acme"
        assert record["decision_id"] == "d-1"

    def test_domain_error_context_becomes_first_class_fields(self) -> None:
        logger = logging.getLogger("glassbox.test.error")
        stream = _capture(logger)
        log_error(
            logger,
            LimitStoreUnavailable("redis unreachable", key="glassbox|limit|acme", attempts=3),
        )
        record = _records(stream)[0]
        assert record["error_code"] == "limit_store_unavailable"
        assert record["error_class"] == "LimitStoreUnavailable"
        assert record["ctx_key"] == "glassbox|limit|acme"
        assert record["ctx_attempts"] == "3"

    def test_unexpected_exceptions_are_no_less_legible(self) -> None:
        logger = logging.getLogger("glassbox.test.unexpected")
        stream = _capture(logger)
        log_error(logger, ValueError("boom"))
        record = _records(stream)[0]
        assert record["error_code"] == "unhandled_exception"
        assert record["error_class"] == "ValueError"

    def test_configure_logging_is_idempotent(self) -> None:
        """A rebuilt runtime must not double-log every record."""
        config = dev_config()
        first = configure_logging(config)
        count = len(first.handlers)
        second = configure_logging(config)
        assert second is first
        assert len(second.handlers) == count

    def test_development_profile_emits_the_banner(self) -> None:
        logger = logging.getLogger("glassbox.test.banner")
        stream = _capture(logger)
        log_startup(logger, dev_config())
        messages = [record["message"] for record in _records(stream)]
        assert DEV_PROFILE_BANNER in messages

    def test_production_profile_emits_no_banner(self) -> None:
        logger = logging.getLogger("glassbox.test.nobanner")
        stream = _capture(logger)
        log_startup(logger, prod_config())
        assert DEV_PROFILE_BANNER not in [record["message"] for record in _records(stream)]

    def test_disabled_switches_are_reported_at_startup(self) -> None:
        logger = logging.getLogger("glassbox.test.switches")
        stream = _capture(logger)
        log_startup(logger, dev_config(signing=SigningConfig(allow_local_key=True)))
        assert any(
            record.get("unsafe_switches") == ["signing.allow_local_key"]
            for record in _records(stream)
        )


# --------------------------------------------------------------------------- #
# Composition
# --------------------------------------------------------------------------- #


class _Recorder:
    """Counts factory invocations, so ordering guarantees can be asserted."""

    def __init__(self) -> None:
        self.calls: List[str] = []

    def factory_for(self, name: str, component: Any):
        def factory(config: GlassBoxConfig) -> Any:
            self.calls.append(name)
            return component

        return factory


def conforming_adapter_set(recorder: _Recorder | None = None, **overrides: Any) -> AdapterSet:
    """Build a complete adapter set from the in-memory reference implementations."""
    from glassbox.adapters.outbound.memory import memory_adapter_set

    base = memory_adapter_set()
    factories = dict(base.factories)
    if recorder is not None:
        factories = {
            name: recorder.factory_for(name, factory(GlassBoxConfig(profile=RuntimeProfile.DEV)))
            for name, factory in factories.items()
        }
    factories.update(overrides)
    return AdapterSet(name=base.name, factories=factories, dev_only=True)


class TestAdapterSet:
    """An adapter set is validated before it is ever used."""

    def test_missing_factory_is_rejected_by_name(self) -> None:
        with pytest.raises(CompositionError) as excinfo:
            AdapterSet(name="partial", factories={"clock": lambda config: None})
        assert any("missing factory: evidence_store" in item for item in excinfo.value.failures)

    def test_unknown_factory_is_rejected(self) -> None:
        from glassbox.adapters.outbound.memory import memory_adapter_set

        factories = dict(memory_adapter_set().factories)
        factories["telepathy"] = lambda config: None
        with pytest.raises(CompositionError) as excinfo:
            AdapterSet(name="extra", factories=factories, dev_only=True)
        assert "unknown factory: telepathy" in excinfo.value.failures

    def test_non_callable_factory_is_rejected(self) -> None:
        from glassbox.adapters.outbound.memory import memory_adapter_set

        factories = dict(memory_adapter_set().factories)
        factories["clock"] = "not a factory"  # type: ignore[assignment]
        with pytest.raises(CompositionError) as excinfo:
            AdapterSet(name="broken", factories=factories, dev_only=True)
        assert "factory is not callable: clock" in excinfo.value.failures


class TestBuildRuntime:
    """Composition succeeds completely or fails completely."""

    def test_runtime_is_fully_wired(self) -> None:
        runtime = build_runtime(dev_config(), conforming_adapter_set())
        for name, protocol in REQUIRED_COMPONENTS:
            assert isinstance(getattr(runtime, name), protocol)

    def test_runtime_is_immutable(self) -> None:
        runtime = build_runtime(dev_config(), conforming_adapter_set())
        with pytest.raises(Exception):
            runtime.dispatcher = None  # type: ignore[misc]

    def test_required_components_matches_the_runtime_fields(self) -> None:
        """Guards against a port being added without being verified.

        ``workflow_engine`` is the one deliberate exception (Workstream D):
        it is optional, attached post-construction via
        ``with_workflow_engine``, and never conformance-checked by
        ``build_runtime`` -- an adapter set that omits it still builds.
        """
        declared = {name for name, _ in REQUIRED_COMPONENTS}
        fields = {
            field.name
            for field in dataclasses.fields(GovernanceRuntime)
            if field.name not in {"config", "adapter_set_name", "workflow_engine"}
        }
        assert declared == fields

    def test_workflow_engine_is_optional_and_absent_by_default(self) -> None:
        """The optional field must never be part of the conformance-checked set."""
        declared = {name for name, _ in REQUIRED_COMPONENTS}
        assert "workflow_engine" not in declared
        runtime = build_runtime(dev_config(), conforming_adapter_set())
        assert runtime.workflow_engine is None

    def test_description_names_the_adapter_set_and_components(self) -> None:
        described = build_runtime(dev_config(), conforming_adapter_set()).describe()
        assert described["adapter_set"] == "in-memory-reference"
        assert described["components"]["evidence_store"] == "InMemoryEvidenceStore"

    def test_unknown_component_lookup_is_rejected(self) -> None:
        runtime = build_runtime(dev_config(), conforming_adapter_set())
        with pytest.raises(CompositionError):
            runtime.component("telepathy")


class TestProfileEnforcement:
    """A development adapter set can never serve a production profile."""

    def test_dev_only_set_is_refused_by_production(self) -> None:
        with pytest.raises(ProfileViolationError) as excinfo:
            build_runtime(prod_config(), conforming_adapter_set())
        assert "dev_only" in excinfo.value.violations[0]

    def test_no_factory_runs_before_the_profile_check(self) -> None:
        """A dev adapter must not even open a connection against production config."""
        recorder = _Recorder()
        adapters = conforming_adapter_set(recorder)
        recorder.calls.clear()
        with pytest.raises(ProfileViolationError):
            build_runtime(prod_config(), adapters)
        assert recorder.calls == []

    def test_dev_only_set_is_accepted_by_development(self) -> None:
        assert build_runtime(dev_config(), conforming_adapter_set()) is not None


class TestCompositionFailures:
    """Every failure is collected and reported together."""

    def test_non_conforming_component_names_the_missing_members(self) -> None:
        class HalfBakedEvidenceStore:
            def append_intent(self, record: Any) -> Any:
                raise NotImplementedError

        with pytest.raises(CompositionError) as excinfo:
            build_runtime(
                dev_config(),
                conforming_adapter_set(evidence_store=lambda config: HalfBakedEvidenceStore()),
            )
        failure = next(item for item in excinfo.value.failures if item.startswith("evidence_store"))
        assert "does not satisfy EvidenceStore" in failure
        assert "append_outcome" in failure and "verify" in failure

    def test_factory_returning_none_is_reported(self) -> None:
        with pytest.raises(CompositionError) as excinfo:
            build_runtime(dev_config(), conforming_adapter_set(clock=lambda config: None))
        assert "clock: factory returned None" in excinfo.value.failures

    def test_factory_exception_is_reported_not_propagated(self) -> None:
        def exploding(config: GlassBoxConfig) -> Any:
            raise RuntimeError("connection refused")

        with pytest.raises(CompositionError) as excinfo:
            build_runtime(dev_config(), conforming_adapter_set(limit_store=exploding))
        assert any(
            "limit_store: factory raised RuntimeError: connection refused" in item
            for item in excinfo.value.failures
        )

    def test_all_failures_are_collected_in_one_report(self) -> None:
        """An operator sees the whole gap, not one problem per restart."""
        with pytest.raises(CompositionError) as excinfo:
            build_runtime(
                dev_config(),
                conforming_adapter_set(
                    clock=lambda config: None,
                    limit_store=lambda config: None,
                    dispatcher=lambda config: None,
                ),
            )
        assert len(excinfo.value.failures) == 3

    def test_wrong_argument_types_are_refused(self) -> None:
        with pytest.raises(CompositionError):
            build_runtime("not a config", conforming_adapter_set())  # type: ignore[arg-type]
        with pytest.raises(CompositionError):
            build_runtime(dev_config(), "not an adapter set")  # type: ignore[arg-type]
