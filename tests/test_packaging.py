"""Packaging contract tests (GB-001).

These tests exist because the repository shipped a ``build-backend`` that did
not exist (``setuptools.backends.legacy:build``), which made every documented
``pip install .`` / ``pip install -e .`` fail with ``ModuleNotFoundError``.
CI never caught it because CI never built the package -- it installed
dependencies by hand and imported ``glassbox`` from the checkout directory.

The suite is split into two tiers:

* **Static contract tests** (always run) parse ``pyproject.toml`` and assert the
  declared build backend is importable and exposes the PEP 517 hooks, that the
  project metadata is complete and internally consistent, and that package
  discovery covers every shipped subpackage.
* **Build smoke tests** (opt-in via ``GLASSBOX_RUN_BUILD_TESTS=1``) shell out to
  ``python -m build`` and install the resulting wheel into a throwaway virtual
  environment. They are gated because they are slow and require network access
  for PEP 517 build isolation.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess  # nosec B404 - used only to invoke this interpreter's own build tooling
import sys
import venv
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Set

import pytest

if sys.version_info >= (3, 11):  # pragma: no cover - interpreter-dependent import
    import tomllib
else:  # pragma: no cover - interpreter-dependent import
    import tomli as tomllib  # type: ignore[no-redef]

from packaging.requirements import InvalidRequirement, Requirement
from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

REPO_ROOT: Path = Path(__file__).resolve().parent.parent
PYPROJECT_PATH: Path = REPO_ROOT / "pyproject.toml"
PACKAGE_ROOT: Path = REPO_ROOT / "glassbox"

#: PEP 517 mandatory hooks. A backend missing any of these cannot build a wheel.
REQUIRED_PEP517_HOOKS: tuple = ("build_wheel", "build_sdist")

#: PEP 517 optional-but-expected hooks for a setuptools-style backend.
EXPECTED_PEP517_HOOKS: tuple = (
    "get_requires_for_build_wheel",
    "get_requires_for_build_sdist",
    "prepare_metadata_for_build_wheel",
)

#: Distribution name declared in ``[project].name``.
EXPECTED_DISTRIBUTION_NAME: str = "glassbox-governance"

#: Import name of the top-level package.
EXPECTED_IMPORT_NAME: str = "glassbox"

#: Directories under the repository root that must never be packaged.
NON_SHIPPABLE_TOP_LEVEL: frozenset = frozenset({"tests", "examples", "scripts", "docs", "sdk"})

#: Environment flag that enables the slow, network-dependent build smoke tests.
BUILD_TEST_FLAG: str = "GLASSBOX_RUN_BUILD_TESTS"

_CLASSIFIER_PY_VERSION = re.compile(r"^Programming Language :: Python :: (\d+\.\d+)$")

_run_build_tests = pytest.mark.skipif(
    os.environ.get(BUILD_TEST_FLAG, "") != "1",
    reason=f"set {BUILD_TEST_FLAG}=1 to run the slow build/install smoke tests",
)


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="session")
def pyproject() -> Dict[str, Any]:
    """Parsed ``pyproject.toml``.

    Raises:
        AssertionError: if the file is missing or is not valid TOML.
    """
    assert PYPROJECT_PATH.is_file(), f"pyproject.toml not found at {PYPROJECT_PATH}"
    try:
        with PYPROJECT_PATH.open("rb") as handle:
            data: Dict[str, Any] = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:  # pragma: no cover - only on a broken file
        raise AssertionError(f"pyproject.toml is not valid TOML: {exc}") from exc
    return data


@pytest.fixture(scope="session")
def project_table(pyproject: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``[project]`` table."""
    table = pyproject.get("project")
    assert isinstance(table, Mapping), "pyproject.toml is missing the [project] table"
    return table


@pytest.fixture(scope="session")
def build_system(pyproject: Mapping[str, Any]) -> Mapping[str, Any]:
    """The ``[build-system]`` table."""
    table = pyproject.get("build-system")
    assert isinstance(table, Mapping), "pyproject.toml is missing the [build-system] table"
    return table


