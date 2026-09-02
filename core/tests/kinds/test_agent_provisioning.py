"""Extension-shipped agents applied to a workspace, through the sample extension.

The sample declares one `AgentProvision`, so these drive the real seam: activation creates the
ordinary row, a second application changes nothing, an identical row the workspace already held is
adopted, a differing one is left alone under its name, a member's edit survives a later application,
and a later version of the extension rewrites nothing. Rows are read back through the `agent` table
every other read uses.
"""

from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet
from ufo_ext_sample import (
    CONNECTOR_PROVIDER,
    PROVISIONED_AGENT_NAME,
    PROVISIONED_AGENT_PROMPT,
    PROVISIONED_AGENT_PURPOSE,
)

from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.db import workspace_tx
from ufo.onboard.onboarding import Onboarding
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.manifest import SETUP_TOOLS, AgentProvision, Manifest
from ufo.runtime.kinds.agent_setup import AgentSetup
from ufo.runtime.kinds.agents import ARCHIVED_AGENT_NAME_PREFIX, AgentSpec
from ufo.runtime.kinds.provisioning import ADOPTED, CREATED, PRESENT, AgentProvisioning
from ufo.runtime.object_name import validate_object_name
from ufo.runtime.queue import _agent_tools, _apply_provisions, _provisioned_workspaces
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    AGENT_ICONS,
    DEFAULT_AGENT_NAME,
    INTENT_ADMISSION,
    MAIN_AGENT_ICON,
    MEMBER_ADMISSION,
)

OWNER_EMAIL = "owner@example.com"
DEFAULT_MODEL = "claude-opus-4-8"
OTHER_EXTENSION = "other"
MEMBER_PROMPT = "A prompt this workspace wrote for itself."


def _provision(
    name: str = PROVISIONED_AGENT_NAME,
    icon: str | None = None,
    setup: AgentSetup | None = None,
    main: bool = False,
    **overrides: object,
) -> AgentProvision:
    spec = AgentSpec(
        model="auto",
        reasoning="auto",
        internet_access_allowed=False,
        prompt=PROVISIONED_AGENT_PROMPT,
        purpose=PROVISIONED_AGENT_PURPOSE,
    )
    return AgentProvision(
        name=name,
        spec=spec.model_copy(update=overrides),
        tools=("sample_echo", *SETUP_TOOLS),
        icon=icon,
        setup=setup if setup is not None else AgentSetup(),
        main=main,
    )


def _manifest(extension: str, *provisions: AgentProvision, version: str = "0.1.0") -> Manifest:
    return Manifest(name=extension, version=version, agents=provisions)


async def _workspace(database_url: str, tmp_path: Path, manifests: tuple[Manifest, ...]) -> UUID:
    onboarded = await Onboarding(
        config=Config(
            database=DatabaseConfig(url=database_url),
            blob=BlobConfig(backend="filesystem", root=tmp_path),
        ),
        email=OWNER_EMAIL,
        model=DEFAULT_MODEL,
        credentials=CredentialStore(fernet=Fernet(Fernet.generate_key())),
        manifests=manifests,
    ).run()
    return onboarded.workspace_id


async def _row(workspace_id: UUID, name: str) -> sa.Row | None:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            return (
                await connection.execute(sa.select(tables.agent).where(tables.agent.c.name == name))
            ).one_or_none()


