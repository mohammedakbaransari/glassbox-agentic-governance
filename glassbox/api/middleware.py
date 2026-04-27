"""
GlassBox Framework — Flask-Native Middleware (v1.1.0)
=====================================================

Flask-native equivalents of the middleware defined in
glassbox/governance/api_gateway.py, wired directly into Flask's
before_request / after_request hooks.

This is the authoritative HTTP middleware for the REST API (glassbox/api/app.py).
api_gateway.py remains available as an alternative non-Flask (ASGI / Lambda)
HTTP layer for deployments that cannot use Flask.

Usage:
    from glassbox.api.middleware import RateLimitMiddleware, require_auth

    rate_limiter = RateLimitMiddleware(requests_per_minute=1000)
    rate_limiter.init_app(app)

    @app.route("/decisions", methods=["POST"])
    @require_auth
    def submit():
        ...
"""
from __future__ import annotations

import hmac
import threading
import time
from functools import wraps
from typing import Callable

from flask import Flask, abort, g, jsonify, request

from glassbox.governance.logging_manager import get_logger

log = get_logger("api.middleware")


class RateLimitMiddleware:
    """
    Flask sliding-window rate limiter.

    Registered via init_app() and enforced on every request via
    app.before_request.  Each key (X-User-ID header, falling back to
    remote_addr) gets its own 60-second sliding window.

    Thread-safe: internal state protected by a single RLock.
    Memory-safe: stale windows are evicted lazily on each check.
    """

    def __init__(self, app: Flask | None = None, requests_per_minute: int = 1000):
        self._rpm = requests_per_minute
        # key -> (window_start: float, count: int)
        self._windows: dict[str, tuple[float, int]] = {}
        self._lock = threading.RLock()
        if app is not None:
            self.init_app(app)

    def init_app(self, app: Flask) -> None:
        """Register middleware with a Flask application."""
        app.before_request(self._check)

    def _check(self) -> None:
        """before_request hook — aborts 429 if rate exceeded."""
        key = request.headers.get("X-User-ID") or request.remote_addr or "unknown"
        now = time.monotonic()

        with self._lock:
            start, count = self._windows.get(key, (now, 0))
            if now - start > 60.0:
                # Window expired — reset
                start, count = now, 0
            count += 1
            self._windows[key] = (start, count)

        if count > self._rpm:
            log.warning(
                "Rate limit exceeded",
                extra={"component": "api.middleware", "key": key, "count": count},
            )
            abort(429, description=f"Rate limit exceeded ({self._rpm} req/min)")


def require_auth(secret_key: str | None = None) -> Callable:
    """
    Flask endpoint decorator that enforces Bearer token authentication.

    Uses hmac.compare_digest() for constant-time comparison — prevents
    timing oracle attacks on the token value.

    Usage (static token):
        from glassbox.api.middleware import require_auth

        @app.route("/decisions", methods=["POST"])
        @require_auth(secret_key=os.environ["GLASSBOX_API_KEY"])
        def submit():
            ...

    The validated token is available as g.token inside the decorated view.
    """
    def decorator(f: Callable) -> Callable:
        @wraps(f)
        def decorated(*args, **kwargs):
            auth = request.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                log.warning(
                    "Missing or malformed Authorization header",
                    extra={"component": "api.middleware", "path": request.path},
                )
                abort(401, description="Missing Bearer token")
            token = auth[7:]
            if secret_key is not None:
                # Constant-time comparison — prevents timing oracle attacks
                if not hmac.compare_digest(token.encode(), secret_key.encode()):
                    log.warning(
                        "Invalid Bearer token",
                        extra={"component": "api.middleware", "path": request.path},
                    )
                    abort(401, description="Invalid token")
            g.token = token
            return f(*args, **kwargs)
        return decorated
    return decorator


def add_cors_headers(app: Flask, allowed_origins: str = "*") -> None:
    """
    Register an after_request hook that adds CORS headers.

    Call once during app creation:
        add_cors_headers(app, allowed_origins="https://dashboard.example.com")
    """
    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = allowed_origins
        resp.headers["Access-Control-Allow-Headers"] = (
            "Content-Type, Authorization, X-Request-ID, X-User-ID"
        )
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp
