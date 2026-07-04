import pytest
from pydantic import BaseModel

from selfhost.ext.manifest import SUBAGENT_ROUND_LIMIT, SubagentProfile
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


def test_general_purpose_carries_the_subagent_round_budget() -> None:
    profile = SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE)
    assert profile.max_rounds == SUBAGENT_ROUND_LIMIT == 50


def test_a_deep_profile_lifts_its_round_budget_above_the_subagent_default() -> None:
    deep = SubagentProfile(
        name="deep",
        prompt="p",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
        max_rounds=200,
    )
    assert deep.max_rounds == 200 > SUBAGENT_ROUND_LIMIT


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
    for skill in ("sandbox", "delegation"):
        assert skill in prompt
    assert "result" in prompt and "JSON" in prompt


def test_core_ships_only_the_general_purpose_profile() -> None:
    assert {profile.name for profile in CORE_SUBAGENT_PROFILES} == {GENERAL_PURPOSE}


def test_subagent_prompt_wraps_the_profile_with_the_shared_citation_discipline() -> None:
    prompt = subagent_system_prompt(_profile("research"))
    assert "research instructions" in prompt
    assert "<citation_instructions>" in prompt


def test_subagent_prompt_fills_the_skill_index_slot() -> None:
    profile = SubagentProfile(
        name="slotted",
        prompt="do the task\n\n{{skill_index}}",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
    )
    prompt = subagent_system_prompt(profile)
    assert "{{skill_index}}" not in prompt
    assert "<available_skills>" in prompt


def test_subagent_prompt_fails_loud_on_an_unfilled_slot() -> None:
    profile = SubagentProfile(
        name="stray",
        prompt="do it {{mystery}}",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
    )
    with pytest.raises(ValueError, match="unresolved slots: mystery"):
        subagent_system_prompt(profile)


def test_profile_model_defaults_to_none_meaning_inherit_the_parent() -> None:
    assert _profile("a").model is None


def test_general_purpose_inherits_the_parent_model() -> None:
    assert SubagentRegistry(CORE_SUBAGENT_PROFILES).get(GENERAL_PURPOSE).model is None


def test_a_profile_can_pin_a_distinct_model() -> None:
    """The queue resolves `profile.model or agent.model`, so a set model overrides the parent's and
    None falls back — proven end-to-end by the billing test; here the field carries the choice."""
    pinned = SubagentProfile(
        name="pinned",
        prompt="p",
        tool_names=(),
        input_model=_Task,
        output_model=_Finding,
        model="gpt-5.4",
    )
    assert pinned.model == "gpt-5.4"
    assert (pinned.model or "claude-opus-4-8") == "gpt-5.4"
    assert (_profile("a").model or "claude-opus-4-8") == "claude-opus-4-8"
