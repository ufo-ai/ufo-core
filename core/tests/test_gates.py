"""Unit tests for the U3 gates: the sdk-only import boundary and the sample-conformance check.

`gates.py` lives at the repo root, outside the package path, so it is loaded here by file location
and its pure AST helpers are driven with hand-built trees — no filesystem, no subprocess."""

import ast
import importlib.util
from pathlib import Path

from ufo.product import PRODUCT_CENSUS_SECONDS

_GATES_PATH = Path(__file__).resolve().parents[2] / "gates.py"
_spec = importlib.util.spec_from_file_location("ufo_gates", _GATES_PATH)
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

ROGUE = Path("extensions/rogue/rogue.py")
CORE_FILE = Path("core/src/ufo/db.py")
EXT_TEST = Path("extensions/perplexity/tests/test_ext_perplexity.py")
EXT_SHIPPED_MODULE = Path("extensions/perplexity/ufo_ext_perplexity.py")
EXT_SHIPPED_PACKAGE = Path("extensions/memory/ufo_ext_memory/store.py")
CORE_MIGRATIONS = Path("core/src/ufo/schema/migrations/versions")
EXT_MIGRATIONS = Path("extensions/probe/migrations")
CORE_STAMPS = ("20260801000001", "20260801000002", "20260801000003", "20260801000004")
INGRESS_FILE = Path("core/src/ufo/sandbox/ingress.py")


def _migration(revision: str, down: str, depends: str = "None") -> ast.Module:
    return ast.parse(
        f"revision: str = {revision!r}\n"
        f"down_revision: str | tuple[str, ...] | None = {down}\n"
        f"depends_on: str | None = {depends}\n"
    )


def test_the_gate_walk_skips_vendored_dependency_trees() -> None:
    """A frontend build checks an npm tree out inside `extensions/`, and a virtualenv can sit under
    any source root. Neither is code this repo gates, and a single vendored `.py` would otherwise
    fail every push — so the walk skips both at any depth while gating the tree around them."""
    assert gates._vendored(Path("extensions/web/frontend/node_modules/pkg/setup.py"))
    assert gates._vendored(Path("extensions/web/frontend/node_modules/a/b/c/deep.py"))
    assert gates._vendored(Path("core/.venv/lib/python3.12/site-packages/thing.py"))
    assert not gates._vendored(Path("extensions/web/ufo_ext_web/surface.py"))
    assert not gates._vendored(Path("extensions/web/tests/test_ext_web.py"))


def test_sdk_only_gate_rejects_core_internal_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from ufo.db import workspace_tx\n")}
    failures = gates._sdk_import_failures(trees)
    assert failures and "ufo.db" in failures[0]


def test_sdk_only_gate_rejects_bare_ufo_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("import ufo\n")}
    assert gates._sdk_import_failures(trees)


def test_sdk_only_gate_allows_sdk_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from ufo.sdk.tools import ToolDef\nimport ufo.sdk.jobs\n")}
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_ignores_core_internal_imports() -> None:
    trees = {CORE_FILE: ast.parse("from ufo.db import workspace_tx\n")}
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_exempts_extension_test_scaffold() -> None:
    trees = {EXT_TEST: ast.parse("from ufo.db import workspace_tx\n")}
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_still_binds_the_shipped_extension_package() -> None:
    trees = {
        EXT_SHIPPED_MODULE: ast.parse("from ufo.db import workspace_tx\n"),
        EXT_SHIPPED_PACKAGE: ast.parse("from ufo.db import workspace_tx\n"),
    }
    failures = gates._sdk_import_failures(trees)
    assert len(failures) == 2 and all("ufo.db" in failure for failure in failures)


def test_conformance_gate_flags_a_manifest_point_the_sample_drops() -> None:
    manifest_src = (
        "class Manifest:\n"
        "    name: str\n"
        "    version: str\n"
        "    tools: tuple[int, ...] = ()\n"
        "    jobs: tuple[int, ...] = ()\n"
    )
    sample_src = "def manifest():\n    return Manifest(name='s', version='1', tools=(T,))\n"
    trees = {
        gates.MANIFEST_MODULE: ast.parse(manifest_src),
        gates.SAMPLE_MODULE: ast.parse(sample_src),
    }
    failures = gates._conformance_failures(trees)
    assert any("jobs" in failure for failure in failures)


def test_skills_gate_passes_for_the_shipped_core_skills() -> None:
    assert gates._skill_failures() == []


def test_skills_gate_flags_a_skill_outside_the_fixed_set() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES | {"rogue"})
    assert any("rogue" in failure for failure in failures)


def test_skills_gate_flags_a_missing_core_skill() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES - {"sandbox"})
    assert any("sandbox" in failure for failure in failures)


APP_SHIPPED = frozenset({"app_radar", "app_wiki"})
APP_HOMES = frozenset({"app-radar-home", "app-wiki-home"})
APP_BUILT = frozenset({"radar", "wiki"})


