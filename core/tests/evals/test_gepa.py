import asyncio
import os
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from random import Random

import pytest

import evals.stack as eval_stack
from evals import gepa
from evals.gepa import (
    EGRESS_BINARY,
    STATE_FILE,
    Arm,
    CandidateRecord,
    CaseOutcome,
    Gepa,
    OptimizeSpec,
    Proposal,
    RecordEvidence,
    Review,
    Rollout,
    StackRollout,
    accept_objection,
    arms_experiment,
    collect_outcomes,
    frontier,
    latest,
    load_optimization,
    load_state,
    main,
    module_slug,
    planned_instances,
    render_report,
    scored_cases,
    select_parent,
    ship_objection,
    split_instances,
)

MODULE = "prompts/register.md"
OTHER_MODULE = "skills/digest/SKILL.md"
BASE_TEXT = "Answer the question.\n"
MARKER = "Answer with one line."
TEMPLATE = 'template = { pack = { name = "assistant_eval" } }'


@dataclass
class FakeRollout:
    """Stands in for the stack: it scores an arm by how many of its modules carry the marker, and
    records every batch it was asked for so the loop's own decisions can be read back. The `breaks`
    case scores the other way round, so an arm that gains everywhere else still regresses one
    neighbour. No provider is reached and nothing is spent."""

    cost_usd: float = 0.0
    breaks: str = ""
    batches: list[tuple[tuple[str, ...], tuple[str, ...]]] = field(default_factory=list)

    async def run(self, arms: tuple[Arm, ...], instances: tuple[str, ...]) -> dict[str, Rollout]:
        self.batches.append((tuple(arm.label for arm in arms), instances))
        measured = {}
        for arm in arms:
            marks = sum(1 for text in arm.texts.values() if MARKER in text)
            outcomes = {
                name: CaseOutcome(
                    (0 if marks else 2) if name == self.breaks else min(marks, 2),
                    2,
                    () if marks else ("sample 0 failed", "member message: digest it"),
                )
                for name in instances or ("capital", "arithmetic", "three-bullets")
            }
            measured[arm.label] = Rollout(outcomes, self.cost_usd)
        return measured


@dataclass
class FakeReflector:
    revision: str = BASE_TEXT + MARKER + "\n"
    seen: list[tuple[str, str, tuple[str, ...]]] = field(default_factory=list)

    async def propose(
        self, module: str, text: str, outcomes: Mapping[str, CaseOutcome]
    ) -> Proposal:
        self.seen.append(
            (module, text, tuple(note for outcome in outcomes.values() for note in outcome.notes))
        )
        return Proposal(self.revision, 0.0)


@dataclass
class FakeClaims:
    objection: str = ""
    reviewed: list[str] = field(default_factory=list)

    async def review(self, module: str, base_text: str, text: str) -> Review:
        self.reviewed.append(module)
        return Review(self.objection, 0.0)


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    for module in (MODULE, OTHER_MODULE):
        (repo / Path(module).parent).mkdir(parents=True, exist_ok=True)
        (repo / module).write_text(BASE_TEXT)
    git = ("git", "-c", "user.email=t@evals.invalid", "-c", "user.name=t", "-C", str(repo))
    for args in (("init", "-q"), ("add", MODULE, OTHER_MODULE), ("commit", "-qm", "root")):
        subprocess.run((*git, *args), check=True, capture_output=True)
    binary = repo / EGRESS_BINARY
    binary.parent.mkdir(parents=True)
    binary.write_text("")
    base = subprocess.run(
        (*git, "rev-parse", "HEAD"), check=True, capture_output=True, text=True
    ).stdout.strip()
    return repo, base


def _spec(base: str, **overrides: object) -> OptimizeSpec:
    fields: dict[str, object] = {
        "name": "exp",
        "base": base,
        "suites": ("basics",),
        "modules": (MODULE,),
        "iterations": 1,
        "minibatch_size": 1,
        "budget_usd": 10.0,
        "est_usd_per_case": 0.0,
        "template": {"pack": {"name": "assistant_eval"}},
    }
    return OptimizeSpec.model_validate(fields | overrides)


