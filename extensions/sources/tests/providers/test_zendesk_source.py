"""The Zendesk connector over a mock transport: the incremental cursor export (with the sideloaded
`users` lifting `requester_email` onto each ticket), the `next_page`-linked default list, the
`ticket_events` feed transformed into comment rows stamped with `ticket_id`, and a refusal surfacing
as `StreamSkipped`. The class base URL is empty (per-subdomain), so the tenant host is bound through
`SourceAuth.base_url` — the real per-tenant path. Offline — a canned transport, no
token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.zendesk import ZendeskConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

BASE_URL = "https://acme.zendesk.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), base_url=BASE_URL)
    return await ConnectorBackend(connector=ZendeskConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_tickets_incremental_cursor_with_sideload_email() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "acme.zendesk.com"
        assert request.url.path == "/api/v2/incremental/tickets/cursor.json"
        assert request.url.params.get("include") == "users"
        return httpx.Response(
            200,
            json={
                "tickets": [{"id": 1, "requester_id": 5, "updated_at": "2026-02-01T00:00:00Z"}],
                "users": [{"id": 5, "email": "ada@example.com"}],
                "end_of_stream": True,
            },
        )

    result = await _fetch("tickets", handle)
    assert _refs(result) == {"tickets/1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    body = result.pages[0].body
    assert "ada@example.com" in body


async def test_groups_default_next_page_walk() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/groups.json"
        return httpx.Response(
            200, json={"groups": [{"id": 2, "updated_at": "2026-02-01T00:00:00Z"}]}
        )

    result = await _fetch("groups", handle)
    assert _refs(result) == {"groups/2"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"


async def test_ticket_comments_lift_comment_child_events() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/incremental/ticket_events.json"
        return httpx.Response(
            200,
            json={
                "ticket_events": [
                    {
                        "ticket_id": 9,
                        "timestamp": 1700000000,
                        "child_events": [
                            {"id": "c1", "event_type": "Comment", "body": "hello"},
                            {"id": "x2", "event_type": "Create"},
                        ],
                    }
                ],
                "end_of_stream": True,
            },
        )

    result = await _fetch("ticket_comments", handle)
    assert _refs(result) == {"ticket_comments/c1"}
    body = result.pages[0].body
    assert '"ticket_id": 9' in body or "'ticket_id': 9" in body
    assert result.pages[0].created_at == "2023-11-14T22:13:20.000000+00:00"


async def test_ticket_comments_preserve_a_string_event_timestamp() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v2/incremental/ticket_events.json"
        return httpx.Response(
            200,
            json={
                "ticket_events": [
                    {
                        "ticket_id": 9,
                        "timestamp": "2026-02-01T00:00:00Z",
                        "child_events": [
                            {"id": "c1", "event_type": "Comment", "body": "hello"},
                        ],
                    }
                ],
                "end_of_stream": True,
            },
        )

    result = await _fetch("ticket_comments", handle)
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": "Forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("tickets", handle)


async def test_sla_policies_read_the_key_zendesk_actually_answers_with() -> None:
    """`slas/policies.json` holds its rows under `sla_policies`, which is the stream's own name and
    so the default the dispatch already derives. An override naming anything else reads no rows at
    all: the list comes back absent rather than wrong, so the run lands nothing and reports fine."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/slas/policies.json":
            return httpx.Response(
                200,
                json={
                    "sla_policies": [
                        {"id": 7, "title": "Urgent", "updated_at": "2026-03-01T00:00:00Z"}
                    ],
                    "next_page": None,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("sla_policies", handle)

    assert _refs(result) == {"sla_policies/7"}


async def test_attribute_definitions_lift_both_condition_lists_to_rows() -> None:
    """The routing-attribute conditions arrive nested one level deeper than every other list here,
    as two same-shaped lists under `definitions`. Read as a flat collection it is an object, not
    records, which fails the page contract outright. One attribute may sit in both lists, so the
    condition it was listed under qualifies the row rather than one overwriting the other."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/routing/attributes/definitions.json":
            return httpx.Response(
                200,
                json={
                    "definitions": {
                        "conditions_all": [{"id": "att-1", "title": "Language"}],
                        "conditions_any": [{"id": "att-1", "title": "Language"}],
                    }
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("attribute_definitions", handle)

    assert _refs(result) == {
        "attribute_definitions/att-1/all",
        "attribute_definitions/att-1/any",
    }


async def test_article_comments_fan_out_over_articles() -> None:
    """Zendesk publishes article comments only under their article — there is no flat collection to
    ask for, and asking for one is a 404 the run cannot skip, so the stream never landed a row. The
    walk enumerates articles, pages each one's comments, and follows the parent's own pages so an
    article on page two is not missed."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/v2/help_center/articles.json":
            if b"page=2" in request.url.query:
                return httpx.Response(200, json={"articles": [{"id": 2}], "next_page": None})
            return httpx.Response(
                200,
                json={
                    "articles": [{"id": 1}],
                    "next_page": "https://acme.zendesk.com/api/v2/help_center/articles.json?page=2",
                },
            )
        if request.url.path == "/api/v2/help_center/articles/1/comments.json":
            return httpx.Response(
                200,
                json={
                    "comments": [{"id": 11, "body": "one", "updated_at": "2026-03-01T00:00:00Z"}],
                    "next_page": None,
                },
            )
        if request.url.path == "/api/v2/help_center/articles/2/comments.json":
            return httpx.Response(
                200,
                json={
                    "comments": [{"id": 22, "body": "two", "updated_at": "2026-03-02T00:00:00Z"}],
                    "next_page": None,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("article_comments", handle)

    assert _refs(result) == {"article_comments/11", "article_comments/22"}
    assert "/api/v2/help_center/article_comments.json" not in asked


async def test_article_comment_votes_carry_every_id_they_were_reached_through() -> None:
    """A vote sits two collections down, under a comment under an article. Each row carries both
    ancestor ids because the record names neither, and that is what ties a vote back to its
    article."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/v2/help_center/articles.json":
            return httpx.Response(200, json={"articles": [{"id": 5}], "next_page": None})
        if request.url.path == "/api/v2/help_center/articles/5/comments.json":
            return httpx.Response(200, json={"comments": [{"id": 50}], "next_page": None})
        if request.url.path == "/api/v2/help_center/articles/5/comments/50/votes.json":
            return httpx.Response(200, json={"votes": [{"id": 500, "value": 1}], "next_page": None})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("article_comment_votes", handle)

    assert _refs(result) == {"article_comment_votes/500"}
    body = result.pages[0].body
    assert '"article_id": 5' in body
    assert '"comment_id": 50' in body
