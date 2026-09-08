"""The onboarding engine end-to-end: a cold start creates the workspace, initial admin, and main
agent, requires the chosen model's key, refuses a second run, and runs installed extension steps.

The engine tests drive `Onboarding.run` against a fresh db (both dialects) and assert the rows the
turn path consumes. The cold-start test drives the real `ufoctl init` command through Click: a
first run writes the token and durable state, a second run fails loud."""

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from click.testing import CliRunner
from cryptography.fernet import Fernet
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

from ufo import cli
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.models.interface import AUTO_MODEL
from ufo.host.ext import loader
from ufo.host.ext.loader import load_manifests
from ufo.onboard.onboarding import (
    DEFAULT_AGENT_MODEL,
    DEFAULT_AGENT_PROMPT,
    AlreadyInitialized,
    Onboarding,
)
from ufo.product import INIT_SURFACE, ONBOARDING_STEP_METRIC, STEP_COMPLETED, STEP_FAILED
from ufo.runtime.access.credentials import CredentialSlotUnset, CredentialStore
from ufo.runtime.billing.balance import read_balance
from ufo.runtime.ext.context import CredentialAccess, ExtensionContext, ScopedStore
from ufo.runtime.ext.manifest import CredentialSlot, Manifest, OnboardingStep
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, MAIN_AGENT_ICON, ReasoningEffort

OWNER_EMAIL = "owner@example.com"
DEFAULT_MODEL = "claude-opus-4-8"


