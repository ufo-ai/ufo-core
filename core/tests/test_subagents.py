import pytest
from pydantic import BaseModel

from selfhost.ext.manifest import SubagentProfile
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt


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
