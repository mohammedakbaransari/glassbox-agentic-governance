"""Tests for the governed tool registry domain types (GB-013)."""

from __future__ import annotations

import pytest

from glassbox.domain.action import ConsequenceClass
from glassbox.domain.catalogue import ActionDefinition, ExposureRule
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.tool_registry import ToolDefinition, ToolRegistryBundle

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64


def _definition(*, tool_name: str = "mcp.send_email", digest: str = DIGEST_A) -> ToolDefinition:
    return ToolDefinition(
        tool_name=tool_name,
        definition_sha256=digest,
        action=ActionDefinition(
            action="mcp.send_email",
            consequence=ConsequenceClass.REVERSIBLE,
            exposure_rule=ExposureRule(),
        ),
    )


class TestToolDefinition:
    def test_normalises_the_digest_to_lowercase(self) -> None:
        definition = _definition(digest=DIGEST_A.upper())
        assert definition.definition_sha256 == DIGEST_A

    def test_rejects_a_non_action_definition(self) -> None:
        with pytest.raises(DomainValidationError):
            ToolDefinition(tool_name="a", definition_sha256=DIGEST_A, action="not-an-action")  # type: ignore[arg-type]

    def test_as_evidence_is_a_plain_serialisable_mapping(self) -> None:
        definition = _definition()
        evidence = definition.as_evidence()
        assert evidence["tool_name"] == "mcp.send_email"
        assert evidence["definition_sha256"] == DIGEST_A
        assert evidence["action"]["consequence"] == "reversible"


class TestToolRegistryBundle:
    def test_resolve_returns_none_for_an_unregistered_tool(self) -> None:
        bundle = ToolRegistryBundle(bundle_id="b", tenant_id="acme", version=1)
        assert bundle.resolve("mcp.send_email") is None

    def test_resolve_finds_the_matching_definition(self) -> None:
        definition = _definition()
        bundle = ToolRegistryBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(definition,)
        )
        assert bundle.resolve("mcp.send_email") is definition

    def test_rejects_duplicate_tool_definitions(self) -> None:
        one = _definition(digest=DIGEST_A)
        two = _definition(digest=DIGEST_B)
        with pytest.raises(DomainValidationError):
            ToolRegistryBundle(bundle_id="b", tenant_id="acme", version=1, definitions=(one, two))

    def test_digest_changes_when_a_definition_changes(self) -> None:
        base = ToolRegistryBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(_definition(digest=DIGEST_A),)
        )
        changed = ToolRegistryBundle(
            bundle_id="b", tenant_id="acme", version=1, definitions=(_definition(digest=DIGEST_B),)
        )
        assert base.digest() != changed.digest()
