"""Unit tests for the U3 gates: the sdk-only import boundary and the sample-conformance check.

`gates.py` lives at the repo root, outside the package path, so it is loaded here by file location
and its pure AST helpers are driven with hand-built trees — no filesystem, no subprocess."""

import ast
import importlib.util
from pathlib import Path

_GATES_PATH = Path(__file__).resolve().parents[2] / "gates.py"
_spec = importlib.util.spec_from_file_location("selfhost_gates", _GATES_PATH)
gates = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gates)

ROGUE = Path("extensions/rogue/rogue.py")
CORE_FILE = Path("core/src/selfhost/db.py")
EXT_TEST = Path("extensions/exa/tests/test_ext_exa.py")
EXT_EVAL = Path("extensions/exa/evals/web_research.py")
EXT_SHIPPED_MODULE = Path("extensions/exa/selfhost_ext_exa.py")
EXT_SHIPPED_PACKAGE = Path("extensions/memory/selfhost_ext_memory/store.py")


def test_sdk_only_gate_rejects_core_internal_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from selfhost.db import workspace_tx\n")}
    failures = gates._sdk_import_failures(trees)
    assert failures and "selfhost.db" in failures[0]


def test_sdk_only_gate_rejects_bare_selfhost_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("import selfhost\n")}
    assert gates._sdk_import_failures(trees)


def test_sdk_only_gate_allows_sdk_import_from_extensions() -> None:
    trees = {ROGUE: ast.parse("from selfhost.sdk.tools import ToolDef\nimport selfhost.sdk.jobs\n")}
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_ignores_core_internal_imports() -> None:
    trees = {CORE_FILE: ast.parse("from selfhost.db import workspace_tx\n")}
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_exempts_extension_test_and_eval_scaffold() -> None:
    trees = {
        EXT_TEST: ast.parse("from selfhost.db import workspace_tx\n"),
        EXT_EVAL: ast.parse("from selfhost.config import Config\n"),
    }
    assert gates._sdk_import_failures(trees) == []


def test_sdk_only_gate_still_binds_the_shipped_extension_package() -> None:
    trees = {
        EXT_SHIPPED_MODULE: ast.parse("from selfhost.db import workspace_tx\n"),
        EXT_SHIPPED_PACKAGE: ast.parse("from selfhost.db import workspace_tx\n"),
    }
    failures = gates._sdk_import_failures(trees)
    assert len(failures) == 2 and all("selfhost.db" in failure for failure in failures)


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


def test_skill_boundary_gate_flags_a_script_importing_selfhost() -> None:
    trees = {
        Path("extensions/x/skills/y/s.py"): ast.parse("from selfhost.sdk.tools import ToolDef\n")
    }
    failures = gates._skill_boundary_failures(trees)
    assert failures and "selfhost.sdk" in failures[0]


def test_skill_boundary_gate_allows_stdlib_and_third_party_imports() -> None:
    trees = {
        Path("extensions/x/skills/y/s.py"): ast.parse("import sys\nfrom pypdf import PdfReader\n")
    }
    assert gates._skill_boundary_failures(trees) == []


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
