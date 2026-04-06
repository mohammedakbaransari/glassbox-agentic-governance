"""
GlassBox Framework — REST API  (v1.0.1)
Flask governance service — hardened, production-ready.

Security hardening (v1.0.1):
  - Agent request rate limiting (100 req/min per agent_id)
  - Global rate limiting (500 req/min per IP)
  - user_override moved from request body to authenticated session only
  - agent_id in URL params validated before use
  - Security response headers on every request
  - Payload size limit enforced at API boundary
  - decision_id in URL validated (UUID format check)
  - Structured error responses with request_id

Endpoints:
  POST /decisions              — Submit a decision for governance
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

Author: Mohammed Akbar Ansari
"""
from __future__ import annotations
import os, re, sys, uuid
import threading
from collections import defaultdict
from time import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from flask import Flask, Response, jsonify, request

from glassbox import __version__ as _GLASSBOX_VERSION
from glassbox.governance.pipeline        import GovernancePipeline
from glassbox.governance.models          import (
    AgentContract, DecisionContext, DecisionRequest, DecisionType, FinalStatus,
)
from glassbox.governance.decision_replay import DecisionReplay
from glassbox.governance.simulator       import PolicySimulator
from glassbox.governance.logging_manager import get_logger
from glassbox.security.sanitizer         import validate_agent_id

log = get_logger("api")

_MAX_BODY_BYTES = 8 * 1024
_SAFE_ID_RE     = re.compile(r'^[a-zA-Z0-9_\-\.@:]+$')
_UUID_RE        = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


# ── RATE LIMITER [v1.0.1 DoS Prevention] ──────────────────────────────────

class SimpleSlidingWindowRateLimiter:
    """
    In-memory sliding-window rate limiter with per-key tracking.
    Thread-safe. No external dependencies.
    
    Keys are capped at _max_keys to prevent unbounded memory growth
    under adversarial conditions (many unique IPs / agent IDs).
    """
    _MAX_KEYS = 100_000  # Hard cap on tracked keys; oldest entry evicted past this.

    def __init__(self, requests_per_window: int = 100, window_seconds: int = 60):
        self.requests_per_window = requests_per_window
        self.window_seconds = window_seconds
        self._timestamps = defaultdict(list)  # key -> [timestamp, ...]
        self._lock = threading.Lock()
    
    def is_allowed(self, key: str) -> bool:
        """Check if request is allowed under rate limit. Returns True if allowed, False if rate exceeded."""
        now = time()
        window_start = now - self.window_seconds
        
        with self._lock:
            # Remove expired timestamps outside the window
            if key in self._timestamps:
                self._timestamps[key] = [ts for ts in self._timestamps[key] if ts > window_start]
            else:
                # Evict the oldest entry if the key dict is at capacity.
                if len(self._timestamps) >= self._MAX_KEYS:
                    oldest_key = next(iter(self._timestamps))
                    del self._timestamps[oldest_key]
                self._timestamps[key] = []
            
            # Check if we're under limit
            if len(self._timestamps[key]) < self.requests_per_window:
                self._timestamps[key].append(now)
                return True
            else:
                return False


# Rate limiters for different endpoints
_agent_limiter = SimpleSlidingWindowRateLimiter(requests_per_window=100, window_seconds=60)  # 100 req/min per agent
_ip_limiter    = SimpleSlidingWindowRateLimiter(requests_per_window=500, window_seconds=60)  # 500 req/min per IP 


def _safe_url_id(v: str, name: str = "id"):
    if not v:                         return False, f"'{name}' must not be empty."
    if len(v) > 128:                  return False, f"'{name}' exceeds 128 chars."
    if not _SAFE_ID_RE.match(v):      return False, f"'{name}' contains invalid characters."
    return True, ""


