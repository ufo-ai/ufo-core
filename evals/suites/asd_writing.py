"""The two surfaces a member reads without asking for them: the radar entry a scheduled report
earns, and the wiki row a memory item becomes. Both are written by a background model pass, never
by a turn, so this suite drives those passes themselves — the report-digest writer's own standard
and tool schema, and the condenser's own extraction and consolidation prompts — the way the
ambient-reply suite drives the classifier rather than a Slack surface. A change to either standard
is then measurable by swapping the file that holds it.

Three defects, one verdict each off one generation, so an arm that fixed the vocabulary and left
the ranking alone reports exactly that. `readable` is deterministic and then judged: a token, an
unapproved word and a term the shipped entry carried are all countable, and the one question no
checker decides — whether a member who does not operate the system can act on the line — is put to
the judge only once the countable half has passed. `ranked` and `glance` are deterministic alone:
a ranking is an index and a glance budget is characters and repeated content words.

Each case carries what the pipeline shipped on 2026-08-21 and 2026-08-26 as `production` and,
where one was written, the hand rewrite as `rewrite`. Together they are the calibration: a grader
that passes today's output measures nothing, and a grader that fails a good rewrite measures the
wrong thing, so the scorer tests assert both ends on every case that has them."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from functools import partial
from typing import cast

from ufo_ext_memory.condenser import (
    CONSOLIDATE_MAX_TOKENS,
    CONSOLIDATE_REASONING,
    CONSOLIDATE_SYSTEM,
    FACT_EXTRACT_MAX_TOKENS,
    FACT_EXTRACT_SYSTEM,
    FACT_EXTRACT_TOOL,
    FACT_EXTRACT_TOOL_DESCRIPTION,
    ExtractedFacts,
)
from ufo_ext_memory.store import MEMORY_BODY_MAX_CHARS
from ufo_ext_report_digest.digest import (
    FINISH_DESCRIPTION,
    FINISH_TOOL,
    MAX_OUTPUT_TOKENS,
    DigestEntry,
    bounded,
    writing_standard,
)

from evals.harness.harness import (
    EvalCaseResult,
    EvalReport,
    Json,
    JsonObject,
    digest_payload,
    is_transient_fault,
)
from evals.harness.judge import JUDGE_REVISION, JudgeLeg, ModelJudge, rubric_pass
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.scorers import content_words, max_pairwise_overlap
from evals.harness.target import CapabilityTarget
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL
from ufo.sdk.context import ModelAccess
from ufo.sdk.models import Message, ModelRequest, ToolSchema, ToolUseBlock

SUITE = "asd_writing"
SAMPLES = 3

READABLE = "readable"
RANKED = "ranked"
GLANCE = "glance"

LEAD_BY_FIELD = 1
MAX_FIELD_OVERLAP = 0.30
MIN_NEW_CONTENT_WORDS = 3
MAX_ENTRY_CHARS = 300
MAX_ROW_CHARS = 160
MAX_OVERVIEW_CHARS = 320

SYSTEM_TOKEN = re.compile(
    r"`[^`]*`"
    r"|\b[a-z]+_[a-z_]+\b"
    r"|\b[a-z]+[A-Z][A-Za-z]*\b"
    r"|\b[0-9a-f]{8,}\b"
    r"|\d{4}-\d{2}-\d{2}T[\d:]+Z"
    r"|#\d+"
    r"|\bPR \d+\b"
)
SENTENCE = re.compile(r"[.!?;]+\s+")
ELLIPSIS = re.compile("…|[.]{3}")
UNRESOLVED_SUBJECT = re.compile(
    r"^\s*The (?:pull request|repository|file|line|run|report|sweep|change|entry|row)\b",
    re.IGNORECASE,
)
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "this",
        "to",
        "was",
        "were",
        "with",
        "you",
        "your",
    }
)

"""The product's registered technical names. ASD-STE100 rule 1.5 admits a domain word the reader is
given a name for, and without that gate the dictionary flags `request` inside `pull request`. A name
earns a row here when the product shows it to a member, never because a writer wanted the word."""
TECHNICAL_NAMES = ("code review", "pull request", "ufo review")
REGISTERED = re.compile("|".join(map(re.escape, TECHNICAL_NAMES)))

INFLECTIONS = ("s", "es", "ed", "ing", "d")

"""Every word the ASD-STE100 Issue 4 dictionary marks unapproved that occurs in software and
operations writing, against the approved alternative the dictionary names. A blocklist starter cut
from an allowlist standard: high precision, never complete, and the alternatives are the
dictionary's suggestions rather than rewrites — the entry is the finding, not the fix.

