"""The coding pack's proof: its manifest registers a single `coding` SubagentProfile over the core
code builtins, and core's shell-wrap renders it with the shared citation discipline and a filled
skill index. The profile names only tool names, so the pack is self-contained — nothing here
imports a core internal."""

import selfhost_ext_coding as coding

from selfhost.loop.subagents import subagent_system_prompt
from selfhost.tools.builtins import BUILTIN_TOOLS


def test_coding_manifest_registers_a_single_coding_profile() -> None:
    manifest = coding.manifest()
    assert manifest.tools == ()
    (profile,) = manifest.subagents
    assert profile.name == "coding"
    assert profile.input_model.model_validate({"objective": "fix it"}).objective == "fix it"
    assert profile.output_model.model_validate({"result": "fixed"}).result == "fixed"


def test_coding_tools_are_core_builtins_plus_the_repl_and_exclude_the_forbidden_ones() -> None:
    profile = coding.CODING_PROFILE
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    for name in ("bash", "read", "write", "edit", "glob", "grep"):
        assert name in profile.tool_names and name in builtin_names
    assert "js_repl" in profile.tool_names
    assert {"ask_user", "spawn_subagent", "wait_for_subagents", "cancel_subagent"}.isdisjoint(
        profile.tool_names
    )


def test_coding_prompt_wraps_with_citation_and_fills_the_skill_index() -> None:
    prompt = subagent_system_prompt(coding.CODING_PROFILE)
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt
    assert "<citation_instructions>" in prompt
    assert "software-engineering task" in prompt
    assert "JSON" in prompt
