"""YC recall cases grade grounded answers and the source trajectory used to produce them."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass

from evals.harness.capability import (
    CapabilityCase,
    CapabilityOutput,
    CapabilityVerdict,
    Grader,
    ToolInvocation,
)

URL_PATTERN = re.compile(r"https?://[^\s)>\]]+")


@dataclass(frozen=True)
class ExpectedCall:
    name: str
    action: str | None = None
    entity: str | None = None


@dataclass(frozen=True)
class RecallSpec:
    name: str
    message: str
    calls: tuple[ExpectedCall, ...]
    concepts: tuple[tuple[str, ...], ...]
    links: int = 1
    web_dependent: bool = False
    ordered: bool = False
    forbidden: tuple[ExpectedCall, ...] = ()
    asks_question: bool = False
    max_response_chars: int | None = None
    predicate: Callable[[str], bool] | None = None


def _matches(call: ToolInvocation, expected: ExpectedCall) -> bool:
    if call.name != expected.name:
        return False
    if expected.action is not None and call.input.get("action") != expected.action:
        return False
    return expected.entity is None or call.input.get("entity") == expected.entity


def _grader(spec: RecallSpec) -> Grader:
    async def grade(output: CapabilityOutput) -> CapabilityVerdict:
        positions: list[int] = []
        missing: list[str] = []
        for expected in spec.calls:
            found = next(
                (
                    index
                    for index, call in enumerate(output.calls)
                    if call.succeeded and _matches(call, expected)
                ),
                None,
            )
            if found is None:
                missing.append(
                    "/".join(
                        part for part in (expected.name, expected.action, expected.entity) if part
                    )
                )
            else:
                positions.append(found)
        if missing:
            return CapabilityVerdict(False, f"missing calls: {', '.join(missing)}")
        if spec.ordered and positions != sorted(positions):
            return CapabilityVerdict(False, "required calls ran out of order")
        blocked = [
            call.name
            for call in output.calls
            if any(_matches(call, expected) for expected in spec.forbidden)
        ]
        if blocked:
            return CapabilityVerdict(False, f"used forbidden call: {', '.join(blocked)}")
        text = output.response.casefold()
        absent = [
            "/".join(group) for group in spec.concepts if not any(term in text for term in group)
        ]
        if absent:
            return CapabilityVerdict(False, f"missing answer concepts: {'; '.join(absent)}")
        if spec.predicate is not None and not spec.predicate(output.response):
            return CapabilityVerdict(False, "answer failed its structural constraint")
        found_links = URL_PATTERN.findall(output.response)
        if len(found_links) < spec.links:
            return CapabilityVerdict(
                False, f"found {len(found_links)} source links, expected {spec.links}"
            )
        if spec.asks_question and "?" not in output.response:
            return CapabilityVerdict(False, "did not ask a clarifying question")
        if spec.max_response_chars is not None and len(output.response) > spec.max_response_chars:
            return CapabilityVerdict(False, "answer exceeded the limited-paraphrase bound")
        return CapabilityVerdict(True, "grounding, trajectory, and answer constraints met")

    return grade


MEMORY = (ExpectedCall("memory_search"),)
YC_ASK = (ExpectedCall("yc_read", action="ask"),)


def _series_a_recommendation(text: str) -> bool:
    return (
        re.search(
            r"\b(?:yes|no|start|begin|raise now|should raise|wait|hold off|not yet|too early|"
            r"delay)\b",
            text,
            flags=re.IGNORECASE,
        )
        is not None
    )


SPECS = (
    RecallSpec(
        "r01-fundraise-runway-timing",
        "We have ten months of runway. Is it time to start our Series A?",
        MEMORY,
        (("guidance", "manual", "runway"),),
        predicate=_series_a_recommendation,
    ),
    RecallSpec(
        "r02-manual-over-library",
        "An older YC talk conflicts with what I'm seeing on Bookface. Which should I follow?",
        MEMORY,
        (("manual", "bookface"), ("library", "talk"), ("current", "historical", "older")),
        links=2,
    ),
    RecallSpec(
        "r03-series-a-benchmark-caveats",
        "What are the current Series A revenue and growth benchmarks?",
        YC_ASK,
        (("date", "dated", "as of"), ("method", "basis"), ("caveat", "context", "varies")),
        web_dependent=True,
    ),
    RecallSpec(
        "r04-customer-reference-gating",
        "Three VCs want customer reference calls before showing much interest. Should we agree?",
        MEMORY,
        (("serious", "committed", "interest"), ("customer",), ("burden", "protect", "limit")),
    ),
    RecallSpec(
        "r05-bio-fundraise-differences",
        "Does fundraising work differently for a biotech startup than for a software startup?",
        MEMORY,
        (("general",), ("sector", "biotech", "specific")),
    ),
    RecallSpec(
        "r06-delaware-wind-down-order",
        "We're low on cash and may need to shut down our Delaware company. What should we do?",
        MEMORY,
        (("sequence", "order", "first"), ("legal", "lawyer", "counsel"), ("tax",)),
    ),
    RecallSpec(
        "r07-enterprise-security-review",
        "A prospect sent us a 250-question security form and asked if we're SOC 2 compliant. How "
        "should we respond?",
        MEMORY,
        (("questionnaire",), ("audit", "soc 2"), ("honest", "accurate"), ("gate", "control")),
    ),
    RecallSpec(
        "r08-yc-candidate-closing-help",
        "Can YC help convince a potential employee to join?",
        MEMORY,
        (("candidate", "employee", "hire"), ("call", "talk", "speak"), ("partner", "yc")),
    ),
    RecallSpec(
        "r09-visitor-versus-founder-work",
        "Can a founder enter on ESTA, go on US payroll, and build the company while visiting?",
        MEMORY,
        (
            ("visitor", "esta"),
            ("work authorization", "employment authorization"),
            ("counsel", "lawyer", "attorney"),
        ),
    ),
    RecallSpec(
        "r10-early-equity-grant",
        "We're pre-seed and hiring our third engineer. Should we wait until our next round to "
        "grant equity?",
        MEMORY,
        (("stage", "pre-seed", "early"), ("mechanic", "grant", "option"), ("deadline", "timing")),
    ),
    RecallSpec(
        "r11-first-engineer-interview",
        "Should our first engineering interview use coding puzzles or something closer to the "
        "actual job?",
        MEMORY,
        (("realistic", "job-like", "work sample"), ("structured", "consistent")),
    ),
    RecallSpec(
        "r12-first-sales-hire-economics",
        "Our product is $3k a year. Is it time to hire our first account executive?",
        MEMORY,
        (
            ("founder-led", "founder led"),
            ("economic", "acv", "payback"),
            ("current", "caveat", "depends"),
        ),
    ),
    RecallSpec(
        "r13-yc-public-launch-sequence",
        "How should we launch our unpolished demo?",
        MEMORY,
        (
            ("directory",),
            ("launch bookface", "bookface launch"),
            ("launch yc",),
            ("hacker news", "hn"),
            ("product hunt", "ph"),
            ("sequence", "order"),
        ),
    ),
    RecallSpec(
        "r14-user-interview-behavior",
        "Prospects keep saying they'd pay, but nobody has. What should we ask in user interviews?",
        MEMORY,
        (
            ("past behavior", "actually did"),
            ("pain", "problem"),
            ("commitment", "paid", "action"),
            ("hypothetical",),
        ),
    ),
    RecallSpec(
        "r15-pmf-signal-synthesis",
        "Paid acquisition is growing signups, but retention is poor. What's wrong?",
        MEMORY,
        (
            ("acquisition",),
            ("retention",),
            ("durable demand", "product-market fit", "pmf"),
            ("historical", "heuristic"),
        ),
    ),
    RecallSpec(
        "r16-pricing-guidance-conflict",
        "We've found conflicting YC advice on enterprise pricing. What should we do?",
        MEMORY,
        (
            ("old", "historical"),
            ("current",),
            ("authority", "manual", "guidance"),
            ("test", "experiment", "measure"),
        ),
        links=2,
    ),
    RecallSpec(
        "r17-consumer-metric-selection",
        "For our consumer product, should we optimize signups, DAU, or revenue?",
        MEMORY,
        (
            ("stage", "product loop"),
            ("acquisition",),
            ("activation",),
            ("retention",),
            ("monetization", "revenue"),
            ("no universal", "depends"),
        ),
    ),
    RecallSpec(
        "r18-company-directory-filtering",
        "Which active W25 B2B infrastructure companies are hiring remotely and have raised a "
        "Series A?",
        (ExpectedCall("yc_read", action="search", entity="companies"),),
        (("current", "as of"), ("evidence", "source")),
        web_dependent=True,
    ),
    RecallSpec(
        "r19-founder-background-filtering",
        "Which active W24 founders studied computer science at MIT and previously worked at "
        "Google?",
        (ExpectedCall("yc_read", action="search", entity="founders"),),
        (("identity", "disambigu"), ("profile", "evidence"), ("privacy", "access")),
        web_dependent=True,
    ),
    RecallSpec(
        "r20-investor-portfolio-join",
        "Which investor named Northstar backs YC developer-tools companies that are hiring?",
        (
            ExpectedCall("yc_read", action="search", entity="investors"),
            ExpectedCall("yc_read", action="search", entity="companies"),
        ),
        (("disambigu", "which northstar"), ("investor",), ("company",), ("hiring",)),
        web_dependent=True,
        ordered=True,
    ),
    RecallSpec(
        "r21-forum-anecdote-versus-policy",
        "What are founders saying on Bookface about confidentiality in investor updates?",
        (ExpectedCall("yc_read", action="search", entity="forum"),),
        (
            ("founder", "forum", "discussion"),
            ("confidential", "privacy"),
            ("investor update", "investor communication"),
        ),
        links=2,
        web_dependent=True,
        max_response_chars=2500,
    ),
    RecallSpec(
        "r22-public-versus-bookface-launch",
        "Which AI security companies have launched recently, and what are founders saying about "
        "them on Bookface?",
        (
            ExpectedCall("yc_read", action="search", entity="launches"),
            ExpectedCall("yc_read", action="search", entity="forum"),
        ),
        (
            ("public",),
            ("founder-only", "bookface", "private"),
            ("dedup", "same company"),
            ("access",),
        ),
        web_dependent=True,
    ),
    RecallSpec(
        "r23-current-ml-job-search",
        "Are there any remote, full-time machine-learning roles that include salary and equity?",
        (ExpectedCall("yc_read", action="search", entity="jobs"),),
        (("current", "as of"), ("salary",), ("equity",)),
        web_dependent=True,
        forbidden=(ExpectedCall("yc_read", action="search", entity="candidates"),),
    ),
    RecallSpec(
        "r24-deals-company-batch-trap",
        "What YC deals can our W25 company use for SOC 2 or security compliance?",
        (ExpectedCall("yc_read", action="search", entity="deals"),),
        (("discount", "perk", "offer"), ("eligib",), ("current", "terms"), ("batch", "w25")),
        web_dependent=True,
    ),
    RecallSpec(
        "r25-deal-versus-fundraising-ambiguity",
        "Find YC deals for our fundraise.",
        (),
        (("discount", "perk", "vendor deal"), ("investor",), ("manual", "fundraising guidance")),
        links=0,
        forbidden=(ExpectedCall("yc_read", action="search", entity="deals"),),
        asks_question=True,
    ),
)

CASES = tuple(
    CapabilityCase(
        spec.name,
        spec.message,
        _grader(spec),
        web_dependent=spec.web_dependent,
        digest_tag=f"yc-recall:{spec.name}",
    )
    for spec in SPECS
)
