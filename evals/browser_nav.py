"""Browser navigation cases: score the agent's answer AND that it drove the real browser tools to
get there — `navigate` plus a page-reading tool (`get_page_text`/`read_page`/`find`) — against a
STABLE, well-known page whose content will not drift. A restraint case checks the agent does not
reach for the browser on a question it can answer from its own knowledge. `web_dependent` marks the
navigation cases: a real outage infra-excludes them rather than counting a capability failure."""

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
)
from evals.harness.scorers import combine, exact_scorer, restraint_scorer

BROWSER_TOOLS = (
    "navigate",
    "tabs_context",
    "tabs_create",
    "tabs_close",
    "upload_file",
    "read_page",
    "get_page_text",
    "find",
    "form_input",
    "computer",
    "wait_for_download",
)
PAGE_READ_TOOLS = frozenset({"get_page_text", "read_page", "find"})


async def _browsed(output: CapabilityOutput) -> CapabilityVerdict:
    navigated = [call for call in output.calls if call.name == "navigate" and call.succeeded]
    if not navigated:
        return CapabilityVerdict(False, "navigate did not complete successfully")
    readers = [
        call.name for call in output.calls if call.name in PAGE_READ_TOOLS and call.succeeded
    ]
    if not readers:
        return CapabilityVerdict(False, "no page-reading tool completed successfully")
    return CapabilityVerdict(True, f"browsed: navigate, {', '.join(readers)}")


NAVIGATION_SPECS: list[tuple[str, str, Grader]] = [
    (
        "example-domain-title",
        "Navigate to http://example.com with the browser and report the exact page title. Reply "
        "with a single line 'ANSWER: <title>'.",
        combine(exact_scorer("Example Domain"), _browsed),
    ),
    (
        "rfc2119-title",
        "Navigate to https://www.rfc-editor.org/rfc/rfc2119 with the browser and report the "
        "document's exact title as it appears on the page. Reply with a single line "
        "'ANSWER: <title>'.",
        combine(exact_scorer("Key words for use in RFCs to Indicate Requirement Levels"), _browsed),
    ),
]

CASES = (
    *(
        CapabilityCase(name, brief, grader, web_dependent=True, digest_tag=f"browser:{name}")
        for name, brief, grader in NAVIGATION_SPECS
    ),
    CapabilityCase(
        "gold-symbol-restraint",
        "What is the chemical symbol for gold?",
        restraint_scorer(BROWSER_TOOLS),
        digest_tag="browser:gold-symbol-restraint",
    ),
)