def _app_failures(
    shipped: frozenset[str] = APP_SHIPPED,
    homes: frozenset[str] = APP_HOMES,
    built: frozenset[str] = APP_BUILT,
    entries: frozenset[str] | None = None,
    typechecked: frozenset[str] | None = None,
) -> list[str]:
    settled = APP_BUILT if entries is None else entries
    return gates._app_slug_failures(
        shipped, homes, built, settled, built if typechecked is None else typechecked
    )


def test_app_bundle_gate_passes_for_the_apps_this_repo_ships() -> None:
    assert gates._app_bundle_failures() == []


def test_app_bundle_gate_flags_a_home_skill_named_for_another_slug() -> None:
    """The failure this gate exists for: the extension installs, the deploy builds a bundle with no
    page in it, and a member opens the app to a blank frame."""
    failures = _app_failures(homes=frozenset({"app-radar-home", "app-wikipage-home"}))
    assert any("app_wiki" in failure and "app-wiki-home" in failure for failure in failures)


def test_app_bundle_gate_flags_an_app_the_bundle_never_names() -> None:
    failures = _app_failures(
        built=frozenset({"radar"}), entries=frozenset({"radar"}), typechecked=frozenset({"radar"})
    )
    assert any("does not build 'wiki'" in failure for failure in failures)


def test_app_bundle_gate_flags_a_built_page_with_no_entry_document() -> None:
    failures = _app_failures(entries=frozenset({"radar"}))
    assert any("apps/wiki/index.html" in failure for failure in failures)


def test_app_bundle_gate_flags_a_bundle_entry_no_extension_ships() -> None:
    built = APP_BUILT | {"ghost"}
    failures = _app_failures(built=built, entries=built, typechecked=built)
    assert any("app_ghost" in failure for failure in failures)


def test_app_bundle_gate_flags_a_slug_the_homepage_read_cannot_key() -> None:
    """A slug is lowercase letters and digits, because the homepage read keys an agent by the one
    in its extension name — so a page under any other name is unreachable however carefully the
    rest is spelled."""
    failures = _app_failures(shipped=frozenset({"app_code_review"}))
    assert any("app_code_review" in failure and "lowercase" in failure for failure in failures)


def test_app_rebuild_gate_passes_for_the_home_skills_this_repo_ships() -> None:
    """Every app's home skill says what a rebuild takes. A member's page is built against the kit of
    the day it was built, and the skill is the only place the agent doing the rebuild reads that —
    so a page nobody rebuilds silently keeps the kit it started on."""
    assert gates._app_rebuild_failures() == []


def test_app_bundle_gate_flags_a_page_the_typecheck_never_reads() -> None:
    """A page left off the typecheck's file list is built and shipped having never been checked:
    the bundle transpiles without types, so a prop the kit does not declare is dropped in silence
    and the page draws without it."""
    failures = _app_failures(typechecked=frozenset({"radar"}))
    assert any("does not read the 'wiki' page" in failure for failure in failures)


def test_skill_boundary_gate_flags_a_script_importing_ufo() -> None:
    trees = {Path("extensions/x/skills/y/s.py"): ast.parse("from ufo.sdk.tools import ToolDef\n")}
    failures = gates._skill_boundary_failures(trees)
    assert failures and "ufo.sdk" in failures[0]


def test_skill_boundary_gate_allows_stdlib_and_third_party_imports() -> None:
    trees = {
        Path("extensions/x/skills/y/s.py"): ast.parse("import sys\nfrom pypdf import PdfReader\n")
    }
    assert gates._skill_boundary_failures(trees) == []


def test_naming_gate_flags_a_dash_in_a_registered_name() -> None:
    from ufo.ext.manifest import CdpProviderSpec, Manifest, Pack

    bad_ext = Manifest(name="bad-ext", version="0")
    assert any("bad-ext" in failure for failure in gates._naming_failures((bad_ext,), ()))

    bad_provider = Manifest(
        name="ok",
        version="0",
        cdp_providers=(CdpProviderSpec(backend="bad-cdp", build=lambda _: None),),
    )
    provider_failures = gates._naming_failures((bad_provider,), ())
    assert any("bad-cdp" in failure and "cdp_providers" in failure for failure in provider_failures)

    bad_pack = Pack(name="bad-pack", version="0")
    assert any("bad-pack" in failure for failure in gates._naming_failures((), (bad_pack,)))


def test_naming_gate_passes_on_the_installed_tree() -> None:
    assert gates._registered_naming_failures() == []


