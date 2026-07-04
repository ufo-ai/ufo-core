"""The Zendesk Support connector — tickets, users, organizations, admin surface, Help Center, and
community synced as recallable pages.

Three read shapes share one `paginate` dispatch. High-volume objects (`tickets`, `users`,
`organizations`, `ticket_metric_events`) ride the cursor incremental export
(`/api/v2/incremental/<obj>/cursor.json?start_time=<unix>`), following `after_url` until
`end_of_stream`; `tickets` also sideloads `users` and lifts requester/assignee emails onto each
record. The rest page through `next_page` links. `ticket_comments` derives from the
`incremental/ticket_events.json` feed — each `Comment` child event is lifted to a row stamped with
its `ticket_id`; `users_identities` fans out per user. The base URL is per-tenant
(`https://<subdomain>.zendesk.com`). A refusal (401/403) raises `StreamSkipped`. The credential is
resolved through the auth proxy the runner threads; this connector holds no token. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

import httpx

from selfhost.sdk.sources import RestConnector, StreamSkipped, StreamSpec

PAGE_SIZE = 100
_REFUSAL_STATUS = frozenset({401, 403})

_INCREMENTAL_CURSOR_STREAMS = {
    "tickets": "tickets",
    "users": "users",
    "organizations": "organizations",
    "ticket_metric_events": "ticket_metric_events",
}

_SIDELOAD: dict[str, dict[str, Any]] = {
    "tickets": {
        "include": "users",
        "flatten": [
            ("requester_id", "users", "id", "requester_email"),
            ("submitter_id", "users", "id", "submitter_email"),
            ("assignee_id", "users", "id", "assignee_email"),
        ],
    },
}

_DATA_FIELD_OVERRIDES = {
    "account_attributes": "attributes",
    "attribute_definitions": "definitions",
    "ticket_audits": "audits",
    "ticket_skips": "skips",
    "ticket_activities": "activities",
    "sla_policies": "policies",
    "schedules": "schedules",
    "deleted_tickets": "deleted_tickets",
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = "updated_at",
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


ZENDESK_STREAMS: list[StreamSpec] = [
    _stream("tickets", canonical=True),
    _stream(
        "ticket_comments", source_object="ticket_events", cursor_field="created_at", canonical=True
    ),
    _stream("users", canonical=True),
    _stream("organizations", canonical=True),
    _stream("groups"),
    _stream("group_memberships"),
    _stream("organization_memberships"),
    _stream("organization_fields"),
    _stream("user_fields"),
    _stream("ticket_fields"),
    _stream("ticket_forms"),
    _stream("custom_roles"),
    _stream("account_attributes", source_object="routing/attributes", cursor_field=None),
    _stream(
        "attribute_definitions",
        source_object="routing/attributes/definitions",
        cursor_field=None,
    ),
    _stream("brands", cursor_field=None),
    _stream("schedules", source_object="business_hours/schedules"),
    _stream("macros"),
    _stream("automations", cursor_field=None),
    _stream("triggers"),
    _stream("sla_policies", source_object="slas/policies"),
    _stream("ticket_audits", cursor_field="created_at"),
    _stream("ticket_metrics"),
    _stream("ticket_metric_events", cursor_field="time"),
    _stream("ticket_activities", source_object="activities"),
    _stream("ticket_skips", source_object="skips"),
    _stream("satisfaction_ratings"),
    _stream("audit_logs", cursor_field="created_at"),
    _stream("tags", primary_key="name", cursor_field=None),
    _stream("deleted_tickets", cursor_field=None),
    _stream("users_identities", source_object="users", cursor_field="updated_at"),
    _stream("categories", source_object="help_center/categories"),
    _stream("sections", source_object="help_center/sections"),
    _stream("articles", source_object="help_center/articles"),
    _stream(
        "article_attachments", source_object="help_center/article_attachments", cursor_field=None
    ),
    _stream("article_comments", source_object="help_center/article_comments"),
    _stream(
        "article_comment_votes",
        source_object="help_center/article_comment_votes",
        cursor_field=None,
    ),
    _stream("article_votes", source_object="help_center/article_votes", cursor_field=None),
    _stream("topics", source_object="community/topics"),
    _stream("posts", source_object="community/posts"),
    _stream("post_comments", source_object="community/post_comments"),
    _stream("post_comment_votes", source_object="community/post_comment_votes", cursor_field=None),
    _stream("post_votes", source_object="community/post_votes", cursor_field=None),
]


def _apply_sideload(
    records: list[dict[str, Any]],
    page: dict[str, Any],
    flatten: list[tuple[str, str, str, str]],
) -> None:
    cache: dict[str, dict[Any, dict[str, Any]]] = {}
    for _, sideload_array, sideload_pk, _target in flatten:
        if sideload_array in cache:
            continue
        items = page.get(sideload_array) or []
        cache[sideload_array] = {
            item.get(sideload_pk): item for item in items if isinstance(item, dict)
        }
    for record in records:
        for role_field, sideload_array, _pk, target_field in flatten:
            if record.get(target_field):
                continue
            ref_id = record.get(role_field)
            if ref_id is None:
                continue
            entry = cache[sideload_array].get(ref_id)
            if entry is None:
                continue
            email = entry.get("email")
            if isinstance(email, str) and email:
                record[target_field] = email


class ZendeskConnector(RestConnector):
    name = "zendesk"
    base_url = "https://example.zendesk.com"
    streams_list = ZENDESK_STREAMS

    @staticmethod
    def _data_field(stream: StreamSpec) -> str:
        return _DATA_FIELD_OVERRIDES.get(stream.name, stream.name)

    @staticmethod
    def _cursor_to_unix(cursor: str | None) -> int:
        if not cursor:
            return 0
        text = str(cursor).strip()
        if text.isdigit():
            return int(text)
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return 0
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return int(parsed.timestamp())

    @staticmethod
    def _next_page_path(next_page: str | None) -> str | None:
        if not next_page:
            return None
        parsed = urlparse(next_page)
        if not parsed.path:
            return None
        path = parsed.path
        if parsed.query:
            path = f"{path}?{parsed.query}"
        return path

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "ticket_comments":
                async for page in self._paginate_ticket_comments(client, cursor):
                    yield page
                return
            if stream.name == "users_identities":
                async for page in self._paginate_user_identities(client, cursor):
                    yield page
                return
            if stream.name in _INCREMENTAL_CURSOR_STREAMS:
                async for page in self._paginate_incremental_cursor(client, stream, cursor=cursor):
                    yield page
                return
            async for page in self._paginate_default(client, stream):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"zendesk: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    async def _paginate_incremental_cursor(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        slug = _INCREMENTAL_CURSOR_STREAMS[stream.name]
        sideload = _SIDELOAD.get(stream.name)
        include_param = f"&include={sideload['include']}" if sideload else ""
        exclude_deleted = "&exclude_deleted=true" if stream.name == "tickets" else ""
        path: str | None = (
            f"/api/v2/incremental/{slug}/cursor.json"
            f"?start_time={self._cursor_to_unix(cursor)}"
            f"&per_page={PAGE_SIZE}{include_param}{exclude_deleted}"
        )
        while path:
            data = await self._get(client, path)
            records = data.get(slug) or []
            if records and sideload is not None:
                _apply_sideload(records, data, sideload["flatten"])
            if records:
                yield records
            if data.get("end_of_stream"):
                return
            path = self._next_page_path(data.get("after_url") or data.get("next_page"))

    async def _paginate_default(
        self, client: httpx.AsyncClient, stream: StreamSpec
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path: str | None = f"/api/v2/{stream.source_object}.json?per_page={PAGE_SIZE}"
        data_key = self._data_field(stream)
        while path:
            data = await self._get(client, path)
            records = data.get(data_key) or []
            if records:
                yield records
            path = self._next_page_path(data.get("next_page"))

    async def _paginate_ticket_comments(
        self, client: httpx.AsyncClient, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path: str | None = (
            f"/api/v2/incremental/ticket_events.json"
            f"?start_time={self._cursor_to_unix(cursor)}&include=comment_events"
            f"&per_page={PAGE_SIZE}"
        )
        while path:
            data = await self._get(client, path)
            comments: list[dict[str, Any]] = []
            for event in data.get("ticket_events") or []:
                if not isinstance(event, dict):
                    continue
                for child in event.get("child_events") or []:
                    if not isinstance(child, dict) or child.get("event_type") != "Comment":
                        continue
                    enriched = dict(child)
                    enriched["ticket_id"] = event.get("ticket_id")
                    if "created_at" not in enriched and event.get("timestamp"):
                        enriched["created_at"] = event["timestamp"]
                    comments.append(enriched)
            if comments:
                yield comments
            if data.get("end_of_stream"):
                return
            path = self._next_page_path(data.get("after_url") or data.get("next_page"))

    async def _paginate_user_identities(
        self, client: httpx.AsyncClient, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        users_path: str | None = (
            f"/api/v2/incremental/users/cursor.json"
            f"?start_time={self._cursor_to_unix(cursor)}&per_page={PAGE_SIZE}"
        )
        while users_path:
            users_data = await self._get(client, users_path)
            for user in users_data.get("users") or []:
                if not isinstance(user, dict) or not user.get("id"):
                    continue
                ident_path: str | None = (
                    f"/api/v2/users/{user['id']}/identities.json?per_page={PAGE_SIZE}"
                )
                while ident_path:
                    ident_data = await self._get(client, ident_path)
                    identities = ident_data.get("identities") or []
                    if identities:
                        yield identities
                    ident_path = self._next_page_path(ident_data.get("next_page"))
            if users_data.get("end_of_stream"):
                return
            users_path = self._next_page_path(
                users_data.get("after_url") or users_data.get("next_page")
            )
