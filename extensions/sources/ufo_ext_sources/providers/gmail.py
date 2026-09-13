"""The Gmail connector — a mailbox's messages synced as recallable prose over the history delta.

A Gmail message's readable content lives inside a nested MIME `payload` tree — `text/plain` and
`text/html` parts whose characters are URL-safe base64 under `body.data`, with the subject and
sender in `payload.headers` — not in flat columns, so this is a connector that overrides `render`:
the default JSON dump of that tree is unrecallable. `render` decodes the MIME parts into a readable
`From`/`To`/`Subject` header block over the plain-text body (HTML stripped when only HTML is
present), so a synced email recalls as what a member would read. A `Subject` header is optional, so
a message carrying none is titled by its identity — the same title the default render falls back to
— because a page's title is never empty.

Sync is a `historyId` delta, carried opaquely as the run cursor. With no cursor the run backfills —
`GET /gmail/v1/users/me/messages` enumerates the message ids inside the stream's pinned backfill
window (`q=after:<epoch seconds>`, 30 days unless the binding named its own, the whole mailbox when
it asked for all history), and the cursor is seeded from the newest message's `historyId` — or, when
the window held no message at all, from the mailbox profile's, read before the enumeration so a
message delivered during it lands above the seed rather than below. An empty window therefore still
leaves backfill mode instead of re-enumerating itself every interval. With a cursor
`GET /gmail/v1/users/me/history` from that id names the messages added and deleted since (label
moves surface as add/delete pairs), the net-added ids are body-fetched, and the deleted ids land as
tombstones alongside the new `historyId`. A `404` on the history walk means the id aged out of
Gmail's window, so the connector raises `CursorExpired` and core refetches from scratch — the next
run backfills the same pinned window, never a fresh one; a grant that lacks the scope (`401`/`403`)
yields `StreamSkipped` so the run records a skip, not a failure, while a refusal naming a usage
limit instead of the grant raises (`ufo_ext_sources.providers.google`); a message that vanished
between the
history walk and its body fetch (`404`) is skipped. The credential is resolved through the auth
proxy the runner threads — this connector holds no token. The write path is intentionally absent —
the source seam only reads."""

import base64
from collections.abc import AsyncIterator
from datetime import datetime
from email.utils import getaddresses
from html.parser import HTMLParser
from typing import Any

import httpx

from ufo.sdk.sources import (
    MAIL_BACKFILL_WINDOW_DAYS,
    CursorExpired,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
)
from ufo_ext_sources.providers import google

GMAIL_API_BASE = "https://gmail.googleapis.com"
MESSAGES_PATH = "/gmail/v1/users/me/messages"
HISTORY_PATH = "/gmail/v1/users/me/history"
PROFILE_PATH = "/gmail/v1/users/me/profile"
LIST_PAGE_SIZE = 500
BODIES_CHUNK_SIZE = 200
_HEADER_INTEREST = frozenset({"from", "to", "cc", "subject"})
_BLOCK_TAGS = frozenset(
    {
        "p",
        "br",
        "div",
        "hr",
        "li",
        "tr",
        "td",
        "th",
        "blockquote",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
    }
)

GMAIL_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="messages",
        source_object="messages",
        primary_key="id",
        created_at_field="internal_date",
        updated_at_field=None,
        backfill_window_days=MAIL_BACKFILL_WINDOW_DAYS,
        canonical=True,
    ),
]


