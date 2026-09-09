"""A connection's feeds are created with the connection, and the job retries what did not land.

Every assertion drives a real seam and reads back durable rows. The connect path runs
`ConnectFlow.complete` over an injected OAuth descriptor with this extension's
`connection_recorded` hook bound exactly as `serve` binds it, so the rows are asserted at the
instant the callback answers. The retry path runs the declared job through `JobRunner`, so its
candidates and handler are the ones exercised. Covered here: the callback leaves one row per
canonical stream on the connection, whichever agent asked for the connection; a per-tenant provider
registers nothing until the connection names a tenant URL and then registers on the next tick; the
job creates what a connect-time creation did not and adds nothing once the rows are there; a stream
a later connector release marks canonical joins a connection registered long ago; the connection's
`backfill_days` governs every stream that takes a window and no stream that declares none; and
raising that window re-pins the live rows from their own anchor and refetches them while lowering it
leaves them exactly where they are."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_sources import manifest as sources_manifest
from ufo_ext_sources.connected import ConnectedSources
from ufo_ext_sources.registry import CONNECTORS, direct_slots

from ufo.db import workspace_tx
from ufo.host.ext.loader import connection_hooks
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import ConnectFlow, GrantStore, OAuthAccount
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import ExtensionContext, context_for
from ufo.runtime.jobs import JobRunner, bindings_from
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.ids import uuid7
from ufo.sdk.sources import ConnectorSourceConfig, RestConnector, StreamSpec

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ASANA = "asana"
FRESHDESK = "freshdesk"
WINDOWED = "windowed"
KEYED = "klaviyo"
ACCOUNT = "acct-one"
REDIRECT_URI = "http://surface/v1/connect/callback"
TENANT_URL = "https://acme.freshdesk.com"
STREAM_WINDOW_DAYS = 30
RAISED_WINDOW_DAYS = 90
LOWERED_WINDOW_DAYS = 7
JOB_KEY = f"{sources_manifest.NAME}:{sources_manifest.CONNECTED_SOURCES_RETRY_JOB}"
DECLARED_SLOTS = frozenset(slot.name for slot in sources_manifest.manifest().credentials)
DATADOG = "datadog"
DATADOG_SLOTS = direct_slots(CONNECTORS[DATADOG])
DATADOG_SITE = "https://api.us5.datadoghq.com"


class _WindowedConnector(RestConnector):
    """A stand-in provider whose canonical set holds a windowed stream, a zero-day one, and one
    declaring no window at all — the three answers the registrar has to resolve. It stands in for
    the provider and is never the thing asserted — the rows the registrar writes are."""

    name = WINDOWED
    base_url = "https://api.windowed.test"

    def streams(self) -> list[StreamSpec]:
        return [
            StreamSpec(
                name="dated",
                source_object="dated",
                canonical=True,
                backfill_window_days=STREAM_WINDOW_DAYS,
            ),
            StreamSpec(
                name="instant", source_object="instant", canonical=True, backfill_window_days=0
            ),
            StreamSpec(name="undated", source_object="undated", canonical=True),
            StreamSpec(name="lookup", source_object="lookup"),
        ]


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
        providers={provider: _StubProvider(provider=provider) for provider in (ASANA, FRESHDESK)},
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


async def _connect_without_the_hook(
    state: _Workspace, provider: str, agent_id: UUID | None = None
) -> UUID:
    """A connection whose creation left no rows — the hook raised, or the process died after the
    connection committed. What the retry job exists for."""
    with ws(state.workspace_id), agent(agent_id or state.main_id):
        return await GrantStore().record(
            provider=provider,
            account_id=ACCOUNT,
            host=f"api.{provider}.test",
            grantor_member_id=state.member_id,
            shared=False,
        )


async def _set_window(state: _Workspace, connection_id: UUID, days: int | None) -> None:
    with ws(state.workspace_id), agent(state.main_id):
        assert (
            await GrantStore().set_feed(
                connection_id,
                base_url=None,
                backfill_days=days,
                actor_member_id=state.member_id,
            )
            is True
        )


async def _set_tenant_url(state: _Workspace, connection_id: UUID, base_url: str) -> None:
    with ws(state.workspace_id), agent(state.main_id):
        assert (
            await GrantStore().set_feed(
                connection_id,
                base_url=base_url,
                backfill_days=None,
                actor_member_id=state.member_id,
            )
            is True
        )


def _runner() -> JobRunner:
    declared = sources_manifest.manifest()
    return JobRunner(bindings=bindings_from((declared,), ()), manifests=(declared,))


async def _tick(state: _Workspace) -> None:
    await _runner().fire(JOB_KEY, state.workspace_id)


def _ext() -> ExtensionContext:
    return context_for(sources_manifest.NAME, DECLARED_SLOTS)


async def _register(state: _Workspace) -> None:
    """The registrar on its own, for the connectors the connect flow's stub providers do not
    serve — the same call the job's handler makes."""
    with ws(state.workspace_id), agent(state.main_id):
        await ConnectedSources(ext=_ext()).register()


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


