"""The Asana connector — work-tracking objects (projects, tasks, stories, users) and workspace
taxonomy synced into recallable memory pages.

Asana's REST API wraps every list response in `{data: [...], next_page: {offset, ...} | null}` and
paginates by cursor-token offsets: `?limit=100&offset=<token>`, followed until `next_page.offset`
goes null. Records arrive flat under `data`, keyed by Asana's `gid`. `tasks` and `projects` accept
`?modified_since=<iso>` for incremental sync (watermarked on `modified_at`); `stories` watermarks on
`created_at`; every other stream full-refreshes each run. The credential is resolved through the
auth proxy the runner threads (a broker's proxying transport, or a member-added key host-side) —
this connector holds no token. A refusal (401/403) raises `StreamSkipped`. The write path is
intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    Run,
    StreamSkipped,
    StreamSpec,
    list_or_empty,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
_MODIFIED_SINCE_STREAMS = frozenset({"tasks", "projects"})
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(
    name: str,
    *,
    cursor_field: str | None = None,
    updated_at_field: str | None = None,
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=name,
        primary_key="gid",
        cursor_field=cursor_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
    )


ASANA_STREAMS: list[StreamSpec] = [
    _stream(
        "projects",
        cursor_field="modified_at",
        updated_at_field="modified_at",
        canonical=True,
    ),
    _stream("tasks", cursor_field="modified_at", updated_at_field="modified_at", canonical=True),
    _stream("stories", cursor_field="created_at", canonical=True),
    _stream("users"),
    _stream("attachments"),
    _stream("attachments_compact"),
    _stream("organization_exports"),
    _stream("portfolio_items"),
    _stream("portfolios"),
    _stream("sections"),
    _stream("sections_compact"),
    _stream("stories_compact"),
    _stream("tags"),
    _stream("team_memberships"),
    _stream("teams"),
    _stream("workspaces"),
]


class AsanaConnector(RestConnector):
    name = "asana"
    base_url = "https://app.asana.com/api/1.0"
    streams_list = ASANA_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]]]:
        path = f"/{stream.source_object}"
        params: dict[str, Any] = {"limit": PAGE_SIZE}
        if run.cursor and stream.name in _MODIFIED_SINCE_STREAMS:
            params["modified_since"] = run.cursor
        try:
            while True:
                data = await self._get(client, path, params=params)
                records = list_or_empty(data.get("data"))
                if records:
                    yield records
                next_page = data.get("next_page")
                offset = next_page.get("offset") if isinstance(next_page, dict) else None
                if not isinstance(offset, str) or not offset:
                    return
                params = {**params, "offset": offset}
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"asana: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise
