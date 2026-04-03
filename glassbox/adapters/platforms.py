"""
GlassBox Platform Adapters  (v1.0.0)
Ready-to-use adapters for major deployment platforms.

Adapters provide:
  - Platform-specific configuration helpers
  - Log path resolution (DBFS, K8s PVC, Azure ADLS, local)
  - Environment detection
  - Health/readiness endpoint helpers

Adapters are entirely optional — GlassBox works without them.
They exist to reduce boilerplate in platform-specific deployments.

Usage:
    # Databricks
    from glassbox.adapters.databricks import DatabricksAdapter
    pipeline = DatabricksAdapter().create_pipeline()

    # Kubernetes
    from glassbox.adapters.kubernetes import KubernetesAdapter
    pipeline = KubernetesAdapter().create_pipeline()

    # Microsoft Fabric
    from glassbox.adapters.fabric import FabricAdapter
    pipeline = FabricAdapter().create_pipeline()

Author: Mohammed Akbar Ansari
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional


# ── Base Adapter ──────────────────────────────────────────────────────────────

class BaseAdapter:
    """
    Base class for all platform adapters.
    Subclass and override _detect(), _log_dir(), _env_name() as needed.
    """

    platform_name: str = "generic"

    def detect(self) -> bool:
        """Return True if this adapter's platform is detected."""
        return self._detect()

    def _detect(self) -> bool:
        return True

    def _log_dir(self) -> Optional[str]:
        return os.environ.get("GLASSBOX_LOG_DIR")

    def _env_name(self) -> str:
        return os.environ.get("GLASSBOX_ENV", "production")

    def _max_memory(self) -> int:
        return int(os.environ.get("GLASSBOX_MAX_MEMORY_RECORDS", "100000"))

    def _echo(self) -> bool:
        return os.environ.get("GLASSBOX_ECHO", "false").lower() == "true"

    def get_config(self) -> Dict[str, Any]:
        """Return configuration dict for GovernancePipeline constructor."""
        return {
            "log_dir":            self._log_dir(),
            "environment":        self._env_name(),
            "max_memory_records": self._max_memory(),
            "echo":               self._echo(),
        }

    def create_pipeline(self, **overrides):
        """Create a fully configured GovernancePipeline for this platform."""
        from glassbox.governance.pipeline import GovernancePipeline
        cfg = {**self.get_config(), **overrides}
        return GovernancePipeline(**cfg)

    def platform_info(self) -> Dict[str, Any]:
        return {"platform": self.platform_name, "config": self.get_config()}


# ── Databricks Adapter ────────────────────────────────────────────────────────

class DatabricksAdapter(BaseAdapter):
    """
    Adapter for Databricks Runtime (DBR).

    Auto-detects Databricks by checking for DATABRICKS_RUNTIME_VERSION
    or the presence of /dbfs.

    Log path defaults to /dbfs/tmp/glassbox/logs unless overridden by
    GLASSBOX_LOG_DIR environment variable.

    Compatible with:
      - Databricks Runtime 10.x+
      - Azure Databricks
      - AWS EMR (Databricks-compatible)
      - Databricks on GCP
    """

    platform_name = "databricks"

    def _detect(self) -> bool:
        return (
            "DATABRICKS_RUNTIME_VERSION" in os.environ or
            os.path.exists("/dbfs") or
            os.environ.get("DB_CLUSTER_ID") is not None
        )

    def _log_dir(self) -> str:
        default = "/dbfs/tmp/glassbox/logs"
        return os.environ.get("GLASSBOX_LOG_DIR", default)

    def _env_name(self) -> str:
        cluster_id = os.environ.get("DB_CLUSTER_ID", "")
        env = os.environ.get("GLASSBOX_ENV", "")
        if env:
            return env
        if "prod" in cluster_id.lower():
            return "production"
        if "stage" in cluster_id.lower():
            return "staging"
        return "databricks"

    def get_spark_config(self) -> Dict[str, str]:
        """Return Spark configuration hints for Databricks deployments."""
        return {
            "spark.serializer":                  "org.apache.spark.serializer.KryoSerializer",
            "spark.sql.execution.arrow.enabled":  "true",
        }

    def platform_info(self) -> Dict[str, Any]:
        info = super().platform_info()
        info.update({
            "databricks_runtime": os.environ.get("DATABRICKS_RUNTIME_VERSION", "unknown"),
            "cluster_id":         os.environ.get("DB_CLUSTER_ID", "unknown"),
            "workspace_url":      os.environ.get("DATABRICKS_HOST", "unknown"),
        })
        return info


