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
from selfhost.credentials import CredentialSlotUnset, CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import ExtensionContext, ScopedStore
from selfhost.ext.loader import load_manifests
from selfhost.ext.manifest import CredentialSlot, Manifest, OnboardingStep
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


async def test_onboarding_seeds_declared_slots_the_environment_provides(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("SEEDED_API_KEY", "seeded-value")
    monkeypatch.delenv("UNSEEDED_API_KEY", raising=False)
    manifest = Manifest(
        name="seeding_ext",
        version="0.1.0",
        credentials=(
            CredentialSlot(name="seeded_api_key", description="seeded from env"),
            CredentialSlot(name="unseeded_api_key", description="absent from env"),
        ),
    )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    onboarded = await _onboarding(
        database_url, tmp_path, credentials=store, manifests=(manifest,)
    ).run()
    assert await store.get(onboarded.workspace_id, "seeded_api_key") == "seeded-value"
    with pytest.raises(CredentialSlotUnset):
        await store.get(onboarded.workspace_id, "unseeded_api_key")


async def test_an_environment_credential_without_a_key_fails_before_the_db(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("SEEDED_API_KEY", "seeded-value")
    manifest = Manifest(
        name="seeding_ext",
        version="0.1.0",
        credentials=(CredentialSlot(name="seeded_api_key", description="seeded from env"),),
    )
    with pytest.raises(RuntimeError, match="SELFHOST_CREDENTIAL_KEY"):
        await _onboarding(database_url, tmp_path, credentials=None, manifests=(manifest,)).run()
    async with workspace_tx() as connection:
        workspaces = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
    assert workspaces == 0


async def test_a_failing_onboarding_step_is_isolated_from_its_siblings(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))

    async def _boom(ctx: ExtensionContext) -> None:
        raise RuntimeError("bad extension onboarding step")

    async def _record(ctx: ExtensionContext) -> None:
        await ctx.store.put("recorded", {"ran": True})

    failing = Manifest(
        name="failing_ext",
        version="0.1.0",
        onboarding_steps=(OnboardingStep(name="boom", handler=_boom),),
    )
    working = Manifest(
        name="working_ext",
        version="0.1.0",
        onboarding_steps=(OnboardingStep(name="ok", handler=_record),),
    )
    onboarded = await _onboarding(
        database_url, tmp_path, credentials=store, manifests=(failing, working)
    ).run()
    scoped = ScopedStore(workspace_id=onboarded.workspace_id, extension="working_ext")
    assert await scoped.get("recorded") == {"ran": True}


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


def test_cold_start_without_credential_key_fails_cleanly_then_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O1: init without SELFHOST_CREDENTIAL_KEY, when an active extension's onboarding step needs it
    (the sample adds one, activated here via an unpinned config that runs the full discovered set),
    must fail loud BEFORE creating anything, leaving no wedged half-onboarded workspace — setting
    the key and re-running then succeeds, not hitting AlreadyInitialized."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.delenv("SELFHOST_CREDENTIAL_KEY", raising=False)
    monkeypatch.setattr(cli, "SELFHOST_DIR", tmp_path / ".selfhost")
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("selfhost.toml").write_text(
            '[database]\nurl = "sqlite+aiosqlite:///selfhost.db"\n\n'
            '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
        )
        first = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert first.exit_code != 0
        assert "SELFHOST_CREDENTIAL_KEY" in first.output
        assert not (tmp_path / ".selfhost" / "token").exists()
        monkeypatch.setenv("SELFHOST_CREDENTIAL_KEY", Fernet.generate_key().decode())
        recovered = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert recovered.exit_code == 0, recovered.output
        assert (tmp_path / ".selfhost" / "token").read_text()
