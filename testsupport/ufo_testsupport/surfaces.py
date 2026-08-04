"""Inert dependencies for tests that mount surfaces without exercising the skills view or the
subagent roster — `_mount_shared_surfaces` requires a skill registry and a subagent registry, so a
test that reads neither passes something real and empty."""

from ufo.loop.subagents import SubagentRegistry
from ufo.skills.runtime import RuntimeSkill, SkillRegistry

EMPTY_SKILL_REGISTRY = SkillRegistry({})
NO_SUBAGENTS = SubagentRegistry(())


async def no_user_skills() -> tuple[RuntimeSkill, ...]:
    return ()
