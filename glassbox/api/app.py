"""
GlassBox Framework — REST API  (v1.2.0)
Flask governance service — hardened, production-ready.

Security hardening (v1.0.1 → v1.2.0):
  - Agent request rate limiting (100 req/min per agent_id)
  - Global rate limiting (500 req/min per IP)
  - Trusted-proxy-aware client IP extraction (GLASSBOX_TRUSTED_PROXY_COUNT)
  - user_override moved from request body to authenticated session only
  - agent_id in URL params validated before use
  - Security + CORS response headers on every request (GLASSBOX_CORS_ORIGINS)
  - Payload size limit enforced at API boundary (per-endpoint, not just global)
  - decision_id in URL validated (UUID format check)
  - Structured error responses with request_id

Endpoints (root + mirrored under /v1/ for API stability):
  POST /decisions              — Submit a decision for governance
  POST /decisions/simulate     — Dry-run policy simulation
  POST /decisions/batch        — Govern up to 500 decisions in parallel
  GET  /decisions              — List audit records (filterable)
  GET  /decisions/<id>         — Get a specific audit record
  POST /decisions/<id>/replay  — Replay a historical decision
  GET  /stats                  — Aggregate governance statistics
  GET  /agents/<id>/velocity   — Velocity circuit breaker status
  GET  /agents/<id>/anomaly    — Anomaly detection baseline stats
  GET  /policies               — List registered policies
  GET  /contracts              — List registered agent contracts
  GET  /ecosystem              — Ecosystem breaker status
  GET  /health                 — Health check
  GET  /ready                  — K8s readiness probe
  GET  /metrics                — Prometheus text-format metrics
  GET  /openapi.json           — OpenAPI 3.0 specification
  GET  /events/stream          — Server-Sent Events stream

Author: Mohammed Akbar Ansari
"""
from __future__ import annotations
import os, re, sys, uuid
import threading
from collections import defaultdict
from time import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from flask import Blueprint, Flask, Response, jsonify, request

from glassbox import __version__ as _GLASSBOX_VERSION
from glassbox.api.middleware import require_auth
from glassbox.governance.pipeline        import GovernancePipeline
from glassbox.governance.models          import (
    AgentContract, DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)
from glassbox.governance.decision_replay import DecisionReplay
from glassbox.governance.simulator       import PolicySimulator
from glassbox.governance.logging_manager import get_logger
from glassbox.governance.request_context import RequestContext
from glassbox.security.sanitizer         import validate_agent_id

log = get_logger("api")

_MAX_BODY_BYTES       = 8 * 1024        # 8 KB  — single-decision endpoints
_MAX_BATCH_BODY_BYTES = 512 * 1024      # 512 KB — batch endpoint
_SAFE_ID_RE     = re.compile(r'^[a-zA-Z0-9_\-\.@:]+$')
_TENANT_ID_RE   = re.compile(r'^[a-zA-Z0-9_-]{1,128}$')
_UUID_RE        = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


# ── RATE LIMITER [v1.0.1 DoS Prevention] ──────────────────────────────────

