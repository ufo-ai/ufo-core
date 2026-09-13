"""A connection's feed through durable pages and the memory tool, on both auth paths: a broker
connection resolving through its broker, and the workspace's own connection resolving a member-added
key through the `direct` backend — plus the pinned backfill window holding across a real
`CursorExpired` reset, registration to wire."""

import asyncio
import base64
import hashlib
import json
from collections.abc import Mapping
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
from ufo_ext_sources.providers.github import GitHubConnector
from ufo_ext_sources.providers.klaviyo import KLAVIYO_REVISION, KlaviyoConnector
from ufo_ext_sources.tools import on_page_change
from ufo_ext_sources.triggers import SourceTriggerStore

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
from ufo.runtime.ext.manifest import HookContext, PageChangeBatch
from ufo.runtime.indexing import TextChunker
from ufo.runtime.sources import rest
from ufo.runtime.sources.sync import CorePageFeed, SyncDriver
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker
from ufo.runtime.tools.context import ToolContext
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.sdk.audience import conversation_audience
from ufo.sdk.sources import ConnectorSourceConfig
from ufo.serve import _select_auth_proxy, _source_backends, _source_watch_readers

TOOL_NARRATION = "syncing their pages"

ASANA_ACCOUNT = "ca_asana_e2e"
GMAIL_ACCOUNT = "apn_gmail_e2e"
KLAVIYO_KEY = "pk_live_byok_e2e"
GITHUB_KEY = "ghp_byok_e2e"
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


WATCHED_PULL = "https://github.com/acme/repo1/pull/3122"
WATCHED_PULL_IDENTITY = "pull_requests/acme/repo1/9003122"
PULL_STREAMS = ("organizations", "repositories", "pull_requests")


def _pull_node(number: int, checks: str) -> dict[str, Any]:
    return {
        "databaseId": int(f"900{number}"),
        "number": number,
        "title": "Add retry to egress dial",
        "state": "OPEN",
        "isDraft": False,
        "mergeable": "MERGEABLE",
        "reviewDecision": None,
        "updatedAt": "2026-01-05T00:00:00Z",
        "createdAt": "2026-01-01T00:00:00Z",
        "author": {"login": "ada"},
        "headRefName": "work",
        "baseRefName": "main",
        "url": f"https://github.com/acme/repo1/pull/{number}",
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {
                            "state": checks,
                            "contexts": {"nodes": [{"name": "ci", "conclusion": checks}]},
                        }
                    }
                }
            ]
        },
        "reviews": {"nodes": []},
        "reviewThreads": {"nodes": []},
        "files": {"nodes": [{"path": "a.py", "additions": 1, "deletions": 0}]},
        "timelineItems": {"nodes": []},
    }


def _graphql_answer(request_body: dict[str, Any], checks: str) -> dict[str, Any]:
    variables = request_body["variables"]
    repository = (
        {"pullRequest": _pull_node(variables["number"], checks)}
        if "number" in variables
        else {
            "pullRequests": {
                "nodes": [_pull_node(4, "SUCCESS")],
                "pageInfo": {"hasNextPage": False, "endCursor": None},
            }
        }
    )
    return {
        "data": {
            "rateLimit": {"cost": 3, "remaining": 4000, "resetAt": "2026-09-12T23:00:00Z"},
            "repository": repository,
        }
    }


PAGE_BATCH = 50


def _sources_context(invoker: object | None = None) -> Any:
    manifest = sources_manifest.manifest()
    declared = frozenset(slot.name for slot in manifest.credentials)
    return context_for(manifest.name, declared, invoker=invoker)


@dataclass
class _StubDbos:
    async def enqueue_async(self, options: object, workspace_id: str, workflow_id: str) -> None:
        return None


GITHUB_BODIES: dict[str, list[dict[str, Any]]] = {
    "/user/orgs": [{"login": "acme", "id": 1}],
    "/orgs/acme/repos": [
        {
            "id": 100,
            "name": "repo1",
            "full_name": "acme/repo1",
            "owner": {"login": "acme"},
            "updated_at": "2026-02-02T00:00:00Z",
            "archived": False,
            "fork": False,
        }
    ],
    "/repos/acme/repo1/issues": [
        {"id": 500, "number": 1, "title": "Bug", "updated_at": "2026-02-04T00:00:00Z"}
    ],
}


