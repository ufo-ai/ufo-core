"""The onboarding engine end-to-end: a cold start creates the durable workspace + owner + agent,
requires the chosen model's key, refuses a second run, and runs each installed extension's steps.

The engine tests drive `Onboarding.run` against a fresh db (both dialects) and assert the rows the
turn path consumes. The cold-start test drives the real `selfhost init` command through Click: a
first run writes the token and durable state, a second run fails loud."""

from pathlib import Path

import pytest
import selfhost_ext_sample as sample
import sqlalchemy as sa
from click.testing import CliRunner
from cryptography.fernet import Fernet

from selfhost import cli
from selfhost.config import BlobConfig, Config, DatabaseConfig
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ScopedStore
from selfhost.ext.loader import load_manifests
from selfhost.onboarding import DEFAULT_AGENT_PROMPT, AlreadyInitialized, Onboarding
from selfhost.schema import tables
from selfhost.schema.records import DEFAULT_AGENT_NAME

OWNER_EMAIL = "owner@example.com"
DEFAULT_MODEL = "claude-opus-4-8"


def _onboarding(
    database_url: str,
    tmp_path: Path,
    credentials: CredentialStore | None = None,
    manifests: tuple = (),
) -> Onboarding:
    return Onboarding(
        config=Config(
            database=DatabaseConfig(url=database_url),
            blob=BlobConfig(backend="filesystem", root=tmp_path),
        ),
        email=OWNER_EMAIL,
        model=DEFAULT_MODEL,
        credentials=credentials,
        manifests=manifests,
    )


async def test_onboarding_creates_the_workspace_owner_and_default_agent(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarded = await _onboarding(database_url, tmp_path).run()
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        member = (await connection.execute(sa.select(tables.member))).one()
        agent = (await connection.execute(sa.select(tables.agent))).one()
    assert workspace_id == onboarded.workspace_id
    assert (member.id, member.email, member.workspace_id) == (
        onboarded.member_id,
        OWNER_EMAIL,
        onboarded.workspace_id,
    )
    assert (agent.name, agent.model, agent.prompt) == (
        DEFAULT_AGENT_NAME,
        DEFAULT_MODEL,
        DEFAULT_AGENT_PROMPT,
    )
    assert agent.workspace_id == onboarded.workspace_id


async def test_re_running_against_an_initialized_workspace_fails_loud(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarding = _onboarding(database_url, tmp_path)
    await onboarding.run()
    with pytest.raises(AlreadyInitialized, match="already initialized"):
        await onboarding.run()
    async with workspace_tx() as connection:
        members = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.member))
        ).scalar_one()
    assert members == 1


async def test_onboarding_requires_the_chosen_models_key_before_touching_the_db(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"):
        await _onboarding(database_url, tmp_path).run()
    async with workspace_tx() as connection:
        workspaces = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
    assert workspaces == 0


async def test_onboarding_runs_each_installed_extensions_steps(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    onboarded = await _onboarding(
        database_url, tmp_path, credentials=store, manifests=(manifest,)
    ).run()
    scoped = ScopedStore(workspace_id=onboarded.workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.ONBOARDING_KEY) == {"onboarded": True}


def test_cold_start_init_creates_durable_state_and_then_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("SELFHOST_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cli, "SELFHOST_DIR", tmp_path / ".selfhost")
    runner = CliRunner()
    with runner.isolated_filesystem():
        first = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert first.exit_code == 0, first.output
        assert "workspace ready" in first.output
        assert (tmp_path / ".selfhost" / "token").read_text()
        second = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert second.exit_code != 0
        assert "already initialized" in second.output
