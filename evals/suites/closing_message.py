"""Delivery cases: the closing message must carry everything the member needs to read.

A turn's reply is its closing message alone. Text the model writes between tool calls streams to a
tailing surface and is summarized into a long turn's progress post, but it is never the reply, so a
closing message that points at it ("the draft above") delivers a reference to nothing.

Asking for the failure does not produce it: given a task that invites drafting before a lookup, the
model gathers first and writes once, at the end, so there is no mid-turn prose to strand. Seven
cases therefore seed the state instead of hoping for it — an `undelivered` round puts the
deliverable in the model's own narration behind a tool call, and the case message arrives as a
member writing into a working turn. The only act left is the closing message, and the whole
question is whether the content comes with it.

Grading reads the delivered text — the closing message plus any `ask_user` question, which the
surface renders — for the content anchors the deliverable must carry and for the backreference
phrases that mark a pointer. Two cases seed the failure as the member met it, an "above" that was
never delivered, and ask for the content again. Two hold the opposite line, so the suite cannot be
bought by restating everything: a lookup stays a one-liner even with a whole config file sitting
undelivered behind it, and a document the member asked to have as a file stays a file.

Cases carry `samples=1`: the metric is the rate at which a real turn delivers, and a best-of-three
selection would report the rate at which one of three did."""

import json
import re

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    DescribedGrader,
    Grader,
    UndeliveredRound,
    WorkspaceFile,
)
from evals.harness.scorers import combine, shared_artifact_scorer

BACKREFERENCE_RE = re.compile(
    r"see\s+above"
    r"|as\s+(?:i\s+)?(?:mentioned|noted|said|wrote|drafted|showed|laid out)\s+(?:above|earlier)"
    r"|(?:draft|rewrite|revision|rewording|wording|version|copy|text|email|message|note|summary"
    r"|announcement|entry|options?|comparison|breakdown|timeline)"
    r"\s+i\s+(?:just\s+)?(?:wrote|shared|drafted|posted|gave|sent|laid out)"
    r"|earlier\s+in\s+(?:this|the)\s+turn",
    re.IGNORECASE,
)


def delivered(output: CapabilityOutput) -> str:
    """Everything the turn put in front of the member: the closing message and the question the
    turn ended on, which the surface renders as its own block. Only a trailing `ask_user` counts —
    the engine recomputes the pending question every round and clears it the moment the turn works
    on, so a question asked mid-turn and then worked past reaches nobody either."""
    final = output.calls[-1] if output.calls else None
    if final is None or final.name != "ask_user" or not final.succeeded:
        return output.response
    return f"{output.response}\n{json.dumps(final.input)}"


def no_backreference_scorer() -> Grader:
    """What the member received stands alone: no phrase in it points at prose they never got. The
    question a turn ends on is read too, since a draft handed over for sign-off is exactly where a
    pointer back at the working prose lands."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        found = BACKREFERENCE_RE.search(delivered(output))
        if found is None:
            return CapabilityVerdict(True, "no dangling backreference")
        return CapabilityVerdict(
            False,
            f"points at undelivered prose: {found.group(0)!r}",
            {"backreference": found.group(0)},
        )

    return DescribedGrader("the closing message refers to no content outside itself", grade)


def inlined_scorer(anchors: tuple[str, ...], min_words: int) -> Grader:
    """The deliverable itself reached the member: every anchor present in the delivered text, at
    the length real content takes rather than the length a pointer to it takes."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = delivered(output)
        lowered = text.lower()
        words = len(text.split())
        missing = [anchor for anchor in anchors if anchor.lower() not in lowered]
        failures = []
        if missing:
            failures.append(f"delivered without {', '.join(repr(item) for item in missing)}")
        if words < min_words:
            failures.append(f"{words} words, under the {min_words} a delivered draft takes")
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), {"words": words})
        return CapabilityVerdict(True, f"delivered whole: {words} words", {"words": words})

    return DescribedGrader(
        f"the delivered text runs at least {min_words} words and carries "
        + ", ".join(repr(anchor) for anchor in anchors),
        grade,
    )


