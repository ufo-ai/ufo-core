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
from ufo_ext_sample import PROVISIONED_AGENT_NAME, PROVISIONED_AGENT_PROMPT

from ufo.agent_setup import (
    SETUP_SKILL_NAME,
    AgentSetup,
    pending_setup,
    setup_skill,
)
from ufo.agents import AgentSpec
from ufo.config import BlobConfig, Config, DatabaseConfig
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext
from ufo.ext.manifest import SETUP_TOOLS, AgentProvision, JobSpec, Manifest
from ufo.jobs import JobRunner, bindings_from
from ufo.loop.queue import _agent_tools, _apply_provisions, _provisioned_workspaces
from ufo.object_name import validate_object_name
from ufo.onboarding import Onboarding
from ufo.provisioning import ADOPTED, CREATED, PRESENT, AgentProvisioning
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, INTENT_ADMISSION, MEMBER_ADMISSION
from ufo.tools.registry import ToolDef
from ufo.workspace import ws

OWNER_EMAIL = "owner@example.com"
DEFAULT_MODEL = "claude-opus-4-8"
OTHER_EXTENSION = "other"
MEMBER_PROMPT = "A prompt this workspace wrote for itself."


def _provision(name: str = PROVISIONED_AGENT_NAME, **overrides: object) -> AgentProvision:
    spec = AgentSpec(
        model="auto",
        reasoning="auto",
        internet_access_allowed=False,
        prompt=PROVISIONED_AGENT_PROMPT,
    )
    return AgentProvision(
        name=name,
        spec=spec.model_copy(update=overrides),
        tools=("sample_echo", *SETUP_TOOLS),
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
    assert (created.provisioned_by, created.provisioned_name, created.provisioned_version) == (
        sample.NAME,
        PROVISIONED_AGENT_NAME,
        sample.manifest().version,
    )


async def test_a_second_application_creates_nothing_and_edits_nothing(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    first = await AgentProvisioning((sample.manifest(),)).apply(workspace_id)
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    second = await AgentProvisioning((sample.manifest(),)).apply(workspace_id)
    unchanged = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert [outcome.result for outcome in first] == [CREATED]
    assert [outcome.result for outcome in second] == [PRESENT]
    assert created is not None and unchanged is not None
    assert (unchanged.id, unchanged.updated_at) == (created.id, created.updated_at)


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


async def test_the_shipped_sandbox_size_reaches_the_created_row(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declared tier is the one every new conversation of the agent is provisioned at, so the
    row carries it rather than the table's `small` default."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    manifest = _manifest(OTHER_EXTENSION, _provision(sandbox_size="large"))
    outcomes = await AgentProvisioning((manifest,)).apply(workspace_id)
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert [outcome.result for outcome in outcomes] == [CREATED]
    assert created is not None
    assert created.sandbox_size == "large"


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


async def test_an_identical_row_is_adopted(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    await _insert_agent(workspace_id, PROVISIONED_AGENT_NAME, tools=["sample_echo", *SETUP_TOOLS])
    outcomes = await AgentProvisioning((_manifest(OTHER_EXTENSION, _provision()),)).apply(
        workspace_id
    )
    adopted = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert [outcome.result for outcome in outcomes] == [ADOPTED]
    assert adopted is not None
    assert (adopted.provisioned_by, adopted.provisioned_name, adopted.provisioned_version) == (
        OTHER_EXTENSION,
        PROVISIONED_AGENT_NAME,
        "0.1.0",
    )


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
    later = _manifest(OTHER_EXTENSION, _provision(prompt="A prompt the next release ships."))
    outcomes = await AgentProvisioning((later,)).apply(workspace_id)
    row = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert [outcome.result for outcome in outcomes] == [PRESENT]
    assert row is not None
    assert (row.prompt, row.provisioned_version) == (PROVISIONED_AGENT_PROMPT, "1.0.0")


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


async def test_an_extension_job_provisions_its_agent_before_the_handler(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    observed: list[bool] = []

    async def handler(_ctx: ExtensionContext) -> None:
        observed.append(await _row(workspace_id, PROVISIONED_AGENT_NAME) is not None)

    async def candidates() -> tuple[UUID, ...]:
        return (workspace_id,)

    manifest = Manifest(
        name=sample.NAME,
        version=sample.manifest().version,
        agents=sample.manifest().agents,
        jobs=(JobSpec(name="probe", schedule=None, handler=handler, candidates=candidates),),
    )
    runner = JobRunner(bindings=bindings_from((manifest,), ()))
    await runner.fire(f"{sample.NAME}:probe", workspace_id)
    assert observed == [True]


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


def test_a_provision_refuses_a_name_that_is_not_an_object_name() -> None:
    with pytest.raises(ValueError, match="object name"):
        _provision(name="Sample Probe")


def test_a_provision_refuses_an_empty_prompt() -> None:
    with pytest.raises(ValueError, match="no prompt"):
        _provision(prompt="  ")


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


TOOLS = (_tool("read"), _tool("checkout_code_review", profile_only=True))


def test_an_agent_naming_no_allowlist_runs_the_member_facing_set() -> None:
    assert [tool.name for tool in _agent_tools(TOOLS, None, MEMBER_ADMISSION)] == ["read"]


def test_an_allowlist_reaches_the_primitive_it_names() -> None:
    selected = _agent_tools(TOOLS, ("checkout_code_review",), MEMBER_ADMISSION)
    assert [tool.name for tool in selected] == ["checkout_code_review"]


def test_an_allowlist_holds_nothing_it_does_not_name() -> None:
    assert _agent_tools(TOOLS, ("absent_tool",), MEMBER_ADMISSION) == ()


def test_a_prepared_intent_runs_past_the_allowlist() -> None:
    """The panel dispatches its verb verbatim, under the submitting member's authority and its own
    gate, with no model round for an allowlist to govern. An agent that carries one would otherwise
    refuse every panel mutation on itself, `connect_account` and `request_credentials` included —
    the two that would give it an account or a key."""
    selected = _agent_tools(TOOLS, ("checkout_code_review",), INTENT_ADMISSION)
    assert [tool.name for tool in selected] == ["read"]


async def _conversation(workspace_id: UUID, agent_id: UUID) -> UUID:
    conversation_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="test",
                queue_key=conversation_id.hex,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return conversation_id


async def _grant_connection(workspace_id: UUID, agent_id: UUID, provider: str) -> UUID:
    connection_id = uuid4()
    with ws(workspace_id):
        conversation_id = await _conversation(workspace_id, agent_id)
        async with workspace_tx() as connection:
            owner = (
                (
                    await connection.execute(
                        sa.select(tables.member.c.id).where(
                            tables.member.c.workspace_id == workspace_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            await connection.execute(
                sa.insert(tables.connection).values(
                    id=connection_id,
                    workspace_id=workspace_id,
                    provider=provider,
                    account_id=f"acct-{connection_id.hex[:8]}",
                    host="api.sample.test",
                    owner_member_id=owner,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.connector_grant).values(
                    id=uuid4(),
                    workspace_id=workspace_id,
                    agent_id=agent_id,
                    connection_id=connection_id,
                    conversation_id=conversation_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    return connection_id


async def _unmet(workspace_id: UUID, name: str) -> AgentSetup | None:
    with ws(workspace_id):
        return {name: missing for _, name, missing in await pending_setup()}.get(name)


async def test_the_shipped_agent_records_every_grant_it_still_needs(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A shipped agent arrives with no edges, so what it cannot do yet is the first thing a member
    must be told. The declaration lands on the row beside the prompt, and the read reports it."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    assert created.setup == {
        "connectors": [sample.CONNECTOR_PROVIDER],
        "instructions": sample.PROVISIONED_AGENT_SETUP,
    }
    assert await _unmet(workspace_id, PROVISIONED_AGENT_NAME) == AgentSetup(
        connectors=(sample.CONNECTOR_PROVIDER,), instructions=sample.PROVISIONED_AGENT_SETUP
    )


async def test_the_grant_a_member_makes_settles_its_need(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The declaration names a kind of authority, so any connection of that provider answers it —
    which account stays the member's choice."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    await _grant_connection(workspace_id, created.id, sample.CONNECTOR_PROVIDER)
    assert await _unmet(workspace_id, PROVISIONED_AGENT_NAME) is None


async def test_a_grant_to_another_agent_settles_nothing(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both edges are keyed on the agent, so a connection the main agent holds does not reach the
    shipped one. Reporting otherwise would tell a member they were done while it still cannot
    act."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    assert main is not None
    await _grant_connection(workspace_id, main.id, sample.CONNECTOR_PROVIDER)
    assert await _unmet(workspace_id, PROVISIONED_AGENT_NAME) == AgentSetup(
        connectors=(sample.CONNECTOR_PROVIDER,), instructions=sample.PROVISIONED_AGENT_SETUP
    )


async def test_an_agent_no_extension_shipped_needs_nothing(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member's own agent declares no needs, so the read reports none rather than an empty shape
    every caller would have to tell apart from a satisfied one."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    assert await _unmet(workspace_id, DEFAULT_AGENT_NAME) is None


async def test_an_agent_that_is_not_set_up_is_told_so_in_its_own_conversation(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The setup slot is how a shipped agent asks for what it needs. It reaches the one agent that
    can act on it, carries the extension's own instructions, and names every missing grant."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    with ws(workspace_id):
        skill = await setup_skill(created.id, is_main=False, has_speaker=True)
    assert skill is not None
    assert skill.name == SETUP_SKILL_NAME
    assert "You are installed but not set up" in skill.instructions
    assert f"a {sample.CONNECTOR_PROVIDER} account" in skill.instructions
    assert sample.PROVISIONED_AGENT_SETUP in skill.instructions
    assert PROVISIONED_AGENT_NAME not in skill.description


async def test_the_setup_slot_empties_as_the_grants_land(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It disappears on its own, so a wired workspace carries no standing instruction naming work
    that is already done."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert created is not None
    with ws(workspace_id):
        assert await setup_skill(created.id, is_main=False, has_speaker=True) is not None
    await _grant_connection(workspace_id, created.id, sample.CONNECTOR_PROVIDER)
    with ws(workspace_id):
        assert await setup_skill(created.id, is_main=False, has_speaker=True) is None


async def test_a_workspace_with_nothing_shipped_carries_no_setup_slot(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, ())
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    assert main is not None
    with ws(workspace_id):
        assert await setup_skill(main.id, is_main=True, has_speaker=True) is None


async def test_an_agent_with_nothing_outstanding_is_told_nothing(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An agent that is not the main one is told about itself alone. Another agent's outstanding
    grants would name work it cannot do: it may not grant an account to a different agent."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    shipped = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    assert main is not None and shipped is not None
    with ws(workspace_id):
        assert await setup_skill(main.id, is_main=False, has_speaker=True) is None
        assert await setup_skill(shipped.id, is_main=False, has_speaker=True) is not None


def test_a_provision_refuses_an_allowlist_that_cannot_obtain_its_own_grants() -> None:
    """The skill tells an agent to call `connect_account` and `object_apply`, and an allowlist
    holds nothing it does not name — so a provision declaring both is a shipped agent that could
    read its own instructions and follow none of them. It is refused where it is written."""
    spec = AgentSpec(model="auto", reasoning="auto", internet_access_allowed=False, prompt="probe")
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
    spec = AgentSpec(model="auto", reasoning="auto", internet_access_allowed=False, prompt="probe")
    provision = AgentProvision(name="self-contained", spec=spec, tools=("sample_echo",))
    assert provision.tools == ("sample_echo",)


async def test_the_main_agent_is_told_which_agents_it_can_connect_an_account_for(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A member on Slack or the CLI reaches only the main agent, and the main agent is the one
    agent that may grant an account to another. So it is told the roster and the verb, and the
    member finishes the setup by asking rather than by opening the portal."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    assert main is not None
    with ws(workspace_id):
        roster = await setup_skill(main.id, is_main=True, has_speaker=True)
        assert await setup_skill(main.id, is_main=False, has_speaker=True) is None
    assert roster is not None
    assert f"- {PROVISIONED_AGENT_NAME} — still needs" in roster.instructions
    assert "`connect_account` with `agent` set to" in roster.instructions


async def test_a_turn_nobody_speaks_on_is_not_told_to_ask(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every act the skill names is speaker-gated: `connect_account` refuses without one. A spawn, a
    schedule, or a source arrival would be handed instructions it cannot follow and a member it
    cannot ask, so it is told nothing and the grant waits for a member's own turn."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-provision")
    workspace_id = await _workspace(database_url, tmp_path, (sample.manifest(),))
    created = await _row(workspace_id, PROVISIONED_AGENT_NAME)
    main = await _row(workspace_id, DEFAULT_AGENT_NAME)
    assert created is not None and main is not None
    with ws(workspace_id):
        assert await setup_skill(created.id, is_main=False, has_speaker=True) is not None
        assert await setup_skill(created.id, is_main=False, has_speaker=False) is None
        assert await setup_skill(main.id, is_main=True, has_speaker=False) is None
