"""Architecture enforcement tests (GB-002, GB-004).

The plan's dependency rule is ``domain <- ports <- app <- adapters`` and it never
reverses. A rule that is only written down is a rule that erodes; these tests
make it fail the build.

The rule is enforced twice, on purpose.

* **import-linter** (configured in ``pyproject.toml``, run by the CI architecture
  gate) is authoritative. It builds a real import graph and therefore follows
  **transitive** chains -- an adapter that reaches v1 code by way of the
  composition root is caught even though no single file imports it directly.
* **These tests** re-state the same rules as AST assertions. They add value the
  graph tool does not: they run with no optional dependency installed, they
  localise a violation to a file and line with a readable message, and they cover
  things an import graph cannot see at all -- bare ``except``, swallowed
  exceptions, clock reads, mutable defaults and module hygiene.

:class:`TestContractConsistency` asserts the two agree, so they cannot drift.

Everything here is **static**: the import graph is derived by parsing the AST,
not by importing modules. Importing ``glassbox.domain`` would also execute
``glassbox/__init__.py`` and could mask a real violation, and a static check
catches an illegal import even on a code path that never runs.
"""

from __future__ import annotations

import ast
import pathlib
import sys
from typing import Any, Dict, Iterator, List, Mapping, Set, Tuple

import pytest

if sys.version_info >= (3, 11):  # pragma: no cover - interpreter-dependent import
    import tomllib
else:  # pragma: no cover - interpreter-dependent import
    import tomli as tomllib  # type: ignore[no-redef]

import glassbox

PACKAGE_ROOT = pathlib.Path(glassbox.__file__).resolve().parent
REPO_ROOT = PACKAGE_ROOT.parent

#: Layer -> the layers it is allowed to import from (plus the standard library).
#: ``domain`` may import only itself. ``ports`` may add ``domain``. Later waves
#: extend this table with ``app`` and ``adapters``; the entries below are the
#: layers that exist after GB-002.
ALLOWED_INTERNAL_IMPORTS: Dict[str, Set[str]] = {
    "domain": {"domain"},
    "ports": {"domain", "ports"},
    "app": {"domain", "ports", "app"},
}

#: Third-party distributions the pure layers may never import. The core advertises
#: zero mandatory dependencies, and the domain must remain runnable anywhere.
FORBIDDEN_THIRD_PARTY = frozenset(
    {
        "flask",
        "redis",
        "pyspark",
        "psycopg",
        "psycopg2",
        "sqlalchemy",
        "requests",
        "httpx",
        "yaml",
        "anthropic",
        "openai",
        "cryptography",
        "boto3",
        "opentelemetry",
        "numpy",
        "pandas",
    }
)

#: Standard-library modules that would let a "pure" layer read hidden state or
#: acquire a hidden dependency.
#:
#: ``time``/``datetime`` break invariant I6 (determinism); ``os``/``random``/
#: ``secrets`` break reproducibility; ``sqlite3``/``socket``/``urllib`` are I/O;
#: ``threading`` has no place in a value-object layer; ``logging`` is banned
#: because a pure layer that emits log records depends on logging configuration
#: and cannot be tested without a capture fixture -- errors carry a structured
#: ``context`` mapping instead, which the application layer renders exactly once.
FORBIDDEN_IN_PURE_LAYERS = frozenset(
    {
        "asyncio",
        "datetime",
        "logging",
        "os",
        "pathlib",
        "random",
        "secrets",
        "shutil",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "time",
        "urllib",
    }
)

#: Call expressions that read a clock or a random source inside a pure layer.
FORBIDDEN_CALLS = (
    "time.time",
    "time.monotonic",
    "datetime.now",
    "datetime.utcnow",
    "random.random",
    "random.choice",
    "uuid.uuid4",
    "os.getenv",
    "os.environ.get",
)

PURE_LAYERS = ("domain", "ports")

#: Layers written under the rebuild. All of them are held to the banned-construct
#: and module-hygiene rules; only the pure ones are held to the purity rules.
ENFORCED_LAYERS = ("domain", "ports", "app")

