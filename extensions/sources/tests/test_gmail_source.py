"""Gmail connector over a mock transport: the `historyId` delta — a first run that backfills every
message and seeds the cursor from the newest message's `historyId`, a delta run that walks
`history.list` from the stored id to surface added and deleted messages while advancing the cursor,
`CursorExpired` when the id aged out (404), and — the point of this provider — the `render` override
that decodes the MIME `payload.parts` into a readable From/To/Subject header block over the
plain-text body, never the raw base64/multipart markers. Offline — a canned transport, no DB, no
token, no broker."""

import base64
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
from ufo_ext_sources.gmail import GmailConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import CursorExpired, SourceAuth, StreamSkipped

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(handler: Callable[[httpx.Request], httpx.Response], cursor: str | None = None):
    return await ConnectorBackend(connector=GmailConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="messages"), cursor, _auth(handler)
    )


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(message_id: str, subject: str, body: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "historyId": "9001",
        "labelIds": ["INBOX"],
        "snippet": "a short preview",
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": "Ada Lovelace <ada@example.com>"},
                {"name": "To", "value": "team@example.com"},
                {"name": "Subject", "value": subject},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {"mimeType": "text/html", "body": {"data": _b64(f"<p>{body}</p>")}},
            ],
        },
    }


MESSAGES = {
    "m1": _message("m1", "Launch plan", "Ship it by Friday."),
    "m2": _message("m2", "Standup", "Notes from standup."),
    "m3": _message("m3", "Re: Launch plan", "Pushed to Monday."),
}


def _get(message_id: str, request: httpx.Request) -> httpx.Response:
    if request.url.params.get("format") == "minimal":
        return httpx.Response(200, json={"id": message_id, "historyId": "9001"})
    return httpx.Response(200, json=MESSAGES[message_id])


async def test_backfill_lists_messages_seeds_the_history_cursor_and_renders() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "gmail.googleapis.com"
        path = request.url.path
        if path == "/gmail/v1/users/me/messages":
            return httpx.Response(
                200, json={"messages": [{"id": "m1"}, {"id": "m2"}], "nextPageToken": None}
            )
        if path.startswith("/gmail/v1/users/me/messages/"):
            return _get(path.rsplit("/", 1)[-1], request)
        return httpx.Response(404, json={"path": path})

    result = await _fetch(handle)

    assert {page.source_ref for page in result.pages} == {"messages/m1", "messages/m2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "9001"

    body = next(page.body for page in result.pages if page.source_ref == "messages/m1")
    assert "From: Ada Lovelace <ada@example.com>" in body
    assert "To: team@example.com" in body
    assert "Subject: Launch plan" in body
    assert "Ship it by Friday." in body
    # the raw MIME never leaks into the recallable body
    assert "multipart" not in body
    assert "Content-Type" not in body
    assert _b64("Ship it by Friday.") not in body


async def test_delta_run_threads_history_id_surfaces_deletes_and_advances_cursor() -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/history":
            seen.append(request.url.params.get("startHistoryId", ""))
            return httpx.Response(
                200,
                json={
                    "history": [
                        {"messagesAdded": [{"message": {"id": "m3"}}]},
                        {"messagesDeleted": [{"message": {"id": "m1"}}]},
                    ],
                    "historyId": "9100",
                },
            )
        if path.startswith("/gmail/v1/users/me/messages/"):
            return _get(path.rsplit("/", 1)[-1], request)
        return httpx.Response(404, json={"path": path})

    result = await _fetch(handle, cursor="9001")

    assert seen == ["9001"]
    assert {page.source_ref for page in result.pages} == {"messages/m3"}
    assert result.deletes == ("messages/m1",)
    assert result.next_cursor == "9100"


async def test_expired_history_id_raises_cursor_expired() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/gmail/v1/users/me/history":
            return httpx.Response(
                404, json={"error": {"code": 404, "message": "historyId invalid"}}
            )
        return httpx.Response(404, json={"path": request.url.path})

    try:
        await _fetch(handle, cursor="1")
    except CursorExpired:
        return
    raise AssertionError("a 404 on history.list must raise CursorExpired")


async def test_scope_refusal_yields_stream_skipped() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    try:
        await _fetch(refuse)
    except StreamSkipped:
        return
    raise AssertionError("a 403 from Gmail must raise StreamSkipped")