async def _stamp_cursor(state: _Workspace, cursor: str) -> None:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(cursor=cursor)
                .where(tables.source.c.workspace_id == state.workspace_id)
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


def _pins(rows: list[sa.RowMapping]) -> dict[str, tuple[int | None, str | None]]:
    return {
        row["config"]["stream"]: (row["config"]["backfill_days"], row["config"]["backfill_after"])
        for row in rows
    }


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
    connection_id = await _connection_id(state)
    assert {row["config"]["stream"] for row in rows} == _canonical(ASANA)
    for row in rows:
        assert row["backend"] == ASANA
        assert row["connection_id"] == connection_id
        assert row["config"] == {
            "stream": row["config"]["stream"],
            "backfill_days": None,
            "backfill_after": None,
        }


async def test_a_connection_a_specialist_agent_recorded_gets_its_feeds_too(db: None) -> None:
    """The registrar asks nothing about grants: a connection is content the workspace is authorized
    to read, and which agents may reach it is the grant's answer at read time. A feed skipped
    because the connecting agent was not main would leave the account connected and silent."""
    state = await _workspace()

    await _connect(state, state.other_id, ASANA)

    assert {row["config"]["stream"] for row in await _rows(state)} == _canonical(ASANA)


async def test_the_retry_job_reaches_a_workspace_holding_only_a_specialists_connection(
    db: None,
) -> None:
    """The job's candidates are the workspaces holding a connection, not the ones whose main agent
    holds a grant. A connect whose hook did not finish is the whole reason the job exists, so a
    workspace reachable only through a specialist's connection has to fire like any other — under
    the narrower rule its account would stay connected and permanently silent."""
    state = await _workspace()
    await _connect_without_the_hook(state, ASANA, agent_id=state.other_id)
    assert await _rows(state) == []

    assert state.workspace_id in set(await _runner().candidates(JOB_KEY))
    await _tick(state)

    assert {row["config"]["stream"] for row in await _rows(state)} == _canonical(ASANA)


async def test_a_per_tenant_provider_waits_for_its_tenant_url(db: None) -> None:
    """A connector that declares no host of its own dials the connection's `base_url`, so a row
    written before the member names one would fail every run. It registers nothing and no state
    records the wait — the next tick reads the connection again and finds the URL there."""
    state = await _workspace()
    await _connect(state, state.main_id, FRESHDESK)
    await _tick(state)
    assert await _rows(state) == []

    await _set_tenant_url(state, await _connection_id(state), TENANT_URL)
    await _tick(state)

    assert {row["config"]["stream"] for row in await _rows(state)} == _canonical(FRESHDESK)


async def test_the_job_creates_what_the_callback_did_not_and_then_adds_nothing(db: None) -> None:
    state = await _workspace()
    await _connect_without_the_hook(state, ASANA)
    assert await _rows(state) == []

    await _tick(state)
    created = await _rows(state)
    await _tick(state)

    assert {row["config"]["stream"] for row in created} == _canonical(ASANA)
    assert await _rows(state) == created


async def test_a_stream_newly_marked_canonical_joins_a_connection_that_already_syncs(
    db: None,
) -> None:
    """The row a stream would take is derived from the connection and the stream, so a connector
    release that marks one more stream canonical reaches accounts connected long ago, and the rows
    already syncing are left exactly as they are."""
    state = await _workspace()
    connection_id = await _connect_without_the_hook(state, ASANA)
    first = sorted(_canonical(ASANA))[0]
    with ws(state.workspace_id), agent(state.main_id):
        await _ext().register_source(
            ASANA, ConnectorSourceConfig(stream=first), connection_id=connection_id
        )
    held = await _rows(state)

    await _tick(state)

    rows = await _rows(state)
    assert {row["config"]["stream"] for row in held} == {first}
    assert {row["config"]["stream"] for row in rows} == _canonical(ASANA)
    assert held[0]["id"] in {row["id"] for row in rows}


