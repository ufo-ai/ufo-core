"""Repo-wide CI gates. Run: uv run python gates.py"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
import tokenize
from collections import defaultdict
from pathlib import Path
from sys import stdlib_module_names
from typing import TYPE_CHECKING

from ufo_ext_sources.registry import CONNECTORS

from ufo.runtime.access.grants import TENANT_URL_RULES
from ufo.runtime.sources.rest import RestConnector

if TYPE_CHECKING:
    from ufo.runtime.ext.manifest import Manifest, Pack

ROOT = Path(__file__).parent
SOURCE_ROOTS = ("core", "extensions", "packs", "evals")
CORE_ROOT = Path("core")
CORE_SRC = Path("core/src/ufo")
CORE_TEST_ROOTS = (Path("core/tests"), Path("core/harness/tests"))
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
FLAG_TOMBSTONES = ROOT / "infra" / "flag_tombstones.json"
RETIRED_RESOURCES = ROOT / "infra" / "retired_resources.json"
INFRA_ROOT = ROOT / "infra"
ENGINE_TOKENS = ("create_async_engine", "async_sessionmaker", ".begin(")
SDK_EXEMPT_PART = "sdk"
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
        Path("evals/__main__.py"),
        Path("evals/issue_recall/materialize.py"),
        Path("evals/memory_100/materialize.py"),
        Path("evals/memory_ingestion/materialize.py"),
    }
)
ADMISSION_FACTORY = CORE_SRC / "serve.py"
ADMISSION_FACTORY_NAME = "_admission"
ADMISSION_HARNESS_ROOT = "evals"
ENVELOPE_COLUMNS = {"workspace_id", "created_at", "updated_at"}
# Written only by the image a deploy replaces; the revision after that release drops them.
OUTGOING_IMAGE_COLUMNS = {
    ("detached_task", "capability_id"),
    ("sandbox_call_capability", "id"),
    ("sandbox_call_capability", "turn_id"),
    ("sandbox_call_capability", "call"),
    ("sandbox_call_capability", "connections"),
}
SCHEMA_TABLES = CORE_SRC / "schema" / "tables.py"
SCHEDULING_MODULE = Path("extensions/scheduled_tasks/ufo_ext_scheduled_tasks/schedules.py")
AMBIENT_SCHEDULE_METHODS = frozenset({"create", "update", "cancel", "list", "inspect"})
PORTAL_SOURCE = Path("extensions/web/frontend/src")
APP_PAGE_GLOB = "extensions/app_*/ufo_ext_*/skills/*/**/*.tsx"
APP_EXTENSION = re.compile(r"app_(?P<slug>[a-z0-9]+)$")
APP_HOME_SKILL = "app-{slug}-home"
APPS_CONFIG = PORTAL_SOURCE.parent / "vite.apps.config.ts"
APPS_TYPECHECK = PORTAL_SOURCE.parent / "tsconfig.apps.json"
APP_HOME_SKILL_FILE = "SKILL.md"
KIT_CATALOGUE = Path("core/src/ufo/runtime/skills/ufo-style/references/kit.md")
KIT_CATALOGUE_SCRIPT = Path("extensions/web/frontend/kit-catalogue.mjs")
KIT_EXPORT = re.compile(r"^  ([A-Za-z][A-Za-z0-9]*),$", re.M)
KIT_DESCRIBED = re.compile(r"^- \*\*`([A-Za-z][A-Za-z0-9]*)`\*\*", re.M)
KIT_NAMED = re.compile(r"`([A-Za-z][A-Za-z0-9]*)`")
KIT_NAMED_HEADING = "## Also published"
COMPOSITION_STEPS = frozenset({"hair", "2xs", "sm", "2xl", "6xl", "8xl"})
COMPOSITION_GAP = re.compile(r"(?<![\w-])gap-(?:x-|y-)?(\[[^\]]*\]|[\w.]+)")
FRAMED_STAT = re.compile(r"<Stat[\s>][^>]*?(?<![\w-])(border)(?![\w-])", re.S)
APP_REBUILD_LINE = "takes the platform kit as it stands today"
APPS_LIST = re.compile(r"const APPS = \[(?P<apps>[^\]]*)\]")
PORTAL_ENTRIES = frozenset({PORTAL_SOURCE / "main.tsx", PORTAL_SOURCE / "apps" / "kit.ts"})
BLOCKS_SOURCE = PORTAL_SOURCE / "blocks"
PORTAL_THEME = PORTAL_SOURCE / "theme.css"
PORTAL_MODULE_SUFFIXES = frozenset({".ts", ".tsx", ".js", ".jsx", ".mts", ".cts"})
STYLESHEET_IMPORT = re.compile(r"""["'][^"']*\.css["']""")
ARBITRARY_VALUE = re.compile(r"[a-z][\w-]*-\[([^\]\n]*)\]")
ARBITRARY_PROPERTY = re.compile(r"\[([a-z-]+:[^\]\n]*)\]")
PORTAL_CLASS_REFUSALS = (
    (re.compile(r"(?<![\w-])space-[xy]-"), "stack with flex and a gap"),
    (re.compile(r"(?<![\w-])dark:"), "color-scheme carries the scheme"),
    (re.compile(r"overflow-hidden text-ellipsis whitespace-nowrap"), "truncate says this"),
    (
        re.compile(r"(?<![\w-])([wh])-(\S+) (?!\1)[wh]-\2(?![\w-])"),
        "one size- utility says it once",
    ),
    (re.compile(r"className=\{`"), "compose classes with cn()"),
)
RAW_CSS_VALUE = re.compile(
    r"#[0-9a-fA-F]|\d+(?:\.\d+)?(?:px|rem|em|ch|ex|vh|vw|vmin|vmax|%)(?![\w-])"
)
WAITING_MODULE = PORTAL_SOURCE / "kernel" / "panel.tsx"
WAITING_COMPONENT = "export function Loading("
WAITING_LINE = re.compile(r"Loading(?:…|\.\.\.)")
WAITING_CLOSE = re.compile(r"^}$", re.M)
UFO_SURFACE_MODULE = Path("extensions/ufo/ufo_ext_ufo/surface.py")
GATEWAY_MODULES = (
    Path("servers/control/src/gateway.rs"),
    Path("servers/control/src/directives.rs"),
)
RUST_WIRE_MODULE = Path("client/src/wire.rs")
ONBOARD_WEB_MODULE = Path("servers/control/src/login.html")
RUST_DIRECTIVE_CALL = re.compile(r'\bdirective\(\s*"([a-z]+)"')
TERMINAL_DROPPED_VERBS = frozenset({"debugger", "first"})
ONBOARD_WEB_DROPPED_VERBS = frozenset({"install"})
WEB_SURFACE_MODULE = Path("extensions/web/ufo_ext_web/surface.py")
DEBUGGER_SURFACE_MODULE = Path("extensions/debugger/ufo_ext_debugger/surface.py")
REDIS_HUB_MODULE = Path("extensions/redis_hub/ufo_ext_redis_hub/stream_hub.py")
TURN_RECORD_MODULE = Path("extensions/web/frontend/src/lib/turnRecord.ts")
CORE_SURFACE_MODULE = Path("core/src/ufo/runtime/ext/surface.py")
SILENCE_SENTINEL_NAME = "SILENCE_SENTINEL"
CALLBACK_PAGE_MODULE = Path("core/src/ufo/sdk/callback_page.py")
CONSENT_MODULES = (Path("extensions/web/frontend/src/lib/consent.tsx"),)
CONSENT_MARK_NAME = "CONSENT_WINDOW_MARK"
DEBUGGER_TAIL_MODULE = Path("extensions/debugger/frontend/src/Tail.tsx")
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
SAMPLE_MODULE = Path(EXTENSIONS_ROOT) / "sample" / "ufo_ext_sample.py"
SILENCE_CONSUMER = "writeback_says_nothing"
CORE_SKILLS_DIR = RUNTIME_SRC / "skills"
CORE_SKILL_NAMES = frozenset({"sandbox", "create-application", "ufo-style"})
SKILL_MANIFEST = "SKILL.md"
RECURSIVE_COPY = re.compile(r"\bcp\s+(?:-\w+\s+)*-\w*[rR]")
SKILL_COPY_MODE = "--no-preserve=mode"
HOUSE_STYLE_TOKENS = CORE_SKILLS_DIR / "ufo-style/references/tokens.css"
DESIGN_SKILLS = Path("extensions/documents/ufo_ext_documents/skills")
SITE_SKILL_SHARED = Path("extensions/sites/ufo_ext_sites/skills/website-building/shared")
DESIGN_PALETTE = DESIGN_SKILLS / "design-foundations/references/color.md"
PALETTE_RESTATEMENTS = (
    HOUSE_STYLE_TOKENS,
    DESIGN_SKILLS / "design-foundations/references/dataviz.md",
    DESIGN_SKILLS / "office-pptx" / SKILL_MANIFEST,
    DESIGN_SKILLS / "pdf/libraries/reportlab.md",
    SITE_SKILL_SHARED / "01-design-tokens.md",
    SITE_SKILL_SHARED / "06-css-and-tailwind.md",
    Path("extensions/repl/ufo_ext_repl/skills/data-visualization") / SKILL_MANIFEST,
)
HEX = r"#[0-9A-Fa-f]{6}"
PALETTE_DECLARATION = re.compile(
    rf"--((?:bkgd-[123]00)|(?:text|accent)-(?:primary|secondary|tertiary)):\s*"
    rf"(?:light-dark\(({HEX}),\s*({HEX})\)|({HEX}));"
)
PALETTE_ROW = re.compile(rf"^\|\s*`--([\w-]+)`\s*\|\s*`({HEX})`\s*\|\s*(?:`({HEX})`\s*\|)?", re.M)
PAIRING_ROW = re.compile(
    rf"^\|[^|]+\|\s*(body|18px\+)\s*\|\s*`({HEX})`\s*\|\s*`({HEX})`\s*\|\s*([\d.]+):1\s*\|$", re.M
)
FILL_LABEL_ROW = re.compile(rf"^\|\s*`({HEX})`\s*\|\s*([\d.]+):1\s*\|$", re.M)
AA_RATIOS = {"body": 4.5, "18px+": 3.0}
PRINTED_RATIO_TOLERANCE = 0.05
MIGRATION_DIR_PART = "migrations"
CORE_OWNER = "core"
STAMP_REVISION = re.compile(r"\d{14}")
HAND_NUMBERED_REVISION = re.compile(r"\d{4}")
LAST_HAND_NUMBERED_REVISION = "0113"
GRANDFATHERED_CORE_REVISIONS = frozenset({"knowledge_graph_0001", "sweep_0001", "sweep_0002"})
NAME_SEPARATOR = "-"
CANDIDATES_FIELD = "candidates"
ENV_ROOTS = Path("infra/envs")
SHARED_SINGLETON_RESOURCES = (
    "datadog_dashboard",
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


def _built(path: Path) -> bool:
    """A tree a build wrote — a provider cache, a static site's output, its generated types. Every
    one of them is gitignored, so nothing a reader could edit lives under it."""
    return bool({".terraform", "dist", ".astro"}.intersection(path.parts))


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
    """A raw blob backend reads and writes every key in the bucket, so shipped code holds one only
    at a boot boundary that immediately decides its scope — everywhere else the store arrives
    already wrapped as `WorkspaceBlobStore`/`FleetBlobStore`, and the prefix cannot be misspelled.
    Tests construct backends freely: they are the fixture under the wrappers."""
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
    """`Admission` is the turn gate every surface admits through, and a process that builds two of
    them can take a fix on the copy nobody serves. Shipped code constructs it in one place,
    `serve._admission`, so the gate the member surface holds is the gate that was changed. Tests and
    the eval harness build their own: they are the fixture under the gate, not a surface a member
    reaches."""
    failures = []
    for rel, tree in trees.items():
        if "tests" in rel.parts or rel.parts[0] == ADMISSION_HARNESS_ROOT:
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
            elif not any(word.arg == "key_slot_for" for word in call.keywords):
                failures.append(
                    f"{rel}: {ADMISSION_FACTORY_NAME} drops key_slot_for — the balance gate's "
                    f"own-key exemption needs the model an agent runs"
                )
        if rel == ADMISSION_FACTORY and not any(id(call) in inside for call in built):
            failures.append(
                f"{rel}: {ADMISSION_FACTORY_NAME} no longer builds Admission — shipped code has no "
                f"single construction path for the turn gate"
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


LAYERED_BOOT_MODULES = frozenset({HARNESS_SRC / "sandbox" / "ingress_serve.py"})


def _layering_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """host → runtime → harness is one-way: the runtime and the harness never import `ufo.host` —
    the host layer's discovered contributions reach them as injected values (`Runtime.manifests`,
    `Runtime.environment`) bound at a composition root. The named boot modules are process entry
    points that compose the layers themselves."""
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
    """The engine core is product-neutral: stdlib plus other engine-core modules, so an outside
    host can run it without the tenant runtime, extensions, HTTP, database, or durable executor.
    The rest of ufo.harness (model providers, sandbox carriers, durability, o11y) is execution
    machinery with real dependencies; only the engine core carries the purity property."""
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
        and not any(path.is_relative_to(root) for root in CORE_TEST_ROOTS)
    ]


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
                    and keyword.arg not in ("name", "version", "member_context_read")
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
    """Every durable surface drops the silence sentinel. A turn whose whole answer says nothing is
    delivered by sending nothing, and any prompt can ask for that answer on any surface, so a
    `post` whose extension never reads `writeback_says_nothing` would send `<response></response>`
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
    """Schedule member paths and their write primitive accept no agent selector. A missing module is
    a failure, not a pass: the store this reads is free to move, and a stale path would leave the
    gate reading nothing while reporting green. A named method that no longer exists is that same
    silence one rename later, so the set is resolved against the class as well as read off it."""
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
    """Every LiveFrame kind has a handler in every consumer of the hub stream — the mirror of
    `_live_frame_failures`' emitter check. A consumer that deliberately ignores a kind names it in
    FRAME_EXEMPTIONS, so absence is always a decision; an exemption for a kind the consumer handles
    is stale and fails too. The SSE event names each projection emits must equal the names its one
    frontend listens for, so a kind cannot reach a browser as an event nothing subscribes to."""
    failures: list[str] = []
    kinds = set(_live_frame_kinds(trees))
    if not kinds:
        return ["hub: LiveFrame union not found in core/src/ufo/runtime/hub.py"]

    web = trees.get(WEB_SURFACE_MODULE)
    debugger = trees.get(DEBUGGER_SURFACE_MODULE)
    ufo_surface = trees.get(UFO_SURFACE_MODULE)
    redis = trees.get(REDIS_HUB_MODULE)
    record = trees.get(RECORD_MODULE)
    consumers: list[tuple[str, ast.AST | None]] = [
        ("record frame_event", _function_scope(record, "frame_event") if record else None),
        ("debugger _sse", _function_scope(debugger, "_sse") if debugger else None),
        (
            "ufo directives_for",
            _function_scope(ufo_surface, "directives_for") if ufo_surface else None,
        ),
        ("record fold", _function_scope(record, "fold") if record else None),
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

    if web is not None and debugger is not None and record is not None:
        failures.extend(_sse_listener_failures(web, debugger, record))
    return failures


def _bytes_event_names(scope: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes):
            names.update(
                match.group(1).decode() for match in re.finditer(rb"event: (\w+)", node.value)
            )
    return names


def _returned_strings(scope: ast.AST) -> set[str]:
    return {
        node.value.value
        for node in ast.walk(scope)
        if isinstance(node, ast.Return)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }


def _sse_listener_failures(web: ast.Module, debugger: ast.Module, record: ast.Module) -> list[str]:
    """The web stream's event names are the frame names `frame_event` answers plus the events the
    surface synthesizes itself, and the portal's `EVENT_KINDS` must spell exactly that set."""
    failures: list[str] = []
    naming = _function_scope(record, "frame_event")
    web_events = _returned_strings(naming) if naming else set()
    web_events |= {
        node.args[0].value
        for node in ast.walk(web)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_event"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    record_source = _required_text(TURN_RECORD_MODULE, failures)
    if record_source is not None:
        declared = _declared_event_kinds(record_source)
        expected = web_events | {"message"}
        failures.extend(
            f"sse: turnRecord.ts does not declare event {name!r}"
            for name in sorted(expected - declared)
        )
        failures.extend(
            f"sse: turnRecord.ts declares event {name!r} that the web surface never sends"
            for name in sorted(declared - expected)
        )

    debugger_sse = _function_scope(debugger, "_sse")
    debugger_kinds: set[str] = set()
    if debugger_sse is not None:
        debugger_kinds = {
            node.value.decode()
            for node in ast.walk(debugger_sse)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, bytes)
            and re.fullmatch(rb"[a-z_]+", node.value)
        }
    tail_source = _required_text(DEBUGGER_TAIL_MODULE, failures)
    if tail_source is not None:
        tail_kinds = _declared_event_kinds(tail_source)
        failures.extend(
            f"sse: debugger Tail.tsx does not listen for event {name!r}"
            for name in sorted(debugger_kinds - tail_kinds)
        )
        failures.extend(
            f"sse: debugger Tail.tsx listens for event {name!r} that its surface never sends"
            for name in sorted(tail_kinds - debugger_kinds)
        )
    return failures


def _declared_event_kinds(source: str) -> set[str]:
    """The SSE event names a frontend module declares in its `EVENT_KINDS` array — the one list
    its listeners and its decoder are both spelled from."""
    array = re.search(r"EVENT_KINDS = \[(.*?)\]", source, re.S)
    return set(re.findall(r'"(\w+)"', array.group(1))) if array else set()


def _silence_sentinel_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The silence sentinel is one string in two languages: the engine's surfaces read it off a
    terminal frame to post nothing, and the portal reads it off the same frame to draw no row. A
    sentinel spelled differently on either side draws the sentinel to the member as text."""
    failures: list[str] = []
    core = trees.get(CORE_SURFACE_MODULE)
    if core is None:
        return [f"silence: {CORE_SURFACE_MODULE} not found"]
    spelled = {
        node.value.value
        for node in ast.walk(core)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == SILENCE_SENTINEL_NAME for t in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    if len(spelled) != 1:
        return [f"silence: {CORE_SURFACE_MODULE} does not assign {SILENCE_SENTINEL_NAME} a string"]
    sentinel = spelled.pop()
    source = _required_text(TURN_RECORD_MODULE, failures)
    if source is None:
        return failures
    written = re.findall(rf'{SILENCE_SENTINEL_NAME} = "([^"]*)"', source)
    if written != [sentinel]:
        failures.append(
            f"silence: {TURN_RECORD_MODULE} spells {SILENCE_SENTINEL_NAME} {written!r}, "
            f"and core reads {sentinel!r}"
        )
    return failures


def _consent_mark_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The consent window's mark is one string in two languages: the portal writes it into session
    storage before it opens a consent window, and the return page core serves reads it back to know
    it may close that window. A key spelled differently on either side reads as no mark at all —
    every consent window would then be carried to a connectors screen inside a 520-pixel popup —
    and nothing else in the tree would notice, since neither language ever sees the other's copy."""
    failures: list[str] = []
    page = trees.get(CALLBACK_PAGE_MODULE)
    if page is None:
        return [f"consent: {CALLBACK_PAGE_MODULE} not found"]
    spelled = {
        node.value.value
        for node in ast.walk(page)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == CONSENT_MARK_NAME for t in node.targets)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    if len(spelled) != 1:
        return [f"consent: {CALLBACK_PAGE_MODULE} does not assign {CONSENT_MARK_NAME} a string"]
    mark = spelled.pop()
    for rel in CONSENT_MODULES:
        source = _required_text(rel, failures)
        if source is None:
            continue
        written = re.findall(rf'{CONSENT_MARK_NAME} = "([^"]*)"', source)
        if written != [mark]:
            failures.append(
                f"consent: {rel} spells {CONSENT_MARK_NAME} {written!r}, "
                f"and the return page reads {mark!r}"
            )
        if f"sessionStorage.setItem({CONSENT_MARK_NAME}" not in source:
            failures.append(f"consent: {rel} never writes {CONSENT_MARK_NAME} to session storage")
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


def _stripped_dump(tree: ast.Module, function: str) -> str | None:
    scope = _function_scope(tree, function)
    if scope is None or not isinstance(scope, ast.FunctionDef):
        return None
    body = scope.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[]))


