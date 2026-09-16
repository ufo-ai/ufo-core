"""Unit tests for the U3 gates: the sdk-only import boundary and the sample-conformance check.

`gates.py` lives at the repo root, outside the package path, so it is loaded here by file location
and its pure AST helpers are driven with hand-built trees — no filesystem, no subprocess."""

import ast
import importlib.util
import json
import tempfile
from pathlib import Path

from ufo.product import PRODUCT_CENSUS_SECONDS

_GATES_PATH = Path(__file__).resolve().parents[2] / "gates.py"
_spec = importlib.util.spec_from_file_location("ufo_gates", _GATES_PATH)
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

SWEPT_KEY = "enable-swept-away"
ROGUE = Path("extensions/rogue/rogue.py")
CORE_FILE = Path("core/src/ufo/db.py")
EXT_TEST = Path("extensions/perplexity/tests/test_ext_perplexity.py")
EXT_SHIPPED_MODULE = Path("extensions/perplexity/ufo_ext_perplexity.py")
EXT_SHIPPED_PACKAGE = Path("extensions/memory/ufo_ext_memory/store.py")
CORE_MIGRATIONS = Path("core/src/ufo/schema/migrations/versions")
EXT_MIGRATIONS = Path("extensions/probe/migrations")
CORE_STAMPS = ("20260801000001", "20260801000002", "20260801000003", "20260801000004")
HARNESS_FILE = Path("core/src/ufo/harness/rounds.py")
RUST_FILE = Path("servers/egress/src/server.rs")
PY_FILE = Path("core/src/ufo/turn.py")
TSX_FILE = Path("extensions/web/frontend/src/app.tsx")
CSS_FILE = Path("extensions/web/frontend/src/theme.css")
SH_FILE = Path("dev/stack.sh")
DEPLOY_GATE = Path(".github/scripts/deploy_change_gate.py")


def _migration(revision: str, down: str, depends: str = "None") -> ast.Module:
    return ast.parse(
        f"revision: str = {revision!r}\n"
        f"down_revision: str | tuple[str, ...] | None = {down}\n"
        f"depends_on: str | None = {depends}\n"
    )


def _check_the_gate_walk_skips_vendored_dependency_trees() -> None:
    """A frontend build checks an npm tree out inside `extensions/`, and a virtualenv can sit under
    any source root. Neither is code this repo gates, and a single vendored `.py` would otherwise
    fail every push — so the walk skips both at any depth while gating the tree around them."""
    assert gates._vendored(Path("extensions/web/frontend/node_modules/pkg/setup.py"))
    assert gates._vendored(Path("extensions/web/frontend/node_modules/a/b/c/deep.py"))
    assert gates._vendored(Path("core/.venv/lib/python3.12/site-packages/thing.py"))
    assert not gates._vendored(Path("extensions/web/ufo_ext_web/surface.py"))
    assert not gates._vendored(Path("extensions/web/tests/test_ext_web.py"))


def _check_log_field_gate_rejects_the_reserved_status_field() -> None:
    reserved = ast.parse(
        'log("turn.terminal", turn_id="t", status="failed")\n'
        'o11y.warn("ingress.refused", status=502)\n'
        'log_error("jobs.failed", job="j", turn_status="failed")\n'
        'emit_metric("turn_terminal_total", status="done")\n'
    )
    failures = gates._log_field_failures({CORE_FILE: reserved})
    assert len(failures) == 2
    assert all("'status'" in failure and "reserved" in failure for failure in failures)
    assert gates._log_field_failures({CORE_FILE: ast.parse('log("x", http_status=200)\n')}) == []


def _check_antijoin_gate_rejects_not_in_over_a_select_in_a_migration() -> None:
    migration = Path("core/src/ufo/schema/migrations/versions/20260909000000_x.py")
    slow = ast.parse(
        "connection.execute(sa.delete(mem_page).where("
        "mem_page.c.page_id.not_in(sa.select(page.c.id))))\n"
    )
    failures = gates._migration_antijoin_failures({migration: slow})
    assert len(failures) == 1
    assert "anti-join" in failures[0]
    fast = ast.parse(
        "connection.execute(sa.delete(mem_page).where("
        "~sa.exists(sa.select(sa.literal(1)).where(page.c.id == mem_page.c.page_id))))\n"
    )
    assert gates._migration_antijoin_failures({migration: fast}) == []
    # A value list is not a subquery, and outside a migration the planner is the caller's problem.
    assert gates._migration_antijoin_failures({migration: ast.parse("x.not_in([1, 2])\n")}) == []
    assert gates._migration_antijoin_failures({CORE_FILE: slow}) == []


ADMISSION_FACTORY_SOURCE = (
    "def _admission(dbos_client, manifests, hub, key_slot_for, billing_url):\n"
    "    return Admission(\n"
    "        dbos=dbos_client,\n"
    "        durable_surfaces=durable_surfaces(manifests),\n"
    "        hub=hub,\n"
    "        key_slot_for=key_slot_for,\n"
    "        billing_url=billing_url,\n"
    "    )\n"
)


def _check_admission_gate_accepts_the_one_factory() -> None:
    trees = {gates.ADMISSION_FACTORY: ast.parse(ADMISSION_FACTORY_SOURCE)}
    assert gates._admission_construction_failures(trees) == []


def _check_admission_gate_rejects_a_second_gate_beside_the_factory() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(
            ADMISSION_FACTORY_SOURCE
            + "\n\ndef run():\n    return Admission(dbos=None, durable_surfaces=frozenset())\n"
        )
    }
    failures = gates._admission_construction_failures(trees)
    assert failures and "outside" in failures[0], failures


