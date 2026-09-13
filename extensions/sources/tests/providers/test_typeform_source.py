"""The Typeform connector over a mock transport: the `page_count` page-number loop (`forms`),
the per-form response fan-out through the `next_page_token` body cursor, and a refusal surfacing as
`StreamSkipped`. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.typeform import TypeformConnector

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

LANDED_FORMS: Landed = {"forms": (ParentRecord(ref="forms/f1", fields={"id": "f1"}),)}


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
    landed: Landed = LANDED_FORMS,
) -> SyncResult:
    auth = SourceAuth(
        workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=parents_reader(landed)
    )
    return await ConnectorBackend(connector=TypeformConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_forms_page_count_loop_and_watermark(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/forms"
        assert request.url.params.get("page_size") == "200"
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "f1", "title": "Survey", "last_updated_at": "2026-02-01T00:00:00Z"}
                ],
                "page_count": 1,
            },
        )

    result = await _fetch("forms", handle, parents_reader=parents_reader)
    assert _refs(result) == {"forms/f1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


def _response_page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200, json={"items": [{"token": "t1", "submitted_at": "2026-02-01T00:00:00Z"}]}
    )


async def test_responses_fan_out_per_form(parents_reader: ParentsReader) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _response_page(request)

    result = await _fetch("responses", handle, parents_reader=parents_reader)
    assert seen == ["/forms/f1/responses"]
    assert _refs(result) == {"responses/t1"}
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_a_response_keeps_the_address_its_flat_declaration_gave_it(
    parents_reader: ParentsReader,
) -> None:
    streams = {stream.name: stream for stream in TypeformConnector().streams()}
    assert streams["responses"].key_scope == "global"

    result = await _fetch("responses", _response_page, parents_reader=parents_reader)
    assert {page.source_identity for page in result.pages} == {"responses/t1"}
    assert _refs(result) == {"responses/t1"}


async def test_a_form_resumes_its_responses_past_its_own_watermark(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.params.get("since"))
        return _response_page(request)

    cursor = json.dumps({"forms/f1\n/forms/f1/responses": "2026-01-01T00:00:00Z"})
    await _fetch("responses", handle, cursor=cursor, parents_reader=parents_reader)
    assert seen == ["2026-01-01T00:00:00Z"]


def _webhook_page(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "items": [
                {
                    "id": "wh1",
                    "tag": "renamed-hook",
                    "form_id": "f1",
                    "url": "https://example.test/hook",
                }
            ]
        },
    )


async def test_webhooks_key_on_the_webhook_id_not_the_caller_chosen_tag(
    parents_reader: ParentsReader,
) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _webhook_page(request)

    result = await _fetch("webhooks", handle, parents_reader=parents_reader)
    assert seen == ["/forms/f1/webhooks"]
    assert _refs(result) == {"webhooks/f1/renamed-hook"}
    assert {page.source_identity for page in result.pages} == {"webhooks/f1/wh1"}
    assert "renamed-hook" in result.pages[0].body


async def test_the_webhook_address_moves_under_its_form_and_restamps_nothing() -> None:
    streams = {stream.name: stream for stream in TypeformConnector().streams()}
    assert streams["webhooks"].canonical is False
    assert "webhooks" not in syncing_streams(list(streams.values()))


async def test_a_form_landed_without_its_id_raises_at_the_fan_out(
    parents_reader: ParentsReader,
) -> None:
    landed = {"forms": (ParentRecord(ref="forms/f1", fields={}),)}
    with pytest.raises(RuntimeError, match="webhooks"):
        await _fetch("webhooks", _webhook_page, landed=landed, parents_reader=parents_reader)


async def test_no_landed_form_spends_no_webhook_request(parents_reader: ParentsReader) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return _webhook_page(request)

    result = await _fetch("webhooks", handle, landed={}, parents_reader=parents_reader)
    assert seen == []
    assert result.pages == ()


async def test_forms_project_the_id_their_webhooks_read(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": "f1", "title": "Survey", "last_updated_at": "2026-02-01T00:00:00Z"}
                ],
                "page_count": 1,
            },
        )

    result = await _fetch("forms", handle, parents_reader=parents_reader)
    assert result.pages[0].parent_fields == {"id": "f1"}


async def test_stream_skipped_on_refusal(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"code": "AUTHORIZATION_ERROR"})

    with pytest.raises(StreamSkipped):
        await _fetch("forms", handle, parents_reader=parents_reader)
