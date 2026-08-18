"""
GlassBox Governance Framework — Core Exports

Public API for governance components:
- Velocity breakers (single-instance and distributed)
- Policy engine and evaluation
- Audit logging and decision tracking
- Risk evaluation
- Pipeline orchestration
"""

from glassbox.governance.anomaly_detector import AnomalyDetector
from glassbox.governance.audit_logger import AuditLogger
from glassbox.governance.models import (
    DecisionRequest,
    DecisionResponse,
    Disposition,
)

# Core components
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.risk_evaluator import RiskEvaluator

# Velocity breakers
from glassbox.governance.velocity_breaker import (
    DistributedVelocityBreaker,
    RedisVelocityBreakerBackend,
    VelocityBreaker,
    create_velocity_breaker_distributed,
)

__all__ = [
    # Velocity breakers
    "VelocityBreaker",
    "DistributedVelocityBreaker",
    "RedisVelocityBreakerBackend",
    "create_velocity_breaker_distributed",
    # Pipeline
    "GovernancePipeline",
    # Components
    "Policy",
    "PolicyEngine",
    "AuditLogger",
    "RiskEvaluator",
    "AnomalyDetector",
    # Models
    "DecisionRequest",
    "DecisionResponse",
    "Disposition",
]

# v1.0.1+: Thread pool & queue monitoring
try:
    from glassbox.governance.threadpool_config import (
        AsyncWorkQueue,
        QueueDepthMonitor,
        ThreadPoolConfig,
        create_async_queue,
    )

    __all__.extend(
        [
            "ThreadPoolConfig",
            "QueueDepthMonitor",
            "AsyncWorkQueue",
            "create_async_queue",
        ]
    )
except ImportError:
    pass

from glassbox.governance.access_control import AccessControl
from glassbox.governance.advanced_audit import TamperEvidentAuditLogger

# v1.1.0+: Enterprise components
from glassbox.governance.enterprise_pipeline import EnterpriseGovernancePipeline
from glassbox.governance.policy_parameters import PolicyParameterStore
from glassbox.governance.request_context import RequestContext

__all__.extend(
    [
        "EnterpriseGovernancePipeline",
        "TamperEvidentAuditLogger",
        "AccessControl",
        "RequestContext",
        "PolicyParameterStore",
    ]
)

# CryptoManager requires the 'cryptography' package (optional)
try:
    from glassbox.governance.encryption import CryptoManager, EncryptedField, SecretManager

    __all__.extend(["CryptoManager", "SecretManager", "EncryptedField"])
except (ImportError, RuntimeError):
    pass

__version__ = "1.2.0"  # Derived from git tags
__author__ = "Mohammed Akbar Ansari"
