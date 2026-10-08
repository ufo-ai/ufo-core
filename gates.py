"""Repo-wide CI gates. Run: uv run python gates.py"""

from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
import tomllib
from collections import defaultdict
from collections.abc import Collection, Iterator, Mapping
from pathlib import Path
from sys import stdlib_module_names
from typing import TYPE_CHECKING

from ufo_ext_sources.registry import CONNECTORS

from ufo.harness.o11y import HISTOGRAMS, METRICS, UP_DOWN_METRICS, MetricSpec
from ufo.host.ext.loader import discovered
from ufo.runtime.access.grants import TENANT_URL_RULES
from ufo.runtime.sources.rest import RestConnector

if TYPE_CHECKING:
    from ufo.runtime.ext.manifest import Manifest, Pack

ROOT = Path(__file__).parent
SOURCE_ROOTS = ("core", "extensions", "packs")
CORE_ROOT = Path("core")
CORE_SRC = Path("core/src/ufo")
CORE_TESTS = Path("core/tests")
HARNESS_SRC = Path("core/src/ufo/harness")
RUNTIME_SRC = Path("core/src/ufo/runtime")
ENGINE_CORE_FILES = (
    "agent.py",
    "context.py",
    "replies.py",
    "rounds.py",
    "tools.py",
    "untrusted.py",
    "sandbox/protocol.py",
)
FORBIDDEN_MODULE_NAMES = {"utils", "helpers", "common"}
DB_MODULE = CORE_SRC / "db.py"
ENGINE_TOKENS = ("create_async_engine", "async_sessionmaker", ".begin(")
SDK_EXEMPT_PART = "sdk"
# Called from the hosted repository past `ufo.sdk`: the hosted-sites tests, the skill-loading and
# writing evals. Their call sites are never in this tree.
CROSS_REPO_CALLERS = frozenset({"bounded", "catalog_fits", "site_token"})
SESSION_COOKIE_FACTORY = CORE_SRC / "sdk" / "http.py"
COMPOSITION_ROOTS = (CORE_SRC / "serve.py", CORE_SRC / "proxy_serve.py", CORE_SRC / "cli.py")
ROLE_PACKAGES = (
    "ufo.runtime.surfaces",
    "ufo.runtime.jobs",
    "ufo.runtime.delivery",
    "ufo.runtime.engine",
    "ufo.runtime.profiles",
    "ufo.runtime.queue",
    "ufo.runtime.runtime_instance",
    "ufo.host.spawn_catalog",
    "ufo.runtime.steps",
    "ufo.runtime.subagents",
    "ufo.runtime.tool_bridge",
    "ufo.runtime.transcript",
    "ufo.host.assemble",
)
LOOP_ROLE = frozenset(ROLE_PACKAGES[2:])
BLOB_MODULE = CORE_SRC / "blob.py"
RAW_BLOB_CONSTRUCTORS = frozenset({"FilesystemBlobStore", "S3BlobStore"})
RAW_BLOB_BOOT_MODULES = frozenset(
    {
        CORE_SRC / "serve.py",
        CORE_SRC / "cli.py",
        CORE_SRC / "proxy_serve.py",
        HARNESS_SRC / "sandbox" / "ingress_serve.py",
    }
)
ADMISSION_FACTORY = CORE_SRC / "serve.py"
ADMISSION_FACTORY_NAME = "_admission"
SPEND_THREADED_TYPES = frozenset({"SpendGates", "Ledger"})
UNTHREADED_SPEND = frozenset({"NO_SPEND_GATES", "UNGATED_LEDGER"})
ENVELOPE_COLUMNS = {"workspace_id", "created_at", "updated_at"}
# Read or written only by the image a deploy replaces; the revision after that release drops them,
# and with `egress_rules_generation` the `bump_egress_rules_generation` triggers.
OUTGOING_IMAGE_COLUMNS = {
    ("ledger", "debited_micro_usd"),
    ("workspace", "egress_rules_generation"),
}
SCHEMA_TABLES = CORE_SRC / "schema" / "tables.py"
SCHEDULING_MODULE = Path("extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py")
AMBIENT_SCHEDULE_METHODS = frozenset({"create", "update", "cancel", "list", "inspect"})
UFO_SURFACE_MODULE = Path("extensions/ufo/ufo_ext_ufo/surface.py")
DEBUGGER_SURFACE_MODULE = Path("extensions/debugger/ufo_ext_debugger/surface.py")
DEBUGGER_TAIL_MODULE = Path("extensions/debugger/frontend/src/Tail.tsx")
RUST_WIRE_MODULE = Path("client/src/wire.rs")

TERMINAL_DROPPED_VERBS = frozenset({"debugger", "first"})
REDIS_HUB_MODULE = Path("extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py")
RECORD_MODULE = Path("core/src/ufo/runtime/turns/record.py")
FRAME_EXEMPTIONS: dict[str, frozenset[str]] = {}
EXTENSIONS_ROOT = "extensions"
PACKS_ROOT = "packs"
CORE_SRC_ROOT = "core/src"
PEOPLE_DOCS = frozenset({"AGENTS.md", "CLAUDE.md", "README.md"})
PROMPT_POINTS = {
    "PromptSection": "body",
    "SubagentProfile": "prompt",
    "AgentSpec": "prompt",
    "SetupSchedule": "prompt",
}
PROMPT_LITERAL_BOUND = 100
EXT_SCAFFOLD_DIRS = frozenset({"tests"})
SDK_PUBLIC_PREFIX = "ufo.sdk"
MANIFEST_MODULE = RUNTIME_SRC / "ext" / "manifest.py"
SAMPLE_MODULE = Path(EXTENSIONS_ROOT) / "sample" / "ufo_ext_sample" / "manifest.py"
SINGLE_POINTS = frozenset({"member_added"})
SILENCE_CONSUMER = "writeback_says_nothing"
CORE_SKILLS_DIR = RUNTIME_SRC / "skills"
CORE_SKILL_NAMES = frozenset({"sandbox", "ufo-style"})
SKILL_MANIFEST = "SKILL.md"
RECURSIVE_COPY = re.compile(r"\bcp\s+(?:-\w+\s+)*-\w*[rR]")
SKILL_COPY_MODE = "--no-preserve=mode"
MIGRATION_DIR_PART = "migrations"
CORE_OWNER = "core"
STAMP_REVISION = re.compile(r"\d{14}")
HAND_NUMBERED_REVISION = re.compile(r"\d{4}")
LAST_HAND_NUMBERED_REVISION = "0113"
GRANDFATHERED_CORE_REVISIONS = frozenset(
    {
        "knowledge_graph_0001",
        "sweep_0001",
        "sweep_0002",
        "objectives_0001",
        "objectives_0002",
    }
)
NAME_SEPARATOR = "-"
CANDIDATES_FIELD = "candidates"


def _is_skill_content(path: Path) -> bool:
    return any((parent / SKILL_MANIFEST).is_file() for parent in path.parents)


def _built(path: Path) -> bool:
    """A tree a build wrote — a static site's output, its generated types. Every one of them is
    gitignored, so nothing a reader could edit lives under it."""
    return bool({"dist", ".astro"}.intersection(path.parts))


def _vendored(path: Path) -> bool:
    """A tree whose files came from somewhere else — a python virtualenv, a pnpm install a frontend
    build needs, a copy of an upstream file. None of it is anybody's code here to gate."""
    return bool({".venv", "node_modules", "vendor"}.intersection(path.parts))


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


