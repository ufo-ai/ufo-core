"""Intercom connector over a mock transport: the search-API POST body + `pages.next.starting_after`
cursor, the scroll API, the `Intercom-Version` header, numeric watermark ordering, segments read
under the company that holds them, and the `StreamSkipped` a refusal raises. Offline — a canned
transport, no DB, no token, no broker."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.intercom import (
    INTERCOM_STREAMS,
    INTERCOM_VERSION,
    IntercomConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    no_parents,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]

LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "companies": (ParentRecord(ref="companies/comp1", fields={"id": "comp1"}),),
    "conversations": (ParentRecord(ref="conversations/c1", fields={"id": "c1"}),),
}


def _spec(name: str):
    return next(spec for spec in INTERCOM_STREAMS if spec.name == name)


CONV1 = {
    "id": "c1",
    "updated_at": 1700000000,
    "title": "Login issue",
    "source": {"type": "conversation", "subject": "Help", "body": "<p>I can't log in</p>"},
}
CONV2 = {
    "id": "c2",
    "updated_at": 1700000100,
    "title": "Billing question",
    "source": {"type": "conversation", "subject": "Invoice", "body": "<p>Charged twice</p>"},
}


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages = no_parents,
):
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=IntercomConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _flat(result, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


def _conversations_handler(
    seen_values: list[object],
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.intercom.io"
        assert request.headers.get("Intercom-Version") == INTERCOM_VERSION
        if request.method == "POST" and request.url.path == "/conversations/search":
            body = json.loads(request.content)
            assert body["sort"] == {"field": "updated_at", "order": "ascending"}
            seen_values.append(body["query"]["value"])
            after = body.get("pagination", {}).get("starting_after")
            if after == "sa1":
                return httpx.Response(200, json={"conversations": [CONV2], "pages": {}})
            return httpx.Response(
                200,
                json={"conversations": [CONV1], "pages": {"next": {"starting_after": "sa1"}}},
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_search_paginates_and_normalizes_the_cursor() -> None:
    seen: list[object] = []
    result = await _fetch("conversations", _conversations_handler(seen))

    assert {page.source_ref for page in result.pages} == {"conversations/c1", "conversations/c2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "1700000100"
    assert seen[0] == 0

    page = next(page for page in result.pages if page.source_ref == "conversations/c1")
    assert page.updated_at == "2023-11-14T22:13:20.000000+00:00"
    assert "Login issue" in page.body


async def test_watermark_compares_integer_values_across_decimal_widths() -> None:
    seen: list[int] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content)["query"]["value"])
        return httpx.Response(
            200,
            json={
                "conversations": [{"id": str(value), "updated_at": value} for value in (999, 1000)],
                "pages": {},
            },
        )

    first = await _fetch("conversations", handle, cursor="998")
    assert first.next_cursor == "1000"
    await _fetch("conversations", handle, cursor=first.next_cursor)
    assert seen == [998, 1000]


async def test_conversations_flatten_lifts_source_and_requester_and_keeps_cursor_stringify() -> (
    None
):
    conv = {
        "id": "c9",
        "updated_at": 1700000200,
        "source": {"type": "email", "subject": "Bug", "body": "<p>broken</p>"},
        "contacts": {"contacts": [{"id": "ct1"}]},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/conversations/search":
            return httpx.Response(200, json={"conversations": [conv], "pages": {}})
        return httpx.Response(404, json={"path": request.url.path})

    record = _flat(await _fetch("conversations", handle), "conversations/c9")
    assert record["source__type"] == "email"
    assert record["source__subject"] == "Bug"
    assert record["source__body"] == "<p>broken</p>"
    assert record["requester_id"] == "ct1"
    assert record["updated_at"] == 1700000200


async def test_conversation_parts_flatten_surface_author_type_and_id(
    parents_reader: ParentsReader,
) -> None:
    """An Intercom conversation part id is unique account-wide, so the stream keys globally and its
    pages keep the address they have on the release this replaces — `conversation_parts/p1`, with
    the conversation carried as a record field, which is the only place a reader finds it once the
    address does not. The `/conversations/search` walk the stream ran for itself is gone: the
    conversations row already landed those records."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        return httpx.Response(
            200,
            json={
                "conversation_parts": {
                    "conversation_parts": [{"id": "p1", "author": {"type": "admin", "id": "a1"}}]
                }
            },
        )

    result = await _fetch("conversation_parts", handle, parents=parents_reader(LANDED))
    assert seen == ["GET /conversations/c1"]
    assert [page.source_identity for page in result.pages] == ["conversation_parts/p1"]
    assert [page.source_ref for page in result.pages] == ["conversation_parts/p1"]
    record = _flat(result, "conversation_parts/p1")
    assert record["author_type"] == "admin"
    assert record["author_id"] == "a1"
    assert record["conversation_id"] == "c1"


