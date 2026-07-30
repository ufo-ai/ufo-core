"""The onboarding_help graders are pure decisions over one turn's tool calls, and every case must
name a reference file the hosted corpus actually ships: a renamed or misspelled reference would fail
every case forever, and a corpus the pack stopped carrying would make the whole suite unscoreable.
The negative-loading control is asserted from the same trajectory shape as the positive one, so a
grader that stopped seeing loads would pass those cases for the wrong reason."""

from re import findall

from evals.harness.capability import CapabilityOutput, ToolInvocation, grading_statement
from evals.onboarding_help import (
    CASES,
    CORPUS_SKILL,
    ONBOARDING_HELP_PACKS,
    REFERENCES_DIR,
    SLACK_SETUP_SKILL,
    catalog_scorer,
    corpus_scorer,
    own_work_scorer,
    slack_setup_scorer,
)
from ufo.ext.loader import load_manifests, skill_registry
from ufo.loop.engine import SKILL_LOAD_TOOL

GETTING_STARTED = "getting-started.md"
BILLING = "billing-and-seats.md"
RESTRAINT_CASES = frozenset({"internal-probe", "uncovered-compliance"})
"""The cases whose point is what the answer withholds. They stay at one sample for the same reason
the own-work controls do."""
OVERVIEW_CASES = frozenset({"what-can-you-do", "show-me-what-you-can-do"})
"""The two phrasings a customer opens with. The description is the only thing that decides whether
either reaches the corpus, and both get the same answer, so both are pinned to it."""
UNROUTED = frozenset({"internal-only.md"})
"""The one reference no case grades a read of. Its rule is stated in `SKILL.md` itself, so an agent
that refuses a probe without opening the file is behaving correctly — requiring the read would fail
the right answer. `internal-probe` grades that refusal instead."""


def _load(skill: str = CORPUS_SKILL, is_error: bool = False, result: str = "") -> ToolInvocation:
    return ToolInvocation(
        name=SKILL_LOAD_TOOL,
        input={"name": skill},
        result=result or ("no such skill" if is_error else "mounted"),
        has_result=True,
        is_error=is_error,
    )


def _read(reference: str) -> ToolInvocation:
    return ToolInvocation(
        name="read",
        input={"path": f"{REFERENCES_DIR}/{reference}"},
        result="# Getting Started",
        has_result=True,
    )


def _output(*calls: ToolInvocation, response: str = "Here is your next step.") -> CapabilityOutput:
    return CapabilityOutput(response=response, calls=calls)


async def test_reading_the_matching_reference_passes() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load(), _read(GETTING_STARTED)))

    assert verdict.passed
    assert GETTING_STARTED in verdict.reason


async def test_answering_without_reading_the_reference_fails() -> None:
    """The corpus's SKILL.md carries no product facts, so a load with no reference read is an
    answer from the model's own guesses about the product."""
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load()))

    assert not verdict.passed
    assert "never read" in verdict.reason


async def test_reading_a_different_reference_fails() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load(), _read("billing-and-seats.md")))

    assert not verdict.passed


async def test_either_of_two_named_references_satisfies_the_read() -> None:
    """The seat case's rubric spans two files, so its grader accepts either. Passing on the second
    name is the half that a single-reference grader got wrong."""
    grader = corpus_scorer(BILLING, "troubleshooting.md")

    assert (await grader(_output(_load(), _read(BILLING)))).passed
    assert (await grader(_output(_load(), _read("troubleshooting.md")))).passed
    assert not (await grader(_output(_load(), _read(GETTING_STARTED)))).passed


async def test_the_catalogue_must_be_consulted_before_speaking_to_availability() -> None:
    """`catalog_scorer` is half the deterministic gate of the connectors case: a judge cannot tell a
    checked negative from an assumed one, so the trajectory carries it."""
    checked = ToolInvocation(
        name="list_external_tools", input={"query": "snowflake"}, result="[]", has_result=True
    )
    failed = ToolInvocation(
        name="list_external_tools",
        input={"query": "snowflake"},
        result="upstream refused",
        has_result=True,
        is_error=True,
    )

    assert (await catalog_scorer()(_output(_load(), checked))).passed
    assert not (await catalog_scorer()(_output(_load()))).passed
    assert not (await catalog_scorer()(_output(_load(), failed))).passed


