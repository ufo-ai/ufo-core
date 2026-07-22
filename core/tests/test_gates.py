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