Precision is what the list is chosen for, so a word this product uses correctly is not on it. The
dictionary is drawn from aerospace maintenance manuals and rules on words a workspace writes plainly
— a severity is `critical`, a defect is one somebody `fixes`, an outage `affects` a workspace — and
each of those flagged a real radar line whose wording was right. A word earns a row here when
flagging it would improve a line the product actually shipped."""
STE_SUBSTITUTIONS = {
    "abandon": "STOP",
    "abate": "DECREASE",
    "ability": "CAN (v)",
    "able": "CAN (v)",
    "abnormality": "DEFECT (TN)",
    "absence": "NONE (pn), NOT (adv), NO (adj)",
    "absent": "MISSING, NO",
    "absolutely": "FULLY",
    "abundant": "LARGE",
    "acceptable": "PERMITTED, SATISFACTORY, SERVICEABLE",
    "acceptance": "ACCEPT (v)",
    "accessible": "ACCESS (n)",
    "accommodate": "LET",
    "accomplish": "DO or other commanding verb construction",
    "according to": "REFER (v)",
    "account for": "MAKE SURE",
    "accumulate": "COLLECT",
    "accuracy": "PRECISION",
    "achieve": "GET",
    "activate": "START, OPERATE, CONNECT",
    "activity": "WORK",
    "additional": "MORE",
    "adequate": "SUFFICIENT",
    "advise": "TELL, RECOMMEND",
    "aid": "HELP",
    "allow": "LET",
    "alter": "CHANGE",
    "amount": "QUANTITY",
    "approach": "GO NEAR",
    "appropriate": "APPLICABLE",
    "approve": "APPROVAL (n)",
    "ascertain": "MAKE SURE",
    "ask": "TELL, SPEAK",
    "assist": "HELP",
    "assume": "THINK",
    "at least": "MINIMUM (adj, n)",
    "attain": "SHOW, BE, GET",
    "attempt": "TRY, TRY (v)",
    "augment": "INCREASE",
    "avoid": "PREVENT",
    "begin": "START",
    "by means of": "WITH",
    "capability": "FUNCTION, CAN (v)",
    "capable": "CAN (v), APPROVED",
    "carry out": "DO or other commanding verb construction",
    "cease": "STOP",
    "choice": "ALTERNATIVE (adj), SELECTION",
    "close to": "NEAR (pre)",
    "combine": "MIX, PUT TOGETHER",
    "commence": "START",
    "complete": "FULL, COMPLETE (v), ALL",
    "comprise": "HAVE",
    "consequence": "BECAUSE OF (pre)",
    "consider": "THINK",
    "considerable": "LARGE, IMPORTANT, DANGEROUS",
    "consist of": "HAVE",
    "construct": "ASSEMBLE",
    "create": "MAKE, CAUSE",
    "deactivate": "STOP, DISCONNECT, ISOLATE",
    "define": "CALCULATE, SPECIFIED (adj), GIVE",
    "delete": "ERASE",
    "deliver": "SUPPLY",
    "detect": "FIND, SENSE",
    "determine": "FIND, GIVE",
    "diminish": "DECREASE",
    "discontinue": "STOP",
    "discrepancy": "DIFFERENCE",
    "dispatch": "SEND",
    "display": "SHOW",
    "dispose of": "DISCARD",
    "due to": "BECAUSE OF, BECAUSE (con)",
    "duration": "DURING (pre)",
    "effective": "GOOD",
    "efficient": "SATISFACTORY",
    "eliminate": "REMOVE, STOP, PREVENT",
    "employ": "USE",
    "enable": "LET",
    "encounter": "THERE IS/ARE, FIND",
    "end": "STOP, COMPLETE",
    "enough": "SUFFICIENT",
    "ensure": "MAKE SURE",
    "entire": "FULL, ALL (pn)",
    "essential": "MUST (v), NECESSARY",
    "establish": "MAKE SURE",
    "event": "IF (con)",
    "execute": "DO",
    "expect": "POSSIBLE (adj)",
    "facilitate": "HELP",
    "factor": "CAUSE",
    "failure": "IF … NOT",
    "feasible": "POSSIBLE",
    "final": "LAST",
    "finish": "COMPLETE",
    "function": "OPERATE, MOVE",
    "furnish": "GIVE, SUPPLY",
    "generate": "BE, GIVE, SUPPLY",
    "give rise to": "CAUSE",
    "halt": "STOP",
    "handle": "MOVE, TOUCH, CAREFUL (adj)",
    "happen": "OCCUR",
    "however": "BUT (con)",
    "impact": "HIT (v), HIT, EFFECT (n)",
    "improve": "BETTER (adj)",
    "incorporate": "INCLUDE, HAVE",
    "indicate": "SHOW",
    "inform": "TELL",
    "information": "DATA",
    "initiate": "START",
    "inspect": "INSPECTION (n), EXAMINE",
    "instead of": "ALTERNATIVE (n)",
    "kind": "TYPE",
    "locate": "FIND, ENGAGE, PUT",
    "maintain": "KEEP, MAINTENANCE (n), HOLD",
    "make certain": "MAKE SURE",
    "meet": "ALIGN, ENGAGE, TOUCH",
    "method": "PROCEDURE",
    "minimize": "MINIMUM (n)",
    "modify": "CHANGE",
    "next to": "ADJACENT TO",
    "notify": "TELL",
    "observe": "MONITOR, SEE, OBEY",
    "obtain": "GET",
    "operational": "SERVICEABLE, OPERATE (v)",
    "option": "POSSIBLE (adj)",
    "partial": "NOT FULLY",
    "people": "PERSONS, PERSONNEL",
    "per": "FOR EACH, REFER (v)",
    "perform": "DO or other commanding verb construction",
    "periodically": "INTERVAL (n)",
    "permit": "LET",
    "persist": "CONTINUE",
    "persistent": "CONTINUOUS",
    "place": "POSITION, AREA, PUT",
    "portion": "PIECE, PART",
    "position": "PUT, SET",
    "present": "BE (v), GIVE, SHOW",
    "preserve": "PRESERVATION (TN)",
    "previous": "BEFORE (con)",
    "previously": "BEFORE (con)",
    "prior to": "BEFORE (con)",
    "proceed": "CONTINUE",
    "process": "PROCEDURE, SEND",
    "produce": "CAUSE, GIVE, MAKE",
    "prohibit": "PREVENT, TELL (NOT TO)",
    "provide": "GIVE, SUPPLY",
    "purge": "REMOVE",
    "purpose": "FUNCTION, DO (v)",
    "rapid": "FAST",
    "rapidly": "QUICKLY",
    "reason": "CAUSE, BECAUSE OF (pre)",
    "reduce": "DECREASE",
    "reference": "REFER (v)",
    "repeat": "AGAIN (adv)",
    "request": "TELL (v), WRITE (v), TELL, WRITE",
    "require": "NECESSARY (adj)",
    "respond": "RESULT (n)",
    "retain": "KEEP",
    "review": "INSPECTION",
    "search": "EXAMINE",
    "select": "SET, SELECTION (n)",
    "setting": "ADJUSTMENT, POSITION, SET (v)",
    "several": "SOME",
    "shut down": "STOP",
    "significant": "IMPORTANT",
    "switch off": "STOP, SWITCH (TN)",
    "switch on": "SWITCH (TN)",
    "technique": "PROCEDURE",
    "terminate": "STOP",
    "therefore": "THUS",
    "transfer": "MOVE, MOVEMENT, SUPPLY",
    "up to": "UNTIL, THRU, MAXIMUM (n)",
    "usage": "USE (v)",
    "utilization": "USE (v)",
    "utilize": "USE",
    "various": "DIFFERENT",
    "verify": "MAKE SURE",
    "via": "THROUGH",
    "vital": "IMPORTANT",
    "whilst": "WHILE",
}
UNAPPROVED_FORMS = {
    form: approved
    for word, approved in STE_SUBSTITUTIONS.items()
    for form in (word, *(f"{word}{suffix}" for suffix in INFLECTIONS))
}
UNAPPROVED = re.compile(
    r"\b(?:" + "|".join(sorted(map(re.escape, UNAPPROVED_FORMS), key=len, reverse=True)) + r")\b"
)

COMPREHENSION = (
    "A member who has never operated this system could act on every line: each names the thing in "
    "words the product shows a member, and no line needs a part, field, job or code name to be "
    "understood."
)
GRADING: JsonObject = {
    READABLE: (
        "every line is in the reader's words: no code span, identifier, digest, timestamp or issue "
        "number, no word ASD-STE100 leaves unapproved, none of the operator terms the shipped line "
        "carried, and a member who does not operate the system can act on it"
    ),
    RANKED: (
        f"the finding the reader must act on appears by field {LEAD_BY_FIELD}, and no finding they "
        "cannot act on comes before it"
    ),
    GLANCE: (
        f"no two lines share more than {MAX_FIELD_OVERLAP:.2f} of their content words, every line "
        f"after the first adds at least {MIN_NEW_CONTENT_WORDS} words no earlier line carried, and "
        "the whole stays inside its character budget"
    ),
}


def sentences(passage: str) -> tuple[str, ...]:
    return tuple(part for part in SENTENCE.split(passage.strip()) if part.strip())


def system_tokens(passages: tuple[str, ...]) -> tuple[str, ...]:
    """Every token the writer carried over from the system rather than translating for the reader:
    a code span, a snake_case or camelCase name, a hex digest, a machine timestamp, an issue
    number."""
    return tuple(
        dict.fromkeys(
            match.group(0) for passage in passages for match in SYSTEM_TOKEN.finditer(passage)
        )
    )


def unapproved_words(passages: tuple[str, ...]) -> dict[str, str]:
    """Every unapproved word against the alternative the dictionary names for it. A word inside a
    registered technical name is the product's own name for something the reader is given, so it is
    admitted rather than flagged."""
    found: dict[str, str] = {}
    for passage in passages:
        lowered = passage.casefold()
        registered = tuple((held.start(), held.end()) for held in REGISTERED.finditer(lowered))
        for match in UNAPPROVED.finditer(lowered):
            if any(start <= match.start() and match.end() <= end for start, end in registered):
                continue
            found[match.group(0)] = UNAPPROVED_FORMS[match.group(0)]
    return found


def shipped_terms(passages: tuple[str, ...], terms: tuple[str, ...]) -> tuple[str, ...]:
    """The operator vocabulary this source's shipped line actually carried, still present."""
    joined = " ".join(passages).casefold()
    return tuple(term for term in terms if term.casefold() in joined)


