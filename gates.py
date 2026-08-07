"""Repo-wide CI gates. Run: uv run python gates.py"""

from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ufo.ext.manifest import Manifest, Pack

ROOT = Path(__file__).parent
SOURCE_ROOTS = ("core", "extensions", "packs", "evals")
CORE_SRC = Path("core/src/ufo")
FORBIDDEN_MODULE_NAMES = {"utils", "helpers", "common"}
DB_MODULE = CORE_SRC / "db.py"
ENGINE_TOKENS = ("create_async_engine", "async_sessionmaker", ".begin(")
SDK_EXEMPT_PART = "sdk"
SESSION_COOKIE_FACTORY = CORE_SRC / "sdk" / "http.py"
COMPOSITION_ROOTS = (CORE_SRC / "serve.py", CORE_SRC / "proxy_serve.py")
ROLE_PACKAGES = ("ufo.surfaces", "ufo.loop", "ufo.jobs", "ufo.sandbox.proxy")
ENVELOPE_COLUMNS = {"workspace_id", "created_at", "updated_at"}
SCHEMA_TABLES = CORE_SRC / "schema" / "tables.py"
SCHEDULING_MODULE = CORE_SRC / "scheduling.py"
AMBIENT_SCHEDULE_METHODS = frozenset({"create", "pause", "_upsert", "cancel", "list", "inspect"})
PORTAL_SOURCE = Path("extensions/web/frontend/src")
PORTAL_ENTRY = PORTAL_SOURCE / "main.tsx"
PORTAL_THEME = PORTAL_SOURCE / "theme.css"
PORTAL_MODULE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"})
STYLESHEET_IMPORT = re.compile(r"""["'][^"']*\.css["']""")
EXTENSIONS_ROOT = "extensions"
PACKS_ROOT = "packs"
EXT_SCAFFOLD_DIRS = frozenset({"tests"})
SDK_PUBLIC_PREFIX = "ufo.sdk"
MANIFEST_MODULE = CORE_SRC / "ext" / "manifest.py"
SAMPLE_MODULE = Path(EXTENSIONS_ROOT) / "sample" / "ufo_ext_sample.py"
CORE_SKILLS_DIR = CORE_SRC / "skills"
CORE_SKILL_NAMES = frozenset({"sandbox", "delegation"})
SKILL_MANIFEST = "SKILL.md"
MIGRATION_DIR_PART = "migrations"
CORE_OWNER = "core"
NAME_SEPARATOR = "-"
CANDIDATES_FIELD = "candidates"
ENV_ROOTS = Path("infra/envs")
CONTAINMENT_MODULE = CORE_SRC / "sandbox" / "containment.py"
SANDBOX_IMAGE_DIR = CORE_SRC / "sandbox" / "image"
CONTAINMENT_IMPORT_PATH = "ufo.sandbox.containment"
CONTAINMENT_IMPORT = "containment"
CONTAINMENT_GUARDS = frozenset(
    {
        "contained_dir",
        "contained_file",
        "contained_glob",
        "contained_regular",
        "contained_root",
        "configured_root",
        "is_contained_regular",
    }
)
"""The entry points that answer against the real filesystem — a canonicalization, a per-component
`O_NOFOLLOW` descent, an `lstat`. Only what one of these returns may reach a filesystem call.

`workspace_path` is not one, and neither is the lexical tier below: proving a name lexically and
then opening that name is exactly the gap this gate exists to find. `workspace_path` stays in
`DEFERRED_INGRESS`, which is where a site holding only a lexical check belongs."""
LEXICAL_GUARDS = frozenset(
    {
        "contained_leaf",
        "contained_pattern",
        "contained_relative",
        "inbox_name",
    }
)
"""Check 1 on its own, for an ingress naming a path this process cannot stat. Each returns a string
its caller still has to open through a canonical guard — `contained_leaf` drops directory components
without asking what the root is, `contained_relative` resolves `a/../b` to `b` even where `a` is a
symlink — so crediting one here would pass `(ROOT / contained_leaf(name)).write_bytes(...)`, the
CVE-2026-56692 shape itself. They are named so a failure can say which tier the site stopped at."""
FILESYSTEM_METHODS = frozenset(
    {
        "chmod",
        "glob",
        "iterdir",
        "mkdir",
        "read_bytes",
        "read_text",
        "rglob",
        "symlink_to",
        "touch",
        "unlink",
        "write_bytes",
        "write_text",
    }
)
ENUMERATION_METHODS = frozenset({"glob", "rglob"})
FILESYSTEM_FUNCTIONS = {
    "os": frozenset(
        {
            "chmod",
            "chown",
            "link",
            "listdir",
            "makedirs",
            "mkdir",
            "open",
            "remove",
            "rename",
            "replace",
            "rmdir",
            "scandir",
            "symlink",
            "truncate",
            "unlink",
            "walk",
        }
    ),
    "shutil": frozenset(
        {
            "copy",
            "copy2",
            "copyfile",
            "copyfileobj",
            "copytree",
            "make_archive",
            "move",
            "rmtree",
            "unpack_archive",
        }
    ),
}
RECEIVER_ARGUMENTS = frozenset({"self", "cls"})
GUARD_HANDLE_TYPE = "ContainedFile"
PARENT_NAME = ".."
LEXICAL_CHECK_METHODS = frozenset({"is_absolute", "normpath"})
SANDBOX_PROGRAM_SUFFIX = "_PROG"
PROGRAM_FILESYSTEM_TOKENS = ("open(", "os.", "Path(", "shutil.")
DEFERRED_INGRESS: dict[tuple[Path, str], str] = {
    (CORE_SRC / "artifact_token.py", "verify_artifact_token"): (
        "the claim is a blob key, not a host path: the store contains keys at its own root"
    ),
    (CORE_SRC / "blob.py", "_walk"): (
        "the walk starts from _resolve, which contains the key under the lstat'ed store root"
    ),
    (CORE_SRC / "config.py", "load_config"): "the config file is deploy input, read before a turn",
    (Path(EXTENSIONS_ROOT) / "web" / "ufo_ext_web" / "surface.py", "load_assets"): (
        "the portal's built asset directory is deploy input, listed at boot"
    ),
    (
        CORE_SRC / "ext" / "loader.py",
        "extension_digest",
    ): "installed package files, not agent input",
    (CORE_SRC / "ext" / "loader.py", "read_lockfile"): "UFO_EXT_LOCKFILE is deploy input",
    (CORE_SRC / "ext" / "loader.py", "write_lockfile"): "UFO_EXT_LOCKFILE is deploy input",
    (CORE_SRC / "ext" / "store.py", "read_catalog"): "the catalog path is deploy input",
    (
        CORE_SRC / "skills" / "runtime.py",
        "_child_skill_dirs",
    ): "skill dirs ship in the package tree",
    (CORE_SRC / "skills" / "runtime.py", "_load_core_skills"): "core's own skills ship in the tree",
    (CORE_SRC / "skills" / "runtime.py", "parse_skill"): "skill dirs ship in the package tree",
    (SANDBOX_IMAGE_DIR / "sbxfs", "_file_lock"): (
        "the lock name is a digest of the path, not the path: the lock file lives outside the"
        " workspace on purpose, so no root contains it"
    ),
    (CORE_SRC / "sandbox" / "session.py", "workspace_path"): (
        "issue #1112: the logical guard for a container path this process cannot stat; it must"
        " accept /workspace itself, so it stays beside the canonical guard rather than inside it"
    ),
    (CORE_SRC / "sources" / "sync.py", "_read"): (
        "issue #1112 row F10: a folder source's root is operator config; deferred with the"
        " symlink-following read it does under that root"
    ),
    (
        Path(EXTENSIONS_ROOT) / "coding" / "ufo_ext_coding" / "review_checkout.py",
        "_review_relative_path",
    ): (
        "issue #1112: the near-duplicate lexical check to fold in next; the tool's own git-index"
        " mode check is what refuses a symlink today"
    ),
    (
        Path(EXTENSIONS_ROOT) / "coding" / "ufo_ext_coding" / "review_checkout.py",
        "validate_repository",
    ): "a repository slug is an identifier, not a path; the checkout root is the tool's own",
    (
        Path(EXTENSIONS_ROOT) / "coding" / "ufo_ext_coding" / "review_routing.py",
        "repository_has_two_names",
    ): "a repository slug is an identifier, not a path; the checkout root is the tool's own",
    (Path(EXTENSIONS_ROOT) / "e2b" / "ufo_ext_e2b.py", "write"): (
        "issue #1112 row F3: the copy-in is the provider's own `files.write`, which takes no stdin,"
        " so it cannot run COPY_IN_PROG the way docker's `exec -i` does; closing it needs a staged"
        " upload plus an in-sandbox rename through the guard"
    ),
    (
        Path(EXTENSIONS_ROOT) / "skill_create" / "ufo_ext_skill_create" / "manifest.py",
        "FILES_READ_PROG",
    ): (
        "issue #1112 row E5: still its own os.lstat check in a bash program; moves to"
        " SandboxSession.python plus the guard"
    ),
}
SHARED_SINGLETON_RESOURCES = (
    "datadog_integration_aws_account",
    "datadog_integration_aws_external_id",
    "datadog_metric_metadata",
    "datadog_metric_tag_configuration",
)


