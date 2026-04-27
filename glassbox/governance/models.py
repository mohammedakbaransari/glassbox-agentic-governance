"""
GlassBox Framework — Core Data Models  (v1.1.0)
Zero external dependencies — Python stdlib only.

Formal decision model:  D = (τ, P, C, L)
  τ = decision_type, P = payload, C = context, L = lineage (agent_chain)

Governance function:  G(D) → {EXECUTE, REVIEW, BLOCK}
  EXECUTE  iff risk_score ≤ 35  ∧ policy_result.passed
  REVIEW   iff 35 < risk_score ≤ 70  ∧ policy_result.passed
  BLOCK    iff ¬policy_result.passed  ∨ risk_score > 70

Author: Mohammed Akbar Ansari
"""
from __future__ import annotations
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


class DecisionType(str, Enum):
    # Original 8 types
    PROCUREMENT = "procurement"
    PRICING     = "pricing"
    INVENTORY   = "inventory"
    FINANCIAL   = "financial"
    LOGISTICS   = "logistics"
    IT_OPS      = "it_ops"
    HR          = "hr"
    CUSTOM      = "custom"
    # v1.1 additions — new operational domains
    CLINICAL    = "clinical"    # Healthcare: prescriptions, dosage, procedures
    TRADING     = "trading"     # Financial markets: orders, positions, hedges
    CONTENT     = "content"     # Generative AI output governance (GDPR Art.22)
    LEGAL       = "legal"       # Contract AI, e-discovery, compliance filings

class RiskLevel(str, Enum):
    LOW      = "low"
    MEDIUM   = "medium"
    HIGH     = "high"
    CRITICAL = "critical"

class Disposition(str, Enum):
    AUTO_EXECUTE  = "auto_execute"
    HUMAN_REVIEW  = "human_review"
    BLOCK         = "block"
    APPROVED      = "approved"

class FinalStatus(str, Enum):
    EXECUTED       = "executed"
    PENDING_REVIEW = "pending_review"
    BLOCKED        = "blocked"
    REPLAYED       = "replayed"

class WorkflowStatus(str, Enum):
    PENDING    = "pending"
    IN_REVIEW  = "in_review"
    APPROVED   = "approved"
    REJECTED   = "rejected"
    ESCALATED  = "escalated"
    TIMED_OUT  = "timed_out"


class PolicyStatus(str, Enum):
    DRAFT      = "draft"
    ACTIVE     = "active"
    DEPRECATED = "deprecated"
    ARCHIVED   = "archived"


class RetryStrategy(str, Enum):
    NONE               = "none"
    FIXED              = "fixed"
    EXPONENTIAL        = "exponential"
    EXPONENTIAL_JITTER = "exponential_jitter"


@dataclass
class RetryConfig:
    """Retry-with-backoff settings for downstream executor calls."""
    strategy:             RetryStrategy = RetryStrategy.EXPONENTIAL_JITTER
    max_attempts:         int   = 3
    base_delay_s:         float = 0.5
    max_delay_s:          float = 10.0
    backoff_factor:       float = 2.0
    # OSError covers network/socket failures (BrokenPipeError, ConnectionResetError,
    # etc.) which are subtypes on all major platforms.
    retryable_exceptions: tuple = (ConnectionError, TimeoutError, OSError)

    def to_dict(self) -> Dict[str, Any]:
        return {"strategy": self.strategy.value, "max_attempts": self.max_attempts,
                "base_delay_s": self.base_delay_s, "max_delay_s": self.max_delay_s,
                "backoff_factor": self.backoff_factor}


@dataclass
class LogConfig:
    """Structured logging — stdlib only, no external deps."""
    level:           str           = "INFO"
    format:          str           = "json"
    log_dir:         Optional[str] = None
    max_bytes:       int           = 10 * 1024 * 1024
    backup_count:    int           = 5
    include_payload: bool          = False
    include_audit:   bool          = True


@dataclass
class EcosystemBreakerConfig:
    """Cross-agent aggregate velocity circuit breaker."""
    enabled:          bool = True
    max_decisions:    int  = 100
    window_seconds:   int  = 60
    cooldown_seconds: int  = 120


@dataclass
class AgentContract:
    """
    Per-agent governance boundary — Stage 0 of the pipeline.
    Restricts decision types, transaction authority, and delegation depth.
    """
    agent_id:                str
    permitted_types:         List[DecisionType]
    max_amount:              float = 999_999_999.0
    requires_approval_above: float = 999_999_999.0
    delegation_allowed:      bool  = True
    max_delegation_depth:    int   = 5
    description:             str   = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"agent_id": self.agent_id,
                "permitted_types": [t.value for t in self.permitted_types],
                "max_amount": self.max_amount,
                "requires_approval_above": self.requires_approval_above,
                "delegation_allowed": self.delegation_allowed,
                "max_delegation_depth": self.max_delegation_depth,
                "description": self.description}


@dataclass
class DecisionContext:
    session_id:    str            = field(default_factory=lambda: str(uuid.uuid4()))
    environment:   str            = "production"
    source_system: str            = "unknown"
    user_override: bool           = False
    confidence:    float          = 1.0
    agent_chain:   List[str]      = field(default_factory=list)
    metadata:      Dict[str, Any] = field(default_factory=dict)
    # v1.1 additions
    currency:      str            = "USD"        # ISO 4217 currency code
    jurisdiction:  str            = "US"         # ISO 3166-1 country code
    patient_id:    Optional[str]  = None         # Healthcare context
    account_type:  str            = "unknown"    # Financial context (retail/institutional)

    @property
    def delegation_depth(self) -> int:
        return len(self.agent_chain)