def brief_scorer(anchors: tuple[str, ...], max_words: int) -> Grader:
    """The answer the member asked for and nothing else: the content that answers it, inside a
    budget a restated file or an inlined document blows through."""

    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        text = delivered(output)
        lowered = text.lower()
        words = len(text.split())
        missing = [anchor for anchor in anchors if anchor.lower() not in lowered]
        failures = []
        if missing:
            failures.append(f"answered without {', '.join(repr(item) for item in missing)}")
        if words > max_words:
            failures.append(f"{words} words over the {max_words} budget")
        if failures:
            return CapabilityVerdict(False, "; ".join(failures), {"words": words})
        return CapabilityVerdict(True, f"answered in {words} words", {"words": words})

    return DescribedGrader(
        f"the delivered text stays within {max_words} words"
        + (" and carries " + ", ".join(repr(anchor) for anchor in anchors) if anchors else ""),
        grade,
    )


def _file(path: str, body: str) -> WorkspaceFile:
    return WorkspaceFile(path, body.strip().encode() + b"\n")


OUTREACH_DRAFT = _file(
    "outreach/draft.md",
    """
Subject: Following up on our conversation about Halyard

Hi Dana,

I hope this email finds you well and that everything has been going smoothly on your side since we
last spoke. I wanted to circle back around and touch base with you regarding the conversation we
had at that time about Halyard, and specifically about whether it might turn out to be a reasonable
fit for the way that your team is currently handling inbound scheduling, which if I am remembering
our discussion correctly was still a fairly manual process for you at that point.

Since the two of us last spoke, we have gone ahead and shipped a number of things that I believe
are quite relevant to what you were describing to me. The single biggest one of those, and the one
I think you will care about the most, is that we now support shared queues across multiple sites,
which was more or less exactly the gap that you called out to me at the time.

If you would be open to it, we would be very glad to go ahead and set you up with a 30-day pilot
beginning the Monday after you say the word, at no cost to you whatsoever, capped at 60 seats, with
our onboarding team handling the entire data import on your behalf. There is no obligation at the
end of the pilot and no card is required in order to begin.

Let me know if any of this sounds interesting to you and we can find a time that works.

Best regards,
Priya
""",
)
OUTREACH_SEGMENTS = _file(
    "outreach/segments.csv",
    """
segment,who,typical_seats,what_decides_it
A,multi-site operations teams,40-200,shared queues across sites
B,single-clinic front desks,3-12,price and setup time
C,procurement-led enterprise buyers,250+,security review and MSA terms
""",
)
OUTREACH_ACCOUNTS = _file(
    "outreach/accounts.csv",
    """
account,owner,region,stage
Brightwater Clinics,Marta Vela,west,renewal
Dana Whitfield / Cardinal Group,Owen Sparks,east,pilot pending
Trellis Health,Marta Vela,central,closed lost
""",
)
OUTREACH_ANCHORS = ("Dana", "Halyard", "60", "shared queues")
TIGHTENED_OUTREACH = """
Subject: A Halyard pilot for your team

Hi Dana,

Since we last spoke we shipped the piece you called out: shared queues across sites.

If you want to try it, we can start a 30-day pilot the Monday after you say the word, at no cost,
capped at 60 seats, with our team handling the data import. No obligation at the end, no card to
begin.

Worth a conversation?

Best regards,
Priya
""".strip()

RELEASE_COMMITS = _file(
    "release/commits.txt",
    """
a1c9f02 add saved views to the inbox (#812)
7b3e114 fix: retry webhook delivery on 5xx (#815)
44f0ab8 speed up the activity feed query (#818)
9de2c30 chore: bump asyncpg to 0.30 (#819)
c02b7a1 add CSV export to reports (#821)
e77d5b9 test: cover the digest scheduler (#823)
""",
)
RELEASE_VERSIONS = _file(
    "release/versions.json",
    """
{
  "latest_released": "4.1.3",
  "unreleased_prs": [812, 815, 819, 823],
  "next_planned": "4.2.0"
}
""",
)

MIGRATION_PLAN = _file(
    "notes/migration.md",
    """
# Storage migration plan

- Window: this Saturday, 02:00 to 06:00 UTC
- Effect: uploads and downloads return 503 for the whole window; chat and search are unaffected
- Rollback: repoint the bucket alias, about 15 minutes
- Owner: the platform team
""",
)
ONCALL = _file(
    "notes/oncall.csv",
    """
weekend,primary,secondary
last,Ines Calder,Ravi Nathan
this,Tomas Reiner,Ines Calder
next,Ravi Nathan,Priya Anand
""",
)

