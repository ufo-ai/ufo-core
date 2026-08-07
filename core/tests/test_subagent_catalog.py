"""The generated subagent-catalog skill reads the same registry a spawn dispatches against —
both-ends for docs: every registered profile appears with its payload keys, so an agent that loads
the catalog has the profile names before its first call."""

from pydantic import BaseModel

from ufo.ext.loader import skill_registry
from ufo.ext.manifest import SubagentProfile
from ufo.loop.profiles import CORE_SUBAGENT_PROFILES
from ufo.loop.subagent_catalog import (
    SUBAGENT_CATALOG_DESCRIPTION,
    SUBAGENT_CATALOG_SKILL_NAME,
    subagent_catalog_skill,
)
from ufo.loop.subagents import SubagentRegistry


class _Payload(BaseModel):
    question: str
    depth: int = 1


def _profile(name: str) -> SubagentProfile:
    return SubagentProfile(
        name=name, prompt="p", tool_names=(), input_model=_Payload, output_model=_Payload
    )


def test_catalog_lists_every_profile_and_marks_optional_payload_keys() -> None:
    registry = SubagentRegistry((_profile("scout"), *CORE_SUBAGENT_PROFILES))
    skill = subagent_catalog_skill(registry)

    assert skill.name == SUBAGENT_CATALOG_SKILL_NAME
    for profile in registry.profiles:
        assert f"`{profile.name}`" in skill.instructions
    assert "`question`" in skill.instructions
    assert "`depth` (optional)" in skill.instructions


def test_the_catalog_stands_on_its_own_in_the_index() -> None:
    """A folder skill cannot `depends` on a boot-generated one — `CORE_SKILL_REGISTRY` resolves at
    import, before the registry exists. So the catalog carries its own index entry and closes over
    nothing, which is what makes it reachable by name."""
    registry = skill_registry(
        (), (subagent_catalog_skill(SubagentRegistry(CORE_SUBAGENT_PROFILES)),)
    )
    assert [s.skill.name for s in registry.closure(SUBAGENT_CATALOG_SKILL_NAME)] == [
        SUBAGENT_CATALOG_SKILL_NAME
    ]
    assert (SUBAGENT_CATALOG_SKILL_NAME, SUBAGENT_CATALOG_DESCRIPTION) in registry.index()
