import asyncio
import json
import os
import signal
import subprocess
import tomllib
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest
import tomli_w

import evals.stack as eval_stack
from evals import ablate
from evals.ablate import (
    BUILT_TREES,
    LOGS_DIR,
    REMOTE_CLIENT_BINARY,
    RUNS_DIR,
    STACK_LOG,
    STACK_RUNS_DIR,
    WORKTREES_DIR,
    Ablation,
    ArmReplacement,
    ArmResult,
    ArmSpec,
    CaseCount,
    ExperimentSpec,
    StackRun,
    collect_counts,
    collect_metrics,
    ingestion_suites,
    load_experiment,
    record_gaps,
    render_report,
    verdict,
)
from evals.harness.harness import EvalCaseResult, EvalReport
from evals.harness.viewer import EvalRun
from evals.memory_ingestion.materialize import DERIVATION_MODEL, DerivedCorpus, DerivedFact
from evals.memory_ingestion.models import (
    IngestionCase,
    IngestionPage,
    content_digest,
    load_snapshot,
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


def test_load_experiment_keeps_an_exact_repo_replacement(tmp_path: Path) -> None:
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\nsuites = ["basics"]\nbudget_usd = 5.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "model"\n'
        '[[arm.replacements]]\npath = "profile.py"\nold = "MODEL = \\"a\\""\n'
        'new = "MODEL = \\"b\\""\n',
    )

    spec = load_experiment(path)

    assert spec.arm[0].files == {}
    assert spec.arm[0].replacements == (
        ArmReplacement(path="profile.py", old='MODEL = "a"', new='MODEL = "b"'),
    )


def test_arm_replacement_requires_one_exact_match(tmp_path: Path) -> None:
    target = tmp_path / "profile.py"
    target.write_text('MODEL = "a"\n')
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(
            ArmSpec(
                name="model",
                replacements=(
                    ArmReplacement(path="profile.py", old='MODEL = "a"', new='MODEL = "b"'),
                ),
            ),
        ),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    ablation._apply_arm(spec.arm[0], tmp_path)

    assert target.read_text() == 'MODEL = "b"\n'
    with pytest.raises(RuntimeError, match="matched 0 times"):
        ablation._apply_arm(spec.arm[0], tmp_path)


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
    snapshot = _snapshot(tmp_path / "snapshot")
    corpus = tmp_path / "derived-corpus.json"
    derived = DerivedCorpus.build(
        load_snapshot(snapshot).manifest.digest,
        DERIVATION_MODEL,
        (
            DerivedFact(
                source_ref="longmem/polaris/answer/00/00.txt",
                body="The project codename is Polaris.",
                memory_kind="fact",
                confidence=8,
                as_of=datetime(2026, 8, 17, tzinfo=UTC),
            ),
        ),
    )
    corpus.write_text(derived.model_dump_json())
    return ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=(INGESTION_SUITE,),
        cases=cases,
        memory_ingestion=snapshot,
        memory_ingestion_corpus=corpus,
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
    spec = _ingestion_spec(tmp_path)
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\n'
        f'suites = ["{INGESTION_SUITE}"]\n'
        'memory_ingestion = "snapshot"\n'
        'memory_ingestion_corpus = "derived-corpus.json"\nbudget_usd = 500.0\n'
        f"{TEMPLATE}\n"
        '[[arm]]\nname = "knockout"\n[arm.files]\n"packs/thing.py" = "variant.py"\n',
    )
    loaded = load_experiment(path)
    assert loaded.memory_ingestion == spec.memory_ingestion
    assert loaded.memory_ingestion_corpus == spec.memory_ingestion_corpus


