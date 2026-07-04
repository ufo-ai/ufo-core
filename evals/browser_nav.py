"""Browser navigation cases: score the agent's answer AND that it drove the real browser tools to
get there — `navigate` plus a page-reading tool (`get_page_text`/`read_page`/`find`) — against a
STABLE, well-known page whose content will not drift. A restraint case checks the agent does not
reach for the browser on a question it can answer from its own knowledge. `web_dependent` marks the
navigation cases: a real outage infra-excludes them rather than counting a capability failure."""

from selfhost_ext_eval_harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
)
from selfhost_ext_eval_harness.scorers import combine, exact_scorer, restraint_scorer

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
    if "navigate" not in output.tools:
        return CapabilityVerdict(False, "did not call navigate")
    if not any(tool in PAGE_READ_TOOLS for tool in output.tools):
        return CapabilityVerdict(False, f"did not read the page: {list(output.tools)}")
    return CapabilityVerdict(True, f"browsed: {', '.join(output.tools)}")


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