@pytest.mark.parametrize(("stream", "path"), [("tags", "/tags"), ("teams", "/teams")])
async def test_tags_and_teams_key_on_their_id_so_a_rename_keeps_the_page(
    stream: str, path: str
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == path:
            return httpx.Response(
                200, json={"data": [{"id": "7", "name": "Renamed", "type": stream[:-1]}]}
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(stream, handle)
    assert [page.source_ref for page in result.pages] == [f"{stream}/Renamed"]
    assert [page.source_identity for page in result.pages] == [f"{stream}/7"]
    assert result.pages[0].title == "Renamed"


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": [{"code": "forbidden"}]})

    with pytest.raises(StreamSkipped):
        await _fetch("conversations", handle)


async def test_data_attributes_key_on_the_id_and_a_standard_one_on_its_full_name() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/data_attributes":
            assert request.url.params.get("model") == "contact"
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": 91, "name": "renamed_field", "full_name": "custom_attributes.paid"},
                        {"name": "email", "full_name": "email"},
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("contact_attributes", handle)
    assert {page.source_ref for page in result.pages} == {
        "contact_attributes/renamed_field",
        "contact_attributes/email",
    }
    assert {page.source_identity for page in result.pages} == {
        "contact_attributes/91",
        "contact_attributes/email",
    }


async def test_segments_are_addressed_under_the_company_that_holds_them(
    parents_reader: ParentsReader,
) -> None:
    """One request per landed company, and the `/companies/scroll` walk the companies row already
    spends is not spent again here. A segment id is unique workspace-wide, so the company in the
    address scopes rather than disambiguates it — the stream is not canonical, so no page has ever
    landed under the unscoped key."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200, json={"data": [{"id": "seg1", "name": "Enterprise", "updated_at": 1700000000}]}
        )

    result = await _fetch("company_segments", handle, parents=parents_reader(LANDED))

    assert seen == ["/companies/comp1/segments"]
    assert [page.source_identity for page in result.pages] == ["company_segments/comp1/seg1"]
    assert [page.source_ref for page in result.pages] == ["company_segments/comp1/seg1"]
    assert _spec("company_segments").canonical is False


async def test_a_company_that_has_landed_nothing_yet_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    result = await _fetch("company_segments", handle, parents=no_parents)
    assert result.pages == ()


async def test_conversation_parts_declare_the_global_key_that_keeps_their_address() -> None:
    """`conversation_parts` is the one canonical stream converted here, so the declaration that
    leaves its landed pages where they are is load-bearing: `local` would address every one of them
    a second time under its conversation."""
    parts = _spec("conversation_parts")
    assert parts.canonical is True
    assert parts.key_scope == "global"
    assert [edge.path for edge in parts.parents] == ["/conversations/{id}"]


async def test_conversation_parts_refan_only_the_conversations_that_moved(
    parents_reader: ParentsReader,
) -> None:
    """Intercom moves a conversation's `updated_at` when a part lands, which is the filter the
    `/conversations/search` walk this stream ran on main narrowed by. The edge declares `refan`, so
    a pass after the first reads only the conversations whose page moved — `1 + changed` detail
    requests a tick, not one per landed conversation."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        conversation = request.url.path.rsplit("/", 1)[-1]
        return httpx.Response(
            200,
            json={"conversation_parts": {"conversation_parts": [{"id": f"p-{conversation}"}]}},
        )

    def landed(second: int) -> Mapping[str, tuple[ParentRecord, ...]]:
        return {
            "conversations": (
                ParentRecord(ref="conversations/c1", fields={"id": "c1"}, revision=3),
                ParentRecord(ref="conversations/c2", fields={"id": "c2"}, revision=second),
            )
        }

    first = await _fetch("conversation_parts", handle, parents=parents_reader(landed(5)))
    assert seen == ["/conversations/c1", "/conversations/c2"]

    seen.clear()
    unchanged = await _fetch(
        "conversation_parts", handle, first.next_cursor, parents=parents_reader(landed(5))
    )
    assert seen == []

    seen.clear()
    await _fetch(
        "conversation_parts", handle, unchanged.next_cursor, parents=parents_reader(landed(8))
    )
    assert seen == ["/conversations/c2"]
