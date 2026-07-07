"""The operator CLI end to end through its real Click verbs, keyless.

Every verb runs through `CliRunner` against `selfhost.cli`, a real SQLite database, and the real
filesystem — no mocks of the CLI, the loader, the database, or the ledger. The paid dependency, the
model, is the only double: the chat turn runs against a StandIn model client (mirroring
`test_turn_lifecycle`'s registry) served by a real in-process uvicorn server, so a `ping` streams a
real terminal frame and bills a real ledger row without an API key. The DB-owning verbs
(`spend-cap`, `spend`, `grants`, `bundle`, `ext`) each manage their own connection, so they run
in an isolated filesystem with no persistent engine; the chat flow holds a persistent engine and the
session's DBOS worker, so its ledger read disposes that engine first and lets the `spend` verb open
its own — the same lifecycle boundary the real process has between `serve` and a one-shot verb."""

import asyncio
import hashlib
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
import uvicorn
from click.testing import CliRunner
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from selfhost_ext_index_default import DefaultIndex
from selfhost_testsupport.tables import DELETE_ORDER

from selfhost import cli
from selfhost.accounting import CORE_PRICING
from selfhost.blob import FilesystemBlobStore
from selfhost.browser import SandboxCdpProvider
from selfhost.config import Config, load_config
from selfhost.credentials import CredentialStore
from selfhost.db import dispose_db, init_db, workspace_tx
from selfhost.ext.loader import skill_registry
from selfhost.ext.manifest import ModelProviderSpec
from selfhost.hub import InProcessHub
from selfhost.loop import queue as loop_queue
from selfhost.loop.subagents import SubagentRegistry
from selfhost.models.interface import ModelEvent, ModelRequest, TextDelta
from selfhost.models.registry import ModelRegistry
from selfhost.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME, Usage
from selfhost.surfaces.cli import router

OWNER_EMAIL = "owner@example.com"
SERVER_START_TIMEOUT_SECONDS = 10.0
SERVER_POLL_SECONDS = 0.02
CATALOG = """\
[[extensions]]
name = "memory"
version = "0.1.0"

[[extensions]]
name = "todos"
version = "0.1.0"
"""
EXT_STORE_CONFIG = """\
[database]
url = "sqlite+aiosqlite:///selfhost.db"

[blob]
backend = "filesystem"
root = "./blobs"

[ext]
store = "catalog.toml"
"""


