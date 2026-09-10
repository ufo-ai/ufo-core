"""The skill_authoring verdicts are pure decisions over a saved skill and observed mounts; the
catalog's anchors are only worth grading if the rule they anchor is actually stated in the request
they must survive — and if deleting that text is enough to fail the anchor. Every case name must
also be a slug the store will accept and one no built-in skill already owns, since a save that
collides is refused and the whole case would fail on the harness rather than on the agent."""

from typing import Any, cast

import pytest
from ufo_ext_skill_create.store import SKILL_NAME_PATTERN

from evals.harness.mounts import MountObservation
from evals.skill_authoring.catalog import CASES
from evals.skill_authoring.runner import (
    CriticalInstruction,
    LoadProbe,
    SkillAuthorCase,
    SkillAuthoringSuite,
    authored_verdict,
    case_names,
    instruction_verdict,
    probe_verdict,
    skill_authoring_task,
)
from evals.suites.skill_gtm import CASES as GTM_CASES
from ufo.host.ext.loader import load_manifests, skill_registry
from ufo.runtime.skills.runtime import RuntimeSkill, parse_skill_content
from ufo.schema.records import TurnStatus

CASE = SkillAuthorCase(
    name="escalation-triage",
    request="Save a skill called escalation-triage that posts every P1 to #escalations.",
    must=(CriticalInstruction("escalations-channel", r"#escalations"),),
    probes=(
        LoadProbe("churn-threat", "Acme is threatening to leave."),
        LoadProbe("demo-followup", "Confirm Thursday's demo.", loads=False),
    ),
)
LOADS = CASE.probes[0]
DECLINES = CASE.probes[1]
TRIOS = (("skill_authoring", CASES), ("skill_gtm", GTM_CASES))


def _skill(
    description: str = "Load when a customer escalation arrives.", body: str = "Rules."
) -> RuntimeSkill:
    front = f"---\nname: escalation-triage\ndescription: {description}\n---\n"
    return parse_skill_content("escalation-triage", {"SKILL.md": f"{front}{body}\n".encode()})


def _observed(
    mounted: tuple[str, ...] = (),
    status: TurnStatus | None = "running",
    startup_seconds: float | None = 2.0,
) -> MountObservation:
    return MountObservation(
        mounted=mounted,
        status=status,
        cancelled=status == "running",
        elapsed_seconds=4.0,
        charged_seconds=2.0,
        startup_seconds=startup_seconds,
    )


def test_a_case_must_name_its_skill_in_its_request() -> None:
    with pytest.raises(ValueError, match="does not name its skill"):
        SkillAuthorCase(
            name="pricing-quote",
            request="Save a skill for quoting prospects.",
            must=(),
            probes=(LoadProbe("seat-quote", "Quote Meridian."),),
        )


def test_a_case_must_carry_a_probe_its_skill_answers() -> None:
    with pytest.raises(ValueError, match="no probe its skill must answer"):
        SkillAuthorCase(
            name="pricing-quote",
            request="Save a skill called pricing-quote.",
            must=(),
            probes=(LoadProbe("payment-terms", "Net 30 or net 60?", loads=False),),
        )


def test_an_unsaved_skill_fails_the_authoring_case() -> None:
    passed, reason = authored_verdict(CASE, None)
    assert not passed
    assert "was saved" in reason


def test_a_description_that_summarizes_the_workflow_fails() -> None:
    passed, reason = authored_verdict(CASE, _skill(description="Triages customer escalations."))
    assert not passed
    assert "Load when" in reason


def test_a_description_longer_than_the_routing_budget_fails() -> None:
    passed, reason = authored_verdict(CASE, _skill(description=f"Load when {'word ' * 60}"))
    assert not passed
    assert "over 50" in reason


def test_a_routing_description_passes() -> None:
    passed, reason = authored_verdict(CASE, _skill())
    assert passed
    assert "routing description" in reason


def test_an_instruction_is_found_with_its_surrounding_words() -> None:
    passed, reason = instruction_verdict(
        CASE.must[0], "Post every P1 to #escalations within 30 minutes."
    )
    assert passed
    assert "#escalations" in reason


def test_a_dropped_instruction_names_its_pattern() -> None:
    passed, reason = instruction_verdict(CASE.must[0], "Post every P1 to the escalation channel.")
    assert not passed
    assert "#escalations" in reason


def test_the_probes_skill_mounting_passes() -> None:
    verdict = probe_verdict(CASE, LOADS, _observed(mounted=("escalation-triage",)))
    assert verdict.passed
    assert "escalation-triage" in verdict.reason


def test_a_sibling_mounting_alongside_the_right_skill_passes() -> None:
    verdict = probe_verdict(
        CASE, LOADS, _observed(mounted=("escalation-triage", "escalation-recap"))
    )
    assert verdict.passed
    assert "alongside" in verdict.reason


