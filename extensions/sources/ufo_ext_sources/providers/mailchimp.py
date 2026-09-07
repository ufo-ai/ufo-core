"""The Mailchimp Marketing v3.0 connector — audiences, subscribers, campaigns, automations, reports,
and their per-list / per-report fan-out collections synced as recallable pages.

Every top-level resource comes back under a plural JSON key (`{lists: [...], total_items: N}`) paged
by `?count=N&offset=M` until a short page. `paginate` has three families sharing one dispatcher:
top-level offset walks (lists, campaigns, automations, reports); per-list fan-outs (list_members,
segments, tags, interest_categories) and the deeper list→category→interest and list→segment→member
walks; and per-report fan-outs (unsubscribes, and email_activity, whose per-recipient `activity[]`
array is exploded into one row per action with a synthesized stable id). A cursor-bearing stream
seeds `?since_<field>=<iso>` when Mailchimp filters on that field. Auth is an OAuth2 bearer sent by
the base client. The base URL is the per-tenant data-center host (`https://<dc>.api.mailchimp.com`)
— the stream paths carry the `/3.0` version prefix — so the class default is empty and a run without
a resolved host fails loud. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import quote

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 500
_REFUSAL_STATUS = frozenset({401, 403})

# Stream-name → top-level path (straight offset pagination, no parent fanout).
_TOP_LEVEL_PATHS: dict[str, str] = {
    "lists": "/3.0/lists",
    "campaigns": "/3.0/campaigns",
    "automations": "/3.0/automations",
    "reports": "/3.0/reports",
}

# Stream-name → the JSON key Mailchimp wraps its records under.
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

# Cursor field → Mailchimp's `?since_*` query parameter for server-side incremental filtering.
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
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str = "created_at",
    updated_at_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
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
        source_object="members",
        cursor_field="last_changed",
        updated_at_field="last_changed",
        canonical=True,
    ),
    _stream("segments", cursor_field="updated_at"),
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
        source_object="emails",
        cursor_field="timestamp",
        created_at_field="timestamp",
        updated_at_field=None,
    ),
    _stream(
        "reports", cursor_field="send_time", created_at_field="send_time", updated_at_field=None
    ),
    _stream("tags"),
    _stream("interest_categories"),
    _stream("interests"),
    _stream(
        "segment_members",
        source_object="members",
        cursor_field="last_changed",
        updated_at_field="last_changed",
    ),
    _stream(
        "unsubscribes",
        primary_key="email_id",
        cursor_field="timestamp",
        created_at_field="timestamp",
        updated_at_field=None,
    ),
]


class MailchimpConnector(RestConnector):
    name = "mailchimp"
    base_url = ""
    streams_list = MAILCHIMP_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def record_identity(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != "unsubscribes":
            return super().record_identity(record, stream)
        campaign_id = record.get("campaign_id")
        email_id = record.get("email_id")
        if campaign_id is None or email_id is None:
            return None
        return f"{campaign_id}:{email_id}"

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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name in _TOP_LEVEL_PATHS:
                async for page in self._paginate_top_level(
                    client, stream, _TOP_LEVEL_PATHS[stream.name], cursor=cursor
                ):
                    yield page
                return
            per_list_child = {
                "list_members": "members",
                "segments": "segments",
                "tags": "tag-search",
                "interest_categories": "interest-categories",
            }
            if stream.name in per_list_child:
                async for page in self._paginate_per_list(
                    client,
                    stream,
                    child_path=per_list_child[stream.name],
                    cursor=cursor,
                    stamp_parent_field="list_id",
                ):
                    yield page
                return
            if stream.name == "interests":
                async for page in self._paginate_interests(client, stream, cursor=cursor):
                    yield page
                return
            if stream.name == "segment_members":
                async for page in self._paginate_segment_members(client, stream, cursor=cursor):
                    yield page
                return
            per_report_child = {"unsubscribes": "unsubscribed"}
            if stream.name in per_report_child:
                async for page in self._paginate_per_report(
                    client,
                    stream,
                    child_path=per_report_child[stream.name],
                    cursor=cursor,
                    stamp_parent_field="campaign_id",
                ):
                    yield page
                return
            if stream.name == "email_activity":
                async for page in self._paginate_email_activity(client, cursor=cursor):
                    yield page
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

    async def _paginate_child(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        data_field: str,
        params_base: dict[str, Any] | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Generic offset walk for a nested endpoint, reading records at `data_field`."""
        async for page in self._get_offset_pages(
            client,
            path,
            records_path=data_field,
            limit=PAGE_SIZE,
            params=params_base,
            limit_param="count",
        ):
            yield page

    async def _ids(
        self, client: httpx.AsyncClient, path: str, data_field: str
    ) -> AsyncIterator[str]:
        async for page in self._get_offset_pages(
            client, path, records_path=data_field, limit=PAGE_SIZE, limit_param="count"
        ):
            for row in page:
                if isinstance(row, dict) and row.get("id"):
                    yield str(row["id"])

    async def _list_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]:
        async for list_id in self._ids(client, "/3.0/lists", "lists"):
            yield list_id

    async def _report_ids(self, client: httpx.AsyncClient) -> AsyncIterator[str]:
        async for report_id in self._ids(client, "/3.0/reports", "reports"):
            yield report_id

    async def _paginate_per_list(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        child_path: str,
        cursor: str | None,
        stamp_parent_field: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """For each list, fetch the child collection, stamping the parent `list_id`."""
        data_field = self._data_field(stream)
        params_base = self._cursor_params(stream, cursor)
        async for list_id in self._list_ids(client):
            path = f"/3.0/lists/{quote(list_id, safe='')}/{child_path}"
            async for page in self._paginate_child(
                client, path, data_field=data_field, params_base=params_base
            ):
                if stamp_parent_field:
                    for row in page:
                        if isinstance(row, dict):
                            row.setdefault(stamp_parent_field, list_id)
                yield page

    async def _paginate_interests(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk lists → interest-categories → interests."""
        async for list_id in self._list_ids(client):
            cat_path = f"/3.0/lists/{quote(list_id, safe='')}/interest-categories"
            async for cat_page in self._paginate_child(client, cat_path, data_field="categories"):
                for cat in cat_page:
                    if not isinstance(cat, dict) or not cat.get("id"):
                        continue
                    cat_id = str(cat["id"])
                    int_path = (
                        f"/3.0/lists/{quote(list_id, safe='')}"
                        f"/interest-categories/{quote(cat_id, safe='')}/interests"
                    )
                    async for int_page in self._paginate_child(
                        client, int_path, data_field="interests"
                    ):
                        for row in int_page:
                            if isinstance(row, dict):
                                row.setdefault("list_id", list_id)
                                row.setdefault("category_id", cat_id)
                        yield int_page

    async def _paginate_segment_members(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params_base = self._cursor_params(stream, cursor)
        async for list_id in self._list_ids(client):
            seg_path = f"/3.0/lists/{quote(list_id, safe='')}/segments"
            async for seg_page in self._paginate_child(client, seg_path, data_field="segments"):
                for seg in seg_page:
                    if not isinstance(seg, dict) or seg.get("id") is None:
                        continue
                    seg_id = str(seg["id"])
                    mem_path = (
                        f"/3.0/lists/{quote(list_id, safe='')}"
                        f"/segments/{quote(seg_id, safe='')}/members"
                    )
                    async for mem_page in self._paginate_child(
                        client, mem_path, data_field="members", params_base=params_base
                    ):
                        for row in mem_page:
                            if isinstance(row, dict):
                                row.setdefault("list_id", list_id)
                                row.setdefault("segment_id", seg_id)
                        yield mem_page

    async def _paginate_per_report(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        child_path: str,
        cursor: str | None,
        stamp_parent_field: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        data_field = self._data_field(stream)
        params_base = self._cursor_params(stream, cursor)
        async for cid in self._report_ids(client):
            path = f"/3.0/reports/{quote(cid, safe='')}/{child_path}"
            async for page in self._paginate_child(
                client, path, data_field=data_field, params_base=params_base
            ):
                if stamp_parent_field:
                    for row in page:
                        if isinstance(row, dict):
                            row.setdefault(stamp_parent_field, cid)
                yield page

    async def _paginate_email_activity(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk reports → email-activity, exploding each recipient's `activity[]` into one row per
        action with a synthesized `<email_id>:<action>:<timestamp>` id (Mailchimp ships no per-event
        id) so the page's primary key stays stable across re-syncs."""
        params_base: dict[str, Any] = {}
        if cursor:
            params_base["since"] = cursor
        async for cid in self._report_ids(client):
            path = f"/3.0/reports/{quote(cid, safe='')}/email-activity"
            async for page in self._paginate_child(
                client, path, data_field="emails", params_base=params_base
            ):
                exploded: list[dict[str, Any]] = []
                for parent in page:
                    if not isinstance(parent, dict):
                        continue
                    base = {key: value for key, value in parent.items() if key != "activity"}
                    base.setdefault("campaign_id", cid)
                    for act in parent.get("activity") or []:
                        if not isinstance(act, dict):
                            continue
                        row = {**base, **act}
                        row.setdefault(
                            "id",
                            f"{base.get('email_id', '')}:"
                            f"{act.get('action', '')}:"
                            f"{act.get('timestamp', '')}",
                        )
                        exploded.append(row)
                if exploded:
                    yield exploded
