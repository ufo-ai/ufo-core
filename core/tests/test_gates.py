import ast
import importlib.util
import tomllib
from collections import defaultdict
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import make_url

from ufo.harness.o11y import MetricSpec

_GATES_PATH = Path(__file__).resolve().parents[2] / "gates.py"
_spec = importlib.util.spec_from_file_location("ufo_gates", _GATES_PATH)
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

ROGUE = Path("extensions/rogue/rogue.py")
CORE_FILE = Path("core/src/ufo/db.py")
EXT_TEST = Path("extensions/perplexity/tests/test_ext_perplexity.py")
EXT_SHIPPED_MODULE = Path("extensions/perplexity/ufo_ext_perplexity.py")
EXT_SHIPPED_PACKAGE = Path("extensions/memory/ufo_ext_memory/store.py")
EXT_SURFACE = Path("extensions/ufo/ufo_ext_ufo/surface.py")
EXT_SURFACE_MODULE = Path("core/src/ufo/runtime/ext/surface.py")
CORE_MIGRATIONS = Path("core/src/ufo/schema/migrations/versions")
EXT_MIGRATIONS = Path("extensions/probe/migrations")
CORE_STAMPS = ("20260801000001", "20260801000002", "20260801000003", "20260801000004")
HARNESS_FILE = Path("core/src/ufo/harness/rounds.py")
RUST_FILE = Path("servers/egress/src/server.rs")
PY_FILE = Path("core/src/ufo/turn.py")
PY_TEST_FILE = Path("core/tests/test_turn.py")
TS_FILE = Path("extensions/sites/ufo_ext_sites/page/vite.config.ts")
CSS_FILE = Path("core/src/ufo/runtime/skills/ufo-style/references/tokens.css")
SH_FILE = Path(".github/scripts/sandbox_image_key.sh")
PROBE_CLIENT = Path("extensions/probe/ufo_ext_probe/client.py")
PROBE_MANIFEST = Path("extensions/probe/ufo_ext_probe/manifest.py")
PIPEDREAM_MANIFEST = Path("extensions/pipedream/ufo_ext_pipedream/manifest.py")
UNSCOPED_TABLES = frozenset({"alembic_version", "workspace"})
TABLE_COLUMNS = {
    "sqlite": (
        "select t.name, c.name from pragma_table_list as t, pragma_table_info(t.name) as c "
        "where t.schema = 'main' and t.type = 'table' and t.name not like 'sqlite_%'"
    ),
    "postgresql": (
        "select c.table_name, c.column_name from information_schema.columns as c "
        "join pg_tables as t on t.schemaname = c.table_schema and t.tablename = c.table_name "
        "where c.table_schema = 'public'"
    ),
}
SYNC_DRIVERS = {"sqlite": "sqlite", "postgresql": "postgresql+psycopg"}


def _migration(revision: str, down: str, depends: str = "None") -> ast.Module:
    return ast.parse(
        f"revision: str = {revision!r}\n"
        f"down_revision: str | tuple[str, ...] | None = {down}\n"
        f"depends_on: str | None = {depends}\n"
    )


def _tree(rel: Path) -> ast.Module:
    return ast.parse((gates.ROOT / rel).read_text(), filename=str(rel))


def _check_the_gate_walk_skips_vendored_dependency_trees() -> None:
    """A frontend build checks an npm tree out inside `extensions/`, and a virtualenv can sit
    under any source root."""
    assert gates._vendored(Path("extensions/sites/ufo_ext_sites/page/node_modules/pkg/setup.py"))
    assert gates._vendored(Path("extensions/sites/ufo_ext_sites/page/node_modules/a/b/c/deep.py"))
    assert gates._vendored(Path("core/.venv/lib/python3.12/site-packages/thing.py"))
    assert not gates._vendored(EXT_SURFACE)
    assert not gates._vendored(Path("extensions/ufo/tests/test_ext_ufo.py"))


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
    "def _admission(dbos_client, manifests, hub, spend):\n"
    "    return Admission(\n"
    "        dbos=dbos_client,\n"
    "        durable_surfaces=durable_surfaces(manifests),\n"
    "        hub=hub,\n"
    "        spend=spend,\n"
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


def _check_admission_gate_leaves_tests_to_their_own() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(ADMISSION_FACTORY_SOURCE),
        EXT_TEST: ast.parse("gate = Admission(dbos=None, durable_surfaces=frozenset())\n"),
    }
    assert gates._admission_construction_failures(trees) == []


