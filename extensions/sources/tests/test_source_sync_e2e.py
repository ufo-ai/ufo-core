"""A connection's feed through durable pages and the memory tool, on both auth paths: a broker
connection resolving through its broker, and the workspace's own connection resolving a member-added
key through the `direct` backend — plus the pinned backfill window holding across a real
`CursorExpired` reset, registration to wire."""

import asyncio
import base64
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
import ufo_ext_composio.client as composio
import ufo_ext_composio.manifest as composio_manifest
import ufo_ext_memory.manifest as memory_manifest
import ufo_ext_pipedream.client as pipedream
import ufo_ext_pipedream.manifest as pipedream_manifest
import ufo_ext_sources.manifest as sources_manifest
from cryptography.fernet import Fernet
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import PageIndexer
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.providers.klaviyo import KLAVIYO_REVISION, KlaviyoConnector

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.runtime.access.connectors import (
    AuthProxy,
    ConnectorEntry,
    ConnectorRegistry,
    SourceCredentialResolver,
)
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.access.grants import GrantStore
from ufo.runtime.agent_scope import agent
from ufo.runtime.ext.context import context_for
from ufo.runtime.indexing import TextChunker
from ufo.runtime.sources import rest
from ufo.runtime.sources.sync import CorePageFeed, SyncDriver
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.serve import _select_auth_proxy, _source_backends

TOOL_NARRATION = "syncing their pages"

ASANA_ACCOUNT = "ca_asana_e2e"
GMAIL_ACCOUNT = "apn_gmail_e2e"
KLAVIYO_KEY = "pk_live_byok_e2e"
MEMBER_WINDOW_DAYS = 7
DUE_AGAIN_AT = datetime(2000, 1, 1, tzinfo=UTC)
KLAVIYO_PROFILE = {
    "type": "profile",
    "id": "p1",
    "attributes": {
        "email": "ada@windward.test",
        "organization": "Windward Summit Logistics",
        "title": "Fleet operations lead",
        "updated": "2026-02-01T00:00:00+00:00",
    },
}


class StubEmbed:
    def __init__(self) -> None:
        values = [0.0] * EMBED_DIM
        values[11] = 1.0
        self.vector = tuple(values)

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.vector for _ in texts)


@dataclass(frozen=True)
class State:
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


async def _state() -> State:
    state = State(uuid4(), uuid4(), uuid4(), uuid4())
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=state.workspace_id, created_at=now, updated_at=now
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=state.member_id,
                workspace_id=state.workspace_id,
                email=f"{state.member_id.hex}@source.test",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=state.agent_id,
                workspace_id=state.workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                is_main=True,
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=state.conversation_id,
                workspace_id=state.workspace_id,
                agent_id=state.agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=state.member_id,
                created_at=now,
                updated_at=now,
            )
        )
    return state


