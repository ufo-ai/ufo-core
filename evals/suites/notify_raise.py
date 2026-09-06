"""Raising discipline: a business or operational fact in the member's own data reaches the
notification queue, once per subject, and everything else never does.

`notify` is the producer half of the Notification app: a tool every agent holds that puts one
message in a queue another agent triages. The member is a startup founder with Gmail, GitHub, and
Stripe connected, and what they want interrupted for is what changes what they do today: revenue
moving, production down, a person waiting on them. Over-raising is the expensive failure — a queue
full of green runs and receipts is one the triage agent stops trusting — and so is raising ufo's own
condition: a connection that stopped authenticating is `report_problem`'s, not a notification.

The suite is built as opposing pairs and neither half can be passed by learning the other. Signal:
a churn spike, a deploy failed on main, an investor's deadline going unanswered, a customer turning,
a cluster of failed payments, and two of those landing in one report (two subjects, two calls, never
one merged and never three). Restraint: green CI, newsletters and receipts, one small refund, a
member who is reading the answer as it is written, the agent's own failed step, and the sync whose
own connection broke — which routes to `report_problem` and never to `notify`.

The cases are member messages to the main agent standing in for the background turns that raise
notifications in production; the harness admits no speakerless turn. Deterministic graders
throughout: what was called, how many times, and with which subjects are exact facts about the
trajectory. Every case runs one sample, since one raise in three of a routine run is the noise this
suite exists to catch."""

import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
    WorkspaceFile,
)
from evals.harness.scorers import attempted_tools_scorer, restraint_scorer

NOTIFY = "notify"
REPORT_PROBLEM = "report_problem"
STRIPE = "source/stripe"
GITHUB = "source/github"
GMAIL = "source/gmail"

REPORT_FRAME = (
    "This is the nightly report for your connected {source}; {path} in the workspace is the "
    "export it was built from. Read it and do whatever the report calls for."
)

CHURN_CSV = "stripe-subscriptions-week.csv"
CHURN_ROWS = """customer,plan,mrr_usd,event,at
Northwind Robotics,team,1900,canceled,2026-09-01T14:02Z
Halvorsen Labs,team,1400,canceled,2026-09-02T09:11Z
Pale Blue Freight,starter,190,canceled,2026-09-02T17:40Z
Kestrel Analytics,team,1200,canceled,2026-09-03T08:03Z
Orbit Dental,starter,90,canceled,2026-09-03T19:22Z
Marrow & Finch,team,1600,canceled,2026-09-04T07:45Z
Bluewater Tax,starter,90,created,2026-09-03T12:00Z
"""
CHURN_PRIOR_WEEKS = "stripe-subscriptions-prior.csv"
CHURN_PRIOR_ROWS = """week,canceled,created,canceled_mrr_usd
2026-08-04,1,4,90
2026-08-11,0,3,0
2026-08-18,1,5,190
2026-08-25,1,2,90
"""

DEPLOY_LOG = "github-actions-main.log"
DEPLOY_LINES = """2026-09-04T13:02:11Z workflow=ci ref=main sha=8f21c0d conclusion=success
2026-09-04T13:04:52Z workflow=deploy ref=main sha=8f21c0d job=migrate conclusion=success
2026-09-04T13:07:30Z workflow=deploy ref=main sha=8f21c0d job=rollout conclusion=failure
2026-09-04T13:07:30Z rollout: readiness probe failed 3/3 on api-7c9d; rolled back to 4be0a11
2026-09-04T13:07:31Z deploy: production serves 4be0a11; 8f21c0d (#412 billing rework) is not live
"""
GREEN_LOG = "github-actions-main.log"
GREEN_LINES = """2026-09-04T09:02:11Z workflow=ci ref=main sha=2ac1e77 conclusion=success
2026-09-04T09:05:40Z workflow=deploy ref=main sha=2ac1e77 job=migrate conclusion=success
2026-09-04T09:08:12Z workflow=deploy ref=main sha=2ac1e77 job=rollout conclusion=success
2026-09-04T11:41:03Z workflow=ci ref=feat/onboarding sha=91be004 conclusion=success
2026-09-04T15:12:44Z workflow=ci ref=main sha=d0e33f1 conclusion=success
2026-09-04T15:16:09Z workflow=deploy ref=main sha=d0e33f1 job=rollout conclusion=success
"""