def test_skill_content_is_held_out_of_the_code_gates() -> None:
    """A bundled skill script (a .py file under a SKILL.md folder) is sandbox content, not framework
    code: it appears in `_skill_scripts` for the boundary gate and never in `_python_files`, so the
    process-side code gates skip it."""
    scripts = gates._skill_scripts()
    assert any(str(path).endswith("skills/pdf/render.py") for path in scripts)
    assert not any(gates._is_skill_content(path) for path in gates._python_files())
    manifest_src = (
        "class Manifest:\n"
        "    name: str\n"
        "    version: str\n"
        "    tools: tuple[int, ...] = ()\n"
        "    jobs: tuple[int, ...] = ()\n"
    )
    sample_src = (
        "def manifest():\n    return Manifest(name='s', version='1', tools=(T,), jobs=(J,))\n"
    )
    trees = {
        gates.MANIFEST_MODULE: ast.parse(manifest_src),
        gates.SAMPLE_MODULE: ast.parse(sample_src),
    }
    assert gates._conformance_failures(trees) == []


def test_migration_gate_allows_one_core_merge_head() -> None:
    base, left, right, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{right}_right.py": _migration(right, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, right))),
    }
    assert gates._migration_failures(trees) == []


def test_migration_gate_checks_every_merge_parent() -> None:
    base, left, _, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, "missing"))),
    }
    failures = gates._migration_failures(trees)
    assert any("references no known revision" in failure for failure in failures)


def test_migration_gate_rejects_cross_owner_merge_parents() -> None:
    base, left, _, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, "probe_1"))),
        EXT_MIGRATIONS / "probe_1.py": _migration("probe_1", "None", repr(base)),
    }
    failures = gates._migration_failures(trees)
    assert any("chains across owners" in failure for failure in failures)


def test_migration_gate_still_rejects_multiple_heads_with_a_merge() -> None:
    base, left, right, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{right}_right.py": _migration(right, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left,))),
    }
    failures = gates._migration_failures(trees)
    assert any("has 2 heads" in failure for failure in failures)


def test_migration_gate_rejects_a_new_core_revision_that_is_not_a_stamp() -> None:
    """A hand-numbered id races: two branches read the same directory listing, both write the next
    number, and one of them is renumbered before it can land."""
    base, *_ = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / "0114_next.py": _migration("0114", repr(base)),
    }
    failures = gates._migration_failures(trees)
    assert any("is not a UTC stamp" in failure for failure in failures)


def test_migration_gate_grandfathers_the_ids_that_predate_the_stamp_rule() -> None:
    """A live database is stamped with the hand-numbered chain, and both test targets and extension
    `depends_on` values name those ids, so nothing renumbers them."""
    trees = {
        CORE_MIGRATIONS / "0001_base.py": _migration("0001", "None"),
        CORE_MIGRATIONS / "0113_last.py": _migration("0113", "'0001'"),
        CORE_MIGRATIONS / "knowledge_graph_0001_graph.py": _migration(
            "knowledge_graph_0001", "'0113'"
        ),
    }
    assert gates._migration_failures(trees) == []


def test_migration_gate_leaves_an_extension_family_to_its_own_ids() -> None:
    """An extension namespaces its ids by its own name, so two extensions never race for one."""
    base, *_ = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        EXT_MIGRATIONS / "0001_probe.py": _migration("probe_0001", "None", repr(base)),
    }
    assert gates._migration_failures(trees) == []


def test_job_selector_gate_rejects_a_jobspec_without_candidates() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h)\n")}
    failures = gates._job_selector_failures(trees)
    assert failures and "candidates" in failures[0]


def test_job_selector_gate_rejects_a_none_selector() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h, candidates=None)\n")}
    failures = gates._job_selector_failures(trees)
    assert failures and "None" in failures[0]


def test_job_selector_gate_allows_a_declared_selector() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h, candidates=sel)\n")}
    assert gates._job_selector_failures(trees) == []


def test_schedule_authority_gate_rejects_explicit_agent_selection() -> None:
    trees = {
        gates.SCHEDULING_MODULE: ast.parse(
            "class ScheduleStore:\n"
            "    async def create(self, conversation_id, on_behalf_of_agent_id):\n"
            "        pass\n"
            "    async def cancel(self, expected, selected_agent):\n"
            "        pass\n"
        )
    }
    failures = gates._schedule_authority_failures(trees)
    assert len(failures) == 2
    assert all("ambient" in failure for failure in failures)


def test_schedule_authority_gate_refuses_a_store_it_cannot_find() -> None:
    """The store moves — out of core, and later wherever it goes next. A gate that answers green
    when its module is absent would follow that move by silently checking nothing."""
    failures = gates._schedule_authority_failures({})
    assert len(failures) == 1
    assert "repoint" in failures[0]


def test_schedule_authority_gate_allows_ambient_agent_selection() -> None:
    trees = {
        gates.SCHEDULING_MODULE: ast.parse(
            "class ScheduleStore:\n"
            "    async def create(self, conversation_id):\n"
            "        pass\n"
            "    async def claim_due(self, now):\n"
            "        pass\n"
        )
    }
    assert gates._schedule_authority_failures(trees) == []


