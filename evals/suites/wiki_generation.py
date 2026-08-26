"""What the Wiki app's page is made of: the rows the fact-extraction pass writes out of source
pages, and the Overview paragraph the consolidation pass folds aged rows into. The page itself only
reads those items, so a wiki a member cannot trust is a derivation defect, and this suite drives the
two passes directly — the condenser's own prompts, tool schema and bounds — the way `asd_writing`
drives them for the writing standard.

Four defects, one verdict each off one generation:

`attributed` is the self-versus-others question, and it is asked of a stranger's page: a row off an
outside party's page has to name that party, and may not head with this workspace or a member of
it. A row off the workspace's own page is under no such rule — a wiki is its own workspace's, so
"Product design — …" is a well-headed row there and the deploy writes it that way. The payload
names the page's title and stream exactly as `FactDeriver` sends them, because that is where a page
states who it belongs to. Deterministic first, then judged, for the one question no checker
decides: whether a reader takes an outside party's news for their own.

`distinct` is deduplication, and it is the defect the deploy carries at scale — 40,324 page-derived
rows in the testing workspace on 2026-08-26, of which 545 are superseded. Nothing collapses a
page-derived row outside its own extraction reply: `_restates` compares entries of one reply,
`supersede_page_facts` retires only what a re-derivation of the same page replaces, and
`MemoryDeduper` never reads a page-derived row at all. So one meeting delivered on two pages is
written twice, and a change spread over a request, its comment and its notification is written
three times.

`filed` is the same self-versus-others question asked of the section rather than the sentence, and
it is the one no reading of a row can answer. `memory_kind` is not a label a member ever sees — it
is the band the wiki files the row under, and three of those bands speak for the workspace. The
waitlist is what that costs: every signup on the deploy is filed `preference` on the shared subject,
so "Jayesh at Silurian — He wants agents to automate customer-data ingestion" — a row that names its
person perfectly — is listed under "How this workspace has said it wants things done".

`stated` is the row shape the prompt asks for and the reader scans: one subject, an em dash, one
sentence about it, inside the row budget, with no value the system reports about itself, and every
person by their full name.

Every corpus runs in both batchings, because the batch turned out to be the variable each dimension
moves with, and the pair holds the pages constant against it. The row budget is measured on what the
model recorded, not on the validated fact: `ExtractedFact` clips an over-budget body to its last
whole word, so a pass that overruns is invisible downstream while the words it lost are gone.

A verdict takes every sample, never the best of them, and the report carries the sample rate beside
the case rate. A wiki is written once and read as it stands, so a dimension that holds two runs in
three is a defect a member meets one visit in three — which best-of-N reports as met.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from functools import partial
from typing import cast

from pydantic import BaseModel
from ufo_ext_memory.condenser import (
    CONSOLIDATE_MAX_TOKENS,
    CONSOLIDATE_REASONING,
    CONSOLIDATE_SYSTEM,
    FACT_EXTRACT_MAX_TOKENS,
    FACT_EXTRACT_SYSTEM,
    FACT_EXTRACT_TOOL,
    FACT_EXTRACT_TOOL_DESCRIPTION,
    MAX_SUMMARY_SENTENCES,
    MAX_SUMMARY_WORDS,
    ExtractedFacts,
)
from ufo_ext_memory.store import MEMORY_BODY_MAX_CHARS, OVERVIEW_BODY_MAX_CHARS

from evals.harness.harness import (
    EvalCaseResult,
    EvalMetric,
    EvalReport,
    Json,
    JsonObject,
    digest_payload,
    is_transient_fault,
)
from evals.harness.judge import JUDGE_REVISION, JudgeLeg, ModelJudge, rubric_pass
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL
from ufo.sdk.context import ModelAccess
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock

SUITE = "wiki_generation"
SAMPLES = 3

ATTRIBUTED = "attributed"
DISTINCT = "distinct"
FILED = "filed"
STATED = "stated"

WORKSPACE_BANDS = {
    "preference": "How the team works",
    "task": "Open work",
    "decision": "Decisions",
}
"""The wiki bands whose note speaks for the workspace — "How this workspace has said it wants things
done", "What the workspace asked to have carried", "What was settled". A `memory_kind` is not a
label on a row, it is the section the row is filed under, so a stranger's stated want filed
`preference` renders as this workspace's own way of working however well the row itself names them.
The bands are `WORKSPACE_TOPICS` in the wiki app's `app.tsx`."""

SAMPLES_MET = "samplesMet"
SAMPLES_SCORED = "samplesScored"

WORKSPACE = "Metalcraft"
MEMBERS = ("Marshall Kiely", "Alex Graveley")
"""The workspace the corpus belongs to. Nothing in the extraction payload states it — a page's
title, stream and body are all the pass reads — so a row that hands a stranger's claim to this
company was inferred, and that inference is what the `attributed` dimension counts."""

ROW_SEPARATOR = " — "
SENTENCE_END = re.compile(r"(?<=[a-z0-9)`\"'])[.!?](?=\s|$)", re.IGNORECASE)
FIRST_PERSON = re.compile(r"\b(?:we|our|ours|us|ourselves|i|my|mine)\b(?![-\w])", re.IGNORECASE)
"""First person in a row. The trailing guard keeps `us-east-1` out: a region is not a pronoun, and
every service notice the deploy syncs carries one."""
UNRESOLVED_HEAD = re.compile(
    r"^(?:the|this|that|these|those|a|an)\s+\w+$|^(?:the\s+)?(?:company|customer|team|sender|"
    r"email|thread|message|meeting|renewal|deal|round|decision|update|note)s?$",
    re.IGNORECASE,
)