def _check_admission_gate_rejects_a_shipped_module_building_its_own() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(ADMISSION_FACTORY_SOURCE),
        EXT_SHIPPED_PACKAGE: ast.parse(
            "gate = Admission(dbos=None, durable_surfaces=frozenset())\n"
        ),
    }
    failures = gates._admission_construction_failures(trees)
    assert failures and str(EXT_SHIPPED_PACKAGE) in failures[0], failures


def _check_admission_gate_leaves_tests_and_the_eval_harness_to_their_own() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(ADMISSION_FACTORY_SOURCE),
        EXT_TEST: ast.parse("gate = Admission(dbos=None, durable_surfaces=frozenset())\n"),
        Path("evals/driver.py"): ast.parse(
            "gate = Admission(dbos=None, durable_surfaces=frozenset())\n"
        ),
    }
    assert gates._admission_construction_failures(trees) == []


def _check_admission_gate_rejects_a_factory_that_drops_the_model_resolution() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(
            "def _admission(dbos_client, manifests, hub, key_slot_for, billing_url):\n"
            "    return Admission(dbos=dbos_client, durable_surfaces=frozenset())\n"
        )
    }
    failures = gates._admission_construction_failures(trees)
    assert failures and "key_slot_for" in failures[0], failures


def _check_admission_gate_rejects_a_serve_that_builds_no_gate_at_all() -> None:
    trees = {gates.ADMISSION_FACTORY: ast.parse("def run():\n    return None\n")}
    failures = gates._admission_construction_failures(trees)
    assert failures and "single construction path" in failures[0], failures


def _check_sdk_only_gate_rejects_core_internal_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from ufo.db import workspace_tx\n")}
    failures = gates._sdk_import_failures(trees)
    assert failures and "ufo.db" in failures[0]


def _check_sdk_only_gate_rejects_bare_ufo_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("import ufo\n")}
    assert gates._sdk_import_failures(trees)


def _check_sdk_only_gate_allows_sdk_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from ufo.sdk.tools import ToolDef\nimport ufo.sdk.jobs\n")}
    assert gates._sdk_import_failures(trees) == []


def _check_sdk_only_gate_ignores_core_internal_imports() -> None:
    trees = {CORE_FILE: ast.parse("from ufo.db import workspace_tx\n")}
    assert gates._sdk_import_failures(trees) == []


def _check_sdk_only_gate_exempts_extension_test_scaffold() -> None:
    trees = {EXT_TEST: ast.parse("from ufo.db import workspace_tx\n")}
    assert gates._sdk_import_failures(trees) == []


def _check_sdk_only_gate_still_binds_the_shipped_extension_package() -> None:
    trees = {
        EXT_SHIPPED_MODULE: ast.parse("from ufo.db import workspace_tx\n"),
        EXT_SHIPPED_PACKAGE: ast.parse("from ufo.db import workspace_tx\n"),
    }
    failures = gates._sdk_import_failures(trees)
    assert len(failures) == 2 and all("ufo.db" in failure for failure in failures)


def _check_engine_core_gate_rejects_runtime_and_host_dependencies() -> None:
    for imported in (
        "from ufo.runtime.billing import accounting",
        "from ufo.harness.models.registry import ModelRegistry",
        "from ufo.harness.durability import replay_safe_client",
        "import ufo_ext_memory",
        "from dbos import DBOS",
        "import sqlalchemy",
        "from fastapi import FastAPI",
        "import httpx",
    ):
        failures = gates._harness_import_failures({HARNESS_FILE: ast.parse(imported)})
        assert len(failures) == 1 and "engine core" in failures[0]


def _check_engine_core_gate_allows_standard_library_and_other_core_modules() -> None:
    tree = ast.parse("import asyncio\nfrom ufo.harness.rounds import ModelRoundRunner\n")
    assert gates._harness_import_failures({HARNESS_FILE: tree}) == []


def _check_engine_core_gate_ignores_files_outside_the_core() -> None:
    trees = {
        CORE_FILE: ast.parse("from sqlalchemy import select\n"),
        Path("core/src/ufo/harness/models/registry.py"): ast.parse("import httpx\n"),
    }
    assert gates._harness_import_failures(trees) == []


def _check_core_layout_gate_requires_a_source_or_test_owner() -> None:
    outside = Path("core/probe.py")
    trees = {
        outside: ast.parse("VALUE = 1\n"),
        CORE_FILE: ast.parse("VALUE = 1\n"),
        HARNESS_FILE: ast.parse("VALUE = 1\n"),
    }

    assert gates._core_layout_failures(trees) == [
        f"{outside}: core Python must live under core/src/ufo or a core test tree"
    ]


def _check_conformance_gate_flags_a_manifest_point_the_sample_drops() -> None:
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


def _check_skills_gate_passes_for_the_shipped_core_skills() -> None:
    assert gates._skill_failures() == []


def _check_skills_gate_flags_a_skill_outside_the_fixed_set() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES | {"rogue"})
    assert any("rogue" in failure for failure in failures)


def _check_skills_gate_flags_a_missing_core_skill() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES - {"sandbox"})
    assert any("sandbox" in failure for failure in failures)


APP_SHIPPED = frozenset({"app_code", "app_wiki"})
APP_HOMES = frozenset({"app-code-home", "app-wiki-home"})
APP_BUILT = frozenset({"code", "wiki"})


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


def _check_app_bundle_gate_passes_for_the_apps_this_repo_ships() -> None:
    assert gates._app_bundle_failures() == []