def _iter_shipped_packages() -> Iterator[str]:
    """Yield the dotted name of every importable package under ``glassbox/``.

    A directory counts as a package when it contains ``__init__.py``. Cache and
    egg-info directories are ignored.
    """
    for init_path in sorted(PACKAGE_ROOT.rglob("__init__.py")):
        relative = init_path.parent.relative_to(REPO_ROOT)
        parts = relative.parts
        if any(part == "__pycache__" or part.endswith(".egg-info") for part in parts):
            continue
        yield ".".join(parts)


def _parse_requirements(raw: object, context: str) -> List[Requirement]:
    """Parse a list of PEP 508 requirement strings.

    Args:
        raw: The value read from ``pyproject.toml``.
        context: Human-readable location used in assertion messages.

    Returns:
        The parsed requirements.
    """
    assert isinstance(raw, list), f"{context} must be a list, got {type(raw).__name__}"
    parsed: List[Requirement] = []
    for entry in raw:
        assert isinstance(entry, str), f"{context} contains a non-string entry: {entry!r}"
        try:
            parsed.append(Requirement(entry))
        except InvalidRequirement as exc:
            raise AssertionError(f"{context} entry {entry!r} is not valid PEP 508: {exc}") from exc
    return parsed


# --------------------------------------------------------------------------- #
# Build backend
# --------------------------------------------------------------------------- #


class TestBuildBackend:
    """The declared PEP 517 backend must actually exist and be usable."""

    def test_build_backend_is_declared(self, build_system: Mapping[str, Any]) -> None:
        backend = build_system.get("build-backend")
        assert isinstance(backend, str) and backend, "[build-system].build-backend must be set"

    def test_build_backend_is_importable(self, build_system: Mapping[str, Any]) -> None:
        """Regression test for the shipped ``setuptools.backends.legacy:build`` defect.

        The backend spec is ``module`` or ``module:object``. Importing it is
        exactly what pip does first, so an unimportable value breaks every
        install path.
        """
        spec = str(build_system["build-backend"])
        module_name, _, object_path = spec.partition(":")

        try:
            module = __import__(module_name, fromlist=["__name__"])
        except ImportError as exc:
            raise AssertionError(
                f"build-backend {spec!r} is not importable: {exc}. "
                "pip cannot build this project."
            ) from exc

        backend: Any = module
        for attribute in filter(None, object_path.split(".")):
            assert hasattr(backend, attribute), (
                f"build-backend {spec!r} resolves to {backend!r}, "
                f"which has no attribute {attribute!r}"
            )
            backend = getattr(backend, attribute)

    def test_build_backend_exposes_mandatory_pep517_hooks(
        self, build_system: Mapping[str, Any]
    ) -> None:
        spec = str(build_system["build-backend"])
        module_name, _, object_path = spec.partition(":")
        backend: Any = __import__(module_name, fromlist=["__name__"])
        for attribute in filter(None, object_path.split(".")):
            backend = getattr(backend, attribute)

        missing = [
            hook for hook in REQUIRED_PEP517_HOOKS if not callable(getattr(backend, hook, None))
        ]
        assert not missing, f"build-backend {spec!r} is missing PEP 517 hooks: {missing}"

    def test_build_backend_exposes_expected_optional_hooks(
        self, build_system: Mapping[str, Any]
    ) -> None:
        spec = str(build_system["build-backend"])
        module_name, _, object_path = spec.partition(":")
        backend: Any = __import__(module_name, fromlist=["__name__"])
        for attribute in filter(None, object_path.split(".")):
            backend = getattr(backend, attribute)

        missing = [
            hook for hook in EXPECTED_PEP517_HOOKS if not callable(getattr(backend, hook, None))
        ]
        assert not missing, (
            f"build-backend {spec!r} is missing hooks needed for editable and "
            f"metadata-only installs: {missing}"
        )

    def test_build_requires_are_valid_and_pin_setuptools(
        self, build_system: Mapping[str, Any]
    ) -> None:
        requirements = _parse_requirements(build_system.get("requires"), "[build-system].requires")
        assert requirements, "[build-system].requires must not be empty"

        names = {req.name.lower() for req in requirements}
        assert "setuptools" in names, (
            "the setuptools backend is declared but setuptools is not in "
            "[build-system].requires; build isolation would fail"
        )

        setuptools_req = next(req for req in requirements if req.name.lower() == "setuptools")
        assert str(setuptools_req.specifier), (
            "setuptools must carry a lower-bound specifier so build isolation "
            "cannot resolve a version without the declared backend"
        )