COMPREHENSION = (
    "No line hands one party's claim to another. A reader who works at "
    f"{WORKSPACE} can tell, from the line alone, whether it states what {WORKSPACE} or a person "
    "there did, or what an outside company or person did, and no line about an outside party "
    f"reads as this reader's own news. A line about {WORKSPACE}'s own work may be headed by its "
    f"topic and need not name {WORKSPACE}."
)
GRADING: JsonObject = {
    ATTRIBUTED: (
        "every row names one of the parties its source page belongs to, no row takes "
        f"{WORKSPACE} or one of its members as its subject when the page belongs to a stranger, no "
        "row is written in the first person, and a reader cannot mistake a stranger's news for "
        "their own company's"
    ),
    DISTINCT: (
        "each claim the corpus states more than once reaches the wiki once, and the batch stays "
        "inside the row count its pages earn"
    ),
    FILED: (
        "no row off an outside party's page carries a memory_kind the wiki renders under a band "
        f"that speaks for {WORKSPACE} — preference, task or decision"
    ),
    STATED: (
        f"every row is one subject, an em dash, and one sentence about it inside "
        f"{MEMORY_BODY_MAX_CHARS} characters, its subject is named rather than implied, and it "
        "carries no value the source reports about itself"
    ),
}


@dataclass(frozen=True)
class SourcePage:
    """One page exactly as `FactDeriver` sends it, and the parties its claims belong to. On an
    outside party's page a row must name one of `owners`, and every spelling that party is known by
    counts; on the workspace's own page the rule is off, since the wiki belongs to it. `noise` is
    what the page reports about itself, which no row may carry."""

    ref: str
    title: str
    stream: str
    body: str
    owners: tuple[str, ...]
    noise: tuple[str, ...] = ()

    @property
    def is_ours(self) -> bool:
        return any(owner == WORKSPACE or owner in MEMBERS for owner in self.owners)

    def payload(self) -> JsonObject:
        return {
            "ref": self.ref,
            "title": self.title,
            "stream": self.stream,
            "body": self.body,
            "owners": list(self.owners),
            "noise": list(self.noise),
        }


@dataclass(frozen=True)
class Claim:
    """One claim the corpus states more than once. A row carries it when the row carries every one
    of `words`. A claim says nothing about how a row should be headed — a wiki row about its own
    workspace's decision is well headed by the topic — so this is a duplication probe alone."""

    name: str
    words: tuple[str, ...]

    def carried_by(self, row: str) -> bool:
        folded = row.casefold()
        return all(word.casefold() in folded for word in self.words)

    def payload(self) -> JsonObject:
        return {"name": self.name, "words": list(self.words)}


@dataclass(frozen=True)
class Failures:
    reasons: tuple[str, ...]
    evidence: JsonObject

    @property
    def passed(self) -> bool:
        return not self.reasons


@dataclass(frozen=True)
class Row:
    """One entry the extraction recorded, kept as the model wrote it: `body` is pre-clip, so a row
    that overran the budget is still measurable."""

    page_ref: str
    body: str
    kind: str = "fact"

    @property
    def head(self) -> str:
        return self.body.split(ROW_SEPARATOR, 1)[0].strip() if ROW_SEPARATOR in self.body else ""


@dataclass(frozen=True)
class ExtractionCase:
    """Source pages as the deriver delivers them — one model pass per batch, because `apply` groups
    a change set into `EXTRACT_PAGE_BATCH` pages and each group is one call — and what the wiki
    those passes write must be true of. The batching is the case: a claim two pages state is visible
    to itself inside one pass and invisible across two, and the wiki a member reads is the union."""

    name: str
    batches: tuple[tuple[SourcePage, ...], ...]
    max_rows: int
    repeated: tuple[Claim, ...] = ()
    production: tuple[Row, ...] = ()
    rewrite: tuple[Row, ...] = ()

    @property
    def pages(self) -> tuple[SourcePage, ...]:
        return tuple(page for batch in self.batches for page in batch)

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (ATTRIBUTED, DISTINCT, FILED, STATED)

    @property
    def instruction(self) -> str:
        return "Extract the wiki rows of these source pages\n\n" + "\n\n".join(
            f"{page.stream} — {page.title}\n{page.body}" for page in self.pages
        )

    def parts(self, rows: tuple[Row, ...]) -> dict[str, Failures]:
        by_ref = {page.ref: page for page in self.pages}
        return {
            ATTRIBUTED: _attributed(rows, by_ref),
            DISTINCT: self._distinct(rows),
            FILED: _filed(rows, by_ref),
            STATED: self._stated(rows, by_ref),
        }

    def _distinct(self, rows: tuple[Row, ...]) -> Failures:
        reasons: list[str] = []
        restated: JsonObject = {}
        for claim in self.repeated:
            carriers = tuple(row.body for row in rows if claim.carried_by(row.body))
            restated[claim.name] = list(carriers)
            if len(carriers) > 1:
                reasons.append(f"{len(carriers)} rows carry the {claim.name} claim")
        if len(rows) > self.max_rows:
            reasons.append(f"{len(rows)} rows over the {self.max_rows} these pages earn")
        return Failures(tuple(reasons), {"rowCount": len(rows), "restated": restated})

    def _stated(self, rows: tuple[Row, ...], by_ref: dict[str, SourcePage]) -> Failures:
        reasons: list[str] = []
        for index, row in enumerate(rows):
            for defect in _shape(row, by_ref.get(row.page_ref)):
                reasons.append(f"row {index}: {defect}")
        return Failures(tuple(reasons), {"rows": [row.body for row in rows]})

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "batches": [[page.payload() for page in batch] for batch in self.batches],
            "maxRows": self.max_rows,
            "repeated": [claim.payload() for claim in self.repeated],
        }