def _onboarding(
    database_url: str,
    tmp_path: Path,
    credentials: CredentialStore | None = None,
    manifests: tuple = (),
    reasoning: ReasoningEffort = "auto",
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
        reasoning=reasoning,
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_creates_the_initial_admin_and_main_agent(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarded = await _onboarding(database_url, tmp_path, reasoning="high").run()
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
    assert member.is_admin
    assert (agent.name, agent.model, agent.prompt, agent.reasoning) == (
        DEFAULT_AGENT_NAME,
        DEFAULT_MODEL,
        DEFAULT_AGENT_PROMPT,
        "high",
    )
    assert agent.workspace_id == onboarded.workspace_id
    assert agent.is_main
    assert agent.visibility == "workspace"
    assert agent.icon == MAIN_AGENT_ICON


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_accepts_the_ufo_prefixed_model_key(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("UFO_ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarded = await _onboarding(database_url, tmp_path).run()
    assert onboarded.member_id is not None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_requires_the_chosen_models_key_before_touching_the_db(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("UFO_ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="UFO_ANTHROPIC_API_KEY"):
        await _onboarding(database_url, tmp_path).run()
    async with workspace_tx() as connection:
        workspaces = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
    assert workspaces == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_default_agent_defers_its_model_to_the_deploy_knob(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The agent onboarding creates carries the `auto` sentinel, not a model id, so
    `models.auto_model` is the one place a deploy names its model — a concrete id here would pin
    every new workspace past the knob. The key check still resolves the sentinel, because the key
    the first turn needs belongs to the model that turn actually runs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarding = Onboarding(
        config=Config(
            database=DatabaseConfig(url=database_url),
            blob=BlobConfig(backend="filesystem", root=tmp_path),
        ),
        email=OWNER_EMAIL,
        model=DEFAULT_AGENT_MODEL,
        credentials=None,
        manifests=(),
    )
    await onboarding.run()
    async with workspace_tx() as connection:
        agent = (await connection.execute(sa.select(tables.agent))).one()
    assert agent.model == AUTO_MODEL
    assert onboarding.config.models.auto_model != AUTO_MODEL


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_runs_each_installed_extensions_steps(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    onboarded = await _onboarding(
        database_url, tmp_path, credentials=store, manifests=(manifest,)
    ).run()
    with ws(onboarded.workspace_id):
        scoped = ScopedStore(extension=sample.NAME)
        assert await scoped.get(sample.ONBOARDING_KEY) == {"onboarded": True}


def test_third_party_extension_cannot_declare_privileged_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = SimpleNamespace(
        load=lambda: lambda: Manifest(name="outside", version="1", member_context_read=True),
        dist=SimpleNamespace(name="outside-package"),
    )
    monkeypatch.setattr(loader, "entry_points", lambda group: (entry,))
    with pytest.raises(ValueError, match="third-party extension"):
        loader.discovered()


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_platform_credential_is_read_live_not_seeded(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A platform credential the environment provides is read live through CredentialAccess, never
    copied into the workspace at onboarding — so rotating the deploy's value reaches the workspace
    with no re-seed, and a per-workspace override still wins."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("SEEDED_API_KEY", "platform-value")
    monkeypatch.delenv("UNSEEDED_API_KEY", raising=False)
    manifest = Manifest(
        name="platform_ext",
        version="0.1.0",
        credentials=(
            CredentialSlot(name="seeded_api_key", description="platform default from env"),
            CredentialSlot(name="unseeded_api_key", description="absent from env and store"),
        ),
    )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    onboarded = await _onboarding(
        database_url, tmp_path, credentials=store, manifests=(manifest,)
    ).run()
    with pytest.raises(CredentialSlotUnset):
        await store.get(onboarded.workspace_id, "seeded_api_key")
    init_workspace_credentials(store)
    access = CredentialAccess(declared=frozenset({"seeded_api_key", "unseeded_api_key"}))
    with ws(onboarded.workspace_id):
        assert await access.get("seeded_api_key") == "platform-value"
        monkeypatch.setenv("SEEDED_API_KEY", "rotated-value")
        assert await access.get("seeded_api_key") == "rotated-value"
        await store.put(onboarded.workspace_id, "seeded_api_key", "workspace-byok")
        assert await access.get("seeded_api_key") == "workspace-byok"
        with pytest.raises(CredentialSlotUnset):
            await access.get("unseeded_api_key")


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_steps_without_a_credential_key_fail_before_the_db(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An extension contributing onboarding steps still needs the credential key (a step may store a
    per-workspace secret); the guard fails before the DB, leaving no half-created workspace."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    with pytest.raises(RuntimeError, match="UFO_CREDENTIAL_KEY"):
        await _onboarding(database_url, tmp_path, credentials=None, manifests=(manifest,)).run()
    async with workspace_tx() as connection:
        workspaces = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.workspace))
        ).scalar_one()
    assert workspaces == 0


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
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
    with ws(onboarded.workspace_id):
        scoped = ScopedStore(extension="working_ext")
        assert await scoped.get("recorded") == {"ran": True}


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_each_onboarding_step_counts_its_outcome_as_an_onboarding_step(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A step that raised is isolated, which means the workspace carries on without what the step
    was for. Nothing but this count says so: the board reads the failure beside the steps a member
    completed, in the same funnel."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    reader = InMemoryMetricReader()
    monkeypatch.setattr(o11y.metrics, "get_meter", MeterProvider(metric_readers=[reader]).get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    monkeypatch.setattr(o11y, "_histograms", {})
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))

    async def _boom(ctx: ExtensionContext) -> None:
        raise RuntimeError("bad extension onboarding step")

    async def _fine(ctx: ExtensionContext) -> None:
        return None

    manifest = Manifest(
        name="stepped_ext",
        version="0.1.0",
        onboarding_steps=(
            OnboardingStep(name="boom", handler=_boom),
            OnboardingStep(name="ok", handler=_fine),
        ),
    )
    await _onboarding(database_url, tmp_path, credentials=store, manifests=(manifest,)).run()

    data = reader.get_metrics_data()
    assert data is not None
    counted = {
        (point.attributes["step"], point.attributes["status"], point.attributes["surface"])
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == f"ufo.{ONBOARDING_STEP_METRIC}"
        for point in metric.data.data_points
    }
    assert counted == {
        ("stepped_ext/boom", STEP_FAILED, INIT_SURFACE),
        ("stepped_ext/ok", STEP_COMPLETED, INIT_SURFACE),
    }


def test_cold_start_init_creates_durable_state_and_then_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    monkeypatch.setenv("UFO_CREDENTIAL_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / ".ufoctl"))
    runner = CliRunner()
    with runner.isolated_filesystem():
        first = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL, "--reasoning", "high"])
        assert first.exit_code == 0, first.output
        assert "workspace ready" in first.output
        assert "high reasoning" in first.output
        with sqlite3.connect("ufo.db") as connection:
            assert connection.execute("select reasoning from agent").fetchone() == ("high",)
        assert (tmp_path / ".ufoctl" / "token").read_text()
        second = runner.invoke(cli.main, ["init", "--email", OWNER_EMAIL])
        assert second.exit_code != 0
        assert "already initialized" in second.output


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
    monkeypatch.setenv("UFOCTL_DIR", str(tmp_path / ".ufoctl"))
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


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_self_host_init_creates_no_balance(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The grant is the hosted path's; `ufoctl init` gives a self-host workspace nothing, so its
    turns are never gated on a balance it was never meant to hold."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-onboard")
    onboarded = await _onboarding(database_url, tmp_path).run()
    with ws(onboarded.workspace_id):
        async with workspace_tx() as connection:
            current = await read_balance(connection, onboarded.workspace_id)
    assert current is None
