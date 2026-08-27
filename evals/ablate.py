"""Run a text-ablation experiment: one eval arm per variant of the text the model reads.

`python -m evals.ablate experiment.toml` reads an experiment file naming a base revision and a
set of arms — each arm a map of repo paths to variant files — and measures every arm on the same
cases. Each arm is materialized as its own git worktree and venv, so an arm is exactly a git
state and its diff is reviewable; a control arm on the unmodified base always runs beside the
variants. Repeats become extra `[[run]]` blocks in the arm's `evals.stack` matrix, so each repeat
is an isolated stack with its own database and serve. Results compare sample-level pass counts
per case against the control, and the report calls a case moved only when the gap is wide enough
to survive the suite's own noise.

Each arm's records, its stacks' own logs and the orchestrator's whole view of the run are archived
under `runs/<arm>/` before the worktree goes. An arm that owed a record and never wrote one keeps
its worktree and says so in the report: the stack had already paid for those cases, and its logs
and database are the only account of what the money bought.

The experiment file:

    name = "customers-section-clauses"
    base = "origin/main"
    suites = ["onboarding_help"]
    cases = ["slack-install-pending", "promised-credits"]
    repeats = 1
    remote = true
    budget_usd = 60.0

    [template]
    database = { url = "postgresql+asyncpg://ufo:ufo@127.0.0.1:5541/ufo" }
    blob = { backend = "filesystem", root = "./blobs" }
    connect = { public_base_url = "http://evals.invalid" }
    pack = { name = "assistant_hosted" }

    [[arm]]
    name = "no-topic-list"
    [arm.files]
    "packs/assistant_hosted/ufo_pack_assistant_hosted.py" = "arms/no-topic-list.py"

A `memory_ingestion` snapshot path makes the ingestion suites measurable: it rides every matrix row
the orchestrator writes, so each arm's stack materializes the corpus itself and hands its eval child
that snapshot together with the readiness state its own materialization produced. Those suites are
named `memory_ingestion.<corpus>.<category>` and are built from the snapshot at run time, so the
budget preflight counts their cases from the snapshot instead of the registry.

Credentials come from the invoking environment — the orchestrator adds nothing and strips
nothing, so run it under the same minimal environment an `evals.stack` run takes. A remote
experiment selects and validates one native client before it creates an arm, then copies those
exact bytes into every worktree and puts that copy first on the arm's command path."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.harness.registry import narrowed_tasks
from evals.memory_ingestion.models import MANIFEST_FILE, load_snapshot
from evals.registry import TASKS
from ufo.sandbox.client_binary import CLIENT_BINARY_NAME
from ufo.schema.records import ReasoningEffort

CONTROL_ARM = "control"
INGESTION_PREFIX = "memory_ingestion"
ARM_LABEL_PREFIX = "ablate"
EGRESS_BINARY = Path("servers/egress/target/debug/ufo-egress")
REMOTE_CLIENT_BINARY = Path(".local/bin/ufo")
BUILT_TREES = (
    Path("extensions/web/ufo_ext_web/apps"),
    Path("extensions/sites/ufo_ext_sites/page/kit"),
)
RUNS_DIR = Path("eval-reports/runs")
EXPERIMENTS_DIR = Path("eval-reports/experiments")
WORKTREES_DIR = Path(".local/ablate")
STACK_RUNS_DIR = Path(".local/evals")
STACK_LOG = "stack.log"
LOGS_DIR = "logs"
TAIL_CHARS = 1500
SIGNAL_GAP = 2
SIGNAL_FLOOR = 3


class ArmReplacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    old: str = Field(min_length=1)
    new: str

    @model_validator(mode="after")
    def _changes_one_relative_repo_file(self) -> ArmReplacement:
        path = Path(self.path)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"replacement path must stay inside the repo: {self.path!r}")
        if self.old == self.new:
            raise ValueError("replacement old and new text are identical")
        return self


class ArmSpec(BaseModel):
    """One variant arm: variant files or exact replacements applied in its own worktree."""

    model_config = ConfigDict(extra="forbid")
    name: str
    files: dict[str, Path] = Field(default_factory=dict)
    replacements: tuple[ArmReplacement, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_fits_a_stack_label(cls, name: str) -> str:
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,20}", name):
            raise ValueError(f"arm name {name!r} must match [a-z0-9][a-z0-9_-]{{0,20}}")
        if name == CONTROL_ARM:
            raise ValueError(f"{CONTROL_ARM!r} is implicit — every experiment runs it")
        return name


class ExperimentSpec(BaseModel):
    """The experiment file: what to vary, what to measure it on, and what it may spend."""

    model_config = ConfigDict(extra="forbid")
    name: str
    base: str
    suites: tuple[str, ...]
    cases: tuple[str, ...] = ()
    agent: str = ""
    """The workspace agent every arm's cases run as, when the text under test is read by one agent
    rather than by the deploy: an app agent's home skill is measured from that agent's own prompt.
    Empty runs the default agent."""
    memory_ingestion: Path | None = None
    repeats: int = 1
    concurrency: int = 4
    max_stacks: int = 3
    remote: bool = False
    budget_usd: float
    est_usd_per_case: float = 1.20
    model: str | None = None
    reasoning: ReasoningEffort | None = None
    template: dict[str, dict[str, str]]
    arm: tuple[ArmSpec, ...]

    @field_validator("arm")
    @classmethod
    def _arm_names_are_unique(cls, arms: tuple[ArmSpec, ...]) -> tuple[ArmSpec, ...]:
        names = [arm.name for arm in arms]
        if len(set(names)) != len(names):
            raise ValueError(f"duplicate arm names: {sorted(names)}")
        return arms

    @model_validator(mode="after")
    def _ingestion_suites_name_their_snapshot(self) -> ExperimentSpec:
        named = [name for name in self.suites if name.startswith(f"{INGESTION_PREFIX}.")]
        if named and self.memory_ingestion is None:
            raise ValueError(
                f"suites {', '.join(named)} need memory_ingestion naming the snapshot they "
                "are built from"
            )
        return self


def ingestion_suites(snapshot_root: Path) -> dict[str, tuple[str, ...]]:
    """The ingestion suites the snapshot carries, each with its case ids. The runner groups the
    corpus into suites at run time, so they reach `TASKS` only inside a stack that materialized
    them and the orchestrator reads them from the snapshot instead."""
    grouped: dict[str, list[str]] = {}
    for case in load_snapshot(snapshot_root).cases:
        suite = f"{INGESTION_PREFIX}.{case.corpus}.{case.category}"
        grouped.setdefault(suite, []).append(case.id)
    return {suite: tuple(ids) for suite, ids in sorted(grouped.items())}


def load_experiment(path: Path) -> ExperimentSpec:
    spec = ExperimentSpec.model_validate(tomllib.loads(path.read_text()))
    snapshot = None
    if spec.memory_ingestion is not None:
        snapshot = (path.parent / spec.memory_ingestion).resolve()
        if not (snapshot / MANIFEST_FILE).is_file():
            raise SystemExit(f"memory_ingestion snapshot missing {MANIFEST_FILE}: {snapshot}")
    resolved = tuple(
        ArmSpec(
            name=arm.name,
            files={
                repo_path: (path.parent / variant).resolve()
                for repo_path, variant in arm.files.items()
            },
            replacements=arm.replacements,
        )
        for arm in spec.arm
    )
    for arm in resolved:
        for repo_path, variant in arm.files.items():
            if not variant.is_file():
                raise SystemExit(f"arm {arm.name!r}: variant for {repo_path} missing: {variant}")
    return spec.model_copy(update={"arm": resolved, "memory_ingestion": snapshot})


@dataclass(frozen=True)
class CaseCount:
    """Sample-level tallies for one case in one arm, summed across repeats."""

    passes: int
    samples: int
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class ArmResult:
    name: str
    counts: dict[str, CaseCount]
    cost_usd: float
    error: str | None = None
    gaps: tuple[str, ...] = ()
    kept_worktree: Path | None = None


def collect_counts(records: list[dict]) -> tuple[dict[str, CaseCount], float]:
    """Sample-level pass counts per case across every record of one arm. A case's `passed` is
    any-sample semantics; ablation resolution needs the samples themselves, so this reads the
    attempts. A runner that records no attempts (the arc, slack-silence, and skill-selection
    suites) still scored the case, so its case-level verdict counts as the one sample it is.
    Excluded cases are dropped and infra-excluded samples leave the case's denominator,
    the way the harness scores them — a transport-dead sample measures the harness, not the text.
    Their cost stays in the total: the run paid for them."""
    passes: dict[str, int] = {}
    samples: dict[str, int] = {}
    reasons: dict[str, list[str]] = {}
    cost = 0
    for record in records:
        for report in record["reports"]:
            for case in report["cases"]:
                if case.get("excluded"):
                    continue
                name = case["name"]
                evidence = case.get("evidence") or {}
                attempts = evidence.get("attempts") or []
                excluded = (evidence.get("excludedSamples") or 0) + (
                    evidence.get("excludedTrials") or 0
                )
                if attempts:
                    passes[name] = passes.get(name, 0) + sum(
                        1 for attempt in attempts if attempt.get("passed")
                    )
                    samples[name] = samples.get(name, 0) + len(attempts) - excluded
                    cost += sum(attempt.get("costMicroUsd") or 0 for attempt in attempts)
                else:
                    passes[name] = passes.get(name, 0) + (1 if case["passed"] else 0)
                    samples[name] = samples.get(name, 0) + 1
                if not case["passed"]:
                    reasons.setdefault(name, []).append((case.get("reason") or "")[:160])
    counts = {
        name: CaseCount(passes[name], samples[name], tuple(reasons.get(name, ())))
        for name in samples
    }
    return counts, cost / 1e6


def record_gaps(spec: ExperimentSpec, arm: str, records: list[dict]) -> tuple[str, ...]:
    """The missing or infra-excluded evidence in this arm's records. Naming each gap keeps it out
    of the arithmetic's blind spot and retains the stack worktree for diagnosis."""
    by_label = {record.get("label"): record for record in records}
    gaps = []
    for index in range(spec.repeats):
        label = f"{ARM_LABEL_PREFIX}-{arm}-{index}"
        record = by_label.get(label)
        if record is None:
            gaps.append(f"{label}: no record")
            continue
        landed = {report["name"] for report in record["reports"]}
        absent = [name for name in spec.suites if name not in landed]
        if absent:
            gaps.append(f"{label}: no report for {', '.join(absent)}")
        excluded = sorted(
            case["name"]
            for report in record["reports"]
            for case in report["cases"]
            if case.get("excluded")
        )
        if excluded:
            gaps.append(f"{label}: excluded case {', '.join(excluded)}")
        partial = sorted(
            (case["name"], count)
            for report in record["reports"]
            for case in report["cases"]
            if not case.get("excluded")
            if (
                count := ((case.get("evidence") or {}).get("excludedSamples") or 0)
                + ((case.get("evidence") or {}).get("excludedTrials") or 0)
            )
        )
        gaps.extend(
            f"{label}: {name} has {count} infra-excluded sample(s)" for name, count in partial
        )
    return tuple(gaps)