@dataclass(frozen=True)
class OverviewCase:
    """The aged rows the consolidation pass folds into one Overview paragraph, and the parties that
    paragraph has to keep apart."""

    name: str
    facts: tuple[str, ...]
    parties: tuple[str, ...]
    production: tuple[Row, ...] = ()
    rewrite: tuple[Row, ...] = ()

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (ATTRIBUTED, STATED)

    @property
    def instruction(self) -> str:
        return "Consolidate these wiki rows into one Overview\n\n" + "\n".join(self.facts)

    def parts(self, rows: tuple[Row, ...]) -> dict[str, Failures]:
        summary = rows[0].body if rows else ""
        return {ATTRIBUTED: self._attributed(summary), STATED: self._stated(summary)}

    def _attributed(self, summary: str) -> Failures:
        folded = summary.casefold()
        missing = tuple(party for party in self.parties if party.casefold() not in folded)
        reasons = [f"the Overview drops {party}" for party in missing]
        person = FIRST_PERSON.search(summary)
        if person is not None:
            reasons.append(f"the Overview writes {person.group(0)!r} in the first person")
        return Failures(tuple(reasons), {"summary": summary, "missingParties": list(missing)})

    def _stated(self, summary: str) -> Failures:
        words = len(summary.split())
        sentences = len(SENTENCE_END.findall(summary))
        reasons: list[str] = []
        if words > MAX_SUMMARY_WORDS:
            reasons.append(f"{words} words over the {MAX_SUMMARY_WORDS} the prompt asks for")
        if sentences > MAX_SUMMARY_SENTENCES:
            reasons.append(f"{sentences} sentences over the {MAX_SUMMARY_SENTENCES} allowed")
        if len(summary) > OVERVIEW_BODY_MAX_CHARS:
            reasons.append(f"{len(summary)} characters over the {OVERVIEW_BODY_MAX_CHARS} stored")
        return Failures(
            tuple(reasons), {"words": words, "sentences": sentences, "characters": len(summary)}
        )

    def payload(self) -> JsonObject:
        return {"name": self.name, "facts": list(self.facts), "parties": list(self.parties)}


type WikiCase = ExtractionCase | OverviewCase


def _attributed(rows: tuple[Row, ...], by_ref: dict[str, SourcePage]) -> Failures:
    """Whose claim each row carries. A row naming none of its page's parties is the defect a member
    reports as "news about other people appears as news about us": the reader supplies the missing
    subject, and on their own wiki the subject they supply is their own company. A row that heads
    with the wrong party is the same defect stated outright, and a claim restated on a page both
    companies wrote is where it lands — so an owned claim is checked against its owner rather than
    against the page."""
    reasons: list[str] = []
    strangers = (WORKSPACE, *MEMBERS)
    for index, row in enumerate(rows):
        page = by_ref.get(row.page_ref)
        if page is None:
            reasons.append(f"row {index} carries no source page")
            continue
        folded = row.body.casefold()
        if not page.is_ours and not any(owner.casefold() in folded for owner in page.owners):
            reasons.append(f"row {index} names none of {', '.join(page.owners)}")
        head = row.head.casefold()
        if not page.is_ours and any(stranger.casefold() in head for stranger in strangers):
            reasons.append(f"row {index} takes {row.head!r} as the subject of a stranger's page")
        person = FIRST_PERSON.search(row.body)
        if person is not None:
            reasons.append(f"row {index} writes {person.group(0)!r} in the first person")
    return Failures(
        tuple(reasons),
        {"rows": [{"page": row.page_ref, "body": row.body, "head": row.head} for row in rows]},
    )


def _filed(rows: tuple[Row, ...], by_ref: dict[str, SourcePage]) -> Failures:
    """Which band each row lands in. `memory_kind` is not a label the reader ever sees — it is the
    section the wiki files the row under, and three of those sections speak for the workspace. A
    waitlist signup filed `preference` is rendered under "How this workspace has said it wants
    things done", so the page asserts a stranger's want as the team's own way of working while the
    row itself names them perfectly. No row can be read alone to catch it."""
    reasons: list[str] = []
    for index, row in enumerate(rows):
        page = by_ref.get(row.page_ref)
        if page is None or page.is_ours:
            continue
        band = WORKSPACE_BANDS.get(row.kind)
        if band is not None:
            reasons.append(
                f"row {index} files an outside party's claim as {row.kind!r}, "
                f"which the wiki renders under {band!r}"
            )
    return Failures(
        tuple(reasons),
        {"kinds": [{"page": row.page_ref, "kind": row.kind, "body": row.body} for row in rows]},
    )


