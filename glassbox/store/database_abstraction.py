"""
GlassBox Framework — Database Abstraction Layer (v1.1.0)
=========================================================

Pluggable database backend supporting:
  - SQLite (embedded, zero-config)
  - PostgreSQL (production, high-throughput)
  - SQL Server (enterprise, Windows native)

Design:
  - Abstract interface (DatabaseBackend)
  - Concrete implementations per DB type
  - Connection pooling for network DBs
  - Query builder for portability
  - Automatic schema migration

Usage:
    from glassbox.store.database_abstraction import DatabaseFactory

    # SQLite (default)
    db = DatabaseFactory.create('sqlite', db_path='/tmp/glassbox.db')

    # PostgreSQL (high-throughput)
    db = DatabaseFactory.create('postgresql',
        host='pg.example.com',
        port=5432,
        database='glassbox',
        user='app',
        password=os.getenv("DB_PASSWORD"),
        pool_size=10,
    )

    # SQL Server
    db = DatabaseFactory.create('sqlserver',
        server='sql.example.com',
        database='glassbox',
        user='app',
        password=os.getenv("DB_PASSWORD"),
        pool_size=10,
    )

    # Use
    db.execute("INSERT INTO audit_records (...) VALUES (...)")
    result = db.query_one("SELECT * FROM audit_records WHERE id=?", (123,))

    # Cleanup
    db.close()

Author: Mohammed Akbar Ansari
"""

import sqlite3
import threading
import time
from abc import ABC, abstractmethod
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from queue import Empty, Queue
from typing import Any, Dict, List, Optional, Tuple

from glassbox.governance.logging_manager import get_logger

log = get_logger("db_abstraction")


