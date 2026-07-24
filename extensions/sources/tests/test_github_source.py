"""The GitHub source connector, offline over a mock transport.

GitHub authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `api.github.com` — no live API, no
token. Covered: the `/user/orgs` → `/orgs/{org}/repos` fan-out feeding the repo-scoped walk, the
`repositories` and `organizations` collections advancing a watermark (never a snapshot — GitHub has
no delete signal, so the sync runner's row-level cursor handles re-reads), the per-repo cursor map
a repo-scoped stream checkpoints (each repo keeps its own `?since` watermark, so a capped run
resumes without skipping an unvisited repo's history), a newest-first stream stopping its page walk
at the repo's watermark and, once capped, resuming its backfill downward by `{high, until}` window
via `?until`, and a grant that cannot enumerate orgs at all (`/user/orgs` → 403) surfacing as
`StreamSkipped` so the run records a skip, not a failure."""

import json
from collections.abc import Callable
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.github import GitHubConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources import backend as backend_module
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
            assert request.url.params.get("sort") == "updated"
            assert request.url.params.get("direction") == "asc"
            seen.append(request.url.params.get("since") or "")
            return httpx.Response(200, json=[issue, pull])
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_issues_filter_pull_requests_and_checkpoint_the_repo_watermark() -> None:
    result = await _fetch("issues", _issues_handler([]))
    assert result.snapshot is False
    assert _refs(result) == {"issues/500"}
    assert result.next_cursor == json.dumps({"acme/repo1": "2026-02-04T00:00:00Z"}, sort_keys=True)


async def test_issues_incremental_sends_since() -> None:
    seen: list[str] = []
    cursor = json.dumps({"acme/repo1": "2026-02-01T00:00:00Z"}, sort_keys=True)
    await _fetch("issues", _issues_handler(seen), cursor=cursor)
    assert seen and seen[0] == "2026-02-01T00:00:00Z"


