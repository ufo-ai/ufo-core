"""Report Digest Skill behavior over one already-published report carried in the turn: the report
is the whole of the input, so a case collects nothing and calls no tool. The reply is pinned to
labelled lines, which is what lets the deterministic grader count the points, read every actor
back, hold each field to its stated budget, and see whether the title spent itself on the genre
and date the reader is already shown."""

import re

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import Predicate, predicate_scorer

MAX_POINTS = 2
MAX_TITLE_WORDS = 10
MAX_SUMMARY_WORDS = 15
MAX_POINT_WORDS = 10

REPLY_SHAPE = (
    "Reply with exactly these lines and nothing else: one 'Title: <line>', one "
    "'Summary: <line>', and a 'Point: <line> | Actor: <person>' line for each point your entry "
    "carries, leaving the actor empty when the report names no person for that line. The title "
    f"takes {MAX_TITLE_WORDS} words or fewer, the summary {MAX_SUMMARY_WORDS} or fewer, and each "
    f"point {MAX_POINT_WORDS} or fewer; the entry carries at most {MAX_POINTS} points."
)
POINT_LINE = re.compile(r"^\s*point:\s*(.*?)\s*(?:\|\s*actor:\s*(.*))?$", re.IGNORECASE)
MARKDOWN_LINK = re.compile(r"\[[^\]]*\]\([^)]*\)")


def _points(text: str) -> tuple[re.Match[str], ...]:
    return tuple(match for match in map(POINT_LINE.match, text.splitlines()) if match)


def _actors(text: str) -> tuple[str, ...]:
    return tuple((match.group(2) or "").strip() for match in _points(text))


def _labelled(text: str, label: str) -> tuple[str, ...]:
    prefix = f"{label.lower()}:"
    return tuple(
        line.strip()[len(prefix) :].strip()
        for line in text.splitlines()
        if line.strip().lower().startswith(prefix)
    )


def _bounds(genre: str) -> tuple[Predicate, ...]:
    return (
        (
            "exactly one Title line and one Summary line",
            lambda text: (
                len(_labelled(text, "Title")) == 1 and len(_labelled(text, "Summary")) == 1
            ),
        ),
        (f"at most {MAX_POINTS} points", lambda text: len(_points(text)) <= MAX_POINTS),
        (
            f"a title of {MAX_TITLE_WORDS} words or fewer",
            lambda text: all(
                len(line.split()) <= MAX_TITLE_WORDS for line in _labelled(text, "Title")
            ),
        ),
        (
            f"a summary of {MAX_SUMMARY_WORDS} words or fewer",
            lambda text: all(
                len(line.split()) <= MAX_SUMMARY_WORDS for line in _labelled(text, "Summary")
            ),
        ),
        (
            f"points of {MAX_POINT_WORDS} words or fewer",
            lambda text: all(
                len(match.group(1).split()) <= MAX_POINT_WORDS for match in _points(text)
            ),
        ),
        ("no markdown link syntax", lambda text: not MARKDOWN_LINK.search(text)),
        (
            f"a title that does not echo {genre!r}",
            lambda text: (
                not any(genre.lower() in line.lower() for line in _labelled(text, "Title"))
            ),
        ),
    )


WEEKLY_GENRE = "Platform Engineering Weekly"
WEEKLY_PEOPLE = ("Dana Okafor", "Marco Ruiz", "Ines Bertrand")
WEEKLY_REPORT = (
    "Platform Engineering Weekly — 14 August 2026\n"
    "- Deploy queue drained: median merge-to-production fell from 41 minutes to 19 once the "
    "build cache landed (Dana Okafor).\n"
    "- The staging certificate expires 28 August and the renewal is blocked on a vendor ticket "
    "nobody has answered (Marco Ruiz).\n"
    "- Search reindex now finishes in 12 minutes, down from 50, after the writes were batched "
    "(Ines Bertrand).\n"
    "- Headcount is unchanged this week."
)

MARKET_GENRE = "Competitive Intelligence"
MARKET_REPORT = (
    "Competitive Intelligence — week of 14 August 2026\n"
    "- Northwind now ships its coding agent free on every paid tier "
    "([announcement](https://northwind.example/blog/agent)).\n"
    "- OneCLI dropped its editor to sell the harness alone and cut its list price 40 percent "
    "([pricing](https://onecli.example/pricing)).\n"
    "- Halcyon raised $80M at a $1.2B valuation ([filing](https://halcyon.example/press)).\n"
    "- Two smaller vendors merged. Terms were not disclosed."
)

CASES = (
    CapabilityCase(
        "report-digest-attributed-findings",
        "Write the digest entry for this report. It is already published and pasted below — do "
        "not open it again, and do not write a report of your own. I run the platform team.\n\n"
        f"{WEEKLY_REPORT}\n\n{REPLY_SHAPE}",
        predicate_scorer(
            (
                *_bounds(WEEKLY_GENRE),
                (
                    "every actor is a person the report attributed",
                    lambda text: set(filter(None, _actors(text))) <= set(WEEKLY_PEOPLE),
                ),
                (
                    "at least two of the attributed people are named",
                    lambda text: sum(person in text for person in WEEKLY_PEOPLE) >= 2,
                ),
            )
        ),
        digest_tag="skill:report-digest-attributed-findings",
        rubric=(
            "The title names the findings rather than the report's genre and date.",
            "The expiring staging certificate leads the points, ahead of the larger but inert "
            "speedups.",
            "Each point carries the magnitude the report stated and adds no explanation the "
            "report did not.",
            "A name in an actor is taken out of that point's text rather than written twice.",
        ),
    ),
    CapabilityCase(
        "report-digest-outside-companies",
        "Write the digest entry for this report. It is already published and pasted below — do "
        "not open it again, and do not write a report of your own. I own our pricing and "
        f"packaging.\n\n{MARKET_REPORT}\n\n{REPLY_SHAPE}",
        predicate_scorer(
            (
                *_bounds(MARKET_GENRE),
                ("no point names an actor", lambda text: not any(_actors(text))),
            )
        ),
        digest_tag="skill:report-digest-outside-companies",
        rubric=(
            "The title names what the companies did rather than that the report is competitive "
            "intelligence.",
            "No person is named anywhere in the entry, and no company stands in for one.",
            "The OneCLI price cut is ranked for a reader who owns pricing.",
            "Every claim traces to the report and the entry says no more than it does.",
        ),
    ),
)
