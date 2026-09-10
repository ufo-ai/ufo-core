"""The GitHub source connector, offline over a mock transport.

GitHub authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `api.github.com` — no live API, no
token. Covered: the `/user/orgs` → `/orgs/{org}/repos` fan-out feeding the repo-scoped walk, the
`repositories` and `organizations` collections advancing a watermark (never a snapshot — GitHub has
no delete signal, so the sync runner's row-level cursor handles re-reads), the per-repo cursor map
a repo-scoped stream checkpoints (each repo keeps its own `?since` watermark, so a capped run
resumes without skipping an unvisited repo's history), a newest-first stream stopping its page walk
at the repo's watermark and, once capped, resuming its backfill downward by `{high, until}` window
via `?until`, the Actions runs stream bounding every pass at its pinned floor with `created=>=`, and
a grant that cannot enumerate orgs at all (`/user/orgs` → 403) surfacing as `StreamSkipped` so the
run records a skip, not a failure."""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.github import GitHubConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

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

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
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
        ConnectorSourceConfig(stream=stream, backfill_after=backfill_after),
        cursor,
        auth,
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _commit(sha: str, date: str) -> dict[str, object]:
    return {"sha": sha, "commit": {"committer": {"date": date}}}


def _pull(number: int, updated_at: str) -> dict[str, object]:
    return {"id": number, "title": f"pr {number}", "updated_at": updated_at}


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


