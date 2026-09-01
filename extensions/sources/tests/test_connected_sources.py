"""A connected account's feeds are created with the connection, and the job retries what did not
land.

Every assertion drives a real seam and reads back durable rows. The connect path runs
`ConnectFlow.complete` over an injected OAuth descriptor with this extension's
`connection_recorded` hook bound exactly as `serve` binds it, so the rows are asserted at the
instant the callback answers. The retry path runs the declared job through `JobRunner`, so its
candidates and handler are the ones exercised. Covered here: the callback leaves one private row
per canonical stream granted to the main agent, each pinned to the window its stream declares; a
connection only another agent holds is left alone; a per-tenant provider is never auto-registered;
the job creates what a connect-time creation did not and adds nothing once the rows are there; a
binding the member deleted stays deleted; a canonical stream missing from a binding the member
configured joins it under that binding's window and the connection owner's own private disclosure; a
binding holding a workspace-shared row gains nothing at all; and a member narrowing an auto-created
binding through `object_apply` keeps exactly the streams they named."""

from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import yaml
from cryptography.fernet import Fernet
from ufo_ext_sources import manifest as sources_manifest
from ufo_ext_sources.registry import CONNECTORS, SOURCE_KIND

from ufo.db import workspace_tx
from ufo.host.ext.loader import connection_hooks, turn_tools
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import ConnectFlow, GrantStore, OAuthAccount
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.turns.subjects import SHARED_SUBJECT, member_subject
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.connectors import ConnectorEntry, ConnectorRegistry
from ufo.sdk.sources import ConnectorSourceConfig, binding_name
from ufo.sdk.tools import ToolContext

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ASANA = "asana"
GMAIL = "gmail"
FRESHDESK = "freshdesk"
ACCOUNT = "acct-one"
REDIRECT_URI = "http://surface/v1/connect/callback"
MEMBER_WINDOW_DAYS = 90
JOB_KEY = f"{sources_manifest.NAME}:{sources_manifest.CONNECTED_SOURCES_RETRY_JOB}"


@dataclass(frozen=True)
class _Workspace:
    workspace_id: UUID
    member_id: UUID
    main_id: UUID
    other_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class _StubProvider:
    """The injected OAuth descriptor: it stands in for the connector extension's provider so the
    connect flow completes offline, and is never the thing asserted. `authorize_url` echoes the
    sealed state the callback replays; `exchange` yields the connected account the broker holds."""

    provider: str
    host: str = "api.provider.test"

    def authorize_url(self, state: str, redirect_uri: str) -> str:
        return f"https://provider.test/oauth?state={state}&redirect_uri={redirect_uri}"

    async def exchange(
        self, code: str, redirect_uri: str, workspace_id: UUID, state: str
    ) -> OAuthAccount:
        return OAuthAccount(account_id=ACCOUNT)


async def _workspace() -> _Workspace:
    workspace_id, member_id, main_id, other_id = uuid4(), uuid4(), uuid4(), uuid4()
    conversation_id = uuid4()
    created_at = datetime(2026, 8, 16, tzinfo=UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=created_at, updated_at=created_at
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex}@x.test",
                is_admin=True,
                created_at=created_at,
                updated_at=created_at,
            )
        )
        await connection.execute(
            sa.insert(tables.agent),
            [
                {
                    "id": main_id,
                    "workspace_id": workspace_id,
                    "name": "assistant",
                    "prompt": "p",
                    "model": "claude-opus-4-8",
                    "is_main": True,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
                {
                    "id": other_id,
                    "workspace_id": workspace_id,
                    "name": "sweep",
                    "prompt": "p",
                    "model": "claude-opus-4-8",
                    "is_main": False,
                    "created_at": created_at,
                    "updated_at": created_at,
                },
            ],
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=main_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=created_at,
                updated_at=created_at,
            )
        )
    return _Workspace(workspace_id, member_id, main_id, other_id, conversation_id)


def _flow() -> ConnectFlow:
    """The deploy's connect flow as `serve` assembles it: the extension's declared hooks bound
    through `connection_hooks`, so completing the handoff publishes to the real chain."""
    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    return ConnectFlow(
        providers={
            provider: _StubProvider(provider=provider) for provider in (ASANA, GMAIL, FRESHDESK)
        },
        fernet=credentials.fernet,
        store=GrantStore(),
        redirect_uri=REDIRECT_URI,
        connections=connection_hooks((sources_manifest.manifest(),), credentials),
    )


