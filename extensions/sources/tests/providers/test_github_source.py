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
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.github import GitHubConnector

from ufo.access.connectors import Credential
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
    backfill_after: datetime | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler))
    return await ConnectorBackend(connector=GitHubConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream=stream, backfill_after=backfill_after),
        cursor,
        auth,
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _commit(sha: str, date: str) -> dict[str, object]:
    return {"sha": sha, "commit": {"committer": {"date": date}}}


async def test_a_pinned_floor_bounds_commits_server_side() -> None:
    """`commits` is the one newest-first repo feed GitHub will bound for us: the row's pinned floor
    goes out as `?since`, so a repo with years of history never sends the older commits at all. It
    crosses as the ISO string GitHub stamps `commit.committer.date` with, which is the value space
    the walk compares against — any other shape would compare wrong and bound nothing."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/commits":
            queries.append(dict(request.url.params))
            return httpx.Response(200, json=[_commit("c1", "2026-07-20T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("commits", handle, backfill_after=pinned)

    assert queries[0]["since"] == "2026-07-08T12:00:00Z"
    assert "until" not in queries[0]


async def test_a_page_entirely_below_the_floor_stops_the_descent() -> None:
    """The client-side floor filter must not hide the page span from the walk. Filtering the
    records first and reporting only the survivors' span means `low` never falls below the floor,
    so the walk's floor stop never fires — and a page filtered away entirely reports nothing at
    all, leaving the link header to be followed through a repo's whole history while landing none
    of it. Nothing lands, so the per-run record cap never trips either, and the run has no
    checkpointed progress to show for the API calls it spent.

    That is strictly worse than no floor at all, which is what makes it a regression rather than a
    missing feature: unfiltered, the records landed and the cap ended the run with a resumable
    window. So the assertion is the request count, not the records — one page, then stop."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/events":
            calls.append(str(request.url))
            # every record predates the floor, and GitHub offers another page below it
            return httpx.Response(
                200,
                json=[{"id": f"e{len(calls)}", "created_at": "2020-01-01T00:00:00Z"}],
                headers={
                    "Link": '<https://api.github.com/repos/acme/repo1/events?page=2>; rel="next"'
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("events", handle, backfill_after=pinned)

    assert len(calls) == 1  # stopped at the floor rather than walking the history down
    assert _refs(result) == set()


async def test_a_pinned_floor_drops_events_below_it_client_side() -> None:
    """`events` and `issue_events` expose no time filter, so the floor cannot be pushed server-side
    and is applied to the records instead: everything below it is dropped rather than landed. That
    caps what the window admits even where it cannot cap what is fetched, and the walk stopping at
    the floor is what bounds the paging."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/events":
            return httpx.Response(
                200,
                json=[
                    {"id": "e-inside", "created_at": "2026-07-20T00:00:00Z"},
                    {"id": "e-below", "created_at": "2020-01-01T00:00:00Z"},
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("events", handle, backfill_after=pinned)

    assert _refs(result) == {"events/acme/repo1/e-inside"}


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
    assert _refs(result) == {"repositories/acme/100"}
    assert result.next_cursor == "2026-02-02T00:00:00Z"
    assert result.pages[0].updated_at == "2026-02-02T00:00:00.000000+00:00"


async def test_organizations_walk_the_user_orgs_collection() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/user/orgs"
        return httpx.Response(200, json=[ORG, {"login": "beta", "id": 2}])

    result = await _fetch("organizations", handle)
    assert result.snapshot is False
    assert result.next_cursor is None
    assert _refs(result) == {"organizations/1", "organizations/2"}


async def test_stargazers_request_star_timestamps_and_project_user_identity() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/stargazers":
            assert "application/vnd.github.star+json" in request.headers["Accept"]
            return httpx.Response(
                200,
                json=[
                    {
                        "starred_at": "2026-02-03T00:00:00Z",
                        "user": {"id": 42, "login": "ada"},
                    }
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("stargazers", handle)
    assert _refs(result) == {"stargazers/acme/repo1/42"}
    assert result.pages[0].created_at == "2026-02-03T00:00:00.000000+00:00"


async def test_one_starrer_of_two_repos_lands_as_two_pages() -> None:
    """A starrer is one GitHub user id whatever they star, and `flatten` merges that user into the
    record, so both repos' rows key on `42` and used to settle on one page whose `starred_at` each
    sync overwrote with the other repo's. The repo qualifier splits them — the records differ only
    in their star time, never in the key — and each page keeps its own timestamp."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        starred_at = (
            "2026-02-03T00:00:00Z" if "repo1" in request.url.path else "2026-04-09T00:00:00Z"
        )
        if request.url.path in {"/repos/acme/repo1/stargazers", "/repos/acme/repo2/stargazers"}:
            return httpx.Response(
                200, json=[{"starred_at": starred_at, "user": {"id": 42, "login": "ada"}}]
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("stargazers", handle)
    assert _refs(result) == {"stargazers/acme/repo1/42", "stargazers/acme/repo2/42"}
    assert {page.source_ref: page.created_at for page in result.pages} == {
        "stargazers/acme/repo1/42": "2026-02-03T00:00:00.000000+00:00",
        "stargazers/acme/repo2/42": "2026-04-09T00:00:00.000000+00:00",
    }


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
    assert _refs(result) == {"issues/acme/repo1/500"}
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
    assert _refs(result) == {"issues/acme/repo2/600"}
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
    stale = _commit("aaa", "2026-02-01T00:00:00Z")
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
    newest = _commit("c1", "2026-03-03T00:00:00Z")
    middle = _commit("c2", "2026-03-02T00:00:00Z")
    oldest = _commit("c3", "2026-03-01T00:00:00Z")
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
    assert _refs(first) == {"commits/acme/repo1/c1", "commits/acme/repo1/c2"}
    assert json.loads(first.next_cursor) == {
        "acme/repo1": {"high": "2026-03-03T00:00:00Z", "until": "2026-03-02T00:00:00Z"}
    }

    second = await _fetch("commits", handle, cursor=first.next_cursor)
    assert seen_until[-1] == "2026-03-02T00:00:00Z"
    assert _refs(second) == {"commits/acme/repo1/c2", "commits/acme/repo1/c3"}
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
    assert _refs(result) == {f"{stream_name}/acme/repo1/901"}


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
    markers behind, so the next run re-walks in full. A branch name is unique only inside its repo,
    so each page ref carries the repo it was walked from and every repo's `main` lands as its own
    page instead of rewriting one shared page every sync. The scoped key is the record's own `name`,
    so the title a member recalls names its repo too rather than a bare `main` per repo."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path == "/repos/acme/repo1/branches":
            return httpx.Response(200, json=[{"name": "main"}])
        if request.url.path == "/repos/acme/repo2/branches":
            return httpx.Response(200, json=[{"name": "main"}, {"name": "dev"}])
        return httpx.Response(404, json={"path": request.url.path})

    expected = {"branches/acme/repo1/main", "branches/acme/repo2/main", "branches/acme/repo2/dev"}
    result = await _fetch("branches", handle)
    assert _refs(result) == expected
    assert result.snapshot is False
    bodies = {page.source_ref: json.loads(page.body.split("\n\n", 1)[1]) for page in result.pages}
    assert bodies["branches/acme/repo1/main"]["repo_full_name"] == "acme/repo1"
    assert bodies["branches/acme/repo2/main"]["repo_full_name"] == "acme/repo2"
    assert bodies["branches/acme/repo1/main"]["name"] == "acme/repo1/main"
    assert {page.title for page in result.pages} == {
        "acme/repo1/main",
        "acme/repo2/main",
        "acme/repo2/dev",
    }
    rerun = await _fetch("branches", handle, cursor=result.next_cursor)
    assert _refs(rerun) == expected


async def test_org_scoped_streams_are_qualified_by_the_org_they_were_walked_from() -> None:
    """A member of two granted orgs is one GitHub user id but two org-membership records, so the
    org-scoped fan-out qualifies each page ref with its org rather than collapsing both onto one
    page."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG, {"login": "beta", "id": 2}])
        if request.url.path in {"/orgs/acme/members", "/orgs/beta/members"}:
            return httpx.Response(200, json=[{"id": 42, "login": "ada"}])
        if request.url.path == "/users/ada":
            return httpx.Response(200, json={"id": 42, "login": "ada", "name": "Ada"})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("users", handle)
    assert _refs(result) == {"users/acme/42", "users/beta/42"}


@pytest.mark.parametrize(
    ("stream_name", "record", "key"),
    [
        ("tags", {"name": "v1.0.0"}, "v1.0.0"),
        ("assignees", {"id": 42, "login": "ada"}, "42"),
        ("collaborators", {"id": 42, "login": "ada"}, "42"),
        ("commits", _commit("abc123", "2026-03-03T00:00:00Z"), "abc123"),
    ],
)
async def test_an_identical_record_in_two_repos_lands_as_two_pages(
    stream_name: str, record: dict[str, object], key: str
) -> None:
    """The repo-scoped collision class, one identical record per repo: a tag name is unique only
    inside its repo, one person is one GitHub user id across every repo they are assignable on, and
    a sha reachable from two repos is one sha. Serving byte-identical records from two repos proves
    the repo qualifier, not the record, is what separates the pages — a distinct-key fixture would
    only prove the ref format changed."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path in {
            f"/repos/acme/repo1/{stream_name}",
            f"/repos/acme/repo2/{stream_name}",
        }:
            return httpx.Response(200, json=[record])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(stream_name, handle)
    assert _refs(result) == {
        f"{stream_name}/acme/repo1/{key}",
        f"{stream_name}/acme/repo2/{key}",
    }


async def test_a_record_with_no_primary_key_still_separates_per_repo() -> None:
    """`flatten` writes no prefix when the record carries no `primary_key` — `contributor_activity`
    is a runnable stream whose `/stats/contributors` records have no `id`, so the branch fires on
    every sync of it. Separation then rests entirely on the stamped `repo_full_name` being part of
    the record the adapter content-hashes: two repos serving the identical keyless record still land
    two pages, neither carrying a repo prefix."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path in {"/repos/acme/repo1/assignees", "/repos/acme/repo2/assignees"}:
            return httpx.Response(200, json=[{"login": "ada"}])
        return httpx.Response(404, json={"path": request.url.path})

    refs = _refs(await _fetch("assignees", handle))
    assert len(refs) == 2
    assert all(ref.startswith("assignees/") and "acme/repo" not in ref for ref in refs)


def test_flatten_raises_when_a_fanned_out_record_carries_no_partition() -> None:
    """The fan-out sites stamp the partition; `flatten` scoping it is the only reader, so a record
    that reached it unstamped is a wiring bug and fails loud rather than landing a colliding ref."""
    connector = GitHubConnector()
    branches = next(stream for stream in connector.streams() if stream.name == "branches")
    with pytest.raises(RuntimeError, match="fans out over"):
        connector.flatten({"name": "main"}, branches)


def test_no_runnable_stream_keys_on_its_own_cursor_field() -> None:
    """Scoping the primary key is safe only while no stream's key IS its cursor: the watermark the
    sync advances compares raw provider values, and a scoped key would corrupt the comparison."""
    assert [
        stream.name
        for stream in GitHubConnector().streams()
        if stream.primary_key == stream.cursor_field
    ] == []


async def test_repo_refusal_mid_walk_skips_only_that_repo() -> None:
    """A repo gone 404 mid-walk raises `PartitionSkipped` inside its page factory: the walk keeps
    the repo's stored state untouched — its mid-backfill window survives to resume, where a silent
    end would have dissolved it — and its neighbors still sync."""
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}
    fresh = _commit("c9", "2026-03-05T00:00:00Z")

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
    assert _refs(result) == {"commits/acme/repo2/c9"}
    assert json.loads(result.next_cursor) == {
        "acme/repo1": {"high": "2026-03-04T00:00:00Z", "until": "2026-03-01T00:00:00Z"},
        "acme/repo2": "2026-03-05T00:00:00Z",
    }
