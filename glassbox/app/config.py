"""Validated runtime configuration (GB-003).

Configuration is a governance surface. Every setting that can weaken a control is
modelled as an explicit **safety switch** with a safe default, and the production
profile refuses to start when any of them is off. That is deliberate: in v1 the
single most dangerous setting -- ``tenant_scoping_required`` -- defaulted to
``False`` and nothing anywhere objected.

Three rules shape this module.

1. **Safe by default.** Every switch defaults to the strict value. A missing
   environment variable can only make a deployment safer, never weaker.
2. **Fail closed, loudly, with everything.** Validation collects *all* violations
   and raises once. An operator fixes the deployment in a single pass rather than
   discovering one problem per restart.
3. **No silent coercion.** ``GLASSBOX_LIMITS_FAIL_CLOSED=maybe`` is an error, not
   a falsy value. Silent coercion is how a typo disables a control.

There is no third-party validation dependency: the core advertises a
zero-mandatory-dependency install and this module honours it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from enum import Enum
from typing import Any, Dict, List, Mapping, Optional, Tuple, get_type_hints

from glassbox.app.errors import ConfigurationError
from glassbox.domain.serialization import require_identifier

__all__ = [
    "RuntimeProfile",
    "EvidenceConfig",
    "LimitsConfig",
    "BaselineConfig",
    "SigningConfig",
    "IdentityConfig",
    "PolicyConfig",
    "DispatchConfig",
    "ObservabilityConfig",
    "GlassBoxConfig",
    "ENV_PREFIX",
]

#: Prefix for every environment variable read by :meth:`GlassBoxConfig.from_env`.
ENV_PREFIX = "GLASSBOX_"

_TRUE_LITERALS = frozenset({"1", "true", "yes", "on"})
_FALSE_LITERALS = frozenset({"0", "false", "no", "off"})

#: Resolved annotations per section type, populated on first use.
_FIELD_TYPE_CACHE: Dict[type, Dict[str, Any]] = {}


class RuntimeProfile(Enum):
    """Which deployment posture the process is running under.

    ``dev`` exists so the system can be exercised on a laptop. It is *not* a
    weaker production mode: it is loudly labelled, it refuses to be selected
    implicitly, and it provides no assurance whatsoever.
    """

    DEV = "dev"
    PROD = "prod"

    @property
    def permits_dev_only_adapters(self) -> bool:
        """Whether in-memory / non-durable adapters may be wired in."""
        return self is RuntimeProfile.DEV

    @property
    def permits_unsafe_switches(self) -> bool:
        """Whether any safety switch may be turned off."""
        return self is RuntimeProfile.DEV

    @property
    def provides_assurance(self) -> bool:
        """Whether evidence produced under this profile is defensible."""
        return self is RuntimeProfile.PROD


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #


def _parse_bool(raw: str, *, key: str) -> bool:
    """Parse a strict boolean literal.

    Raises:
        ConfigurationError: If ``raw`` is not an accepted literal. Unrecognised
            values are rejected rather than treated as false, because a typo in a
            safety switch must not silently disable a control.
    """
    normalised = raw.strip().lower()
    if normalised in _TRUE_LITERALS:
        return True
    if normalised in _FALSE_LITERALS:
        return False
    raise ConfigurationError(
        "value is not a recognised boolean",
        violations=[f"{key}={raw!r} (expected one of true/false/1/0/yes/no/on/off)"],
        key=key,
    )


def _parse_int(raw: str, *, key: str) -> int:
    """Parse a strict integer literal."""
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            "value is not an integer", violations=[f"{key}={raw!r}"], key=key
        ) from exc


def _parse_float(raw: str, *, key: str) -> float:
    """Parse a strict float literal."""
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(
            "value is not a number", violations=[f"{key}={raw!r}"], key=key
        ) from exc


def _field_types(section_type: type) -> Dict[str, Any]:
    """Return the section's resolved field annotations.

    ``dataclasses.Field.type`` is the *string* ``"bool"`` under
    ``from __future__ import annotations``, so comparing it to the ``bool`` type
    silently never matches and every environment value stays a string. Resolving
    the hints is what makes ``GLASSBOX_LIMITS_FAIL_CLOSED=false`` an actual
    boolean instead of the truthy string ``"false"``.
    """
    cached = _FIELD_TYPE_CACHE.get(section_type)
    if cached is None:
        cached = get_type_hints(section_type)
        _FIELD_TYPE_CACHE[section_type] = cached
    return cached


def _coerce(value: Any, annotation: Any, *, key: str) -> Any:
    """Coerce an environment string into the field's declared type."""
    if not isinstance(value, str):
        return value
    if annotation is bool:
        return _parse_bool(value, key=key)
    if annotation is int:
        return _parse_int(value, key=key)
    if annotation is float:
        return _parse_float(value, key=key)
    return value


