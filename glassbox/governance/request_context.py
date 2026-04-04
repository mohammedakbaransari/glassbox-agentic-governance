"""
GlassBox Framework — Request Context & Configuration (v1.1.0)
==============================================================

Request-scoped context management and enterprise configuration:
  - Context thread-local storage (user, tenant, request metadata)
  - Distributed request tracing (X-Request-ID, X-Correlation-ID)
  - Multi-tenant context isolation
  - Configuration management (environment, files, defaults)
  - Secrets management via environment variables or secure vaults

Design:
  - RequestContext captures: user, tenant, request ID, timestamp, metadata
  - Context is isolated to request thread (no cross-request bleeding)
  - Configuration supports: env vars, YAML/JSON files, defaults
  - Secrets loaded from env or external vaults (HashiCorp Vault, etc.)

Usage:
    from glassbox.governance.request_context import RequestContext, Config
    
    # Set request context
    ctx = RequestContext(
        request_id="req-123",
        user_id="user456",
        tenant_id="tenant789",
        correlation_id="corr-123"
    )
    RequestContext.set_current(ctx)
    
    # Access in any thread context
    current_ctx = RequestContext.get_current()
    print(current_ctx.user_id, current_ctx.tenant_id)
    
    # Load configuration
    config = Config.load("/etc/glassbox/config.yaml")
    db_url = config.get("database.url", default="sqlite:///:memory:")
    
    # Get secrets from environment
    api_key = config.get_secret("api_key", env_var="GLASSBOX_API_KEY")

Author: Mohammed Akbar Ansari
"""

import os
import json
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from glassbox.governance.logging_manager import get_logger

log = get_logger("request_context")


@dataclass
class RequestContext:
    """Request-scoped context information."""

    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    tenant_id: Optional[str] = None
    correlation_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    session_id: Optional[str] = None
    impersonated_by: Optional[str] = None

    _local = threading.local()

    @classmethod
    def set_current(cls, ctx: "RequestContext") -> None:
        """Set current request context (thread-local)."""
        cls._local.context = ctx

    @classmethod
    def get_current(cls) -> "RequestContext":
        """Get current request context (thread-local)."""
        if not hasattr(cls._local, "context"):
            # Auto-create default context for new threads
            cls._local.context = RequestContext()

        return cls._local.context

    @classmethod
    def clear_current(cls) -> None:
        """Clear current request context."""
        if hasattr(cls._local, "context"):
            delattr(cls._local, "context")

    def get_trace_id(self) -> str:
        """Get trace ID for distributed tracing (X-Trace-ID header)."""
        return self.correlation_id or self.request_id

    def add_metadata(self, key: str, value: Any) -> None:
        """Add metadata to request context."""
        self.metadata[key] = value

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Get metadata from request context."""
        return self.metadata.get(key, default)

    def to_dict(self) -> Dict[str, Any]:
        """Convert context to dictionary for logging/serialization."""
        return {
            "request_id": self.request_id,
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "correlation_id": self.correlation_id,
            "session_id": self.session_id,
            "impersonated_by": self.impersonated_by,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }

    def __repr__(self) -> str:
        return (
            f"RequestContext(request_id={self.request_id}, "
            f"user_id={self.user_id}, tenant_id={self.tenant_id})"
        )


class Config:
    """Enterprise configuration management."""

    def __init__(self):
        self.data: Dict[str, Any] = {}
        self._lock = threading.RLock()

    @staticmethod
    def load(config_path: Optional[str] = None) -> "Config":
        """
        Load configuration from file or environment.

        Supports: .yaml, .yml, .json

        Precedence:
            1. Environment variable "GLASSBOX_CONFIG_PATH" if set
            2. Provided config_path
            3. Default locations: /etc/glassbox/config.yaml
        """
        config = Config()

        # Determine config path
        path = (
            os.getenv("GLASSBOX_CONFIG_PATH")
            or config_path
            or "/etc/glassbox/config.yaml"
        )

        # Try to load
        if os.path.exists(path):
            try:
                if path.endswith(".json"):
                    with open(path, "r") as f:
                        config.data = json.load(f)
                    log.info("Loaded config from JSON: %s", path)

                elif path.endswith((".yaml", ".yml")):
                    try:
                        import yaml
                        with open(path, "r") as f:
                            config.data = yaml.safe_load(f) or {}
                        log.info("Loaded config from YAML: %s", path)
                    except ImportError:
                        log.warning(
                            "YAML support requires: pip install pyyaml"
                        )

                else:
                    log.warning("Unknown config file type: %s", path)

            except Exception as exc:
                log.error("Failed to load config from %s: %s", path, exc)

        else:
            log.warning("Config file not found: %s", path)

        return config

    def get(
        self,
        key: str,
        default: Any = None,
        env_var: Optional[str] = None,
    ) -> Any:
        """
        Get configuration value.

        Supports dot notation: "database.url"
        Precedence:
            1. Environment variable (if env_var provided)
            2. Config file value
            3. Default value
        """
        # Check environment variable first
        if env_var and env_var in os.environ:
            return os.environ[env_var]

        # Navigate config using dot notation
        keys = key.split(".")
        value = self.data

        try:
            for k in keys:
                value = value[k]
            return value
        except (KeyError, TypeError):
            return default

    def get_secret(
        self,
        key: str,
        env_var: Optional[str] = None,
        default: Any = None,
    ) -> Optional[str]:
        """
        Get secret value (never logs the value for security).

        Checks: environment variable first, then config file.
        """
        # Check environment variable first (recommended for secrets)
        if env_var:
            secret = os.getenv(env_var)
            if secret:
                log.info("Loaded secret from env var: %s", env_var)
                return secret

        # Check config file (not recommended for secrets)
        value = self.get(key, default=None)
        if value:
            log.warning(
                "WARNING: Secret loaded from config file (not env var): %s. "
                "Use environment variables for better security.",
                key
            )
            return value

        return default

    def set(self, key: str, value: Any) -> None:
        """Set configuration value (runtime override)."""
        with self._lock:
            keys = key.split(".")
            current = self.data

            # Navigate to parent and set
            for k in keys[:-1]:
                if k not in current:
                    current[k] = {}
                current = current[k]

            current[keys[-1]] = value
            log.debug("Config set: %s = %s", key, value)

    def get_section(self, section: str) -> Dict[str, Any]:
        """Get entire config section."""
        return self.get(section, default={})

    def to_dict(self) -> Dict[str, Any]:
        """Export configuration as dictionary."""
        with self._lock:
            return dict(self.data)

    def __repr__(self) -> str:
        return f"Config(keys={len(self.data)})"


class ContextManager:
    """Helper for managing request context lifecycle."""

    def __init__(
        self,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        correlation_id: Optional[str] = None,
    ):
        self.user_id = user_id
        self.tenant_id = tenant_id
        self.correlation_id = correlation_id
        self.previous_context: Optional[RequestContext] = None

    def __enter__(self) -> RequestContext:
        """Enter context manager."""
        # Save previous context
        self.previous_context = (
            RequestContext.get_current()
            if hasattr(RequestContext._local, "context")
            else None
        )

        # Create and set new context
        ctx = RequestContext(
            user_id=self.user_id,
            tenant_id=self.tenant_id,
            correlation_id=self.correlation_id,
        )
        RequestContext.set_current(ctx)

        log.debug("Context entered: %s", ctx)
        return ctx

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Exit context manager."""
        if self.previous_context:
            RequestContext.set_current(self.previous_context)
            log.debug("Context restored: %s", self.previous_context)
        else:
            RequestContext.clear_current()
            log.debug("Context cleared")

        return False  # Don't suppress exceptions
