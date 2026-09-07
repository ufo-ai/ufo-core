"""The Mailchimp connector over a mock transport: the top-level `?count&offset` walk hitting the
per-tenant data-center host (with the watermark advancing over `date_created`), the per-list fan-out
that stamps `list_id` on each member, and a refusal surfacing as `StreamSkipped`. The class base URL
is empty (per-tenant data center), so the host is bound through `SourceAuth.base_url`.
Offline — a canned transport, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.mailchimp import MailchimpConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

BASE_URL = "https://us21.api.mailchimp.com"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(stream: str, handler: Callable[[httpx.Request], httpx.Response]) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), base_url=BASE_URL)
    return await ConnectorBackend(connector=MailchimpConnector()).fetch(
        ConnectorSourceConfig(stream=stream), None, auth
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


async def test_list_members_fan_out_stamps_the_parent_list_id() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "us21.api.mailchimp.com"
        if request.url.path == "/3.0/lists":
            return httpx.Response(200, json={"lists": [{"id": "l1"}], "total_items": 1})
        assert request.url.path == "/3.0/lists/l1/members"
        return httpx.Response(
            200,
            json={
                "members": [
                    {
                        "id": "m1",
                        "timestamp_signup": "",
                        "timestamp_opt": "2026-01-01T00:00:00Z",
                        "last_changed": "2026-02-02T00:00:00Z",
                    }
                ]
            },
        )

    result = await _fetch("list_members", handle)
    assert _refs(result) == {"list_members/m1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
    assert '"list_id": "l1"' in result.pages[0].body


async def test_unsubscribes_key_on_the_campaign_and_subscriber_hash() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3.0/reports":
            return httpx.Response(
                200, json={"reports": [{"id": "camp1"}, {"id": "camp2"}], "total_items": 2}
            )
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

    result = await _fetch("unsubscribes", handle)
    assert _refs(result) == {"unsubscribes/md5-of-ada"}
    assert {page.source_identity for page in result.pages} == {
        "unsubscribes/camp1:md5-of-ada",
        "unsubscribes/camp2:md5-of-ada",
    }


async def test_an_unsubscribe_without_a_subscriber_hash_is_dropped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/3.0/reports":
            return httpx.Response(200, json={"reports": [{"id": "camp1"}], "total_items": 1})
        return httpx.Response(
            200,
            json={
                "unsubscribes": [
                    {"email_id": "md5-of-ada", "timestamp": "2026-01-01T00:00:00Z"},
                    {"timestamp": "2026-01-02T00:00:00Z"},
                ]
            },
        )

    result = await _fetch("unsubscribes", handle)
    assert _refs(result) == {"unsubscribes/md5-of-ada"}
    assert {page.source_identity for page in result.pages} == {"unsubscribes/camp1:md5-of-ada"}
    assert result.dropped == 1


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "unauthorized"})

    with pytest.raises(StreamSkipped):
        await _fetch("lists", handle)