def _rollout(tmp_path: Path) -> StackRollout:
    return StackRollout(tmp_path, _spec("base"), "base", tmp_path / "out")


def _gepa(spec: OptimizeSpec, repo: Path, base: str, out: Path, **deps: object) -> Gepa:
    return Gepa(
        repo=repo,
        spec=spec,
        base=base,
        out=out,
        rollout=deps.get("rollout") or FakeRollout(),
        reflector=deps.get("reflector") or FakeReflector(),
        claims=deps.get("claims") or FakeClaims(),
    )


def _run(tmp_path: Path, spec: OptimizeSpec, repo: Path, base: str, **deps: object) -> Path:
    out = tmp_path / "out"
    asyncio.run(_gepa(spec, repo, base, out, **deps).run())
    return out


def test_load_optimization_reads_the_loop_knobs(tmp_path: Path) -> None:
    path = tmp_path / "optimize.toml"
    path.write_text(
        'name = "register"\nbase = "origin/main"\nsuites = ["basics"]\n'
        f'modules = ["{MODULE}"]\niterations = 4\ncontrols = ["arithmetic"]\n'
        f"budget_usd = 300.0\n{TEMPLATE}\n"
    )
    spec = load_optimization(path, tmp_path)
    assert spec.modules == (MODULE,)
    assert spec.iterations == 4
    assert spec.controls == ("arithmetic",)


def test_a_module_the_reflector_cannot_read_whole_is_refused_at_config_load(
    tmp_path: Path,
) -> None:
    (tmp_path / Path(MODULE).parent).mkdir(parents=True)
    (tmp_path / MODULE).write_text("one line of standing instruction.\n" * 1_000)
    path = tmp_path / "optimize.toml"
    path.write_text(
        'name = "register"\nbase = "origin/main"\nsuites = ["basics"]\n'
        f'modules = ["{MODULE}"]\nbudget_usd = 300.0\n{TEMPLATE}\n'
    )
    with pytest.raises(SystemExit, match="lose the tail"):
        load_optimization(path, tmp_path)
    (tmp_path / MODULE).write_text(BASE_TEXT)
    assert load_optimization(path, tmp_path).modules == (MODULE,)


def test_modules_sharing_an_arm_name_are_rejected() -> None:
    with pytest.raises(ValueError, match="distinct"):
        OptimizeSpec(
            name="exp",
            base="origin/main",
            suites=("basics",),
            modules=("skills/pdf/SKILL.md", "other/skills/pdf/SKILL.md"),
            budget_usd=5.0,
            template={"pack": {"name": "assistant_eval"}},
        )


def test_a_control_case_is_not_also_a_target_case() -> None:
    with pytest.raises(ValueError, match="control case"):
        OptimizeSpec(
            name="exp",
            base="origin/main",
            suites=("basics",),
            cases=("capital",),
            controls=("capital",),
            modules=(MODULE,),
            budget_usd=5.0,
            template={"pack": {"name": "assistant_eval"}},
        )


def test_module_slug_names_the_file_and_the_directory_that_identifies_it() -> None:
    assert module_slug("extensions/documents/skills/office-xlsx/SKILL.md") == "office-xlsx-skill-md"


def test_an_unscorable_case_leaves_the_task_set_before_optimizing() -> None:
    spec = OptimizeSpec(
        name="exp",
        base="origin/main",
        suites=("basics",),
        unscorable=("arithmetic",),
        modules=(MODULE,),
        budget_usd=5.0,
        template={"pack": {"name": "assistant_eval"}},
    )
    assert planned_instances(spec) == ("capital", "three-bullets")


