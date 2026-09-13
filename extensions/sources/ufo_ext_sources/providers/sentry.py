"""The Sentry connector — organizations, projects, issues, events, members, and releases synced as
recallable pages.

Sentry paginates through a `Link` header carrying a `cursor="…"` token with `results="true"` until
the last page; `_paged_list` reads that token off each response's headers. `/organizations/` and
`/projects/` are the two flat collections the API publishes — the project listing spans every
organization the grant reaches, so it is a root rather than a collection under one. Members and
releases are published under an organization's `slug`, issues and events under a project's
`organization.slug` and `slug`, which Sentry's project record carries. An issue id is unique across
the install, so `issues` declares `key_scope="global"` and an issue page is addressed by that id
alone. The project listing is the join table an issue reads its slugs from rather than content an
account connects for, so it is `indexed=False`. `issues` and `events` bound their resume
server-side — `lastSeen:>` and `event.timestamp:>` — and `releases` filters its `dateCreated`
client-side, its endpoint taking no bound. A refusal (401/403) raises `StreamSkipped`. The
credential is resolved through the auth proxy the runner threads; this connector holds no token. The
write path is intentionally absent — the source seam only reads."""

import re
from collections.abc import AsyncIterator, Mapping
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    Ordering,
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

_REFUSAL_STATUS = frozenset({401, 403})
_SENTRY_NEXT_RE = re.compile(r'rel="next";\s*results="true";\s*cursor="([^"]+)"')
_ORG_SLUG = {"organization_slug": "slug"}
_PROJECT_SLUGS = {"organization_slug": "organization.slug", "project_slug": "slug"}
_BOUND_QUERY = {"issues": "lastSeen", "events": "event.timestamp"}

SENTRY_STREAMS: list[StreamSpec] = [
    StreamSpec(
        name="organizations",
        source_object="organizations",
        primary_key="id",
        created_at_field="dateCreated",
        updated_at_field=None,
    ),
    StreamSpec(
        name="members",
        source_object="members",
        primary_key="id",
        created_at_field="dateCreated",
        updated_at_field=None,
        parents=(
            ParentEdge(
                stream="organizations", path="/organizations/{slug}/members/", carry=_ORG_SLUG
            ),
        ),
    ),
    StreamSpec(
        name="projects",
        source_object="projects",
        primary_key="id",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
        indexed=False,
    ),
    StreamSpec(
        name="issues",
        source_object="issues",
        primary_key="id",
        cursor_field="lastSeen",
        created_at_field="firstSeen",
        updated_at_field="lastSeen",
        canonical=True,
        ordering=Ordering.ascending,
        key_scope="global",
        parents=(
            ParentEdge(
                stream="projects",
                path="/projects/{organization.slug}/{slug}/issues/",
                carry=_PROJECT_SLUGS,
            ),
        ),
    ),
    StreamSpec(
        name="events",
        source_object="events",
        primary_key="id",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(
                stream="projects",
                path="/projects/{organization.slug}/{slug}/events/",
                carry=_PROJECT_SLUGS,
            ),
        ),
    ),
    StreamSpec(
        name="releases",
        source_object="releases",
        primary_key="version",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(
                stream="organizations", path="/organizations/{slug}/releases/", carry=_ORG_SLUG
            ),
        ),
    ),
]


def _sentry_next_cursor(headers: httpx.Headers) -> str | None:
    link = headers.get("link") or headers.get("Link")
    if not link:
        return None
    match = _SENTRY_NEXT_RE.search(link)
    return match.group(1) if match else None


class SentryConnector(RestConnector):
    name = "sentry"
    base_url = "https://sentry.io/api/0"
    streams_list = SENTRY_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != "organizations":
            return super().record_ref(record, stream)
        value = record.get("slug")
        return str(value) if isinstance(value, (str, int)) else None

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            async for page in self._stream_pages(client, stream, run):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"sentry: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    def _stream_pages(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.name == "organizations":
            return self._paged_list(client, "/organizations/")
        if stream.name == "projects":
            return self._project_pages(client, run.cursor)
        return fanned_out(stream, run, partial(self._partition_pages, client, stream))

    async def _project_pages(
        self, client: httpx.AsyncClient, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """`/projects/` takes no time bound, so the stored `dateCreated` watermark filters the page
        here."""
        async for page in self._paged_list(client, "/projects/"):
            records = (
                [record for record in page if str(record.get("dateCreated") or "") > cursor]
                if cursor
                else page
            )
            if records:
                yield records

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One parent's slice, carrying the walk's resume `bound` in Sentry's own terms: `issues`
        and `events` push it into the search query their endpoints take, and `releases` filters its
        own page, its endpoint taking none."""
        cursor_field = stream.cursor_field
        query = _BOUND_QUERY.get(stream.name)
        params = {"query": f"{query}:>{bound.after}"} if query and bound.after else None
        after = bound.after if query is None and cursor_field else None
        async for page in self._paged_list(client, partition.path, params=params):
            records = (
                [record for record in page if str(record.get(cursor_field) or "") > after]
                if after and cursor_field
                else page
            )
            if not records:
                continue
            values = (
                [value for record in records if isinstance(value := record.get(cursor_field), str)]
                if cursor_field
                else []
            )
            yield WalkPage(
                records=records,
                high=max(values) if values else None,
                low=min(values) if values else None,
            )

    async def _paged_list(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        while True:
            query = {**(params or {})}
            if cursor:
                query["cursor"] = cursor
            response = await self._get_raw(client, path, params=query)
            data = response.json() if response.content else []
            records = (
                [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
            )
            if records:
                yield records
            cursor = _sentry_next_cursor(response.headers)
            if not cursor:
                return