def unresolved_subjects(passages: tuple[str, ...]) -> tuple[int, ...]:
    """The lines opening on a definite subject the line never names — "The pull request …" beside
    six others, where the reader cannot tell which one."""
    return tuple(
        index for index, passage in enumerate(passages) if UNRESOLVED_SUBJECT.match(passage)
    )


def anchored_field(passages: tuple[str, ...], anchors: tuple[str, ...]) -> int:
    """The first field carrying any of the anchors, as an index; -1 when none does."""
    for index, passage in enumerate(passages):
        lowered = passage.casefold()
        if any(anchor.casefold() in lowered for anchor in anchors):
            return index
    return -1


def novelty(passages: tuple[str, ...]) -> tuple[int, ...]:
    """What each line adds that no earlier one carried — the information a line buys for the line of
    the reader's attention it spends."""
    seen: frozenset[str] = frozenset()
    counts = []
    for passage in passages:
        words = content_words(passage, STOP_WORDS)
        counts.append(len(words - seen))
        seen = seen | words
    return tuple(counts)


@dataclass(frozen=True)
class Failures:
    """One dimension's verdict for one written sample: why it failed, and the numbers it failed on
    kept for the archive whether it failed or not."""

    reasons: tuple[str, ...]
    evidence: JsonObject

    @property
    def passed(self) -> bool:
        return not self.reasons


def readable(passages: tuple[str, ...], terms: tuple[str, ...]) -> Failures:
    """D1, the countable half: every line is in the reader's words."""
    tokens = system_tokens(passages)
    unapproved = unapproved_words(passages)
    shipped = shipped_terms(passages, terms)
    unresolved = unresolved_subjects(passages)
    reasons = []
    if tokens:
        reasons.append("carries system tokens: " + ", ".join(repr(token) for token in tokens))
    if unapproved:
        reasons.append(
            "carries unapproved words: "
            + ", ".join(f"{word!r} (use {approved})" for word, approved in unapproved.items())
        )
    if shipped:
        reasons.append("carries operator vocabulary: " + ", ".join(repr(term) for term in shipped))
    if unresolved:
        reasons.append(
            "opens on a subject the reader cannot resolve: "
            + ", ".join(str(index) for index in unresolved)
        )
    return Failures(
        tuple(reasons),
        {
            "systemTokens": list(tokens),
            "unapprovedWords": dict(unapproved),
            "shippedTerms": list(shipped),
            "unresolvedLines": list(unresolved),
        },
    )


def ranked(passages: tuple[str, ...], leads: tuple[str, ...], buried: tuple[str, ...]) -> Failures:
    """D2: the finding the reader must act on is the one they meet first."""
    lead = anchored_field(passages, leads)
    inert = anchored_field(passages, buried)
    reasons = []
    if lead < 0:
        reasons.append("the leading finding is absent: " + ", ".join(repr(one) for one in leads))
    elif lead > LEAD_BY_FIELD:
        reasons.append(f"the leading finding waits for field {lead}")
    elif 0 <= inert < lead:
        reasons.append(f"field {inert} leads with a finding the reader cannot act on")
    return Failures(tuple(reasons), {"leadField": lead, "inertField": inert})


def glanceable(passages: tuple[str, ...], budget: int) -> Failures:
    """D3: a fixed small budget, and no line restating another."""
    overlap = max_pairwise_overlap(passages, STOP_WORDS)
    added = novelty(passages)
    characters = sum(len(passage) for passage in passages)
    thin = tuple(
        index for index, count in enumerate(added) if index > 0 and count < MIN_NEW_CONTENT_WORDS
    )
    cut = tuple(index for index, passage in enumerate(passages) if ELLIPSIS.search(passage))
    reasons = []
    if overlap > MAX_FIELD_OVERLAP:
        reasons.append(f"two lines repeat one fact: overlap {overlap:.2f} over {MAX_FIELD_OVERLAP}")
    if thin:
        reasons.append("lines adding nothing new: " + ", ".join(str(index) for index in thin))
    if characters > budget:
        reasons.append(f"{characters} characters over the {budget} a glance buys")
    if cut:
        reasons.append("lines written to be cut: " + ", ".join(str(index) for index in cut))
    return Failures(
        tuple(reasons),
        {
            "maxFieldOverlap": round(overlap, 3),
            "newContentWords": list(added),
            "characters": characters,
        },
    )


@dataclass(frozen=True)
class RadarCase:
    """One published report, the member it is written for, and what its entry must lead with."""

    name: str
    reader: str
    report: str
    leads: tuple[str, ...]
    buried: tuple[str, ...]
    jargon: tuple[str, ...]
    production: tuple[str, ...]
    rewrite: tuple[str, ...] = ()

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (READABLE, RANKED, GLANCE)

    @property
    def instruction(self) -> str:
        return f"Write the digest entry of this report for {self.reader}\n\n{self.report}"

    def parts(self, passages: tuple[str, ...]) -> dict[str, Failures]:
        return {
            READABLE: readable(passages, self.jargon),
            RANKED: ranked(passages, self.leads, self.buried),
            GLANCE: glanceable(passages, MAX_ENTRY_CHARS),
        }

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "reader": self.reader,
            "report": self.report,
            "leads": list(self.leads),
            "buried": list(self.buried),
            "jargon": list(self.jargon),
        }


@dataclass(frozen=True)
class WikiPage:
    """One synced source page as the extraction pass meets it. A page's title and stream ride the
    payload beside its body because a body that leaves its subject implied has one only there: a
    pull request page is titled with the change, and a comment page is titled
    `comments/<repo>/<id>` and names nothing."""

    title: str
    stream: str
    body: str


