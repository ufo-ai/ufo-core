import json
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
import sqlalchemy as sa

from evals.__main__ import _terminal_bench_credentials
from evals.__main__ import main as evals_main
from evals.terminal_bench.run import (
    CUSTOM_AGENT,
    DAYTONA_BACKEND,
    BenchCredentials,
    HarborBackend,
    TerminalBenchRun,
    TrialOutcome,
    harbor_command,
    harbor_environment,
    official_outputs,
    select_cases,
)
from evals.terminal_bench.setup import UPSTREAM_FILE, load_upstream
from ufo.config import BlobConfig, Config, ConnectConfig, DatabaseConfig
from ufo.db import workspace_tx
from ufo.harness.auth.bearer import UFO_TOKEN_SECRET_ENV, verified_claims
from ufo.schema import tables

TOKEN = "ufo-bearer-4d0f2c8a1b6e"
DATASET = "terminal-bench/terminal-bench-2-1"
DATASET_DIGEST = "sha256:7d7bdc1cbedad549fc1140404bd4dc45e5fd0ea7c4186773687d177ad3a0699a"
CASES = ("openssl-selfsigned-cert", "regex-log", "cancel-async-tasks")


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


def test_harbor_environment_carries_the_document_path_only_when_the_run_pins_one(
    tmp_path: Path,
) -> None:
    credentials = BenchCredentials(
        client=tmp_path / "ufo",
        token=TOKEN,
        workspace_url="https://eval.ufo.test",
    )
    document = tmp_path / "overrides.yaml"
    pinned = harbor_environment({"PATH": "/usr/bin"}, credentials, None, document)
    assert pinned["UFO_BENCH_ENVIRONMENT"] == str(document)
    inherited = harbor_environment(
        {"PATH": "/usr/bin", "UFO_BENCH_ENVIRONMENT": "stale"}, credentials
    )
    assert "UFO_BENCH_ENVIRONMENT" not in inherited


async def test_terminal_bench_agent_uploads_and_pins_the_environment_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import (
        CLIENT_TARGET,
        CREDENTIALS_TARGET,
        ENVIRONMENT_TARGET,
        UfoAgent,
    )

    client = tmp_path / "ufo"
    client.write_text("client")
    client.chmod(0o755)
    document = tmp_path / "overrides.yaml"
    document.write_text("main:\n  model: z-ai/glm-5.3-flash\n")
    monkeypatch.setenv("UFO_BENCH_CLIENT", str(client))
    monkeypatch.setenv("UFO_BENCH_TOKEN", TOKEN)
    monkeypatch.setenv("UFO_BENCH_WORKSPACE_URL", "https://eval.ufo.test")
    monkeypatch.setenv("UFO_BENCH_ENVIRONMENT", str(document))
    monkeypatch.delenv("UFO_BENCH_MODEL", raising=False)
    environment = SimpleNamespace(default_user="agent", upload_file=AsyncMock(), context_id=uuid4())
    agent = UfoAgent(tmp_path)
    monkeypatch.setattr(agent, "exec_as_root", AsyncMock())
    execute = AsyncMock(return_value=SimpleNamespace(stdout="0"))
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.install(environment)
    assert [call.args[1] for call in environment.upload_file.await_args_list] == [
        CLIENT_TARGET,
        CREDENTIALS_TARGET,
        ENVIRONMENT_TARGET,
    ]

    await agent.run("repair input", environment, AgentContext())
    start = execute.await_args_list[1]
    channel = start.kwargs["env"]["UFO_CHANNEL"]
    assert shlex.split(start.kwargs["command"])[3] == (
        "/installed-agent/ufo --environment /installed-agent/home/environment.yaml "
        f"--json 'repair input' </dev/null; echo $? >/installed-agent/home/{channel}.exit"
    )