@dataclass
class DecisionRequest:
    agent_id:      str
    decision_type: DecisionType = DecisionType.CUSTOM
    payload:       Dict[str, Any] = field(default_factory=dict)
    context:       Optional[DecisionContext] = None
    request_id:    str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class PolicyEvaluation:
    policy_id:   str
    policy_name: str = ""
    result:      str = ""
    message:     str = ""
    compliant:   bool = True
    reasoning:   str = ""

@dataclass
class PolicyResult:
    passed:             bool
    evaluated_policies: List[PolicyEvaluation] = field(default_factory=list)
    violations:         List[str]              = field(default_factory=list)
    warnings:           List[str]              = field(default_factory=list)

@dataclass
class RiskFactor:
    factor: str
    score:  float
    weight: float

@dataclass
class RiskResult:
    risk_score:  float
    risk_level:  RiskLevel
    disposition: Disposition
    factors:     List[RiskFactor] = field(default_factory=list)

@dataclass
class CircuitBreakerResult:
    triggered:        bool
    breaker_name:     Optional[str]   = None
    reason:           Optional[str]   = None
    velocity_count:   Optional[int]   = None
    anomaly_score:    Optional[float] = None
    anomalous_fields: List[str]       = field(default_factory=list)
    is_ecosystem:     bool            = False

@dataclass
class ExecutionResult:
    success:        bool = True
    result:         Optional[Dict[str, Any]] = None
    error:          Optional[str]            = None
    attempts:       int                      = 1
    total_delay_ms: float                    = 0.0
    response:       Optional[Any]            = None
    decision_time_ms: float                  = 0.0
    trace:          Optional[Any]            = None


@dataclass
class AuditRecord:
    agent_id:      str
    decision_type: DecisionType
    payload:       Dict[str, Any]
    context:       DecisionContext
    decision_id:   str  = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp:     str  = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    policy_result:          Optional[PolicyResult]         = None
    risk_result:            Optional[RiskResult]           = None
    circuit_breaker_result: Optional[CircuitBreakerResult] = None
    execution_result:       Optional[ExecutionResult]      = None
    final_status:           Optional[FinalStatus]          = None
    reviewer:               Optional[str]                  = None
    review_outcome:         Optional[str]                  = None
    review_timestamp:       Optional[str]                  = None
    replay_of:              Optional[str]                  = None
    pipeline_latency_ms:    Optional[float]                = None
    contract_validated:     bool                           = False

    def to_dict(self) -> Dict[str, Any]:
        def _v(v: Any, _seen: set = None) -> Any:
            if _seen is None:
                _seen = set()
            if isinstance(v, Enum): return v.value
            if isinstance(v, list): return [_v(i, _seen) for i in v]
            if isinstance(v, dict): return {k: _v(vv, _seen) for k, vv in v.items()}
            if hasattr(v, "__dataclass_fields__"):
                obj_id = id(v)
                if obj_id in _seen:
                    return f"<circular ref: {type(v).__name__}>"
                _seen = _seen | {obj_id}
                return {k: _v(getattr(v, k), _seen) for k in v.__dataclass_fields__}
            return v
        return {k: _v(getattr(self, k)) for k in self.__dataclass_fields__}


@dataclass
class DecisionResponse:
    decision_id:               str
    final_status:              FinalStatus
    request_id:                Optional[str]         = None
    risk_level:                Optional[RiskLevel]   = None
    risk_score:                Optional[float]       = None
    disposition:               Optional[Disposition] = None
    policy_violations:         List[str]             = field(default_factory=list)
    policy_warnings:           List[str]             = field(default_factory=list)
    circuit_breaker_triggered: bool                  = False
    circuit_breaker_reason:    Optional[str]         = None
    ecosystem_breaker:         bool                  = False
    message:                   str                   = ""
    reasoning:                 str                   = ""
    pipeline_latency_ms:       Optional[float]       = None
    retry_attempts:            int                   = 0
    audit_record:              Optional[AuditRecord] = None
    execution_trace:           Optional[Any]         = None
    # v1.1 additions
    risk_explanation:          Optional[str]         = None
    explanation:               Optional[str]         = None

    def to_dict(self) -> Dict[str, Any]:
        def _v(v: Any, _seen: set = None) -> Any:
            if _seen is None:
                _seen = set()
            if isinstance(v, Enum): return v.value
            if isinstance(v, list): return [_v(i, _seen) for i in v]
            if isinstance(v, dict): return {k: _v(vv, _seen) for k, vv in v.items()}
            if hasattr(v, "__dataclass_fields__"):
                obj_id = id(v)
                if obj_id in _seen:
                    return f"<circular ref: {type(v).__name__}>"
                _seen = _seen | {obj_id}
                return {k: _v(getattr(v, k), _seen) for k in v.__dataclass_fields__}
            return v
        d = {k: _v(getattr(self, k)) for k in self.__dataclass_fields__ if k != "audit_record"}
        if self.audit_record: d["audit_record"] = self.audit_record.to_dict()
        return d