def _is_skill_content(path: Path) -> bool:
    """A file bundled inside a skill folder — a directory holding a `SKILL.md`, at or above it. Such
    a file is sandbox content mounted verbatim (a script the agent runs in the sandbox, an asset it
    reads), never framework Python: it is held only to the skill boundary gate, not the code gates
    that govern the ufo process."""
    return any((parent / SKILL_MANIFEST).is_file() for parent in path.parents)


def _vendored(path: Path) -> bool:
    """A dependency tree checked out inside a source root — a python virtualenv or an npm install
    a frontend build needs. Its files are nobody's code to gate."""
    return bool({".venv", "node_modules"}.intersection(path.parts))


def _skill_scripts() -> list[Path]:
    return [
        path
        for root in SOURCE_ROOTS
        for path in (ROOT / root).rglob("*.py")
        if not _vendored(path) and _is_skill_content(path)
    ]


def _python_files() -> list[Path]:
    return [
        path
        for root in SOURCE_ROOTS
        for path in (ROOT / root).rglob("*.py")
        if not _vendored(path) and not _is_skill_content(path)
    ]


def _sandbox_scripts() -> list[Path]:
    """The in-sandbox CLIs. They carry no `.py` suffix, so `_python_files` never collects them — and
    they are the ingress that matters most: they run inside the sandbox on paths the model named,
    where a planted link needs no race to win."""
    return [
        path for path in (ROOT / SANDBOX_IMAGE_DIR).iterdir() if path.is_file() and not path.suffix
    ]


