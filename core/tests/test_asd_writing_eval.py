from evals.harness.scorers import content_words, max_pairwise_overlap
from evals.registry import SEMANTIC_JUDGE_MODEL, TASKS
from evals.suites.asd_writing import (
    CASES,
    GLANCE,
    MAX_FIELD_OVERLAP,
    RANKED,
    READABLE,
    STOP_WORDS,
    anchored_field,
    asd_writing_task,
    glanceable,
    novelty,
    ranked,
    readable,
    sentences,
    shipped_terms,
    system_tokens,
    unapproved_words,
    unresolved_subjects,
)
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL

LEADS_CORRECTLY = frozenset(
    {
        "radar-assistants-api-shutdown-ranked",
        "radar-buttons-inert-ranked",
        "radar-recurring-tasks-pause-ranked",
        "radar-slack-code-free-ranked",
        "radar-slack-inbound-dropped-ranked",
        "wiki-open-work-one-introduction-ranked",
    }
)


def test_system_tokens_name_what_the_writer_carried_over_from_the_system() -> None:
    assert system_tokens(
        (
            "The `_choices` line already reads a domain.",
            "workspace_by_domain returns a workspace ID.",
            "Created at 2026-08-21T20:19:37Z under #2186 at c72efc24e93d89d2.",
            "QuickBooks made no sync for 5 hours.",
        )
    ) == (
        "`_choices`",
        "workspace_by_domain",
        "2026-08-21T20:19:37Z",
        "#2186",
        "c72efc24e93d89d2",
    )


def test_unapproved_words_admit_a_registered_technical_name() -> None:
    """`request` is unapproved and `pull request` is the name the product gives a member, so the
    dictionary must not read the second as the first."""
    assert unapproved_words(("Pull request 2189 is open.",)) == {}
    assert unapproved_words(("Marshall requested a review of the failure.",)) == {
        "requested": "TELL (v), WRITE (v), TELL, WRITE",
        "review": "INSPECTION",
        "failure": "IF … NOT",
    }


def test_shipped_terms_and_unresolved_subjects_read_the_line_the_reader_meets() -> None:
    rows = (
        "The pull request was created on 2026-08-21 and was open.",
        "Rob Ryan said the direction overshot.",
    )

    assert shipped_terms(rows, ("overshot", "wordmark")) == ("overshot",)
    assert unresolved_subjects(rows) == (0,)


def test_readable_passes_a_line_written_in_the_reader_words() -> None:
    entry = (
        "QuickBooks made no sync for 5 hours.",
        "Connect the QuickBooks account again to start the syncs.",
    )

    verdict = readable(entry, ("realm-specific binding", "telemetry"))

    assert verdict.passed
    assert verdict.evidence["unapprovedWords"] == {}


def test_ranked_reads_the_field_the_reader_meets_first() -> None:
    leads = ("failed",)
    buried = ("81%",)

    assert anchored_field(("Assistant evals remain at 81%", "the sweep failed"), leads) == 1
    assert ranked(("The nightly test run failed.", "81% of cases pass."), leads, buried).passed
    assert ranked(
        ("Assistant evals remain at 81%", "The latest sweep still failed."), leads, buried
    ).reasons == ("field 0 leads with a finding the reader cannot act on",)
    assert ranked(("Seven cases newly passed.",), leads, buried).reasons == (
        "the leading finding is absent: 'failed'",
    )
    assert ranked(
        ("Seven cases newly passed.", "Five flipped.", "The sweep failed."), leads, buried
    ).reasons == ("the leading finding waits for field 2",)


def test_glanceable_counts_restatement_rather_than_only_characters() -> None:
    restated = (
        "QuickBooks syncs blocked on missing realm",
        "Reconnect the affected QuickBooks account or apply its realm-specific binding",
        "QuickBooks account needs reconnection or realm-specific binding",
    )

    verdict = glanceable(restated, 300)

    assert not verdict.passed
    assert verdict.evidence["maxFieldOverlap"] > 0.30
    assert verdict.evidence["newContentWords"] == [5, 6, 2]
    assert glanceable(
        (
            "61 web addresses give data to persons who did not sign in.",
            "Close the 61 open web addresses. The tool also merged its own work with no approval.",
            "One check said the work was incorrect after the web tests passed.",
        ),
        300,
    ).passed


def test_novelty_and_overlap_read_the_same_content_words() -> None:
    lines = ("The sweep failed today.", "The sweep failed today.", "Seven memory tests passed.")

    assert content_words(lines[0], STOP_WORDS) == frozenset({"sweep", "failed", "today"})
    assert max_pairwise_overlap(lines, STOP_WORDS) == 1.0
    assert novelty(lines) == (3, 0, 4)


def test_sentences_are_the_lines_of_a_consolidated_paragraph() -> None:
    assert sentences("Main is guarded by a ruleset; it needs five checks. Six shards run.") == (
        "Main is guarded by a ruleset",
        "it needs five checks",
        "Six shards run.",
    )


