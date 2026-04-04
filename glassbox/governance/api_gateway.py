"""
GlassBox Framework — API Gateway & Middleware (v1.1.0)
======================================================

Enterprise API gateway with:
  - Request/response middleware pipeline
  - Authentication & authorization checks
  - Rate limiting per user/endpoint
  - Request/response logging and tracing
  - Request validation (JSON schema)
  - CORS support
  - Error standardization

Design:
  - Middleware layers: Auth -> Validation -> Rate Limit -> Business Logic -> Logging
  - Extensible middleware system (add custom middleware)
  - Request context propagation
  - Distributed tracing headers (X-Request-ID, X-Correlation-ID)
  - Standardized error responses

Usage:
    from glassbox.governance.api_gateway import APIGateway, Middleware
    
    # Create gateway
    gateway = APIGateway()
    
    # Add middleware
    gateway.add_middleware(AuthenticationMiddleware())
    gateway.add_middleware(RateLimitMiddleware(requests_per_minute=100))
    gateway.add_middleware(RequestValidationMiddleware())
    
    # Route request
    response = gateway.handle_request(
        method="POST",
        path="/api/policies",
        body={"name": "my_policy"},
        headers={"Authorization": "Bearer token123"}
    )

Author: Mohammed Akbar Ansari
"""

import json
import time
import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Callable, Dict, List, Optional
from collections import defaultdict

from glassbox.governance.logging_manager import get_logger
from glassbox.governance.request_context import RequestContext

log = get_logger("api_gateway")


@dataclass
class Request:
    """HTTP request object."""

    method: str
    path: str
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    query_params: Dict[str, str] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class Response:
    """HTTP response object."""

    status_code: int
    headers: Dict[str, str] = field(default_factory=dict)
    body: Any = None
    error: Optional[str] = None


class Middleware(ABC):
    """Base middleware class."""

    @abstractmethod
    def process_request(self, request: Request) -> Optional[Response]:
        """
        Process request before handler.

        Return Response to short-circuit, None to continue.
        """
        pass

    @abstractmethod
    def process_response(self, request: Request, response: Response) -> Response:
        """Process response after handler."""
        pass


class AuthenticationMiddleware(Middleware):
    """Authenticate requests via Authorization header."""

    def __init__(self, secret_key: str = "secret"):
        self.secret_key = secret_key

    def process_request(self, request: Request) -> Optional[Response]:
        """Check Authorization header."""
        auth_header = request.headers.get("Authorization", "")

        if not auth_header:
            return Response(
                status_code=401,
                error="Missing Authorization header"
            )

        # Simple token validation (in production, use JWT)
        if not auth_header.startswith("Bearer "):
            return Response(
                status_code=401,
                error="Invalid Authorization header format"
            )

        token = auth_header[7:]  # Remove "Bearer "

        # Validate token (simplified; use JWT in production)
        if token != self.secret_key:
            return Response(
                status_code=401,
                error="Invalid token"
            )

        # Extract user info from token (mock)
        ctx = RequestContext.get_current()
        ctx.user_id = "user_from_token"

        return None  # Continue

    def process_response(self, request: Request, response: Response) -> Response:
        """No post-processing."""
        return response


