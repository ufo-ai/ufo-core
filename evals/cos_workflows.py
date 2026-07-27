"""Chief-of-staff routing cases: the skill loads and reaches for memory_search to place people.

The pack syncs a member's own state repo, so these cases grade routing rather than content.
Each distractor is a neighboring skill, never a dependency that loads with the requested skill.
"""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import combine, required_tools_scorer, skill_scorer

COS_WORKFLOWS_PACKS = ("chief_of_staff",)
MEMORY_SEARCH = ("memory_search",)

CASES = (
    CapabilityCase(
        "prep-one-on-one",
        "Prep me for my 1:1 with Priya Raman.",
        combine(skill_scorer("prep", "sync"), required_tools_scorer(MEMORY_SEARCH)),
        digest_tag="cos-workflow:prep-one-on-one",
    ),
    CapabilityCase(
        "triage-weigh-signal",
        "Two people flagged the deploy pain this week — how much weight should that carry?",
        combine(skill_scorer("triage", "prep"), required_tools_scorer(MEMORY_SEARCH)),
        digest_tag="cos-workflow:triage-weigh-signal",
    ),
    CapabilityCase(
        "sync-place-people",
        "Run my sync.",
        combine(skill_scorer("sync", "prep"), required_tools_scorer(MEMORY_SEARCH)),
        digest_tag="cos-workflow:sync-place-people",
    ),
)
