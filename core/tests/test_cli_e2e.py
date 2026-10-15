"""The operator CLI end to end through its real Click verbs, keyless.

Every verb runs through `CliRunner` against `ufo.cli`, a real database, and the real
filesystem — no mocks of the CLI, the loader, the database, or the ledger. The paid dependency, the
model, is the only double: the wire turn runs against a StandIn model client (mirroring
`test_turn_lifecycle`'s registry) served by a real in-process uvicorn server, so a `ping` streams a
real terminal frame and bills a real ledger row without an API key. The DB-owning verbs
(`spend-cap`, `spend`, `grants`, `bundle`, `ext`) each manage their own connection, so they run
in an isolated filesystem with no persistent engine; the wire turn holds a persistent engine and the
session's DBOS worker, so its ledger read disposes that engine first and lets the `spend` verb open
its own — the same lifecycle boundary the real process has between `serve` and a one-shot verb."""

import asyncio
import json
import os
import shutil
import socket
import threading
import time
import tomllib
from collections.abc import AsyncIterator, Iterator
from dataclasses import replace
from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import uvicorn
from click.testing import CliRunner
from cryptography.fernet import Fernet
from fastapi import FastAPI
from sqlalchemy.engine import make_url
from ufo_ext_context_rollover.manifest import manifest as rollover_manifest
from ufo_ext_ufo.manifest import manifest as ufo_manifest
from ufo_testsupport.index import default_index
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.surfaces import (
    EMPTY_SKILL_REGISTRY,
    UNREACHED_AMBIENT_REPLY,
    no_member_skills,
)
from ufo_testsupport.tables import reset_workspace_data

from ufo import bundle, cli
from ufo.blob import FilesystemBlobStore
from ufo.config import Config, DatabaseConfig, load_config
from ufo.db import dispose_db, init_db, workspace_tx
from ufo.harness.auth.bearer import mint_token
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import ANTHROPIC_KEY_SLOT, CORE_MODEL_SPECS, CORE_PRICING
from ufo.harness.models.interface import ModelEvent, ModelRequest, TextDelta
from ufo.harness.models.pricing import MICRO_USD_PER_USD
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.sandbox.conversation import SANDBOX_IMAGE_REF, ConversationSandbox
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialStore, member_slot
from ufo.runtime.hub import InProcessHub
from ufo.runtime.subagents import SubagentRegistry
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, Usage
from ufo.serve import _mount_shared_surfaces

