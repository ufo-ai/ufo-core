"""The Composio-fed source backend end to end: its `fetch` proxies provider records through Composio
and renders them into pages, and the core sync driver lands those pages in memory where they are
recalled — the both-ends proof for a real connector source. Composio's connected-account metadata
and its proxy-execute endpoint are mocked with `httpx.MockTransport` (no live API, no token, no
direct provider call), so the real backend, proxy transport, driver, and derivation pipeline run
against canned Composio responses. This file lives apart from `test_ext_connectors` so the
connector-tool seam and the connector-source seam evolve without colliding."""

import json
from collections.abc import Callable
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import selfhost_ext_connectors.composio as composio
import selfhost_ext_connectors.sources as sources
import sqlalchemy as sa
from cryptography.fernet import Fernet

from selfhost.blob import FilesystemBlobStore
from selfhost.credentials import CredentialStore
from selfhost.db import workspace_tx
from selfhost.ext.context import context_for
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import PageIndexer
from selfhost.memory.service import SHARED_SUBJECT, MemoryService
from selfhost.memory.sources import SourceAuth, SyncDriver
from selfhost.schema import tables

COMPOSIO_ACCOUNT = "ca_asana_1"


def _vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class _StubEmbed:
    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


def _composio_handler(
    owner: str, page_by_offset: dict[str | None, dict[str, object]]
) -> Callable[[httpx.Request], httpx.Response]:
    """A Composio mock: the connected-account read reports `owner` (metadata, no token), and
    proxy-execute returns the Asana page for the request's `offset` query param, wrapped in
    Composio's `{data: {data, status, headers}}` envelope so the transport's unwrap runs."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and "/connected_accounts/" in path:
            return httpx.Response(
                200, json={"id": COMPOSIO_ACCOUNT, "user_id": owner, "status": "ACTIVE"}
            )
        if request.method == "POST" and path.endswith(sources.PROXY_EXECUTE_PATH):
            payload = json.loads(request.content)
            offset = next(
                (
                    param["value"]
                    for param in payload.get("parameters", [])
                    if param["name"] == "offset" and param["type"] == "query"
                ),
                None,
            )
            provider = page_by_offset[offset]
            return httpx.Response(
                200, json={"data": {"data": provider, "status": 200, "headers": {}}}
            )
        return httpx.Response(404, json={})

    return handle


def _composio(
    owner: str, page_by_offset: dict[str | None, dict[str, object]]
) -> Callable[[], composio.ComposioClient]:
    client = composio.ComposioClient(
        api_key="test", transport=httpx.MockTransport(_composio_handler(owner, page_by_offset))
    )
    return lambda: client


def _single_page() -> dict[str | None, dict[str, object]]:
    return {None: {"data": [{"gid": "111", "name": "Acme HQ workspace"}], "next_page": None}}


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def test_asana_source_proxies_and_renders_workspaces_into_pages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    monkeypatch.setattr(composio, "composio_client", _composio(owner, _single_page()))

    result = await sources.AsanaSource().fetch(
        sources.AsanaSourceConfig(account=COMPOSIO_ACCOUNT),
        None,
        SourceAuth(workspace_id=workspace_id),
    )

    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.source_ref == "workspaces/111"
    assert page.subject == SHARED_SUBJECT
    assert "Acme HQ workspace" in page.body
    assert page.digest.startswith("sha256:")


async def test_asana_source_follows_offset_pagination_through_the_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id = uuid4()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    paged: dict[str | None, dict[str, object]] = {
        None: {"data": [{"gid": "1", "name": "One"}], "next_page": {"offset": "o2"}},
        "o2": {"data": [{"gid": "2", "name": "Two"}], "next_page": None},
    }
    monkeypatch.setattr(composio, "composio_client", _composio(owner, paged))

    result = await sources.AsanaSource().fetch(
        sources.AsanaSourceConfig(account=COMPOSIO_ACCOUNT),
        None,
        SourceAuth(workspace_id=workspace_id),
    )

    assert {page.source_ref for page in result.pages} == {"workspaces/1", "workspaces/2"}


async def test_asana_source_refuses_a_foreign_composio_account(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The guard reads the account's owning `user_id` metadata (never a token); an account owned by
    another workspace's broker user fails loud before any provider fetch, so no page is synced from
    an account this workspace never connected."""
    foreign_owner = f"{composio.EXTERNAL_USER_PREFIX}{uuid4()}"
    monkeypatch.setattr(composio, "composio_client", _composio(foreign_owner, _single_page()))
    with pytest.raises(composio.ComposioError, match="owned by"):
        await sources.AsanaSource().fetch(
            sources.AsanaSourceConfig(account=COMPOSIO_ACCOUNT),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


async def test_asana_source_syncs_through_the_driver_into_recallable_memory(
    db: None, database_url: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end: register an Asana source row through the SDK, let the core sync driver drive the
    connector's SourceBackend (records pulled through mocked Composio proxy-execute, the credential
    never read), and recall the landed page through memory — the Composio-fed source proof."""
    workspace_id = await _workspace()
    owner = f"{composio.EXTERNAL_USER_PREFIX}{workspace_id}"
    monkeypatch.setattr(composio, "composio_client", _composio(owner, _single_page()))
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))

    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    context = context_for(workspace_id, "connectors", frozenset(), credentials)
    await context.register_source(
        sources.ASANA_BACKEND, sources.AsanaSourceConfig(account=COMPOSIO_ACCOUNT)
    )

    embed = _StubEmbed(_vec((6, 1.0)))
    index = index_backend_for(database_url, embed)
    blob = FilesystemBlobStore(root=tmp_path)
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(
        backends={sources.ASANA_BACKEND: sources.AsanaSource()},
        blob=blob,
        postgres=postgres,
    )
    page_indexer = PageIndexer(
        index=index, embed=embed, chunker=TextChunker(), blob=blob, postgres=postgres
    )
    service = MemoryService(index=index, embed=embed)

    await driver.run()
    async with workspace_tx() as connection:
        embedding_digest = (
            await connection.execute(
                sa.select(tables.page.c.embedding_digest).where(
                    tables.page.c.workspace_id == workspace_id
                )
            )
        ).scalar_one()
    assert embedding_digest is None

    await page_indexer.run()
    matches = await service.search_sources(
        "Acme HQ workspace", frozenset({SHARED_SUBJECT}), 5
    )
    assert matches and "Acme HQ workspace" in matches[0].text