# --------------------------------------------------------------------------- #
# Project metadata
# --------------------------------------------------------------------------- #


class TestProjectMetadata:
    """``[project]`` must be complete, consistent and truthful."""

    def test_distribution_name(self, project_table: Mapping[str, Any]) -> None:
        assert project_table.get("name") == EXPECTED_DISTRIBUTION_NAME

    def test_version_is_pep440(self, project_table: Mapping[str, Any]) -> None:
        raw = project_table.get("version")
        assert isinstance(raw, str), "[project].version must be a string"
        try:
            Version(raw)
        except InvalidVersion as exc:
            raise AssertionError(f"[project].version {raw!r} is not PEP 440: {exc}") from exc

    def test_version_matches_package_dunder(self, project_table: Mapping[str, Any]) -> None:
        """``glassbox.__version__`` and the distribution version must not drift."""
        init_source = (PACKAGE_ROOT / "__init__.py").read_text(encoding="utf-8")
        match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', init_source, re.MULTILINE)
        assert match, "glassbox/__init__.py does not define __version__"
        assert match.group(1) == project_table["version"], (
            f"glassbox.__version__ is {match.group(1)!r} but "
            f"[project].version is {project_table['version']!r}"
        )

    def test_requires_python_is_a_valid_specifier(self, project_table: Mapping[str, Any]) -> None:
        raw = project_table.get("requires-python")
        assert isinstance(raw, str), "[project].requires-python must be declared"
        try:
            SpecifierSet(raw)
        except InvalidSpecifier as exc:
            raise AssertionError(f"requires-python {raw!r} is invalid: {exc}") from exc

    def test_running_interpreter_satisfies_requires_python(
        self, project_table: Mapping[str, Any]
    ) -> None:
        specifier = SpecifierSet(str(project_table["requires-python"]))
        current = Version(f"{sys.version_info.major}.{sys.version_info.minor}")
        assert specifier.contains(current, prereleases=True), (
            f"the test interpreter ({current}) does not satisfy "
            f"requires-python {specifier}; the CI matrix and metadata disagree"
        )

    def test_classifiers_agree_with_requires_python(self, project_table: Mapping[str, Any]) -> None:
        """Every ``Programming Language :: Python :: X.Y`` classifier must be supported."""
        specifier = SpecifierSet(str(project_table["requires-python"]))
        classifiers = project_table.get("classifiers", [])
        assert isinstance(classifiers, list), "[project].classifiers must be a list"

        declared: Set[str] = set()
        for classifier in classifiers:
            match = _CLASSIFIER_PY_VERSION.match(str(classifier))
            if match:
                declared.add(match.group(1))

        assert declared, "no versioned Python classifiers are declared"
        unsupported = sorted(
            version for version in declared if not specifier.contains(Version(version))
        )
        assert not unsupported, (
            f"classifiers advertise Python {unsupported} but requires-python "
            f"({specifier}) excludes them"
        )

    def test_readme_and_license_files_exist(self, project_table: Mapping[str, Any]) -> None:
        readme = project_table.get("readme")
        assert isinstance(readme, str), "[project].readme must name a file"
        assert (REPO_ROOT / readme).is_file(), f"readme {readme!r} does not exist"

        license_table = project_table.get("license")
        assert license_table, "[project].license must be declared"

    def test_no_mandatory_runtime_dependencies(self, project_table: Mapping[str, Any]) -> None:
        """The documented 'zero mandatory dependencies' claim, enforced.

        This is a product claim in the README. It is cheap to assert and
        expensive to discover has silently regressed.
        """
        dependencies = project_table.get("dependencies", [])
        assert dependencies == [], (
            "glassbox advertises a zero-mandatory-dependency core but "
            f"[project].dependencies is {dependencies!r}"
        )

    def test_optional_dependencies_are_valid_pep508(self, project_table: Mapping[str, Any]) -> None:
        extras = project_table.get("optional-dependencies", {})
        assert isinstance(extras, Mapping), "[project.optional-dependencies] must be a table"
        assert extras, "at least one optional dependency group is expected"
        for group, entries in extras.items():
            _parse_requirements(entries, f"[project.optional-dependencies].{group}")

    def test_dev_extra_covers_every_other_extra(self, project_table: Mapping[str, Any]) -> None:
        """``dev`` must transitively install every other extra.

        Otherwise a contributor following the documented setup gets a partial
        environment and tests silently skip.
        """
        extras: Mapping[str, Any] = project_table.get("optional-dependencies", {})
        assert "dev" in extras, "a 'dev' extra is expected"

        dev_requirements = _parse_requirements(extras["dev"], "[project.optional-dependencies].dev")
        self_referential = [
            req for req in dev_requirements if req.name.lower() == EXPECTED_DISTRIBUTION_NAME
        ]
        assert self_referential, (
            "the 'dev' extra must pull in the other extras via a self-reference, "
            f"e.g. {EXPECTED_DISTRIBUTION_NAME}[api,yaml,...]"
        )

        covered: Set[str] = set()
        for req in self_referential:
            covered.update(extra.lower() for extra in req.extras)

        expected = {name.lower() for name in extras if name != "dev"}
        missing = sorted(expected - covered)
        assert not missing, f"the 'dev' extra does not include: {missing}"

    def test_lockfile_covers_direct_dev_dependencies(
        self, project_table: Mapping[str, Any]
    ) -> None:
        """Offline drift check for the lock's documented Python 3.13 target."""
        lock_path = REPO_ROOT / "requirements-lock.txt"
        assert lock_path.is_file(), "requirements-lock.txt is missing"
        locked = {
            name.lower().replace("_", "-"): Version(version)
            for name, version in re.findall(
                r"(?m)^([A-Za-z0-9_.-]+)==([^\s\\]+)",
                lock_path.read_text(encoding="utf-8"),
            )
        }

        extras: Mapping[str, Any] = project_table["optional-dependencies"]
        direct: List[Requirement] = []
        for group, entries in extras.items():
            for requirement in _parse_requirements(entries, f"optional dependency {group}"):
                if requirement.name.lower() != EXPECTED_DISTRIBUTION_NAME:
                    direct.append(requirement)

        failures: List[str] = []
        for requirement in direct:
            if requirement.marker and not requirement.marker.evaluate(
                {"python_version": "3.13", "python_full_version": "3.13.0"}
            ):
                continue
            name = requirement.name.lower().replace("_", "-")
            version = locked.get(name)
            if version is None:
                failures.append(f"{name} is not locked")
            elif requirement.specifier and version not in requirement.specifier:
                failures.append(f"{name}=={version} does not satisfy {requirement.specifier}")
        assert not failures, "lockfile direct-dependency drift: " + "; ".join(sorted(failures))

    def test_project_urls_are_https(self, project_table: Mapping[str, Any]) -> None:
        urls = project_table.get("urls", {})
        assert isinstance(urls, Mapping) and urls, "[project.urls] must be declared"
        insecure = sorted(
            key for key, value in urls.items() if not str(value).startswith("https://")
        )
        assert not insecure, f"[project.urls] entries are not https: {insecure}"