def _context(state: State, grants: GrantStore, connectors: ConnectorRegistry) -> ToolContext:
    manifest = sources_manifest.manifest()
    return ToolContext(
        sandbox=None,
        blob=None,
        turn=Turn(
            id=uuid4(),
            workspace_id=state.workspace_id,
            conversation_id=state.conversation_id,
            agent_id=state.agent_id,
            seq=1,
            status="running",
            inbound="connect this source",
            created_at=datetime.now(UTC),
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=None,
        speaker_member_id=state.member_id,
        audience=conversation_audience(state.member_id),
        artifact_token_secret="",
        grants=grants,
        connectors=connectors,
        ext=context_for(manifest.name, frozenset(slot.name for slot in manifest.credentials)),
    )


def _registry(manifest: object, provider: str, fallback: AuthProxy) -> ConnectorRegistry:
    explicit = {connector.oauth.provider: connector for connector in manifest.connectors}.get(
        provider
    )
    entries = (
        {provider: ConnectorEntry(provider=provider, label=explicit.label, broker=explicit.broker)}
        if explicit is not None
        else {}
    )
    return ConnectorRegistry(
        entries=entries, resolver=manifest.connector_resolver, fallback=fallback
    )


def _selected_fallback(store: CredentialStore, broker: object) -> DirectAuthProxy:
    """The fallback backend a deploy installing sources plus a broker extension actually gets, built
    by core's own boot selection rather than by hand: sources registers `direct`, a broker extension
    registers none, so the sole backend is automatic with `[connectors] auth_backend` unset."""
    fallback = _select_auth_proxy(
        Config.model_validate(
            {
                "database": {"url": "sqlite+aiosqlite:///unused.db"},
                "blob": {"backend": "filesystem", "root": "/tmp/unused"},
            }
        ),
        (sources_manifest.manifest(), broker),
        store,
    )
    assert isinstance(fallback, DirectAuthProxy)
    return fallback


async def _register_grant(
    state: State,
    grants: GrantStore,
    provider: str,
    account: str,
    host: str,
    *,
    member_id: UUID | None = None,
    conversation_id: UUID | None = None,
) -> UUID:
    with ws(state.workspace_id), agent(state.agent_id):
        return await grants.record(
            provider=provider,
            account_id=account,
            host=host,
            grantor_member_id=member_id or state.member_id,
            shared=False,
        )


async def _sync_and_search(
    state: State,
    context: ToolContext,
    provider: str,
    connection_id: UUID,
    stream: str,
    query: str,
    database_url: str,
    tmp_path: Path,
) -> tuple[str, SyncDriver]:
    with ws(state.workspace_id), agent(state.agent_id):
        await context.ext.register_source(
            provider, ConnectorSourceConfig(stream=stream), connection_id=connection_id
        )
        driver = SyncDriver(
            blob=FilesystemBlobStore(root=tmp_path / "blobs"),
            postgres=database_url.startswith("postgresql"),
            backends=_source_backends((sources_manifest.manifest(),)),
            source_credentials=SourceCredentialResolver(context.connectors),
        )
        await driver.run()

        embed = StubEmbed()
        index = DefaultIndex(transaction=workspace_tx)
        feed = CorePageFeed(blob=driver.blob)
        indexer = PageIndexer(
            index=index,
            embed=embed,
            transaction=workspace_tx,
            chunker=TextChunker(),
            workspace_id=state.workspace_id,
            page_states=context_for("memory", frozenset()).page_states,
        )
        await indexer.apply((await feed.pages_changed_since(None, 50)).changes)

        memory = memory_manifest.manifest()
        search = next(tool for tool in memory.tools if tool.name == "memory_search")
        result = await search.handler(
            replace(
                context,
                ext=context_for(memory.name, frozenset(), index=index, embed=embed),
            ),
            search.input_model.model_validate({"queries": [query]}),
        )
    return result.content[0].text, driver


async def _run_due(state: State, driver: SyncDriver) -> int:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(
                    next_sync_at=DUE_AGAIN_AT,
                    claimed_by=None,
                    claim_expires_at=None,
                )
                .where(tables.source.c.workspace_id == state.workspace_id)
            )
        await driver.run()
        async with workspace_tx() as connection:
            return int(
                (
                    await connection.execute(
                        sa.select(tables.source.c.consecutive_errors).where(
                            tables.source.c.workspace_id == state.workspace_id
                        )
                    )
                ).scalar_one()
            )


async def _add_member(state: State) -> tuple[UUID, UUID]:
    member_id, conversation_id = uuid4(), uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=state.workspace_id,
                email=f"{member_id.hex}@source.test",
                created_at=now,
                updated_at=now,
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=state.workspace_id,
                agent_id=state.agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=now,
                updated_at=now,
            )
        )
    return member_id, conversation_id