class GmailConnector(RestConnector):
    name = "gmail"
    base_url = GMAIL_API_BASE
    streams_list = GMAIL_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[StreamPage]:
        if stream.name != "messages":
            raise NotImplementedError(f"gmail: stream {stream.name!r} has no paginate dispatch")
        try:
            if run.cursor is None:
                added_ids, next_history = await self._backfill(client, after=run.backfill_after)
                deleted_ids: list[str] = []
            else:
                added_ids, deleted_ids, next_history = await self._history(client, run.cursor)
            chunks = [
                added_ids[start : start + BODIES_CHUNK_SIZE]
                for start in range(0, len(added_ids), BODIES_CHUNK_SIZE)
            ]
            if not chunks:
                yield StreamPage(records=[], deletes=tuple(deleted_ids), next_cursor=next_history)
                return
            last = len(chunks) - 1
            for index, chunk in enumerate(chunks):
                records = await self._fetch_bodies(client, chunk)
                yield StreamPage(
                    records=records,
                    deletes=tuple(deleted_ids) if index == last else (),
                    next_cursor=next_history if index == last else None,
                )
        except httpx.HTTPStatusError as error:
            if google.refused_for_scope(error):
                raise StreamSkipped(
                    f"gmail: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the Gmail read scope"
                ) from error
            raise

    async def _backfill(
        self, client: httpx.AsyncClient, *, after: datetime | None
    ) -> tuple[list[str], str | None]:
        """Enumerate the message ids the pinned window reaches (every id when the row pins none),
        then seed the cursor so the next run walks the delta forward from here. `after:` carries the
        cutoff as epoch seconds because its bare-date form is resolved in the mailbox's own local
        time, and a floor later than the pin drops the window's oldest edge for good — mail is not
        `delete_missing` and every replay sends the same cutoff."""
        floor = await self._profile_history_id(client)
        added: list[str] = []
        params: dict[str, Any] = {"maxResults": LIST_PAGE_SIZE}
        if after is not None:
            params["q"] = f"after:{int(after.timestamp())}"
        token: str | None = None
        while True:
            if token:
                params["pageToken"] = token
            data = await self._get(client, MESSAGES_PATH, params=params)
            for message in data.get("messages") or []:
                if isinstance(message, dict):
                    message_id = message.get("id")
                    if isinstance(message_id, str) and message_id:
                        added.append(message_id)
            token = data.get("nextPageToken")
            if not isinstance(token, str) or not token:
                return added, await self._seed_history_id(client, added, floor=floor)

    async def _profile_history_id(self, client: httpx.AsyncClient) -> str | None:
        profile = await self._get(client, PROFILE_PATH)
        history_id = profile.get("historyId")
        return history_id if isinstance(history_id, str) else None

    async def _seed_history_id(
        self, client: httpx.AsyncClient, added: list[str], *, floor: str | None
    ) -> str | None:
        """The `historyId` the next run walks forward from: the newest listed message's, falling
        back to `floor` when the window enumerated nothing or that message has gone. A run seeding
        no cursor stays in backfill mode, re-enumerating every interval and never reaching the delta
        path where a deletion becomes a tombstone.

        `floor` is the mailbox profile's `historyId` read BEFORE the enumeration. Read after, mail
        delivered during an empty window's walk would sit below the seed and the next
        `history.list` would start past it. Re-reporting a listed message is free; skipping one is
        not."""
        if added:
            try:
                newest = await self._get(
                    client, f"{MESSAGES_PATH}/{added[0]}", params={"format": "minimal"}
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 404:
                    raise
            else:
                newest_history_id = newest.get("historyId")
                if isinstance(newest_history_id, str):
                    return newest_history_id
        return floor

    async def _history(
        self, client: httpx.AsyncClient, history_id: str
    ) -> tuple[list[str], list[str], str | None]:
        """Walk `history.list` from `history_id`, collecting added and deleted message ids and the
        latest `historyId`. A `404` means the id aged out of Gmail's window — raise `CursorExpired`
        so core refetches from scratch."""
        params: dict[str, Any] = {
            "startHistoryId": history_id,
            "maxResults": LIST_PAGE_SIZE,
            "historyTypes": ["messageAdded", "messageDeleted"],
        }
        added: set[str] = set()
        deleted: set[str] = set()
        next_history = history_id
        token: str | None = None
        try:
            while True:
                if token:
                    params["pageToken"] = token
                data = await self._get(client, HISTORY_PATH, params=params)
                for record in data.get("history") or []:
                    if not isinstance(record, dict):
                        continue
                    added.update(_message_ids(record.get("messagesAdded")))
                    deleted.update(_message_ids(record.get("messagesDeleted")))
                latest = data.get("historyId")
                if isinstance(latest, str):
                    next_history = latest
                token = data.get("nextPageToken")
                if not isinstance(token, str) or not token:
                    break
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 404:
                raise CursorExpired(f"gmail historyId {history_id} expired") from error
            raise
        return list(added - deleted), list(deleted), next_history

    async def _fetch_bodies(
        self, client: httpx.AsyncClient, ids: list[str]
    ) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for message_id in ids:
            try:
                raw = await self._get(
                    client, f"{MESSAGES_PATH}/{message_id}", params={"format": "full"}
                )
            except httpx.HTTPStatusError as error:
                if error.response.status_code == 404:
                    continue
                raise
            records.append(_flatten_message(raw))
        return records

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        if stream.name != "messages":
            return super().render(record, stream)
        subject = _str(record.get("subject"))
        title = subject or super().render(record, stream)[0]
        header_lines: list[str] = []
        sender = _format_contact(record.get("from_handle"), record.get("from_display_name"))
        if sender:
            header_lines.append(f"From: {sender}")
        for label, key in (("To", "to"), ("Cc", "cc")):
            recipients = _format_recipients(record.get(key))
            if recipients:
                header_lines.append(f"{label}: {recipients}")
        if subject:
            header_lines.append(f"Subject: {subject}")
        parts = [
            f"# gmail messages: {title}",
            "\n".join(header_lines),
            _message_body(record),
        ]
        return title, "\n\n".join(part for part in parts if part).strip()


def _message_ids(entries: Any) -> list[str]:
    ids: list[str] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        message = entry.get("message")
        message_id = message.get("id") if isinstance(message, dict) else None
        if isinstance(message_id, str) and message_id:
            ids.append(message_id)
    return ids


def _flatten_message(raw: dict[str, Any]) -> dict[str, Any]:
    """Lift a `messages.get` payload into the flat record the sync writes: the interesting headers
    and the decoded plain-text and HTML bodies."""
    payload = raw.get("payload") or {}
    headers: dict[str, str] = {}
    for header in payload.get("headers") or []:
        if isinstance(header, dict):
            name = (header.get("name") or "").lower()
            if name in _HEADER_INTEREST:
                headers[name] = header.get("value") or ""
    body_text, body_html = _extract_bodies(payload)
    from_handle, from_name = _parse_first_address(headers.get("from"))
    label_ids = [label for label in raw.get("labelIds") or [] if isinstance(label, str)]
    return {
        "id": raw.get("id") or "",
        "thread_id": raw.get("threadId"),
        "internal_date": raw.get("internalDate"),
        "subject": headers.get("subject"),
        "snippet": raw.get("snippet"),
        "from_handle": from_handle,
        "from_display_name": from_name,
        "to": _addresses(headers.get("to")),
        "cc": _addresses(headers.get("cc")),
        "labels": label_ids,
        "body_text": body_text,
        "body_html": body_html,
        "direction": "outbound" if "SENT" in label_ids else "inbound",
    }


def _extract_bodies(payload: dict[str, Any]) -> tuple[str | None, str | None]:
    """Walk the MIME tree; return the first `text/plain` and first `text/html` decoded bodies."""
    found: dict[str, str] = {}

    def walk(part: dict[str, Any]) -> None:
        mime = part.get("mimeType") or ""
        data = (part.get("body") or {}).get("data")
        if isinstance(data, str) and mime in ("text/plain", "text/html") and mime not in found:
            found[mime] = _b64url_decode(data)
        for child in part.get("parts") or []:
            if isinstance(child, dict):
                walk(child)

    walk(payload)
    return found.get("text/plain"), found.get("text/html")


def _b64url_decode(data: str) -> str:
    """Gmail encodes part bodies as URL-safe base64 without padding."""
    padding = "=" * (-len(data) % 4)
    try:
        return base64.urlsafe_b64decode((data + padding).encode()).decode("utf-8", errors="replace")
    except ValueError:
        return ""


def _parse_first_address(header: str | None) -> tuple[str | None, str | None]:
    if not header:
        return None, None
    pairs = getaddresses([header])
    if not pairs:
        return None, None
    name, addr = pairs[0]
    return (addr.lower() if addr else None), (name or None)


def _addresses(header: str | None) -> list[dict[str, str | None]]:
    if not header:
        return []
    return [
        {"handle": addr.lower(), "display_name": name or None}
        for name, addr in getaddresses([header])
        if addr
    ]


def _format_contact(handle: Any, display_name: Any) -> str:
    if not isinstance(handle, str) or not handle:
        return ""
    return (
        f"{display_name} <{handle}>" if isinstance(display_name, str) and display_name else handle
    )


def _format_recipients(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return ", ".join(
        _format_contact(item.get("handle"), item.get("display_name"))
        for item in items
        if isinstance(item, dict)
    )


def _message_body(record: dict[str, Any]) -> str:
    text = record.get("body_text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    html = record.get("body_html")
    if isinstance(html, str) and html.strip():
        return _HtmlText.extract(html)
    snippet = record.get("snippet")
    return snippet.strip() if isinstance(snippet, str) else ""


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


class _HtmlText(HTMLParser):
    """Lift readable text from an email's HTML body: keep the character data, break lines on
    block-level boundaries, and drop every tag and attribute (entities unescaped by the parser)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    @classmethod
    def extract(cls, raw: str) -> str:
        parser = cls()
        parser.feed(raw)
        joined = "".join(parser._parts)
        lines = (" ".join(line.split()) for line in joined.split("\n"))
        return "\n".join(line for line in lines if line).strip()

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")