def test_split_instances_is_stable_per_seed_and_holds_both_sides_back() -> None:
    instances = ("a", "b", "c", "d")
    feedback, pareto = split_instances(instances, 0.5, 7)
    assert sorted((*feedback, *pareto)) == ["a", "b", "c", "d"]
    assert not set(feedback) & set(pareto)
    assert split_instances(instances, 0.5, 7) == (feedback, pareto)
    assert split_instances(instances, 0.9, 7)[0]


def _case(name: str, passed: bool, **evidence: object) -> dict:
    return {
        "name": name,
        "passed": passed,
        "reason": "joined grader phrasing",
        "evidence": evidence,
    }


def test_the_evidence_reader_uses_the_structured_record_not_the_reason_line() -> None:
    notes = RecordEvidence().notes(
        _case(
            "report-digest-attributed-findings",
            False,
            message="roll up last week's reports",
            attempts=[
                {
                    "passed": False,
                    "response": "Here is the digest.",
                    "calls": [
                        {
                            "name": "load_skill",
                            "input": {"name": "research-report"},
                            "result": "loaded",
                            "hasResult": True,
                            "isError": False,
                        }
                    ],
                    "toolErrors": ["write failed: bad path"],
                    "judge": [
                        {"criterion": "attributes each finding", "passed": False, "reason": "none"}
                    ],
                    "grader": {"skill": "research-report"},
                }
            ],
        )
    )
    assert "member message: roll up last week's reports" in notes
    assert "sample 0 answer: Here is the digest." in notes
    assert any(note.startswith("tool load_skill (ok)") for note in notes)
    assert "judge unmet 'attributes each finding': none" in notes
    assert any("grader evidence" in note for note in notes)
    assert all("joined grader phrasing" not in note for note in notes)


def test_collect_outcomes_scores_samples_and_prices_them() -> None:
    outcomes, cost = collect_outcomes(
        [
            {
                "reports": [
                    {
                        "cases": [
                            _case(
                                "capital",
                                False,
                                attempts=[
                                    {"passed": False, "costMicroUsd": 500_000},
                                    {"passed": True, "costMicroUsd": 500_000},
                                ],
                            ),
                            {
                                "name": "arithmetic",
                                "passed": False,
                                "excluded": True,
                                "reason": "infra-excluded (web unavailable): 503",
                                "evidence": {
                                    "attempts": [{"passed": False, "costMicroUsd": 250_000}]
                                },
                            },
                        ]
                    }
                ]
            }
        ],
        RecordEvidence(),
    )
    assert outcomes["capital"].passes == 1
    assert outcomes["capital"].samples == 2
    assert outcomes["arithmetic"].samples == 0
    assert outcomes["arithmetic"].score is None
    assert scored_cases(outcomes) == {"capital": 0.5}
    assert any("rig fault" in note for note in outcomes["arithmetic"].notes)
    assert cost == pytest.approx(1.25)