def test_load_experiment_rejects_a_missing_snapshot(tmp_path: Path) -> None:
    path = _experiment(
        tmp_path,
        'name = "exp"\nbase = "origin/main"\n'
        f'suites = ["{INGESTION_SUITE}"]\n'
        'memory_ingestion = "gone"\n'
        'memory_ingestion_corpus = "gone.json"\nbudget_usd = 500.0\n'
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


def test_an_ingestion_suite_without_a_shared_derivation_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="need memory_ingestion_corpus"):
        ExperimentSpec(
            name="exp",
            base="origin/main",
            suites=(INGESTION_SUITE,),
            memory_ingestion=_snapshot(tmp_path / "snapshot"),
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
        cases=("daily-brief-generic-report", "digest-stacked-up-reports"),
        budget_usd=50.0,
        template={"pack": {"name": "assistant_hosted"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )

    assert Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._planned_cases() == 2


def test_every_ablation_arm_and_repeat_carries_one_derived_corpus(tmp_path: Path) -> None:
    spec = _ingestion_spec(tmp_path, cases=("longmem/polaris",)).model_copy(update={"repeats": 2})
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    arms = (ArmSpec.model_construct(name="control", files={}), spec.arm[0])
    matrices = tuple(
        Matrix.model_validate(
            tomllib.loads(tomli_w.dumps(ablation.matrix(arm, tmp_path / "ablate-template.toml")))
        )
        for arm in arms
    )

    assert all(len(matrix.run) == 2 for matrix in matrices)
    for row in (row for matrix in matrices for row in matrix.run):
        assert row.memory_ingestion == spec.memory_ingestion
        assert row.memory_ingestion_corpus == spec.memory_ingestion_corpus
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


def test_remote_ablation_routes_every_arm_through_the_remote_transport(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        remote=True,
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    written = ablation.matrix(spec.arm[0], tmp_path / "ablate-template.toml")
    matrix = Matrix.model_validate(tomllib.loads(tomli_w.dumps(written)))

    assert matrix.run[0].args == (
        "--concurrency",
        "4",
        "--remote",
        "--budget-usd",
        "2.5",
        "--only",
        "basics",
    )


def test_an_ablation_matrix_seeds_the_members_model_provider(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        member_model_provider="anthropic",
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    written = ablation.matrix(spec.arm[0], tmp_path / "ablate-template.toml")
    matrix = Matrix.model_validate(tomllib.loads(tomli_w.dumps(written)))

    assert matrix.run[0].member_model_provider == "anthropic"


def test_an_ablation_rejects_an_unknown_member_model_provider() -> None:
    with pytest.raises(ValueError, match="member_model_provider"):
        ExperimentSpec(
            name="exp",
            base="origin/main",
            suites=("basics",),
            member_model_provider="z-ai",
            budget_usd=5.0,
            template={"pack": {"name": "assistant_eval"}},
            arm=(ArmSpec(name="knockout", files={}),),
        )


def test_remote_run_allocations_conserve_the_experiment_budget(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        repeats=2,
        remote=True,
        budget_usd=10.000003,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="left", files={}), ArmSpec(name="right", files={})),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")
    control = ArmSpec.model_construct(name="control", files={})

    matrices = (
        ablation.matrix(control, tmp_path / "template.toml"),
        *(ablation.matrix(arm, tmp_path / "template.toml") for arm in spec.arm),
    )
    allocations = []
    for matrix in matrices:
        for run in Matrix.model_validate(matrix).run:
            index = run.args.index("--budget-usd")
            allocations.append(Decimal(run.args[index + 1]))

    assert len(allocations) == 6
    assert sum(allocations) == Decimal("10.000003")


def test_remote_ablation_preflights_the_selected_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "ufo"
    client.write_text("#!/bin/sh\nprintf '%s\\n' 'Usage: ufo [--remote] [--json]'\n")
    client.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    spec = _spec(remote=True)

    selected = asyncio.run(
        Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._preflight_remote_client()
    )

    assert selected == client.resolve()


def test_remote_ablation_rejects_a_client_without_the_remote_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = tmp_path / "ufo"
    client.write_text("#!/bin/sh\nprintf '%s\\n' 'Usage: ufo [message...]'\n")
    client.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path))
    spec = _spec(remote=True)

    with pytest.raises(SystemExit, match="ufo client with --remote and --json"):
        asyncio.run(
            Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._preflight_remote_client()
        )


def test_every_arm_uses_the_experiment_model_and_reasoning(tmp_path: Path) -> None:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        budget_usd=5.0,
        model="google/gemini-3.7-flash",
        reasoning="medium",
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    written = ablation.matrix(spec.arm[0], tmp_path / "ablate-template.toml")
    matrix = Matrix.model_validate(tomllib.loads(tomli_w.dumps(written)))

    assert matrix.run[0].model == "google/gemini-3.7-flash"
    assert matrix.run[0].reasoning == "medium"


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


def test_collect_metrics_averages_each_suite_score_over_an_arms_records() -> None:
    metrics = collect_metrics(
        [
            {
                "reports": [
                    {
                        "name": "ufo-app-bench",
                        "cases": [],
                        "metrics": [
                            {"name": "process_score", "value": 0.0},
                            {"name": "app_score", "value": 0.5},
                        ],
                    }
                ]
            },
            {
                "reports": [
                    {
                        "name": "ufo-app-bench",
                        "cases": [],
                        "metrics": [
                            {"name": "process_score", "value": 0.6},
                            {"name": "app_score", "value": 0.4},
                        ],
                    }
                ]
            },
        ]
    )

    assert metrics == {"ufo-app-bench/process_score": 0.3, "ufo-app-bench/app_score": 0.45}


def test_the_report_scores_two_arms_that_pass_no_case() -> None:
    """A suite whose case is a whole build spends whole runs failing every case, and pass counts
    read flat across arms that are nowhere near each other. The scores the suite already wrote say
    which way the arm moved."""

    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("ufo-app-bench",),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    results = (
        ArmResult(
            "control",
            {"kanban-board": CaseCount(0, 1, ("no terminal transcript",))},
            1.0,
            metrics={"ufo-app-bench/process_score": 0.0},
        ),
        ArmResult(
            "knockout",
            {"kanban-board": CaseCount(0, 1, ("no terminal transcript",))},
            1.0,
            metrics={"ufo-app-bench/process_score": 0.222},
        ),
    )

    report = render_report(spec, results)

    assert "no case moved" in report
    assert "| ufo-app-bench/process_score | 0.000 | 0.222 (+0.222) |" in report


def test_an_arm_keeps_a_worktree_when_materialization_fails_late(tmp_path: Path) -> None:
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
    result = asyncio.run(ablation._arm(spec.arm[0], base, asyncio.Semaphore(1), None))
    root = repo / WORKTREES_DIR / "exp" / "knockout"
    assert result.error is not None
    assert "gone.py" in result.error
    assert result.kept_worktree == root
    assert root.exists()


def test_an_arm_materializes_beside_retained_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(repeats=1)
    materialized: list[Path] = []

    def materialize(
        self: Ablation,
        arm: ArmSpec,
        base: str,
        root: Path,
        remote_client: Path | None,
    ) -> None:
        assert remote_client is None
        (root / RUNS_DIR).mkdir(parents=True)
        (root / RUNS_DIR / "0.json").write_text(json.dumps(_stack_record(0, spec.suites)))
        materialized.append(root)

    async def stack(self: Ablation, arm: ArmSpec, root: Path, index: int = 0) -> tuple[int, str]:
        return 0, ""

    async def drop_databases(self: Ablation, root: Path) -> None:
        return None

    monkeypatch.setattr(Ablation, "_materialize", materialize)
    monkeypatch.setattr(Ablation, "_stack", stack)
    monkeypatch.setattr(Ablation, "_drop_databases", drop_databases)
    kept = tmp_path / WORKTREES_DIR / spec.name / spec.arm[0].name
    kept.mkdir(parents=True)
    marker = kept / "keep"
    marker.write_text("evidence")
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    result = asyncio.run(ablation._arm(spec.arm[0], "base", asyncio.Semaphore(1), None))

    assert result.error is None
    assert result.kept_worktree is None
    assert materialized == [kept.with_name(f"{kept.name}.2")]
    assert marker.read_text() == "evidence"


def _spec(**updates: object) -> ExperimentSpec:
    spec = ExperimentSpec(
        name="exp",
        base="origin/main",
        suites=("closing_message", "response_register"),
        repeats=2,
        budget_usd=50.0,
        template={"pack": {"name": "assistant_eval"}},
        arm=(ArmSpec(name="knockout", files={}),),
    )
    return spec.model_copy(update=updates)


def _stack_record(index: int, suites: tuple[str, ...]) -> dict:
    return {
        "label": f"ablate-knockout-{index}",
        "reports": [{"name": suite, "cases": []} for suite in suites],
    }


def test_ablation_writes_one_offline_viewer_for_archived_app_artifacts(tmp_path: Path) -> None:
    run = EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC),
        label="ablate-control-0",
        agent="assistant",
        ufo_version="test",
        revision="abc123",
        reports=(
            EvalReport(
                name="New application",
                suite="new_application",
                digest="sha256:test",
                cases=(
                    EvalCaseResult(
                        name="A07-named-homepage-journey",
                        passed=True,
                        reason="passed",
                        evidence={
                            "attempts": [
                                {
                                    "artifactContents": [
                                        {
                                            "name": "homepage-interactive.html",
                                            "mediaType": "text/html",
                                            "dataUri": (
                                                "data:text/html;base64,PG1haW4+QXBwPC9tYWluPg=="
                                            ),
                                        }
                                    ]
                                }
                            ]
                        },
                    ),
                ),
            ),
        ),
    )
    archive = tmp_path / "out/runs/control"
    archive.mkdir(parents=True)
    (archive / f"{run.id}.json").write_text(run.model_dump_json(by_alias=True, exclude_none=True))
    ablation = Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")

    viewer = ablation._write_viewer()

    assert viewer == tmp_path / "out/viewer/index.html"
    page = viewer.read_text()
    assert "homepage-interactive.html" in page
    assert "data:text/html;base64,PG1haW4+QXBwPC9tYWluPg==" in page
    assert "http://" not in page