async def _insert_agent(workspace_id: UUID, name: str, **values: object) -> None:
    row: dict[str, object] = {
        "id": uuid4(),
        "workspace_id": workspace_id,
        "name": name,
        "prompt": PROVISIONED_AGENT_PROMPT,
        "model": "auto",
        "reasoning": "auto",
        "is_main": False,
        "internet_access_allowed": False,
        "created_at": sa.func.now(),
        "updated_at": sa.func.now(),
    }
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(sa.insert(tables.agent).values(row | values))


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_onboarding_creates_the_shipped_agent(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    assert (created.prompt, created.is_main, created.internet_access_allowed) == (
        PROVISIONED_AGENT_PROMPT,
        False,
        False,
    )
    assert created.tools == ["sample_echo", *SETUP_TOOLS]
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    assert main is not None
    assert main.icon == MAIN_AGENT_ICON
    assert created.icon in AGENT_ICONS
    assert (created.provisioned_by, created.provisioned_name, created.provisioned_version) == (
        sample.NAME,
        PROVISIONED_AGENT_NAME,
        sample.manifest().version,
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_an_archived_shipped_app_stays_archived_when_provisioning_runs_again(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(
                    name=f"~archived-{created.id}",
                    archived_name=tables.agent.c.name,
                    archived_at=sa.func.now(),
                )
                .where(tables.agent.c.id == created.id)
            )

    outcomes = await AgentProvisioning((sample.manifest(),)).apply(workspace_id)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            kept = (
                await connection.execute(
                    sa.select(tables.agent).where(tables.agent.c.id == created.id)
                )
            ).one()

    assert [outcome.result for outcome in outcomes] == [PRESENT]
    assert [outcome.name for outcome in outcomes] == [PROVISIONED_AGENT_NAME]
    assert kept.id == created.id
    assert kept.archived_at is not None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_two_executions_that_both_reach_the_insert_create_one_agent(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two turns of one workspace can read the absent row together, in one process or in two
    replicas, and both then reach the insert. The unique name settles which one writes, and the
    loser writes nothing, rather than failing the member's turn with an IntegrityError."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    provisioning = AgentProvisioning((sample.manifest(),))
    provision = _provision()
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await provisioning._create(
                connection, workspace_id, _manifest("one"), provision, PROVISIONED_AGENT_NAME
            )
            await provisioning._create(
                connection, workspace_id, _manifest("two"), provision, PROVISIONED_AGENT_NAME
            )
    row = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert row is not None
    assert row.provisioned_by == "one"


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_member_edit_survives_the_next_application(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(reasoning="high", internet_access_allowed=True)
                .where(tables.agent.c.name == PROVISIONED_AGENT_NAME)
            )
    await AgentProvisioning((sample.manifest(),)).apply(workspace_id)
    edited = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert edited is not None
    assert (edited.reasoning, edited.internet_access_allowed) == ("high", True)


async def _names(workspace_id: UUID) -> list[str]:
    with ws(workspace_id):
        async with workspace_tx() as connection:
            return sorted(
                (
                    await connection.execute(
                        sa.select(tables.agent.c.name).where(
                            tables.agent.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_main_provision_lands_on_the_workspaces_main_agent(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision()),)).apply(workspace_id)
    old = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert old is not None
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(name="helper")
                .where(tables.agent.c.name == DEFAULT_AGENT_NAME)
            )
    before = await _row(workspace_id, "helper")
    assert before is not None
    manifest = _manifest(OTHER_EXTENSION, _provision(main=True))

    outcomes = await AgentProvisioning((manifest,)).apply(workspace_id)
    adopted = await _row(workspace_id, "helper")
    assert [(outcome.result, outcome.name) for outcome in outcomes] == [(ADOPTED, "helper")]
    assert set(await _names(workspace_id)) == {
        f"{ARCHIVED_AGENT_NAME_PREFIX}{old.id}",
        "helper",
    }
    assert adopted is not None
    assert (adopted.provisioned_by, adopted.provisioned_name, adopted.provisioned_version) == (
        OTHER_EXTENSION,
        PROVISIONED_AGENT_NAME,
        "0.1.0",
    )
    assert adopted.is_main
    assert (adopted.id, adopted.prompt, adopted.icon, adopted.internet_access_allowed) == (
        before.id,
        before.prompt,
        before.icon,
        before.internet_access_allowed,
    )
    assert adopted.purpose == PROVISIONED_AGENT_PURPOSE
    with ws(workspace_id):
        async with workspace_tx() as connection:
            retired = (
                await connection.execute(sa.select(tables.agent).where(tables.agent.c.id == old.id))
            ).one()
    assert retired.archived_at is not None
    assert retired.archived_name == PROVISIONED_AGENT_NAME
    assert retired.provisioned_by is None

    again = await AgentProvisioning((manifest,)).apply(workspace_id)
    assert [outcome.result for outcome in again] == [PRESENT]
    assert set(await _names(workspace_id)) == {
        f"{ARCHIVED_AGENT_NAME_PREFIX}{old.id}",
        "helper",
    }


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_main_provision_creates_the_main_agent_where_a_workspace_holds_none(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .values(is_main=False, name="orphan")
                .where(tables.agent.c.name == DEFAULT_AGENT_NAME)
            )

    outcomes = await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision(main=True)),)).apply(
        workspace_id
    )
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    orphan = await _row(workspace_id, "orphan")
    assert [outcome.result for outcome in outcomes] == [CREATED]
    assert created is not None and orphan is not None
    assert created.is_main
    assert not orphan.is_main
    assert orphan.provisioned_by is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_name_the_workspace_already_uses_leaves_that_row_alone(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace keeps the name and the row it wrote. The shipped agent lands beside it under a
    free name, so an extension can never take a name a member is using, and never gets stuck."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await _insert_agent(workspace_id, PROVISIONED_AGENT_NAME, prompt=MEMBER_PROMPT)
    manifest = _manifest(OTHER_EXTENSION, _provision())
    outcomes = await AgentProvisioning((manifest,)).apply(workspace_id)
    kept = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    landed = await _row(workspace_id, f"{PROVISIONED_AGENT_NAME}-{OTHER_EXTENSION}")
    assert [outcome.result for outcome in outcomes] == [CREATED]
    assert kept is not None
    assert (kept.prompt, kept.provisioned_by) == (MEMBER_PROMPT, None)
    assert landed is not None
    assert (landed.provisioned_by, landed.provisioned_name) == (
        OTHER_EXTENSION,
        PROVISIONED_AGENT_NAME,
    )

    again = await AgentProvisioning((manifest,)).apply(workspace_id)
    assert [outcome.result for outcome in again] == [PRESENT]
    assert [outcome.name for outcome in again] == [f"{PROVISIONED_AGENT_NAME}-{OTHER_EXTENSION}"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_two_extensions_that_claim_one_name_both_land(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two packs may ship an agent under the same name. Each keeps its own row: the first takes the
    declared name, the second the suffixed one. Neither the deploy nor a turn fails over it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    manifests = (_manifest("one", _provision()), _manifest("two", _provision()))
    outcomes = await AgentProvisioning(manifests).apply(workspace_id)
    assert [(outcome.extension, outcome.name, outcome.result) for outcome in outcomes] == [
        ("one", PROVISIONED_AGENT_NAME, CREATED),
        ("two", f"{PROVISIONED_AGENT_NAME}-two", CREATED),
    ]
    again = await AgentProvisioning(manifests).apply(workspace_id)
    assert [outcome.result for outcome in again] == [PRESENT, PRESENT]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_later_version_never_rewrites_the_row_it_already_shipped(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The workspace's copy is the live configuration from the moment it lands. A newer extension
    reaches new workspaces only, and the row keeps the version that created it, so the version a
    workspace actually runs is readable rather than assumed."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision(), version="1.0.0"),)).apply(
        workspace_id
    )
    later = _manifest(
        OTHER_EXTENSION,
        _provision(prompt="A prompt the next release ships.", visibility="workspace"),
    )
    outcomes = await AgentProvisioning((later,)).apply(workspace_id)
    row = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert [outcome.result for outcome in outcomes] == [PRESENT]
    assert row is not None
    assert (row.prompt, row.visibility, row.provisioned_version) == (
        PROVISIONED_AGENT_PROMPT,
        "private",
        "1.0.0",
    )


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_purpose_a_member_wrote_stands(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the same fill. A purpose is a sentence a member may replace — they say
    what their app is for, in their own words — so the extension's own never lands on top of it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision()),)).apply(workspace_id)
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.agent)
                .where(tables.agent.c.name == PROVISIONED_AGENT_NAME)
                .values(purpose="Ours reads the night shift's log.")
            )

    await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision()),)).apply(workspace_id)
    row = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert row.purpose == "Ours reads the night shift's log."


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_new_account_a_feature_needs_reaches_a_workspace_that_already_holds_the_app(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`setup` is the extension's declaration whole and a member never writes it, so unlike the
    purpose it is rewritten every pass. A release that gives an app a feature needing a second
    account would otherwise state that need to new workspaces only, and every workspace already
    holding the app would read a setup list missing the account its app now needs."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision()),)).apply(workspace_id)

    widened = _provision(
        setup=AgentSetup(connectors=(CONNECTOR_PROVIDER, "googledocs"), instructions="Ask first.")
    )
    await AgentProvisioning((_manifest(OTHER_EXTENSION, widened),)).apply(workspace_id)
    row = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert row.setup["connectors"] == [CONNECTOR_PROVIDER, "googledocs"]
    assert row.setup["instructions"] == "Ask first."


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_workspace_that_predates_the_extension_gets_the_agent_on_its_next_turn(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Onboarding covers a new workspace. An older workspace receives its agents at the first turn
    the process runs for it. A later turn of the same process does no work at all, which the
    deleted row proves: the second call reads nothing and writes nothing."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    assert await _row(workspace_id, PROVISIONED_AGENT_NAME) is None
    runtime = SimpleNamespace(manifests=(sample.manifest(),))
    _provisioned_workspaces.discard(workspace_id)
    await _apply_provisions(runtime, workspace_id)
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    assert workspace_id in _provisioned_workspaces
    with ws(workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.delete(tables.agent).where(tables.agent.c.name == PROVISIONED_AGENT_NAME)
            )
    await _apply_provisions(runtime, workspace_id)
    assert await _row(workspace_id, PROVISIONED_AGENT_NAME) is None


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_a_suffixed_name_is_addressable_by_an_extension_whose_own_name_is_not(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Eleven installed extensions spell their name with an underscore, which the object grammar
    does not allow. The suffix is minted, not declared, so it conforms by construction — otherwise
    the row lands under a name no `object_get` or `object_apply` can address."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await _insert_agent(workspace_id, PROVISIONED_AGENT_NAME, prompt=MEMBER_PROMPT)
    outcomes = await AgentProvisioning((_manifest("brief_pipeline", _provision()),)).apply(
        workspace_id
    )
    (landed,) = outcomes
    assert landed.name == f"{PROVISIONED_AGENT_NAME}-brief-pipeline"
    validate_object_name(landed.name)


def test_a_provision_refuses_an_empty_prompt() -> None:
    with pytest.raises(ValueError, match="no prompt"):
        _provision(prompt="  ")


def test_a_provision_refuses_an_icon_that_names_no_mark() -> None:
    with pytest.raises(ValueError, match="names no mark"):
        _provision(icon="Not A Mark")


def _tool(name: str, *, profile_only: bool = False) -> ToolDef:
    return ToolDef(
        name=name,
        description=name,
        input_model=AgentSpec,
        handler=_unreached,
        profile_only=profile_only,
    )


async def _unreached(ctx: object, args: object) -> object:
    raise AssertionError("the filter never dispatches")


TOOLS = (
    _tool("read"),
    _tool("checkout_code_review", profile_only=True),
    _tool("load_skill"),
)


def test_an_allowlist_holds_nothing_it_does_not_name() -> None:
    assert _agent_tools(TOOLS, ("absent_tool",), MEMBER_ADMISSION) == ()


def test_a_prepared_intent_runs_past_the_allowlist() -> None:
    """The panel dispatches its verb verbatim, under the submitting member's authority and its own
    gate, with no model round for an allowlist to govern. An agent that carries one would otherwise
    refuse every panel mutation on itself, `connect_account` and `request_credentials` included —
    the two that would give it an account or a key."""
    selected = _agent_tools(TOOLS, ("checkout_code_review",), INTENT_ADMISSION, uuid4())
    assert [tool.name for tool in selected] == ["read", "load_skill"]


def test_a_tool_bridge_intent_stays_inside_the_allowlist() -> None:
    selected = _agent_tools(TOOLS, ("read",), INTENT_ADMISSION)
    assert [tool.name for tool in selected] == ["read"]


@pytest.mark.parametrize("database_url", ["sqlite"], indirect=True)
async def test_the_shipped_agent_records_every_grant_it_still_needs(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup declaration lands on the agent row."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    assert created.setup == sample.manifest().agents[0].setup.model_dump(mode="json")
    assert created.setup["connectors"] == [sample.CONNECTOR_PROVIDER]
    assert created.setup["instructions"] == sample.PROVISIONED_AGENT_SETUP


def test_a_provision_refuses_an_allowlist_that_cannot_obtain_its_own_grants() -> None:
    """The skill tells an agent to call `connect_account` and `object_apply`, and an allowlist
    holds nothing it does not name — so a provision declaring both is a shipped agent that could
    read its own instructions and follow none of them. It is refused where it is written."""
    spec = AgentSpec(
        model="auto",
        reasoning="auto",
        internet_access_allowed=False,
        prompt="probe",
        purpose="probe what the workspace recorded",
    )
    with pytest.raises(ValueError, match="allowlist omits"):
        AgentProvision(
            name="short-handed",
            spec=spec,
            tools=("sample_echo",),
            setup=AgentSetup(connectors=("sample_connector",)),
        )


def test_a_provision_that_declares_no_setup_keeps_a_bare_allowlist() -> None:
    """The verbs are required by the setup slot, not by shipping an agent, so an agent that asks
    for nothing still holds exactly what it declares."""
    spec = AgentSpec(
        model="auto",
        reasoning="auto",
        internet_access_allowed=False,
        prompt="probe",
        purpose="probe what the workspace recorded",
    )
    provision = AgentProvision(name="self-contained", spec=spec, tools=("sample_echo",))
    assert provision.tools == ("sample_echo",)
