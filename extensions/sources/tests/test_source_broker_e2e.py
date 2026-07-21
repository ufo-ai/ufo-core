"""Brokered source registration through durable pages and the memory tool."""

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
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import PageIndexer
from ufo_ext_sources.tools import SourceObjects, SourceSpec, _binding_name

from ufo.blob import FilesystemBlobStore
from ufo.connectors import ConnectorEntry, ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.grants import GrantStore
from ufo.indexing import TextChunker
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.serve import _source_backends
from ufo.sources.sync import CorePageFeed, SyncDriver
from ufo.tools.context import ToolContext
from ufo.workspace import ws

ASANA_ACCOUNT = "ca_asana_e2e"
GMAIL_ACCOUNT = "apn_gmail_e2e"


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


def _registry(manifest: object, provider: str) -> ConnectorRegistry:
    connector = next(
        connector for connector in manifest.connectors if connector.oauth.provider == provider
    )
    return ConnectorRegistry(
        entries={
            provider: ConnectorEntry(
                provider=provider, label=connector.label, broker=connector.broker
            )
        }
    )


async def _register_grant(
    state: State, grants: GrantStore, provider: str, account: str, host: str
) -> None:
    await grants.record(
        workspace_id=state.workspace_id,
        agent_id=state.agent_id,
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
    with ws(state.workspace_id):
        await SourceObjects().apply(
            context,
            _binding_name(provider, account, None),
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
        index = DefaultIndex(embed=embed, transaction=workspace_tx)
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
    state = await _state()
    grants = GrantStore()
    if provider == "asana":
        client = composio.ComposioClient(
            api_key="test", transport=_composio_transport(state.workspace_id)
        )
        monkeypatch.setattr(composio, "composio_client", lambda: client)
        connectors = _registry(composio_manifest.manifest(), provider)
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
        connectors = _registry(pipedream_manifest.manifest(), provider)
        account, host, stream, query = (
            GMAIL_ACCOUNT,
            "gmail.googleapis.com",
            "messages",
            "zephyr launch review",
        )
    await _register_grant(state, grants, provider, account, host)
    context = _context(state, grants, connectors)

    recalled = await _sync_and_search(
        state, context, provider, account, stream, query, database_url, tmp_path / provider
    )

    assert query.lower() in recalled.lower()