def _sdk_exports(trees: dict[Path, ast.Module]) -> set[str]:
    """Every name an `ufo.sdk` module re-exports: API for the extensions built on this module,
    whose call sites this tree never holds."""
    return {
        alias.asname or alias.name
        for rel, tree in trees.items()
        if SDK_EXEMPT_PART in rel.parts
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("ufo.")
        for alias in node.names
    }


def _call_names(tree: ast.Module) -> list[str]:
    names = []
    for node in ast.walk(tree):
        match node:
            case ast.Call(func=ast.Name(id=name)) | ast.Call(func=ast.Attribute(attr=name)):
                names.append(name)
    return names


def _set_cookie_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """`ufo.sdk.http.set_session_cookie` takes no `domain`; a raw `Response.set_cookie` could add a
    parent `Domain` and leak a session across a subdomain, environment, or preview host."""
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
    matched = next(
        (pkg for pkg in ROLE_PACKAGES if module == pkg or module.startswith(pkg + ".")), None
    )
    return "loop" if matched in LOOP_ROLE else matched


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


def _raw_blob_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A raw blob backend reads and writes every key in the bucket."""
    failures = []
    for rel, tree in trees.items():
        if rel == BLOB_MODULE or "tests" in rel.parts:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            match node.func:
                case ast.Name(id=name) | ast.Attribute(attr=name):
                    pass
                case _:
                    continue
            if name in RAW_BLOB_CONSTRUCTORS:
                failures.append(
                    f"{rel}: constructs {name} directly — a raw backend lives only inside "
                    f"{BLOB_MODULE}; take a WorkspaceBlobStore or FleetBlobStore"
                )
            if name == "blob_store_for" and rel not in RAW_BLOB_BOOT_MODULES:
                failures.append(
                    f"{rel}: calls blob_store_for outside a boot boundary "
                    f"({', '.join(sorted(str(m) for m in RAW_BLOB_BOOT_MODULES))})"
                )
    return failures


def _admission_construction_failures(trees: dict[Path, ast.Module]) -> list[str]:
    failures = []
    for rel, tree in trees.items():
        if "tests" in rel.parts:
            continue
        inside = {
            id(node)
            for definition in (ast.walk(tree) if rel == ADMISSION_FACTORY else ())
            if isinstance(definition, ast.FunctionDef) and definition.name == ADMISSION_FACTORY_NAME
            for node in ast.walk(definition)
        }
        built = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "Admission"
        ]
        for call in built:
            if id(call) not in inside:
                failures.append(
                    f"{rel}: constructs Admission outside "
                    f"{ADMISSION_FACTORY}:{ADMISSION_FACTORY_NAME} — one turn gate per process, so "
                    f"a fix cannot land on an instance nothing serves"
                )
            elif not any(word.arg == "spend" for word in call.keywords):
                failures.append(
                    f"{rel}: {ADMISSION_FACTORY_NAME} drops spend — admission would run no spend "
                    f"gate the deploy declares"
                )
        if rel == ADMISSION_FACTORY and not any(id(call) in inside for call in built):
            failures.append(
                f"{rel}: {ADMISSION_FACTORY_NAME} no longer builds Admission — shipped code has no "
                f"single construction path for the turn gate"
            )
    return failures


def _spend_parameters(
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> dict[str, int | None]:
    """The fields or arguments `node` takes typed `SpendGates` or `Ledger`, each with the position
    a call may pass it at, None where it is keyword-only."""
    match node:
        case ast.ClassDef(body=body):
            fields = [
                (statement.target.id, statement.annotation)
                for statement in body
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name)
            ]
            return {
                name: position
                for position, (name, annotation) in enumerate(fields)
                if ast.unparse(annotation) in SPEND_THREADED_TYPES
            }
        case _:
            positional = [*node.args.posonlyargs, *node.args.args]
            found: dict[str, int | None] = {
                argument.arg: position
                for position, argument in enumerate(positional)
                if argument.annotation is not None
                and ast.unparse(argument.annotation) in SPEND_THREADED_TYPES
            }
            found.update(
                (argument.arg, None)
                for argument in node.args.kwonlyargs
                if argument.annotation is not None
                and ast.unparse(argument.annotation) in SPEND_THREADED_TYPES
            )
            return found


def _module_callables(
    tree: ast.Module,
) -> dict[str, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef]:
    """Every class and function a module-level name or a function-local name in `tree` binds —
    what a bare-name call there reaches before its imports."""
    found: dict[str, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = {}
    for top in tree.body:
        if isinstance(top, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            found[top.name] = top
        if isinstance(top, ast.FunctionDef | ast.AsyncFunctionDef):
            found.update(
                (inner.name, inner)
                for inner in ast.walk(top)
                if inner is not top and isinstance(inner, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    return found


def _spend_threading_failures(trees: dict[Path, ast.Module]) -> list[str]:
    shipped = {
        rel: tree
        for rel, tree in trees.items()
        if rel.parts[:3] == CORE_SRC.parts and "tests" not in rel.parts
    }
    modules = {_module_name(rel): _module_callables(tree) for rel, tree in shipped.items()}
    failures = []
    for rel, tree in shipped.items():
        local = modules[_module_name(rel)]
        imported = {
            alias.asname or alias.name: modules.get(node.module, {}).get(alias.name)
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
            for alias in node.names
        }
        defaults = {
            id(default)
            for node in ast.walk(tree)
            for default in (
                [*node.args.defaults, *node.args.kw_defaults]
                if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                else [node.value]
                if isinstance(node, ast.AnnAssign | ast.Assign) and node.value is not None
                else []
            )
            if default is not None
        }
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in UNTHREADED_SPEND
                and id(node) not in defaults
            ):
                failures.append(
                    f"{rel}:{node.lineno}: passes {node.id} on — thread the deploy's own"
                )
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            callee = local.get(node.func.id) or imported.get(node.func.id)
            if callee is None:
                continue
            passed = {word.arg for word in node.keywords}
            if None in passed:
                continue
            missing = sorted(
                name
                for name, position in _spend_parameters(callee).items()
                if name not in passed and (position is None or position >= len(node.args))
            )
            if missing:
                failures.append(
                    f"{rel}:{node.lineno}: {node.func.id} drops {', '.join(missing)} — it would "
                    f"fall back to no spend gate"
                )
    return failures


def _is_ext_scaffold(rel: Path) -> bool:
    return len(rel.parts) > 2 and rel.parts[2] in EXT_SCAFFOLD_DIRS


def _sdk_import_failures(trees: dict[Path, ast.Module]) -> list[str]:
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


EXT_SEAM_ROOT = Path(CORE_SRC_ROOT) / "ufo" / "runtime" / "ext"


def _deferred_import_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """`ext.context` imports `ext.surface`, so a deferred `ufo` import at the seam hides a cycle
    back into it."""
    failures = []
    for rel, tree in trees.items():
        if rel.parent != EXT_SEAM_ROOT:
            continue
        guarded = {
            node
            for outer in ast.walk(tree)
            if isinstance(outer, ast.If) and _is_type_checking(outer.test)
            for node in ast.walk(outer)
        }
        for parent in ast.walk(tree):
            if not isinstance(parent, ast.AsyncFunctionDef | ast.FunctionDef):
                continue
            for node in ast.walk(parent):
                if node in guarded or not isinstance(node, ast.ImportFrom) or node.level:
                    continue
                if node.module and (node.module == "ufo" or node.module.startswith("ufo.")):
                    failures.append(
                        f"{rel}:{node.lineno}: {parent.name} defers `{node.module}` into its body; "
                        f"the seam's imports stand at module level"
                    )
    return failures


def _is_type_checking(test: ast.expr) -> bool:
    return (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
        isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
    )


SDK_DIR = Path(CORE_SRC_ROOT) / "ufo" / "sdk"


def _sdk_constants(trees: dict[Path, ast.Module]) -> frozenset[str]:
    """Every SCREAMING_SNAKE_CASE name `ufo.sdk` re-exports — the policy an extension reads rather
    than restates."""
    return frozenset(
        alias.asname
        for rel, tree in trees.items()
        if rel.parent == SDK_DIR
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
        if alias.asname == alias.name and alias.name.isupper()
    )


def _sdk_constant_shadow_failures(
    trees: dict[Path, ast.Module], sdk_constants: frozenset[str]
) -> list[str]:
    failures = []
    for rel, tree in trees.items():
        if rel.parts[0] != EXTENSIONS_ROOT or _is_ext_scaffold(rel):
            continue
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.isupper()
                    and target.id in sdk_constants
                ):
                    failures.append(
                        f"{rel}: {target.id} is exported by {SDK_PUBLIC_PREFIX}; import it "
                        f"rather than restating its value"
                    )
    return failures


LAYERED_BOOT_MODULES = frozenset({HARNESS_SRC / "sandbox" / "ingress_serve.py"})


def _layering_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Host contributions reach the runtime and harness as injected values (`Runtime.manifests`,
    `Runtime.environment`) bound at a composition root."""
    return [
        f"{rel}: the runtime and harness never import ufo.host — host contributions arrive "
        f"through the composition root"
        for rel, tree in trees.items()
        if (rel.is_relative_to(RUNTIME_SRC) or rel.is_relative_to(HARNESS_SRC))
        and rel not in LAYERED_BOOT_MODULES
        for imported in _imported_modules(tree)
        if imported == "ufo.host" or imported.startswith("ufo.host.")
    ]