async def _fill_slot(state: _Workspace, slot: str, key: str) -> None:
    """A member adding a provider key — the whole of what they do for a feed no broker can grant."""
    init_workspace_credentials(CredentialStore(fernet=Fernet(Fernet.generate_key())))
    with ws(state.workspace_id):
        await ws_current().put_credential(slot, key)


async def _clear_slot(state: _Workspace, slot: str) -> None:
    await CredentialStore(fernet=Fernet(Fernet.generate_key())).clear(state.workspace_id, slot)


async def _connections(state: _Workspace) -> list[sa.RowMapping]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(tables.connection)
                        .where(tables.connection.c.workspace_id == state.workspace_id)
                        .order_by(tables.connection.c.provider)
                    )
                )
                .mappings()
                .all()
            )


async def test_a_filled_credential_slot_mints_its_connection_and_syncs_it(db: None) -> None:
    """A member fills a provider's key and nothing else: the workspace becomes a candidate on
    the strength of the credential alone — it holds no connection yet, so a candidate seam asking
    only about connections would never look at it — and the tick mints the workspace's own
    connection (no account handle, no owner, shared, because a connection nobody owns is nobody's to
    keep private) and registers its canonical streams. A second tick settles on what is there rather
    than minting a second connection or a second row."""
    state = await _workspace()
    await _fill_slot(state, KEYED, "pk_live_member_key")

    assert state.workspace_id in set(await _runner().candidates(JOB_KEY))
    await _tick(state)
    minted = await _connections(state)
    rows = await _rows(state)
    await _tick(state)

    assert [row["provider"] for row in minted] == [KEYED]
    assert (minted[0]["account_id"], minted[0]["owner_member_id"], minted[0]["shared"]) == (
        "",
        None,
        True,
    )
    assert {row["config"]["stream"] for row in rows} == _canonical(KEYED)
    assert {row["connection_id"] for row in rows} == {minted[0]["id"]}
    assert await _connections(state) == minted
    assert await _rows(state) == rows


async def test_a_cleared_credential_slot_removes_its_connection_and_pages(db: None) -> None:
    """The slot is the whole of a keyed feed's lifecycle. Clearing it removes the connection the
    tick minted, and its streams, their pages and its grants go with it — so nothing retries a key
    that is gone and nothing it synced stays recallable. An account a member connected beside an
    empty slot is not the registrar's to remove."""
    state = await _workspace()
    await _fill_slot(state, KEYED, "pk_live_member_key")
    owned = await _connect_without_the_hook(state, ASANA)
    await _tick(state)
    minted = next(row for row in await _connections(state) if row["provider"] == KEYED)
    landed = next(row for row in await _rows(state) if row["connection_id"] == minted["id"])
    page_id = uuid4()
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.page).values(
                    uid=uuid7(),
                    id=page_id,
                    workspace_id=state.workspace_id,
                    source_id=landed["id"],
                    source_uid=sa.select(tables.source.c.uid)
                    .where(tables.source.c.id == landed["id"])
                    .scalar_subquery(),
                    digest="d" * 64,
                    body_ref="pages/seed",
                    stream=landed["config"]["stream"],
                    title="seed",
                    subject="shared",
                    tombstone=False,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )

    await CredentialStore(fernet=Fernet(Fernet.generate_key())).clear(state.workspace_id, KEYED)
    await _tick(state)

    assert [row["id"] for row in await _connections(state)] == [owned]
    assert {row["connection_id"] for row in await _rows(state)} == {owned}
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            pages = (
                await connection.execute(
                    sa.select(tables.page.c.id).where(tables.page.c.id == page_id)
                )
            ).all()
    assert pages == []


async def test_a_provider_already_connected_mints_no_second_connection(db: None) -> None:
    """An account someone connected is the authority for that provider, so a key filled beside it
    changes nothing. A second connection would sync the same content twice, under two disclosures,
    on two credentials."""
    state = await _workspace()
    connection_id = await _connect_without_the_hook(state, ASANA)
    await _fill_slot(state, ASANA, "pk_live_member_key")

    await _tick(state)

    assert [row["id"] for row in await _connections(state)] == [connection_id]
    assert {row["connection_id"] for row in await _rows(state)} == {connection_id}