def test_wiring_gate_counts_database_program_reads_and_writes() -> None:
    trees = {
        gates.SCHEMA_TABLES: ast.parse(
            "workspace = sa.Table(\n"
            "    'workspace', metadata, sa.Column('page_revision', sa.BigInteger)\n"
            ")\n"
            "page = sa.Table('page', metadata, sa.Column('revision', sa.BigInteger))\n"
        ),
        CORE_MIGRATIONS / "revision.py": ast.parse(
            'op.execute("""\n'
            "create trigger assign_page_revision after insert on page begin\n"
            "update workspace set page_revision = page_revision + 1;\n"
            "update page set revision = (select page_revision from workspace);\n"
            'end\n""")\n'
        ),
        CORE_FILE: ast.parse("select(page.c.revision)\n"),
    }
    assert gates._wiring_failures(trees) == []


def test_wiring_gate_does_not_count_column_declaration_as_a_write() -> None:
    trees = {
        gates.SCHEMA_TABLES: ast.parse(
            "page = sa.Table('page', metadata, sa.Column('revision', sa.BigInteger))\n"
        ),
        CORE_MIGRATIONS / "revision.py": ast.parse(
            "op.add_column('page', sa.Column('revision', sa.BigInteger))\n"
        ),
        CORE_FILE: ast.parse("select(page.c.revision)\n"),
    }
    assert gates._wiring_failures(trees) == ["schema: page.revision has no write site"]


WEB_SURFACE = Path("extensions/web/ufo_ext_web/surface.py")


def test_set_cookie_gate_flags_raw_set_cookie_outside_the_factory() -> None:
    trees = {WEB_SURFACE: ast.parse("response.set_cookie('s', token)\n")}
    failures = gates._set_cookie_failures(trees)
    assert failures and "set_session_cookie" in failures[0]


def test_set_cookie_gate_exempts_the_factory_module() -> None:
    trees = {gates.SESSION_COOKIE_FACTORY: ast.parse("response.set_cookie('s', token)\n")}
    assert gates._set_cookie_failures(trees) == []


def test_set_cookie_gate_allows_the_factory_helper_at_call_sites() -> None:
    trees = {WEB_SURFACE: ast.parse("set_session_cookie(response, 's', token, samesite='lax')\n")}
    assert gates._set_cookie_failures(trees) == []


TESTING_TF = Path("infra/envs/testing/datadog_aws.tf")
PROD_TF = Path("infra/envs/prod/datadog_aws.tf")
DASHBOARD = 'resource "datadog_dashboard" "database" {}\n'
INTEGRATION = 'resource "datadog_integration_aws_account" "ufo" {}\n'
EXTERNAL_ID = 'resource "datadog_integration_aws_external_id" "ufo" {}\n'
TAG_CONFIGURATION = 'resource "datadog_metric_tag_configuration" "turn_ms" {}\n'
METRIC_METADATA = 'resource "datadog_metric_metadata" "turn_ms" {}\n'


def test_shared_singleton_gate_flags_an_integration_declared_in_two_roots() -> None:
    failures = gates._shared_singleton_failures({TESTING_TF: INTEGRATION, PROD_TF: INTEGRATION})
    assert failures and "prod, testing" in failures[0]


def test_shared_singleton_gate_allows_one_root() -> None:
    assert gates._shared_singleton_failures({TESTING_TF: INTEGRATION + EXTERNAL_ID}) == []


def test_shared_singleton_gate_covers_the_external_id_that_pairs_with_it() -> None:
    """The external id carries no arguments, so nothing about a second declaration looks wrong on
    its own — it is the integration it pairs with that cannot exist twice."""
    failures = gates._shared_singleton_failures({TESTING_TF: EXTERNAL_ID, PROD_TF: EXTERNAL_ID})
    assert failures and "datadog_integration_aws_external_id" in failures[0]


def test_shared_singleton_gate_covers_the_metric_tag_configuration() -> None:
    """A tag configuration is keyed by metric name alone, so the same metric declared from two roots
    is one remote object with two owners — and the per-environment `env` tag on the metric makes a
    second declaration read as environment-scoped when nothing about it is."""
    failures = gates._shared_singleton_failures(
        {TESTING_TF: TAG_CONFIGURATION, PROD_TF: TAG_CONFIGURATION}
    )
    assert failures and "datadog_metric_tag_configuration" in failures[0]


def test_shared_singleton_gate_covers_the_metric_metadata() -> None:
    """A metric's unit is org-wide and keyed by metric name, so a second root declaring it is two
    states owning one object — and a unit reads correctly from either, which is what would make a
    divergence here silent."""
    failures = gates._shared_singleton_failures(
        {TESTING_TF: METRIC_METADATA, PROD_TF: METRIC_METADATA}
    )
    assert failures and "datadog_metric_metadata" in failures[0]