async def _github_listener(
    seen: list[str], bodies: Mapping[str, list[dict[str, Any]]] = GITHUB_BODIES
) -> asyncio.Server:
    """GitHub's own host as a real listener, answering the three paths its catalog root, its repo
    collection and one repo's issues take. `seen` is the request path each dial actually carried."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        target = head.split("\r\n")[0].split(" ")[1]
        path = target.split("?")[0]
        seen.append(path)
        payload = json.dumps(bodies.get(path, [])).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(payload) + payload
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


async def _github_graphql_listener(seen: list[str], checks: list[str]) -> asyncio.Server:
    """GitHub's own host, answering the catalog over REST and pull requests over GraphQL. `seen` is
    every path dialed, in order, and a POST body names the pull request it asked for."""

    async def answer(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        head = (await reader.readuntil(b"\r\n\r\n")).decode()
        lines = head.split("\r\n")
        path = lines[0].split(" ")[1].split("?")[0]
        length = next(
            (int(line.split(":", 1)[1]) for line in lines if line.lower().startswith("content-l")),
            0,
        )
        body = json.loads(await reader.readexactly(length)) if length else {}
        if path == "/graphql":
            variables = body["variables"]
            seen.append(f"/graphql/{variables['number']}" if "number" in variables else "/graphql")
            payload = json.dumps(_graphql_answer(body, checks[0])).encode()
        else:
            seen.append(path)
            payload = json.dumps(GITHUB_BODIES.get(path, [])).encode()
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Connection: close\r\nContent-Length: %d\r\n\r\n" % len(payload) + payload
        )
        await writer.drain()
        writer.close()

    return await asyncio.start_server(answer, "127.0.0.1", 0)


async def _tick(state: State, driver: SyncDriver) -> None:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(next_sync_at=DUE_AGAIN_AT, claimed_by=None, claim_expires_at=None)
                .where(tables.source.c.workspace_id == state.workspace_id)
            )
        await driver.run()


async def _pull_revision(state: State) -> tuple[int, str]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            row = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.revision, tables.page.c.body_ref).where(
                            tables.page.c.workspace_id == state.workspace_id,
                            tables.page.c.source_identity == WATCHED_PULL_IDENTITY,
                        )
                    )
                )
                .mappings()
                .one()
            )
    return row["revision"], row["body_ref"]


async def _github_driver(
    state: State,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    seen: list[str],
    checks: list[str],
) -> tuple[SyncDriver, UUID, asyncio.Server]:
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    def composio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "github", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)
    listener = await _github_graphql_listener(seen, checks)
    monkeypatch.setattr(
        GitHubConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    manifest = sources_manifest.manifest()
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((manifest,)),
        source_credentials=SourceCredentialResolver(connectors),
        watch_readers=_source_watch_readers((manifest,)),
    )
    with ws(state.workspace_id), agent(state.agent_id):
        await ws_current().put_credential("github", GITHUB_KEY)
        connection_id = await context.ext.register_connection("github")
        for stream in PULL_STREAMS:
            await context.ext.register_source(
                "github", ConnectorSourceConfig(stream=stream), connection_id=connection_id
            )
    return driver, connection_id, listener


async def test_a_watched_pull_request_is_read_every_tick_and_wakes_its_conversation(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole watch path over a real socket: a trigger naming a pull request URL and no streams
    at all, the connection's own rows syncing under the real driver, and the conversation woken by
    what moved.

    The pull request's `updatedAt` never moves — a check run does not touch it — so the repository's
    newest-first walk reaches it on no tick. It lands every tick because the trigger pinned it, its
    page takes a new revision when the rollup flips, and that revision is what wakes the
    conversation. The pass interval holds the catalog walk back on the second tick and the watched
    read goes out regardless, which is the whole shape of the two together."""
    state = await _state()
    seen: list[str] = []
    checks = ["PENDING"]
    driver, connection_id, listener = await _github_driver(
        state, database_url, tmp_path, monkeypatch, seen, checks
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await SourceTriggerStore(_sources_context()).create(
                conversation_id=state.conversation_id,
                connection_id=connection_id,
                delivery="current",
                created_by_member_id=state.member_id,
                resource=WATCHED_PULL,
            )
        await _tick(state, driver)
        pending_revision, pending_body = await _pull_revision(state)

        checks[0] = "FAILURE"
        await _tick(state, driver)
        failed_revision, failed_body = await _pull_revision(state)
    finally:
        listener.close()
        await listener.wait_closed()

    graphql = [path for path in seen if path.startswith("/graphql")]
    assert graphql == ["/graphql/3122", "/graphql", "/graphql/3122"]
    assert failed_revision > pending_revision
    assert failed_body != pending_body

    with ws(state.workspace_id), agent(state.agent_id):
        ext = _sources_context(
            invoker=AdmissionInvoker(
                admission=Admission(dbos=_StubDbos(), durable_surfaces=frozenset({"cli"})),
                workspace_id=state.workspace_id,
            )
        )
        feed = CorePageFeed(blob=FilesystemBlobStore(root=tmp_path / "blobs"))
        batch = await feed.pages_changed_since(None, PAGE_BATCH)
        await on_page_change(HookContext(ext=ext, payload=PageChangeBatch(changes=batch.changes)))

    async with workspace_tx() as connection:
        turns = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.inbound).where(
                        tables.turn.c.conversation_id == state.conversation_id
                    )
                )
            )
            .mappings()
            .all()
        )
    assert len(turns) == 1
    assert turns[0]["inbound"].startswith("github: Add retry to egress dial — pull_requests")
    assert WATCHED_PULL in turns[0]["inbound"]


