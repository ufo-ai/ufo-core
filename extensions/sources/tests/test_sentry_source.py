"""The Sentry connector over a mock transport: the `Link: rel="next"; results="true"; cursor="…"`
header pagination, the project fan-out feeding `issues` (with the `lastSeen` watermark), and a
refusal surfacing as `StreamSkipped`. Offline — a canned transport, no DB, no token."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.sentry import SentryConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str, handler: Callable[[httpx.Request], httpx.Response], *, cursor: str | None = None
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=SentryConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_organizations_follow_the_link_header_cursor() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/0/organizations/"
        if request.url.params.get("cursor") == "c2":
            return httpx.Response(200, json=[{"slug": "o2", "dateCreated": "2026-01-02T00:00:00Z"}])
        link = (
            "<https://sentry.io/api/0/organizations/?cursor=c2>; "
            'rel="next"; results="true"; cursor="c2"'
        )
        return httpx.Response(
            200,
            json=[{"slug": "o1", "dateCreated": "2026-01-01T00:00:00Z"}],
            headers={"Link": link},
        )

    result = await _fetch("organizations", handle)
    assert _refs(result) == {"organizations/o1", "organizations/o2"}
    assert result.snapshot is False
    assert result.next_cursor is None
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_members_preserve_sentry_date_created() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/organizations/":
            return httpx.Response(200, json=[{"slug": "acme"}])
        if request.url.path == "/api/0/organizations/acme/members/":
            return httpx.Response(200, json=[{"id": "m1", "dateCreated": "2026-01-01T00:00:00Z"}])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("members", handle)
    assert _refs(result) == {"members/m1"}
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_issues_fan_out_over_projects_and_advance_last_seen() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/projects/":
            return httpx.Response(200, json=[{"slug": "proj", "organization": {"slug": "acme"}}])
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

    result = await _fetch("issues", handle)
    assert _refs(result) == {"issues/i1"}
    assert result.next_cursor == "2026-02-01T00:00:00Z"
    assert result.pages[0].created_at == "2026-01-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at == "2026-02-01T00:00:00.000000+00:00"


@pytest.mark.parametrize("stream", ["projects", "events", "releases"])
async def test_created_streams_preserve_sentry_date_created(stream: str) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/0/projects/":
            if stream == "projects":
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
            return httpx.Response(200, json=[{"slug": "proj", "organization": {"slug": "acme"}}])
        if request.url.path == "/api/0/organizations/":
            return httpx.Response(200, json=[{"slug": "acme"}])
        if request.url.path == "/api/0/projects/acme/proj/events/":
            return httpx.Response(200, json=[{"id": "e1", "dateCreated": "2026-02-01T00:00:00Z"}])
        if request.url.path == "/api/0/organizations/acme/releases/":
            return httpx.Response(
                200, json=[{"version": "r1", "dateCreated": "2026-02-01T00:00:00Z"}]
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(stream, handle)
    assert result.pages[0].created_at == "2026-02-01T00:00:00.000000+00:00"
    assert result.pages[0].updated_at is None


async def test_stream_skipped_on_refusal() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"detail": "forbidden"})

    with pytest.raises(StreamSkipped):
        await _fetch("organizations", handle)
