"""The connector source framework end to end, decoupled from any one auth backend.

A connector authenticates through the `AuthProxy` seam, so these tests drive it with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to the provider host — no live API, no
token, no broker. Covered here: the shared REST pagination strategies (against a probe connector),
the adapter that collapses a connector's stream pages into a core `SyncResult` (snapshot vs
delta/watermark/deletes), the GitHub provider's org/repo fan-out + Link pagination + `?since`
incremental, the asana provider through the core `SyncDriver` into recallable memory (the both-ends
proof), the `direct` BYOK backend reading a member-added key host-side, and the REST client
honouring each `Credential` shape. These live in `extensions/sources/tests` so the framework evolves
without colliding with the composio broker's auth-proxy proof in `extensions/connectors/tests`."""

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import DefaultIndex
from ufo_ext_memory.store import MemoryStore, PageIndexer
from ufo_ext_sources.asana import AsanaConnector
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.github import GitHubConnector

from ufo.blob import FilesystemBlobStore
from ufo.connectors import Credential
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess, context_for
from ufo.indexing import TextChunker
from ufo.schema import tables
from ufo.sdk.sources import (
    Connector,
    ConnectorBackend,
    ConnectorSourceConfig,
    Pagination,
    PaginationStrategy,
    RestConnector,
    StreamPage,
    StreamSpec,
)
from ufo.sources.sync import CorePageFeed, SourceAuth, StreamSkipped, SyncDriver
from ufo.subjects import SHARED_SUBJECT

ACCOUNT = "acct-1"


@dataclass(frozen=True)
class _MockProxy:
    """A reusable auth proxy: every `credential` yields a `Credential` carrying an
    `httpx.MockTransport` bound to the provider host, so a whole connector fetch runs against canned
    provider responses without a live API or a real token."""

    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    connector: Connector, stream: str, handler: Callable[[httpx.Request], httpx.Response]
):
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), None, _auth(handler)
    )


# --- the shared pagination strategies (a probe connector over a mock transport) ------------------


class _ProbeConnector(RestConnector):
    """A real RestConnector whose one stream and canned handler drive the strategy loops against
    mock HTTP — a real consumer of the framework, so the loops are exercised, never faked."""

    name = "probe"
    base_url = "https://api.probe.test"

    def __init__(
        self, stream: StreamSpec, handler: Callable[[httpx.Request], httpx.Response]
    ) -> None:
        self._probe_stream = stream
        self._handler = handler

    def streams(self) -> list[StreamSpec]:
        return [self._probe_stream]

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url.rstrip("/"), transport=httpx.MockTransport(self._handler)
        )


async def _pages(
    connector: Connector, cursor: str | None = None
) -> list[list[dict[str, Any]] | StreamPage]:
    stream = connector.streams()[0]
    return [
        page
        async for page in connector.fetch_page(
            stream, cursor=cursor, credential=Credential(bearer="tok"), base_url=""
        )
    ]


def _ids(pages: list[list[dict[str, Any]] | StreamPage]) -> set[Any]:
    ids: set[Any] = set()
    for page in pages:
        records = page.records if isinstance(page, StreamPage) else page
        ids.update(record["id"] for record in records)
    return ids


async def test_next_link_strategy_follows_the_link_header() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"id": 2}])
        link = '<https://api.probe.test/items?page=2>; rel="next"'
        return httpx.Response(200, json=[{"id": 1}], headers={"link": link})

    stream = StreamSpec(
        name="items",
        source_object="items",
        pagination=Pagination(strategy=PaginationStrategy.next_link, path="/items"),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2}


async def test_next_cursor_strategy_follows_the_body_token() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json={"data": [{"id": 2}], "next": None})
        return httpx.Response(200, json={"data": [{"id": 1}], "next": "c2"})

    stream = StreamSpec(
        name="things",
        source_object="things",
        pagination=Pagination(
            strategy=PaginationStrategy.next_cursor,
            path="/things",
            record_path="data",
            cursor_path="next",
            cursor_param="cursor",
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2}


async def test_offset_limit_strategy_advances_until_short_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("offset") == "2":
            return httpx.Response(200, json={"data": [{"id": 3}]})
        return httpx.Response(200, json={"data": [{"id": 1}, {"id": 2}]})

    stream = StreamSpec(
        name="rows",
        source_object="rows",
        pagination=Pagination(
            strategy=PaginationStrategy.offset_limit,
            path="/rows",
            record_path="data",
            offset_param="offset",
            limit_param="limit",
            page_size=2,
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2, 3}


async def test_page_number_strategy_advances_until_short_page() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json={"rows": [{"id": 3}]})
        return httpx.Response(200, json={"rows": [{"id": 1}, {"id": 2}]})

    stream = StreamSpec(
        name="list",
        source_object="list",
        pagination=Pagination(
            strategy=PaginationStrategy.page_number,
            path="/list",
            record_path="rows",
            cursor_param="page",
            page_size=2,
        ),
    )
    assert _ids(await _pages(_ProbeConnector(stream, handle))) == {1, 2, 3}


async def test_time_window_strategy_sends_the_cursor_value() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json={"data": [{"id": 1, "after": request.url.params.get("after")}]}
        )

    stream = StreamSpec(
        name="win",
        source_object="win",
        pagination=Pagination(
            strategy=PaginationStrategy.time_window,
            path="/win",
            record_path="data",
            cursor_param="after",
        ),
    )
    [page] = await _pages(_ProbeConnector(stream, handle), cursor="2026-01-01")
    assert isinstance(page, list) and page[0]["after"] == "2026-01-01"