def _viewer_run(label: str) -> EvalRun:
    return EvalRun(
        id=uuid4(),
        created_at=datetime.now(UTC),
        label=label,
        agent="assistant",
        ufo_version="test",
        revision="abc123",
        reports=(),
    )


def test_ablation_viewer_keeps_valid_runs_beside_a_truncated_json_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "out/runs"
    left = _viewer_run("control")
    right = _viewer_run("treatment")
    for arm in ("a-control", "b-broken", "c-treatment"):
        (archive / arm).mkdir(parents=True)
    (archive / "a-control/run.json").write_text(left.model_dump_json())
    (archive / "b-broken/run.json").write_text('{"id":')
    (archive / "c-treatment/run.json").write_text(right.model_dump_json())
    captured: list[tuple[EvalRun, ...]] = []

    def capture(root: Path, runs: tuple[EvalRun, ...]) -> Path:
        captured.append(runs)
        return root / "index.html"

    monkeypatch.setattr(ablate, "write_viewer", capture)
    experiment = Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")

    experiment._write_viewer()
    experiment._write_viewer()

    assert [run.label for run in captured[0]] == [
        "control",
        "Invalid archive record: b-broken/run.json",
        "treatment",
    ]
    assert captured[0][1] == captured[1][1]
    error = captured[0][1].reports[0].cases[0]
    assert error.passed is False
    assert error.evidence == {
        "record": "b-broken/run.json",
        "error": "ValidationError",
    }