async def test_the_overview_closes_by_loading_the_install_skill() -> None:
    assert (await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL)))).passed
    assert not (await slack_setup_scorer()(_output(_load(), _read("capabilities.md")))).passed


async def test_a_failed_install_skill_load_does_not_count_as_closing() -> None:
    verdict = await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL, is_error=True)))

    assert not verdict.passed
    assert "never mounted" in verdict.reason
    assert "no such skill" in verdict.reason


async def test_only_a_skill_load_counts_not_any_call_naming_the_skill() -> None:
    named = ToolInvocation(
        name="share_file",
        input={"name": SLACK_SETUP_SKILL},
        result="mounted",
        has_result=True,
    )
    verdict = await slack_setup_scorer()(_output(_load(), named))

    assert not verdict.passed
    assert "never loaded" in verdict.reason


async def test_the_passing_verdict_names_the_skill_it_credited() -> None:
    verdict = await slack_setup_scorer()(_output(_load(), _load(SLACK_SETUP_SKILL)))

    assert verdict.passed
    assert SLACK_SETUP_SKILL in verdict.reason


async def test_the_failure_reason_names_the_first_failed_attempt() -> None:
    verdict = await slack_setup_scorer()(
        _output(
            _load(),
            _load(SLACK_SETUP_SKILL, is_error=True, result="mount raced"),
            _load(SLACK_SETUP_SKILL, is_error=True, result="second reason"),
        )
    )

    assert not verdict.passed
    assert "mount raced" in verdict.reason


async def test_a_load_that_succeeded_before_a_later_failure_still_counts() -> None:
    verdict = await slack_setup_scorer()(
        _output(_load(), _load(SLACK_SETUP_SKILL), _load(SLACK_SETUP_SKILL, is_error=True))
    )

    assert verdict.passed


async def test_a_retried_install_skill_load_is_scored_on_the_attempt_that_succeeded() -> None:
    verdict = await slack_setup_scorer()(
        _output(_load(), _load(SLACK_SETUP_SKILL, is_error=True), _load(SLACK_SETUP_SKILL))
    )

    assert verdict.passed


async def test_a_companion_skill_alongside_the_corpus_passes() -> None:
    """A Slack question legitimately loads the install skill too — the corpus is what must be
    there, not what must be alone."""
    verdict = await corpus_scorer("slack-install.md")(
        _output(_load("slack-app-setup"), _load(), _read("slack-install.md"))
    )

    assert verdict.passed


async def test_loading_only_another_skill_fails_and_names_it() -> None:
    verdict = await corpus_scorer(GETTING_STARTED)(_output(_load("slack-app-setup")))

    assert not verdict.passed
    assert "slack-app-setup" in verdict.reason


async def test_a_retried_load_is_scored_on_the_attempt_that_succeeded() -> None:
    """Mounted files are written over the network in the sandbox this suite runs against, so a first
    load can fail and the retry is correct behavior — the verdict follows the successful attempt."""
    verdict = await corpus_scorer(GETTING_STARTED)(
        _output(_load(is_error=True), _load(), _read(GETTING_STARTED))
    )

    assert verdict.passed


async def test_loading_the_corpus_is_enough_when_no_reference_is_named() -> None:
    """The two guardrail cases grade a refusal, not a read: `internal-probe` and
    `uncovered-compliance` pass on the load alone, so this branch is their entire deterministic
    half."""
    verdict = await corpus_scorer()(_output(_load()))

    assert verdict.passed
    assert CORPUS_SKILL in verdict.reason


