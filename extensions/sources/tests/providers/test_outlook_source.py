"""Outlook connector over a mock transport: the derived `conversations` collapse over `/me/messages`
with its `updated_at` watermark, the Graph `/delta` path that lands `@removed` items as tombstones
and captures the `@odata.deltaLink` as the resume cursor, the pinned backfill window flooring both
mail streams' first walk and no resume of one, and the `StreamSkipped` a refused mailbox raises.
Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.outlook import CONVERSATIONS, MESSAGES, OutlookConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import MAIL_BACKFILL_WINDOW_DAYS, ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped

ACCOUNT = "acct-1"
DELTA_LINK = "https://graph.microsoft.com/v1.0/me/mailFolders/delta?$deltatoken=abc"
MESSAGES_DELTA_LINK = (
    "https://graph.microsoft.com/v1.0/me/mailFolders/f1/messages/delta?$deltatoken=abc"
)
PINNED_CUTOFF = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    backfill_after: datetime | None = None,
):
    return await ConnectorBackend(connector=OutlookConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, backfill_after=backfill_after),
        cursor,
        _auth(handler),
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
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
    assert "Roadmap" in result.pages[0].body


async def test_conversations_preserve_creation_time_across_odata_pages() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1.0/me/messages":
            return httpx.Response(404, json={"path": request.url.path})
        if request.url.params.get("page") == "2":
            return httpx.Response(
                200,
                json={
                    "value": [
                        {
                            "id": "m2",
                            "conversationId": "c1",
                            "subject": "Latest",
                            "sentDateTime": "2026-02-03T00:00:00Z",
                            "lastModifiedDateTime": "2026-02-04T00:00:00Z",
                        }
                    ]
                },
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "m1",
                        "conversationId": "c1",
                        "subject": "First",
                        "sentDateTime": "2026-02-01T00:00:00Z",
                        "lastModifiedDateTime": "2026-02-02T00:00:00Z",
                    }
                ],
                "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?page=2",
            },
        )

    result = await _fetch("conversations", handle)
    assert len(result.pages) == 1
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-04T00:00:00.000000+00:00"
    assert "Latest" in result.pages[0].body


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
    result = await _fetch("contacts", _contacts_handler())
    record = _flat(result, "contacts/ct1")
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
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
                            "createdDateTime": "2026-01-31T00:00:00Z",
                            "sentDateTime": "2026-02-01T00:00:00Z",
                            "lastModifiedDateTime": "2026-02-02T00:00:00Z",
                            "conversationId": "conv1",
                        }
                    ],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": path})

    return handle


async def test_messages_flatten_derives_subject_snippet_and_from_handle() -> None:
    result = await _fetch("messages", _messages_handler())
    record = _flat(result, "messages/m1")
    assert result.pages[0].created_at == "2026-01-31T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
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
                            "createdDateTime": "2026-02-01T00:00:00Z",
                            "lastModifiedDateTime": "2026-02-02T00:00:00Z",
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
    result = await _fetch("events", _events_handler())
    record = _flat(result, "events/e1")
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"
    assert record["title"] == "Weekly Sync"
    assert record["start_at"] == "2026-02-05T09:00:00"
    assert record["location"] == "Room 1"
    assert "<" not in record["description"]
    assert "sync" in record["description"]


async def test_the_pinned_window_floors_the_message_delta_but_not_its_resume() -> None:
    """Graph's message delta accepts one comparison, `receivedDateTime ge`, and only on the request
    that opens a folder's walk — a delta link carries its own floor, so a resume sends none."""
    filters: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1.0/me/mailFolders":
            return httpx.Response(200, json={"value": [{"id": "f1", "displayName": "Inbox"}]})
        if path == "/v1.0/me/mailFolders/f1/messages/delta":
            filters.append(request.url.params.get("$filter"))
            return httpx.Response(
                200,
                json={
                    "value": [{"id": "m1", "subject": "Roadmap"}],
                    "@odata.deltaLink": DELTA_LINK,
                },
            )
        return httpx.Response(404, json={"path": path})

    first = await _fetch("messages", handle, backfill_after=PINNED_CUTOFF)
    await _fetch(
        "messages",
        handle,
        cursor=json.dumps({"f1": MESSAGES_DELTA_LINK}),
        backfill_after=PINNED_CUTOFF,
    )
    await _fetch("messages", handle)

    assert filters == ["receivedDateTime ge 2026-01-15T09:30:00Z", None, None]
    assert {page.source_ref for page in first.pages} == {"messages/m1"}
    assert MESSAGES.backfill_window_days == MAIL_BACKFILL_WINDOW_DAYS


async def test_the_pinned_window_floors_the_conversations_walk() -> None:
    filters: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/v1.0/me/messages":
            return httpx.Response(404, json={"path": request.url.path})
        filters.append(request.url.params.get("$filter"))
        return httpx.Response(200, json={"value": []})

    await _fetch("conversations", handle, backfill_after=PINNED_CUTOFF)
    await _fetch(
        "conversations", handle, cursor="2026-03-01T00:00:00Z", backfill_after=PINNED_CUTOFF
    )
    await _fetch("conversations", handle)

    assert filters == [
        "lastModifiedDateTime ge 2026-01-15T09:30:00Z",
        "lastModifiedDateTime gt 2026-03-01T00:00:00Z",
        None,
    ]
    assert CONVERSATIONS.backfill_window_days == MAIL_BACKFILL_WINDOW_DAYS


async def test_stream_skipped_when_mailbox_refused() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": "ErrorAccessDenied"}})

    with pytest.raises(StreamSkipped):
        await _fetch("mail_folders", handle)
