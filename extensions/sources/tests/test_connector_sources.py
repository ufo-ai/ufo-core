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
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

import httpx
import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_sources.direct import DirectAuthProxy
from ufo_ext_sources.providers.asana import AsanaConnector
from ufo_ext_sources.providers.github import GitHubConnector
from ufo_ext_sources.watermark import text_checkpoint

from ufo.db import workspace_tx
from ufo.runtime.access.connectors import (
    Credential,
    GrantUnusable,
)
from ufo.runtime.access.credentials import CredentialStore
from ufo.runtime.ext.context import CredentialAccess
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.runtime.workspace import init_workspace_credentials, ws
from ufo.schema import tables
from ufo.sdk.sources import (
    Connector,
    ConnectorBackend,
    ConnectorSourceConfig,
    Pagination,
    PaginationStrategy,
    ProviderRateLimited,
    RestConnector,
    StreamPage,
    StreamSpec,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]


@dataclass(frozen=True)
class _MockProxy:
    """A reusable auth proxy: every `credential` yields a `Credential` carrying an
    `httpx.MockTransport` bound to the provider host, so a whole connector fetch runs against canned
    provider responses without a live API or a real token."""

    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    connector: Connector, stream: str, handler: Callable[[httpx.Request], httpx.Response]
):
    return await ConnectorBackend(connector=connector).fetch(
        ConnectorSourceConfig(stream=stream), None, _auth(handler)
    )


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
    connector: Connector, cursor: str | None = None, *, yield_rate_limits: bool = True
) -> list[list[dict[str, Any]] | StreamPage]:
    stream = connector.streams()[0]
    return [
        page
        async for page in connector.fetch_page(
            stream,
            cursor=cursor,
            credential=Credential(bearer="tok"),
            base_url="",
            self_user_id=None,
            yield_rate_limits=yield_rate_limits,
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


def _connect_error() -> httpx.Response:
    raise httpx.ConnectError("connection refused")


def _retry_stream() -> StreamSpec:
    return StreamSpec(
        name="items",
        source_object="items",
        pagination=Pagination(strategy=PaginationStrategy.next_link, path="/items"),
    )


def _record_waits(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Collect what the retry envelope would sleep, without sleeping it."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("ufo.runtime.sources.rest.asyncio.sleep", fake_sleep)
    return waits


RETRY_WAIT_CASES = (
    ([lambda: httpx.Response(503, json={}, headers={"retry-after": "42"})], [(42.0, 60.0)]),
    ([lambda: httpx.Response(504, json={}, headers={"retry-after": "42"})], [(42.0, 60.0)]),
    ([lambda: httpx.Response(500, json={}, headers={"retry-after": "42"})], [(1.0, 1.5)]),
    ([_connect_error], [(1.0, 1.5)]),
    (
        [lambda: httpx.Response(503, json={}), lambda: httpx.Response(503, json={})],
        [(1.0, 1.5), (2.0, 3.0)],
    ),
)


async def test_retry_waits_a_bounded_retry_after_else_a_jittered_doubling_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for case, (failing_calls, expected_bounds) in enumerate(RETRY_WAIT_CASES):
        waits = _record_waits(monkeypatch)
        calls = 0

        def handle(
            request: httpx.Request,
            failures: list[Callable[[], httpx.Response]] = failing_calls,
        ) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls <= len(failures):
                return failures[calls - 1]()
            return httpx.Response(200, json=[{"id": 1}])

        assert _ids(await _pages(_ProbeConnector(_retry_stream(), handle))) == {1}, case
        assert len(waits) == len(expected_bounds), case
        for wait, (low, high) in zip(waits, expected_bounds, strict=True):
            assert low <= wait <= high, case


async def test_rate_limit_yields_without_sleep_or_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={}, headers={"retry-after": "60"})

    with pytest.raises(ProviderRateLimited) as raised:
        await _pages(_ProbeConnector(_retry_stream(), handle))

    assert raised.value.retry_after_seconds >= 60
    assert calls == 1
    assert waits == []


async def test_protected_rate_limit_waits_and_continues(monkeypatch: pytest.MonkeyPatch) -> None:
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={}, headers={"retry-after": "42"})
        return httpx.Response(200, json=[{"id": 1}])

    pages = await _pages(_ProbeConnector(_retry_stream(), handle), yield_rate_limits=False)

    assert _ids(pages) == {1}
    assert calls == 2
    assert len(waits) == 1
    assert 42 <= waits[0] <= 60


async def test_protected_rate_limit_stays_within_the_retry_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("ufo.runtime.sources.rest.random.uniform", lambda low, high: low)
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={}, headers={"retry-after": "600"})

    with pytest.raises(httpx.HTTPStatusError):
        await _pages(_ProbeConnector(_retry_stream(), handle), yield_rate_limits=False)

    assert waits == [60, 60]
    assert calls == 3


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, 60.0),
        ("0.25", 1.0),
        ("42", 42.0),
        ("600", 600.0),
        ("7200", 3600.0),
        ("nan", 60.0),
        ("-5", 60.0),
    ],
)
async def test_rate_limit_delay_is_finite_and_bounded(header: str | None, expected: float) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        headers = {} if header is None else {"retry-after": header}
        return httpx.Response(429, json={}, headers=headers)

    with pytest.raises(ProviderRateLimited) as raised:
        await _pages(_ProbeConnector(_retry_stream(), handle))

    assert raised.value.retry_after_seconds == expected


