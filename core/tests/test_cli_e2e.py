"""The operator CLI end to end through its real Click verbs, keyless.

Every verb runs through `CliRunner` against `ufo.cli`, a real SQLite database, and the real
filesystem — no mocks of the CLI, the loader, the database, or the ledger. The paid dependency, the
model, is the only double: the chat turn runs against a StandIn model client (mirroring
`test_turn_lifecycle`'s registry) served by a real in-process uvicorn server, so a `ping` streams a
real terminal frame and bills a real ledger row without an API key. The DB-owning verbs
(`spend-cap`, `spend`, `grants`, `bundle`, `ext`) each manage their own connection, so they run
in an isolated filesystem with no persistent engine; the chat flow holds a persistent engine and the
session's DBOS worker, so its ledger read disposes that engine first and lets the `spend` verb open
its own — the same lifecycle boundary the real process has between `serve` and a one-shot verb."""

import asyncio
import os
import socket
import threading
import time
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import uvicorn
from click.testing import CliRunner
from cryptography.fernet import Fernet
from dbos import DBOSClient
from fastapi import FastAPI
from ufo_ext_index_default import DefaultIndex
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_testsupport.surfaces import EMPTY_SKILL_REGISTRY, no_user_skills
from ufo_testsupport.tables import reset_workspace_data

from ufo import cli
from ufo.bearer import mint_token
from ufo.blob import FilesystemBlobStore
from ufo.config import Config, load_config
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.ext.loader import skill_registry
from ufo.hub import InProcessHub
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.models.registry import ModelRegistry
from ufo.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, Usage
from ufo.serve import _mount_shared_surfaces

OWNER_EMAIL = "owner@example.com"
TOKEN_SECRET = "cli-e2e-token-secret"
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
url = "sqlite+aiosqlite:///ufo.db"

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
    sample's onboarding step needs one). `UFOCTL_DIR` is redirected into the tmp tree so the token
    and session files never touch the developer's home. The artifact-token secret starts unset —
    `init` exports what it mints into this process, so a prior test's mint would otherwise
    suppress minting here."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-cli")
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.delenv("UFO_ARTIFACT_TOKEN_SECRET", raising=False)
    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / ".ufoctl"))
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
    config = Path("ufo.toml").read_text()
    assert 'url = "sqlite+aiosqlite:///ufo.db"' in config
    assert 'backend = "filesystem"' in config
    assert (tmp_path / ".ufoctl" / "token").read_text()

    second = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert second.exit_code != 0
    assert "already initialized" in second.output


def _dotenv(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if line.strip())