REFUND_POLICY = _file(
    "policy/refunds.md",
    """
# Refund policy (current wording)

Requests for the return of merchandise must be initiated by the purchasing party within a period of
fourteen (14) days commencing from the date of delivery as recorded by the carrier of record.
Merchandise must be returned in its original condition and packaging. A restocking fee of fifteen
percent (15%) shall be assessed against all returned merchandise excepting only merchandise
determined by the company to be defective. Outbound shipping charges are non-refundable in all
cases. Refunds are remitted to the original instrument of payment within a period of ten (10)
business days following receipt and inspection.
""",
)
REFUND_TICKETS = _file(
    "policy/tickets.csv",
    """
ticket,days_since_delivery,reason,condition
T-1041,6,changed mind,original packaging
T-1042,21,changed mind,original packaging
T-1043,3,arrived damaged,damaged
T-1044,12,wrong size,original packaging
T-1045,30,defective,used
T-1046,9,duplicate order,unopened
""",
)

DANA_REPLY = _file(
    "inbox/from-dana.md",
    """
From: Dana Whitfield
Subject: Re: Following up on our conversation about Halyard

This is good timing. That start works for the pilot, and 60 seats covers the two sites we would
begin with. Can we do a kickoff call on Thursday at 15:00 UTC? I would want our operations lead on
it. One question before we sign anything: does the shared queue respect our existing site-level
permissions, or does it flatten them?
""",
)
CALENDAR = _file(
    "inbox/calendar.md",
    """
# This week

- Tuesday, 16:00-17:00 UTC — pipeline review
- Thursday, 14:30-16:00 UTC — board prep, cannot move
- Friday, 09:00-09:30 UTC — standup
- Monday, 13:00-14:00 UTC — vendor check-in
""",
)

VENDOR_QUOTES = (
    _file(
        "quotes/northwind.md",
        """
# Northwind — quote

- Platform fee: $4,000 per month, billed annually up front
- Included: 50 seats, then $60 per seat per month
- Support: business hours, 8-hour first response
- Term: 24 months, no early exit
- Migration: $12,000 one time
""",
    ),
    _file(
        "quotes/atlas.md",
        """
# Atlas — quote

- Platform fee: $5,500 per month, billed monthly
- Included: 100 seats, then $40 per seat per month
- Support: 24/7, 1-hour first response on Sev 1
- Term: 12 months, 60-day exit for convenience
- Migration: included
""",
    ),
    _file(
        "quotes/beacon.md",
        """
# Beacon — quote

- Platform fee: $2,750 per month, billed annually up front
- Included: 25 seats, then $95 per seat per month
- Support: business hours, best effort
- Term: 36 months, early exit at 50% of the remainder
- Migration: $4,000 one time
""",
    ),
)

INCIDENT = _file(
    "incidents/mar03.md",
    """
# March 3 outage — raw notes

- 14:02 UTC: deploy 1188 rolls out the new upload path
- 14:09 UTC: upload error rate goes from 0.2% to 41%
- 14:11 UTC: first customer report, a failed 2 GB upload
- 14:15 UTC: on-call paged, starts looking at the CDN
- 14:31 UTC: CDN ruled out, attention moves to the deploy
- 14:38 UTC: deploy 1188 rolled back
- 14:44 UTC: error rate back to 0.3%
- 15:20 UTC: root cause found, the new path buffered the whole body in memory and the pods OOMed
- Customer impact: 35 minutes of failed uploads over 100 MB; downloads never affected
""",
)

SCHEDULE = _file(
    "config/schedule.toml",
    """
# Scheduled jobs. Times are UTC unless a job sets its own timezone.

[digest]
enabled = true
time = "07:15Z"
timezone = "UTC"
skip_weekends = true
audience = "every member with an active seat"
include_threads = true
max_items = 40

[reindex]
enabled = true
time = "03:00Z"
batch_size = 500
concurrency = 4
retry_backoff_seconds = 30

[usage_rollup]
enabled = true
time = "01:00Z"
window_days = 1
emit_zero_rows = false

[retention_sweep]
enabled = false
time = "04:30Z"
keep_days = 90
dry_run = true

[health_probe]
enabled = true
every_minutes = 5
timeout_seconds = 10
alert_after_failures = 3
""",
)