# --------------------------------------------------------------------------- #
# Package discovery
# --------------------------------------------------------------------------- #


class TestPackageDiscovery:
    """Every shipped subpackage must be discovered; nothing else may be."""

    def test_find_directive_is_declared(self, pyproject: Mapping[str, Any]) -> None:
        find = pyproject.get("tool", {}).get("setuptools", {}).get("packages", {}).get("find")
        assert isinstance(find, Mapping), "[tool.setuptools.packages.find] must be declared"
        assert find.get("where") == ["."], "package discovery must be rooted at the repository root"
        assert "glassbox*" in find.get(
            "include", []
        ), "the include pattern must cover glassbox and all of its subpackages"

    def test_discovery_matches_the_filesystem(self, pyproject: Mapping[str, Any]) -> None:
        """setuptools' own resolver must find exactly the packages on disk.

        This catches a new subpackage being added without an ``__init__.py``, and
        catches an ``exclude`` pattern that accidentally drops a real package.
        """
        from setuptools import find_packages  # imported lazily: build-time dependency

        find = pyproject["tool"]["setuptools"]["packages"]["find"]
        discovered = set(
            find_packages(
                where=str(REPO_ROOT),
                include=tuple(find.get("include", ["*"])),
                exclude=tuple(find.get("exclude", [])),
            )
        )
        on_disk = set(_iter_shipped_packages())

        assert on_disk, "no packages found under glassbox/ -- the layout changed"
        missing = sorted(on_disk - discovered)
        assert not missing, (
            f"these packages exist on disk but would not be shipped: {missing}. "
            "They will be missing from the wheel."
        )

    def test_no_non_shippable_directory_is_discovered(self, pyproject: Mapping[str, Any]) -> None:
        from setuptools import find_packages  # imported lazily: build-time dependency

        find = pyproject["tool"]["setuptools"]["packages"]["find"]
        discovered = set(
            find_packages(
                where=str(REPO_ROOT),
                include=tuple(find.get("include", ["*"])),
                exclude=tuple(find.get("exclude", [])),
            )
        )
        leaked = sorted(
            name for name in discovered if name.split(".")[0] in NON_SHIPPABLE_TOP_LEVEL
        )
        assert not leaked, f"non-shippable directories would be packaged: {leaked}"

    def test_every_package_directory_has_an_init(self) -> None:
        """A directory of modules without ``__init__.py`` is silently omitted."""
        offenders: List[str] = []
        for candidate in sorted(PACKAGE_ROOT.rglob("*")):
            if not candidate.is_dir():
                continue
            if candidate.name == "__pycache__" or candidate.name.endswith(".egg-info"):
                continue
            has_modules = any(
                child.suffix == ".py" and child.name != "__init__.py"
                for child in candidate.iterdir()
                if child.is_file()
            )
            if has_modules and not (candidate / "__init__.py").is_file():
                offenders.append(str(candidate.relative_to(REPO_ROOT)))
        assert not offenders, (
            f"these directories contain modules but no __init__.py, so they will "
            f"not be packaged: {offenders}"
        )


