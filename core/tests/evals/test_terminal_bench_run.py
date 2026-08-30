import json
import subprocess
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa

from evals.__main__ import _terminal_bench_credentials
from evals.__main__ import main as evals_main
from evals.terminal_bench.configure import (
    CONFIG_DIGEST_FILE,
    CONFIG_FILE,
    TEMPLATE,
    render_config,
)
from evals.terminal_bench.configure import (
    main as configure_main,
)
from evals.terminal_bench.run import (
    CUSTOM_AGENT,
    DAYTONA_BACKEND,
    BenchCredentials,
    HarborBackend,
    TerminalBenchRun,
    harbor_command,
    harbor_environment,
    official_outputs,
    select_cases,
)
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream
from ufo.auth.bearer import UFO_TOKEN_SECRET_ENV, verified_claims
from ufo.config import BlobConfig, Config, ConnectConfig, DatabaseConfig, load_config
from ufo.db import workspace_tx
from ufo.models.interface import AUTO_MODEL
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME

TOKEN = "ufo-bearer-4d0f2c8a1b6e"
DATASET = "terminal-bench/terminal-bench-2-1"
DATASET_DIGEST = "sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a"
CASES = ("openssl-selfsigned-cert", "regex-log", "cancel-async-tasks")


def test_tracked_config_pins_the_terminal_bench_control_plane() -> None:
    config = load_config(TEMPLATE)

    assert config.models.auto_model == "z-ai/glm-5.3-flash"
    assert config.models.ambient_reply_model == "gpt-5.6-luna"
    assert config.models.background_jobs_model == "gpt-5.6-luna"
    assert config.models.subagent_models == {"coding": "z-ai/glm-5.3-flash"}
    assert config.pack.name == "assistant_hosted"
    assert config.sandbox.backend == "local"
    assert config.hub.backend == "in_process"
    assert config.terminal.backend == "in_process"


def test_rendered_config_isolates_runtime_values_and_records_its_digest(tmp_path: Path) -> None:
    root = tmp_path / "candidate"

    output = render_config(
        root,
        "https://candidate.eval.test",
        "candidate/model",
        serve_port=58001,
        proxy_port=58002,
    )
    payload = output.read_bytes()
    config = load_config(output)

    assert output == root / CONFIG_FILE
    assert config.database.url == f"sqlite+aiosqlite:///{root / 'ufo.db'}"
    assert config.database.owner_url == config.database.url
    assert config.blob.root == root / "blobs"
    assert config.models.auto_model == "candidate/model"
    assert config.models.subagent_models == {"coding": "candidate/model"}
    assert config.serve.port == 58001
    assert config.connect.public_base_url == "https://candidate.eval.test"
    assert config.sandbox.workspace_root == root / "workspaces"
    assert config.sandbox.proxy_port == 58002
    assert (root / CONFIG_DIGEST_FILE).read_text() == f"{sha256(payload).hexdigest()}\n"
    assert (
        render_config(
            root,
            "https://candidate.eval.test",
            "candidate/model",
            serve_port=58001,
            proxy_port=58002,
        ).read_bytes()
        == payload
    )


