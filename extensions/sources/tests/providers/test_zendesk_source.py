"""The Zendesk connector over a mock transport: the incremental cursor export (with the sideloaded
`users` lifting `requester_email` onto each ticket), the `next_page`-linked default list, the
`ticket_events` feed transformed into comment rows stamped with `ticket_id`, and a refusal surfacing
as `StreamSkipped`. The class base URL is empty (per-subdomain), so the tenant host is bound through
`SourceAuth.base_url` — the real per-tenant path. Offline — a canned transport, no
token."""

import hashlib
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.zendesk import ZENDESK_STREAMS, ZendeskConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

BASE_URL = "https://acme.zendesk.com"
COMMENT = {
    "id": 11,
    "url": "https://acme.zendesk.com/api/v2/help_center/articles/101/comments/11.json",
    "body": "one",
    "author_id": 77,
    "source_id": 101,
    "source_type": "Article",
    "locale": "en-us",
    "vote_sum": 2,
    "vote_count": 3,
    "created_at": "2026-03-01T00:00:00Z",
    "updated_at": "2026-03-01T00:00:00Z",
}
COMMENT_BODY_DIGEST = "4367929d6bd1d55f9f7543005cab14cfe8ac4e82ce2d73dfd398758fd76e0ff6"
"""What `COMMENT` renders to, pinned so a change to the body a landed comment page holds — the
`article_id` its edge carries included — has to be made on purpose."""

LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "articles": (ParentRecord(ref="articles/101", fields={"id": 101}),),
    "article_comments": (
        ParentRecord(ref="article_comments/11", fields={"id": 11, "source_id": 101}),
    ),
    "posts": (ParentRecord(ref="posts/201", fields={"id": 201}),),
    "post_comments": (ParentRecord(ref="post_comments/61", fields={"id": 61, "post_id": 201}),),
    "users": (ParentRecord(ref="users/301", fields={"id": 301}),),
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
    cursor: str | None = None,
    parents: ParentPages | None = None,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        base_url=BASE_URL,
        parents=parents,
    )
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


async def test_article_comments_hang_on_their_article_and_keep_a_comment_id_address(
    parents_reader: ParentsReader,
) -> None:
    """Zendesk publishes article comments only under their article, and a comment id is unique
    across the account — so the edge reads the article to compose the request and the comment keeps
    the address it is already recallable at. Its page projects both fields its own child reads."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        assert request.url.params.get("per_page") == "100"
        if request.url.path == "/api/v2/help_center/articles/101/comments.json":
            return httpx.Response(200, json={"comments": [COMMENT], "next_page": None})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("article_comments", handle, parents=parents_reader(LANDED))

    assert _refs(result) == {"article_comments/11"}
    assert {page.source_identity for page in result.pages} == {"article_comments/11"}
    assert asked == ["/api/v2/help_center/articles/101/comments.json"]
    assert result.pages[0].parent_fields == {"id": 11, "source_id": 101}
    assert hashlib.sha256(result.pages[0].body.encode()).hexdigest() == COMMENT_BODY_DIGEST


async def test_article_comment_votes_read_the_article_off_the_comment_record(
    parents_reader: ParentsReader,
) -> None:
    """A vote sits two collections down, under a comment under an article, and Zendesk's comment
    record names its article `source_id` — so that is the field the edge reads, and the values it
    reads are what address the vote."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/v2/help_center/articles/101/comments/11/votes.json":
            return httpx.Response(200, json={"votes": [{"id": 500, "value": 1}], "next_page": None})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("article_comment_votes", handle, parents=parents_reader(LANDED))

    assert _refs(result) == {"article_comment_votes/101/11/500"}
    assert asked == ["/api/v2/help_center/articles/101/comments/11/votes.json"]


