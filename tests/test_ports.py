"""Contract tests for the GlassBox port layer (GB-002).

Ports are the seam that fixes v1's dependency-inversion score of 1/5. These tests
enforce the properties that make the seam real:

* every port is a ``Protocol`` and cannot be instantiated;
* every port method is a declaration only -- no logic leaks into the seam;
* conforming stubs satisfy the protocol and non-conforming ones do not, which is
  what lets the application layer be tested without any adapter;
* the signatures that carry an invariant actually carry it -- notably that
  :meth:`Dispatcher.dispatch` requires an evidence receipt (I1), that tenant
  parameters are never ``Optional`` with a ``None`` default, and that no port
  offers a "dependency unavailable" return value that could be mistaken for a
  permissive answer (I4).
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any, Dict, List, Optional, Protocol, get_type_hints

import pytest

import glassbox.ports as ports_package
from glassbox.domain.action import BlastRadius, ConsequenceClass, ProposedAction, ResourceRef
from glassbox.domain.catalogue import ActionCatalogueBundle, ActionDefinition, ExposureRule
from glassbox.domain.decision import (
    AuthorizationDecision,
    AuthorizationRequest,
    ExecutionOutcome,
    ExecutionStatus,
)
from glassbox.domain.errors import ActionNotGovernedError, ToolNotGovernedError
from glassbox.domain.evidence import (
    EvidenceReceipt,
    IntegrityReport,
    IntegrityStatus,
    IntentRecord,
    OutcomeRecord,
)
from glassbox.domain.identity import RawCredential, VerifiedPrincipal
from glassbox.domain.limits import LimitKey, LimitVerdict, Window
from glassbox.domain.mandate import Mandate
from glassbox.domain.risk import RiskInputs, RiskScore
from glassbox.domain.tool_registry import ToolDefinition, ToolRegistryBundle
from glassbox.ports.attestation import AttestationProvider
from glassbox.ports.baseline import Baseline, BaselineKey, BaselineStore, BaselineVerdict
from glassbox.ports.catalogue import ActionCatalogue
from glassbox.ports.clock import Clock
from glassbox.ports.dispatcher import Dispatcher
from glassbox.ports.evidence import EvidenceStore
from glassbox.ports.identity import IdentityVerifier
from glassbox.ports.keys import MacSigner
from glassbox.ports.limits import LimitStore
from glassbox.ports.mandate import MandateStore
from glassbox.ports.policy import PolicyDecisionPoint
from glassbox.ports.risk import RiskEngine
from glassbox.ports.tool_registry import ToolRegistry

PORTS_DIR = pathlib.Path(ports_package.__file__).resolve().parent

#: Every behavioural port. Value objects that happen to live in the ports package
#: (Baseline, BaselineKey, BaselineVerdict) are excluded deliberately.
ALL_PORTS = (
    ActionCatalogue,
    AttestationProvider,
    BaselineStore,
    Clock,
    Dispatcher,
    EvidenceStore,
    IdentityVerifier,
    LimitStore,
    MacSigner,
    MandateStore,
    PolicyDecisionPoint,
    RiskEngine,
    ToolRegistry,
)


# --------------------------------------------------------------------------- #
# Conforming stubs
# --------------------------------------------------------------------------- #


class FrozenClock:
    """A conforming :class:`Clock` that never advances."""

    def __init__(self, instant: float) -> None:
        self._instant = instant

    def now(self) -> float:
        return self._instant


class NullDispatcher:
    """A conforming :class:`Dispatcher` that refuses to cause any effect.

    This is the stub replay uses (GB-012): it satisfies the protocol so the
    decision service can be wired normally, and raises if anything ever tries to
    dispatch through it.
    """

    def __init__(self) -> None:
        self.calls: List[str] = []

    def dispatch(
        self,
        action: ProposedAction,
        receipt: EvidenceReceipt,
        *,
        timeout_s: float,
        now: float,
    ) -> ExecutionOutcome:
        self.calls.append(action.idempotency_key)
        raise AssertionError("NullDispatcher must never be invoked")


class RecordingEvidenceStore:
    """A conforming :class:`EvidenceStore` for application-layer tests."""

    def __init__(self) -> None:
        self.intents: List[IntentRecord] = []
        self.outcomes: List[OutcomeRecord] = []

    def append_intent(self, record: IntentRecord) -> EvidenceReceipt:
        self.intents.append(record)
        return EvidenceReceipt(
            decision_id=record.decision_id,
            segment_id=record.segment_id,
            seq=len(self.intents) - 1,
            record_hmac=b"\x00" * 32,
            signer_key_id="stub.key",
            persisted_at=record.created_at,
        )

    def append_outcome(self, receipt: EvidenceReceipt, record: OutcomeRecord) -> None:
        if receipt.decision_id != record.decision_id:
            raise ValueError("receipt and outcome describe different decisions")
        self.outcomes.append(record)

    def verify(self, segment_id: str, *, now: float) -> IntegrityReport:
        return IntegrityReport(
            segment_id=segment_id,
            status=IntegrityStatus.INTACT,
            records_checked=len(self.intents),
            verified_at=now,
        )


class StubIdentityVerifier:
    """A conforming :class:`IdentityVerifier`."""

    def __init__(self, principal: VerifiedPrincipal) -> None:
        self._principal = principal

    def verify(self, credential: RawCredential, *, now: float) -> VerifiedPrincipal:
        self._principal.require_valid_at(now)
        return self._principal

    def assert_matches_assertion(
        self,
        principal: VerifiedPrincipal,
        *,
        asserted_tenant_id: str = "",
        asserted_subject: str = "",
    ) -> None:
        if asserted_tenant_id and asserted_tenant_id != principal.tenant_id:
            raise ValueError("tenant assertion does not match the verified principal")


class StubMandateStore:
    """A conforming :class:`MandateStore`."""

    def __init__(self, mandate: Optional[Mandate] = None) -> None:
        self._mandate = mandate

    def get(self, tenant_id: str, agent_ref: str, *, now: float) -> Optional[Mandate]:
        return self._mandate

    def is_revoked(self, tenant_id: str, agent_ref: str, *, now: float) -> bool:
        return self._mandate is None or self._mandate.is_revoked_at(now)


class StubLimitStore:
    """A conforming :class:`LimitStore`."""

    def try_consume(
        self, key: LimitKey, *, cost: float, decision_id: str, now: float
    ) -> LimitVerdict:
        return LimitVerdict(admitted=True, key=key, limit=10.0, observed=cost, evaluated_at=now)

    def cumulative(self, key: LimitKey, window: Window, *, now: float) -> float:
        return 0.0

    def release(self, key: LimitKey, *, decision_id: str) -> None:
        return None


class StubBaselineStore:
    """A conforming :class:`BaselineStore`."""

    def get(self, key: BaselineKey, *, now: float) -> Optional[Baseline]:
        return None

    def evaluate(
        self,
        key: BaselineKey,
        observation: float,
        *,
        peer_group: str,
        threshold: float,
        now: float,
    ) -> BaselineVerdict:
        return BaselineVerdict(
            anomalous=False,
            key=key,
            observation=observation,
            z_score=0.0,
            threshold=threshold,
            sample_count=0,
            used_peer_prior=True,
        )

    def observe(self, key: BaselineKey, observation: float, *, now: float) -> None:
        return None


class StubMacSigner:
    """A conforming :class:`MacSigner`."""

    @property
    def key_id(self) -> str:
        return "stub.key"

    def mac(self, payload: bytes) -> bytes:
        return b"\x00" * 32

    def verify(self, payload: bytes, mac: bytes, *, key_id: str) -> bool:
        return mac == b"\x00" * 32


class StubPolicyDecisionPoint:
    """A conforming :class:`PolicyDecisionPoint`."""

    def decide(self, request: AuthorizationRequest) -> AuthorizationDecision:
        return AuthorizationDecision.allow(
            rationale="stub",
            policy_bundle_id="stub.bundle",
            policy_bundle_sha256="0" * 64,
        )

    def active_bundle_digest(self, tenant_id: str) -> str:
        return "0" * 64


class StubRiskEngine:
    """A conforming :class:`RiskEngine`."""

    @property
    def model_version(self) -> str:
        return "stub-v0"

    def score(self, inputs: RiskInputs) -> RiskScore:
        return RiskScore(
            value=0.0, model_version=self.model_version, inputs=inputs
        ).with_consequence_floor()

    def score_with_model(self, inputs: RiskInputs, model_version: str) -> RiskScore:
        return RiskScore(value=0.0, model_version=model_version, inputs=inputs)


class StubActionCatalogue:
    """A conforming :class:`ActionCatalogue` backed by one fixed bundle."""

    def __init__(self) -> None:
        self._bundle = ActionCatalogueBundle(
            bundle_id="stub.bundle",
            tenant_id="acme",
            version=1,
            definitions=(
                ActionDefinition(
                    action="payments.wire_transfer",
                    consequence=ConsequenceClass.COMPENSABLE,
                    exposure_rule=ExposureRule(
                        blast_radius=BlastRadius.SINGLE, monetary_field="amount"
                    ),
                ),
            ),
        )

    def resolve(self, tenant_id: str, action: str) -> ActionDefinition:
        definition = self._bundle.resolve(action)
        if definition is None:
            raise ActionNotGovernedError("not governed", tenant_id=tenant_id, action=action)
        return definition

    def active_bundle_digest(self, tenant_id: str) -> str:
        return self._bundle.digest()


class StubAttestationProvider:
    """A conforming :class:`AttestationProvider` that resolves everything ``True``."""

    def resolve(self, tenant_id: str, resource: ResourceRef, name: str, *, now: float) -> bool:
        return True


class StubToolRegistry:
    """A conforming :class:`ToolRegistry` backed by one fixed bundle."""

    def __init__(self) -> None:
        self._bundle = ToolRegistryBundle(
            bundle_id="stub.bundle",
            tenant_id="acme",
            version=1,
            definitions=(
                ToolDefinition(
                    tool_name="mcp.send_email",
                    definition_sha256="a" * 64,
                    action=ActionDefinition(
                        action="mcp.send_email", consequence=ConsequenceClass.REVERSIBLE
                    ),
                ),
            ),
        )

    def resolve(self, tenant_id: str, tool_name: str, definition_sha256: str) -> ToolDefinition:
        definition = self._bundle.resolve(tool_name)
        if definition is None or definition.definition_sha256 != definition_sha256.lower():
            raise ToolNotGovernedError("not governed", tenant_id=tenant_id, tool_name=tool_name)
        return definition

    def active_bundle_digest(self, tenant_id: str) -> str:
        return self._bundle.digest()


CONFORMING: Dict[Any, Any] = {
    ActionCatalogue: StubActionCatalogue(),
    AttestationProvider: StubAttestationProvider(),
    BaselineStore: StubBaselineStore(),
    Clock: FrozenClock(1_760_000_000.0),
    Dispatcher: NullDispatcher(),
    EvidenceStore: RecordingEvidenceStore(),
    LimitStore: StubLimitStore(),
    MacSigner: StubMacSigner(),
    MandateStore: StubMandateStore(),
    PolicyDecisionPoint: StubPolicyDecisionPoint(),
    RiskEngine: StubRiskEngine(),
    ToolRegistry: StubToolRegistry(),
}


# --------------------------------------------------------------------------- #
# Protocol shape
# --------------------------------------------------------------------------- #


class TestProtocolShape:
    """Ports declare behaviour; they hold no state and no logic."""

    @pytest.mark.parametrize("port", ALL_PORTS, ids=lambda port: port.__name__)
    def test_port_is_a_protocol(self, port: type) -> None:
        assert issubclass(port, Protocol)  # type: ignore[arg-type]
        assert getattr(port, "_is_protocol", False) is True

    @pytest.mark.parametrize("port", ALL_PORTS, ids=lambda port: port.__name__)
    def test_port_is_runtime_checkable(self, port: type) -> None:
        """Needed so composition-root wiring can assert conformance at startup."""
        assert getattr(port, "_is_runtime_protocol", False) is True

    @pytest.mark.parametrize("port", ALL_PORTS, ids=lambda port: port.__name__)
    def test_port_cannot_be_instantiated(self, port: type) -> None:
        with pytest.raises(TypeError):
            port()  # type: ignore[misc]

    @pytest.mark.parametrize("port", ALL_PORTS, ids=lambda port: port.__name__)
    def test_port_declares_at_least_one_method(self, port: type) -> None:
        members = [
            name
            for name in vars(port)
            if not name.startswith("_") and callable(getattr(port, name, None))
        ]
        properties = [name for name, value in vars(port).items() if isinstance(value, property)]
        assert members or properties, f"{port.__name__} declares no behaviour"

    @pytest.mark.parametrize("port", ALL_PORTS, ids=lambda port: port.__name__)
    def test_every_method_is_documented(self, port: type) -> None:
        """A port docstring is the contract; an undocumented method has none."""
        for name, member in vars(port).items():
            if name.startswith("_"):
                continue
            target = member.fget if isinstance(member, property) else member
            if not callable(target):
                continue
            assert target.__doc__, f"{port.__name__}.{name} has no docstring"

    def test_port_bodies_contain_no_logic(self) -> None:
        """A method body other than a docstring plus `...` is logic in the seam."""
        offenders: List[str] = []
        for source_file in sorted(PORTS_DIR.glob("*.py")):
            tree = ast.parse(source_file.read_text(encoding="utf-8"))
            for class_node in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
                is_protocol = any(
                    isinstance(base, ast.Name) and base.id == "Protocol"
                    for base in class_node.bases
                )
                if not is_protocol:
                    continue
                for func in [n for n in class_node.body if isinstance(n, ast.FunctionDef)]:
                    body = [
                        node
                        for node in func.body
                        if not (
                            isinstance(node, ast.Expr)
                            and isinstance(node.value, ast.Constant)
                            and isinstance(node.value.value, str)
                        )
                    ]
                    if len(body) != 1 or not (
                        isinstance(body[0], ast.Expr)
                        and isinstance(body[0].value, ast.Constant)
                        and body[0].value.value is Ellipsis
                    ):
                        offenders.append(f"{source_file.name}:{class_node.name}.{func.name}")
        assert not offenders, f"port methods contain logic: {offenders}"


# --------------------------------------------------------------------------- #
# Conformance
# --------------------------------------------------------------------------- #


class TestConformance:
    """Stubs satisfy the protocols; incomplete implementations do not."""

    @pytest.mark.parametrize(
        ("port", "stub"), list(CONFORMING.items()), ids=lambda value: getattr(value, "__name__", "")
    )
    def test_conforming_stub_satisfies_the_protocol(self, port: type, stub: Any) -> None:
        assert isinstance(stub, port)

    def test_missing_method_fails_conformance(self) -> None:
        class HalfBakedEvidenceStore:
            def append_intent(self, record: IntentRecord) -> EvidenceReceipt:
                raise NotImplementedError

        assert not isinstance(HalfBakedEvidenceStore(), EvidenceStore)

    def test_identity_verifier_stub_enforces_the_credential_window(self) -> None:
        from tests.test_domain import make_principal

        verifier = StubIdentityVerifier(make_principal())
        credential = RawCredential(
            credential_type=make_principal().credential_type,
            material="token",
            presented_at=1_760_000_000.0,
        )
        assert verifier.verify(credential, now=1_760_000_000.0).tenant_id == "acme"
        with pytest.raises(Exception):
            verifier.verify(credential, now=1_760_000_000.0 + 7200.0)

    def test_identity_verifier_stub_rejects_a_spoofed_tenant_assertion(self) -> None:
        from tests.test_domain import make_principal

        principal = make_principal()
        verifier = StubIdentityVerifier(principal)
        verifier.assert_matches_assertion(principal, asserted_tenant_id="acme")
        with pytest.raises(ValueError):
            verifier.assert_matches_assertion(principal, asserted_tenant_id="evilcorp")


# --------------------------------------------------------------------------- #
# Signatures that carry invariants
# --------------------------------------------------------------------------- #


class TestInvariantBearingSignatures:
    """Some invariants are enforced by a parameter list, not by a runtime check."""

    def test_dispatch_requires_an_evidence_receipt(self) -> None:
        """Invariant I1: there is no signature that dispatches without a receipt."""
        signature = inspect.signature(Dispatcher.dispatch)
        assert "receipt" in signature.parameters
        receipt_param = signature.parameters["receipt"]
        assert receipt_param.default is inspect.Parameter.empty
        hints = get_type_hints(Dispatcher.dispatch)
        assert hints["receipt"] is EvidenceReceipt

    def test_append_intent_returns_a_receipt_not_a_boolean(self) -> None:
        """A bool return would let a caller ignore a failed write."""
        assert get_type_hints(EvidenceStore.append_intent)["return"] is EvidenceReceipt

    @pytest.mark.parametrize(
        ("port", "method"),
        [
            (MandateStore, "get"),
            (MandateStore, "is_revoked"),
        ],
    )
    def test_tenant_parameters_are_required_and_not_optional(self, port: type, method: str) -> None:
        """Regression: v1's query(tenant_id=None) dropped the tenant predicate."""
        parameter = inspect.signature(getattr(port, method)).parameters["tenant_id"]
        assert parameter.default is inspect.Parameter.empty
        assert get_type_hints(getattr(port, method))["tenant_id"] is str

    def test_no_port_method_defaults_a_time_argument(self) -> None:
        """Invariant I6: time is injected, never defaulted to a live clock read."""
        offenders: List[str] = []
        for port in ALL_PORTS:
            for name, member in vars(port).items():
                if name.startswith("_") or not callable(member):
                    continue
                parameters = inspect.signature(member).parameters
                if "now" in parameters and parameters["now"].default is not inspect.Parameter.empty:
                    offenders.append(f"{port.__name__}.{name}")
        assert not offenders, f"time arguments must not have defaults: {offenders}"

    def test_limit_verdict_has_no_unavailable_state(self) -> None:
        """Invariant I4: an outage is an exception, not a permissive verdict."""
        fields = set(LimitVerdict.__dataclass_fields__)
        assert not fields & {"unavailable", "degraded", "unknown", "fail_open"}

    def test_limit_store_declares_no_fail_open_helper(self) -> None:
        names = {name for name in vars(LimitStore) if not name.startswith("_")}
        assert not {name for name in names if "fail_open" in name or "bypass" in name}

    def test_risk_engine_exposes_a_model_version(self) -> None:
        """Without a pinned version, a replay cannot reproduce a historical score."""
        assert isinstance(vars(RiskEngine)["model_version"], property)
        assert "score_with_model" in vars(RiskEngine)

    def test_mac_signer_exposes_a_key_id_for_rotation(self) -> None:
        assert isinstance(vars(MacSigner)["key_id"], property)
        assert "key_id" in inspect.signature(MacSigner.verify).parameters