def _drawn_mark_failures() -> list[str]:
    """A logo is drawn artwork, so a package that serves one copies the file rather than rewriting
    it: hand-minifying this mark drew it wrongly, because the `fill-rule: evenodd` its `<defs>`
    stylesheet carries is what cuts the counters in the letters. Nothing about a rendered glyph
    fails a size assertion, so the bytes are compared — which is also what keeps the copies from
    drifting apart as the brand changes."""
    copies = {
        Path("extensions/web/frontend/src/assets/ufo-logo.svg"): (
            Path("servers/control/src/assets/ufo-logo.svg"),
            Path("core/src/ufo/runtime/surfaces/assets/ufo-logo.svg"),
        ),
        Path("assets/brand/ufo-mark.svg"): (Path("servers/control/src/assets/ufo-mark.svg"),),
    }
    failures: list[str] = []
    for drawn, served in copies.items():
        if not drawn.exists():
            failures.append(f"mark: {drawn} is missing — every served copy is taken from it")
            continue
        failures.extend(
            f"mark: {copy} is not {drawn} byte for byte — a logo is copied, never rewritten"
            for copy in served
            if not copy.exists() or copy.read_bytes() != drawn.read_bytes()
        )
    return failures


def _directive_wire_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """The two directive wires stay closed vocabularies with both ends held: every verb a producer
    emits is in its wire's table, every table verb is emitted, and every client of a wire handles
    its whole vocabulary minus the drops it declares. The two `directive()` codecs — the ufo
    surface's and the gateway's, one per package — must stay byte-for-byte the same shape, since a
    field escaped by one and not the other splits the line framing every client parses. The verb
    tables live in `ufo_testsupport.wire_fixture`, the same home the golden fixture every client
    replays is generated from."""
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

    gateway_emitted: set[str] = set()
    found_gateway = False
    for rel in GATEWAY_MODULES:
        text = _required_text(rel, failures)
        if text is not None:
            found_gateway = True
            gateway_emitted |= set(RUST_DIRECTIVE_CALL.findall(text))
    if found_gateway:
        failures.extend(
            f"wire: verb {verb!r} emitted by the gateway but not in ONBOARD_WIRE"
            for verb in sorted(gateway_emitted - ONBOARD_WIRE)
        )
        failures.extend(
            f"wire: ONBOARD_WIRE verb {verb!r} is emitted nowhere in the gateway"
            for verb in sorted(ONBOARD_WIRE - gateway_emitted)
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

    web_source = _required_text(ONBOARD_WEB_MODULE, failures)
    if web_source is not None:
        js_verbs = set(re.findall(r"directive\.verb === '([a-z]+)'", web_source))
        expected = ONBOARD_WIRE - ONBOARD_WEB_DROPPED_VERBS
        failures.extend(
            f"wire: the onboarding page does not handle verb {verb!r}"
            for verb in sorted(expected - js_verbs)
        )
        failures.extend(
            f"wire: the onboarding page handles verb {verb!r} that the gateway never emits"
            for verb in sorted(js_verbs - expected)
        )
    return failures


def _canonical_stream_failures() -> list[str]:
    """A connector's canonical streams are the whole set that syncs when a member connects the
    account, so a connector marking none syncs nothing and the account is connected for nothing.
    `StreamSpec.canonical` defaults to False — a stream is content only where it says so — which
    makes "nobody marked one" a silent empty feed rather than a loud one. This is the loud half.

    It also refuses a stream a connector declares but `streams` filters out. Such a spec can never
    produce a source row, so a flag on it is unreachable and a reader counting declarations counts
    streams that do not exist — a declaration with no producer, which is the same silence in the
    other direction.

    It reads the constructed specs out of the registry, the same import `connected.py` makes to
    decide what to register, so the gate cannot disagree with what ships. Reading the source text
    instead cannot answer it: a provider may mark a stream canonical at the call site, or through
    the default of a helper it defines itself, and a text scan sees only the first — it would fail
    the providers that use the second and pass one whose streams are all built through a helper it
    does not recognise, which is the exact silence this gate exists to break."""
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
    """A connector with no fixed `base_url` reads a per-tenant host off its connection, and the only
    thing standing between an admin and a feed that sends the workspace credential to a host they
    control is the provider's row in `TENANT_URL_RULES`. The two sets are one set: a per-tenant
    connector with no rule can never take a URL, and a rule for a fixed-host connector admits a
    host the connector never dials. Either is a table that has drifted from the connectors it
    guards.

    A connector that dials no host at all (`dials_host` False — it reads through broker tool
    executions) is in neither set: its empty `base_url` is its whole address, so a rule for it
    would admit a host nothing ever dials."""
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


LOG_EMITTERS = frozenset({"log", "warn", "log_error"})
RESERVED_LOG_FIELDS = frozenset({"status"})


def _log_field_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """`status` on a log record is Datadog's reserved level attribute: its remapper re-levels the
    record from the value, so a turn's `failed` filed as emergency, `done` as debug, and no failure
    ever answered `status:error`. The emitter alone sets a record's level; the field takes a name
    that says what it is (`turn_status`, `http_status`)."""
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
    """A skill ships with the thing it teaches, and core teaches its own builtins plus the one
    thing no extension owns, so core ships exactly three — the sandbox its tools run in, the
    `agent` object a member asks for in chat, and the house style every pack's skills default to,
    which lives here because core is the only tier they can all reach. This gate is scoped to
    `core/skills` alone: a SKILL.md folder there outside that set is an extension or a pack living
    in the wrong tree, a missing one is a broken floor. Packs contribute any number of their own
    skills elsewhere, held only to the skill boundary gate."""
    root = ROOT / CORE_SKILLS_DIR
    if not root.is_dir():
        return [f"skills: core skills directory missing at {CORE_SKILLS_DIR}"]
    present = frozenset(path.parent.name for path in root.rglob(SKILL_MANIFEST))
    return _rogue_skill_failures(present)


def _skill_copy_mode_failures() -> list[str]:
    """A skill that tells the agent to copy one of its own directories must reset the mode as it
    copies. The sandbox image publishes the skills tree read-only (`chmod -R a-w` over
    `SYSTEM_SKILLS_ROOT` in `sandbox/build_template.py`), and `cp -r` carries a directory's mode
    onto the copy, so a plain recursive copy hands the agent a project it cannot write into: the
    install fails, the edits fail, and the shell reports a permission error naming files rather
    than the directory that caused it. Copying a file needs nothing — the agent's own directory
    holds the new one, and the tools that rewrite it replace rather than open for append.

    Only fenced blocks are read. Prose naming the flag is how a skill explains the rule, and a gate
    that failed on the explanation would push every skill into writing about the copy without
    showing it."""
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


def _stamped_or_grandfathered(revision: str) -> bool:
    """A core revision id the stamp rule accepts: a UTC stamp, or one of the ids that predate the
    rule — the hand-numbered chain, which ends at its last number, plus the one labelled branch
    base."""
    return (
        STAMP_REVISION.fullmatch(revision) is not None
        or revision in GRANDFATHERED_CORE_REVISIONS
        or (
            HAND_NUMBERED_REVISION.fullmatch(revision) is not None
            and revision <= LAST_HAND_NUMBERED_REVISION
        )
    )


def _migration_antijoin_failures(trees: dict[Path, ast.Module]) -> list[str]:
    """`col.not_in(select(...))` inside a migration. Postgres converts no `NOT IN (subquery)` into
    an anti-join — the planner limitation holds even where both sides are NOT NULL — so it
    materializes the subquery and rescans it per row. `memory_0017` spelled the same set that way
    and cost 7.2 billion against 456k rows against 489k, ran 51 minutes without finishing, and took
    two production deploys down with it on 2026-09-08. `~sa.exists(...)` is a hash anti-join."""
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
    """The migration seam's single-head discipline, generalized across owners so it composes with
    optional table-owning extensions. Each owner (core, each extension) has one base and one head,
    chaining only within itself — an extension never chains onto core or a sibling via down_revision
    (that would fork core or dangle when the sibling is not pinned), it attaches by declaring
    depends_on a core revision so `upgrade heads` applies core's shared tables first. So the DAG is
    deterministic and core-first, with no orphan reference.

    A new core revision id is a UTC stamp, never the next number: two branches pick the same number
    and one of them is renumbered to land, while two stamps never collide. The ids already merged
    keep working — they are the graph a live database is stamped with — so the rule reaches only
    ids the hand-numbered chain does not already hold."""
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
    """Every registered provider name a manifest declares, paired with the point it came from —
    reading each spec type's own identifier field (cdp, search, and flag carry it as `backend`,
    carrier, embed, and index as `name`)."""
    return [
        *(("cdp_providers", spec.backend) for spec in manifest.cdp_providers),
        *(("carriers", spec.name) for spec in manifest.carriers),
        *(("search_providers", spec.backend) for spec in manifest.search_providers),
        *(("flag_providers", spec.backend) for spec in manifest.flag_providers),
        *(("embeds", spec.name) for spec in manifest.embeds),
        *(("indexes", spec.name) for spec in manifest.indexes),
    ]


def _naming_failures(manifests: tuple[Manifest, ...], packs: tuple[Pack, ...]) -> list[str]:
    """Every name a subsystem selects a component by uses `_`, never `-`: an extension name, a pack
    name, and each provider name across the cdp/carrier/search/flag/embed/index points. A `-`
    fractures the selector — a config `cdp_provider = "sandbox_chrome"` would never match a provider
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
    from ufo.host.ext.loader import discovered, discovered_packs

    manifests = tuple(manifest for manifest, _ in discovered().values())
    packs = tuple(discovered_packs().values())
    return _naming_failures(manifests, packs)


def _retired_resource_failures() -> list[str]:
    """A retired resource stays retired.

    `infra/retired_resources.json` is what lets the deploy destroy a database or a queue, which the
    plan guard refuses to every other address. Left at that, the record would permit a deletion
    nobody meant the day somebody declared the name again — and the resource terraform then created
    would be a new, empty one wearing a retired resource's address. So the name is spent: no root
    may declare a resource whose type and name a retirement holds."""
    retired = json.loads(RETIRED_RESOURCES.read_text())
    spent = {tuple(address.split(".")[-2:]) for address in retired}
    failures = []
    for path in sorted(INFRA_ROOT.rglob("*.tf")):
        source = path.read_text()
        for kind, name in sorted(spent):
            if re.search(rf'^resource\s+"{re.escape(kind)}"\s+"{re.escape(name)}"', source, re.M):
                failures.append(
                    f"{path.relative_to(ROOT)} declares {kind}.{name}, which a retirement spent"
                )
    return failures


def _declared_flag_failures(terraform: dict[Path, str]) -> list[str]:
    """Every flag the code reads is a flag each environment's terraform declares, and the reverse.

    A key the portal reads and an environment does not declare never reaches its flag service, so it
    answers its call-site default in every workspace forever — a feature nobody can turn on, or one
    nobody can turn off, and neither states which. A key declared and never read is worse: an
    operator sets it, the dashboard says the feature moved, and no code ever asked.

    The two lists are in different languages, so nothing but this holds them together. The pack is
    read from each environment root's own serve config, and the flags from the edge root, which is
    where every Cloudflare resource for both environments lives. A pack's declarations are its
    extensions' plus core's own, because core reads a flag of its own beside the ones extensions
    read."""
    from ufo.host.ext.loader import load_manifests
    from ufo.runtime.context_boundary import CORE_FLAGS

    packs = {
        path.parent.name: found.group(1)
        for path, source in terraform.items()
        if path.name == "ufo.tf"
        and (found := re.search(r'\[pack\]\s*\n\s*name = "([a-z0-9_]+)"', source))
    }
    flags_source = next(
        (source for path, source in terraform.items() if path.name == "flags.tf"), None
    )
    if flags_source is None:
        return ["flags: no env root declares flags.tf"]
    tombstones = frozenset(json.loads(FLAG_TOMBSTONES.read_text()))
    failures = []
    for environment, pack in sorted(packs.items()):
        declared = {spec.key for manifest in load_manifests(pack) for spec in manifest.flags}
        if not declared:
            continue
        declared |= {spec.key for spec in CORE_FLAGS}
        block = re.search(rf"{environment}\s*=\s*\{{(.*?)\n    \}}", flags_source, re.DOTALL)
        if block is None:
            failures.append(f"flags: no portal_flags map for {environment}")
            continue
        keys = set(re.findall(r'"([a-z0-9-]+)"\s*=\s*(?:true|false)', block.group(1)))
        for key in sorted((keys | declared) & tombstones):
            failures.append(f"flags: {environment} uses tombstoned key {key!r}")
        for key in sorted(keys - declared):
            failures.append(
                f"flags: {environment} declares {key!r}, which no extension in {pack} reads"
            )
        for key in sorted(declared - keys):
            failures.append(
                f"flags: {environment} omits {key!r}, which an extension in {pack} reads"
            )
    return failures


def _census_period_failures(sources: dict[Path, str]) -> list[str]:
    """The product board reads every workspace count out of one rollup bucket as wide as the census
    period, so terraform holding a different number than the census fires on counts a workspace once
    per tick the bucket covers — silently, and in the direction that looks like growth rather than
    like a bug. Nothing but this holds the two together: one is a cron in Python, the other a rollup
    width in HCL."""
    from ufo.product import PRODUCT_CENSUS_SECONDS

    declared = {
        path.parent.name: int(found.group(1))
        for path, source in sources.items()
        if (found := re.search(r"product_census_seconds\s*=\s*(\d+)", source))
    }
    if not declared:
        return ["product board: no env root declares product_census_seconds"]
    return [
        f"product board: {root} buckets workspace counts at {seconds}s, "
        f"but the census fires every {PRODUCT_CENSUS_SECONDS}s"
        for root, seconds in sorted(declared.items())
        if seconds != PRODUCT_CENSUS_SECONDS
    ]


def _portal_style_failures() -> list[str]:
    """The portal's look lives in the Tailwind theme and the component set. A view that imports a
    stylesheet or emits a `<style>` tag can restyle a sibling it never named, which is how a rule
    written for one panel silently reshaped another. An inline `style` object is a computed value,
    not a selector, and stays allowed. The portal's own markup entry is checked too: a `<style>`
    there is the same escape by a different door. A bracket value in a class that names a raw
    measurement or colour re-decides a token inline — it must resolve through the theme
    (`var(--…)`) or stay structural. The refused class shapes are the ones with a shorter spelling
    that already means the same thing, so the long form is drift rather than intent. The extension
    app pages compile in the browser against this same theme, so they are held to the same rules —
    and a page glob that matches nothing is itself a failure, not a pass."""
    source = ROOT / PORTAL_SOURCE
    if not source.is_dir():
        return [f"{PORTAL_SOURCE}: the portal source is missing"]
    failures = []
    for path in sorted(source.rglob("*")):
        rel = path.relative_to(ROOT)
        if BLOCKS_SOURCE in rel.parents:
            continue
        if path.suffix == ".css" and rel != PORTAL_THEME:
            failures.append(f"{rel}: the theme is the only stylesheet")
        if path.suffix not in PORTAL_MODULE_SUFFIXES:
            continue
        text = path.read_text()
        if "<style" in text:
            failures.append(f"{rel}: a view may not emit a <style> tag")
        if STYLESHEET_IMPORT.search(text) and rel not in PORTAL_ENTRIES:
            failures.append(f"{rel}: only the entry module imports the theme")
        failures.extend(
            f"{rel}: {segment.group(0)} names a raw value — resolve it through a theme token"
            for pattern in (ARBITRARY_VALUE, ARBITRARY_PROPERTY)
            for segment in pattern.finditer(text)
            if RAW_CSS_VALUE.search(segment.group(1))
        )
        failures.extend(
            f"{rel}: {found.group(0)!r} — {refusal}"
            for pattern, refusal in PORTAL_CLASS_REFUSALS
            for found in pattern.finditer(text)
        )
    pages = sorted(ROOT.glob(APP_PAGE_GLOB))
    if not pages:
        failures.append(f"{APP_PAGE_GLOB}: no app pages found — the gate lost its subjects")
    for path in pages:
        rel = path.relative_to(ROOT)
        text = path.read_text()
        if "<style" in text:
            failures.append(f"{rel}: a view may not emit a <style> tag")
        if STYLESHEET_IMPORT.search(text):
            failures.append(
                f"{rel}: an app page imports no stylesheet — the kit's theme is the sheet"
            )
        failures.extend(
            f"{rel}: {segment.group(0)} names a raw value — resolve it through a theme token"
            for pattern in (ARBITRARY_VALUE, ARBITRARY_PROPERTY)
            for segment in pattern.finditer(text)
            if RAW_CSS_VALUE.search(segment.group(1))
        )
        failures.extend(
            f"{rel}: {found.group(0)!r} — {refusal}"
            for pattern, refusal in PORTAL_CLASS_REFUSALS
            for found in pattern.finditer(text)
        )
    markup = ROOT / PORTAL_SOURCE.parent / "index.html"
    if not markup.is_file():
        return [*failures, f"{markup.relative_to(ROOT)}: the portal entry markup is missing"]
    if "<style" in markup.read_text():
        failures.append(f"{markup.relative_to(ROOT)}: a view may not emit a <style> tag")
    return failures


def _app_slug_failures(
    shipped: frozenset[str],
    homes: frozenset[str],
    built: frozenset[str],
    entries: frozenset[str],
    typechecked: frozenset[str],
) -> list[str]:
    """The decision an app's five spellings of its slug have to agree on.

    `shipped` is the app extensions by directory name, `homes` the home skills they ship, `built`
    the slugs the app bundle names, `entries` the slugs it has an entry document for, and
    `typechecked` the pages the app typecheck reads. A slug that disagrees between any two fails
    silently and late: the deploy builds a bundle missing the page, the extension installs fine,
    and a member opens the app to a blank frame.

    The typecheck is a list of files rather than a glob, so a page left off it is built and shipped
    having never been checked — the bundle transpiles without types, and a prop the kit does not
    declare is dropped in silence."""
    failures = []
    slugs = set()
    for name in sorted(shipped):
        named = APP_EXTENSION.match(name)
        if named is None:
            failures.append(
                f"extensions/{name}: an app extension is `app_<slug>`, lowercase letters and "
                "digits — the homepage read keys an agent by that slug"
            )
            continue
        slug = named.group("slug")
        slugs.add(slug)
        home = APP_HOME_SKILL.format(slug=slug)
        if home not in homes:
            failures.append(
                f"extensions/{name}: ships no skill {home!r} — an app's page is the skill named "
                "for its own slug, and a mismatch drops the page out of the deploy bundle"
            )
        if slug not in built:
            failures.append(
                f"{APPS_CONFIG}: does not build {slug!r} — an app extension the bundle does not "
                "name installs fine and opens to a blank frame"
            )
        elif slug not in entries:
            failures.append(f"{_app_entry(slug)}: the app bundle's entry for {slug!r} is missing")
        if slug not in typechecked:
            failures.append(
                f"{APPS_TYPECHECK}: does not read the {slug!r} page — a page left off this list "
                "ships having never been typechecked, and the build transpiles without types"
            )
    failures.extend(
        f"{APPS_CONFIG}: builds {slug!r}, which no `app_{slug}` extension ships"
        for slug in sorted(built - slugs)
    )
    return failures


def _app_rebuild_failures() -> list[str]:
    """Every app's home skill says what a rebuild takes.

    A member's page is built against the kit of the day it was built, and a redeploy takes the kit
    as it stands then — so a rebuild is how a page gains what the kit has since gained, and a page
    nobody rebuilds silently keeps the old one. The skill is the only place an agent reads that,
    because the agent doing the rebuild is following the skill and nothing else.

    Home skills alone. An app ships other skills — a feature's own procedure — and none of them
    builds a page, so asking them what a rebuild takes would demand a line about a page they never
    touch."""
    home = APP_HOME_SKILL.format(slug="*")
    skills = sorted(
        ROOT.glob(f"extensions/app_*/ufo_ext_app_*/skills/{home}/{APP_HOME_SKILL_FILE}")
    )
    if not skills:
        return ["extensions/app_*: no app home skills found — the gate lost its subjects"]
    return [
        f"{path.relative_to(ROOT)}: does not say a rebuild {APP_REBUILD_LINE!r} — an agent "
        "following this skill would rebuild a page onto the kit it was first built against"
        for path in skills
        if APP_REBUILD_LINE not in path.read_text()
    ]


def _app_entry(slug: str) -> Path:
    return PORTAL_SOURCE.parent / "apps" / slug / "index.html"


def _app_bundle_failures() -> list[str]:
    """Every app's slug, read off the tree and off the bundle's own list, and handed to the one
    decision above."""
    extensions = ROOT / "extensions"
    shipped = frozenset(
        path.name for path in extensions.iterdir() if path.is_dir() and path.name.startswith("app_")
    )
    if not shipped:
        return ["extensions/app_*: no app extensions found — the gate lost its subjects"]
    config = ROOT / APPS_CONFIG
    if not config.is_file():
        return [f"{APPS_CONFIG}: the app bundle's config is missing"]
    stated = APPS_LIST.search(config.read_text())
    if stated is None:
        return [f"{APPS_CONFIG}: the bundle names no apps — the gate lost its list"]
    homes = frozenset(
        skill.name
        for path in extensions.iterdir()
        if path.is_dir() and path.name.startswith("app_")
        for skills in path.rglob("skills")
        if skills.is_dir()
        for skill in skills.iterdir()
        if skill.is_dir()
    )
    built = frozenset(
        word.strip().strip("\"'") for word in stated.group("apps").split(",") if word.strip()
    )
    entries = frozenset(slug for slug in built if (ROOT / _app_entry(slug)).is_file())
    typecheck = ROOT / APPS_TYPECHECK
    if not typecheck.is_file():
        return [f"{APPS_TYPECHECK}: the app pages' typecheck config is missing"]
    read = typecheck.read_text()
    typechecked = frozenset(slug for slug in built if f"app-{slug}-home/app.tsx" in read)
    return _app_slug_failures(shipped, homes, built, entries, typechecked)


COMMENT_CEILING = 2
COMMENT_GATED_ROOTS = (
    ".github",
    "client",
    "core",
    "dev",
    "extensions",
    "infra",
    "packs",
    "sandbox",
    "scripts",
    "servers",
    "testsupport",
)
COMMENT_GATED_SUFFIXES = (
    ".css",
    ".py",
    ".rs",
    ".sh",
    ".tf",
    ".toml",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
)
COMMENT_ALIGNMENT_FILES = (
    Path("infra/modules/platform/iam.tf"),
    Path("infra/modules/platform/ses.tf"),
)
COMMENT_GENERATED = re.compile(r"_pb2\.py$|/page/kit/[^/]+-[A-Za-z0-9_]{8,}\.js$|\.tsbuildinfo$")
COMMENT_KEPT = re.compile(
    r"SPDX|Copyright|Licensed under|@license|@preserve|noqa|type:\s*ignore|mypy:|pyright|ruff:"
    r"|flake8|pylint|eslint|@ts-|prettier-ignore|biome-ignore|(?:c8|istanbul|v8)\s+ignore"
    r"|@vitest-environment|@jsxImportSource|sourceMappingURL|vite-ignore|webpackIgnore"
    r"|\#region|\#endregion|rustfmt::skip|clippy::|shellcheck|tflint|checkov|yamllint|hadolint"
    r"|@charset|-\*-\s*coding",
    re.I,
)
HASH_COMMENT = ("#",)
SLASH_COMMENT = ("//", "/*", "{/*")
COMMENT_OPENERS = {
    ".css": ("/*", "{/*"),
    ".py": HASH_COMMENT,
    ".rs": SLASH_COMMENT,
    ".sh": HASH_COMMENT,
    ".tf": (*HASH_COMMENT, "//"),
    ".toml": HASH_COMMENT,
    ".ts": SLASH_COMMENT,
    ".tsx": SLASH_COMMENT,
    ".yaml": HASH_COMMENT,
    ".yml": HASH_COMMENT,
}
PUBLIC_DECLARATION = re.compile(r"^(?:export\b|pub\b|pub\(crate\)|#\[|@)")


def _python_comment_lines(text: str) -> frozenset[int]:
    """The lines Python's own tokenizer calls a whole-line comment, so a `#` inside a string
    literal is a string — a test that holds a narrated module as a fixture would otherwise read as
    a narrated module. A file that does not parse contributes nothing."""
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
            if body.startswith(("/*", "{/*")) and "*/" not in line:
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
    """A docstring on a public API, which the comment rule allows at any length: `///` or `//!` in
    Rust, `/** */` in TypeScript, standing immediately over the declaration it documents. `cargo
    doc` and the kit catalogue publish these to a reader who never opens the file, so they are
    documentation with an audience rather than commentary beside code. A doc comment floating over
    nothing, and one over a private item, is commentary and takes the ceiling."""
    opener = run[0].strip()
    if opener.startswith("//!"):
        return True
    if not opener.startswith(("///", "/**")):
        return False
    return bool(PUBLIC_DECLARATION.match(after.strip()))


def _overlong_comments(rel: Path, text: str) -> list[str]:
    """A comment block past the ceiling. A docstring on a public API is exempt at any length, and so
    is a licence or a tool pragma — deleting either breaks a build rather than a paragraph."""
    lines = text.split("\n")
    failures = []
    for start, run in _comment_runs(text, rel.suffix):
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
    """Two lines is the ceiling for a comment, because a paragraph is what a reader skips. The
    exempt trees are the ones whose comments are input rather than commentary: a `skills/` folder is
    text a model reads and ships only ablated, `evals/` holds fixtures pinned by digest and arms
    that pin the bytes they replace, and the platform authorization files hand the deploy gate an
    `=` column that a deleted comment realigns into a false grant contraction."""
    trees = (path for root in COMMENT_GATED_ROOTS for path in (ROOT / root).rglob("*"))
    failures = []
    for path in sorted({*trees, *ROOT.glob("*")}):
        if path.suffix not in COMMENT_GATED_SUFFIXES or not path.is_file():
            continue
        if _vendored(path) or _is_skill_content(path) or _built(path):
            continue
        rel = path.relative_to(ROOT)
        if rel in COMMENT_ALIGNMENT_FILES or COMMENT_GENERATED.search(str(rel)):
            continue
        failures.extend(_overlong_comments(rel, path.read_text()))
    return failures


def _kit_catalogue_failures() -> list[str]:
    """The catalogue is what an agent reads before it composes an app page — the one place the kit
    describes itself. It is generated from `kit.ts`, and it is committed because the agent reads it
    while it edits, long before anything is deployed. A generated file under version control goes
    stale in silence, so this reads the kit's own export list against it: a component the kit
    publishes and the catalogue never names is a component no page will be built out of.

    It compares names and not bytes. The description of a component is the first sentence of its
    docstring, which a reviewer may reword without regenerating anything; the surface is the thing
    that must not drift."""
    kit = ROOT / PORTAL_SOURCE / "apps" / "kit.ts"
    catalogue = ROOT / KIT_CATALOGUE
    if not kit.is_file() or not catalogue.is_file():
        return [f"{KIT_CATALOGUE}: the kit or its catalogue is missing"]
    if not (ROOT / KIT_CATALOGUE_SCRIPT).is_file():
        return [f"{KIT_CATALOGUE_SCRIPT}: the catalogue's generator is missing"]
    source = kit.read_text()
    exported = set(KIT_EXPORT.findall(source[source.index("export {") :]))
    page = catalogue.read_text()
    if KIT_NAMED_HEADING not in page:
        return [
            f"{KIT_CATALOGUE}: no {KIT_NAMED_HEADING!r} section — the surface is not named whole"
        ]
    described = set(KIT_DESCRIBED.findall(page))
    described |= set(KIT_NAMED.findall(page[page.index(KIT_NAMED_HEADING) :]))
    regenerate = f"regenerate with `node {KIT_CATALOGUE_SCRIPT.name}`"
    return [
        f"{KIT_CATALOGUE}: names {name}, which the kit does not export — {regenerate}"
        for name in sorted(described - exported)
    ] + [
        f"{KIT_CATALOGUE}: never names {name}, which the kit exports — {regenerate}"
        for name in sorted(exported - described)
    ]


def _framed_stat_failures() -> list[str]:
    """A figure divides by the space around it. A tile is what a `Stat` already is, so a border
    drawn on one is the second answer to a question the gap has answered — the same seam stated
    twice, and the reason a screen of tiles reads as a grid of boxes rather than a row of figures.
    The four ways to divide are one each: space, rule, fill, frame."""
    return [
        f"{path.relative_to(ROOT)}: a Stat carries a border — a figure divides by the space "
        f"around it, and a tile is what a Stat already is"
        for path in sorted(ROOT.glob(APP_PAGE_GLOB))
        if FRAMED_STAT.search(path.read_text())
    ]


def _composition_rhythm_failures() -> list[str]:
    """A page's vertical rhythm is six steps, each with one job, and a band that spaces its parts at
    a seventh breaks the beat for every band under it. The ramp declares fourteen steps two pixels
    apart, which is how one page came to carry six different gaps with no rule between them: too
    many near-identical choices, each defensible alone. The rule is stated in the web guidelines;
    this reads it off the app pages, where a page is composed rather than a control inset. A
    control's own padding is left alone — `p-`, `px-`, `py-` are a component's inset, and the rule
    governs the distance between two things a member reads, which is a gap."""
    pages = sorted(ROOT.glob(APP_PAGE_GLOB))
    if not pages:
        return [f"{APP_PAGE_GLOB}: no app pages found — the gate lost its subjects"]
    return [
        f"{path.relative_to(ROOT)}: {found.group(0)!r} is not one of the six composition steps"
        for path in pages
        for found in COMPOSITION_GAP.finditer(path.read_text())
        if found.group(1) not in COMPOSITION_STEPS
    ]


def _waiting_line_failures() -> list[str]:
    """The waiting mark's words are written once. `Loading` states the line a screen shows while it
    has nothing else, and it appears on the theme's threshold, so the same literal spelled anywhere
    else draws a second mark at a different moment — the one that lands above rows already drawn
    while a re-read is in flight. What is read is that literal, not the idea of waiting: a screen
    that invents its own words for it passes here and answers to a reviewer instead. A sentence
    naming what is coming (`Loading earlier messages…`, `Loading skill`) is another idiom with its
    own rule and is left alone. The exempt span is the component's own declaration, not its file,
    so a second mark beside it fails; the walk covers the portal source and every module of an
    extension's app pages, since a page is built against the kit that publishes the component."""
    source = ROOT / PORTAL_SOURCE
    if not source.is_dir():
        return [f"{PORTAL_SOURCE}: the portal source is missing"]
    drawn = ROOT / WAITING_MODULE
    if not drawn.is_file():
        return [f"{WAITING_MODULE}: the waiting component is missing"]
    text = drawn.read_text()
    start = text.find(WAITING_COMPONENT)
    if start < 0:
        return [f"{WAITING_MODULE}: {WAITING_COMPONENT!r} — the waiting component is missing"]
    closing = WAITING_CLOSE.search(text, start + len(WAITING_COMPONENT))
    if closing is None:
        return [f"{WAITING_MODULE}: the waiting component does not close — the gate lost its span"]
    written = range(start, closing.end())
    failures = []
    if not any(found.start() in written for found in WAITING_LINE.finditer(text)):
        failures.append(f"{WAITING_MODULE}: Loading states no line — the gate lost its mark")
    pages = sorted(ROOT.glob(APP_PAGE_GLOB))
    if not pages:
        failures.append(f"{APP_PAGE_GLOB}: no app pages found — the gate lost its subjects")
    walked = [path for path in sorted(source.rglob("*")) if path.suffix in PORTAL_MODULE_SUFFIXES]
    for path in [*walked, *pages]:
        rel = path.relative_to(ROOT)
        failures.extend(
            f"{rel}: {found.group(0)!r} outside Loading — the surface has one waiting mark"
            for found in WAITING_LINE.finditer(path.read_text())
            if rel != WAITING_MODULE or found.start() not in written
        )
    return failures


def _luminance(color: str) -> float:
    channels = [int(color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast(foreground: str, background: str) -> float:
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _ratio_failures(pairings: list[tuple[str, str, str, float]]) -> list[str]:
    failures = []
    for size, background, text, printed in pairings:
        measured = _contrast(text, background)
        if abs(measured - printed) > PRINTED_RATIO_TOLERANCE:
            failures.append(
                f"{DESIGN_PALETTE}: {text} on {background} is {measured:.2f}:1, printed {printed}:1"
            )
        if measured < AA_RATIOS[size]:
            failures.append(
                f"{DESIGN_PALETTE}: {text} on {background} is {measured:.2f}:1 — "
                f"{size} text needs {AA_RATIOS[size]}:1"
            )
    return failures


def _blocks_boundary_failures() -> list[str]:
    """The blocks tree is a second design system: its own tokens, its own stylesheets, its own page.
    It answers to the portal's style gates only by staying out of the portal — a portal view that
    imported a block would pull a palette the theme does not own into the fleet's bundle, and the
    theme excludes the tree from its scan on the same understanding. So the wall is checked from
    both sides: no portal module reaches into `blocks`, and no block reaches back out."""
    source = ROOT / PORTAL_SOURCE
    if not source.is_dir():
        return [f"{PORTAL_SOURCE}: the portal source is missing"]
    failures = []
    for path in sorted(source.rglob("*")):
        if path.suffix not in PORTAL_MODULE_SUFFIXES:
            continue
        rel = path.relative_to(ROOT)
        text = path.read_text()
        inside = BLOCKS_SOURCE in rel.parents
        if not inside and re.search(r"""from ["']@/blocks/""", text):
            failures.append(f"{rel}: a portal module may not import a block")
        if inside and re.search(r"""from ["']@/(?!blocks/)""", text):
            failures.append(f"{rel}: a block may not import the portal")
    if not any((ROOT / BLOCKS_SOURCE).glob("*.tsx")):
        failures.append(f"{BLOCKS_SOURCE}: the blocks source is missing")
    return failures


def _prompt_home_failures() -> list[str]:
    """Every prompt lives in a directory named `prompts/`. One glob over the repo — `*/prompts/*.md`
    — is then the whole set, so an agent looking for the text a model reads finds it without knowing
    which module loads it. A package cannot write into another's tree, so the directory repeats per
    package rather than centralizing into one; the predictable name is what does the work.

    Only a `.md` a module reads is held. A skill is a directory of its own with its own gates, and
    `AGENTS.md`/`CLAUDE.md` are written for people."""
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
    """A declared prompt is a file, never a Python string. The four points that hand text straight
    to a model — a pack's `PromptSection`, a `SubagentProfile`, the `AgentSpec` an extension ships,
    and the `SetupSchedule` a provision arms — each read a `.md` under their package's `prompts/`,
    so the text a reviewer reads is the text the model gets, a wording change is a diff of prose
    rather than of re-wrapped string literals, and the eval arms an ablation needs are files to
    swap.

    Only the literal fails. A prompt assembled from parts, or read through anything that reaches
    `read_text`, is already living in a file; a short one under PROMPT_LITERAL_BOUND is a test stub
    or a one-line identity, not a prompt. The bound is what separates them: every prompt this repo
    ships cleared it before the gate landed, and the stubs sat far beneath."""
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


def _skill_palette_failures() -> list[str]:
    """The portal theme follows the artifact palette, except body-size secondary text follows the
    AA-safe ufo-style step. Every printed ratio is recomputed, and a palette restatement may write
    no hex the design reference has not declared."""
    theme = ROOT / PORTAL_THEME
    palette = ROOT / DESIGN_PALETTE
    house = ROOT / HOUSE_STYLE_TOKENS
    if not theme.is_file() or not palette.is_file() or not house.is_file():
        return [f"{DESIGN_PALETTE}: the theme or a palette reference is missing"]
    painted = {
        step: (light.upper(), dark.upper()) if light else (single.upper(), single.upper())
        for step, light, dark, single in PALETTE_DECLARATION.findall(theme.read_text())
    }
    text = palette.read_text()
    restated = {
        step: (light.upper(), (dark or light).upper())
        for step, light, dark in PALETTE_ROW.findall(text)
    }
    house_steps = {
        step: (light.upper(), dark.upper()) if light else (single.upper(), single.upper())
        for step, light, dark, single in PALETTE_DECLARATION.findall(house.read_text())
    }
    expected = {**restated, "text-secondary": house_steps.get("text-secondary")}
    failures = [
        f"{HOUSE_STYLE_TOKENS if step == 'text-secondary' else DESIGN_PALETTE}: "
        f"--{step} is {expected.get(step)}, the theme paints {painted[step]}"
        for step in sorted(painted)
        if expected.get(step) != painted[step]
    ]
    failures.extend(
        f"{DESIGN_PALETTE}: --{step} is not a step {PORTAL_THEME} declares"
        for step in sorted(set(restated) - set(painted))
    )
    ink = painted["text-primary"][0]
    pairings = [
        (size, background, foreground, float(printed))
        for size, background, foreground, printed in PAIRING_ROW.findall(text)
    ]
    pairings.extend(
        ("body", fill, ink, float(printed)) for fill, printed in FILL_LABEL_ROW.findall(text)
    )
    if not pairings:
        return [*failures, f"{DESIGN_PALETTE}: no pairing states the contrast it clears"]
    failures.extend(_ratio_failures(pairings))
    declared = {found.upper() for found in re.findall(HEX, text)}
    for path in PALETTE_RESTATEMENTS:
        restatement = ROOT / path
        if not restatement.is_file():
            failures.append(f"{path}: the doc that restates the palette is missing")
            continue
        written = {found.upper() for found in re.findall(HEX, restatement.read_text())}
        failures.extend(
            f"{path}: {hex_color} is not a colour {DESIGN_PALETTE} declares"
            for hex_color in sorted(written - declared)
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
    failures.extend(
        f"{rel}: single-return function {name!r} has exactly one call site — inline it"
        for name, rel in aliases.items()
        if calls.count(name) == 1
    )
    failures.extend(_canonical_stream_failures())
    failures.extend(_tenant_rule_failures())
    failures.extend(_init_code_failures(trees))
    failures.extend(_raw_blob_failures(trees))
    failures.extend(_admission_construction_failures(trees))
    failures.extend(_boundary_failures(trees))
    failures.extend(_sdk_import_failures(trees))
    failures.extend(_core_layout_failures(trees))
    failures.extend(_harness_import_failures(trees))
    failures.extend(_layering_failures(trees))
    failures.extend(_conformance_failures(trees))
    failures.extend(_silence_consumer_failures(trees))
    failures.extend(_job_selector_failures(trees))
    failures.extend(_schedule_authority_failures(trees))
    failures.extend(_wiring_failures(trees))
    failures.extend(_live_frame_failures(trees))
    failures.extend(_live_frame_consumer_failures(trees))
    failures.extend(_directive_wire_failures(trees))
    failures.extend(_consent_mark_failures(trees))
    failures.extend(_silence_sentinel_failures(trees))
    failures.extend(_drawn_mark_failures())
    failures.extend(_to_thread_failures(trees))
    failures.extend(_log_field_failures(trees))
    failures.extend(_set_cookie_failures(trees))
    failures.extend(_skill_failures())
    failures.extend(_skill_copy_mode_failures())
    failures.extend(_skill_palette_failures())
    skill_trees = {
        path.relative_to(ROOT): ast.parse(path.read_text(), filename=str(path))
        for path in _skill_scripts()
    }
    failures.extend(_skill_boundary_failures(skill_trees))
    failures.extend(_migration_failures(trees))
    failures.extend(_migration_antijoin_failures(trees))
    failures.extend(_registered_naming_failures())
    failures.extend(_portal_style_failures())
    failures.extend(_blocks_boundary_failures())
    failures.extend(_composition_rhythm_failures())
    failures.extend(_kit_catalogue_failures())
    failures.extend(_comment_length_failures())
    failures.extend(_framed_stat_failures())
    failures.extend(_waiting_line_failures())
    failures.extend(_app_bundle_failures())
    failures.extend(_app_rebuild_failures())
    failures.extend(_prompt_placement_failures(trees))
    failures.extend(_prompt_home_failures())
    terraform = _env_terraform()
    if not terraform:
        failures.append(f"env roots: no terraform found under {ENV_ROOTS}")
    failures.extend(_shared_singleton_failures(terraform))
    failures.extend(_declared_flag_failures(terraform))
    failures.extend(_retired_resource_failures())
    failures.extend(_census_period_failures(terraform))

    for failure in failures:
        print(f"GATE: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