# --------------------------------------------------------------------------- #
# Component configuration
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class EvidenceConfig:
    """Evidence store settings.

    Attributes:
        dsn: Connection string for the append-only evidence database.
        segment_prefix: Prefix for generated segment identifiers.
        seal_interval_seconds: How often an open segment is sealed and anchored.
        fsync_required: **Safety switch.** When ``True``, ``append_intent`` may
            only return after a committed, fsynced write (invariant I1).
        worm_anchor_uri: Object-lock destination for sealed Merkle roots.
    """

    dsn: str = ""
    segment_prefix: str = "seg"
    seal_interval_seconds: int = 3600
    fsync_required: bool = True
    worm_anchor_uri: str = ""

    def __post_init__(self) -> None:
        require_identifier(self.segment_prefix, field="evidence.segment_prefix")
        if self.seal_interval_seconds <= 0:
            raise ConfigurationError(
                "seal interval must be positive",
                violations=[f"evidence.seal_interval_seconds={self.seal_interval_seconds}"],
            )


@dataclass(frozen=True, slots=True)
class LimitsConfig:
    """Distributed limit store settings.

    Attributes:
        url: Connection URL for the atomic counter store.
        default_window_seconds: Window applied when a limit declares none.
        fail_closed: **Safety switch.** When ``True``, an unreachable store
            denies every non-advisory action. Turning this off reproduces the v1
            defect in which a Redis outage admitted all traffic.
        cooldown_seconds: How long a tripped breaker stays tripped.
    """

    url: str = ""
    default_window_seconds: int = 60
    fail_closed: bool = True
    cooldown_seconds: int = 300

    def __post_init__(self) -> None:
        violations: List[str] = []
        if self.default_window_seconds <= 0:
            violations.append(f"limits.default_window_seconds={self.default_window_seconds}")
        if self.cooldown_seconds < 0:
            violations.append(f"limits.cooldown_seconds={self.cooldown_seconds}")
        if violations:
            raise ConfigurationError("invalid limit settings", violations=violations)


@dataclass(frozen=True, slots=True)
class BaselineConfig:
    """Behavioural baseline settings.

    Attributes:
        url: Connection URL for the baseline store.
        anomaly_threshold: Standardised deviation above which an observation is
            anomalous.
        min_samples: Observations required before a subject's own baseline is
            used instead of its peer-group prior.
        peer_group_prior_required: **Safety switch.** When ``True``, a subject
            with too little history falls back to a peer-group prior rather than
            skipping detection -- the v1 cold-start bypass.
    """

    url: str = ""
    anomaly_threshold: float = 3.0
    min_samples: int = 30
    peer_group_prior_required: bool = True

    def __post_init__(self) -> None:
        violations: List[str] = []
        if self.anomaly_threshold <= 0:
            violations.append(f"baseline.anomaly_threshold={self.anomaly_threshold}")
        if self.min_samples < 0:
            violations.append(f"baseline.min_samples={self.min_samples}")
        if violations:
            raise ConfigurationError("invalid baseline settings", violations=violations)