class SimpleSlidingWindowRateLimiter:
    """
    In-memory sliding-window rate limiter with per-key tracking.
    Thread-safe. No external dependencies.

    Keys are capped at _max_keys to prevent unbounded memory growth
    under adversarial conditions (many unique IPs / agent IDs).

    Eviction strategy (LRU, not FIFO):
      When a shard is full and a new key arrives:
        1. Prefer to evict a key whose entire timestamp window has expired
           (those entries carry no rate-limit state worth preserving).
        2. If no fully-expired key exists, evict the key whose most-recent
           request is oldest (true LRU), so active keys are never displaced
           by an attacker flooding with fresh IPs.
    """
    _MAX_KEYS = 100_000
    _NUM_SHARDS = 64

    def __init__(self, requests_per_window: int = 100, window_seconds: int = 60):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._max_keys_per_shard = max(1, (self._MAX_KEYS + self._NUM_SHARDS - 1) // self._NUM_SHARDS)
        self._shards = [
            {"lock": threading.Lock(), "timestamps": defaultdict(list)}
            for _ in range(self._NUM_SHARDS)
        ]

    def _get_shard(self, key: str) -> dict:
        return self._shards[hash(key) % self._NUM_SHARDS]

    @staticmethod
    def _evict_one(timestamps: dict, window_start: float) -> None:
        """Remove one key from a full shard using LRU eviction.

        Priority 1 — evict a key whose window has fully expired (no active
        timestamps remain). Such a key is functionally identical to being
        absent, so evicting it has zero security impact.

        Priority 2 — evict the key with the oldest most-recent request (true
        LRU), ensuring that currently-active legitimate keys are the last to
        be displaced.
        """
        # Priority 1: fully-expired key (all timestamps outside the window)
        expired_key = next(
            (k for k, ts_list in timestamps.items()
             if not ts_list or ts_list[-1] <= window_start),
            None,
        )
        if expired_key is not None:
            del timestamps[expired_key]
            return
        # Priority 2: LRU — oldest most-recent request timestamp
        lru_key = min(
            timestamps,
            key=lambda k: timestamps[k][-1] if timestamps[k] else 0.0,
        )
        del timestamps[lru_key]

    def is_allowed(self, key: str) -> bool:
        now = time()
        window_start = now - self.window_seconds
        shard = self._get_shard(key)

        with shard["lock"]:
            timestamps = shard["timestamps"]
            if key in timestamps:
                # Prune expired entries for this key on each access
                timestamps[key] = [ts for ts in timestamps[key] if ts > window_start]
            else:
                if len(timestamps) >= self._max_keys_per_shard:
                    self._evict_one(timestamps, window_start)
                timestamps[key] = []

            if len(timestamps[key]) < self.requests_per_window:
                timestamps[key].append(now)
                return True
            return False


_agent_limiter = SimpleSlidingWindowRateLimiter(requests_per_window=100, window_seconds=60)
_ip_limiter    = SimpleSlidingWindowRateLimiter(requests_per_window=500, window_seconds=60)


# ── TRUSTED-PROXY-AWARE CLIENT IP [S-1 fix] ───────────────────────────────
# Set GLASSBOX_TRUSTED_PROXY_COUNT=N (N = number of trusted reverse-proxy hops)
# and optionally GLASSBOX_PROXY_IP_HEADER (default: X-Forwarded-For).
# Without config, falls back to request.remote_addr (safe for direct exposure).

_TRUSTED_PROXY_COUNT = int(os.environ.get("GLASSBOX_TRUSTED_PROXY_COUNT", "0"))
_PROXY_IP_HEADER     = os.environ.get("GLASSBOX_PROXY_IP_HEADER", "X-Forwarded-For")


def _get_client_ip() -> str:
    """Return the real client IP, stripping trusted reverse-proxy hops."""
    if _TRUSTED_PROXY_COUNT <= 0:
        return request.remote_addr or "unknown"
    forwarded = request.headers.get(_PROXY_IP_HEADER, "").strip()
    if not forwarded:
        return request.remote_addr or "unknown"
    ips = [ip.strip() for ip in forwarded.split(",") if ip.strip()]
    # The leftmost IP is the client; rightmost entries are added by proxies.
    # With N trusted hops, discard the last N entries and take the remainder.
    idx = max(0, len(ips) - _TRUSTED_PROXY_COUNT - 1)
    return ips[idx] if ips else (request.remote_addr or "unknown")


def _safe_url_id(v: str, name: str = "id"):
    if not v:                         return False, f"'{name}' must not be empty."
    if len(v) > 128:                  return False, f"'{name}' exceeds 128 chars."
    if not _SAFE_ID_RE.match(v):      return False, f"'{name}' contains invalid characters."
    return True, ""


def create_app(
    pipeline=None,
    log_dir=None,
    echo=False,
    testing=False,
    auth_required=None,
    tenant_scoping_required=None,
) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    # Global limit covers the batch endpoint (512 KB).
    # Single-decision endpoints enforce _MAX_BODY_BYTES themselves.
    app.config["MAX_CONTENT_LENGTH"] = _MAX_BATCH_BODY_BYTES

    _pipeline = pipeline or GovernancePipeline(
        log_dir=log_dir, echo=echo,
        environment=os.environ.get("GLASSBOX_ENV", "production"),
    )
    _replay = DecisionReplay(_pipeline)
    _api_key = os.environ.get("GLASSBOX_API_KEY")
    env_auth_override   = os.environ.get("GLASSBOX_REQUIRE_AUTH")
    env_tenant_override = os.environ.get("GLASSBOX_REQUIRE_TENANT_SCOPE")

    if auth_required is None:
        if env_auth_override is not None:
            auth_required = str(env_auth_override).strip().lower() in {"1", "true", "yes", "on"}
        else:
            auth_required = not testing
    if tenant_scoping_required is None:
        if env_tenant_override is not None:
            tenant_scoping_required = str(env_tenant_override).strip().lower() in {"1", "true", "yes", "on"}
        else:
            tenant_scoping_required = False

    _auth_required        = bool(auth_required)
    _tenant_scope_required = bool(tenant_scoping_required)

    if _auth_required and not _api_key and not testing:
        raise RuntimeError(
            "GLASSBOX_API_KEY must be set when API authentication is enabled. "
            "Pass testing=True or auth_required=False only for controlled non-production use."
        )

    # ── CORS [S-2] ──────────────────────────────────────────────────────────
    # GLASSBOX_CORS_ORIGINS: comma-separated allowed origins, or "*" for all.
    # Leave unset (default) to disable CORS headers entirely.
    _cors_raw     = os.environ.get("GLASSBOX_CORS_ORIGINS", "").strip()
    _cors_origins = {o.strip() for o in _cors_raw.split(",") if o.strip()} if _cors_raw else set()
    _cors_allow_all = "*" in _cors_origins

    if _auth_required:
        _auth_probe = require_auth(secret_key=_api_key)(lambda: None)

        @app.before_request
        def _enforce_auth():
            if request.path in {"/health", "/ready", "/metrics"}:
                return None
            if request.method == "OPTIONS":
                return None
            return _auth_probe()

    @app.before_request
    def _set_request_context():
        if request.path in {"/health", "/ready", "/metrics"}:
            return None
        request_id     = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        correlation_id = request.headers.get("X-Correlation-ID") or request_id
        tenant_id      = request.headers.get("X-Tenant-ID")
        if request.method == "GET":
            tenant_qs = request.args.get("tenant_id")
            if tenant_id and tenant_qs and tenant_id != tenant_qs:
                return _err("Tenant mismatch between X-Tenant-ID header and tenant_id query parameter.", 400, request_id)
            tenant_id = tenant_id or tenant_qs
        if tenant_id is not None:
            tenant_id = str(tenant_id).strip()
            if tenant_id and not _TENANT_ID_RE.match(tenant_id):
                return _err("Invalid tenant_id format.", 400, request_id)
        RequestContext.set_current(RequestContext(
            request_id=request_id,
            user_id=request.headers.get("X-User-ID"),
            tenant_id=tenant_id or None,
            correlation_id=correlation_id,
            metadata={
                "environment":   os.environ.get("GLASSBOX_ENV", "production"),
                "source_system": "api",
                "http_method":   request.method,
                "http_path":     request.path,
            },
        ))
        return None

    @app.after_request
    def _add_headers(resp: Response) -> Response:
        # Security headers
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"]        = "DENY"
        resp.headers["X-XSS-Protection"]       = "1; mode=block"
        resp.headers["Cache-Control"]          = "no-store"
        resp.headers["X-GlassBox-Version"]     = _GLASSBOX_VERSION

        # CORS headers [S-2]
        if _cors_origins:
            origin = request.headers.get("Origin", "")
            if _cors_allow_all or origin in _cors_origins:
                resp.headers["Access-Control-Allow-Origin"]  = "*" if _cors_allow_all else origin
                resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
                resp.headers["Access-Control-Allow-Headers"] = (
                    "Content-Type, Authorization, X-API-Key, "
                    "X-Tenant-ID, X-User-ID, X-Request-ID, X-Correlation-ID"
                )
                resp.headers["Access-Control-Max-Age"] = "86400"
                if resp.status_code == 200 and request.method == "OPTIONS":
                    resp.status_code = 204
        return resp

    @app.teardown_request
    def _clear_request_context(exc):
        RequestContext.clear_current()
        return None

    # ── CORS preflight catch-all [S-2] ─────────────────────────────────────
    @app.route("/", defaults={"path": ""}, methods=["OPTIONS"])
    @app.route("/<path:path>", methods=["OPTIONS"])
    def _cors_preflight(path=""):
        return Response("", status=204)

    def _err(msg, code, rid=""):
        body = {"error": msg, "status": code}
        if rid: body["request_id"] = rid
        return jsonify(body), code

    def _tenant_from_request_context() -> str | None:
        rc = RequestContext.get_current()
        tenant_id = rc.tenant_id if rc else None
        if tenant_id is None:
            return None
        tenant_id = str(tenant_id).strip()
        if not tenant_id:
            return None
        if not _TENANT_ID_RE.match(tenant_id):
            raise ValueError("Invalid tenant_id format.")
        return tenant_id

    def _tenant_from_decision_request(req: DecisionRequest) -> str | None:
        metadata = {}
        if req.context and isinstance(req.context.metadata, dict):
            metadata = req.context.metadata
        tenant_id = metadata.get("tenant_id")
        if tenant_id is None:
            return None
        tenant_id = str(tenant_id).strip()
        if not tenant_id:
            return None
        if not _TENANT_ID_RE.match(tenant_id):
            raise ValueError("Invalid tenant_id format.")
        return tenant_id

    def _pipeline_request_metadata(tenant_id: str | None) -> dict:
        rc = RequestContext.get_current()
        metadata: dict = {}
        if tenant_id:
            metadata["tenant_id"] = tenant_id
        if rc is not None:
            if rc.user_id:       metadata["user_id"]       = rc.user_id
            if rc.correlation_id: metadata["correlation_id"] = rc.correlation_id
            if rc.request_id:    metadata["request_id"]    = rc.request_id
        return metadata

    def _is_event_visible_to_tenant(evt, tenant_id: str | None) -> bool:
        if tenant_id is None:
            return True
        payload     = getattr(evt, "payload", {}) or {}
        payload_tenant = payload.get("tenant_id") if isinstance(payload, dict) else None
        if payload_tenant is not None:
            return str(payload_tenant).strip() == tenant_id
        decision_id = payload.get("decision_id") if isinstance(payload, dict) else None
        audit_repo  = getattr(_pipeline, "audit_repo", None)
        if decision_id and audit_repo and hasattr(audit_repo, "get_by_id"):
            try:
                return audit_repo.get_by_id(decision_id, tenant_id=tenant_id) is not None
            except Exception:
                return False
        return False

    def _tenant_scoped_stats(tenant_id: str | None) -> dict:
        if tenant_id is None:
            return dict(_pipeline.stats)
        audit_repo = getattr(_pipeline, "audit_repo", None)
        if audit_repo is None or not hasattr(audit_repo, "count"):
            raise RuntimeError(
                "Tenant-scoped stats require an audit repository with tenant-aware count support."
            )
        total          = audit_repo.count(tenant_id=tenant_id)
        blocked        = audit_repo.count(tenant_id=tenant_id, final_status=FinalStatus.BLOCKED.value)
        executed       = audit_repo.count(tenant_id=tenant_id, final_status=FinalStatus.EXECUTED.value)
        pending_review = audit_repo.count(tenant_id=tenant_id, final_status=FinalStatus.PENDING_REVIEW.value)
        return {
            "total": total,
            "status_breakdown": {"blocked": blocked, "executed": executed, "pending_review": pending_review},
            "tenant_id":     tenant_id,
            "scope":         "tenant",
            "block_rate_pct": (blocked / total * 100.0) if total else 0.0,
        }

    def _require_read_tenant(rid: str = "") -> str | None:
        try:
            tenant_id = _tenant_from_request_context()
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        if _tenant_scope_required and not tenant_id:
            raise ValueError(
                "Tenant scope is required. Provide X-Tenant-ID header or tenant_id query parameter."
            )
        return tenant_id

    def _get_record_tenant(record) -> str | None:
        if record is None:
            return None
        if hasattr(record, "context"):
            context  = getattr(record, "context", None)
            metadata = getattr(context, "metadata", {}) if context else {}
        else:
            context  = (record or {}).get("context") if isinstance(record, dict) else None
            metadata = (context or {}).get("metadata", {}) if isinstance(context, dict) else {}
        tenant_id = metadata.get("tenant_id") if isinstance(metadata, dict) else None
        return str(tenant_id).strip() if tenant_id else None

    def _ensure_record_tenant(record, tenant_id: str | None):
        if record is None:
            return None
        if tenant_id is None:
            return record
        if _get_record_tenant(record) != tenant_id:
            return None
        return record

    def _deserialize_record(record):
        if record is None or hasattr(record, "decision_id"):
            return record
        deserializer = getattr(_pipeline.audit_logger, "_deserialize_audit_record", None)
        if callable(deserializer):
            return deserializer(record)
        return record

    def _filter_in_memory_records(tenant_id, status_f, agent_f, type_f, limit, offset):
        get_all = getattr(_pipeline.audit_logger, "get_all", None)
        if not callable(get_all):
            return None, 0
        filtered = []
        for raw_record in get_all():
            record = _ensure_record_tenant(_deserialize_record(raw_record), tenant_id)
            if record is None:
                continue
            record_agent  = getattr(record, "agent_id", None)
            record_type   = getattr(getattr(record, "decision_type", None), "value",
                                    getattr(record, "decision_type", None))
            record_status = getattr(getattr(record, "final_status", None), "value",
                                    getattr(record, "final_status", None))
            if agent_f  and record_agent  != agent_f:  continue
            if type_f   and record_type   != type_f:   continue
            if status_f and record_status != status_f: continue
            filtered.append(record)
        total = len(filtered)
        return filtered[offset: offset + limit], total

    def _parse(data: dict) -> DecisionRequest:
        agent_id = (data.get("agent_id") or "").strip()
        if not agent_id: raise ValueError("'agent_id' is required.")
        ok, err = validate_agent_id(agent_id)
        if not ok: raise ValueError(f"Invalid agent_id: {err}")
        raw_type = data.get("decision_type")
        if not raw_type: raise ValueError("'decision_type' is required.")
        try:
            dtype = DecisionType(str(raw_type).lower())
        except ValueError:
            raise ValueError(
                f"Invalid decision_type '{raw_type}'. Valid: {[t.value for t in DecisionType]}")
        payload = data.get("payload")
        if not payload or not isinstance(payload, dict):
            raise ValueError("'payload' must be a non-empty object.")
        ctx = data.get("context") or {}

        # [v1.0.1 SECURITY] user_override MUST NOT come from untrusted request body
        if "user_override" in ctx:
            raise ValueError(
                "'user_override' cannot be set from request body. "
                "Only authenticated sessions can enable this.")

        rc = RequestContext.get_current()
        rc_fields: dict = {}
        if rc is not None:
            try:
                rc_dc = rc.to_decision_context()
                rc_fields = {
                    "environment":   rc_dc.environment   if rc_dc.environment   else None,
                    "source_system": rc_dc.source_system if rc_dc.source_system else None,
                    "metadata":      dict(rc_dc.metadata or {}),
                }
            except Exception:
                pass

        merged_metadata = {**rc_fields.get("metadata", {}), **(ctx.get("metadata") or {})}
        context = DecisionContext(
            environment   = str(ctx.get("environment",   rc_fields.get("environment",   "production")))[:32],
            source_system = str(ctx.get("source_system", rc_fields.get("source_system", "api")))[:64],
            user_override = False,
            confidence    = max(0.0, min(1.0, float(ctx.get("confidence", 1.0)))),
            agent_chain   = [str(a)[:128] for a in (ctx.get("agent_chain") or [])[:10]],
            metadata      = merged_metadata,
        )
        return DecisionRequest(agent_id=agent_id, decision_type=dtype,
                               payload=payload, context=context)

    # ── Health / Readiness ─────────────────────────────────────────────────

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(_pipeline.health()), 200

    @app.route("/ready", methods=["GET"])
    def ready():
        h = _pipeline.health()
        return jsonify({"ready": h.get("status") == "healthy"}), 200

    # ── Prometheus metrics [O-2] ───────────────────────────────────────────

    @app.route("/metrics", methods=["GET"])
    def metrics():
        """GET /metrics — Prometheus text-format metrics (no extra dependency)."""
        stats      = _pipeline.stats
        breakdown  = stats.get("status_breakdown") or {}
        executed   = breakdown.get("executed", 0)
        blocked    = breakdown.get("blocked", 0)
        pending    = breakdown.get("pending_review", 0)
        total      = stats.get("total", 0)
        block_rate = stats.get("block_rate_pct", 0.0)
        persisted  = stats.get("persisted", 0)
        failed     = stats.get("failed", 0)

        lines = [
            "# HELP glassbox_decisions_total Total governed decisions by outcome",
            "# TYPE glassbox_decisions_total counter",
            f'glassbox_decisions_total{{status="executed"}} {executed}',
            f'glassbox_decisions_total{{status="blocked"}} {blocked}',
            f'glassbox_decisions_total{{status="pending_review"}} {pending}',
            f'glassbox_decisions_total{{status="total"}} {total}',
            "# HELP glassbox_block_rate_pct Rolling block rate percentage",
            "# TYPE glassbox_block_rate_pct gauge",
            f"glassbox_block_rate_pct {block_rate:.4f}",
            "# HELP glassbox_audit_persisted_total Audit records successfully persisted",
            "# TYPE glassbox_audit_persisted_total counter",
            f"glassbox_audit_persisted_total {persisted}",
            "# HELP glassbox_audit_failed_total Audit records that failed persistence",
            "# TYPE glassbox_audit_failed_total counter",
            f"glassbox_audit_failed_total {failed}",
            "# HELP glassbox_policies_active Number of active registered policies",
            "# TYPE glassbox_policies_active gauge",
            f"glassbox_policies_active {len(_pipeline.policy_engine.policies)}",
        ]

        # Per-stage latency
        stage_stats = _pipeline.stage_latency_stats()
        if stage_stats:
            lines += [
                "# HELP glassbox_stage_latency_p50_ms Per-stage P50 latency (ms)",
                "# TYPE glassbox_stage_latency_p50_ms gauge",
            ]
            for stage, s in stage_stats.items():
                lines.append(f'glassbox_stage_latency_p50_ms{{stage="{stage}"}} {s.get("p50_ms", 0):.3f}')
            lines += [
                "# HELP glassbox_stage_latency_p99_ms Per-stage P99 latency (ms)",
                "# TYPE glassbox_stage_latency_p99_ms gauge",
            ]
            for stage, s in stage_stats.items():
                lines.append(f'glassbox_stage_latency_p99_ms{{stage="{stage}"}} {s.get("p99_ms", 0):.3f}')

        lines.append("")
        return Response(
            "\n".join(lines),
            mimetype="text/plain; version=0.0.4; charset=utf-8",
        )

    # ── OpenAPI 3.0 Specification [D-1] ───────────────────────────────────

    @app.route("/openapi.json", methods=["GET"])
    def openapi_spec():
        """GET /openapi.json — Machine-readable OpenAPI 3.0 specification."""
        spec = {
            "openapi": "3.0.3",
            "info": {
                "title": "GlassBox Agentic Governance API",
                "version": _GLASSBOX_VERSION,
                "description": "Runtime decision governance for autonomous AI agents.",
                "contact": {"name": "Mohammed Akbar Ansari"},
                "license": {"name": "Apache-2.0"},
            },
            "servers": [{"url": "/v1", "description": "Stable v1 API"}, {"url": "/", "description": "Legacy root (deprecated)"}],
            "components": {
                "securitySchemes": {
                    "ApiKeyHeader": {"type": "apiKey", "in": "header", "name": "X-API-Key"},
                },
                "schemas": {
                    "DecisionRequest": {
                        "type": "object", "required": ["agent_id", "decision_type", "payload"],
                        "properties": {
                            "agent_id":      {"type": "string", "example": "procurement-agent-1"},
                            "decision_type": {"type": "string", "enum": [t.value for t in DecisionType]},
                            "payload":       {"type": "object"},
                            "context":       {"type": "object"},
                        },
                    },
                    "DecisionResponse": {
                        "type": "object",
                        "properties": {
                            "decision_id":               {"type": "string", "format": "uuid"},
                            "final_status":              {"type": "string", "enum": ["executed", "blocked", "pending_review"]},
                            "risk_score":                {"type": "number"},
                            "risk_level":                {"type": "string"},
                            "policy_violations":         {"type": "array", "items": {"type": "string"}},
                            "pipeline_latency_ms":       {"type": "number"},
                            "circuit_breaker_triggered": {"type": "boolean"},
                            "message":                   {"type": "string"},
                        },
                    },
                    "Error": {
                        "type": "object",
                        "properties": {
                            "error":      {"type": "string"},
                            "status":     {"type": "integer"},
                            "request_id": {"type": "string"},
                        },
                    },
                },
            },
            "security": [{"ApiKeyHeader": []}],
            "paths": {
                "/decisions": {
                    "post": {
                        "summary": "Submit a decision for governance",
                        "operationId": "submitDecision",
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DecisionRequest"}}}},
                        "responses": {
                            "200": {"description": "Decision processed", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/DecisionResponse"}}}},
                            "422": {"description": "Validation error", "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Error"}}}},
                            "429": {"description": "Rate limit exceeded"},
                        },
                    },
                    "get": {
                        "summary": "List governed decisions",
                        "operationId": "listDecisions",
                        "parameters": [
                            {"in": "query", "name": "status", "schema": {"type": "string"}},
                            {"in": "query", "name": "agent_id", "schema": {"type": "string"}},
                            {"in": "query", "name": "decision_type", "schema": {"type": "string"}},
                            {"in": "query", "name": "limit", "schema": {"type": "integer", "default": 100, "maximum": 500}},
                            {"in": "query", "name": "offset", "schema": {"type": "integer", "default": 0}},
                        ],
                        "responses": {"200": {"description": "Audit record list"}},
                    },
                },
                "/decisions/simulate": {
                    "post": {"summary": "Dry-run decision without persisting", "operationId": "simulateDecision",
                             "responses": {"200": {"description": "Simulation result"}}},
                },
                "/decisions/batch": {
                    "post": {"summary": "Govern up to 500 decisions in parallel", "operationId": "batchDecisions",
                             "responses": {"200": {"description": "Batch results"}}},
                },
                "/decisions/{decision_id}": {
                    "get": {"summary": "Get a specific decision by ID", "operationId": "getDecision",
                            "parameters": [{"in": "path", "name": "decision_id", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                            "responses": {"200": {"description": "Audit record"}, "404": {"description": "Not found"}}},
                },
                "/decisions/{decision_id}/replay": {
                    "post": {"summary": "Replay a historical decision", "operationId": "replayDecision",
                             "parameters": [{"in": "path", "name": "decision_id", "required": True, "schema": {"type": "string", "format": "uuid"}}],
                             "responses": {"200": {"description": "Replay result"}}},
                },
                "/stats":    {"get": {"summary": "Aggregate governance statistics", "operationId": "getStats", "responses": {"200": {"description": "Stats"}}}},
                "/health":   {"get": {"summary": "Health check", "operationId": "health", "security": [], "responses": {"200": {"description": "Healthy"}}}},
                "/ready":    {"get": {"summary": "K8s readiness probe", "operationId": "ready", "security": [], "responses": {"200": {"description": "Ready"}}}},
                "/metrics":  {"get": {"summary": "Prometheus text metrics", "operationId": "metrics", "security": [], "responses": {"200": {"description": "Prometheus format"}}}},
                "/policies": {"get": {"summary": "List registered policies", "operationId": "listPolicies", "responses": {"200": {"description": "Policy list"}}}},
                "/contracts":{"get": {"summary": "List agent contracts", "operationId": "listContracts", "responses": {"200": {"description": "Contract list"}}}},
                "/ecosystem":{"get": {"summary": "Ecosystem breaker status", "operationId": "ecosystemStatus", "responses": {"200": {"description": "Status"}}}},
                "/agents/{agent_id}/velocity": {
                    "get": {"summary": "Velocity breaker status for agent", "operationId": "agentVelocity",
                            "parameters": [{"in": "path", "name": "agent_id", "required": True, "schema": {"type": "string"}}],
                            "responses": {"200": {"description": "Velocity status"}}},
                },
                "/agents/{agent_id}/anomaly": {
                    "get": {"summary": "Anomaly detection stats for agent", "operationId": "agentAnomaly",
                            "parameters": [
                                {"in": "path", "name": "agent_id", "required": True, "schema": {"type": "string"}},
                                {"in": "query", "name": "decision_type", "required": True, "schema": {"type": "string"}},
                            ],
                            "responses": {"200": {"description": "Anomaly stats"}}},
                },
            },
        }
        return jsonify(spec), 200

    # ── Decision endpoints ─────────────────────────────────────────────────

    @app.route("/decisions", methods=["POST"])
    def submit():
        rid = str(uuid.uuid4())[:8]

        # Per-endpoint body size guard (single-decision limit, not batch limit)
        if request.content_length and request.content_length > _MAX_BODY_BYTES:
            return _err(f"Request body too large (max {_MAX_BODY_BYTES // 1024}KB).", 413, rid)

        client_ip = _get_client_ip()
        if not _ip_limiter.is_allowed(client_ip):
            log.warning("Rate limit exceeded", extra={"component": "api", "client_ip": client_ip, "reason": "global_limit"})
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)

        data = request.get_json(silent=True, force=False)
        if not data:           return _err("Request body must be valid JSON.", 400, rid)
        if not isinstance(data, dict): return _err("Request body must be a JSON object.", 400, rid)
        try:
            req       = _parse(data)
            tenant_id = _tenant_from_decision_request(req)
        except (ValueError, TypeError) as exc:
            return _err(str(exc), 422, rid)
        except Exception:
            return _err("Request could not be processed.", 500, rid)
        if _tenant_scope_required and not tenant_id:
            return _err(
                "tenant_id is required for decision requests. Provide X-Tenant-ID or context.metadata.tenant_id.",
                422, rid,
            )

        if not _agent_limiter.is_allowed(req.agent_id):
            log.warning("Rate limit exceeded", extra={"component": "api", "agent_id": req.agent_id, "reason": "agent_limit"})
            return _err("Rate limit exceeded (100 req/min per agent).", 429, rid)

        try:
            resp = _pipeline.process(req, request_metadata=_pipeline_request_metadata(tenant_id))
        except Exception as exc:
            log.error("Pipeline error for agent '%s': %s", req.agent_id, exc,
                      extra={"component": "api", "request_id": rid}, exc_info=True)
            return _err("Decision processing failed.", 500, rid)
        return jsonify(resp.to_dict()), 200

    @app.route("/decisions/simulate", methods=["POST"])
    def simulate():
        """POST /decisions/simulate — Dry-run policy simulation without commit."""
        rid = str(uuid.uuid4())[:8]

        if request.content_length and request.content_length > _MAX_BODY_BYTES:
            return _err(f"Request body too large (max {_MAX_BODY_BYTES // 1024}KB).", 413, rid)

        client_ip = _get_client_ip()
        if not _ip_limiter.is_allowed(client_ip):
            log.warning("Rate limit exceeded", extra={"component": "api", "client_ip": client_ip, "reason": "global_limit"})
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)

        data = request.get_json(silent=True, force=False)
        if not data:           return _err("Request body must be valid JSON.", 400, rid)
        if not isinstance(data, dict): return _err("Request body must be a JSON object.", 400, rid)
        try:
            req = _parse(data)
        except (ValueError, TypeError) as exc:
            return _err(str(exc), 422, rid)
        except Exception:
            return _err("Request could not be processed.", 500, rid)

        if not _agent_limiter.is_allowed(req.agent_id):
            log.warning("Rate limit exceeded", extra={"component": "api", "agent_id": req.agent_id, "reason": "agent_limit"})
            return _err("Rate limit exceeded (100 req/min per agent).", 429, rid)

        try:
            sim = PolicySimulator(_pipeline)
            sim_result = sim.simulate(req)
            return jsonify({
                "simulation":            True,
                "predicted_decision_id": rid,
                "request_id":            req.request_id,
                "agent_id":              req.agent_id,
                "decision_type":         req.decision_type.value,
                "predicted_status":      sim_result.get("final_status", "UNKNOWN"),
                "predicted_disposition": sim_result.get("disposition", "UNKNOWN"),
                "blocking_policy":       sim_result.get("blocking_policy"),
                "risk_score":            sim_result.get("risk_score"),
                "note": "This is a simulated decision - no audit record was created.",
            }), 200
        except Exception as exc:
            log.error(f"Simulation failed: {exc}", extra={"component": "api", "request_id": rid}, exc_info=True)
            return _err(f"Simulation error: {str(exc)}", 500, rid)

    @app.route("/decisions", methods=["GET"])
    def list_decisions():
        try:
            tenant_id = _require_read_tenant()
        except ValueError as exc:
            return _err(str(exc), 400)
        status_f = request.args.get("status")
        agent_f  = request.args.get("agent_id")
        type_f   = request.args.get("decision_type")

        try:
            limit  = int(request.args.get("limit", 100))
            offset = int(request.args.get("offset", 0))
        except (ValueError, TypeError):
            limit, offset = 100, 0
        limit  = max(1, min(limit, 500))
        offset = max(0, offset)

        if agent_f:
            ok, _ = _safe_url_id(agent_f, "agent_id")
            if not ok: agent_f = None

        records = []
        total   = 0
        if getattr(_pipeline, "audit_repo", None) and hasattr(_pipeline.audit_repo, "query"):
            status_db = status_f
            type_db   = type_f
            if status_f:
                try:   status_db = FinalStatus(status_f).value
                except ValueError: status_db = None
            if type_f:
                try:   type_db = DecisionType(type_f).value
                except ValueError: type_db = None
            records = _pipeline.audit_repo.query(
                agent_id=agent_f, decision_type=type_db, final_status=status_db,
                tenant_id=tenant_id, limit=limit, offset=offset,
            )
            total = (_pipeline.audit_repo.count(
                tenant_id=tenant_id, agent_id=agent_f,
                decision_type=type_db, final_status=status_db,
            ) if hasattr(_pipeline.audit_repo, "count") else len(records))
            note = "Results returned from repository-backed audit storage."
        else:
            records, total = _filter_in_memory_records(
                tenant_id=tenant_id, status_f=status_f,
                agent_f=agent_f, type_f=type_f,
                limit=limit, offset=offset,
            )
            if records is None:
                log.warning("No audit listing backend configured.")
                return _err("Audit listing is unavailable because no queryable audit backend is configured.", 503)
            note = "Results returned from in-memory audit records."

        return jsonify({
            "count":   len(records),
            "total":   total,
            "offset":  offset,
            "limit":   limit,
            "note":    note,
            "records": [r.to_dict() if hasattr(r, "to_dict") else r for r in records],
        }), 200

    @app.route("/decisions/<decision_id>", methods=["GET"])
    def get_decision(decision_id: str):
        if not _UUID_RE.match(decision_id): return _err("Invalid decision ID format.", 400)
        try:
            tenant_id = _require_read_tenant()
        except ValueError as exc:
            return _err(str(exc), 400)
        if getattr(_pipeline, "audit_repo", None) and hasattr(_pipeline.audit_repo, "get_by_id"):
            rec = _deserialize_record(_pipeline.audit_repo.get_by_id(decision_id, tenant_id=tenant_id))
        else:
            rec = _ensure_record_tenant(_pipeline.audit_logger.get_by_id(decision_id), tenant_id)
        if not rec: return _err("Decision not found.", 404)
        return jsonify(rec.to_dict() if hasattr(rec, "to_dict") else rec), 200

    @app.route("/decisions/<decision_id>/replay", methods=["POST"])
    def replay_decision(decision_id: str):
        if not _UUID_RE.match(decision_id): return _err("Invalid decision ID format.", 400)
        # Apply per-agent rate limiting to replay as well (prevents audit log flooding)
        rid = str(uuid.uuid4())[:8]
        client_ip = _get_client_ip()
        if not _ip_limiter.is_allowed(client_ip):
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)
        try:
            tenant_id = _require_read_tenant()
        except ValueError as exc:
            return _err(str(exc), 400)
        if getattr(_pipeline, "audit_repo", None) and hasattr(_pipeline.audit_repo, "get_by_id"):
            rec = _deserialize_record(_pipeline.audit_repo.get_by_id(decision_id, tenant_id=tenant_id))
        else:
            rec = _ensure_record_tenant(_pipeline.audit_logger.get_by_id(decision_id), tenant_id)
        if not rec: return _err("Decision not found.", 404)
        replayed = _replay.replay_one(rec)
        return jsonify({
            "original_id":     decision_id,
            "original_status": rec.final_status.value if rec.final_status else None,
            "replayed":        replayed.to_dict(),
            "outcome_changed": rec.final_status != replayed.final_status,
        }), 200

    @app.route("/stats", methods=["GET"])
    def stats():
        try:
            tenant_id = _require_read_tenant()
            return jsonify(_tenant_scoped_stats(tenant_id)), 200
        except ValueError as exc:
            return _err(str(exc), 400)
        except RuntimeError as exc:
            return _err(str(exc), 503)

    @app.route("/agents/<agent_id>/velocity", methods=["GET"])
    def velocity(agent_id: str):
        ok, err = _safe_url_id(agent_id, "agent_id")
        if not ok: return _err(err, 400)
        return jsonify(_pipeline.velocity_status(agent_id)), 200

    @app.route("/agents/<agent_id>/anomaly", methods=["GET"])
    def anomaly(agent_id: str):
        ok, err = _safe_url_id(agent_id, "agent_id")
        if not ok: return _err(err, 400)
        dtype = request.args.get("decision_type")
        if not dtype:
            return _err("'decision_type' query parameter is required.", 400)
        return jsonify(_pipeline.anomaly_stats(agent_id, dtype)), 200

    @app.route("/policies", methods=["GET"])
    def policies():
        pols = _pipeline.policy_engine.list_policies()
        serialised = [
            p.to_dict() if hasattr(p, "to_dict") else {
                "policy_id":   getattr(p, "policy_id", getattr(p, "id", None)),
                "policy_name": getattr(p, "policy_name", getattr(p, "name", None)),
                "enabled":     getattr(p, "enabled", True),
            }
            for p in pols
        ]
        return jsonify({"policies": serialised}), 200

    @app.route("/contracts", methods=["GET"])
    def contracts():
        return jsonify({"contracts": _pipeline.list_contracts()}), 200

    @app.route("/ecosystem", methods=["GET"])
    def ecosystem():
        return jsonify(_pipeline.ecosystem_status()), 200

    # ── Batch endpoint [v1.1] ──────────────────────────────────────────────

    @app.route("/decisions/batch", methods=["POST"])
    def batch_submit():
        """POST /decisions/batch — Govern up to 500 decisions in parallel."""
        import time as _t
        rid       = str(uuid.uuid4())[:8]
        client_ip = _get_client_ip()
        if not _ip_limiter.is_allowed(client_ip):
            log.warning("Rate limit exceeded", extra={"component": "api", "client_ip": client_ip, "reason": "global_limit"})
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)

        data = request.get_json(silent=True)
        if not data:
            return _err("JSON body required", 400, rid)
        decisions_raw = data.get("decisions", [])
        if not isinstance(decisions_raw, list) or not decisions_raw:
            return _err("'decisions' must be non-empty list", 400, rid)
        if len(decisions_raw) > 500:
            return _err("Batch size limited to 500 decisions per request", 400, rid)
        max_workers = min(int(data.get("max_workers", 4)), 16)

        errors, parsed = [], []
        batch_tenants  = set()
        for i, d in enumerate(decisions_raw):
            try:
                req = _parse(d)
                if not _agent_limiter.is_allowed(req.agent_id):
                    raise ValueError(f"Rate limit exceeded (100 req/min per agent): {req.agent_id}")
                tenant_id = _tenant_from_decision_request(req)
                if _tenant_scope_required and not tenant_id:
                    raise ValueError(
                        "tenant_id is required for every decision in the batch. "
                        "Provide X-Tenant-ID or context.metadata.tenant_id."
                    )
                if tenant_id:
                    batch_tenants.add(tenant_id)
                parsed.append((req, tenant_id))
            except Exception as e:
                errors.append({"index": i, "error": str(e)})

        if _tenant_scope_required and len(batch_tenants) > 1:
            return _err("Batch requests must be scoped to exactly one tenant.", 422, rid)

        t0 = _t.perf_counter()
        if max_workers <= 1 or len(parsed) < 4:
            responses = [
                _pipeline.process(req, request_metadata=_pipeline_request_metadata(tenant_id))
                for req, tenant_id in parsed
            ]
        else:
            resp_map = {}
            executor = getattr(_pipeline, "shared_executor", None)
            if executor is not None:
                futs = {
                    executor.submit(
                        _pipeline.process, req, _pipeline_request_metadata(tenant_id),
                    ): i
                    for i, (req, tenant_id) in enumerate(parsed)
                }
                import concurrent.futures as _cf
                for fut in _cf.as_completed(futs):
                    idx = futs[fut]
                    try:
                        resp_map[idx] = fut.result()
                    except Exception as e:
                        errors.append({"index": idx, "error": str(e)})
                responses = [resp_map[i] for i in sorted(resp_map)]
            else:
                log.warning("No shared thread pool found on pipeline. Processing batch serially for safety.")
                responses = [
                    _pipeline.process(req, request_metadata=_pipeline_request_metadata(tenant_id))
                    for req, tenant_id in parsed
                ]

        results   = [r.to_dict() for r in responses]
        elapsed   = round((_t.perf_counter() - t0) * 1000, 1)
        executed  = sum(1 for r in results if r.get("final_status") == "executed")
        blocked   = sum(1 for r in results if r.get("final_status") == "blocked")
        reviewing = sum(1 for r in results if r.get("final_status") == "pending_review")
        return jsonify({
            "results": results,
            "errors":  errors,
            "summary": {
                "total": len(results), "executed": executed,
                "blocked": blocked, "pending_review": reviewing,
                "parse_errors": len(errors), "batch_latency_ms": elapsed,
            },
        }), 200

    # ── SSE real-time event stream [v1.1] ──────────────────────────────────

    @app.route("/events/stream", methods=["GET"])
    def events_stream():
        """GET /events/stream — Server-Sent Events stream of governance events."""
        import queue as _q, json as _j
        try:
            tenant_id = _require_read_tenant()
        except ValueError as exc:
            return _err(str(exc), 400)
        event_q: _q.Queue = _q.Queue(maxsize=200)

        def _on(evt):
            if not _is_event_visible_to_tenant(evt, tenant_id):
                return
            try:
                event_q.put_nowait(evt)
            except _q.Full:
                pass

        bus = getattr(_pipeline, "event_bus", None)
        if bus:
            bus.subscribe("*", _on)

        NL, SEP = "\n", "\n\n"

        def _gen():
            yield "retry: 3000" + SEP
            try:
                while True:
                    try:
                        evt   = event_q.get(timeout=15)
                        etype = getattr(evt, "event_type", "event")
                        data  = _j.dumps({
                            "event_type": etype,
                            "payload":    getattr(evt, "payload", {}),
                        })
                        yield "event: " + etype + NL + "data: " + data + SEP
                    except _q.Empty:
                        yield ": heartbeat" + SEP
            finally:
                if bus:
                    try:
                        bus.unsubscribe("*", _on)
                    except Exception:
                        pass

        return Response(
            _gen(), mimetype="text/event-stream",
            headers={
                "Cache-Control":         "no-cache",
                "X-Accel-Buffering":     "no",
                "Connection":            "keep-alive",
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    # ── Error handlers ─────────────────────────────────────────────────────

    @app.errorhandler(400)
    def bad_request(e):   return _err("Bad request.", 400)
    @app.errorhandler(404)
    def not_found(e):     return _err("Endpoint not found.", 404)
    @app.errorhandler(413)
    def too_large(e):     return _err(f"Request body too large (max {_MAX_BODY_BYTES // 1024}KB for single decisions, {_MAX_BATCH_BODY_BYTES // 1024}KB for batch).", 413)
    @app.errorhandler(500)
    def server_error(e):  return _err("Internal server error.", 500)

    # ── /v1/ Blueprint [AP-2] ──────────────────────────────────────────────
    # Mirror all application routes under /v1/ for long-term API stability.
    # The root routes remain for backward compatibility.

    _v1 = Blueprint("v1", __name__, url_prefix="/v1")
    _SKIP_ENDPOINTS = frozenset({"static", "_enforce_auth", "_cors_preflight"})
    for _rule in list(app.url_map.iter_rules()):
        _ep = _rule.endpoint
        if _ep in _SKIP_ENDPOINTS or _ep.startswith("v1_"):
            continue
        _fn = app.view_functions.get(_ep)
        if _fn is None:
            continue
        _methods = {m for m in _rule.methods if m not in {"HEAD", "OPTIONS"}}
        try:
            _v1.add_url_rule(
                _rule.rule,
                endpoint=f"v1_{_ep}",
                view_func=_fn,
                methods=_methods,
            )
        except Exception:
            pass
    app.register_blueprint(_v1)

    return app