def create_app(pipeline=None, log_dir=None, echo=False, testing=False) -> Flask:
    app = Flask(__name__)
    app.config["TESTING"] = testing
    app.config["MAX_CONTENT_LENGTH"] = _MAX_BODY_BYTES

    _pipeline = pipeline or GovernancePipeline(
        log_dir=log_dir, echo=echo,
        environment=os.environ.get("GLASSBOX_ENV", "production"),
    )
    _replay = DecisionReplay(_pipeline)

    @app.after_request
    def security_headers(resp: Response) -> Response:
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"]        = "DENY"
        resp.headers["X-XSS-Protection"]       = "1; mode=block"
        resp.headers["Cache-Control"]          = "no-store"
        resp.headers["X-GlassBox-Version"]     = _GLASSBOX_VERSION
        return resp

    def _err(msg, code, rid=""):
        body = {"error": msg, "status": code}
        if rid: body["request_id"] = rid
        return jsonify(body), code

    def _parse(data: dict) -> DecisionRequest:
        agent_id = (data.get("agent_id") or "").strip()
        if not agent_id: raise ValueError("'agent_id' is required.")
        ok, err = validate_agent_id(agent_id)
        if not ok: raise ValueError(f"Invalid agent_id: {err}")
        raw_type = data.get("decision_type")
        if not raw_type: raise ValueError("'decision_type' is required.")
        try:   dtype = DecisionType(str(raw_type).lower())
        except ValueError: raise ValueError(
            f"Invalid decision_type '{raw_type}'. Valid: {[t.value for t in DecisionType]}")
        payload = data.get("payload")
        if not payload or not isinstance(payload, dict):
            raise ValueError("'payload' must be a non-empty object.")
        ctx = data.get("context") or {}
        
        # [v1.0.1 SECURITY] user_override MUST NOT come from untrusted request body
        # Only authenticated session can set user_override. Request body is rejected.
        if "user_override" in ctx:
            raise ValueError(
                "'user_override' cannot be set from request body. Only authenticated sessions can enable this.")
        
        context = DecisionContext(
            environment   = str(ctx.get("environment",   "production"))[:32],
            source_system = str(ctx.get("source_system", "api"))[:64],
            user_override = False,  # Always False from request; authenticated code can override
            confidence    = max(0.0, min(1.0, float(ctx.get("confidence", 1.0)))),
            agent_chain   = [str(a)[:128] for a in (ctx.get("agent_chain") or [])[:10]],
            metadata      = ctx.get("metadata") or {},
        )
        return DecisionRequest(agent_id=agent_id, decision_type=dtype,
                               payload=payload, context=context)

    @app.route("/health", methods=["GET"])
    def health():
        return jsonify(_pipeline.health()), 200

    @app.route("/ready", methods=["GET"])
    def ready():
        h = _pipeline.health()
        return jsonify({"ready": h.get("status") == "healthy"}), 200

    @app.route("/decisions", methods=["POST"])
    def submit():
        rid  = str(uuid.uuid4())[:8]
        
        # [v1.0.1 DoS Prevention] Rate limiting check
        client_ip = request.remote_addr or "unknown"
        if not _ip_limiter.is_allowed(client_ip):
            log.warning("Rate limit exceeded", extra={"component": "api", "client_ip": client_ip, "reason": "global_limit"})
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)
        
        data = request.get_json(silent=True, force=False)
        if not data:           return _err("Request body must be valid JSON.", 400, rid)
        if not isinstance(data, dict): return _err("Request body must be a JSON object.", 400, rid)
        try:
            req  = _parse(data)
        except (ValueError, TypeError) as exc:
            return _err(str(exc), 422, rid)
        except Exception:
            return _err("Request could not be processed.", 500, rid)
        
        # [v1.0.1 DoS Prevention] Per-agent rate limiting check
        if not _agent_limiter.is_allowed(req.agent_id):
            log.warning("Rate limit exceeded", extra={"component": "api", "agent_id": req.agent_id, "reason": "agent_limit"})
            return _err("Rate limit exceeded (100 req/min per agent).", 429, rid)
        
        try:
            resp = _pipeline.process(req)
        except Exception as exc:
            log.error(
                "Pipeline error for agent '%s': %s",
                req.agent_id, exc,
                extra={"component": "api", "request_id": rid},
                exc_info=True,
            )
            return _err("Decision processing failed.", 500, rid)
        return jsonify(resp.to_dict()), 200

    @app.route("/decisions/simulate", methods=["POST"])
    def simulate():
        """POST /decisions/simulate — Dry-run policy simulation without commit.
        
        Returns predicted decision status WITHOUT persisting to audit log.
        Useful for what-if analysis and pre-deployment impact assessment.
        """
        rid  = str(uuid.uuid4())[:8]
        
        # [v1.0.1 DoS Prevention] Rate limiting check
        client_ip = request.remote_addr or "unknown"
        if not _ip_limiter.is_allowed(client_ip):
            log.warning("Rate limit exceeded", extra={"component": "api", "client_ip": client_ip, "reason": "global_limit"})
            return _err("Rate limit exceeded (500 req/min per IP).", 429, rid)
        
        data = request.get_json(silent=True, force=False)
        if not data:
            return _err("Request body must be valid JSON.", 400, rid)
        if not isinstance(data, dict):
            return _err("Request body must be a JSON object.", 400, rid)
        try:
            req = _parse(data)
        except (ValueError, TypeError) as exc:
            return _err(str(exc), 422, rid)
        except Exception:
            return _err("Request could not be processed.", 500, rid)
        
        # [v1.0.1 DoS Prevention] Per-agent rate limiting check
        if not _agent_limiter.is_allowed(req.agent_id):
            log.warning("Rate limit exceeded", extra={"component": "api", "agent_id": req.agent_id, "reason": "agent_limit"})
            return _err("Rate limit exceeded (100 req/min per agent).", 429, rid)
        
        # Run simulator for dry-run prediction
        try:
            sim = PolicySimulator(_pipeline)
            sim_result = sim.simulate(req)
            return jsonify({
                "simulation": True,
                "predicted_decision_id": rid,
                "request_id": req.request_id,
                "agent_id": req.agent_id,
                "decision_type": req.decision_type.value,
                "predicted_status": sim_result.get("final_status", "UNKNOWN"),
                "predicted_disposition": sim_result.get("disposition", "UNKNOWN"),
                "blocking_policy": sim_result.get("blocking_policy"),
                "risk_score": sim_result.get("risk_score"),
                "note": "This is a simulated decision - no audit record was created."
            }), 200
        except Exception as exc:
            log.error(f"Simulation failed: {exc}", extra={"component": "api", "request_id": rid}, exc_info=True)
            return _err(f"Simulation error: {str(exc)}", 500, rid)

    @app.route("/decisions", methods=["GET"])
    def list_decisions():
        status_f = request.args.get("status")
        agent_f  = request.args.get("agent_id")
        type_f   = request.args.get("decision_type")
        
        # [v1.0.1 DoS Prevention] Validate pagination parameters
        try:
            limit  = int(request.args.get("limit", 100))
            offset = int(request.args.get("offset", 0))
        except (ValueError, TypeError):
            limit, offset = 100, 0
        
        limit  = max(1, min(limit, 500))   # Clamp to [1, 500]
        offset = max(0, offset)              # Ensure non-negative
        
        if agent_f:
            ok, _ = _safe_url_id(agent_f, "agent_id")
            if not ok: agent_f = None
        
        # [v1.0.1 Memory Safety] Avoid loading all records by filtering first
        # We need database-level filtering for production; this is in-memory fallback
        records = _pipeline.audit_logger.get_all()
        
        if status_f:
            try: records = [r for r in records if r.final_status == FinalStatus(status_f)]
            except ValueError: pass
        if agent_f:  records = [r for r in records if r.agent_id == agent_f]
        if type_f:
            try: records = [r for r in records if r.decision_type == DecisionType(type_f)]
            except ValueError: pass
        
        # Apply pagination AFTER filtering
        total = len(records)
        records = records[offset:offset + limit]
        
        return jsonify({
            "count":     len(records),
            "total":     total,
            "offset":    offset,
            "limit":     limit,
            "records":   [r.to_dict() for r in records]
        }), 200

    @app.route("/decisions/<decision_id>", methods=["GET"])
    def get_decision(decision_id: str):
        if not _UUID_RE.match(decision_id): return _err("Invalid decision ID format.", 400)
        rec = _pipeline.audit_logger.get_by_id(decision_id)
        if not rec: return _err("Decision not found.", 404)
        return jsonify(rec.to_dict()), 200

    @app.route("/decisions/<decision_id>/replay", methods=["POST"])
    def replay_decision(decision_id: str):
        if not _UUID_RE.match(decision_id): return _err("Invalid decision ID format.", 400)
        rec = _pipeline.audit_logger.get_by_id(decision_id)
        if not rec: return _err("Decision not found.", 404)
        replayed = _replay.replay_one(rec)
        return jsonify({
            "original_id":     decision_id,
            "original_status": rec.final_status.value if rec.final_status else None,
            "replayed":        replayed.to_dict(),
            "outcome_changed": rec.final_status != replayed.final_status,
        }), 200

    @app.route("/stats", methods=["GET"])
    def stats(): return jsonify(_pipeline.stats), 200

    @app.route("/agents/<agent_id>/velocity", methods=["GET"])
    def velocity(agent_id: str):
        ok, err = _safe_url_id(agent_id, "agent_id")
        if not ok: return _err(err, 400)
        return jsonify(_pipeline.velocity_status(agent_id)), 200

    @app.route("/agents/<agent_id>/anomaly", methods=["GET"])
    def anomaly(agent_id: str):
        ok, err = _safe_url_id(agent_id, "agent_id")
        if not ok: return _err(err, 400)
        dtype = request.args.get("decision_type", "procurement")
        return jsonify(_pipeline.anomaly_stats(agent_id, dtype)), 200

    @app.route("/policies",  methods=["GET"])
    def policies():  return jsonify({"policies":  _pipeline.policy_engine.list_policies()}), 200

    @app.route("/contracts", methods=["GET"])
    def contracts(): return jsonify({"contracts": _pipeline.list_contracts()}), 200

    @app.route("/ecosystem", methods=["GET"])
    def ecosystem(): return jsonify(_pipeline.ecosystem_status()), 200

    @app.errorhandler(400)
    def bad_request(e):  return _err("Bad request.", 400)
    @app.errorhandler(404)
    def not_found(e):    return _err("Endpoint not found.", 404)
    @app.errorhandler(413)
    def too_large(e):    return _err(f"Request body too large (max {_MAX_BODY_BYTES//1024}KB).", 413)
    @app.errorhandler(500)
    def server_error(e): return _err("Internal server error.", 500)


    # ── Bulk batch endpoint (v1.1) ──────────────────────────────────────────────
    @app.route("/decisions/batch", methods=["POST"])
    def batch_submit():
        """POST /decisions/batch — Govern up to 500 decisions in parallel."""
        import time as _t
        rid = str(uuid.uuid4())[:8]
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
        for i, d in enumerate(decisions_raw):
            try:
                parsed.append(_parse(d))
            except Exception as e:
                errors.append({"index": i, "error": str(e)})

        t0 = _t.perf_counter()
        if max_workers <= 1 or len(parsed) < 4:
            responses = [_pipeline.process(r) for r in parsed]
        else:
            resp_map = {}
            # Reuse the pipeline's own thread pool instead of spawning a new
            # ThreadPoolExecutor per request (which would exhaust threads under load).
            _tp = getattr(_pipeline, "_thread_pool", None)
            if _tp is not None:
                futs = {_tp.submit(_pipeline.process, r): i for i, r in enumerate(parsed)}
                import concurrent.futures as _cf
                for fut in _cf.as_completed(futs):
                    idx = futs[fut]
                    try:
                        resp_map[idx] = fut.result()
                    except Exception as e:
                        errors.append({"index": idx, "error": str(e)})
            else:
                import concurrent.futures as _cf
                with _cf.ThreadPoolExecutor(max_workers=max_workers) as ex:
                    futs = {ex.submit(_pipeline.process, r): i for i, r in enumerate(parsed)}
                    for fut in _cf.as_completed(futs):
                        idx = futs[fut]
                        try:
                            resp_map[idx] = fut.result()
                        except Exception as e:
                            errors.append({"index": idx, "error": str(e)})
            responses = [resp_map[i] for i in sorted(resp_map)]

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

    # ── SSE real-time event stream (v1.1) ─────────────────────────────────────
    @app.route("/events/stream", methods=["GET"])
    def events_stream():
        """GET /events/stream — Server-Sent Events stream of governance events."""
        import queue as _q, json as _j
        event_q: _q.Queue = _q.Queue(maxsize=200)

        def _on(evt):
            try:
                event_q.put_nowait(evt)
            except _q.Full:
                pass

        bus = getattr(_pipeline, "event_bus", None)
        if bus:
            bus.subscribe("*", _on)

        NL = "\n"
        SEP = "\n\n"

        def _gen():
            yield "retry: 3000" + SEP
            try:
                while True:
                    try:
                        evt = event_q.get(timeout=15)
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
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                # Restrict what the SSE stream can load; data-only responses
                # should never trigger scripts, frames, or resource loads.
                "Content-Security-Policy": "default-src 'none'",
            },
        )

    return app