class RateLimitMiddleware(Middleware):
    """Rate limit requests per user."""

    def __init__(self, requests_per_minute: int = 100):
        self.requests_per_minute = requests_per_minute
        self.request_history: Dict[str, List[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def process_request(self, request: Request) -> Optional[Response]:
        """Check rate limit."""
        ctx = RequestContext.get_current()
        user_id = ctx.user_id or "anonymous"

        with self._lock:
            now = time.time()
            cutoff = now - 60  # Last minute

            # Clean old entries
            if user_id in self.request_history:
                self.request_history[user_id] = [
                    t for t in self.request_history[user_id] if t > cutoff
                ]

            # Check limit
            if len(self.request_history[user_id]) >= self.requests_per_minute:
                return Response(
                    status_code=429,
                    error=f"Rate limit exceeded (max {self.requests_per_minute}/min)"
                )

            # Record request
            self.request_history[user_id].append(now)

        return None  # Continue

    def process_response(self, request: Request, response: Response) -> Response:
        """No post-processing."""
        return response


class RequestValidationMiddleware(Middleware):
    """Validate request format (JSON, required fields, etc.)."""

    def __init__(self):
        self.validators: Dict[str, Callable[[Request], bool]] = {}

    def register_validator(
        self,
        path_pattern: str,
        validator: Callable[[Request], bool],
    ) -> None:
        """Register validator for path pattern."""
        self.validators[path_pattern] = validator

    def process_request(self, request: Request) -> Optional[Response]:
        """Validate request."""
        # Validate JSON
        if request.method in ["POST", "PUT", "PATCH"]:
            if isinstance(request.body, str):
                try:
                    json.loads(request.body)
                except json.JSONDecodeError:
                    return Response(
                        status_code=400,
                        error="Invalid JSON body"
                    )

        # Run registered validators
        for pattern, validator in self.validators.items():
            if pattern in request.path:
                if not validator(request):
                    return Response(
                        status_code=400,
                        error=f"Validation failed for {pattern}"
                    )

        return None  # Continue

    def process_response(self, request: Request, response: Response) -> Response:
        """No post-processing."""
        return response


class RequestLoggingMiddleware(Middleware):
    """Log all requests and responses."""

    def process_request(self, request: Request) -> Optional[Response]:
        """Log incoming request."""
        ctx = RequestContext.get_current()

        log.info(
            "Incoming request: %s %s [user=%s, tenant=%s, req_id=%s]",
            request.method, request.path, ctx.user_id, ctx.tenant_id, ctx.request_id
        )

        return None

    def process_response(self, request: Request, response: Response) -> Response:
        """Log outgoing response."""
        ctx = RequestContext.get_current()

        log.info(
            "Outgoing response: %s %s -> %d [req_id=%s]",
            request.method, request.path, response.status_code, ctx.request_id
        )

        return response


class CORSMiddleware(Middleware):
    """Handle CORS (Cross-Origin Resource Sharing)."""

    def __init__(
        self,
        allowed_origins: Optional[List[str]] = None,
        allowed_methods: Optional[List[str]] = None,
        allowed_headers: Optional[List[str]] = None,
    ):
        self.allowed_origins = allowed_origins or ["*"]
        self.allowed_methods = allowed_methods or [
            "GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"
        ]
        self.allowed_headers = allowed_headers or [
            "Content-Type", "Authorization", "X-Request-ID"
        ]

    def process_request(self, request: Request) -> Optional[Response]:
        """Handle CORS preflight requests."""
        if request.method == "OPTIONS":
            return Response(
                status_code=200,
                headers=self._get_cors_headers(request),
            )

        return None

    def process_response(self, request: Request, response: Response) -> Response:
        """Add CORS headers to response."""
        response.headers.update(self._get_cors_headers(request))
        return response

    def _get_cors_headers(self, request: Request) -> Dict[str, str]:
        """Generate CORS headers."""
        origin = request.headers.get("Origin", "")
        if origin in self.allowed_origins or "*" in self.allowed_origins:
            return {
                "Access-Control-Allow-Origin": origin or "*",
                "Access-Control-Allow-Methods": ", ".join(self.allowed_methods),
                "Access-Control-Allow-Headers": ", ".join(self.allowed_headers),
            }

        return {}


class APIGateway:
    """Main API gateway."""

    def __init__(self):
        self.middleware_stack: List[Middleware] = []
        self.routes: Dict[str, Callable] = {}
        self._lock = threading.Lock()

        log.info("APIGateway initialized")

    def add_middleware(self, middleware: Middleware) -> None:
        """Add middleware to stack."""
        with self._lock:
            self.middleware_stack.append(middleware)
            log.info("Middleware added: %s", middleware.__class__.__name__)

    def register_route(
        self,
        method: str,
        path: str,
        handler: Callable[[Request], Response],
    ) -> None:
        """Register request handler."""
        key = f"{method} {path}"
        with self._lock:
            self.routes[key] = handler
            log.info("Route registered: %s", key)

    def handle_request(
        self,
        method: str,
        path: str,
        headers: Optional[Dict[str, str]] = None,
        body: Any = None,
        query_params: Optional[Dict[str, str]] = None,
    ) -> Response:
        """
        Process HTTP request through middleware pipeline.

        Returns: Response object
        """
        # Create request and context
        request = Request(
            method=method,
            path=path,
            headers=headers or {},
            body=body,
            query_params=query_params or {},
        )

        ctx = RequestContext.get_current()
        ctx.request_id = request.headers.get("X-Request-ID", ctx.request_id)
        ctx.correlation_id = request.headers.get(
            "X-Correlation-ID", ctx.correlation_id
        )

        # Run request middleware
        for middleware in self.middleware_stack:
            response = middleware.process_request(request)
            if response:
                # Short-circuit
                log.info(
                    "Middleware short-circuited: %s",
                    middleware.__class__.__name__
                )
                return response

        # Find and execute route handler
        route_key = f"{method} {path}"
        handler = self.routes.get(route_key)

        if handler:
            try:
                response = handler(request)
            except Exception as exc:
                log.error("Handler error: %s", exc)
                response = Response(
                    status_code=500,
                    error=str(exc),
                )
        else:
            response = Response(
                status_code=404,
                error=f"Route not found: {route_key}",
            )

        # Run response middleware
        for middleware in reversed(self.middleware_stack):
            response = middleware.process_response(request, response)

        return response

    def get_stats(self) -> Dict[str, Any]:
        """Get gateway statistics."""
        return {
            "middleware_count": len(self.middleware_stack),
            "routes_count": len(self.routes),
            "routes": list(self.routes.keys()),
        }
