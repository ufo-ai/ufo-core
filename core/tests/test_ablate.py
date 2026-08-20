import asyncio
import subprocess
from pathlib import Path

import pytest

from evals.ablate import (
    WORKTREES_DIR,
    Ablation,
    ArmSpec,
    CaseCount,
    ExperimentSpec,
    collect_counts,
    load_experiment,
    render_report,
    verdict,
)

TEMPLATE = 'template = { pack = { name = "assistant_eval" } }'


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
