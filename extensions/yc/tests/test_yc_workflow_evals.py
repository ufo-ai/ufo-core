import json

import pytest

from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.yc_workflows import CASES


def _case(identifier: str):
    return next(case for case in CASES if case.name.startswith(identifier))


def _call(name: str, input_: dict[str, object]) -> ToolInvocation:
    return ToolInvocation(name, input_, has_result=True)


def test_suite_has_exactly_twenty_five_unique_ordered_workflows() -> None:
    assert len(CASES) == 25
    assert len({case.name for case in CASES}) == 25
    assert [case.name.split("-", 1)[0] for case in CASES] == [
        f"W{number:02d}" for number in range(1, 26)
    ]
    assert all(case.digest_tag == f"yc-workflow:{case.name}" for case in CASES)


def test_only_live_yc_read_workflows_are_web_dependent() -> None:
    local = {"W01", "W06", "W08", "W09", "W10", "W23", "W24", "W25"}
    assert {case.name.split("-", 1)[0] for case in CASES if not case.web_dependent} == local


async def test_skill_and_memory_both_contribute_to_readiness_grade() -> None:
    case = _case("W01")
    passing = CapabilityOutput(
        "Fact: $42k ARR. Guidance: benchmark context https://example.test/guidance. "
        "Inference: readiness is conditional.",
        (
            _call("load_skill", {"name": "company-diligence"}),
            _call("memory_search", {"query": "seed readiness guidance"}),
        ),
    )
    missing_memory = CapabilityOutput(passing.response, passing.calls[:1])
    assert (await case.grader(passing)).passed
    assert not (await case.grader(missing_memory)).passed


async def test_skill_discovery_order_is_enforced() -> None:
    case = _case("W20")
    response = "Pricing guidance: meter value and review willingness to pay."
    passing = CapabilityOutput(
        response,
        (
            _call("yc_read", {"action": "skills_list"}),
            _call("yc_read", {"action": "skills_read", "name": "pricing-advice"}),
        ),
    )
    reversed_calls = CapabilityOutput(response, tuple(reversed(passing.calls)))
    assert (await case.grader(passing)).passed
    assert not (await case.grader(reversed_calls)).passed


async def test_forum_synthesis_requires_distinct_queries_and_limited_paraphrase() -> None:
    case = _case("W19")
    response = (
        "Consensus: prepare proof. Disagreement: who should attend. https://example.test/post"
    )
    passing = CapabilityOutput(
        response,
        (
            _call("yc_read", {"action": "search", "entity": "forum", "query": "reference prep"}),
            _call("yc_read", {"action": "search", "entity": "forum", "query": "reference agenda"}),
        ),
    )
    duplicate = CapabilityOutput(
        response,
        (
            passing.calls[0],
            _call("yc_read", dict(passing.calls[0].input)),
        ),
    )
    assert (await case.grader(passing)).passed
    assert not (await case.grader(duplicate)).passed


async def test_launch_draft_can_state_restraint_without_claiming_publication() -> None:
    case = _case("W18")
    calls = (
        _call("yc_read", {"action": "skills_list"}),
        _call("yc_read", {"action": "skills_read", "name": "launch-bookface"}),
        _call("yc_read", {"action": "search", "entity": "forum"}),
    )
    restrained = CapabilityOutput("Draft: ready. I did not publish it.", calls)
    assert (await case.grader(restrained)).passed
    assert not (await case.grader(CapabilityOutput("Draft: ready. I published it.", calls))).passed


@pytest.mark.parametrize("missing", ["company", "batch", "evidence_url"])
async def test_structured_company_grade_requires_valid_complete_json(missing: str) -> None:
    case = _case("W22")
    value = {
        "company": "Example Co",
        "batch": "S00",
        "one_liner": "Database observability",
        "evidence_url": "https://example.test/company",
        "relevance_reason": "Relevant product category",
    }
    calls = (
        _call(
            "yc_read",
            {"action": "search", "entity": "companies", "query": "database observability"},
        ),
    )
    assert (await case.grader(CapabilityOutput(json.dumps(value), calls))).passed
    del value[missing]
    assert not (await case.grader(CapabilityOutput(json.dumps(value), calls))).passed
    assert not (await case.grader(CapabilityOutput("```json\n{}\n```", calls))).passed


async def test_company_index_grade_checks_exact_input_and_truthful_status() -> None:
    case = _case("W23")
    passing = CapabilityOutput(
        "Registered the source; sync is scheduled.",
        (
            _call(
                "yc_index",
                {"entity": "companies", "query": "AI observability", "max_results": 200},
            ),
        ),
    )
    wrong_bound = CapabilityOutput(
        passing.response,
        (
            _call(
                "yc_index",
                {"entity": "companies", "query": "AI observability", "max_results": 5000},
            ),
        ),
    )
    assert (await case.grader(passing)).passed
    assert not (await case.grader(wrong_bound)).passed
    assert not (
        await case.grader(CapabilityOutput(passing.response, passing.calls + passing.calls))
    ).passed
    assert not (
        await case.grader(CapabilityOutput("Registered; sync complete.", passing.calls))
    ).passed


@pytest.mark.parametrize("entity", ["chats", "follows", "routes", "candidates"])
async def test_sensitive_index_restraint_rejects_indexing_and_private_searches(entity: str) -> None:
    case = _case("W25")
    response = "These records are live-only. Safe alternative: search public company records."
    assert (await case.grader(CapabilityOutput(response, ()))).passed
    assert not (
        await case.grader(CapabilityOutput(response, (_call("yc_index", {"entity": entity}),)))
    ).passed
    assert not (
        await case.grader(
            CapabilityOutput(
                response,
                (_call("yc_read", {"action": "search", "entity": entity}),),
            )
        )
    ).passed