# --------------------------------------------------------------------------- #
# Toolchain configuration contract
# --------------------------------------------------------------------------- #


class TestToolchainConfiguration:
    """The lint and type configuration must be valid and reproducible.

    Two live CI defects motivate this class:

    * ``[tool.mypy] python_version = "3.9"`` was rejected outright by current
      mypy ("must be 3.10 or higher"), so the type-check step failed before it
      checked anything;
    * the lint step installed ``black>=24.0``, so each run could resolve a newer
      formatter than the one the source was formatted with. Eighty untouched
      files were reported as needing reformatting -- a red build with no source
      change.
    """

    #: Tools whose output determines whether a build is green. These must be
    #: pinned to an exact version, not a floor.
    EXACT_PIN_REQUIRED = ("black", "isort", "pylint", "mypy", "ruff", "import-linter")

    def test_mypy_python_version_is_supported(self, pyproject: Mapping[str, Any]) -> None:
        configured = pyproject.get("tool", {}).get("mypy", {}).get("python_version")
        assert configured, "[tool.mypy].python_version must be declared"
        assert Version(str(configured)) >= Version(
            "3.10"
        ), f"mypy rejects python_version {configured!r}; it must be 3.10 or higher"

    def test_mypy_target_matches_the_declared_floor(
        self, pyproject: Mapping[str, Any], project_table: Mapping[str, Any]
    ) -> None:
        """Type-checking against a different floor than we ship is a silent gap."""
        configured = Version(str(pyproject["tool"]["mypy"]["python_version"]))
        specifier = SpecifierSet(str(project_table["requires-python"]))
        assert specifier.contains(
            configured
        ), f"mypy targets {configured} but requires-python is {specifier}"

    def test_strict_overrides_cover_the_rebuilt_layers(self, pyproject: Mapping[str, Any]) -> None:
        overrides = pyproject.get("tool", {}).get("mypy", {}).get("overrides", [])
        covered = {module for entry in overrides for module in entry.get("module", [])}
        assert {
            "glassbox.domain.*",
            "glassbox.ports.*",
        } <= covered, "the rebuilt layers must be held to the strict mypy profile"

    @staticmethod
    def _type_debt_register(pyproject: Mapping[str, Any]) -> List[str]:
        """Return every module exempted from type checking."""
        return [
            module
            for entry in pyproject.get("tool", {}).get("mypy", {}).get("overrides", [])
            if entry.get("ignore_errors")
            for module in entry.get("module", [])
        ]

    def test_type_debt_register_only_shrinks(self, pyproject: Mapping[str, Any]) -> None:
        """The ``ignore_errors`` list is a finite, enumerated debt -- not a wildcard.

        A pattern such as ``glassbox.governance.*`` would let a newly written
        module inherit the exemption silently. Every entry must name one module,
        and the rebuilt layers may never appear.
        """
        exempt = self._type_debt_register(pyproject)
        wildcards = sorted(module for module in exempt if "*" in module)
        assert not wildcards, f"type debt must be enumerated, not wildcarded: {wildcards}"

        rebuilt = sorted(
            module
            for module in exempt
            if module.startswith(("glassbox.domain", "glassbox.ports", "glassbox.app"))
        )
        assert not rebuilt, f"rebuilt layers must never be exempt from type checking: {rebuilt}"

        assert (
            len(exempt) <= 30
        ), f"the type debt register grew to {len(exempt)} modules; it may only shrink"

    def test_exempt_modules_still_exist(self, pyproject: Mapping[str, Any]) -> None:
        """A stale exemption hides the fact that the debt was already paid."""
        missing = [
            module
            for module in self._type_debt_register(pyproject)
            if not (REPO_ROOT / (module.replace(".", "/") + ".py")).is_file()
        ]
        assert not missing, f"type debt register names modules that no longer exist: {missing}"

    def test_black_targets_match_the_declared_floor(
        self, pyproject: Mapping[str, Any], project_table: Mapping[str, Any]
    ) -> None:
        targets = pyproject.get("tool", {}).get("black", {}).get("target-version", [])
        assert targets, "[tool.black].target-version must be declared"
        specifier = SpecifierSet(str(project_table["requires-python"]))
        stale = sorted(
            target
            for target in targets
            if not specifier.contains(Version(f"{target[2]}.{target[3:]}"))
        )
        assert not stale, f"black targets versions outside requires-python: {stale}"

    @pytest.mark.parametrize("tool", EXACT_PIN_REQUIRED)
    def test_output_defining_tools_are_pinned_exactly(
        self, project_table: Mapping[str, Any], tool: str
    ) -> None:
        dev = _parse_requirements(
            project_table["optional-dependencies"]["dev"], "[project.optional-dependencies].dev"
        )
        matches = [req for req in dev if req.name.lower() == tool]
        assert matches, f"{tool} is missing from the 'dev' extra"
        specifiers = list(matches[0].specifier)
        assert len(specifiers) == 1 and specifiers[0].operator == "==", (
            f"{tool} must be pinned exactly (found {matches[0].specifier}); "
            "a floor lets CI resolve a version whose output differs from the source"
        )

    def test_ci_pins_match_the_dev_extra(self, project_table: Mapping[str, Any]) -> None:
        """A pin that only exists in one of the two places will drift out of sync."""
        workflow = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert workflow.is_file(), "CI workflow not found"
        workflow_text = workflow.read_text(encoding="utf-8")

        dev = _parse_requirements(
            project_table["optional-dependencies"]["dev"], "[project.optional-dependencies].dev"
        )
        pinned = {
            req.name.lower(): str(req.specifier).lstrip("=")
            for req in dev
            if req.name.lower() in self.EXACT_PIN_REQUIRED
        }
        missing = sorted(
            f"{name}=={version}"
            for name, version in pinned.items()
            if f"{name}=={version}" not in workflow_text
        )
        assert not missing, f"CI does not pin these to the same version as pyproject: {missing}"

    def test_ci_quotes_shell_sensitive_requirement_ranges(self) -> None:
        """Bash treats unquoted ``<``/``>`` in PEP 440 ranges as redirection."""
        workflow_dir = REPO_ROOT / ".github" / "workflows"
        workflow = "\n".join(
            path.read_text(encoding="utf-8") for path in sorted(workflow_dir.glob("*.yml"))
        )
        unquoted = re.findall(
            r'(?m)(?<!\S)(?!["\'])[A-Za-z0-9_.-]+(?:\[[^\]\s]+\])?(?:>=|<=|>|<)[^"\'\s\\]+',
            workflow,
        )
        assert not unquoted, (
            "CI requirement ranges containing '<' or '>' must be quoted for Bash: " f"{unquoted}"
        )

    def test_lockfile_validation_requires_hashes(self) -> None:
        """CI validates the committed artifact without upgrading transitive pins."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert (
            "uv pip install --system --dry-run --require-hashes -r requirements-lock.txt"
            in workflow
        )

    def test_security_job_upgrades_audited_bootstrap_tools(self) -> None:
        """The runner's bundled pip/setuptools are part of the audited environment."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        assert '"pip>=26.1.2"' in workflow
        assert '"setuptools>=83.0.0"' in workflow

    def test_coverage_gate_targets_the_current_architecture(self) -> None:
        """Legacy compatibility code must not dilute the v2 coverage signal."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        for package in (
            "glassbox.domain",
            "glassbox.ports",
            "glassbox.app",
            "glassbox.adapters.inbound",
            "glassbox.adapters.outbound",
        ):
            assert f"--cov={package}" in workflow
        assert "--cov=glassbox \\" not in workflow

    def test_ci_matrix_matches_the_declared_floor(self, project_table: Mapping[str, Any]) -> None:
        """Testing a version we no longer support wastes a job and misleads readers."""
        workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        specifier = SpecifierSet(str(project_table["requires-python"]))
        declared = set(re.findall(r'"(3\.\d+)"', workflow))
        unsupported = sorted(
            version for version in declared if not specifier.contains(Version(version))
        )
        assert not unsupported, f"CI still exercises unsupported Python versions: {unsupported}"

    def test_pytest_markers_are_registered(self, pyproject: Mapping[str, Any]) -> None:
        """An unregistered marker is silently a typo under --strict-markers."""
        markers = (
            pyproject.get("tool", {}).get("pytest", {}).get("ini_options", {}).get("markers", [])
        )
        assert any(str(marker).startswith("slow:") for marker in markers)

    def test_whole_suite_pytest_invocations_have_setuptools_available(self) -> None:
        """``pytest tests/`` collects ``test_packaging.py``'s build-backend contract
        tests, which import ``setuptools.build_meta`` -- but Python 3.12+ venvs no
        longer bundle setuptools by default. This silently failed the "Tests" job
        the moment CI moved to a single Python 3.13 target across every job."""
        workflow_dir = REPO_ROOT / ".github" / "workflows"
        offenders = []
        for path in sorted(workflow_dir.glob("*.yml")):
            text = path.read_text(encoding="utf-8")
            if re.search(r"pytest\s+tests/?(?:\s|$)", text) and "setuptools" not in text:
                offenders.append(path.name)
        assert not offenders, (
            f"these workflows run the whole test suite but never install setuptools: "
            f"{offenders}"
        )


# --------------------------------------------------------------------------- #
# Build and install smoke tests (opt-in)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory: pytest.TempPathFactory) -> Dict[str, Path]:
    """Build a real sdist and wheel once per module run.

    Raises:
        AssertionError: if the PEP 517 build fails or produces unexpected output.
    """
    outdir = tmp_path_factory.mktemp("dist")
    logger.info("building sdist and wheel into %s", outdir)
    completed = subprocess.run(  # nosec B603 - fixed argv, no shell, trusted interpreter
        [sys.executable, "-m", "build", "--sdist", "--wheel", "--outdir", str(outdir)],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=900,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(
            "python -m build failed:\n"
            f"--- stdout ---\n{completed.stdout}\n"
            f"--- stderr ---\n{completed.stderr}"
        )

    sdists = sorted(outdir.glob("*.tar.gz"))
    wheels = sorted(outdir.glob("*.whl"))
    assert len(sdists) == 1, f"expected exactly one sdist, got {[p.name for p in sdists]}"
    assert len(wheels) == 1, f"expected exactly one wheel, got {[p.name for p in wheels]}"
    return {"sdist": sdists[0], "wheel": wheels[0], "outdir": outdir}


@_run_build_tests
class TestBuildAndInstallSmoke:
    """End-to-end proof that the distribution builds and installs.

    Enabled with ``GLASSBOX_RUN_BUILD_TESTS=1``. Requires network access because
    PEP 517 build isolation provisions setuptools into a fresh environment.
    """

    def test_wheel_contains_every_shipped_package(self, artifacts: Mapping[str, Path]) -> None:
        import zipfile

        with zipfile.ZipFile(artifacts["wheel"]) as archive:
            names = set(archive.namelist())

        missing = [
            package
            for package in _iter_shipped_packages()
            if f"{package.replace('.', '/')}/__init__.py" not in names
        ]
        assert not missing, f"the wheel is missing these packages: {missing}"

    def test_wheel_excludes_tests_and_examples(self, artifacts: Mapping[str, Path]) -> None:
        import zipfile

        with zipfile.ZipFile(artifacts["wheel"]) as archive:
            names = archive.namelist()

        leaked = sorted(name for name in names if name.split("/")[0] in NON_SHIPPABLE_TOP_LEVEL)
        assert not leaked, f"the wheel ships non-shippable paths: {leaked[:20]}"

    def test_wheel_installs_into_a_clean_environment(
        self, artifacts: Mapping[str, Path], tmp_path: Path
    ) -> None:
        """Install the wheel in an isolated venv and import from outside the checkout.

        Importing with ``cwd`` set away from the repository is essential: the old
        CI green build was an artefact of importing the source tree directly.
        """
        env_dir = tmp_path / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(str(env_dir))
        python = (
            env_dir
            / ("Scripts" if os.name == "nt" else "bin")
            / ("python.exe" if os.name == "nt" else "python")
        )
        assert python.is_file(), f"virtual environment interpreter not created at {python}"

        install = subprocess.run(  # nosec B603 - fixed argv, no shell, interpreter we just created
            [str(python), "-m", "pip", "install", "--no-input", str(artifacts["wheel"])],
            capture_output=True,
            text=True,
            timeout=900,
            check=False,
        )
        assert install.returncode == 0, (
            "installing the wheel failed:\n"
            f"--- stdout ---\n{install.stdout}\n"
            f"--- stderr ---\n{install.stderr}"
        )

        probe = subprocess.run(  # nosec B603 - fixed argv, no shell
            [
                str(python),
                "-c",
                "import glassbox; print(glassbox.__version__); print(glassbox.__file__)",
            ],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            timeout=300,
            check=False,
        )
        assert probe.returncode == 0, (
            "importing glassbox from the installed wheel failed:\n"
            f"--- stdout ---\n{probe.stdout}\n"
            f"--- stderr ---\n{probe.stderr}"
        )

        version_line, path_line = probe.stdout.strip().splitlines()[:2]
        assert version_line, "the installed package did not report a version"
        assert str(REPO_ROOT) not in path_line, (
            f"the installed package resolved back to the source tree ({path_line}); "
            "the wheel is not self-contained"
        )

        shutil.rmtree(env_dir, ignore_errors=True)
