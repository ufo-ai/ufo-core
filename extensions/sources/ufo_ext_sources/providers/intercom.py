"""The Intercom connector — conversations, contacts, companies, and their siblings synced into
recallable pages.

Intercom exposes three pagination shapes, dispatched on stream name in `paginate`: the search API
(POST `/conversations|contacts|tickets/search`, whose body carries a `pagination.starting_after`
cursor and a `query` filtering `updated_at > cursor`), the scroll API (GET `/companies/scroll`,
resumed by the `scroll_param` a page returns), and the plain list API (GET `/admins|tags|teams|
segments`, a single response). `conversation_parts` and `company_segments` are substreams that fan
out from a parent conversation/company. Auth layers an `Intercom-Version` header on whichever client
the base built from the resolved `Credential`.

Intercom's incremental cursor is `updated_at`, a unix-second integer; ufo advances a watermark
only over a string cursor, so `flatten` renders that integer as its decimal string (the value the
search filter parses back with `int(...)`), leaving the record otherwise untouched. A refusal
(HTTP 401/403) raises `StreamSkipped` so the run records a skip. The write path is intentionally
absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_LIMIT = 150
INTERCOM_VERSION = "2.11"
_REFUSAL_STATUS = frozenset({401, 403})

_SEARCH_PATHS: dict[str, str] = {
    "conversations": "/conversations/search",
    "contacts": "/contacts/search",
    "tickets": "/tickets/search",
}
_SEARCH_RECORD_KEYS: dict[str, str] = {
    "conversations": "conversations",
    "contacts": "data",
    "tickets": "tickets",
}
_LIST_PATHS: dict[str, str] = {
    "admins": "/admins",
    "tags": "/tags",
    "teams": "/teams",
    "segments": "/segments",
}
_ATTRIBUTE_MODELS: dict[str, str] = {
    "company_attributes": "company",
    "contact_attributes": "contact",
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    canonical: bool = True,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


INTERCOM_STREAMS: list[StreamSpec] = [
    _stream("conversations"),
    _stream("conversation_parts"),
    _stream("contacts", source_object="contact"),
    _stream("companies", source_object="company"),
    _stream("admins", cursor_field=None),
    _stream("activity_logs", cursor_field="created_at", canonical=False),
    _stream("tags", cursor_field=None, canonical=False),
    _stream("teams", cursor_field=None, canonical=False),
    _stream("segments", canonical=False),
    _stream(
        "company_attributes",
        source_object="company",
        cursor_field=None,
        canonical=False,
    ),
    _stream(
        "contact_attributes",
        source_object="contact",
        cursor_field=None,
        canonical=False,
    ),
    _stream("company_segments", canonical=False),
    _stream("tickets", canonical=False),
]


class IntercomConnector(RestConnector):
    name = "intercom"
    base_url = "https://api.intercom.io"
    streams_list = INTERCOM_STREAMS

    def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name not in _ATTRIBUTE_MODELS:
            return super().record_identity(record, stream)
        value = record.get("id") or record.get("full_name")
        return str(value) if isinstance(value, (str, int)) else None

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name not in {"tags", "teams", *_ATTRIBUTE_MODELS}:
            return super().record_ref(record, stream)
        value = record.get("name")
        return str(value) if isinstance(value, (str, int)) else None

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Intercom-Version"] = INTERCOM_VERSION
        return client

    @staticmethod
    def _build_search_body(
        stream: StreamSpec,
        cursor: str | None,
        starting_after: str | None,
    ) -> dict[str, Any]:
        """The POST body for a search-API page: `pagination.starting_after` drives intra-page
        paging, `query` filters `cursor_field > cursor` (the value as a JSON number — Intercom
        rejects a string), and `sort` ascends the cursor field so the watermark advances."""
        body: dict[str, Any] = {
            "pagination": {"per_page": PAGE_LIMIT},
            "sort": {"field": stream.cursor_field or "updated_at", "order": "ascending"},
        }
        if starting_after:
            body["pagination"]["starting_after"] = starting_after
        if cursor and stream.cursor_field:
            try:
                cursor_value: int | str = int(cursor)
            except (TypeError, ValueError):
                cursor_value = cursor
            body["query"] = {
                "field": stream.cursor_field,
                "operator": ">",
                "value": cursor_value,
            }
        else:
            body["query"] = {
                "field": stream.cursor_field or "updated_at",
                "operator": ">",
                "value": 0,
            }
        return body

    @staticmethod
    def _first(value: Any) -> dict[str, Any] | None:
        """Pick the first dict out of `{ <key>.<key>: [ {...}, ... ] }`."""
        if isinstance(value, list) and value:
            head = value[0]
            return head if isinstance(head, dict) else None
        return None

    @classmethod
    def _flatten_conversation(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Lift `source.{type,subject,body}` and the first associated
        contact id so SQL transforms can reach them as flat keys."""
        flat = dict(record)
        source = record.get("source")
        if isinstance(source, dict):
            flat["source__type"] = source.get("type")
            flat["source__subject"] = source.get("subject")
            flat["source__body"] = source.get("body")
        contacts = record.get("contacts")
        if isinstance(contacts, dict):
            first = cls._first(contacts.get("contacts"))
            if first is not None:
                flat["requester_id"] = first.get("id")
        # Some Intercom inboxes nest the `team_assignee_id` inside a
        # `teammates`/`assignee` envelope; the search API also returns
        # it at the top level as `team_assignee_id`. Don't overwrite.
        return flat

    @classmethod
    def _flatten_conversation_part(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Surface `author.{type,id}` as flat `author_type` / `author_id`.
        `conversation_id` is stamped by the substream paginator before
        records reach flatten — keep it untouched."""
        flat = dict(record)
        author = record.get("author")
        if isinstance(author, dict):
            flat["author_type"] = author.get("type")
            flat["author_id"] = author.get("id")
        return flat

    @classmethod
    def _flatten_contact(cls, record: dict[str, Any]) -> dict[str, Any]:
        """Lift the first associated company id to `org_id`."""
        flat = dict(record)
        companies = record.get("companies")
        if isinstance(companies, dict):
            first = cls._first(companies.get("companies"))
            if first is not None:
                flat["org_id"] = first.get("id") or first.get("company_id")
        return flat

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift each stream's envelope fields onto flat keys, then render the integer `cursor_field`
        as its decimal string so the watermark (string-only in the adapter) advances; the search
        filter parses it back with `int(...)`."""
        if stream.name == "conversations":
            record = self._flatten_conversation(record)
        elif stream.name == "conversation_parts":
            record = self._flatten_conversation_part(record)
        elif stream.name == "contacts":
            record = self._flatten_contact(record)
        if stream.cursor_field:
            value = record.get(stream.cursor_field)
            if isinstance(value, int):
                return {**record, stream.cursor_field: str(value)}
        return record

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            async for page in self._stream_pages(client, stream, cursor):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"intercom: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

    def _stream_pages(
        self, client: httpx.AsyncClient, stream: StreamSpec, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        name = stream.name
        if name in _SEARCH_PATHS:
            return self._paginate_search(client, stream, cursor=cursor)
        if name == "companies":
            return self._paginate_scroll(client)
        if name in _LIST_PATHS:
            return self._paginate_list(client, stream)
        if name in _ATTRIBUTE_MODELS:
            return self._paginate_attributes(client, stream)
        if name == "conversation_parts":
            return self._paginate_conversation_parts(client, cursor=cursor)
        if name == "company_segments":
            return self._paginate_company_segments(client)
        if name == "activity_logs":
            return self._paginate_activity_logs(client, cursor=cursor)
        raise NotImplementedError(f"intercom: no pagination strategy for stream {name!r}")

    async def _paginate_search(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _SEARCH_PATHS[stream.name]
        record_key = _SEARCH_RECORD_KEYS[stream.name]
        starting_after: str | None = None
        while True:
            body = self._build_search_body(stream, cursor, starting_after)
            data = await self._post(client, path, json=body)
            records = data.get(record_key) or []
            if records:
                yield records
            pages = data.get("pages") or {}
            nxt = pages.get("next") or {}
            starting_after = nxt.get("starting_after") if isinstance(nxt, dict) else None
            if not starting_after:
                return

    async def _paginate_scroll(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        scroll_param: str | None = None
        while True:
            params = {"scroll_param": scroll_param} if scroll_param else None
            data = await self._get(client, "/companies/scroll", params=params)
            records = data.get("data") or []
            if not records:
                return
            yield records
            scroll_param = data.get("scroll_param")
            if not scroll_param:
                return

    async def _paginate_list(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = _LIST_PATHS[stream.name]
        data = await self._get(client, path)
        for key in (stream.name, "data"):
            recs = data.get(key)
            if isinstance(recs, list):
                if recs:
                    yield recs
                return

    async def _paginate_attributes(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        model = _ATTRIBUTE_MODELS[stream.name]
        data = await self._get(client, "/data_attributes", params={"model": model})
        recs = [rec for rec in data.get("data") or [] if isinstance(rec, dict)]
        if recs:
            yield recs

    async def _paginate_conversation_parts(
        self,
        client: httpx.AsyncClient,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        starting_after: str | None = None
        conv_stream = next(s for s in INTERCOM_STREAMS if s.name == "conversations")
        while True:
            body = self._build_search_body(conv_stream, cursor, starting_after)
            data = await self._post(client, "/conversations/search", json=body)
            convs = data.get("conversations") or []
            for conv in convs:
                conv_id = conv.get("id")
                if not conv_id:
                    continue
                detail = await self._get(client, f"/conversations/{conv_id}")
                parts_envelope = detail.get("conversation_parts") or {}
                parts = (
                    parts_envelope.get("conversation_parts")
                    if isinstance(parts_envelope, dict)
                    else None
                ) or []
                for part in parts:
                    if isinstance(part, dict):
                        part.setdefault("conversation_id", conv_id)
                if parts:
                    yield parts
            pages = data.get("pages") or {}
            nxt = pages.get("next") or {}
            starting_after = nxt.get("starting_after") if isinstance(nxt, dict) else None
            if not starting_after:
                return

    async def _paginate_company_segments(
        self,
        client: httpx.AsyncClient,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        scroll_param: str | None = None
        while True:
            params = {"scroll_param": scroll_param} if scroll_param else None
            data = await self._get(client, "/companies/scroll", params=params)
            companies = data.get("data") or []
            if not companies:
                return
            for company in companies:
                cid = company.get("id")
                if not cid:
                    continue
                resp = await self._get(client, f"/companies/{cid}/segments")
                segs = resp.get("data") or []
                for seg in segs:
                    if isinstance(seg, dict):
                        seg.setdefault("company_id", cid)
                if segs:
                    yield segs
            scroll_param = data.get("scroll_param")
            if not scroll_param:
                return

    async def _paginate_activity_logs(
        self,
        client: httpx.AsyncClient,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params: dict[str, Any] = {}
        if cursor:
            params["created_at_after"] = cursor
        next_path: str | None = "/admins/activity_logs"
        first = True
        while next_path:
            data = await self._get(client, next_path, params=params if first else None)
            first = False
            recs = data.get("activity_logs") or []
            if recs:
                yield recs
            pages = data.get("pages") or {}
            nxt = pages.get("next")
            if isinstance(nxt, str) and nxt:
                next_path = nxt.replace(self.base_url, "") if nxt.startswith("http") else nxt
            else:
                next_path = None