def test_shared_singleton_gate_covers_the_dashboard() -> None:
    """A board selects its fleet through an `env` template variable, so one declaration already
    reads both. A second root declaring the same board builds a second board nobody chose between,
    and each apply keeps its own id, so the two drift instead of colliding loudly."""
    failures = gates._shared_singleton_failures({TESTING_TF: DASHBOARD, PROD_TF: DASHBOARD})
    assert failures and "datadog_dashboard" in failures[0]


def test_env_terraform_reaches_every_environment_root() -> None:
    """The gate above judges what this collects, so a root it cannot see is a rule that quietly
    stops applying. Nothing else in the suite would notice: a glob matching nothing yields no
    sources, no failures, and a green gate."""
    roots = {path.parent.name for path in gates._env_terraform()}
    assert {"prod", "testing"} <= roots


def test_shared_singleton_gate_allows_two_files_in_one_root() -> None:
    """A root may split its terraform across files. The rule is one root, not one file."""
    other = Path("infra/envs/testing/datadog_extra.tf")
    assert gates._shared_singleton_failures({TESTING_TF: INTEGRATION, other: INTEGRATION}) == []


def test_portal_style_gate_reaches_the_source_it_judges() -> None:
    """The gate reads a fixed path, so a rename makes the rule quietly stop applying: a scan root
    that resolves to nothing yields no files, no failures, and a green gate. It names the miss
    instead."""
    assert (gates.ROOT / gates.PORTAL_SOURCE).is_dir()
    assert (gates.ROOT / gates.PORTAL_THEME).is_file()
    for entry in gates.PORTAL_ENTRIES:
        assert (gates.ROOT / entry).is_file()
    assert len(list(gates.ROOT.glob(gates.APP_PAGE_GLOB))) >= 5
    assert gates._portal_style_failures() == []


def test_palette_gate_matches_the_aa_safe_portal_secondary_step() -> None:
    assert gates._skill_palette_failures() == []


def test_portal_style_gate_names_a_missing_source_rather_than_passing() -> None:
    """The failure this gate cannot afford is silence, so an absent root is itself a failure."""
    original = gates.PORTAL_SOURCE
    gates.PORTAL_SOURCE = Path("extensions/web/frontend/renamed")
    try:
        failures = gates._portal_style_failures()
    finally:
        gates.PORTAL_SOURCE = original
    assert failures == ["extensions/web/frontend/renamed: the portal source is missing"]


def test_portal_style_gate_refuses_a_stylesheet_a_single_quoted_import_hides() -> None:
    """Prettier writes double quotes, so a single-quoted import is exactly how a stylesheet would
    arrive unnoticed. The rule is about the import, not the quoting."""
    source = gates.ROOT / gates.PORTAL_SOURCE
    smuggled = source / "views" / "smuggled.tsx"
    smuggled.write_text("import 'some-package/dist/widget.css';\nexport const x = 1;\n")
    try:
        failures = gates._portal_style_failures()
    finally:
        smuggled.unlink()
    assert failures == [
        "extensions/web/frontend/src/views/smuggled.tsx: only the entry module imports the theme"
    ]


def test_portal_style_gate_holds_an_app_page_to_the_portal_rules() -> None:
    """The app pages are the portal's former views, compiled in the browser against the portal's
    own theme, so a stylesheet import or a raw bracket value erodes the same design system from a
    different directory. The gate scans them by glob, so the probe is a new sibling skill dir."""
    probe_dir = gates.ROOT / "extensions/app_chat/ufo_ext_app_chat/skills/app-gate-probe"
    probe_dir.mkdir()
    probe = probe_dir / "app.tsx"
    probe.write_text(
        "import 'some-package/dist/widget.css';\n"
        'export const x = "bg-[#fff] max-h-[var(--media-tall)]";\n'
    )
    try:
        failures = gates._portal_style_failures()
    finally:
        probe.unlink()
        probe_dir.rmdir()
    rel = "extensions/app_chat/ufo_ext_app_chat/skills/app-gate-probe/app.tsx"
    assert failures == [
        f"{rel}: an app page imports no stylesheet — the kit's theme is the sheet",
        f"{rel}: bg-[#fff] names a raw value — resolve it through a theme token",
    ]


def test_portal_style_gate_refuses_a_raw_value_a_bracket_class_smuggles() -> None:
    """A bracket utility naming a raw measurement re-decides a token inline, which is how the
    design system erodes under Tailwind. The var-resolving and structural forms stay allowed —
    the rule is about the raw value, not the bracket."""
    source = gates.ROOT / gates.PORTAL_SOURCE
    smuggled = source / "views" / "smuggled.tsx"
    smuggled.write_text(
        'export const x = "max-w-[20ch] bg-[#fff] max-h-[var(--media-tall)]'
        ' [overflow-wrap:anywhere] grid-cols-[auto_1fr]";\n'
    )
    try:
        failures = gates._portal_style_failures()
    finally:
        smuggled.unlink()
    assert failures == [
        "extensions/web/frontend/src/views/smuggled.tsx: max-w-[20ch] names a raw value — "
        "resolve it through a theme token",
        "extensions/web/frontend/src/views/smuggled.tsx: bg-[#fff] names a raw value — "
        "resolve it through a theme token",
    ]


