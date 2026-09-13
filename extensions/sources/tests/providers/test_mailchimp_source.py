"""The Mailchimp connector over a mock transport: the top-level `?count&offset` walk hitting the
per-tenant data-center host (with the watermark advancing over `date_created`), the collections
under a list, a segment, a category and a report — three of them a whole list path deep — where a
capped run resumes in them, the members walk that stamps `list_id` on each member, and a refusal
surfacing as `StreamSkipped`. The class base URL is empty (per-tenant data center), so the host is
bound through `SourceAuth.base_url`. Offline — a canned transport, no token."""

from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.mailchimp import (
    MAILCHIMP_STREAMS,
    PAGE_SIZE,
    MailchimpConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.backend import MAX_RECORDS_PER_RUN
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]

BASE_URL = "https://us21.api.mailchimp.com"
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "lists": (ParentRecord(ref="lists/l1", fields={"id": "l1"}),),
    "interest_categories": (
        ParentRecord(ref="interest_categories/l1/c1", fields={"id": "c1", "list_id": "l1"}),
    ),
    "segments": (ParentRecord(ref="segments/l1/9", fields={"id": "9", "list_id": "l1"}),),
    "reports": (
        ParentRecord(ref="reports/camp1", fields={"id": "camp1"}),
        ParentRecord(ref="reports/camp2", fields={"id": "camp2"}),
    ),
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
    return await ConnectorBackend(connector=MailchimpConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_lists_top_level_walk_hits_the_dc_host_and_advances_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "us21.api.mailchimp.com"
        assert request.url.path == "/3.0/lists"
        return httpx.Response(
            200,
            json={
                "lists": [{"id": "l1", "date_created": "2026-02-01T00:00:00Z"}],
                "total_items": 1,
            },
        )

    result = await _fetch("lists", handle)
    assert _refs(result) == {"lists/l1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_one_subscriber_hash_in_two_lists_stops_landing_on_one_page(
    parents_reader: ParentsReader,
) -> None:
    """A member id is the hash of the address, so the same contact carries the same id in every list
    it belongs to. Keyed on the hash alone those two subscriptions were one page, rewritten by
    whichever list was walked last — so the member address gains its list, and the pages the old one
    holds are swept by the migration that ships with it."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "us21.api.mailchimp.com"
        assert request.url.path in {"/3.0/lists/l1/members", "/3.0/lists/l2/members"}
        return httpx.Response(
            200,
            json={
                "members": [
                    {
                        "id": "md5-of-ada",
                        "email_address": "ada@example.com",
                        "timestamp_signup": "",
                        "timestamp_opt": "2026-01-01T00:00:00Z",
                        "last_changed": "2026-02-02T00:00:00Z",
                    }
                ]
            },
        )

    landed = {
        "lists": (
            ParentRecord(ref="lists/l1", fields={"id": "l1"}),
            ParentRecord(ref="lists/l2", fields={"id": "l2"}),
        )
    }
    result = await _fetch("list_members", handle, parents=parents_reader(landed))

    assert _refs(result) == {"list_members/l1/md5-of-ada", "list_members/l2/md5-of-ada"}
    assert {page.source_identity for page in result.pages} == {
        "list_members/l1/md5-of-ada",
        "list_members/l2/md5-of-ada",
    }
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
    assert '"list_id": "l1"' in result.pages[0].body
    assert '"list_id": "l2"' in result.pages[1].body


async def test_list_children_fan_out_over_landed_lists(parents_reader: ParentsReader) -> None:
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        bodies = {
            "/3.0/lists/l1/segments": {"segments": [{"id": 9, "name": "seg", "list_id": "l1"}]},
            "/3.0/lists/l1/tag-search": {"tags": [{"id": 5, "name": "vip"}]},
            "/3.0/lists/l1/interest-categories": {
                "categories": [{"id": "c1", "title": "cat", "list_id": "l1"}]
            },
        }
        body = bodies.get(request.url.path)
        if body is None:
            return httpx.Response(404, json={"path": request.url.path})
        return httpx.Response(200, json=body)

    reader = parents_reader(LANDED)
    assert _refs(await _fetch("segments", handle, parents=reader)) == {"segments/l1/9"}
    assert _refs(await _fetch("tags", handle, parents=reader)) == {"tags/l1/5"}
    assert _refs(await _fetch("interest_categories", handle, parents=reader)) == {
        "interest_categories/l1/c1"
    }
    assert asked == [
        "/3.0/lists/l1/segments",
        "/3.0/lists/l1/tag-search",
        "/3.0/lists/l1/interest-categories",
    ]


async def test_three_deep_children_read_the_list_off_their_own_parent_record(
    parents_reader: ParentsReader,
) -> None:
    """Mailchimp's segment and interest-category records both carry the `list_id` their collection
    hangs under, so a grandchild composes its whole path from its parent's record alone."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/3.0/lists/l1/interest-categories/c1/interests":
            return httpx.Response(200, json={"interests": [{"id": "i1", "name": "int"}]})
        if request.url.path == "/3.0/lists/l1/segments/9/members":
            return httpx.Response(
                200,
                json={
                    "members": [
                        {"id": "sm1", "last_changed": "2026-02-02T00:00:00Z", "timestamp_opt": ""}
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    reader = parents_reader(LANDED)
    assert _refs(await _fetch("interests", handle, parents=reader)) == {"interests/l1/c1/i1"}
    assert _refs(await _fetch("segment_members", handle, parents=reader)) == {
        "segment_members/l1/9/sm1"
    }
    assert asked == [
        "/3.0/lists/l1/interest-categories/c1/interests",
        "/3.0/lists/l1/segments/9/members",
    ]


async def test_email_activity_explodes_each_recipients_actions_under_its_report(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in {
            "/3.0/reports/camp1/email-activity",
            "/3.0/reports/camp2/email-activity",
        }:
            return httpx.Response(
                200,
                json={
                    "emails": [
                        {
                            "email_id": "md5-of-ada",
                            "activity": [{"action": "open", "timestamp": "2026-01-01T00:00:00Z"}],
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("email_activity", handle, parents=parents_reader(LANDED))
    assert _refs(result) == {
        "email_activity/camp1/md5-of-ada:open:2026-01-01T00:00:00Z",
        "email_activity/camp2/md5-of-ada:open:2026-01-01T00:00:00Z",
    }


async def test_a_capped_run_does_not_re_ask_the_segment_it_finished(
    parents_reader: ParentsReader,
) -> None:
    """A run capped inside one segment's members checkpoints the segment it completed, so the resume
    starts at the one it did not reach."""
    asked: list[str] = []
    bulk = [{"id": f"m{index}"} for index in range(MAX_RECORDS_PER_RUN)]

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/3.0/lists/l1/segments/10/members":
            if request.url.params.get("offset") == str(PAGE_SIZE):
                return httpx.Response(200, json={"members": [{"id": "tail"}]})
            return httpx.Response(200, json={"members": bulk})
        return httpx.Response(200, json={"members": [{"id": "other"}]})

    landed = {
        "segments": (
            ParentRecord(ref="segments/l1/9", fields={"id": "9", "list_id": "l1"}),
            ParentRecord(ref="segments/l1/10", fields={"id": "10", "list_id": "l1"}),
        )
    }
    capped = await _fetch("segment_members", handle, parents=parents_reader(landed))

    assert len(capped.pages) > MAX_RECORDS_PER_RUN
    assert set(asked) == {"/3.0/lists/l1/segments/10/members"}
    assert capped.next_cursor is not None

    asked.clear()
    resumed = await _fetch(
        "segment_members", handle, cursor=capped.next_cursor, parents=parents_reader(landed)
    )

    assert asked == ["/3.0/lists/l1/segments/9/members"]
    assert _refs(resumed) == {"segment_members/l1/9/other"}


async def test_children_declare_the_parent_that_holds_them() -> None:
    declared = {stream.name: stream for stream in MAILCHIMP_STREAMS}
    edges = {
        name: tuple((edge.stream, edge.path) for edge in stream.parents)
        for name, stream in declared.items()
        if stream.parents
    }
    assert edges == {
        "list_members": (("lists", "/3.0/lists/{id}/members"),),
        "segments": (("lists", "/3.0/lists/{id}/segments"),),
        "tags": (("lists", "/3.0/lists/{id}/tag-search"),),
        "interest_categories": (("lists", "/3.0/lists/{id}/interest-categories"),),
        "interests": (
            ("interest_categories", "/3.0/lists/{list_id}/interest-categories/{id}/interests"),
        ),
        "segment_members": (("segments", "/3.0/lists/{list_id}/segments/{id}/members"),),
        "unsubscribes": (("reports", "/3.0/reports/{id}/unsubscribed"),),
        "email_activity": (("reports", "/3.0/reports/{id}/email-activity"),),
    }
    assert {name for name in edges if declared[name].canonical} == {"list_members"}
    assert declared["list_members"].parents[0].carry == {"list_id": "id"}
    assert all(declared[name].key_scope == "local" for name in edges)


async def test_unsubscribes_are_addressed_under_the_report_that_holds_them(
    parents_reader: ParentsReader,
) -> None:
    """One subscriber hash unsubscribing from two campaigns is two rows, and the report each was
    read under is what tells them apart."""

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path in {
            "/3.0/reports/camp1/unsubscribed",
            "/3.0/reports/camp2/unsubscribed",
        }
        return httpx.Response(
            200,
            json={
                "unsubscribes": [
                    {
                        "email_id": "md5-of-ada",
                        "contact_id": "ct1",
                        "email_address": "ada@example.com",
                        "timestamp": "2026-01-01T00:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("unsubscribes", handle, parents=parents_reader(LANDED))
    assert _refs(result) == {"unsubscribes/camp1/md5-of-ada", "unsubscribes/camp2/md5-of-ada"}
    assert {page.source_identity for page in result.pages} == {
        "unsubscribes/camp1/md5-of-ada",
        "unsubscribes/camp2/md5-of-ada",
    }


async def test_an_unsubscribe_without_a_subscriber_hash_is_dropped(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "unsubscribes": [
                    {"email_id": "md5-of-ada", "timestamp": "2026-01-01T00:00:00Z"},
                    {"timestamp": "2026-01-02T00:00:00Z"},
                ]
            },
        )

    result = await _fetch(
        "unsubscribes",
        handle,
        parents=parents_reader(
            {"reports": (ParentRecord(ref="reports/camp1", fields={"id": "camp1"}),)}
        ),
    )
    assert _refs(result) == {"unsubscribes/camp1/md5-of-ada"}
    assert result.dropped == 1


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("lists", handle)