INBOX_JSONL = "gmail-unanswered.jsonl"
INVESTOR_THREAD = (
    '{"from": "priya@corvidcapital.com", "subject": "Data room before Friday IC", '
    '"received": "2026-08-31T16:20Z", "replied": false, '
    '"snippet": "We are taking you to IC Friday. Can you get the data room link and the Aug '
    'metrics over by Thursday? Without them we slip to the October cycle."}\n'
)
ESCALATION_THREAD = (
    '{"from": "cfo@marrowfinch.com", "subject": "Re: Renewal and the export failures", '
    '"received": "2026-09-02T10:05Z", "replied": false, '
    '"snippet": "Third export failure this month and nobody has answered our ticket. We are '
    'evaluating alternatives before the renewal on the 15th. Copying our CFO."}\n'
)
NEWSLETTERS = "".join(
    (
        '{"from": "digest@producthunt.com", "subject": "Top launches this week", '
        '"received": "2026-09-04T06:00Z", "replied": false, "snippet": "Ten new tools."}\n',
        '{"from": "billing@vercel.com", "subject": "Your receipt for September", '
        '"received": "2026-09-03T02:00Z", "replied": false, "snippet": "$20.00 paid."}\n',
        '{"from": "noreply@github.com", "subject": "[repo] dependabot merged 3 PRs", '
        '"received": "2026-09-03T04:10Z", "replied": false, "snippet": "Bumps lodash."}\n',
        '{"from": "news@a16z.com", "subject": "The weekly", '
        '"received": "2026-09-02T12:00Z", "replied": false, "snippet": "Essays on AI."}\n',
    )
)

PAYMENTS_CSV = "stripe-charges-today.csv"
PAYMENT_FAILURES = "customer,plan,amount_usd,outcome,code,at\n" + "".join(
    f"cust_{index:03d},team,190,failed,card_declined,2026-09-04T14:{index:02d}Z\n"
    for index in range(14)
)
PAYMENTS_NOTE = "stripe-changes.md"
PAYMENTS_CHANGE = (
    "# Billing changes\n\n2026-09-04 13:30Z: team plan price object replaced (price_1Q… → "
    "price_1R…) as part of #412 billing rework.\n"
)
REFUND_CSV = "stripe-charges-today.csv"
REFUND_ROWS = """customer,plan,amount_usd,outcome,code,at
cust_207,starter,90,succeeded,,2026-09-04T09:10Z
cust_118,team,190,succeeded,,2026-09-04T10:44Z
cust_042,starter,12,refunded,requested_by_customer,2026-09-04T11:02Z
cust_311,team,190,succeeded,,2026-09-04T13:15Z
"""

AUTH_LOG = "nightly-sync.log"
AUTH_LINES = """2026-09-02T02:00:01Z stripe sync start
2026-09-02T02:00:02Z stripe sync FAILED: 401 invalid_grant, refresh token revoked
2026-09-03T02:00:01Z stripe sync start
2026-09-03T02:00:02Z stripe sync FAILED: 401 invalid_grant, refresh token revoked
2026-09-04T02:00:01Z stripe sync start
2026-09-04T02:00:02Z stripe sync FAILED: 401 invalid_grant, refresh token revoked
"""

RECEIPTS_SCRIPT = "receipts.py"
RECEIPTS_DATA = "receipts.csv"
RECEIPTS_CODE = """import csv

with open("receipts-2026.csv") as handle:
    rows = list(csv.DictReader(handle))

print(sum(float(row["amount"]) for row in rows))
"""
RECEIPTS_ROWS = "vendor,amount\nprint shop,412.50\nfreight,1180.00\nanodizing,675.25\n"


def _file(path: str, content: str) -> WorkspaceFile:
    return WorkspaceFile(path=path, content=content.encode())


MENTIONS_THE_QUEUE = re.compile(r"\bnotif|\bqueue", re.IGNORECASE)


