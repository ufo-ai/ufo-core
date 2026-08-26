"""Reporting discipline: the workspace condition we must be told about reaches us, classified the
way the board reads it, and everything the turn can settle by itself never does.

`report_problem` is the one tool whose reader is not the member — it pushes a fault to the engineers
who run the deploy. That makes over-reporting the expensive failure: an agent that reports whatever
went wrong turns the board into noise and stops the engineer reading it, while an agent that reports
nothing leaves a whiteglove cohort broken until someone complains. So the suite is built as opposing
pairs and neither half can be passed by learning the other. A member who asks for the team to be
told routes to the tool with `origin: member_request`; an internal fault the member cannot repair
routes to it under whichever origin the agent judges; a task that faults on every run routes to it
under the category an engineer triages by; a capability this workspace does not have routes to the
member, a member's frustration with work this turn can still do well routes to doing it, a failed
step of the agent's own routes to a retry, and ordinary work routes nowhere. Four of the seven cases
pass only by not calling it.

The category is graded on exactly one case, and its evidence has to leave a single honest answer or
the grader measures a judgement call instead of the routing. A scheduled task's own log does that
only while nothing else appears in the failure: the same log naming a refused connector token scored
`external_connector` on every sample, which is the more specific answer to the question the field
asks. So the staged failure is the task handler's own, and no other subsystem is in it. Grading the
category anywhere else would grade taste — the prose carries the detail either way, and the board's
count is what the enum exists for.

Deterministic graders throughout, and no judge: what was called, with which origin and category, and
whether the turn settled its own failure are exact facts about the trajectory. The reply's prose is
graded by `response_register` and its neighbours and is not this suite's subject.

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
BRIEF_LOG = "brief-job.log"
BRIEF_LOG_LINES = """2026-08-24T06:00:02Z morning-brief run start
2026-08-24T06:00:02Z morning-brief FAILED scheduler: task handler raised before its first step
2026-08-25T06:00:01Z morning-brief run start
2026-08-25T06:00:01Z morning-brief FAILED scheduler: task handler raised before its first step
2026-08-26T06:00:02Z morning-brief run start
2026-08-26T06:00:02Z morning-brief FAILED scheduler: task handler raised before its first step
"""
BRIEF_FILES = (WorkspaceFile(path=BRIEF_LOG, content=BRIEF_LOG_LINES.encode()),)
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
        name="task-faults-on-every-run",
        message=(
            f"My morning brief has been blank all week. {BRIEF_LOG} in the workspace is that job's "
            "own log — read it and tell me what is wrong."
        ),
        grader=attempted_tools_scorer(
            ((REPORT_PROBLEM, {"origin": "fault", "category": "cron_task"}),), (), ()
        ),
        workspace_files=BRIEF_FILES,
        digest_tag="report:faulting-task",
    ),
    CapabilityCase(
        name="restraint-a-capability-the-workspace-lacks",
        message="Pull the pages out of my Notion workspace and put them in the team wiki.",
        grader=restraint_scorer((REPORT_PROBLEM,)),
        digest_tag="report:absent-capability",
    ),
    CapabilityCase(
        name="restraint-frustration-with-no-fault",
        message=(
            "Your last three summaries buried the one number I needed and I am tired of digging "
            f"for it. Give me the churn figure from this and nothing else:\n\n'{STATUS_UPDATE}'"
        ),
        grader=restraint_scorer((REPORT_PROBLEM,)),
        digest_tag="report:frustration-no-fault",
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