# ── Kubernetes Adapter ────────────────────────────────────────────────────────

class KubernetesAdapter(BaseAdapter):
    """
    Adapter for Kubernetes deployments.

    Auto-detects K8s by checking for KUBERNETES_SERVICE_HOST.

    Log path defaults to /var/log/glassbox (mount a PVC here for persistence).

    Provides:
      - readiness_check() for K8s readiness probes
      - liveness_check() for K8s liveness probes

    Recommended K8s deployment pattern:
        pipeline = KubernetesAdapter().create_pipeline()
        # Mount PVC at /var/log/glassbox for audit log persistence
        # Expose /health endpoint via KubernetesAdapter().readiness_check()
    """

    platform_name = "kubernetes"

    def _detect(self) -> bool:
        return (
            "KUBERNETES_SERVICE_HOST" in os.environ or
            os.path.exists("/var/run/secrets/kubernetes.io")
        )

    def _log_dir(self) -> str:
        return os.environ.get("GLASSBOX_LOG_DIR", "/var/log/glassbox")

    def _env_name(self) -> str:
        return os.environ.get("GLASSBOX_ENV",
               os.environ.get("K8S_NAMESPACE", "production"))

    def readiness_check(self, pipeline) -> Dict[str, Any]:
        """
        K8s readiness probe.
        Returns a dict suitable for JSON response with HTTP 200 / 503.
        """
        health = pipeline.health()
        ready  = health.get("status") == "healthy"
        return {
            "ready":   ready,
            "details": health,
            "pod":     os.environ.get("HOSTNAME", "unknown"),
        }

    def liveness_check(self) -> Dict[str, Any]:
        """K8s liveness probe — checks that the process is alive."""
        return {
            "alive":   True,
            "pod":     os.environ.get("HOSTNAME", "unknown"),
            "service": "GlassBox",
        }

    def platform_info(self) -> Dict[str, Any]:
        info = super().platform_info()
        info.update({
            "pod_name":   os.environ.get("HOSTNAME", "unknown"),
            "namespace":  os.environ.get("K8S_NAMESPACE", "unknown"),
            "node_name":  os.environ.get("K8S_NODE_NAME", "unknown"),
            "service_account": os.environ.get("K8S_SERVICE_ACCOUNT", "unknown"),
        })
        return info


# ── Microsoft Fabric Adapter ──────────────────────────────────────────────────

class FabricAdapter(BaseAdapter):
    """
    Adapter for Microsoft Fabric (notebooks, pipelines, Lakehouse).

    Microsoft Fabric shares significant architecture with Databricks.
    Log path defaults to /lakehouse/default/Files/glassbox/logs if the
    Fabric Lakehouse mount is detected, otherwise falls back to /tmp/glassbox.

    Compatible with:
      - Microsoft Fabric Spark Notebooks
      - Fabric Data Engineering pipelines
      - Fabric Lakehouse (OneLake)
    """

    platform_name = "microsoft_fabric"

    def _detect(self) -> bool:
        return (
            os.path.exists("/lakehouse") or
            "FABRIC_WORKSPACE_ID" in os.environ or
            os.environ.get("FABRIC_ENVIRONMENT") is not None
        )

    def _log_dir(self) -> str:
        if os.path.exists("/lakehouse/default/Files"):
            return os.environ.get(
                "GLASSBOX_LOG_DIR",
                "/lakehouse/default/Files/glassbox/logs"
            )
        return os.environ.get("GLASSBOX_LOG_DIR", "/tmp/glassbox/logs")

    def _env_name(self) -> str:
        return os.environ.get("GLASSBOX_ENV",
               os.environ.get("FABRIC_ENVIRONMENT", "fabric"))

    def platform_info(self) -> Dict[str, Any]:
        info = super().platform_info()
        info.update({
            "workspace_id":  os.environ.get("FABRIC_WORKSPACE_ID", "unknown"),
            "lakehouse_path": "/lakehouse/default/Files" if os.path.exists("/lakehouse/default/Files") else "not_mounted",
        })
        return info


# ── Auto-detect helper ────────────────────────────────────────────────────────

def auto_detect_adapter() -> BaseAdapter:
    """
    Automatically detect the current platform and return the appropriate adapter.
    Falls back to BaseAdapter if no specific platform is detected.

    Usage:
        pipeline = auto_detect_adapter().create_pipeline()
    """
    candidates = [DatabricksAdapter(), FabricAdapter(), KubernetesAdapter()]
    for adapter in candidates:
        if adapter.detect():
            return adapter
    return BaseAdapter()
