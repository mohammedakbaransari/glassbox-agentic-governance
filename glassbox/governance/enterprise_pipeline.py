"""Compatibility wrapper for enterprise-enabled GovernancePipeline.

Enterprise request-context propagation, RBAC gating, and tamper-evident audit
writing now live in ``GovernancePipeline`` itself. This subclass remains as a
stable entry point for callers already depending on the enterprise name.
"""

from __future__ import annotations

from typing import Any

from glassbox.governance.pipeline import GovernancePipeline


class EnterpriseGovernancePipeline(GovernancePipeline):
    """Backward-compatible alias with enterprise constructor keywords."""

    def __init__(
        self,
        access_control=None,  # AccessControl | None
        hash_audit=None,      # TamperEvidentAuditLogger | None
        **kwargs,
    ) -> None:
        super().__init__(
            access_control=access_control,
            hash_audit=hash_audit,
            **kwargs,
        )