PERMISSIONS = _file(
    "docs/halyard-permissions.md",
    """
# Shared queues and permissions

A shared queue does not flatten site-level permissions. Membership of a queue is evaluated against
the viewer's site roles on every read, so a user sees only the items from sites they already have
access to. Two caveats worth stating to a customer: an org-level admin sees every item in a shared
queue regardless of site, and a role change takes up to five minutes to propagate to open sessions.
""",
)

DRAFT_ANNOUNCEMENT = """
Subject: Storage maintenance this Saturday, 02:00-06:00 UTC

We are migrating our storage system on Saturday from 02:00 to 06:00 UTC.

During the window, uploads and downloads will fail. Chat and search are unaffected and stay up.

If you have file work that has to happen, do it before 02:00 or after 06:00. If anything goes
wrong we can roll back in about 15 minutes.
""".strip()

DRAFT_CHANGELOG = """
## 4.2

- Saved views in the inbox: keep the filters you use every day and switch between them in one click.
- More reliable webhooks: a delivery that fails with a server error is now retried automatically.
- A faster activity feed: the feed query was rewritten and now loads noticeably quicker.
- CSV export for reports: export any report to CSV from the report header.
""".strip()

DRAFT_POLICY = """
## Returns and refunds

You have 14 days from the day your order is delivered to start a return. Items need to come back
in their original condition and packaging.

We keep a 15% restocking fee on returns, unless the item is defective, in which case there is no
fee. The shipping you paid to receive the order is not refunded.

Once we receive the item and check it, your refund goes back to the card or account you paid with
within 10 business days.
""".strip()

DRAFT_COMPARISON = """
At 60 seats today and 120 inside a year:

- Northwind: $4,000/mo plus $60 for each seat over 50. That is $4,600/mo now and $8,200/mo at 120,
  paid annually up front, on a 24-month term with no way out, plus $12,000 to migrate.
- Atlas: $5,500/mo with 100 seats included, then $40 each. That is $5,500/mo now and $6,300/mo at
  120, billed monthly, 12-month term with a 60-day exit, migration included.
- Beacon: $2,750/mo plus $95 for each seat over 25. That is $6,075/mo now and $11,775/mo at 120, on
  a 36-month term, plus $4,000 to migrate.

Beacon is the cheapest sticker price and the most expensive way to get to 120 seats. Northwind
looks reasonable today and locks you into 24 months of growing per-seat cost. Atlas costs the most
this month and the least by the time you are at 120, and it is the only one you can leave.
""".strip()

DRAFT_REPLY = """
Hi Dana,

The start date works on our side, and 60 seats covers the two sites to start.

On permissions: the shared queue does not flatten your site-level permissions. Queue membership is
checked against each viewer's site roles on every read, so people see only items from sites they
already have access to. Two caveats worth knowing up front: an org-level admin sees everything in a
shared queue regardless of site, and a role change takes up to five minutes to reach open sessions.

Happy to get the kickoff booked with your operations lead on the call.

Best,
Priya
""".strip()