@pytest.mark.parametrize(
    "failure",
    (
        None,
        "missing-record",
        "wrong-repeat",
        "missing-case",
        "materialize",
        "stack",
        "archive",
        "cleanup",
        "cancel",
    ),
)
def test_stack_rollout_removes_only_after_archive_and_database_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str | None,
) -> None:
    events: list[str] = []

    def materialize(self: StackRollout, arm: Arm, instances: tuple[str, ...], root: Path) -> None:
        root.mkdir(parents=True)
        events.append("materialize")
        if failure == "materialize":
            raise RuntimeError("materialize failed")

    async def stack(self: StackRollout, root: Path) -> tuple[int, str]:
        events.append("stack")
        if failure == "stack":
            raise RuntimeError("stack failed")
        if failure == "cancel":
            raise asyncio.CancelledError
        return 1, "no report"

    def archive(self: StackRollout, label: str, root: Path, stack_output: str) -> list[dict]:
        events.append("archive")
        if failure == "archive":
            raise RuntimeError("archive failed")
        if failure == "missing-record":
            return []
        cases = ("capital", "arithmetic", "three-bullets")
        cases = cases[:-1] if failure == "missing-case" else cases
        label = "other-0" if failure == "wrong-repeat" else "candidate-0"
        return [
            {
                "label": label,
                "reports": [
                    {"cases": [_case(name, True, attempts=[{"passed": True}]) for name in cases]}
                ],
            }
        ]

    async def drop_databases(self: StackRollout, root: Path) -> None:
        events.append("cleanup")
        if failure == "cleanup":
            raise RuntimeError("cleanup failed")

    def remove_worktree(self: StackRollout, root: Path) -> None:
        events.append("remove")
        root.rmdir()

    monkeypatch.setattr(StackRollout, "_materialize", materialize)
    monkeypatch.setattr(StackRollout, "_stack", stack)
    monkeypatch.setattr(StackRollout, "_archive", archive)
    monkeypatch.setattr(StackRollout, "_drop_databases", drop_databases)
    monkeypatch.setattr(StackRollout, "_remove_worktree", remove_worktree)
    rollout = _rollout(tmp_path)

    if failure == "cancel":
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(rollout._arm(Arm("candidate", {}), (), asyncio.Semaphore(1)))
    else:
        result = asyncio.run(rollout._arm(Arm("candidate", {}), (), asyncio.Semaphore(1)))
        assert (result.error is None) == (failure is None)

    expected = ["materialize"]
    if failure != "materialize":
        expected.append("stack")
    if failure not in {"materialize", "stack", "cancel"}:
        expected.append("archive")
    if failure in {None, "cleanup"}:
        expected.append("cleanup")
    if failure is None:
        expected.append("remove")
    assert events == expected
    assert (tmp_path / ".local/gepa/exp/candidate").exists() == (failure is not None)


def test_stack_rollout_archives_the_stack_and_per_run_logs(tmp_path: Path) -> None:
    root = tmp_path / "root"
    records = root / gepa.RUNS_DIR
    records.mkdir(parents=True)
    (records / "record.json").write_text("{}")
    logs = root / gepa.STACK_RUNS_DIR / "stamp" / "candidate-0"
    logs.mkdir(parents=True)
    (logs / "serve.log").write_text("served")
    rollout = _rollout(tmp_path)

    rollout._archive("candidate", root, "stack output")

    archive = tmp_path / "out/runs/candidate"
    assert (archive / gepa.STACK_LOG).read_text() == "stack output"
    assert (archive / gepa.LOGS_DIR / "candidate-0/serve.log").read_text() == "served"


def test_stack_rollout_materializes_beside_retained_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    materialized: list[Path] = []

    def materialize(self: StackRollout, arm: Arm, instances: tuple[str, ...], root: Path) -> None:
        root.mkdir(parents=True)
        materialized.append(root)

    async def stack(self: StackRollout, root: Path) -> tuple[int, str]:
        return 0, ""

    def archive(self: StackRollout, label: str, root: Path, stack_output: str) -> list[dict]:
        return [
            {
                "label": "candidate-0",
                "reports": [{"cases": [_case("capital", True, attempts=[{"passed": True}])]}],
            }
        ]

    async def drop_databases(self: StackRollout, root: Path) -> None:
        return None

    monkeypatch.setattr(StackRollout, "_materialize", materialize)
    monkeypatch.setattr(StackRollout, "_stack", stack)
    monkeypatch.setattr(StackRollout, "_archive", archive)
    monkeypatch.setattr(StackRollout, "_drop_databases", drop_databases)
    monkeypatch.setattr(StackRollout, "_remove_worktree", lambda self, root: root.rmdir())
    rollout = _rollout(tmp_path)
    kept = tmp_path / ".local/gepa/exp/candidate"
    kept.mkdir(parents=True)
    marker = kept / "keep"
    marker.write_text("evidence")

    result = asyncio.run(rollout._arm(Arm("candidate", {}), ("capital",), asyncio.Semaphore(1)))

    assert result.error is None
    assert materialized == [tmp_path / ".local/gepa/exp/candidate.2"]
    assert marker.read_text() == "evidence"