OWNER_EMAIL = "owner@example.com"
TOKEN_SECRET = "cli-e2e-token-secret"
SERVER_START_TIMEOUT_SECONDS = 10.0
SERVER_POLL_SECONDS = 0.02
SERVER_STOP_GRACE_SECONDS = 5
SERVER_STOP_TIMEOUT_SECONDS = 20.0
STOP_FLOOR_SECONDS = 3.0
STOP_DEADLINE_SECONDS = 8.0
REGISTERED_TIMEOUT_SECONDS = 5.0
WIRE_TURN_TIMEOUT_SECONDS = 90.0
WIRE_RECONNECT_LIMIT = 30
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
    monkeypatch.delenv("UFO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("UFO_OPENAI_API_KEY", raising=False)
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
    monkeypatch.delenv("UFO_CREDENTIAL_KEY")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert ".env" in result.output
    env = _dotenv(Path(".env").read_text())
    Fernet(env["UFO_CREDENTIAL_KEY"].encode())
    assert env["UFO_ARTIFACT_TOKEN_SECRET"]


def test_init_names_a_declared_deploy_key_the_environment_lacks(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert "UFO_OPENAI_API_KEY is unset" in result.output


def test_init_names_an_empty_deploy_key_in_dotenv(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    Path(".env").write_text("UFO_OPENAI_API_KEY=\n")
    monkeypatch.setenv("UFO_OPENAI_API_KEY", "outer-openai")

    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])

    assert result.exit_code == 0, result.output
    assert "UFO_OPENAI_API_KEY is unset" in result.output


def test_init_names_the_broker_and_model_keys_the_environment_lacks(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    names = (
        "COMPOSIO_API_KEY",
        "PIPEDREAM_CLIENT_ID",
        "PIPEDREAM_CLIENT_SECRET",
        "PIPEDREAM_PROJECT_ID",
        "OPENROUTER_API_KEY",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)
        monkeypatch.delenv(f"UFO_{name}", raising=False)
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    for name in names:
        assert f"UFO_{name} is unset" in result.output


@pytest.mark.parametrize("name", ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"))
def test_a_bare_provider_key_in_dotenv_is_refused(name: str, cli_home: CliRunner) -> None:
    """Every tool reading `.env` picks up the bare provider names, so ufo's own config surface
    refuses them before any verb runs — the key it should hold is the `UFO_`-prefixed one."""
    Path(".env").write_text(f"{name}=sk-bare\n")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code != 0
    assert f"rename it to UFO_{name}" in result.output


def test_init_runs_on_the_ufo_prefixed_model_key_alone(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "sk-ufo-scoped")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output


def test_init_seeds_the_owners_private_model_key(cli_home: CliRunner) -> None:
    result = cli_home.invoke(
        cli.main,
        [
            "init",
            "--email",
            OWNER_EMAIL,
            "--member-model-provider",
            "anthropic",
        ],
    )
    assert result.exit_code == 0, result.output

    config = load_config()
    init_db(config.database.url)

    async def check() -> None:
        async with workspace_tx() as connection:
            workspace_id, member_id = (
                await connection.execute(
                    sa.select(tables.workspace.c.id, tables.member.c.id).join(
                        tables.member,
                        tables.member.c.workspace_id == tables.workspace.c.id,
                    )
                )
            ).one()
        store = CredentialStore(fernet=Fernet(os.environ["UFO_CREDENTIAL_KEY"].encode()))
        init_workspace_credentials(store)
        try:
            assert (
                await store.get(workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member_id))
                == "sk-test-cli"
            )
            with ws(workspace_id):
                assert await ws_current().member_model_accounts(member_id) == (
                    ("anthropic", member_slot(ANTHROPIC_KEY_SLOT, member_id)),
                )
        finally:
            init_workspace_credentials(None)

    try:
        asyncio.run(check())
    finally:
        asyncio.run(dispose_db())


def test_init_reads_the_ufo_prefixed_model_key_from_dotenv(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole client chain: `.env` autoloads on every verb, and the model-key check resolves
    the `UFO_`-prefixed name it finds there."""
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    Path(".env").write_text("UFO_ANTHROPIC_API_KEY=sk-ufo-file\n")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output


def test_init_stays_quiet_about_a_deploy_key_already_set(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-present")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY is unset" not in result.output


def test_init_counts_the_ufo_prefixed_form_as_the_deploy_key(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("UFO_OPENAI_API_KEY", "sk-ufo-scoped")
    result = cli_home.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY is unset" not in result.output


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


TWO_WORKSPACE_CONFIG = """\
[database]
url = "{url}"

[blob]
backend = "filesystem"
root = "./blobs"

[pack]
name = "assistant"
"""
WORKSPACE_VERBS = (
    ("spend-cap", "list"),
    ("spend",),
    ("transcript-reads",),
    ("grants",),
    ("credential", "list"),
)
THEIR_EMAIL = "admin@theirs.example"
THEIR_ACCOUNT = "their-github-account"
THEIR_SPEND_MICRO_USD = 1_000


async def _seed_two_workspaces(url: str) -> tuple[UUID, UUID, UUID]:
    mine, theirs, their_turn = uuid4(), uuid4(), uuid4()
    agent_id, conversation_id, member_id, connection_id = uuid4(), uuid4(), uuid4(), uuid4()
    init_db(url)
    try:
        async with workspace_tx() as connection:
            await reset_workspace_data(connection)
            for workspace_id in (mine, theirs):
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
            await connection.execute(
                sa.insert(tables.agent).values(
                    id=agent_id,
                    workspace_id=theirs,
                    name="assistant",
                    prompt="p",
                    model="claude-opus-4-8",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=theirs,
                    agent_id=agent_id,
                    surface="cli",
                    queue_key="session",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=their_turn,
                    workspace_id=theirs,
                    conversation_id=conversation_id,
                    agent_id=agent_id,
                    seq=1,
                    status="running",
                    inbound="hello",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=theirs,
                    email=THEIR_EMAIL,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.transcript_access).values(
                    id=uuid4(),
                    workspace_id=theirs,
                    conversation_id=conversation_id,
                    reader_member_id=member_id,
                    subject_member_id=member_id,
                    created_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=connection_id,
                    workspace_id=theirs,
                    provider="github",
                    account_id=THEIR_ACCOUNT,
                    host="",
                    shared=True,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=theirs,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.ledger).values(
                    id=uuid4(),
                    workspace_id=theirs,
                    turn_id=their_turn,
                    dimension="tokens",
                    amount=10,
                    prompt_tokens=10,
                    input_tokens=10,
                    priced_micro_usd=THEIR_SPEND_MICRO_USD,
                    model="claude-opus-4-8",
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        return mine, theirs, their_turn
    finally:
        await dispose_db()


@pytest.fixture
def two_workspaces(
    cli_home: CliRunner, database_url: str, tmp_path: Path
) -> tuple[CliRunner, UUID, UUID, UUID]:
    url = database_url
    if make_url(database_url).get_backend_name() == "sqlite":
        private = tmp_path / "two_workspaces.db"
        shutil.copy(make_url(database_url).database, private)
        url = f"sqlite+aiosqlite:///{private}"
    Path("ufo.toml").write_text(TWO_WORKSPACE_CONFIG.format(url=url))
    return cli_home, *asyncio.run(_seed_two_workspaces(url))


def test_every_workspace_verb_names_one_of_several_and_reads_only_it(
    two_workspaces: tuple[CliRunner, UUID, UUID, UUID],
) -> None:
    runner, mine, theirs, _ = two_workspaces
    for verb in WORKSPACE_VERBS:
        bare = runner.invoke(cli.main, list(verb))
        assert bare.exit_code != 0, verb
        assert "this deploy serves 2 workspaces; name one with --workspace-id" in bare.output
        named = runner.invoke(cli.main, [*verb, "--workspace-id", str(mine)])
        assert named.exit_code == 0, named.output

    their_spend = f"${THEIR_SPEND_MICRO_USD / MICRO_USD_PER_USD:,.6f}"
    for verb, held, empty in (
        ("spend", their_spend, "$0.000000"),
        ("transcript-reads", THEIR_EMAIL, "no transcript reads recorded"),
        ("grants", THEIR_ACCOUNT, "no grants"),
    ):
        their_output = runner.invoke(cli.main, [verb, "--workspace-id", str(theirs)]).output
        my_output = runner.invoke(cli.main, [verb, "--workspace-id", str(mine)]).output
        assert held in their_output, their_output
        assert held not in my_output, my_output
        assert empty in my_output, my_output

    cap = ["--scope", "workspace", "--window-seconds", "3600", "--limit-micro-usd", "5000000"]
    created = runner.invoke(cli.main, ["spend-cap", "set", *cap, "--workspace-id", str(theirs)])
    assert created.exit_code == 0, created.output
    their_caps = runner.invoke(cli.main, ["spend-cap", "list", "--workspace-id", str(theirs)])
    assert "3600s" in their_caps.output
    my_caps = runner.invoke(cli.main, ["spend-cap", "list", "--workspace-id", str(mine)])
    assert my_caps.output.strip() == "no spend caps set"

    stored = runner.invoke(
        cli.main,
        ["credential", "set", "perplexity_api_key", "--workspace-id", str(theirs)],
        input="their-secret\n",
    )
    assert stored.exit_code == 0, stored.output
    for workspace_id, status in ((theirs, "set"), (mine, "unset")):
        listed = runner.invoke(
            cli.main, ["credential", "list", "--workspace-id", str(workspace_id)]
        )
        slots = {line.split()[0]: line.split()[-1] for line in listed.output.splitlines()}
        assert slots["perplexity_api_key"] == status

    stranger = uuid4()
    missing = runner.invoke(cli.main, ["spend", "--workspace-id", str(stranger)])
    assert missing.exit_code != 0
    assert f"no workspace {stranger}" in missing.output


def test_turn_verbs_refuse_a_turn_of_another_workspace(
    two_workspaces: tuple[CliRunner, UUID, UUID, UUID],
) -> None:
    runner, mine, _, their_turn = two_workspaces
    for verb in ("cancel", "steps"):
        refused = runner.invoke(
            cli.main, ["turn", verb, str(their_turn), "--workspace-id", str(mine)]
        )
        assert refused.exit_code != 0, verb
        assert f"no turn {their_turn} in workspace {mine}" in refused.output


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


def test_transcript_reads_lists_none_when_no_disclosure_is_recorded(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["transcript-reads"])
    assert result.exit_code == 0, result.output
    assert "no transcript reads recorded" in result.output


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
        cli.main, ["credential", "set", "perplexity_api_key"], input="perplexity-cli-secret\n"
    )
    assert result.exit_code == 0, result.output
    assert "perplexity-cli-secret" not in result.output
    assert asyncio.run(_read_credential("perplexity_api_key")) == "perplexity-cli-secret"


def test_init_does_not_seed_platform_credentials_into_the_store(
    cli_home: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform default the environment provides is read live at use, never copied into the
    workspace at init — so the store holds no seeded perplexity_api_key."""
    monkeypatch.setenv("PERPLEXITY_API_KEY", "perplexity-env-seeded")
    _init(cli_home)
    with pytest.raises(CredentialSlotUnset):
        asyncio.run(_read_credential("perplexity_api_key"))


def test_credential_set_rejects_an_undeclared_slot(cli_home: CliRunner) -> None:
    _init(cli_home)
    result = cli_home.invoke(cli.main, ["credential", "set", "no_such_slot"], input="value\n")
    assert result.exit_code != 0
    assert "perplexity_api_key" in result.output


def test_credential_list_reports_set_and_unset_without_values(cli_home: CliRunner) -> None:
    _init(cli_home)
    setting = cli_home.invoke(
        cli.main, ["credential", "set", "perplexity_api_key"], input="perplexity-cli-secret\n"
    )
    assert setting.exit_code == 0, setting.output
    listed = cli_home.invoke(cli.main, ["credential", "list"])
    assert listed.exit_code == 0, listed.output
    assert "perplexity-cli-secret" not in listed.output
    statuses = {
        line.split()[0]: line.split()[-1] for line in listed.output.splitlines() if line.strip()
    }
    assert statuses["perplexity_api_key"] == "set"
    assert statuses["datadog_api_key"] == "unset"


def test_bundle_writes_a_runnable_artifact(cli_home: CliRunner) -> None:
    _init(cli_home)
    client = Path("ufo-sandbox-client")
    client.write_bytes(b"client")
    result = cli_home.invoke(
        cli.main,
        ["bundle", "--out", "bundle", "--client-binary", str(client)],
    )
    assert result.exit_code == 0, result.output
    assert "extension(s) pinned" in result.output
    out = Path("bundle")
    dockerfile = (out / "Dockerfile").read_text()
    assert (out / bundle.wheel_name()).is_file()
    assert bundle.wheel_name() in dockerfile
    assert 'ENTRYPOINT ["ufoctl"]' in dockerfile
    assert 'CMD ["serve"]' in dockerfile
    assert (out / "ufo.toml").read_text()
    assert (out / "ufo-sandbox-client").read_bytes() == b"client"
    lockfile = (out / "ufo.lock").read_text()
    assert "ufo_version" in lockfile
    assert "memory" in lockfile


def test_bundle_requires_a_sandbox_client(cli_home: CliRunner) -> None:
    result = cli_home.invoke(cli.main, ["bundle", "--out", "bundle"])
    assert result.exit_code == 2
    assert "Missing option '--client-binary'" in result.output


def test_ext_search_lists_the_catalog(cli_home: CliRunner) -> None:
    Path("ufo.toml").write_text(EXT_STORE_CONFIG)
    Path("catalog.toml").write_text(CATALOG)
    result = cli_home.invoke(cli.main, ["ext", "search"])
    assert result.exit_code == 0, result.output
    assert "memory" in result.output
    assert "todos" in result.output
    assert "available" in result.output


def test_ext_install_then_remove_round_trips_the_lockfile(cli_home: CliRunner) -> None:
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
    def __init__(self, app: FastAPI, port: int) -> None:
        self._server = uvicorn.Server(
            uvicorn.Config(
                app,
                host="127.0.0.1",
                port=port,
                log_level="warning",
                timeout_graceful_shutdown=SERVER_STOP_GRACE_SECONDS,
            )
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
        if self._thread.is_alive():
            self._thread.join(timeout=SERVER_STOP_TIMEOUT_SECONDS)

    @property
    def stopped(self) -> bool:
        return not self._thread.is_alive()


def test_a_connection_still_open_at_stop_does_not_hold_the_threaded_server() -> None:
    entered = threading.Event()
    app = FastAPI()

    @app.get("/never")
    async def never() -> None:
        entered.set()
        await asyncio.Event().wait()

    port = _free_port()
    server = _ThreadedServer(app, port)
    held = socket.socket()
    try:
        server.start()
        held.connect(("127.0.0.1", port))
        held.sendall(b"GET /never HTTP/1.1\r\nHost: x\r\n\r\n")
        assert entered.wait(REGISTERED_TIMEOUT_SECONDS), "the handler never ran"
        started = time.monotonic()
        server.stop()
        waited = time.monotonic() - started
    finally:
        server.stop()
        held.close()
    assert server.stopped
    assert waited >= SERVER_STOP_GRACE_SECONDS, waited
    assert STOP_FLOOR_SECONDS <= waited < STOP_DEADLINE_SECONDS, waited


async def _bootstrap_workspace() -> UUID:
    """Seed one workspace, its owner (whose email the CLI's bearer names — the `ufo` surface
    links the member on first contact), and the default agent."""
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
def wire_server(
    dbos_launched_cli: Config, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[CliRunner, str]]:
    session_config = dbos_launched_cli
    config = session_config
    if session_config.database.url.startswith("sqlite"):
        private = tmp_path / "private.db"
        shutil.copy(make_url(session_config.database.url).database, private)
        config = session_config.model_copy(
            update={
                "database": DatabaseConfig(
                    url=f"sqlite+aiosqlite:///{private}",
                    system_url=session_config.database.system_url,
                )
            }
        )
    monkeypatch.setenv("UFO_TOKEN_SECRET", TOKEN_SECRET)
    init_db(config.database.url)
    server = None
    dbos_client = None
    try:
        workspace_id = asyncio.run(_bootstrap_workspace())

        hub = InProcessHub()
        blob = FilesystemBlobStore(root=config.blob.root)
        dbos_client = replay_safe_client(config.database.system_url)
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
                invoker_for=invoker_factory(dbos_client),
                subagents=SubagentRegistry(()),
                subagent_grants={},
                manifests=(rollover_manifest(),),
                environment=HostEnvironment(
                    manifests=(rollover_manifest(),),
                    credentials=None,
                    index=default_index(),
                    embed=StubEmbed(),
                ),
                registry=STANDIN_REGISTRY,
                skills=skill_registry(()),
                credentials=None,
                index=default_index(),
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
            None,
            ("auto", "claude-opus-4-8", "claude-sonnet-5"),
            ambient_reply=UNREACHED_AMBIENT_REPLY,
            skills=EMPTY_SKILL_REGISTRY,
            member_skill_listing=no_member_skills,
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

        yield CliRunner(), str(config_path)
    finally:
        stopped = True
        if server is not None:
            server.stop()
            stopped = server.stopped
        if dbos_client is not None:
            dbos_client.destroy()
        loop_queue.reset_runtime()
        asyncio.run(dispose_db())
        assert stopped, (
            f"uvicorn server did not stop within {SERVER_STOP_TIMEOUT_SECONDS}s of should_exit"
        )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
def test_the_wire_streams_the_answer_then_spend_reports_the_burn(
    wire_server: tuple[CliRunner, str],
) -> None:
    runner, config_path = wire_server
    serve = tomllib.loads(Path(config_path).read_text())["serve"]
    token = (Path(os.environ["UFOCTL_DIR"]) / "token").read_text().strip()
    endpoint = f"http://{serve['host']}:{serve['port']}/surface/ufo/{uuid4().hex}"
    headers = {"authorization": f"Bearer {token}", "content-type": "text/plain"}
    lines: list[str] = []
    body = "ping"
    with httpx.Client(timeout=WIRE_TURN_TIMEOUT_SECONDS) as client:
        for _reconnect in range(WIRE_RECONNECT_LIMIT):
            response = client.post(endpoint, content=body.encode(), headers=headers)
            assert response.status_code == 200, response.text
            lines.extend(response.text.splitlines())
            verbs = {line.split("\t", 1)[0] for line in lines}
            if verbs & {"ask", "exit"}:
                break
            body = ""
        else:
            pytest.fail(f"stream never capped: {lines}")
    answer = "".join(
        json.loads(line.split("\t", 2)[2])["text"]
        for line in lines
        if line.startswith("frame\tmessage\t")
    )
    assert "echo:1" in answer

    asyncio.run(dispose_db())
    spent = runner.invoke(cli.main, ["spend"])
    assert spent.exit_code == 0, spent.output
    assert "$0.000110" in spent.output
    assert f"by service:\n  {'models':<32}$0.000110\n" in spent.output


def test_migrate_prefers_owner_dsn_env_as_asyncpg(monkeypatch: pytest.MonkeyPatch) -> None:
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


def _second_workspace() -> UUID:
    workspace_id = uuid4()

    async def _insert() -> None:
        init_db("sqlite+aiosqlite:///ufo.db")
        try:
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.insert(tables.workspace).values(
                        id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
                    )
                )
        finally:
            await dispose_db()

    asyncio.run(_insert())
    return workspace_id


def test_workspace_verbs_initialize_the_owner_database_before_reading_across_workspaces(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_target_workspace` reads the workspace table through `owner_tx`."""
    from ufo.config import BlobConfig

    config = Config(
        database=DatabaseConfig(url="sqlite+aiosqlite:///tenant.db"),
        blob=BlobConfig(backend="filesystem", root=Path("/tmp/blobs")),
    )
    owner_urls: list[str] = []
    monkeypatch.setattr(cli, "load_config", lambda: config)
    monkeypatch.setattr(cli, "init_db", lambda url: None)
    monkeypatch.setattr(cli, "init_owner_db", owner_urls.append)
    monkeypatch.setattr(cli, "dispose_db", _noop_dispose)
    monkeypatch.setattr(cli, "_target_workspace", _unreached_target)
    monkeypatch.setenv("UFO_OWNER_DSN", "postgresql://ufo_owner@rds/ufo")

    for argv in (["spend-cap", "list"], ["credential", "list"]):
        owner_urls.clear()
        CliRunner().invoke(cli.main, argv)
        assert owner_urls == ["postgresql://ufo_owner@rds/ufo"], argv


def test_turn_cancel_requires_a_workspace_when_the_owner_database_is_unavailable(
    cli_home: CliRunner,
) -> None:
    _init(cli_home)
    config = Path("ufo.toml")
    config.write_text(config.read_text().replace('owner_url = "sqlite+aiosqlite:///ufo.db"\n', ""))
    turn_id = uuid4()

    bare = cli_home.invoke(cli.main, ["turn", "cancel", str(turn_id)])
    assert bare.exit_code != 0
    assert "--workspace-id" in bare.output

    workspace_id = _second_workspace()
    scoped = cli_home.invoke(
        cli.main,
        ["turn", "cancel", str(turn_id), "--workspace-id", str(workspace_id)],
    )
    assert scoped.exit_code != 0
    assert f"no turn {turn_id} in workspace {workspace_id}" in scoped.output


async def _noop_dispose() -> None:
    return None


async def _unreached_target(named: str) -> UUID:
    raise RuntimeError("stop after the owner database is initialized")
