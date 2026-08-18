"""Postgres connection seam (GB-005).

A deliberately small protocol sits between the evidence store and the database
driver, for three reasons.

* **``psycopg`` stays optional.** The core advertises a zero-mandatory-dependency
  install. Importing this module must not require the driver; only *constructing*
  :class:`PsycopgConnectionProvider` does.
* **The store owns no transaction semantics.** The provider yields a cursor that
  is already inside a transaction and commits or rolls back around it, so the
  store cannot accidentally leave a half-written chain committed.
* **The exact SQL and transaction sequence is testable without a server.** A fake
  provider records statements, which is how the ordering guarantees in this card
  are asserted deterministically rather than hopefully.

Driver errors never escape as driver types: they are translated to
:class:`~glassbox.domain.errors.EvidenceWriteError`, so a caller that fails
closed on the domain exception cannot be surprised by a ``psycopg.OperationalError``
leaking through.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, List, Optional, Protocol, Sequence, Tuple, runtime_checkable

from glassbox.domain.errors import EvidenceWriteError

__all__ = [
    "Cursor",
    "ConnectionProvider",
    "PsycopgConnectionProvider",
    "DriverUnavailableError",
]


class DriverUnavailableError(EvidenceWriteError):
    """The Postgres driver is not installed or a connection cannot be obtained."""

    code = "postgres_driver_unavailable"


@runtime_checkable
class Cursor(Protocol):
    """The minimum cursor surface the evidence store needs."""

    def execute(self, sql: str, params: Sequence[Any] = ()) -> Any:
        """Execute one statement with bound parameters.

        Parameters are always bound, never interpolated. String interpolation
        into SQL is the single most common way an append-only store stops being
        append-only.
        """
        ...

    def fetchone(self) -> Optional[Tuple[Any, ...]]:
        """Return the next row, or ``None``."""
        ...

    def fetchall(self) -> List[Tuple[Any, ...]]:
        """Return all remaining rows."""
        ...


@runtime_checkable
class ConnectionProvider(Protocol):
    """Supplies cursors that are already inside a transaction."""

    def transaction(self) -> Any:
        """Return a context manager yielding a :class:`Cursor`.

        The provider commits when the block exits normally and rolls back when it
        raises. A store must never see a partially applied chain append.
        """
        ...

    def close(self) -> None:
        """Release pooled resources."""
        ...


class PsycopgConnectionProvider:
    """A :class:`ConnectionProvider` backed by a ``psycopg`` connection pool.

    Args:
        dsn: Postgres connection string.
        min_size: Minimum pooled connections.
        max_size: Maximum pooled connections. Bounded, because an unbounded pool
            turns a slow database into a process-wide memory leak.
        connect_timeout_s: How long to wait for a connection before failing
            closed.
        application_name: Reported to Postgres, so evidence writes are
            distinguishable in ``pg_stat_activity``.

    Raises:
        DriverUnavailableError: If ``psycopg`` (with the pool extra) is not
            installed, or the pool cannot be opened.
    """

    __slots__ = ("_pool", "_dsn")

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int = 1,
        max_size: int = 8,
        connect_timeout_s: float = 5.0,
        application_name: str = "glassbox-evidence",
    ) -> None:
        try:
            from psycopg_pool import ConnectionPool
        except ImportError as exc:
            raise DriverUnavailableError(
                "the Postgres evidence store requires the 'postgres' extra",
                remedy="pip install 'glassbox-governance[postgres]'",
            ) from exc

        try:
            self._pool = ConnectionPool(
                conninfo=dsn,
                min_size=min_size,
                max_size=max_size,
                timeout=connect_timeout_s,
                kwargs={"application_name": application_name},
                open=True,
            )
        except Exception as exc:
            raise DriverUnavailableError(
                "could not open the Postgres connection pool",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc
        self._dsn = dsn

    @contextmanager
    def transaction(self) -> Iterator[Cursor]:
        """Yield a cursor inside a transaction, committing or rolling back.

        Raises:
            DriverUnavailableError: If no connection can be obtained. Callers
                fail closed; they do not proceed to an effect.
        """
        try:
            with self._pool.connection() as connection:
                with connection.transaction():
                    with connection.cursor() as cursor:
                        yield cursor
        except EvidenceWriteError:
            raise
        except Exception as exc:
            raise DriverUnavailableError(
                "Postgres transaction failed",
                cause=type(exc).__name__,
                detail=str(exc),
            ) from exc

    def close(self) -> None:
        """Close the pool."""
        self._pool.close()