async def test_a_failed_corpus_load_fails() -> None:
    verdict = await corpus_scorer()(_output(_load(is_error=True)))

    assert not verdict.passed
    assert "failed" in verdict.reason


async def test_own_work_passes_only_while_the_corpus_stays_unloaded() -> None:
    assert (await own_work_scorer()(_output(_load("office-xlsx")))).passed
    assert not (await own_work_scorer()(_output(_load(), _read(GETTING_STARTED)))).passed


def test_the_cases_are_wired_to_the_graders_their_rubrics_need() -> None:
    """Pinning the mechanism is not pinning the wiring: reverting the seat case to one reference, or
    dropping the catalogue half of the connectors case, would leave every other test green."""
    seat = grading_statement(
        next(case for case in CASES if case.name == "teammate-has-no-seat").grader
    )
    connectors = grading_statement(
        next(case for case in CASES if case.name == "what-can-you-connect-to").grader
    )

    assert set(_references_of(seat)) == {BILLING, "troubleshooting.md"}
    assert "list_external_tools" in connectors
    assert "capabilities.md" in _references_of(connectors)


def test_sampling_follows_which_way_the_case_points() -> None:
    """A case that grades an act is re-run, because routing is about nine rounds in ten and one
    sample makes a clean suite a coin toss. A case that grades restraint is not, whichever way its
    grader points: "any sample passes" would let the one round that stayed quiet excuse the two that
    disclosed."""
    for case in CASES:
        restraint = case.name in RESTRAINT_CASES or "never loads" in grading_statement(case.grader)
        expected = 1 if restraint else 3
        assert case.samples == expected, f"{case.name} has samples={case.samples}"


def test_the_description_carries_the_overview_phrasing_verbatim() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]
    description = corpus.description.casefold()

    for name in OVERVIEW_CASES:
        sent = next(case for case in CASES if case.name == name).message.casefold().rstrip("?")
        assert sent in description, f"{name!r} sends {sent!r}, which the description does not carry"


def test_the_description_opens_as_a_routing_trigger() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]

    assert corpus.description.startswith("Load when")


def test_the_overview_cases_grade_the_slack_close() -> None:
    for name in OVERVIEW_CASES:
        statement = grading_statement(next(case for case in CASES if case.name == name).grader)

        assert SLACK_SETUP_SKILL in statement, f"{name!r} does not grade the install close"
        assert "capabilities.md" in _references_of(statement)


def test_the_pack_mounts_the_install_skill_the_close_names() -> None:
    mounted = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name

    assert SLACK_SETUP_SKILL in mounted, sorted(mounted)


def test_every_case_names_a_reference_the_corpus_ships() -> None:
    corpus = skill_registry(load_manifests(ONBOARDING_HELP_PACKS[0])).by_name[CORPUS_SKILL]
    shipped = {
        path.removeprefix("references/")
        for path in corpus.mounted_files()
        if path.startswith("references/")
    }
    graded = {
        reference for case in CASES for reference in _references_of(grading_statement(case.grader))
    }

    assert graded, "the suite grades no reference file — the routing table is unproven"
    assert graded <= shipped, f"cases grade references the corpus does not ship: {graded - shipped}"
    assert graded == shipped - UNROUTED, (
        "every row of the routing table needs a case that reaches its file: "
        f"{shipped - UNROUTED - graded} ungraded"
    )


def _references_of(statement: str) -> tuple[str, ...]:
    """Every reference the statement names, not just the last — a case that accepts either of two
    files ("how do I add my team" is seating as much as joining) states both, and taking one would
    read the coverage set short and pass while a routing row went ungraded."""
    return tuple(findall(r"references/(\S+\.md)", statement))


def test_a_multi_reference_statement_contributes_every_file_it_names() -> None:
    both = grading_statement(corpus_scorer(GETTING_STARTED, "billing-and-seats.md"))

    assert _references_of(both) == (GETTING_STARTED, "billing-and-seats.md")
    assert _references_of(grading_statement(corpus_scorer())) == ()
