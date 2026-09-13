"""The `rag_prefetch` graders: each one measures whether the answer is right against the corpus the
case seeded. The suite asks no case whether the prefetch routed, so the trajectory dimension is
recorded and never gates a verdict, except on the two cases whose member asked for an action."""

from random import Random

from ufo_ext_rag.prefetch import MAX_PASSAGES, TOTAL_MAX_CHARS, Passage, Prefetch

import evals.suites.rag_prefetch as rag_prefetch
from evals.harness.capability import CapabilityOutput, ToolInvocation
from evals.registry import SEMANTIC_JUDGE_MODEL, TASKS
from evals.suites.rag_prefetch import (
    ANSWERED_WITHOUT_A_SEARCH,
    AUDITED_SEATS,
    BOTH,
    CALLS_TOOLS,
    CASES,
    CONTRACT_RATE,
    DISAGREEING_RATES,
    DISAGREEING_SEATS,
    DISAGREEING_SUPPORT,
    DISAGREEING_WEB,
    LIST_PRICE,
    OVERAGE_RATE,
    SUPPORT_HOURS,
    USAGE_WEB,
    WEB_ONLY,
    WORKSPACE_ONLY,
    Corpus,
    Fact,
    never_states,
    states,
)

CORPORA = (
    WEB_ONLY,
    WORKSPACE_ONLY,
    BOTH,
    DISAGREEING_WEB,
    DISAGREEING_SEATS,
    DISAGREEING_RATES,
    DISAGREEING_SUPPORT,
    USAGE_WEB,
)

CONFLICT_CASES = (
    "R07-workspace-contradicts-the-web",
    "R08-two-web-pages-disagree",
    "R11-two-workspace-records-disagree",
    "R12-web-overrules-a-stale-workspace-note",
    "R15-amendment-supersedes-the-order-form",
)


def _output(response: str, *calls: ToolInvocation) -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def test_a_value_without_its_source_is_not_an_accurate_answer() -> None:
    grader = states(LIST_PRICE)
    bare = await grader(_output("Northwind Team is $30 per seat per month."))
    assert not bare.passed
    assert "no source" in bare.reason
    sourced = await grader(
        _output("Team is $30 per seat, per https://northwind.example/pricing of 11 February 2026.")
    )
    assert sourced.passed


async def test_the_wrong_value_fails_however_it_is_worded() -> None:
    grader = states(CONTRACT_RATE)
    wrong = await grader(_output("We pay $30 per seat under the order form."))
    assert not wrong.passed
    assert "no value" in wrong.reason


async def test_any_phrasing_of_the_value_is_accepted() -> None:
    for hours in ("09:00 to 17:00 UTC", "9:00 to 17:00 UTC", "9am to 5pm UTC"):
        verdict = await states(SUPPORT_HOURS)(
            _output(f"Support answers {hours} on weekdays, per status.northwind.example.")
        )
        assert verdict.passed, hours


async def test_the_rate_must_keep_the_unit_its_passage_gives_it() -> None:
    grader = states(OVERAGE_RATE)
    per_call = await grader(
        _output("Overage is $0.40 per call, per https://northwind.example/usage-rates.")
    )
    assert not per_call.passed
    for stated in ("$0.40 per 1,000 calls", "$0.0004 per call"):
        verdict = await grader(
            _output(f"Overage is {stated}, per https://northwind.example/usage-rates.")
        )
        assert verdict.passed, stated


async def test_a_merged_figure_fails_the_conflict_case_deterministically() -> None:
    grader = never_states("a rate merged out of $24 and $30", r"\$2[5-9]\b")
    merged = await grader(_output("Call it $27 per seat across the two records."))
    assert not merged.passed
    assert merged.evidence == {"found": ["$27"]}
    reported = await grader(_output("The order form says $24; the pricing page says $30."))
    assert reported.passed