async def test_undeclared_pagination_raises() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    stream = StreamSpec(name="bare", source_object="bare")
    with pytest.raises(NotImplementedError):
        await _pages(_ProbeConnector(stream, handle))


async def test_next_cursor_strategy_fails_on_a_repeated_cursor() -> None:
    """A provider returning a constant next-cursor (a bug or a bad/hostile response) must fail the
    run, not spin the fetch loop forever re-fetching the same page while holding the claim lease."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": 1}], "next": "stuck"})

    stream = StreamSpec(
        name="things",
        source_object="things",
        pagination=Pagination(
            strategy=PaginationStrategy.next_cursor,
            path="/things",
            record_path="data",
            cursor_path="next",
            cursor_param="cursor",
        ),
    )
    with pytest.raises(RuntimeError, match="repeated"):
        await _pages(_ProbeConnector(stream, handle))


async def test_next_link_strategy_fails_on_a_repeated_link() -> None:
    """A constant `Link: rel=next` pointing back at the same page must fail the run, not spin."""

    def handle(request: httpx.Request) -> httpx.Response:
        link = '<https://api.probe.test/items?page=stuck>; rel="next"'
        return httpx.Response(200, json=[{"id": 1}], headers={"link": link})

    stream = StreamSpec(
        name="items",
        source_object="items",
        pagination=Pagination(strategy=PaginationStrategy.next_link, path="/items"),
    )
    with pytest.raises(RuntimeError, match="repeated"):
        await _pages(_ProbeConnector(stream, handle))


# --- the adapter: connector pages → SyncResult ---------------------------------------------------


class _CannedConnector(RestConnector):
    """A connector whose `paginate` yields pre-canned pages, so the adapter's collapse — snapshot vs
    delta, watermark, deletes → source refs — is what's under test, not an HTTP loop."""

    name = "canned"
    base_url = "https://api.canned.test"

    def __init__(self, stream: StreamSpec, pages: list[list[dict[str, Any]] | StreamPage]) -> None:
        self._canned_stream = stream
        self._pages = pages

    def streams(self) -> list[StreamSpec]:
        return [self._canned_stream]

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=base_url, transport=httpx.MockTransport(lambda r: httpx.Response(200))
        )

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        for page in self._pages:
            yield page


def _ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json=[])


async def test_full_collection_stream_returns_a_snapshot() -> None:
    stream = StreamSpec(name="repos", source_object="repos", delete_missing=True)
    connector = _CannedConnector(stream, [[{"id": "1", "name": "Widgets"}]])
    result = await _fetch(connector, "repos", _ok)
    assert result.snapshot is True
    assert result.next_cursor is None
    assert result.deletes == ()
    assert result.pages[0].source_ref == "repos/1"
    assert "Widgets" in result.pages[0].body


async def test_incremental_stream_advances_watermark_and_tombstones_deletes() -> None:
    stream = StreamSpec(
        name="tickets", source_object="tickets", cursor_field="updated_at", delete_missing=False
    )
    page = StreamPage(records=[{"id": "5", "updated_at": "2026-02-02T00:00:00Z"}], deletes=("9",))
    result = await ConnectorBackend(connector=_CannedConnector(stream, [page])).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="tickets"), "2026-02-01T00:00:00Z", _auth(_ok)
    )
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert result.deletes == ("tickets/9",)
    assert {p.source_ref for p in result.pages} == {"tickets/5"}


async def test_stream_page_cursor_wins_over_watermark() -> None:
    stream = StreamSpec(name="tickets", source_object="tickets", cursor_field="updated_at")
    page = StreamPage(
        records=[{"id": "5", "updated_at": "2026-02-02T00:00:00Z"}], next_cursor="opaque-token"
    )
    result = await _fetch(_CannedConnector(stream, [page]), "tickets", _ok)
    assert result.next_cursor == "opaque-token"


