"""Unit tests for the U3 gates: the sdk-only import boundary and the sample-conformance check.

`gates.py` lives at the repo root, outside the package path, so it is loaded here by file location
and its pure AST helpers are driven with hand-built trees — no filesystem, no subprocess."""

import ast
import importlib.util
from pathlib import Path

_GATES_PATH = Path(__file__).resolve().parents[2] / "gates.py"
_spec = importlib.util.spec_from_file_location("ufo_gates", _GATES_PATH)
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

ROGUE = Path("extensions/rogue/rogue.py")
CORE_FILE = Path("core/src/ufo/db.py")
EXT_TEST = Path("extensions/exa/tests/test_ext_exa.py")
EXT_SHIPPED_MODULE = Path("extensions/exa/ufo_ext_exa.py")
EXT_SHIPPED_PACKAGE = Path("extensions/memory/ufo_ext_memory/store.py")
CORE_MIGRATIONS = Path("core/src/ufo/schema/migrations/versions")
EXT_MIGRATIONS = Path("extensions/probe/migrations")


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
    trees = {
        CORE_MIGRATIONS / "a.py": _migration("a", "None"),
        CORE_MIGRATIONS / "b.py": _migration("b", "'a'"),
        CORE_MIGRATIONS / "c.py": _migration("c", "'a'"),
        CORE_MIGRATIONS / "d.py": _migration("d", "('b', 'c')"),
    }
    assert gates._migration_failures(trees) == []


def test_migration_gate_checks_every_merge_parent() -> None:
    trees = {
        CORE_MIGRATIONS / "a.py": _migration("a", "None"),
        CORE_MIGRATIONS / "b.py": _migration("b", "'a'"),
        CORE_MIGRATIONS / "merge.py": _migration("merge", "('b', 'missing')"),
    }
    failures = gates._migration_failures(trees)
    assert any("references no known revision" in failure for failure in failures)


def test_migration_gate_rejects_cross_owner_merge_parents() -> None:
    trees = {
        CORE_MIGRATIONS / "a.py": _migration("a", "None"),
        CORE_MIGRATIONS / "b.py": _migration("b", "'a'"),
        CORE_MIGRATIONS / "merge.py": _migration("merge", "('b', 'probe_1')"),
        EXT_MIGRATIONS / "probe_1.py": _migration("probe_1", "None", "'a'"),
    }
    failures = gates._migration_failures(trees)
    assert any("chains across owners" in failure for failure in failures)


def test_migration_gate_still_rejects_multiple_heads_with_a_merge() -> None:
    trees = {
        CORE_MIGRATIONS / "a.py": _migration("a", "None"),
        CORE_MIGRATIONS / "b.py": _migration("b", "'a'"),
        CORE_MIGRATIONS / "c.py": _migration("c", "'a'"),
        CORE_MIGRATIONS / "merge.py": _migration("merge", "('b',)"),
    }
    failures = gates._migration_failures(trees)
    assert any("has 2 heads" in failure for failure in failures)


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
            "    async def _upsert(self, conversation_id, selected_agent):\n"
            "        pass\n"
        )
    }
    failures = gates._schedule_authority_failures(trees)
    assert len(failures) == 2
    assert all("ambient" in failure for failure in failures)


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
INTEGRATION = 'resource "datadog_integration_aws_account" "ufo" {}\n'
EXTERNAL_ID = 'resource "datadog_integration_aws_external_id" "ufo" {}\n'
TAG_CONFIGURATION = 'resource "datadog_metric_tag_configuration" "turn_ms" {}\n'


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
    assert (gates.ROOT / gates.PORTAL_ENTRY).is_file()
    assert gates._portal_style_failures() == []


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
