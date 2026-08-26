from evals.registry import SEMANTIC_JUDGE_MODEL, TASKS
from evals.suites.wiki_generation import (
    ATTRIBUTED,
    CASES,
    DISTINCT,
    EXTRACTION_CASES,
    OVERVIEW_CASES,
    SAMPLES_MET,
    SAMPLES_SCORED,
    STATED,
    WORKSPACE,
    Failures,
    Generated,
    Row,
    WikiCase,
    WikiGenerationSuite,
    sample_rate,
    wiki_generation_task,
)
from ufo.config import DEFAULT_BACKGROUND_JOBS_MODEL

BY_NAME = {case.name: case for case in CASES}
SYNC = BY_NAME["daily-sync-across-passes"]
NOTICE = BY_NAME["service-notice-in-one-pass"]
PULL_REQUEST = BY_NAME["pull-request-in-one-pass"]
OVERVIEW = BY_NAME["overview-keeps-the-parties-apart"]


def test_the_deploys_own_rows_fail_every_defect_they_were_read_for() -> None:
    """The testing workspace's rows for one daily sync, read 2026-08-26. Both halves are the
    calibration: a grader that passes this measures nothing, and the hand rewrite says it is not
    failing good writing instead."""
    failed = SYNC.parts(SYNC.production)

    assert failed[DISTINCT].reasons == (
        "3 rows carry the fork an app claim",
        "2 rows carry the dashboards over chat claim",
        "2 rows carry the private app copies claim",
        "2 rows carry the staging and production claim",
        "2 rows carry the trial users claim",
        "9 rows over the 6 these pages earn",
    )
    assert failed[ATTRIBUTED].passed, failed[ATTRIBUTED].reasons
    assert "row 1: a person by a short name, 'Alex'" in failed[STATED].reasons
    assert "row 4: an implied subject, 'The group'" in failed[STATED].reasons


def test_the_hand_rewrite_passes_every_dimension() -> None:
    for dimension, failures in SYNC.parts(SYNC.rewrite).items():
        assert failures.passed, (dimension, failures.reasons)


def test_a_stranger_notice_about_our_own_cluster_keeps_both_parties() -> None:
    """The self-versus-others shape the deploy actually meets: the announcement is Amazon's, the
    cluster is ours, and a row that drops Amazon reads as our own news."""
    dropped = (
        Row(
            "page/service-notice",
            "ufo-testing-redis — Needs update elasticache-july-patch-update-202607 by 30 August.",
        ),
    )
    handed_over = (
        Row(
            "page/service-notice",
            f"{WORKSPACE} — Announced elasticache-july-patch-update-202607 for ufo-testing-redis.",
        ),
    )

    assert NOTICE.parts(dropped)[ATTRIBUTED].reasons == (
        "row 0 names none of Amazon Web Services, Amazon ElastiCache, AWS",
    )
    assert f"row 0 takes '{WORKSPACE}' as the subject of a stranger's page" in (
        NOTICE.parts(handed_over)[ATTRIBUTED].reasons
    )


def test_one_notice_restated_with_extra_detail_is_still_one_claim() -> None:
    """The live pair on the deploy: one page, one revision, two rows, both alive. The second added
    the account number and the clock time, which carried it under the restatement threshold the
    extraction pass collapses on."""
    both = (
        Row(
            "page/service-notice",
            "Amazon Web Services — Asks for elasticache-july-patch-update-202607 by 30 August.",
        ),
        Row(
            "page/service-notice",
            "Amazon Web Services — Asks for elasticache-july-patch-update-202607 on "
            "ufo-testing-redis by 30 August 2026 05:59:59 UTC.",
        ),
    )

    assert NOTICE.parts(both)[DISTINCT].reasons == ("2 rows carry the elasticache update claim",)


