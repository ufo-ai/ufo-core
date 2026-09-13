"""The Mailchimp Marketing v3.0 connector — audiences, subscribers, campaigns, automations, reports
and the collections under them synced as recallable pages.

Every resource comes back under a plural JSON key (`{lists: [...], total_items: N}`) paged by
`?count=N&offset=M` until a short page. A top-level stream seeds `?since_<field>=<iso>` where
Mailchimp filters on that field; the collections under a list, a segment, an interest category or a
report take no such filter, since Mailchimp orders none of them by the field a run would resume at.
`email_activity` explodes each recipient's `activity[]` array into one row per action with a
synthesized `<email_id>:<action>:<timestamp>` id, Mailchimp shipping no per-event id of its own.

A member id is the subscriber hash — the same value for one contact in every list it belongs to —
so a member is addressed under its list, and the list reaches the record as `list_id`.

Auth is an OAuth2 bearer sent by the base client. The base URL is the per-tenant data-center host
(`https://<dc>.api.mailchimp.com`) — the stream paths carry the `/3.0` version prefix — so the class
default is empty and a run without a resolved host fails loud. A refusal (401/403) raises
`StreamSkipped`. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 500
_REFUSAL_STATUS = frozenset({401, 403})

_TOP_LEVEL_PATHS: dict[str, str] = {
    "lists": "/3.0/lists",
    "campaigns": "/3.0/campaigns",
    "automations": "/3.0/automations",
    "reports": "/3.0/reports",
}

_DATA_FIELDS: dict[str, str] = {
    "lists": "lists",
    "campaigns": "campaigns",
    "automations": "automations",
    "reports": "reports",
    "list_members": "members",
    "segments": "segments",
    "tags": "tags",
    "interest_categories": "categories",
    "interests": "interests",
    "segment_members": "members",
    "unsubscribes": "unsubscribes",
    "email_activity": "emails",
}

_CURSOR_PARAM: dict[str, str] = {
    "last_changed": "since_last_changed",
    "create_time": "since_create_time",
    "send_time": "since_send_time",
    "timestamp": "since",
    "updated_at": "since_updated_at",
    "date_created": "since_date_created",
}


def _stream(
    name: str,
    *,
    parent: str | None = None,
    path: str | None = None,
    carry: Mapping[str, str] | None = None,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str = "created_at",
    updated_at_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    if (parent is None) != (path is None):
        raise ValueError(
            f"mailchimp: stream {name!r} names a parent without a path, or the reverse"
        )
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
        parents=()
        if parent is None or path is None
        else (ParentEdge(stream=parent, path=path, carry=carry or {}),),
    )


MAILCHIMP_STREAMS: list[StreamSpec] = [
    _stream(
        "lists",
        cursor_field="date_created",
        created_at_field="date_created",
        updated_at_field=None,
    ),
    _stream(
        "list_members",
        parent="lists",
        path="/3.0/lists/{id}/members",
        carry={"list_id": "id"},
        source_object="members",
        cursor_field="last_changed",
        updated_at_field="last_changed",
        canonical=True,
    ),
    _stream("segments", parent="lists", path="/3.0/lists/{id}/segments", cursor_field="updated_at"),
    _stream(
        "campaigns",
        cursor_field="create_time",
        created_at_field="create_time",
        updated_at_field=None,
        canonical=True,
    ),
    _stream(
        "automations",
        cursor_field="create_time",
        created_at_field="create_time",
        updated_at_field=None,
    ),
    _stream(
        "email_activity",
        parent="reports",
        path="/3.0/reports/{id}/email-activity",
        source_object="emails",
        cursor_field="timestamp",
        created_at_field="timestamp",
        updated_at_field=None,
    ),
    _stream(
        "reports", cursor_field="send_time", created_at_field="send_time", updated_at_field=None
    ),
    _stream("tags", parent="lists", path="/3.0/lists/{id}/tag-search"),
    _stream("interest_categories", parent="lists", path="/3.0/lists/{id}/interest-categories"),
    _stream(
        "interests",
        parent="interest_categories",
        path="/3.0/lists/{list_id}/interest-categories/{id}/interests",
    ),
    _stream(
        "segment_members",
        parent="segments",
        path="/3.0/lists/{list_id}/segments/{id}/members",
        source_object="members",
        cursor_field="last_changed",
        updated_at_field="last_changed",
    ),
    _stream(
        "unsubscribes",
        parent="reports",
        path="/3.0/reports/{id}/unsubscribed",
        primary_key="email_id",
        cursor_field="timestamp",
        created_at_field="timestamp",
        updated_at_field=None,
    ),
]


def _activity_rows(page: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per action out of each recipient's `activity[]` array, keyed by a synthesized
    `<email_id>:<action>:<timestamp>` id — Mailchimp ships no per-event id, so this is what keeps
    the page's primary key stable across re-syncs."""
    rows: list[dict[str, Any]] = []
    for recipient in page:
        if not isinstance(recipient, dict):
            continue
        base = {key: value for key, value in recipient.items() if key != "activity"}
        for action in recipient.get("activity") or []:
            if not isinstance(action, dict):
                continue
            row = {**base, **action}
            row.setdefault(
                "id",
                f"{base.get('email_id', '')}:"
                f"{action.get('action', '')}:"
                f"{action.get('timestamp', '')}",
            )
            rows.append(row)
    return rows


class MailchimpConnector(RestConnector):
    name = "mailchimp"
    base_url = ""
    streams_list = MAILCHIMP_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name in {"list_members", "segment_members"}:
            return {
                **record,
                "created_at": record.get("timestamp_signup") or record.get("timestamp_opt"),
            }
        return record

    @staticmethod
    def _data_field(stream: StreamSpec) -> str:
        return _DATA_FIELDS.get(stream.name, stream.name)

    @staticmethod
    def _cursor_params(stream: StreamSpec, cursor: str | None) -> dict[str, Any]:
        """`?since_<field>=<iso>` when the stream has a cursor value and Mailchimp filters on it."""
        if not cursor or not stream.cursor_field:
            return {}
        param = _CURSOR_PARAM.get(stream.cursor_field)
        if not param:
            return {}
        return {param: cursor}

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name in _TOP_LEVEL_PATHS:
                async for page in self._paginate_top_level(
                    client, stream, _TOP_LEVEL_PATHS[stream.name], cursor=run.cursor
                ):
                    yield page
                return
            if stream.parents:
                pages = partial(self._partition_pages, client, stream)
                async for child_page in fanned_out(stream, run, pages):
                    yield child_page
                return
            raise NotImplementedError(f"mailchimp: stream {stream.name!r} has no paginate dispatch")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"mailchimp: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _paginate_top_level(
        self, client: httpx.AsyncClient, stream: StreamSpec, path: str, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._get_offset_pages(
            client,
            path,
            records_path=self._data_field(stream),
            limit=PAGE_SIZE,
            params=self._cursor_params(stream, cursor),
            limit_param="count",
        ):
            yield page

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        async for page in self._get_offset_pages(
            client,
            partition.path,
            records_path=self._data_field(stream),
            limit=PAGE_SIZE,
            limit_param="count",
        ):
            records = _activity_rows(page) if stream.name == "email_activity" else page
            if records:
                yield WalkPage(records=records)