@dataclass(frozen=True, slots=True)
class SigningConfig:
    """Evidence MAC signing settings.

    Attributes:
        key_id: Identifier of the signing key, recorded on every evidence row.
        kms_endpoint: Endpoint of the key management service.
        allow_local_key: **Safety switch.** When ``True``, a key readable by the
            application process may be used. In production this is forbidden: a
            key the writer can read makes the chain forgeable by anyone who can
            write a row, which is the v1 defect.
    """

    key_id: str = ""
    kms_endpoint: str = ""
    allow_local_key: bool = False

    def __post_init__(self) -> None:
        if self.key_id:
            require_identifier(self.key_id, field="signing.key_id")


@dataclass(frozen=True, slots=True)
class IdentityConfig:
    """Workload identity settings.

    Attributes:
        trust_domain: SPIFFE trust domain accepted by the verifier.
        oidc_issuer: OIDC issuer accepted by the verifier.
        allow_shared_api_key: **Safety switch.** When ``True``, the v1 single
            shared bearer token is accepted. It cannot identify a tenant, an
            agent or a delegating human, so production forbids it.
        reject_mismatched_assertions: **Safety switch.** When ``True``, a
            transport header such as ``X-Tenant-ID`` that disagrees with the
            verified principal is refused instead of ignored, so the spoofing
            attempt is visible in evidence.
    """

    trust_domain: str = ""
    oidc_issuer: str = ""
    allow_shared_api_key: bool = False
    reject_mismatched_assertions: bool = True


@dataclass(frozen=True, slots=True)
class PolicyConfig:
    """Policy bundle settings.

    Attributes:
        bundle_registry_dsn: Connection string for the signed bundle registry.
        require_signature: **Safety switch.** When ``True``, only ACTIVE,
            signature-verified bundles are loaded.
        deny_on_bundle_unavailable: **Safety switch.** When ``True``, absence of
            a usable bundle denies rather than falls back (invariant I4).
    """

    bundle_registry_dsn: str = ""
    require_signature: bool = True
    deny_on_bundle_unavailable: bool = True


@dataclass(frozen=True, slots=True)
class DispatchConfig:
    """Side-effect dispatch settings.

    Attributes:
        default_timeout_s: Wall-clock budget for a dispatched effect.
        max_in_flight: Bounded concurrency. v1's batch endpoint submitted up to
            500 tasks into the pipeline's own executor, a trivial self-DoS.
        require_evidence_receipt: **Safety switch.** When ``True``, dispatch is
            impossible without a durable receipt (invariant I1).
    """

    default_timeout_s: float = 30.0
    max_in_flight: int = 64
    require_evidence_receipt: bool = True

    def __post_init__(self) -> None:
        violations: List[str] = []
        if self.default_timeout_s <= 0:
            violations.append(f"dispatch.default_timeout_s={self.default_timeout_s}")
        if self.max_in_flight <= 0:
            violations.append(f"dispatch.max_in_flight={self.max_in_flight}")
        if violations:
            raise ConfigurationError("invalid dispatch settings", violations=violations)


@dataclass(frozen=True, slots=True)
class ObservabilityConfig:
    """Logging and telemetry settings."""

    service_name: str = "glassbox"
    log_level: str = "INFO"
    json_logs: bool = True

    def __post_init__(self) -> None:
        require_identifier(self.service_name, field="observability.service_name")
        if self.log_level.upper() not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ConfigurationError(
                "unsupported log level",
                violations=[f"observability.log_level={self.log_level!r}"],
            )


# --------------------------------------------------------------------------- #
# Root configuration
# --------------------------------------------------------------------------- #

#: Every safety switch, as ``(dotted path, value that is safe)``. The production
#: profile refuses to start when any switch is not at its safe value. Adding a
#: new switch means adding it here -- ``tests/test_composition.py`` asserts that
#: every ``bool`` field flagged as a switch in its docstring is present.
SAFETY_SWITCHES: Tuple[Tuple[str, bool], ...] = (
    ("evidence.fsync_required", True),
    ("limits.fail_closed", True),
    ("baseline.peer_group_prior_required", True),
    ("signing.allow_local_key", False),
    ("identity.allow_shared_api_key", False),
    ("identity.reject_mismatched_assertions", True),
    ("policy.require_signature", True),
    ("policy.deny_on_bundle_unavailable", True),
    ("dispatch.require_evidence_receipt", True),
)

