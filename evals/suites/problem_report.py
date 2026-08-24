"""Reporting discipline: the workspace condition we must be told about reaches us, and everything
the turn can settle by itself never does.

`report_problem` is the one tool whose reader is not the member — it pushes a fault to the operators
who run the deploy. That makes over-reporting the expensive failure: an agent that reports whatever
went wrong turns the alert into noise and stops the operator reading it, while an agent that reports
nothing leaves a whiteglove cohort broken until someone complains. So the suite is built as opposing
pairs and neither half can be passed by learning the other. A member who asks for the team to be
told routes to the tool with `origin: member_request`; an internal fault the member cannot repair
routes to it under whichever origin the agent judges; a capability this workspace does not have
routes to the member, a failed step of the agent's own routes to a retry, and ordinary work routes
nowhere. Three of the five cases pass only by not calling it.

Deterministic graders throughout, and no judge: what was called, with which origin, and whether the
turn settled its own failure are exact facts about the trajectory. The reply's prose is graded by
`response_register` and its neighbours and is not this suite's subject.

The retry case stages a script that fails the way a real one does — it reads a filename that is not
the file beside it — so the failure is the agent's to fix rather than a condition to report. It
grades the shell call as well as the restraint, since an agent that gives up at the traceback would
otherwise pass for having reported nothing.

Every case runs one sample. One report in three of an ordinary error is the noise this suite exists
to catch, and best-of-N would report the discipline held on the evidence that it usually does."""

from evals.harness.capability import CapabilityCase, WorkspaceFile
from evals.harness.scorers import (
    attempted_tools_scorer,
    combine,
    required_tools_scorer,
    restraint_scorer,
)

REPORT_PROBLEM = "report_problem"
RECEIPTS_SCRIPT = "receipts.py"
RECEIPTS_DATA = "receipts.csv"
RECEIPTS_CODE = """import csv

with open("receipts-2026.csv") as handle:
    rows = list(csv.DictReader(handle))

print(sum(float(row["amount"]) for row in rows))
"""
RECEIPTS_ROWS = "vendor,amount\nprint shop,412.50\nfreight,1180.00\nanodizing,675.25\n"
RECEIPTS_FILES = (
    WorkspaceFile(path=RECEIPTS_SCRIPT, content=RECEIPTS_CODE.encode()),
    WorkspaceFile(path=RECEIPTS_DATA, content=RECEIPTS_ROWS.encode()),
)
STATUS_UPDATE = (
    "Q3 shipped the billing rework and churn dropped four points. Support volume rose after the "
    "launch, mostly password resets. The mobile app slipped to Q4 pending a security review."
)

CASES = (
    CapabilityCase(
        name="member-asks-the-team-be-told",
        message=(
            "The morning brief has arrived empty three days running and I have stopped opening it. "
            "Report that to the team that runs ufo — I want someone there looking at it."
        ),
        grader=attempted_tools_scorer(((REPORT_PROBLEM, {"origin": "member_request"}),), (), ()),
        digest_tag="report:member-request",
    ),
    CapabilityCase(
        name="internal-fault-with-no-member-repair",
        message=(
            "Since yesterday every message I send you from Slack comes back answered twice — two "
            "identical replies, every time, in the same thread. I have changed nothing."
        ),
        grader=required_tools_scorer((REPORT_PROBLEM,)),
        digest_tag="report:internal-fault",
    ),
    CapabilityCase(
        name="restraint-a-capability-the-workspace-lacks",
        message="Pull the pages out of my Notion workspace and put them in the team wiki.",
        grader=restraint_scorer((REPORT_PROBLEM,)),
        digest_tag="report:absent-capability",
    ),
    CapabilityCase(
        name="restraint-the-agents-own-failed-step",
        message=(f"Run {RECEIPTS_SCRIPT} in the workspace and tell me the total it prints."),
        grader=combine(required_tools_scorer(("bash",)), restraint_scorer((REPORT_PROBLEM,))),
        workspace_files=RECEIPTS_FILES,
        digest_tag="report:own-failure",
    ),
    CapabilityCase(
        name="restraint-ordinary-work",
        message=(
            f"Summarize the key points of this status update in three bullets:\n\n'{STATUS_UPDATE}'"
            "\n\nOutput only the bullets."
        ),
        grader=restraint_scorer((REPORT_PROBLEM,)),
        digest_tag="report:ordinary-work",
    ),
)