def _composio_transport(workspace_id: UUID, *, dial_faults: int = 0) -> httpx.MockTransport:
    """Composio mocked: the connected-account read names `workspace_id`'s broker user, and
    proxy-execute answers the Asana workspaces page — after answering its own 400 `Connection
    failed` to the first `dial_faults` calls, the shape Composio gives a dial to the provider that
    failed on its side."""
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    faults_left = [dial_faults]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and "/connected_accounts/" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "id": ASANA_ACCOUNT,
                    "user_id": owner,
                    "status": "ACTIVE",
                    "toolkit": {"slug": "asana"},
                },
            )
        if request.method == "POST" and request.url.path.endswith("/tools/execute/proxy"):
            payload = json.loads(request.content)
            assert payload["connected_account_id"] == ASANA_ACCOUNT
            assert httpx.URL(payload["endpoint"]).path.endswith("/workspaces")
            if faults_left[0] > 0:
                faults_left[0] -= 1
                return httpx.Response(
                    400,
                    json={
                        "error": {
                            "message": f"Connection failed to {payload['endpoint']}: fetch failed. "
                            "Please verify the endpoint is accessible."
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "data": {
                        "data": {
                            "data": [{"gid": "111", "name": "Orbital launch workspace Alpha"}],
                            "next_page": None,
                        },
                        "status": 200,
                        "headers": {},
                    }
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return httpx.MockTransport(handle)


def _gmail_message() -> dict[str, object]:
    body = base64.urlsafe_b64encode(b"The zephyr launch review is Friday.").decode()
    return {
        "id": "m1",
        "threadId": "t1",
        "historyId": "9001",
        "labelIds": ["INBOX"],
        "payload": {
            "mimeType": "text/plain",
            "headers": [
                {"name": "From", "value": "Ada <ada@example.com>"},
                {"name": "To", "value": "team@example.com"},
                {"name": "Subject", "value": "Zephyr launch review"},
            ],
            "body": {"data": body},
        },
    }


def _pipedream_transport(workspace_id: UUID) -> httpx.MockTransport:
    owner = pipedream.connection_user_id(workspace_id, "e2e")

    def provider(target: str) -> httpx.Response:
        url = httpx.URL(target)
        if url.path == "/gmail/v1/users/me/messages":
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if url.path == "/gmail/v1/users/me/profile":
            return httpx.Response(200, json={"emailAddress": "e2e@example.com", "historyId": "1"})
        if url.path == "/gmail/v1/users/me/history":
            return httpx.Response(200, json={"historyId": "9001"})
        if url.path.endswith("/messages/m1"):
            query = parse_qs(url.query.decode())
            if query.get("format") == ["minimal"]:
                return httpx.Response(200, json={"id": "m1", "historyId": "9001"})
            return httpx.Response(200, json=_gmail_message())
        return httpx.Response(404, json={"target": target})

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.method == "GET" and f"/accounts/{GMAIL_ACCOUNT}" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": GMAIL_ACCOUNT,
                        "external_id": owner,
                        "healthy": True,
                        "app": {"name_slug": "gmail"},
                    }
                },
            )
        if "/proxy/" in request.url.path:
            assert request.url.params["account_id"] == GMAIL_ACCOUNT
            encoded = request.url.path.rsplit("/", 1)[-1]
            target = base64.urlsafe_b64decode(
                (encoded + "=" * (-len(encoded) % 4)).encode()
            ).decode()
            return provider(target)
        return httpx.Response(404, json={"path": request.url.path})

    return httpx.MockTransport(handle)