#: Settings a production deployment must supply, as ``(dotted path, why)``.
_PROD_REQUIRED: Tuple[Tuple[str, str], ...] = (
    ("evidence.dsn", "evidence must be written to an append-only database"),
    ("evidence.worm_anchor_uri", "sealed segments must be anchored to WORM storage"),
    ("limits.url", "limits must be held outside the process"),
    ("baseline.url", "baselines must be held outside the process"),
    ("signing.key_id", "evidence must be MAC-signed with a managed key"),
    ("policy.bundle_registry_dsn", "policy must be loaded from a signed bundle registry"),
)


@dataclass(frozen=True, slots=True)
class GlassBoxConfig:
    """The validated configuration for one process.

    Construction validates the whole object. A production profile that is missing
    required infrastructure, or that has any safety switch turned off, raises
    :class:`~glassbox.app.errors.ProfileViolationError` listing every problem.
    """

    profile: RuntimeProfile
    evidence: EvidenceConfig = field(default_factory=EvidenceConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    baseline: BaselineConfig = field(default_factory=BaselineConfig)
    signing: SigningConfig = field(default_factory=SigningConfig)
    identity: IdentityConfig = field(default_factory=IdentityConfig)
    policy: PolicyConfig = field(default_factory=PolicyConfig)
    dispatch: DispatchConfig = field(default_factory=DispatchConfig)
    observability: ObservabilityConfig = field(default_factory=ObservabilityConfig)

    def __post_init__(self) -> None:
        from glassbox.app.errors import ProfileViolationError

        if not isinstance(self.profile, RuntimeProfile):
            raise ConfigurationError(
                "profile must be a RuntimeProfile",
                violations=[f"profile={self.profile!r}"],
            )
        if self.profile.permits_unsafe_switches:
            return

        violations: List[str] = [
            f"{path} is required in the {self.profile.value} profile: {reason}"
            for path, reason in _PROD_REQUIRED
            if not self._lookup(path)
        ]
        violations.extend(
            f"{path} must be {safe} in the {self.profile.value} profile"
            for path, safe in SAFETY_SWITCHES
            if self._lookup(path) is not safe
        )
        if violations:
            raise ProfileViolationError(
                f"configuration is not safe for the {self.profile.value} profile",
                violations=violations,
                profile=self.profile.value,
            )

    # ----------------------------------------------------------------- #
    # Introspection
    # ----------------------------------------------------------------- #

    def _lookup(self, path: str) -> Any:
        """Resolve a dotted setting path such as ``limits.fail_closed``."""
        section_name, _, attribute = path.partition(".")
        section = getattr(self, section_name, None)
        if section is None or not hasattr(section, attribute):
            raise ConfigurationError("unknown setting path", violations=[path], path=path)
        return getattr(section, attribute)

    def unsafe_switches(self) -> Tuple[str, ...]:
        """Return every safety switch that is not at its safe value.

        Always empty in production (construction would have failed). In
        development it is the exact list printed in the startup banner, so an
        operator can see which controls are not in force.
        """
        return tuple(path for path, safe in SAFETY_SWITCHES if self._lookup(path) is not safe)

    def describe(self) -> Dict[str, Any]:
        """Return a redacted summary for the startup log and health endpoint.

        Connection strings and endpoints are reported as *configured* or not,
        never by value: a DSN routinely carries a password.
        """
        return {
            "profile": self.profile.value,
            "provides_assurance": self.profile.provides_assurance,
            "service_name": self.observability.service_name,
            "evidence_configured": bool(self.evidence.dsn),
            "worm_anchor_configured": bool(self.evidence.worm_anchor_uri),
            "limits_configured": bool(self.limits.url),
            "baseline_configured": bool(self.baseline.url),
            "signing_key_id": self.signing.key_id or None,
            "policy_registry_configured": bool(self.policy.bundle_registry_dsn),
            "unsafe_switches": list(self.unsafe_switches()),
        }

    # ----------------------------------------------------------------- #
    # Loading
    # ----------------------------------------------------------------- #

    @classmethod
    def from_mapping(cls, values: Mapping[str, Any]) -> "GlassBoxConfig":
        """Build a configuration from a nested mapping.

        Args:
            values: ``{"profile": "prod", "limits": {"url": "redis://..."}}``.

        Raises:
            ConfigurationError: If the profile is missing or unknown, or if a
                section or key is not recognised. Unknown keys are rejected
                rather than ignored, because a silently ignored setting is
                indistinguishable from a control that is switched off.
        """
        raw_profile = values.get("profile")
        if raw_profile is None:
            raise ConfigurationError(
                "profile must be set explicitly",
                violations=["profile is required; there is no default"],
            )
        profile = cls._parse_profile(raw_profile)

        sections: Dict[str, Any] = {"profile": profile}
        known_sections = {declared.name for declared in fields(cls) if declared.name != "profile"}
        unknown = sorted(set(values) - known_sections - {"profile"})
        if unknown:
            raise ConfigurationError(
                "unknown configuration section",
                violations=[f"unknown section: {name}" for name in unknown],
            )

        section_types = {
            "evidence": EvidenceConfig,
            "limits": LimitsConfig,
            "baseline": BaselineConfig,
            "signing": SigningConfig,
            "identity": IdentityConfig,
            "policy": PolicyConfig,
            "dispatch": DispatchConfig,
            "observability": ObservabilityConfig,
        }
        for name, section_type in section_types.items():
            provided = values.get(name, {})
            if not isinstance(provided, Mapping):
                raise ConfigurationError(
                    "configuration section must be a mapping",
                    violations=[f"{name} is {type(provided).__name__}"],
                )
            declared = _field_types(section_type)
            unknown_keys = sorted(set(provided) - set(declared))
            if unknown_keys:
                raise ConfigurationError(
                    "unknown configuration key",
                    violations=[f"unknown key: {name}.{key}" for key in unknown_keys],
                )
            sections[name] = section_type(
                **{
                    key: _coerce(value, declared[key], key=f"{name}.{key}")
                    for key, value in provided.items()
                }
            )
        return cls(**sections)

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "GlassBoxConfig":
        """Build a configuration from environment variables.

        Variables are named ``GLASSBOX_<SECTION>_<KEY>``; the profile is
        ``GLASSBOX_PROFILE``. Unknown ``GLASSBOX_*`` variables are rejected, so a
        misspelled safety switch fails the deployment instead of leaving the
        control silently at its default.

        Args:
            environ: Source mapping. Defaults to ``os.environ``.
        """
        source = os.environ if environ is None else environ
        section_names = tuple(
            declared.name for declared in fields(cls) if declared.name != "profile"
        )

        nested: Dict[str, Any] = {}
        unknown: List[str] = []
        for raw_key, raw_value in source.items():
            if not raw_key.startswith(ENV_PREFIX):
                continue
            remainder = raw_key[len(ENV_PREFIX) :].lower()
            if remainder == "profile":
                nested["profile"] = raw_value
                continue
            for section in section_names:
                prefix = f"{section}_"
                if remainder.startswith(prefix):
                    nested.setdefault(section, {})[remainder[len(prefix) :]] = raw_value
                    break
            else:
                unknown.append(raw_key)

        if unknown:
            raise ConfigurationError(
                "unknown environment variable",
                violations=[f"unrecognised variable: {name}" for name in sorted(unknown)],
            )
        return cls.from_mapping(nested)

    @staticmethod
    def _parse_profile(raw: Any) -> RuntimeProfile:
        """Resolve a profile name, rejecting anything unrecognised."""
        if isinstance(raw, RuntimeProfile):
            return raw
        try:
            return RuntimeProfile(str(raw).strip().lower())
        except ValueError as exc:
            supported = ", ".join(profile.value for profile in RuntimeProfile)
            raise ConfigurationError(
                "unknown runtime profile",
                violations=[f"profile={raw!r} (supported: {supported})"],
            ) from exc