@dataclass(frozen=True)
class WikiRowsCase:
    """The source pages a wiki section's rows are extracted from, and the row set a reader can use:
    `max_rows` is what the section may spend once near-duplicates have collapsed onto one row, and
    zero is a page the wiki keeps no row of at all."""

    name: str
    pages: tuple[WikiPage, ...]
    max_rows: int
    leads: tuple[str, ...]
    buried: tuple[str, ...]
    jargon: tuple[str, ...]
    production: tuple[str, ...]
    rewrite: tuple[str, ...] = ()

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (READABLE, RANKED, GLANCE) if self.leads else (READABLE, GLANCE)

    @property
    def instruction(self) -> str:
        return "Extract the facts of this page\n\n" + "\n\n".join(
            f"{page.title} ({page.stream})\n{page.body}" for page in self.pages
        )

    def parts(self, passages: tuple[str, ...]) -> dict[str, Failures]:
        parts = {READABLE: readable(passages, self.jargon), GLANCE: self._glance(passages)}
        if self.leads:
            parts[RANKED] = ranked(passages, self.leads, self.buried)
        return parts

    def _glance(self, rows: tuple[str, ...]) -> Failures:
        budget = glanceable(rows, MAX_ROW_CHARS * self.max_rows)
        reasons = list(budget.reasons)
        if len(rows) > self.max_rows:
            reasons.append(f"{len(rows)} rows over the {self.max_rows} the section earns")
        long_rows = tuple(index for index, row in enumerate(rows) if len(row) > MAX_ROW_CHARS)
        if long_rows:
            reasons.append(
                f"rows over {MAX_ROW_CHARS} characters: "
                + ", ".join(str(index) for index in long_rows)
            )
        return Failures(tuple(reasons), budget.evidence | {"rowCount": len(rows)})

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "pages": [
                {"title": page.title, "stream": page.stream, "body": page.body}
                for page in self.pages
            ],
            "maxRows": self.max_rows,
            "leads": list(self.leads),
            "buried": list(self.buried),
            "jargon": list(self.jargon),
        }


@dataclass(frozen=True)
class WikiOverviewCase:
    """The aged facts the condenser folds into one Overview paragraph. A paragraph is read one
    sentence at a time, so its sentences are the lines every check counts."""

    name: str
    facts: tuple[str, ...]
    jargon: tuple[str, ...]
    production: tuple[str, ...]
    rewrite: tuple[str, ...] = ()

    @property
    def dimensions(self) -> tuple[str, ...]:
        return (READABLE, GLANCE)

    @property
    def instruction(self) -> str:
        return "Consolidate these facts into one summary\n\n" + "\n".join(self.facts)

    def parts(self, passages: tuple[str, ...]) -> dict[str, Failures]:
        lines = tuple(line for passage in passages for line in sentences(passage))
        return {
            READABLE: readable(lines, self.jargon),
            GLANCE: glanceable(lines, MAX_OVERVIEW_CHARS),
        }

    def payload(self) -> JsonObject:
        return {"name": self.name, "facts": list(self.facts), "jargon": list(self.jargon)}


type AsdCase = RadarCase | WikiRowsCase | WikiOverviewCase


SENTINEL_REALM_REPORT = """Sentinel error loop — 21 August 2026 05:00 UTC
- QuickBooks: 0 successful syncs across 9 streams since the 20 August merge; the last success was
  19 August 22:41 UTC.
- 167 sync failures carry no failure key in telemetry; the unresolved-key field is null on every
  one of them.
- The affected QuickBooks account has no realm binding after the merge; reconnecting the account
  restores it.
- One stalled job event was recorded at 03:12 UTC and cleared on its own.
- Nightly backup, Slack delivery, and Gmail sync are unaffected."""

EVAL_FINDINGS_TODAY_REPORT = """Eval findings — 21 August 2026
- The latest sweep concluded FAILURE.
- 382 of 470 cases passed (81%), unchanged from the previous sweep's 81%.
- 5 cases newly failed; 7 cases newly passed.
- 40 rotated-digest cases flipped verdict between runs.
- memory-ingestion passed 72 of 100 cases.
- No case was excluded for infrastructure."""

COMPETITIVE_TODAY_REPORT = """Competitive intelligence — 21 August 2026
- The Assistants API shuts down in five days. No migration guidance has been published for teams
  still calling it.
- Slack expanded its code channels into marketing, legal, and IT workflows.
- OneCLI published hosted pricing starting at $499 per month.
- Two smaller vendors merged; terms undisclosed."""

PRODUCT_LEARNINGS_TODAY_REPORT = """Product learnings — 21 August 2026
- 61 HTTP routes answer requests that carry no member session.
- The unattended merge automation merged its own work while nobody was watching.
- Onboarding reached 22 of 24 steps completed.
- A blocking review verdict was posted after the web job had already gone green."""

SENTINEL_SLACK_REPORT = """Sentinel error loop — 20 August 2026
- One production Slack installation cannot prove who sent an inbound message: its bot token is
  unset, so every inbound message is dropped.
- QuickBooks recorded 60 ReadTimeout events across 9 streams; the broker has not answered.
- Nothing else changed since the previous loop."""

EVAL_FINDINGS_YESTERDAY_REPORT = """Eval findings — 20 August 2026
- The nightly sweep concluded FAILURE.
- 340 of 434 cases passed (78%).
- The hosted assistant passed 13 of 27 cases (48%).
- Skill-loading failures affected 12 latest-arm cases.
- Memory ingestion and workflow handback also failed."""

PRODUCT_CHANGES_REPORT = """Shipped — 20 August 2026
- Expired access now pauses a member's recurring tasks instead of deleting them.
- Markdown folders sync into memory nightly for hosted workspaces.
- Radar reports now have permalinks to the files they were built from."""

COMPETITIVE_YESTERDAY_REPORT = """Competitive intelligence — 20 August 2026
- Slack Code is now free on every plan, including the free tier.
- OneCLI moved policy enforcement outside the model, into the harness.
- Its SDK hooks can annotate each permission decision with the rule that made it."""

PRODUCT_LEARNINGS_YESTERDAY_REPORT = """Product learnings — 20 August 2026
- A broken install path left every button in the workspace inert; no member could click anything.
- The review gate did not run on 24 of 73 merges because no reviewer was available.
- Prompt tools consumed 76% of the fixed token budget."""