def test_portal_style_gate_reads_a_pages_own_data_as_data() -> None:
    """An issue reference and a percentage are spelled exactly like a raw colour and a raw length,
    and a page carries them as its content. A Tailwind arbitrary value is always a utility holding
    one — `w-[3px]` — or an arbitrary property, which spells `[prop:value]`. A bare `[...]` is a
    JavaScript array, and reading the whole file for brackets refused a page over its own data."""
    source = gates.ROOT / gates.PORTAL_SOURCE
    held = source / "views" / "held.tsx"
    held.write_text(
        'export const REFS = ["#2040", "#2051"];\n'
        'export const SHARES = ["41.2%", "22.8%"];\n'
        "export const first = REFS[0];\n"
    )
    try:
        failures = gates._portal_style_failures()
    finally:
        held.unlink()
    assert failures == []


def test_portal_style_gate_refuses_the_long_spelling_of_a_class_that_has_a_short_one() -> None:
    """Each refused shape has a shorter spelling that already means the same thing, so the long
    form is drift, not intent. The short forms beside them stay allowed."""
    source = gates.ROOT / gates.PORTAL_SOURCE
    smuggled = source / "views" / "smuggled.tsx"
    smuggled.write_text(
        'export const x = "space-y-md dark:bg-ink '
        "overflow-hidden text-ellipsis whitespace-nowrap w-lg h-lg "
        'gap-md truncate size-lg w-lg h-md";\n'
        "export const y = <div className={`x ${z}`} />;\n"
    )
    try:
        failures = gates._portal_style_failures()
    finally:
        smuggled.unlink()
    rel = "extensions/web/frontend/src/views/smuggled.tsx"
    assert failures == [
        f"{rel}: 'space-y-' — stack with flex and a gap",
        f"{rel}: 'dark:' — color-scheme carries the scheme",
        f"{rel}: 'overflow-hidden text-ellipsis whitespace-nowrap' — truncate says this",
        f"{rel}: 'w-lg h-lg' — one size- utility says it once",
        f"{rel}: 'className={{`' — compose classes with cn()",
    ]


def test_containment_gate_flags_a_new_ingress_that_opens_what_it_was_handed() -> None:
    """The rule issue #1112 is about: a site that builds a host path from agent, model, connector or
    provider input and then opens it. Validating the name is not validating the path — the open is
    where a planted symlink wins — so the write has to go through the shared guard."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def stage(name, root):\n    target = root / name\n    target.write_bytes(b'body')\n"
        )
    }
    failures = gates._ingress_containment_failures(trees)
    assert len(failures) == 1
    assert ".write_bytes" in failures[0] and "stage" in failures[0]
    assert "(target)" in failures[0], "the failure names the value the call opened"


def test_containment_gate_passes_a_site_that_goes_through_the_guard() -> None:
    trees = {
        INGRESS_FILE: ast.parse(
            "def stage(name, root):\n"
            "    with contained_file(name, root) as target:\n"
            "        target.replace_bytes(b'body', 0o644)\n"
        )
    }
    assert gates._ingress_containment_failures(trees) == []


def test_containment_gate_leaves_a_path_the_module_fixed_itself_alone() -> None:
    """A path built from the package's own constants is not ingress, and a gate that fired on it
    would be turned off within a week."""
    trees = {
        INGRESS_FILE: ast.parse("def read_template(kind):\n    return (TEMPLATES).read_text()\n")
    }
    assert gates._ingress_containment_failures(trees) == []


def test_containment_gate_exempts_the_guard_itself_and_the_test_scaffold() -> None:
    """The guard is the implementation every other file is held to, and a test names its own
    roots."""
    source = "def stage(name, root):\n    (root / name).write_bytes(b'body')\n"
    trees = {gates.CONTAINMENT_MODULE: ast.parse(source), EXT_TEST: ast.parse(source)}
    assert gates._ingress_containment_failures(trees) == []


def test_containment_gate_honours_the_deferred_row_allowlist() -> None:
    """A row #1112 deliberately left unrouted is named with its reason, which is the review the gate
    exists to force — and every name in the allowlist still points at a site in the tree."""
    trees = {
        INGRESS_FILE: ast.parse("def stage(name, root):\n    (root / name).write_bytes(b'x')\n")
    }
    gates.DEFERRED_INGRESS[(INGRESS_FILE, "stage")] = "deferred for this test"
    try:
        assert gates._ingress_containment_failures(trees) == []
    finally:
        del gates.DEFERRED_INGRESS[(INGRESS_FILE, "stage")]
    for rel, site in gates.DEFERRED_INGRESS:
        assert (gates.ROOT / rel).is_file(), f"{rel} is allowlisted but not in the tree"
        assert site in (gates.ROOT / rel).read_text(), f"{rel} no longer defines {site}"


def test_containment_gate_reads_a_core_module_that_opens_a_handed_path() -> None:
    """The file ops moved into the `ufo` client, so the sandbox holds no Python script of ours to
    scan — but the gate still governs every core module that builds a host path from model, agent,
    connector or provider input."""
    module = gates.CORE_SRC / "sandbox" / "ingress_host.py"
    trees = {
        module: ast.parse("def op_leak(params):\n    return Path(params['path']).read_text()\n")
    }
    failures = gates._ingress_containment_failures(trees)
    assert len(failures) == 1 and "op_leak" in failures[0]


def test_containment_gate_does_not_let_one_guarded_call_launder_a_function() -> None:
    """A name handed *to* a guard is not proved by it. In a handler shaped like the real ops,
    marking every name mentioned in the guard call would prove `params` and pass every other
    `params[...]` open in the same function."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def read(params):\n"
            "    with contained_file(params['path'], params['workspace']) as target:\n"
            "        target.lstat()\n"
            "    return Path(params['other']).read_text()\n"
        )
    }
    failures = gates._ingress_containment_failures(trees)
    assert len(failures) == 1 and ".read_text" in failures[0]