def _harness_import_failures(trees: dict[Path, ast.Module]) -> list[str]:
    core_paths = {HARNESS_SRC / name for name in ENGINE_CORE_FILES}
    allowed = {"ufo.harness", "ufo.harness.sandbox"} | {
        "ufo.harness." + name.removesuffix(".py").replace("/", ".") for name in ENGINE_CORE_FILES
    }
    failures = []
    for rel, tree in trees.items():
        if rel not in core_paths:
            continue
        for imported in _imported_modules(tree):
            root = imported.split(".", 1)[0]
            if imported not in allowed and root not in stdlib_module_names:
                failures.append(f"{rel}: the engine core cannot import {imported!r}")
    return failures


def _core_layout_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Core Python is source under `ufo` or a test of that source."""
    return [
        f"{path}: core Python must live under core/src/ufo or a core test tree"
        for path in trees
        if path.is_relative_to(CORE_ROOT)
        and not path.is_relative_to(CORE_SRC)
        and not path.is_relative_to(CORE_TESTS)
    ]


def _manifest_point_fields(trees: dict[Path, ast.Module]) -> set[str]:
    """Detection keys on the `= ()` literal; a `field(default_factory=tuple)` point escapes it."""
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
                    and (
                        (isinstance(stmt.value, ast.Tuple) and not stmt.value.elts)
                        or (
                            isinstance(stmt.value, ast.Constant)
                            and stmt.value.value is None
                            and stmt.target.id in SINGLE_POINTS
                        )
                    )
                }
    return fields


def _sample_declared_points(trees: dict[Path, ast.Module]) -> set[str] | None:
    """Detection is literal — `tools=_var` reads as registered even if `_var` is empty — so the
    sample registers each point with an inline non-empty tuple."""
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
                    and keyword.arg
                    not in (
                        "name",
                        "version",
                        "member_context_read",
                        "deploy_bearer_env",
                        "deploy_keys",
                    )
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


def _silence_consumer_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A `post` whose extension never reads `writeback_says_nothing` sends `<response></response>`
    to the member as text."""
    consumers = {
        Path(*rel.parts[:2])
        for rel, tree in trees.items()
        if rel.parts[0] == EXTENSIONS_ROOT
        and not _is_ext_scaffold(rel)
        and SILENCE_CONSUMER in _call_names(tree)
    }
    failures = []
    for rel, tree in trees.items():
        if rel.parts[0] != EXTENSIONS_ROOT or Path(*rel.parts[:2]) in consumers:
            continue
        for node in ast.walk(tree):
            match node:
                case ast.Call(func=ast.Name(id="SurfaceSpec"), keywords=keywords) if any(
                    keyword.arg == "post"
                    and not (
                        isinstance(keyword.value, ast.Constant) and keyword.value.value is None
                    )
                    for keyword in keywords
                ):
                    failures.append(
                        f"{rel}:{node.lineno}: a durable surface declares post and its extension "
                        f"never reads {SILENCE_CONSUMER} — it would post the silence sentinel"
                    )
    return failures