RADAR_CASES = (
    RadarCase(
        "radar-quickbooks-syncs-blocked",
        "I own the accounting integrations.",
        SENTINEL_REALM_REPORT,
        leads=("no sync", "zero successful sync", "stopped syncing", "did not sync"),
        buried=("telemetry", "failure key", "stalled job"),
        jargon=("realm-specific binding", "telemetry", "unresolved key", "stalled job event"),
        production=(
            "QuickBooks syncs blocked on missing realm; stalled job event appears",
            "Reconnect the affected QuickBooks account or apply its realm-specific binding; "
            "telemetry still omits the failure key.",
            "Nine streams have had zero successful syncs since merge",
            "QuickBooks account needs reconnection or realm-specific binding",
            "Telemetry omits the unresolved key across 167 failures",
        ),
        rewrite=(
            "QuickBooks made no sync for 5 hours.",
            "Connect the QuickBooks account again to start the syncs.",
            "167 syncs failed on nine data streams.",
        ),
    ),
    RadarCase(
        "radar-eval-sweep-failed",
        "I own the assistant's quality.",
        EVAL_FINDINGS_TODAY_REPORT,
        leads=("failed", "did not pass"),
        buried=("81%", "81 percent", "unchanged"),
        jargon=("rotated-digest", "latest-arm", "memory-ingestion"),
        production=(
            "Assistant evals remain at 81%, with seven newly passing cases",
            "The latest sweep still failed, while 40 rotated-digest cases flipped and "
            "memory-ingestion passed 72%.",
            "Latest sweep passed 382 of 470 cases",
            "Five cases newly failed; seven newly passed",
            "Memory-ingestion passed 72 of 100 cases",
        ),
        rewrite=(
            "Tests failed: 88 of 470 did not pass.",
            "Five tests that passed yesterday failed today.",
            "The memory test group passed 72 of 100 tests.",
        ),
    ),
    RadarCase(
        "radar-assistants-api-shutdown",
        "I own our pricing and packaging.",
        COMPETITIVE_TODAY_REPORT,
        leads=("five days", "shuts down", "shutdown"),
        buried=("marketing, legal", "$499"),
        jargon=("Assistants API shutdown nears", "monetizes", "infrastructure ownership"),
        production=(
            "Slack enters ops, OneCLI monetizes, Assistants API shutdown nears",
            "Workspace agents face direct competition as Slack expands beyond coding; "
            "infrastructure ownership and migration readiness matter",
            "Slack expands code channels into marketing, legal, and IT",
            "OneCLI hosted pricing starts at $499 monthly",
            "Assistants API shutdown arrives in five days without updated guidance",
        ),
    ),
    RadarCase(
        "radar-anonymous-routes",
        "I own the platform's safety.",
        PRODUCT_LEARNINGS_TODAY_REPORT,
        leads=(
            "61 web address",
            "61 address",
            "61 route",
            "did not sign in",
            "not signed in",
            "no member session",
        ),
        buried=("22", "onboarding"),
        jargon=("Babysitter", "anonymous routes", "review gate cries wolf", "blocking verdict"),
        production=(
            "Auto-merge loop, anonymous routes, review gate cries wolf",
            "The workspace's unattended automation can merge itself while exposed routes and "
            "imprecise verdicts threaten safe operation.",
            "Babysitter merged unattended work while onboarding reached 22/24",
            "61 routes answer without member sessions",
            "A blocking verdict followed a green web job",
        ),
        rewrite=(
            "61 web addresses give data to persons who did not sign in.",
            "Close the 61 open web addresses. The tool also merged its own work with no approval.",
            "One check said the work was incorrect after the web tests passed.",
        ),
    ),
    RadarCase(
        "radar-slack-inbound-dropped",
        "I run the Slack workspace.",
        SENTINEL_SLACK_REPORT,
        leads=("Slack", "dropped", "cannot receive"),
        buried=("ReadTimeout", "broker"),
        jargon=("identity proofs", "inbound attribution", "ReadTimeout", "unset bot token"),
        production=(
            "Slack production identity proofs fail; QuickBooks timeouts persist",
            "One production Slack installation blocks inbound attribution; QuickBooks remains "
            "stuck pending broker and timeout evidence.",
            "Slack identity proofs fail with unset bot token",
            "QuickBooks recorded 60 ReadTimeout events across nine streams",
        ),
    ),
    RadarCase(
        "radar-nightly-sweep-failed",
        "I own the assistant's quality.",
        EVAL_FINDINGS_YESTERDAY_REPORT,
        leads=("failed", "did not pass"),
        buried=("78%", "48%"),
        jargon=("latest-arm", "workflow handback", "skill loading"),
        production=(
            "Nightly sweep fails at 78%, hosted assistant at 48%",
            "Skill loading, memory ingestion, and workflow handback failures dominate the latest "
            "run; the sweep concluded failure.",
            "Latest sweep passed 340/434 cases, or 78%",
            "Hosted assistant passed 13/27 cases, or 48%",
            "Skill-loading failures affected 12 latest-arm cases",
        ),
    ),
    RadarCase(
        "radar-recurring-tasks-pause",
        "I run the workspace.",
        PRODUCT_CHANGES_REPORT,
        leads=("recurring task", "pause", "no longer deleted"),
        buried=("permalink", "markdown folders"),
        jargon=("durable markdown-note memory", "hosted workspaces gain"),
        production=(
            "Recurring tasks pause safely; markdown folders sync nightly",
            "Hosted workspaces gain safer task recovery and durable markdown-note memory, while "
            "radar reports become directly linkable.",
            "Expired access pauses recurring tasks instead of deleting them",
            "Markdown folders sync into memory nightly for hosted workspaces",
            "Radar reports gain permalinks to shared files",
        ),
    ),
    RadarCase(
        "radar-slack-code-free",
        "I own our pricing and packaging.",
        COMPETITIVE_YESTERDAY_REPORT,
        leads=("free", "every plan", "no charge"),
        buried=("SDK hooks", "annotate"),
        jargon=(
            "agent harness",
            "SDK hooks",
            "annotate permission decisions",
            "policy outside the model",
        ),
        production=(
            "Slack Code goes free, OneCLI becomes the agent harness",
            "The workspace's native agent surface is now contested; security and approval "
            "boundaries are becoming the differentiator.",
            "Slack Code is free on every plan",
            "OneCLI enforces policy outside the model",
            "SDK hooks can annotate permission decisions",
        ),
    ),
    RadarCase(
        "radar-buttons-inert",
        "I own the platform's safety.",
        PRODUCT_LEARNINGS_YESTERDAY_REPORT,
        leads=("button", "could not click", "nothing worked"),
        buried=("76%", "token"),
        jargon=("inert", "fixed tokens", "prompt tools", "review gate"),
        production=(
            "Workspace buttons inert; review gate missed 24 of 73 merges",
            "A broken install path blocked every interaction, while reviewer absence turned merge "
            "protection into an availability problem.",
            "Install path left every workspace button inert",
            "Review gate missed 24 of 73 merges",
            "Prompt tools consumed 76% of fixed tokens",
        ),
    ),
)

INTRODUCTION_THREAD = """From: Ivan Petrov
To: Marshall Reed, Alex Graveley
Subject: intro — Nalu Concepcion, Idler.ai

Marshall, you should book a call with Nalu Concepcion, a co-founder of Idler.ai. Her scheduling
link is in her signature.

Nalu Concepcion replied: happy to chat and learn more about what you are building. Could you send
some context ahead of the call so we make the best use of the time?

Alex Graveley replied: I will send the context this week."""