def _shape(row: Row, page: SourcePage | None) -> tuple[str, ...]:
    """The row shape the extraction prompt states: one subject, an em dash, one sentence."""
    defects: list[str] = []
    if row.body.count(ROW_SEPARATOR) != 1:
        defects.append("no subject and em dash")
    elif not row.head:
        defects.append("an empty subject")
    elif UNRESOLVED_HEAD.match(row.head):
        defects.append(f"an implied subject, {row.head!r}")
    if len(row.body) > MEMORY_BODY_MAX_CHARS:
        defects.append(f"{len(row.body)} characters over {MEMORY_BODY_MAX_CHARS}")
    ends = SENTENCE_END.findall(row.body)
    if len(ends) != 1 or SENTENCE_END.search(row.body.rstrip()) is None:
        defects.append(f"{len(ends)} sentence endings")
    elif not row.body.rstrip().endswith((".", "!", "?")):
        defects.append("no end punctuation")
    if page is not None:
        folded = row.body.casefold()
        carried = tuple(value for value in page.noise if value.casefold() in folded)
        if carried:
            defects.append(f"a value the source reports about itself: {', '.join(carried)}")
        for owner in page.owners:
            parts = owner.split()
            if len(parts) != 2 or owner.casefold() in folded:
                continue
            if parts[0].casefold() in folded:
                defects.append(f"a person by a short name, {parts[0]!r}")
    return tuple(defects)


DAILY_SYNC_NOTES = """Notes: "daily sync" Aug 24, 2026

Product design: the team favoured dashboards over chat-only interfaces, and agreed that a member
may fork an existing app.

Alex Graveley will build private app copies that are edited before they are published, and will
split staging from production. Editing an app that already exists stays off until the new-app flow
ships.

Marshall Kiely will pick 5 to 10 trial users for a case study, and will ask Ada Yang at Perihelion
whether she is free to take one of the seats.

Latency: moving work into the connected tools cuts the call count. The group takes that next
week."""

DAILY_SYNC_THREAD = """#standup — thread on "daily sync" Aug 24, 2026
Posted by the Notetaker app

Product design: the team favoured dashboards over chat-only interfaces, and agreed that a member
may fork an existing app.

Alex Graveley will build private app copies that are edited before they are published, and will
split staging from production.

Marshall Kiely will pick 5 to 10 trial users for a case study.

Latency: moving work into the connected tools cuts the call count."""

SERVICE_NOTICE = """From: no-reply@aws.amazon.com

A new service update is available for your Amazon ElastiCache cluster ufo-testing-redis in
us-east-1. Update elasticache-july-patch-update-202607 is marked important. Amazon Web Services
recommends that you apply it by 30 August 2026 05:59:59 UTC. The cluster does not update itself
after the due date.

Account: 899147036157. Region: us-east-1. This message was generated automatically. Do not reply."""

PULL_REQUEST = """stabilize remote SWE-bench runs (#2311) — open, opened by Alex Graveley

A remote SWE-bench case that lost its sandbox mid-command left the run wedged until the job
ceiling. The lease is now renewed from the command loop rather than at launch, so a box that goes
away is seen at the next command instead of at the stop.

Validation included `make test-one FILE=core/tests/test_swebench_runner.py`.
Validation included `make test-one FILE=core/tests/test_sandbox_lease.py`.
Frontend checks passed 985 tests across 52 files.
Extension tests passed: 474 web, 183 hosted-sites, and 150 ingress tests.
`make check` passed all gates, including mypy, actionlint, and cargo.
The source branch is `swebench-lease` at commit c72efc24e93d89d222ed57aff0b9e0adb16429cf.
The pull request has no assignee, no labels, and is not a draft."""

PR_COMMENT = """comments/metalcraftai/ufo/5417259299

Marshall Kiely wrote: renewing the lease from the command loop is the right cut — a box that goes
away is then seen at the next command rather than at the stop. Approving.

Merged as a squash at 23:52:18Z."""

PR_NOTIFICATION = """Re: [metalcraftai/ufo] stabilize remote SWE-bench runs (#2311)

Alex Graveley merged pull request #2311 into main.

The lease is renewed from the command loop, so a sandbox that goes away is seen at the next
command instead of at the stop.

You are receiving this because you are subscribed to this thread. Reply to this email directly, or
view it on GitHub. Message ID: <metalcraftai/ufo/pull/2311/issue_event/5417259299@github.com>"""

SYNC_PAGES = (
    SourcePage(
        "page/daily-sync-message",
        'Notes: "daily sync" Aug 24, 2026',
        "messages",
        DAILY_SYNC_NOTES,
        owners=(WORKSPACE, *MEMBERS),
    ),
    SourcePage(
        "page/daily-sync-thread",
        'Notes: "daily sync" Aug 24, 2026',
        "messages",
        DAILY_SYNC_THREAD,
        owners=(WORKSPACE, *MEMBERS),
    ),
)
"""One meeting, two pages. Measured on the testing deploy 2026-08-26: every daily sync since 6
August carries a page pair a minute apart, and both sets of rows are live — the pages differ, so
`supersede_page_facts` cannot reach across them, and `MemoryDeduper` never reads a page-derived
row at all."""

SYNC_CLAIMS = (
    Claim("fork an app", ("fork",)),
    Claim("dashboards over chat", ("dashboard",)),
    Claim("private app copies", ("private app cop",)),
    Claim("staging and production", ("staging",)),
    Claim("trial users", ("5 to 10",)),
    Claim("tool call count", ("call count",)),
)

