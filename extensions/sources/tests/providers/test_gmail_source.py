"""Gmail connector over a mock transport: the `historyId` delta — a first run that backfills the
messages its pinned window reaches and seeds the cursor from the newest message's `historyId` (from
the mailbox profile's when the window held none), a delta run that walks `history.list` from the
stored id to surface added and deleted messages while advancing the cursor whether or not the row
carries a window, `CursorExpired` when the id aged out (404), and — the point of this provider —
the `render` override that decodes the MIME `payload.parts` into a readable From/To/Subject header
block over the plain-text body, never the raw base64/multipart markers. Offline — a canned
transport, no DB, no token, no broker."""

import base64
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.gmail import GmailConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import CursorExpired, SourceAuth, StreamSkipped
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

ACCOUNT = "acct-1"
PINNED_CUTOFF = datetime(2026, 1, 15, 9, 30, tzinfo=UTC)


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


def _auth(handler: Callable[[httpx.Request], httpx.Response]) -> SourceAuth:
    return SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))


async def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    backfill_after: datetime | None = None,
):
    return await ConnectorBackend(connector=GmailConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="messages", backfill_after=backfill_after),
        cursor,
        _auth(handler),
    )


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode()


def _message(message_id: str, subject: str, body: str) -> dict:
    return {
        "id": message_id,
        "threadId": f"t-{message_id}",
        "historyId": "9001",
        "internalDate": "1769904000000",
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


def _without_subject(message: dict) -> dict:
    payload = message["payload"]
    headers = [header for header in payload["headers"] if header["name"] != "Subject"]
    return {**message, "payload": {**payload, "headers": headers}}


MESSAGES = {
    "m1": _message("m1", "Launch plan", "Ship it by Friday."),
    "m2": _message("m2", "Standup", "Notes from standup."),
    "m3": _message("m3", "Re: Launch plan", "Pushed to Monday."),
    "m4": _without_subject(_message("m4", "", "Scanned document attached.")),
}


def _get(message_id: str, request: httpx.Request) -> httpx.Response:
    if request.url.params.get("format") == "minimal":
        return httpx.Response(200, json={"id": message_id, "historyId": "9001"})
    return httpx.Response(200, json=MESSAGES[message_id])


def _profile(history_id: str = "8500") -> httpx.Response:
    """Every backfill reads the mailbox profile before it enumerates, so its `historyId` can floor
    the seeded cursor when the window turns out to hold nothing."""
    return httpx.Response(200, json={"emailAddress": "ada@example.com", "historyId": history_id})


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
        if path == "/gmail/v1/users/me/profile":
            return _profile()
        return httpx.Response(404, json={"path": path})

    result = await _fetch(handle)

    assert {page.source_ref for page in result.pages} == {"messages/m1", "messages/m2"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "9001"
    assert {page.created_at for page in result.pages} == {"2026-02-01T00:00:00.000000+00:00"}

    body = next(page.body for page in result.pages if page.source_ref == "messages/m1")
    assert "From: Ada Lovelace <ada@example.com>" in body
    assert "To: team@example.com" in body
    assert "Subject: Launch plan" in body
    assert "Ship it by Friday." in body
    # the raw MIME never leaks into the recallable body
    assert "multipart" not in body
    assert "Content-Type" not in body
    assert _b64("Ship it by Friday.") not in body


async def test_a_message_carrying_no_subject_header_is_titled_by_its_identity() -> None:
    """A `Subject` header is optional, so a mailbox holds messages without one — a scanner, a
    device notification. The page model rejects an empty title, so titling such a message by its
    subject verbatim failed the run that fetched it, and a delta stream that fails advances no
    cursor: the same message came back every interval and nothing synced again. It titles by its
    identity instead, and the messages it shares the page with land with it."""

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/messages":
            return httpx.Response(
                200, json={"messages": [{"id": "m4"}, {"id": "m1"}], "nextPageToken": None}
            )
        if path.startswith("/gmail/v1/users/me/messages/"):
            return _get(path.rsplit("/", 1)[-1], request)
        if path == "/gmail/v1/users/me/profile":
            return _profile()
        return httpx.Response(404, json={"path": path})

    result = await _fetch(handle)

    assert {page.source_ref: page.title for page in result.pages} == {
        "messages/m4": "messages/m4",
        "messages/m1": "Launch plan",
    }
    untitled = next(page for page in result.pages if page.source_ref == "messages/m4")
    assert "Scanned document attached." in untitled.body
    assert "From: Ada Lovelace <ada@example.com>" in untitled.body
    assert "Subject:" not in untitled.body


async def test_the_pinned_window_bounds_the_backfill_and_survives_a_cursor_reset() -> None:
    """The row's pinned cutoff becomes `q=after:<epoch seconds>` on the id enumeration — the exact
    instant, because Gmail resolves the operator's bare-date form in the mailbox's own local time
    and a floor later than the pin would drop the oldest edge of the window permanently. A run
    driven from a cleared cursor — what `CursorExpired` leaves behind — sends the same floor rather
    than a fresh one, so nothing that landed inside the window is ever orphaned outside it."""
    queries: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/messages":
            queries.append(request.url.params.get("q"))
            return httpx.Response(200, json={"messages": [{"id": "m1"}], "nextPageToken": None})
        if path.startswith("/gmail/v1/users/me/messages/"):
            return _get(path.rsplit("/", 1)[-1], request)
        if path == "/gmail/v1/users/me/profile":
            return _profile()
        return httpx.Response(404, json={"path": path})

    first = await _fetch(handle, backfill_after=PINNED_CUTOFF)
    second = await _fetch(handle, cursor=None, backfill_after=PINNED_CUTOFF)

    assert queries == ["after:1768469400", "after:1768469400"]
    assert int(PINNED_CUTOFF.timestamp()) == 1768469400
    assert {page.source_ref for page in first.pages} == {"messages/m1"}
    assert {page.source_ref for page in second.pages} == {"messages/m1"}


async def test_a_windowed_row_still_walks_the_history_delta_it_resumes_from() -> None:
    """The window floors a first sync only. A row that already holds a `historyId` walks
    `history.list` from it and enumerates nothing — the steady-state path the pinned cutoff has to
    leave alone, since the cursor is already past the window's floor."""
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/gmail/v1/users/me/history":
            seen.append(request.url.params.get("startHistoryId", ""))
            return httpx.Response(
                200,
                json={
                    "history": [{"messagesAdded": [{"message": {"id": "m3"}}]}],
                    "historyId": "9100",
                },
            )
        if path.startswith("/gmail/v1/users/me/messages/"):
            return _get(path.rsplit("/", 1)[-1], request)
        return httpx.Response(404, json={"path": path})

    result = await _fetch(handle, cursor="9001", backfill_after=PINNED_CUTOFF)

    assert seen == ["9001"]
    assert {page.source_ref for page in result.pages} == {"messages/m3"}
    assert result.next_cursor == "9100"


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


@pytest.mark.parametrize(
    "error",
    [
        {
            "code": 403,
            "message": "Rate Limit Exceeded",
            "errors": [{"reason": "userRateLimitExceeded"}],
        },
        {"code": 403, "message": "Quota exceeded", "status": "RESOURCE_EXHAUSTED"},
    ],
    ids=["usage-limits-reason", "resource-exhausted-status"],
)
async def test_a_quota_refusal_is_not_a_scope_skip(error: dict[str, object]) -> None:
    """A `403` naming a usage limit is not a refusal the grant can answer: it clears as the quota
    window rolls, so it fails the run and takes the error backoff. Skipped instead, it would spend
    the driver's park threshold and take a stream that was about to come back out of reach until
    someone reconnected an account that was never the problem."""

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": error})

    with pytest.raises(httpx.HTTPStatusError):
        await _fetch(refuse)
