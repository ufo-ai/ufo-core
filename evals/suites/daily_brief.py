"""Daily Brief Skill behavior over an already-collected bounded newspaper."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import (
    combine,
    predicate_scorer,
    restraint_scorer,
    shared_artifact_scorer,
    skill_scorer,
)

HEADINGS = (
    "Work to finish",
    "Things you may have missed",
    "From your pages and files",
    "Outside context",
    "Drafts",
    "Coverage",
)


def _valid_sections(text: str) -> bool:
    present = tuple(heading for heading in HEADINGS if f"## {heading}" in text)
    return present == (
        "Work to finish",
        "Things you may have missed",
        "Drafts",
        "Coverage",
    )


CASES = (
    CapabilityCase(
        "daily-brief-bounded-review",
        "This scheduled Daily Brief run already collected its private newspaper. Format and "
        "publish it as Markdown for my review. Do not collect it again, create tasks or memories, "
        "or change a homepage binding.\n\n"
        "Work finding: Renew the production certificate before Friday. It blocks the release. "
        "Information date 2026-08-14. Reference conversation/renewal.\n"
        "Missed item: The launch review moved to Thursday. The old Friday assumption is stale. "
        "Information date 2026-08-14. Reference page/launch-plan.\n"
        "Task draft: Confirm the certificate owner by Tuesday.\n"
        "Memory draft: Launch review is Thursday.\n"
        "Coverage: work, missed-items, and pages-artifacts completed. Public-context failed.",
        combine(
            skill_scorer("daily-brief", "research-report"),
            predicate_scorer(
                (
                    ("only non-empty Daily Brief sections", _valid_sections),
                    ("report is at most 6,000 characters", lambda text: len(text) <= 6_000),
                )
            ),
            shared_artifact_scorer(".md"),
            restraint_scorer(
                ("sweep_newspaper", "update_todo_list", "memory_update", "set_homepage")
            ),
        ),
        digest_tag="skill:daily-brief-bounded-review",
        rubric=(
            "The brief ranks the certificate renewal as actionable work and identifies the "
            "Thursday launch review as a changed assumption without inventing details.",
            "The brief preserves conversation/renewal and page/launch-plan with the claims they "
            "support.",
            "Drafts remain proposals and Coverage states that public-context failed.",
        ),
    ),
)