@pytest.mark.parametrize("provider", ["asana", "gmail"])
async def test_brokered_source_reaches_memory_search(
    provider: str,
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A connected account's source syncs through its broker with the `direct` fallback installed
    alongside — the deploy shape the connection routing has to keep. The fallback's store holds no
    key for either provider, so a source that took it would fail its run and recall nothing.
    Disconnecting then takes the source row and every page it landed by cascade, and a second
    member connecting the same account gets a connection, a row and a feed of their own."""
    state = await _state()
    grants = GrantStore()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    if provider == "asana":
        client = composio.ComposioClient(
            api_key="test", transport=_composio_transport(state.workspace_id)
        )
        monkeypatch.setattr(composio, "composio_client", lambda: client)
        broker_manifest = composio_manifest.manifest()
        connectors = _registry(
            broker_manifest, provider, _selected_fallback(store, broker_manifest)
        )
        account, host, stream, query = (
            ASANA_ACCOUNT,
            "app.asana.com",
            "workspaces",
            "orbital launch workspace",
        )
    else:
        client = pipedream.PipedreamClient(
            client_id=f"cid_{uuid4().hex}",
            client_secret="secret",
            project_id="project",
            transport=_pipedream_transport(state.workspace_id),
        )
        monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)
        broker_manifest = pipedream_manifest.manifest()
        connectors = _registry(
            broker_manifest, provider, _selected_fallback(store, broker_manifest)
        )
        account, host, stream, query = (
            GMAIL_ACCOUNT,
            "gmail.googleapis.com",
            "messages",
            "zephyr launch review",
        )
    connection_id = await _register_grant(state, grants, provider, account, host)
    context = _context(state, grants, connectors)

    with ws(state.workspace_id), agent(state.agent_id):
        (grant,) = await grants.active_grants()
    resolved = (
        await SourceCredentialResolver(connectors)
        .bind(connection_id)
        .credential(state.workspace_id, provider)
    )
    assert resolved.transport is not None
    assert resolved.bearer is None

    recalled, driver = await _sync_and_search(
        state, context, provider, connection_id, stream, query, database_url, tmp_path / provider
    )

    assert query.lower() in recalled.lower()
    with ws(state.workspace_id), agent(state.agent_id):
        assert (
            await grants.revoke(
                grant.id,
                actor_member_id=state.member_id,
            )
            is True
        )
    assert await _run_due(state, driver) == 0
    with ws(state.workspace_id), agent(state.agent_id):
        assert (
            await grants.disconnect(
                connection_id,
                actor_member_id=state.member_id,
            )
            is True
        )
    assert state.workspace_id not in await driver.candidate_workspaces()
    async with workspace_tx() as connection:
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.source)
                .where(tables.source.c.workspace_id == state.workspace_id)
            )
        ).scalar_one() == 0
        assert (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.page)
                .where(tables.page.c.workspace_id == state.workspace_id)
            )
        ).scalar_one() == 0

    other_member, other_conversation = await _add_member(state)
    rebound_id = await _register_grant(
        state,
        grants,
        provider,
        account,
        host,
        member_id=other_member,
        conversation_id=other_conversation,
    )
    assert state.workspace_id not in await driver.candidate_workspaces()
    bob = replace(
        state,
        member_id=other_member,
        conversation_id=other_conversation,
    )
    bob_context = _context(bob, grants, connectors)
    recalled, _ = await _sync_and_search(
        bob,
        bob_context,
        provider,
        rebound_id,
        stream,
        query,
        database_url,
        tmp_path / f"{provider}-bob",
    )
    assert query.lower() in recalled.lower()
    async with workspace_tx() as connection:
        (rebound,) = (
            await connection.execute(sa.select(tables.source.c.uid, tables.source.c.connection_id))
        ).all()
        connection_owner = (
            await connection.execute(
                sa.select(tables.connection.c.owner_member_id).where(
                    tables.connection.c.id == rebound.connection_id
                )
            )
        ).scalar_one()
        live_pages = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.page)
                .where(
                    tables.page.c.source_uid == rebound.uid,
                    tables.page.c.tombstone.is_(False),
                )
            )
        ).scalar_one()
    assert rebound.connection_id == rebound_id
    assert rebound_id != connection_id
    assert connection_owner == other_member
    assert live_pages > 0


async def _klaviyo_listener(seen: list[tuple[str, dict[str, str]]]) -> asyncio.Server:
    """The provider's own host as a real listener: the direct backend resolves a `bearer`, not a
    broker transport, so the connector builds its own client and dials a socket. This is what that
    dial reaches, and `seen` is the request line and headers it actually carried."""
    payload = json.dumps({"data": [KLAVIYO_PROFILE], "links": {"next": None}}).encode()

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = head.split("\r\n")
        headers = {
            name.lower(): value
            for name, _, value in (line.partition(": ") for line in lines[1:])
            if name
        }
        seen.append((lines[0].split(" ")[1], headers))
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(payload) + payload
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


async def test_a_brokered_run_survives_the_broker_failing_to_reach_the_provider(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Composio's dial to the provider fails on one page request and the run still lands the page:
    the transport raises the broker's own 400 as a transport fault, the connector's retry envelope
    re-asks, and the row ends the run clean. Handed through as the provider's 400, the same answer
    ended the run with nothing written and a failure on the row — the fault a multi-hour fan-out met
    on every run."""
    monkeypatch.setattr(rest, "RETRY_INITIAL_DELAY_SECONDS", 0.0)
    state = await _state()
    grants = GrantStore()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    client = composio.ComposioClient(
        api_key="test", transport=_composio_transport(state.workspace_id, dial_faults=1)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "asana", _selected_fallback(store, broker_manifest))
    connection_id = await _register_grant(state, grants, "asana", ASANA_ACCOUNT, "app.asana.com")
    context = _context(state, grants, connectors)

    recalled, _ = await _sync_and_search(
        state,
        context,
        "asana",
        connection_id,
        "workspaces",
        "orbital launch workspace",
        database_url,
        tmp_path,
    )

    assert "orbital launch workspace" in recalled.lower()
    async with workspace_tx() as connection:
        errors = (
            await connection.execute(
                sa.select(tables.source.c.consecutive_errors).where(
                    tables.source.c.workspace_id == state.workspace_id
                )
            )
        ).scalar_one()
    assert errors == 0


async def test_keyed_source_reaches_memory_search_with_the_broker_namespace_installed(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BYOK path in the deploy shape that broke it: composio's open namespace is installed and
    claims every slug, including klaviyo — which composio cannot broker at all. The source hangs off
    the workspace's own connection, which holds no account handle, so its run must resolve through
    the `direct` backend instead.

    Nothing between the member's key and the provider wire is stood in for: the key is stored
    encrypted and read back through the workspace credential store, the fallback is the one core's
    boot selection builds, and the provider is a real socket — so the proof is the `Authorization`
    header klaviyo's own scheme put on the request. Composio's client answers 404 to everything and
    records each call; an empty record is the broker never being asked."""
    state = await _state()
    broker_calls: list[str] = []

    def composio_handler(request: httpx.Request) -> httpx.Response:
        broker_calls.append(request.url.path)
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    connectors = _registry(broker_manifest, "klaviyo", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)

    seen: list[tuple[str, dict[str, str]]] = []
    listener = await _klaviyo_listener(seen)
    monkeypatch.setattr(
        KlaviyoConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await ws_current().put_credential("klaviyo", KLAVIYO_KEY)
            connection_id = await context.ext.register_connection("klaviyo")
        recalled, _driver = await _sync_and_search(
            state,
            context,
            "klaviyo",
            connection_id,
            "profiles",
            "windward summit logistics",
            database_url,
            tmp_path / "klaviyo",
        )
    finally:
        listener.close()
        await listener.wait_closed()

    assert "windward summit logistics" in recalled.lower()
    assert broker_calls == []
    assert len(seen) == 1
    target, headers = seen[0]
    assert target.startswith("/api/profiles")
    assert headers["authorization"] == f"Klaviyo-API-Key {KLAVIYO_KEY}"
    assert headers["revision"] == KLAVIYO_REVISION


async def test_a_pinned_window_survives_a_cursor_reset_through_the_whole_path(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The constraint the design calls the one that matters most, end to end: a row is pinned to a
    7-day mail window, it syncs, its `historyId` ages out, and the driver clears the cursor — and
    the second backfill has to ask the provider for the same instant the registration pinned. A
    window recomputed per run (`now` minus the declared 30 days, or minus the connection's 7) sends
    a different floor here, and every message between the two floors is then dropped for good: mail
    is not `delete_missing`, so nothing tombstones or revisits it. The floor is read off the wire,
    the pin off the row the driver left behind. The one thing handed in is the instant registration
    reads as `now`, six hours back, because Gmail's floor crosses the wire in whole seconds: a floor
    recomputed from the connection's own 7 days would otherwise be caught only when the two runs
    fall in different seconds, which is a coin flip over a run this short."""
    state = await _state()
    grants = GrantStore()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    owner = pipedream.connection_user_id(state.workspace_id, "e2e")
    queries: list[str | None] = []
    aged_out: list[bool] = [False]

    def provider(target: str) -> httpx.Response:
        url = httpx.URL(target)
        if url.path == "/gmail/v1/users/me/messages":
            queries.append(parse_qs(url.query.decode()).get("q", [None])[0])
            return httpx.Response(200, json={"messages": [{"id": "m1"}]})
        if url.path == "/gmail/v1/users/me/profile":
            return httpx.Response(200, json={"emailAddress": "e2e@example.com", "historyId": "1"})
        if url.path == "/gmail/v1/users/me/history":
            if aged_out[0]:
                return httpx.Response(404, json={"error": {"code": 404}})
            return httpx.Response(200, json={"historyId": "9001"})
        if url.path.endswith("/messages/m1"):
            if parse_qs(url.query.decode()).get("format") == ["minimal"]:
                return httpx.Response(200, json={"id": "m1", "historyId": "9001"})
            return httpx.Response(200, json=_gmail_message())
        return httpx.Response(404, json={"target": target})

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/oauth/token":
            return httpx.Response(200, json={"access_token": "at", "expires_in": 3600})
        if request.method == "GET" and f"/accounts/{GMAIL_ACCOUNT}" in request.url.path:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "id": GMAIL_ACCOUNT,
                        "external_id": owner,
                        "healthy": True,
                        "app": {"name_slug": "gmail"},
                    }
                },
            )
        if "/proxy/" in request.url.path:
            encoded = request.url.path.rsplit("/", 1)[-1]
            return provider(
                base64.urlsafe_b64decode((encoded + "=" * (-len(encoded) % 4)).encode()).decode()
            )
        return httpx.Response(404, json={"path": request.url.path})

    client = pipedream.PipedreamClient(
        client_id=f"cid_{uuid4().hex}",
        client_secret="secret",
        project_id="project",
        transport=httpx.MockTransport(handle),
    )
    monkeypatch.setattr(pipedream, "pipedream_client", lambda: client)
    broker_manifest = pipedream_manifest.manifest()
    connectors = _registry(broker_manifest, "gmail", _selected_fallback(store, broker_manifest))
    connection_id = await _register_grant(
        state, grants, "gmail", GMAIL_ACCOUNT, "gmail.googleapis.com"
    )
    context = _context(state, grants, connectors)
    registered_at = datetime.now(UTC) - timedelta(hours=6)

    with ws(state.workspace_id), agent(state.agent_id):
        await context.ext.register_source(
            "gmail",
            ConnectorSourceConfig(
                stream="messages",
                backfill_days=MEMBER_WINDOW_DAYS,
                backfill_after=registered_at - timedelta(days=MEMBER_WINDOW_DAYS),
            ),
            connection_id=connection_id,
        )
        driver = SyncDriver(
            blob=FilesystemBlobStore(root=tmp_path / "blobs"),
            postgres=database_url.startswith("postgresql"),
            backends=_source_backends((sources_manifest.manifest(),)),
            source_credentials=SourceCredentialResolver(connectors),
        )
        await driver.run()
    backfilled = await _source_row(state)
    pinned = datetime.fromisoformat(str(backfilled.config["backfill_after"]))
    assert backfilled.cursor == "9001"
    assert pinned == registered_at - timedelta(days=MEMBER_WINDOW_DAYS)

    aged_out[0] = True
    assert await _run_due(state, driver) == 1
    reset = await _source_row(state)
    assert reset.cursor is None
    assert reset.config == backfilled.config

    aged_out[0] = False
    assert await _run_due(state, driver) == 0
    assert queries == [f"after:{int(pinned.timestamp())}"] * 2