SPEND_TAKER = Path("core/src/ufo/runtime/probe_taker.py")
SPEND_CALLER = Path("core/src/ufo/runtime/probe_caller.py")
SPEND_TAKER_SOURCE = (
    "@dataclass(frozen=True)\n"
    "class Thing:\n"
    "    name: str\n"
    "    spend: SpendGates = NO_SPEND_GATES\n"
    "\n"
    "def build(name: str, *, ledger: Ledger = UNGATED_LEDGER) -> None: ...\n"
)


def _spend_threading(caller: str, path: Path = SPEND_CALLER) -> list[str]:
    return gates._spend_threading_failures(
        {
            SPEND_TAKER: ast.parse(SPEND_TAKER_SOURCE),
            path: ast.parse("from ufo.runtime.probe_taker import Thing, build\n" + caller),
        }
    )


def _check_spend_threading_gate_refuses_a_call_that_drops_the_gates() -> None:
    assert _spend_threading("Thing('a', gates)\nbuild('a', ledger=ledger)\n") == []
    assert _spend_threading("Thing('a', spend=gates)\n") == []
    dropped = _spend_threading("Thing('a')\nbuild('a')\n")
    assert [failure.split(": ", 1)[1].split(" ", 2)[:2] for failure in dropped] == [
        ["Thing", "drops"],
        ["build", "drops"],
    ], dropped
    passed_on = _spend_threading("Thing('a', NO_SPEND_GATES)\nbuild('a', ledger=UNGATED_LEDGER)\n")
    assert len(passed_on) == 2 and all("passes" in failure for failure in passed_on), passed_on
    assert _spend_threading("Thing('a')\n", Path("core/tests/test_probe.py")) == []


def _check_admission_gate_rejects_a_factory_that_drops_the_spend_gates() -> None:
    trees = {
        gates.ADMISSION_FACTORY: ast.parse(
            "def _admission(dbos_client, manifests, hub, spend):\n"
            "    return Admission(dbos=dbos_client, durable_surfaces=frozenset())\n"
        )
    }
    failures = gates._admission_construction_failures(trees)
    assert failures and "drops spend" in failures[0], failures


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
    single = (
        manifest_src + "    member_added: int | None = None\n    member_skills: int | None = None\n"
    )
    trees[gates.MANIFEST_MODULE] = ast.parse(single)
    trees[gates.SAMPLE_MODULE] = ast.parse(
        sample_src.replace("tools=(T,)", "tools=(T,), jobs=(J,)")
    )
    assert gates._conformance_failures(trees) == [
        "conformance: sample does not register Manifest point 'member_added'"
    ]


def _check_skills_gate_passes_for_the_shipped_core_skills() -> None:
    assert gates._skill_failures() == []


def _check_skills_gate_flags_a_skill_outside_the_fixed_set() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES | {"rogue"})
    assert any("rogue" in failure for failure in failures)


def _check_skills_gate_flags_a_missing_core_skill() -> None:
    failures = gates._rogue_skill_failures(gates.CORE_SKILL_NAMES - {"sandbox"})
    assert any("sandbox" in failure for failure in failures)


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
    scripts = gates._skill_scripts()
    assert any(str(path).endswith("skills/sample_skill/probe.py") for path in scripts)
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
            "ledger = sa.Table(\n"
            "    'ledger', metadata, sa.Column('debited_micro_usd', sa.BigInteger)\n"
            ")\n"
        ),
        CORE_FILE: ast.parse("select(ledger.c.turn_id)\n"),
    }
    assert gates._wiring_failures(trees) == []


def _check_outgoing_row_read_gate_refuses_a_whole_row_read_of_an_outgoing_table() -> None:
    for read in (
        "sa.select(tables.ledger)\n",
        "insert(tables.page).returning(tables.ledger)\n",
        "charged = tables.ledger.alias('charged')\nsa.select(charged)\n",
        "sa.select(tables.ledger.alias())\n",
        "sa.select(tables.turn.join(tables.ledger, on))\n",
        "tables.ledger.select()\n",
        "sa.select(*tables.ledger.c)\n",
    ):
        line = read.count("\n")
        assert gates._outgoing_row_read_failures({CORE_FILE: ast.parse(read)}) == [
            f"{CORE_FILE}:{line}: a whole-row read of ledger names ledger.debited_micro_usd, "
            "which only the outgoing image keeps"
        ]


def _check_outgoing_row_read_gate_allows_named_columns_and_other_tables() -> None:
    trees = {
        CORE_FILE: ast.parse(
            "charged = tables.ledger.alias('charged')\n"
            "sa.select(tables.ledger.c.id, charged.c.id)\n"
            "sa.select(tables.turn)\n"
            "sa.select(tables.turn.c.id).select_from(tables.turn.join(tables.ledger, on))\n"
            "tables.ledger.update().returning(tables.ledger.c.id)\n"
        )
    }
    assert gates._outgoing_row_read_failures(trees) == []


