"""The tail of the member catalog: a saved skill that neither the prompt index nor the saved-skills
block shows is reached through the skill kind's search action before it is loaded. The seed writes
skill_loading's stress corpus around the target through the real member save path and the cleanup
forgets it again — left standing, the corpus would ride every later turn's saved-skills block — so
the task runs exclusive, exactly as skill_loading's seeding suites do. The case is authored; the
brief is skill_loading's own tail brief."""

from uuid import UUID

from ufo_ext_skill_create.manifest import NAME as SKILL_CREATE

from evals.harness.capability import CapabilityCase, CapabilitySeed
from evals.harness.scorers import combine, required_tools_scorer, skill_scorer
from evals.skill_loading.member import CLEANUP_NOTE, RETENTION_HOLDS, STRESS_SPREAD
from evals.skill_loading.runner import forget_workspace_skills, seed_member_skills
from ufo.agent_scope import agent
from ufo.blob import BlobStore
from ufo.ext.context import context_for
from ufo.ext.loader import load_manifests, skill_registry

SKILL_SEARCH_ACTION = "action:skill:skill_search"
LOAD_TOOL = "load_skill"


def _tail_corpus() -> CapabilitySeed:
    async def seed(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        loadable = frozenset(skill_registry(load_manifests()).by_name)
        with agent(agent_id):
            await forget_workspace_skills()
            await seed_member_skills(
                context_for(SKILL_CREATE, frozenset()), (*STRESS_SPREAD, RETENTION_HOLDS), loadable
            )

    return seed


def _forget_corpus() -> CapabilitySeed:
    async def cleanup(_workspace_id: UUID, agent_id: UUID, _blob: BlobStore) -> None:
        with agent(agent_id):
            await forget_workspace_skills()

    return cleanup


CASES = (
    CapabilityCase(
        "tail-skill-search",
        "Before we clear out old data, run the review described in notes/data-cleanup.md and "
        "tell me what we may delete.",
        combine(
            required_tools_scorer(
                (SKILL_SEARCH_ACTION, LOAD_TOOL), ((SKILL_SEARCH_ACTION, LOAD_TOOL),)
            ),
            skill_scorer(RETENTION_HOLDS.name, "sandbox"),
        ),
        workspace_files=(CLEANUP_NOTE,),
        seed=_tail_corpus(),
        cleanup=_forget_corpus(),
        digest_tag="skill:tail-skill-search:authored",
    ),
)