async def test_terminal_bench_agent_closes_json_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(return_value=SimpleNamespace(stdout="0"))
    context = AgentContext()
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.run("repair 'quoted' input", environment, context)

    start = execute.await_args_list[0]
    channel = start.kwargs["env"]["UFO_CHANNEL"]
    assert start.args == (environment,)
    assert start.kwargs["env"] == {
        "UFO_HOME": "/installed-agent/home",
        "WORKSPACE_URL": "https://eval.ufo.test",
        "UFO_CHANNEL": channel,
    }
    assert start.kwargs["command"].endswith(
        f"</dev/null >/installed-agent/home/{channel}.out 2>/installed-agent/home/{channel}.err &"
    )
    words = shlex.split(start.kwargs["command"])
    assert words[:3] == ["nohup", "sh", "-c"]
    client, _, epilogue = words[3].partition("; ")
    assert client == "/installed-agent/ufo --json 'repair '\"'\"'quoted'\"'\"' input' </dev/null"
    assert epilogue == f"echo $? >/installed-agent/home/{channel}.exit"
    assert shlex.split(client) == [
        "/installed-agent/ufo",
        "--json",
        "repair 'quoted' input",
        "</dev/null",
    ]
    assert execute.await_args_list[1].kwargs["command"] == (
        f"cat /installed-agent/home/{channel}.exit 2>/dev/null || true"
    )
    assert execute.await_count == 2
    assert context.metadata == {
        "channel": channel,
        "client_target": "/installed-agent/ufo",
    }


async def test_terminal_bench_agent_sends_the_selected_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    agent._model = "z-ai/glm-5.3-flash"
    execute = AsyncMock(return_value=SimpleNamespace(stdout="0"))
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.run("repair input", environment, AgentContext())

    start = execute.await_args_list[0]
    channel = start.kwargs["env"]["UFO_CHANNEL"]
    assert shlex.split(start.kwargs["command"])[3] == (
        "/installed-agent/ufo --model z-ai/glm-5.3-flash --json 'repair input' </dev/null; "
        f"echo $? >/installed-agent/home/{channel}.exit"
    )


async def test_terminal_bench_agent_polls_until_the_exit_file_appears(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench import agent as agent_module
    from evals.terminal_bench.agent import UfoAgent

    monkeypatch.setattr(agent_module, "POLL_INTERVAL_SECONDS", 0)
    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout="0\n"),
        ]
    )
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.run("repair input", environment, AgentContext())

    channel = execute.await_args_list[0].kwargs["env"]["UFO_CHANNEL"]
    poll = f"cat /installed-agent/home/{channel}.exit 2>/dev/null || true"
    assert [call.kwargs["command"] for call in execute.await_args_list[1:]] == [poll, poll, poll]


async def test_terminal_bench_agent_raises_a_nonzero_exit_with_the_stderr_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.agents.installed.base import NonZeroAgentExitCodeError
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout="3\n"),
            SimpleNamespace(stdout="turn output tail"),
            SimpleNamespace(stdout="wedged: connection reset by peer"),
        ]
    )
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    with pytest.raises(NonZeroAgentExitCodeError, match="connection reset by peer") as raised:
        await agent.run("repair input", environment, AgentContext())

    assert "exit 3" in str(raised.value)
    channel = execute.await_args_list[0].kwargs["env"]["UFO_CHANNEL"]
    assert [call.kwargs["command"] for call in execute.await_args_list[2:]] == [
        f"tail -c 10000 /installed-agent/home/{channel}.out 2>/dev/null || true",
        f"tail -c 10000 /installed-agent/home/{channel}.err 2>/dev/null || true",
    ]


async def test_terminal_bench_agent_classifies_the_recorded_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from harbor.agents.installed.base import ApiRateLimitError
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout="1"),
            SimpleNamespace(stdout=""),
            SimpleNamespace(stdout="request failed: rate limit exceeded"),
        ]
    )
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    with pytest.raises(ApiRateLimitError, match="rate limit exceeded"):
        await agent.run("repair input", environment, AgentContext())


async def test_terminal_bench_agent_retries_a_transport_fault_on_the_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    import httpcore
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench import agent as agent_module
    from evals.terminal_bench.agent import UfoAgent

    monkeypatch.setattr(agent_module, "TRANSPORT_RETRY_SECONDS", 0)
    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(
        side_effect=[
            SimpleNamespace(stdout=""),
            httpcore.ReadError("read failed"),
            httpcore.LocalProtocolError("connection closed"),
            SimpleNamespace(stdout="0\n"),
        ]
    )
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    await agent.run("repair input", environment, AgentContext())

    channel = execute.await_args_list[0].kwargs["env"]["UFO_CHANNEL"]
    poll = f"cat /installed-agent/home/{channel}.exit 2>/dev/null || true"
    assert [call.kwargs["command"] for call in execute.await_args_list[1:]] == [poll, poll, poll]