CASES = (
    CapabilityCase(
        "outreach-then-owner",
        "one more thing while you're in there — who owns that account on our side? should be in "
        "outreach/accounts.csv",
        combine(inlined_scorer(OUTREACH_ANCHORS, min_words=70), no_backreference_scorer()),
        digest_tag="closing:outreach-then-owner",
        workspace_files=(OUTREACH_DRAFT, OUTREACH_SEGMENTS, OUTREACH_ACCOUNTS),
        prior_messages=(
            "Tighten the email in /workspace/outreach/draft.md — roughly half the length, keep "
            "every specific.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"Here it is at about half the length, with the pilot terms "
                    f"intact:\n\n{TIGHTENED_OUTREACH}\n\nLet me check which of our segments this "
                    "version actually lands with."
                ),
                tool="read",
                input={"file_path": "/workspace/outreach/segments.csv"},
                result=OUTREACH_SEGMENTS.content.decode(),
            ),
        ),
        rubric=(
            "The reply contains the tightened email as text the member can read and send, not a "
            "description of it or a pointer to it.",
            "The reply names Owen Sparks as the account owner.",
        ),
    ),
    CapabilityCase(
        "announcement-then-oncall",
        "who's primary on call this weekend? notes/oncall.csv",
        combine(inlined_scorer(("02:00", "Tomas Reiner"), min_words=50), no_backreference_scorer()),
        digest_tag="closing:announcement-then-oncall",
        workspace_files=(MIGRATION_PLAN, ONCALL),
        prior_messages=(
            "Draft the company-wide announcement for the storage migration from "
            "notes/migration.md. Keep it plain.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"Here is the announcement:\n\n{DRAFT_ANNOUNCEMENT}\n\nLet me re-check the "
                    "rollback line against the plan."
                ),
                tool="read",
                input={"file_path": "/workspace/notes/migration.md"},
                result=MIGRATION_PLAN.content.decode(),
            ),
        ),
        rubric=(
            "The reply contains the announcement as postable text, not a description of it.",
            "The announcement states the window and that uploads and downloads fail during it "
            "while chat and search are unaffected.",
            "The reply names Tomas Reiner as the primary on call.",
        ),
    ),
    CapabilityCase(
        "changelog-then-verify",
        "hold on — are all of those actually in the unreleased set? release/versions.json",
        combine(
            inlined_scorer(("saved views", "webhook", "activity feed", "CSV export"), min_words=45),
            no_backreference_scorer(),
        ),
        digest_tag="closing:changelog-then-verify",
        workspace_files=(RELEASE_COMMITS, RELEASE_VERSIONS),
        prior_messages=(
            "Write the user-facing changelog entry for 4.2 from release/commits.txt, leaving out "
            "anything a user would not notice.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"Dropping the dependency bump and the test-only change, that "
                    f"leaves:\n\n{DRAFT_CHANGELOG}\n\nLet me confirm the commit list is complete."
                ),
                tool="read",
                input={"file_path": "/workspace/release/commits.txt"},
                result=RELEASE_COMMITS.content.decode(),
            ),
        ),
        rubric=(
            "The reply contains the changelog entry itself, written for users, not a description "
            "of what it would say.",
            "The reply identifies the activity feed change (#818) and the CSV export change "
            "(#821) as absent from the unreleased set.",
        ),
    ),
    CapabilityCase(
        "policy-then-count",
        "how many of last month's tickets would that cover cleanly? policy/tickets.csv",
        combine(
            inlined_scorer(("14 days", "15%", "shipping"), min_words=60), no_backreference_scorer()
        ),
        digest_tag="closing:policy-then-count",
        workspace_files=(REFUND_POLICY, REFUND_TICKETS),
        prior_messages=(
            "Rewrite policy/refunds.md in plain language a customer can follow. Same rules, just "
            "readable.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"Same rules, plain language:\n\n{DRAFT_POLICY}\n\nLet me check the original "
                    "once more for anything I dropped."
                ),
                tool="read",
                input={"file_path": "/workspace/policy/refunds.md"},
                result=REFUND_POLICY.content.decode(),
            ),
        ),
        rubric=(
            "The reply contains the rewritten policy as the customer-facing text itself.",
            "The rewrite keeps the 14-day window, the 15% restocking fee and its defect exception, "
            "the non-refundable outbound shipping, and the 10-business-day refund timing.",
            "The reply gives a count of the tickets the rewrite clearly covers.",
        ),
    ),
    CapabilityCase(
        "comparison-then-constraint",
        "forgot to say — we need 24/7 support, the on-call rotation depends on it. does that "
        "change anything?",
        combine(
            inlined_scorer(("Northwind", "Atlas", "Beacon", "8,200", "6,300"), min_words=120),
            no_backreference_scorer(),
        ),
        digest_tag="closing:comparison-then-constraint",
        workspace_files=VENDOR_QUOTES,
        prior_messages=(
            "Compare the three quotes under /workspace/quotes for a team of 60 that expects to "
            "reach 120 seats inside a year, and tell me which one to take.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"{DRAFT_COMPARISON}\n\nLet me re-read the Atlas terms before I commit to a "
                    "recommendation."
                ),
                tool="read",
                input={"file_path": "/workspace/quotes/atlas.md"},
                result=VENDOR_QUOTES[1].content.decode(),
            ),
        ),
        rubric=(
            "The comparison itself is in the reply, covering all three vendors on cost at both "
            "seat counts, support, and term.",
            "The reply picks Atlas, the only quote offering 24/7 support, and says why the "
            "requirement decides it.",
        ),
    ),
    CapabilityCase(
        "reply-then-slot",
        "one constraint — don't put the kickoff on Thursday, I have board prep that afternoon",
        combine(
            inlined_scorer(("Dana", "permissions", "site"), min_words=60),
            no_backreference_scorer(),
        ),
        digest_tag="closing:reply-then-slot",
        workspace_files=(DANA_REPLY, CALENDAR, PERMISSIONS),
        prior_messages=(
            "Dana wrote back, it is in inbox/from-dana.md. Draft my reply: accept the pilot "
            "start and answer her permissions question — the behaviour is in "
            "docs/halyard-permissions.md. Do not send it without my sign-off.",
        ),
        undelivered=(
            UndeliveredRound(
                narration=(
                    f"Here is the reply for your sign-off:\n\n{DRAFT_REPLY}\n\nLet me find a "
                    "kickoff slot that works before I hand it over."
                ),
                tool="read",
                input={"file_path": "/workspace/inbox/calendar.md"},
                result=CALENDAR.content.decode(),
            ),
        ),
        rubric=(
            "The complete reply to Dana is in front of the member to approve, as the text that "
            "would go out.",
            "The reply to Dana answers the site-level permissions question rather than deferring "
            "it.",
            "The kickoff slot proposed is not on Thursday.",
        ),
    ),
    CapabilityCase(
        "recover-missing-draft",
        "I can't see anything above. There is one line from you and nothing else. Paste me the "
        "actual text.",
        combine(inlined_scorer(OUTREACH_ANCHORS, min_words=70), no_backreference_scorer()),
        digest_tag="closing:recover-missing-draft",
        workspace_files=(OUTREACH_DRAFT,),
        prior_messages=(
            "tighten the email in /workspace/outreach/draft.md, keep the specifics",
            "Tightened it to about half the length above, with the pilot terms kept intact. Do you "
            "want me to keep the paragraph about the shared queues?",
        ),
        rubric=(
            "The reply contains the tightened email in full, as text the member can read.",
            "The reply does not ask the member for anything before producing the text.",
        ),
    ),
    CapabilityCase(
        "recover-missing-options",
        "All I got was that one line. Spell the three out for me, with what each one costs us "
        "against the window in notes/migration.md.",
        combine(
            inlined_scorer(("staged by region", "dark launch", "big-bang"), min_words=90),
            no_backreference_scorer(),
        ),
        digest_tag="closing:recover-missing-options",
        workspace_files=(MIGRATION_PLAN,),
        prior_messages=(
            "we need to pick a rollout shape for the storage migration. what are the options?",
            "Three shapes make sense here: staged by region, dark launch, or big-bang inside one "
            "weekend window. The detail on each is above.",
        ),
        rubric=(
            "The reply develops all three rollout shapes, each with what it costs against the "
            "migration window.",
            "The reply does not refer the member back to detail it claims to have given already.",
        ),
    ),
    CapabilityCase(
        "lookup-stays-short",
        "and is it skipped at the weekend?",
        combine(brief_scorer(("07:15",), max_words=30), no_backreference_scorer()),
        digest_tag="closing:lookup-stays-short",
        workspace_files=(SCHEDULE,),
        prior_messages=("what time does the nightly digest go out? it's in config/schedule.toml",),
        undelivered=(
            UndeliveredRound(
                narration="Let me open the schedule config.",
                tool="read",
                input={"file_path": "/workspace/config/schedule.toml"},
                result=SCHEDULE.content.decode(),
            ),
        ),
        rubric=(
            "The reply gives the time and says it is skipped at weekends, without restating the "
            "config file or listing the other scheduled jobs.",
        ),
    ),
    CapabilityCase(
        "shared-file-stays-shared",
        "Write up the March 3 outage from incidents/mar03.md as a markdown timeline document and "
        "share the file with me. In the thread just give me the one line I should put on the "
        "status page.",
        combine(
            shared_artifact_scorer(".md"),
            brief_scorer((), max_words=60),
            no_backreference_scorer(),
        ),
        digest_tag="closing:shared-file-stays-shared",
        workspace_files=(INCIDENT,),
        rubric=(
            "The reply is a short status page summary, not the timeline document restated in the "
            "thread.",
            "The status page line names the impact and that it is resolved.",
        ),
    ),
)