async def test_pull_requests_walk_the_order_they_checkpoint_on_and_stop_at_the_floor() -> None:
    """The pull requests a member reads are the recent ones and the ones still moving, so the walk
    descends `sort=updated&direction=desc` — the order its `updated_at` cursor is in, which GitHub's
    default `created` order is not — and stops on the first page reaching the row's floor instead of
    following the link header through every pull request the repo ever had.

    Descending by `created` instead would checkpoint the newest pull request number against an
    `updated_at` cursor — two different orders — which the steady-state pass below is the other half
    of."""
    pinned = datetime(2026, 8, 9, tzinfo=UTC)
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/pulls":
            queries.append(dict(request.url.params))
            return httpx.Response(
                200,
                json=[_pull(2, "2026-09-01T00:00:00Z"), _pull(1, "2026-01-01T00:00:00Z")],
                headers={
                    "Link": '<https://api.github.com/repos/acme/repo1/pulls?page=2>; rel="next"'
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    first = await _fetch("pull_requests", handle, backfill_after=pinned)

    assert len(queries) == 1
    assert queries[0]["sort"] == "updated"
    assert queries[0]["direction"] == "desc"
    assert queries[0]["state"] == "all"
    assert _refs(first) == {"pull_requests/acme/repo1/2"}
    assert json.loads(first.next_cursor) == {"acme/repo1": "2026-09-01T00:00:00Z"}


async def test_a_pull_request_updated_since_the_watermark_lands_again() -> None:
    """What a reviewer's trigger rests on: a pull request pushed to after the last pass reappears at
    the head of an `updated`-descending walk and lands, waking the conversation that watches the
    feed. The pass still stops at the first page below the watermark, so re-landing the moved rows
    costs one page and not the repo."""
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/pulls":
            queries.append(dict(request.url.params))
            if request.url.params.get("page"):
                return httpx.Response(200, json=[_pull(2, "2026-08-01T00:00:00Z")])
            return httpx.Response(
                200,
                json=[_pull(1, "2026-09-02T00:00:00Z")],
                headers={
                    "Link": '<https://api.github.com/repos/acme/repo1/pulls?page=2>; rel="next"'
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "pull_requests", handle, cursor=json.dumps({"acme/repo1": "2026-09-01T00:00:00Z"})
    )

    assert _refs(result) == {"pull_requests/acme/repo1/1"}
    assert json.loads(result.next_cursor) == {"acme/repo1": "2026-09-02T00:00:00Z"}
    assert len(queries) == 2


async def test_review_comments_climb_from_the_floor_on_a_first_pass() -> None:
    """`/pulls/comments` answers `?since`, so a floor on this stream is a bound the API keeps: a
    first pass climbs from it and the older comments are never fetched. The window is the row's
    only cutoff — the walk itself climbs and cannot overshoot one."""
    pinned = datetime(2026, 9, 1, tzinfo=UTC)
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/pulls/comments":
            queries.append(dict(request.url.params))
            return httpx.Response(
                200, json=[{"id": 9, "body": "nit", "updated_at": "2026-09-06T00:00:00Z"}]
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("review_comments", handle, backfill_after=pinned)

    assert queries[0]["since"] == "2026-09-01T00:00:00Z"
    assert queries[0]["sort"] == "updated"
    assert queries[0]["direction"] == "asc"
    assert _refs(result) == {"review_comments/acme/repo1/9"}
    assert json.loads(result.next_cursor) == {"acme/repo1": "2026-09-06T00:00:00Z"}


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


async def test_pull_requests_render_only_pull_request_content() -> None:
    def pull(
        *,
        title: str = "Feature",
        stars: int = 10,
        updated_at: str = "2026-02-06T00:00:00Z",
        head_sha: str = "abc",
        state: str = "open",
        draft: bool = False,
        merged_at: str | None = None,
    ) -> dict[str, object]:
        side_repo = {**REPO, "stargazers_count": stars}
        return {
            "id": 700,
            "title": title,
            "updated_at": updated_at,
            "state": state,
            "draft": draft,
            "merged_at": merged_at,
            "head": {
                "label": "ada:feature",
                "ref": "feature",
                "sha": head_sha,
                "repo": side_repo,
            },
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

    first = await _fetch("pull_requests", handler(pull()))
    timestamp_changed = await _fetch(
        "pull_requests", handler(pull(updated_at="2026-02-07T00:00:00Z"))
    )
    repo_changed = await _fetch("pull_requests", handler(pull(stars=42)))
    material_changes = (
        await _fetch("pull_requests", handler(pull(title="Feature v2"))),
        await _fetch("pull_requests", handler(pull(head_sha="fed"))),
        await _fetch("pull_requests", handler(pull(state="closed"))),
        await _fetch("pull_requests", handler(pull(draft=True))),
        await _fetch(
            "pull_requests",
            handler(pull(state="closed", merged_at="2026-02-07T00:00:00Z")),
        ),
    )

    assert first.pages[0].digest == timestamp_changed.pages[0].digest
    assert timestamp_changed.pages[0].updated_at == "2026-02-07T00:00:00.000000+00:00"
    assert first.pages[0].digest == repo_changed.pages[0].digest
    assert all(first.pages[0].digest != changed.pages[0].digest for changed in material_changes)
    body = json.loads(first.pages[0].body.split("\n\n", 1)[1])
    assert "updated_at" not in body
    assert body["head"] == {"label": "ada:feature", "ref": "feature", "sha": "abc"}
    assert body["base"] == {"label": "acme:main", "ref": "main", "sha": "def"}


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


def _contributor(total: int, commits: int) -> dict[str, object]:
    return {
        "author": {"id": 42, "login": "ada"},
        "total": total,
        "weeks": [{"w": 1767225600, "a": 10, "d": 2, "c": commits}],
    }


async def test_contributor_activity_keys_on_the_author_id_not_the_commit_counts() -> None:
    repo2 = {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}
    counts = iter((3, 9))

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, repo2])
        if request.url.path in {
            "/repos/acme/repo1/stats/contributors",
            "/repos/acme/repo2/stats/contributors",
        }:
            total = next(counts)
            return httpx.Response(200, json=[_contributor(total, total)])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("contributor_activity", handle)
    assert {page.source_identity for page in result.pages} == {
        "contributor_activity/acme/repo1/42",
        "contributor_activity/acme/repo2/42",
    }
    refs = _refs(result)
    counts = iter((4, 10))
    rerun = await _fetch("contributor_activity", handle)
    assert {page.source_identity for page in rerun.pages} == {
        "contributor_activity/acme/repo1/42",
        "contributor_activity/acme/repo2/42",
    }
    assert _refs(rerun).isdisjoint(refs)


async def test_a_contributor_with_no_author_is_dropped_and_named(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/stats/contributors":
            return httpx.Response(
                200, json=[{"author": None, "total": 1, "weeks": []}, _contributor(2, 2)]
            )
        return httpx.Response(404, json={"path": request.url.path})

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _fetch("contributor_activity", handle)

    assert {page.source_identity for page in result.pages} == {"contributor_activity/acme/repo1/42"}
    assert result.dropped == 1
    assert [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "source_sync.unkeyed_record"
    ] == [{"connector": "github", "stream": "contributor_activity", "primary_key": "author.id"}]


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


async def test_the_actions_api_lands_its_records_from_a_counted_envelope() -> None:
    """`/actions/runs` is the one GitHub collection that arrives wrapped — `{total_count,
    workflow_runs}` rather than the bare array every other stream answers with — so the walk reads
    the records out of the envelope. Reading the body as an array instead lands nothing while the
    link header still advances: no error, no refusal, and the page cap that ends a run counts landed
    records, so a run that lands none of them never reaches it and walks the whole history."""
    pages: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/actions/runs":
            pages.append(str(request.url))
            if len(pages) == 1:
                return httpx.Response(
                    200,
                    json={
                        "total_count": 2,
                        "workflow_runs": [
                            {"id": 11, "name": "ci", "updated_at": "2026-03-01T00:00:00Z"}
                        ],
                    },
                    headers={
                        "Link": (
                            "<https://api.github.com/repos/acme/repo1/actions/runs?page=2>; "
                            'rel="next"'
                        )
                    },
                )
            return httpx.Response(
                200,
                json={
                    "total_count": 2,
                    "workflow_runs": [
                        {"id": 12, "name": "deploy", "updated_at": "2026-02-01T00:00:00Z"}
                    ],
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("workflow_runs", handle)

    assert _refs(result) == {"workflow_runs/acme/repo1/11", "workflow_runs/acme/repo1/12"}
    assert len(pages) == 2


async def test_a_pinned_floor_bounds_workflow_runs_server_side() -> None:
    """Actions runs outnumber every other collection a busy repo publishes, and the stream is
    ordered `none`, so every pass re-walks each repo whole rather than resuming from a watermark.
    Its zero-day window pins the floor at registration and the slice sends it as the Actions API's
    own `created=>=` range, which is what keeps a pass to the runs made since the row was — without
    it the pass walks the repo's entire run history and the per-run cap ends it partway, every run.

    The filter is the Actions API's alone, so a repo stream that is not one of its collections must
    not carry it: `deployments` is pinned by the same floor and sends no `created`."""
    pinned = datetime(2026, 9, 8, 4, 45, tzinfo=UTC)
    runs: list[dict[str, str]] = []
    deployments: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/actions/runs":
            runs.append(dict(request.url.params))
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "workflow_runs": [
                        {"id": 11, "name": "ci", "updated_at": "2026-09-08T05:00:00Z"}
                    ],
                },
            )
        if request.url.path == "/repos/acme/repo1/deployments":
            deployments.append(dict(request.url.params))
            return httpx.Response(200, json=[{"id": 31, "updated_at": "2026-09-08T05:00:00Z"}])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("workflow_runs", handle, backfill_after=pinned)
    await _fetch("deployments", handle, backfill_after=pinned)

    spec = next(s for s in GitHubConnector().streams() if s.name == "workflow_runs")
    assert spec.backfill_window_days == 0
    assert runs[0]["created"] == ">=2026-09-08T04:45:00Z"
    assert "created" not in deployments[0]
    assert _refs(result) == {"workflow_runs/acme/repo1/11"}


async def test_workflows_lands_its_records_from_its_own_envelope_key() -> None:
    """The second Actions collection wraps its items under `workflows`, not the `workflow_runs` key
    its sibling uses, so the record path is the stream's own rather than one spelling per API."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/actions/workflows":
            return httpx.Response(
                200,
                json={
                    "total_count": 1,
                    "workflows": [
                        {"id": 21, "name": "ci.yaml", "updated_at": "2026-03-02T00:00:00Z"}
                    ],
                },
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("workflows", handle)

    assert _refs(result) == {"workflows/acme/repo1/21"}