def test_a_pull_requests_own_gate_results_are_not_wiki_rows() -> None:
    """The heaviest stream on the deploy at 4.33 rows a page, and one request wrote fifteen — most
    of them counts and gate names the prompt tells the pass to leave to the system."""
    ci = (
        Row("page/pull-request-2311", "Pull request 2311 — Frontend checks passed 985 tests."),
        Row("page/pull-request-2311", "Pull request 2311 — `make check` passed all gates."),
        Row(
            "page/pull-request-2311",
            "Pull request 2311 — Validation included `make test-one FILE=core/tests/x.py`.",
        ),
    )

    assert PULL_REQUEST.parts(ci)[STATED].reasons == (
        "row 0: a value the source reports about itself: 985",
        "row 1: a value the source reports about itself: make check",
        "row 2: a value the source reports about itself: make test-one",
    )


def test_the_overview_must_keep_every_party_it_was_given() -> None:
    merged = (
        Row("", f"{WORKSPACE} renewed the sandbox lease and applied the ElastiCache update."),
    )
    failures = OVERVIEW.parts(merged)[ATTRIBUTED]

    assert failures.reasons == (
        "the Overview drops Amazon Web Services",
        "the Overview drops Ada Yang",
    )
    assert OVERVIEW.parts((Row("", " ".join(OVERVIEW.facts)),))[ATTRIBUTED].passed


def _sample(case: WikiCase, met: bool) -> Generated:
    return Generated({name: Failures(() if met else ("no",), {}) for name in case.dimensions}, {})


def test_a_dimension_two_runs_in_three_get_right_is_a_failed_dimension() -> None:
    """Best-of-N is what let a repeatedly-observed defect score as met: a member reads whichever
    rebuild ran, so the verdict answers to every sample."""
    suite = WikiGenerationSuite(cases=CASES, digest="sha256:test")
    mixed = [_sample(SYNC, met) for met in (True, True, False)]

    result = suite._scored(SYNC, DISTINCT, mixed)

    assert not result.passed
    assert result.reason.startswith("2/3 samples distinct")
    assert (result.evidence[SAMPLES_MET], result.evidence[SAMPLES_SCORED]) == (2, 3)


def test_the_sample_rate_counts_runs_where_the_case_rate_counts_dimensions() -> None:
    suite = WikiGenerationSuite(cases=CASES, digest="sha256:test")
    cases = (
        suite._scored(SYNC, DISTINCT, [_sample(SYNC, True) for _ in range(3)]),
        suite._scored(SYNC, STATED, [_sample(SYNC, met) for met in (True, False, False)]),
    )

    assert [case.passed for case in cases] == [True, False]
    assert sample_rate(cases) == 4 / 6


def test_every_corpus_spanning_pages_runs_in_both_batchings() -> None:
    """The batch is the variable, so each multi-page corpus is a pair holding its pages constant."""
    paired = {
        case.name.removesuffix("-in-one-pass").removesuffix("-across-passes")
        for case in EXTRACTION_CASES
        if len(case.pages) > 1
    }

    for corpus in paired:
        one, split = BY_NAME[f"{corpus}-in-one-pass"], BY_NAME[f"{corpus}-across-passes"]
        assert one.pages == split.pages
        assert len(one.batches) == 1
        assert len(split.batches) == len(split.pages)


def test_the_corpus_carries_the_streams_the_deploy_actually_syncs() -> None:
    """Measured on the testing workspace 2026-08-26: `messages`, `comments`, `pull_requests` and
    `issues` are 96% of the pages a wiki is built from. A corpus of invented email is not that."""
    streams = {page.stream for case in EXTRACTION_CASES for page in case.pages}

    assert streams == {"messages", "comments", "pull_requests"}


def test_the_suite_writes_on_the_background_model_and_judges_on_the_semantic_one() -> None:
    task = {task.name: task for task in TASKS}["wiki_generation"]

    assert task.simulator_model == DEFAULT_BACKGROUND_JOBS_MODEL
    assert task.judge_model == SEMANTIC_JUDGE_MODEL
    assert len(task.cases) == 3 * len(EXTRACTION_CASES) + 2 * len(OVERVIEW_CASES)
    assert task.narrow is not None
    assert task.narrow(("daily-sync-across-passes-distinct",)).cases == (
        "daily-sync-across-passes-distinct",
    )
    assert wiki_generation_task(SEMANTIC_JUDGE_MODEL).digest == task.digest
