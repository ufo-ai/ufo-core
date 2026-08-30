"""The `object_tools` capability cases run concurrently against one workspace and each grader finds
its own durable rows by term, so a term that appears in another case's ask grades that case's row
and flips the verdict with the order the cases finish."""

import ast
from pathlib import Path

import pytest

from evals.suites.object_tools import CASES, ROW_TERMS, SINGLE_RUN_FIRES, _graded_run_count

ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOTS = ("core", "extensions", "packs", "evals")
TERM_READERS = frozenset({"_rows_about", "_graded_run_count"})
VENDORED = frozenset({"node_modules", ".venv"})


def test_every_row_term_belongs_to_a_case_and_is_matchable() -> None:
    asks = {case.name: case.message.lower() for case in CASES}

    assert set(ROW_TERMS) <= set(asks)
    assert [term for terms in ROW_TERMS.values() for term in terms if term != term.lower()] == []
    orphans = [
        (name, term)
        for name, terms in ROW_TERMS.items()
        for term in terms
        if term not in asks[name]
    ]
    assert orphans == []


def test_no_row_term_matches_another_cases_ask() -> None:
    asks = {case.name: case.message.lower() for case in CASES}

    collisions = [
        (owner, term, other)
        for owner, terms in ROW_TERMS.items()
        for term in terms
        for other, ask in asks.items()
        if other != owner and term in ask
    ]

    assert collisions == []


def test_a_row_term_where_a_case_name_belongs_fails_at_the_call_site() -> None:
    term = ROW_TERMS["O14-explicit-single-run"][0]

    with pytest.raises(KeyError) as raised:
        _graded_run_count(SINGLE_RUN_FIRES, term)

    assert term in str(raised.value)
    assert "case name" in str(raised.value)


def _case_arguments() -> list[tuple[str, str]]:
    named: list[tuple[str, str]] = []
    for root in SOURCE_ROOTS:
        for path in (ROOT / root).rglob("*.py"):
            if VENDORED & set(path.parts):
                continue
            source = path.read_text(encoding="utf-8")
            if not any(reader in source for reader in TERM_READERS):
                continue
            for node in ast.walk(ast.parse(source)):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id not in TERM_READERS:
                    continue
                named.extend(
                    (str(path.relative_to(ROOT)), argument.value)
                    for argument in node.args
                    if isinstance(argument, ast.Constant) and isinstance(argument.value, str)
                )
    return named


def test_every_term_reader_call_site_names_a_case() -> None:
    """mypy excludes the extension test trees, so a call site that passes a row term instead of a
    case name is caught by this walk of every caller in the repository."""
    sites = _case_arguments()

    assert sites
    assert [(path, name) for path, name in sites if name not in ROW_TERMS] == []