NOTICE_PAGE = SourcePage(
    "page/service-notice",
    "A new Amazon ElastiCache service update is available",
    "messages",
    SERVICE_NOTICE,
    owners=("Amazon Web Services", "Amazon ElastiCache", "AWS"),
    noise=("899147036157", "generated automatically", "Do not reply"),
)
"""A stranger's announcement about our own cluster — the self-versus-others question in the shape
the deploy meets it. Measured 2026-08-26: this page, at one revision, carries two live rows that
say the same thing, because one of them added the account number and the clock time and so fell
under `EXTRACT_RESTATEMENT_OVERLAP`."""

NOTICE_CLAIMS = (Claim("elasticache update", ("elasticache-july-patch-update-202607",)),)

PR_PAGES = (
    SourcePage(
        "page/pull-request-2311",
        "stabilize remote SWE-bench runs",
        "pull_requests",
        PULL_REQUEST,
        owners=("Pull request 2311", "Alex Graveley", WORKSPACE),
        noise=(
            "make test-one",
            "985",
            "474",
            "make check",
            "c72efc24",
            "no assignee",
            "not a draft",
        ),
    ),
    SourcePage(
        "page/pull-request-2311-comment",
        "comments/metalcraftai/ufo/5417259299",
        "comments",
        PR_COMMENT,
        owners=("Pull request 2311", "Marshall Kiely", WORKSPACE),
    ),
    SourcePage(
        "page/pull-request-2311-notification",
        "Re: [metalcraftai/ufo] stabilize remote SWE-bench runs (#2311)",
        "messages",
        PR_NOTIFICATION,
        owners=("Pull request 2311", "Alex Graveley", WORKSPACE),
        noise=("Message ID", "subscribed to this thread", "view it on GitHub"),
    ),
)
"""A pull request, the comment on it, and the notification of its merge: three pages, one change.
Measured 2026-08-26, `pull_requests` is the heaviest stream on the deploy at 4.33 rows a page, and
a single request wrote fifteen — most of them test counts and gate names the prompt tells the pass
to leave to the system."""

PR_CLAIMS = (
    Claim("lease renewal", ("lease",)),
    Claim("merge", ("merge",)),
)

SYNC_PRODUCTION = (
    Row(
        "page/daily-sync-message",
        "Product design — The team favored dashboards over relying solely on chat interfaces.",
    ),
    Row(
        "page/daily-sync-message",
        "Alex G — Will implement private app copies for editing before publishing changes.",
    ),
    Row("page/daily-sync-message", "Alex G — Will separate application staging and production."),
    Row(
        "page/daily-sync-message",
        "Marshall Kiely — Will select 5 to 10 potential users for platform testing and a case "
        "study.",
    ),
    Row(
        "page/daily-sync-message",
        "The group — Will design a near-term mechanism for app forking and versioning.",
    ),
    Row(
        "page/daily-sync-thread",
        "Daily sync — The team favored dashboards over chat-only interfaces and decided users can "
        "fork apps.",
    ),
    Row(
        "page/daily-sync-thread",
        "Alex G — Alex G will develop private app copies for editing with separate staging and "
        "production.",
    ),
    Row(
        "page/daily-sync-thread",
        "Marshall Kiely — Marshall Kiely will select 5 to 10 trial users and provide credits for a "
        "case study.",
    ),
    Row(
        "page/daily-sync-thread",
        "The group — The group will design a near-term mechanism for app forking and versioning.",
    ),
)
"""The deploy's own rows for one daily sync, read from the testing workspace on 2026-08-26 — both
pages of the pair, every row live. Each claim lands twice, the surname goes, and the second pass
opens its sentence with the subject it just wrote in the head."""

SYNC_REWRITE = (
    Row(
        "page/daily-sync-message",
        f"{WORKSPACE} — Favoured dashboards over chat-only interfaces on 24 August 2026.",
    ),
    Row("page/daily-sync-message", f"{WORKSPACE} — Lets a member fork an app that already exists."),
    Row(
        "page/daily-sync-message",
        "Alex Graveley — Builds private app copies that are edited before they are published.",
    ),
    Row(
        "page/daily-sync-message",
        "Alex Graveley — Splits app staging from app production.",
    ),
    Row(
        "page/daily-sync-message",
        "Marshall Kiely — Picks 5 to 10 trial users for a case study.",
    ),
    Row(
        "page/daily-sync-message",
        f"{WORKSPACE} — Cuts the tool call count by moving work into the connected tools.",
    ),
)

WAITLIST_FORM = """Form Responses 1 — UFO.ai waitlist

Timestamp | Name | Company | What do you want agents to do?
2026-08-19 09:14 | Jayesh Rana | Silurian | Automate customer-data ingestion and quality assurance.
2026-08-19 11:02 | Jeremy Vance | PitPro Automation | Handle internal admin workflows, and build a
computer-vision data-labeling pipeline.
2026-08-20 08:41 | Bruno Salas | OfferLab | Handle business development.
2026-08-20 15:23 | Andrew Fenn | FreeCode | Run go-to-market on autopilot so I focus on product."""

WAITLIST_EMAIL = """Set up your account

New waitlist signups since Monday:

Jayesh Rana (Silurian) wants agents to automate customer-data ingestion and quality assurance.
Jeremy Vance (PitPro Automation) wants agents for internal admin workflows and a computer-vision
data-labeling pipeline.
Bruno Salas (OfferLab) wants agents working on business development.
Andrew Fenn (FreeCode) wants his go-to-market work on autopilot so he can focus on product."""

