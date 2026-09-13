"""The Outlook connector — a mailbox's messages, conversations, contacts, calendar events, and mail
folders synced over Microsoft Graph's delta feed.

Outlook speaks Microsoft Graph (OData). Messages, contacts, events, and mail folders sync through
Graph's `/delta` endpoints, carried opaquely as the run cursor: the first run walks the
`@odata.nextLink` pages and captures the terminal `@odata.deltaLink` as the next cursor; a later run
resumes from that delta link and Graph reports only what changed since — items marked `@removed`
land as tombstones alongside the fresh delta link. Messages and contacts fan out per folder, so the
cursor is a folder→delta-link map encoded as JSON. Conversations are derived: `/me/messages` is
walked and collapsed to one record per `conversationId`, incremental over `lastModifiedDateTime`.
Both mail streams floor their first walk at the stream's pinned backfill window — a `$filter` on the
initial request only, since a delta or watermark resume already carries its own floor. The messages
delta takes `receivedDateTime ge`, the one comparison Graph accepts there; the derived conversations
walk filters the property it already orders and resumes by, `lastModifiedDateTime`.
`render` uses the default titled-JSON — the list/delta reads carry only a `bodyPreview` snippet, not
a full body. A `401`/`403` on the first Graph call yields `StreamSkipped` so the run records a skip,
not a failure. The credential is resolved through the auth proxy the runner threads — this connector
holds no token. The write path is intentionally absent — the source seam only reads."""

import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

import httpx

from ufo.sdk.sources import (
    MAIL_BACKFILL_WINDOW_DAYS,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    get_path,
)
from ufo_ext_sources.watermark import text_checkpoint

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_EVENT_DELTA_LOOKBACK = timedelta(days=365)
_EVENT_DELTA_LOOKAHEAD = timedelta(days=730)
_REFUSAL_STATUS = frozenset({401, 403})


