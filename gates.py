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
SCHEMA_TABLES = CORE_SRC / "schema" / "tables.py"
EXTENSIONS_ROOT = "extensions"
SDK_PUBLIC_PREFIX = "selfhost.sdk"
MANIFEST_MODULE = CORE_SRC / "ext" / "manifest.py"
SAMPLE_MODULE = Path(EXTENSIONS_ROOT) / "sample" / "selfhost_ext_sample.py"


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
        module = _module_name(rel)
        if not module or rel == COMPOSITION_ROOT:
            continue
        role = _role_of(module)
        for imported in _imported_modules(tree):
            target = _role_of(imported)
            if target is not None and target != role:
                failures.append(
                    f"{rel}: imports {imported} across the role boundary "
                    f"(roles talk through queues/blob/hub/HTTP)"
                )
    return failures


def _sdk_import_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Every file under `extensions/` reaches core only through the public `selfhost.sdk` surface;
    any other `selfhost.<internal>` import is a break of the seam the SDK exists to pin."""
    failures = []
    for rel, tree in trees.items():
        if rel.parts[0] != EXTENSIONS_ROOT:
            continue
        for imported in _imported_modules(tree):
            in_sdk = imported == SDK_PUBLIC_PREFIX or imported.startswith(SDK_PUBLIC_PREFIX + ".")
            in_core = imported == "selfhost" or imported.startswith("selfhost.")
            if in_core and not in_sdk:
                failures.append(
                    f"{rel}: extensions import selfhost only via {SDK_PUBLIC_PREFIX} "
                    f"(found {imported!r})"
                )
    return failures


def _manifest_point_fields(trees: dict[Path, ast.Module]) -> set[str]:
    """The Manifest's declared points: fields whose default is a literal empty tuple (`= ()`) — the
    `name`/`version` scalars and the `KW_ONLY` sentinel are excluded. Detection keys on the `= ()`
    literal, so a point written `field(default_factory=tuple)` would escape this gate; keep points
    declared as `name: tuple[...] = ()`."""
    tree = trees.get(MANIFEST_MODULE)
    fields: set[str] = set()
    for node in ast.walk(tree) if tree else ():
        match node:
            case ast.ClassDef(name="Manifest", body=body):
                fields = {
                    stmt.target.id
                    for stmt in body
                    if isinstance(stmt, ast.AnnAssign)
                    and isinstance(stmt.target, ast.Name)
                    and isinstance(stmt.value, ast.Tuple)
                    and not stmt.value.elts
                }
    return fields


def _sample_declared_points(trees: dict[Path, ast.Module]) -> set[str] | None:
    """The points the sample registers: Manifest keyword args whose value is not a literal empty
    tuple. Detection is literal — `tools=_var` reads as registered even if `_var` is empty — so keep
    the sample registering each point with an inline non-empty tuple."""
    tree = trees.get(SAMPLE_MODULE)
    if tree is None:
        return None
    declared: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Name(id="Manifest"), keywords=keywords):
                declared = {
                    keyword.arg
                    for keyword in keywords
                    if keyword.arg not in (None, "name", "version")
                    and not (isinstance(keyword.value, ast.Tuple) and not keyword.value.elts)
                }
    return declared


def _conformance_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The sample's non-empty Manifest points must equal the Manifest's point fields, so a field
    added to the Manifest without the sample exercising it fails CI (the sample is the probe)."""
    declared = _sample_declared_points(trees)
    if declared is None:
        return [f"conformance: sample extension missing at {SAMPLE_MODULE}"]
    points = _manifest_point_fields(trees)
    return [
        *(
            f"conformance: sample does not register Manifest point {point!r}"
            for point in sorted(points - declared)
        ),
        *(
            f"conformance: sample registers {point!r}, not a Manifest point"
            for point in sorted(declared - points)
        ),
    ]


def _schema_columns(trees: dict[Path, ast.Module]) -> tuple[list[tuple[str, str]], set[str]]:
    tree = trees.get(SCHEMA_TABLES)
    if tree is None:
        return [], set()
    columns: list[tuple[str, str]] = []
    literals: set[str] = set()
    for node in ast.walk(tree):
        match node:
            case ast.Call(
                func=ast.Attribute(attr="Table"),
                args=[ast.Constant(value=str() as table), *rest],
            ):
                for arg in rest:
                    match arg:
                        case ast.Call(
                            func=ast.Attribute(attr="Column"),
                            args=[ast.Constant(value=str() as column), *_],
                        ):
                            columns.append((table, column))
                        case ast.Call(
                            func=ast.Attribute(attr="CheckConstraint"),
                            args=[ast.Constant(value=str() as check), *_],
                        ):
                            literals.update(re.findall(r"'([a-z_]+)'", check))
    return columns, literals


def _wiring_failures(trees: dict[Path, ast.Module]) -> list[str]:
    columns, literals = _schema_columns(trees)
    if not columns:
        return []
    write_columns: set[str] = set()
    read_columns: set[str] = set()
    produced: set[str] = set()
    for rel, tree in trees.items():
        if (
            not str(rel).startswith(str(CORE_SRC))
            or rel == SCHEMA_TABLES
            or "migrations" in rel.parts
        ):
            continue
        for node in ast.walk(tree):
            match node:
                case ast.Call(func=ast.Attribute(attr="values"), keywords=keywords):
                    write_columns.update(keyword.arg for keyword in keywords if keyword.arg)
                case ast.Attribute(attr=attr, value=ast.Attribute(attr="c")):
                    read_columns.add(attr)
                case ast.Constant(value=str() as value):
                    produced.add(value)
    failures = []
    for table, column in columns:
        if column in ENVELOPE_COLUMNS:
            continue
        if column not in write_columns:
            failures.append(f"schema: {table}.{column} has no write site")
        if column not in read_columns:
            failures.append(f"schema: {table}.{column} has no read site")
    failures.extend(
        f"schema: literal '{literal}' has no producer in core"
        for literal in sorted(literals)
        if literal not in produced
    )
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


def _init_code_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """__init__.py is a package marker, never a place code lives: no imports, no re-exports, no
    definitions. Code goes in a named module the reader can find by its name; a leading docstring
    is the only statement an __init__ may carry."""
    failures = []
    for rel, tree in trees.items():
        if rel.name != "__init__.py":
            continue
        for index, node in enumerate(tree.body):
            is_docstring = (
                index == 0
                and isinstance(node, ast.Expr)
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)
            )
            if is_docstring:
                continue
            failures.append(
                f"{rel}: __init__.py holds code — a package is a marker; code lives in a module"
            )
            break
    return failures


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
    failures.extend(_init_code_failures(trees))
    failures.extend(_boundary_failures(trees))
    failures.extend(_sdk_import_failures(trees))
    failures.extend(_conformance_failures(trees))
    failures.extend(_wiring_failures(trees))
    failures.extend(_live_frame_failures(trees))
    failures.extend(_to_thread_failures(trees))

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