@pytest.fixture
def cli_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[CliRunner]:
    """A CliRunner in an isolated filesystem with the presence-only env the verbs require: a dummy
    Anthropic key (the StandIn never calls it) and a real Fernet credential key (the installed
    sample's onboarding step needs one). `SELFHOST_DIR` is redirected into the tmp tree so the token
    and session files never touch the developer's home."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-cli")
    monkeypatch.setenv("SELFHOST_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cli, "SELFHOST_DIR", tmp_path / ".selfhost")
    runner = CliRunner()
    with runner.isolated_filesystem():
        yield runner


def _init(runner: CliRunner) -> None:
    result = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output


def test_init_writes_config_and_token_then_fails_loud_on_rerun(
    cli_home: CliRunner, tmp_path: Path
) -> None:
    first = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert first.exit_code == 0, first.output
    assert "workspace ready" in first.output
    config = Path("selfhost.toml").read_text()
    assert 'url = "sqlite+aiosqlite:///selfhost.db"' in config
    assert 'backend = "filesystem"' in config
    assert (tmp_path / ".selfhost" / "token").read_text()

    second = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert second.exit_code != 0
    assert "already initialized" in second.output


def test_spend_cap_set_then_list(cli_home: CliRunner) -> None:
    _init(cli_home)
    created = cli_home.invoke(
        cli.main,
        [
            "spend-cap",
            "set",
            "--scope",
            "workspace",
            "--window-seconds",
            "3600",
            "--limit-micro-usd",
            "5000000",
        ],
    )
    assert created.exit_code == 0, created.output
    assert "spend cap" in created.output
    assert "workspace" in created.output

    listed = cli_home.invoke(cli.main, ["spend-cap", "list"])
    assert listed.exit_code == 0, listed.output
    assert "workspace" in listed.output
    assert "3600s" in listed.output


def test_spend_reads_an_empty_ledger_as_zero(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["spend"])
    assert result.exit_code == 0, result.output
    assert "$0.000000" in result.output


def test_grants_lists_none_when_no_account_is_connected(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["grants"])
    assert result.exit_code == 0, result.output
    assert "no grants" in result.output


async def _read_credential(slot: str) -> str:
    config = load_config()
    init_db(config.database.url)
    try:
        async with workspace_tx() as connection:
            workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        key = os.environ["SELFHOST_CREDENTIAL_KEY"]
        return await CredentialStore(fernet=Fernet(key.encode())).get(workspace_id, slot)
    finally:
        await dispose_db()


def test_credential_set_round_trips_into_the_store_the_surface_reads(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["credential", "set", "exa_api"], input="exa-cli-secret\n")
    assert result.exit_code == 0, result.output
    assert "exa-cli-secret" not in result.output
    assert asyncio.run(_read_credential("exa_api")) == "exa-cli-secret"


def test_init_seeds_declared_slots_from_env(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("EXA_API", "exa-env-seeded")
    _init(cli_home)
    assert asyncio.run(_read_credential("exa_api")) == "exa-env-seeded"


def test_credential_set_rejects_an_undeclared_slot(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["credential", "set", "no_such_slot"], input="value\n")
    assert result.exit_code != 0
    assert "exa_api" in result.output


def test_credential_list_reports_set_and_unset_without_values(cli_home: CliRunner) -> None:
    _init(cli_home)
    setting = cli_home.invoke(cli.main, ["credential", "set", "exa_api"], input="exa-cli-secret\n")
    assert setting.exit_code == 0, setting.output
    listed = cli_home.invoke(cli.main, ["credential", "list"])
    assert listed.exit_code == 0, listed.output
    assert "exa-cli-secret" not in listed.output
    statuses = {
        line.split()[0]: line.split()[-1] for line in listed.output.splitlines() if line.strip()
    }
    assert statuses["exa_api"] == "set"
    assert statuses["mcp_servers"] == "unset"


def test_bundle_writes_a_runnable_artifact(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["bundle", "--out", "bundle"])
    assert result.exit_code == 0, result.output
    assert "extension(s) pinned" in result.output
    out = Path("bundle")
    dockerfile = (out / "Dockerfile").read_text()
    assert "selfhost" in dockerfile
    assert 'ENTRYPOINT ["selfhost"]' in dockerfile
    assert 'CMD ["serve"]' in dockerfile
    assert (out / "selfhost.toml").read_text()
    lockfile = (out / "selfhost.lock").read_text()
    assert "selfhost_version" in lockfile
    assert "memory" in lockfile


def test_ext_search_lists_the_catalog(cli_home: CliRunner) -> None:
    Path("selfhost.toml").write_text(EXT_STORE_CONFIG)
    Path("catalog.toml").write_text(CATALOG)
    result = cli_home.invoke(cli.main, ["ext", "search"])
    assert result.exit_code == 0, result.output
    assert "memory" in result.output
    assert "todos" in result.output
    assert "available" in result.output


def test_ext_install_then_remove_round_trips_the_lockfile(cli_home: CliRunner) -> None:
    """`ext install`/`remove` write only the deploy's cwd-local `selfhost.lock` (never the venv):
    install pins the installed extension's real digest and search then marks it installed; remove
    drops the pin. The isolated filesystem keeps the lockfile out of the developer's tree."""
    Path("selfhost.toml").write_text(EXT_STORE_CONFIG)
    Path("catalog.toml").write_text(CATALOG)

    installed = cli_home.invoke(cli.main, ["ext", "install", "memory"])
    assert installed.exit_code == 0, installed.output
    assert "installed memory" in installed.output
    assert "sha256:" in Path("selfhost.lock").read_text()

    searched = cli_home.invoke(cli.main, ["ext", "search"])
    assert "installed" in searched.output

    removed = cli_home.invoke(cli.main, ["ext", "remove", "memory"])
    assert removed.exit_code == 0, removed.output
    assert "removed memory" in removed.output
    assert "memory" not in Path("selfhost.lock").read_text()


@pytest.fixture(scope="session")
def dbos_launched_cli(dbos_launched: Config) -> Config:
    return dbos_launched


class StandInModel:
    """Echoes the round count back so a real turn runs the full queue path without a provider — the
    same shape `test_turn_lifecycle` doubles the model with, so `ping` streams `echo:1` and bills.
    """

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