def test_containment_gate_proves_a_call_that_takes_the_guard_result_itself() -> None:
    """What the guard returns may reach the filesystem, however it is spelled: bound to a name,
    given straight to the call, or unpacked from a pair."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def walk(params, root):\n"
            "    for base, _dirs, names in os.walk(contained_dir(params['path'], root)):\n"
            "        yield base, names\n"
            "\n"
            "def enumerate_hits(params, root):\n"
            "    start, pattern = contained_glob(params['pattern'], params['path'], root)\n"
            "    return list(start.glob(pattern))\n"
        )
    }
    assert gates._ingress_containment_failures(trees) == []


def test_the_lexical_only_guard_is_not_in_the_guard_vocabulary() -> None:
    """`workspace_path` resolves a container path this process cannot stat, so proving a name with
    it and then opening that name is the gap the gate exists to find — it is allowlisted as a
    deferred site, never counted as a guard."""
    assert "workspace_path" not in gates.CONTAINMENT_GUARDS
    assert (gates.CORE_SRC / "sandbox" / "session.py", "workspace_path") in gates.DEFERRED_INGRESS
    trees = {
        INGRESS_FILE: ast.parse(
            "def stage(path):\n"
            "    target = workspace_path(path)\n"
            "    return Path(target).read_bytes()\n"
        )
    }
    failures = gates._ingress_containment_failures(trees)
    assert len(failures) == 1 and ".read_bytes" in failures[0]


def test_the_lexical_tier_does_not_launder_the_open_that_follows_it() -> None:
    """`contained_leaf(name)` joined under a module constant and written is CVE-2026-56692's own
    shape: the leaf says nothing about the root, so a link at the root or at any component still
    steers the bytes. Every lexical helper returns a string its caller must still open through a
    canonical guard, so none of them may credit a name here."""
    assert not gates.LEXICAL_GUARDS & gates.CONTAINMENT_GUARDS
    sites = {
        "contained_leaf": "safe = contained_leaf(name, FALLBACK)\n    (ROOT / safe).write_bytes(b)",
        "contained_relative": (
            "safe = contained_relative(name, ROOT)\n    Path(safe).write_bytes(b)"
        ),
        "inbox_name": "safe = inbox_name(name, set())\n    (ROOT / safe).write_bytes(b)",
        "contained_pattern": "safe = contained_pattern(name, ROOT)\n    list(ROOT.glob(safe))",
    }
    assert sites.keys() == set(gates.LEXICAL_GUARDS)

    for guard, body in sites.items():
        trees = {INGRESS_FILE: ast.parse(f"def stage(name, b):\n    {body}\n")}

        failures = gates._ingress_containment_failures(trees)

        assert len(failures) == 1, guard
        assert "safe" in failures[0] and "lexical tier" in failures[0], guard


def test_containment_gate_follows_a_path_through_the_loop_that_hands_it_out() -> None:
    """A staging site is shaped `for file in files:`, so a name the iteration hands out is as much
    the caller's as the sequence it came from — a gate that lost the path at the loop would pass
    every fetch-each-produced-file site in the tree."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def stage(files):\n"
            "    for file in files:\n"
            "        Path(file.name).write_bytes(file.body)\n"
        )
    }

    failures = gates._ingress_containment_failures(trees)

    assert len(failures) == 1 and "(file)" in failures[0]