def _check_set_cookie_gate_flags_raw_set_cookie_outside_the_factory() -> None:
    trees = {EXT_SURFACE: ast.parse("response.set_cookie('s', token)\n")}
    failures = gates._set_cookie_failures(trees)
    assert failures and "set_session_cookie" in failures[0]


def _check_set_cookie_gate_exempts_the_factory_module() -> None:
    trees = {gates.SESSION_COOKIE_FACTORY: ast.parse("response.set_cookie('s', token)\n")}
    assert gates._set_cookie_failures(trees) == []


def _check_set_cookie_gate_allows_the_factory_helper_at_call_sites() -> None:
    trees = {EXT_SURFACE: ast.parse("set_session_cookie(response, 's', token, samesite='lax')\n")}
    assert gates._set_cookie_failures(trees) == []


def _check_directive_wire_gate_holds_the_surface_and_the_client_to_the_tables() -> None:
    """The walk itself, on the real surface and the real client: every verb the ufo surface emits
    is in the table, every table verb is emitted, and `wire.rs` parses the terminal's vocabulary."""
    assert (
        gates._directive_wire_failures({gates.UFO_SURFACE_MODULE: _tree(gates.UFO_SURFACE_MODULE)})
        == []
    )


def _check_directive_wire_gate_flags_a_verb_outside_the_table() -> None:
    trees = {gates.UFO_SURFACE_MODULE: ast.parse('directive("rogue", "x")\n')}
    failures = gates._directive_wire_failures(trees)
    assert any("'rogue'" in failure and "not in WORKSPACE_WIRE" in failure for failure in failures)


def _check_directive_wire_gate_refuses_a_surface_it_cannot_find() -> None:
    assert gates._directive_wire_failures({}) == [f"wire: {gates.UFO_SURFACE_MODULE} not found"]


LIVE_FRAME_MODULES = (
    gates.RUNTIME_SRC / "hub.py",
    gates.UFO_SURFACE_MODULE,
    gates.RECORD_MODULE,
    gates.REDIS_HUB_MODULE,
    gates.DEBUGGER_SURFACE_MODULE,
)


def _check_live_frame_consumers_handle_every_kind_on_the_tree() -> None:
    trees = {rel: _tree(rel) for rel in LIVE_FRAME_MODULES}
    assert gates._live_frame_consumer_failures(trees) == []


def _check_live_frame_consumer_gate_names_a_consumer_it_cannot_find() -> None:
    """A consumer that moves out of the tree would leave the gate reading nothing over it; the miss
    is named instead, for every consumer the hub stream has."""
    failures = gates._live_frame_consumer_failures(
        {LIVE_FRAME_MODULES[0]: _tree(LIVE_FRAME_MODULES[0])}
    )
    assert "hub: consumer record frame_event not found" in failures
    assert "hub: consumer ufo directives_for not found" in failures
    assert "hub: consumer record fold not found" in failures
    assert f"hub: consumer {gates.REDIS_HUB_MODULE} not found" in failures
    assert "hub: consumer debugger _sse not found" in failures


def _check_debugger_tail_gate_holds_the_tail_to_the_events_its_surface_sends() -> None:
    assert (
        gates._debugger_tail_failures(
            {gates.DEBUGGER_SURFACE_MODULE: _tree(gates.DEBUGGER_SURFACE_MODULE)}
        )
        == []
    )
    unheard = ast.parse(
        "def _sse(cursor, frame):\n"
        "    match frame:\n"
        "        case Terminal():\n"
        '            kind = b"terminal"\n'
        "        case Fresh():\n"
        '            kind = b"fresh_kind"\n'
    )
    failures = gates._debugger_tail_failures({gates.DEBUGGER_SURFACE_MODULE: unheard})
    assert "sse: debugger Tail.tsx does not listen for event 'fresh_kind'" in failures
    assert "sse: debugger Tail.tsx listens for event 'text' that its surface never sends" in (
        failures
    )
    assert gates._debugger_tail_failures({}) == [
        f"sse: {gates.DEBUGGER_SURFACE_MODULE} _sse not found"
    ]


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
    for path, prefix in ((PY_FILE, "# "), (RUST_FILE, "// "), (TS_FILE, "// ")):
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
    """`cargo doc` publishes a doc comment to a reader who never opens the file, so it is
    documentation with an audience. Over a private item it is commentary beside code."""
    paragraph = "/// one\n/// two\n/// three\n"
    assert gates._overlong_comments(RUST_FILE, paragraph + "pub fn relay() {}\n") == []
    assert gates._overlong_comments(RUST_FILE, paragraph + "pub(crate) struct Caps;\n") == []
    assert len(gates._overlong_comments(RUST_FILE, paragraph + "fn relay() {}\n")) == 1
    assert len(gates._overlong_comments(RUST_FILE, paragraph + "\nlet a = 1;\n")) == 1
    module = "//! one\n//! two\n//! three\n\nuse std::io;\n"
    assert gates._overlong_comments(RUST_FILE, module) == []
    doc = "/**\n * The button.\n * @param label its text\n */\nexport function Button() {}\n"
    assert gates._overlong_comments(TS_FILE, doc) == []
    assert len(gates._overlong_comments(TS_FILE, doc.replace("export ", ""))) == 1


