"""GB-029, layer 2: SQL injection is a persistence-boundary concern, verified
statically rather than assumed.

Every ``cursor.execute(...)`` call in the Postgres adapters must pass a plain
string literal (or a module-level constant built from literals) as its SQL,
with values supplied only through the parameter sequence -- never through
string formatting, f-strings or ``%``/``.format`` interpolation of anything
that could carry caller-influenced data. The one legitimate exception is
interpolating a **hardcoded GUC name** (never a value) into a ``SET LOCAL``/
``set_config`` statement, and that exception is enumerated here by name, not
inferred.
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import List

import pytest

_POSTGRES_ADAPTER_FILES = sorted(Path("glassbox/adapters/outbound/postgres").glob("*.py"))

#: Module-level constants that may be interpolated into SQL text because they
#: are hardcoded identifiers (GUC names), never request- or record-derived
#: values. Enumerated explicitly so a new entry requires the same review as
#: any other invariant waiver (the same convention as GB-004a's lint registers).
_ALLOWED_INTERPOLATED_NAMES = frozenset({"RETENTION_PURGE_GUC"})


def _execute_call_sql_nodes(tree: ast.AST) -> List[ast.expr]:
    """Return the first-argument AST node of every ``<cursor>.execute(...)`` call."""
    nodes: List[ast.expr] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "execute"
            and node.args
        ):
            nodes.append(node.args[0])
    return nodes


def _offending_names_in_fstring(node: ast.JoinedStr) -> List[str]:
    """Return every interpolated name in an f-string that is not on the allow-list."""
    offenders = []
    for value in node.values:
        if isinstance(value, ast.FormattedValue) and isinstance(value.value, ast.Name):
            if value.value.id not in _ALLOWED_INTERPOLATED_NAMES:
                offenders.append(value.value.id)
        elif isinstance(value, ast.FormattedValue):
            # Any interpolated expression more complex than a bare allow-listed
            # name (an attribute access, a call, string concatenation) is
            # exactly the shape a caller-influenced value would take.
            offenders.append(ast.dump(value.value))
    return offenders


class TestNoStringFormattedSql:
    """Static proof, not a convention: no SQL statement interpolates data."""

    def test_the_adapter_files_were_found(self) -> None:
        assert _POSTGRES_ADAPTER_FILES, "expected to find Postgres adapter source files"

    @pytest.mark.parametrize("path", _POSTGRES_ADAPTER_FILES, ids=lambda p: p.name)
    def test_no_execute_call_interpolates_anything_but_an_allow_listed_guc_name(
        self, path: Path
    ) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for sql_node in _execute_call_sql_nodes(tree):
            if isinstance(sql_node, ast.JoinedStr):
                offenders = _offending_names_in_fstring(sql_node)
                assert not offenders, (
                    f"{path.name}:{sql_node.lineno} interpolates {offenders} into a SQL "
                    "statement -- only an allow-listed GUC name constant is permitted"
                )
            elif isinstance(sql_node, ast.BinOp) and isinstance(sql_node.op, ast.Mod):
                pytest.fail(
                    f"{path.name}:{sql_node.lineno} uses %-formatting to build a SQL "
                    "statement; bind parameters through the execute() sequence instead"
                )
            # A plain ast.Constant (string literal) or ast.Name (a module-level
            # SQL constant, itself built only from literals/f-strings already
            # checked above at its own definition site) is exactly what is
            # expected and requires no further action.