async def test_a_feed_whose_only_reader_is_archived_makes_no_candidate_workspace(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A source the archive took every reader from is nobody's feed, so its workspace is not a
    candidate — and a shared connection standing beside it, holding no source of its own, does not
    make it one. Whether a feed has a reader is a fact of the source's OWN connection, so a
    workspace holding one shared connection anywhere cannot make every feed in it look readable.
    Restoring the agent is the control: the same workspace becomes a candidate again, so the
    exclusion is the archive and not a row that was never due."""
    state = await _state()
    archived_id = uuid4()
    now = datetime.now(UTC)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=archived_id,
                workspace_id=state.workspace_id,
                name="sweep",
                prompt="p",
                model="claude-opus-4-8",
                created_at=now,
                updated_at=now,
            )
        )
    with ws(state.workspace_id), agent(archived_id):
        held = await GrantStore().record(
            provider="asana",
            account_id=ASANA_ACCOUNT,
            host="app.asana.com",
            grantor_member_id=state.member_id,
            shared=False,
        )
        ext = context_for(sources_manifest.NAME, frozenset())
        await ext.register_source(
            "asana", ConnectorSourceConfig(stream="workspaces"), connection_id=held
        )
        await ext.register_connection("klaviyo")
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((sources_manifest.manifest(),)),
    )

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(archived_at=now, archived_name=tables.agent.c.name)
            .where(tables.agent.c.id == archived_id)
        )
    archived_candidates = await driver.candidate_workspaces()

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.agent)
            .values(archived_at=None, archived_name=None)
            .where(tables.agent.c.id == archived_id)
        )
    restored_candidates = await driver.candidate_workspaces()

    assert state.workspace_id not in archived_candidates
    assert state.workspace_id in restored_candidates


async def _source_row(state: State) -> sa.Row[Any]:
    """The workspace's one source row, config and cursor, as the driver left it."""
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(tables.source.c.config, tables.source.c.cursor).where(
                        tables.source.c.workspace_id == state.workspace_id
                    )
                )
            ).one()
