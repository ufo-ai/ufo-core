import re

from evals.harness.capability import CapabilityOutput, ToolInvocation, grading_statement
from evals.yc_recall import CASES, SPECS, ExpectedCall


def _case(name: str):
    return next(case for case in CASES if case.name == name)


def _output(response: str, *calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


def _call(name: str, **input_: str) -> ToolInvocation:
    return ToolInvocation(name=name, input=input_, has_result=True)


def test_recall_suite_has_exactly_twenty_five_unique_cases() -> None:
    assert len(CASES) == 25
    assert len(SPECS) == 25
    assert len({case.name for case in CASES}) == 25
    assert [case.name[:3] for case in CASES] == [f"r{index:02}" for index in range(1, 26)]
    assert all(case.digest_tag == f"yc-recall:{case.name}" for case in CASES)


def test_recall_cases_encode_the_expected_source_and_outage_boundaries() -> None:
    for spec in SPECS:
        expected = set(spec.calls)
        if spec.web_dependent:
            assert any(call.name == "yc_read" for call in expected)
        elif spec.name != "r25-deal-versus-fundraising-ambiguity":
            assert ExpectedCall("memory_search") in expected
    assert [case.web_dependent for case in CASES] == [spec.web_dependent for spec in SPECS]


def test_recall_cases_contain_no_private_artifacts_or_benchmark_answer_keys() -> None:
    corpus = "\n".join(
        (
            *(spec.message for spec in SPECS),
            *(term for spec in SPECS for group in spec.concepts for term in group),
        )
    ).casefold()
    forbidden = (
        "access_token",
        "refresh_token",
        "credentials.json",
        "@ycombinator.com",
        "bookface.ycombinator.com/knowledge/",
        "bookface.ycombinator.com/company/",
    )
    assert not any(marker in corpus for marker in forbidden)
    assert (
        re.search(
            r"\$\d|\b\d+(?:\.\d+)?%|\b\d+\s*(?:days?|weeks?|months?|years?|x)\b",
            "\n".join(term for spec in SPECS for group in spec.concepts for term in group),
        )
        is None
    )


def test_recall_cases_guard_sensitive_searches_and_private_paraphrase() -> None:
    jobs = next(spec for spec in SPECS if spec.name == "r23-current-ml-job-search")
    ambiguous = next(spec for spec in SPECS if spec.name == "r25-deal-versus-fundraising-ambiguity")
    forum = next(spec for spec in SPECS if spec.name == "r21-forum-anecdote-versus-policy")
    assert ExpectedCall("yc_read", action="search", entity="candidates") in jobs.forbidden
    assert ExpectedCall("yc_read", action="search", entity="deals") in ambiguous.forbidden
    assert ambiguous.asks_question
    assert forum.max_response_chars == 2500


def test_recall_grading_statements_derive_from_the_spec() -> None:
    ordered = _case("r20-investor-portfolio-join")
    assert grading_statement(ordered.grader) == (
        "successful calls in order: yc_read/search/investors, yc_read/search/companies; "
        "the answer covers: disambigu or which northstar; investor; company; hiring; "
        "cites at least 1 source link(s)"
    )
    ambiguous = _case("r25-deal-versus-fundraising-ambiguity")
    assert grading_statement(ambiguous.grader) == (
        "never calls yc_read/search/deals; "
        "the answer covers: discount or perk or vendor deal; investor; "
        "manual or fundraising guidance; asks a clarifying question"
    )
    constrained = _case("r01-fundraise-runway-timing")
    assert "the answer satisfies its structural constraint" in grading_statement(constrained.grader)
    bounded = _case("r21-forum-anecdote-versus-policy")
    statement = grading_statement(bounded.grader)
    assert "cites at least 2 source link(s)" in statement
    assert "stays within 2500 characters" in statement


async def test_indexed_recall_grader_requires_memory_and_grounded_answer() -> None:
    case = _case("r01-fundraise-runway-timing")
    response = (
        "Start now based on the manual guidance. Source: https://bookface.ycombinator.com/example"
    )
    passed = await case.grader(_output(response, _call("memory_search", queries="fundraise")))
    failed = await case.grader(_output(response))
    assert passed.passed
    assert not failed.passed
    assert "memory_search" in failed.reason


async def test_runway_answer_requires_a_series_a_recommendation() -> None:
    case = _case("r01-fundraise-runway-timing")
    calls = (_call("memory_search", queries="fundraise"),)
    response = "Current guidance: https://bookface.ycombinator.com/example"
    assert not (await case.grader(_output(response, *calls))).passed
    neutral = "Series A raise guidance: https://bookface.ycombinator.com/example"
    assert not (await case.grader(_output(neutral, *calls))).passed
    ungrounded = "Yes. Source: https://bookface.ycombinator.com/example"
    assert not (await case.grader(_output(ungrounded, *calls))).passed


async def test_runway_answer_does_not_need_an_unasked_timeline() -> None:
    case = _case("r01-fundraise-runway-timing")
    response = (
        "Based on the current guidance, start now with ten months of runway. "
        "Source: https://bookface.ycombinator.com/example"
    )
    assert (
        await case.grader(_output(response, _call("memory_search", queries="fundraise")))
    ).passed
    natural = (
        "Yes, the runway guidance says to raise now. "
        "Source: https://bookface.ycombinator.com/example"
    )
    assert (await case.grader(_output(natural, _call("memory_search", queries="fundraise")))).passed
    negative = "No, ten months of runway is too soon. https://bookface.ycombinator.com/example"
    assert (
        await case.grader(_output(negative, _call("memory_search", queries="fundraise")))
    ).passed


async def test_forum_answer_does_not_need_an_unasked_policy_comparison() -> None:
    case = _case("r21-forum-anecdote-versus-policy")
    response = (
        "Founders favor narrow distribution to protect confidentiality in investor updates. "
        "https://bookface.ycombinator.com/forum/example "
        "https://bookface.ycombinator.com/knowledge/example"
    )
    assert (
        await case.grader(
            _output(
                response,
                _call("yc_read", action="search", entity="forum"),
            )
        )
    ).passed


async def test_live_join_grader_requires_investor_before_company_search() -> None:
    case = _case("r20-investor-portfolio-join")
    response = (
        "I disambiguated the investor, then joined its company portfolio to current hiring "
        "evidence: https://bookface.ycombinator.com/example"
    )
    investor = _call("yc_read", action="search", entity="investors")
    company = _call("yc_read", action="search", entity="companies")
    passed = await case.grader(_output(response, investor, company))
    failed = await case.grader(_output(response, company, investor))
    assert passed.passed
    assert not failed.passed
    assert "out of order" in failed.reason


async def test_job_grader_rejects_candidate_profile_search() -> None:
    case = _case("r23-current-ml-job-search")
    response = (
        "Current roles include salary and equity details: https://www.ycombinator.com/jobs/example"
    )
    jobs = _call("yc_read", action="search", entity="jobs")
    candidates = _call("yc_read", action="search", entity="candidates")
    passed = await case.grader(_output(response, jobs))
    failed = await case.grader(_output(response, jobs, candidates))
    assert passed.passed
    assert not failed.passed
    assert "forbidden" in failed.reason


async def test_ambiguous_deal_grader_requires_clarification_before_search() -> None:
    case = _case("r25-deal-versus-fundraising-ambiguity")
    response = (
        "Do you mean vendor discounts, investor research, or the fundraising manual guidance?"
    )
    passed = await case.grader(_output(response))
    failed = await case.grader(_output(response, _call("yc_read", action="search", entity="deals")))
    assert passed.passed
    assert not failed.passed
    assert "forbidden" in failed.reason