async def test_issues_send_since_per_repo() -> None:
    """Each repo keeps its own watermark: a repo the cursor map knows gets `?since`, a repo it has
    never finished gets the full walk — a mid-fan-out checkpoint cannot skip an unvisited repo's
    history."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}
    since_by_repo: dict[str, str | None] = {}
    issue2 = {"id": 600, "number": 3, "title": "Other", "updated_at": "2026-02-05T00:00:00Z"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path == "/repos/acme/repo1/issues":
            since_by_repo["repo1"] = request.url.params.get("since")
            return httpx.Response(200, json=[])
        if request.url.path == "/repos/acme/repo2/issues":
            since_by_repo["repo2"] = request.url.params.get("since")
            return httpx.Response(200, json=[issue2])
        return httpx.Response(404, json={"path": request.url.path})

    cursor = json.dumps({"acme/repo1": "2026-02-01T00:00:00Z"}, sort_keys=True)
    result = await _fetch("issues", handle, cursor=cursor)
    assert since_by_repo == {"repo1": "2026-02-01T00:00:00Z", "repo2": None}
    assert _refs(result) == {"issues/600"}
    assert result.next_cursor == json.dumps(
        {"acme/repo1": "2026-02-01T00:00:00Z", "acme/repo2": "2026-02-05T00:00:00Z"},
        sort_keys=True,
    )


async def test_pull_requests_drop_embedded_repositories() -> None:
    def pull(*, title: str, stars: int) -> dict[str, object]:
        side_repo = {**REPO, "stargazers_count": stars}
        return {
            "id": 700,
            "title": title,
            "updated_at": "2026-02-06T00:00:00Z",
            "head": {"label": "ada:feature", "ref": "feature", "sha": "abc", "repo": side_repo},
            "base": {"label": "acme:main", "ref": "main", "sha": "def", "repo": side_repo},
        }

    def handler(record: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/user/orgs":
                return httpx.Response(200, json=[ORG])
            if request.url.path == "/orgs/acme/repos":
                return httpx.Response(200, json=[REPO])
            if request.url.path == "/repos/acme/repo1/pulls":
                return httpx.Response(200, json=[record])
            return httpx.Response(404, json={"path": request.url.path})

        return handle

    first = await _fetch("pull_requests", handler(pull(title="Feature", stars=10)))
    repo_changed = await _fetch("pull_requests", handler(pull(title="Feature", stars=42)))
    pr_changed = await _fetch("pull_requests", handler(pull(title="Feature v2", stars=42)))

    assert first.pages[0].digest == repo_changed.pages[0].digest
    assert repo_changed.pages[0].digest != pr_changed.pages[0].digest
    body = json.loads(first.pages[0].body.split("\n\n", 1)[1])
    assert body["head"] == {"label": "ada:feature", "ref": "feature", "sha": "abc"}
    assert body["base"] == {"label": "acme:main", "ref": "main", "sha": "def"}


async def test_newest_first_stream_stops_at_the_repo_watermark() -> None:
    """`commits` arrives newest-first and append-only: once a whole page sits at or below the
    repo's watermark every later page is older, so the walk stops instead of re-reading history."""
    stale = {"sha": "aaa", "created_at": "2026-02-01T00:00:00Z"}
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path + ("?page=2" if request.url.params.get("page") else ""))
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/commits":
            if request.url.params.get("page"):
                raise AssertionError("paged past the watermark")
            return httpx.Response(
                200,
                json=[stale],
                headers={
                    "Link": '<https://api.github.com/repos/acme/repo1/commits?page=2>; rel="next"'
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    cursor = json.dumps({"acme/repo1": "2026-02-02T00:00:00Z"}, sort_keys=True)
    result = await _fetch("commits", handle, cursor=cursor)
    assert result.pages == ()
    assert result.next_cursor == cursor
    assert "/repos/acme/repo1/commits?page=2" not in requested


async def test_commits_backfill_windows_and_resumes_downward_with_until(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A capped newest-first backfill checkpoints a `{high, until}` window per repo and resumes by
    sending `?until` (inclusive) — walking the collection downward by value, so a commit prepended
    between slices is never dropped (it stays above the frozen `high` for the next steady-state
    pass), and the window dissolves to the plain `high` watermark once the walk exhausts."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    newest = {"sha": "c1", "created_at": "2026-03-03T00:00:00Z"}
    middle = {"sha": "c2", "created_at": "2026-03-02T00:00:00Z"}
    oldest = {"sha": "c3", "created_at": "2026-03-01T00:00:00Z"}
    seen_until: list[str | None] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/commits":
            until = request.url.params.get("until")
            seen_until.append(until)
            if until:
                return httpx.Response(200, json=[middle, oldest])
            if request.url.params.get("page"):
                return httpx.Response(200, json=[middle])
            return httpx.Response(
                200,
                json=[newest],
                headers={
                    "Link": ('<https://api.github.com/repos/acme/repo1/commits?page=2>; rel="next"')
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    first = await _fetch("commits", handle)
    assert _refs(first) == {"commits/c1", "commits/c2"}
    assert json.loads(first.next_cursor) == {
        "acme/repo1": {"high": "2026-03-03T00:00:00Z", "until": "2026-03-02T00:00:00Z"}
    }

    second = await _fetch("commits", handle, cursor=first.next_cursor)
    assert seen_until[-1] == "2026-03-02T00:00:00Z"
    assert _refs(second) == {"commits/c2", "commits/c3"}
    assert json.loads(second.next_cursor) == {"acme/repo1": "2026-03-03T00:00:00Z"}


@pytest.mark.parametrize("stream_name", ["events", "issue_events"])
async def test_events_steady_state_stops_early_at_the_repo_watermark(stream_name: str) -> None:
    """`events` and `issue_events` are newest-first but their APIs take no time filter, so
    steady-state resume relies on the walk's client-side stop-early: a page whose newest record
    sits at or below the repo watermark ends the walk without paging further."""
    stale = {"id": 900, "created_at": "2026-02-01T00:00:00Z"}
    requested: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path + ("?page=2" if request.url.params.get("page") else ""))
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == f"/repos/acme/repo1/{path_part}":
            if request.url.params.get("page"):
                raise AssertionError("paged past the watermark")
            return httpx.Response(
                200,
                json=[stale],
                headers={
                    "Link": (
                        f'<https://api.github.com/repos/acme/repo1/{path_part}?page=2>; rel="next"'
                    )
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    path_part = "events" if stream_name == "events" else "issues/events"
    cursor = json.dumps({"acme/repo1": "2026-02-02T00:00:00Z"}, sort_keys=True)
    result = await _fetch(stream_name, handle, cursor=cursor)
    assert result.pages == ()
    assert result.next_cursor == cursor
    assert f"/repos/acme/repo1/{path_part}?page=2" not in requested


@pytest.mark.parametrize("stream_name", ["events", "issue_events"])
async def test_events_backfill_filters_client_side_without_a_time_param(
    stream_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither `events` nor `issue_events` has a server-side `until`, so a backfill resume walks
    from the newest each slice and the walk drops records above `until` client-side (inclusive
    `<=`), landing only the unsynced tail — no request carries a time bound."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    above = {"id": 902, "created_at": "2026-02-04T00:00:00Z"}
    boundary = {"id": 901, "created_at": "2026-02-03T00:00:00Z"}
    params_seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == f"/repos/acme/repo1/{path_part}":
            params_seen.append(dict(request.url.params))
            return httpx.Response(200, json=[above, boundary])
        return httpx.Response(404, json={"path": request.url.path})

    path_part = "events" if stream_name == "events" else "issues/events"
    cursor = json.dumps(
        {"acme/repo1": {"high": "2026-02-05T00:00:00Z", "until": "2026-02-03T00:00:00Z"}},
        sort_keys=True,
    )
    result = await _fetch(stream_name, handle, cursor=cursor)
    assert all("until" not in params and "since" not in params for params in params_seen)
    assert _refs(result) == {f"{stream_name}/901"}


async def test_org_enumeration_refusal_raises_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(403, json={"message": "SAML enforcement"})
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(StreamSkipped, match="org scope"):
        await _fetch("repositories", handle)


async def test_branches_walk_the_default_none_path_per_repo() -> None:
    """The default `Ordering.none` streams — the majority of the catalog — ride the partition walk
    through the real connector: every repo's collection lands, and a completed pass leaves no
    markers behind, so the next run re-walks in full."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path == "/repos/acme/repo1/branches":
            return httpx.Response(200, json=[{"name": "main"}])
        if request.url.path == "/repos/acme/repo2/branches":
            return httpx.Response(200, json=[{"name": "dev"}])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("branches", handle)
    assert _refs(result) == {"branches/main", "branches/dev"}
    assert result.snapshot is False
    rerun = await _fetch("branches", handle, cursor=result.next_cursor)
    assert _refs(rerun) == {"branches/main", "branches/dev"}


async def test_repo_refusal_mid_walk_skips_only_that_repo() -> None:
    """A repo gone 404 mid-walk raises `PartitionSkipped` inside its page factory: the walk keeps
    the repo's stored state untouched — its mid-backfill window survives to resume, where a silent
    end would have dissolved it — and its neighbors still sync."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}
    fresh = {"sha": "c9", "created_at": "2026-03-05T00:00:00Z"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path == "/repos/acme/repo1/commits":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/repos/acme/repo2/commits":
            return httpx.Response(200, json=[fresh])
        return httpx.Response(404, json={"path": request.url.path})

    cursor = json.dumps(
        {
            "acme/repo1": {"high": "2026-03-04T00:00:00Z", "until": "2026-03-01T00:00:00Z"},
            "acme/repo2": "2026-03-02T00:00:00Z",
        },
        sort_keys=True,
    )
    result = await _fetch("commits", handle, cursor=cursor)
    assert _refs(result) == {"commits/c9"}
    assert json.loads(result.next_cursor) == {
        "acme/repo1": {"high": "2026-03-04T00:00:00Z", "until": "2026-03-01T00:00:00Z"},
        "acme/repo2": "2026-03-05T00:00:00Z",
    }