def _check_app_bundle_gate_flags_a_home_skill_named_for_another_slug() -> None:
    """The failure this gate exists for: the extension installs, the deploy builds a bundle with no
    page in it, and a member opens the app to a blank frame."""
    failures = _app_failures(homes=frozenset({"app-code-home", "app-wikipage-home"}))
    assert any("app_wiki" in failure and "app-wiki-home" in failure for failure in failures)


def _check_app_bundle_gate_flags_an_app_the_bundle_never_names() -> None:
    failures = _app_failures(
        built=frozenset({"code"}), entries=frozenset({"code"}), typechecked=frozenset({"code"})
    )
    assert any("does not build 'wiki'" in failure for failure in failures)


def _check_app_bundle_gate_flags_a_built_page_with_no_entry_document() -> None:
    failures = _app_failures(entries=frozenset({"code"}))
    assert any("apps/wiki/index.html" in failure for failure in failures)


def _check_app_bundle_gate_flags_a_bundle_entry_no_extension_ships() -> None:
    built = APP_BUILT | {"ghost"}
    failures = _app_failures(built=built, entries=built, typechecked=built)
    assert any("app_ghost" in failure for failure in failures)


def _check_app_bundle_gate_flags_a_slug_the_homepage_read_cannot_key() -> None:
    """A slug is lowercase letters and digits, because the homepage read keys an agent by the one
    in its extension name — so a page under any other name is unreachable however carefully the
    rest is spelled."""
    failures = _app_failures(shipped=frozenset({"app_code_review"}))
    assert any("app_code_review" in failure and "lowercase" in failure for failure in failures)


def _check_app_rebuild_gate_passes_for_the_home_skills_this_repo_ships() -> None:
    """Every app's home skill says what a rebuild takes. A member's page is built against the kit of
    the day it was built, and the skill is the only place the agent doing the rebuild reads that —
    so a page nobody rebuilds silently keeps the kit it started on."""
    assert gates._app_rebuild_failures() == []


def _check_app_bundle_gate_flags_a_page_the_typecheck_never_reads() -> None:
    """A page left off the typecheck's file list is built and shipped having never been checked:
    the bundle transpiles without types, so a prop the kit does not declare is dropped in silence
    and the page draws without it."""
    failures = _app_failures(typechecked=frozenset({"code"}))
    assert any("does not read the 'wiki' page" in failure for failure in failures)


def _check_skill_boundary_gate_flags_a_script_importing_ufo() -> None:
    trees = {Path("extensions/x/skills/y/s.py"): ast.parse("from ufo.sdk.tools import ToolDef\n")}
    failures = gates._skill_boundary_failures(trees)
    assert failures and "ufo.sdk" in failures[0]


def _check_skill_boundary_gate_allows_stdlib_and_third_party_imports() -> None:
    trees = {
        Path("extensions/x/skills/y/s.py"): ast.parse("import sys\nfrom pypdf import PdfReader\n")
    }
    assert gates._skill_boundary_failures(trees) == []


def _check_naming_gate_flags_a_dash_in_a_registered_name() -> None:
    from ufo.runtime.ext.manifest import CdpProviderSpec, Manifest, Pack

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


def _check_naming_gate_passes_on_the_installed_tree() -> None:
    assert gates._registered_naming_failures() == []


def _check_skill_content_is_held_out_of_the_code_gates() -> None:
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


def _check_migration_gate_allows_one_core_merge_head() -> None:
    base, left, right, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{right}_right.py": _migration(right, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, right))),
    }
    assert gates._migration_failures(trees) == []


def _check_migration_gate_checks_every_merge_parent() -> None:
    base, left, _, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, "missing"))),
    }
    failures = gates._migration_failures(trees)
    assert any("references no known revision" in failure for failure in failures)


def _check_migration_gate_rejects_cross_owner_merge_parents() -> None:
    base, left, _, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left, "probe_1"))),
        EXT_MIGRATIONS / "probe_1.py": _migration("probe_1", "None", repr(base)),
    }
    failures = gates._migration_failures(trees)
    assert any("chains across owners" in failure for failure in failures)


def _check_migration_gate_still_rejects_multiple_heads_with_a_merge() -> None:
    base, left, right, merge = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / f"{left}_left.py": _migration(left, repr(base)),
        CORE_MIGRATIONS / f"{right}_right.py": _migration(right, repr(base)),
        CORE_MIGRATIONS / f"{merge}_merge.py": _migration(merge, repr((left,))),
    }
    failures = gates._migration_failures(trees)
    assert any("has 2 heads" in failure for failure in failures)


def _check_migration_gate_rejects_a_new_core_revision_that_is_not_a_stamp() -> None:
    """A hand-numbered id races: two branches read the same directory listing, both write the next
    number, and one of them is renumbered before it can land."""
    base, *_ = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        CORE_MIGRATIONS / "0114_next.py": _migration("0114", repr(base)),
    }
    failures = gates._migration_failures(trees)
    assert any("is not a UTC stamp" in failure for failure in failures)


def _check_migration_gate_grandfathers_the_ids_that_predate_the_stamp_rule() -> None:
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


def _check_migration_gate_leaves_an_extension_family_to_its_own_ids() -> None:
    """An extension namespaces its ids by its own name, so two extensions never race for one."""
    base, *_ = CORE_STAMPS
    trees = {
        CORE_MIGRATIONS / f"{base}_base.py": _migration(base, "None"),
        EXT_MIGRATIONS / "0001_probe.py": _migration("probe_0001", "None", repr(base)),
    }
    assert gates._migration_failures(trees) == []


def _check_job_selector_gate_rejects_a_jobspec_without_candidates() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h)\n")}
    failures = gates._job_selector_failures(trees)
    assert failures and "candidates" in failures[0]


