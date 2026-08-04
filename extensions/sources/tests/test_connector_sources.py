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

import json
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

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
from ufo.connectors import (
    DIRECT_ACCOUNT,
    ConnectorRegistry,
    Credential,
    SourceCredentialResolver,
)
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import CredentialAccess, SourceReader, context_for
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
from ufo.sources import rest
from ufo.sources.sync import CorePageFeed, SourceAuth, StreamSkipped, SyncDriver
from ufo.subjects import SHARED_SUBJECT
from ufo.workspace import init_workspace_credentials, ws

ACCOUNT = "acct-1"
QUOTA_WINDOW_SECONDS = 60.0
STATED_RESET_SECONDS = 45.0


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
            stream,
            cursor=cursor,
            credential=Credential(bearer="tok"),
            base_url="",
            self_user_id=None,
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

    monkeypatch.setattr("ufo.sources.rest.asyncio.sleep", fake_sleep)
    return waits


@pytest.mark.parametrize(
    ("failing_calls", "expected_bounds"),
    [
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "42"})], [(42.0, 60.0)]),
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "600"})], [(60.0, 60.0)]),
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "0.25"})], [(1.0, 1.5)]),
        (
            [lambda: httpx.Response(503, json={})] * 5
            + [lambda: httpx.Response(429, json={}, headers={"retry-after": "1"})],
            [(1.0, 1.5), (2.0, 3.0), (4.0, 6.0), (8.0, 12.0), (16.0, 24.0), (30.0, 30.0)],
        ),
        (
            [lambda: httpx.Response(503, json={})] * 5
            + [lambda: httpx.Response(429, json={}, headers={"retry-after": "30"})],
            [(1.0, 1.5), (2.0, 3.0), (4.0, 6.0), (8.0, 12.0), (16.0, 24.0), (30.0, 30.0)],
        ),
        (
            [lambda: httpx.Response(503, json={})] * 5
            + [lambda: httpx.Response(429, json={}, headers={"retry-after": "32"})],
            [(1.0, 1.5), (2.0, 3.0), (4.0, 6.0), (8.0, 12.0), (16.0, 24.0), (32.0, 48.0)],
        ),
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "inf"})], [(1.0, 1.5)]),
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "nan"})], [(1.0, 1.5)]),
        ([lambda: httpx.Response(429, json={}, headers={"retry-after": "-5"})], [(1.0, 1.5)]),
        ([lambda: httpx.Response(429, json={})], [(1.0, 1.5)]),
        (
            [
                lambda: httpx.Response(
                    429, json={}, headers={"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}
                )
            ],
            [(1.0, 1.5)],
        ),
        ([lambda: httpx.Response(503, json={}, headers={"retry-after": "42"})], [(42.0, 60.0)]),
        ([lambda: httpx.Response(504, json={}, headers={"retry-after": "42"})], [(42.0, 60.0)]),
        ([lambda: httpx.Response(500, json={}, headers={"retry-after": "42"})], [(1.0, 1.5)]),
        ([_connect_error], [(1.0, 1.5)]),
        (
            [lambda: httpx.Response(503, json={}), lambda: httpx.Response(503, json={})],
            [(1.0, 1.5), (2.0, 3.0)],
        ),
    ],
    ids=[
        "429-seconds",
        "429-seconds-over-the-cap",
        "429-seconds-under-the-doubling-delay",
        "429-seconds-under-a-late-doubling-delay",
        "429-seconds-tying-the-ladder-bound-under-a-late-doubling-delay",
        "429-seconds-over-the-ladder-bound-under-a-late-doubling-delay",
        "429-inf",
        "429-nan",
        "429-negative",
        "429-no-header",
        "429-http-date",
        "503-header-read",
        "504-header-read",
        "500-header-not-read",
        "transport-error",
        "503-twice-doubles",
    ],
)
async def test_retry_waits_a_bounded_retry_after_else_a_jittered_doubling_delay(
    failing_calls: list[Callable[[], httpx.Response]],
    expected_bounds: list[tuple[float, float]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A come-back-later response carrying `Retry-After` must wait at least that long — the provider
    is telling us when it will accept the next request, which beats the connector's own guessed
    doubling delay — capped at `RETRY_AFTER_MAX_SECONDS` so a header naming a distant rate-limit
    reset cannot park one page fetch on it. A stated reset at or below the delay the ladder has
    already reached, clamped by the ladder's own bound, leaves that delay standing under that bound:
    a quarter-second reset must not turn the remaining attempts into back-to-back requests, and one
    arriving late — after five failures have carried the delay to 32 — must not lift the draw to the
    higher cap a raised floor would have earned. A reset naming exactly that clamped delay ties, and
    the tie goes to the ladder: 30 seconds against a ladder its own bound already holds to 30 draws
    the single point 30, not the spread up to 45 the stated reset's cap would open. A reset above
    that clamped delay does earn it, even where the nominal delay has outrun the reset: 32 seconds
    against a ladder standing at 32 draws from 32 under the stated reset's cap, not the 30 the
    ladder's bound would hold it to. `503` and `504` are read alongside `429`: behind a quota proxy
    they name a reset just as usefully. A `500` is a fault rather than a schedule, so its header
    stays unread — the set of statuses whose header counts is deliberate, not blanket.

    Every other shape takes the doubling delay: a non-finite or negative seconds count (a `nan`
    reaching `asyncio.sleep` corrupts the shared loop's timer heap), no header at all, an RFC 9110
    HTTP-date that the seconds parse rejects, and a transport error — which the envelope hands to
    the same wait calculation carrying no response at all to read a header off. The last case
    fails twice, so the doubling is observed across consecutive retries rather than assumed from
    one.

    Each pair of bounds is the interval the envelope draws from: the floor it computed, up to
    `JITTER_MAX_FACTOR` times that floor and never past the cap governing it — which is why the
    over-the-cap row is one point rather than a range. The lower bound is what pins the jitter as
    one-sided, so a stated reset is never undercut."""
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= len(failing_calls):
            return failing_calls[calls - 1]()
        return httpx.Response(200, json=[{"id": 1}])

    assert _ids(await _pages(_ProbeConnector(_retry_stream(), handle))) == {1}
    assert len(waits) == len(expected_bounds)
    for wait, (low, high) in zip(waits, expected_bounds, strict=True):
        assert low <= wait <= high


async def test_the_retry_envelope_outlasts_a_per_minute_quota_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A permanently rate-limited request must spend more than a minute of waiting before it gives
    up, because the quota bucket it waits on refills on roughly that cadence. A ladder that raises
    inside the window discards every page the run has already paid for and re-spends the same
    requests a minute later.

    Jitter is pinned to its floor so the ladder is asserted exactly; the property under test is the
    total."""
    monkeypatch.setattr("ufo.sources.rest.random.uniform", lambda low, high: low)
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={})

    with pytest.raises(httpx.HTTPStatusError) as raised:
        await _pages(_ProbeConnector(_retry_stream(), handle))

    assert raised.value.response.status_code == 429
    assert calls == rest.MAX_ATTEMPTS
    assert waits == [1.0, 2.0, 4.0, 8.0, 16.0, 30.0, 30.0]
    assert sum(waits) > QUOTA_WINDOW_SECONDS
    assert sum(waits) <= rest.RETRY_BUDGET_SECONDS


async def test_the_retry_budget_stops_a_long_retry_after_before_the_attempt_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bounds, not one: a provider naming a 60-second reset on every attempt would otherwise
    spend the attempt cap on minutes of waiting for a single request. The budget ends the envelope
    first, before the attempts are used up."""
    monkeypatch.setattr("ufo.sources.rest.random.uniform", lambda low, high: low)
    waits = _record_waits(monkeypatch)
    calls = 0

    def handle(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(429, json={}, headers={"retry-after": "60"})

    with pytest.raises(httpx.HTTPStatusError):
        await _pages(_ProbeConnector(_retry_stream(), handle))

    assert waits == [60.0, 60.0]
    assert calls < rest.MAX_ATTEMPTS


async def test_retry_waits_are_jittered_so_workers_do_not_retry_in_lockstep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of the jitter: many workers sharing one provider's per-user quota trip it
    together, and a fixed doubling grid has them all retry at the same instants and re-collide.
    Repeated runs of the same single failure must therefore not settle on one wait — while staying
    inside the one-sided factor range, so the nominal delay remains a floor."""
    waits = _record_waits(monkeypatch)

    for _ in range(20):
        calls = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(429, json={}) if calls == 1 else httpx.Response(200, json=[])

        await _pages(_ProbeConnector(_retry_stream(), handle))

    ceiling = rest.RETRY_INITIAL_DELAY_SECONDS * rest.JITTER_MAX_FACTOR
    assert len(waits) == 20
    assert all(rest.RETRY_INITIAL_DELAY_SECONDS <= wait <= ceiling for wait in waits)
    assert len(set(waits)) > 1


async def test_a_stated_reset_spreads_across_the_headroom_below_its_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stated reset is a floor the spread builds up from, and `RETRY_AFTER_MAX_SECONDS` is the
    ceiling it stops at, so a header naming 45 seconds spreads over the 15 seconds of headroom
    between them. The cap is asserted absent from the draws, not merely as their upper bound."""
    waits = _record_waits(monkeypatch)

    for _ in range(20):
        calls = 0

        def handle(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                header = f"{STATED_RESET_SECONDS:g}"
                return httpx.Response(429, json={}, headers={"retry-after": header})
            return httpx.Response(200, json=[])

        await _pages(_ProbeConnector(_retry_stream(), handle))

    assert len(waits) == 20
    assert all(STATED_RESET_SECONDS <= wait <= rest.RETRY_AFTER_MAX_SECONDS for wait in waits)
    assert rest.RETRY_AFTER_MAX_SECONDS not in waits
    assert len(set(waits)) > 1


async def test_a_jittered_wait_stays_under_the_bound_that_governs_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two bounds govern the two kinds of wait, and the spread crosses neither: a doubling delay
    stops at `RETRY_MAX_DELAY_SECONDS`, and a stated reset stops at `RETRY_AFTER_MAX_SECONDS`. Both
    ladders here run their jitter live, and both climb past their bound in nominal terms — the
    doubling one reaches 32 and 64, the stated one names 600 — so each asserts the bound holding the
    drawn wait, not the nominal one."""
    waits = _record_waits(monkeypatch)

    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={})

    with pytest.raises(httpx.HTTPStatusError):
        await _pages(_ProbeConnector(_retry_stream(), rate_limited))

    assert all(wait <= rest.RETRY_MAX_DELAY_SECONDS for wait in waits)
    assert waits[-1] == rest.RETRY_MAX_DELAY_SECONDS
    assert sum(waits) > QUOTA_WINDOW_SECONDS

    waits.clear()

    def distant_reset(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={}, headers={"retry-after": "600"})

    with pytest.raises(httpx.HTTPStatusError):
        await _pages(_ProbeConnector(_retry_stream(), distant_reset))

    assert waits == [rest.RETRY_AFTER_MAX_SECONDS, rest.RETRY_AFTER_MAX_SECONDS]


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
    assert {page.source_ref for page in result.pages} == {"repositories/acme/7"}
    assert "acme/widgets" in result.pages[0].body


async def test_github_issues_fan_out_link_pagination_and_pr_filter() -> None:
    """Issues are incremental: fan out over granted repos, follow the Link header, drop pull
    requests, and checkpoint the repo's watermark into the per-repo cursor map — snapshot=False,
    no deletes (GitHub has no delete)."""
    result = await _fetch(GitHubConnector(), "issues", _github_handler([]))
    assert {p.source_ref for p in result.pages} == {
        "issues/acme/widgets/1",
        "issues/acme/widgets/3",
    }
    assert any("Bug" in p.body for p in result.pages)
    assert result.snapshot is False
    assert result.next_cursor == json.dumps(
        {"acme/widgets": "2026-01-05T00:00:00Z"}, sort_keys=True
    )
    assert result.deletes == ()


async def test_github_issues_send_since_and_state_when_a_cursor_is_stored() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    await ConnectorBackend(connector=GitHubConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="issues"),
        json.dumps({"acme/widgets": "2026-01-01T00:00:00Z"}, sort_keys=True),
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
    init_workspace_credentials(store)
    await store.put(workspace_id, "github", "ghp_realkey")
    access = CredentialAccess(declared=frozenset({"github"}))
    with ws(workspace_id):
        credential = await DirectAuthProxy(credentials=access).credential(
            workspace_id, "github", ACCOUNT
        )
    assert credential == Credential(bearer="ghp_realkey")


async def test_direct_backend_refuses_a_provider_slot_it_never_declared() -> None:
    from ufo.ext.context import UndeclaredCredentialSlot

    access = CredentialAccess(declared=frozenset())
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


async def test_asana_source_syncs_through_the_driver_into_recallable_memory(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """End to end: register an asana source through the SDK, let the core sync driver drive the
    connector's backend (records pulled through a mock auth-proxy transport, no token read), and
    recall the landed page through memory — the both-ends proof for a connector source."""
    workspace_id = await _workspace()

    context = context_for("sources", frozenset())
    with ws(workspace_id):
        await context.register_source(
            "asana",
            ConnectorSourceConfig(account=DIRECT_ACCOUNT, stream="workspaces"),
            subject=SHARED_SUBJECT,
            owner_member_id=None,
        )

    handler = _asana_handler({None: {"data": [{"gid": "111", "name": "Acme HQ workspace"}]}})
    embed = _StubEmbed(_vec((6, 1.0)))
    index = DefaultIndex(transaction=workspace_tx)
    blob = FilesystemBlobStore(root=tmp_path)
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(
        backends={"asana": ConnectorBackend(connector=AsanaConnector())},
        blob=blob,
        postgres=postgres,
        source_credentials=SourceCredentialResolver(
            ConnectorRegistry(entries={}, fallback=_MockProxy(handler=handler))
        ),
    )
    page_feed = CorePageFeed(blob=blob)
    page_indexer = PageIndexer(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        chunker=TextChunker(),
        workspace_id=workspace_id,
        page_states=context.page_states,
    )
    service = MemoryStore(
        index=index,
        embed=embed,
        transaction=workspace_tx,
        workspace_id=workspace_id,
        page_states=context.page_states,
        readable_page_states=context.readable_page_states,
        readable_source_ids=context.readable_source_ids,
    )

    await driver.run()
    async with workspace_tx() as connection:
        chunks = (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()
    assert chunks == 0

    with ws(workspace_id):
        await page_indexer.apply((await page_feed.pages_changed_since(None, 50)).changes)
    with ws(workspace_id):
        matches = await service.search_sources(
            "Acme HQ workspace",
            frozenset({SHARED_SUBJECT}),
            5,
            source_reader=SourceReader(
                agent_id=uuid5(NAMESPACE_URL, f"{workspace_id}/main"),
                requesting_member_id=None,
                subjects=frozenset({SHARED_SUBJECT}),
            ),
        )
    assert matches and "Acme HQ workspace" in matches[0].text
