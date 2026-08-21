import asyncio
import subprocess
import tomllib
from pathlib import Path

import pytest
import tomli_w

from evals.ablate import (
    WORKTREES_DIR,
    Ablation,
    ArmSpec,
    CaseCount,
    ExperimentSpec,
    collect_counts,
    ingestion_suites,
    load_experiment,
    render_report,
    verdict,
)
from evals.memory_ingestion.models import (
    IngestionCase,
    IngestionPage,
    content_digest,
    write_snapshot,
)
from evals.stack import Matrix

TEMPLATE = 'template = { pack = { name = "assistant_eval" } }'
INGESTION_SUITE = "memory_ingestion.longmem.information_extraction"


def _experiment(tmp_path: Path, body: str) -> Path:
    variant = tmp_path / "variant.py"
    variant.write_text("x = 1\n")
    path = tmp_path / "experiment.toml"
    path.write_text(body)
    return path


def test_load_experiment_resolves_variants_against_the_file(tmp_path: Path) -> None:
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\nsuites = ["basics"]\nbudget_usd = 5.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "knockout"\n[arm.files]\n"packs/thing.py" = "variant.py"\n',
    )
    spec = load_experiment(path)
    assert spec.arm[0].files["packs/thing.py"] == (tmp_path / "variant.py").resolve()


def test_load_experiment_rejects_a_missing_variant(tmp_path: Path) -> None:
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\nsuites = ["basics"]\nbudget_usd = 5.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "knockout"\n[arm.files]\n"packs/thing.py" = "gone.py"\n',
    )
    with pytest.raises(SystemExit, match=r"gone\.py"):
        load_experiment(path)


def test_the_control_arm_name_is_reserved() -> None:
    with pytest.raises(ValueError, match="implicit"):
        ArmSpec(name="control", files={})


def test_duplicate_arm_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate"):
        ExperimentSpec(
            name="exp",
            base="origin/main",
            suites=("basics",),
            budget_usd=5.0,
            template={"pack": {"name": "assistant_eval"}},
            arm=(ArmSpec(name="twin", files={}), ArmSpec(name="twin", files={})),
        )


def _snapshot(root: Path) -> Path:
    body = "The project codename is Polaris and it remains active for the launch."
    write_snapshot(
        root,
        upstreams=(),
        builder_digest="sha256:" + "0" * 64,
        cases=(
            IngestionCase(
                id="longmem/polaris",
                corpus="longmem",
                category="information_extraction",
                question="What is the project codename?",
                expected_answer="Polaris",
                evidence_refs=("longmem/polaris/session/answer",),
            ),
            IngestionCase(
                id="longmem/atlas",
                corpus="longmem",
                category="information_extraction",
                question="Which team owns the launch?",
                expected_answer="Atlas",
            ),
            IngestionCase(
                id="locomo/conv-26/120",
                corpus="locomo",
                category="multi_session",
                question="When did the move happen?",
                expected_answer="In May",
            ),
        ),
        pages=(
            IngestionPage(
                source_ref="longmem/polaris/answer/00/00.txt",
                evidence_ref="longmem/polaris/session/answer",
                body=body,
                digest=content_digest(body),
                origin="longmem:polaris",
            ),
        ),
    )
    return root


def _ingestion_spec(tmp_path: Path, cases: tuple[str, ...] = ()) -> ExperimentSpec:
    return ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=(INGESTION_SUITE,),
        cases=cases,
        memory_ingestion=_snapshot(tmp_path / "snapshot"),
        budget_usd=500.0,
        template={"pack": {"name": "assistant"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )


def test_ingestion_suites_group_the_snapshot_by_corpus_and_category(tmp_path: Path) -> None:
    assert ingestion_suites(_snapshot(tmp_path / "snapshot")) == {
        INGESTION_SUITE: ("longmem/polaris", "longmem/atlas"),
        "memory_ingestion.locomo.multi_session": ("locomo/conv-26/120",),
    }


def test_load_experiment_resolves_the_snapshot_against_the_file(tmp_path: Path) -> None:
    _snapshot(tmp_path / "snapshot")
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\n'
        f'suites = ["{INGESTION_SUITE}"]\n'
        'memory_ingestion = "snapshot"\nbudget_usd = 500.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "knockout"\n[arm.files]\n"packs/thing.py" = "variant.py"\n',
    )
    spec = load_experiment(path)
    assert spec.memory_ingestion == (tmp_path / "snapshot").resolve()