def verdict(control: CaseCount, arm: CaseCount) -> str:
    """One case's movement between the control and an arm, at sample level. A pass-count gap only
    means something when both sides ran the same number of samples, so an arm that lost a repeat
    or a case reports `needs-samples` rather than a movement it did not measure. `regressed` and
    `improved` require a gap of at least SIGNAL_GAP samples with both sides holding at least
    SIGNAL_FLOOR — below that a flip is inside the suite's own noise — except a total collapse
    or a total fix (one side zero, the other everything), which compares rates and so is a signal
    at any size."""
    gap = control.passes - arm.passes
    same_samples = control.samples == arm.samples
    comparable = same_samples and arm.samples >= SIGNAL_FLOOR
    collapse = arm.samples > 0 and arm.passes == 0 and control.passes == control.samples > 0
    fixed = control.samples > 0 and control.passes == 0 and arm.passes == arm.samples > 0
    if collapse or (comparable and gap >= SIGNAL_GAP):
        return "regressed"
    if fixed or (comparable and -gap >= SIGNAL_GAP):
        return "improved"
    if same_samples and gap == 0:
        return "flat"
    return "needs-samples"


def _diagnostics(result: ArmResult) -> list[str]:
    """What the arm owes and where the evidence for it is, in the report rather than only in the
    console the run scrolled past."""
    lines = [f"- record gap: {gap}" for gap in result.gaps]
    if result.kept_worktree is not None:
        lines.append(f"- worktree kept for diagnosis: {result.kept_worktree}")
    return lines