WAITLIST_PAGES = (
    SourcePage(
        "page/waitlist-form",
        "Form Responses 1",
        "sheet_values",
        WAITLIST_FORM,
        owners=("Jayesh Rana", "Jeremy Vance", "Bruno Salas", "Andrew Fenn"),
        noise=("Timestamp",),
    ),
    SourcePage(
        "page/waitlist-email",
        "Set up your account",
        "messages",
        WAITLIST_EMAIL,
        owners=("Jayesh Rana", "Jeremy Vance", "Bruno Salas", "Andrew Fenn"),
    ),
)
"""The waitlist, on the two pages that carry it — the signup form and the digest email. Read from
the testing deploy 2026-08-26, where every one of these people is filed `preference` on the shared
subject, so the wiki lists them under "How the team works" as this workspace's own way of working.
Each row names its person correctly; the section is what makes the page lie."""

WAITLIST_CLAIMS = (
    Claim("Jayesh's want", ("Jayesh",)),
    Claim("Jeremy's want", ("Jeremy",)),
    Claim("Bruno's want", ("Bruno",)),
    Claim("Andrew's want", ("Andrew",)),
)

WAITLIST_PRODUCTION = (
    Row(
        "page/waitlist-email",
        "Jayesh — Wants to automate customer data ingestion and quality assurance with UFO.ai.",
        "preference",
    ),
    Row(
        "page/waitlist-email",
        "Jeremy — He requested internal admin workflows and a computer-vision data-labeling "
        "pipeline.",
        "preference",
    ),
    Row(
        "page/waitlist-email",
        "Bruno — Wants agents working on business development, according to Marshall Kiely.",
        "preference",
    ),
    Row(
        "page/waitlist-email",
        "Andrew — He wants his go-to-market work on autopilot so he can focus on product.",
        "preference",
    ),
    Row(
        "page/waitlist-form",
        "Jayesh at Silurian — He wants agents to automate customer-data ingestion and quality "
        "assurance.",
        "preference",
    ),
    Row(
        "page/waitlist-form",
        "Bruno at OfferLab — He wants agents to handle business development.",
        "preference",
    ),
    Row(
        "page/waitlist-form",
        "Andrew at FreeCode — He wants UFO to run his go-to-market on autopilot so he can focus on "
        "product.",
        "preference",
    ),
)
"""The rows behind the screenshot, read from the testing deploy 2026-08-26. Every one is filed
`preference`, every person is named by a first name alone, each signup is written twice, and one
row carries the sourcing hedge "according to Marshall Kiely"."""

WAITLIST_REWRITE = (
    Row(
        "page/waitlist-form",
        "Jayesh Rana — Joined the waitlist for Silurian, to automate customer-data ingestion.",
        "event",
    ),
    Row(
        "page/waitlist-form",
        "Jeremy Vance — Joined the waitlist for PitPro Automation, for admin workflows.",
        "event",
    ),
    Row(
        "page/waitlist-form",
        "Bruno Salas — Joined the waitlist for OfferLab, for business development.",
        "event",
    ),
    Row(
        "page/waitlist-form",
        "Andrew Fenn — Joined the waitlist for FreeCode, to run go-to-market.",
        "event",
    ),
)

EXTRACTION_CASES = (
    ExtractionCase("daily-sync-in-one-pass", (SYNC_PAGES,), max_rows=6, repeated=SYNC_CLAIMS),
    ExtractionCase(
        "daily-sync-across-passes",
        tuple((page,) for page in SYNC_PAGES),
        max_rows=6,
        repeated=SYNC_CLAIMS,
        production=SYNC_PRODUCTION,
        rewrite=SYNC_REWRITE,
    ),
    ExtractionCase(
        "service-notice-in-one-pass", ((NOTICE_PAGE,),), max_rows=3, repeated=NOTICE_CLAIMS
    ),
    ExtractionCase("pull-request-in-one-pass", (PR_PAGES,), max_rows=4, repeated=PR_CLAIMS),
    ExtractionCase(
        "pull-request-across-passes",
        tuple((page,) for page in PR_PAGES),
        max_rows=4,
        repeated=PR_CLAIMS,
    ),
    ExtractionCase("waitlist-in-one-pass", (WAITLIST_PAGES,), max_rows=4, repeated=WAITLIST_CLAIMS),
    ExtractionCase(
        "waitlist-across-passes",
        tuple((page,) for page in WAITLIST_PAGES),
        max_rows=4,
        repeated=WAITLIST_CLAIMS,
        production=WAITLIST_PRODUCTION,
        rewrite=WAITLIST_REWRITE,
    ),
)
"""Every corpus that spans pages runs in both batchings, because the batch is the variable the pair
holds everything else constant against. One pass is what a backfill of a settled source gives the
deriver; a page at a time is what a live sync gives it, and the deploy's own pairs arrive a minute
apart, which is two ticks."""

OVERVIEW_CASES = (
    OverviewCase(
        "overview-keeps-the-parties-apart",
        (
            "Pull request 2311 — Renews the sandbox lease from the command loop.",
            "Alex Graveley — Merged pull request 2311 into main on 24 August 2026.",
            "Amazon Web Services — Asks for ElastiCache update "
            "elasticache-july-patch-update-202607 by 30 August 2026.",
            f"{WORKSPACE} — Runs the ufo-testing-redis cluster the ElastiCache update applies to.",
            "Marshall Kiely — Picks 5 to 10 trial users for a case study.",
            "Ada Yang — Was asked whether Perihelion can take one of the trial seats.",
        ),
        parties=("Amazon Web Services", WORKSPACE, "Ada Yang"),
    ),
)
CASES: tuple[WikiCase, ...] = (*EXTRACTION_CASES, *OVERVIEW_CASES)