HISTORY_PAGE = """metalcraftai/ufo — repository activity, 21 August 2026
The private beta opened to outside members at 18:40 UTC; the first four accounts signed in.
The repository was created on 2026-07-07 at 20:31:50 UTC.
The repository was last updated on 2026-08-21 at 20:06:04 UTC.
The repository was last pushed to on 2026-08-21 at 20:19:18 UTC.
Pull request 2189 was created on 2026-08-21 at 20:12:15 UTC and is open.
Pull request 2190 was created at 2026-08-21T20:19:37Z and is open."""

DECISIONS_PAGE = """Connector logo review, 20 August 2026
The svgl Cal.com match was dropped: its 101x22 wordmark paints as 32x7 in a square slot.
The svgl Dart match was dropped: it represents the programming language, not the connected tool.
`intercom`, `jira`, and `bitbucket` now draw Tabler brand outlines instead.
The five Google and four Zoho sub-products return marks byte-identical to their parents, so those
nine duplicates were dropped.
Pull request #2186 changes the CSS object-position value from object-top to object-top-left."""

SEATS_PAGE = """Domain scope debugging notes, 21 August 2026
The seats unit tests fail when the LIKE pattern is left unescaped.
`workspace_by_domain` runs on an `owner_tx` connection, uses the address as the predicate, and
returns a workspace ID and nothing else.
Because `workspace_by_domain` returns only a workspace ID, no row contents cross a tenant boundary.
The `_choices` line already reads a domain.
The pull request source branch is `debug-surface-domain-scope` at commit
`c72efc24e93d89d222ed57aff0b9e0adb16429cf`.
The pull request has no assignee, no requested reviewers, no labels, and is not a draft."""

PREFERENCE_PAGE = """Design review, 19 August 2026
Rob Ryan said the direction is correct but probably slightly overshot. He asked to try 2x and then
view the screen at that size before deciding."""

REVIEW_PULL_PAGE = """metalcraftai/ufo pull request 2455 — an app's settings stand in a panel over
the screen
Opened by alexg-ufo on 26 August 2026 at 09:12 UTC, from the branch settings-panel into main.
The pull request is open, has no assignee, and carries no labels."""

REVIEW_COMMENT_PAGE = """Comment on metalcraftai/ufo pull request 2455, 26 August 2026 at 09:41 UTC
alexg-ufo wrote: @claude review this one before I merge it."""

REVIEW_EVENT_PAGE = """metalcraftai/ufo issue event 18446744 — mentioned
alexg-ufo mentioned claude[bot] on pull request 2455 on 26 August 2026 at 09:41 UTC."""

CI_RUN_PAGE = """test — workflow run 4417 of metalcraftai/ufo, 26 August 2026
The run concluded success at 08:02 UTC after 12 minutes 35 seconds, on commit a24e8236 of main.
A push from alexg-ufo started it, and it is attempt 1 of 1."""

PRICE_RULE_PAGE = """Comment on metalcraftai/ufo pull request 2461, 26 August 2026 at 14:08 UTC
Marshall Reed wrote: I will not merge the price change until Rob Ryan and the Idler.ai finance team
have both approved it, and every hosted workspace hears the new price fourteen days before it
starts."""

WIKI_ROW_CASES = (
    WikiRowsCase(
        "wiki-open-work-one-introduction",
        (WikiPage("intro — Nalu Concepcion, Idler.ai", "messages", INTRODUCTION_THREAD),),
        max_rows=2,
        leads=("call", "talk", "speak"),
        buried=(),
        jargon=("identified as a co-founder", "make the best use of the time"),
        production=(
            "Ivan suggested that Marshall book a call with Nalu, who was identified as a "
            "co-founder of Idler.ai.",
            "Nalu Concepcion requested additional context so the call could make the best use of "
            "the time.",
            "Nalu Concepcion looked forward to chatting on a call and learning more about how "
            "Idler.ai might be able to help Marshal…",
            "Ivan suggested that Marshall book a call with Nalu, identified as an Idler.ai "
            "co-founder, using the Idler scheduling li…",
            "Nalu Concepcion requested additional context from Marshall and Alex Graveley so the "
            "call could make the best use of the…",
        ),
    ),
    WikiRowsCase(
        "wiki-history-beta-leads",
        (WikiPage("metalcraftai/ufo", "repositories", HISTORY_PAGE),),
        max_rows=3,
        leads=("private beta", "beta"),
        buried=("created on", "last pushed", "last updated"),
        jargon=("2026-08-21T20:19:37Z",),
        production=(
            "The pull request was created on 2026-08-21T20:19:37Z and was open at the time "
            "represented by the source page.",
            'The repository "metalcraftai/ufo" was last updated on 2026-08-21 at 20:06:04 UTC.',
            'The repository "metalcraftai/ufo" was last pushed to on 2026-08-21 at 20:19:18 UTC.',
            'The repository "metalcraftai/ufo" was created on 2026-07-07 at 20:31:50 UTC.',
            "The pull request was created at `2026-08-21T19:59:41Z`.",
            "Pull request 2189 was created on 2026-08-21 at 20:12:15 UTC.",
        ),
        rewrite=("Pull request 2189 is open. It started on 21 August 2026.",),
    ),
    WikiRowsCase(
        "wiki-decisions-logo-review",
        (WikiPage("Connector logo review", "documents", DECISIONS_PAGE),),
        max_rows=4,
        leads=(),
        buried=(),
        jargon=("wordmark", "object-position", "byte-identical", "square slot"),
        production=(
            "The svgl Cal.com match was dropped because its 101x22 wordmark paints as 32x7 in a "
            "square slot; logo.dev supplies both…",
            "The svgl Dart match was dropped because it represents the programming language "
            "rather than the project tool connected b…",
            "`intercom`, `jira`, and `bitbucket` draw real Tabler brand outlines instead; "
            "`bitbucket` gains a Tabler outline it alwa…",
            "The five Google and four Zoho sub-products return marks byte-identical to their "
            "respective parents, so those nine dupli…",
            "Pull request #2186 changes the CSS object-position value from object-top to "
            "object-top-left.",
        ),
        rewrite=(
            "We do not use the svgl logo for Cal.com. It is too wide, and it becomes too small in "
            "a square space. We use the logo.dev picture instead.",
        ),
    ),
    WikiRowsCase(
        "wiki-facts-domain-scope",
        (WikiPage("Domain scope debugging notes", "documents", SEATS_PAGE),),
        max_rows=3,
        leads=(),
        buried=(),
        jargon=("workspace_by_domain", "_choices", "owner_tx", "LIKE pattern", "tenant boundary"),
        production=(
            "The seats unit tests fail when the LIKE pattern is left unescaped.",
            "Because `workspace_by_domain` returns only a workspace ID, no row contents cross a "
            "tenant boundary.",
            "`workspace_by_domain` runs on an `owner_tx` connection, uses the address as the "
            "predicate, and returns a workspace ID a…",
            "The `_choices` line already reads a domain.",
            "The pull request source branch is `debug-surface-domain-scope` at commit "
            "`c72efc24e93d89d222ed57aff0b9e0adb16429cf`.",
        ),
        rewrite=("The sign-in form already knows a person's company from their email address.",),
    ),
    WikiRowsCase(
        "wiki-preference-whole-line",
        (WikiPage("Design review", "documents", PREFERENCE_PAGE),),
        max_rows=2,
        leads=(),
        buried=(),
        jargon=("overshot",),
        production=(
            "Rob Ryan described the direction as correct but probably slightly overshot, "
            "requested trying 2x and then viewing screen…",
        ),
    ),
    WikiRowsCase(
        "wiki-history-one-review-across-three-pages",
        (
            WikiPage(
                "an app's settings stand in a panel over the screen",
                "pull_requests",
                REVIEW_PULL_PAGE,
            ),
            WikiPage("comments/metalcraftai/ufo/3391042118", "comments", REVIEW_COMMENT_PAGE),
            WikiPage("issue_events/metalcraftai/ufo/18446744", "issue_events", REVIEW_EVENT_PAGE),
        ),
        max_rows=1,
        leads=(),
        buried=(),
        jargon=("alexg-ufo", "claude[bot]"),
        production=(
            "MetalcraftAI UFO pull request 2455 — Alexg-ufo asked Claude to review it on "
            "26 August 2026.",
            "Alexg-Ufo — Asked Claude to review UFO pull request 2455 on 26 August 2026.",
            "Alex G-UFO — Requested a Claude review of MetalcraftAI UFO pull request 2455 on "
            "26 August 2026.",
        ),
        rewrite=("Pull request 2455 — Alex Graveley told Claude to examine it on 26 August 2026.",),
    ),
    WikiRowsCase(
        "wiki-history-ci-run-keeps-nothing",
        (WikiPage("test", "workflow_runs", CI_RUN_PAGE),),
        max_rows=0,
        leads=(),
        buried=(),
        jargon=("ci run", "workflow run"),
        production=(
            "MetalcraftAI UFO CI run a24e823 — Finished on 26 August 2026 after 12 minutes "
            "35 seconds.",
        ),
    ),
    WikiRowsCase(
        "wiki-decisions-price-change-cut",
        (WikiPage("comments/metalcraftai/ufo/3391118742", "comments", PRICE_RULE_PAGE),),
        max_rows=2,
        leads=(),
        buried=(),
        jargon=("metalcraftai/ufo",),
        production=(
            "metalcraftai/ufo pull request 2461 — Marshall Reed will not merge the price change "
            "until Rob Ryan and the…",
        ),
        rewrite=(
            "Pull request 2461 — Marshall Reed keeps it closed until Rob Ryan and the Idler.ai "
            "finance team agree.",
            "Hosted workspaces — Hear about a new price fourteen days before it starts.",
        ),
    ),
)

