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
from ufo_ext_sources.providers.gmail import GmailConnector

from ufo.access.connectors import Credential
from ufo.sdk.sources import MAIL_BACKFILL_WINDOW_DAYS, ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import CursorExpired, SourceAuth, StreamSkipped

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


async def test_a_row_pinning_no_window_lists_the_whole_mailbox() -> None:
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

    await _fetch(handle)

    assert queries == [None]
    assert GmailConnector().streams()[0].backfill_window_days == MAIL_BACKFILL_WINDOW_DAYS


async def test_a_window_holding_no_message_still_seeds_the_history_cursor() -> None:
    """A bounded window is a state a member can ask for and get nothing back from — a quiet mailbox,
    a narrow window — and a run that seeded no cursor would stay in backfill mode: it would
    re-enumerate the window every interval and never reach the delta path, the only place a
    provider-side deletion becomes a tombstone. The mailbox profile carries a `historyId` of its
    own, so an empty window still hands the next run its starting point.

    That profile read has to happen BEFORE the enumeration, and the request order is the assertion
    that holds it there. Read after, it names a point the walk never saw: a message delivered
    between the empty listing and the profile read gets a `historyId` below the seeded cursor, and
    the next `history.list` starts past it and never reports it — one message lost silently, on the
    quiet mailbox this path exists for. Read before, the same message sits above the seed and the
    delta names it. The mailbox here answers a later `historyId` than the run seeds, which is what
    a read-after would have picked up."""
    paths: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        if request.url.path == "/gmail/v1/users/me/messages":
            return httpx.Response(200, json={})
        if request.url.path == "/gmail/v1/users/me/profile":
            # a message lands the instant the empty listing comes back, moving the mailbox on
            return _profile("8500" if len(paths) == 1 else "8600")
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(handle, backfill_after=PINNED_CUTOFF)

    assert result.pages == ()
    assert paths == ["/gmail/v1/users/me/profile", "/gmail/v1/users/me/messages"]
    assert result.next_cursor == "8500"


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