def test_ablation_viewer_isolates_one_truncated_jsonl_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = tmp_path / "out/runs/control"
    archive.mkdir(parents=True)
    first = _viewer_run("first")
    second = _viewer_run("second")
    (archive / "runs.jsonl").write_text(
        "\n".join((first.model_dump_json(), '{"id":', second.model_dump_json())) + "\n"
    )
    captured: list[EvalRun] = []

    def capture(root: Path, runs: tuple[EvalRun, ...]) -> Path:
        captured.extend(runs)
        return root / "index.html"

    monkeypatch.setattr(ablate, "write_viewer", capture)

    Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")._write_viewer()

    assert [run.label for run in captured] == [
        "first",
        "Invalid archive record: control/runs.jsonl:2",
        "second",
    ]
    assert captured[1].reports[0].cases[0].name == "control/runs.jsonl:2"


def test_max_stacks_bounds_all_arm_repeats_and_releases_after_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec(
        repeats=3,
        max_stacks=2,
        arm=(ArmSpec(name="left", files={}), ArmSpec(name="right", files={})),
    )
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")
    arms = (ArmSpec.model_construct(name="control", files={}), *spec.arm)
    active = 0
    peak = 0
    calls: list[tuple[str, int]] = []
    first_pair = asyncio.Event()

    async def stack(self: Ablation, arm: ArmSpec, root: Path, index: int = 0) -> tuple[int, str]:
        nonlocal active, peak
        call = (arm.name, index)
        calls.append(call)
        active += 1
        peak = max(peak, active)
        try:
            if len(calls) == 2:
                first_pair.set()
            if len(calls) <= 2:
                await first_pair.wait()
            if call == ("right", 1):
                raise RuntimeError("stack failed")
            return 0, f"{arm.name}-{index}"
        finally:
            active -= 1

    async def run() -> tuple[tuple[ablate.StackRun, ...], ...]:
        slots = asyncio.Semaphore(spec.max_stacks)
        return tuple(
            await asyncio.gather(
                *(ablation._run_arm_stacks(arm, tmp_path / arm.name, slots) for arm in arms)
            )
        )

    monkeypatch.setattr(Ablation, "_stack", stack)
    results = asyncio.run(run())

    assert peak == 2
    assert sorted(calls) == sorted(
        (arm.name, index) for arm in arms for index in range(spec.repeats)
    )
    assert [[item.index for item in result] for result in results] == [[0, 1, 2]] * 3
    assert [[item.output for item in result] for result in results] == [
        ["control-0", "control-1", "control-2"],
        ["left-0", "left-1", "left-2"],
        ["right-0", "", "right-2"],
    ]
    assert results[2][1].error == "repeat 1: RuntimeError: stack failed"