async def test_an_unfilled_slot_mints_nothing(db: None) -> None:
    """The platform's own default for a slot is not a member asking for a feed — only a key this
    workspace stored is."""
    state = await _workspace()
    init_workspace_credentials(CredentialStore(fernet=Fernet(Fernet.generate_key())))

    assert state.workspace_id not in set(await _runner().candidates(JOB_KEY))
    await _tick(state)

    assert await _connections(state) == []
    assert await _rows(state) == []


async def test_a_provider_no_connector_serves_registers_nothing(db: None) -> None:
    state = await _workspace()
    await _connect_without_the_hook(state, "unserved")

    await _tick(state)

    assert await _rows(state) == []


async def test_the_connections_window_governs_every_stream_that_takes_one(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The window resolves per row: the connection's where it names one, the stream's declaration
    where it does not, and none at all for a stream that declares none — such a stream reads its
    whole history, and a cutoff on it would be honoured by nothing. A zero-day declaration is a
    window, not the absence of one: it pins the row at the instant it was registered, so the stream
    reads forward from there rather than reading everything."""
    monkeypatch.setitem(CONNECTORS, WINDOWED, _WindowedConnector)
    silent, asking = await _workspace(), await _workspace()
    await _connect_without_the_hook(silent, WINDOWED)
    await _set_window(asking, await _connect_without_the_hook(asking, WINDOWED), RAISED_WINDOW_DAYS)

    await _register(silent)
    await _register(asking)

    declared, asked = _pins(await _rows(silent)), _pins(await _rows(asking))
    assert set(declared) == {"dated", "instant", "undated"} == set(asked)
    assert declared["dated"][0] == STREAM_WINDOW_DAYS
    assert declared["dated"][1] is not None
    assert asked["dated"][0] == RAISED_WINDOW_DAYS
    assert declared["instant"][0] == 0
    assert declared["instant"][1] is not None
    assert asked["instant"][0] == RAISED_WINDOW_DAYS
    assert declared["undated"] == asked["undated"] == (None, None)


async def test_raising_the_connections_window_repins_its_rows_and_refetches(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A raised window has to be fetched, not just recorded. The new floor is measured from the
    instant the row was registered — its own pin plus the days it was pinned with — so widening an
    old connection cannot pin a later floor than the one it replaces, and the cursor is dropped so
    the next run re-walks from there."""
    monkeypatch.setitem(CONNECTORS, WINDOWED, _WindowedConnector)
    state = await _workspace()
    connection_id = await _connect_without_the_hook(state, WINDOWED)
    await _register(state)
    await _stamp_cursor(state, "watermark")
    before = _pins(await _rows(state))

    await _set_window(state, connection_id, RAISED_WINDOW_DAYS)
    await _register(state)

    rows = await _rows(state)
    after = _pins(rows)
    dated = next(row for row in rows if row["config"]["stream"] == "dated")
    undated = next(row for row in rows if row["config"]["stream"] == "undated")
    assert after["dated"][0] == RAISED_WINDOW_DAYS
    assert datetime.fromisoformat(str(after["dated"][1])) == datetime.fromisoformat(
        str(before["dated"][1])
    ) - timedelta(days=RAISED_WINDOW_DAYS - STREAM_WINDOW_DAYS)
    assert dated["cursor"] is None
    assert after["undated"] == (None, None)
    assert undated["cursor"] == "watermark"


async def test_lowering_the_connections_window_leaves_live_rows_where_they_are(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Narrowing would strand the pages between the two floors — never re-walked, never tombstoned
    — so a lowered window leaves every live row exactly as it is, cursor included, and governs only
    the rows registered after it.

    The connection carries one integer for streams that declare different windows, so which
    direction it moved is each row's own answer, not the connection's: the same seven days narrow
    the thirty-day stream and widen the zero-day one. Reading the direction off the connection
    would either strand the narrowed row's pages or leave the widened row never reaching back."""
    monkeypatch.setitem(CONNECTORS, WINDOWED, _WindowedConnector)
    state = await _workspace()
    connection_id = await _connect_without_the_hook(state, WINDOWED)
    await _register(state)
    await _stamp_cursor(state, "watermark")
    before = _pins(await _rows(state))

    await _set_window(state, connection_id, LOWERED_WINDOW_DAYS)
    await _register(state)

    rows = await _rows(state)
    after = _pins(rows)
    cursors = {row["config"]["stream"]: row["cursor"] for row in rows}
    assert after["dated"] == before["dated"]
    assert after["undated"] == before["undated"]
    assert cursors["dated"] == cursors["undated"] == "watermark"
    assert after["instant"][0] == LOWERED_WINDOW_DAYS
    assert datetime.fromisoformat(str(after["instant"][1])) == datetime.fromisoformat(
        str(before["instant"][1])
    ) - timedelta(days=LOWERED_WINDOW_DAYS)
    assert cursors["instant"] is None


async def test_a_row_that_took_the_streams_declared_window_repins_from_that_window(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row the old registrar pinned without naming a window — `backfill_days` None beside a
    resolved `backfill_after` — was pinned to the stream's declared window, so that is what its
    registration instant is measured from. Every connected-account row on a deploy that predates
    the connection's own window has this shape, so raising the window must re-pin it rather than
    refuse the whole connection."""
    monkeypatch.setitem(CONNECTORS, WINDOWED, _WindowedConnector)
    state = await _workspace()
    connection_id = await _connect_without_the_hook(state, WINDOWED)
    await _register(state)
    before = _pins(await _rows(state))
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    sa.select(tables.source.c.id, tables.source.c.config).where(
                        tables.source.c.workspace_id == state.workspace_id
                    )
                )
            ).all()
            for row in rows:
                await connection.execute(
                    sa.update(tables.source)
                    .values(config={**row.config, "backfill_days": None})
                    .where(tables.source.c.id == row.id)
                )

    await _set_window(state, connection_id, RAISED_WINDOW_DAYS)
    await _register(state)

    after = _pins(await _rows(state))
    assert after["dated"][0] == RAISED_WINDOW_DAYS
    assert datetime.fromisoformat(str(after["dated"][1])) == datetime.fromisoformat(
        str(before["dated"][1])
    ) - timedelta(days=RAISED_WINDOW_DAYS - STREAM_WINDOW_DAYS)
    assert after["undated"] == (None, None)


async def test_a_provider_keyed_by_two_slots_mints_only_once_both_are_filled(db: None) -> None:
    """Datadog refuses a read carrying an API key with no application key beside it, so its slots
    are its feed's lifecycle together rather than one at a time. One of the pair mints nothing — a
    connection that cannot authenticate would only park — and the workspace is a candidate on the
    strength of that one slot, because the registrar is what has to look and decide."""
    state = await _workspace()
    await _fill_slot(state, DATADOG_SLOTS[0], "dd-api")

    assert state.workspace_id in set(await _runner().candidates(JOB_KEY))
    await _tick(state)
    assert [row["provider"] for row in await _connections(state)] == []

    await _fill_slot(state, DATADOG_SLOTS[1], "dd-app")
    await _tick(state)

    minted = await _connections(state)
    assert [row["provider"] for row in minted] == [DATADOG]
    assert await _rows(state) == []

    await _set_tenant_url(state, minted[0]["id"], DATADOG_SITE)
    await _tick(state)
    assert {row["config"]["stream"] for row in await _rows(state)} == _canonical(DATADOG)


async def test_clearing_one_key_of_a_pair_keeps_the_feed_and_clearing_both_removes_it(
    db: None,
) -> None:
    """A member rotating one key of a pair is not asking for their synced pages to be destroyed, so
    the connection stands while the pair is half filled — its runs skip until the slot is filled
    again. Clearing both is the off switch, and then the connection and its rows go."""
    state = await _workspace()
    for slot, key in zip(DATADOG_SLOTS, ("dd-api", "dd-app"), strict=True):
        await _fill_slot(state, slot, key)
    await _tick(state)
    minted = await _connections(state)
    assert [row["provider"] for row in minted] == [DATADOG]
    await _set_tenant_url(state, minted[0]["id"], DATADOG_SITE)
    await _tick(state)

    await _clear_slot(state, DATADOG_SLOTS[1])
    await _tick(state)
    assert [row["id"] for row in await _connections(state)] == [minted[0]["id"]]
    assert {row["config"]["stream"] for row in await _rows(state)} == _canonical(DATADOG)

    await _clear_slot(state, DATADOG_SLOTS[0])
    await _tick(state)
    assert await _connections(state) == []
    assert await _rows(state) == []