class _CannedConnector(RestConnector):
    """A connector whose `paginate` yields pre-canned pages, so the adapter's collapse — snapshot vs
    delta, checkpoint, deletes → source refs — is what's under test, not an HTTP loop. It owns its
    checkpoint the way a text-watermark provider does, since the adapter reads no record field."""

    name = "canned"
    base_url = "https://api.canned.test"
    checkpoint = staticmethod(text_checkpoint)

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


async def test_incremental_stream_advances_watermark_and_tombstones_deletes() -> None:
    stream = StreamSpec(
        name="tickets", source_object="tickets", cursor_field="updated_at", delete_missing=False
    )
    page = StreamPage(records=[{"id": "5", "updated_at": "2026-02-02T00:00:00Z"}], deletes=("9",))
    result = await ConnectorBackend(connector=_CannedConnector(stream, [page])).fetch(
        ConnectorSourceConfig(stream="tickets"), "2026-02-01T00:00:00Z", _auth(_ok)
    )
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert result.deletes == ("tickets/9",)
    assert {p.source_ref for p in result.pages} == {"tickets/5"}


async def test_connector_backend_fails_loud_without_an_auth_proxy() -> None:
    with pytest.raises(RuntimeError, match="needs an auth proxy"):
        await ConnectorBackend(connector=AsanaConnector()).fetch(
            ConnectorSourceConfig(stream="workspaces"),
            None,
            SourceAuth(workspace_id=uuid4()),
        )


class _PerTenantConnector(RestConnector):
    """A per-tenant connector whose class `base_url` is empty (like Freshdesk/Zendesk): the host
    must come from the connection's own tenant URL on `SourceAuth`, not a class default."""

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


async def test_per_tenant_fetch_without_a_base_url_fails_loud() -> None:
    """A per-tenant connector (class `base_url=""`) whose connection names no tenant URL fails its
    run naming the connector, rather than dialing an empty host."""
    with pytest.raises(RuntimeError, match=r"pertenant.*resolved no base_url"):
        await _fetch(_PerTenantConnector(), "rows", _ok)


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
    assert {page.source_ref for page in result.pages} == {"repositories/acme/7"}
    assert "acme/widgets" in result.pages[0].body


async def test_github_skips_when_org_enumeration_is_refused() -> None:
    """A grant with no org scope (`/user/orgs` → 403) can read no stream, so the fetch raises
    `StreamSkipped` — the driver records a skip, never a failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "insufficient scope"})

    with pytest.raises(StreamSkipped):
        await _fetch(GitHubConnector(), "repositories", handler)


def _asana_handler(
    page_by_offset: dict[str | None, dict[str, object]],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "app.asana.com"
        assert request.url.path.endswith("/workspaces")
        return httpx.Response(200, json=page_by_offset[request.url.params.get("offset")])

    return handle


async def test_direct_backend_returns_a_bearer_read_from_the_credential_store(db: None) -> None:
    """The direct backend reads a member-added key from the credential store host-side and returns
    it as a bearer — the secret is decrypted in-process, never surfaced to the sandbox or agent."""
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    await store.put(workspace_id, "github", "ghp_realkey")
    access = CredentialAccess(declared=frozenset({"github"}))
    with ws(workspace_id):
        credential = await DirectAuthProxy(credentials=access).credential(workspace_id, "github")
    assert credential == Credential(bearer="ghp_realkey")


async def _keyed_slots(**filled: str) -> tuple[UUID, CredentialAccess]:
    workspace_id = await _workspace()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    for slot, value in filled.items():
        await store.put(workspace_id, slot, value)
    declared = frozenset({"datadog_api_key", "datadog_application_key"})
    return workspace_id, CredentialAccess(declared=declared)


async def test_direct_backend_reads_a_two_key_provider_into_its_headers(db: None) -> None:
    """A connector declaring `key_headers` names one credential slot per header, under the
    provider's own slot names, so the backend fills each header from the slot beside it."""
    workspace_id, access = await _keyed_slots(
        datadog_api_key="dd-api", datadog_application_key="dd-app"
    )
    with ws(workspace_id):
        credential = await DirectAuthProxy(credentials=access).credential(workspace_id, "datadog")
    assert credential == Credential(
        headers={"DD-API-KEY": "dd-api", "DD-APPLICATION-KEY": "dd-app"}
    )


async def test_direct_backend_answers_grant_unusable_while_a_secret_is_unfilled(db: None) -> None:
    """A half-filled pair cannot authenticate, and Datadog refuses an API key with no application
    key beside it — so the run is skipped naming the slot to fill, spending no refusal."""
    workspace_id, access = await _keyed_slots(datadog_api_key="dd-api")
    with ws(workspace_id), pytest.raises(GrantUnusable, match="datadog_application_key"):
        await DirectAuthProxy(credentials=access).credential(workspace_id, "datadog")


async def test_direct_backend_refuses_a_provider_slot_it_never_declared() -> None:
    from ufo.runtime.ext.context import UndeclaredCredentialSlot

    access = CredentialAccess(declared=frozenset())
    with pytest.raises(UndeclaredCredentialSlot):
        await DirectAuthProxy(credentials=access).credential(uuid4(), "github")


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
    agent_id = uuid5(NAMESPACE_URL, f"{workspace_id}/main")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="main",
                prompt="p",
                model="m",
                is_main=True,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id
