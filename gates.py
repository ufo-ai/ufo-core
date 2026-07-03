"""Repo-wide CI gates. Run: uv run python gates.py"""

import ast
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent
SOURCE_ROOTS = ("core", "extensions", "packs")
CORE_SRC = Path("core/src/selfhost")
FORBIDDEN_MODULE_NAMES = {"utils", "helpers", "common"}
DB_MODULE = CORE_SRC / "db.py"
ENGINE_TOKENS = ("create_async_engine", "async_sessionmaker", ".begin(")
SDK_EXEMPT_PART = "sdk"
COMPOSITION_ROOT = CORE_SRC / "serve.py"
ROLE_PACKAGES = ("selfhost.surfaces", "selfhost.loop", "selfhost.jobs", "selfhost.sandbox.proxy")
ENVELOPE_COLUMNS = {"workspace_id", "created_at", "updated_at"}
SQL_MARKERS = ("select", "insert", "update")
CONSTRAINT_STARTERS = ("unique", "primary", "check", "foreign", "constraint")


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


def _call_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Name(id=name)) | ast.Call(func=ast.Attribute(attr=name)):
                names.append(name)
    return names


def _module_name(rel: Path) -> str:
    if rel.parts[:3] != ("core", "src", "selfhost"):
        return ""
    return ".".join(("selfhost", *rel.parts[3:])).removesuffix(".py").removesuffix(".__init__")


def _role_of(module: str) -> str | None:
    return next(
        (pkg for pkg in ROLE_PACKAGES if module == pkg or module.startswith(pkg + ".")), None
    )


def _imported_modules(tree: ast.Module) -> list[str]:
    modules = []
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                modules.extend(alias.name for alias in aliases)
            case ast.ImportFrom(module=str() as module):
                modules.append(module)
    return modules


def _boundary_failures(trees: dict[Path, ast.Module]) -> list[str]:
    failures = []
    for rel, tree in trees.items():
        if rel == COMPOSITION_ROOT:
            continue
        role = _role_of(_module_name(rel))
        for imported in _imported_modules(tree):
            target = _role_of(imported)
            if target is not None and target != role:
                failures.append(
                    f"{rel}: imports {imported} across the role boundary "
                    f"(roles talk through queues/blob/hub/HTTP)"
                )
    return failures


def _schema_columns() -> tuple[list[tuple[str, str]], set[str]]:
    columns: list[tuple[str, str]] = []
    literals: set[str] = set()
    for sql_file in sorted((ROOT / CORE_SRC / "schema").glob("*.sql")):
        text = sql_file.read_text()
        literals.update(re.findall(r"'([a-z_]+)'", text))
        for table, body in re.findall(
            r"create table if not exists (\w+) \(((?:[^()]|\([^)]*\))*)\)", text
        ):
            for line in body.splitlines():
                word = line.strip().split(" ")[0].lower()
                if word and word.isidentifier() and not word.startswith(CONSTRAINT_STARTERS):
                    columns.append((table, word))
    return columns, literals


def _sql_strings(trees: dict[Path, ast.Module]) -> list[str]:
    strings = []
    for rel, tree in trees.items():
        if not str(rel).startswith(str(CORE_SRC)):
            continue
        for node in ast.walk(tree):
            match node:
                case ast.Constant(value=str() as value) if any(
                    marker in value.lower() for marker in SQL_MARKERS
                ):
                    strings.append(" ".join(value.lower().split()))
    return strings


def _wiring_failures(trees: dict[Path, ast.Module]) -> list[str]:
    columns, sql_literals = _schema_columns()
    if not columns:
        return []
    statements = _sql_strings(trees)
    failures = []
    for table, column in columns:
        if column in ENVELOPE_COLUMNS:
            continue
        pattern = re.compile(rf"\b{column}\b")
        writes = [
            s
            for s in statements
            if pattern.search(s) and (f"insert into {table}" in s or f"update {table}" in s)
        ]
        reads = [s for s in statements if pattern.search(s) and "select" in s]
        if not writes:
            failures.append(f"schema: {table}.{column} has no write site")
        if not reads:
            failures.append(f"schema: {table}.{column} has no read site")
    produced = {
        node.value
        for rel, tree in trees.items()
        if str(rel).startswith(str(CORE_SRC))
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    for literal in sorted(sql_literals):
        if literal not in produced:
            failures.append(f"schema: literal '{literal}' has no producer in core")
    return failures


def _live_frame_failures(trees: dict[Path, ast.Module]) -> list[str]:
    hub = trees.get(CORE_SRC / "hub.py")
    if hub is None:
        return []
    members: list[str] = []
    for node in hub.body:
        match node:
            case ast.Assign(targets=[ast.Name(id="LiveFrame")], value=value):
                members = [n.id for n in ast.walk(value) if isinstance(n, ast.Name)]
    calls = {
        name
        for rel, tree in trees.items()
        if str(rel).startswith(str(CORE_SRC)) and rel != CORE_SRC / "hub.py"
        for name in _call_names(tree)
    }
    return [f"hub: LiveFrame kind {m!r} has no emitter" for m in members if m not in calls]


def _to_thread_failures(trees: dict[Path, ast.Module]) -> list[str]:
    failures = []
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            match node:
                case ast.Call(
                    func=ast.Attribute(attr="to_thread") | ast.Name(id="to_thread"), args=args
                ) if not (args and isinstance(args[0], ast.Attribute)):
                    failures.append(
                        f"{rel}: to_thread must wrap a named library callable "
                        f"(the no-async-API library it stands in for)"
                    )
    return failures


def main() -> int:
    failures = []
    trees: dict[Path, ast.Module] = {}
    for path in _python_files():
        rel = path.relative_to(ROOT)
        if path.stem in FORBIDDEN_MODULE_NAMES:
            failures.append(f"{rel}: forbidden module name {path.stem!r}")
        text = path.read_text()
        if "arbitrary_types_allowed" in text:
            failures.append(f"{rel}: arbitrary_types_allowed is banned (misclassified internal)")
        if rel != DB_MODULE:
            failures.extend(
                f"{rel}: {token!r} outside {DB_MODULE}" for token in ENGINE_TOKENS if token in text
            )
        trees[rel] = ast.parse(text, filename=str(path))

    aliases = {
        name: rel for rel, tree in trees.items() for name in _single_return_defs(tree, rel)
    }
    calls = [name for tree in trees.values() for name in _call_names(tree)]
    failures.extend(
        f"{rel}: single-return function {name!r} has exactly one call site — inline it"
        for name, rel in aliases.items()
        if calls.count(name) == 1
    )
    failures.extend(_boundary_failures(trees))
    failures.extend(_wiring_failures(trees))
    failures.extend(_live_frame_failures(trees))
    failures.extend(_to_thread_failures(trees))

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
