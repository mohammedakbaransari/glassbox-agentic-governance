"""Tests for confining PySpark to the evidence plane (GB-032).

Two independent guarantees, matching the plan's acceptance criterion:

1. every executor-side callable in the new Spark adapters cloudpickles --
   proving, not merely arranging, that nothing here closes over a
   ``GovernancePipeline``, a lock, a thread pool or any other unpicklable
   driver-local state, which is exactly what made v1's ``_govern_via_udf``
   broken;
2. a real (small, local-mode) Spark job runs end to end through
   :func:`preauthorise_dataframe`.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from typing import Any, Iterator, List

import pytest

pyspark = pytest.importorskip("pyspark")
from pyspark.cloudpickle import dumps as cloudpickle_dumps  # noqa: E402
from pyspark.sql import Row, SparkSession  # noqa: E402

from glassbox.adapters.outbound.spark import batch_preauth, control_testing
from glassbox.domain.action import ConsequenceClass
from glassbox.domain.policy_bundle import PolicyBundle, PolicyRule, RuleEffect

_SPARK_ADAPTER_DIR = Path("glassbox/adapters/outbound/spark")

#: Constructs that must never appear in an executor-side Spark module: each one
#: is exactly the shape of state v1's `_govern_via_udf` closed over.
_FORBIDDEN_NAMES = frozenset({"RLock", "Lock", "ThreadPoolExecutor", "Queue", "WeakSet"})
_FORBIDDEN_IMPORTS = frozenset({"glassbox.governance", "glassbox.api", "glassbox.store"})


def _module_functions(module: Any) -> List[Any]:
    return [
        obj
        for name, obj in vars(module).items()
        if name in module.__all__ and inspect.isfunction(obj)
    ]


class TestExecutorSideCallablesAreSerialisable:
    @pytest.mark.parametrize("module", [batch_preauth, control_testing], ids=lambda m: m.__name__)
    def test_every_exported_callable_cloudpickles(self, module: Any) -> None:
        for func in _module_functions(module):
            cloudpickle_dumps(func)  # raises on failure; success is the assertion

    def test_a_broadcastable_policy_bundle_cloudpickles(self) -> None:
        """The one piece of cluster-side state this adapter set uses at all --
        an immutable, already-signed policy bundle -- must itself be
        broadcastable, unlike v1's live ``GovernancePipeline``."""
        bundle = PolicyBundle(
            bundle_id="bundle.v1",
            tenant_id="acme",
            version=1,
            created_at=0.0,
            rules=(PolicyRule(name="allow-all", effect=RuleEffect.ALLOW),),
        )
        restored: PolicyBundle = pyspark.cloudpickle.loads(cloudpickle_dumps(bundle))
        assert restored.digest() == bundle.digest()


class TestNoUnpicklableConstructIsPresent:
    """Static proof that the defect class cannot reappear, not just that today's
    code happens to avoid it."""

    @pytest.mark.parametrize("path", sorted(_SPARK_ADAPTER_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_module_imports_no_legacy_stateful_package(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not any(
                    node.module == banned or node.module.startswith(banned + ".")
                    for banned in _FORBIDDEN_IMPORTS
                ), f"{path.name} imports {node.module}, banned for executor-side code"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not any(
                        alias.name == banned or alias.name.startswith(banned + ".")
                        for banned in _FORBIDDEN_IMPORTS
                    ), f"{path.name} imports {alias.name}, banned for executor-side code"

    @pytest.mark.parametrize("path", sorted(_SPARK_ADAPTER_DIR.glob("*.py")), ids=lambda p: p.name)
    def test_the_module_never_names_a_forbidden_stateful_construct(self, path: Path) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        used_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        used_attrs = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        offenders = (used_names | used_attrs) & _FORBIDDEN_NAMES
        assert not offenders, f"{path.name} references forbidden stateful construct(s): {offenders}"


@pytest.fixture(scope="module")
def spark() -> Iterator[SparkSession]:
    """A real local-mode Spark session.

    Gated behind ``GLASSBOX_SPARK_LOCAL_JOB``, the same convention
    ``GLASSBOX_POSTGRES_DSN`` uses for real-Postgres integration tests: some
    sandboxed environments block the loopback sockets the Py4J driver-JVM
    gateway needs (observed on this project's Windows dev machine, where
    ``JAVA_HOME``/``SPARK_HOME``/``HADOOP_HOME`` and ``winutils.exe`` are all
    correctly configured, yet the gateway connection is refused -- almost
    certainly endpoint-security software blocking a dynamically allocated
    loopback port, not a PySpark configuration defect). CI's docker-compose
    Spark container (GB-035) sets this variable so the real job always runs
    there.
    """
    if not os.environ.get("GLASSBOX_SPARK_LOCAL_JOB"):
        pytest.skip(
            "set GLASSBOX_SPARK_LOCAL_JOB=1 to run a real local-mode Spark job "
            "(requires a working Py4J driver-JVM gateway; some sandboxed "
            "environments block the loopback sockets it needs)"
        )
    session = (
        SparkSession.builder.master("local[2]")
        .appName("glassbox-gb032-test")
        .config("spark.ui.enabled", "false")
        .getOrCreate()
    )
    yield session
    session.stop()


class TestRealSparkJob:
    """The plan's other half of the acceptance criterion: a real Spark job runs."""

    def _bundle(self) -> PolicyBundle:
        return PolicyBundle(
            bundle_id="bundle.v1",
            tenant_id="acme",
            version=1,
            created_at=0.0,
            rules=(
                PolicyRule(
                    name="allow-small-transfers",
                    effect=RuleEffect.ALLOW,
                    action_pattern="payments.*",
                    max_consequence=ConsequenceClass.COMPENSABLE,
                    max_monetary=1_000.0,
                    priority=10,
                ),
            ),
        )

    def test_preauthorise_dataframe_evaluates_every_row(self, spark: SparkSession) -> None:
        rows = [
            Row(
                action="payments.wire_transfer",
                resource_kind="account",
                resource_id="ACC-1",
                tenant_id="acme",
                consequence_class="reversible",
                idempotency_key="idem-0001",
                exposure_monetary=101.0,
            ),
            Row(
                action="payments.wire_transfer",
                resource_kind="account",
                resource_id="ACC-2",
                tenant_id="acme",
                consequence_class="irreversible",
                idempotency_key="idem-0002",
                exposure_monetary=5_000_000.0,
            ),
        ]
        df = spark.createDataFrame(rows)
        result = batch_preauth.preauthorise_dataframe(df, self._bundle()).collect()
        by_key = {row["idempotency_key"]: row for row in result}
        assert by_key["idem-0001"]["policy_effect"] == "allow"
        assert by_key["idem-0002"]["policy_effect"] == "deny"
        assert by_key["idem-0001"]["policy_bundle_sha256"] == self._bundle().digest()

    def test_a_malformed_row_is_denied_not_an_uncaught_executor_exception(
        self, spark: SparkSession
    ) -> None:
        df = spark.createDataFrame([Row(action="payments.wire_transfer", tenant_id="acme")])
        result = batch_preauth.preauthorise_dataframe(df, self._bundle()).collect()
        assert result[0]["policy_effect"] == "deny"
        assert result[0]["preauth_error"] is not None
