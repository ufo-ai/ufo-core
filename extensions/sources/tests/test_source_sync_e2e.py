"""Source registration through durable pages and the memory tool, on both auth paths: a broker grant
resolving through its broker, and a member-added key resolving through the `direct` backend."""

import asyncio
import base64
import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
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
from ufo_ext_sources.klaviyo import KLAVIYO_REVISION, KlaviyoConnector
from ufo_ext_sources.registry import binding_name
from ufo_ext_sources.tools import SourceObjects, SourceSpec

from ufo.agent_scope import agent
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import DIRECT_ACCOUNT, AuthProxy, ConnectorEntry, ConnectorRegistry
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.grants import GrantStore
from ufo.indexing import TextChunker
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.serve import _select_auth_proxy, _source_backends
from ufo.sources.sync import CorePageFeed, SyncDriver
from ufo.tools.context import ToolContext
from ufo.workspace import init_workspace_credentials, ws, ws_current

ASANA_ACCOUNT = "ca_asana_e2e"
GMAIL_ACCOUNT = "apn_gmail_e2e"
KLAVIYO_KEY = "pk_live_byok_e2e"
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
        audience_member_id=state.member_id,
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
    state: State, grants: GrantStore, provider: str, account: str, host: str
) -> None:
    with ws(state.workspace_id), agent(state.agent_id):
        await grants.record(
            provider=provider,
            account_id=account,
            host=host,
            grantor_member_id=state.member_id,
            conversation_id=state.conversation_id,
            shared=False,
        )


async def _sync_and_search(
    state: State,
    context: ToolContext,
    provider: str,
    account: str,
    stream: str,
    query: str,
    database_url: str,
    tmp_path: Path,
) -> str:
    with ws(state.workspace_id), agent(state.agent_id):
        await SourceObjects().apply(
            context,
            binding_name(provider, account, None),
            SourceSpec(provider=provider, streams=(stream,)),
            None,
        )
        driver = SyncDriver(
            blob=FilesystemBlobStore(root=tmp_path / "blobs"),
            postgres=database_url.startswith("postgresql"),
            backends=_source_backends((sources_manifest.manifest(),)),
            auth_proxy=context.connectors,
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
    return result.content[0].text


def _composio_transport(workspace_id: UUID) -> httpx.MockTransport:
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"

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
    """A granted source syncs through its broker with the `direct` fallback installed alongside —
    the deploy shape the account-handle routing has to keep. The fallback's store holds no key for
    either provider, so a source that took it would fail its run and recall nothing."""
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
    await _register_grant(state, grants, provider, account, host)
    context = _context(state, grants, connectors)

    resolved = await connectors.credential(state.workspace_id, provider, account)
    assert resolved.transport is not None
    assert resolved.bearer is None

    recalled = await _sync_and_search(
        state, context, provider, account, stream, query, database_url, tmp_path / provider
    )

    assert query.lower() in recalled.lower()


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


async def test_keyed_source_reaches_memory_search_with_the_broker_namespace_installed(
    db: None,
    database_url: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The BYOK path in the deploy shape that broke it: composio's open namespace is installed and
    claims every slug, including klaviyo — which composio cannot broker at all. The source carries
    `DIRECT_ACCOUNT`, so its run must resolve through the `direct` backend instead.

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
        recalled = await _sync_and_search(
            state,
            context,
            "klaviyo",
            DIRECT_ACCOUNT,
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
