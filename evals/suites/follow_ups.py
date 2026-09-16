"""The rows a settled thread ends on, graded where they are written rather than through a turn.

The web surface ranks these off the thread's tail with one model call, so this suite drives that
call itself — the shipped instructions, the shipped tool, the shipped model — the way the writing
suite drives the condenser. A change to `prompts/follow_ups.md` is then measurable by swapping the
file that holds it.

Two verdicts per case off one generation, so an arm that fixed the shape and left the judgement
alone reports exactly that. `slate` is countable: how many rows stand, whether two say the same
thing, whether a kind the workspace cannot take got through. `rows` is the half no checker decides —
whether a member would press one, and whether pressing it starts work rather than a conversation —
and it is put to the judge only where rows were expected at all.

Each case carries a thread this product actually held, with the reply the shipped agent wrote. A
grader that passes every slate measures nothing, so `supports_rows` marks the two threads that earn
none. A closed question, answered, leaves no work to offer. A reply that ends by asking the member
something leaves none either: the answer is theirs to give, the assistant already said what it
would do with it, and the card that takes an answer is the question card rather than a row. A
ranker that fills the screen in either case is the defect those cases exist to catch.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from functools import partial
from typing import cast
from uuid import UUID

from ufo_ext_web.followups import (
    FOLLOW_UPS,
    HOOK_CHARS,
    OFFERS_MAX,
    PROMPT_CHARS,
    Offer,
    ranking_request,
    settle_offers,
)
from ufo_ext_web.surface import ALWAYS_OFFERED, FOLLOW_UPS_DRAWN

from evals.harness.harness import (
    EvalCaseResult,
    EvalReport,
    JsonObject,
    digest_payload,
    is_transient_fault,
)
from evals.harness.judge import JUDGE_REVISION, JudgeLeg, ModelJudge, rubric_pass
from evals.harness.registry import EvalTask, gather_cases
from evals.harness.target import CapabilityTarget
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL
from ufo.sdk.context import ModelAccess

SUITE = "follow_ups"
"""One generation per case moved the suite's own score by eighteen points between runs of one
unchanged prompt (82, 82, 64 on 2026-09-14), which is wider than any wording change worth making.
Each case is ranked `SAMPLES` times and every sample carries its own verdicts, so a defect that
shows in one slate of three is reported as one, and an arm is compared against paired samples
rather than against a coin."""
SAMPLES = 3

SLATE = "slate"

# No turn wrote these rows, and nothing stores them: the id settling takes is the suite's own.
RANKED = UUID("00000000-0000-4000-8000-00000000f011")

WITH_SLACK = ALWAYS_OFFERED | {"share"}

GRADING = (
    (
        "grounded",
        "Every row follows from this thread and invents nothing: no row states a fact, a number, a "
        "file, a name, a person, or an arrangement already in place that the thread never carried. "
        "Naming what a row's own kind implies is not an invention — Slack for a share, the "
        "workspace for a keep, a run that repeats for a watch — because the workspace declared "
        "those kinds reachable and the thread had no reason to mention them.",
    ),
    (
        "startable",
        "Every row is work the assistant can start with what the thread and the workspace already "
        "hold. No row's prompt begins by asking the member for a number, a date, a cadence, or a "
        "choice it needs before it can do anything — a row that can only answer with a question "
        "is a form, not an offer.",
    ),
    (
        "distinct",
        "No two rows are the same decision in different words, including one act split across the "
        "accounts or formats it could use.",
    ),
    (
        "complete",
        "Where the reply proposed work, or named a gap the assistant itself could close, a row "
        "offers it rather than only offering to summarise, compare, or file what was already said.",
    ),
    (
        "forward",
        "No row offers work this thread already did, or what the reply said it was about to do "
        "next.",
    ),
    (
        "voice",
        "Each hook is the short form of its own prompt and addressed the same way. A hook that "
        'speaks to the member about their own things — "your mailbox", "your contract '
        'value" — over a prompt the member speaks to the assistant — "my mailbox" — is two '
        "voices for one act, and the member reads one and sends the other.",
    ),
)
"""Six criteria, each its own verdict rather than one folded out of all six. A judge that flips on
one slate in six turns an all-or-nothing fold into a coin — six individually stable criteria fold to
`0.95 ** 6` — and the run-to-run spread that produced reads as the ranking moving when it is the
instrument. Scored apart, a flaky criterion costs its own verdict and nothing else, and the same
judge call answers six verdicts instead of one: the cheapest resolution this suite can buy."""


@dataclass(frozen=True)
class FollowUpCase:
    """One settled thread and what its rows have to answer for. `kinds` is the reach the workspace
    would report on the read, so a case can state that Slack is absent and a `share` row is then a
    row the screen would drop."""

    name: str
    thread: tuple[tuple[str, str], ...]
    kinds: frozenset[str]
    supports_rows: bool

    def payload(self) -> JsonObject:
        return {
            "name": self.name,
            "thread": [{"from": role, "said": said} for role, said in self.thread],
            "kinds": [*sorted(self.kinds)],
            "supportsRows": self.supports_rows,
        }


TRIVIAL = FollowUpCase(
    "closed-question",
    (
        ("user", "What is the capital of Japan?"),
        (
            "assistant",
            "Tokyo, the capital since 1868 when the city was renamed from Edo "
            "([Britannica](https://www.britannica.com/question/What-is-the-capital-of-Japan)).",
        ),
    ),
    kinds=ALWAYS_OFFERED,
    supports_rows=False,
)

ANALYSIS = FollowUpCase(
    "series-analysed",
    (
        (
            "user",
            "Weekly signups for the last twelve weeks, oldest first: 118, 131, 127, 144, 139, "
            "152, 166, 158, 174, 191, 187, 205. Are we still growing, and how fast?",
        ),
        (
            "assistant",
            "Yes, still growing, at about 4.9% per week. Fitting a trend to the twelve numbers "
            "you gave puts each week roughly 4.9% above the one before, a doubling roughly every "
            "15 weeks, and the fit is tight (it explains about 96% of the movement). The last "
            "four weeks average 189 signups against 130 for the first four, and week 12 at 205 "
            "is 74% above week 1's 118. The dips in weeks 3, 5, 8 and 11 are noise around the "
            "trend rather than a slowdown.  signups-trend.png",
        ),
    ),
    kinds=WITH_SLACK,
    supports_rows=True,
)

BLOCKED = FollowUpCase(
    "account-missing",
    (
        (
            "user",
            "Summarise everything unread in my inbox from this week and tell me what needs an "
            "answer today.",
        ),
        (
            "assistant",
            "No mailbox is connected to me, so there is nothing to read: Gmail and Outlook both "
            "show no connected account, which means I cannot see your unread messages or judge "
            "what needs an answer today. I can connect one for you privately, and it stays yours "
            "alone. Tell me which mailbox you use and I will start the sign-in, then run the "
            "summary as soon as it is linked.",
        ),
    ),
    kinds=ALWAYS_OFFERED,
    supports_rows=False,
)

DECISION = FollowUpCase(
    "recommendation-made",
    (
        (
            "user",
            "We accept a comma-separated list of regular expressions. A bug report shows that a "
            "regex containing a comma inside a {2,3} quantifier is split in half. Should I fix "
            "the quantifier case only, or write a real parser?",
        ),
        (
            "assistant",
            "Neither. A quantifier-only patch leaves `[a,b]`, `(a|b,c)` and `\\,` still broken, "
            "so the split needs to be delimiter-aware in general: one scan that splits on commas "
            "only at depth zero, tracking `\\` escapes and `[]`, `()`, `{}` nesting. That is "
            "about twenty lines, not a grammar. If you can change the input format, take that "
            "instead — one regex per line, or a JSON array — and the ambiguity disappears rather "
            "than being parsed around.",
        ),
    ),
    kinds=ALWAYS_OFFERED,
    supports_rows=True,
)

DOCUMENT = FollowUpCase(
    "draft-with-gaps",
    (
        (
            "user",
            "Draft our Q4 board update from these three points: revenue grew 14% to $2.1M ARR, "
            "we shipped the new billing system in October, and we lost two enterprise customers "
            "to a competitor on price.",
        ),
        (
            "assistant",
            "Draft is ready: it leads with the three points, states the two enterprise losses "
            "plainly with cause and response, and turns the pricing gap into a board ask. Two "
            "spots need your numbers: cash position and runway.  q4-board-update.md",
        ),
    ),
    kinds=WITH_SLACK,
    supports_rows=True,
)

SETTLED = FollowUpCase(
    "work-already-set-up",
    (
        (
            "user",
            "Acme is pushing us from net-30 to net-45 payment terms on the renewal. Lay out what "
            "that actually costs us and what to trade for it.",
        ),
        (
            "assistant",
            "Fifteen extra days is a permanent carry of roughly 0.33% of Acme's annual contract "
            "value each year at an 8% cost of capital, plus a one-time cash hole of 15 days' "
            "billing in the quarter it starts. Trade it for a 0.5% to 1% rate increase or a "
            "multi-year commitment, with an enforced late fee. Your contract value and cost of "
            "capital drop straight into the table.  acme-net45-terms.md",
        ),
        ("user", "None of these — just remind me weekly."),
        (
            "assistant",
            "The weekly checkpoint is set for Mondays at 9:00 your time, starting next Monday. "
            "Each run asks you what moved on the Acme terms — stated payment term, notice "
            "period, auto-renewal window, late-fee language — and re-prices the float in "
            "acme-net45-terms.md against any numbers you give it. Say the word if you want a "
            "different day or time.",
        ),
    ),
    kinds=ALWAYS_OFFERED,
    supports_rows=True,
)

CASES = (TRIVIAL, ANALYSIS, BLOCKED, DECISION, DOCUMENT, SETTLED)


def _instruction(case: FollowUpCase) -> str:
    said = "\n\n".join(f"{role}: {text}" for role, text in case.thread)
    return (
        "A member and a work assistant held this thread, and it has settled. Rows stand under it "
        "that the member can press to say the next thing.\n\n"
        f"{said}\n\n"
        f"The workspace can take these kinds of row: {', '.join(sorted(case.kinds))}."
    )


def _answer(rows: tuple[Offer, ...]) -> str:
    return json.dumps(
        [{"kind": row.kind, "hook": row.hook, "prompt": row.prompt} for row in rows], indent=2
    )


def _slate_fault(case: FollowUpCase, rows: tuple[Offer, ...]) -> str:
    """What is wrong with this slate before anybody reads it. A thread that earns no rows and got
    some is the whole verdict; so is a row whose kind this workspace cannot take, and a pair of
    hooks that read the same."""
    if not case.supports_rows:
        return "" if not rows else f"{len(rows)} rows on a thread that supports none"
    if not rows:
        return "no rows on a thread that supports them"
    if len(rows) > OFFERS_MAX:
        return f"{len(rows)} rows over the {OFFERS_MAX} the contract takes"
    unreachable = sorted({row.kind for row in rows} - case.kinds)
    if unreachable:
        return f"rows of a kind this workspace cannot take: {', '.join(unreachable)}"
    hooks = [row.hook.casefold() for row in rows]
    if len(set(hooks)) != len(hooks):
        return "two rows read the same"
    return ""


@dataclass(frozen=True)
class FollowUpSuite:
    cases: tuple[FollowUpCase, ...]
    digest: str
    samples: int
    selected: frozenset[str]

    def names(self, case: FollowUpCase) -> tuple[str, ...]:
        verdicts = (SLATE, *(tuple(key for key, _ in GRADING) if case.supports_rows else ()))
        wanted = tuple(
            f"{case.name}:{verdict}:{sample}"
            for sample in range(self.samples)
            for verdict in verdicts
        )
        return tuple(name for name in wanted if not self.selected or name in self.selected)

    async def run(self, target: CapabilityTarget, slots: asyncio.Semaphore) -> EvalReport:
        ranker = cast("ModelJudge | None", target.simulator)
        if ranker is None:
            raise RuntimeError(f"the {SUITE} suite requires its ranking leg")
        if target.judge is None:
            raise RuntimeError(f"the {SUITE} suite requires its judge leg")
        wanted = tuple(
            (case, sample)
            for case in self.cases
            for sample in range(self.samples)
            if any(name.endswith(f":{sample}") for name in self.names(case))
        )
        graded = await gather_cases(
            slots,
            tuple(
                partial(self._case, case, sample, ranker.model, target.judge)
                for case, sample in wanted
            ),
        )
        return EvalReport(
            name=SUITE,
            suite=SUITE,
            digest=self.digest,
            cases=tuple(result for results in graded for result in results),
            simulator_model=DEFAULT_BACKGROUND_JOBS_MODEL,
            judge_revision=JUDGE_REVISION,
        )

    async def _case(
        self, case: FollowUpCase, sample: int, ranker: ModelAccess, judge: JudgeLeg
    ) -> tuple[EvalCaseResult, ...]:
        try:
            rows = await self._rank(case, ranker)
        except Exception as error:
            return self._faulted(case, sample, error)
        drawn = tuple(row for row in rows if row.kind in case.kinds)[:FOLLOW_UPS_DRAWN]
        evidence: JsonObject = {
            "thread": case.payload()["thread"],
            "ranked": [row.model_dump(mode="json") for row in rows],
            "drawn": [row.model_dump(mode="json") for row in drawn],
        }
        fault = _slate_fault(case, rows)
        results = [
            EvalCaseResult(
                name=f"{case.name}:{SLATE}:{sample}",
                passed=not fault,
                reason=fault or f"{len(rows)} rows, each of a kind this workspace takes",
                evidence=evidence,
            )
        ]
        if case.supports_rows:
            verdict = await rubric_pass(
                _instruction(case), _answer(drawn), tuple(text for _, text in GRADING), judge
            )
            judged = {item.criterion: item for item in verdict.criteria}
            for key, text in GRADING:
                # A judge that answered nothing for a criterion carries the whole verdict's reason:
                # a truncated or unparsed reply is a failure of every criterion, not a pass of the
                # ones it never reached.
                item = judged.get(text)
                results.append(
                    EvalCaseResult(
                        name=f"{case.name}:{key}:{sample}",
                        passed=verdict.passed if item is None else item.passed,
                        reason=verdict.reason if item is None else item.reason,
                        evidence=evidence,
                    )
                )
        return tuple(result for result in results if result.name in set(self.names(case)))

    def _faulted(
        self, case: FollowUpCase, sample: int, error: Exception
    ) -> tuple[EvalCaseResult, ...]:
        """One ranking that raised costs its own sample and nothing else. Every other sample of
        every other case was already paid for, and letting the raise leave `_case` takes the whole
        report down with it — `gather_cases` re-raises and the run ends holding no verdicts at all.

        A provider's own uncertainty — a rate limit, an overload, a timeout — is excluded rather
        than scored, the way the two sibling model-leg suites exclude it. A reply that recorded no
        call is not that: a ranking answering with no slate failed, and it is scored."""
        fault = type(error).__name__
        transient = is_transient_fault(fault)
        return tuple(
            EvalCaseResult(
                name=name,
                passed=False,
                reason=f"{fault}: {error}",
                evidence={"thread": case.payload()["thread"], "fault": fault},
                excluded=transient,
                provider_fault=transient,
            )
            for name in self.names(case)
            if name.endswith(f":{sample}")
        )

    async def _rank(self, case: FollowUpCase, ranker: ModelAccess) -> tuple[Offer, ...]:
        reply = await ranker.turn(ranking_request(ranker.model, case.thread, case.kinds))
        return settle_offers(reply, RANKED).offers


def follow_ups_task(
    judge_model: str,
    cases: tuple[FollowUpCase, ...] = CASES,
    samples: int = SAMPLES,
    selected: frozenset[str] = frozenset(),
) -> EvalTask:
    """Two model legs, because this suite writes as well as grades. The ranking leg is pinned to
    the model the surface's own call runs on, so what is measured is the slate a member would have
    read; the judge leg answers the half no checker decides."""
    digest = digest_payload(
        {
            "runner": "follow-ups",
            "task": SUITE,
            "rankerModel": DEFAULT_BACKGROUND_JOBS_MODEL,
            "judgeModel": judge_model,
            "hookChars": HOOK_CHARS,
            "promptChars": PROMPT_CHARS,
            "drawn": FOLLOW_UPS_DRAWN,
            "samples": samples,
            "grading": [{"key": key, "criterion": text} for key, text in GRADING],
            "instructions": FOLLOW_UPS,
            "cases": [case.payload() for case in cases],
            "selected": [*sorted(selected)],
        }
    )
    suite = FollowUpSuite(cases=cases, digest=digest, samples=samples, selected=selected)
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
        narrow=lambda names: follow_ups_task(judge_model, cases, samples, frozenset(names)),
    )
