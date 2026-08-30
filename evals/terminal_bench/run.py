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

from evals.terminal_bench.models import UpstreamMetadata
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream

CUSTOM_AGENT = "evals.terminal_bench.agent:UfoAgent"
CLIENT = "bin/x86_64/ufo"
BENCH_ENVIRONMENT = (
    "UFO_BENCH_CLIENT",
    "UFO_BENCH_TOKEN",
    "UFO_BENCH_WORKSPACE_URL",
    "UFO_BENCH_MODEL",
)
REWARD_FILE = "reward.txt"
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


def select_cases(upstream: UpstreamMetadata, requested: tuple[str, ...]) -> tuple[str, ...]:
    """Resolve requested names into pinned manifest order; empty selects all."""
    if not requested:
        return upstream.tasks
    duplicates = sorted({name for name in requested if requested.count(name) > 1})
    if duplicates:
        raise ValueError(f"duplicate selected cases: {', '.join(duplicates)}")
    unknown = sorted(set(requested) - set(upstream.tasks))
    if unknown:
        raise ValueError(f"unknown selected cases: {', '.join(unknown)}")
    return tuple(task for task in upstream.tasks if task in set(requested))


def harbor_command(
    dataset: str,
    digest: str,
    cases: tuple[str, ...],
    jobs_dir: Path,
    job_name: str,
    concurrency: int,
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
        "--dataset",
        f"{dataset}@{digest}",
        *(
            part
            for case in cases
            for part in ("--include-task-name", f"{dataset.partition('/')[0]}/{case}")
        ),
        "--agent",
        CUSTOM_AGENT,
        "--env",
        backend.environment,
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


def harbor_environment(
    base: Mapping[str, str], credentials: BenchCredentials, model: str | None = None
) -> dict[str, str]:
    """Pass benchmark credentials to the custom-agent process only."""
    environment = {name: value for name, value in base.items() if name not in BENCH_ENVIRONMENT}
    environment.update(
        {
            "UFO_BENCH_CLIENT": str(credentials.client),
            "UFO_BENCH_TOKEN": credentials.token,
            "UFO_BENCH_WORKSPACE_URL": credentials.workspace_url,
        }
    )
    if model is not None:
        environment["UFO_BENCH_MODEL"] = model
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
        reward = verifier / REWARD_FILE
        if not reward.is_file():
            raise FileNotFoundError(f"Harbor reward is missing: {reward}")
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
    model: str | None = None

    def run(self) -> int:
        """Run one concurrent Harbor job and return its exit status."""
        upstream = load_upstream(self.upstream_file)
        selected = select_cases(upstream, self.cases)
        self._validate_inputs()
        jobs_dir = self.root / "jobs"
        job_name = f"ufo-{datetime.now(UTC).strftime(JOB_STAMP)}-{uuid4().hex[:8]}"
        command = harbor_command(
            upstream.dataset,
            upstream.digest,
            selected,
            jobs_dir,
            job_name,
            self.concurrency,
            upstream.harbor_version,
            self.backend,
        )
        print(shlex.join(command))
        completed = subprocess.run(
            command,
            check=False,
            env=harbor_environment(os.environ, self.credentials, self.model),
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

    def _validate_inputs(self) -> None:
        if self.concurrency < 1:
            raise ValueError("Terminal-Bench concurrency must be at least 1")
        parsed = urlsplit(self.credentials.workspace_url)
        if parsed.scheme != "https" or not parsed.hostname:
            raise ValueError("Terminal-Bench remote runs require a public HTTPS serve URL")
        if not self.credentials.client.is_file():
            raise FileNotFoundError(
                f"Terminal-Bench client is missing: {self.credentials.client}. "
                "Run python -m evals.terminal_bench.setup"
            )
        if not os.access(self.credentials.client, os.X_OK):
            raise PermissionError(
                f"Terminal-Bench client is not executable: {self.credentials.client}"
            )
