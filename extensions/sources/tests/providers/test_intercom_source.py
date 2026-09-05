"""Intercom connector over a mock transport: the search-API POST body + `pages.next.starting_after`
cursor, the scroll API, the `Intercom-Version` header, numeric watermark ordering,
and the `StreamSkipped` a refusal raises. Offline — a canned
transport, no DB, no token, no broker."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.intercom import INTERCOM_VERSION, IntercomConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"

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

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
):
    return await ConnectorBackend(connector=IntercomConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
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


async def test_conversation_parts_flatten_surface_author_type_and_id() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/conversations/search":
            return httpx.Response(
                200, json={"conversations": [{"id": "c1", "updated_at": 1700000000}], "pages": {}}
            )
        if request.method == "GET" and request.url.path == "/conversations/c1":
            return httpx.Response(
                200,
                json={
                    "conversation_parts": {
                        "conversation_parts": [
                            {"id": "p1", "author": {"type": "admin", "id": "a1"}}
                        ]
                    }
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    record = _flat(await _fetch("conversation_parts", handle), "conversation_parts/p1")
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