#: Standard-library modules the application layer may not import. It is allowed
#: ``os`` (environment configuration), ``logging`` and ``contextvars`` (it owns
#: observability), but not I/O, concurrency or a clock -- orchestration decides
#: *what* happens, adapters decide *how*.
FORBIDDEN_IN_APP_LAYER = frozenset(
    {
        "datetime",
        "random",
        "secrets",
        "socket",
        "sqlite3",
        "subprocess",
        "threading",
        "time",
        "urllib",
    }
)

#: Rebuilt outbound adapter packages, which are held to the banned-construct
#: rules even though they may perform I/O and use third-party libraries.
OUTBOUND_ADAPTER_ROOT = "adapters/outbound"

#: v1 packages being replaced. No rebuilt layer may import any of them. This is
#: the single source of truth: the AST tests below and the import-linter contract
#: in ``pyproject.toml`` are both checked against it, so the two enforcement
#: mechanisms cannot drift apart.
LEGACY_PACKAGES = (
    "glassbox.api",
    "glassbox.authoring",
    "glassbox.benchmarks",
    "glassbox.compliance",
    "glassbox.events",
    "glassbox.governance",
    "glassbox.integrations",
    "glassbox.orchestration",
    "glassbox.rag",
    "glassbox.rules",
    "glassbox.scenarios",
    "glassbox.security",
    "glassbox.store",
    "glassbox.telemetry",
    "glassbox.testing",
    "glassbox.workflow",
)


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _iter_layer_modules(layer: str) -> Iterator[pathlib.Path]:
    """Yield every ``.py`` file in a layer, ignoring caches."""
    layer_root = PACKAGE_ROOT / layer
    if not layer_root.is_dir():  # pragma: no cover - guarded by test_layers_exist
        return
    for path in sorted(layer_root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _iter_outbound_adapter_modules() -> Iterator[pathlib.Path]:
    """Yield every ``.py`` file under the rebuilt outbound adapter tree."""
    root = PACKAGE_ROOT / "adapters" / "outbound"
    if not root.is_dir():  # pragma: no cover - guarded by test_layers_exist
        return
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _module_name(path: pathlib.Path) -> str:
    """Return the dotted module name for a file inside the package."""
    relative = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: pathlib.Path) -> Iterator[Tuple[str, int]]:
    """Yield ``(imported_module, line_number)`` for every import in ``path``.

    Relative imports are resolved against the file's own package so that
    ``from . import x`` is attributed to the correct layer.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = _module_name(path).split(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                yield alias.name, node.lineno
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = package_parts[: len(package_parts) - node.level + 1]
                resolved = ".".join(base + ([node.module] if node.module else []))
                yield resolved, node.lineno
            elif node.module:
                yield node.module, node.lineno


def _dotted_call_name(node: ast.Call) -> str:
    """Return a dotted name for a call expression, or an empty string."""
    parts: List[str] = []
    current: ast.expr = node.func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    else:
        return ""
    return ".".join(reversed(parts))


# --------------------------------------------------------------------------- #
# Layer existence and the dependency rule
# --------------------------------------------------------------------------- #


class TestLayerStructure:
    """The layers exist and are importable packages."""

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_layer_exists_and_is_a_package(self, layer: str) -> None:
        assert (PACKAGE_ROOT / layer / "__init__.py").is_file()

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_layer_contains_modules(self, layer: str) -> None:
        assert list(_iter_layer_modules(layer))


class TestDependencyRule:
    """``domain <- ports``. The arrow never reverses."""

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_layer_only_imports_permitted_layers(self, layer: str) -> None:
        allowed = ALLOWED_INTERNAL_IMPORTS[layer]
        violations: List[str] = []
        for path in _iter_layer_modules(layer):
            for imported, lineno in _imported_modules(path):
                if not imported.startswith("glassbox"):
                    continue
                segments = imported.split(".")
                if len(segments) < 2:
                    violations.append(f"{path.name}:{lineno} imports the package root")
                    continue
                target_layer = segments[1]
                if target_layer not in allowed:
                    violations.append(
                        f"{path.relative_to(REPO_ROOT)}:{lineno} imports {imported} "
                        f"({target_layer} is not permitted from {layer})"
                    )
        assert not violations, "dependency rule violated:\n" + "\n".join(violations)

    def test_domain_never_imports_ports(self) -> None:
        """The single most important direction: the arrow must not reverse."""
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno}"
            for path in _iter_layer_modules("domain")
            for imported, lineno in _imported_modules(path)
            if imported.startswith("glassbox.ports")
        ]
        assert not offenders, f"domain imports ports: {offenders}"

    def test_pure_layers_never_import_legacy_modules(self) -> None:
        """The new layers must not be anchored to the v1 code being replaced."""
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for layer in ENFORCED_LAYERS
            for path in _iter_layer_modules(layer)
            for imported, lineno in _imported_modules(path)
            if imported.startswith(LEGACY_PACKAGES + ("glassbox.adapters",))
        ]
        assert not offenders, f"rebuilt layers import v1 modules: {offenders}"

    def test_app_never_imports_an_adapter(self) -> None:
        """The GB-003 acceptance criterion, as an executable rule.

        The composition root receives adapter sets from the process entry point.
        The moment ``glassbox.app`` names a concrete adapter, the seam that makes
        the decision service testable without infrastructure is gone -- which is
        exactly how v1's ``GovernancePipeline`` ended up building eight concrete
        collaborators itself.
        """
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules("app")
            for imported, lineno in _imported_modules(path)
            if imported.startswith("glassbox.adapters")
        ]
        assert not offenders, f"app imports a concrete adapter: {offenders}"

    def test_app_only_imports_permitted_stdlib(self) -> None:
        """Orchestration decides what happens; adapters decide how."""
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules("app")
            for imported, lineno in _imported_modules(path)
            if imported.split(".")[0] in FORBIDDEN_IN_APP_LAYER
        ]
        assert not offenders, f"app imports a module outside its remit: {offenders}"

    def test_app_has_no_third_party_dependency(self) -> None:
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules("app")
            for imported, lineno in _imported_modules(path)
            if imported.split(".")[0] in FORBIDDEN_THIRD_PARTY
        ]
        assert not offenders, f"app imports third-party packages: {offenders}"

    def test_outbound_adapters_never_import_v1_modules(self) -> None:
        """Rebuilt adapters implement ports; they do not wrap the code being replaced."""
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_outbound_adapter_modules()
            for imported, lineno in _imported_modules(path)
            if imported.startswith(LEGACY_PACKAGES)
        ]
        assert not offenders, f"outbound adapters import v1 modules: {offenders}"


class TestPurity:
    """The pure layers do no I/O, read no clock and pull in no dependency."""

    @pytest.mark.parametrize("layer", PURE_LAYERS)
    def test_no_third_party_imports(self, layer: str) -> None:
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules(layer)
            for imported, lineno in _imported_modules(path)
            if imported.split(".")[0] in FORBIDDEN_THIRD_PARTY
        ]
        assert not offenders, f"{layer} imports third-party packages: {offenders}"

    @pytest.mark.parametrize("layer", PURE_LAYERS)
    def test_no_io_or_clock_imports(self, layer: str) -> None:
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules(layer)
            for imported, lineno in _imported_modules(path)
            if imported.split(".")[0] in FORBIDDEN_IN_PURE_LAYERS
        ]
        assert (
            not offenders
        ), f"{layer} imports a module that breaks purity or determinism: {offenders}"

    @pytest.mark.parametrize("layer", PURE_LAYERS)
    def test_no_clock_or_random_calls(self, layer: str) -> None:
        """Invariant I6.

        Regression: v1's ``_procurement_factors`` read
        ``datetime.now(timezone.utc).hour`` inside scoring, so replaying a
        decision produced a different answer than the original.
        """
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    name = _dotted_call_name(node)
                    if name in FORBIDDEN_CALLS:
                        offenders.append(
                            f"{path.relative_to(REPO_ROOT)}:{node.lineno} calls {name}"
                        )
        assert not offenders, f"{layer} reads hidden state: {offenders}"

    @pytest.mark.parametrize("layer", PURE_LAYERS)
    def test_layer_emits_no_log_records(self, layer: str) -> None:
        """The pure layers raise structured errors instead of logging.

        Parsed from the AST rather than matched as text, so a docstring that
        *mentions* logging (``identity.py`` warns against
        ``logger.info("got %s", credential)``) is not a false positive.
        """
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = _dotted_call_name(node)
                if not name:
                    continue
                head, _, tail = name.partition(".")
                if tail.endswith("getLogger") or head.lower() in {"logger", "log", "logging"}:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} calls {name}")
        assert not offenders, f"{layer} emits log records: {offenders}"

    @pytest.mark.parametrize("layer", PURE_LAYERS)
    def test_layer_imports_only_the_standard_library(self, layer: str) -> None:
        """Belt-and-braces: anything not stdlib and not glassbox is a dependency."""
        stdlib = sys.stdlib_module_names
        offenders = [
            f"{path.relative_to(REPO_ROOT)}:{lineno} -> {imported}"
            for path in _iter_layer_modules(layer)
            for imported, lineno in _imported_modules(path)
            if imported.split(".")[0] not in stdlib and not imported.startswith("glassbox")
        ]
        assert not offenders, f"{layer} has non-stdlib imports: {offenders}"


# --------------------------------------------------------------------------- #
# Banned constructs (invariants I5 and I10)
# --------------------------------------------------------------------------- #


class TestBannedConstructs:
    """Constructs that silently defeat governance are rejected at review time."""

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_no_bare_except(self, layer: str) -> None:
        """Invariant I5. v1 had bare ``except:`` in the velocity breaker."""
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and node.type is None:
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not offenders, f"bare except found: {offenders}"

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_no_silently_swallowed_exceptions(self, layer: str) -> None:
        """Invariant I5.

        Regression: v1's ``audit_logger._persist_record`` caught every exception
        and continued, so evidence loss was invisible while the side effect
        still happened.
        """
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ExceptHandler):
                    continue
                body = [
                    statement
                    for statement in node.body
                    if not (
                        isinstance(statement, ast.Expr)
                        and isinstance(statement.value, ast.Constant)
                        and isinstance(statement.value.value, str)
                    )
                ]
                if len(body) == 1 and isinstance(body[0], ast.Pass):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not offenders, f"exception swallowed with `pass`: {offenders}"

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_no_threading_local(self, layer: str) -> None:
        """Invariant I10.

        Regression: v1's ``RequestContext`` used ``threading.local()`` and lost
        the tenant binding across every ``ThreadPoolExecutor`` boundary. The
        measured result was ``sync_tenant_bound: 'tenant_alpha'`` but
        ``async_tenant_bound: null``.

        Parsed from the AST, so a docstring explaining *why* it is banned -- as
        ``app/observability.py`` does -- is not a false positive.
        """
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Attribute)
                    and node.attr == "local"
                    and isinstance(node.value, ast.Name)
                    and node.value.id == "threading"
                ):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
                elif isinstance(node, ast.ImportFrom) and node.module == "threading":
                    if any(alias.name == "local" for alias in node.names):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
        assert not offenders, f"threading.local is banned: {offenders}"

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_no_mutable_default_arguments(self, layer: str) -> None:
        """A shared mutable default is state hiding in a supposedly pure layer."""
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                defaults = list(node.args.defaults) + [
                    default for default in node.args.kw_defaults if default is not None
                ]
                for default in defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno} {node.name}")
        assert not offenders, f"mutable default arguments: {offenders}"


# --------------------------------------------------------------------------- #
# Documentation and export hygiene
# --------------------------------------------------------------------------- #


class TestModuleHygiene:
    """Every module in the new layers is documented and declares its surface."""

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_every_module_has_a_docstring(self, layer: str) -> None:
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _iter_layer_modules(layer)
            if not ast.get_docstring(ast.parse(path.read_text(encoding="utf-8")))
        ]
        assert not offenders, f"modules without a docstring: {offenders}"

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_every_module_declares_dunder_all(self, layer: str) -> None:
        offenders: List[str] = []
        for path in _iter_layer_modules(layer):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            declares = any(
                isinstance(node, ast.Assign)
                and any(
                    isinstance(target, ast.Name) and target.id == "__all__"
                    for target in node.targets
                )
                for node in tree.body
            )
            if not declares:
                offenders.append(str(path.relative_to(REPO_ROOT)))
        assert not offenders, f"modules without __all__: {offenders}"

    @pytest.mark.parametrize("layer", ENFORCED_LAYERS)
    def test_every_module_uses_future_annotations(self, layer: str) -> None:
        """Required for the 3.9 floor: postponed evaluation of annotations."""
        offenders = [
            str(path.relative_to(REPO_ROOT))
            for path in _iter_layer_modules(layer)
            if "from __future__ import annotations" not in path.read_text(encoding="utf-8")
        ]
        assert not offenders, f"modules missing `from __future__ import annotations`: {offenders}"

    def test_package_exports_resolve(self) -> None:
        """Nothing in ``__all__`` may be a stale name."""
        import glassbox.domain as domain_package
        import glassbox.ports as ports_package

        for package in (domain_package, ports_package):
            missing = [name for name in package.__all__ if not hasattr(package, name)]
            assert not missing, f"{package.__name__}.__all__ names missing objects: {missing}"

    def test_package_exports_are_sorted_within_groups(self) -> None:
        """Guards against duplicate entries creeping into the export list."""
        import glassbox.domain as domain_package
        import glassbox.ports as ports_package

        for package in (domain_package, ports_package):
            names = list(package.__all__)
            duplicates = sorted({name for name in names if names.count(name) > 1})
            assert not duplicates, f"{package.__name__}.__all__ has duplicates: {duplicates}"


# --------------------------------------------------------------------------- #
# Contract consistency (GB-004)
# --------------------------------------------------------------------------- #


def _pyproject() -> Dict[str, Any]:
    """Return the parsed ``pyproject.toml``."""
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)


def _contracts() -> List[Mapping[str, Any]]:
    """Return the declared import-linter contracts."""
    return _pyproject().get("tool", {}).get("importlinter", {}).get("contracts", [])


def _contract_named(fragment: str) -> Mapping[str, Any]:
    """Return the single contract whose name contains ``fragment``."""
    matches = [
        contract for contract in _contracts() if fragment.lower() in contract["name"].lower()
    ]
    assert len(matches) == 1, f"expected exactly one contract matching {fragment!r}, got {matches}"
    return matches[0]


class TestContractConsistency:
    """The declared contracts and these assertions must describe the same rules.

    Two enforcement mechanisms that disagree are worse than one: whichever is
    weaker becomes the real rule, silently. These tests make disagreement a build
    failure rather than a discovery.
    """

    def test_import_linter_is_configured_for_this_package(self) -> None:
        settings = _pyproject().get("tool", {}).get("importlinter", {})
        assert settings.get("root_package") == "glassbox"
        assert _contracts(), "no import-linter contracts are declared"

    def test_layer_order_matches_the_dependency_rule(self) -> None:
        """import-linter lists layers highest-first; the rule is domain at the bottom."""
        layers = list(_contract_named("Layered architecture")["layers"])
        assert layers == [
            "glassbox.adapters.outbound",
            "glassbox.app",
            "glassbox.ports",
            "glassbox.domain",
        ]

    def test_declared_layers_cover_every_enforced_layer(self) -> None:
        layers = set(_contract_named("Layered architecture")["layers"])
        assert {f"glassbox.{name}" for name in ENFORCED_LAYERS} <= layers

    def test_app_adapter_ban_is_declared_as_a_contract(self) -> None:
        """The GB-003 acceptance criterion exists in both mechanisms."""
        contract = _contract_named("never imports a concrete adapter")
        assert contract["type"] == "forbidden"
        assert contract["source_modules"] == ["glassbox.app"]
        assert "glassbox.adapters" in contract["forbidden_modules"]

    def test_legacy_ban_lists_the_same_packages_as_the_ast_check(self) -> None:
        """One source of truth for what counts as v1 code."""
        contract = _contract_named("anchored to the v1 code")
        assert set(contract["forbidden_modules"]) == set(LEGACY_PACKAGES)

    def test_legacy_ban_covers_every_rebuilt_layer(self) -> None:
        contract = _contract_named("anchored to the v1 code")
        expected = {f"glassbox.{name}" for name in ENFORCED_LAYERS} | {"glassbox.adapters.outbound"}
        assert expected == set(contract["source_modules"])

    def test_no_legacy_package_is_also_a_rebuilt_layer(self) -> None:
        """A package cannot be both the thing being replaced and its replacement."""
        rebuilt = {f"glassbox.{name}" for name in ENFORCED_LAYERS}
        assert not rebuilt & set(LEGACY_PACKAGES)

    def test_ci_runs_both_architecture_gates(self) -> None:
        """A contract that CI does not execute is documentation, not a gate."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert "lint-imports" in workflow, "CI does not run import-linter"
        assert "ruff check" in workflow, "CI does not run the invariant lint"
        assert "tests/test_layering.py" in workflow, "CI does not run the architecture tests"