def render_report(spec: ExperimentSpec, results: tuple[ArmResult, ...]) -> str:
    control = next(result for result in results if result.name == CONTROL_ARM)
    lines = [f"# Ablation: {spec.name}", ""]
    lines.append(f"Base {spec.base}, {spec.repeats} repeat(s), suites {', '.join(spec.suites)}.")
    total = sum(result.cost_usd for result in results)
    lines.append(f"Total case cost ${total:.2f}.")
    for result in results:
        if result.error:
            lines += ["", f"## {result.name}: FAILED — {result.error}", *_diagnostics(result)]
            continue
        if result.name == CONTROL_ARM:
            lines += ["", f"## control (${result.cost_usd:.2f})", ""]
            lines += _diagnostics(result)
            for name in sorted(control.counts):
                count = control.counts[name]
                lines.append(f"- {name}: {count.passes}/{count.samples}")
            continue
        moved = []
        lines += ["", f"## {result.name} (${result.cost_usd:.2f})", ""]
        lines += _diagnostics(result)
        for name in sorted(control.counts):
            base = control.counts[name]
            arm = result.counts.get(name)
            if arm is None:
                lines.append(f"- {name}: MISSING (excluded or not recorded)")
                continue
            call = verdict(base, arm)
            if call in ("regressed", "improved"):
                moved.append((name, call))
            note = f" [{call}]" if call != "flat" else ""
            lines.append(
                f"- {name}: {arm.passes}/{arm.samples} vs control "
                f"{base.passes}/{base.samples}{note}"
            )
            if call == "regressed" and arm.reasons:
                lines.append(f"    reason: {arm.reasons[0]}")
        summary = "; ".join(f"{name} {call}" for name, call in moved) if moved else "no case moved"
        lines.append(f"  => {summary}")
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class Ablation:
    """The experiment run: materialize one worktree and venv per arm, drive one `evals.stack`
    per arm with `repeats` run blocks, collect sample counts, and write the comparison."""

    repo: Path
    spec: ExperimentSpec
    out: Path

    async def run(self) -> int:
        self._preflight()
        remote_client = await self._preflight_remote_client()
        base = self._resolve_base()
        arms = (ArmSpec.model_construct(name=CONTROL_ARM, files={}), *self.spec.arm)
        slots = asyncio.Semaphore(self.spec.max_stacks)
        results = await asyncio.gather(
            *(self._arm(arm, base, slots, remote_client) for arm in arms)
        )
        report = render_report(self.spec, tuple(results))
        self.out.mkdir(parents=True, exist_ok=True)
        (self.out / "report.md").write_text(report)
        (self.out / "experiment.json").write_text(
            json.dumps(self.spec.model_dump(mode="json"), indent=2) + "\n"
        )
        print(report)
        incomplete = [result.name for result in results if result.gaps]
        if incomplete:
            print(
                f"records incomplete, worktrees kept: {', '.join(incomplete)} — the report names "
                f"each gap, and each arm's logs are under {(self.out / 'runs').resolve()}",
                file=sys.stderr,
            )
        failed = [result.name for result in results if result.error]
        if failed:
            print(f"arms failed: {', '.join(failed)}", file=sys.stderr)
        return 1 if failed else 0

    def _preflight(self) -> None:
        binary = self.repo / EGRESS_BINARY
        if not binary.is_file():
            raise SystemExit(
                f"{EGRESS_BINARY} missing — build it: "
                "cargo build --manifest-path servers/egress/Cargo.toml"
            )
        cases = self._planned_cases()
        runs = (len(self.spec.arm) + 1) * self.spec.repeats
        cost = runs * cases * self.spec.est_usd_per_case
        if cost > self.spec.budget_usd:
            raise SystemExit(
                f"estimated ${cost:.0f} ({runs} runs x {cases} cases at "
                f"${self.spec.est_usd_per_case}/case) exceeds budget ${self.spec.budget_usd:.0f}"
            )
        print(
            f"estimated ${cost:.0f} ({runs} runs x {cases} cases), "
            f"budget ${self.spec.budget_usd:.0f}"
        )

    async def _preflight_remote_client(self) -> Path | None:
        if not self.spec.remote:
            return None
        found = shutil.which(CLIENT_BINARY_NAME)
        if found is None:
            raise SystemExit("remote ablation requires ufo on PATH")
        selected = Path(found)
        try:
            process = await asyncio.create_subprocess_exec(
                str(selected),
                "--help",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as error:
            raise SystemExit(f"remote ablation has no runnable ufo client: {error}") from error
        stdout, stderr = await process.communicate()
        if process.returncode or b"--remote" not in stdout or b"--json" not in stdout:
            detail = (stderr or stdout).decode("utf-8", "replace").strip()[-TAIL_CHARS:]
            raise SystemExit(
                f"remote ablation requires a ufo client with --remote and --json: "
                f"{selected}: {detail}"
            )
        return selected

    def _planned_cases(self) -> int:
        if self.spec.memory_ingestion is not None:
            return self._planned_ingestion_cases(self.spec.memory_ingestion)
        by_suite = {task.name: task for task in TASKS}
        unknown = [name for name in self.spec.suites if name not in by_suite]
        if unknown:
            raise SystemExit(f"unknown suites: {', '.join(unknown)}")
        tasks = tuple(by_suite[name] for name in self.spec.suites)
        if self.spec.cases:
            tasks = narrowed_tasks(tasks, self.spec.cases)
        return max(sum(len(task.cases) for task in tasks), 1)

    def _planned_ingestion_cases(self, snapshot: Path) -> int:
        suites = ingestion_suites(snapshot)
        unknown = [name for name in self.spec.suites if name not in suites]
        if unknown:
            raise SystemExit(f"unknown suites: {', '.join(unknown)}")
        selected = tuple(case for name in self.spec.suites for case in suites[name])
        if not self.spec.cases:
            return max(len(selected), 1)
        unknown_cases = [name for name in self.spec.cases if name not in selected]
        if unknown_cases:
            raise SystemExit(f"unknown eval case: {', '.join(unknown_cases)}")
        return len(self.spec.cases)

    def _resolve_base(self) -> str:
        return self._git("rev-parse", self.spec.base).strip()

    def _git(self, *args: str) -> str:
        return subprocess.run(
            ("git", "-C", str(self.repo), *args), check=True, capture_output=True, text=True
        ).stdout

    async def _arm(
        self,
        arm: ArmSpec,
        base: str,
        slots: asyncio.Semaphore,
        remote_client: Path | None,
    ) -> ArmResult:
        """One arm end to end. The worktree is removed only once the arm archived every record it
        owed: a stack pays before it records, so a lost record is the one moment the worktree's
        stack logs and databases are the only evidence of what the money bought, and the run that
        threw them away could not be diagnosed at all. A materialization that never reached a
        stack spent nothing and keeps no worktree."""
        async with slots:
            root = self.repo / WORKTREES_DIR / self.spec.name / arm.name
            archive = self.out / "runs" / arm.name
            keep = False
            print(f"[{arm.name}] materializing", flush=True)
            try:
                await asyncio.to_thread(self._materialize, arm, base, root, remote_client)
                keep = True
                print(f"[{arm.name}] stack running", flush=True)
                exit_code, output = await self._stack(arm, root)
                await asyncio.to_thread(self._archive, root, archive, output)
                records = [
                    json.loads(path.read_text())
                    for path in sorted((root / RUNS_DIR).glob("*.json"))
                ]
                gaps = record_gaps(self.spec, arm.name, records)
                keep = bool(gaps)
                kept = root if keep else None
                if not records:
                    tail = output[-TAIL_CHARS:]
                    return ArmResult(
                        arm.name,
                        {},
                        0.0,
                        error=f"stack exited {exit_code}, no record: {tail}",
                        gaps=gaps,
                        kept_worktree=kept,
                    )
                counts, cost = collect_counts(records)
                print(f"[{arm.name}] done (${cost:.2f})", flush=True)
                return ArmResult(arm.name, counts, cost, gaps=gaps, kept_worktree=kept)
            except Exception as error:
                print(f"[{arm.name}] FAILED: {type(error).__name__}: {error}", flush=True)
                return ArmResult(
                    arm.name,
                    {},
                    0.0,
                    error=f"{type(error).__name__}: {error}",
                    kept_worktree=root if keep else None,
                )
            finally:
                if keep:
                    print(f"[{arm.name}] worktree kept at {root}", flush=True)
                else:
                    await asyncio.to_thread(self._remove_worktree, root)

    def _archive(self, root: Path, archive: Path, output: str) -> None:
        """Everything the arm produced that has to outlive its worktree: its records, this
        orchestrator's whole view of the stack run, and each stack's own `seed`, `serve`, `egress`
        `eval`, and process-lifecycle logs. Written before any verdict is read, because a record
        that never landed is only explainable from the logs of the stack that owed it."""
        archive.mkdir(parents=True, exist_ok=True)
        (archive / STACK_LOG).write_text(output)
        for path in (root / RUNS_DIR).glob("*.json"):
            shutil.copy(path, archive / path.name)
        for path in sorted((root / STACK_RUNS_DIR).glob("*/*/*.log")):
            logs = archive / LOGS_DIR / path.parent.name
            logs.mkdir(parents=True, exist_ok=True)
            shutil.copy(path, logs / path.name)

    def _materialize(self, arm: ArmSpec, base: str, root: Path, remote_client: Path | None) -> None:
        if root.exists():
            self._remove_worktree(root)
        root.parent.mkdir(parents=True, exist_ok=True)
        self._git("worktree", "add", "--detach", str(root), base)
        self._carry_build_output(root)
        self._apply_arm(arm, root)
        self._sync(root)
        binary = root / EGRESS_BINARY
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(self.repo / EGRESS_BINARY, binary)
        binary.chmod(0o755)
        if remote_client is not None:
            carried_client = root / REMOTE_CLIENT_BINARY
            carried_client.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(remote_client, carried_client)
            carried_client.chmod(0o755)
        config = root / "ablate-template.toml"
        config.write_text(tomli_w.dumps(self.spec.template))
        (root / "ablate-matrix.toml").write_text(tomli_w.dumps(self.matrix(arm, config)))

    def _apply_arm(self, arm: ArmSpec, root: Path) -> None:
        for repo_path, variant in arm.files.items():
            target = root / repo_path
            if not target.is_file():
                raise RuntimeError(
                    f"arm {arm.name!r}: {repo_path} is not a file at the selected base"
                )
            shutil.copy(variant, target)
        for replacement in arm.replacements:
            target = root / replacement.path
            if not target.is_file():
                raise RuntimeError(
                    f"arm {arm.name!r}: {replacement.path} is not a file at the selected base"
                )
            source = target.read_text()
            matches = source.count(replacement.old)
            if matches != 1:
                raise RuntimeError(
                    f"arm {arm.name!r}: {replacement.path} replacement matched {matches} times"
                )
            target.write_text(source.replace(replacement.old, replacement.new))

    def _carry_build_output(self, root: Path) -> None:
        """Carry the ignored app pages and page SDK into an arm worktree when they exist."""
        for path in BUILT_TREES:
            source = self.repo / path
            if source.is_dir():
                shutil.copytree(source, root / path, dirs_exist_ok=True)

    def _sync(self, root: Path) -> None:
        """Only what the arm runs: `evals.stack`, its serve and its eval children, never the repo's
        lint, type and test tooling. A bare `uv sync` installs the dev group too, and a dev package
        that builds by downloading a release binary fails wherever that download is closed — which
        materializes no arm at all, for a group no arm needs.

        `uv sync` reports the failing package on stderr and `CalledProcessError` carries none of it,
        so the tail rides the raised error instead."""
        done = subprocess.run(
            ("uv", "sync", "--no-dev"), cwd=root, check=False, capture_output=True, text=True
        )
        if done.returncode != 0:
            raise RuntimeError(f"uv sync --no-dev failed in {root}: {done.stderr[-TAIL_CHARS:]}")

    def matrix(self, arm: ArmSpec, config: Path) -> dict[str, list[dict[str, object]]]:
        """One arm's `evals.stack` matrix: a run block per repeat, each an isolated stack.

        A memory-ingestion snapshot travels as a row key, never as an argument. The stack owns
        `--memory-ingestion` and `--memory-ingestion-state`, because it materializes the corpus
        itself and only then knows where the readiness state landed."""
        args = ["--concurrency", str(self.spec.concurrency)]
        if self.spec.remote:
            args.append("--remote")
        if self.spec.agent:
            args += ["--agent", self.spec.agent]
        args += ["--only", *self.spec.suites]
        if self.spec.cases:
            args += ["--case", *self.spec.cases]
        corpus: dict[str, object] = (
            {"memory_ingestion": str(self.spec.memory_ingestion)}
            if self.spec.memory_ingestion is not None
            else {}
        )
        agent: dict[str, object] = {
            key: value
            for key, value in (("model", self.spec.model), ("reasoning", self.spec.reasoning))
            if value is not None
        }
        return {
            "run": [
                {
                    "label": f"{ARM_LABEL_PREFIX}-{arm.name}-{index}",
                    "config": str(config),
                    "args": args,
                    **agent,
                    **corpus,
                }
                for index in range(self.spec.repeats)
            ]
        }

    async def _stack(self, arm: ArmSpec, root: Path) -> tuple[int, str]:
        environment = None
        if self.spec.remote:
            carried_client = (root / REMOTE_CLIENT_BINARY).resolve()
            environment = dict(os.environ)
            environment["PATH"] = os.pathsep.join(
                part for part in (str(carried_client.parent), environment.get("PATH")) if part
            )
        process = await asyncio.create_subprocess_exec(
            "uv",
            "run",
            "--no-dev",
            "--project",
            str(root),
            "python",
            "-m",
            "evals.stack",
            str(root / "ablate-matrix.toml"),
            cwd=root,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        output, _ = await process.communicate()
        return process.returncode or 0, output.decode(errors="replace")

    def _remove_worktree(self, root: Path) -> None:
        if not root.exists():
            return
        subprocess.run(
            ("git", "-C", str(self.repo), "worktree", "remove", "--force", str(root)),
            check=False,
            capture_output=True,
        )
        shutil.rmtree(root, ignore_errors=True)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.ablate")
    parser.add_argument("experiment", type=Path, help="experiment TOML")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="output directory (default eval-reports/experiments/<name>)",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    spec = load_experiment(args.experiment)
    repo = Path(
        subprocess.run(
            ("git", "rev-parse", "--show-toplevel"), check=True, capture_output=True, text=True
        ).stdout.strip()
    )
    out = args.out or (repo / EXPERIMENTS_DIR / spec.name)
    code = asyncio.run(Ablation(repo=repo, spec=spec, out=out).run())
    raise SystemExit(code)


if __name__ == "__main__":
    main()