def _job_selector_failures(trees: dict[Path, ast.Module]) -> list[str]:
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
    tree = trees.get(SCHEDULING_MODULE)
    if tree is None:
        return [f"{SCHEDULING_MODULE}: the schedule store is not here — repoint the authority gate"]
    failures = []
    declared = set()
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "ScheduleStore":
            continue
        for method in node.body:
            if not isinstance(method, ast.AsyncFunctionDef):
                continue
            declared.add(method.name)
            if method.name not in AMBIENT_SCHEDULE_METHODS:
                continue
            parameters = (*method.args.posonlyargs, *method.args.args, *method.args.kwonlyargs)
            if any("agent" in parameter.arg.casefold() for parameter in parameters):
                failures.append(
                    f"{SCHEDULING_MODULE}: ScheduleStore.{method.name} accepts an agent selector — "
                    "member-facing schedule authority is ambient"
                )
    gone = sorted(AMBIENT_SCHEDULE_METHODS - declared)
    if gone:
        failures.append(
            f"{SCHEDULING_MODULE}: the authority gate names ScheduleStore methods that are gone "
            f"({', '.join(gone)}) — a rename empties the gate in silence"
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
        match node:
            case ast.Constant(value=str() as sql):
                pass
            case ast.JoinedStr(values=values):
                sql = "".join(
                    value.value
                    for value in values
                    if isinstance(value, ast.Constant) and isinstance(value.value, str)
                )
            case _:
                continue
        if re.search(r"\bcreate\s+(?:function|trigger)\b", sql, re.IGNORECASE) is None:
            continue
        writes.update(re.findall(r"\bset\s+([a-z_][a-z0-9_]*)\s*=", sql, re.IGNORECASE))
        writes.update(re.findall(r"\binto\s+new\.([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE))
        reads.update(
            re.findall(r"\b(?:select|returning)\s+([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE)
        )
        reads.update(re.findall(r"\bnew\.([a-z_][a-z0-9_]*)\b", sql, re.IGNORECASE))
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
        if column in ENVELOPE_COLUMNS or (table, column) in OUTGOING_IMAGE_COLUMNS:
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


def _whole_row_tables(node: ast.expr, aliases: dict[str, set[str]]) -> set[str]:
    match node:
        case ast.Attribute(value=ast.Name(id="tables"), attr=table):
            return {table}
        case ast.Name(id=name):
            return aliases.get(name, set())
        case ast.Starred(value=ast.Attribute(attr="c" | "columns", value=selectable)):
            return _whole_row_tables(selectable, aliases)
        case ast.Call(
            func=ast.Attribute(attr="alias" | "join" | "outerjoin", value=selectable), args=args
        ):
            return _whole_row_tables(selectable, aliases).union(
                *(_whole_row_tables(arg, aliases) for arg in args)
            )
    return set()


def _outgoing_row_read_failures(trees: dict[Path, ast.Module]) -> list[str]:
    failures: list[str] = []
    for rel, tree in trees.items():
        if not str(rel).startswith(str(CORE_SRC)) or "migrations" in rel.parts:
            continue
        aliases: dict[str, set[str]] = {}
        for node in ast.walk(tree):
            match node:
                case ast.Assign(targets=[ast.Name(id=name)], value=value):
                    if named := _whole_row_tables(value, aliases):
                        aliases[name] = named
        for node in ast.walk(tree):
            match node:
                case ast.Call(
                    func=ast.Attribute(attr="select" | "returning", value=owner), args=args
                ):
                    read = _whole_row_tables(owner, aliases)
                case ast.Call(func=ast.Name(id="select"), args=args):
                    read = set()
                case _:
                    continue
            read = read.union(*(_whole_row_tables(arg, aliases) for arg in args))
            failures.extend(
                f"{rel}:{node.lineno}: a whole-row read of {table} names {table}.{column}, which "
                "only the outgoing image keeps"
                for table, column in sorted(OUTGOING_IMAGE_COLUMNS)
                if table in read
            )
    return failures


def _live_frame_kinds(trees: dict[Path, ast.Module]) -> list[str]:
    hub = trees.get(RUNTIME_SRC / "hub.py")
    if hub is None:
        return []
    members: list[str] = []
    for node in hub.body:
        match node:
            case ast.Assign(targets=[ast.Name(id="LiveFrame")], value=value):
                members = [n.id for n in ast.walk(value) if isinstance(n, ast.Name)]
    return members


def _live_frame_failures(trees: dict[Path, ast.Module]) -> list[str]:
    calls = {
        name
        for rel, tree in trees.items()
        if str(rel).startswith(str(CORE_SRC)) and rel != RUNTIME_SRC / "hub.py"
        for name in _call_names(tree)
    }
    return [
        f"hub: LiveFrame kind {m!r} has no emitter"
        for m in _live_frame_kinds(trees)
        if m not in calls
    ]


def _match_class_names(scope: ast.AST) -> set[str]:
    return {
        node.cls.id
        for node in ast.walk(scope)
        if isinstance(node, ast.MatchClass) and isinstance(node.cls, ast.Name)
    }


def _function_scope(tree: ast.Module, function: str, owner: str | None = None) -> ast.AST | None:
    haystack: ast.AST = tree
    if owner is not None:
        classes = [
            node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner
        ]
        if not classes:
            return None
        haystack = classes[0]
    for node in ast.walk(haystack):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == function:
            return node
    return None


def _live_frame_consumer_failures(trees: dict[Path, ast.Module]) -> list[str]:
    failures: list[str] = []
    kinds = set(_live_frame_kinds(trees))
    if not kinds:
        return ["hub: LiveFrame union not found in core/src/ufo/runtime/hub.py"]

    ufo_surface = trees.get(UFO_SURFACE_MODULE)
    debugger = trees.get(DEBUGGER_SURFACE_MODULE)
    redis = trees.get(REDIS_HUB_MODULE)
    record = trees.get(RECORD_MODULE)
    consumers: list[tuple[str, ast.AST | None]] = [
        ("record frame_event", _function_scope(record, "frame_event") if record else None),
        (
            "ufo directives_for",
            _function_scope(ufo_surface, "directives_for") if ufo_surface else None,
        ),
        ("record fold", _function_scope(record, "fold") if record else None),
        ("debugger _sse", _function_scope(debugger, "_sse") if debugger else None),
    ]
    for label, scope in consumers:
        if scope is None:
            failures.append(f"hub: consumer {label} not found")
            continue
        handled = _match_class_names(scope) & (kinds | {"MatchClass"})
        exempt = FRAME_EXEMPTIONS.get(label, frozenset())
        failures.extend(
            f"hub: {label} does not handle live frame kind {kind!r}"
            for kind in sorted(kinds - handled - exempt)
        )
        failures.extend(
            f"hub: {label} exemption for {kind!r} is stale — it is handled"
            for kind in sorted(exempt & handled)
        )

    if redis is None:
        failures.append(f"hub: consumer {REDIS_HUB_MODULE} not found")
    else:
        codec: set[str] = set()
        for node in ast.walk(redis):
            target = None
            match node:
                case ast.Assign(targets=[ast.Name(id="_FRAME_KINDS")], value=value):
                    target = value
                case ast.AnnAssign(target=ast.Name(id="_FRAME_KINDS"), value=value):
                    target = value
            if target is not None:
                codec = {n.id for n in ast.walk(target) if isinstance(n, ast.Name)} & kinds
        failures.extend(
            f"hub: redis _FRAME_KINDS does not carry live frame kind {kind!r}"
            for kind in sorted(kinds - codec)
        )
    return failures


def _debugger_tail_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The debugger's `_sse` names each event it sends and its Tail's `EVENT_KINDS` must spell
    exactly that set, so no kind reaches the browser as an event nothing subscribes to."""
    failures: list[str] = []
    debugger = trees.get(DEBUGGER_SURFACE_MODULE)
    sse = _function_scope(debugger, "_sse") if debugger else None
    if sse is None:
        return [f"sse: {DEBUGGER_SURFACE_MODULE} _sse not found"]
    sent = {
        node.value.decode()
        for node in ast.walk(sse)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, bytes)
        and re.fullmatch(rb"[a-z_]+", node.value)
    }
    tail = _required_text(DEBUGGER_TAIL_MODULE, failures)
    if tail is None:
        return failures
    array = re.search(r"EVENT_KINDS = \[(.*?)\]", tail, re.S)
    listened = set(re.findall(r'"(\w+)"', array.group(1))) if array else set()
    failures.extend(
        f"sse: debugger Tail.tsx does not listen for event {name!r}"
        for name in sorted(sent - listened)
    )
    failures.extend(
        f"sse: debugger Tail.tsx listens for event {name!r} that its surface never sends"
        for name in sorted(listened - sent)
    )
    return failures


def _required_text(rel: Path, failures: list[str]) -> str | None:
    path = ROOT / rel
    if not path.exists():
        failures.append(f"wire: {rel} not found")
        return None
    return path.read_text()


def _directive_calls(scope: ast.AST) -> set[str]:
    verbs: set[str] = set()
    for node in ast.walk(scope):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "directive"
            and node.args
        ):
            continue
        head = node.args[0]
        parts = (head.body, head.orelse) if isinstance(head, ast.IfExp) else (head,)
        verbs |= {
            part.value
            for part in parts
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        }
    return verbs


def _directive_wire_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The verb tables live in `ufo_testsupport.wire_fixture`, which generates the golden fixture
    every client replays."""
    from ufo_testsupport.wire_fixture import ONBOARD_WIRE, WORKSPACE_WIRE

    failures: list[str] = []
    surface = trees.get(UFO_SURFACE_MODULE)
    if surface is None:
        return [f"wire: {UFO_SURFACE_MODULE} not found"]
    emitted = _directive_calls(surface)
    failures.extend(
        f"wire: verb {verb!r} emitted by the ufo surface but not in WORKSPACE_WIRE"
        for verb in sorted(emitted - WORKSPACE_WIRE)
    )
    failures.extend(
        f"wire: WORKSPACE_WIRE verb {verb!r} is emitted nowhere in the ufo surface"
        for verb in sorted(WORKSPACE_WIRE - emitted)
    )

    terminal_verbs = (WORKSPACE_WIRE | ONBOARD_WIRE) - TERMINAL_DROPPED_VERBS
    rust_source = _required_text(RUST_WIRE_MODULE, failures)
    if rust_source is not None:
        rust_verbs = set(re.findall(r'"([a-z_]+)"(?:\s+if .*?)?\s*=>', rust_source))
        failures.extend(
            f"wire: client/src/wire.rs does not parse verb {verb!r}"
            for verb in sorted(terminal_verbs - rust_verbs)
        )
        failures.extend(
            f"wire: client/src/wire.rs parses verb {verb!r} that nothing emits"
            for verb in sorted(rust_verbs - terminal_verbs)
        )
    return failures


def _canonical_stream_failures() -> list[str]:
    """Reads constructed specs from the registry: a source-text scan misses a stream marked
    canonical through the default of a provider's own helper."""
    failures = []
    for name, connector_type in sorted(CONNECTORS.items()):
        connector = connector_type()
        streams = connector.streams()
        declared = connector.streams_list if isinstance(connector, RestConnector) else streams
        undriveable = {spec.name for spec in declared} - {spec.name for spec in streams}
        if undriveable:
            failures.append(
                f"connector {name!r} declares streams it cannot drive "
                f"({', '.join(sorted(undriveable))}) — a spec `streams` filters out can never "
                "produce a source row"
            )
        if not streams:
            failures.append(f"connector {name!r} declares no streams")
        elif not any(stream.canonical for stream in streams):
            failures.append(
                f"connector {name!r} marks none of its {len(streams)} streams canonical — "
                "connecting this provider would sync nothing"
            )
    return failures


def _tenant_rule_failures() -> list[str]:
    """`TENANT_URL_RULES` is the only guard between an admin and a feed that sends the workspace
    credential to a host they control."""
    per_tenant = {
        name
        for name, connector_type in CONNECTORS.items()
        if not connector_type.base_url and connector_type.dials_host
    }
    ruled = set(TENANT_URL_RULES)
    failures = []
    for name in sorted(per_tenant - ruled):
        failures.append(
            f"connector {name!r} declares no fixed base_url and TENANT_URL_RULES names no rule for "
            "it — its connections can never take a tenant URL"
        )
    for name in sorted(ruled - per_tenant):
        failures.append(
            f"TENANT_URL_RULES names {name!r} but that connector declares a fixed base_url — the "
            "rule admits a host the connector never dials"
        )
    return failures


def _init_code_failures(trees: dict[Path, ast.Module]) -> list[str]:
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


LOG_EMITTERS = frozenset({"log", "warn", "log_error"})
RESERVED_LOG_FIELDS = frozenset({"status"})


def _log_field_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Datadog's remapper re-levels a record from its reserved `status` attribute: a turn's `failed`
    filed as emergency, `done` as debug."""
    failures: list[str] = []
    for rel, tree in trees.items():
        for node in ast.walk(tree):
            match node:
                case ast.Call(
                    func=ast.Name(id=name) | ast.Attribute(attr=name), keywords=keywords
                ) if name in LOG_EMITTERS:
                    failures.extend(
                        f"{rel}: {name}() field {keyword.arg!r} is Datadog's reserved level "
                        f"attribute — name the field for what it holds"
                        for keyword in keywords
                        if keyword.arg in RESERVED_LOG_FIELDS
                    )
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
    root = ROOT / CORE_SKILLS_DIR
    if not root.is_dir():
        return [f"skills: core skills directory missing at {CORE_SKILLS_DIR}"]
    present = frozenset(path.parent.name for path in root.rglob(SKILL_MANIFEST))
    return _rogue_skill_failures(present)


def _skill_copy_mode_failures() -> list[str]:
    """`sandbox/build_template.py` publishes the skills tree read-only (`chmod -R a-w`) and `cp -r`
    carries a directory's mode onto the copy, so a plain recursive copy is unwritable."""
    failures = []
    for root in SOURCE_ROOTS:
        for path in (ROOT / root).rglob(SKILL_MANIFEST):
            if _vendored(path):
                continue
            fenced = False
            for line in path.read_text().splitlines():
                if line.lstrip().startswith("```"):
                    fenced = not fenced
                    continue
                if not fenced:
                    continue
                if RECURSIVE_COPY.search(line) and SKILL_COPY_MODE not in line:
                    failures.append(
                        f"{path.relative_to(ROOT)}: {line.strip()!r} copies a directory out of the "
                        f"read-only skills tree — add {SKILL_COPY_MODE} or the project it makes "
                        f"cannot be written to"
                    )
    return failures


def _skill_boundary_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """A bundled skill script runs inside the sandbox, where the ufo package does not exist."""
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


def _stamped_or_grandfathered(revision: str) -> bool:
    """Live databases are stamped with the hand-numbered chain and the retired extension branches'
    revisions."""
    return (
        STAMP_REVISION.fullmatch(revision) is not None
        or revision in GRANDFATHERED_CORE_REVISIONS
        or (
            HAND_NUMBERED_REVISION.fullmatch(revision) is not None
            and revision <= LAST_HAND_NUMBERED_REVISION
        )
    )


def _migration_antijoin_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """Postgres never plans `NOT IN (subquery)` as an anti-join, even over NOT NULL columns;
    `memory_0017` ran 51 minutes that way and took two production deploys down on 2026-09-08."""
    failures = []
    for rel, tree in trees.items():
        if MIGRATION_DIR_PART not in rel.parts:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr != "not_in" or not node.args:
                continue
            argument = node.args[0]
            selects = isinstance(argument, ast.Call) and (
                getattr(argument.func, "attr", None) == "select"
                or getattr(argument.func, "id", None) == "select"
            )
            if selects:
                failures.append(
                    f"{rel}:{node.lineno}: `not_in(select(...))` in a migration — Postgres "
                    f"reads it as a subplan rescanned per row, never an anti-join. Write "
                    f"`~sa.exists(sa.select(...).where(...))`"
                )
    return failures


def _migration_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """An extension chaining onto a sibling via down_revision dangles when the sibling is not
    pinned; it declares depends_on a core revision instead."""
    revisions = _revisions(trees)
    owner_of = {revision: owner for _, owner, revision, _, _ in revisions}
    failures: list[str] = []
    seen: dict[str, Path] = {}
    for rel, owner, revision, _, _ in revisions:
        if revision in seen:
            failures.append(
                f"migrations: revision {revision!r} defined in {rel} and {seen[revision]}"
            )
        seen[revision] = rel
        if owner == CORE_OWNER and not _stamped_or_grandfathered(revision):
            failures.append(
                f"migrations: {rel} revision {revision!r} is not a UTC stamp; scaffold a new core "
                f"migration with `ufoctl new-migration <slug>` — the id carries no ordering, "
                f"down_revision does"
            )
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
    return [
        *(("cdp_providers", spec.backend) for spec in manifest.cdp_providers),
        *(("carriers", spec.name) for spec in manifest.carriers),
        *(("search_providers", spec.backend) for spec in manifest.search_providers),
        *(("flag_providers", spec.backend) for spec in manifest.flag_providers),
        *(("embeds", spec.name) for spec in manifest.embeds),
        *(("indexes", spec.name) for spec in manifest.indexes),
    ]


def _naming_failures(manifests: tuple[Manifest, ...], packs: tuple[Pack, ...]) -> list[str]:
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


def _typed_marker_failures() -> list[str]:
    """mypy reads the source tree here, so a missing `py.typed` shows only in the repository that
    installs this package as a dependency."""
    manifest = tomllib.loads((ROOT / "pyproject.toml").read_text())
    build = manifest["tool"]["hatch"]["build"]["targets"]["wheel"]
    packages = [ROOT / package for package in build.get("packages", ())]
    return [
        f"{package.relative_to(ROOT)} is distributed but carries no py.typed marker"
        for package in packages
        if package.is_dir() and not (package / "py.typed").is_file()
    ]


def _registered_naming_failures() -> list[str]:
    from ufo.host.ext.loader import discovered, discovered_packs

    manifests = tuple(manifest for manifest, _ in discovered().values())
    packs = tuple(discovered_packs().values())
    return _naming_failures(manifests, packs)


COMMENT_CEILING = 2
COMMENT_GATED_ROOTS = (
    ".github",
    "client",
    "core",
    "extensions",
    "packs",
    "sandbox",
    "servers",
    "testsupport",
)
COMMENT_GATED_SUFFIXES = (
    ".css",
    ".py",
    ".rs",
    ".sh",
    ".toml",
    ".ts",
    ".yaml",
    ".yml",
)
COMMENT_KEPT = re.compile(
    r"SPDX|Copyright|Licensed under|@license|@preserve|noqa|type:\s*ignore|mypy:|pyright|ruff:"
    r"|flake8|pylint|eslint|@ts-|prettier-ignore|biome-ignore|(?:c8|istanbul|v8)\s+ignore"
    r"|@vitest-environment|@jsxImportSource|sourceMappingURL|vite-ignore|webpackIgnore"
    r"|\#region|\#endregion|rustfmt::skip|clippy::|shellcheck|tflint|checkov|yamllint|hadolint"
    r"|@charset|-\*-\s*coding",
    re.I,
)
HASH_COMMENT = ("#",)
SLASH_COMMENT = ("//", "/*")
COMMENT_OPENERS = {
    ".css": ("/*",),
    ".py": HASH_COMMENT,
    ".rs": SLASH_COMMENT,
    ".sh": HASH_COMMENT,
    ".toml": HASH_COMMENT,
    ".ts": SLASH_COMMENT,
    ".yaml": HASH_COMMENT,
    ".yml": HASH_COMMENT,
}
PUBLIC_DECLARATION = re.compile(r"^(?:export\b|pub\b|pub\(crate\)|#\[|@)")


def _python_comment_lines(text: str) -> frozenset[int]:
    """Tokenized so a `#` inside a string literal — a test's narrated-module fixture — is not read
    as a comment."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (SyntaxError, tokenize.TokenError):
        return frozenset()
    return frozenset(
        token.start[0]
        for token in tokens
        if token.type == tokenize.COMMENT and not token.line[: token.start[1]].strip()
    )


def _comment_runs(text: str, suffix: str) -> list[tuple[int, list[str]]]:
    """Every run of consecutive whole-line comments, as (first line number, lines). A comment
    trailing code is not a run: it cannot grow into the paragraph this bounds."""
    openers = COMMENT_OPENERS[suffix]
    tokenized = _python_comment_lines(text) if suffix == ".py" else frozenset()
    runs: list[tuple[int, list[str]]] = []
    run: list[str] = []
    start = 0
    inside = False
    for number, line in enumerate(text.split("\n"), start=1):
        body = line.strip()
        if number == 1 and body.startswith("#!"):
            comment = False
        elif suffix == ".py":
            comment = number in tokenized
        elif inside:
            comment = True
            if "*/" in line:
                inside = False
        elif body.startswith(openers):
            comment = True
            if body.startswith("/*") and "*/" not in line:
                inside = True
        else:
            comment = False
        if comment:
            if not run:
                start = number
            run.append(line)
            continue
        if run:
            runs.append((start, run))
            run = []
    if run:
        runs.append((start, run))
    return runs


def _is_public_docstring(run: list[str], after: str) -> bool:
    """`cargo doc` publishes `///` and `//!` to a reader who never opens the file; one floating over
    nothing or over a private item is commentary."""
    opener = run[0].strip()
    if opener.startswith("//!"):
        return True
    if not opener.startswith(("///", "/**")):
        return False
    return bool(PUBLIC_DECLARATION.match(after.strip()))


DocstringOwner = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _python_docstring_runs(rel: Path, text: str) -> list[tuple[int, list[str]]]:
    lines = text.split("\n")
    governed: list[DocstringOwner] = []
    _collect_governed_symbols(ast.parse(text), "tests" in rel.parts, governed)
    return sorted(
        (node.body[0].lineno, lines[node.body[0].lineno - 1 : node.body[0].end_lineno])
        for node in governed
        if ast.get_docstring(node, clean=False) is not None
    )


def _collect_governed_symbols(node: ast.AST, governed: bool, found: list[DocstringOwner]) -> None:
    inner = governed
    match node:
        case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.ClassDef(name=name):
            governed = governed or (name.startswith("_") and not name.endswith("__"))
            inner = governed or not isinstance(node, ast.ClassDef)
            if governed:
                found.append(node)
    for child in ast.iter_child_nodes(node):
        _collect_governed_symbols(child, inner, found)


def _overlong_comments(rel: Path, text: str) -> list[str]:
    """A comment block past the ceiling. A docstring on a public API is exempt at any length, and so
    is a licence or a tool pragma — deleting either breaks a build rather than a paragraph."""
    lines = text.split("\n")
    failures = []
    runs = _comment_runs(text, rel.suffix)
    if rel.suffix == ".py":
        runs = sorted(runs + _python_docstring_runs(rel, text))
    for start, run in runs:
        if len(run) <= COMMENT_CEILING:
            continue
        block = "\n".join(run)
        if COMMENT_KEPT.search(block):
            continue
        after = next((line for line in lines[start - 1 + len(run) :] if line.strip()), "")
        if _is_public_docstring(run, after):
            continue
        failures.append(
            f"{rel}:{start}: comment block of {len(run)} lines — a comment a reader could derive "
            f"from the code it sits on is deleted, not shortened ({COMMENT_CEILING}-line ceiling)"
        )
    return failures


def _comment_length_failures() -> list[str]:
    """A `skills/` folder is exempt: its text is model input and ships only ablated."""
    trees = (path for root in COMMENT_GATED_ROOTS for path in (ROOT / root).rglob("*"))
    failures = []
    for path in sorted({*trees, *ROOT.glob("*")}):
        if path.suffix not in COMMENT_GATED_SUFFIXES or not path.is_file():
            continue
        if _vendored(path) or _is_skill_content(path) or _built(path):
            continue
        failures.extend(_overlong_comments(path.relative_to(ROOT), path.read_text()))
    return failures


def _prompt_home_failures() -> list[str]:
    failures = []
    for root in (CORE_SRC_ROOT, EXTENSIONS_ROOT, PACKS_ROOT):
        for path in sorted((ROOT / root).rglob("*.md")):
            rel = path.relative_to(ROOT)
            if _vendored(path) or "skills" in rel.parts or path.name in PEOPLE_DOCS:
                continue
            if "prompts" not in rel.parts:
                failures.append(
                    f"{rel}: a prompt lives in a prompts/ directory — move it to "
                    f"{rel.parent / 'prompts' / rel.name}"
                )
    return failures


def _prompt_placement_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """PROMPT_LITERAL_BOUND separates every prompt this repo ships from test stubs and one-line
    identities, which sit far beneath it."""
    failures = []
    for rel, tree in trees.items():
        if rel.parts[0] not in ("core", "extensions", "packs") or _vendored(ROOT / rel):
            continue
        assigned = {
            target.id: node.value
            for node in tree.body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            field = PROMPT_POINTS.get(ast.unparse(node.func).split(".")[-1])
            if field is None:
                continue
            for keyword in node.keywords:
                if keyword.arg != field:
                    continue
                value = keyword.value
                if isinstance(value, ast.Name):
                    value = assigned.get(value.id, value)
                if any(
                    isinstance(inner, ast.Attribute) and inner.attr == "read_text"
                    for inner in ast.walk(value)
                ):
                    continue
                written = _literal_chars(value)
                if written >= PROMPT_LITERAL_BOUND:
                    point = ast.unparse(node.func).split(".")[-1]
                    failures.append(
                        f"{rel}:{node.lineno}: {point}.{field} is {written} chars of Python "
                        f"string — move it to a .md the module reads"
                    )
    return failures


def _literal_chars(node: ast.expr) -> int:
    """How much of an expression a model would read as written text."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return len(node.value)
    if isinstance(node, ast.JoinedStr):
        return sum(
            len(part.value)
            for part in node.values
            if isinstance(part, ast.Constant) and isinstance(part.value, str)
        )
    return 0


def _module_strings(tree: ast.Module) -> dict[str, str]:
    strings: dict[str, str] = {}
    for node in tree.body:
        match node:
            case ast.Assign(targets=[ast.Name(id=name)], value=ast.Constant(value=str() as value)):
                strings[name] = value
            case ast.AnnAssign(target=ast.Name(id=name), value=ast.Constant(value=str() as value)):
                strings[name] = value
    return strings


def _named_string(node: ast.expr, strings: Mapping[str, str]) -> str | None:
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.Name(id=name):
            return strings.get(name)
    return None


METRIC_REGISTRY: dict[str, Mapping[str, tuple[str, ...] | None]] = {
    "emit_metric": dict.fromkeys(METRICS),
    "emit_histogram": HISTOGRAMS,
    "emit_up_down_metric": UP_DOWN_METRICS,
}
METRIC_KINDS = {
    "emit_metric": "counter",
    "emit_histogram": "histogram",
    "emit_up_down_metric": "up_down",
}


def _metric_failures(
    trees: dict[Path, ast.Module],
    registry: Mapping[str, Mapping[str, tuple[str, ...] | None]],
    declared: Mapping[str, tuple[MetricSpec, ...]],
) -> list[str]:
    failures = []
    emitted: dict[str | None, set[str]] = defaultdict(set)
    for rel, tree in trees.items():
        if "tests" in rel.parts:
            continue
        owner = (
            Path(rel.parts[2]).stem
            if rel.parts[0] == EXTENSIONS_ROOT and len(rel.parts) >= 3
            else None
        )
        own = {spec.name: spec for spec in declared.get(owner or "", ())}
        strings = _module_strings(tree)
        for node in ast.walk(tree):
            match node:
                case ast.Call(
                    func=ast.Name(id=emitter) | ast.Attribute(attr=emitter), args=[named, *_]
                ) if emitter in registry:
                    pass
                case _:
                    continue
            name = _named_string(named, strings)
            if name is None:
                failures.append(
                    f"{rel}:{node.lineno}: {emitter} names its metric by an expression — pass a "
                    f"literal or a module constant"
                )
                continue
            if name in registry[emitter]:
                dimensions = registry[emitter][name]
            elif name in own and own[name].kind == METRIC_KINDS[emitter]:
                dimensions = own[name].dimensions
            else:
                failures.append(
                    f"{rel}:{node.lineno}: {emitter}({name!r}) is declared neither by core nor "
                    f"by its extension's manifest"
                )
                continue
            emitted[owner].add(name)
            carried = {keyword.arg for keyword in node.keywords if keyword.arg is not None}
            undeclared = sorted(carried - set(dimensions)) if dimensions is not None else []
            if undeclared:
                failures.append(
                    f"{rel}:{node.lineno}: {emitter}({name!r}) carries undeclared dimensions "
                    f"{', '.join(undeclared)}"
                )
    for module, specs in sorted(declared.items()):
        failures.extend(
            f"{EXTENSIONS_ROOT}: {module} declares metric {spec.name!r} that none of its modules "
            f"emits"
            for spec in specs
            if spec.name not in emitted[module]
        )
    for name in sorted({name for names in registry.values() for name in names} - emitted[None]):
        emitters = [module for module, names in emitted.items() if module and name in names]
        if len(emitters) > 1:
            continue
        failures.append(
            f"core declares metric {name!r} that no core module emits"
            + (f" — {emitters[0]} alone emits it, so its manifest declares it" if emitters else "")
        )
    return failures


DEPLOY_KEY_READER = "deploy_env"
DEPLOY_KEY_PREFIX = "UFO_"
DEPLOY_KEY_FIELDS = frozenset({"key_env", "custom_oauth_env", "deploy_bearer_env"})
DEPLOY_KEY_FIELD_DECLARATION = "field(default=None, kw_only=True)"
DEPLOY_KEYS_FIELD = "deploy_keys"
ENVIRON_KEY_METHODS = frozenset({"get", "pop", "setdefault"})
ENV_SETTINGS = frozenset(
    {
        "BROWSERBASE_PROXIES",
        "COMPOSIO_AUTH_CONFIGS",
        "PIPEDREAM_ATTIO_OAUTH_APP_ID",
        "PIPEDREAM_LINEAR_OAUTH_APP_ID",
        "GOOGLE_ADS_LOGIN_CUSTOMER_ID",
        "PIPEDREAM_ENVIRONMENT",
        "PIPEDREAM_GITHUB_OAUTH_APP_ID",
        "PIPEDREAM_GMAIL_OAUTH_APP_ID",
        "UFO_CLIENT_BINARY_URL",
        "UFO_CLIENT_VERSION",
    }
)


def _imported_strings(trees: Mapping[Path, ast.Module]) -> dict[Path, dict[str, str]]:
    own = {rel: _module_strings(tree) for rel, tree in trees.items()}
    by_module = {
        ".".join(rel.with_suffix("").parts[2:]).removesuffix(".__init__"): strings
        for rel, strings in own.items()
    }
    return {
        rel: {
            **{
                alias.asname or alias.name: by_module[node.module][alias.name]
                for node in tree.body
                if isinstance(node, ast.ImportFrom) and node.module in by_module
                for alias in node.names
                if alias.name in by_module[node.module]
            },
            **own[rel],
        }
        for rel, tree in trees.items()
    }


def _env_uses(tree: ast.Module) -> dict[ast.expr, str]:
    modules: set[str] = set()
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.Import(names=aliases):
                modules.update(
                    alias.asname or "os"
                    for alias in aliases
                    if alias.name == "os" or (alias.name.startswith("os.") and not alias.asname)
                )
            case ast.ImportFrom(module=module, names=aliases):
                bound.update(
                    (alias.asname or alias.name, alias.name)
                    for alias in aliases
                    if alias.name == DEPLOY_KEY_READER
                    or (module == "os" and alias.name in ("environ", "getenv"))
                )
    uses: dict[ast.expr, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.Attribute(value=ast.Name(id=module), attr="environ" | "getenv" as role) if (
                module in modules
            ):
                uses[node] = role
            case ast.Attribute(attr=role) | ast.Name(id=role, ctx=ast.Load()) if (
                role == DEPLOY_KEY_READER
            ):
                uses[node] = role
            case ast.Name(id=name, ctx=ast.Load()) if name in bound:
                uses[node] = bound[name]
    return uses


def _env_reads(tree: ast.Module) -> Iterator[tuple[int, ast.expr | None, bool]]:
    for node in ast.walk(tree):
        match node:
            case ast.keyword(arg=field, value=named) if field in DEPLOY_KEY_FIELDS:
                yield node.lineno, named, False
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    for node, role in _env_uses(tree).items():
        parent = parents[node]
        match role, parent, parents.get(parent):
            case "environ", ast.Attribute(attr=method), ast.Call(func=call, args=[named, *_]) if (
                call is parent and method in ENVIRON_KEY_METHODS
            ):
                yield node.lineno, named, True
            case "environ", ast.Subscript(slice=named, ctx=ast.Load()) | ast.Compare(
                left=named, ops=[ast.In() | ast.NotIn()]
            ), _:
                yield node.lineno, named, True
            case "environ", ast.Attribute(attr="copy") | ast.Subscript() | ast.Dict() | (
                ast.keyword()
            ), _:
                pass
            case "environ", ast.Call(func=call), _ if call is not node:
                pass
            case "getenv", ast.Call(func=call, args=[named, *_]), _ if call is node:
                yield node.lineno, named, True
            case "deploy_env", ast.Call(func=call, args=[ast.Attribute(attr=field), *_]), _ if (
                call is node and field in DEPLOY_KEY_FIELDS
            ):
                pass
            case "deploy_env", ast.Call(func=call, args=[named, *_]), _ if call is node:
                yield node.lineno, named, False
            case _:
                yield node.lineno, None, role != DEPLOY_KEY_READER


def _deploy_key_failures(trees: dict[Path, ast.Module], settings: Collection[str]) -> list[str]:
    shipped = {
        rel: tree
        for rel, tree in trees.items()
        if rel.parts[0] == EXTENSIONS_ROOT and len(rel.parts) > 2 and not _is_ext_scaffold(rel)
    }
    strings = _imported_strings(shipped)
    failures = []
    declared: dict[str, set[str]] = defaultdict(set)
    for rel, tree in shipped.items():
        for node in ast.walk(tree):
            match node:
                case ast.keyword(arg=arg, value=value) if arg == DEPLOY_KEYS_FIELD:
                    for element in (
                        value.elts if isinstance(value, ast.Tuple | ast.List) else [value]
                    ):
                        key = _named_string(element, strings[rel])
                        if key is None:
                            failures.append(
                                f"{rel}:{node.lineno}: lists a deploy key by an expression — list "
                                f"literals or module constants"
                            )
                        else:
                            declared[rel.parts[1]].add(key)
                case ast.ClassDef(body=body):
                    failures.extend(
                        f"{rel}:{statement.lineno}: declares {statement.target.id} other than as "
                        f"{DEPLOY_KEY_FIELD_DECLARATION} — a key set positionally or by default "
                        f"escapes this gate"
                        for statement in body
                        if isinstance(statement, ast.AnnAssign)
                        and isinstance(statement.target, ast.Name)
                        and statement.target.id in DEPLOY_KEY_FIELDS
                        and (
                            statement.value is None
                            or ast.unparse(statement.value) != DEPLOY_KEY_FIELD_DECLARATION
                        )
                    )
    read_settings = set()
    for rel, tree in shipped.items():
        for line, named, from_environ in _env_reads(tree):
            match None if named is None else _named_string(named, strings[rel]):
                case None if named is None:
                    failures.append(
                        f"{rel}:{line}: uses the environment other than to read one named key or "
                        f"pass it on whole"
                    )
                case None:
                    failures.append(
                        f"{rel}:{line}: names an environment key by an expression — pass a "
                        f"literal or a module constant"
                    )
                case key if from_environ and not key.startswith(DEPLOY_KEY_PREFIX):
                    failures.append(
                        f"{rel}:{line}: reads {key!r} from os.environ, which never sees "
                        f"{DEPLOY_KEY_PREFIX}{key} — read it through {DEPLOY_KEY_READER}"
                    )
                case key if key in settings:
                    read_settings.add(key)
                case key if from_environ:
                    failures.append(
                        f"{rel}:{line}: reads {key!r} from os.environ — name a non-secret setting "
                        f"in ENV_SETTINGS, or read a deploy key through {DEPLOY_KEY_READER}"
                    )
                case key if key not in declared[rel.parts[1]]:
                    failures.append(
                        f"{rel}:{line}: reads deploy key {key!r} that its manifest leaves out of "
                        f"deploy_keys"
                    )
    failures.extend(
        f"gates.py: ENV_SETTINGS names {key!r}, which no extension reads"
        for key in sorted(set(settings) - read_settings)
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

    aliases = {name: rel for rel, tree in trees.items() for name in _single_return_defs(tree, rel)}
    calls = [name for tree in trees.values() for name in _call_names(tree)]
    exported = _sdk_exports(trees) | CROSS_REPO_CALLERS
    failures.extend(
        f"{rel}: single-return function {name!r} has exactly one call site — inline it"
        for name, rel in aliases.items()
        if calls.count(name) == 1 and name not in exported
    )
    failures.extend(_canonical_stream_failures())
    failures.extend(_tenant_rule_failures())
    failures.extend(_init_code_failures(trees))
    failures.extend(_raw_blob_failures(trees))
    failures.extend(_admission_construction_failures(trees))
    failures.extend(_spend_threading_failures(trees))
    failures.extend(_boundary_failures(trees))
    failures.extend(_sdk_import_failures(trees))
    failures.extend(_sdk_constant_shadow_failures(trees, _sdk_constants(trees)))
    failures.extend(_deferred_import_failures(trees))
    failures.extend(_core_layout_failures(trees))
    failures.extend(_harness_import_failures(trees))
    failures.extend(_layering_failures(trees))
    failures.extend(_conformance_failures(trees))
    failures.extend(_silence_consumer_failures(trees))
    failures.extend(_job_selector_failures(trees))
    failures.extend(_schedule_authority_failures(trees))
    failures.extend(_wiring_failures(trees))
    failures.extend(_outgoing_row_read_failures(trees))
    failures.extend(_live_frame_failures(trees))
    failures.extend(_live_frame_consumer_failures(trees))
    failures.extend(_debugger_tail_failures(trees))
    failures.extend(_directive_wire_failures(trees))
    failures.extend(_to_thread_failures(trees))
    failures.extend(_log_field_failures(trees))
    failures.extend(_set_cookie_failures(trees))
    failures.extend(_skill_failures())
    failures.extend(_skill_copy_mode_failures())
    skill_trees = {
        path.relative_to(ROOT): ast.parse(path.read_text(), filename=str(path))
        for path in _skill_scripts()
    }
    failures.extend(_skill_boundary_failures(skill_trees))
    failures.extend(_migration_failures(trees))
    failures.extend(_migration_antijoin_failures(trees))
    failures.extend(_registered_naming_failures())
    failures.extend(_typed_marker_failures())
    failures.extend(_comment_length_failures())
    failures.extend(_prompt_placement_failures(trees))
    failures.extend(_prompt_home_failures())
    failures.extend(
        _metric_failures(
            trees,
            METRIC_REGISTRY,
            {
                entry.module.split(".", 1)[0]: manifest.metrics
                for manifest, entry in discovered().values()
            },
        )
    )
    failures.extend(_deploy_key_failures(trees, ENV_SETTINGS))

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