def _check_job_selector_gate_rejects_a_none_selector() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h, candidates=None)\n")}
    failures = gates._job_selector_failures(trees)
    assert failures and "None" in failures[0]


def _check_job_selector_gate_allows_a_declared_selector() -> None:
    trees = {CORE_FILE: ast.parse("JobSpec(name='x', schedule=None, handler=h, candidates=sel)\n")}
    assert gates._job_selector_failures(trees) == []


SCHEDULE_STORE = "class ScheduleStore:\n" + "".join(
    f"    async def {name}(self, expected):\n        pass\n"
    for name in sorted(gates.AMBIENT_SCHEDULE_METHODS)
)


def _check_schedule_authority_gate_rejects_explicit_agent_selection() -> None:
    trees = {
        gates.SCHEDULING_MODULE: ast.parse(
            SCHEDULE_STORE + "    async def create(self, conversation_id, selected_agent_id):\n"
            "        pass\n"
            "    async def cancel(self, expected, selected_agent):\n"
            "        pass\n"
        )
    }
    failures = gates._schedule_authority_failures(trees)
    assert len(failures) == 2
    assert all("ambient" in failure for failure in failures)


def _check_schedule_authority_gate_refuses_a_name_no_method_answers() -> None:
    """A frozenset of method names decays in silence: a rename left the gate reporting green over a
    name nothing answered. Resolving the set against the class is what fails instead."""
    trees = {
        gates.SCHEDULING_MODULE: ast.parse(
            "class ScheduleStore:\n    async def create(self, conversation_id):\n        pass\n"
        )
    }
    failures = gates._schedule_authority_failures(trees)
    assert len(failures) == 1
    assert "are gone" in failures[0]
    assert "update" in failures[0]


def _check_schedule_authority_gate_refuses_a_store_it_cannot_find() -> None:
    """The store moves — out of core, and later wherever it goes next. A gate that answers green
    when its module is absent would follow that move by silently checking nothing."""
    failures = gates._schedule_authority_failures({})
    assert len(failures) == 1
    assert "repoint" in failures[0]


def _check_schedule_authority_gate_allows_ambient_agent_selection() -> None:
    trees = {
        gates.SCHEDULING_MODULE: ast.parse(
            SCHEDULE_STORE + "    async def claim_due(self, now, agent_id):\n        pass\n"
        )
    }
    assert gates._schedule_authority_failures(trees) == []


def _check_wiring_gate_counts_database_program_reads_and_writes() -> None:
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


def _check_wiring_gate_does_not_count_column_declaration_as_a_write() -> None:
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


def _check_wiring_gate_accepts_a_column_only_the_outgoing_image_writes() -> None:
    trees = {
        gates.SCHEMA_TABLES: ast.parse(
            "detached_task = sa.Table(\n"
            "    'detached_task', metadata, sa.Column('capability_id', sa.Uuid)\n"
            ")\n"
        ),
        CORE_FILE: ast.parse("select(detached_task.c.turn_id)\n"),
    }
    assert gates._wiring_failures(trees) == []


WEB_SURFACE = Path("extensions/web/ufo_ext_web/surface.py")


def _check_set_cookie_gate_flags_raw_set_cookie_outside_the_factory() -> None:
    trees = {WEB_SURFACE: ast.parse("response.set_cookie('s', token)\n")}
    failures = gates._set_cookie_failures(trees)
    assert failures and "set_session_cookie" in failures[0]


def _check_set_cookie_gate_exempts_the_factory_module() -> None:
    trees = {gates.SESSION_COOKIE_FACTORY: ast.parse("response.set_cookie('s', token)\n")}
    assert gates._set_cookie_failures(trees) == []


def _check_set_cookie_gate_allows_the_factory_helper_at_call_sites() -> None:
    trees = {WEB_SURFACE: ast.parse("set_session_cookie(response, 's', token, samesite='lax')\n")}
    assert gates._set_cookie_failures(trees) == []


TESTING_TF = Path("infra/envs/testing/datadog_aws.tf")
PROD_TF = Path("infra/envs/prod/datadog_aws.tf")
DASHBOARD = 'resource "datadog_dashboard" "database" {}\n'
INTEGRATION = 'resource "datadog_integration_aws_account" "ufo" {}\n'
EXTERNAL_ID = 'resource "datadog_integration_aws_external_id" "ufo" {}\n'
TAG_CONFIGURATION = 'resource "datadog_metric_tag_configuration" "turn_ms" {}\n'
METRIC_METADATA = 'resource "datadog_metric_metadata" "turn_ms" {}\n'


def _check_shared_singleton_gate_flags_an_integration_declared_in_two_roots() -> None:
    failures = gates._shared_singleton_failures({TESTING_TF: INTEGRATION, PROD_TF: INTEGRATION})
    assert failures and "prod, testing" in failures[0]


def _check_shared_singleton_gate_allows_one_root() -> None:
    assert gates._shared_singleton_failures({TESTING_TF: INTEGRATION + EXTERNAL_ID}) == []


def _check_shared_singleton_gate_covers_the_external_id_that_pairs_with_it() -> None:
    """The external id carries no arguments, so nothing about a second declaration looks wrong on
    its own — it is the integration it pairs with that cannot exist twice."""
    failures = gates._shared_singleton_failures({TESTING_TF: EXTERNAL_ID, PROD_TF: EXTERNAL_ID})
    assert failures and "datadog_integration_aws_external_id" in failures[0]