def _graph_instant(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _strip_html(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    return _HTML_TAG_RE.sub(" ", value).strip()


def _first_email(record: dict[str, Any]) -> str | None:
    addresses = record.get("emailAddresses")
    if not isinstance(addresses, list):
        return None
    for address in addresses:
        value = get_path(address, "emailAddress.address") if isinstance(address, dict) else None
        if isinstance(value, str) and value:
            return value
    return None


def _phone(record: dict[str, Any]) -> str | None:
    mobile = record.get("mobilePhone")
    if isinstance(mobile, str) and mobile:
        return mobile
    phones = record.get("businessPhones")
    if not isinstance(phones, list):
        return None
    for phone in phones:
        if isinstance(phone, str) and phone:
            return phone
    return None


CONTACTS = StreamSpec(
    name="contacts",
    source_object="contacts",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    canonical=True,
)
MESSAGES = StreamSpec(
    name="messages",
    source_object="messages",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    backfill_window_days=MAIL_BACKFILL_WINDOW_DAYS,
    canonical=True,
)
CONVERSATIONS = StreamSpec(
    name="conversations",
    source_object="messages",
    primary_key="id",
    cursor_field="updated_at",
    created_at_field="created_at",
    updated_at_field="updated_at",
    backfill_window_days=MAIL_BACKFILL_WINDOW_DAYS,
    canonical=True,
)
EVENTS = StreamSpec(
    name="events",
    source_object="events",
    primary_key="id",
    cursor_field="lastModifiedDateTime",
    created_at_field="createdDateTime",
    updated_at_field="lastModifiedDateTime",
    canonical=True,
)
MAIL_FOLDERS = StreamSpec(
    name="mail_folders",
    source_object="mailFolders",
    primary_key="id",
)

ALL_STREAMS = [CONTACTS, MESSAGES, CONVERSATIONS, EVENTS, MAIL_FOLDERS]


class OutlookConnector(RestConnector):
    name = "outlook"
    base_url = "https://graph.microsoft.com/v1.0"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "conversations":
                async for conversation_page in self._conversation_pages(
                    client, cursor=run.cursor, after=run.backfill_after
                ):
                    yield conversation_page
                return
            if stream.name == "messages":
                async for message_page in self._message_delta_pages(
                    client, cursor=run.cursor, after=run.backfill_after
                ):
                    yield message_page
                return
            if stream.name == "contacts":
                async for contact_page in self._contact_delta_pages(client, cursor=run.cursor):
                    yield contact_page
                return
            if stream.name == "events":
                async for event_page in self._event_delta_pages(client, cursor=run.cursor):
                    yield event_page
                return
            if stream.name == "mail_folders":
                async for folder_page in self._graph_delta_pages(
                    client, initial_path="/me/mailFolders/delta", cursor=run.cursor
                ):
                    yield folder_page
                return
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"outlook: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks the Microsoft Graph mail/calendar scope"
                ) from error
            raise
        raise StreamSkipped(f"outlook stream {stream.name!r} is not implemented")

    async def _conversation_pages(
        self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params: dict[str, Any] = {"$top": 100, "$orderby": "lastModifiedDateTime asc"}
        if cursor:
            params["$filter"] = f"lastModifiedDateTime gt {cursor}"
        elif after is not None:
            params["$filter"] = f"lastModifiedDateTime ge {_graph_instant(after)}"
        conversations: dict[str, dict[str, Any]] = {}
        async for messages in self._get_odata_pages(client, "/me/messages", params=params):
            for message in messages:
                conversation_id = message.get("conversationId")
                if not isinstance(conversation_id, str) or not conversation_id:
                    continue
                sent_at = message.get("sentDateTime") or message.get("createdDateTime")
                updated_at = message.get("lastModifiedDateTime") or sent_at
                existing = conversations.get(conversation_id)
                created_at = sent_at
                if existing is not None:
                    existing_created_at = existing.get("created_at")
                    if existing_created_at and (
                        not created_at or str(existing_created_at) < str(created_at)
                    ):
                        created_at = existing_created_at
                if existing is not None and str(existing.get("updated_at") or "") >= str(
                    updated_at or ""
                ):
                    existing["created_at"] = created_at
                    continue
                conversations[conversation_id] = {
                    "id": conversation_id,
                    "title": message.get("subject") or conversation_id,
                    "conversation_type": "email_thread",
                    "subject": message.get("subject"),
                    "snippet": message.get("bodyPreview"),
                    "last_message_at": sent_at,
                    "created_at": created_at,
                    "updated_at": updated_at,
                }
        if conversations:
            yield list(conversations.values())

    async def _graph_delta_pages(
        self,
        client: httpx.AsyncClient,
        *,
        initial_path: str,
        cursor: str | None,
        params: dict[str, Any] | None = None,
    ) -> AsyncIterator[StreamPage]:
        path = cursor or initial_path
        query = None if cursor else params
        while path:
            response = await self._get_raw(client, path, params=query)
            data = response.json() if response.content else {}
            records: list[dict[str, Any]] = []
            deletes: list[str] = []
            for item in data.get("value") or []:
                if not isinstance(item, dict):
                    continue
                item_id = item.get("id")
                if not isinstance(item_id, str) or not item_id:
                    continue
                if isinstance(item.get("@removed"), dict):
                    deletes.append(item_id)
                else:
                    records.append(item)
            next_link = data.get("@odata.nextLink")
            delta_link = data.get("@odata.deltaLink")
            next_cursor = next_link or delta_link
            yield StreamPage(
                records=records,
                deletes=tuple(deletes),
                next_cursor=next_cursor if isinstance(next_cursor, str) else None,
            )
            if isinstance(next_link, str) and next_link:
                path = next_link
                query = None
                continue
            return

    async def _message_delta_pages(
        self, client: httpx.AsyncClient, *, cursor: str | None, after: datetime | None
    ) -> AsyncIterator[StreamPage]:
        folder_cursors = _decode_cursor_map(cursor)
        folders = await self._list_mail_folders(client)
        next_cursors = dict(folder_cursors)
        window = (
            None if after is None else {"$filter": f"receivedDateTime ge {_graph_instant(after)}"}
        )
        for folder_id in folders:
            folder_cursor = folder_cursors.get(folder_id)
            path = folder_cursor or f"/me/mailFolders/{quote(folder_id, safe='')}/messages/delta"
            async for page in self._graph_delta_pages(
                client, initial_path=path, cursor=folder_cursor, params=window
            ):
                for record in page.records:
                    record.setdefault("mail_folder_id", folder_id)
                if page.next_cursor:
                    next_cursors[folder_id] = page.next_cursor
                yield StreamPage(
                    records=page.records,
                    deletes=page.deletes,
                    next_cursor=_encode_cursor_map(next_cursors),
                )

    async def _contact_delta_pages(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        folder_cursors = _decode_cursor_map(cursor)
        folders = ["__default__", *await self._list_contact_folders(client)]
        next_cursors = dict(folder_cursors)
        for folder_id in folders:
            folder_cursor = folder_cursors.get(folder_id)
            initial = (
                "/me/contacts/delta"
                if folder_id == "__default__"
                else f"/me/contactFolders/{quote(folder_id, safe='')}/contacts/delta"
            )
            try:
                async for page in self._graph_delta_pages(
                    client, initial_path=initial, cursor=folder_cursor
                ):
                    if page.next_cursor:
                        next_cursors[folder_id] = page.next_cursor
                    yield StreamPage(
                        records=page.records,
                        deletes=page.deletes,
                        next_cursor=_encode_cursor_map(next_cursors),
                    )
            except httpx.HTTPStatusError as error:
                if folder_id == "__default__" and error.response.status_code in {400, 404}:
                    continue
                raise

    async def _event_delta_pages(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[StreamPage]:
        now = datetime.now(UTC)
        params = {
            "startDateTime": (now - _EVENT_DELTA_LOOKBACK).isoformat(),
            "endDateTime": (now + _EVENT_DELTA_LOOKAHEAD).isoformat(),
        }
        async for page in self._graph_delta_pages(
            client, initial_path="/me/calendarView/delta", cursor=cursor, params=params
        ):
            yield page

    async def _list_mail_folders(self, client: httpx.AsyncClient) -> list[str]:
        folder_ids: list[str] = []
        async for page in self._get_odata_pages(client, "/me/mailFolders", params={"$top": 100}):
            for folder in page:
                folder_id = folder.get("id")
                if isinstance(folder_id, str) and folder_id:
                    folder_ids.append(folder_id)
        return folder_ids

    async def _list_contact_folders(self, client: httpx.AsyncClient) -> list[str]:
        folder_ids: list[str] = []
        async for page in self._get_odata_pages(client, "/me/contactFolders", params={"$top": 100}):
            for folder in page:
                folder_id = folder.get("id")
                if isinstance(folder_id, str) and folder_id:
                    folder_ids.append(folder_id)
        return folder_ids

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "contacts":
            return {
                **record,
                "first_name": record.get("givenName"),
                "last_name": record.get("surname"),
                "name": record.get("displayName"),
                "email": _first_email(record),
                "phone": _phone(record),
                "created_at": record.get("createdDateTime"),
            }
        if stream.name == "messages":
            return {
                **record,
                "subject": record.get("subject"),
                "snippet": record.get("bodyPreview"),
                "from_handle": get_path(record, "from.emailAddress.address"),
                "sent_at": record.get("sentDateTime") or record.get("createdDateTime"),
                "conversation_id": record.get("conversationId"),
                "thread_id": record.get("conversationId"),
            }
        if stream.name == "events":
            return {
                **record,
                "title": record.get("subject"),
                "description": _strip_html(get_path(record, "body.content")),
                "start_at": get_path(record, "start.dateTime"),
                "end_at": get_path(record, "end.dateTime"),
                "location": get_path(record, "location.displayName"),
            }
        return record


def _decode_cursor_map(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    if not isinstance(value, dict):
        return {}
    return {str(k): str(v) for k, v in value.items() if isinstance(v, str) and v}


def _encode_cursor_map(value: dict[str, str]) -> str | None:
    return json.dumps(value, sort_keys=True) if value else None