class RecordedRow(BaseModel):
    """One entry of the extraction's `record_facts` arguments, read before `ExtractedFact` clips it
    to the row budget — what the pass wrote, not what the store kept."""

    page_id: str = ""
    body: str = ""
    memory_kind: str = "fact"


@dataclass(frozen=True)
class Generated:
    """One generation, scored on every dimension its case declares. A sample the provider dropped
    carries no parts and leaves the denominator instead of counting as a failure."""

    parts: dict[str, Failures]
    evidence: JsonObject
    excluded: bool = False


@dataclass(frozen=True)
class WikiGenerationSuite:
    cases: tuple[WikiCase, ...]
    digest: str
    samples: int = SAMPLES
    selected: frozenset[str] = frozenset()

    def names(self, case: WikiCase) -> tuple[str, ...]:
        every = tuple(f"{case.name}-{dimension}" for dimension in case.dimensions)
        return tuple(name for name in every if not self.selected or name in self.selected)

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        writer = cast("ModelJudge | None", target.simulator)
        if writer is None:
            raise RuntimeError(f"the {SUITE} suite requires its background writer leg")
        if target.judge is None:
            raise RuntimeError(f"the {SUITE} suite requires its attribution judge leg")
        wanted = tuple(case for case in self.cases if self.names(case))
        generated = await gather_cases(
            slots,
            tuple(partial(self._source, case, writer.model, target.judge) for case in wanted),
        )
        cases = tuple(result for source in generated for result in source)
        return EvalReport(
            name=SUITE,
            suite=SUITE,
            digest=self.digest,
            cases=cases,
            metrics=(EvalMetric(name="sample_pass_rate", value=sample_rate(cases)),),
        )

    async def _source(
        self, case: WikiCase, writer: ModelAccess, judge: JudgeLeg
    ) -> tuple[EvalCaseResult, ...]:
        generated = [await self._sample(case, writer, judge) for _ in range(self.samples)]
        kept = frozenset(self.names(case))
        return tuple(
            self._scored(case, dimension, generated)
            for dimension in case.dimensions
            if f"{case.name}-{dimension}" in kept
        )

    async def _sample(self, case: WikiCase, writer: ModelAccess, judge: JudgeLeg) -> Generated:
        try:
            rows, evidence = await self._generate(case, writer)
        except Exception as error:
            fault = type(error).__name__
            return Generated({}, {"fault": f"{fault}: {error}"}, is_transient_fault(fault))
        parts = case.parts(rows)
        if isinstance(case, ExtractionCase) and parts[ATTRIBUTED].passed:
            parts = parts | {ATTRIBUTED: await self._judged(case, rows, parts[ATTRIBUTED], judge)}
        return Generated(parts, evidence)

    async def _judged(
        self, case: WikiCase, rows: tuple[Row, ...], counted: Failures, judge: JudgeLeg
    ) -> Failures:
        """The one attribution question no checker decides — whether a reader takes a stranger's
        news for their own company's — put only to rows the countable half already passed, the
        order the capability harness grades in. It is asked of rows alone: the criterion answers
        line by line, and an Overview is one paragraph."""
        answer = "\n".join(row.body for row in rows)
        verdict = await rubric_pass(case.instruction, answer, (COMPREHENSION,), judge)
        evidence = counted.evidence | {"comprehension": verdict.reason}
        return Failures(() if verdict.passed else (verdict.reason,), evidence)

    async def _generate(
        self, case: WikiCase, writer: ModelAccess
    ) -> tuple[tuple[Row, ...], JsonObject]:
        match case:
            case ExtractionCase():
                rows = await self._rows(case, writer)
                return rows, {"rows": [{"page": row.page_ref, "body": row.body} for row in rows]}
            case OverviewCase():
                summary = await self._summary(case, writer)
                return (Row("", summary),), {"summary": summary}

    async def _rows(self, case: ExtractionCase, writer: ModelAccess) -> tuple[Row, ...]:
        """The wiki the case's pages produce: one extraction pass per batch, in delivery order, and
        the rows they wrote together. The passes run in sequence because that is how a source
        delivers — and because a pass has no sight of what an earlier one already recorded."""
        rows: list[Row] = []
        for batch in case.batches:
            rows.extend(await self._pass(batch, writer))
        return tuple(rows)

    async def _pass(self, batch: tuple[SourcePage, ...], writer: ModelAccess) -> tuple[Row, ...]:
        """One `FactDeriver` extraction pass, with every field it sends: `title` and `stream` are
        where a page states which party it belongs to, so a payload without them measures the prompt
        against a source the deploy never gives it."""
        payload = {
            "pages": [
                {
                    "page_id": page.ref,
                    "title": page.title,
                    "stream": page.stream,
                    "body": page.body,
                }
                for page in batch
            ]
        }
        reply = await writer.turn(
            ModelRequest(
                model=writer.model,
                system=FACT_EXTRACT_SYSTEM,
                messages=(
                    Message(role="user", content=json.dumps(payload, separators=(",", ":"))),
                ),
                max_tokens=FACT_EXTRACT_MAX_TOKENS,
                conversation_cache_ttl="5m",
                tools=(
                    ToolSchema(
                        name=FACT_EXTRACT_TOOL,
                        description=FACT_EXTRACT_TOOL_DESCRIPTION,
                        input_schema=ExtractedFacts.model_json_schema(),
                    ),
                ),
                tool_choice=FACT_EXTRACT_TOOL,
                reasoning="off",
            )
        )
        entries = _recorded(reply, FACT_EXTRACT_TOOL).get("facts")
        if not isinstance(entries, list):
            raise ValueError(f"{FACT_EXTRACT_TOOL} arguments carry no facts list")
        recorded = tuple(RecordedRow.model_validate(entry) for entry in entries)
        return tuple(Row(row.page_id, row.body.strip(), row.memory_kind) for row in recorded)

    async def _summary(self, case: OverviewCase, writer: ModelAccess) -> str:
        """The consolidator's own pass over the case's aged rows."""
        request = ModelRequest(
            model=writer.model,
            system=CONSOLIDATE_SYSTEM,
            messages=(
                Message(
                    role="user",
                    content=json.dumps({"facts": list(case.facts)}, separators=(",", ":")),
                ),
            ),
            max_tokens=CONSOLIDATE_MAX_TOKENS,
            conversation_cache_ttl="5m",
            reasoning=CONSOLIDATE_REASONING,
        )
        return (await writer.complete(request)).strip()

    def _scored(self, case: WikiCase, dimension: str, generated: list[Generated]) -> EvalCaseResult:
        """One verdict off every sample, not off the best one. A member reads whichever rebuild
        ran, so a dimension one sample in three gets wrong is a wiki that is wrong one time in
        three — which is what a best-of-N verdict reports as met."""
        attempts: list[Json] = []
        stated: list[str] = []
        passes = 0
        excluded = 0
        for sample in generated:
            if sample.excluded:
                excluded += 1
                attempts.append({"passed": False, "reason": "excluded", **sample.evidence})
                continue
            failures = sample.parts.get(dimension)
            met = failures is not None and failures.passed
            passes += met
            reason = (
                "; ".join(failures.reasons) or f"{dimension}: met"
                if failures is not None
                else "the pass recorded nothing to score"
            )
            if not met:
                stated.append(reason)
            attempts.append(
                {
                    "passed": met,
                    "reason": reason,
                    **sample.evidence,
                    **(failures.evidence if failures is not None else {}),
                }
            )
        scored = len(generated) - excluded
        note = f" ({excluded} infra-excluded)" if excluded else ""
        told = f": {stated[0]}" if stated else ""
        return EvalCaseResult(
            name=f"{case.name}-{dimension}",
            passed=passes == scored,
            reason=f"{passes}/{scored} samples {dimension}{note}{told}",
            evidence={
                "grading": GRADING[dimension],
                "case": case.payload(),
                "production": [row.body for row in case.production],
                "rewrite": [row.body for row in case.rewrite],
                "attempts": attempts,
                SAMPLES_MET: passes,
                SAMPLES_SCORED: scored,
                "excludedSamples": excluded,
            },
            excluded=scored == 0,
        )