def test_containment_gate_flags_a_sandbox_program_that_checks_paths_itself() -> None:
    """An in-sandbox program opens files on paths the model named, where a planted link needs no
    race to win. The bootstrap carries the guard into the program so it can import it."""
    trees = {INGRESS_FILE: ast.parse('READ_PROG = "import os\\nopen(sys.argv[1])\\n"\n')}
    failures = gates._sandbox_program_failures(trees)
    assert len(failures) == 1 and "READ_PROG" in failures[0]
    guarded = {
        INGRESS_FILE: ast.parse('READ_PROG = "from containment import contained_file\\nopen(p)"\n')
    }
    assert gates._sandbox_program_failures(guarded) == []


def test_containment_gate_flags_a_seventh_hand_rolled_lexical_check() -> None:
    """Six near-duplicate `..` checks is the census #1112 opened with, each a different subset of
    the same rule. The lexical tier is published, so a fresh one is a finding, not a fix."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def safe(path):\n"
            "    if '..' in PurePosixPath(path).parts:\n"
            "        raise ValueError('escape')\n"
            "    return path\n"
        )
    }
    failures = gates._lexical_containment_failures(trees)
    assert len(failures) == 1 and "'..' in" in failures[0]


def test_containment_gate_ignores_a_membership_test_that_is_not_about_paths() -> None:
    """`in` is the most common operator in the codebase; only the guard's own vocabulary is
    fenced."""
    trees = {
        INGRESS_FILE: ast.parse(
            "def kind(payload):\n    return 'value' in payload or payload in (None, '')\n"
        )
    }
    assert gates._lexical_containment_failures(trees) == []


ENV_UFO_TF = Path("infra/envs/testing/ufo.tf")
EDGE_FLAGS = Path("infra/envs/edge/flags.tf")
PACK_CONFIG = '  serve_config = <<-TOML\n    [pack]\n    name = "assistant_hosted"\n  TOML\n'


def _declared_keys() -> set[str]:
    from ufo.ext.loader import load_manifests

    return {spec.key for manifest in load_manifests("assistant_hosted") for spec in manifest.flags}


def _flags_tf(*keys: str) -> str:
    body = "".join(f'      "{key}" = true\n' for key in keys)
    return "locals {\n  portal_flags = {\n    testing = {\n" + body + "    }\n  }\n}\n"


def test_flag_gate_names_a_key_the_code_reads_and_the_environment_omits() -> None:
    """The failure with no other signal: the deploy comes up, the flag service is never told about
    that key, and every workspace reads the call-site default forever."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: _flags_tf("enable-memory-tab")}
    )
    assert failures
    assert any("enable-wiki-app" in failure and "omits" in failure for failure in failures)


def test_flag_gate_names_a_key_the_environment_declares_and_nothing_reads() -> None:
    """The reverse, which reads worse: an operator sets it, the dashboard says the feature moved,
    and no code ever asked."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: _flags_tf(*_declared_keys(), "enable-nothing-at-all")}
    )
    assert [failure for failure in failures if "enable-nothing-at-all" in failure]
    assert not [failure for failure in failures if "omits" in failure]


def test_flag_gate_passes_where_the_two_lists_agree() -> None:
    declared = _flags_tf(*_declared_keys())
    assert gates._declared_flag_failures({ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: declared}) == []


def test_flag_gate_names_an_environment_with_no_map_of_its_own() -> None:
    """Each environment answers for itself: a root whose deploy reads flags and whose map is absent
    would apply nothing, and every flag in it would read its default."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: "locals {\n  portal_flags = {\n  }\n}\n"}
    )
    assert len(failures) == 1 and "no portal_flags map for testing" in failures[0]


CENSUS_BOARD_TF = Path("infra/envs/testing/dashboards.tf")


def test_census_period_gate_flags_a_board_bucketing_at_the_wrong_period() -> None:
    """Terraform and the census hold the same number in two languages. A bucket wider than the
    census fires counts one workspace once per tick it covers, which reads as growth rather than
    as a bug, so it has to fail here instead."""
    wrong = f"locals {{\n  product_census_seconds = {PRODUCT_CENSUS_SECONDS * 2}\n}}\n"

    failures = gates._census_period_failures({CENSUS_BOARD_TF: wrong})

    assert failures and f"every {PRODUCT_CENSUS_SECONDS}s" in failures[0]


def test_census_period_gate_allows_the_period_the_census_fires_on() -> None:
    right = f"locals {{\n  product_census_seconds = {PRODUCT_CENSUS_SECONDS}\n}}\n"

    assert gates._census_period_failures({CENSUS_BOARD_TF: right}) == []


def test_census_period_gate_flags_a_board_that_declares_no_period() -> None:
    """A glob that matches nothing turns this gate into a no-op reporting success, which is the one
    failure a gate must not have."""
    failures = gates._census_period_failures({CENSUS_BOARD_TF: 'resource "datadog_dashboard" {}\n'})

    assert failures == ["product board: no env root declares product_census_seconds"]