STANDIN_REGISTRY = ModelRegistry(
    providers=(
        ModelProviderSpec(
            name="standin",
            matches=lambda model: True,
            client=lambda model: StandInModel(),
        ),
    ),
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


class StandInCarrier:
    """Stands in for the Docker carrier: create-or-attach returns a handle, exec is never reached
    because the StandIn model makes no tool calls."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


class StubEmbed:
    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


class _ThreadedServer:
    """A real uvicorn server on a background thread so the CLI's own httpx client reaches it over a
    real socket — signal handlers are skipped off the main thread, so `should_exit` is the clean
    stop the process's own shutdown uses."""

    def __init__(self, app: FastAPI, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        self._thread = threading.Thread(target=self._server.run, daemon=True)

    def start(self) -> None:
        self._thread.start()
        deadline = time.monotonic() + SERVER_START_TIMEOUT_SECONDS
        while not self._server.started:
            if time.monotonic() > deadline:
                raise RuntimeError("uvicorn server did not start")
            time.sleep(SERVER_POLL_SECONDS)

    def stop(self) -> None:
        self._server.should_exit = True
        self._thread.join(timeout=SERVER_START_TIMEOUT_SECONDS)


async def _bootstrap_workspace(token: str) -> None:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for table in DELETE_ORDER:
            await connection.execute(sa.delete(table))
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=OWNER_EMAIL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=DEFAULT_AGENT_NAME,
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface="cli",
                external_id=hashlib.sha256(token.encode()).hexdigest(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )


@pytest.fixture
def chat_server(
    dbos_launched_cli: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[CliRunner, str]]:
    """A live in-process server the real `chat` verb talks to: the CLI surface router over the
    session DBOS worker and a StandIn-model runtime, on an ephemeral port the written config names.
    Yields the runner and the config path so verbs run against this same SQLite database."""
    config = dbos_launched_cli
    init_db(config.database.url)
    token = "cli-token-" + uuid4().hex
    asyncio.run(_bootstrap_workspace(token))

    hub = InProcessHub()
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=FilesystemBlobStore(root=config.blob.root),
            workspace_fs=None,
            hub=hub,
            carrier=StandInCarrier(),
            cdp_provider=SandboxCdpProvider(endpoint=None),
            search_provider=None,
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            dbos=dbos_client,
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(embed=StubEmbed(), transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret="",
        )
    )

    port = _free_port()
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = dbos_client
    app.state.durable_surfaces = frozenset()
    app.include_router(router)
    server = _ThreadedServer(app, port)
    server.start()

    config_path = tmp_path / "selfhost.toml"
    config_path.write_text(
        f'[database]\nurl = "{config.database.url}"\n\n'
        f'[blob]\nbackend = "filesystem"\nroot = "{config.blob.root}"\n\n'
        f'[serve]\nhost = "127.0.0.1"\nport = {port}\n'
    )
    monkeypatch.setenv("SELFHOST_CONFIG", str(config_path))
    monkeypatch.setattr(cli, "SELFHOST_DIR", tmp_path / ".selfhost")
    (tmp_path / ".selfhost").mkdir(mode=0o700, exist_ok=True)
    (tmp_path / ".selfhost" / "token").write_text(token)

    try:
        yield CliRunner(), str(config_path)
    finally:
        server.stop()
        dbos_client.destroy()
        loop_queue.reset_runtime()
        asyncio.run(dispose_db())


def test_chat_streams_a_terminal_frame_then_spend_reports_the_burn(
    chat_server: tuple[CliRunner, str],
) -> None:
    """The headline keyless end-to-end: the real `chat` verb admits a turn on the live server, the
    StandIn turn runs through the full DBOS queue, and the stream ends on a terminal frame carrying
    the streamed text and the authoritative cost line. Disposing the server's engine then lets the
    real `spend` verb open its own connection and read the nonzero ledger row the turn billed."""
    runner, _config_path = chat_server
    chatted = runner.invoke(cli.main, ["chat", "ping"])
    assert chatted.exit_code == 0, chatted.output
    assert "echo:1" in chatted.output
    assert "claude-opus-4-8" in chatted.output
    assert "10 tok" in chatted.output
    assert "$0.000110" in chatted.output

    asyncio.run(dispose_db())
    spent = runner.invoke(cli.main, ["spend"])
    assert spent.exit_code == 0, spent.output
    assert "$0.000110" in spent.output