class TestNullDispatcherSafety:
    """The replay dispatcher must be structurally incapable of causing an effect."""

    def test_null_dispatcher_conforms_but_always_raises(self) -> None:
        from tests.test_domain import make_action

        dispatcher = NullDispatcher()
        assert isinstance(dispatcher, Dispatcher)
        receipt = EvidenceReceipt(
            decision_id="decision-1",
            segment_id="seg-1",
            seq=0,
            record_hmac=b"\x00" * 32,
            signer_key_id="stub.key",
            persisted_at=1_760_000_000.0,
        )
        with pytest.raises(AssertionError):
            dispatcher.dispatch(make_action(), receipt, timeout_s=1.0, now=1_760_000_000.0)


class TestStubBehaviour:
    """The stubs are test infrastructure for later waves; they must be correct."""

    def test_recording_store_rejects_a_mismatched_receipt(self) -> None:
        from tests.test_domain import make_intent

        store = RecordingEvidenceStore()
        receipt = store.append_intent(make_intent())
        wrong = OutcomeRecord(
            decision_id="decision-9999",
            outcome=ExecutionOutcome(status=ExecutionStatus.EXECUTED, completed_at=1_760_000_000.0),
        )
        with pytest.raises(ValueError):
            store.append_outcome(receipt, wrong)

    def test_recording_store_allocates_monotonic_sequence_numbers(self) -> None:
        from tests.test_domain import make_intent

        store = RecordingEvidenceStore()
        first = store.append_intent(make_intent(decision_id="decision-1"))
        second = store.append_intent(make_intent(decision_id="decision-2"))
        assert (first.seq, second.seq) == (0, 1)

    def test_stub_risk_engine_applies_the_consequence_floor(self) -> None:
        from glassbox.domain.action import ConsequenceClass, Exposure
        from glassbox.domain.risk import RiskLevel

        inputs = RiskInputs(
            consequence=ConsequenceClass.IRREVERSIBLE,
            exposure=Exposure(monetary=1.0),
            evaluated_at=1_760_000_000.0,
        )
        assert StubRiskEngine().score(inputs).level is RiskLevel.HIGH

    def test_frozen_clock_never_advances(self) -> None:
        clock = FrozenClock(1_760_000_000.0)
        assert clock.now() == clock.now() == 1_760_000_000.0
        assert isinstance(clock, Clock)