def test_production_output_fails_every_grader_but_the_entries_that_lead() -> None:
    """The calibration. A grader today's output passes measures nothing, and a grader that fails
    everything measures nothing either: the six entries that do lead with the finding hold the line
    while the other thirty-seven verdicts are the defect the suite was built to see move."""
    verdicts = {
        f"{case.name}-{dimension}": case.parts(case.production)[dimension].passed
        for case in CASES
        for dimension in case.dimensions
    }

    assert len(verdicts) == 43
    assert sum(verdicts.values()) == 6
    assert {name for name, passed in verdicts.items() if passed} == LEADS_CORRECTLY
    assert all(name.endswith(RANKED) for name in LEADS_CORRECTLY)


def test_the_hand_rewrites_pass_where_the_shipped_lines_fail() -> None:
    """The positive control: the same graders, run over rewrites a person wrote for these exact
    sources. Every dimension a rewrite is scoped to flips. `wiki-history-beta-leads-ranked` does
    not,
    and must not: that rewrite repairs one row of the section, and a History section showing a pull
    request instead of the private beta that opened has still buried its news."""
    flipped = []
    standing = []
    for case in CASES:
        if not case.rewrite:
            continue
        shipped = case.parts(case.production)
        rewritten = case.parts(case.rewrite)
        for dimension in case.dimensions:
            assert not shipped[dimension].passed, f"{case.name}-{dimension} passes today"
            name = f"{case.name}-{dimension}"
            (flipped if rewritten[dimension].passed else standing).append(name)

    assert len(flipped) == 15
    assert standing == ["wiki-history-beta-leads-ranked"]
    assert {name.rsplit("-", 1)[1] for name in flipped} == {READABLE, RANKED, GLANCE}


def test_the_suite_writes_on_the_background_model_and_judges_on_the_semantic_one() -> None:
    task = {task.name: task for task in TASKS}["asd_writing"]

    assert task.simulator_model == DEFAULT_BACKGROUND_JOBS_MODEL
    assert task.judge_model == SEMANTIC_JUDGE_MODEL
    assert len(task.cases) == 43
    assert task.narrow is not None
    assert task.narrow(
        ("radar-eval-sweep-failed-ranked", "wiki-history-beta-leads-ranked")
    ).cases == (
        "radar-eval-sweep-failed-ranked",
        "wiki-history-beta-leads-ranked",
    )
    assert asd_writing_task(SEMANTIC_JUDGE_MODEL).digest == task.digest


"""Five entries the graders never saw. They were published after the nine the graders were written
against, so they are what says whether the instrument reads writing or reads its own corpus."""
HELD_OUT = (
    (
        "Slack Code commoditizes coding agents; OneCLI pivots to team harnesses",
        "Your assistant app faces free coding-agent competition, while cross-system work "
        "remains Slack Code's stated gap.",
        "Slack Code offers coding agents free on every plan",
        "Cross-system work remains open beyond coding scope",
    ),
    (
        "Chief-of-staff costs $28.37 for 27%; mailbox wording fails three cases",
        "Cache quarter-plan fetches and hold reworded mailbox prompts until Friday's ablation.",
        "Chief-of-staff passes 12/44 at 27%, costing $28.37",
        "Caching fetches cuts chief-of-staff spend below $10",
        "Reworded prompts failed three second-mailbox cases",
    ),
    (
        "Reports endpoint needs workspace IDs; agent reads markdown folders",
        "Update the assistant app before the September 1 reports cutoff; markdown ingestion "
        "and pauseable recurring tasks also shipped.",
        "Reports without workspace IDs stop answering September 1",
        "Agent reads and nightly-syncs markdown folders",
        "Recurring tasks pause and resume without deletion",
    ),
    (
        "Export timeouts need manual work; billing now leads volume",
        "Seventeen large workspaces hit the proxy limit, while two unscheduled billing fixes "
        "could prevent roughly 60 weekly tickets.",
        "Export timeouts affect 17 workspaces; nine exports needed manual runs",
        "Billing fixes could resolve roughly 60 tickets weekly",
    ),
    (
        "No critical or high vulnerabilities; three medium findings unchanged",
        "The assistant app scan found no urgent action, while three test-only transitive "
        "findings remain.",
        "Three medium findings carried over unchanged from 14 August",
    ),
)


def test_the_blocklist_leaves_the_words_this_product_uses_correctly() -> None:
    """A severity is critical, a defect is one somebody fixes, an outage affects a workspace. Each
    flagged a shipped line whose wording was right, so each costs the precision the list is for."""
    for entry in HELD_OUT:
        assert readable(entry, ()).passed, unapproved_words(entry)


def test_the_held_out_entries_still_fail_the_glance_they_were_published_failing() -> None:
    """The restatement is the finding, and it survives every change made for the false positives."""
    for entry in HELD_OUT:
        failed = glanceable(entry, MAX_FIELD_OVERLAP)
        assert not failed.passed
        assert any("repeat one fact" in reason for reason in failed.reasons), failed.reasons
