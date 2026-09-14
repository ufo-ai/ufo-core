"""GitHub reads through the sandbox CLI. A member's question about a repository is one `gh` call
in bash: `GH_TOKEN` already authenticates it as the connected GitHub account, and the search API
answers a count outright. The connector route to the same answer is a discovery call, a describe
call, and a search tool whose results cap at a hundred and page by hand — the trajectory that took
28 model rounds and 102 seconds to count one week of closed pull requests.

Every case grades the answer and the trajectory together: the expected counts appear in the reply,
no connector tool is called, the turn spends at most `MAX_TOOL_CALLS` calls, and a bash command that
succeeded invoked `gh`. A pass is a fast trajectory, not merely a correct one. The fixed windows are
closed history, so their counts do not move; the verbatim ask is the live one and grades the
trajectory and the presence of a count.

The suite runs against a workspace whose speaking member holds a connected GitHub account that
reaches `metalcraftai/ufo`, so it is explicit-only: a fresh workspace has no such account."""

import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
)
from evals.harness.scorers import combine, restraint_scorer

CONNECTOR_TOOLS = ("list_external_tools", "describe_external_tools", "call_external_tool")
MAX_TOOL_CALLS = 3
COUNT_TOLERANCE = 0.01
REPO = "metalcraftai/ufo"
WINDOW = "between 2026-09-01T00:00Z and 2026-09-07T23:59:59Z"
CLOSED_IN_WINDOW = 312
MERGED_IN_WINDOW = 302
MERGED_BY_ALEXG_IN_WINDOW = 129
GH_WORD = re.compile(r"(?:^|[\s;&|(`$])gh\s")
INTEGER = re.compile(r"(?<![\w.])\d+(?![\w.])")


def gh_in_bash_scorer() -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        for call in output.calls:
            if call.name != "bash" or not call.succeeded:
                continue
            if GH_WORD.search(str(call.input.get("command", ""))):
                return CapabilityVerdict(True, "a successful bash call invoked gh")
        return CapabilityVerdict(False, "no successful bash call invoked gh")

    return DescribedGrader("a bash call that succeeded invokes `gh`", grade)


def tool_budget_scorer(limit: int) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        used = len(output.calls)
        if used > limit:
            return CapabilityVerdict(False, f"{used} tool calls, budget {limit}")
        return CapabilityVerdict(True, f"{used} tool calls")

    return DescribedGrader(f"at most {limit} tool calls", grade)


def counts_scorer(expected: tuple[int, ...]) -> Grader:
    """Every expected count appears in the reply as an integer within `COUNT_TOLERANCE`; an empty
    `expected` requires at least one integer, for an ask whose live count the grader cannot pin."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        found = [int(token) for token in INTEGER.findall(output.response.replace(",", ""))]
        if not expected:
            if found:
                return CapabilityVerdict(True, f"counts in reply: {found}")
            return CapabilityVerdict(False, "no count in the reply")
        missing = [
            value
            for value in expected
            if not any(
                abs(number - value) <= max(1, round(value * COUNT_TOLERANCE)) for number in found
            )
        ]
        if missing:
            return CapabilityVerdict(False, f"reply lacks {missing}; integers present: {found}")
        return CapabilityVerdict(True, f"reply carries {list(expected)}")

    counts = ", ".join(str(value) for value in expected)
    statement = (
        f"the reply carries {counts} within ±{COUNT_TOLERANCE:.0%}"
        if expected
        else "the reply carries a count"
    )
    return DescribedGrader(statement, grade)


def fast_gh_read(expected: tuple[int, ...]) -> Grader:
    return combine(
        counts_scorer(expected),
        restraint_scorer(CONNECTOR_TOOLS),
        tool_budget_scorer(MAX_TOOL_CALLS),
        gh_in_bash_scorer(),
    )


CASES = (
    CapabilityCase(
        "prs-closed-this-week",
        "how many prs closed this week",
        fast_gh_read(()),
        web_dependent=True,
        digest_tag="github-reads:prs-closed-this-week",
    ),
    CapabilityCase(
        "prs-closed-fixed-window",
        f"How many pull requests in {REPO} were closed {WINDOW}, and how many of those were "
        "merged?",
        fast_gh_read((CLOSED_IN_WINDOW, MERGED_IN_WINDOW)),
        web_dependent=True,
        digest_tag="github-reads:prs-closed-fixed-window",
    ),
    CapabilityCase(
        "prs-merged-by-author",
        f"How many pull requests authored by alexg-ufo were merged in {REPO} {WINDOW}?",
        fast_gh_read((MERGED_BY_ALEXG_IN_WINDOW,)),
        web_dependent=True,
        digest_tag="github-reads:prs-merged-by-author",
    ),
)
