"""Outlook connector over a mock transport: the derived `conversations` collapse over `/me/messages`
with its `updated_at` watermark, the Graph `/delta` path that lands `@removed` items as tombstones
and captures the `@odata.deltaLink` as the resume cursor, and the `StreamSkipped` a refused mailbox
raises. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from selfhost_ext_sources.outlook import OutlookConnector

from selfhost.connectors import Credential
from selfhost.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from selfhost.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"
DELTA_LINK = "https://graph.microsoft.com/v1.0/me/mailFolders/delta?$deltatoken=abc"


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
    return await ConnectorBackend(connector=OutlookConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, _auth(handler)
    )


def _conversations_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.microsoft.com"
        if request.url.path == "/v1.0/me/messages":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "m1",
                            "conversationId": "c1",
                            "subject": "Roadmap",
                            "sentDateTime": "2026-02-01T00:00:00Z",
                            "lastModifiedDateTime": "2026-02-02T00:00:00Z",
                            "bodyPreview": "Q3 planning",
                        }
                    ]
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_conversations_collapse_and_watermark() -> None:
    result = await _fetch("conversations", _conversations_handler())
    assert {page.source_ref for page in result.pages} == {"conversations/c1"}
    assert result.snapshot is False
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert "Roadmap" in result.pages[0].body


def _mail_folders_delta_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/me/mailFolders/delta":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {"id": "f1", "displayName": "Inbox"},
                        {"id": "f2", "@removed": {"reason": "deleted"}},
                    ],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_mail_folders_delta_tombstones_and_captures_delta_link() -> None:
    result = await _fetch("mail_folders", _mail_folders_delta_handler())
    assert {page.source_ref for page in result.pages} == {"mail_folders/f1"}
    assert result.deletes == ("mail_folders/f2",)
    assert result.snapshot is False
    assert result.next_cursor == DELTA_LINK


async def test_stream_skipped_when_mailbox_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})

    with pytest.raises(StreamSkipped):
        await _fetch("mail_folders", handle)