SWEEP_FACTS = (
    "The claude[bot] pull request sweep in metalcraftai/ufo finished on 2026-08-20 with all 18 "
    "pull requests resolved and the queue empty.",
    "Twelve pull requests were merged: 1360, 1457, 1936, 1704, 1937, 2106, 2098, 1941, and 1458 as "
    "written; 1776, 2017, and 1865 after fixes.",
    "Six pull requests were closed: 1342, 1611, 1774, and 1859 on review; 2096 and 1860 on "
    "Marshall's own call.",
    "Marshall decided an SDK re-export that names an obligation extensions must satisfy stays even "
    "with zero importers, as RequestForwarder types CliCredential.forward.",
    "Marshall decided a removal that leaves a bare registration statement no comment explains is "
    "not worth it, because a later dead-code pass can silently unregister the thing.",
    "The sweep report is at /workspace/pr-sweep/SWEEP-REPORT.md.",
)

MERGE_FACTS = (
    "The main branch of metalcraftai/ufo is guarded by ruleset 18681839 rather than classic branch "
    "protection, so /branches/main/protection returns 404.",
    'The ruleset requires five contexts: test, checks, rls, deployment, and "ufo review".',
    "The test context is an aggregator that posts only after every lane the triage job selected "
    "for the change has finished; a push to main selects every lane.",
    'The "ufo review" commit status is not posted automatically and must be created with a '
    "gh api call.",
)

WIKI_OVERVIEW_CASES = (
    WikiOverviewCase(
        "wiki-overview-pr-sweep",
        SWEEP_FACTS,
        jargon=("SDK re-export", "RequestForwarder", "dead-code pass", "zero importers"),
        production=(
            "claude[bot] PR sweep in metalcraftai/ufo — FINAL, 2026-08-20: all 18 resolved, queue "
            "empty. 12 merged (1360, 1457, 1936, 1704, 1937, 2106, 2098, 1941, 1458 as written; "
            "1776, 2017, 1865 after I fixed them), 6 closed (1342, 1611, 1774, 1859 on my review; "
            "2096 and 1860 on Marshall's own call when I put them to him in the thread). "
            "Marshall's two decisions, worth keeping as precedent for future dead-code sweeps in "
            "this repo: (1) an SDK re-export that names an obligation extensions must satisfy "
            "STAYS even with zero importers — RequestForwarder types CliCredential.forward, same "
            "reason WorkspaceCandidates came back in #1723; (2) a removal that leaves a bare "
            "registration statement no comment may explain is not worth it, because a later "
            "dead-code pass can delete it and silently unregister the thing — TURN_QUEUE.",
        ),
    ),
    WikiOverviewCase(
        "wiki-overview-merge-mechanics",
        MERGE_FACTS,
        jargon=("ruleset 18681839", "aggregator", "commit status", "check runs"),
        production=(
            "metalcraftai/ufo merge mechanics, confirmed 2026-08-20: main is guarded by ruleset "
            "18681839 (not classic branch protection, so /branches/main/protection returns 404) "
            'requiring five contexts — test, checks, rls, deployment, and "ufo review". The '
            'first four are GitHub Actions check runs; "test" is an aggregator that only posts '
            "after all ten test shards AND the three integration jobs finish, so a head with ten "
            "green shards is not yet mergeable.",
        ),
    ),
)

CASES: tuple[AsdCase, ...] = (*RADAR_CASES, *WIKI_ROW_CASES, *WIKI_OVERVIEW_CASES)


@dataclass(frozen=True)
class Written:
    """One generation, scored on every dimension its case declares. A sample the provider dropped
    carries no parts and leaves the denominator instead of counting as a failure."""

    parts: dict[str, Failures]
    evidence: JsonObject
    excluded: bool = False