async def _connect(state: _Workspace, agent_id: UUID, provider: str) -> None:
    """The member's whole connect act: the sealed link the tool hands them, then the callback."""
    flow = _flow()
    url = flow.authorize(
        workspace_id=state.workspace_id,
        agent_id=agent_id,
        provider=provider,
        grantor_member_id=state.member_id,
        conversation_id=state.conversation_id,
        shared=False,
    )
    sealed = parse_qs(urlparse(url).query)["state"][0]
    await flow.complete(state=sealed, code="oauth-code")


async def _connect_without_the_hook(state: _Workspace, agent_id: UUID, provider: str) -> None:
    """A connection whose creation left no rows — the hook raised, or the process died after the
    grant committed. What the retry job exists for."""
    with ws(state.workspace_id), agent(agent_id):
        await GrantStore().record(
            provider=provider,
            account_id=ACCOUNT,
            host=f"api.{provider}.test",
            grantor_member_id=state.member_id,
            conversation_id=state.conversation_id,
            shared=False,
        )


def _runner() -> JobRunner:
    declared = sources_manifest.manifest()
    return JobRunner(bindings=bindings_from((declared,), ()), manifests=(declared,))


async def _tick(state: _Workspace) -> None:
    await _runner().fire(JOB_KEY, state.workspace_id)


def _ext() -> ExtensionContext:
    return context_for(sources_manifest.NAME, frozenset(CONNECTORS))


async def _rows(state: _Workspace) -> list[sa.RowMapping]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.source)
                        .where(tables.source.c.workspace_id == state.workspace_id)
                        .order_by(tables.source.c.backend, tables.source.c.id)
                    )
                )
                .mappings()
                .all()
            )


async def _granted_agents(state: _Workspace) -> set[UUID]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return set(
                (
                    await connection.execute(
                        sa.select(tables.source_grant.c.agent_id).where(
                            tables.source_grant.c.workspace_id == state.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )


async def _connection_id(state: _Workspace) -> UUID:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.connection.c.id).where(
                        tables.connection.c.workspace_id == state.workspace_id
                    )
                )
            ).scalar_one()


def _canonical(provider: str) -> set[str]:
    return {stream.name for stream in CONNECTORS[provider]().streams() if stream.canonical}


class _UnusedBroker:
    async def credential(self, workspace_id: UUID, provider: str, account: str) -> None:
        raise AssertionError("narrowing a binding must not resolve provider credentials")


_APPLY: ToolDef = next(
    tool
    for tool in turn_tools(
        (sources_manifest.manifest(),),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )[0]
    if tool.name == "object_apply"
)


def _turn_context(state: _Workspace) -> ToolContext:
    """The connecting member's own turn on the main agent, with the connector registered as the
    connectors extension registers it — so their narrowing apply runs the shipped dispatch over the
    rows the callback left."""
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=state.main_id,
            seq=1,
            status="running",
            inbound="sync less of asana",
            created_at=datetime(2026, 8, 16, tzinfo=UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=state.member_id,
        audience=conversation_audience(state.member_id),
        artifact_token_secret="",
        grants=GrantStore(),
        connectors=ConnectorRegistry(
            entries={ASANA: ConnectorEntry(provider=ASANA, label=ASANA, broker=_UnusedBroker())},
            resolver=None,
            fallback=None,
        ),
        ext=_ext(),
    )


async def _member_row(state: _Workspace, *, subject: str, owner: UUID) -> str:
    """One asana stream a member registered themselves, with a window of their own — the binding the
    registrar meets when it reaches an account that already holds rows."""
    registered = sorted(_canonical(ASANA))[0]
    with ws(state.workspace_id), agent(state.main_id):
        await _ext().register_source(
            ASANA,
            ConnectorSourceConfig(
                account=ACCOUNT, stream=registered, backfill_days=MEMBER_WINDOW_DAYS
            ),
            subject=subject,
            owner_member_id=owner,
            connection_id=await _connection_id(state),
        )
    return registered


async def _narrow(state: _Workspace, streams: tuple[str, ...]) -> None:
    manifest_text = yaml.safe_dump(
        {
            "kind": SOURCE_KIND,
            "name": binding_name(ASANA, ACCOUNT, None),
            "spec": {"provider": ASANA, "streams": list(streams)},
        }
    )
    with ws(state.workspace_id), agent(state.main_id):
        result = await _APPLY.handler(
            _turn_context(state),
            _APPLY.input_model.model_validate({"manifest": manifest_text}),
        )
    assert result.is_error is False


