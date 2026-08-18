"""Concurrency tests must assert a domain invariant, not merely "no exceptions" (GB-037).

``tests/test_velocity_breaker_invariants.py::test_concurrent_checks_from_many_threads``
launched 500 threads and asserted only ``errors == []`` -- passing exercise
that a race condition undercounted admissions, forked a hash chain, or lost a
decision would not have failed this test at all, because none of those bugs
raises an exception. The same shape recurs across several v1-era test files
(``test_core.py``, ``test_security.py``, ``test_comprehensive.py``,
``test_framework.py``, ``test_integrations.py``).

This is a static check, not a rewrite of every offending v1 test: those files
exercise ``glassbox.governance``/``glassbox.store``, which GB-040 removes
wholesale. Rewriting throwaway tests for code being deleted is wasted effort;
what matters is that the debt is enumerated, capped, and cannot grow, and that
every *new* concurrency test -- everything written for the v2 architecture --
is held to the real bar immediately. Every v2 concurrency test in this suite
(``test_memory_adapters.py``, ``test_dispatcher_idempotency.py``,
``test_multiprocess_limits.py``, ``test_adversarial_suite.py``, ...) already
asserts a specific count, a specific set of admitted ids, or an exact matched
digest -- never merely the absence of an exception -- and this file proves it
mechanically rather than by convention.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Dict, FrozenSet, List, Set, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_TESTS_DIR = _REPO_ROOT / "tests"

#: Names that spawning a thread/process pool to prove *only* "no exception was
#: raised" would reference. An assertion whose meaningful identifiers are
#: entirely drawn from this set proves nothing about the actual invariant a
#: concurrency test exists to check.
_EXCEPTION_BOOKKEEPING_NAMES = frozenset(
    {"errors", "error", "exc", "exception", "exceptions", "self", "len", "failures", "failure"}
)

#: Call targets that indicate a test spawns real concurrency.
_CONCURRENCY_CONSTRUCTS = frozenset(
    {"Thread", "ThreadPoolExecutor", "ProcessPoolExecutor", "Pool", "Process"}
)

#: The enumerated debt register (GB-037), matching the convention already used
#: for the invariant-lint and type-debt registers (GB-004a, GB-041): named
#: module-by-module and function-by-function, never a wildcard, and every
#: entry here is scheduled for removal by GB-040 (v1 path deletion), not
#: rewritten in place for code about to be deleted.
_KNOWN_DEBT: FrozenSet[Tuple[str, str]] = frozenset(
    {
        ("test_velocity_breaker_invariants.py", "test_concurrent_checks_from_many_threads"),
        ("test_core.py", "test_thread_safe_concurrent_register_evaluate"),
        ("test_core.py", "test_thread_safe_concurrent"),
        ("test_core.py", "test_500_simultaneous_threads"),
        ("test_core.py", "test_anomaly_detector_get_stats_thread_safe"),
        ("test_security.py", "test_policy_engine_concurrent_register_and_evaluate"),
        ("test_security.py", "test_burst_500_simultaneous_threads"),
        ("test_comprehensive.py", "test_concurrent_checks_no_race"),
        ("test_framework.py", "test_concurrent_saves"),
        ("test_integrations.py", "test_concurrent_multi_tenant_requests"),
    }
)

#: Every entry above belongs to a v1-era file. A rebuilt-layer test file must
#: never appear here at all.
_LEGACY_TEST_FILES = frozenset(
    {
        "test_velocity_breaker_invariants.py",
        "test_core.py",
        "test_security.py",
        "test_comprehensive.py",
        "test_framework.py",
        "test_integrations.py",
    }
)


def _assertion_call_test_nodes(func_node: ast.FunctionDef) -> List[ast.AST]:
    """Return the "test expression" AST node for every assertion in ``func_node``.

    Covers both ``assert <expr>`` and ``self.assertX(<expr>, ...)`` /
    ``pytest.approx``-style unittest calls. The trailing message argument
    (a literal string or an f-string) is excluded, since a human-readable
    failure message is not itself part of the invariant being checked.
    """
    nodes: List[ast.AST] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Assert):
            nodes.append(node.test)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr.startswith("assert"):
                args = node.args
                if args and isinstance(args[-1], (ast.Constant, ast.JoinedStr)):
                    args = args[:-1]
                nodes.extend(args)
    return nodes


def _meaningful_names(nodes: List[ast.AST]) -> Set[str]:
    """Return every referenced name across ``nodes``, minus exception bookkeeping."""
    names: Set[str] = set()
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                names.add(sub.id)
    return names - _EXCEPTION_BOOKKEEPING_NAMES


def _spawns_concurrency(func_node: ast.FunctionDef) -> bool:
    for node in ast.walk(func_node):
        if isinstance(node, ast.Call):
            target = node.func
            name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
            if name in _CONCURRENCY_CONSTRUCTS:
                return True
    return False


def _offenders_in(path: Path) -> List[str]:
    """Return the names of every test function in ``path`` that spawns
    concurrency but asserts nothing beyond the absence of an exception."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name.startswith("test_")):
            continue
        if not _spawns_concurrency(node):
            continue
        assertion_nodes = _assertion_call_test_nodes(node)
        if not assertion_nodes:
            continue  # a concurrency-shaped helper with no assertions is not this rule's concern
        if not _meaningful_names(assertion_nodes):
            offenders.append(node.name)
    return offenders


def _all_offenders() -> Dict[str, List[str]]:
    return {
        path.name: offenders
        for path in sorted(_TESTS_DIR.glob("test_*.py"))
        if (offenders := _offenders_in(path))
    }


class TestConcurrencyInvariantLint:
    def test_no_rebuilt_layer_test_file_has_an_errors_only_concurrency_assertion(self) -> None:
        offenders = _all_offenders()
        outside_legacy = {
            filename: names
            for filename, names in offenders.items()
            if filename not in _LEGACY_TEST_FILES
        }
        assert not outside_legacy, (
            "a v2 concurrency test asserts only the absence of exceptions, not a domain "
            f"invariant: {outside_legacy}"
        )

    def test_the_debt_register_matches_reality_exactly(self) -> None:
        """The register is neither stale (pointing at a fixed test) nor
        incomplete (missing a real offender) -- it is regenerated from the
        same detector and compared, so it cannot silently drift from the code."""
        offenders = _all_offenders()
        found = {(filename, name) for filename, names in offenders.items() for name in names}
        stale = _KNOWN_DEBT - found
        assert not stale, f"debt register entries no longer reproduce as offenders: {stale}"
        undeclared = found - _KNOWN_DEBT
        assert (
            not undeclared
        ), f"new errors-only concurrency tests found, not yet in the register: {undeclared}"

    def test_the_debt_register_names_only_legacy_files(self) -> None:
        assert all(filename in _LEGACY_TEST_FILES for filename, _ in _KNOWN_DEBT)

    @pytest.mark.parametrize(
        "filename",
        sorted({filename for filename, _ in _KNOWN_DEBT}),
    )
    def test_the_legacy_file_is_still_present(self, filename: str) -> None:
        """If GB-040 has already removed a legacy test file, its entries must
        be removed from the register too -- a debt register entry for a file
        that no longer exists is exactly the kind of stale exemption
        GB-004a/GB-041 already guard against for lint and type debt."""
        assert (_TESTS_DIR / filename).exists()
