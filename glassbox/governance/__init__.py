"""
GlassBox Governance Framework — Core Exports

Public API for governance components:
- Velocity breakers (single-instance and distributed)
- Policy engine and evaluation
- Audit logging and decision tracking
- Risk evaluation
- Pipeline orchestration
"""

# Velocity breakers
from glassbox.governance.velocity_breaker import (
    VelocityBreaker,
    DistributedVelocityBreaker,
    RedisVelocityBreakerBackend,
    create_velocity_breaker_distributed,
)

# Core components
from glassbox.governance.pipeline import GovernancePipeline
from glassbox.governance.policy_engine import Policy, PolicyEngine
from glassbox.governance.audit_logger import AuditLogger
from glassbox.governance.risk_evaluator import RiskEvaluator
from glassbox.governance.anomaly_detector import AnomalyDetector
from glassbox.governance.models import (
    DecisionRequest,
    DecisionResponse,
    Disposition,
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
        ThreadPoolConfig,
        QueueDepthMonitor,
        AsyncWorkQueue,
        create_async_queue,
    )
    __all__.extend([
        "ThreadPoolConfig",
        "QueueDepthMonitor",
        "AsyncWorkQueue",
        "create_async_queue",
    ])
except ImportError:
    pass

# Note: EnhancedGovernancePipeline example moved to ARCHIVE/RELEASES/pipeline_v1_1_reference.py
# Can be imported from there if needed

__version__ = "1.1.0"  # Derived from git tags
__author__ = "Mohammed Akbar Ansari"
