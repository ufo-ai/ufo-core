"""The Sentry connector — organizations, projects, issues, events, members, and releases synced as
recallable pages.

Sentry paginates through a `Link` header carrying a `cursor="…"` token with `results="true"` until
the last page; `paginate` reads that token off each response's headers. Most streams fan out over
org/project tree the grant exposes: `_organizations` and `_projects` are walked first, then issues
and events are pulled per project (filtered server-side by `lastSeen`/`event.timestamp` past the
cursor), members and releases per org — each row stamped with its `organization_slug`/`project_slug`
context. A refusal (401/403) raises `StreamSkipped`; an unimplemented stream raises it too. The
credential is resolved through the auth proxy the runner threads; this connector holds no token. The
write path is intentionally absent — the source seam only reads."""

import re
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, with_context

_REFUSAL_STATUS = frozenset({401, 403})
_SENTRY_NEXT_RE = re.compile(r'rel="next";\s*results="true";\s*cursor="([^"]+)"')

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
    ),
    StreamSpec(
        name="projects",
        source_object="projects",
        primary_key="id",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
    ),
    StreamSpec(
        name="issues",
        source_object="issues",
        primary_key="id",
        cursor_field="lastSeen",
        created_at_field="firstSeen",
        updated_at_field="lastSeen",
    ),
    StreamSpec(
        name="events",
        source_object="events",
        primary_key="id",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
        canonical=False,
    ),
    StreamSpec(
        name="releases",
        source_object="releases",
        primary_key="version",
        cursor_field="dateCreated",
        created_at_field="dateCreated",
        updated_at_field=None,
        canonical=False,
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

    def record_ref(self, record: Mapping[str, Any], stream: StreamSpec) -> str | None:
        if stream.name != "organizations":
            return super().record_ref(record, stream)
        value = record.get("slug")
        return str(value) if isinstance(value, (str, int)) else None

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            async for page in self._stream_pages(client, stream.name, cursor):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"sentry: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks scope or the key is invalid"
                ) from error
            raise

    def _stream_pages(
        self, client: httpx.AsyncClient, name: str, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if name in {"organizations", "projects"}:
            return self._root_pages(client, name, cursor)
        if name == "members":
            return self._members(client)
        if name == "issues":
            return self._issues(client, cursor=cursor)
        if name == "events":
            return self._events(client, cursor=cursor)
        if name == "releases":
            return self._releases(client, cursor=cursor)
        raise StreamSkipped(f"sentry stream {name!r} is not implemented")

    async def _root_pages(
        self, client: httpx.AsyncClient, name: str, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        records = (
            await self._organizations(client)
            if name == "organizations"
            else await self._projects(client)
        )
        if name == "projects" and cursor:
            records = [
                record for record in records if str(record.get("dateCreated") or "") > cursor
            ]
        if records:
            yield records

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

    async def _organizations(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self._paged_list(client, "/organizations/"):
            out.extend(page)
        return out

    async def _projects(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self._paged_list(client, "/projects/"):
            out.extend(page)
        return out

    async def _members(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        for org in await self._organizations(client):
            slug = org.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            async for page in self._paged_list(client, f"/organizations/{slug}/members/"):
                yield with_context(page, organization_slug=slug)

    async def _issues(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for project in await self._projects(client):
            org = project.get("organization")
            org_slug = (
                org.get("slug") if isinstance(org, dict) else project.get("organization_slug")
            )
            project_slug = project.get("slug")
            if not isinstance(org_slug, str) or not isinstance(project_slug, str):
                continue
            params = {"query": f"lastSeen:>{cursor}"} if cursor else None
            path = f"/projects/{org_slug}/{project_slug}/issues/"
            async for page in self._paged_list(client, path, params=params):
                yield with_context(page, organization_slug=org_slug, project_slug=project_slug)

    async def _events(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for project in await self._projects(client):
            org = project.get("organization")
            org_slug = (
                org.get("slug") if isinstance(org, dict) else project.get("organization_slug")
            )
            project_slug = project.get("slug")
            if not isinstance(org_slug, str) or not isinstance(project_slug, str):
                continue
            params = {"query": f"event.timestamp:>{cursor}"} if cursor else None
            path = f"/projects/{org_slug}/{project_slug}/events/"
            async for page in self._paged_list(client, path, params=params):
                yield with_context(page, organization_slug=org_slug, project_slug=project_slug)

    async def _releases(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for org in await self._organizations(client):
            slug = org.get("slug")
            if not isinstance(slug, str) or not slug:
                continue
            async for releases in self._paged_list(client, f"/organizations/{slug}/releases/"):
                if cursor:
                    releases = [r for r in releases if str(r.get("dateCreated") or "") > cursor]
                if releases:
                    yield with_context(releases, organization_slug=slug)
