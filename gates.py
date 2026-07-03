"""Repo-wide CI gates. Run: uv run python gates.py"""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE_ROOTS = ("core", "extensions", "packs")
FORBIDDEN_MODULE_NAMES = {"utils", "helpers", "common"}
DB_MODULE = Path("core/src/selfhost/db.py")
ENGINE_TOKENS = ("create_async_engine", "async_sessionmaker", ".begin(")
SDK_EXEMPT_PART = "sdk"


def _python_files() -> list[Path]:
    return [
        path
        for root in SOURCE_ROOTS
        for path in (ROOT / root).rglob("*.py")
        if ".venv" not in path.parts
    ]


def _single_return_defs(tree: ast.Module, path: Path) -> list[str]:
    if SDK_EXEMPT_PART in path.parts:
        return []
    names = []
    for node in tree.body:
        match node:
            case ast.FunctionDef(decorator_list=[], body=body) | ast.AsyncFunctionDef(
                decorator_list=[], body=body
            ):
                statements = [s for s in body if not isinstance(s, ast.Expr)]
                if len(statements) == 1 and isinstance(statements[0], ast.Return):
                    names.append(node.name)
    return names


def _call_sites(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Name(id=name)) | ast.Call(func=ast.Attribute(attr=name)):
                names.append(name)
    return names


def main() -> int:
    failures = []
    trees = {}
    for path in _python_files():
        rel = path.relative_to(ROOT)
        if path.stem in FORBIDDEN_MODULE_NAMES:
            failures.append(f"{rel}: forbidden module name {path.stem!r}")
        text = path.read_text()
        if "arbitrary_types_allowed" in text:
            failures.append(f"{rel}: arbitrary_types_allowed is banned (misclassified internal)")
        if rel != DB_MODULE and not rel.parts[-2].startswith("test"):
            for token in ENGINE_TOKENS:
                if token in text:
                    failures.append(f"{rel}: {token!r} outside {DB_MODULE}")
        trees[rel] = ast.parse(text, filename=str(path))

    aliases = {
        name: rel for rel, tree in trees.items() for name in _single_return_defs(tree, rel)
    }
    calls = [name for tree in trees.values() for name in _call_sites(tree)]
    for name, rel in aliases.items():
        if calls.count(name) == 1:
            failures.append(
                f"{rel}: single-return function {name!r} has exactly one call site — inline it"
            )

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