def _check_shared_singleton_gate_covers_the_metric_tag_configuration() -> None:
    """A tag configuration is keyed by metric name alone, so the same metric declared from two roots
    is one remote object with two owners — and the per-environment `env` tag on the metric makes a
    second declaration read as environment-scoped when nothing about it is."""
    failures = gates._shared_singleton_failures(
        {TESTING_TF: TAG_CONFIGURATION, PROD_TF: TAG_CONFIGURATION}
    )
    assert failures and "datadog_metric_tag_configuration" in failures[0]


def _check_shared_singleton_gate_covers_the_metric_metadata() -> None:
    """A metric's unit is org-wide and keyed by metric name, so a second root declaring it is two
    states owning one object — and a unit reads correctly from either, which is what would make a
    divergence here silent."""
    failures = gates._shared_singleton_failures(
        {TESTING_TF: METRIC_METADATA, PROD_TF: METRIC_METADATA}
    )
    assert failures and "datadog_metric_metadata" in failures[0]


def _check_shared_singleton_gate_covers_the_dashboard() -> None:
    """A board selects its fleet through an `env` template variable, so one declaration already
    reads both. A second root declaring the same board builds a second board nobody chose between,
    and each apply keeps its own id, so the two drift instead of colliding loudly."""
    failures = gates._shared_singleton_failures({TESTING_TF: DASHBOARD, PROD_TF: DASHBOARD})
    assert failures and "datadog_dashboard" in failures[0]


def _check_env_terraform_reaches_every_environment_root() -> None:
    """The gate above judges what this collects, so a root it cannot see is a rule that quietly
    stops applying. Nothing else in the suite would notice: a glob matching nothing yields no
    sources, no failures, and a green gate."""
    roots = {path.parent.name for path in gates._env_terraform()}
    assert {"prod", "testing"} <= roots


def _check_shared_singleton_gate_allows_two_files_in_one_root() -> None:
    """A root may split its terraform across files. The rule is one root, not one file."""
    other = Path("infra/envs/testing/datadog_extra.tf")
    assert gates._shared_singleton_failures({TESTING_TF: INTEGRATION, other: INTEGRATION}) == []


def _check_portal_style_gate_reaches_the_source_it_judges() -> None:
    """The gate reads a fixed path, so a rename makes the rule quietly stop applying: a scan root
    that resolves to nothing yields no files, no failures, and a green gate. It names the miss
    instead."""
    assert (gates.ROOT / gates.PORTAL_SOURCE).is_dir()
    assert (gates.ROOT / gates.PORTAL_THEME).is_file()
    for entry in gates.PORTAL_ENTRIES:
        assert (gates.ROOT / entry).is_file()
    assert len(list(gates.ROOT.glob(gates.APP_PAGE_GLOB))) >= 5
    assert gates._portal_style_failures() == []


def _check_palette_gate_matches_the_aa_safe_portal_secondary_step() -> None:
    assert gates._skill_palette_failures() == []


def _check_portal_style_gate_names_a_missing_source_rather_than_passing() -> None:
    """The failure this gate cannot afford is silence, so an absent root is itself a failure."""
    original = gates.PORTAL_SOURCE
    gates.PORTAL_SOURCE = Path("extensions/web/frontend/renamed")
    try:
        failures = gates._portal_style_failures()
    finally:
        gates.PORTAL_SOURCE = original
    assert failures == ["extensions/web/frontend/renamed: the portal source is missing"]


def _check_portal_style_gate_refuses_a_stylesheet_a_single_quoted_import_hides() -> None:
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


def _check_portal_style_gate_holds_an_app_page_to_the_portal_rules() -> None:
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


def _check_portal_style_gate_refuses_a_raw_value_a_bracket_class_smuggles() -> None:
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


def _check_portal_style_gate_reads_a_pages_own_data_as_data() -> None:
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


def _check_portal_style_gate_refuses_the_long_spelling_of_a_class_that_has_a_short_one() -> None:
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


ENV_UFO_TF = Path("infra/envs/testing/ufo.tf")
EDGE_FLAGS = Path("infra/envs/edge/flags.tf")
PACK_CONFIG = '  serve_config = <<-TOML\n    [pack]\n    name = "assistant_hosted"\n  TOML\n'


def _declared_keys() -> set[str]:
    from ufo.host.ext.loader import load_manifests
    from ufo.runtime.context_boundary import CORE_FLAGS

    return {spec.key for spec in CORE_FLAGS} | {
        spec.key for manifest in load_manifests("assistant_hosted") for spec in manifest.flags
    }


def _flags_tf(*keys: str) -> str:
    body = "".join(f'      "{key}" = true\n' for key in keys)
    return "locals {\n  portal_flags = {\n    testing = {\n" + body + "    }\n  }\n}\n"


def _check_flag_gate_names_a_key_the_code_reads_and_the_environment_omits() -> None:
    """The failure with no other signal: the deploy comes up, the flag service is never told about
    that key, and every workspace reads the call-site default forever."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: _flags_tf("enable-memory-tab")}
    )
    assert failures
    assert any("enable-wiki-app" in failure and "omits" in failure for failure in failures)


def _check_flag_gate_names_a_key_the_environment_declares_and_nothing_reads() -> None:
    """The reverse, which reads worse: an operator sets it, the dashboard says the feature moved,
    and no code ever asked."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: _flags_tf(*_declared_keys(), "enable-nothing-at-all")}
    )
    assert [failure for failure in failures if "enable-nothing-at-all" in failure]
    assert not [failure for failure in failures if "omits" in failure]