async def test_connector_backend_fails_loud_without_an_auth_proxy() -> None:
    with pytest.raises(RuntimeError, match="needs an auth proxy"):
        await ConnectorBackend(connector=AsanaConnector()).fetch(
            ConnectorSourceConfig(account=ACCOUNT, stream="workspaces"),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


# --- per-tenant base_url carried by the source config --------------------------------------------


class _PerTenantConnector(RestConnector):
    """A per-tenant connector whose class `base_url` is empty (like Freshdesk/Zendesk): the host
    must come from the source row's `ConnectorSourceConfig.base_url`, not a class default."""

    name = "pertenant"
    base_url = ""

    def streams(self) -> list[StreamSpec]:
        return [
            StreamSpec(
                name="rows",
                source_object="rows",
                pagination=Pagination(strategy=PaginationStrategy.next_link, path="/rows"),
            )
        ]


async def test_per_tenant_fetch_dials_the_config_base_url() -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.host)
        return httpx.Response(200, json=[{"id": 1}])

    result = await ConnectorBackend(connector=_PerTenantConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="rows", base_url="https://acme.tenant.test"),
        None,
        _auth(handle),
    )
    assert {page.source_ref for page in result.pages} == {"rows/1"}
    assert seen == ["acme.tenant.test"]


async def test_per_tenant_fetch_without_a_base_url_fails_loud() -> None:
    """A per-tenant connector (class `base_url=""`) whose source row set no `base_url` fails its run
    naming the connector, rather than dialing an empty host."""
    with pytest.raises(RuntimeError, match=r"pertenant.*resolved no base_url"):
        await _fetch(_PerTenantConnector(), "rows", _ok)


# --- GitHub: fan-out + Link pagination + ?since --------------------------------------------------