@pytest.mark.parametrize(
    ("url", "model", "serve_port", "proxy_port", "message"),
    (
        ("http://eval.test", "candidate/model", 58001, 58002, "public HTTPS"),
        ("https:///missing", "candidate/model", 58001, 58002, "public HTTPS"),
        ("https://eval.test", "auto", 58001, 58002, "concrete model"),
        ("https://eval.test", "candidate/model", 0, 58002, "between 1 and 65535"),
        ("https://eval.test", "candidate/model", 58001, 58001, "must differ"),
    ),
)
def test_rendered_config_rejects_ambiguous_runtime_values(
    tmp_path: Path,
    url: str,
    model: str,
    serve_port: int,
    proxy_port: int,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        render_config(tmp_path, url, model, serve_port, proxy_port)


def test_configure_cli_uses_the_tracked_model(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "control"

    configure_main(
        [
            "--root",
            str(root),
            "--public-base-url",
            "https://control.eval.test",
        ]
    )

    assert capsys.readouterr().out.strip() == str(root / CONFIG_FILE)
    assert load_config(root / CONFIG_FILE).models.auto_model == "z-ai/glm-5.3-flash"


def test_default_and_named_cases_resolve_in_manifest_order() -> None:
    upstream = load_upstream(UPSTREAM_FILE)

    assert len(select_cases(upstream, ())) == 89
    assert select_cases(upstream, ("cancel-async-tasks", "regex-log")) == (
        "regex-log",
        "cancel-async-tasks",
    )


@pytest.mark.parametrize(
    ("requested", "message"),
    (
        (("regex-log", "regex-log"), "duplicate"),
        (("unknown",), "unknown"),
    ),
)
def test_invalid_case_selections_fail(requested: tuple[str, ...], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        select_cases(load_upstream(UPSTREAM_FILE), requested)


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


def test_harbor_command_accepts_an_environment_and_its_extra(tmp_path: Path) -> None:
    command = harbor_command(
        DATASET,
        DATASET_DIGEST,
        select_cases(load_upstream(UPSTREAM_FILE), ("regex-log",)),
        tmp_path / "jobs",
        "ufo-one",
        1,
        "0.21.0",
        HarborBackend(environment="e2b", extra="e2b"),
    )

    assert command[3] == "harbor[e2b]==0.21.0"
    assert command[command.index("--env") + 1] == "e2b"


def test_token_reaches_harbor_only_through_the_environment(tmp_path: Path) -> None:
    credentials = BenchCredentials(
        client=tmp_path / "ufo",
        token=TOKEN,
        workspace_url="https://eval.ufo.test",
    )
    command = harbor_command(
        DATASET,
        DATASET_DIGEST,
        select_cases(load_upstream(UPSTREAM_FILE), ("regex-log",)),
        tmp_path / "jobs",
        "ufo-one",
        1,
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


async def test_terminal_bench_agent_closes_json_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock()
    context = AgentContext()
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.run("repair 'quoted' input", environment, context)

    execute.assert_awaited_once_with(
        environment,
        command="/installed-agent/ufo --json 'repair '\"'\"'quoted'\"'\"' input' </dev/null",
        env={
            "UFO_HOME": "/installed-agent/home",
            "WORKSPACE_URL": "https://eval.ufo.test",
            "UFO_CHANNEL": execute.await_args.kwargs["env"]["UFO_CHANNEL"],
        },
    )
    assert context.metadata == {
        "channel": execute.await_args.kwargs["env"]["UFO_CHANNEL"],
        "client_target": "/installed-agent/ufo",
    }


def test_selected_run_keeps_harbor_grading_and_one_concurrent_job(
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

    assert TerminalBenchRun(root, CASES, 12, credentials, TEMPLATE).run() == 0
    assert len(seen) == 1
    command, environment = seen[0]
    assert command[command.index("--n-concurrent") + 1] == "12"
    assert tuple(
        command[index + 1] for index, value in enumerate(command) if value == "--include-task-name"
    ) == tuple(f"terminal-bench/{case}" for case in CASES)
    assert environment["UFO_BENCH_TOKEN"] == TOKEN
    assert TOKEN not in " ".join(command)
    job_dir = (
        Path(command[command.index("--jobs-dir") + 1]) / command[command.index("--job-name") + 1]
    )
    config = TEMPLATE.read_bytes()
    assert (job_dir / CONFIG_FILE).read_bytes() == config
    assert (job_dir / CONFIG_DIGEST_FILE).read_text() == f"{sha256(config).hexdigest()}\n"


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
        TerminalBenchRun(root, (), concurrency, credentials, TEMPLATE).run()

    assert not (root / "jobs").exists()


def test_missing_client_fails_before_harbor(tmp_path: Path) -> None:
    root, credentials = _prepared_root(tmp_path)
    credentials.client.unlink()
    with pytest.raises(FileNotFoundError, match="client"):
        TerminalBenchRun(root, (), 1, credentials, TEMPLATE).run()


def test_missing_config_fails_before_harbor(tmp_path: Path) -> None:
    root, credentials = _prepared_root(tmp_path)

    with pytest.raises(FileNotFoundError, match="ufo config"):
        TerminalBenchRun(root, (), 1, credentials, tmp_path / "missing.toml").run()


def test_official_outputs_require_results_and_rewards(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _job(job_dir, ("regex-log",))

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
        _job(job_dir, ("regex-log",), exception="NonZeroAgentExitCodeError")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", complete)

    assert TerminalBenchRun(root, ("regex-log",), 1, credentials, TEMPLATE).run() == 1


def test_harbor_failure_still_retains_the_rendered_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root, credentials = _prepared_root(tmp_path)
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(command, 7),
    )

    assert TerminalBenchRun(root, ("regex-log",), 1, credentials, TEMPLATE).run() == 7

    (job_dir,) = (root / "jobs").iterdir()
    payload = TEMPLATE.read_bytes()
    assert (job_dir / CONFIG_FILE).read_bytes() == payload
    assert (job_dir / CONFIG_DIGEST_FILE).read_text() == f"{sha256(payload).hexdigest()}\n"


def test_eval_runner_routes_remote_scale_into_one_harbor_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    root = tmp_path / "terminal-bench"
    credentials = BenchCredentials(root / "bin/x86_64/ufo", TOKEN, "https://eval.ufo.test")
    captured: list[tuple[Path, tuple[str, ...], int, BenchCredentials, Path, HarborBackend]] = []

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
            config_file: Path,
            backend: HarborBackend,
        ) -> None:
            captured.append((root, cases, concurrency, credentials, config_file, backend))

        def run(self) -> int:
            return 0

    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///evals.db"),
        blob=BlobConfig(backend="filesystem", root=tmp_path / "blobs"),
    )
    monkeypatch.setattr("evals.__main__.load_config", lambda: config)
    monkeypatch.setattr("evals.__main__.config_path", lambda: TEMPLATE)
    monkeypatch.setattr("evals.__main__.init_db", lambda _url: None)
    monkeypatch.setattr("evals.__main__.dispose_db", disposed)
    monkeypatch.setattr("evals.__main__._terminal_bench_credentials", resolved)
    monkeypatch.setattr("evals.__main__.TerminalBenchRun", CapturedRun)

    evals_main(
        [
            "--terminal-bench",
            "--terminal-bench-case",
            "regex-log",
            "--terminal-bench-root",
            str(root),
            "--terminal-bench-environment",
            "e2b",
            "--terminal-bench-harbor-extra",
            "e2b",
            "--remote",
            "--workspace",
            str(workspace_id),
            "--concurrency",
            "18",
        ]
    )

    assert captured == [
        (
            root,
            ("regex-log",),
            18,
            credentials,
            TEMPLATE.resolve(),
            HarborBackend(environment="e2b", extra="e2b"),
        )
    ]


def test_eval_runner_requires_remote_workspace_for_terminal_bench(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    load = Mock(side_effect=AssertionError("configuration was loaded"))
    monkeypatch.setattr("evals.__main__.load_config", load)
    with pytest.raises(SystemExit):
        evals_main(["--terminal-bench"])
    assert "--terminal-bench requires --remote" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(["--terminal-bench", "--remote"])
    assert "--terminal-bench requires --workspace" in capsys.readouterr().err

    with pytest.raises(SystemExit):
        evals_main(
            [
                "--terminal-bench",
                "--remote",
                "--workspace",
                str(uuid4()),
                "--budget-usd",
                "50",
            ]
        )
    assert "--budget-usd is not supported with --terminal-bench" in capsys.readouterr().err
    load.assert_not_called()


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
        await connection.execute(
            sa.insert(tables.agent).values(
                id=uuid4(),
                workspace_id=workspace_id,
                name=DEFAULT_AGENT_NAME,
                prompt="",
                model=AUTO_MODEL,
                is_main=True,
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

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .where(tables.agent.c.workspace_id == workspace_id)
            .values(model="candidate/model")
        )
    with pytest.raises(ValueError, match="must use model 'auto'"):
        await _terminal_bench_credentials(config, workspace_id, root)


def _prepared_root(
    tmp_path: Path, workspace_url: str = "https://eval.ufo.test"
) -> tuple[Path, BenchCredentials]:
    root = tmp_path / "terminal-bench"
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
