"""Outlook connector over a mock transport: the derived `conversations` collapse over `/me/messages`
with its `updated_at` watermark, the Graph `/delta` path that lands `@removed` items as tombstones
and captures the `@odata.deltaLink` as the resume cursor, and the `StreamSkipped` a refused mailbox
raises. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.outlook import OutlookConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

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


def _flat(result, ref: str) -> dict:
    body = next(page.body for page in result.pages if page.source_ref == ref)
    return json.loads(body.split("\n\n", 1)[1])


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


def _contacts_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/contactFolders":
            return httpx.Response(200, json={"value": []})
        if path == "/v1.0/me/contacts/delta":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "ct1",
                            "givenName": "Ada",
                            "surname": "Lovelace",
                            "displayName": "Ada Lovelace",
                            "emailAddresses": [{"emailAddress": {"address": "ada@example.com"}}],
                            "mobilePhone": "+15551234",
                            "createdDateTime": "2026-01-01T00:00:00Z",
                        }
                    ],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_contacts_flatten_derives_name_email_and_phone() -> None:
    record = _flat(await _fetch("contacts", _contacts_handler()), "contacts/ct1")
    assert record["name"] == "Ada Lovelace"
    assert record["first_name"] == "Ada"
    assert record["email"] == "ada@example.com"
    assert record["phone"] == "+15551234"


def _messages_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/mailFolders":
            return httpx.Response(200, json={"value": [{"id": "f1", "displayName": "Inbox"}]})
        if path == "/v1.0/me/mailFolders/f1/messages/delta":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "m1",
                            "subject": "Roadmap",
                            "bodyPreview": "Q3 planning",
                            "from": {"emailAddress": {"address": "boss@example.com"}},
                            "sentDateTime": "2026-02-01T00:00:00Z",
                            "conversationId": "conv1",
                        }
                    ],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_messages_flatten_derives_subject_snippet_and_from_handle() -> None:
    record = _flat(await _fetch("messages", _messages_handler()), "messages/m1")
    assert record["subject"] == "Roadmap"
    assert record["snippet"] == "Q3 planning"
    assert record["from_handle"] == "boss@example.com"
    assert record["sent_at"] == "2026-02-01T00:00:00Z"
    assert record["thread_id"] == "conv1"


def _events_handler() -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/me/calendarView/delta":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "e1",
                            "subject": "Weekly Sync",
                            "body": {"contentType": "html", "content": "<p>Weekly <b>sync</b></p>"},
                            "start": {"dateTime": "2026-02-05T09:00:00"},
                            "end": {"dateTime": "2026-02-05T09:30:00"},
                            "location": {"displayName": "Room 1"},
                        }
                    ],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_events_flatten_derives_title_start_and_strips_html_description() -> None:
    record = _flat(await _fetch("events", _events_handler()), "events/e1")
    assert record["title"] == "Weekly Sync"
    assert record["start_at"] == "2026-02-05T09:00:00"
    assert record["location"] == "Room 1"
    assert "<" not in record["description"]
    assert "sync" in record["description"]


async def test_stream_skipped_when_mailbox_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})

    with pytest.raises(StreamSkipped):
        await _fetch("mail_folders", handle)