def test_load_experiment_rejects_a_missing_snapshot(tmp_path: Path) -> None:
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\n'
        f'suites = ["{INGESTION_SUITE}"]\n'
        'memory_ingestion = "gone"\nbudget_usd = 500.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "knockout"\n[arm.files]\n"packs/thing.py" = "variant.py"\n',
    )
    with pytest.raises(SystemExit, match=r"snapshot\.json"):
        load_experiment(path)


def test_an_ingestion_suite_without_a_snapshot_is_rejected() -> None:
    with pytest.raises(ValueError, match="need memory_ingestion"):
        ExperimentSpec(
            name="exp",
            base="origin/main",
            suites=(INGESTION_SUITE,),
            budget_usd=5.0,
            template={"pack": {"name": "assistant"}},
            arm=(),
        )


def test_the_budget_preflight_counts_ingestion_cases_from_the_snapshot(tmp_path: Path) -> None:
    """The ingestion suites are built from the corpus inside a materialized stack, so they are
    absent from `TASKS` and the estimate has to come from the snapshot."""
    ablation = Ablation(repo=tmp_path, spec=_ingestion_spec(tmp_path), out=tmp_path / "out")
    narrowed = Ablation(
        repo=tmp_path,
        spec=_ingestion_spec(tmp_path, cases=("longmem/polaris",)),
        out=tmp_path / "out",
    )

    assert ablation._planned_cases() == 2
    assert narrowed._planned_cases() == 1


def test_the_preflight_names_an_unknown_ingestion_suite_or_case(tmp_path: Path) -> None:
    spec = _ingestion_spec(tmp_path)
    unknown_suite = Ablation(
        repo=tmp_path,
        spec=spec.model_copy(update={"suites": ("memory_ingestion.longmem.typo",)}),
        out=tmp_path / "out",
    )
    unknown_case = Ablation(
        repo=tmp_path,
        spec=spec.model_copy(update={"cases": ("locomo/conv-26/120",)}),
        out=tmp_path / "out",
    )

    with pytest.raises(SystemExit, match="unknown suites"):
        unknown_suite._planned_cases()
    with pytest.raises(SystemExit, match="unknown eval case"):
        unknown_case._planned_cases()


