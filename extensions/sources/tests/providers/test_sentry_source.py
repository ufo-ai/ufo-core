"""The Sentry connector over a mock transport: the `Link: rel="next"; results="true"; cursor="…"`
header pagination, the two flat catalogs (`/organizations/`, `/projects/`), the four streams fanned
out over the pages those rows landed, and a refusal surfacing as `StreamSkipped`. A Sentry issue id
is unique across the install, so an issue page keeps the address it had before the tree —
`issues/<id>`, no scope. Offline — a canned transport, no DB, no token."""

import json
from collections.abc import Callable, Mapping
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.sentry import SentryConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]
ORG_REF = "organizations/1"
PROJECT_REF = "projects/p1"
ISSUES_KEY = f"{PROJECT_REF}\n/projects/acme/proj/issues/"
LANDED: Landed = {
    "organizations": (ParentRecord(ref=ORG_REF, fields={"slug": "acme"}),),
    "projects": (
        ParentRecord(ref=PROJECT_REF, fields={"organization.slug": "acme", "slug": "proj"}),
    ),
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    reader: ParentsReader,
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
    landed: Landed = LANDED,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler), parents=reader(landed))
    return await ConnectorBackend(connector=SentryConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_organizations_follow_the_link_header_cursor(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/0/organizations/"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(
                200, json=[{"id": "2", "slug": "o2", "dateCreated": "2026-01-02T00:00:00Z"}]
            )
        link = (
            "<https://sentry.io/api/0/organizations/?cursor=c2>; "
            'rel="next"; results="true"; cursor="c2"'
        )
        return httpx.Response(
            200,
            json=[{"id": "1", "slug": "o1", "dateCreated": "2026-01-01T00:00:00Z"}],
            headers={"Link": link},
        )

    result = await _fetch(parents_reader, "organizations", handle)
    assert _refs(result) == {"organizations/o1", "organizations/o2"}
    assert {page.source_identity for page in result.pages} == {"organizations/1", "organizations/2"}
    assert result.snapshot is False
    assert result.next_cursor is None
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_an_organization_keys_on_its_id_so_a_renamed_slug_keeps_its_page(
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/0/organizations/"
        return httpx.Response(200, json=[{"id": "42", "slug": "acme-renamed"}])

    result = await _fetch(parents_reader, "organizations", handle)
    assert _refs(result) == {"organizations/acme-renamed"}
    assert {page.source_identity for page in result.pages} == {"organizations/42"}
    assert "acme-renamed" in result.pages[0].body


async def test_members_preserve_sentry_date_created(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/organizations/acme/members/":
            return httpx.Response(200, json=[{"id": "m1", "dateCreated": "2026-01-01T00:00:00Z"}])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "members", handle)
    assert _refs(result) == {"members/acme/m1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_issues_read_only_the_projects_that_landed_and_keep_their_bare_address(
    parents_reader: ParentsReader,
) -> None:
    """The project listing is walked by the `projects` row, so an issues tick asks one endpoint:
    the issues under the project page that landed. A Sentry issue id is unique across the install,
    so `issues` declares `key_scope="global"` and the page keeps the `issues/<id>` address main
    writes — the project still keys the partition's cursor and composes its path."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/0/projects/acme/proj/issues/":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "i1",
                        "title": "Boom",
                        "firstSeen": "2026-01-01T00:00:00Z",
                        "lastSeen": "2026-02-01T00:00:00Z",
                    }
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "issues", handle)

    assert calls == ["/api/0/projects/acme/proj/issues/"]
    assert _refs(result) == {"issues/i1"}
    assert {page.source_identity for page in result.pages} == {"issues/i1"}
    assert json.loads(result.next_cursor) == {ISSUES_KEY: "2026-02-01T00:00:00Z"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


async def test_an_issue_carries_the_slugs_it_was_read_under(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/projects/acme/proj/issues/":
            return httpx.Response(200, json=[{"id": "i1", "title": "Boom"}])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, "issues", handle)
    body = json.loads(result.pages[0].body.split("\n\n", 1)[1])
    assert body["organization_slug"] == "acme"
    assert body["project_slug"] == "proj"


async def test_a_project_watermark_bounds_its_own_issues_server_side(
    parents_reader: ParentsReader,
) -> None:
    """Each project keeps its own `lastSeen` watermark, so a resume asks Sentry for that project's
    newer issues rather than re-reading every project from one shared mark."""
    queries: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params.get("query") or "")
        return httpx.Response(200, json=[{"id": "i2", "lastSeen": "2026-03-01T00:00:00Z"}])

    result = await _fetch(
        parents_reader, "issues", handle, cursor=json.dumps({ISSUES_KEY: "2026-02-01T00:00:00Z"})
    )

    assert queries == ["lastSeen:>2026-02-01T00:00:00Z"]
    assert _refs(result) == {"issues/i2"}


@pytest.mark.parametrize("stream", ["projects", "events", "releases"])
async def test_created_streams_preserve_sentry_date_created(
    stream: str, parents_reader: ParentsReader
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/projects/":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "p1",
                        "slug": "proj",
                        "dateCreated": "2026-02-01T00:00:00Z",
                        "organization": {"slug": "acme"},
                    }
                ],
            )
        if request.url.path == "/api/0/projects/acme/proj/events/":
            return httpx.Response(200, json=[{"id": "e1", "dateCreated": "2026-02-01T00:00:00Z"}])
        if request.url.path == "/api/0/organizations/acme/releases/":
            return httpx.Response(
                200, json=[{"version": "r1", "dateCreated": "2026-02-01T00:00:00Z"}]
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(parents_reader, stream, handle)
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_releases_filter_their_own_page_because_the_endpoint_takes_no_bound(
    parents_reader: ParentsReader,
) -> None:
    queries: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        queries.append(request.url.params.get("query") or "")
        return httpx.Response(
            200,
            json=[
                {"version": "old", "dateCreated": "2026-01-01T00:00:00Z"},
                {"version": "new", "dateCreated": "2026-03-01T00:00:00Z"},
            ],
        )

    key = f"{ORG_REF}\n/organizations/acme/releases/"
    result = await _fetch(
        parents_reader, "releases", handle, cursor=json.dumps({key: "2026-02-01T00:00:00Z"})
    )

    assert queries == [""]
    assert _refs(result) == {"releases/acme/new"}


def test_the_catalog_registers_the_projects_that_issues_fan_from() -> None:
    """`issues` is the one canonical stream, so the closure is it and the flat project listing it
    hangs under. `organizations` is a root of its own — `members` and `releases` hang under it and
    neither is canonical — so no connection registers it, and the RFC's expectation that the
    ancestor closure reaches it does not survive the map's finding that `/projects/` is flat."""
    streams = {stream.name: stream for stream in SentryConnector().streams()}

    assert syncing_streams(list(streams.values())) == {"issues", "projects"}
    assert streams["projects"].parents == ()
    assert streams["organizations"].parents == ()
    assert streams["issues"].key_scope == "global"
    assert streams["members"].key_scope == "local"
    assert not streams["projects"].indexed
    assert streams["issues"].indexed


async def test_stream_skipped_on_refusal(parents_reader: ParentsReader) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch(parents_reader, "organizations", handle)