@pytest.mark.parametrize(
    ("ignored", "exit_signal"),
    (
        ((signal.SIGINT,), signal.SIGTERM),
        ((signal.SIGINT, signal.SIGTERM), signal.SIGKILL),
    ),
)
async def test_cancelled_stack_escalates_and_reaps_before_releasing_permit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ignored: tuple[signal.Signals, ...],
    exit_signal: signal.Signals,
) -> None:
    started = asyncio.Event()
    signalled = asyncio.Event()
    may_exit = asyncio.Event()
    first_reaped = asyncio.Event()
    entered: list[str] = []
    sessions: list[bool] = []
    signals: list[signal.Signals] = []
    processes = 0

    class Process:
        def __init__(self, index: int) -> None:
            self.index = index
            self.pid = 700 + index
            self.returncode: int | None = None

        async def communicate(self) -> tuple[bytes, bytes]:
            if self.index == 0:
                started.set()
                await may_exit.wait()
                self.returncode = -exit_signal
                first_reaped.set()
            else:
                self.returncode = 0
            return b"complete", b""

    async def create(*_argv: str, **kwargs: object) -> Process:
        nonlocal processes
        sessions.append(bool(kwargs["start_new_session"]))
        process = Process(processes)
        processes += 1
        return process

    def kill_group(pid: int, sent: signal.Signals) -> None:
        assert pid == 700
        signals.append(sent)
        if sent == exit_signal:
            signalled.set()

    monkeypatch.setattr(ablate.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(eval_stack.os, "killpg", kill_group)
    monkeypatch.setattr(eval_stack, "STACK_CANCEL_SIGINT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(eval_stack, "STACK_CANCEL_SIGTERM_WAIT_SECONDS", 0.01)
    ablation = Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")
    permit = asyncio.Semaphore(1)

    async def guarded(name: str) -> tuple[int, str]:
        async with permit:
            entered.append(name)
            return await ablation._stack(_spec().arm[0], tmp_path / name)

    first = asyncio.create_task(guarded("first"))
    await started.wait()
    second = asyncio.create_task(guarded("second"))
    first.cancel()
    await signalled.wait()

    assert entered == ["first"]
    assert not first_reaped.is_set()
    assert signals == [*ignored, exit_signal]

    may_exit.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert await second == (0, "complete")
    assert entered == ["first", "second"]
    assert first_reaped.is_set()
    assert sessions == [True, True]


def test_an_arm_installs_no_dev_group_and_the_failure_names_the_package(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dev group is the repo's lint, type and test tooling, which no arm runs, and a package in
    it that builds by downloading a release binary fails wherever that download is closed."""
    commands = []

    def failing_sync(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(argv)
        return subprocess.CompletedProcess(
            argv, 1, "", "error: Failed to build `actionlint-py==1.7.12.24`"
        )

    monkeypatch.setattr(ablate.subprocess, "run", failing_sync)
    ablation = Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")

    with pytest.raises(RuntimeError, match="actionlint-py"):
        ablation._sync(tmp_path / "worktree")

    assert commands == [("uv", "sync", "--no-dev", "--reinstall")]


def test_an_arm_reinstalls_so_a_pack_or_extension_replacement_reaches_its_venv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Extensions and packs install as copies, so a sync that reuses the build already present for
    their unchanged version leaves the arm running base code while it reports itself as the
    variant — an arm that measures nothing and says nothing about it."""
    commands = []

    def sync(argv: tuple[str, ...], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(ablate.subprocess, "run", sync)
    Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")._sync(tmp_path / "worktree")

    assert "--reinstall" in commands[0]


def test_an_arm_carries_the_app_page_build_output(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "worktree"
    for index, path in enumerate(BUILT_TREES):
        source = repo / path / f"built-{index}"
        source.parent.mkdir(parents=True)
        source.write_text(str(index))

    Ablation(repo=repo, spec=_spec(), out=tmp_path / "out")._carry_build_output(worktree)

    for index, path in enumerate(BUILT_TREES):
        assert (worktree / path / f"built-{index}").read_text() == str(index)


def test_a_remote_arm_carries_the_preflighted_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    root = tmp_path / "worktree"
    egress = repo / ablate.EGRESS_BINARY
    egress.parent.mkdir(parents=True)
    egress.write_bytes(b"egress")
    client = tmp_path / "selected-ufo"
    client.write_bytes(b"exact-client-bytes")

    def add_worktree(self: Ablation, *args: str) -> str:
        assert args[:2] == ("worktree", "add")
        Path(args[3]).mkdir(parents=True)
        return ""

    monkeypatch.setattr(Ablation, "_git", add_worktree)
    monkeypatch.setattr(Ablation, "_sync", lambda self, path: None)
    spec = _spec(remote=True)
    ablation = Ablation(repo=repo, spec=spec, out=tmp_path / "out")

    ablation._materialize(spec.arm[0], "base", root, client)

    carried = root / REMOTE_CLIENT_BINARY
    assert carried.read_bytes() == client.read_bytes()
    assert carried.stat().st_mode & 0o111
    matrices = [
        Matrix.model_validate(tomllib.loads((root / f"ablate-matrix-{index}.toml").read_text()))
        for index in range(spec.repeats)
    ]
    assert [[run.label for run in matrix.run] for matrix in matrices] == [
        ["ablate-knockout-0"],
        ["ablate-knockout-1"],
    ]


def test_the_stack_runs_the_arm_environment_without_the_dev_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`uv run` syncs before it runs, so the environment the arm built only survives when the run
    refuses the dev group too. The whole output comes back, because it is what gets archived."""
    seen: dict[str, tuple[str, ...]] = {}
    output = ("stack chatter\n" * 500).encode()

    class Fake:
        returncode = 3

        async def communicate(self) -> tuple[bytes, bytes]:
            return output, b""

    async def fake_exec(*argv: str, **kwargs: object) -> Fake:
        seen["argv"] = argv
        return Fake()

    monkeypatch.setattr(ablate.asyncio, "create_subprocess_exec", fake_exec)
    spec = _spec()
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    exit_code, text = asyncio.run(ablation._stack(spec.arm[0], tmp_path / "worktree"))

    assert seen["argv"][:3] == ("uv", "run", "--no-dev")
    assert exit_code == 3
    assert text == output.decode()


def test_a_remote_stack_pins_its_carried_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: dict[str, object] = {}
    monkeypatch.setenv("UFO_CLIENT_BINARY", "/build/linux/ufo")

    class Fake:
        returncode = 0

        async def communicate(self) -> tuple[bytes, bytes]:
            return b"", b""

    async def fake_exec(*argv: str, **kwargs: object) -> Fake:
        seen.update(kwargs)
        return Fake()

    monkeypatch.setattr(ablate.asyncio, "create_subprocess_exec", fake_exec)
    spec = _spec(
        remote=True,
        template={"pack": {"name": "assistant_eval"}, "sandbox": {"backend": "local"}},
    )
    root = tmp_path / "worktree"
    carried = (root / REMOTE_CLIENT_BINARY).resolve()

    asyncio.run(Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._stack(spec.arm[0], root))

    environment = seen["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(os.pathsep)[0] == str(carried.parent)
    assert environment["UFO_CLIENT_BINARY"] == str(carried)


def test_record_gaps_name_the_repeat_and_the_suite_that_recorded_nothing() -> None:
    """A stack that paid for a suite and archived no report for it is invisible to the pass counts:
    `collect_counts` reads what landed."""
    spec = _spec()
    complete = [_stack_record(0, spec.suites), _stack_record(1, spec.suites)]

    assert record_gaps(spec, "knockout", complete) == ()
    assert record_gaps(spec, "knockout", [complete[0]]) == ("ablate-knockout-1: no record",)
    assert record_gaps(spec, "knockout", [complete[0], _stack_record(1, ("closing_message",))]) == (
        "ablate-knockout-1: no report for response_register",
    )


def test_record_gaps_name_an_infra_excluded_case() -> None:
    spec = _spec(repeats=1, suites=("closing_message",))
    record = _stack_record(0, spec.suites)
    record["reports"][0]["cases"] = [{"name": "provider-auth", "excluded": True}]

    assert record_gaps(spec, "knockout", [record]) == (
        "ablate-knockout-0: excluded case provider-auth",
    )


def test_the_report_names_every_record_gap_and_the_worktree_kept_for_it(tmp_path: Path) -> None:
    kept = tmp_path / ".local/ablate/exp/knockout"
    results = (
        ArmResult("control", {"routing": CaseCount(1, 2, ())}, 1.0),
        ArmResult(
            "knockout",
            {"routing": CaseCount(1, 1, ())},
            1.0,
            gaps=("ablate-knockout-1: no report for response_register",),
            kept_worktree=kept,
        ),
    )

    report = render_report(_spec(), results)

    assert "record gap: ablate-knockout-1: no report for response_register" in report
    assert f"worktree kept for diagnosis: {kept}" in report


def _archived_arm(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    records: list[dict],
    spec: ExperimentSpec | None = None,
) -> tuple[ArmResult, Path]:
    """One arm over a worktree the fake materialization fills, so the archive and the retention
    rule are exercised without a stack."""
    spec = spec or _spec()
    root = tmp_path / WORKTREES_DIR / spec.name / spec.arm[0].name

    def materialize(
        self: Ablation,
        arm: ArmSpec,
        base: str,
        target: Path,
        remote_client: Path | None,
    ) -> None:
        assert remote_client is None
        (target / RUNS_DIR).mkdir(parents=True)
        for index, record in enumerate(records):
            (target / RUNS_DIR / f"{index}.json").write_text(json.dumps(record))
            logs = target / STACK_RUNS_DIR / "20260821-224424" / record["label"]
            logs.mkdir(parents=True)
            (logs / "serve.log").write_text("serve booted")

    async def stack(self: Ablation, arm: ArmSpec, target: Path, index: int = 0) -> tuple[int, str]:
        return 1, "one case failed"

    monkeypatch.setattr(Ablation, "_materialize", materialize)
    monkeypatch.setattr(Ablation, "_stack", stack)
    ablation = Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")

    result = asyncio.run(ablation._arm(spec.arm[0], "base", asyncio.Semaphore(1), None))
    return result, root


@pytest.mark.parametrize("excluded_key", ("excludedSamples", "excludedTrials"))
def test_an_arm_with_partial_infra_exclusions_keeps_its_worktree_and_logs_the_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, excluded_key: str
) -> None:
    spec = _spec()
    records = [_stack_record(0, spec.suites), _stack_record(1, spec.suites)]
    records[0]["reports"][0]["cases"] = [
        _case("provider-throttle", 1, 2, excluded_key=excluded_key, excluded=1)
    ]

    result, root = _archived_arm(tmp_path, monkeypatch, records)
    report = render_report(
        spec,
        (ArmResult("control", result.counts, result.cost_usd), result),
    )

    assert result.gaps == ("ablate-knockout-0: provider-throttle has 1 infra-excluded sample(s)",)
    assert result.kept_worktree == root
    assert (
        "record gap: ablate-knockout-0: provider-throttle has 1 infra-excluded sample(s)" in report
    )


def test_a_complete_arm_archives_its_logs_and_gives_the_worktree_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _spec()
    released: list[Path] = []

    async def release_databases(self: Ablation, root: Path) -> None:
        released.append(root)

    monkeypatch.setattr(Ablation, "_drop_databases", release_databases)
    result, root = _archived_arm(
        tmp_path, monkeypatch, [_stack_record(0, spec.suites), _stack_record(1, spec.suites)]
    )
    archive = tmp_path / "out" / "runs" / "knockout"

    assert result.gaps == ()
    assert result.kept_worktree is None
    assert released == [root]
    assert not root.exists()
    assert (archive / STACK_LOG).read_text() == (
        "[repeat 0]\none case failed\n[repeat 1]\none case failed"
    )
    assert (archive / LOGS_DIR / "ablate-knockout-1" / "serve.log").read_text() == "serve booted"
    assert sorted(path.name for path in archive.glob("*.json")) == ["0.json", "1.json"]


def test_an_arm_archive_replaces_the_previous_run(tmp_path: Path) -> None:
    root = tmp_path / "root"
    records = root / RUNS_DIR
    records.mkdir(parents=True)
    (records / "current.json").write_text("{}")
    archive = tmp_path / "out" / "runs" / "knockout"
    archive.mkdir(parents=True)
    (archive / "stale.json").write_text("{}")

    Ablation(repo=tmp_path, spec=_spec(), out=tmp_path / "out")._archive(
        root, archive, (StackRun(0, 0, "current stack"),)
    )

    assert not (archive / "stale.json").exists()
    assert (archive / "current.json").is_file()
    assert (archive / STACK_LOG).read_text() == "[repeat 0]\ncurrent stack"


def test_an_arm_that_lost_a_suite_keeps_its_worktree_and_says_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stack had already paid for the cases, so its logs and its database are the only account
    of what the money bought."""
    released: list[Path] = []

    async def release_databases(self: Ablation, root: Path) -> None:
        released.append(root)

    monkeypatch.setattr(Ablation, "_drop_databases", release_databases)
    result, root = _archived_arm(tmp_path, monkeypatch, [_stack_record(0, ("closing_message",))])

    assert result.gaps == (
        "ablate-knockout-0: no report for response_register",
        "ablate-knockout-1: no record",
    )
    assert released == []
    assert result.kept_worktree == root
    assert (root / RUNS_DIR / "0.json").is_file()
    assert (tmp_path / "out" / "runs" / "knockout" / STACK_LOG).is_file()


def test_an_arm_keeps_its_worktree_when_database_cleanup_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_cleanup(self: Ablation, root: Path) -> None:
        raise RuntimeError("database still connected")

    monkeypatch.setattr(Ablation, "_drop_databases", fail_cleanup)
    result, root = _archived_arm(
        tmp_path, monkeypatch, [_stack_record(0, _spec().suites), _stack_record(1, _spec().suites)]
    )

    assert result.error == "RuntimeError: database still connected"
    assert result.kept_worktree == root
    assert root.is_dir()
    assert (tmp_path / "out" / "runs" / "knockout" / STACK_LOG).is_file()


def test_an_arm_completes_on_a_base_that_records_no_database_ownership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An arm worktree runs the `evals.stack` of the base its experiment pins, and a base older than
    the ownership record writes none. The arm ran every repeat to the end, so it reports its counts
    and gives its worktree back; the cleanup owns no database, drops none and connects to nothing.
    """

    async def connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("an arm that owns no database must not reach Postgres")

    monkeypatch.setattr(eval_stack.asyncpg, "connect", connect)
    spec = _spec(
        template={
            "pack": {"name": "assistant_eval"},
            "database": {"url": "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo"},
            "blob": {"backend": "filesystem", "root": "./blobs"},
        }
    )
    result, root = _archived_arm(
        tmp_path,
        monkeypatch,
        [_stack_record(0, spec.suites), _stack_record(1, spec.suites)],
        spec=spec,
    )

    assert result.error is None
    assert result.gaps == ()
    assert result.kept_worktree is None
    assert not root.exists()


def test_every_committed_experiment_on_head_still_applies_to_the_tree() -> None:
    """An arm on `base = "HEAD"` must match the file it edits exactly once, right now.

    `_apply_arm` counts the `old` text in the worktree and raises when the count is not one, so a
    replacement that has drifted from the file it names does not fail at preflight — it fails after
    the control arm has already spent its budget, and the run exits with nothing to compare. The
    drift arrives by ordinary editing: the text an arm reverts is the text under test, and cutting
    or rewording it in the same branch leaves the arm pointing at a sentence that is gone. An arm
    pinned to a revision instead of HEAD is excluded, because a pinned base is allowed to differ
    from the tree.
    """
    repo = Path(__file__).resolve().parents[3]
    checked = 0
    for path in sorted((repo / "evals").glob("*.toml")):
        spec = load_experiment(path)
        if spec.base != "HEAD":
            continue
        for arm in spec.arm:
            for replacement in arm.replacements:
                target = repo / replacement.path
                assert target.is_file(), f"{path.name} arm {arm.name!r}: {replacement.path} is gone"
                count = target.read_text().count(replacement.old)
                assert count == 1, (
                    f"{path.name} arm {arm.name!r} matches {replacement.path} "
                    f"{count} times, expected 1"
                )
                checked += 1
    assert checked, "no HEAD-based experiment replacements were checked"


def test_every_committed_experiment_can_afford_its_own_estimate() -> None:
    """A committed experiment must pass its own budget preflight as written.

    `_preflight` raises before the control arm materializes when the estimate is over `budget_usd`,
    and `main` takes no flag that lowers the case count or raises the budget, so an experiment whose
    budget sits under its own estimate cannot be run at all. The arithmetic is the runner's own: the
    arms plus the control, times the repeats, times the cases its suites declare.
    """
    repo = Path(__file__).resolve().parents[3]
    checked = 0
    for path in sorted((repo / "evals").glob("*.toml")):
        spec = load_experiment(path)
        if spec.memory_ingestion is not None:
            continue
        runs, cases, cost = Ablation(repo=repo, spec=spec, out=repo / "out").estimate()
        assert cost <= spec.budget_usd, (
            f"{path.name} estimates ${cost:.2f} ({runs} runs x {cases} cases at "
            f"${spec.est_usd_per_case}/case), over budget ${spec.budget_usd:.2f}"
        )
        checked += 1
    assert checked, "no committed experiment budget was checked"