def test_the_preflight_narrows_the_skill_loading_suite(tmp_path: Path) -> None:
    """An experiment on two skill_loading cases estimates two cases per run, not the whole
    catalog: the suite narrows, so the budget and the arm's `--case` arguments agree."""
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("skill_loading",),
        cases=("daily-brief-review", "daily-brief-approval"),
        budget_usd=50.0,
        template={"pack": {"name": "assistant_hosted"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )

    assert Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._planned_cases() == 2


def test_every_matrix_row_carries_the_ingestion_snapshot(tmp_path: Path) -> None:
    """The stack materializes the corpus per row and passes the snapshot with the readiness state it
    produced, so the snapshot must ride the row: `--memory-ingestion` in `args` is rejected."""
    spec = _ingestion_spec(tmp_path, cases=("longmem/polaris",)).model_copy(update={"repeats": 2})
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    written = ablation.matrix(spec.arm[0], tmp_path / "ablate-template.toml")
    matrix = Matrix.model_validate(tomllib.loads(tomli_w.dumps(written)))

    assert len(matrix.run) == 2
    for row in matrix.run:
        assert row.memory_ingestion == spec.memory_ingestion
        assert row.args == (
            "--concurrency",
            "4",
            "--only",
            INGESTION_SUITE,
            "--case",
            "longmem/polaris",
        )


def test_a_matrix_row_without_a_corpus_names_no_snapshot(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    written = ablation.matrix(spec.arm[0], tmp_path / "ablate-template.toml")

    assert "memory_ingestion" not in written["run"][0]


def _record(cases: list[dict]) -> dict:
    return {"reports": [{"cases": cases}]}


def _case(
    name: str,
    passes: int,
    samples: int,
    passed: bool | None = None,
    excluded_key: str = "excludedSamples",
    excluded: int = 0,
) -> dict:
    return {
        "name": name,
        "passed": passes > 0 if passed is None else passed,
        "evidence": {
            "attempts": [
                {"passed": index < passes, "costMicroUsd": 250_000} for index in range(samples)
            ],
            excluded_key: excluded,
        },
    }


def test_collect_counts_sums_samples_across_repeats_and_prices_them() -> None:
    counts, cost = collect_counts(
        [
            _record([_case("routing", 1, 3)]),
            _record([_case("routing", 0, 3), {"name": "flaky", "excluded": True, "passed": False}]),
        ]
    )
    assert counts["routing"] == CaseCount(1, 6, counts["routing"].reasons)
    assert "flaky" not in counts
    assert cost == pytest.approx(1.50)


def test_a_runner_without_attempts_counts_the_case_verdict_as_one_sample() -> None:
    counts, cost = collect_counts(
        [
            _record(
                [
                    {"name": "arc", "passed": True, "evidence": {}},
                    {"name": "silent", "passed": False, "reason": "spoke", "evidence": {}},
                ]
            ),
            _record([{"name": "arc", "passed": False, "reason": "no wake", "evidence": {}}]),
        ]
    )
    assert counts["arc"] == CaseCount(1, 2, ("no wake",))
    assert counts["silent"] == CaseCount(0, 1, ("spoke",))
    assert cost == 0.0


def test_collect_counts_leaves_infra_excluded_samples_out_of_the_denominator() -> None:
    counts, cost = collect_counts(
        [
            _record([_case("routing", 1, 3, excluded=2)]),
            _record([_case("drafting", 1, 2, excluded_key="excludedTrials", excluded=1)]),
        ]
    )
    assert counts["routing"] == CaseCount(1, 1, counts["routing"].reasons)
    assert counts["drafting"] == CaseCount(1, 1, counts["drafting"].reasons)
    assert cost == pytest.approx(1.25)


def test_verdict_needs_a_real_gap_or_a_total_flip() -> None:
    assert verdict(CaseCount(3, 3, ()), CaseCount(0, 3, ())) == "regressed"
    assert verdict(CaseCount(3, 3, ()), CaseCount(1, 3, ())) == "regressed"
    assert verdict(CaseCount(0, 3, ()), CaseCount(3, 3, ())) == "improved"
    assert verdict(CaseCount(2, 3, ()), CaseCount(3, 3, ())) == "needs-samples"
    assert verdict(CaseCount(2, 3, ()), CaseCount(2, 3, ())) == "flat"
    assert verdict(CaseCount(1, 1, ()), CaseCount(0, 1, ())) == "regressed"
    assert verdict(CaseCount(1, 1, ()), CaseCount(1, 1, ())) == "flat"


def test_verdict_holds_off_when_the_arm_ran_fewer_samples() -> None:
    assert verdict(CaseCount(6, 6, ()), CaseCount(3, 3, ())) == "needs-samples"
    assert verdict(CaseCount(6, 6, ()), CaseCount(1, 3, ())) == "needs-samples"
    assert verdict(CaseCount(3, 6, ()), CaseCount(3, 3, ())) == "needs-samples"
    assert verdict(CaseCount(3, 3, ()), CaseCount(0, 0, ())) == "needs-samples"
    assert verdict(CaseCount(6, 6, ()), CaseCount(0, 3, ())) == "regressed"
    assert verdict(CaseCount(0, 6, ()), CaseCount(3, 3, ())) == "improved"


def test_render_report_names_the_moved_cases(tmp_path: Path) -> None:
    from evals.ablate import ArmResult

    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    results = (
        ArmResult("control", {"routing": CaseCount(3, 3, ())}, 1.0),
        ArmResult("knockout", {"routing": CaseCount(0, 3, ("did not load",))}, 1.0),
    )
    report = render_report(spec, results)
    assert "routing regressed" in report
    assert "did not load" in report


def test_an_arm_missing_its_repo_path_is_recorded_as_a_failed_arm(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "kept.py").write_text("x = 1\n")
    git = ("git", "-c", "user.email=t@evals.invalid", "-c", "user.name=t", "-C", str(repo))
    for args in (("init", "-q"), ("add", "kept.py"), ("commit", "-qm", "root")):
        subprocess.run((*git, *args), check=True, capture_output=True)
    base = subprocess.run(
        (*git, "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    ).stdout.strip()
    spec = ExperimentSpec(
        name="exp",
        base=base,
        suites=("basics",),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={"gone.py": tmp_path / "variant.py"}),),
    )
    ablation = Ablation(repo=repo, spec=spec, out=tmp_path / "out")
    result = asyncio.run(ablation._arm(spec.arm[0], base, asyncio.Semaphore(1)))
    assert result.error is not None
    assert "gone.py" in result.error
    assert not (repo / WORKTREES_DIR / "exp" / "knockout").exists()
