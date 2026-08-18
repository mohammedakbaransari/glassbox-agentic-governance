"""The governed action catalogue (GB-010).

Closes fundamental problem **F1** at its most concrete point. v1 read
``confidence``, ``environment`` and ``agent_chain`` straight out of the request
body (``api/app.py:486``), and its policy rules read self-asserted facts --
``recent_transfer_count``, ``ctr_filed``, ``change_window_approved``,
``prescriber_dea_number``, ``contract_id``, ``legal_review_ref`` -- from the
same caller-controlled payload. An agent that set ``ctr_filed: true`` passed the
currency-transaction-report control by simply saying so.

An :class:`ActionDefinition` is the governed alternative: for a given action
name, it fixes the :class:`~glassbox.domain.action.ConsequenceClass`, how
:class:`~glassbox.domain.action.Exposure` is derived from the parameters the
caller *is* allowed to supply (amounts, destinations -- transactional facts, not
governance verdicts), and which attestations must be resolved from a system of
record rather than asserted. Definitions are versioned into an
:class:`ActionCatalogueBundle` with a content digest, the same shape as the
policy bundle in GB-018, so every decision can cite exactly which catalogue
version governed it.

This module is pure: no I/O, no clock, no third-party imports. Loading a bundle
from storage and resolving attestations against a live system of record are
:mod:`glassbox.ports` concerns.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from enum import Enum
from typing import Any, FrozenSet, Mapping, Optional, Tuple

from glassbox.domain.action import BlastRadius, ConsequenceClass, Exposure
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.serialization import (
    canonical_bytes,
    require_identifier,
    require_non_negative,
)

__all__ = [
    "ParameterType",
    "ParameterField",
    "ExposureRule",
    "ActionDefinition",
    "ActionCatalogueBundle",
]


class ParameterType(Enum):
    """The allowed shapes of a caller-supplied parameter value.

    Deliberately not "any JSON value": GB-029 replaces a regex WAF that pattern-
    matched every string value with an **allow-list** of what an action's
    parameters may even be shaped like. A field absent from an action's schema
    is rejected outright, not scanned for suspicious content.
    """

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"

    def accepts(self, value: Any) -> bool:
        """Whether ``value`` has this parameter's declared shape."""
        if isinstance(value, bool):
            return self is ParameterType.BOOLEAN
        if self is ParameterType.STRING:
            return isinstance(value, str)
        if self is ParameterType.NUMBER:
            return isinstance(value, (int, float))
        if self is ParameterType.INTEGER:
            return isinstance(value, int)
        return False


@dataclass(frozen=True, slots=True)
class ParameterField:
    """One allowed field in an action's parameter schema.

    Attributes:
        name: Parameter key.
        type: Required shape of the value.
        required: Whether the field must be present.
        max_length: For :attr:`ParameterType.STRING` only -- a hard length bound,
            not a content pattern. Bounding length is a resource-exhaustion
            control; it is not the business-logic pattern matching the regex
            WAF performed ("Grupo \u00c1gua & Caf\u00e9 Ltda" is not rejected for
            containing non-ASCII characters).
    """

    name: str
    type: ParameterType
    required: bool = False
    max_length: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", require_identifier(self.name, field="name"))
        if not isinstance(self.type, ParameterType):
            raise DomainValidationError(
                "type must be a ParameterType",
                field="type",
                offending_type=type(self.type).__name__,
            )
        if not isinstance(self.required, bool):
            raise DomainValidationError("required must be a bool", field="required")
        if self.max_length is not None:
            if self.type is not ParameterType.STRING:
                raise DomainValidationError(
                    "max_length only applies to STRING fields", field="max_length"
                )
            require_non_negative(self.max_length, field="max_length")

    def violation(self, parameters: Mapping[str, Any]) -> Optional[str]:
        """Return a description of how ``parameters`` breaks this field, or ``None``."""
        if self.name not in parameters:
            if self.required:
                return f"required parameter {self.name!r} is missing"
            return None
        value = parameters[self.name]
        if not self.type.accepts(value):
            return f"parameter {self.name!r} must be of type {self.type.value}"
        if self.max_length is not None and isinstance(value, str) and len(value) > self.max_length:
            return f"parameter {self.name!r} exceeds max_length={self.max_length}"
        return None

    def as_evidence(self) -> Mapping[str, Any]:
        return {
            "name": self.name,
            "type": self.type.value,
            "required": self.required,
            "max_length": self.max_length,
        }