async def test_an_unwatched_connection_reads_no_pull_request_by_number(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The same three rows with no trigger: the pull requests row walks the repository once, waits
    out its pass interval across the ticks after it, and asks for no pull request by number at
    all."""
    state = await _state()
    seen: list[str] = []
    driver, _, listener = await _github_driver(
        state, database_url, tmp_path, monkeypatch, seen, ["SUCCESS"]
    )
    try:
        for _ in range(3):
            await _tick(state, driver)
    finally:
        listener.close()
        await listener.wait_closed()

    assert seen.count("/graphql") == 1
    assert [path for path in seen if path.startswith("/graphql/")] == []


async def test_a_child_stream_fans_out_over_the_pages_its_parent_row_landed(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The tree through the real driver: three rows of one connection, each a stream of GitHub's
    catalog, over a real socket. `organizations` reads the root; `repositories` fans out over the
    organization pages that row landed; `issues` fans out over the repository pages that row landed,
    and every issue page is addressed under the repository page it hangs off.

    Nothing between the rows is stood in for — the parent records come back out of the `page` table
    the previous pass wrote, read under the connection's own authority — so the assertion that
    `/user/orgs` is dialed once per pass is the whole redundancy this replaces: before, all three
    rows re-derived that catalog on every run of every one of them."""
    state = await _state()
    seen: list[str] = []
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    def composio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "github", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)
    listener = await _github_listener(seen)
    monkeypatch.setattr(
        GitHubConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((sources_manifest.manifest(),)),
        source_credentials=SourceCredentialResolver(connectors),
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await ws_current().put_credential("github", GITHUB_KEY)
            connection_id = await context.ext.register_connection("github")
            for stream in ("organizations", "repositories", "issues"):
                await context.ext.register_source(
                    "github", ConnectorSourceConfig(stream=stream), connection_id=connection_id
                )
        for _ in range(3):
            with ws(state.workspace_id), agent(state.agent_id):
                async with workspace_tx() as connection:
                    await connection.execute(
                        sa.update(tables.source)
                        .values(next_sync_at=DUE_AGAIN_AT, claimed_by=None, claim_expires_at=None)
                        .where(tables.source.c.workspace_id == state.workspace_id)
                    )
                await driver.run()
    finally:
        listener.close()
        await listener.wait_closed()

    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            landed = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.source_identity, tables.page.c.parent_fields).where(
                            tables.page.c.workspace_id == state.workspace_id
                        )
                    )
                )
                .mappings()
                .all()
            )
    fields = {row["source_identity"]: row["parent_fields"] for row in landed}
    assert set(fields) == {
        "organizations/1",
        "repositories/acme/100",
        "issues/acme/repo1/500",
    }
    assert fields["organizations/1"] == {"login": "acme"}
    assert fields["repositories/acme/100"] == {
        "full_name": "acme/repo1",
        "name": "repo1",
        "owner.login": "acme",
    }
    assert seen.count("/user/orgs") == 3
    assert seen.count("/repos/acme/repo1/issues") >= 1