async def test_terminal_bench_agent_raises_the_transport_fault_past_the_bound(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    import httpcore
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench import agent as agent_module
    from evals.terminal_bench.agent import UfoAgent

    monkeypatch.setattr(agent_module, "TRANSPORT_RETRY_SECONDS", 0)
    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    faults = [httpcore.ReadError(f"read failed {attempt}") for attempt in range(5)]
    execute = AsyncMock(side_effect=[SimpleNamespace(stdout=""), *faults])
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    with pytest.raises(httpcore.ReadError) as raised:
        await agent.run("repair input", environment, AgentContext())

    assert raised.value is faults[-1]
    assert execute.await_count == 6


async def test_terminal_bench_agent_start_fault_falls_through_when_the_client_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    import httpcore
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    execute = AsyncMock(
        side_effect=[
            httpcore.ReadError("read failed"),
            SimpleNamespace(stdout="started\n"),
            SimpleNamespace(stdout="0\n"),
        ]
    )
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    context = AgentContext()

    await agent.run("repair input", environment, context)

    channel = context.metadata["channel"]
    assert [call.kwargs["command"] for call in execute.await_args_list[1:]] == [
        f"if [ -e /installed-agent/home/{channel}.exit ] || "
        f"[ -e /installed-agent/home/{channel}.out ]; then echo started; fi",
        f"cat /installed-agent/home/{channel}.exit 2>/dev/null || true",
    ]


async def test_terminal_bench_agent_start_fault_raises_when_the_client_never_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    import httpcore
    from harbor.models.agent.context import AgentContext

    from evals.terminal_bench.agent import UfoAgent

    environment = SimpleNamespace(context_id=uuid4())
    agent = UfoAgent(tmp_path)
    agent._workspace_url = "https://eval.ufo.test"
    fault = httpcore.LocalProtocolError("connection closed")
    execute = AsyncMock(side_effect=[fault, SimpleNamespace(stdout="")])
    monkeypatch.setattr(agent, "exec_as_agent", execute)

    with pytest.raises(httpcore.LocalProtocolError) as raised:
        await agent.run("repair input", environment, AgentContext())

    assert raised.value is fault
    assert execute.await_count == 2


async def test_terminal_bench_agent_reads_the_selected_model_from_its_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("harbor")
    from evals.terminal_bench.agent import CLIENT_TARGET, CREDENTIALS_TARGET, UfoAgent

    client = tmp_path / "ufo"
    client.write_text("client")
    client.chmod(0o755)
    monkeypatch.setenv("UFO_BENCH_CLIENT", str(client))
    monkeypatch.setenv("UFO_BENCH_TOKEN", TOKEN)
    monkeypatch.setenv("UFO_BENCH_WORKSPACE_URL", "https://eval.ufo.test")
    monkeypatch.setenv("UFO_BENCH_MODEL", "z-ai/glm-5.3-flash")
    environment = SimpleNamespace(default_user="agent", upload_file=AsyncMock())
    agent = UfoAgent(tmp_path)
    monkeypatch.setattr(agent, "exec_as_root", AsyncMock())
    monkeypatch.setattr(agent, "exec_as_agent", AsyncMock())

    await agent.install(environment)

    assert agent._model == "z-ai/glm-5.3-flash"
    assert [call.args[1] for call in environment.upload_file.await_args_list] == [
        CLIENT_TARGET,
        CREDENTIALS_TARGET,
    ]


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

    assert TerminalBenchRun(root, CASES, 12, credentials, model="z-ai/glm-5.3-flash").run() == 0
    assert len(seen) == 1
    command, environment = seen[0]
    assert command[command.index("--n-concurrent") + 1] == "12"
    assert tuple(
        command[index + 1] for index, value in enumerate(command) if value == "--include-task-name"
    ) == tuple(f"terminal-bench/{case}" for case in CASES)
    assert environment["UFO_BENCH_TOKEN"] == TOKEN
    assert environment["UFO_BENCH_MODEL"] == "z-ai/glm-5.3-flash"
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


def test_missing_client_fails_before_harbor(tmp_path: Path) -> None:
    root, credentials = _prepared_root(tmp_path)
    credentials.client.unlink()
    with pytest.raises(FileNotFoundError, match="client"):
        TerminalBenchRun(root, (), 1, credentials).run()


def test_official_outputs_require_results_and_rewards(tmp_path: Path) -> None:
    job_dir = tmp_path / "job"
    _job(job_dir, ("regex-log",))

    (output,) = official_outputs(job_dir)
    assert output.result.name == "result.json"
    assert output.reward.name == "reward.txt"
    assert output.outcome is TrialOutcome.PASSED
    assert output.exception is None

    output.reward.unlink()
    with pytest.raises(FileNotFoundError, match="reward"):
        official_outputs(job_dir)


def test_official_outputs_void_a_rewardless_trial_with_its_recorded_exception(
    tmp_path: Path,
) -> None:
    job_dir = tmp_path / "job"
    _job(job_dir, CASES)
    _void(job_dir / "cancel-async-tasks__2", "ProtocolError")
    (job_dir / "regex-log__1" / "verifier" / "reward.txt").write_text("0")

    voided, passed, failed = official_outputs(job_dir)
    assert voided.outcome is TrialOutcome.VOIDED
    assert voided.reward is None
    assert voided.exception == "ProtocolError"
    assert passed.outcome is TrialOutcome.PASSED
    assert passed.reward is not None
    assert failed.outcome is TrialOutcome.FAILED
    assert failed.exception is None


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

    assert TerminalBenchRun(root, ("regex-log",), 1, credentials).run() == 1


def test_voided_trials_read_out_fully_and_fail_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root, credentials = _prepared_root(tmp_path)

    def complete(command: tuple[str, ...], **kwargs) -> subprocess.CompletedProcess[str]:
        job_dir = (
            Path(command[command.index("--jobs-dir") + 1])
            / command[command.index("--job-name") + 1]
        )
        _job(job_dir, CASES)
        (job_dir / "regex-log__1" / "verifier" / "reward.txt").write_text("0")
        _void(job_dir / "cancel-async-tasks__2", "ProtocolError")
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", complete)

    assert TerminalBenchRun(root, CASES, 3, credentials).run() == 1
    output = capsys.readouterr().out
    assert "voided by ProtocolError" in output
    assert "reward.txt" in output
    assert "1 passed, 1 failed, 1 voided by exception" in output
    assert "Harbor recorded ProtocolError" in output


def test_eval_runner_routes_remote_scale_into_one_harbor_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id = uuid4()
    root = tmp_path / "terminal-bench"
    credentials = BenchCredentials(root / "bin/x86_64/ufo", TOKEN, "https://eval.ufo.test")
    captured: list[
        tuple[Path, tuple[str, ...], int, BenchCredentials, HarborBackend, str | None]
    ] = []

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
            backend: HarborBackend,
            model: str | None,
            environment_document: Path | None,
        ) -> None:
            captured.append(
                (root, cases, concurrency, credentials, backend, model, environment_document)
            )

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
            "regex-log",
            "--terminal-bench-root",
            str(root),
            "--terminal-bench-environment",
            "e2b",
            "--terminal-bench-harbor-extra",
            "e2b",
            "--remote",
            "--model",
            "z-ai/glm-5.3-flash",
            "--workspace",
            str(workspace_id),
            "--concurrency",
            "18",
            "--environment",
            str(tmp_path / "overrides.yaml"),
        ]
    )

    assert captured == [
        (
            root,
            ("regex-log",),
            18,
            credentials,
            HarborBackend(environment="e2b", extra="e2b"),
            "z-ai/glm-5.3-flash",
            tmp_path / "overrides.yaml",
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


def _void(trial: Path, exception: str) -> None:
    (trial / "verifier" / "reward.txt").unlink()
    (trial / "result.json").write_text(
        json.dumps({"trial_name": trial.name, "exception_info": {"exception_type": exception}})
    )
