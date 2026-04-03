"""
GlassBox Framework — Logging Manager  (v1.0.0)
Structured logging with JSON and text format support, log rotation,
and per-component log levels. Uses Python stdlib logging exclusively.

Features:
  - JSON-structured log lines for machine parsing
  - Human-readable text format for development
  - Rotating file handler with configurable size and backup count
  - Per-component loggers (pipeline, policy, risk, api, audit)
  - PII protection: payload excluded unless explicitly enabled
  - Thread-safe via stdlib logging's built-in locking

Author: Mohammed Akbar Ansari
"""

import json
import logging
import logging.handlers
import os
import threading
from datetime import datetime, timezone
from typing import Any, Dict, Optional


# ── JSON Formatter ────────────────────────────────────────────────────────────

class JsonFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "ts":        datetime.now(timezone.utc).isoformat(),
            "level":     record.levelname,
            "component": getattr(record, "component", "glassbox"),
            "msg":       record.getMessage(),
        }
        if record.exc_info:
            log_obj["exc"] = self.formatException(record.exc_info)
        # Merge any extra fields attached by the caller
        for k, v in record.__dict__.items():
            if k not in ("msg", "args", "created", "filename", "funcName",
                         "levelname", "levelno", "lineno", "module", "msecs",
                         "message", "name", "pathname", "process",
                         "processName", "relativeCreated", "stack_info",
                         "thread", "threadName", "exc_info", "exc_text"):
                if not k.startswith("_"):
                    log_obj[k] = v
        return json.dumps(log_obj, default=str)


# ── GlassBox Logger ───────────────────────────────────────────────────────────

class GlassBoxLogger:
    """
    Central logging façade for the GlassBox framework.
    Maintains one rotating file handler and one stream handler per process.
    All GlassBox components obtain their logger via get_logger().
    """

    _instance: Optional["GlassBoxLogger"] = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        # Singleton: one log manager per process
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(
        self,
        level:           str  = "INFO",
        format:          str  = "json",
        log_dir:         Optional[str] = None,
        max_bytes:       int  = 10 * 1024 * 1024,
        backup_count:    int  = 5,
        include_payload: bool = False,
    ):
        if self._initialized:
            return

        self.level           = getattr(logging, level.upper(), logging.INFO)
        self.format          = format
        self.log_dir         = log_dir
        self.max_bytes       = max_bytes
        self.backup_count    = backup_count
        self.include_payload = include_payload
        self._loggers: Dict[str, logging.Logger] = {}
        self._initialized = True

        self._setup_root()

    def _make_formatter(self) -> logging.Formatter:
        if self.format == "json":
            return JsonFormatter()
        return logging.Formatter(
            fmt="%(asctime)s  %(levelname)-8s  [%(name)s]  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

    def _setup_root(self):
        root = logging.getLogger("glassbox")
        root.setLevel(self.level)
        root.handlers.clear()
        root.propagate = False

        # Console handler
        ch = logging.StreamHandler()
        ch.setFormatter(self._make_formatter())
        ch.setLevel(self.level)
        root.addHandler(ch)

        # Rotating file handler
        if self.log_dir:
            os.makedirs(self.log_dir, exist_ok=True)
            fh = logging.handlers.RotatingFileHandler(
                filename=os.path.join(self.log_dir, "glassbox.log"),
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding="utf-8",
            )
            fh.setFormatter(self._make_formatter())
            fh.setLevel(self.level)
            root.addHandler(fh)

    def get_logger(self, component: str) -> logging.Logger:
        """Return (or create) a child logger for the named component. Thread-safe."""
        # Fast path: already created (read-only, safe after first write)
        if component in self._loggers:
            return self._loggers[component]
        # Slow path: create under lock to prevent concurrent dict mutation
        with self._lock:
            if component not in self._loggers:
                lg = logging.getLogger(f"glassbox.{component}")
                lg.setLevel(self.level)
                self._loggers[component] = lg
            return self._loggers[component]

    def log_decision(
        self,
        component:  str,
        level:      str,
        event:      str,
        decision_id:str,
        agent_id:   str,
        dtype:      str,
        status:     Optional[str]  = None,
        risk_score: Optional[float]= None,
        latency_ms: Optional[float]= None,
        payload:    Optional[Dict] = None,
        extra:      Optional[Dict] = None,
    ):
        """Log a structured decision governance event."""
        lg = self.get_logger(component)
        log_level = getattr(logging, level.upper(), logging.INFO)

        log_data: Dict[str, Any] = {
            "event":       event,
            "decision_id": decision_id,
            "agent_id":    agent_id,
            "dtype":       dtype,
        }
        if status:     log_data["status"]     = status
        if risk_score is not None: log_data["risk_score"] = risk_score
        if latency_ms is not None: log_data["latency_ms"] = round(latency_ms, 3)
        if self.include_payload and payload:
            log_data["payload"] = payload
        if extra:
            log_data.update(extra)

        lg.log(log_level, event, extra=log_data)

    def reconfigure(self, level: str = None, include_payload: bool = None):
        """Reconfigure the log manager at runtime."""
        if level:
            self.level = getattr(logging, level.upper(), self.level)
            logging.getLogger("glassbox").setLevel(self.level)
        if include_payload is not None:
            self.include_payload = include_payload


# ── Module-level convenience ──────────────────────────────────────────────────

_default_manager: Optional[GlassBoxLogger] = None


def setup_logging(
    level:           str  = "INFO",
    format:          str  = "json",
    log_dir:         Optional[str] = None,
    max_bytes:       int  = 10 * 1024 * 1024,
    backup_count:    int  = 5,
    include_payload: bool = False,
) -> GlassBoxLogger:
    """
    Initialise or reconfigure the GlassBox logging subsystem.
    Safe to call multiple times — singleton pattern ensures one manager.
    """
    global _default_manager
    _default_manager = GlassBoxLogger(
        level=level, format=format, log_dir=log_dir,
        max_bytes=max_bytes, backup_count=backup_count,
        include_payload=include_payload,
    )
    return _default_manager


def get_logger(component: str) -> logging.Logger:
    """Get a component logger, initialising defaults if not yet set up."""
    global _default_manager
    if _default_manager is None:
        import os
        level = os.environ.get("GLASSBOX_LOG_LEVEL", "INFO")
        _default_manager = GlassBoxLogger(level=level)
    return _default_manager.get_logger(component)
