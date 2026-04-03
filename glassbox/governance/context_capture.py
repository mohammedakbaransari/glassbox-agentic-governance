"""
GlassBox Framework - Context Capture
Enriches incoming decision requests with runtime environmental metadata
before they enter the governance pipeline.

Author: Mohammed Akbar Ansari
"""

import os
import platform
import socket
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from glassbox.governance.models import DecisionContext, DecisionRequest


def _safe_hostname() -> str:
    """
    Resolve hostname safely for all platforms.
    Precedence: K8s/container env vars → socket.gethostname() → fallback.
    Never blocks or raises.
    """
    # K8s / Docker / Databricks provide hostname via env
    for var in ("HOSTNAME", "POD_NAME", "K8S_NODE_NAME", "DB_CLUSTER_ID"):
        val = os.environ.get(var)
        if val:
            return val[:64]
    try:
        return socket.gethostname()[:64]
    except Exception:
        return "unknown-host"


class ContextCapture:
    """
    Captures and enriches decision context at the moment a request enters
    the governance layer. Adds host metadata, timestamps, and environment
    signals that are useful for audit and replay purposes.

    Platform-safe: hostname resolution uses env-var precedence so it works
    correctly on K8s pods, Databricks clusters, and Fabric notebooks where
    socket.gethostname() may return unhelpful or slow results.
    """

    def __init__(self, environment: str = "production"):
        self.environment = environment
        self.hostname    = _safe_hostname()
        self.platform    = platform.system()

    def enrich(
        self,
        request: DecisionRequest,
        request_metadata: Optional[Dict[str, Any]] = None,
    ) -> DecisionContext:
        """
        Produce an enriched DecisionContext from the incoming request.
        Merges any existing context on the request with runtime metadata.
        """
        base = request.context or DecisionContext()

        enriched_metadata = {
            "governance_entry_utc": datetime.now(timezone.utc).isoformat(),
            "host": self.hostname,
            "platform": self.platform,
            "agent_id": request.agent_id,
            "decision_type": request.decision_type.value,
        }

        if request_metadata:
            enriched_metadata.update(request_metadata)

        # Caller-supplied metadata takes precedence
        merged = {**enriched_metadata, **(base.metadata or {})}

        # Environment resolution: caller-supplied context wins; fall back to pipeline default
        env = base.environment if base.environment not in ("production", "") else self.environment
        source = base.source_system if base.source_system not in ("unknown", "") else "api"

        return DecisionContext(
            session_id=base.session_id,
            environment=env,
            source_system=source,
            user_override=base.user_override,
            confidence=base.confidence,
            agent_chain=base.agent_chain or [],
            metadata=merged,
            # v1.1: preserve caller-supplied currency and jurisdiction
            currency=getattr(base, "currency", "USD"),
            jurisdiction=getattr(base, "jurisdiction", "US"),
            patient_id=getattr(base, "patient_id", None),
            account_type=getattr(base, "account_type", "unknown"),
        )
