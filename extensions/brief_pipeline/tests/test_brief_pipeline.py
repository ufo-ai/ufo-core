"""The brief-pipeline seam: three typed stages register beside core's profiles, each stage's
output schema reaches its system prompt, and the skill that drives the chain names every stage —
so renaming a profile or its payload fields breaks loudly here, not in a member's chat."""

import pytest
from pydantic import ValidationError
from ufo_ext_brief_pipeline.manifest import manifest
from ufo_ext_brief_pipeline.pipeline import (
    CRITIC_PROFILE,
    DRAFT_PROFILE,
    OUTLINE_PROFILE,
    BriefCritique,
    BriefRequest,
    DraftRequest,
)

from ufo.host.ext.loader import turn_subagents
from ufo.runtime.profiles import CORE_SUBAGENT_PROFILES
from ufo.runtime.subagents import SubagentRegistry


def test_profiles_register_beside_core_without_collision() -> None:
    registry = SubagentRegistry(CORE_SUBAGENT_PROFILES + turn_subagents((manifest(),)))
    for profile in (OUTLINE_PROFILE, DRAFT_PROFILE, CRITIC_PROFILE):
        assert registry.get(profile.name) is profile


def test_stages_are_toolless_and_cannot_spawn() -> None:
    for profile in manifest().subagents:
        assert profile.tool_names == ()


def test_typed_payloads_validate_and_reject_missing_fields() -> None:
    assert BriefRequest(topic="q3 roadmap").audience == "the team"
    assert BriefCritique(verdict="ship").improvements == ""
    with pytest.raises(ValidationError):
        DraftRequest(topic="q3 roadmap")