async def test_stack_rollout_cancellation_reaps_its_real_process_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_create = asyncio.create_subprocess_exec
    spawned = asyncio.Event()
    process: asyncio.subprocess.Process | None = None
    sessions: list[bool] = []

    async def create(*argv: str, **kwargs: object) -> asyncio.subprocess.Process:
        nonlocal process
        sessions.append(bool(kwargs.get("start_new_session")))
        process = await real_create(
            sys.executable,
            "-c",
            "import signal,time; "
            "signal.signal(signal.SIGINT, lambda *_: None); "
            "signal.signal(signal.SIGTERM, lambda *_: None); "
            "time.sleep(60)",
            stdout=kwargs["stdout"],
            stderr=kwargs["stderr"],
            start_new_session=sessions[-1],
        )
        spawned.set()
        return process

    monkeypatch.setattr(gepa.asyncio, "create_subprocess_exec", create)
    monkeypatch.setattr(eval_stack, "STACK_CANCEL_SIGINT_WAIT_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(eval_stack, "STACK_CANCEL_SIGTERM_WAIT_SECONDS", 0.01, raising=False)
    rollout = _rollout(tmp_path)
    running = asyncio.create_task(rollout._stack(tmp_path))
    await spawned.wait()
    await asyncio.sleep(0.05)
    running.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await running
        assert process is not None and process.returncode is not None
        with pytest.raises(ProcessLookupError):
            os.kill(process.pid, 0)
        assert sessions == [True]
    finally:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()


def test_an_infra_tool_error_reads_as_a_rig_fault_not_a_capability_failure() -> None:
    notes = RecordEvidence().notes(
        _case("capital", False, attempts=[{"passed": False, "toolErrors": ["search: 429 limit"]}])
    )
    assert any("rig fault" in note for note in notes)


def _candidate(identifier: str, scores: dict[str, float]) -> CandidateRecord:
    return CandidateRecord(id=identifier, iteration=0, accepted=True, scores=scores)


def test_frontier_keeps_every_instance_leader_and_prunes_the_dominated() -> None:
    leads = frontier(
        (
            _candidate("base", {"a": 1.0, "b": 0.0, "c": 0.0}),
            _candidate("c1", {"a": 0.0, "b": 1.0, "c": 1.0}),
            _candidate("c2", {"a": 0.0, "b": 1.0, "c": 0.0}),
        )
    )
    assert leads == {"base": 1, "c1": 2}


def test_select_parent_weights_a_candidate_by_the_instances_it_leads() -> None:
    pool = (
        _candidate("base", {"a": 1.0, "b": 0.0, "c": 0.0}),
        _candidate("c1", {"a": 0.0, "b": 1.0, "c": 1.0}),
        _candidate("c2", {"a": 0.0, "b": 1.0, "c": 0.0}),
    )
    rng = Random(0)
    picked = [select_parent(pool, rng).id for _ in range(300)]
    assert "c2" not in picked
    assert picked.count("c1") > picked.count("base")


def test_the_accept_test_refuses_a_target_gain_that_costs_a_control() -> None:
    variant = {"target": CaseOutcome(1, 1, ()), "guard": CaseOutcome(0, 1, ())}
    control = {"target": CaseOutcome(0, 1, ()), "guard": CaseOutcome(1, 1, ())}
    assert accept_objection(variant, control, ("target",), ("guard",)) == (
        "control regression: guard"
    )
    kept = {"target": CaseOutcome(1, 1, ()), "guard": CaseOutcome(1, 1, ())}
    assert accept_objection(kept, control, ("target",), ("guard",)) == ""
    assert "minibatch gate" in accept_objection(control, control, ("target",), ("guard",))


def test_the_accept_test_reads_only_the_cases_both_arms_scored() -> None:
    control = {"target": CaseOutcome(0, 1, ()), "guard": CaseOutcome(1, 1, ())}
    excluded_guard = {"target": CaseOutcome(1, 1, ()), "guard": CaseOutcome(0, 0, ())}
    assert accept_objection(excluded_guard, control, ("target",), ("guard",)) == ""
    excluded_target = {"target": CaseOutcome(0, 0, ()), "guard": CaseOutcome(1, 1, ())}
    assert accept_objection(excluded_target, control, ("target",), ("guard",)) == (
        "minibatch gate: no target case scored on both arms"
    )


def test_the_ship_gate_names_every_case_below_the_base_in_the_same_run() -> None:
    variant = {"a": CaseOutcome(1, 1, ()), "b": CaseOutcome(0, 1, ())}
    control = {"a": CaseOutcome(1, 1, ()), "b": CaseOutcome(1, 1, ())}
    assert ship_objection(variant, control) == "full-suite gate: b regressed"
    assert ship_objection({"a": CaseOutcome(1, 1, ()), "b": CaseOutcome(0, 0, ())}, control) == ""


def test_the_ship_gate_refuses_an_arm_that_shares_no_scored_case_with_the_base() -> None:
    control = {"a": CaseOutcome(1, 1, ()), "b": CaseOutcome(1, 1, ())}
    excluded = {"a": CaseOutcome(0, 0, ()), "b": CaseOutcome(0, 0, ())}
    assert ship_objection(excluded, control) == (
        "full-suite gate: no case scored on both the arm and the base"
    )


def test_a_win_is_paired_gated_and_reaches_the_ablation_as_an_arm(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    rollout = FakeRollout()
    reflector = FakeReflector()
    claims = FakeClaims()
    out = _run(
        tmp_path,
        _spec(base, controls=("arithmetic",)),
        repo,
        base,
        rollout=rollout,
        reflector=reflector,
        claims=claims,
    )
    records = load_state(out / STATE_FILE)
    selected = next(record for record in records if record.id == "c1")
    assert selected.accepted
    assert selected.parent == "base"
    assert selected.module == MODULE
    assert list(selected.texts) == [MODULE]
    assert any("member message" in note for note in selected.feedback)
    assert reflector.seen[0][0] == MODULE
    shipped = next(record for record in records if record.stage == "ship")
    assert shipped.shipped
    assert shipped.control_scores
    assert claims.reviewed == [MODULE]
    accept_batch = next(
        instances
        for labels, instances in rollout.batches
        if any("accept" in label for label in labels)
    )
    assert accept_batch.count("arithmetic") == 1
    assert all(
        len(labels) >= 2
        for labels, _ in rollout.batches
        if any("accept" in label for label in labels)
    )
    assert (out / "arms" / f"prompts-register-md.c1{Path(MODULE).suffix}").read_text() == (
        reflector.revision
    )
    experiment = (out / "experiment.toml").read_text()
    assert 'name = "gepa-c1"' in experiment
    assert f'"{MODULE}" = "arms/prompts-register-md.c1.md"' in experiment
    assert (repo / MODULE).read_text() == BASE_TEXT


def test_a_sampled_control_is_once_in_the_batch_and_seeded_resume(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    spec = _spec(
        base,
        controls=("arithmetic",),
        modules=(MODULE, OTHER_MODULE),
        iterations=2,
    )
    uninterrupted = FakeRollout()
    _run(tmp_path / "uninterrupted", spec, repo, base, rollout=uninterrupted)
    resume_root = tmp_path / "resumed"
    _run(
        resume_root,
        spec.model_copy(update={"iterations": 1}),
        repo,
        base,
        rollout=FakeRollout(),
    )
    resumed = FakeRollout()
    asyncio.run(_gepa(spec, repo, base, resume_root / "out", rollout=resumed).run())

    uninterrupted_batches = [
        instances
        for labels, instances in uninterrupted.batches
        if any("accept" in label for label in labels)
    ]
    resumed_batches = [
        instances
        for labels, instances in resumed.batches
        if any("accept" in label for label in labels)
    ]
    assert len(uninterrupted_batches) == 2
    assert all(batch.count("arithmetic") == 1 for batch in uninterrupted_batches)
    assert resumed_batches == uninterrupted_batches[1:]

    batch = uninterrupted_batches[0]
    record = {
        "label": "accept-0",
        "reports": [
            {"cases": [{"name": name} for name in dict.fromkeys(batch)]},
        ],
    }
    assert StackRollout(repo, spec, base, tmp_path / "records")._records_complete(
        "accept", batch, [record]
    )


@pytest.mark.parametrize(("est_usd_per_case", "cases"), [(0.9, 13), (1.1, 3), (0.4, 3)])
def test_the_handed_over_budget_covers_the_estimate_the_ablation_recomputes(
    est_usd_per_case: float, cases: int
) -> None:
    spec = _spec("origin/main", est_usd_per_case=est_usd_per_case)
    shipped = CandidateRecord(
        id="c1", iteration=0, accepted=True, shipped=True, texts={MODULE: BASE_TEXT}
    )
    instances = tuple(f"case-{index}" for index in range(cases))
    experiment = arms_experiment(spec, (shipped,), instances)
    budget = experiment["budget_usd"]
    arms = experiment["arm"]
    assert isinstance(budget, float) and isinstance(arms, list)
    runs = (len(arms) + 1) * spec.repeats
    assert budget >= runs * cases * est_usd_per_case
    assert budget == round(budget, 2)


def test_the_full_suite_gate_refuses_an_arm_that_breaks_a_neighbour(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(tmp_path, _spec(base), repo, base, rollout=FakeRollout(breaks="three-bullets"))
    shipped = [record for record in load_state(out / STATE_FILE) if record.stage == "ship"]
    assert shipped and not any(record.shipped for record in shipped)
    assert "full-suite gate" in shipped[0].note
    assert not list((out / "arms").glob("*.md"))
    assert not (out / "experiment.toml").exists()


def test_the_claims_gate_refuses_an_arm_that_invents_a_product_claim(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(
        tmp_path,
        _spec(base),
        repo,
        base,
        claims=FakeClaims(objection="promises a refund the product does not offer"),
    )
    shipped = [record for record in load_state(out / STATE_FILE) if record.stage == "ship"]
    assert not any(record.shipped for record in shipped)
    assert "claims gate" in shipped[0].note


def test_a_proposal_that_does_not_beat_its_parent_is_recorded_and_dropped(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(
        tmp_path,
        _spec(base),
        repo,
        base,
        reflector=FakeReflector(revision="Answer the question, briefly.\n"),
    )
    records = load_state(out / STATE_FILE)
    proposal = next(record for record in records if record.id == "c1")
    assert not proposal.accepted
    assert proposal.scores == {}
    assert "minibatch gate" in proposal.note
    assert proposal.parent_minibatch


def test_a_reflection_that_changes_nothing_is_recorded_without_a_paired_run(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    rollout = FakeRollout()
    out = _run(
        tmp_path,
        _spec(base),
        repo,
        base,
        rollout=rollout,
        reflector=FakeReflector(revision=BASE_TEXT),
    )
    records = load_state(out / STATE_FILE)
    assert next(record for record in records if record.id == "c1").note == (
        "reflection changed nothing"
    )
    assert all(len(labels) == 1 for labels, _ in rollout.batches)


def test_the_loop_stops_when_the_recorded_spend_reaches_the_budget(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(
        tmp_path,
        _spec(base, iterations=3, budget_usd=2.0),
        repo,
        base,
        rollout=FakeRollout(cost_usd=1.0),
    )
    records = load_state(out / STATE_FILE)
    assert sorted({record.iteration for record in records if record.stage == "select"}) == [0, 1]
    assert sum(record.cost_usd for record in latest(records)) >= 2.0


def test_preflight_refuses_an_estimate_over_the_budget(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    with pytest.raises(SystemExit, match="exceeds budget"):
        _run(tmp_path, _spec(base, est_usd_per_case=1.20, budget_usd=1.0), repo, base)


def test_a_resumed_run_keeps_the_recorded_gates_and_the_base_score(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(tmp_path, _spec(base), repo, base)
    resumed = FakeRollout()
    asyncio.run(_gepa(_spec(base, iterations=2), repo, base, out, rollout=resumed).run())
    records = latest(load_state(out / STATE_FILE))
    assert [record.id for record in records] == ["base", "c1", "c2"]
    assert all("base-select0" not in label for labels, _ in resumed.batches for label in labels)


def test_an_interrupted_ship_loop_gates_the_arms_it_never_reached(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    spec = _spec(base, modules=(MODULE, OTHER_MODULE), iterations=2)

    @dataclass
    class DyingClaims:
        calls: int = 0

        async def review(self, module: str, base_text: str, text: str) -> Review:
            self.calls += 1
            if self.calls > 1:
                raise RuntimeError("interrupted")
            return Review("", 0.0)

    out = tmp_path / "out"
    with pytest.raises(RuntimeError, match="interrupted"):
        asyncio.run(_gepa(spec, repo, base, out, claims=DyingClaims()).run())
    assert [record.id for record in load_state(out / STATE_FILE) if record.stage == "ship"] == [
        "c1"
    ]
    resumed = FakeRollout()
    asyncio.run(_gepa(spec, repo, base, out, rollout=resumed).run())
    records = load_state(out / STATE_FILE)
    assert [record.id for record in records if record.stage == "ship"] == ["c1", "c2", "merged"]
    assert all("c1-ship" not in label for labels, _ in resumed.batches for label in labels)
    assert {record.id for record in latest(records) if record.shipped} == {"c1", "c2", "merged"}
    assert (out / "arms" / f"digest-skill-md.c2{Path(MODULE).suffix}").is_file()


def test_the_report_counts_a_gated_candidate_s_spend_once_not_once_per_stage(
    tmp_path: Path,
) -> None:
    selected = CandidateRecord(
        id="c1", iteration=1, accepted=True, module=MODULE, scores={"capital": 1.0}, cost_usd=3.0
    )
    gated = selected.model_copy(update={"stage": "ship", "shipped": True, "cost_usd": 4.0})
    report = render_report(_spec("origin/main"), (selected, gated), tmp_path / "experiment.toml")
    assert "Recorded spend $4.00 of budget $10.00" in report


def test_disjoint_winners_merge_into_one_arm(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)
    out = _run(tmp_path, _spec(base, modules=(MODULE, OTHER_MODULE), iterations=2), repo, base)
    merged = next(record for record in load_state(out / STATE_FILE) if record.id == "merged")
    assert sorted(merged.texts) == sorted((MODULE, OTHER_MODULE))
    assert set(merged.merged_from) == {"c1", "c2"}
    assert (out / "arms" / "digest-skill-md.merged.md").is_file()


def test_a_rollout_that_wrote_no_record_stops_the_run(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path)

    @dataclass
    class DeadRollout:
        async def run(
            self, arms: tuple[Arm, ...], instances: tuple[str, ...]
        ) -> dict[str, Rollout]:
            return {arm.label: Rollout({}, 0.0, error="stack exited 1, no record") for arm in arms}

    with pytest.raises(SystemExit, match="no record"):
        _run(tmp_path, _spec(base), repo, base, rollout=DeadRollout())


def test_dry_run_prints_the_plan_and_reaches_no_rollout(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "optimize.toml"
    path.write_text(
        'name = "register"\nbase = "origin/main"\nsuites = ["basics"]\n'
        f'modules = ["{MODULE}"]\ncontrols = ["arithmetic"]\nbudget_usd = 3000.0\n{TEMPLATE}\n'
    )
    with pytest.raises(SystemExit) as exit_info:
        main([str(path), "--dry-run"])
    assert exit_info.value.code == 0
    printed = capsys.readouterr().out
    assert "D_pareto" in printed
    assert printed.count("arithmetic") == 1
    assert "full-suite ship gate" in printed
    assert "estimated $" in printed
