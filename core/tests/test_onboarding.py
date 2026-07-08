"""The onboarding engine end-to-end: a cold start creates the durable workspace + owner + agent,
requires the chosen model's key, refuses a second run, and runs each installed extension's steps.

The engine tests drive `Onboarding.run` against a fresh db (both dialects) and assert the rows the
turn path consumes. The cold-start test drives the real `ufoctl init` command through Click: a
first run writes the token and durable state, a second run fails loud."""

import asyncio
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from click.testing import CliRunner
from cryptography.fernet import Fernet

from ufo import cli
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.credentials import CredentialSlotUnset, CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore
from ufo.ext.loader import load_manifests
from ufo.ext.manifest import CredentialSlot, Manifest, OnboardingStep
from ufo.onboarding import DEFAULT_AGENT_PROMPT, AlreadyInitialized, Onboarding
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME

OWNER_EMAIL = "owner@example.com"
DEFAULT_MODEL = "claude-opus-4-8"


def _onboarding(
    database_url: str,
    tmp_path: Path,
    credentials: CredentialStore | None = None,
    manifests: tuple = (),
    workspace_id: UUID | None = None,
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
        workspace_id=workspace_id,
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


async def test_onboarding_uses_a_supplied_workspace_id(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control plane mints the workspace uuid and pins the tenant's RLS GUC to it; init takes
    that id verbatim for the workspace row so the INSERT satisfies the policy's WITH CHECK, and the
    owner + default agent attach to it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    pinned = uuid4()
    onboarded = await _onboarding(database_url, tmp_path, workspace_id=pinned).run()
    assert onboarded.workspace_id == pinned
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        member = (await connection.execute(sa.select(tables.member))).one()
        agent = (await connection.execute(sa.select(tables.agent))).one()
    assert workspace_id == pinned
    assert member.workspace_id == pinned
    assert agent.workspace_id == pinned


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
    with pytest.raises(RuntimeError, match="UFO_CREDENTIAL_KEY"):
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
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cli, "UFOCTL_DIR", tmp_path / ".ufoctl")
    runner = CliRunner()
    with runner.isolated_filesystem():
        first = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert first.exit_code == 0, first.output
        assert "workspace ready" in first.output
        assert (tmp_path / ".ufoctl" / "token").read_text()
        second = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert second.exit_code != 0
        assert "already initialized" in second.output


def test_init_workspace_id_option_pins_the_workspace_row(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`ufoctl init --workspace-id <uuid>` threads the supplied id through to the workspace row —
    the producer for the control plane's minted-and-GUC-pinned uuid."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cli, "UFOCTL_DIR", tmp_path / ".ufoctl")
    pinned = uuid4()
    runner = CliRunner()
    with runner.isolated_filesystem():
        result = runner.invoke(
            cli.main, ["init", "--email", OWNER_EMAIL, "--workspace-id", str(pinned)]
        )
        assert result.exit_code == 0, result.output

        async def _read() -> tuple[UUID, UUID, UUID]:
            cli.init_db("sqlite+aiosqlite:///ufo.db")
            try:
                async with workspace_tx() as connection:
                    workspace_id = (
                        await connection.execute(sa.select(tables.workspace.c.id))
                    ).scalar_one()
                    member_ws = (
                        await connection.execute(sa.select(tables.member.c.workspace_id))
                    ).scalar_one()
                    agent_ws = (
                        await connection.execute(sa.select(tables.agent.c.workspace_id))
                    ).scalar_one()
                return workspace_id, member_ws, agent_ws
            finally:
                await cli.dispose_db()

        workspace_id, member_ws, agent_ws = asyncio.run(_read())
    assert workspace_id == pinned
    assert member_ws == pinned
    assert agent_ws == pinned


def test_init_skip_migrations_onboards_without_migrating(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`--skip-migrations` (the shared-database tenant path) onboards against an already-migrated
    schema without re-running migrations — so a pack-narrowed re-migration can't fail to resolve
    another pack's revisions. Migrate once, then init --skip-migrations with `apply_migrations`
    booby-trapped: it must not be called."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setattr(cli, "UFOCTL_DIR", tmp_path / ".ufoctl")
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("ufo.toml").write_text(cli.DEFAULT_CONFIG)
        cli.apply_migrations("sqlite+aiosqlite:///ufo.db", None)

        def _boom(*_args: object, **_kwargs: object) -> None:
            raise AssertionError("apply_migrations must not run under --skip-migrations")

        monkeypatch.setattr(cli, "apply_migrations", _boom)
        result = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL, "--skip-migrations"])
        assert result.exit_code == 0, result.output


def test_cold_start_mints_the_credential_key_for_onboarding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """O1 (zero-config): init without UFO_CREDENTIAL_KEY — even with an active extension whose
    onboarding step needs it (the sample adds one, activated here via an unpinned config that runs
    the full discovered set) — mints the key into `.env` (which `serve` auto-loads), runs the step
    with it, and onboards cleanly. Re-running is idempotent (AlreadyInitialized), not a second
    workspace."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.delenv("UFO_CREDENTIAL_KEY", raising=False)
    monkeypatch.setattr(cli, "UFOCTL_DIR", tmp_path / ".ufoctl")
    runner = CliRunner()
    with runner.isolated_filesystem():
        Path("ufo.toml").write_text(
            '[database]\nurl = "sqlite+aiosqlite:///ufo.db"\n\n'
            '[blob]\nbackend = "filesystem"\nroot = "./blobs"\n'
        )
        first = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert first.exit_code == 0, first.output
        env = Path(".env").read_text()
        minted = next(
            line.split("=", 1)[1].strip()
            for line in env.splitlines()
            if line.startswith("UFO_CREDENTIAL_KEY=")
        )
        Fernet(minted.encode())
        assert (tmp_path / ".ufoctl" / "token").read_text()
        second = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert second.exit_code != 0
