"""Postgres outbound adapters (GB-005).

The durable evidence store: append-only, MAC-chained, and durable **before** any
side effect. This is the production counterpart to
:mod:`glassbox.adapters.outbound.memory`, and it is verified by the same
conformance suite -- ``tests/conformance_evidence.py`` -- so the two cannot drift
apart in semantics the way v1's local and Redis anomaly stores did.

Requires the ``postgres`` extra::

    pip install 'glassbox-governance[postgres]'

Importing this package does **not** require the driver; only constructing
:class:`~glassbox.adapters.outbound.postgres.driver.PsycopgConnectionProvider`
does, which keeps the zero-mandatory-dependency core intact.
"""

from __future__ import annotations

from glassbox.adapters.outbound.postgres.driver import (
    ConnectionProvider,
    Cursor,
    DriverUnavailableError,
    PsycopgConnectionProvider,
)
from glassbox.adapters.outbound.postgres.evidence import PostgresEvidenceStore
from glassbox.adapters.outbound.postgres.schema import (
    MIGRATIONS,
    RETENTION_PURGE_GUC,
    SCHEMA_NOTES,
    SCHEMA_VERSION,
    apply_migrations,
    current_schema_version,
)
from glassbox.app.config import GlassBoxConfig
from glassbox.domain.errors import EvidenceWriteError
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.keys import MacSigner

__all__ = [
    "MIGRATIONS",
    "RETENTION_PURGE_GUC",
    "SCHEMA_NOTES",
    "SCHEMA_VERSION",
    "ConnectionProvider",
    "Cursor",
    "DriverUnavailableError",
    "PostgresEvidenceStore",
    "PsycopgConnectionProvider",
    "apply_migrations",
    "build_evidence_store",
    "current_schema_version",
]


def build_evidence_store(config: GlassBoxConfig, signer: MacSigner) -> EvidenceStore:
    """Factory for the composition root.

    Applies any outstanding migrations before returning, so a process cannot come
    up and start writing evidence against a schema that predates the guarantees
    the store relies on.

    Args:
        config: Validated runtime configuration. ``evidence.dsn`` is required.
        signer: The MAC signer, wired separately so the key can live somewhere
            the application role cannot read.

    Raises:
        EvidenceWriteError: If the DSN is unset, the driver is unavailable, or
            migrations fail. The process must not start.
    """
    if not config.evidence.dsn:
        raise EvidenceWriteError(
            "the Postgres evidence store requires evidence.dsn",
            profile=config.profile.value,
        )
    provider = PsycopgConnectionProvider(config.evidence.dsn)
    apply_migrations(provider)
    return PostgresEvidenceStore(provider, signer)
