import json
import os
import shlex
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from json import JSONDecodeError
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError

from evals.terminal_bench.models import SelectedTask, UpstreamMetadata
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream

CUSTOM_AGENT = "evals.terminal_bench.agent:UfoAgent"
TASK_MANIFEST = "task.toml"
CLIENT = "bin/x86_64/ufo"
BENCH_ENVIRONMENT = (
    "UFO_BENCH_CLIENT",
    "UFO_BENCH_TOKEN",
    "UFO_BENCH_WORKSPACE_URL",
)
REWARD_FILES = ("reward.txt", "reward.json")
JOB_STAMP = "%Y%m%dT%H%M%SZ"


@dataclass(frozen=True)
class HarborBackend:
    environment: str
    extra: str


DAYTONA_BACKEND = HarborBackend(environment="daytona", extra="daytona")


@dataclass(frozen=True, repr=False)
class BenchCredentials:
    client: Path
    token: str
    workspace_url: str

    def __repr__(self) -> str:
        return f"BenchCredentials(client={self.client}, token=<redacted>, url={self.workspace_url})"


class TrialException(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    exception_type: str


class TrialRecord(BaseModel):
    """The identity and recorded failure from Harbor's trial result."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    trial_name: str
    exception_info: TrialException | None = None


@dataclass(frozen=True)
class TrialOutputs:
    """Harbor's result, reward, and recorded failure for one trial."""

    result: Path
    reward: Path
    exception: str | None


def select_cases(
    upstream: UpstreamMetadata, requested: tuple[str, ...]
) -> tuple[SelectedTask, ...]:
    """Resolve requested names into pinned manifest order; empty selects all."""
    pinned = tuple(task.name for task in upstream.tasks)
    if not requested:
        return upstream.tasks
    duplicates = sorted({name for name in requested if requested.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate selected cases: {', '.join(duplicates)}")
    unknown = sorted(set(requested) - set(pinned))
    if unknown:
        raise ValueError(f"unknown selected cases: {', '.join(unknown)}")
    return tuple(task for task in upstream.tasks if task.name in set(requested))


def harbor_command(
    tasks_dir: Path,
    cases: tuple[SelectedTask, ...],
    jobs_dir: Path,
    job_name: str,
    concurrency: int,
    workspace_host: str,
    harbor_version: str,
    backend: HarborBackend = DAYTONA_BACKEND,
) -> tuple[str, ...]:
    """Build one pinned Harbor job for every selected case."""
    return (
        "uv",
        "run",
        "--with",
        f"harbor[{backend.extra}]=={harbor_version}",
        "harbor",
        "run",
        "--path",
        str(tasks_dir),
        *(part for case in cases for part in ("--include-task-name", case.name)),
        "--agent",
        CUSTOM_AGENT,
        "--env",
        backend.environment,
        "--allow-agent-host",
        workspace_host,
        "--jobs-dir",
        str(jobs_dir),
        "--job-name",
        job_name,
        "--n-concurrent",
        str(concurrency),
        "--max-retries",
        "0",
        "--yes",
    )


def harbor_environment(base: Mapping[str, str], credentials: BenchCredentials) -> dict[str, str]:
    """Pass benchmark credentials to the custom-agent process only."""
    environment = {name: value for name, value in base.items() if name not in BENCH_ENVIRONMENT}
    environment.update(
        {
            "UFO_BENCH_CLIENT": str(credentials.client),
            "UFO_BENCH_TOKEN": credentials.token,
            "UFO_BENCH_WORKSPACE_URL": credentials.workspace_url,
        }
    )
    return environment


def official_outputs(job_dir: Path) -> tuple[TrialOutputs, ...]:
    """Locate Harbor's trial results and rewards under a finished job."""
    job_result = job_dir / "result.json"
    if not job_result.is_file():
        raise FileNotFoundError(f"Harbor job result is missing: {job_result}")
    outputs = []
    for trial in sorted(path for path in job_dir.iterdir() if path.is_dir()):
        result = trial / "result.json"
        if not result.is_file():
            raise FileNotFoundError(f"Harbor trial result is missing: {result}")
        verifier = trial / "verifier"
        reward = next(
            (verifier / name for name in REWARD_FILES if (verifier / name).is_file()), None
        )
        if reward is None:
            raise FileNotFoundError(
                f"Harbor wrote no reward file under {verifier}: "
                f"expected one of {', '.join(REWARD_FILES)}"
            )
        record = TrialRecord.model_validate(json.loads(result.read_bytes()))
        outputs.append(
            TrialOutputs(
                result=result,
                reward=reward,
                exception=(
                    None if record.exception_info is None else record.exception_info.exception_type
                ),
            )
        )
    if not outputs:
        raise FileNotFoundError(f"Harbor recorded no trial under {job_dir}")
    return tuple(outputs)


@dataclass(frozen=True)
class TerminalBenchRun:
    """Run the pinned Terminal-Bench cases remotely and retain Harbor's official output."""

    root: Path
    cases: tuple[str, ...]
    concurrency: int
    credentials: BenchCredentials
    backend: HarborBackend = DAYTONA_BACKEND
    upstream_file: Path = UPSTREAM_FILE

    def run(self) -> int:
        """Run one concurrent Harbor job and return its exit status."""
        upstream = load_upstream(self.upstream_file)
        selected = select_cases(upstream, self.cases)
        self._validate_inputs(selected)
        workspace_host = urlsplit(self.credentials.workspace_url).hostname
        if workspace_host is None:
            raise ValueError("Terminal-Bench workspace URL has no host")
        jobs_dir = self.root / "jobs"
        job_name = f"ufo-{datetime.now(UTC).strftime(JOB_STAMP)}-{uuid4().hex[:8]}"
        command = harbor_command(
            self.root / "tasks",
            selected,
            jobs_dir,
            job_name,
            self.concurrency,
            workspace_host,
            upstream.harbor_version,
            self.backend,
        )
        print(shlex.join(command))
        completed = subprocess.run(
            command, check=False, env=harbor_environment(os.environ, self.credentials)
        )
        job_dir = jobs_dir / job_name
        if completed.returncode:
            print(f"Harbor exited {completed.returncode}; retained job {job_dir}")
            return completed.returncode
        try:
            outputs = official_outputs(job_dir)
        except (OSError, JSONDecodeError, UnicodeDecodeError, ValidationError) as error:
            print(f"Harbor output is unreadable: {error}; retained job {job_dir}")
            return 1
        for trial in outputs:
            print(f"{trial.result} {trial.reward}")
        failures = tuple(trial.exception for trial in outputs if trial.exception is not None)
        if failures:
            print(f"Harbor recorded {', '.join(failures)}; retained job {job_dir}")
            return 1
        return 0

    def _validate_inputs(self, selected: tuple[SelectedTask, ...]) -> None:
        if self.concurrency < 1:
            raise ValueError("Terminal-Bench concurrency must be at least 1")
        parsed = urlsplit(self.credentials.workspace_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Terminal-Bench remote runs require a public HTTPS serve URL")
        for task in selected:
            manifest = self.root / "tasks" / task.name / TASK_MANIFEST
            if not manifest.is_file():
                raise FileNotFoundError(
                    f"Terminal-Bench task {task.name} is missing: {manifest}. "
                    "Run python -m evals.terminal_bench.setup"
                )
        if not self.credentials.client.is_file():
            raise FileNotFoundError(
                f"Terminal-Bench client is missing: {self.credentials.client}. "
                "Run python -m evals.terminal_bench.setup"
            )
        if not os.access(self.credentials.client, os.X_OK):
            raise PermissionError(
                f"Terminal-Bench client is not executable: {self.credentials.client}"
            )