def _check_comment_ceiling_holds_a_test_or_private_docstring_and_exempts_a_public_one() -> None:
    """A test declares nothing a reader imports, so its docstring is the per-case rationale the
    ceiling governs. A shipped symbol's docstring is documentation with an audience."""
    case = 'def test_relay() -> None:\n    """one\n    two\n    three"""\n'
    failures = gates._overlong_comments(PY_TEST_FILE, case)
    assert len(failures) == 1
    assert failures[0].startswith(f"{PY_TEST_FILE}:2:")
    assert gates._overlong_comments(PY_FILE, case) == []
    assert gates._overlong_comments(PY_TEST_FILE, case.replace("\n    three", "")) == []
    module = '"""one\ntwo\nthree"""\n\nimport os\n'
    assert gates._overlong_comments(PY_TEST_FILE, module) == []
    pragma = 'class _Probe:\n    """one\n    two\n    three  # noqa: E501"""\n'
    assert gates._overlong_comments(PY_TEST_FILE, pragma) == []
    method = '        """one\n        two\n        three"""\n'
    for private in (
        case.replace("test_relay", "_relay"),
        f"def relay() -> None:\n    def inner() -> None:\n{method}",
        f"class _Relay:\n    def send(self) -> None:\n{method}",
    ):
        assert len(gates._overlong_comments(PY_FILE, private)) == 1, private
    public = f"class Relay:\n    def __init__(self) -> None:\n{method}"
    assert gates._overlong_comments(PY_FILE, public) == []


def _check_comment_ceiling_exempts_a_licence_header_and_a_tool_pragma() -> None:
    licence = "// SPDX-License-Identifier: MIT\n// Copyright the authors\n// All rights reserved\n"
    assert gates._overlong_comments(RUST_FILE, licence) == []
    pragma = "# ruff: noqa: E501\n# a second line\n# a third line\n"
    assert gates._overlong_comments(PY_FILE, pragma) == []


def _check_comment_ceiling_ends_a_block_at_the_first_terminator() -> None:
    """The first `*/` closes the block, so a one-line block comment is one run rather than the
    rest of the file."""
    block = "/*\n * one\n * two\n */\nfn relay() {}\n"
    assert len(gates._overlong_comments(RUST_FILE, block)) == 1
    prose = "/* a member's file */\nlet a = 1;\nlet b = 2;\nlet c = 3;\n"
    assert gates._comment_runs(prose, ".rs") == [(1, ["/* a member's file */"])]
    literal = 'let a = "/* not a comment */";\nlet b = 1;\nlet c = 2;\n'
    assert gates._comment_runs(literal, ".rs") == []


def _check_every_distributed_package_carries_a_typed_marker() -> None:
    """A package without `py.typed` type-checks here, where mypy reads the source, and goes untyped
    wherever it is installed — the repository that bundles this one is the first to see the miss."""
    assert gates._typed_marker_failures() == []
    package = next(
        gates.ROOT / name
        for name in tomllib.loads((gates.ROOT / "pyproject.toml").read_text())["tool"]["hatch"][
            "build"
        ]["targets"]["wheel"]["packages"]
    )
    marker = package / "py.typed"
    moved = marker.read_bytes()
    marker.unlink()
    try:
        assert gates._typed_marker_failures() != []
    finally:
        marker.write_bytes(moved)


def _check_comment_ceiling_holds_over_the_tree_it_gates() -> None:
    """The walk itself, on the real repository. `servers/{cache,egress,preview}` carried ninety
    blocks past the ceiling that no text sweep reached, and only this walk fails on them."""
    assert gates._comment_length_failures() == []
    skill = _GATES_PATH.parent / CSS_FILE
    assert gates._is_skill_content(skill)
    assert gates._overlong_comments(Path("tokens.css"), skill.read_text()) != []


def _check_sdk_constant_gate_rejects_an_extension_redeclaring_an_sdk_constant() -> None:
    trees = {ROGUE: ast.parse("MAX_PROVIDER_RETRIES = 6\n")}
    failures = gates._sdk_constant_shadow_failures(trees, frozenset({"MAX_PROVIDER_RETRIES"}))
    assert failures and "MAX_PROVIDER_RETRIES" in failures[0]