def _check_flag_gate_refuses_a_tombstoned_key() -> None:
    """The property is that a retired key cannot be declared again — never that one named key is
    retired. The tombstone list is swept, so a case reading an entry out of it goes red on the day
    that entry is dropped, and a case naming a key that is not in it proves nothing."""
    with tempfile.TemporaryDirectory() as held:
        swapped = Path(held) / "flag_tombstones.json"
        swapped.write_text(json.dumps([SWEPT_KEY]))
        original = gates.FLAG_TOMBSTONES
        gates.FLAG_TOMBSTONES = swapped
        try:
            failures = gates._declared_flag_failures(
                {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: _flags_tf(*_declared_keys(), SWEPT_KEY)}
            )
        finally:
            gates.FLAG_TOMBSTONES = original
    assert any(f"tombstoned key {SWEPT_KEY!r}" in failure for failure in failures)


def _check_the_flag_gate_reads_the_tombstones_the_repo_holds() -> None:
    """The case above swaps the file out, so this is what holds the gate to the real one."""
    assert gates.FLAG_TOMBSTONES == gates.ROOT / "infra" / "flag_tombstones.json"
    assert json.loads(gates.FLAG_TOMBSTONES.read_text()) == sorted(
        json.loads(gates.FLAG_TOMBSTONES.read_text())
    )


def _check_flag_gate_passes_where_the_two_lists_agree() -> None:
    declared = _flags_tf(*_declared_keys())
    assert gates._declared_flag_failures({ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: declared}) == []


def _check_flag_gate_names_an_environment_with_no_map_of_its_own() -> None:
    """Each environment answers for itself: a root whose deploy reads flags and whose map is absent
    would apply nothing, and every flag in it would read its default."""
    failures = gates._declared_flag_failures(
        {ENV_UFO_TF: PACK_CONFIG, EDGE_FLAGS: "locals {\n  portal_flags = {\n  }\n}\n"}
    )
    assert len(failures) == 1 and "no portal_flags map for testing" in failures[0]


CENSUS_BOARD_TF = Path("infra/envs/testing/dashboards.tf")


def _check_census_period_gate_flags_a_board_bucketing_at_the_wrong_period() -> None:
    """Terraform and the census hold the same number in two languages. A bucket wider than the
    census fires counts one workspace once per tick it covers, which reads as growth rather than
    as a bug, so it has to fail here instead."""
    wrong = f"locals {{\n  product_census_seconds = {PRODUCT_CENSUS_SECONDS * 2}\n}}\n"

    failures = gates._census_period_failures({CENSUS_BOARD_TF: wrong})

    assert failures and f"every {PRODUCT_CENSUS_SECONDS}s" in failures[0]


def _check_census_period_gate_allows_the_period_the_census_fires_on() -> None:
    right = f"locals {{\n  product_census_seconds = {PRODUCT_CENSUS_SECONDS}\n}}\n"

    assert gates._census_period_failures({CENSUS_BOARD_TF: right}) == []


def _check_census_period_gate_flags_a_board_that_declares_no_period() -> None:
    """A glob that matches nothing turns this gate into a no-op reporting success, which is the one
    failure a gate must not have."""
    failures = gates._census_period_failures({CENSUS_BOARD_TF: 'resource "datadog_dashboard" {}\n'})

    assert failures == ["product board: no env root declares product_census_seconds"]


def _check_layering_gate_rejects_a_host_import_from_runtime_or_harness() -> None:
    tree = ast.parse("from ufo.host.ext.loader import turn_tools\n")
    for rel in (Path("core/src/ufo/runtime/queue.py"), Path("core/src/ufo/harness/agent.py")):
        failures = gates._layering_failures({rel: tree})
        assert len(failures) == 1 and "composition root" in failures[0]


def _check_layering_gate_allows_the_named_boot_modules_and_the_host_itself() -> None:
    tree = ast.parse("from ufo.host.ext.loader import load_manifests\n")
    trees = {
        Path("core/src/ufo/harness/sandbox/ingress_serve.py"): tree,
        Path("core/src/ufo/host/assemble.py"): tree,
        Path("core/src/ufo/serve.py"): tree,
    }
    assert gates._layering_failures(trees) == []


def _check_comment_ceiling_flags_a_paragraph_and_leaves_two_lines_alone() -> None:
    over = "\n".join(f"// line {n}" for n in range(3)) + "\nfn relay() {}\n"
    under = "// line 0\n// line 1\nfn relay() {}\n"
    failures = gates._overlong_comments(RUST_FILE, over)
    assert len(failures) == 1
    assert "comment block of 3 lines" in failures[0]
    assert failures[0].startswith(f"{RUST_FILE}:1:")
    assert gates._overlong_comments(RUST_FILE, under) == []


def _check_comment_ceiling_reads_each_language_by_its_own_openers() -> None:
    """A `#` opens a comment in Python and YAML and nothing in Rust; `//` the reverse. Reading one
    language's opener in another is how `#[cfg(...)]` and `--custom-prop` first read as prose."""
    paragraph = "one\ntwo\nthree"
    for path, prefix in ((PY_FILE, "# "), (RUST_FILE, "// "), (TSX_FILE, "// ")):
        text = "\n".join(prefix + line for line in paragraph.split("\n"))
        assert len(gates._overlong_comments(path, text)) == 1, path
    attributes = '#[cfg(test)]\n#[derive(Clone)]\n#[serde(rename = "a")]\nstruct A;\n'
    assert gates._overlong_comments(RUST_FILE, attributes) == []
    properties = ":root {\n  --ufo-a: 1;\n  --ufo-b: 2;\n  --ufo-c: 3;\n}\n"
    assert gates._overlong_comments(CSS_FILE, properties) == []
    fixture = 'NARRATED = """\n# one\n# two\n# three\n"""\n'
    assert gates._overlong_comments(PY_FILE, fixture) == []
    assert gates._python_comment_lines("x = 1  # trailing\n# whole\n") == frozenset({2})