def _github_handler(
    seen: list[tuple[str, dict[str, str]]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        path = request.url.path
        params = {key: value for key, value in request.url.params.items()}
        seen.append((path, params))
        if path == "/user/orgs":
            return httpx.Response(200, json=[{"login": "acme"}])
        if path == "/orgs/acme/repos":
            return httpx.Response(
                200, json=[{"id": 7, "full_name": "acme/widgets", "archived": False, "fork": False}]
            )
        if path == "/repos/acme/widgets/issues":
            if params.get("page") == "2":
                return httpx.Response(
                    200, json=[{"id": 3, "title": "Later", "updated_at": "2026-01-05T00:00:00Z"}]
                )
            link = '<https://api.github.com/repos/acme/widgets/issues?page=2>; rel="next"'
            return httpx.Response(
                200,
                json=[
                    {"id": 1, "title": "Bug", "updated_at": "2026-01-02T00:00:00Z"},
                    {
                        "id": 2,
                        "title": "PR",
                        "pull_request": {"url": "x"},
                        "updated_at": "2026-01-03T00:00:00Z",
                    },
                ],
                headers={"link": link},
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_github_repositories_fan_out_over_granted_orgs() -> None:
    """The org→repo fan-out lands the granted-org repos. GitHub surfaces no delete signal, so every
    stream is incremental (never an authoritative snapshot); the sync runner's row-level cursor
    handles re-reads."""
    result = await _fetch(GitHubConnector(), "repositories", _github_handler([]))
    assert result.snapshot is False
    assert {page.source_ref for page in result.pages} == {"repositories/7"}
    assert "acme/widgets" in result.pages[0].body


async def test_github_issues_fan_out_link_pagination_and_pr_filter() -> None:
    """Issues are incremental: fan out over granted repos, follow the Link header, drop pull
    requests, and advance the watermark — snapshot=False, no deletes (GitHub has no delete)."""
    result = await _fetch(GitHubConnector(), "issues", _github_handler([]))
    assert {p.source_ref for p in result.pages} == {"issues/1", "issues/3"}
    assert any("Bug" in p.body for p in result.pages)
    assert result.snapshot is False
    assert result.next_cursor == "2026-01-05T00:00:00Z"
    assert result.deletes == ()


async def test_github_issues_send_since_and_state_when_a_cursor_is_stored() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    await ConnectorBackend(connector=GitHubConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="issues"),
        "2026-01-01T00:00:00Z",
        _auth(_github_handler(seen)),
    )
    issue_calls = [params for path, params in seen if path.endswith("/issues")]
    assert issue_calls and issue_calls[0].get("since") == "2026-01-01T00:00:00Z"
    assert issue_calls[0].get("state") == "all"


async def test_github_skips_when_org_enumeration_is_refused() -> None:
    """A grant with no org scope (`/user/orgs` → 403) can read no stream, so the fetch raises
    `StreamSkipped` — the driver records a skip, never a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "insufficient scope"})

    with pytest.raises(StreamSkipped):
        await _fetch(GitHubConnector(), "repositories", handler)


# --- asana ---


def _asana_handler(
    page_by_offset: dict[str | None, dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "app.asana.com"
        assert request.url.path.endswith("/workspaces")
        return httpx.Response(200, json=page_by_offset[request.url.params.get("offset")])

    return handle


async def test_asana_follows_offset_pagination_to_the_end() -> None:
    paged: dict[str | None, dict[str, object]] = {
        None: {"data": [{"gid": "1", "name": "One"}], "next_page": {"offset": "o2"}},
        "o2": {"data": [{"gid": "2", "name": "Two"}], "next_page": None},
    }
    result = await _fetch(AsanaConnector(), "workspaces", _asana_handler(paged))
    assert {page.source_ref for page in result.pages} == {"workspaces/1", "workspaces/2"}
    assert result.snapshot is False


# --- direct BYOK backend -------------------------------------------------------------------------


async def test_direct_backend_returns_a_bearer_read_from_the_credential_store(db: None) -> None:
    """The direct backend reads a member-added key from the credential store host-side and returns
    it as a bearer — the secret is decrypted in-process, never surfaced to the sandbox or agent."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    await store.put(workspace_id, "github", "ghp_realkey")
    access = CredentialAccess(
        workspace_id=workspace_id, declared=frozenset({"github"}), _store=store
    )
    credential = await DirectAuthProxy(credentials=access).credential(
        workspace_id, "github", ACCOUNT
    )
    assert credential == Credential(bearer="ghp_realkey")


async def test_direct_backend_refuses_a_provider_slot_it_never_declared() -> None:
    from ufo.ext.context import UndeclaredCredentialSlot

    access = CredentialAccess(workspace_id=uuid4(), declared=frozenset(), _store=None)
    with pytest.raises(UndeclaredCredentialSlot):
        await DirectAuthProxy(credentials=access).credential(uuid4(), "github", ACCOUNT)


# --- credential shapes at the REST client --------------------------------------------------------


def test_rest_client_sends_a_bearer_credential() -> None:
    probe = _ProbeConnector(StreamSpec(name="x", source_object="x"), _ok)
    client = RestConnector._make_client(probe, "https://api.probe.test", Credential(bearer="tok"))
    assert client.headers["authorization"] == "Bearer tok"


def test_rest_client_sends_a_headers_credential() -> None:
    probe = _ProbeConnector(StreamSpec(name="x", source_object="x"), _ok)
    client = RestConnector._make_client(
        probe, "https://api.probe.test", Credential(headers={"X-Api-Key": "k1"})
    )
    assert client.headers["x-api-key"] == "k1"
    assert "authorization" not in client.headers


def test_rest_client_fails_loud_on_an_empty_credential() -> None:
    probe = _ProbeConnector(StreamSpec(name="x", source_object="x"), _ok)
    with pytest.raises(RuntimeError, match="no auth"):
        RestConnector._make_client(probe, "https://api.probe.test", Credential())


# --- the full chain: driver → memory recall ------------------------------------------------------


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


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def test_asana_source_syncs_through_the_driver_into_recallable_memory(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """End to end: register an asana source through the SDK, let the core sync driver drive the
    connector's backend (records pulled through a mock auth-proxy transport, no token read), and
    recall the landed page through memory — the both-ends proof for a connector source."""
    workspace_id = await _workspace()
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        await connection.execute(sa.text("delete from mem_page"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))

    credentials = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    context = context_for(workspace_id, "sources", frozenset(), credentials)
    await context.register_source(
        "asana", ConnectorSourceConfig(account=ACCOUNT, stream="workspaces")
    )

    handler = _asana_handler({None: {"data": [{"gid": "111", "name": "Acme HQ workspace"}]}})
    embed = _StubEmbed(_vec((6, 1.0)))
    index = DefaultIndex(embed=embed, transaction=workspace_tx)
    blob = FilesystemBlobStore(root=tmp_path)
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(
        backends={"asana": ConnectorBackend(connector=AsanaConnector())},
        blob=blob,
        postgres=postgres,
        auth_proxy=_MockProxy(handler=handler),
    )
    page_feed = CorePageFeed(blob=blob)
    page_indexer = PageIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
    )
    service = MemoryStore(
        index=index, embed=embed, transaction=workspace_tx, workspace_id=workspace_id
    )

    await driver.run()
    async with workspace_tx() as connection:
        chunks = (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()
    assert chunks == 0

    await page_indexer.apply((await page_feed.pages_changed_since(None, 50)).changes)
    matches = await service.search_sources("Acme HQ workspace", frozenset({SHARED_SUBJECT}), 5)
    assert matches and "Acme HQ workspace" in matches[0].text