def _check_sdk_constant_gate_allows_a_constant_the_sdk_does_not_export() -> None:
    trees = {ROGUE: ast.parse('OPENROUTER_KEY_SLOT = "openrouter_api_key"\n')}
    assert gates._sdk_constant_shadow_failures(trees, frozenset({"MAX_PROVIDER_RETRIES"})) == []


def _check_sdk_constant_gate_allows_a_lowercase_binding() -> None:
    trees = {ROGUE: ast.parse("client = 1\n")}
    assert gates._sdk_constant_shadow_failures(trees, frozenset({"client"})) == []


def _check_sdk_constant_gate_exempts_extension_test_scaffold() -> None:
    trees = {EXT_TEST: ast.parse("MAX_PROVIDER_RETRIES = 6\n")}
    assert gates._sdk_constant_shadow_failures(trees, frozenset({"MAX_PROVIDER_RETRIES"})) == []


def _check_sdk_constant_gate_ignores_core_modules() -> None:
    trees = {CORE_FILE: ast.parse("MAX_PROVIDER_RETRIES = 6\n")}
    assert gates._sdk_constant_shadow_failures(trees, frozenset({"MAX_PROVIDER_RETRIES"})) == []


def _check_deferred_import_gate_rejects_a_ufo_import_inside_a_function() -> None:
    tree = ast.parse("def f():\n    from ufo.runtime.objects import ObjectListQuery\n")
    failures = gates._deferred_import_failures({EXT_SURFACE_MODULE: tree})
    assert failures and "ufo.runtime.objects" in failures[0]


def _check_deferred_import_gate_allows_a_module_level_import() -> None:
    tree = ast.parse("from ufo.runtime.objects import ObjectListQuery\n")
    assert gates._deferred_import_failures({EXT_SURFACE_MODULE: tree}) == []


def _check_deferred_import_gate_allows_a_type_checking_block() -> None:
    tree = ast.parse(
        "from typing import TYPE_CHECKING\n"
        "if TYPE_CHECKING:\n    from ufo.runtime.objects import ObjectListQuery\n"
    )
    assert gates._deferred_import_failures({EXT_SURFACE_MODULE: tree}) == []


def _check_deferred_import_gate_allows_a_deferred_third_party_import() -> None:
    tree = ast.parse("def f():\n    import httpx\n")
    assert gates._deferred_import_failures({EXT_SURFACE_MODULE: tree}) == []


def _check_deferred_import_gate_ignores_modules_outside_the_ext_seam() -> None:
    tree = ast.parse("def f():\n    from ufo.runtime.objects import ObjectListQuery\n")
    assert gates._deferred_import_failures({CORE_FILE: tree}) == []


def _check_metric_gate_holds_each_emit_to_its_emitters_registry() -> None:
    registry = {
        "emit_metric": {"turn_started_total": None},
        "emit_histogram": {"turn_ms": ("status",)},
    }
    tree = ast.parse(
        'ROUNDS = "turn_rounds_total"\n'
        'emit_metric("turn_started_total", profile="main")\n'
        "emit_metric(ROUNDS, 2)\n"
        'o11y.emit_histogram("turn_ms", 5, status="done")\n'
        'emit_metric("turn_ms")\n'
        "emit_metric(name)\n"
        'emit_histogram("turn_ms", 5, outcome="ok", **tags)\n'
    )
    undeclared = ast.parse('emit_metric("probe_total")\n')
    failures = gates._metric_failures({CORE_FILE: tree, PY_TEST_FILE: undeclared}, registry, {})
    assert len(failures) == 4
    assert "'turn_rounds_total'" in failures[0]
    assert "'turn_ms'" in failures[1]
    assert "expression" in failures[2]
    assert failures[3].endswith("emit_histogram('turn_ms') carries undeclared dimensions outcome")


