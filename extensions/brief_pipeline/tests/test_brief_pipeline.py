"""The brief-pipeline seam: three typed stages register beside core's profiles, each stage's
output schema reaches its system prompt, and the skill that drives the chain names every stage —
so renaming a profile or its payload fields breaks loudly here, not in a member's chat."""

import pytest
from pydantic import ValidationError
from selfhost_ext_brief_pipeline.manifest import manifest
from selfhost_ext_brief_pipeline.pipeline import (
    CRITIC_PROFILE,
    DRAFT_PROFILE,
    OUTLINE_PROFILE,
    BriefCritique,
    BriefRequest,
    DraftRequest,
)

from selfhost.ext.loader import skill_registry, turn_subagents
from selfhost.loop.profiles import CORE_SUBAGENT_PROFILES
from selfhost.loop.subagents import SubagentRegistry, subagent_system_prompt


def test_profiles_register_beside_core_without_collision() -> None:
    registry = SubagentRegistry(CORE_SUBAGENT_PROFILES + turn_subagents((manifest(),)))
    for profile in (OUTLINE_PROFILE, DRAFT_PROFILE, CRITIC_PROFILE):
        assert registry.get(profile.name) is profile


def test_stages_are_toolless_and_cannot_spawn() -> None:
    for profile in manifest().subagents:
        assert profile.tool_names == ()


def test_each_stage_prompt_carries_its_output_schema() -> None:
    assert "outline" in subagent_system_prompt(OUTLINE_PROFILE)
    assert "draft" in subagent_system_prompt(DRAFT_PROFILE)
    assert "verdict" in subagent_system_prompt(CRITIC_PROFILE)


def test_typed_payloads_validate_and_reject_missing_fields() -> None:
    assert BriefRequest(topic="q3 roadmap").audience == "the team"
    assert BriefCritique(verdict="ship").improvements == ""
    with pytest.raises(ValidationError):
        DraftRequest(topic="q3 roadmap")


def test_skill_indexes_and_names_every_stage() -> None:
    registry = skill_registry((manifest(),))
    assert "brief-pipeline" in dict(registry.index())
    instructions = registry.named("brief-pipeline").instructions
    for profile in manifest().subagents:
        assert profile.name in instructions
