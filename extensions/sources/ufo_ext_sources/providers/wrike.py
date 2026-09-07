"""The Wrike connector — contacts, folders, tasks, comments, workflows, and custom fields synced as
recallable pages.

Wrike returns every collection under a `{data: [...], nextPageToken}` envelope; `paginate` follows
`nextPageToken` through the shared body-cursor pager. Wrike exposes no reliable server-side `since`
filter, so an incremental stream reads each page and drops records at or before the stored watermark
on its `updatedDate` cursor. A refusal (401/403) raises `StreamSkipped`; an unimplemented stream
raises it too. The credential is resolved through the auth proxy the runner threads; this connector
holds no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, dict_or_empty
from ufo_ext_sources.watermark import text_checkpoint

_REFUSAL_STATUS = frozenset({401, 403})
_RUNNABLE_STREAMS = frozenset(
    {"contacts", "folders", "tasks", "comments", "workflows", "customfields"}
)

WRIKE_STREAMS: list[StreamSpec] = [
    StreamSpec(name="contacts", source_object="contacts", primary_key="id", canonical=True),
    StreamSpec(
        name="folders",
        source_object="folders",
        primary_key="id",
        cursor_field="updatedDate",
        updated_at_field="updatedDate",
        canonical=True,
    ),
    StreamSpec(
        name="tasks",
        source_object="tasks",
        primary_key="id",
        cursor_field="updatedDate",
        updated_at_field="updatedDate",
        canonical=True,
    ),
    StreamSpec(
        name="comments",
        source_object="comments",
        primary_key="id",
        cursor_field="updatedDate",
        updated_at_field="updatedDate",
        canonical=True,
    ),
    StreamSpec(name="workflows", source_object="workflows", primary_key="id"),
    StreamSpec(
        name="customfields",
        source_object="customfields",
        primary_key="id",
    ),
]


def _profile_email(record: dict[str, Any]) -> str | None:
    profiles = record.get("profiles")
    if not isinstance(profiles, list):
        return None
    for profile in profiles:
        if not isinstance(profile, dict):
            continue
        email = profile.get("email")
        if isinstance(email, str) and email:
            return email
    return None


class WrikeConnector(RestConnector):
    name = "wrike"
    base_url = "https://www.wrike.com/api/v4"
    streams_list = WRIKE_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name not in _RUNNABLE_STREAMS:
                raise StreamSkipped(f"wrike stream {stream.name!r} is not implemented")
            async for records in self._get_cursor_pages(
                client,
                f"/{stream.source_object}",
                records_path="data",
                next_cursor_path="nextPageToken",
                cursor_param="nextPageToken",
                page_size_param=None,
            ):
                if cursor and stream.cursor_field:
                    records = [r for r in records if str(r.get(stream.cursor_field) or "") > cursor]
                if records:
                    yield records
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"wrike: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "contacts":
            return {
                **record,
                "name": " ".join(
                    part
                    for part in [record.get("firstName"), record.get("lastName")]
                    if isinstance(part, str) and part
                )
                or record.get("me")
                or record.get("id"),
                "email": _profile_email(record),
                "created_at": record.get("createdDate"),
            }
        if stream.name == "folders":
            return {
                **record,
                "name": record.get("title"),
                "api_url": f"https://www.wrike.com/api/v4/folders/{record.get('id')}",
                "created_at": record.get("createdDate"),
            }
        if stream.name == "tasks":
            dates = dict_or_empty(record.get("dates"))
            return {
                **record,
                "name": record.get("title"),
                "status": record.get("status") or record.get("customStatusId"),
                "due_date": dates.get("due") or record.get("dueDate"),
                "created_at": record.get("createdDate"),
            }
        if stream.name == "comments":
            return {
                **record,
                "body": record.get("text"),
                "author": record.get("authorId"),
                "created_at": record.get("createdDate"),
                "parent_external_id": record.get("taskId"),
            }
        return record
