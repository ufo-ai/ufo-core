"""Web research cases: score both the agent's answer AND its trajectory — each question needs a
live web lookup (the research pack's `search_web`/`fetch_url`) and has a stable, permanently-
checkable answer, so grading the answer never goes stale even though the fact must be looked up.
`web_dependent` marks every case: a real outage infra-excludes it rather than counting a failure."""

from evals.harness.capability import CapabilityCase, Grader
from evals.harness.scorers import (
    combine,
    exact_scorer,
    numeric_scorer,
    required_tools_scorer,
)

SPECS: list[tuple[str, str, Grader]] = [
    (
        "psf-founding-year",
        "Search the web to confirm the year the Python Software Foundation was founded, then "
        "reply with a single line 'ANSWER: <year>'.",
        combine(
            numeric_scorer(2001),
            required_tools_scorer(("search_web",)),
        ),
    ),
    (
        "rfc2119-fetch",
        "Fetch https://www.rfc-editor.org/rfc/rfc2119 and report the capitalized keyword RFC 2119 "
        "defines for an absolute requirement. Reply with a single line 'ANSWER: <word>'.",
        combine(
            exact_scorer("MUST"),
            required_tools_scorer(("fetch_url",)),
        ),
    ),
    (
        "pep8-line-length",
        "Search the web to find Python's official style guide for code (PEP 8), then fetch it "
        "from peps.python.org and report the maximum recommended line length in characters from "
        "its 'Maximum Line Length' section. Reply with a single line 'ANSWER: <number>'.",
        combine(
            numeric_scorer(79),
            required_tools_scorer(("search_web", "fetch_url"), (("search_web", "fetch_url"),)),
        ),
    ),
]

CASES = tuple(
    CapabilityCase(name, brief, grader, web_dependent=True, digest_tag=f"web:{name}")
    for name, brief, grader in SPECS
)