async def test_article_attachments_and_votes_fan_out_over_landed_articles(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/v2/help_center/articles/101/attachments.json":
            return httpx.Response(
                200,
                json={"article_attachments": [{"id": 31, "file_name": "f.png"}], "next_page": None},
            )
        if request.url.path == "/api/v2/help_center/articles/101/votes.json":
            return httpx.Response(200, json={"votes": [{"id": 41, "value": 1}], "next_page": None})
        return httpx.Response(404, json={"path": request.url.path})

    attachments = await _fetch("article_attachments", handle, parents=parents_reader(LANDED))
    votes = await _fetch("article_votes", handle, parents=parents_reader(LANDED))

    assert _refs(attachments) == {"article_attachments/101/31"}
    assert _refs(votes) == {"article_votes/101/41"}
    assert asked == [
        "/api/v2/help_center/articles/101/attachments.json",
        "/api/v2/help_center/articles/101/votes.json",
    ]


async def test_post_children_fan_out_over_landed_posts_and_their_comments(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/v2/community/posts/201/comments.json":
            return httpx.Response(
                200,
                json={
                    "comments": [{"id": 61, "post_id": 201, "updated_at": "2026-03-01T00:00:00Z"}],
                    "next_page": None,
                },
            )
        if request.url.path == "/api/v2/community/posts/201/votes.json":
            return httpx.Response(200, json={"votes": [{"id": 71, "value": 1}], "next_page": None})
        if request.url.path == "/api/v2/community/posts/201/comments/61/votes.json":
            return httpx.Response(200, json={"votes": [{"id": 81, "value": -1}], "next_page": None})
        return httpx.Response(404, json={"path": request.url.path})

    reader = parents_reader(LANDED)
    assert _refs(await _fetch("post_comments", handle, parents=reader)) == {"post_comments/201/61"}
    assert _refs(await _fetch("post_votes", handle, parents=reader)) == {"post_votes/201/71"}
    assert _refs(await _fetch("post_comment_votes", handle, parents=reader)) == {
        "post_comment_votes/201/61/81"
    }
    assert asked == [
        "/api/v2/community/posts/201/comments.json",
        "/api/v2/community/posts/201/votes.json",
        "/api/v2/community/posts/201/comments/61/votes.json",
    ]


async def test_users_identities_fan_out_over_landed_users(parents_reader: ParentsReader) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/api/v2/users/301/identities.json":
            return httpx.Response(
                200,
                json={
                    "identities": [
                        {"id": 401, "type": "email", "updated_at": "2026-03-01T00:00:00Z"}
                    ],
                    "next_page": None,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("users_identities", handle, parents=parents_reader(LANDED))

    assert _refs(result) == {"users_identities/301/401"}
    assert asked == ["/api/v2/users/301/identities.json"]


async def test_help_centre_children_declare_the_parent_that_holds_them() -> None:
    declared = {stream.name: stream for stream in ZENDESK_STREAMS}
    edges = {
        name: tuple((edge.stream, edge.path) for edge in stream.parents)
        for name, stream in declared.items()
        if stream.parents
    }
    assert edges == {
        "article_attachments": (
            ("articles", "/api/v2/help_center/articles/{id}/attachments.json"),
        ),
        "article_comments": (("articles", "/api/v2/help_center/articles/{id}/comments.json"),),
        "article_votes": (("articles", "/api/v2/help_center/articles/{id}/votes.json"),),
        "article_comment_votes": (
            (
                "article_comments",
                "/api/v2/help_center/articles/{source_id}/comments/{id}/votes.json",
            ),
        ),
        "post_comments": (("posts", "/api/v2/community/posts/{id}/comments.json"),),
        "post_votes": (("posts", "/api/v2/community/posts/{id}/votes.json"),),
        "post_comment_votes": (
            ("post_comments", "/api/v2/community/posts/{post_id}/comments/{id}/votes.json"),
        ),
        "users_identities": (("users", "/api/v2/users/{id}/identities.json"),),
    }
    assert {name for name in edges if declared[name].canonical} == {"article_comments"}
    assert declared["article_comments"].key_scope == "global"
    assert {name for name in edges if declared[name].key_scope == "local"} == edges.keys() - {
        "article_comments"
    }