@dataclass(frozen=True, slots=True)
class ExposureRule:
    """How to compute :class:`~glassbox.domain.action.Exposure` from parameters.

    Deliberately narrow: it names *which* parameter carries the monetary amount
    and the record count, and a fixed blast radius for the action. It cannot
    execute arbitrary code -- unlike v1's 35 Python policy callables -- so a
    catalogue entry cannot be turned into a way to run attacker-influenced logic.

    Attributes:
        monetary_field: Parameter name holding the monetary amount, if any.
        records_field: Parameter name holding an affected-record count, if any.
        blast_radius: Fixed blast radius for every invocation of this action.
    """

    blast_radius: BlastRadius = BlastRadius.SINGLE
    monetary_field: Optional[str] = None
    records_field: Optional[str] = None

    def __post_init__(self) -> None:
        if not isinstance(self.blast_radius, BlastRadius):
            raise DomainValidationError(
                "blast_radius must be a BlastRadius",
                field="blast_radius",
                offending_type=type(self.blast_radius).__name__,
            )
        if self.monetary_field is not None:
            object.__setattr__(
                self,
                "monetary_field",
                require_identifier(self.monetary_field, field="monetary_field"),
            )
        if self.records_field is not None:
            object.__setattr__(
                self, "records_field", require_identifier(self.records_field, field="records_field")
            )

    def extract(self, parameters: Mapping[str, Any]) -> Exposure:
        """Derive exposure from caller-supplied transactional parameters.

        Args:
            parameters: Validated action parameters (amounts, destinations --
                never governance verdicts; those come from an
                ``AttestationProvider`` instead).

        Returns:
            An :class:`~glassbox.domain.action.Exposure`. A configured field
            that is absent or of the wrong type yields an *unknown* magnitude
            (``None``), never a zero -- an unknown amount is never treated as
            small (domain ``Exposure.exceeds`` already fails closed on that).
        """
        monetary = self._numeric_field(parameters, self.monetary_field)
        records = self._integer_field(parameters, self.records_field)
        return Exposure(blast_radius=self.blast_radius, monetary=monetary, records=records)

    @staticmethod
    def _numeric_field(parameters: Mapping[str, Any], field_name: Optional[str]) -> Optional[float]:
        if field_name is None:
            return None
        value = parameters.get(field_name)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return float(value)

    @staticmethod
    def _integer_field(parameters: Mapping[str, Any], field_name: Optional[str]) -> Optional[int]:
        if field_name is None:
            return None
        value = parameters.get(field_name)
        if isinstance(value, bool) or not isinstance(value, int):
            return None
        return int(value)

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation for bundle hashing."""
        return {
            "blast_radius": self.blast_radius.value,
            "monetary_field": self.monetary_field,
            "records_field": self.records_field,
        }


@dataclass(frozen=True, slots=True)
class ActionDefinition:
    """The governed shape of one action.

    Attributes:
        action: Exact action name this definition governs.
        consequence: Fixed consequence class for every invocation.
        exposure_rule: How exposure is derived from parameters.
        required_attestations: Names an ``AttestationProvider`` must resolve to
            ``True`` before the action may proceed. Never resolved from caller
            input -- resolving them is GB-010's other half, alongside this type.
        parameter_schema: The allowed parameter fields for this action (GB-029).
            Empty means no schema is enforced -- an interim state for actions not
            yet migrated, not a licence to accept anything indefinitely. A
            non-empty schema is an allow-list: a key absent from it is rejected,
            not pattern-matched.
        untrusted_text_fields: Names of parameters that hold model- or agent-
            generated free text, as opposed to caller-supplied transactional
            facts. Only these fields are ever passed to the prompt-injection
            control (GB-029) -- a business field such as a supplier name or a
            purchase-order description is never scanned for injection patterns,
            which is what keeps the false-positive rate at zero.
    """

    action: str
    consequence: ConsequenceClass
    exposure_rule: ExposureRule = ExposureRule()
    required_attestations: Tuple[str, ...] = ()
    parameter_schema: Tuple[ParameterField, ...] = ()
    untrusted_text_fields: FrozenSet[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "action", require_identifier(self.action, field="action"))
        if not isinstance(self.consequence, ConsequenceClass):
            raise DomainValidationError(
                "consequence must be a ConsequenceClass",
                field="consequence",
                offending_type=type(self.consequence).__name__,
            )
        if not isinstance(self.exposure_rule, ExposureRule):
            raise DomainValidationError(
                "exposure_rule must be an ExposureRule",
                field="exposure_rule",
                offending_type=type(self.exposure_rule).__name__,
            )
        if not isinstance(self.required_attestations, tuple):
            object.__setattr__(
                self, "required_attestations", tuple(self.required_attestations or ())
            )
        seen = set()
        for name in self.required_attestations:
            require_identifier(name, field="required_attestations")
            if name in seen:
                raise DomainValidationError(
                    "duplicate attestation requirement",
                    field="required_attestations",
                    name=name,
                )
            seen.add(name)
        if not isinstance(self.parameter_schema, tuple):
            object.__setattr__(self, "parameter_schema", tuple(self.parameter_schema or ()))
        field_names = set()
        for parameter_field in self.parameter_schema:
            if not isinstance(parameter_field, ParameterField):
                raise DomainValidationError(
                    "parameter_schema must contain ParameterField instances",
                    field="parameter_schema",
                    offending_type=type(parameter_field).__name__,
                )
            if parameter_field.name in field_names:
                raise DomainValidationError(
                    "duplicate parameter field", field="parameter_schema", name=parameter_field.name
                )
            field_names.add(parameter_field.name)
        if not isinstance(self.untrusted_text_fields, frozenset):
            object.__setattr__(
                self, "untrusted_text_fields", frozenset(self.untrusted_text_fields or ())
            )
        for name in self.untrusted_text_fields:
            require_identifier(name, field="untrusted_text_fields")

    def validate_parameters(self, parameters: Mapping[str, Any]) -> Tuple[str, ...]:
        """Return every way ``parameters`` violates this action's schema.

        An empty :attr:`parameter_schema` validates nothing -- GB-029 replaces a
        regex WAF that scanned every payload with an explicit, per-action
        allow-list; an action not yet migrated to one is not silently blocked.
        A non-empty schema rejects any key it does not name, so an unmapped
        parameter is refused the same way GB-013 refuses an unmapped tool.
        """
        if not self.parameter_schema:
            return ()
        allowed = {field.name for field in self.parameter_schema}
        violations = [
            f"parameter {key!r} is not in this action's schema"
            for key in parameters
            if key not in allowed
        ]
        for parameter_field in self.parameter_schema:
            violation = parameter_field.violation(parameters)
            if violation is not None:
                violations.append(violation)
        return tuple(violations)

    def as_evidence(self) -> Mapping[str, Any]:
        """Return the canonical representation for bundle hashing."""
        return {
            "action": self.action,
            "consequence": self.consequence.value,
            "exposure_rule": dict(self.exposure_rule.as_evidence()),
            "required_attestations": list(self.required_attestations),
            "parameter_schema": [field.as_evidence() for field in self.parameter_schema],
            "untrusted_text_fields": sorted(self.untrusted_text_fields),
        }


@dataclass(frozen=True, slots=True)
class ActionCatalogueBundle:
    """A versioned, attributable set of action definitions.

    Attributes:
        bundle_id: Identifier of this catalogue version.
        tenant_id: Owning tenant.
        version: Monotonic version.
        definitions: Every governed action, keyed by name.
    """

    bundle_id: str
    tenant_id: str
    version: int
    definitions: Tuple[ActionDefinition, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "bundle_id", require_identifier(self.bundle_id, field="bundle_id"))
        object.__setattr__(self, "tenant_id", require_identifier(self.tenant_id, field="tenant_id"))
        if isinstance(self.version, bool) or not isinstance(self.version, int):
            raise DomainValidationError("version must be an integer", field="version")
        require_non_negative(self.version, field="version")
        if not isinstance(self.definitions, tuple):
            object.__setattr__(self, "definitions", tuple(self.definitions or ()))
        seen = set()
        for definition in self.definitions:
            if not isinstance(definition, ActionDefinition):
                raise DomainValidationError(
                    "definitions must contain ActionDefinition instances",
                    field="definitions",
                    offending_type=type(definition).__name__,
                )
            if definition.action in seen:
                raise DomainValidationError(
                    "duplicate action definition", field="definitions", action=definition.action
                )
            seen.add(definition.action)

    def resolve(self, action: str) -> Optional[ActionDefinition]:
        """Return the definition for ``action``, or ``None`` if ungoverned."""
        for definition in self.definitions:
            if definition.action == action:
                return definition
        return None

    def digest(self) -> str:
        """Return the SHA-256 digest of this bundle's content.

        Cited on every evidence row that used it, the same as a policy bundle,
        so a decision is attributable to the exact catalogue version in force.
        """
        payload = {
            "bundle_id": self.bundle_id,
            "tenant_id": self.tenant_id,
            "version": self.version,
            "definitions": [dict(item.as_evidence()) for item in self.definitions],
        }
        return hashlib.sha256(canonical_bytes(payload)).hexdigest()
