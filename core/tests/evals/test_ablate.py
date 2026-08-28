import asyncio
import json
import os
import subprocess
import tomllib
from decimal import Decimal
from pathlib import Path

import pytest
import tomli_w

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
    collect_counts,
    ingestion_suites,
    load_experiment,
    record_gaps,
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
        cases=("daily-brief-generic-report", "digest-stacked-up-reports"),
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
    result = asyncio.run(ablation._arm(spec.arm[0], base, asyncio.Semaphore(1), None))
    assert result.error is not None
    assert "gone.py" in result.error
    assert result.kept_worktree is None
    assert not (repo / WORKTREES_DIR / "exp" / "knockout").exists()


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

    assert commands == [("uv", "sync", "--no-dev")]


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
    spec = _spec(remote=True)
    root = tmp_path / "worktree"
    carried = (root / REMOTE_CLIENT_BINARY).resolve()

    asyncio.run(Ablation(repo=tmp_path, spec=spec, out=tmp_path / "out")._stack(spec.arm[0], root))

    environment = seen["env"]
    assert isinstance(environment, dict)
    assert environment["PATH"].split(os.pathsep)[0] == str(carried.parent)
    assert environment["UFO_CLIENT_BINARY"] == "/build/linux/ufo"


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, records: list[dict]
) -> tuple[ArmResult, Path]:
    """One arm over a worktree the fake materialization fills, so the archive and the retention
    rule are exercised without a stack."""
    spec = _spec()
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

    async def stack(self: Ablation, arm: ArmSpec, target: Path) -> tuple[int, str]:
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
    result, root = _archived_arm(
        tmp_path, monkeypatch, [_stack_record(0, spec.suites), _stack_record(1, spec.suites)]
    )
    archive = tmp_path / "out" / "runs" / "knockout"

    assert result.gaps == ()
    assert result.kept_worktree is None
    assert not root.exists()
    assert (archive / STACK_LOG).read_text() == "one case failed"
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
        root, archive, "current stack"
    )

    assert not (archive / "stale.json").exists()
    assert (archive / "current.json").is_file()
    assert (archive / STACK_LOG).read_text() == "current stack"


def test_an_arm_that_lost_a_suite_keeps_its_worktree_and_says_which(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The stack had already paid for the cases, so its logs and its database are the only account
    of what the money bought."""
    result, root = _archived_arm(tmp_path, monkeypatch, [_stack_record(0, ("closing_message",))])

    assert result.gaps == (
        "ablate-knockout-0: no report for response_register",
        "ablate-knockout-1: no record",
    )
    assert result.kept_worktree == root
    assert (root / RUNS_DIR / "0.json").is_file()
    assert (tmp_path / "out" / "runs" / "knockout" / STACK_LOG).is_file()


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
