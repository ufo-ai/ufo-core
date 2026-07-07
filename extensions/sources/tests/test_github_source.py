"""The GitHub source connector, offline over a mock transport.

GitHub authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `api.github.com` — no live API, no
token. Covered: the `/user/orgs` → `/orgs/{org}/repos` fan-out feeding the repo-scoped walk, the
`repositories` and `organizations` collections advancing a watermark (never a snapshot — GitHub has
no delete signal, so the sync runner's row-level cursor handles re-reads), the incremental `issues`
stream sending `?since` and filtering pull requests out of the issue endpoint's shared response, and
a grant that cannot enumerate orgs at all (`/user/orgs` → 403) surfacing as `StreamSkipped` so the
run records a skip, not a failure."""

from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.github import GitHubConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
ORG = {"login": "acme", "id": 1}
REPO = {
    "id": 100,
    "name": "repo1",
    "full_name": "acme/repo1",
    "owner": {"login": "acme"},
    "updated_at": "2026-02-02T00:00:00Z",
    "archived": False,
    "fork": False,
}


class _MockProxy:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self._handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=GitHubConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream), cursor, auth
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


async def test_repositories_fan_out_over_granted_orgs_and_advance_a_watermark() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("repositories", handle)
    assert result.snapshot is False
    assert result.deletes == ()
    assert _refs(result) == {"repositories/100"}
    assert result.next_cursor == "2026-02-02T00:00:00Z"


async def test_organizations_walk_the_user_orgs_collection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/orgs"
        return httpx.Response(200, json=[ORG, {"login": "beta", "id": 2}])

    result = await _fetch("organizations", handle)
    assert result.snapshot is False
    assert result.next_cursor is None
    assert _refs(result) == {"organizations/1", "organizations/2"}


def _issues_handler(seen: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    issue = {"id": 500, "number": 1, "title": "Bug", "updated_at": "2026-02-04T00:00:00Z"}
    pull = {
        "id": 501,
        "number": 2,
        "title": "PR",
        "updated_at": "2026-02-03T00:00:00Z",
        "pull_request": {"url": "https://api.github.com/repos/acme/repo1/pulls/2"},
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/issues":
            assert request.url.params.get("state") == "all"
            seen.append(request.url.params.get("since") or "")
            return httpx.Response(200, json=[issue, pull])
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_issues_filter_pull_requests_and_advance_the_watermark() -> None:
    result = await _fetch("issues", _issues_handler([]))
    assert result.snapshot is False
    assert _refs(result) == {"issues/500"}
    assert result.next_cursor == "2026-02-04T00:00:00Z"


async def test_issues_incremental_sends_since() -> None:
    seen: list[str] = []
    await _fetch("issues", _issues_handler(seen), cursor="2026-02-01T00:00:00Z")
    assert seen and seen[0] == "2026-02-01T00:00:00Z"


async def test_org_enumeration_refusal_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(403, json={"message": "SAML enforcement"})
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(StreamSkipped, match="org scope"):
        await _fetch("repositories", handle)
