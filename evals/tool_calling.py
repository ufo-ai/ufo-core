"""Trajectory cases: score the agent's real tool choices, not its prose — required tools in the
right order, restraint on questions it can answer from its own knowledge, and file/shell (not web)
on a local task. No LLM judge, no variance. A `web_dependent` case infra-excludes on a web outage —
the grader checks the agent CALLED the web tool, which holds even when the call errors."""

from ufo_ext_eval_harness.capability import CapabilityCase, Grader
from ufo_ext_eval_harness.scorers import (
    WEB_TOOLS,
    local_fs_scorer,
    required_tools_scorer,
    restraint_scorer,
)

SPECS: list[tuple[str, str, Grader, bool]] = [
    (
        "live-fact",
        "Who currently holds the men's 100m world record? Confirm it from a current source.",
        required_tools_scorer(("search_web", "fetch_url"), (("search_web", "fetch_url"),)),
        True,
    ),
    (
        "research-then-save",
        "Find the current men's marathon world record, then remember it for the team.",
        required_tools_scorer(("search_web", "memory_update"), (("search_web", "memory_update"),)),
        True,
    ),
    (
        "just-remember",
        "The team standup moved to 10am on Mondays - remember that.",
        required_tools_scorer(("memory_update",)),
        False,
    ),
    ("capital-restraint", "What is the capital of France?", restraint_scorer(WEB_TOOLS), False),
    ("math-restraint", "What is 17 multiplied by 23?", restraint_scorer(WEB_TOOLS), False),
    (
        "provided-summary",
        "Summarize the key points of this status update in three bullets:\n\n"
        "'Q3 shipped the billing rework and churn dropped four points. Support volume rose "
        "after the launch, mostly password resets. The mobile app slipped to Q4 pending a "
        "security review.'\n\n"
        "Output only the bullets.",
        restraint_scorer(WEB_TOOLS),
        False,
    ),
    (
        "stale-edit",
        "Here is notes.md as I last saw it:\n# Draft\nbody\n"
        "Change the heading from Draft to Final.",
        local_fs_scorer(),
        False,
    ),
    ("tree-find", "Find every TODO comment under the workspace.", local_fs_scorer(), False),
]

CASES = tuple(
    CapabilityCase(name, brief, grader, web_dependent=web, digest_tag=f"tool:{name}")
    for name, brief, grader, web in SPECS
)
