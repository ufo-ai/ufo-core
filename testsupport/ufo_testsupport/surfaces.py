"""Inert skill dependencies for tests that mount surfaces without exercising the skills view —
`SurfaceContext` requires both, so a test that never reads them passes something real and empty."""

from ufo.skills.runtime import RuntimeSkill, SkillRegistry

EMPTY_SKILL_REGISTRY = SkillRegistry({})


async def no_user_skills() -> tuple[RuntimeSkill, ...]:
    return ()
