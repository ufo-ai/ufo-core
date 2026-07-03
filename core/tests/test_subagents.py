import pytest
from pydantic import BaseModel

from selfhost.ext.manifest import SubagentProfile
from selfhost.loop.profiles import CORE_SUBAGENT_PROFILES, GENERAL_PURPOSE
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt
from selfhost.tools.builtins import BUILTIN_TOOLS


class _Task(BaseModel):
    task: str


class _Finding(BaseModel):
    finding: str


def _profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=name,
        prompt=f"{name} instructions",
        tool_names=("bash", "read"),
        input_model=_Task,
        output_model=_Finding,
    )


def test_registry_rejects_duplicate_profiles() -> None:
    with pytest.raises(ValueError, match="duplicate subagent profiles: dup"):
        SubagentRegistry((_profile("dup"), _profile("dup")))


def test_registry_get_unknown_raises() -> None:
    with pytest.raises(KeyError, match="unknown subagent profile: missing"):
        SubagentRegistry((_profile("a"),)).get("missing")


def test_registry_get_returns_named_profile() -> None:
    registry = SubagentRegistry((_profile("a"), _profile("b")))
    assert registry.get("b").name == "b"
    assert registry.get("b").tool_names == ("bash", "read")


def test_system_prompt_carries_instructions_and_output_schema() -> None:
    prompt = subagent_system_prompt(_profile("research"))
    assert "research instructions" in prompt
    assert "finding" in prompt
    assert "JSON" in prompt


def test_core_ships_a_general_purpose_profile_the_registry_resolves() -> None:
    registry = SubagentRegistry(CORE_SUBAGENT_PROFILES)
    profile = registry.get(GENERAL_PURPOSE)
    assert profile.name == GENERAL_PURPOSE
    assert profile.input_model.model_validate({"task": "look into X"}).task == "look into X"
    assert profile.output_model.model_validate({"result": "done"}).result == "done"


def test_general_purpose_tool_subset_excludes_the_tools_a_subagent_must_not_hold() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    assert "load_skill" in profile.tool_names
    assert {"ask_user", "spawn_subagent", "connect_account"}.isdisjoint(profile.tool_names)


def test_general_purpose_tool_names_all_resolve_to_real_builtins() -> None:
    """The queue projects a subagent's tool set by filtering the builtins on these names — a name
    with no builtin would silently vanish, leaving the subagent short a tool."""
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    builtin_names = {tool.name for tool in BUILTIN_TOOLS}
    assert set(profile.tool_names) <= builtin_names


def test_general_purpose_prompt_lists_the_loadable_skills_and_binds_its_output() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    prompt = subagent_system_prompt(profile)
    assert "<available_skills>" in prompt
    for skill in ("sandbox", "memory", "delegation"):
        assert skill in prompt
    assert "result" in prompt and "JSON" in prompt