def test_a_sibling_standing_alone_at_the_terminal_fails() -> None:
    verdict = probe_verdict(CASE, LOADS, _observed(mounted=("escalation-recap",), status="done"))
    assert not verdict.passed
    assert "instead of" in verdict.reason


def test_a_loading_probe_that_ends_without_a_mount_fails() -> None:
    verdict = probe_verdict(CASE, LOADS, _observed(status="done"))
    assert not verdict.passed
    assert "ended done" in verdict.reason


def test_a_loading_probe_that_stalls_fails() -> None:
    verdict = probe_verdict(CASE, LOADS, _observed())
    assert not verdict.passed
    assert "did not load" in verdict.reason


def test_a_declining_probe_that_mounts_anything_authored_fails() -> None:
    verdict = probe_verdict(CASE, DECLINES, _observed(mounted=("escalation-triage",)))
    assert not verdict.passed
    assert "no authored skill answers" in verdict.reason


def test_a_declining_probe_that_ends_clean_passes() -> None:
    verdict = probe_verdict(CASE, DECLINES, _observed(status="done"))
    assert verdict.passed
    assert "no authored skill mounted" in verdict.reason


def test_a_declining_probe_that_outlives_the_deadline_passes() -> None:
    verdict = probe_verdict(CASE, DECLINES, _observed())
    assert verdict.passed
    assert "no authored skill mounted" in verdict.reason


def test_a_probe_whose_turn_never_began_its_work_is_excluded_not_passed() -> None:
    """A turn still queued or in setup mounted nothing because it did nothing. Scoring that as a
    clean no-load would read an outage as correct restraint."""
    stalled = _observed(status="queued", startup_seconds=None)

    declining = probe_verdict(CASE, DECLINES, stalled)
    loading = probe_verdict(CASE, LOADS, stalled)

    assert not declining.passed
    assert declining.excluded
    assert not loading.passed
    assert loading.excluded
    assert "never began its own work" in declining.reason


@pytest.mark.parametrize("probe", [LOADS, DECLINES])
@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_a_turn_that_never_decided_is_excluded_not_graded(
    probe: LoadProbe, status: TurnStatus
) -> None:
    """A turn that died or was cancelled by something other than the watcher made no routing
    decision: counting it as correct non-routing is how a negative half reads clean on a run where
    no agent ever chose anything."""
    verdict = probe_verdict(CASE, probe, _observed(status=status))
    assert verdict.excluded
    assert not verdict.passed
    assert status in verdict.reason


async def test_a_probe_for_a_skill_that_was_never_saved_is_excluded() -> None:
    """Both halves, not just the loading half: a declining probe over an unsaved skill cannot mount
    it, so grading it a pass credits a description that does not exist."""
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")

    for probe in (LOADS, DECLINES):
        result = await suite._probe(CASE, probe, cast("Any", None), frozenset(), frozenset())
        assert result.excluded, probe.name
        assert not result.passed, probe.name


async def test_a_probe_lost_to_provider_weather_archives_the_provider_as_its_owner() -> None:
    """The authoring turn the probe rests on died on a transient, so the probe measured the
    weather. An excluded case that names no owner reaches the nightly cohort gate as drift: five
    `skill_gtm` and `skill_authoring` trios did that to the 2026-09-06 run."""
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")

    weathered = await suite._probe(
        CASE, LOADS, cast("Any", None), frozenset(), frozenset({CASE.name})
    )
    unwritten = await suite._probe(CASE, LOADS, cast("Any", None), frozenset(), frozenset())

    assert weathered.provider_fault
    assert not unwritten.provider_fault


def test_an_instruction_carried_by_a_bundled_file_counts() -> None:
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")
    skill = parse_skill_content(
        "escalation-triage",
        {
            "SKILL.md": b"---\nname: escalation-triage\ndescription: Load when an escalation "
            b"arrives.\n---\nRead references/routing.md first.\n",
            "references/routing.md": b"Post every P1 to #escalations.\n",
        },
    )

    results = suite._instruction_results(CASE, skill, False)

    assert [result.name for result in results] == ["escalation-triage:says-escalations-channel"]
    assert results[0].passed


def test_a_rule_stated_only_in_the_description_does_not_count() -> None:
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")

    results = suite._instruction_results(
        CASE,
        _skill(description="Load when a member posts to #escalations.", body="Rate it."),
        False,
    )

    assert not results[0].passed


def test_instructions_are_excluded_when_no_skill_was_saved() -> None:
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")

    results = suite._instruction_results(CASE, None, False)

    assert results[0].excluded
    assert not results[0].passed
    assert not results[0].provider_fault