def _single_return_defs(tree: ast.Module, path: Path) -> list[str]:
    if SDK_EXEMPT_PART in path.parts:
        return []
    names = []
    for node in tree.body:
        match node:
            case (
                ast.FunctionDef(decorator_list=[], body=body)
                | ast.AsyncFunctionDef(decorator_list=[], body=body)
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


def _set_cookie_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A session cookie stays host-only by construction: `ufo.sdk.http.set_session_cookie` takes no
    `domain`, so it is the only sanctioned setter. A raw `Response.set_cookie` anywhere else could
    add a parent `Domain` and leak a session across a subdomain, environment, or preview host."""
    return [
        f"{rel}: raw set_cookie is banned outside {SESSION_COOKIE_FACTORY} — use set_session_cookie"
        for rel, tree in trees.items()
        if rel != SESSION_COOKIE_FACTORY and "set_cookie" in _call_names(tree)
    ]


def _module_name(rel: Path) -> str:
    if rel.parts[:3] != ("core", "src", "ufo"):
        return ""
    return ".".join(("ufo", *rel.parts[3:])).removesuffix(".py").removesuffix(".__init__")


def _role_of(module: str) -> str | None:
    return next(
        (pkg for pkg in ROLE_PACKAGES if module == pkg or module.startswith(pkg + ".")), None
    )


def _imported_modules(tree: ast.Module) -> list[str]:
    modules: list[str] = []
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
        if not module or rel in COMPOSITION_ROOTS:
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


def _is_ext_scaffold(rel: Path) -> bool:
    """A test under an extension is an in-repo consumer, never shipped in its wheel, so it reaches
    core internals for setup exactly as core's own tests do. The SDK seam is held only for the
    shipped package (`extensions/<name>/<module>`), never for its test scaffold."""
    return len(rel.parts) > 2 and rel.parts[2] in EXT_SCAFFOLD_DIRS


def _sdk_import_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Every file under `extensions/` and `packs/` reaches core only through the public
    `ufo.sdk` surface; any other `ufo.<internal>` import is a break of the seam the SDK
    exists to pin."""
    failures = []
    for rel, tree in trees.items():
        if rel.parts[0] not in (EXTENSIONS_ROOT, PACKS_ROOT):
            continue
        if _is_ext_scaffold(rel):
            continue
        for imported in _imported_modules(tree):
            in_sdk = imported == SDK_PUBLIC_PREFIX or imported.startswith(SDK_PUBLIC_PREFIX + ".")
            in_core = imported == "ufo" or imported.startswith("ufo.")
            if in_core and not in_sdk:
                failures.append(
                    f"{rel}: extensions and packs import ufo only via {SDK_PUBLIC_PREFIX} "
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
                    if keyword.arg is not None
                    and keyword.arg not in ("name", "version")
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


def _job_selector_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Every JobSpec declares a `candidates` selector — the workspaces it has work in — so the
    dispatcher binds each before the handler runs and no handler ever runs unbound or fans the fleet
    itself. `candidates` is a required field with no fleet-wide value, so omission is already a
    construction error; this refuses it statically at every call site too, so a new core or
    extension job physically cannot ship without naming its candidate workspaces, and a
    `candidates=None`
    (a would-be unbound selector) is refused the same."""
    failures = []
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            match node:
                case ast.Call(
                    func=ast.Name(id="JobSpec") | ast.Attribute(attr="JobSpec"),
                    keywords=keywords,
                ):
                    selector = next((kw for kw in keywords if kw.arg == CANDIDATES_FIELD), None)
                    if selector is None:
                        failures.append(
                            f"{rel}: JobSpec declares no {CANDIDATES_FIELD!r} — every job names "
                            f"the workspaces it has work in (no fleet-wide or unbound job)"
                        )
                    elif isinstance(selector.value, ast.Constant) and selector.value.value is None:
                        failures.append(
                            f"{rel}: JobSpec {CANDIDATES_FIELD}=None — a job selector must name "
                            f"candidate workspaces, never None"
                        )
    return failures


def _schedule_authority_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Schedule member paths and their write primitive accept no agent selector."""
    tree = trees.get(SCHEDULING_MODULE)
    if tree is None:
        return []
    failures = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ScheduleStore":
            continue
        for method in node.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            if method.name not in AMBIENT_SCHEDULE_METHODS:
                continue
            parameters = (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs)
            if any("agent" in parameter.arg.casefold() for parameter in parameters):
                failures.append(
                    f"{SCHEDULING_MODULE}: ScheduleStore.{method.name} accepts an agent selector — "
                    "member-facing schedule authority is ambient"
                )
    return failures


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


def _database_program_wiring(tree: ast.Module) -> tuple[set[str], set[str]]:
    writes: set[str] = set()
    reads: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Constant) or not isinstance(node.value, str):
            continue
        sql = node.value
        if re.search(r"\bcreate\s+(?:function|trigger)\b", sql, re.IGNORECASE) is None:
            continue
        writes.update(re.findall(r"\bset\s+([a-z_][a-z0-9_]*)\s*=", sql, re.IGNORECASE))
        writes.update(re.findall(r"\binto\s+new\.([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE))
        reads.update(
            re.findall(r"\b(?:select|returning)\s+([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE)
        )
    return writes, reads


def _wiring_failures(trees: dict[Path, ast.Module]) -> list[str]:
    columns, literals = _schema_columns(trees)
    if not columns:
        return []
    write_columns: set[str] = set()
    read_columns: set[str] = set()
    produced: set[str] = set()
    for rel, tree in trees.items():
        if not str(rel).startswith(str(CORE_SRC)) or rel == SCHEMA_TABLES:
            continue
        if "migrations" in rel.parts:
            database_writes, database_reads = _database_program_wiring(tree)
            write_columns.update(database_writes)
            read_columns.update(database_reads)
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


def _containment_scope(rel: Path) -> bool:
    """Whether a file is one where a path can be built from agent, model, connector, or provider
    input while a turn runs: core's own package and every shipped extension or pack module. A test
    scaffold names its own `tmp_path` roots, and `evals/` is a harness a developer runs against
    roots it chose itself, so neither carries the ingress these gates are about. The guard module is
    its own exemption — it is the implementation every other file is held to."""
    if rel == CONTAINMENT_MODULE or "tests" in rel.parts:
        return False
    return rel.is_relative_to(CORE_SRC) or rel.parts[0] in (EXTENSIONS_ROOT, PACKS_ROOT)


def _functions(tree: ast.Module) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    return [
        node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]


def _expression_names(node: ast.expr) -> set[str]:
    return {inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)}


def _is_guard_call(node: ast.expr, vocabulary: frozenset[str]) -> bool:
    match node:
        case ast.Await(value=value):
            return _is_guard_call(value, vocabulary)
        case ast.Call(func=ast.Name(id=name) | ast.Attribute(attr=name)):
            return name in vocabulary
    return False


def _bindings(func: ast.FunctionDef | ast.AsyncFunctionDef) -> list[tuple[str, ast.expr]]:
    """Every name the function binds to an expression, with that expression — the edges a path
    travels along between the argument it arrived as and the call that opens it. A loop target is
    one of those edges: the real shape of a staging site is `for file in files:`, and a name the
    iteration hands out is as much the caller's as the sequence it came from."""
    bindings = []
    for node in ast.walk(func):
        match node:
            case ast.Assign(targets=[ast.Name(id=name)], value=value):
                bindings.append((name, value))
            case ast.AnnAssign(target=ast.Name(id=name), value=ast.expr() as value):
                bindings.append((name, value))
            case ast.NamedExpr(target=ast.Name(id=name), value=value):
                bindings.append((name, value))
            case ast.withitem(context_expr=value, optional_vars=ast.Name(id=name)):
                bindings.append((name, value))
            case ast.Assign(targets=[ast.Tuple(elts=elts)], value=value):
                bindings.extend(
                    (element.id, value) for element in elts if isinstance(element, ast.Name)
                )
            case (
                ast.For(target=ast.Name(id=name), iter=value)
                | ast.AsyncFor(target=ast.Name(id=name), iter=value)
                | ast.comprehension(target=ast.Name(id=name), iter=value)
            ):
                bindings.append((name, value))
    return bindings


def _guarded_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """The names this function proves with the shared guard: the ones it binds to a guard call's
    result, and nothing else.

    A name merely *handed* to a guard is not proved by it. In a handler shaped like the real ones —
    `contained_file(params["path"], params["workspace"])` — crediting every name mentioned in the
    call would mark `params` proved and launder every other `params[...]` filesystem call in that
    same function. What the guard returns is what may reach the filesystem."""
    return {name for name, value in _bindings(func) if _is_guard_call(value, CONTAINMENT_GUARDS)}


def _is_guard_handle(argument: ast.arg) -> bool:
    """Whether a parameter is typed as the guard's own validated handle. Such a value can only have
    come from a guard call, and its reads run off the pinned parent fd rather than a name, so a
    helper that takes one is downstream of the checks rather than an ingress of its own."""
    return isinstance(argument.annotation, ast.Name) and argument.annotation.id == GUARD_HANDLE_TYPE


def _supplied_path_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Every name that may hold a path the function was handed rather than one it fixed itself: its
    own arguments, plus whatever is derived from them, minus whatever the guard has proved."""
    arguments = func.args
    guarded = _guarded_names(func)
    supplied = {
        argument.arg
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
            *(() if arguments.vararg is None else (arguments.vararg,)),
            *(() if arguments.kwarg is None else (arguments.kwarg,)),
        )
        if argument.arg not in RECEIVER_ARGUMENTS and not _is_guard_handle(argument)
    } - guarded
    bindings = _bindings(func)
    growing = True
    while growing:
        growing = False
        for name, value in bindings:
            if name in supplied or name in guarded:
                continue
            if _expression_names(value) & supplied:
                supplied.add(name)
                growing = True
    return supplied


def _filesystem_calls(
    func: ast.FunctionDef | ast.AsyncFunctionDef,
) -> list[tuple[str, ast.expr]]:
    """Each call in the function that reaches the real filesystem, paired with the expression that
    names the file it reaches. A `Path` method is named by its receiver, an `os`/`shutil` function
    and `open` by their first argument. `Path.open` is deliberately not one of the methods: `open`
    is a verb half the codebase's own objects carry, and the builtin and `os.open` are where a path
    that came from outside actually turns into a descriptor.

    An enumeration names files twice. `root.glob(pattern)` is scoped by its receiver *and* by its
    pattern, and an absolute pattern re-roots the walk at the filesystem anchor with the receiver
    never consulted — issue #1112's own `op_glob` row — so both are paired here."""
    calls = []
    for node in ast.walk(func):
        match node:
            case ast.Call(
                func=ast.Attribute(value=ast.Name(id=module), attr=name), args=[first, *_]
            ) if name in FILESYSTEM_FUNCTIONS.get(module, frozenset()):
                calls.append((f"{module}.{name}", first))
            case ast.Call(
                func=ast.Attribute(value=receiver, attr=str() as name), args=[first, *_]
            ) if name in ENUMERATION_METHODS:
                calls.extend(((f".{name}", receiver), (f".{name}", first)))
            case ast.Call(func=ast.Attribute(value=receiver, attr=name)) if (
                name in FILESYSTEM_METHODS
            ):
                calls.append((f".{name}", receiver))
            case ast.Call(func=ast.Name(id="open"), args=[first, *_]):
                calls.append(("open", first))
    return calls


def _ingress_containment_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A file ingress opens a path it was handed only through `ufo.sandbox.containment`. Validating
    the name and then resolving it leaves the resolution following symlinks, which is how an agent
    plants a link in its own writable directory and reads a host file (CVE-2026-56692) — so a new
    site that builds a host path from agent, model, connector, or provider input and reaches the
    filesystem with it fails here until it goes through the guard. A site that deliberately does
    not is named in `DEFERRED_INGRESS` with the reason — the review this gate exists to force.

    A name the lexical tier returned is still a path it was handed: `contained_leaf` says nothing
    about the root it is joined under and `contained_relative` resolves `a/../b` without asking
    whether `a` is a link, so a site holding one of those is told which tier it stopped at rather
    than told it has no check at all."""
    failures: dict[str, None] = {}
    for rel, tree in trees.items():
        if not _containment_scope(rel):
            continue
        for func in _functions(tree):
            if (rel, func.name) in DEFERRED_INGRESS:
                continue
            supplied = _supplied_path_names(func)
            lexical = {
                name for name, value in _bindings(func) if _is_guard_call(value, LEXICAL_GUARDS)
            }
            for label, expression in _filesystem_calls(func):
                if _is_guard_call(expression, CONTAINMENT_GUARDS):
                    continue
                names = _expression_names(expression)
                handed = sorted(names & supplied)
                if not handed:
                    continue
                stopped = sorted(names & lexical)
                tier = (
                    f" — {', '.join(stopped)} carries only the lexical tier, which proves intent,"
                    " not location"
                    if stopped
                    else ""
                )
                failures[
                    f"{rel}: {func.name} reaches the filesystem with {label} on a path it was "
                    f"handed ({', '.join(handed)}){tier} — open it through "
                    f"{CONTAINMENT_IMPORT_PATH}, or name the site in DEFERRED_INGRESS"
                ] = None
    return list(failures)


def _sandbox_program_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """An in-sandbox program that touches files imports the baked `containment` module rather than
    checking paths itself. These programs run inside the sandbox on paths the model named, where a
    planted symlink needs no race to win, and the image bakes the guard beside `sbxfs` precisely so
    they can call it."""
    failures = []
    for rel, tree in trees.items():
        if not _containment_scope(rel):
            continue
        for node in tree.body:
            match node:
                case ast.Assign(
                    targets=[ast.Name(id=name)], value=ast.Constant(value=str() as program)
                ) if name.endswith(SANDBOX_PROGRAM_SUFFIX):
                    touches = any(token in program for token in PROGRAM_FILESYSTEM_TOKENS)
                    if not touches or CONTAINMENT_IMPORT in program:
                        continue
                    if (rel, name) in DEFERRED_INGRESS:
                        continue
                    failures.append(
                        f"{rel}: {name} opens files in the sandbox without importing "
                        f"{CONTAINMENT_IMPORT!r} — the image bakes the guard beside sbxfs, or name "
                        f"the program in DEFERRED_INGRESS"
                    )
    return failures


def _lexical_containment_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Check 1 has one implementation. Six near-duplicate `".."`/`is_absolute` checks is the census
    issue #1112 opened with, and each was a different subset of the same rule — so the lexical tier
    is published (`contained_relative`, `contained_leaf`, `contained_pattern`) and written afresh
    nowhere else."""
    failures: dict[str, None] = {}
    for rel, tree in trees.items():
        if not _containment_scope(rel):
            continue
        for func in _functions(tree):
            if (rel, func.name) in DEFERRED_INGRESS:
                continue
            for node in ast.walk(func):
                match node:
                    case ast.Compare(left=ast.Constant(value=str() as value), ops=[ast.In()]) if (
                        value == PARENT_NAME
                    ):
                        found = f"{PARENT_NAME!r} in"
                    case ast.Compare(
                        ops=[ast.In()], comparators=[ast.Tuple(elts=elts) | ast.Set(elts=elts)]
                    ) if any(
                        isinstance(elt, ast.Constant) and elt.value == PARENT_NAME for elt in elts
                    ):
                        found = f"in (..., {PARENT_NAME!r})"
                    case ast.Call(func=ast.Attribute(attr=str() as attr)) if (
                        attr in LEXICAL_CHECK_METHODS
                    ):
                        found = f".{attr}()"
                    case _:
                        continue
                failures[
                    f"{rel}: {func.name} checks a path lexically with {found} — the guard's own "
                    f"lexical tier lives in {CONTAINMENT_IMPORT_PATH}, or name the site in "
                    f"DEFERRED_INGRESS"
                ] = None
    return list(failures)


def _rogue_skill_failures(present: frozenset[str]) -> list[str]:
    failures = [
        f"skills: core ships skill {name!r}, outside the fixed set {sorted(CORE_SKILL_NAMES)}"
        for name in sorted(present - CORE_SKILL_NAMES)
    ]
    failures.extend(
        f"skills: core is missing required skill {name!r}"
        for name in sorted(CORE_SKILL_NAMES - present)
    )
    return failures


def _skill_failures() -> list[str]:
    """A skill ships with the thing it teaches, and core teaches only its own builtins, so core
    ships exactly two skills — sandbox, delegation (memory ships with the memory extension). This
    gate is scoped to `core/skills` alone: a third SKILL.md folder there is an extension or a pack
    living in the wrong tree, a missing one is a broken floor. Packs contribute any number of their
    own skills elsewhere, held only to the skill boundary gate."""
    root = ROOT / CORE_SKILLS_DIR
    if not root.is_dir():
        return [f"skills: core skills directory missing at {CORE_SKILLS_DIR}"]
    present = frozenset(path.parent.name for path in root.rglob(SKILL_MANIFEST))
    return _rogue_skill_failures(present)


def _skill_boundary_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A bundled skill script runs inside the sandbox, where the ufo package does not exist; it
    imports only the standard library and third-party tools, never ufo — not core internals and
    not the SDK, which are process-side surfaces. A skill script that imports ufo is either
    mis-placed framework code or a leak of the process boundary into sandbox content."""
    failures = []
    for rel, tree in trees.items():
        for imported in _imported_modules(tree):
            if imported == "ufo" or imported.startswith("ufo."):
                failures.append(
                    f"{rel}: skill script imports {imported!r} — skill content runs in the "
                    f"sandbox and must not import ufo"
                )
    return failures


def _migration_owner(rel: Path) -> str | None:
    """Which schema owner a revision file belongs to: `core` for core's version location, the
    extension (or pack) directory name for an extension's. A non-migration file returns None."""
    if MIGRATION_DIR_PART not in rel.parts:
        return None
    if rel.parts[0] == "core":
        return CORE_OWNER
    if rel.parts[0] in ("extensions", "packs"):
        return rel.parts[1]
    return None


def _str_or_none(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _str_sequence(node: ast.expr | None) -> tuple[str, ...]:
    match node:
        case ast.Constant(value=str() as value):
            return (value,)
        case ast.Tuple(elts=elts) | ast.List(elts=elts):
            return tuple(
                e.value for e in elts if isinstance(e, ast.Constant) and isinstance(e.value, str)
            )
        case _:
            return ()


Revision = tuple[Path, str, str, tuple[str, ...], tuple[str, ...]]


def _revisions(trees: dict[Path, ast.Module]) -> list[Revision]:
    """Every alembic revision across core and the extensions, as (path, owner, revision,
    down_revisions, depends_on). A file is a revision iff it assigns a module-level `revision`."""
    found: list[Revision] = []
    for rel, tree in trees.items():
        owner = _migration_owner(rel)
        if owner is None:
            continue
        constants: dict[str, ast.expr | None] = {}
        for node in tree.body:
            match node:
                case ast.AnnAssign(target=ast.Name(id=name), value=value) if value is not None:
                    constants[name] = value
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    constants[name] = value
        revision = _str_or_none(constants.get("revision"))
        if revision is None:
            continue
        found.append(
            (
                rel,
                owner,
                revision,
                _str_sequence(constants.get("down_revision")),
                _str_sequence(constants.get("depends_on")),
            )
        )
    return found


def _migration_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The migration seam's single-head discipline, generalized across owners so it composes with
    optional table-owning extensions. Each owner (core, each extension) has one base and one head,
    chaining only within itself — an extension never chains onto core or a sibling via down_revision
    (that would fork core or dangle when the sibling is not pinned), it attaches by declaring
    depends_on a core revision so `upgrade heads` applies core's shared tables first. So the DAG is
    deterministic and core-first, with no orphan reference."""
    revisions = _revisions(trees)
    owner_of = {revision: owner for _, owner, revision, _, _ in revisions}
    failures: list[str] = []
    seen: dict[str, Path] = {}
    for rel, _, revision, _, _ in revisions:
        if revision in seen:
            failures.append(
                f"migrations: revision {revision!r} defined in {rel} and {seen[revision]}"
            )
        seen[revision] = rel
    down_by_owner: dict[str, set[str]] = defaultdict(set)
    revs_by_owner: dict[str, set[str]] = defaultdict(set)
    bases_by_owner: dict[str, list[str]] = defaultdict(list)
    for rel, owner, revision, downs, depends in revisions:
        revs_by_owner[owner].add(revision)
        if not downs:
            bases_by_owner[owner].append(revision)
        for down in downs:
            down_by_owner[owner].add(down)
            if down not in owner_of:
                failures.append(
                    f"migrations: {rel} down_revision {down!r} references no known revision"
                )
            elif owner_of[down] != owner:
                failures.append(
                    f"migrations: {rel} chains across owners (down_revision {down!r} is owned "
                    f"by {owner_of[down]!r}); an extension chains only within its own dir and "
                    f"attaches to core via depends_on"
                )
        if (
            owner != CORE_OWNER
            and not downs
            and not any(owner_of.get(dep) == CORE_OWNER for dep in depends)
        ):
            failures.append(
                f"migrations: extension {owner!r} base {revision!r} must declare depends_on a core "
                f"revision so upgrade applies core's shared tables before the extension's"
            )
    for owner, revs in revs_by_owner.items():
        heads = revs - down_by_owner[owner]
        if len(heads) != 1:
            failures.append(
                f"migrations: owner {owner!r} has {len(heads)} heads {sorted(heads)}; "
                f"each owner has a single head"
            )
        if len(bases_by_owner[owner]) != 1:
            failures.append(
                f"migrations: owner {owner!r} has {len(bases_by_owner[owner])} base revisions; "
                f"each owner has a single root"
            )
    return failures


def _provider_names(manifest: Manifest) -> list[tuple[str, str]]:
    """Every registered provider name a manifest declares, paired with the point it came from —
    reading each spec type's own identifier field (cdp and search carry it as `backend`, carrier,
    embed, and index as `name`)."""
    return [
        *(("cdp_providers", spec.backend) for spec in manifest.cdp_providers),
        *(("carriers", spec.name) for spec in manifest.carriers),
        *(("search_providers", spec.backend) for spec in manifest.search_providers),
        *(("embeds", spec.name) for spec in manifest.embeds),
        *(("indexes", spec.name) for spec in manifest.indexes),
    ]


def _naming_failures(manifests: tuple[Manifest, ...], packs: tuple[Pack, ...]) -> list[str]:
    """Every name a subsystem selects a component by uses `_`, never `-`: an extension name, a pack
    name, and each provider name across the cdp/carrier/search/embed/index points. A `-` fractures
    the selector — a config `cdp_provider = "sandbox_chrome"` would never match a provider
    registered as `sandbox-chrome` — so it is refused at the registration seam, not left to surface
    as a boot miss."""
    failures = [
        f"naming: extension name {manifest.name!r} uses {NAME_SEPARATOR!r} — names use '_'"
        for manifest in manifests
        if NAME_SEPARATOR in manifest.name
    ]
    failures.extend(
        f"naming: {point} name {name!r} in extension {manifest.name!r} uses "
        f"{NAME_SEPARATOR!r} — names use '_'"
        for manifest in manifests
        for point, name in _provider_names(manifest)
        if NAME_SEPARATOR in name
    )
    failures.extend(
        f"naming: pack name {pack.name!r} uses {NAME_SEPARATOR!r} — names use '_'"
        for pack in packs
        if NAME_SEPARATOR in pack.name
    )
    return failures


def _env_terraform() -> dict[Path, str]:
    """Every environment root's terraform, keyed by path so a gate can name the root a declaration
    sits in. Read through one function because a mistyped root is silent otherwise — a glob that
    matches nothing turns every gate over it into a no-op that still reports success."""
    return {path: path.read_text() for path in (ROOT / ENV_ROOTS).glob("*/*.tf")}


def _shared_singleton_failures(sources: dict[Path, str]) -> list[str]:
    """A resource declared in more than one environment root when one remote object backs them all.
    A second declaration puts two states on that object and each apply takes it back from the
    other."""
    failures = []
    for resource in SHARED_SINGLETON_RESOURCES:
        declaration = f'resource "{resource}"'
        roots = sorted({path.parent.name for path, text in sources.items() if declaration in text})
        if len(roots) > 1:
            failures.append(
                f"{resource} is declared in {', '.join(roots)}; "
                "one root owns it and the others read its effects"
            )
    return failures


def _registered_naming_failures() -> list[str]:
    """Load the installed manifests and packs the way the loader does — every `ufo.extension` and
    `ufo.pack` entry point resolved to the value object it declares — and scan their registered
    names for `-`."""
    from ufo.ext.loader import discovered, discovered_packs

    manifests = tuple(manifest for manifest, _ in discovered().values())
    packs = tuple(discovered_packs().values())
    return _naming_failures(manifests, packs)


def _portal_style_failures() -> list[str]:
    """The portal's look lives in the Tailwind theme and the component set. A view that imports a
    stylesheet or emits a `<style>` tag can restyle a sibling it never named, which is how a rule
    written for one panel silently reshaped another. An inline `style` object is a computed value,
    not a selector, and stays allowed. The portal's own markup entry is checked too: a `<style>`
    there is the same escape by a different door."""
    source = ROOT / PORTAL_SOURCE
    if not source.is_dir():
        return [f"{PORTAL_SOURCE}: the portal source is missing"]
    failures = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(ROOT)
        if path.suffix == ".css" and rel != PORTAL_THEME:
            failures.append(f"{rel}: the theme is the only stylesheet")
        if path.suffix not in PORTAL_MODULE_SUFFIXES:
            continue
        text = path.read_text()
        if "<style" in text:
            failures.append(f"{rel}: a view may not emit a <style> tag")
        if STYLESHEET_IMPORT.search(text) and rel != PORTAL_ENTRY:
            failures.append(f"{rel}: only the entry module imports the theme")
    markup = ROOT / PORTAL_SOURCE.parent / "index.html"
    if not markup.is_file():
        return [*failures, f"{markup.relative_to(ROOT)}: the portal entry markup is missing"]
    if "<style" in markup.read_text():
        failures.append(f"{markup.relative_to(ROOT)}: a view may not emit a <style> tag")
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

    aliases = {name: rel for rel, tree in trees.items() for name in _single_return_defs(tree, rel)}
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
    failures.extend(_job_selector_failures(trees))
    failures.extend(_schedule_authority_failures(trees))
    failures.extend(_wiring_failures(trees))
    failures.extend(_live_frame_failures(trees))
    failures.extend(_to_thread_failures(trees))
    ingress_trees = {
        **trees,
        **{
            path.relative_to(ROOT): ast.parse(path.read_text(), filename=str(path))
            for path in _sandbox_scripts()
        },
    }
    failures.extend(_ingress_containment_failures(ingress_trees))
    failures.extend(_sandbox_program_failures(ingress_trees))
    failures.extend(_lexical_containment_failures(ingress_trees))
    failures.extend(_set_cookie_failures(trees))
    failures.extend(_skill_failures())
    skill_trees = {
        path.relative_to(ROOT): ast.parse(path.read_text(), filename=str(path))
        for path in _skill_scripts()
    }
    failures.extend(_skill_boundary_failures(skill_trees))
    failures.extend(_migration_failures(trees))
    failures.extend(_registered_naming_failures())
    failures.extend(_portal_style_failures())
    terraform = _env_terraform()
    if not terraform:
        failures.append(f"env roots: no terraform found under {ENV_ROOTS}")
    failures.extend(_shared_singleton_failures(terraform))

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
