"""The v2 HTTP surface (GB-026a).

``api/app.py`` (v1) built a ``GovernancePipeline`` directly inside
``create_app()`` and made tenancy a transport-layer *toggle*
(``tenant_scoping_required``, defaulting ``False``). This module is the
structural fix: it takes an already-composed :class:`GovernanceRuntime` --
built once, by the composition root, the only place allowed to name a concrete
adapter -- and does exactly three things with it, for every request:

1. extract the transport-presented credential material (never trusted, just
   forwarded to :class:`~glassbox.ports.identity.IdentityVerifier` for
   cryptographic verification);
2. call one :class:`~glassbox.app.decision_service.DecisionService` method;
3. serialise the result.

No policy, no risk, no tenancy logic lives here. Tenancy in particular is not a
setting this layer can hold an opinion about: it is a field of the verified
principal, checked by the service, on every path, unconditionally.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple

from flask import Flask, Response, jsonify, request

from glassbox.app.composition import GovernanceRuntime
from glassbox.app.decision_service import DecisionOutcome, DecisionService
from glassbox.app.observability import get_logger, log_error
from glassbox.domain.action import (
    BlastRadius,
    ConsequenceClass,
    Exposure,
    ProposedAction,
    ResourceRef,
)
from glassbox.domain.decision import ExecutionStatus
from glassbox.domain.errors import DomainValidationError, GlassBoxError, IdentityError
from glassbox.domain.identity import CredentialType, RawCredential, VerifiedPrincipal

__all__ = ["create_app"]

_logger = get_logger("http")

#: HTTP status for each error type this layer is expected to see. Anything not
#: listed maps to 500 -- an unmapped domain error is a defect in this table, not
#: a reason to guess at a status code.
_ERROR_STATUS: Mapping[str, int] = {
    "IdentityError": 401,
    "CredentialExpiredError": 401,
    "DelegationError": 401,
    "DomainValidationError": 400,
    "EvidenceWriteError": 503,
    "SigningUnavailableError": 503,
}


def create_app(runtime: GovernanceRuntime) -> Flask:
    """Build the Flask app for one wired :class:`GovernanceRuntime`.

    Args:
        runtime: A fully composed, port-conforming object graph, normally from
            :func:`~glassbox.app.composition.build_runtime`. Exactly one runtime
            per process; nothing here constructs or swaps a collaborator.
    """
    from glassbox.adapters.outbound.otel.configure import configure_otel

    configure_otel(runtime.config)

    app = Flask(__name__)
    service = DecisionService(runtime)

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify(runtime.describe())

    @app.post("/v2/actions/<action_name>")
    def decide_action(action_name: str) -> Tuple[Response, int]:
        try:
            body = _json_body(request)
            outcome = service.decide_and_dispatch_for_request(
                _credential_from(request, now=runtime.clock.now()),
                action_name=action_name,
                resource=_resource_from(body),
                parameters=_mapping(body.get("parameters")),
                idempotency_key=_require_str(body, "idempotency_key"),
                causation_id=body.get("causation_id"),
                asserted_tenant_id=request.headers.get("X-Tenant-ID", ""),
                asserted_subject=request.headers.get("X-Subject-Id", ""),
            )
            return jsonify(_serialise(outcome)), _status_for(outcome)
        except GlassBoxError as exc:
            return _error_response(exc)

    @app.post("/v2/tools/<tool_name>")
    def invoke_tool(tool_name: str) -> Tuple[Response, int]:
        try:
            body = _json_body(request)
            outcome = service.decide_and_dispatch_for_tool_call(
                _credential_from(request, now=runtime.clock.now()),
                tool_name=tool_name,
                definition_sha256=_require_str(body, "definition_sha256"),
                resource=_resource_from(body),
                parameters=_mapping(body.get("parameters")),
                idempotency_key=_require_str(body, "idempotency_key"),
                causation_id=body.get("causation_id"),
                asserted_tenant_id=request.headers.get("X-Tenant-ID", ""),
                asserted_subject=request.headers.get("X-Subject-Id", ""),
            )
            return jsonify(_serialise(outcome)), _status_for(outcome)
        except GlassBoxError as exc:
            return _error_response(exc)

    @app.post("/v2/replay")
    def replay() -> Tuple[Response, int]:
        """Re-evaluate a historical decision's inputs without dispatching (GB-012).

        The caller supplies the historical principal and action fields directly
        -- reconstructed, per :meth:`DecisionService.replay`'s contract, from
        whatever evidence export the caller already holds. This route performs
        no identity verification and accepts no live credential: replaying a
        past decision is not the same operation as authenticating a new one.
        """
        try:
            body = _json_body(request)
            outcome = service.replay(
                _principal_from(_mapping(body.get("principal"))),
                _action_from(_mapping(body.get("action"))),
                causation_id=body.get("causation_id"),
            )
            return jsonify(_serialise(outcome)), 200
        except GlassBoxError as exc:
            return _error_response(exc)

    return app


# --------------------------------------------------------------------------- #
# Request parsing -- transport shape only, no governance meaning is assigned
# --------------------------------------------------------------------------- #


def _json_body(req: Any) -> Mapping[str, Any]:
    body = req.get_json(silent=True)
    if not isinstance(body, dict):
        raise DomainValidationError("request body must be a JSON object")
    return body


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _require_str(body: Mapping[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value:
        raise DomainValidationError(
            f"'{key}' is required and must be a non-empty string", field=key
        )
    return value


def _credential_from(req: Any, *, now: float) -> RawCredential:
    """Extract unverified credential material. Verification happens in the
    service, via the :class:`~glassbox.ports.identity.IdentityVerifier` port --
    nothing here is trusted, only forwarded."""
    auth = req.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return RawCredential(
            credential_type=CredentialType.OIDC, material=auth[len("Bearer ") :], presented_at=now
        )
    client_cert = req.headers.get("X-Client-Cert")
    if client_cert:
        return RawCredential(
            credential_type=CredentialType.MTLS, material=client_cert, presented_at=now
        )
    raise IdentityError("no credential presented", header="Authorization")


def _resource_from(body: Mapping[str, Any]) -> ResourceRef:
    raw = _mapping(body.get("resource"))
    if not raw:
        raise DomainValidationError("'resource' is required", field="resource")
    return ResourceRef(
        kind=_require_str(raw, "kind"),
        id=_require_str(raw, "id"),
        tenant_id=_require_str(raw, "tenant_id"),
    )


def _exposure_from(body: Mapping[str, Any]) -> Exposure:
    raw = _mapping(body.get("exposure"))
    return Exposure(
        blast_radius=BlastRadius(raw.get("blast_radius", "single")),
        monetary=raw.get("monetary"),
        records=raw.get("records"),
    )


def _principal_from(raw: Mapping[str, Any]) -> VerifiedPrincipal:
    return VerifiedPrincipal(
        agent_ref=_require_str(raw, "agent_ref"),
        agent_instance_id=_require_str(raw, "agent_instance_id"),
        tenant_id=_require_str(raw, "tenant_id"),
        credential_type=CredentialType(_require_str(raw, "credential_type")),
        credential_id=_require_str(raw, "credential_id"),
        issued_at=float(raw.get("issued_at", 0.0)),
        expires_at=float(raw.get("expires_at", 0.0)),
        delegating_subject=raw.get("delegating_subject"),
    )


def _action_from(raw: Mapping[str, Any]) -> ProposedAction:
    return ProposedAction(
        action=_require_str(raw, "action"),
        resource=_resource_from(raw),
        consequence=ConsequenceClass(_require_str(raw, "consequence")),
        exposure=_exposure_from(raw),
        idempotency_key=_require_str(raw, "idempotency_key"),
        parameters=tuple(sorted(_mapping(raw.get("parameters")).items())),
    )


# --------------------------------------------------------------------------- #
# Response shaping
# --------------------------------------------------------------------------- #


def _serialise(outcome: DecisionOutcome) -> Mapping[str, Any]:
    return {
        "decision_id": outcome.decision_id,
        "decision": outcome.decision.as_evidence(),
        "receipt": {
            "decision_id": outcome.receipt.decision_id,
            "segment_id": outcome.receipt.segment_id,
            "seq": outcome.receipt.seq,
            "signer_key_id": outcome.receipt.signer_key_id,
            "persisted_at": outcome.receipt.persisted_at,
        },
        "execution": {
            "status": outcome.execution.status.value,
            "completed_at": outcome.execution.completed_at,
            "result_digest": outcome.execution.result_digest,
            "error_class": outcome.execution.error_class,
        },
    }


def _status_for(outcome: DecisionOutcome) -> int:
    if outcome.decision.is_denied:
        return 403
    if outcome.execution.status is ExecutionStatus.PENDING_APPROVAL:
        return 202
    return 200


def _error_response(exc: GlassBoxError) -> Tuple[Response, int]:
    status = _ERROR_STATUS.get(type(exc).__name__, 500)
    log_error(_logger, exc, message="request refused")
    return jsonify(exc.as_dict()), status