def _check_metric_gate_admits_an_extensions_names_to_its_own_modules_alone() -> None:
    declared = {
        "ufo_ext_memory": (
            MetricSpec(name="memory_probe_total", kind="counter", dimensions=("outcome",)),
        )
    }
    registry: dict[str, dict[str, tuple[str, ...] | None]] = {
        "emit_metric": {},
        "emit_histogram": {},
    }
    emits = ast.parse('PROBE = "memory_probe_total"\nemit_metric(PROBE, outcome="ok")\n')
    assert gates._metric_failures({EXT_SHIPPED_PACKAGE: emits}, registry, declared) == []
    elsewhere = gates._metric_failures(
        {EXT_SHIPPED_PACKAGE: emits, EXT_SHIPPED_MODULE: emits, CORE_FILE: emits},
        registry,
        declared,
    )
    assert [failure.split(":", 1)[0] for failure in elsewhere] == [
        str(EXT_SHIPPED_MODULE),
        str(CORE_FILE),
    ]
    assert all("declared neither by core nor by its extension's manifest" in f for f in elsewhere)
    wrong_kind = ast.parse('emit_histogram("memory_probe_total", 1)\n')
    assert gates._metric_failures({EXT_SHIPPED_PACKAGE: wrong_kind}, registry, declared) == [
        f"{EXT_SHIPPED_PACKAGE}:1: emit_histogram('memory_probe_total') is declared neither by "
        "core nor by its extension's manifest",
        "extensions: ufo_ext_memory declares metric 'memory_probe_total' that none of its modules "
        "emits",
    ]
    extra = ast.parse('emit_metric("memory_probe_total", outcome="ok", reason="late")\n')
    assert gates._metric_failures({EXT_SHIPPED_PACKAGE: extra}, registry, declared) == [
        f"{EXT_SHIPPED_PACKAGE}:1: emit_metric('memory_probe_total') carries undeclared "
        "dimensions reason"
    ]


def _check_metric_gate_moves_a_core_name_only_one_extension_emits() -> None:
    registry = {
        "emit_metric": dict.fromkeys(
            ("core_probe_total", "shared_probe_total", "lone_probe_total", "dead_probe_total")
        )
    }
    trees = {
        CORE_FILE: ast.parse('emit_metric("core_probe_total")\n'),
        EXT_SHIPPED_PACKAGE: ast.parse(
            'emit_metric("lone_probe_total")\nemit_metric("shared_probe_total")\n'
        ),
        EXT_SHIPPED_MODULE: ast.parse('emit_metric("shared_probe_total")\n'),
    }
    unused = {"ufo_ext_perplexity": (MetricSpec(name="unused_probe_total", kind="counter"),)}
    assert gates._metric_failures(trees, registry, unused) == [
        "extensions: ufo_ext_perplexity declares metric 'unused_probe_total' that none of its "
        "modules emits",
        "core declares metric 'dead_probe_total' that no core module emits",
        "core declares metric 'lone_probe_total' that no core module emits — ufo_ext_memory alone "
        "emits it, so its manifest declares it",
    ]


def _check_deploy_key_gate_reads_each_declaration_off_the_manifest_tree() -> None:
    client = ast.parse(
        'API_KEY_ENV = "PROBE_API_KEY"\n'
        "key = deploy_env(API_KEY_ENV)\n"
        'spec = ModelSpec(id="m", key_env="PROBE_MODEL_KEY")\n'
        "model_key = deploy_env(spec.key_env)\n"
    )
    manifest = ast.parse(
        "from ufo_ext_probe.client import API_KEY_ENV as KEY\n"
        'Manifest(name="probe", version="0", deploy_keys=(KEY, "PROBE_MODEL_KEY"))\n'
    )
    assert gates._deploy_key_failures({PROBE_CLIENT: client, PROBE_MANIFEST: manifest}, ()) == []
    assert sorted(gates._deploy_key_failures({PROBE_CLIENT: client}, ())) == [
        f"{PROBE_CLIENT}:2: reads deploy key 'PROBE_API_KEY' that its manifest leaves out of "
        "deploy_keys",
        f"{PROBE_CLIENT}:3: reads deploy key 'PROBE_MODEL_KEY' that its manifest leaves out of "
        "deploy_keys",
    ]
    unkeyed = ast.parse('Manifest(name="probe", version="0", deploy_bearer_env="PROBE_TOKEN")\n')
    keyed = ast.parse(
        'Manifest(name="probe", version="0", deploy_bearer_env="PROBE_TOKEN", '
        'deploy_keys=("PROBE_TOKEN",))\n'
    )
    assert gates._deploy_key_failures({PROBE_MANIFEST: keyed}, ()) == []
    assert gates._deploy_key_failures({PROBE_MANIFEST: unkeyed}, ()) == [
        f"{PROBE_MANIFEST}:1: reads deploy key 'PROBE_TOKEN' that its manifest leaves out of "
        "deploy_keys"
    ]


