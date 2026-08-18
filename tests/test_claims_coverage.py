"""Claim-coverage CI gate (GB-039).

Parses ``docs/CLAIMS.md`` and asserts every cited test reference
(``tests/test_x.py``, optionally qualified with ``::TestClass::test_method``)
actually exists. A citation that no longer resolves is exactly the failure
mode this card exists to prevent: a claim whose "proof" quietly rotted away
while the documentation kept asserting it.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import List, Set, Tuple

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CLAIMS_PATH = _REPO_ROOT / "docs" / "CLAIMS.md"

#: Matches `tests/test_foo.py`, `tests/test_foo.py::TestBar`, or
#: `tests/test_foo.py::TestBar::test_baz`, however it appears in the markdown
#: (backtick-quoted or plain prose).
_CITATION_PATTERN = re.compile(
    r"tests/test_[A-Za-z0-9_]+\.py(?:::[A-Za-z0-9_]+)?(?:::[A-Za-z0-9_]+)?"
)


def _extract_citations(text: str) -> Set[str]:
    return set(_CITATION_PATTERN.findall(text))


def _parse_citation(citation: str) -> Tuple[str, List[str]]:
    """Split ``tests/test_x.py::A::b`` into (``tests/test_x.py``, [``A``, ``b``])."""
    parts = citation.split("::")
    return parts[0], parts[1:]


def _defines(tree: ast.AST, name: str) -> bool:
    """Whether ``tree`` (a module or class body) defines a class or function ``name``."""
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return True
    return False


def _find_child(tree: ast.AST, name: str) -> "ast.AST | None":
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == name:
                return node
    return None


def _all_citations() -> List[str]:
    text = _CLAIMS_PATH.read_text(encoding="utf-8")
    return sorted(_extract_citations(text))


class TestClaimsCoverage:
    def test_claims_file_exists(self) -> None:
        assert _CLAIMS_PATH.exists()

    def test_at_least_one_citation_is_present(self) -> None:
        assert len(_all_citations()) > 10

    @pytest.mark.parametrize("citation", _all_citations())
    def test_citation_resolves(self, citation: str) -> None:
        file_part, path_parts = _parse_citation(citation)
        target = _REPO_ROOT / file_part
        assert target.exists(), f"{citation}: {file_part} does not exist"

        if not path_parts:
            return  # a bare file citation needs no further resolution

        tree: ast.AST = ast.parse(target.read_text(encoding="utf-8"), filename=str(target))
        node = tree
        resolved_so_far = file_part
        for part in path_parts:
            child = _find_child(node, part)
            assert child is not None, (
                f"{citation}: {part!r} not found under {resolved_so_far} "
                f"(cited test no longer exists -- update or remove the claim in "
                f"docs/CLAIMS.md)"
            )
            node = child
            resolved_so_far = f"{resolved_so_far}::{part}"