def test_the_manifest_fires_on_connect_and_keeps_a_retry_job() -> None:
    manifest = sources_manifest.manifest()
    assert "connection_recorded" in {spec.event for spec in manifest.hooks}
    [job] = manifest.jobs
    assert job.name == sources_manifest.CONNECTED_SOURCES_RETRY_JOB
    assert job.schedule == sources_manifest.CONNECTED_SOURCES_RETRY_SCHEDULE


async def test_connecting_an_account_creates_its_feeds_in_the_callback(db: None) -> None:
    state = await _workspace()

    await _connect(state, state.main_id, ASANA)

    rows = await _rows(state)
    assert {row["config"]["stream"] for row in rows} == _canonical(ASANA)
    connection_id = await _connection_id(state)
    for row in rows:
        assert row["backend"] == ASANA
        assert row["config"]["account"] == ACCOUNT
        assert row["config"]["base_url"] is None
        assert row["subject"] == member_subject(state.member_id)
        assert row["owner_member_id"] == state.member_id
        assert row["connection_id"] == connection_id
        assert row["removed_at"] is None
    assert await _granted_agents(state) == {state.main_id}


async def test_a_connection_only_another_agent_holds_is_left_alone(db: None) -> None:
    state = await _workspace()

    await _connect(state, state.other_id, ASANA)

    assert await _rows(state) == []
    assert state.workspace_id not in set(await _runner().candidates(JOB_KEY))
    await _tick(state)
    assert await _rows(state) == []


async def test_a_per_tenant_provider_is_never_auto_registered(db: None) -> None:
    state = await _workspace()

    await _connect(state, state.main_id, FRESHDESK)
    await _tick(state)

    assert await _rows(state) == []


async def test_a_binding_the_member_deleted_does_not_return(db: None) -> None:
    state = await _workspace()
    await _connect(state, state.main_id, ASANA)
    with ws(state.workspace_id):
        for row in await _rows(state):
            await _ext().remove_source(row["id"])

    await _connect(state, state.main_id, ASANA)
    await _tick(state)

    rows = await _rows(state)
    assert {row["config"]["stream"] for row in rows} == _canonical(ASANA)
    assert all(row["removed_at"] is not None for row in rows)


async def test_a_member_narrows_an_auto_created_binding_and_it_stays_narrowed(db: None) -> None:
    """The feed this creates is the member's to narrow. One `object_apply source` naming fewer
    streams settles the binding on exactly those, and the dropped rows are removed rather than
    absent — which is the fact the per-stream gate reads, so the next tick leaves them alone. Any
    other answer would make syncing less mean deleting a binding the member never asked for, and
    the job would put back what the delete cleared."""
    state = await _workspace()
    await _connect(state, state.main_id, ASANA)
    kept = sorted(_canonical(ASANA))[0]

    await _narrow(state, (kept,))

    narrowed = await _rows(state)
    await _tick(state)
    assert {row["config"]["stream"] for row in narrowed if row["removed_at"] is None} == {kept}
    assert {row["config"]["stream"] for row in narrowed} == _canonical(ASANA)
    assert await _rows(state) == narrowed


async def test_a_canonical_stream_missing_from_the_members_binding_joins_it(db: None) -> None:
    state = await _workspace()
    await _connect_without_the_hook(state, state.main_id, ASANA)
    await _member_row(state, subject=member_subject(state.member_id), owner=state.member_id)

    await _tick(state)

    rows = await _rows(state)
    assert {row["config"]["stream"] for row in rows} == _canonical(ASANA)
    assert {row["subject"] for row in rows} == {member_subject(state.member_id)}
    assert {row["owner_member_id"] for row in rows} == {state.member_id}
    assert {row["config"]["backfill_days"] for row in rows} == {MEMBER_WINDOW_DAYS}


async def test_a_shared_binding_gains_nothing_the_member_left_out(db: None) -> None:
    """Auto-registration never widens disclosure. A member who shared one stream of an account chose
    what the whole workspace reads, so the streams they left out stay uncreated: registering them
    under the binding would stamp the workspace subject on content nobody selected, and each
    backfilled page of them would wake a `per_page` source trigger on that binding."""
    state = await _workspace()
    await _connect_without_the_hook(state, state.main_id, ASANA)
    shared = await _member_row(state, subject=SHARED_SUBJECT, owner=state.member_id)
    before = await _rows(state)

    await _tick(state)

    assert {row["config"]["stream"] for row in before} == {shared}
    assert await _rows(state) == before