async def test_a_repository_that_stops_qualifying_is_tombstoned_and_leaves_the_partition_set(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The catalog through the real driver: two repositories land and `issues` asks both; the
    second is archived by the next pass, so `repositories` — a full listing every pass, swept
    against as authoritative — no longer holds it and its page is tombstoned. `issues` then reads
    its partitions off the non-tombstoned repository pages and asks the first alone. Nothing between
    the rows is stood in for: the parent records come back out of the `page` table the previous
    pass wrote."""
    state = await _state()
    seen: list[str] = []
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    def composio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "github", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)
    repo1, repo2 = (
        GITHUB_BODIES["/orgs/acme/repos"][0],
        {
            **GITHUB_BODIES["/orgs/acme/repos"][0],
            "id": 101,
            "name": "repo2",
            "full_name": "acme/repo2",
        },
    )
    bodies: dict[str, list[dict[str, Any]]] = {
        **GITHUB_BODIES,
        "/orgs/acme/repos": [repo1, repo2],
        "/repos/acme/repo2/issues": [
            {"id": 600, "number": 1, "title": "Flake", "updated_at": "2026-02-04T00:00:00Z"}
        ],
    }
    listener = await _github_listener(seen, bodies)
    monkeypatch.setattr(
        GitHubConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((sources_manifest.manifest(),)),
        source_credentials=SourceCredentialResolver(connectors),
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await ws_current().put_credential("github", GITHUB_KEY)
            connection_id = await context.ext.register_connection("github")
            rows = {
                stream: await context.ext.register_source(
                    "github", ConnectorSourceConfig(stream=stream), connection_id=connection_id
                )
                for stream in ("organizations", "repositories", "issues")
            }
        for stream in ("organizations", "repositories", "issues"):
            await _park(state)
            await _make_due(state, rows[stream])
            with ws(state.workspace_id), agent(state.agent_id):
                await driver.run()
        landed_before = await _live_identities(state)
        bodies["/orgs/acme/repos"] = [repo1, {**repo2, "archived": True}]
        asked_before = len(seen)
        for stream in ("repositories", "issues"):
            await _park(state)
            await _make_due(state, rows[stream])
            with ws(state.workspace_id), agent(state.agent_id):
                await driver.run()
    finally:
        listener.close()
        await listener.wait_closed()

    assert {"repositories/acme/100", "repositories/acme/101"} <= landed_before
    assert {"issues/acme/repo1/500", "issues/acme/repo2/600"} <= landed_before
    assert await _faults(state) == 0
    tombstoned = await _tombstoned_identities(state)
    assert "repositories/acme/101" in tombstoned
    assert "repositories/acme/100" not in tombstoned
    assert "/repos/acme/repo1/issues" in seen[asked_before:]
    assert "/repos/acme/repo2/issues" not in seen[asked_before:]


async def _live_identities(state: State) -> set[str]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.page.c.source_identity).where(
                    tables.page.c.workspace_id == state.workspace_id,
                    tables.page.c.tombstone.is_(False),
                )
            )
            return {row[0] for row in rows}


async def _tombstoned_identities(state: State) -> set[str]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            rows = await connection.execute(
                sa.select(tables.page.c.source_identity).where(
                    tables.page.c.workspace_id == state.workspace_id,
                    tables.page.c.tombstone.is_(True),
                )
            )
            return {row[0] for row in rows}


async def test_the_pages_a_connection_already_holds_are_adopted_not_relanded(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a connection that already syncs meets on the tick this lands: its `repositories` and
    `issues` rows hold pages addressed `repositories/acme/100` and `issues/acme/repo1/500`, carrying
    no projection because nothing hung under their streams when they landed.

    Every one of them settles on the row it already has — same page uid, no second row — because a
    record is addressed by the values its edge's path reads, which is what the repo-scoped key spelt
    out by hand. And a page carrying no projection is not a record to fan from, so the run that
    meets one raises nothing and its stream keeps syncing."""
    state = await _state()
    seen: list[str] = []
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    def composio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "github", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)
    listener = await _github_listener(seen)
    monkeypatch.setattr(
        GitHubConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=tmp_path / "blobs"),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((sources_manifest.manifest(),)),
        source_credentials=SourceCredentialResolver(connectors),
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await ws_current().put_credential("github", GITHUB_KEY)
            connection_id = await context.ext.register_connection("github")
            rows = {
                stream: await context.ext.register_source(
                    "github", ConnectorSourceConfig(stream=stream), connection_id=connection_id
                )
                for stream in ("organizations", "repositories", "issues")
            }
            planted = {
                "repositories/acme/100": await _plant(
                    state, rows["repositories"], "repositories/acme/100"
                ),
                "issues/acme/repo1/500": await _plant(
                    state, rows["issues"], "issues/acme/repo1/500"
                ),
            }
        await _make_due(state, rows["issues"])
        with ws(state.workspace_id), agent(state.agent_id):
            await driver.run()
        faults = await _faults(state)
        for _ in range(3):
            await _make_due(state)
            with ws(state.workspace_id), agent(state.agent_id):
                await driver.run()
    finally:
        listener.close()
        await listener.wait_closed()

    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            live = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.source_identity, tables.page.c.uid).where(
                            tables.page.c.workspace_id == state.workspace_id,
                            tables.page.c.tombstone.is_(False),
                        )
                    )
                )
                .mappings()
                .all()
            )
    held = {row["source_identity"]: row["uid"] for row in live}
    assert faults == 0
    assert set(held) == {"organizations/1", "repositories/acme/100", "issues/acme/repo1/500"}
    assert held["repositories/acme/100"] == planted["repositories/acme/100"]
    assert held["issues/acme/repo1/500"] == planted["issues/acme/repo1/500"]


