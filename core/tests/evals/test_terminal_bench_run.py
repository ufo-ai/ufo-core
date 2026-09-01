from pathlib import Path

from evals.terminal_bench.run import (
    CUSTOM_AGENT,
    DAYTONA_BACKEND,
    HarborBackend,
    harbor_command,
    select_cases,
)
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream

DATASET = "terminal-bench/terminal-bench-2-1"
DATASET_DIGEST = "sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a"
CASES = ("openssl-selfsigned-cert", "regex-log", "cancel-async-tasks")


def test_harbor_command_runs_one_remote_job_at_bounded_concurrency(tmp_path: Path) -> None:
    selected = select_cases(load_upstream(UPSTREAM_FILE), ())
    command = harbor_command(
        DATASET,
        DATASET_DIGEST,
        selected,
        tmp_path / "jobs",
        "ufo-full",
        24,
        "0.21.0",
    )

    assert command[:6] == (
        "uv",
        "run",
        "--with",
        "harbor[daytona]==0.21.0",
        "harbor",
        "run",
    )
    assert CUSTOM_AGENT == "evals.terminal_bench.agent:UfoAgent"
    assert DAYTONA_BACKEND == HarborBackend(environment="daytona", extra="daytona")
    assert command[command.index("--env") + 1] == "daytona"
    assert command[command.index("--n-concurrent") + 1] == "24"
    assert "--allow-agent-host" not in command
    assert command[command.index("--dataset") + 1] == f"{DATASET}@{DATASET_DIGEST}"
    assert command.count("--include-task-name") == 89
    assert all(f"terminal-bench/{case}" in command for case in CASES)
    assert command[-3:] == ("--max-retries", "0", "--yes")