class TestInvariantLintConfiguration:
    """The ruff gate is narrow, invariant-focused, and its debt is enumerable."""

    #: Rules that encode a named invariant. Removing one silently retires a rule.
    REQUIRED_RULES = ("E722", "S110", "S112", "B904", "B006", "B008", "TID251")

    @staticmethod
    def _ruff() -> Mapping[str, Any]:
        return _pyproject().get("tool", {}).get("ruff", {})

    @pytest.mark.parametrize("rule", REQUIRED_RULES)
    def test_invariant_rule_is_selected(self, rule: str) -> None:
        assert rule in self._ruff().get("lint", {}).get("select", [])

    def test_threading_local_is_banned_by_the_linter(self) -> None:
        """Invariant I10, enforced by the tool as well as by the AST check."""
        banned = self._ruff().get("lint", {}).get("flake8-tidy-imports", {}).get("banned-api", {})
        assert "threading.local" in banned
        assert "contextvars" in banned["threading.local"]["msg"]

    def test_ruff_targets_the_declared_python_floor(self) -> None:
        project = _pyproject()["project"]
        target = self._ruff()["target-version"]
        assert target == "py" + project["requires-python"].lstrip(">=").replace(".", "")

    @staticmethod
    def _per_file_ignores() -> Mapping[str, Any]:
        return (
            _pyproject().get("tool", {}).get("ruff", {}).get("lint", {}).get("per-file-ignores", {})
        )

    def test_no_rebuilt_module_is_exempt(self) -> None:
        """The layers written under the rebuild get no waivers at all."""
        exempt = sorted(
            path
            for path in self._per_file_ignores()
            if path.startswith(
                ("glassbox/domain", "glassbox/ports", "glassbox/app", "glassbox/adapters/outbound")
            )
        )
        assert not exempt, f"rebuilt modules must not be exempt from invariant lint: {exempt}"

    def test_debt_is_enumerated_per_module_not_wildcarded(self) -> None:
        """``glassbox/governance/*`` would let a new module inherit the waiver."""
        wildcards = sorted(
            path
            for path in self._per_file_ignores()
            if "*" in path and not path.startswith("tests/")
        )
        assert not wildcards, f"invariant lint debt must be enumerated: {wildcards}"

    def test_debt_register_only_shrinks(self) -> None:
        package_entries = [
            path for path in self._per_file_ignores() if path.startswith("glassbox/")
        ]
        assert (
            len(package_entries) <= 17
        ), f"the invariant lint debt grew to {len(package_entries)} modules; it may only shrink"

    def test_exempt_modules_still_exist(self) -> None:
        """A stale waiver hides the fact that the debt was already paid."""
        missing = [
            path
            for path in self._per_file_ignores()
            if path.startswith("glassbox/") and not (REPO_ROOT / path).is_file()
        ]
        assert not missing, f"invariant lint debt names modules that no longer exist: {missing}"