async def _park(state: State) -> None:
    """Hold every row of the workspace off the due list, so a run drives exactly the rows a test
    then makes due — registration leaves them all due at once."""
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(next_sync_at=datetime(2099, 1, 1, tzinfo=UTC))
                .where(tables.source.c.workspace_id == state.workspace_id)
            )


async def _make_due(state: State, source_id: UUID | None = None) -> None:
    with ws(state.workspace_id), agent(state.agent_id):
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.source)
                .values(next_sync_at=DUE_AGAIN_AT, claimed_by=None, claim_expires_at=None)
                .where(
                    tables.source.c.workspace_id == state.workspace_id,
                    *(() if source_id is None else (tables.source.c.uid == source_id,)),
                )
            )


async def _faults(state: State) -> int:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            return sum(
                (
                    await connection.execute(
                        sa.select(tables.source.c.consecutive_errors).where(
                            tables.source.c.workspace_id == state.workspace_id
                        )
                    )
                )
                .scalars()
                .all()
            )


def _repository_digest() -> str:
    """The digest the driver derives for the repository the listener answers with, off the real
    connector's own render over the record as the organization edge carries it, so a run of
    `repositories` finds that page unchanged."""
    connector = GitHubConnector()
    stream = next(spec for spec in connector.streams() if spec.name == "repositories")
    record = {**GITHUB_BODIES["/orgs/acme/repos"][0], "org_login": "acme"}
    _, body = connector.render(connector.flatten(record, stream), stream)
    return "sha256:" + hashlib.sha256(body.encode()).hexdigest()


def _stored_bodies(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file())