def _check_deploy_key_gate_sends_every_environ_read_through_deploy_env() -> None:
    client = ast.parse(
        "import os\n"
        "import os as o\n"
        "from os import environ, getenv as read\n"
        "from ufo.sdk.credentials import deploy_env as env\n"
        'os.environ.get("PROBE_SECRET")\n'
        'os.getenv("PROBE_SECRET")\n'
        'os.environ["PROBE_SECRET"]\n'
        'o.environ.get("PROBE_SECRET")\n'
        'environ.setdefault("PROBE_SECRET", "")\n'
        'environ.pop("PROBE_SECRET")\n'
        'read("PROBE_SECRET")\n'
        '"PROBE_SECRET" in os.environ\n'
        'os.environ.get("PROBE_MODE", "live")\n'
        'env("PROBE_UNDECLARED")\n'
        'os.environ.get("UFO_PROBE_TOKEN")\n'
        "copied = environ\n"
        'env("PROBE_SECRET")\n'
        'env("PROBE_MODE")\n'
        'os.environ.get("UFO_PROBE_URL", "")\n'
        'os.environ["PROBE_WRITTEN"] = "1"\n'
        "spawn(os.environ, {**environ}, o.environ.copy(), env=os.environ)\n"
    )
    manifest = ast.parse('Manifest(name="probe", version="0", deploy_keys=("PROBE_SECRET",))\n')
    trees = {PROBE_CLIENT: client, PROBE_MANIFEST: manifest}
    failures = gates._deploy_key_failures(trees, ("PROBE_MODE", "UFO_PROBE_URL"))
    lines = {int(failure.split(":")[1]): failure for failure in failures}
    assert sorted(lines) == list(range(5, 17))
    assert all("never sees UFO_" in lines[line] for line in range(5, 14))
    assert "leaves out of deploy_keys" in lines[14]
    assert "name a non-secret setting in ENV_SETTINGS" in lines[15]
    assert "other than to read one named key" in lines[16]
    assert gates._deploy_key_failures(trees, ("PROBE_MODE", "UFO_PROBE_URL", "PROBE_GONE"))[-1] == (
        "gates.py: ENV_SETTINGS names 'PROBE_GONE', which no extension reads"
    )


def _check_deploy_key_gate_holds_a_deploy_key_field_to_a_keyword_without_a_default() -> None:
    tree = ast.parse(
        "class Positional:\n"
        "    label: str\n"
        "    custom_oauth_env: str | None = None\n"
        "class Defaulted:\n"
        '    key_env: str = field(default="PROBE_SECRET", kw_only=True)\n'
        "class Keyed:\n"
        "    key_env: str | None = field(default=None, kw_only=True)\n"
        'SPEC = Keyed(key_env="PROBE_SECRET")\n'
    )
    failures = gates._deploy_key_failures({PROBE_CLIENT: tree}, ())
    assert [failure.split(": ", 1)[0] for failure in failures] == [
        f"{PROBE_CLIENT}:{line}" for line in (3, 5, 8)
    ]
    assert "declares custom_oauth_env other than as" in failures[0]
    assert "declares key_env other than as" in failures[1]
    assert "leaves out of deploy_keys" in failures[2]


def _check_deploy_key_gate_refuses_a_key_named_by_an_expression() -> None:
    tree = ast.parse(
        "import os\n"
        "deploy_env(name)\n"
        "os.environ.get(spec.key_env)\n"
        "deploy_env(spec.custom_oauth_env)\n"
        'Manifest(name="probe", version="0", deploy_keys=KEYS)\n'
    )
    failures = gates._deploy_key_failures({PROBE_CLIENT: tree}, ())
    assert len(failures) == 3 and all("by an expression" in failure for failure in failures)
    assert gates._deploy_key_failures({EXT_TEST: tree, CORE_FILE: tree}, ()) == []


def _check_metric_and_deploy_key_gates_hold_over_the_tree() -> None:
    trees = {
        path.relative_to(gates.ROOT): ast.parse(path.read_text(), filename=str(path))
        for path in gates._python_files()
    }
    metrics = {
        entry.module.split(".", 1)[0]: manifest.metrics
        for manifest, entry in gates.discovered().values()
    }
    assert {spec.name for spec in metrics["ufo_ext_repl"]} == {"repl_run_total"}
    assert "repl_run_total" not in gates.METRICS
    assert gates._metric_failures(trees, gates.METRIC_REGISTRY, metrics) == []
    assert gates._deploy_key_failures(trees, gates.ENV_SETTINGS) == []
    undeclared = gates._deploy_key_failures(
        {rel: tree for rel, tree in trees.items() if rel != PIPEDREAM_MANIFEST}, gates.ENV_SETTINGS
    )
    assert {failure.split("'")[1] for failure in undeclared} == {
        "PIPEDREAM_CLIENT_ID",
        "PIPEDREAM_CLIENT_SECRET",
        "PIPEDREAM_PROJECT_ID",
    }


