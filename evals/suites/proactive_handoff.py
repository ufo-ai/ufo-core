"""Handle cases: the reply carries the thing the member acts on next.

A turn that reads a meeting, a record, or a request holds the handle for the member's next step —
the join link, the record link, the address. The register requires that handle on the line that
reports the result, so the member acts from the reply instead of asking again for something the
turn already read.

Grading is deterministic on the delivered text. A case that seeds a handle requires that exact
string. Every case also fails a URL the seeded material never carried, so the suite cannot be
bought by writing a plausible link, and the one-handle case fails the reply that answers about one
meeting and pastes the links of the other two. One case seeds material with no handle at all: the
pass there is the result reported without a link, and a rubric reads whether the reply says the
calendar carries none.

Cases carry `samples=1`: the metric is the rate at which one real turn hands the member their next
step."""

import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    WorkspaceFile,
)
from evals.harness.harness import JsonObject

URL_RE = re.compile(r"https?://[^\s<>()\[\]\"']+", re.IGNORECASE)

DESIGN_REVIEW_LINK = "https://meet.example.com/dsn-rvw-441"
ROADMAP_LINK = "https://meet.example.com/rdm-plan-208"
HIRING_LINK = "https://meet.example.com/hir-sync-975"
INVOICE_RECORD = "https://records.example.com/invoices/INV-20418"

CALENDAR = f"""# Team calendar, week of 12 October

## Tuesday
- 09:30 Roadmap planning, 45 minutes, Priya and Sam. Join: {ROADMAP_LINK}

## Wednesday
- 15:00 Design review, 60 minutes, the whole product team. Join: {DESIGN_REVIEW_LINK}
- 17:00 Hiring sync, 30 minutes, Dana and Lee. Join: {HIRING_LINK}
""".encode()

CALENDAR_WITHOUT_LINKS = b"""# Team calendar, week of 12 October

## Tuesday
- 09:30 Roadmap planning, 45 minutes, Priya and Sam. Room 2B.

## Wednesday
- 15:00 Design review, 60 minutes, the whole product team. Room 4A.
- 17:00 Hiring sync, 30 minutes, Dana and Lee. Room 2B.
"""

INVOICE_RECORDS = f"""# Billing records, October

- INV-20418, Northwind Traders, 4,200 USD, unpaid, due 24 October. Record: {INVOICE_RECORD}
- INV-20417, Contoso, 1,150 USD, paid 3 October. Record: https://records.example.com/invoices/INV-20417
""".encode()


def handle_scorer(
    required: tuple[str, ...],
    seeded: tuple[str, ...],
    max_words: int,
) -> Grader:
    """The delivered reply carries every required handle, no URL the seeded material never held,
    and no seeded handle the question did not ask about."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = output.response
        words = len(text.split())
        found = tuple(match.group(0).rstrip(".,;:!?)") for match in URL_RE.finditer(text))
        missing = tuple(handle for handle in required if handle not in text)
        invented = tuple(url for url in found if url not in seeded)
        unasked = tuple(url for url in found if url in seeded and url not in required)
        failures = []
        if missing:
            failures.append("missing the handle the member acts on: " + ", ".join(missing))
        if invented:
            failures.append("links the seeded material never carried: " + ", ".join(invented))
        if unasked:
            failures.append("pastes handles the question did not ask about: " + ", ".join(unasked))
        if words > max_words:
            failures.append(f"{words} words over the {max_words} budget")
        evidence: JsonObject = {"words": words, "urls": list(found)}
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), evidence)
        return CapabilityVerdict(True, f"carried {len(required)} handle(s)", evidence)

    return DescribedGrader(
        f"a reply of at most {max_words} words carrying "
        + (
            "exactly the handle the member acts on next and no other link: " + ", ".join(required)
            if required
            else "no link at all, because the material carried none"
        ),
        grade,
    )


CASES = (
    CapabilityCase(
        "meeting-answer-carries-the-join-link",
        "When is the design review this week?",
        handle_scorer(
            required=(DESIGN_REVIEW_LINK,),
            seeded=(DESIGN_REVIEW_LINK, ROADMAP_LINK, HIRING_LINK),
            max_words=60,
        ),
        samples=1,
        digest_tag="proactive-handoff:meeting-answer-carries-the-join-link",
        workspace_files=(WorkspaceFile("team-calendar.md", CALENDAR),),
        rubric=(
            "The reply gives the day and time of the design review and offers the join link "
            "without the member asking for it.",
        ),
    ),
    CapabilityCase(
        "record-answer-carries-the-record-link",
        "How much does Northwind still owe us on that October invoice?",
        handle_scorer(
            required=(INVOICE_RECORD,),
            seeded=(INVOICE_RECORD, "https://records.example.com/invoices/INV-20417"),
            max_words=60,
        ),
        samples=1,
        digest_tag="proactive-handoff:record-answer-carries-the-record-link",
        workspace_files=(WorkspaceFile("billing-records.md", INVOICE_RECORDS),),
        rubric=("The reply states the unpaid amount and links the invoice record it read.",),
    ),
    CapabilityCase(
        "one-handle-not-every-handle",
        "What time is the hiring sync?",
        handle_scorer(
            required=(HIRING_LINK,),
            seeded=(DESIGN_REVIEW_LINK, ROADMAP_LINK, HIRING_LINK),
            max_words=60,
        ),
        samples=1,
        digest_tag="proactive-handoff:one-handle-not-every-handle",
        workspace_files=(WorkspaceFile("team-calendar.md", CALENDAR),),
        rubric=(
            "The reply answers about the hiring sync alone and does not report the other meetings.",
        ),
    ),
    CapabilityCase(
        "no-handle-in-the-material-invents-none",
        "When is the design review this week, and how do I join it?",
        handle_scorer(
            required=(),
            seeded=(),
            max_words=60,
        ),
        samples=1,
        digest_tag="proactive-handoff:no-handle-in-the-material-invents-none",
        workspace_files=(WorkspaceFile("team-calendar.md", CALENDAR_WITHOUT_LINKS),),
        rubric=(
            "The reply gives the day and time of the design review, says the calendar carries no "
            "join link, and gives the room the calendar names.",
        ),
    ),
)
