"""The Freshdesk connector over a mock transport: the page-number tickets loop (with `updated_since`
and the watermark advancing over `updated_at`) hitting the per-tenant host, the RFC 5988 link-header
default walk, the HTTP Basic auth built from a direct key (password `"X"`), and a refusal surfacing
as `StreamSkipped`. The class base URL is empty (per-tenant), so the tenant host is bound through
`SourceAuth.base_url`. The solutions and discussions descents are walked against a transport that
serves only the paths Freshdesk publishes and 404s the rest, so a request to a flat collection the
API does not expose fails. Offline — a canned transport, no token."""

import base64
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.freshdesk import (
    CONVERSATIONS_FETCH_BUDGET,
    SETTINGS_PAGE_KEY,
    SOLUTIONS_FETCH_BUDGET,
    FreshdeskConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

BASE_URL = "https://acme.freshdesk.com"
LANDED: Landed = {
    "tickets": (ParentRecord(ref="tickets/1", fields={"id": 1}),),
    "canned_response_folders": (ParentRecord(ref="canned_response_folders/12", fields={"id": 12}),),
    "solution_categories": (ParentRecord(ref="solution_categories/11", fields={"id": 11}),),
    "solution_folders": (ParentRecord(ref="solution_folders/11/21", fields={"id": 21}),),
    "discussion_categories": (ParentRecord(ref="discussion_categories/10", fields={"id": 10}),),
    "discussion_forums": (ParentRecord(ref="discussion_forums/10/20", fields={"id": 20}),),
    "discussion_topics": (ParentRecord(ref="discussion_topics/20/30", fields={"id": 30}),),
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    parents_reader: ParentsReader,
    cursor: str | None = None,
    landed: Landed = LANDED,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        base_url=BASE_URL,
        parents=parents_reader(landed),
    )
    return await ConnectorBackend(connector=FreshdeskConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_tickets_page_number_walk_hits_the_tenant_host_and_advances_watermark(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.freshdesk.com"
        assert request.url.path == "/api/v2/tickets"
        assert request.url.params.get("page") == "1"
        return httpx.Response(200, json=[{"id": 1, "updated_at": "2026-02-01T00:00:00Z"}])

    result = await _fetch("tickets", handle, parents_reader=parents_reader)
    assert _refs(result) == {"tickets/1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_groups_follow_the_link_header_default(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/groups"
        return httpx.Response(200, json=[{"id": 2, "name": "Support"}])

    result = await _fetch("groups", handle, parents_reader=parents_reader)
    assert _refs(result) == {"groups/2"}


async def test_the_settings_singleton_uses_a_constant_identity(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/settings/helpdesk"
        return httpx.Response(200, json={"primary_language": "en", "name": "Acme"})

    result = await _fetch("settings", handle, parents_reader=parents_reader)

    assert _refs(result) == {"settings/en"}
    assert {page.source_identity for page in result.pages} == {f"settings/{SETTINGS_PAGE_KEY}"}
    assert '"id"' not in result.pages[0].body


async def test_basic_auth_built_from_a_direct_key() -> None:
    client = FreshdeskConnector()._make_client(BASE_URL, Credential(bearer="key-123"))
    authed = next(client.auth.auth_flow(httpx.Request("GET", BASE_URL)))
    expected = "Basic " + base64.b64encode(b"key-123:X").decode()
    assert authed.headers["Authorization"] == expected


async def test_stream_skipped_on_refusal(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tickets", handle, parents_reader=parents_reader)


_DISCUSSION_TREE: dict[str, list[dict[str, object]]] = {
    "/api/v2/discussions/categories": [{"id": 10, "name": "General"}],
    "/api/v2/discussions/categories/10/forums": [{"id": 20, "name": "Announcements"}],
    "/api/v2/discussions/forums/20/topics": [{"id": 30, "title": "Release notes"}],
    "/api/v2/discussions/topics/30/comments": [{"id": 40, "body": "Fixed in 2.1."}],
}

_SOLUTION_TREE: dict[str, list[dict[str, object]]] = {
    "/api/v2/solutions/categories": [{"id": 11, "name": "Onboarding"}],
    "/api/v2/solutions/categories/11/folders": [{"id": 21, "name": "Setup"}],
    "/api/v2/solutions/folders/21/articles": [{"id": 31, "title": "Install the agent"}],
}


def _only_published(
    routes: dict[str, list[dict[str, object]]], requested: list[str]
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        records = routes.get(request.url.path)
        if records is None:
            return httpx.Response(404, json={"message": "Not Found", "code": "invalid_resource"})
        return httpx.Response(200, json=records)

    return handle


async def test_discussion_forums_descend_from_their_category(parents_reader: ParentsReader) -> None:
    requested: list[str] = []
    result = await _fetch(
        "discussion_forums",
        _only_published(_DISCUSSION_TREE, requested),
        parents_reader=parents_reader,
    )

    assert requested == ["/api/v2/discussions/categories/10/forums"]
    assert _refs(result) == {"discussion_forums/10/20"}


async def test_discussion_topics_descend_from_their_forum_under_its_category(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    result = await _fetch(
        "discussion_topics",
        _only_published(_DISCUSSION_TREE, requested),
        parents_reader=parents_reader,
    )

    assert requested == ["/api/v2/discussions/forums/20/topics"]
    assert _refs(result) == {"discussion_topics/20/30"}


async def test_discussion_comments_descend_the_whole_four_level_chain(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    result = await _fetch(
        "discussion_comments",
        _only_published(_DISCUSSION_TREE, requested),
        parents_reader=parents_reader,
    )

    assert requested == ["/api/v2/discussions/topics/30/comments"]
    assert _refs(result) == {"discussion_comments/30/40"}


async def test_canned_responses_hang_under_their_folder(parents_reader: ParentsReader) -> None:
    requested: list[str] = []
    routes = {"/api/v2/canned_response_folders/12/responses": [{"id": 22, "title": "Greeting"}]}
    result = await _fetch(
        "canned_responses", _only_published(routes, requested), parents_reader=parents_reader
    )

    assert requested == ["/api/v2/canned_response_folders/12/responses"]
    assert _refs(result) == {"canned_responses/12/22"}


async def test_solution_folders_hang_under_their_category(parents_reader: ParentsReader) -> None:
    requested: list[str] = []
    result = await _fetch(
        "solution_folders",
        _only_published(_SOLUTION_TREE, requested),
        parents_reader=parents_reader,
    )

    assert requested == ["/api/v2/solutions/categories/11/folders"]
    assert _refs(result) == {"solution_folders/11/21"}


async def test_the_re_addressed_children_restamp_nothing() -> None:
    streams = {stream.name: stream for stream in FreshdeskConnector().streams()}
    for name in (
        "canned_responses",
        "solution_folders",
        "discussion_forums",
        "discussion_topics",
        "discussion_comments",
    ):
        assert streams[name].canonical is False
        assert streams[name].key_scope == "local"


async def test_the_solutions_ancestors_sync_but_stay_out_of_memory() -> None:
    streams = {stream.name: stream for stream in FreshdeskConnector().streams()}
    syncing = syncing_streams(list(streams.values()))
    assert {"solution_categories", "solution_folders"} <= syncing
    for name in ("solution_categories", "solution_folders"):
        assert streams[name].canonical is False
        assert streams[name].indexed is False
    assert streams["solution_articles"].indexed is True
    assert "discussion_comments" not in syncing


async def test_a_forum_landed_without_its_id_raises_at_the_fan_out(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    landed = {"discussion_forums": (ParentRecord(ref="discussion_forums/10/20", fields={}),)}
    with pytest.raises(RuntimeError, match="discussion_topics"):
        await _fetch(
            "discussion_topics",
            _only_published(_DISCUSSION_TREE, requested),
            landed=landed,
            parents_reader=parents_reader,
        )


async def test_a_category_that_landed_nothing_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    result = await _fetch(
        "discussion_forums",
        _only_published(_DISCUSSION_TREE, requested),
        landed={},
        parents_reader=parents_reader,
    )
    assert requested == []
    assert result.pages == ()


async def test_discussion_categories_project_the_id_their_forums_read(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    result = await _fetch(
        "discussion_categories",
        _only_published(_DISCUSSION_TREE, requested),
        parents_reader=parents_reader,
    )
    assert result.pages[0].parent_fields == {"id": 10}


async def test_solution_articles_descend_from_folder_under_category(
    parents_reader: ParentsReader,
) -> None:
    requested: list[str] = []
    result = await _fetch(
        "solution_articles",
        _only_published(_SOLUTION_TREE, requested),
        parents_reader=parents_reader,
    )

    assert requested == ["/api/v2/solutions/folders/21/articles"]
    assert _refs(result) == {"solution_articles/31"}


async def test_conversations_hang_under_their_ticket(parents_reader: ParentsReader) -> None:
    requested: list[str] = []
    routes = {"/api/v2/tickets/1/conversations": [{"id": 41, "body_text": "On it."}]}
    result = await _fetch(
        "conversations", _only_published(routes, requested), parents_reader=parents_reader
    )

    assert requested == ["/api/v2/tickets/1/conversations"]
    assert _refs(result) == {"conversations/41"}


async def test_a_tenant_unique_id_keeps_the_address_its_flat_declaration_gave_it(
    parents_reader: ParentsReader,
) -> None:
    streams = {stream.name: stream for stream in FreshdeskConnector().streams()}
    assert streams["conversations"].key_scope == "global"
    assert streams["solution_articles"].key_scope == "global"

    requested: list[str] = []
    articles = await _fetch(
        "solution_articles",
        _only_published(_SOLUTION_TREE, requested),
        parents_reader=parents_reader,
    )
    assert {page.source_identity for page in articles.pages} == {"solution_articles/31"}

    routes = {"/api/v2/tickets/1/conversations": [{"id": 41, "body_text": "On it."}]}
    conversations = await _fetch(
        "conversations", _only_published(routes, requested), parents_reader=parents_reader
    )
    assert {page.source_identity for page in conversations.pages} == {"conversations/41"}


async def test_conversations_refan_only_the_tickets_that_moved(
    parents_reader: ParentsReader,
) -> None:
    """Freshdesk's `updated_since` admits a ticket on "any activity", a reply included — the filter
    main's descent walked, so `conversations` cost `1 + changed` requests a tick. The edge declares
    `refan`: a pass after the first reads only the tickets whose page moved."""
    requested: list[str] = []
    routes = {
        "/api/v2/tickets/1/conversations": [{"id": 41, "body_text": "On it."}],
        "/api/v2/tickets/2/conversations": [{"id": 42, "body_text": "Done."}],
    }
    handle = _only_published(routes, requested)

    def tickets(second: int) -> Landed:
        return {
            "tickets": (
                ParentRecord(ref="tickets/1", fields={"id": 1}, revision=3),
                ParentRecord(ref="tickets/2", fields={"id": 2}, revision=second),
            )
        }

    first = await _fetch("conversations", handle, parents_reader=parents_reader, landed=tickets(5))
    assert requested == ["/api/v2/tickets/1/conversations", "/api/v2/tickets/2/conversations"]

    requested.clear()
    held = await _fetch(
        "conversations",
        handle,
        parents_reader=parents_reader,
        cursor=first.next_cursor,
        landed=tickets(5),
    )
    assert requested == []

    requested.clear()
    await _fetch(
        "conversations",
        handle,
        parents_reader=parents_reader,
        cursor=held.next_cursor,
        landed=tickets(8),
    )
    assert requested == ["/api/v2/tickets/2/conversations"]


@pytest.mark.parametrize(
    ("stream", "parent", "template", "budget"),
    [
        (
            "conversations",
            "tickets",
            "/api/v2/tickets/{}/conversations",
            CONVERSATIONS_FETCH_BUDGET,
        ),
        (
            "solution_folders",
            "solution_categories",
            "/api/v2/solutions/categories/{}/folders",
            SOLUTIONS_FETCH_BUDGET,
        ),
        (
            "solution_articles",
            "solution_folders",
            "/api/v2/solutions/folders/{}/articles",
            SOLUTIONS_FETCH_BUDGET,
        ),
    ],
)
async def test_a_tick_asks_no_more_parents_than_the_row_budgets(
    stream: str, parent: str, template: str, budget: int, parents_reader: ParentsReader
) -> None:
    """Freshdesk's Growth plan allows 100 calls a minute and 40 list calls among them, account-wide,
    so a child that fanned over every landed parent each tick would spend the whole account on one
    row. The budget bounds the tick and the next one resumes after the parent it reached."""
    requested: list[str] = []
    landed: Landed = {
        parent: tuple(
            ParentRecord(ref=f"{parent}/{n}", fields={"id": n}) for n in range(1, budget + 4)
        )
    }
    routes = {template.format(n): [{"id": 100 + n}] for n in range(1, budget + 4)}
    handle = _only_published(routes, requested)

    first = await _fetch(stream, handle, parents_reader=parents_reader, landed=landed)
    assert len(requested) == budget

    requested.clear()
    await _fetch(
        stream, handle, parents_reader=parents_reader, cursor=first.next_cursor, landed=landed
    )
    assert len(requested) == 3