def _check_comment_ceiling_ignores_a_shebang_and_the_code_a_comment_trails() -> None:
    script = "#!/usr/bin/env bash\nset -euo pipefail\ncurl -sS http://localhost\n"
    assert gates._overlong_comments(SH_FILE, script) == []
    trailing = "let a = 1; // one\nlet b = 2; // two\nlet c = 3; // three\n"
    assert gates._overlong_comments(RUST_FILE, trailing) == []


def _check_comment_ceiling_exempts_a_public_docstring_and_holds_a_private_one() -> None:
    """`cargo doc` and the kit catalogue publish a doc comment to a reader who never opens the file,
    so it is documentation with an audience. Over a private item it is commentary beside code."""
    paragraph = "/// one\n/// two\n/// three\n"
    assert gates._overlong_comments(RUST_FILE, paragraph + "pub fn relay() {}\n") == []
    assert gates._overlong_comments(RUST_FILE, paragraph + "pub(crate) struct Caps;\n") == []
    assert len(gates._overlong_comments(RUST_FILE, paragraph + "fn relay() {}\n")) == 1
    assert len(gates._overlong_comments(RUST_FILE, paragraph + "\nlet a = 1;\n")) == 1
    module = "//! one\n//! two\n//! three\n\nuse std::io;\n"
    assert gates._overlong_comments(RUST_FILE, module) == []
    doc = "/**\n * The button.\n * @param label its text\n */\nexport function Button() {}\n"
    assert gates._overlong_comments(TSX_FILE, doc) == []
    assert len(gates._overlong_comments(TSX_FILE, doc.replace("export ", ""))) == 1


def _check_comment_ceiling_exempts_a_licence_header_and_a_tool_pragma() -> None:
    licence = "// SPDX-License-Identifier: MIT\n// Copyright the authors\n// All rights reserved\n"
    assert gates._overlong_comments(RUST_FILE, licence) == []
    pragma = "# ruff: noqa: E501\n# a second line\n# a third line\n"
    assert gates._overlong_comments(PY_FILE, pragma) == []


def _check_comment_ceiling_ends_a_block_at_the_first_terminator() -> None:
    """The first `*/` closes the block, so a one-line block comment is one run rather than the rest
    of the file. A comment opener has to start the line: one inside a string literal opens nothing,
    or every path and glob that contains `//` reads as prose."""
    block = "/*\n * one\n * two\n */\nfn relay() {}\n"
    assert len(gates._overlong_comments(RUST_FILE, block)) == 1
    prose = "/* a member's file */\nlet a = 1;\nlet b = 2;\nlet c = 3;\n"
    assert gates._comment_runs(prose, ".rs") == [(1, ["/* a member's file */"])]
    literal = 'let a = "/* not a comment */";\nlet b = 1;\nlet c = 2;\n'
    assert gates._comment_runs(literal, ".rs") == []


def _check_comment_ceiling_exempts_the_files_the_deploy_gate_reads_for_alignment() -> None:
    """Shortening a comment in an authorization file deletes lines, `terraform fmt` realigns the `=`
    column of the block around them, and the deploy gate reads the realigned grants as a
    contraction. The exemption therefore has to name exactly the files that gate reads."""
    tree = ast.parse((_GATES_PATH.parent / DEPLOY_GATE).read_text())
    guarded = next(
        {element.value for element in node.value.args[0].elts}
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign) and node.targets[0].id == "AUTHORIZATION_PATHS"
    )
    assert {str(path) for path in gates.COMMENT_ALIGNMENT_FILES} == guarded
    paragraph = "# one\n# two\n# three\n"
    assert len(gates._overlong_comments(next(iter(gates.COMMENT_ALIGNMENT_FILES)), paragraph)) == 1


def _check_comment_ceiling_holds_over_the_tree_it_gates() -> None:
    """The walk itself, on the real repository. `servers/{cache,egress,preview}` carried ninety
    blocks past the ceiling that no text sweep reached, and only this walk fails on them. A
    `skills/` tree is text a model reads and ships only ablated, so it stays out of the walk."""
    assert gates._comment_length_failures() == []
    skill = _GATES_PATH.parent / "core/src/ufo/runtime/skills/ufo-style/references/tokens.css"
    assert gates._is_skill_content(skill)
    assert gates._overlong_comments(Path("tokens.css"), skill.read_text()) != []