@dataclass(frozen=True)
class AsdWritingSuite:
    """One suite run: every source written `samples` times on the model the deploy's background jobs
    write with, each sample scored on its own so an arm compares sample counts rather than a
    best-of-three."""

    cases: tuple[AsdCase, ...]
    digest: str
    samples: int = SAMPLES
    selected: frozenset[str] = frozenset()

    def names(self, case: AsdCase) -> tuple[str, ...]:
        """The case names one source contributes: one per defect it can be scored on, kept to the
        narrowing when there is one."""
        every = tuple(f"{case.name}-{dimension}" for dimension in case.dimensions)
        return tuple(name for name in every if not self.selected or name in self.selected)

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        writer = cast("ModelJudge | None", target.simulator)
        if writer is None:
            raise RuntimeError(f"the {SUITE} suite requires its background writer leg")
        if target.judge is None:
            raise RuntimeError(f"the {SUITE} suite requires its comprehension judge leg")
        wanted = tuple(case for case in self.cases if self.names(case))
        written = await gather_cases(
            slots,
            tuple(partial(self._source, case, writer.model, target.judge) for case in wanted),
        )
        return EvalReport(
            name=SUITE,
            suite=SUITE,
            digest=self.digest,
            cases=tuple(result for source in written for result in source),
        )

    async def _source(
        self, case: AsdCase, writer: ModelAccess, judge: JudgeLeg
    ) -> tuple[EvalCaseResult, ...]:
        written = [await self._sample(case, writer, judge) for _ in range(self.samples)]
        kept = frozenset(self.names(case))
        return tuple(
            self._scored(case, dimension, written)
            for dimension in case.dimensions
            if f"{case.name}-{dimension}" in kept
        )

    async def _sample(self, case: AsdCase, writer: ModelAccess, judge: JudgeLeg) -> Written:
        try:
            passages, evidence = await self._write(case, writer)
        except Exception as error:
            fault = type(error).__name__
            return Written({}, {"fault": f"{fault}: {error}"}, is_transient_fault(fault))
        parts = case.parts(passages)
        if parts[READABLE].passed:
            parts = parts | {READABLE: await self._judged(case, passages, parts[READABLE], judge)}
        return Written(parts, evidence)

    async def _judged(
        self, case: AsdCase, passages: tuple[str, ...], counted: Failures, judge: JudgeLeg
    ) -> Failures:
        """The one question no checker decides, put only to text the countable half already passed —
        the order the capability harness grades in, and for the same reason: a judge shown a line
        carrying a code span answers about the code span."""
        verdict = await rubric_pass(case.instruction, "\n".join(passages), (COMPREHENSION,), judge)
        evidence = counted.evidence | {"comprehension": verdict.reason}
        return Failures(() if verdict.passed else (verdict.reason,), evidence)

    async def _write(
        self, case: AsdCase, writer: ModelAccess
    ) -> tuple[tuple[str, ...], JsonObject]:
        match case:
            case RadarCase():
                entry = await self._entry(case, writer)
                points = tuple(point.text for point in entry.points)
                return (entry.title, entry.summary, *points), {
                    "entry": entry.model_dump(mode="json")
                }
            case WikiRowsCase():
                rows = await self._rows(case, writer)
                return rows, {"rows": list(rows)}
            case WikiOverviewCase():
                summary = await self._summary(case, writer)
                return (summary,), {"summary": summary}

    async def _entry(self, case: RadarCase, writer: ModelAccess) -> DigestEntry:
        """The report-digest job's own call: its standard as the system prompt, its finish tool as
        the only shape an answer can take."""
        reply = await writer.turn(
            ModelRequest(
                model=writer.model,
                system=writing_standard(),
                messages=(
                    Message(
                        role="user",
                        content=json.dumps(
                            {"report": bounded(case.report), "reader": case.reader},
                            separators=(",", ":"),
                        ),
                    ),
                ),
                max_tokens=MAX_OUTPUT_TOKENS,
                conversation_cache_ttl="5m",
                tools=(
                    ToolSchema(
                        name=FINISH_TOOL,
                        description=FINISH_DESCRIPTION,
                        input_schema=DigestEntry.model_json_schema(),
                    ),
                ),
                tool_choice=FINISH_TOOL,
                reasoning="off",
            )
        )
        return DigestEntry.model_validate(_recorded(reply, FINISH_TOOL))

    async def _rows(self, case: WikiRowsCase, writer: ModelAccess) -> tuple[str, ...]:
        """The condenser's own extraction pass over the case's source pages."""
        payload = {
            "pages": [
                {
                    "page_id": str(index),
                    "title": page.title,
                    "stream": page.stream,
                    "body": page.body,
                }
                for index, page in enumerate(case.pages)
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
        recorded = ExtractedFacts.model_validate(_recorded(reply, FACT_EXTRACT_TOOL))
        return tuple(fact.body for fact in recorded.facts)

    async def _summary(self, case: WikiOverviewCase, writer: ModelAccess) -> str:
        """The condenser's own consolidation pass over the case's aged facts."""
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
        return (await writer.complete(request)).strip()[:MEMORY_BODY_MAX_CHARS]

    def _scored(self, case: AsdCase, dimension: str, written: list[Written]) -> EvalCaseResult:
        attempts: list[Json] = []
        stated: list[str] = []
        passes = 0
        excluded = 0
        for sample in written:
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
                else "the writer recorded nothing to score"
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
        scored = len(written) - excluded
        dropped = scored == 0
        note = f" ({excluded} infra-excluded)" if excluded else ""
        told = f": {stated[0]}" if stated else ""
        return EvalCaseResult(
            name=f"{case.name}-{dimension}",
            passed=passes > 0,
            reason=f"{passes}/{scored} samples {dimension}{note}{told}",
            evidence={
                "grading": GRADING[dimension],
                "case": case.payload(),
                "production": list(case.production),
                "rewrite": list(case.rewrite),
                "attempts": attempts,
                "excludedSamples": excluded,
            },
            excluded=dropped,
            provider_fault=dropped,
        )


def _recorded(reply: Message, tool: str) -> JsonObject:
    blocks = () if isinstance(reply.content, str) else reply.content
    call = next(
        (block for block in blocks if isinstance(block, ToolUseBlock) and block.name == tool),
        None,
    )
    if call is None:
        raise ValueError(f"the writer recorded no {tool} call")
    return call.input


def asd_writing_task(
    judge_model: str,
    cases: tuple[AsdCase, ...] = CASES,
    samples: int = SAMPLES,
    selected: frozenset[str] = frozenset(),
) -> EvalTask:
    """Two model legs, because this suite writes as well as grades. The writer leg is pinned to the
    model background jobs run on, so what is measured is the standard the deploy actually writes to;
    the judge leg answers the one comprehension question no checker decides."""
    digest = digest_payload(
        {
            "runner": "asd-writing",
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
    suite = AsdWritingSuite(cases=cases, digest=digest, samples=samples, selected=selected)
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
        narrow=lambda names: asd_writing_task(judge_model, cases, samples, frozenset(names)),
    )
