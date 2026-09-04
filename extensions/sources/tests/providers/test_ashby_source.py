"""The Ashby connector over a mock transport: the POST `/<resource>.list` body, the
`results`/`moreDataAvailable`/`nextCursor` cursor loop, the run cursor threaded as `syncToken` on
the first request, the `updatedAt` watermark advancing, and the HTTP-Basic auth adaptation (a direct
`bearer` key encoded into an `Authorization: Basic` header). Offline — a canned transport, no DB, no
token, no broker."""

import base64
import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.ashby import ASHBY_STREAMS, AshbyConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=AshbyConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


async def test_candidates_page_by_next_cursor_and_advance_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.ashbyhq.com"
        assert request.url.path == "/candidate.list"
        body = json.loads(request.content)
        if body.get("cursor") == "cur2":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "c2",
                            "createdAt": "2026-01-02T00:00:00Z",
                            "updatedAt": "2026-02-05T00:00:00Z",
                        }
                    ],
                    "moreDataAvailable": False,
                    "nextCursor": None,
                },
            )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "c1",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-02-01T00:00:00Z",
                    }
                ],
                "moreDataAvailable": True,
                "nextCursor": "cur2",
            },
        )

    result = await _fetch("candidates", handle)
    assert {page.source_ref for page in result.pages} == {"candidates/c1", "candidates/c2"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-05T00:00:00Z"
    page = next(page for page in result.pages if page.source_ref == "candidates/c1")
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"
    assert page.updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_metadata_streams_preserve_camel_case_timestamps() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/archiveReason.list"
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "reason-1",
                        "createdAt": "2026-01-01T00:00:00Z",
                        "updatedAt": "2026-02-01T00:00:00Z",
                    }
                ],
                "moreDataAvailable": False,
            },
        )

    result = await _fetch("archive_reasons", handle)
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"
    assert {stream.updated_at_field for stream in ASHBY_STREAMS} == {"updatedAt"}


async def test_the_run_cursor_is_threaded_as_synctoken_on_the_first_request() -> None:
    seen: list[dict[str, object]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(
            200, json={"results": [{"id": "c1"}], "moreDataAvailable": False, "nextCursor": None}
        )

    await _fetch("candidates", handle, cursor="tok-42")
    assert seen == [{"limit": 100, "syncToken": "tok-42"}]


async def test_a_direct_key_is_encoded_as_http_basic() -> None:
    client = AshbyConnector()._make_client("https://api.ashbyhq.com", Credential(bearer="secret"))
    try:
        expected = base64.b64encode(b"secret:").decode()
        assert client.headers["Authorization"] == f"Basic {expected}"
    finally:
        await client.aclose()


async def test_a_refused_stream_is_skipped_rather_than_failed() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"errors": ["forbidden"]})

    with pytest.raises(StreamSkipped):
        await _fetch("candidates", handle)