async def test_both_sides_of_a_conflict_must_reach_the_reply() -> None:
    grader = states(AUDITED_SEATS, CONTRACT_RATE)
    one_side = await grader(_output("We hold 52 seats, per the seat audit."))
    assert not one_side.passed
    assert one_side.evidence["missing"] == ["the $24 contracted rate: no value"]


async def test_a_tool_call_is_recorded_and_never_fails_a_factual_case() -> None:
    searched = await ANSWERED_WITHOUT_A_SEARCH(
        _output("Team is $30 per seat.", ToolInvocation(name="search_web", input={"query": "x"}))
    )
    assert searched.passed
    assert searched.evidence == {"calls": ["search_web"]}


async def test_an_action_case_still_requires_a_successful_tool_call() -> None:
    silent = await CALLS_TOOLS(_output("I have noted the renewal date."))
    assert not silent.passed
    acted = await CALLS_TOOLS(
        _output(
            "Sent.",
            ToolInvocation(name="send_email", input={"to": "dana@evalco.test"}, has_result=True),
        )
    )
    assert acted.passed


def test_every_conflict_case_grades_its_resolution() -> None:
    rubrics = {case.name: case.rubric for case in CASES}
    for name in CONFLICT_CASES:
        assert any("do not agree" in criterion for criterion in rubrics[name]), name
        assert any("lands on" in criterion for criterion in rubrics[name]), name
        assert any("merges" in criterion for criterion in rubrics[name]), name


def test_the_leaf_is_registered_with_a_judge_and_one_corpus_per_case() -> None:
    task = next(task for task in TASKS if task.name == "rag_prefetch")
    assert task.judge_model == SEMANTIC_JUDGE_MODEL
    assert task.cases == tuple(case.name for case in CASES)
    assert all(case.seed is not None and case.cleanup is not None for case in CASES)
    tags = [case.digest_tag for case in CASES]
    assert len(set(tags)) == len(tags)


def _legs(corpus: Corpus) -> tuple[tuple[Passage, ...], ...]:
    web = tuple(
        Passage(
            origin="web",
            title=document.title,
            reference=document.url,
            dated=document.published_date or "",
            text=document.text,
        )
        for document in corpus.web
    )
    workspace = tuple(
        Passage(
            origin="workspace",
            title=f"{document.name}.txt",
            reference=f"page/{index}",
            dated=document.dated,
            text=document.body,
        )
        for index, document in enumerate(corpus.pages)
    )
    return tuple(leg for leg in (web, workspace) if leg)


def test_the_block_does_not_depend_on_which_leg_answered_first() -> None:
    prefetch = Prefetch(search=None, pages=None)
    for corpus in CORPORA:
        legs = _legs(corpus)
        chosen = prefetch._chosen(legs)
        assert prefetch._chosen(tuple(reversed(legs))) == chosen


def test_a_rank_shuffle_keeps_the_same_passages() -> None:
    prefetch = Prefetch(search=None, pages=None)
    for corpus in CORPORA:
        legs = _legs(corpus)
        expected = frozenset(passage.key for passage in prefetch._chosen(legs))
        shuffler = Random(17)
        for _ in range(20):
            shuffled = tuple(tuple(shuffler.sample(leg, len(leg))) for leg in legs)
            chosen = prefetch._chosen(shuffled)
            assert frozenset(passage.key for passage in chosen) == expected


def test_no_case_message_carries_the_value_its_grader_looks_for() -> None:
    facts = [value for value in vars(rag_prefetch).values() if isinstance(value, Fact)]
    for case in CASES:
        for fact in facts:
            for value in fact.values:
                assert value.casefold() not in case.message.casefold(), (case.name, value)


def test_every_corpus_fits_the_block_so_no_case_is_graded_on_what_survived_truncation() -> None:
    for corpus in CORPORA:
        passages = [passage for leg in _legs(corpus) for passage in leg]
        assert len(passages) <= MAX_PASSAGES
        assert sum(len(passage.text) for passage in passages) <= TOTAL_MAX_CHARS
