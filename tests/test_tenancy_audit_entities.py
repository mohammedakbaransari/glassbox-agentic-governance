"""Tests for the first-class ``Tenant`` and ``AuditEvent`` domain entities (P3)."""

from __future__ import annotations

import pytest

from glassbox.domain.audit_event import AuditEvent
from glassbox.domain.errors import DomainValidationError
from glassbox.domain.tenancy import Tenant, TenantStatus

NOW = 1_700_000_000.0


class TestTenant:
    def test_a_valid_tenant_round_trips_through_as_evidence(self) -> None:
        tenant = Tenant.create("acme", "Acme Corp", created_at=NOW, metadata={"tier": "gold"})
        evidence = tenant.as_evidence()
        assert evidence["tenant_id"] == "acme"
        assert evidence["status"] == "pending"
        assert evidence["metadata"] == {"tier": "gold"}

    def test_an_empty_tenant_id_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            Tenant.create("", "Acme Corp", created_at=NOW)

    def test_with_status_returns_a_new_immutable_instance(self) -> None:
        tenant = Tenant.create("acme", "Acme Corp", created_at=NOW)
        active = tenant.with_status(TenantStatus.ACTIVE)
        assert tenant.status is TenantStatus.PENDING
        assert active.status is TenantStatus.ACTIVE

    def test_only_active_tenants_permit_new_decisions(self) -> None:
        assert TenantStatus.ACTIVE.permits_new_decisions is True
        for status in (TenantStatus.PENDING, TenantStatus.SUSPENDED, TenantStatus.OFFBOARDED):
            assert status.permits_new_decisions is False

    def test_the_entity_is_frozen(self) -> None:
        tenant = Tenant.create("acme", "Acme Corp", created_at=NOW)
        with pytest.raises(AttributeError):
            tenant.display_name = "Other"  # type: ignore[misc]


class TestAuditEvent:
    def test_a_valid_event_round_trips_through_as_evidence(self) -> None:
        event = AuditEvent.create(
            "evt-1",
            "tenant.onboarded",
            occurred_at=NOW,
            actor="admin@acme.com",
            tenant_id="acme",
            detail={"plan": "enterprise"},
        )
        evidence = event.as_evidence()
        assert evidence["event_type"] == "tenant.onboarded"
        assert evidence["detail"] == {"plan": "enterprise"}

    def test_an_empty_event_type_is_refused(self) -> None:
        with pytest.raises(DomainValidationError):
            AuditEvent.create("evt-1", "", occurred_at=NOW, actor="system")

    def test_tenant_id_and_subject_id_are_optional(self) -> None:
        event = AuditEvent.create("evt-1", "system.startup", occurred_at=NOW, actor="system")
        assert event.tenant_id is None
        assert event.subject_id is None

    def test_the_entity_is_frozen(self) -> None:
        event = AuditEvent.create("evt-1", "system.startup", occurred_at=NOW, actor="system")
        with pytest.raises(AttributeError):
            event.actor = "other"  # type: ignore[misc]