def _raised_exactly(subjects: frozenset[str]) -> Grader:
    """One `notify` per subject in `subjects`, no more and none for anything else — and a reply
    that never says so. The member reads the reply; the notification reaches them on its own, and a
    line about the tool or the queue is a line they cannot act on and did not ask for."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        raised = [
            str(call.arguments.get("subject")) for call in output.calls if call.call == NOTIFY
        ]
        if sorted(raised) != sorted(subjects):
            return CapabilityVerdict(
                False, f"raised {sorted(raised)}, wanted one each of {sorted(subjects)}"
            )
        said = MENTIONS_THE_QUEUE.search(output.response)
        if said is not None:
            return CapabilityVerdict(False, f"the reply mentions the queue: {said.group(0)!r}")
        return CapabilityVerdict(True, f"raised once each about {sorted(subjects)}, said nothing")

    return grade


CASES = (
    CapabilityCase(
        name="stripe-churn-spike",
        message=(
            REPORT_FRAME.format(source="Stripe account", path=CHURN_CSV)
            + f" {CHURN_PRIOR_WEEKS} holds the prior four weeks for comparison. The source's ref "
            f"is {STRIPE}."
        ),
        grader=_raised_exactly(frozenset({STRIPE})),
        workspace_files=(_file(CHURN_CSV, CHURN_ROWS), _file(CHURN_PRIOR_WEEKS, CHURN_PRIOR_ROWS)),
        digest_tag="notify:churn-spike",
    ),
    CapabilityCase(
        name="github-deploy-failed-on-main",
        message=(
            REPORT_FRAME.format(source="GitHub repository", path=DEPLOY_LOG)
            + f" The source's ref is {GITHUB}."
        ),
        grader=_raised_exactly(frozenset({GITHUB})),
        workspace_files=(_file(DEPLOY_LOG, DEPLOY_LINES),),
        digest_tag="notify:deploy-failed",
    ),
    CapabilityCase(
        name="investor-deadline-unanswered",
        message=(
            REPORT_FRAME.format(source="Gmail inbox", path=INBOX_JSONL)
            + f" Each line is one unanswered thread. The source's ref is {GMAIL}."
        ),
        grader=_raised_exactly(frozenset({GMAIL})),
        workspace_files=(_file(INBOX_JSONL, INVESTOR_THREAD + NEWSLETTERS),),
        digest_tag="notify:investor-deadline",
    ),
    CapabilityCase(
        name="customer-turning",
        message=(
            REPORT_FRAME.format(source="Gmail inbox", path=INBOX_JSONL)
            + f" Each line is one unanswered thread. The source's ref is {GMAIL}."
        ),
        grader=_raised_exactly(frozenset({GMAIL})),
        workspace_files=(_file(INBOX_JSONL, NEWSLETTERS + ESCALATION_THREAD),),
        digest_tag="notify:customer-turning",
    ),
    CapabilityCase(
        name="failed-payments-cluster",
        message=(
            REPORT_FRAME.format(source="Stripe account", path=PAYMENTS_CSV)
            + f" {PAYMENTS_NOTE} lists today's billing changes. The source's ref is {STRIPE}."
        ),
        grader=_raised_exactly(frozenset({STRIPE})),
        workspace_files=(
            _file(PAYMENTS_CSV, PAYMENT_FAILURES),
            _file(PAYMENTS_NOTE, PAYMENTS_CHANGE),
        ),
        digest_tag="notify:failed-payments",
    ),
    CapabilityCase(
        name="two-at-once-are-two-subjects",
        message=(
            "This is the nightly report for your connected Stripe account and GitHub repository; "
            f"{CHURN_CSV} and {DEPLOY_LOG} in the workspace are the exports it was built from, and "
            f"{CHURN_PRIOR_WEEKS} holds the prior four weeks. Read them and do whatever the report "
            f"calls for. The sources' refs are {STRIPE} and {GITHUB}."
        ),
        grader=_raised_exactly(frozenset({STRIPE, GITHUB})),
        workspace_files=(
            _file(CHURN_CSV, CHURN_ROWS),
            _file(CHURN_PRIOR_WEEKS, CHURN_PRIOR_ROWS),
            _file(DEPLOY_LOG, DEPLOY_LINES),
        ),
        digest_tag="notify:two-subjects",
    ),
    CapabilityCase(
        name="restraint-green-ci-and-deploys",
        message=(
            REPORT_FRAME.format(source="GitHub repository", path=GREEN_LOG)
            + f" The source's ref is {GITHUB}."
        ),
        grader=restraint_scorer((NOTIFY,)),
        workspace_files=(_file(GREEN_LOG, GREEN_LINES),),
        digest_tag="notify:green-ci",
    ),
    CapabilityCase(
        name="restraint-newsletters-and-receipts",
        message=(
            REPORT_FRAME.format(source="Gmail inbox", path=INBOX_JSONL)
            + f" Each line is one unanswered thread. The source's ref is {GMAIL}."
        ),
        grader=restraint_scorer((NOTIFY,)),
        workspace_files=(_file(INBOX_JSONL, NEWSLETTERS),),
        digest_tag="notify:newsletters",
    ),
    CapabilityCase(
        name="restraint-one-small-refund",
        message=(
            REPORT_FRAME.format(source="Stripe account", path=REFUND_CSV)
            + f" The source's ref is {STRIPE}."
        ),
        grader=restraint_scorer((NOTIFY,)),
        workspace_files=(_file(REFUND_CSV, REFUND_ROWS),),
        digest_tag="notify:small-refund",
    ),
    CapabilityCase(
        name="restraint-member-is-reading-this",
        message=(
            f"Read {DEPLOY_LOG} in the workspace and tell me, here, whether the billing rework is "
            "live in production. I am waiting on the answer."
        ),
        grader=restraint_scorer((NOTIFY,)),
        workspace_files=(_file(DEPLOY_LOG, DEPLOY_LINES),),
        digest_tag="notify:member-present",
    ),
    CapabilityCase(
        name="restraint-the-agents-own-failed-step",
        message=(
            f"Run {RECEIPTS_SCRIPT} in the workspace and tell me the total it prints. The data is "
            "right there beside it."
        ),
        grader=restraint_scorer((NOTIFY,)),
        workspace_files=(
            _file(RECEIPTS_SCRIPT, RECEIPTS_CODE),
            _file(RECEIPTS_DATA, RECEIPTS_ROWS),
        ),
        digest_tag="notify:own-failed-step",
    ),
    CapabilityCase(
        name="broken-connection-is-a-problem-report-not-a-notification",
        message=(
            f"This is the nightly report for your connected Stripe account; {AUTH_LOG} in the "
            "workspace is the sync's own log. Read it and do whatever the report calls for."
        ),
        grader=attempted_tools_scorer(((REPORT_PROBLEM, {}),), (NOTIFY,), ()),
        workspace_files=(_file(AUTH_LOG, AUTH_LINES),),
        digest_tag="notify:broken-connection-routes-elsewhere",
    ),
)