async def _landed_fields(state: State) -> dict[str, dict[str, str] | None]:
    with ws(state.workspace_id):
        async with workspace_tx() as connection:
            rows = (
                (
                    await connection.execute(
                        sa.select(tables.page.c.source_identity, tables.page.c.parent_fields).where(
                            tables.page.c.workspace_id == state.workspace_id,
                            tables.page.c.tombstone.is_(False),
                        )
                    )
                )
                .mappings()
                .all()
            )
    return {row["source_identity"]: row["parent_fields"] for row in rows}


async def test_an_edge_declared_after_a_page_landed_reaches_it_without_a_refetch(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A page that landed before anything hung under its stream carries no projection, and its body
    has not moved since — so the digest skip writes no blob for it and the browse comparison is the
    only thing that can put the fields on the row. That comparison is what every later edge rests
    on: until it runs the child fans over nothing, and the pass that runs it is the pass after which
    the child reaches the parent, at one request on the edge's path.

    The blob count across the parent's pass says which path wrote it: a run that re-landed the body
    would have written one."""
    state = await _state()
    seen: list[str] = []
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)

    def composio_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"path": request.url.path})

    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(composio_handler)
    )
    monkeypatch.setattr(composio, "composio_client", lambda: client)
    broker_manifest = composio_manifest.manifest()
    connectors = _registry(broker_manifest, "github", _selected_fallback(store, broker_manifest))
    context = _context(state, GrantStore(), connectors)
    listener = await _github_listener(seen)
    monkeypatch.setattr(
        GitHubConnector, "base_url", f"http://127.0.0.1:{listener.sockets[0].getsockname()[1]}"
    )
    blobs = tmp_path / "blobs"
    driver = SyncDriver(
        blob=FilesystemBlobStore(root=blobs),
        postgres=database_url.startswith("postgresql"),
        backends=_source_backends((sources_manifest.manifest(),)),
        source_credentials=SourceCredentialResolver(connectors),
    )
    try:
        with ws(state.workspace_id), agent(state.agent_id):
            await ws_current().put_credential("github", GITHUB_KEY)
            connection_id = await context.ext.register_connection("github")
            rows = {
                stream: await context.ext.register_source(
                    "github", ConnectorSourceConfig(stream=stream), connection_id=connection_id
                )
                for stream in ("organizations", "repositories", "issues")
            }
        await _park(state)
        await _make_due(state, rows["organizations"])
        with ws(state.workspace_id), agent(state.agent_id):
            await driver.run()
        with ws(state.workspace_id):
            await _plant(
                state,
                rows["repositories"],
                "repositories/acme/100",
                _repository_digest(),
            )

        await _make_due(state, rows["issues"])
        with ws(state.workspace_id), agent(state.agent_id):
            await driver.run()
        unreached = list(seen)
        stored = _stored_bodies(blobs)

        await _make_due(state, rows["repositories"])
        with ws(state.workspace_id), agent(state.agent_id):
            await driver.run()
        rewritten = _stored_bodies(blobs)
        carried = await _landed_fields(state)

        await _make_due(state, rows["issues"])
        with ws(state.workspace_id), agent(state.agent_id):
            await driver.run()
    finally:
        listener.close()
        await listener.wait_closed()

    assert unreached.count("/repos/acme/repo1/issues") == 0
    assert rewritten == stored
    assert carried["repositories/acme/100"] == {
        "full_name": "acme/repo1",
        "name": "repo1",
        "owner.login": "acme",
    }
    assert seen.count("/repos/acme/repo1/issues") == 1
    assert "issues/acme/repo1/500" in await _landed_fields(state)


async def _plant(
    state: State,
    source_id: UUID,
    identity: str,
    digest: str = "sha256:" + "0" * 64,
) -> UUID:
    """One page carrying no projection, at the address and body digest given, and its row id."""
    now = datetime.now(UTC)
    uid = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.page).values(
                uid=uid,
                workspace_id=state.workspace_id,
                source_uid=source_id,
                source_identity=identity,
                digest=digest,
                body_ref="planted",
                stream=identity.split("/")[0],
                title=identity,
                subject="shared",
                tombstone=False,
                created_at=now,
                updated_at=now,
            )
        )
    return uid