def sample_rate(cases: tuple[EvalCaseResult, ...]) -> float:
    """How many of every dimension's samples held, over every scored case — the rate a case-level
    verdict cannot state, because a case is one dimension of one corpus across `SAMPLES` runs and
    its verdict says only whether all of them held."""
    counts = tuple(
        (case.evidence[SAMPLES_MET], case.evidence[SAMPLES_SCORED])
        for case in cases
        if not case.excluded
    )
    total = sum(scored for _, scored in counts if isinstance(scored, int))
    met = sum(passes for passes, _ in counts if isinstance(passes, int))
    return met / total if total else 0.0


def _recorded(reply: Message, tool: str) -> JsonObject:
    blocks = () if isinstance(reply.content, str) else reply.content
    call = next(
        (block for block in blocks if isinstance(block, ToolUseBlock) and block.name == tool),
        None,
    )
    if call is None:
        raise ValueError(f"the extraction pass recorded no {tool} call")
    return call.input


def wiki_generation_task(
    judge_model: str,
    cases: tuple[WikiCase, ...] = CASES,
    samples: int = SAMPLES,
    selected: frozenset[str] = frozenset(),
) -> EvalTask:
    """Two model legs: the writer leg is pinned to the model background jobs run on, because the
    wiki is written by a job and never by a turn, and the judge leg answers the one attribution
    question no checker decides."""
    digest = digest_payload(
        {
            "runner": "wiki-generation",
            "task": SUITE,
            "writerModel": DEFAULT_BACKGROUND_JOBS_MODEL,
            "judgeModel": judge_model,
            "samples": samples,
            "grading": GRADING,
            "comprehension": COMPREHENSION,
            "cases": [case.payload() for case in cases],
            "selected": [*sorted(selected)],
        }
    )
    suite = WikiGenerationSuite(cases=cases, digest=digest, samples=samples, selected=selected)
    return EvalTask(
        SUITE,
        SUITE,
        digest,
        tuple(name for case in cases for name in suite.names(case)),
        suite.run,
        judge_model=judge_model,
        judge_revision=JUDGE_REVISION,
        simulator_model=DEFAULT_BACKGROUND_JOBS_MODEL,
        simulator_reasoning="off",
        narrow=lambda names: wiki_generation_task(judge_model, cases, samples, frozenset(names)),
    )