def test_instructions_lost_to_provider_weather_carry_that_owner() -> None:
    """The rules of a skill the provider never let the agent save are weather, not a suite that
    quietly stopped scoring them, and the nightly cohort gate reads the difference off the
    record."""
    suite = SkillAuthoringSuite(name="test", cases=(CASE,), digest="sha256:test")

    results = suite._instruction_results(CASE, None, True)

    assert results[0].excluded
    assert results[0].provider_fault


@pytest.mark.parametrize(("name", "cases"), TRIOS)
def test_the_task_pins_every_graded_case(name: str, cases: tuple[SkillAuthorCase, ...]) -> None:
    task = skill_authoring_task(name, cases)
    assert task.name == name
    assert task.suite == "skill_authoring"
    assert task.cases == case_names(cases)
    assert task.digest == skill_authoring_task(name, cases).digest


def test_the_trios_are_graded_apart() -> None:
    assert skill_authoring_task("skill_authoring", CASES).digest != (
        skill_authoring_task("skill_gtm", GTM_CASES).digest
    )


@pytest.mark.parametrize(("name", "cases"), TRIOS)
def test_every_graded_case_name_is_unique(name: str, cases: tuple[SkillAuthorCase, ...]) -> None:
    names = case_names(cases)
    assert len(names) == len(set(names))


@pytest.mark.parametrize(("name", "cases"), TRIOS)
def test_every_case_names_a_skill_the_store_accepts(
    name: str, cases: tuple[SkillAuthorCase, ...]
) -> None:
    built_in = set(skill_registry(load_manifests("assistant")).by_name)
    for case in cases:
        assert SKILL_NAME_PATTERN.fullmatch(case.name), case.name
        assert case.name not in built_in, case.name


@pytest.mark.parametrize(("name", "cases"), TRIOS)
def test_every_anchor_matches_the_request_it_must_survive(
    name: str, cases: tuple[SkillAuthorCase, ...]
) -> None:
    """An anchor names a rule the member actually stated: one that cannot match its own request is
    a typo that would grade every run against text no agent was ever given."""
    for case in cases:
        for instruction in case.must:
            assert instruction.found(case.request) is not None, (case.name, instruction.label)


@pytest.mark.parametrize(("name", "cases"), TRIOS)
def test_deleting_a_rule_from_the_body_fails_exactly_its_anchor(
    name: str, cases: tuple[SkillAuthorCase, ...]
) -> None:
    """The mutation check: with the request itself standing in for a body that carried every rule,
    cutting one rule's text out must fail that rule's anchor — an anchor that still matches is one
    matching something the rule does not own."""
    for case in cases:
        for instruction in case.must:
            match = instruction.found(case.request)
            assert match is not None
            mutated = case.request[: match.start()] + case.request[match.end() :]
            passed, _ = instruction_verdict(instruction, mutated)
            assert not passed, (case.name, instruction.label)


def _instruction(cases: tuple[SkillAuthorCase, ...], case: str, label: str) -> CriticalInstruction:
    subject = next(item for item in cases if item.name == case)
    return next(rule for rule in subject.must if rule.label == label)


def test_an_anchor_reads_the_hyphenated_form_a_body_writes() -> None:
    """A rule anchored on a small number must not turn into a test of prose style: an agent writes
    "a 14-day trial", and the rule it carries is the same one the member stated."""
    body = "Run a 14-day trial, capped at a 10-seat sandbox, with a day-3 and day-10 check-in."
    for label in ("fourteen-day-trial", "ten-seat-cap", "day-three-check-in"):
        assert _instruction(GTM_CASES, "trial-kickoff", label).found(body), label
    cadence = "Three emails on an 8-day cadence, each under a 90-word cap and a 45-char subject."
    for label in ("eight-day-cadence", "ninety-word-cap", "subject-length"):
        assert _instruction(GTM_CASES, "outbound-sequence", label).found(cadence), label


def test_an_anchor_reads_a_rule_split_across_markdown_lines() -> None:
    """A skill body is headings and bullets, so a two-token rule rarely lands on one line."""
    ladder = "## Severity\n\n### P1\n- The customer is down\n- The customer threatens to churn\n"
    assert _instruction(CASES, "escalation-triage", "churn-is-p1").found(ladder)
    fields = "## The record\n- Stage — never blank\n- Close date — never blank\n"
    assert _instruction(GTM_CASES, "opportunity-kickoff", "stage-and-close-date").found(fields)


def test_the_p1_anchor_refuses_a_body_that_dropped_the_per_p1_line() -> None:
    """The anchor names the rule, not the vocabulary around it: a recap that summarises P1 activity
    in a paragraph did not carry "one line per P1", and a bare mention must not say it did."""
    rule = _instruction(CASES, "escalation-recap", "one-line-per-p1")
    assert not rule.found("Summarize P1 activity for the week in a short paragraph.")
    assert rule.found("Then one line per P1: the account name and how long it stayed open.")
