import json
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa

from evals.__main__ import _terminal_bench_credentials
from evals.__main__ import main as evals_main
from evals.terminal_bench.run import (
    CUSTOM_AGENT,
    REMOTE_ENVIRONMENT,
    BenchCredentials,
    TerminalBenchRun,
    harbor_command,
    harbor_environment,
    official_outputs,
    select_cases,
)
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream
from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV, verified_claims
from ufo.config import BlobConfig, Config, ConnectConfig, DatabaseConfig
from ufo.db import workspace_tx
from ufo.schema import tables

TOKEN = "ufo-bearer-4d0f2c8a1b6e"
CASES = ("interleaved-vigenere", "html-js-filter", "kv-live-surgery")


def test_default_and_named_cases_resolve_in_manifest_order() -> None:
    upstream = load_upstream(UPSTREAM_FILE)

    assert tuple(task.name for task in select_cases(upstream, ())) == CASES
    assert tuple(
        task.name for task in select_cases(upstream, ("kv-live-surgery", "html-js-filter"))
    ) == ("html-js-filter", "kv-live-surgery")


@pytest.mark.parametrize(
    ("requested", "message"),
    (
        (("html-js-filter", "html-js-filter"), "duplicate"),
        (("unknown",), "unknown"),
    ),
)
def test_invalid_case_selections_fail(requested: tuple[str, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        select_cases(load_upstream(UPSTREAM_FILE), requested)


def test_harbor_command_runs_one_remote_job_at_bounded_concurrency(tmp_path: Path) -> None:
    selected = select_cases(load_upstream(UPSTREAM_FILE), ())
    command = harbor_command(
        tmp_path / "tasks",
        selected,
        tmp_path / "jobs",
        "ufo-full",
        24,
        "eval.ufo.test",
        "0.21.0",
    )

    assert command[:6] == ("uv", "run", "--with", "harbor==0.21.0", "harbor", "run")
    assert CUSTOM_AGENT == "evals.terminal_bench.agent:UfoAgent"
    assert command[command.index("--env") + 1] == REMOTE_ENVIRONMENT == "modal"
    assert command[command.index("--n-concurrent") + 1] == "24"
    assert command[command.index("--allow-agent-host") + 1] == "eval.ufo.test"
    assert command.count("--include-task-name") == 3
    assert all(case in command for case in CASES)
    assert command[-3:] == ("--max-retries", "0", "--yes")


def test_token_reaches_harbor_only_through_the_environment(tmp_path: Path) -> None:
    credentials = BenchCredentials(
        client=tmp_path / "ufo",
        token=TOKEN,
        workspace_url="https://eval.ufo.test",
    )
    command = harbor_command(
        tmp_path / "tasks",
        select_cases(load_upstream(UPSTREAM_FILE), ("html-js-filter",)),
        tmp_path / "jobs",
        "ufo-one",
        1,
        "eval.ufo.test",
        "0.21.0",
    )
    environment = harbor_environment({"PATH": "/usr/bin"}, credentials)

    assert TOKEN not in " ".join(command)
    assert TOKEN not in repr(credentials)
    assert environment == {
        "PATH": "/usr/bin",
        "UFO_BENCH_CLIENT": str(tmp_path / "ufo"),
        "UFO_BENCH_TOKEN": TOKEN,
        "UFO_BENCH_WORKSPACE_URL": "https://eval.ufo.test",
    }


def test_full_run_keeps_harbor_grading_and_one_concurrent_job(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, credentials = _prepared_root(tmp_path)
    seen: list[tuple[tuple[str, ...], dict[str, str]]] = []

    def complete(command: tuple[str, ...], **kwargs) -> subprocess.CompletedProcess[str]:
        environment = kwargs["env"]
        seen.append((command, environment))
        job_dir = (
            Path(command[command.index("--jobs-dir") + 1])
            / command[command.index("--job-name") + 1]
        )
        _job(job_dir, CASES)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", complete)

    assert TerminalBenchRun(root, (), 12, credentials).run() == 0
    assert len(seen) == 1
    command, environment = seen[0]
    assert command[command.index("--n-concurrent") + 1] == "12"
    assert (
        tuple(
            command[index + 1]
            for index, value in enumerate(command)
            if value == "--include-task-name"
        )
        == CASES
    )
    assert environment["UFO_BENCH_TOKEN"] == TOKEN
    assert TOKEN not in " ".join(command)


@pytest.mark.parametrize(
    ("concurrency", "url", "message"),
    (
        (0, "https://eval.ufo.test", "at least 1"),
        (1, "http://eval.ufo.test", "public HTTPS"),
        (1, "https:///missing-host", "public HTTPS"),
    ),
)
def test_invalid_remote_configuration_fails_before_harbor(
    tmp_path: Path, concurrency: int, url: str, message: str
) -> None:
    root, credentials = _prepared_root(tmp_path, workspace_url=url)

    with pytest.raises(ValueError, match=message):
        TerminalBenchRun(root, (), concurrency, credentials).run()

    assert not (root / "jobs").exists()


def test_missing_task_or_client_fails_before_harbor(tmp_path: Path) -> None:
    root, credentials = _prepared_root(tmp_path)
    (root / "tasks" / "html-js-filter" / "task.toml").unlink()
    with pytest.raises(FileNotFoundError, match="html-js-filter"):
        TerminalBenchRun(root, (), 1, credentials).run()

    (root / "tasks" / "html-js-filter" / "task.toml").write_text("[task]\n")
    credentials.client.unlink()
    with pytest.raises(FileNotFoundError, match="client"):
        TerminalBenchRun(root, (), 1, credentials).run()


def test_official_outputs_require_results_and_rewards(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _job(job_dir, ("html-js-filter",))

    (output,) = official_outputs(job_dir)
    assert output.result.name == "result.json"
    assert output.reward.name == "reward.txt"
    assert output.exception is None

    output.reward.unlink()
    with pytest.raises(FileNotFoundError, match="reward"):
        official_outputs(job_dir)


def test_recorded_trial_exception_fails_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, credentials = _prepared_root(tmp_path)

    def complete(command: tuple[str, ...], **kwargs) -> subprocess.CompletedProcess[str]:
        job_dir = (
            Path(command[command.index("--jobs-dir") + 1])
            / command[command.index("--job-name") + 1]
        )
        _job(job_dir, ("html-js-filter",), exception="NonZeroAgentExitCodeError")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", complete)

    assert TerminalBenchRun(root, ("html-js-filter",), 1, credentials).run() == 1


def test_eval_runner_routes_remote_scale_into_one_harbor_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    root = tmp_path / "terminal-bench"
    credentials = BenchCredentials(root / "bin/x86_64/ufo", TOKEN, "https://eval.ufo.test")
    captured: list[tuple[Path, tuple[str, ...], int, BenchCredentials]] = []

    async def resolved(*_args: object) -> BenchCredentials:
        return credentials

    async def disposed() -> None:
        return None

    class CapturedRun:
        def __init__(
            self,
            root: Path,
            cases: tuple[str, ...],
            concurrency: int,
            credentials: BenchCredentials,
        ) -> None:
            captured.append((root, cases, concurrency, credentials))

        def run(self) -> int:
            return 0

    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///evals.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    monkeypatch.setattr("evals.__main__.init_db", lambda _url: None)
    monkeypatch.setattr("evals.__main__.dispose_db", disposed)
    monkeypatch.setattr("evals.__main__._terminal_bench_credentials", resolved)
    monkeypatch.setattr("evals.__main__.TerminalBenchRun", CapturedRun)

    evals_main(
        [
            "--terminal-bench",
            "--terminal-bench-case",
            "html-js-filter",
            "--terminal-bench-root",
            str(root),
            "--remote",
            "--workspace",
            str(workspace_id),
            "--concurrency",
            "18",
        ]
    )

    assert captured == [(root, ("html-js-filter",), 18, credentials)]


def test_eval_runner_requires_remote_workspace_for_terminal_bench(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit):
        evals_main(["--terminal-bench"])
    assert "--terminal-bench requires --remote" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(["--terminal-bench", "--remote"])
    assert "--terminal-bench requires --workspace" in capsys.readouterr().err


async def test_remote_credentials_bind_the_workspace_admin_and_built_client(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=uuid4(),
                workspace_id=workspace_id,
                email="owner@example.com",
                is_admin=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    secret = "terminal-bench-test-secret"
    monkeypatch.setenv(UFO_TOKEN_SECRET_ENV, secret)
    root = tmp_path / "terminal-bench"
    config = Config(
        database=DatabaseConfig(url=database_url),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
        connect=ConnectConfig(public_base_url="https://eval.ufo.test"),
    )

    credentials = await _terminal_bench_credentials(config, workspace_id, root)

    assert credentials.client == (root / "bin/x86_64/ufo").resolve()
    assert credentials.workspace_url == "https://eval.ufo.test"
    assert verified_claims(credentials.token) == (str(workspace_id), "owner@example.com")


def _prepared_root(
    tmp_path: Path, workspace_url: str = "https://eval.ufo.test"
) -> tuple[Path, BenchCredentials]:
    root = tmp_path / "terminal-bench"
    for case in CASES:
        manifest = root / "tasks" / case / "task.toml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("[task]\n")
    client = root / "bin" / "x86_64" / "ufo"
    client.parent.mkdir(parents=True)
    client.write_text("#!/bin/sh\n")
    client.chmod(0o755)
    return root, BenchCredentials(client, TOKEN, workspace_url)


def _job(job_dir: Path, cases: tuple[str, ...], exception: str | None = None) -> None:
    job_dir.mkdir(parents=True)
    (job_dir / "result.json").write_text(json.dumps({"n_total_trials": len(cases)}))
    for index, case in enumerate(cases):
        trial = job_dir / f"{case}__{index}"
        (trial / "verifier").mkdir(parents=True)
        (trial / "result.json").write_text(
            json.dumps(
                {
                    "trial_name": trial.name,
                    "exception_info": (
                        None if exception is None else {"exception_type": exception}
                    ),
                }
            )
        )
        (trial / "verifier" / "reward.txt").write_text("1")