def test_init_provisions_dev_secrets_into_dotenv(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`init` mints the dev secrets a zero-config `serve` needs into a cwd `.env` — a valid Fernet
    credential key and the artifact-token secret — and reports that serve auto-loads it, so the next
    `serve` boots with no manual env export."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert ".env" in result.output
    env = _dotenv(Path(".env").read_text())
    Fernet(env["UFO_CREDENTIAL_KEY"].encode())
    assert env["UFO_ARTIFACT_TOKEN_SECRET"]


def test_init_does_not_clobber_an_existing_dotenv_key(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A key already in `.env` is left untouched — init only fills what is missing, so a developer's
    own value (a real model key, a pinned secret) survives re-provisioning."""
    monkeypatch.delenv("UFO_CREDENTIAL_KEY")
    Path(".env").write_text("UFO_ARTIFACT_TOKEN_SECRET=preexisting\n")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    env = _dotenv(Path(".env").read_text())
    assert env["UFO_ARTIFACT_TOKEN_SECRET"] == "preexisting"
    Fernet(env["UFO_CREDENTIAL_KEY"].encode())


def test_init_does_not_mint_an_exported_secret_into_dotenv(cli_home: CliRunner) -> None:
    """A secret already exported in the environment (the fixture exports UFO_CREDENTIAL_KEY) is
    never minted into `.env` — a second, divergent key would silently win on the next verb."""
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    env = _dotenv(Path(".env").read_text())
    assert "UFO_CREDENTIAL_KEY" not in env
    assert env["UFO_ARTIFACT_TOKEN_SECRET"]


def test_init_honors_ufo_config_for_config_and_dotenv(
    cli_home: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With `UFO_CONFIG` set, init writes `ufo.toml` at that path and `.env` beside it — nothing
    lands in the cwd."""
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    monkeypatch.setenv("UFO_CONFIG", str(deploy / "ufo.toml"))
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert 'url = "sqlite+aiosqlite:///ufo.db"' in (deploy / "ufo.toml").read_text()
    env = _dotenv((deploy / ".env").read_text())
    assert env["UFO_ARTIFACT_TOKEN_SECRET"]
    assert not Path("ufo.toml").exists()
    assert not Path(".env").exists()


def test_ufoctl_dir_env_redirects_the_token_file(
    cli_home: CliRunner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`UFOCTL_DIR` is read at call time: the token lands in the redirected dir, and neither the
    fixture's default dir nor the real home dir is touched."""
    home_token = Path.home() / ".ufoctl" / "token"
    before = home_token.read_text() if home_token.exists() else None
    redirected = tmp_path / "ctl"
    monkeypatch.setenv("UFOCTL_DIR", str(redirected))
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert (redirected / "token").read_text()
    assert not (tmp_path / ".ufoctl").exists()
    after = home_token.read_text() if home_token.exists() else None
    assert after == before


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
        key = os.environ["UFO_CREDENTIAL_KEY"]
        return await CredentialStore(fernet=Fernet(key.encode())).get(workspace_id, slot)
    finally:
        await dispose_db()


def test_credential_set_round_trips_into_the_store_the_surface_reads(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(
        cli.main, ["credential", "set", "exa_api_key"], input="exa-cli-secret\n"
    )
    assert result.exit_code == 0, result.output
    assert "exa-cli-secret" not in result.output
    assert asyncio.run(_read_credential("exa_api_key")) == "exa-cli-secret"


def test_init_does_not_seed_platform_credentials_into_the_store(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform default the environment provides is read live at use, never copied into the
    workspace at init — so the store holds no seeded exa_api_key. CredentialAccess resolves it live
    from env (see the onboarding tests), so a rotation is never shadowed by a stale copy."""
    monkeypatch.setenv("EXA_API_KEY", "exa-env-seeded")
    _init(cli_home)
    with pytest.raises(CredentialSlotUnset):
        asyncio.run(_read_credential("exa_api_key"))


def test_credential_set_rejects_an_undeclared_slot(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["credential", "set", "no_such_slot"], input="value\n")
    assert result.exit_code != 0
    assert "exa_api_key" in result.output


def test_credential_set_refuses_a_slot_this_deploy_writes_itself(cli_home: CliRunner) -> None:
    """The operator half of the rule the member's private prompt follows. The installation slot
    holds a seal the install callback composes, so a typed value is not a weaker credential — it is
    one that only refuses when the wire reads it, withholding that host on every turn until someone
    rebinds. The refusal names the reason rather than reporting the slot as unknown, since it is
    declared and this deploy's own callback really does fill it."""
    _init(cli_home)
    result = cli_home.invoke(
        cli.main, ["credential", "set", "github_app_installation"], input="149082716\n"
    )
    assert result.exit_code != 0
    assert "written by this deploy" in result.output
    assert "unknown credential slot" not in result.output


def test_credential_list_reports_set_and_unset_without_values(cli_home: CliRunner) -> None:
    _init(cli_home)
    setting = cli_home.invoke(
        cli.main, ["credential", "set", "exa_api_key"], input="exa-cli-secret\n"
    )
    assert setting.exit_code == 0, setting.output
    listed = cli_home.invoke(cli.main, ["credential", "list"])
    assert listed.exit_code == 0, listed.output
    assert "exa-cli-secret" not in listed.output
    statuses = {
        line.split()[0]: line.split()[-1] for line in listed.output.splitlines() if line.strip()
    }
    assert statuses["exa_api_key"] == "set"
    assert statuses["mcp_servers"] == "unset"


def test_bundle_writes_a_runnable_artifact(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["bundle", "--out", "bundle"])
    assert result.exit_code == 0, result.output
    assert "extension(s) pinned" in result.output
    out = Path("bundle")
    dockerfile = (out / "Dockerfile").read_text()
    assert "ufo" in dockerfile
    assert 'ENTRYPOINT ["ufoctl"]' in dockerfile
    assert 'CMD ["serve"]' in dockerfile
    assert (out / "ufo.toml").read_text()
    lockfile = (out / "ufo.lock").read_text()
    assert "ufo_version" in lockfile
    assert "memory" in lockfile


def test_ext_search_lists_the_catalog(cli_home: CliRunner) -> None:
    Path("ufo.toml").write_text(EXT_STORE_CONFIG)
    Path("catalog.toml").write_text(CATALOG)
    result = cli_home.invoke(cli.main, ["ext", "search"])
    assert result.exit_code == 0, result.output
    assert "memory" in result.output
    assert "todos" in result.output
    assert "available" in result.output


def test_ext_install_then_remove_round_trips_the_lockfile(cli_home: CliRunner) -> None:
    """`ext install`/`remove` write only the deploy's cwd-local `ufo.lock` (never the venv):
    install pins the installed extension's real digest and search then marks it installed; remove
    drops the pin. The isolated filesystem keeps the lockfile out of the developer's tree."""
    Path("ufo.toml").write_text(EXT_STORE_CONFIG)
    Path("catalog.toml").write_text(CATALOG)

    installed = cli_home.invoke(cli.main, ["ext", "install", "memory"])
    assert installed.exit_code == 0, installed.output
    assert "installed memory" in installed.output
    assert "sha256:" in Path("ufo.lock").read_text()

    searched = cli_home.invoke(cli.main, ["ext", "search"])
    assert "installed" in searched.output

    removed = cli_home.invoke(cli.main, ["ext", "remove", "memory"])
    assert removed.exit_code == 0, removed.output
    assert "removed memory" in removed.output
    assert "memory" not in Path("ufo.lock").read_text()


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
    specs={
        spec.id: replace(spec, client=lambda spec, key: StandInModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


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


async def _bootstrap_workspace() -> UUID:
    """Seed one workspace, its owner (whose email the CLI's bearer names — the `ufo` surface links
    the member on first contact), and the default agent. Returns the workspace id the fixture mints
    the bearer for."""
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await reset_workspace_data(connection)
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
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id


@pytest.fixture
def chat_server(
    dbos_launched_cli: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[CliRunner, str]]:
    """A live in-process server the real `chat` verb talks to: the shared `ufo` surface over the
    session DBOS worker and a StandIn-model runtime, on an ephemeral port the written config names.
    The verb reaches it exactly as it reaches the hosted fleet — a signed member bearer to
    `/surface/ufo/{channel}` — so this exercises the real client wire, not a dedicated shortcut.
    Yields the runner and the config path so verbs run against this same SQLite database."""
    config = dbos_launched_cli
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    init_db(config.database.url)
    workspace_id = asyncio.run(_bootstrap_workspace())

    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=tmp_path / "workspaces",
    )
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=sandboxes,
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(TOKEN_SECRET.encode()),
            dbos=dbos_client,
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            registry=STANDIN_REGISTRY,
            skills=skill_registry(()),
            credentials=None,
            index=DefaultIndex(transaction=workspace_tx),
            embed=StubEmbed(),
            artifact_token_secret="",
        )
    )

    port = _free_port()
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (ufo_manifest(),),
        None,
        blob,
        sandboxes,
        hub,
        dbos_client,
        "",
        None,
        ("auto", "claude-opus-4-8", "claude-sonnet-5"),
        skills=EMPTY_SKILL_REGISTRY,
        user_skills=no_user_skills,
    )
    server = _ThreadedServer(app, port)
    server.start()

    config_path = tmp_path / "ufo.toml"
    config_path.write_text(
        f'[database]\nurl = "{config.database.url}"\n\n'
        f'[blob]\nbackend = "filesystem"\nroot = "{config.blob.root}"\n\n'
        f'[serve]\nhost = "127.0.0.1"\nport = {port}\n'
    )
    monkeypatch.setenv("UFO_CONFIG", str(config_path))
    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / ".ufoctl"))
    (tmp_path / ".ufoctl").mkdir(mode=0o700, exist_ok=True)
    token = mint_token(TOKEN_SECRET, str(workspace_id), OWNER_EMAIL, timedelta(hours=1))
    (tmp_path / ".ufoctl" / "token").write_text(token)

    try:
        yield CliRunner(), str(config_path)
    finally:
        server.stop()
        dbos_client.destroy()
        loop_queue.reset_runtime()
        asyncio.run(dispose_db())


def test_chat_streams_the_answer_then_spend_reports_the_burn(
    chat_server: tuple[CliRunner, str],
) -> None:
    """The headline keyless end-to-end: the real `chat` verb signs into the live shared `ufo`
    surface, admits a turn that runs the full DBOS queue on the StandIn model, and renders the
    streamed `txt` answer from the held directive stream. Disposing the server's engine then lets
    the real `spend` verb open its own connection and read the nonzero burn the turn billed."""
    runner, _config_path = chat_server
    chatted = runner.invoke(cli.main, ["chat", "ping"])
    assert chatted.exit_code == 0, chatted.output
    assert "echo:1" in chatted.output

    asyncio.run(dispose_db())
    spent = runner.invoke(cli.main, ["spend"])
    assert spent.exit_code == 0, spent.output
    assert "$0.000110" in spent.output


def test_migrate_prefers_owner_dsn_env_as_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cluster migrate Job runs as the RLS-bypassing owner: `ufoctl migrate` takes UFO_OWNER_DSN
    over the config's url and normalizes it to the asyncpg driver alembic dials (mirrors the proxy).
    Without the env it falls back to the config's own url."""
    from pathlib import Path

    from ufo.config import BlobConfig, Config, DatabaseConfig

    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
    )
    captured: dict[str, str] = {}
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "apply_migrations", lambda url, pack: captured.update(url=url))

    monkeypatch.setenv("UFO_OWNER_DSN", "postgresql://ufo_owner@rds/ufo")
    assert CliRunner().invoke(cli.main, ["migrate"]).exit_code == 0
    assert captured["url"] == "postgresql+asyncpg://ufo_owner@rds/ufo"

    monkeypatch.delenv("UFO_OWNER_DSN", raising=False)
    assert CliRunner().invoke(cli.main, ["migrate"]).exit_code == 0
    assert captured["url"] == "sqlite+aiosqlite:///tenant.db"