def test_repository_gates() -> None:
    for check in (
        _check_the_gate_walk_skips_vendored_dependency_trees,
        _check_log_field_gate_rejects_the_reserved_status_field,
        _check_antijoin_gate_rejects_not_in_over_a_select_in_a_migration,
        _check_admission_gate_accepts_the_one_factory,
        _check_admission_gate_rejects_a_second_gate_beside_the_factory,
        _check_admission_gate_rejects_a_shipped_module_building_its_own,
        _check_admission_gate_leaves_tests_and_the_eval_harness_to_their_own,
        _check_admission_gate_rejects_a_factory_that_drops_the_model_resolution,
        _check_admission_gate_rejects_a_serve_that_builds_no_gate_at_all,
        _check_sdk_only_gate_rejects_core_internal_import_from_extensions,
        _check_sdk_only_gate_rejects_bare_ufo_import_from_extensions,
        _check_sdk_only_gate_allows_sdk_import_from_extensions,
        _check_sdk_only_gate_ignores_core_internal_imports,
        _check_sdk_only_gate_exempts_extension_test_scaffold,
        _check_sdk_only_gate_still_binds_the_shipped_extension_package,
        _check_engine_core_gate_rejects_runtime_and_host_dependencies,
        _check_engine_core_gate_allows_standard_library_and_other_core_modules,
        _check_engine_core_gate_ignores_files_outside_the_core,
        _check_core_layout_gate_requires_a_source_or_test_owner,
        _check_conformance_gate_flags_a_manifest_point_the_sample_drops,
        _check_skills_gate_passes_for_the_shipped_core_skills,
        _check_skills_gate_flags_a_skill_outside_the_fixed_set,
        _check_skills_gate_flags_a_missing_core_skill,
        _check_app_bundle_gate_passes_for_the_apps_this_repo_ships,
        _check_app_bundle_gate_flags_a_home_skill_named_for_another_slug,
        _check_app_bundle_gate_flags_an_app_the_bundle_never_names,
        _check_app_bundle_gate_flags_a_built_page_with_no_entry_document,
        _check_app_bundle_gate_flags_a_bundle_entry_no_extension_ships,
        _check_app_bundle_gate_flags_a_slug_the_homepage_read_cannot_key,
        _check_app_rebuild_gate_passes_for_the_home_skills_this_repo_ships,
        _check_app_bundle_gate_flags_a_page_the_typecheck_never_reads,
        _check_skill_boundary_gate_flags_a_script_importing_ufo,
        _check_skill_boundary_gate_allows_stdlib_and_third_party_imports,
        _check_naming_gate_flags_a_dash_in_a_registered_name,
        _check_naming_gate_passes_on_the_installed_tree,
        _check_skill_content_is_held_out_of_the_code_gates,
        _check_migration_gate_allows_one_core_merge_head,
        _check_migration_gate_checks_every_merge_parent,
        _check_migration_gate_rejects_cross_owner_merge_parents,
        _check_migration_gate_still_rejects_multiple_heads_with_a_merge,
        _check_migration_gate_rejects_a_new_core_revision_that_is_not_a_stamp,
        _check_migration_gate_grandfathers_the_ids_that_predate_the_stamp_rule,
        _check_migration_gate_leaves_an_extension_family_to_its_own_ids,
        _check_job_selector_gate_rejects_a_jobspec_without_candidates,
        _check_job_selector_gate_rejects_a_none_selector,
        _check_job_selector_gate_allows_a_declared_selector,
        _check_schedule_authority_gate_rejects_explicit_agent_selection,
        _check_schedule_authority_gate_refuses_a_store_it_cannot_find,
        _check_schedule_authority_gate_refuses_a_name_no_method_answers,
        _check_schedule_authority_gate_allows_ambient_agent_selection,
        _check_wiring_gate_counts_database_program_reads_and_writes,
        _check_wiring_gate_does_not_count_column_declaration_as_a_write,
        _check_wiring_gate_accepts_a_column_only_the_outgoing_image_writes,
        _check_set_cookie_gate_flags_raw_set_cookie_outside_the_factory,
        _check_set_cookie_gate_exempts_the_factory_module,
        _check_set_cookie_gate_allows_the_factory_helper_at_call_sites,
        _check_shared_singleton_gate_flags_an_integration_declared_in_two_roots,
        _check_shared_singleton_gate_allows_one_root,
        _check_shared_singleton_gate_covers_the_external_id_that_pairs_with_it,
        _check_shared_singleton_gate_covers_the_metric_tag_configuration,
        _check_shared_singleton_gate_covers_the_metric_metadata,
        _check_shared_singleton_gate_covers_the_dashboard,
        _check_env_terraform_reaches_every_environment_root,
        _check_shared_singleton_gate_allows_two_files_in_one_root,
        _check_portal_style_gate_reaches_the_source_it_judges,
        _check_palette_gate_matches_the_aa_safe_portal_secondary_step,
        _check_portal_style_gate_names_a_missing_source_rather_than_passing,
        _check_portal_style_gate_refuses_a_stylesheet_a_single_quoted_import_hides,
        _check_portal_style_gate_holds_an_app_page_to_the_portal_rules,
        _check_portal_style_gate_refuses_a_raw_value_a_bracket_class_smuggles,
        _check_portal_style_gate_reads_a_pages_own_data_as_data,
        _check_portal_style_gate_refuses_the_long_spelling_of_a_class_that_has_a_short_one,
        _check_flag_gate_names_a_key_the_code_reads_and_the_environment_omits,
        _check_flag_gate_names_a_key_the_environment_declares_and_nothing_reads,
        _check_flag_gate_refuses_a_tombstoned_key,
        _check_the_flag_gate_reads_the_tombstones_the_repo_holds,
        _check_flag_gate_passes_where_the_two_lists_agree,
        _check_flag_gate_names_an_environment_with_no_map_of_its_own,
        _check_census_period_gate_flags_a_board_bucketing_at_the_wrong_period,
        _check_census_period_gate_allows_the_period_the_census_fires_on,
        _check_census_period_gate_flags_a_board_that_declares_no_period,
        _check_layering_gate_rejects_a_host_import_from_runtime_or_harness,
        _check_layering_gate_allows_the_named_boot_modules_and_the_host_itself,
        _check_comment_ceiling_flags_a_paragraph_and_leaves_two_lines_alone,
        _check_comment_ceiling_reads_each_language_by_its_own_openers,
        _check_comment_ceiling_ignores_a_shebang_and_the_code_a_comment_trails,
        _check_comment_ceiling_exempts_a_public_docstring_and_holds_a_private_one,
        _check_comment_ceiling_exempts_a_licence_header_and_a_tool_pragma,
        _check_comment_ceiling_ends_a_block_at_the_first_terminator,
        _check_comment_ceiling_exempts_the_files_the_deploy_gate_reads_for_alignment,
        _check_comment_ceiling_holds_over_the_tree_it_gates,
    ):
        check()
