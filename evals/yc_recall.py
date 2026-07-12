"""YC recall cases grade grounded answers and the source trajectory used to produce them."""

from __future__ import annotations

import re
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

SPECS = (
    RecallSpec(
        "r01-fundraise-runway-timing",
        "We have ten months of runway. Should we start our Series A now, and how long might it "
        "take? Use current YC guidance and link the source you rely on.",
        MEMORY,
        (("current", "as of"), ("timeline", "timing", "varies")),
    ),
    RecallSpec(
        "r02-manual-over-library",
        "An old YC talk conflicts with current Bookface guidance. Compare both, link each "
        "artifact, and tell me which one should govern our decision.",
        MEMORY,
        (("manual", "bookface"), ("library", "talk"), ("current", "historical", "older")),
        links=2,
    ),
    RecallSpec(
        "r03-series-a-benchmark-caveats",
        "Find the current YC guidance on Series A revenue and growth benchmarks. Include the "
        "artifact date, its methodology or basis, caveats, and a source link.",
        YC_ASK,
        (("date", "dated", "as of"), ("method", "basis"), ("caveat", "context", "varies")),
        web_dependent=True,
    ),
    RecallSpec(
        "r04-customer-reference-gating",
        "Three VCs want customer calls before showing serious interest. What does YC guidance say "
        "about when to provide references without overburdening customers? Cite it.",
        MEMORY,
        (("serious", "committed", "interest"), ("customer",), ("burden", "protect", "limit")),
    ),
    RecallSpec(
        "r05-bio-fundraise-differences",
        "How should a biotech fundraise differ from a software fundraise? Separate general YC "
        "fundraising advice from sector-specific guidance and cite both where available.",
        MEMORY,
        (("general",), ("sector", "biotech", "specific")),
    ),
    RecallSpec(
        "r06-delaware-wind-down-order",
        "We have little cash and may shut down our Delaware company. Give me the sequence from YC "
        "guidance, call out legal and tax limits, and link the source.",
        MEMORY,
        (("sequence", "order", "first"), ("legal", "lawyer", "counsel"), ("tax",)),
    ),
    RecallSpec(
        "r07-enterprise-security-review",
        "A prospect sent a 250-question security form and asks whether we are SOC 2 compliant. "
        "What should we do first? Distinguish a questionnaire from an audit and cite YC guidance.",
        MEMORY,
        (("questionnaire",), ("audit", "soc 2"), ("honest", "accurate"), ("gate", "control")),
    ),
    RecallSpec(
        "r08-yc-o1-letter-scope",
        "Can YC provide an O-1 support letter for a new employee and make multiple custom "
        "versions? Summarize eligibility and scope limits, recommend counsel, and cite the "
        "current manual.",
        MEMORY,
        (("eligible", "eligibility"), ("scope", "limit"), ("counsel", "lawyer", "attorney")),
    ),
    RecallSpec(
        "r09-visitor-versus-founder-work",
        "Can a founder enter on ESTA, go on US payroll, and build the company while visiting? "
        "Separate visitor activity from work authorization, add a counsel caveat, and cite YC.",
        MEMORY,
        (
            ("visitor", "esta"),
            ("work authorization", "employment authorization"),
            ("counsel", "lawyer", "attorney"),
        ),
    ),
    RecallSpec(
        "r10-early-equity-grant",
        "We are pre-seed and hiring engineer number three. Should we wait for the next round "
        "before granting equity? Retrieve the current stage mechanics and deadlines, with a "
        "source link.",
        MEMORY,
        (("stage", "pre-seed", "early"), ("mechanic", "grant", "option"), ("deadline", "timing")),
    ),
    RecallSpec(
        "r11-first-engineer-interview",
        "Should our first engineering interview use puzzles or a realistic work test? Find YC "
        "guidance and explain how to make the interview structured and job-like.",
        MEMORY,
        (("realistic", "job-like", "work sample"), ("structured", "consistent")),
    ),
    RecallSpec(
        "r12-first-sales-hire-economics",
        "Our product costs $3k annually. Is it time to hire our first account executive? Use YC "
        "guidance to connect founder-led sales, sales economics, and current benchmark caveats.",
        MEMORY,
        (
            ("founder-led", "founder led"),
            ("economic", "acv", "payback"),
            ("current", "caveat", "depends"),
        ),
    ),
    RecallSpec(
        "r13-yc-public-launch-sequence",
        "Our demo is not polished. How should we sequence the YC Directory, Launch Bookface, "
        "Launch YC, Hacker News, and Product Hunt? Distinguish the surfaces and cite current "
        "guidance.",
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
        "Prospects say they would pay, but nobody has. Using the public YC Startup Library, "
        "explain how to interview for past behavior, real pain, and commitment instead of "
        "hypotheticals.",
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
        "Paid acquisition is growing signups but retention is poor. Use the public Startup Library "
        "to separate acquisition from durable demand and retention, noting historical heuristics.",
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
        "An old YC pricing talk conflicts with current enterprise pricing guidance. Compare both, "
        "explain freshness and authority, and propose a measurable pricing test with links.",
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
        "For our consumer product, should we optimize signups, DAU, or revenue? Use the public YC "
        "Library to connect the product loop and stage to acquisition, activation, retention, and "
        "monetization without claiming one universal metric.",
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
        "Find active W25 B2B infrastructure companies that are hiring, support remote work, and "
        "have raised a Series A. Return linked current evidence for every filter.",
        (ExpectedCall("yc_read", action="search", entity="companies"),),
        (("current", "as of"), ("evidence", "source")),
        web_dependent=True,
    ),
    RecallSpec(
        "r19-founder-background-filtering",
        "Find active W24 founders who studied computer science at MIT and previously worked at "
        "Google. Link profile evidence, disambiguate identities, and respect profile privacy.",
        (ExpectedCall("yc_read", action="search", entity="founders"),),
        (("identity", "disambigu"), ("profile", "evidence"), ("privacy", "access")),
        web_dependent=True,
    ),
    RecallSpec(
        "r20-investor-portfolio-join",
        "Find which investor named Northstar backs YC developer-tools companies that are hiring. "
        "Disambiguate the investor before joining to linked company evidence.",
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
        "What do recent Bookface discussions say about confidentiality in investor updates, and "
        "what is the official current rule? Compare multiple dated anecdotes with policy, link the "
        "sources, and paraphrase private posts minimally.",
        (
            ExpectedCall("memory_search"),
            ExpectedCall("yc_read", action="search", entity="forum"),
        ),
        (("dated", "date"), ("anecdote", "discussion"), ("policy", "official")),
        links=2,
        web_dependent=True,
        max_response_chars=2500,
    ),
    RecallSpec(
        "r22-public-versus-bookface-launch",
        "Find public launches by AI security companies and related founder-only Bookface posts. "
        "Distinguish the audiences, deduplicate companies, preserve access boundaries, and link "
        "evidence.",
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
        "Find current remote, full-time machine-learning roles that list salary and equity. Return "
        "current job links, and do not search candidate profiles.",
        (ExpectedCall("yc_read", action="search", entity="jobs"),),
        (("current", "as of"), ("salary",), ("equity",)),
        web_dependent=True,
        forbidden=(ExpectedCall("yc_read", action="search", entity="candidates"),),
    ),
    RecallSpec(
        "r24-deals-company-batch-trap",
        "Find active SOC 2 or security-compliance deals for a W25 company. Explain what YC Deals "
        "means, avoid treating company batch as a deal filter, and cite current eligibility and "
        "terms.",
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
