"""Grade captured WANDR submissions offline with each task's vendored verifier. Every case stages
its captured bytes into a fresh workspace, `tests/test.sh` runs the uv-locked evaluator against
it, and `reward.json` carries the authoritative soft/hard F1 the report rolls up per difficulty
tier. A case with no captured submission grades as zero through the verifier's own empty-workspace
path; a verifier fault raises instead of scoring."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from pydantic import Field

from evals.wandr.models import (
    DIGEST_PATTERN,
    TIER_BY_DIFFICULTY,
    BoundaryModel,
    Difficulty,
    SelectionCase,
    Snapshot,
    Subset,
)
from evals.wandr.runner import SUBMISSIONS_ROOT, SUBSETS
from evals.wandr.snapshot import TASKS_ROOT, load_snapshot

DEFAULT_VERIFIER_TIMEOUT_SECONDS = 28_800.0
GRADES_ROOT = Path(".local/wandr/grades")
VENV_ROOT = Path(".local/wandr/venv")
REQUIRED_ENV = ("OPENAI_API_KEY", "PERPLEXITY_API_KEY")
SOFT_F1_KEY = "soft_f1_full"
HARD_F1_KEY = "hard_f1_full"
COMPLETE_MARKER = ".complete"
METRIC_TEMPLATE = "soft_f1: {value:.4f}"


class WandrCaseGrade(BoundaryModel):
    name: str
    subset: Subset
    difficulty: Difficulty | None
    tier: int | None
    task_digest: str = Field(pattern=DIGEST_PATTERN)
    captured: bool
    soft_f1: float = Field(ge=0.0, le=1.0)
    hard_f1: float = Field(ge=0.0, le=1.0)
    rewards: dict[str, float]
    logs_dir: str


class TierRollup(BoundaryModel):
    tier: int
    cases: int
    mean_soft_f1: float
    mean_hard_f1: float


class WandrGradeReport(BoundaryModel):
    snapshot_digest: str = Field(pattern=DIGEST_PATTERN)
    cases: tuple[WandrCaseGrade, ...] = Field(min_length=1)
    tiers: tuple[TierRollup, ...]
    mean_soft_f1: float
    mean_hard_f1: float


@dataclass(frozen=True)
class VerifierRun:
    """Runs the vendored task-local verifier over captured submissions, one case at a time so the
    shared uv environment bootstraps once and judge rate limits stay bounded."""

    snapshot: Snapshot
    submissions_root: Path
    output_root: Path
    venv: Path
    timeout_seconds: float

    async def grade(self, cases: tuple[SelectionCase, ...]) -> WandrGradeReport:
        for name in REQUIRED_ENV:
            if not os.environ.get(name):
                raise RuntimeError(f"WANDR grading requires {name}")
        grades = [await self._grade_case(case) for case in cases]
        return WandrGradeReport(
            snapshot_digest=self.snapshot.manifest.digest,
            cases=tuple(grades),
            tiers=self._rollups(grades),
            mean_soft_f1=_mean(tuple(grade.soft_f1 for grade in grades)),
            mean_hard_f1=_mean(tuple(grade.hard_f1 for grade in grades)),
        )

    async def _grade_case(self, case: SelectionCase) -> WandrCaseGrade:
        case_root = self.output_root / case.name
        workspace = case_root / "workspace"
        logs = case_root / "verifier"
        captured = await asyncio.to_thread(self._stage, case, case_root, workspace)
        process = await asyncio.create_subprocess_exec(
            "bash",
            str(Path(self.snapshot.root) / TASKS_ROOT / case.name / "tests" / "test.sh"),
            env=self._environment(case, workspace, logs),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), self.timeout_seconds)
        except TimeoutError:
            process.kill()
            await process.communicate()
            raise RuntimeError(
                f"WANDR verifier timed out after {self.timeout_seconds:g}s on {case.name!r}"
            ) from None
        completed = await asyncio.to_thread((logs / COMPLETE_MARKER).exists)
        if process.returncode != 0 or not completed:
            failure = await asyncio.to_thread(self._failure, logs, stdout)
            raise RuntimeError(
                f"WANDR verifier failed on {case.name!r} (exit {process.returncode}): {failure}"
            )
        rewards = await asyncio.to_thread(self._rewards, case, logs)
        return WandrCaseGrade(
            name=case.name,
            subset=case.subset,
            difficulty=case.difficulty,
            tier=None if case.difficulty is None else TIER_BY_DIFFICULTY[case.difficulty],
            task_digest=case.digest,
            captured=captured,
            soft_f1=rewards[SOFT_F1_KEY],
            hard_f1=rewards[HARD_F1_KEY],
            rewards=rewards,
            logs_dir=str(logs),
        )

    def _stage(self, case: SelectionCase, case_root: Path, workspace: Path) -> bool:
        if case_root.exists():
            shutil.rmtree(case_root)
        workspace.mkdir(parents=True)
        submitted = self.submissions_root / case.name
        captured = all((submitted / name).is_file() for name in case.required_files)
        if captured:
            for name in case.required_files:
                shutil.copyfile(submitted / name, workspace / name)
        return captured

    def _environment(self, case: SelectionCase, workspace: Path, logs: Path) -> dict[str, str]:
        tests = Path(self.snapshot.root) / TASKS_ROOT / case.name / "tests"
        return {
            **os.environ,
            "PATH": f"{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "TESTS_DIR": str(tests),
            "WORKSPACE_DIR": str(workspace.resolve()),
            "LOGS_DIR": str(logs.resolve()),
            "UV_PROJECT_ENVIRONMENT": str(self.venv.resolve()),
        }

    def _failure(self, logs: Path, stdout: bytes) -> str:
        error = logs / "error.json"
        if error.is_file():
            return error.read_text().strip()[:500]
        return stdout.decode(errors="replace").strip()[-500:]

    def _rewards(self, case: SelectionCase, logs: Path) -> dict[str, float]:
        payload = json.loads((logs / "reward.json").read_bytes())
        rewards = {
            key: float(value)
            for key, value in payload.items()
            if isinstance(value, (int, float)) and not isinstance(value, bool)
        }
        missing = tuple(key for key in (SOFT_F1_KEY, HARD_F1_KEY) if key not in rewards)
        if missing:
            raise RuntimeError(f"WANDR reward file for {case.name!r} lacks {', '.join(missing)}")
        return rewards

    def _rollups(self, grades: list[WandrCaseGrade]) -> tuple[TierRollup, ...]:
        rollups = []
        for tier in sorted({grade.tier for grade in grades if grade.tier is not None}):
            members = tuple(grade for grade in grades if grade.tier == tier)
            rollups.append(
                TierRollup(
                    tier=tier,
                    cases=len(members),
                    mean_soft_f1=_mean(tuple(grade.soft_f1 for grade in members)),
                    mean_hard_f1=_mean(tuple(grade.hard_f1 for grade in members)),
                )
            )
        return tuple(rollups)


def _mean(values: tuple[float, ...]) -> float:
    return sum(values) / len(values)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m evals.wandr.grading")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--subset", choices=SUBSETS)
    parser.add_argument("--case", action="append", default=[], metavar="TASK")
    parser.add_argument("--submissions", type=Path, default=SUBMISSIONS_ROOT)
    parser.add_argument("--out", type=Path, default=GRADES_ROOT)
    parser.add_argument("--venv", type=Path, default=VENV_ROOT)
    parser.add_argument(
        "--verifier-timeout-seconds", type=float, default=DEFAULT_VERIFIER_TIMEOUT_SECONDS
    )
    parser.add_argument("--metric-stdout", action="store_true")
    args = parser.parse_args(argv)
    if args.subset is None and not args.case:
        parser.error("--subset or --case is required")
    snapshot = load_snapshot(args.snapshot)
    selection = snapshot.manifest.selection
    requested = frozenset(args.case)
    unknown = sorted(requested - {case.name for case in selection.cases})
    if unknown:
        parser.error(f"unknown WANDR cases: {', '.join(unknown)}")
    cases = tuple(
        case
        for case in selection.cases
        if (args.subset is None or case.subset == args.subset)
        and (not requested or case.name in requested)
    )
    if not cases:
        parser.error("no WANDR cases selected")
    report = asyncio.run(
        VerifierRun(
            snapshot=snapshot,
            submissions_root=args.submissions,
            output_root=args.out / "verifier-runs",
            venv=args.venv,
            timeout_seconds=args.verifier_timeout_seconds,
        ).grade(cases)
    )
    label = args.subset if args.subset is not None else "selected"
    record = args.out / f"{label}.json"
    record.parent.mkdir(parents=True, exist_ok=True)
    record.write_text(report.model_dump_json(indent=2) + "\n")
    for grade in report.cases:
        marker = "" if grade.captured else " (no submission)"
        print(
            f"{grade.name} [{grade.subset}/{grade.difficulty or 'smoke'}] "
            f"soft_f1={grade.soft_f1:.4f} hard_f1={grade.hard_f1:.4f}{marker}"
        )
    for tier in report.tiers:
        print(
            f"T{tier.tier}: {tier.cases} cases "
            f"soft_f1={tier.mean_soft_f1:.4f} hard_f1={tier.mean_hard_f1:.4f}"
        )
    print(f"grades {record.resolve()}")
    print(f"mean hard_f1: {report.mean_hard_f1:.4f}")
    if args.metric_stdout:
        print(METRIC_TEMPLATE.format(value=report.mean_soft_f1))


if __name__ == "__main__":
    main()