def test_repository_gates() -> None:
    for check in (
        _check_the_gate_walk_skips_vendored_dependency_trees,
        _check_log_field_gate_rejects_the_reserved_status_field,
        _check_antijoin_gate_rejects_not_in_over_a_select_in_a_migration,
        _check_admission_gate_accepts_the_one_factory,
        _check_admission_gate_rejects_a_second_gate_beside_the_factory,
        _check_admission_gate_rejects_a_shipped_module_building_its_own,
        _check_admission_gate_leaves_tests_to_their_own,
        _check_admission_gate_rejects_a_factory_that_drops_the_spend_gates,
        _check_spend_threading_gate_refuses_a_call_that_drops_the_gates,
        _check_admission_gate_rejects_a_serve_that_builds_no_gate_at_all,
        _check_deferred_import_gate_rejects_a_ufo_import_inside_a_function,
        _check_deferred_import_gate_allows_a_module_level_import,
        _check_deferred_import_gate_allows_a_type_checking_block,
        _check_deferred_import_gate_allows_a_deferred_third_party_import,
        _check_deferred_import_gate_ignores_modules_outside_the_ext_seam,
        _check_sdk_constant_gate_rejects_an_extension_redeclaring_an_sdk_constant,
        _check_sdk_constant_gate_allows_a_constant_the_sdk_does_not_export,
        _check_sdk_constant_gate_allows_a_lowercase_binding,
        _check_sdk_constant_gate_exempts_extension_test_scaffold,
        _check_sdk_constant_gate_ignores_core_modules,
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
        _check_outgoing_row_read_gate_refuses_a_whole_row_read_of_an_outgoing_table,
        _check_outgoing_row_read_gate_allows_named_columns_and_other_tables,
        _check_set_cookie_gate_flags_raw_set_cookie_outside_the_factory,
        _check_set_cookie_gate_exempts_the_factory_module,
        _check_set_cookie_gate_allows_the_factory_helper_at_call_sites,
        _check_directive_wire_gate_holds_the_surface_and_the_client_to_the_tables,
        _check_directive_wire_gate_flags_a_verb_outside_the_table,
        _check_directive_wire_gate_refuses_a_surface_it_cannot_find,
        _check_live_frame_consumers_handle_every_kind_on_the_tree,
        _check_live_frame_consumer_gate_names_a_consumer_it_cannot_find,
        _check_debugger_tail_gate_holds_the_tail_to_the_events_its_surface_sends,
        _check_layering_gate_rejects_a_host_import_from_runtime_or_harness,
        _check_layering_gate_allows_the_named_boot_modules_and_the_host_itself,
        _check_comment_ceiling_flags_a_paragraph_and_leaves_two_lines_alone,
        _check_comment_ceiling_reads_each_language_by_its_own_openers,
        _check_comment_ceiling_ignores_a_shebang_and_the_code_a_comment_trails,
        _check_comment_ceiling_exempts_a_public_docstring_and_holds_a_private_one,
        _check_comment_ceiling_holds_a_test_or_private_docstring_and_exempts_a_public_one,
        _check_comment_ceiling_exempts_a_licence_header_and_a_tool_pragma,
        _check_comment_ceiling_ends_a_block_at_the_first_terminator,
        _check_comment_ceiling_holds_over_the_tree_it_gates,
        _check_every_distributed_package_carries_a_typed_marker,
        _check_metric_gate_holds_each_emit_to_its_emitters_registry,
        _check_metric_gate_admits_an_extensions_names_to_its_own_modules_alone,
        _check_metric_gate_moves_a_core_name_only_one_extension_emits,
        _check_deploy_key_gate_reads_each_declaration_off_the_manifest_tree,
        _check_deploy_key_gate_sends_every_environ_read_through_deploy_env,
        _check_deploy_key_gate_holds_a_deploy_key_field_to_a_keyword_without_a_default,
        _check_deploy_key_gate_refuses_a_key_named_by_an_expression,
        _check_metric_and_deploy_key_gates_hold_over_the_tree,
    ):
        check()


def test_every_table_the_migrations_build_is_scoped_by_workspace(database_url: str) -> None:
    url = make_url(database_url)
    backend = url.get_backend_name()
    engine = sa.create_engine(url.set(drivername=SYNC_DRIVERS[backend]))
    try:
        with engine.connect() as connection:
            rows = connection.execute(sa.text(TABLE_COLUMNS[backend])).all()
    finally:
        engine.dispose()
    columns: dict[str, set[str]] = defaultdict(set)
    for table, column in rows:
        columns[table].add(column)
    assert {"workspace", "sample_ext_note", "workspace_credential_slot", "chunk"} <= set(columns)
    assert "id" in columns["workspace"]
    assert {
        table for table, names in columns.items() if "workspace_id" not in names
    } == UNSCOPED_TABLES
