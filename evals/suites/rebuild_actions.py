"""The derived rebuilds as collection actions: the report collection's `rebuild_report_digest` and
the page collection's `rebuild_page_facts` are whole-collection acts an admin asks for when every
entry or every derived fact reads badly, and neither is the answer to one wrong row. A rebuild marks
the workspace's derived state due, so the suite runs serially. The cases are authored — no
production turn has invoked the moved actions yet — and each brief avoids the action's name."""

from evals.harness.capability import CapabilityCase
from evals.harness.scorers import attempted_tools_scorer, restraint_scorer

REBUILD_REPORT_DIGEST = "action:report:rebuild_report_digest"
REBUILD_PAGE_FACTS = "action:page:rebuild_page_facts"

CASES = (
    CapabilityCase(
        "authored-rebuild-report-entries",
        "The entries under every report on the radar read badly this week — the summaries miss "
        "the point. Write all of them again.",
        attempted_tools_scorer(
            required=((REBUILD_REPORT_DIGEST, {}),),
            forbidden=(REBUILD_PAGE_FACTS,),
            orderings=(),
        ),
        digest_tag="rebuild-actions:rebuild-entries:authored",
    ),
    CapabilityCase(
        "authored-rebuild-page-facts",
        "The facts the wiki derived from our synced documents are wrong across the board. "
        "Regenerate all of them from the pages.",
        attempted_tools_scorer(
            required=((REBUILD_PAGE_FACTS, {}),),
            forbidden=(REBUILD_REPORT_DIGEST,),
            orderings=(),
        ),
        digest_tag="rebuild-actions:rebuild-page-facts:authored",
    ),
    CapabilityCase(
        "authored-one-wrong-fact-is-not-a-rebuild",
        "The wiki says our launch is on March 1. It is March 3 — correct that one fact.",
        restraint_scorer((REBUILD_PAGE_FACTS, REBUILD_REPORT_DIGEST)),
        digest_tag="rebuild-actions:one-row-correction:authored",
    ),
)