class DatabaseBackend(ABC):
    """Abstract database backend interface."""

    @abstractmethod
    def execute(
        self,
        query: str,
        params: Tuple = (),
        commit: bool = True,
    ) -> int:
        """Execute INSERT/UPDATE/DELETE query. Returns affected rows."""
        pass

    @abstractmethod
    def query_one(
        self,
        query: str,
        params: Tuple = (),
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT query, return first row as dict or None."""
        pass

    @abstractmethod
    def query_all(
        self,
        query: str,
        params: Tuple = (),
    ) -> List[Dict[str, Any]]:
        """Execute SELECT query, return all rows as dicts."""
        pass

    @abstractmethod
    def transaction(self):
        """Context manager for transaction block."""
        pass

    @abstractmethod
    def close(self) -> None:
        """Cleanup resources (close connections, etc)."""
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if database is accessible. Returns True if OK."""
        pass

    @abstractmethod
    def get_stats(self) -> Dict[str, Any]:
        """Get connection pool stats, latency, etc."""
        pass


class SQLiteBackend(DatabaseBackend):
    """SQLite backend (embedded, zero-config)."""

    def __init__(
        self,
        db_path: str,
        timeout: float = 5.0,
        enable_wal: bool = True,
        check_same_thread: bool = False,
    ):
        self.db_path = db_path
        self.timeout = timeout
        self.enable_wal = enable_wal

        # SQLite is single-threaded, but we can use context-local connections
        self._tx_connection: ContextVar[Any] = ContextVar(
            f"sqlite_tx_conn_{id(self)}", default=None
        )
        self._tx_depth: ContextVar[int] = ContextVar(f"sqlite_tx_depth_{id(self)}", default=0)
        self._connection: ContextVar[Any] = ContextVar(f"sqlite_conn_{id(self)}", default=None)
        self._lock = threading.Lock()
        self._stats = {"queries": 0, "errors": 0}

    def _set_tx_connection(self, conn) -> None:
        self._tx_connection.set(conn)
        self._tx_depth.set(self._tx_depth.get() + 1)

    def _clear_tx_connection(self) -> None:
        depth = max(0, self._tx_depth.get() - 1)
        self._tx_depth.set(depth)
        if depth == 0:
            self._tx_connection.set(None)

    def _active_tx_connection(self):
        return self._tx_connection.get()

        log.info("SQLiteBackend initialized: db_path=%s, wal=%s", db_path, enable_wal)

    def _get_connection(self):
        """Get context-local connection."""
        tx_conn = self._active_tx_connection()
        if tx_conn is not None:
            return tx_conn
        conn = self._connection.get()
        if conn is None:
            conn = sqlite3.connect(
                self.db_path,
                timeout=self.timeout,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            if self.enable_wal:
                conn.execute("PRAGMA journal_mode=WAL")
            self._connection.set(conn)

        return conn

    def execute(
        self,
        query: str,
        params: Tuple = (),
        commit: bool = True,
    ) -> int:
        """Execute INSERT/UPDATE/DELETE."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(query, params)
            if commit and self._active_tx_connection() is None:
                conn.commit()
            self._stats["queries"] += 1
            return cursor.rowcount
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("SQLiteBackend.execute failed: %s", exc)
            raise

    def query_one(
        self,
        query: str,
        params: Tuple = (),
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT, return first row."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(query, params)
            row = cursor.fetchone()
            self._stats["queries"] += 1
            return dict(row) if row else None
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("SQLiteBackend.query_one failed: %s", exc)
            raise

    def query_all(
        self,
        query: str,
        params: Tuple = (),
    ) -> List[Dict[str, Any]]:
        """Execute SELECT, return all rows."""
        try:
            conn = self._get_connection()
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            self._stats["queries"] += 1
            return [dict(row) for row in rows]
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("SQLiteBackend.query_all failed: %s", exc)
            raise

    @contextmanager
    def transaction(self):
        """Context manager for transaction block."""
        conn = self._get_connection()
        nested = self._active_tx_connection() is conn
        try:
            if not nested:
                conn.execute("BEGIN")
                self._set_tx_connection(conn)
            yield conn
            if not nested:
                conn.commit()
        except Exception as exc:
            if not nested:
                conn.rollback()
            log.error("Transaction failed: %s", exc)
            raise
        finally:
            if not nested:
                self._clear_tx_connection()

    def close(self) -> None:
        """Close context-local connection."""
        conn = self._connection.get()
        if conn:
            conn.close()
            self._connection.set(None)

    def health_check(self) -> bool:
        """Check DB health."""
        try:
            conn = self._get_connection()
            conn.execute("SELECT 1")
            return True
        except Exception as exc:
            log.error("Health check failed: %s", exc)
            return False

    def get_stats(self) -> Dict[str, Any]:
        """Return DB statistics."""
        return {
            "backend": "sqlite",
            "db_path": self.db_path,
            "queries": self._stats["queries"],
            "errors": self._stats["errors"],
            "wal_enabled": self.enable_wal,
        }


class ConnectionPool:
    """Generic connection pool for network databases."""

    def __init__(
        self,
        create_connection_fn,
        pool_size: int = 10,
        timeout: float = 5.0,
    ):
        self.pool: "Queue[Any]" = Queue(maxsize=pool_size)
        self.pool_size = pool_size
        self.timeout = timeout
        self.create_connection_fn = create_connection_fn
        self._lock = threading.Lock()
        self._created = 0
        self._stats = {"gets": 0, "puts": 0, "exhausted": 0}

        # Pre-create connections
        for _ in range(pool_size):
            try:
                conn = create_connection_fn()
                self.pool.put(conn, block=False)
                self._created += 1
            except Exception as exc:
                log.warning("Failed to pre-create connection: %s", exc)

        log.info("ConnectionPool initialized: size=%d, created=%d", pool_size, self._created)

    def get_connection(self):
        """Get connection from pool (or create new)."""
        try:
            conn = self.pool.get(timeout=self.timeout)
            self._stats["gets"] += 1
            return conn
        except Empty:
            # Pool exhausted, try to create temporary connection
            self._stats["exhausted"] += 1
            log.warning(
                "ConnectionPool exhausted, creating temporary connection "
                "(pool_size=%d, gets=%d)",
                self.pool_size,
                self._stats["gets"],
            )
            return self.create_connection_fn()

    def return_connection(self, conn) -> None:
        """Return connection to pool."""
        try:
            self.pool.put(conn, block=False)
            self._stats["puts"] += 1
        except Exception as exc:
            log.warning("Failed to return connection to pool: %s", exc)
            conn.close()

    def close_all(self) -> None:
        """Close all pooled connections."""
        while not self.pool.empty():
            try:
                conn = self.pool.get_nowait()
                conn.close()
            except Exception as exc:
                log.debug("Error closing pooled connection: %s", exc)

        log.info("ConnectionPool closed all %d connections", self._created)

    def get_stats(self) -> Dict[str, Any]:
        """Return pool statistics."""
        return {
            "pool_size": self.pool_size,
            "available": self.pool.qsize(),
            "created": self._created,
            "gets": self._stats["gets"],
            "puts": self._stats["puts"],
            "exhausted": self._stats["exhausted"],
        }


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL backend (high-throughput, production)."""

    def __init__(
        self,
        host: str,
        port: int = 5432,
        database: str = "glassbox",
        user: str = "glassbox",
        password: str = "",
        pool_size: int = 10,
        timeout: float = 5.0,
    ):
        try:
            import psycopg2
            import psycopg2.pool
        except ImportError as exc:
            raise ImportError("PostgreSQL backend requires: pip install psycopg2-binary") from exc

        import warnings as _warnings

        if pool_size < 1:
            raise ValueError("pool_size must be >= 1")
        if pool_size > 500:
            raise ValueError(
                "pool_size > 500 is unreasonable; check PostgreSQL max_connections "
                "(run: SELECT current_setting('max_connections') on your server)"
            )
        if pool_size > 50:
            _warnings.warn(
                f"pool_size={pool_size} may exceed PostgreSQL default max_connections=100. "
                f"Run: SELECT current_setting('max_connections') on your server.",
                ResourceWarning,
                stacklevel=2,
            )

        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.pool_size = pool_size

        def create_conn():
            conn = psycopg2.connect(
                host=host,
                port=port,
                database=database,
                user=user,
                password=password,
                connect_timeout=int(timeout),
            )
            conn.autocommit = False
            return conn

        self.pool = ConnectionPool(create_conn, pool_size, timeout)
        self._stats = {"queries": 0, "errors": 0}
        self._tx_connection: ContextVar[Any] = ContextVar(f"pg_tx_conn_{id(self)}", default=None)

        log.info(
            "PostgreSQLBackend initialized: host=%s:%d, database=%s, pool_size=%d",
            host,
            port,
            database,
            pool_size,
        )

    def execute(
        self,
        query: str,
        params: Tuple = (),
        commit: bool = True,
    ) -> int:
        """Execute INSERT/UPDATE/DELETE."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            affected = cursor.rowcount
            if commit and not self._tx_connection.get():
                conn.commit()
            cursor.close()
            self._stats["queries"] += 1
            return affected
        except Exception as exc:
            conn.rollback()
            self._stats["errors"] += 1
            log.error("PostgreSQLBackend.execute failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    def query_one(
        self,
        query: str,
        params: Tuple = (),
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT, return first row."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            column_names = [desc[0] for desc in cursor.description] if cursor.description else []
            cursor.close()
            self._stats["queries"] += 1

            if not row:
                return None

            # Convert to dict
            return dict(zip(column_names, row))
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("PostgreSQLBackend.query_one failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    def query_all(
        self,
        query: str,
        params: Tuple = (),
    ) -> List[Dict[str, Any]]:
        """Execute SELECT, return all rows."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            column_names = [desc[0] for desc in cursor.description] if cursor.description else []
            cursor.close()
            self._stats["queries"] += 1

            return [dict(zip(column_names, row)) for row in rows]
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("PostgreSQLBackend.query_all failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    @contextmanager
    def transaction(self):
        """Context manager for transaction."""
        conn = self._tx_connection.get()
        nested = conn is not None
        if conn is None:
            conn = self.pool.get_connection()
            self._tx_connection.set(conn)
        try:
            yield conn
            if not nested:
                conn.commit()
        except Exception as exc:
            if not nested:
                conn.rollback()
            log.error("Transaction failed: %s", exc)
            raise
        finally:
            if not nested:
                self._tx_connection.set(None)
                self.pool.return_connection(conn)

    def close(self) -> None:
        """Close connection pool."""
        self.pool.close_all()

    def health_check(self) -> bool:
        """Check DB health."""
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception as exc:
            log.error("Health check failed: %s", exc)
            return False
        finally:
            try:
                self.pool.return_connection(conn)
            except Exception as exc:
                log.debug("Error returning connection to pool: %s", exc)

    def get_stats(self) -> Dict[str, Any]:
        """Return DB statistics."""
        stats = self.pool.get_stats()
        stats.update(
            {
                "backend": "postgresql",
                "host": self.host,
                "database": self.database,
                "queries": self._stats["queries"],
                "errors": self._stats["errors"],
            }
        )
        return stats


class SQLServerBackend(DatabaseBackend):
    """SQL Server backend (enterprise, Windows native)."""

    def __init__(
        self,
        server: str,
        database: str = "glassbox",
        user: str = "sa",
        password: str = "",
        port: int = 1433,
        pool_size: int = 10,
        timeout: float = 5.0,
    ):
        try:
            import pyodbc
        except ImportError as exc:
            raise ImportError("SQL Server backend requires: pip install pyodbc") from exc

        self.server = server
        self.database = database
        self.user = user
        self.port = port
        self.pool_size = pool_size

        def create_conn():
            conn_str = (
                f"Driver={{ODBC Driver 17 for SQL Server}};"
                f"Server={server},{port};"
                f"Database={database};"
                f"UID={user};"
                f"PWD={password};"
                f"Connection Timeout={int(timeout)};"
            )
            conn = pyodbc.connect(conn_str, autocommit=False)
            return conn

        self.pool = ConnectionPool(create_conn, pool_size, timeout)
        self._stats = {"queries": 0, "errors": 0}
        self._tx_connection: ContextVar[Any] = ContextVar(f"mssql_tx_conn_{id(self)}", default=None)

        log.info(
            "SQLServerBackend initialized: server=%s:%d, database=%s, pool_size=%d",
            server,
            port,
            database,
            pool_size,
        )

    def execute(
        self,
        query: str,
        params: Tuple = (),
        commit: bool = True,
    ) -> int:
        """Execute INSERT/UPDATE/DELETE."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            affected = cursor.rowcount
            if commit and not self._tx_connection.get():
                conn.commit()
            cursor.close()
            self._stats["queries"] += 1
            return affected
        except Exception as exc:
            conn.rollback()
            self._stats["errors"] += 1
            log.error("SQLServerBackend.execute failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    def query_one(
        self,
        query: str,
        params: Tuple = (),
    ) -> Optional[Dict[str, Any]]:
        """Execute SELECT, return first row."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            row = cursor.fetchone()
            self._stats["queries"] += 1

            if not row:
                return None

            # Convert to dict
            column_names = [desc[0] for desc in cursor.description]
            return dict(zip(column_names, row))
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("SQLServerBackend.query_one failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    def query_all(
        self,
        query: str,
        params: Tuple = (),
    ) -> List[Dict[str, Any]]:
        """Execute SELECT, return all rows."""
        conn = self._tx_connection.get() or self.pool.get_connection()
        borrowed_from_pool = self._tx_connection.get() is None
        try:
            cursor = conn.cursor()
            cursor.execute(query, params)
            rows = cursor.fetchall()
            self._stats["queries"] += 1

            column_names = [desc[0] for desc in cursor.description]
            return [dict(zip(column_names, row)) for row in rows]
        except Exception as exc:
            self._stats["errors"] += 1
            log.error("SQLServerBackend.query_all failed: %s", exc)
            raise
        finally:
            if borrowed_from_pool:
                self.pool.return_connection(conn)

    @contextmanager
    def transaction(self):
        """Context manager for transaction."""
        conn = self._tx_connection.get()
        nested = conn is not None
        if conn is None:
            conn = self.pool.get_connection()
            self._tx_connection.set(conn)
        try:
            yield conn
            if not nested:
                conn.commit()
        except Exception as exc:
            if not nested:
                conn.rollback()
            log.error("Transaction failed: %s", exc)
            raise
        finally:
            if not nested:
                self._tx_connection.set(None)
                self.pool.return_connection(conn)

    def close(self) -> None:
        """Close connection pool."""
        self.pool.close_all()

    def health_check(self) -> bool:
        """Check DB health."""
        try:
            conn = self.pool.get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            return True
        except Exception as exc:
            log.error("Health check failed: %s", exc)
            return False
        finally:
            try:
                self.pool.return_connection(conn)
            except Exception as exc:
                log.debug("Error returning connection to pool: %s", exc)

    def get_stats(self) -> Dict[str, Any]:
        """Return DB statistics."""
        stats = self.pool.get_stats()
        stats.update(
            {
                "backend": "sqlserver",
                "server": self.server,
                "database": self.database,
                "queries": self._stats["queries"],
                "errors": self._stats["errors"],
            }
        )
        return stats


class DatabaseFactory:
    """Factory for creating database backends."""

    @staticmethod
    def create(backend_type: str, **kwargs) -> DatabaseBackend:
        """
        Create database backend.

        Args:
            backend_type: 'sqlite', 'postgresql', or 'sqlserver'
            **kwargs: Backend-specific configuration

        Returns:
            DatabaseBackend instance
        """
        backend_type = backend_type.lower()

        if backend_type == "sqlite":
            return SQLiteBackend(
                db_path=kwargs.get("db_path", ":memory:"),
                timeout=kwargs.get("timeout", 5.0),
                enable_wal=kwargs.get("enable_wal", True),
            )

        elif backend_type == "postgresql":
            return PostgreSQLBackend(
                host=kwargs.get("host", "localhost"),
                port=kwargs.get("port", 5432),
                database=kwargs.get("database", "glassbox"),
                user=kwargs.get("user", "glassbox"),
                password=kwargs.get("password", ""),
                pool_size=kwargs.get("pool_size", 10),
                timeout=kwargs.get("timeout", 5.0),
            )

        elif backend_type == "sqlserver":
            return SQLServerBackend(
                server=kwargs.get("server", "localhost"),
                database=kwargs.get("database", "glassbox"),
                user=kwargs.get("user", "sa"),
                password=kwargs.get("password", ""),
                port=kwargs.get("port", 1433),
                pool_size=kwargs.get("pool_size", 10),
                timeout=kwargs.get("timeout", 5.0),
            )

        else:
            raise ValueError(
                f"Unknown backend type: {backend_type}. " "Supported: sqlite, postgresql, sqlserver"
            )
