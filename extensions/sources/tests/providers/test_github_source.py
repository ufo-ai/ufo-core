"""The GitHub source connector, offline over a mock transport.

GitHub authenticates through the `AuthProxy` seam, so these drive the connector with a mock proxy
whose `Credential` carries an `httpx.MockTransport` bound to `api.github.com` — no live API, no
token. The landed parent records a stream fans out over are handed in the way the sync driver hands
them in, so a repo-scoped stream's requests are exactly the ones it spends. Covered: a repo-scoped
stream asking only its own endpoint, `repositories` fanning out over the landed organizations as a
snapshot (the catalog is a full listing each pass, so a repository that stops qualifying is
tombstoned and leaves the partition set; every stream under a repository is incremental, GitHub
having no delete signal for those), the cursor map a repo-scoped stream checkpoints against the
parent page ref (each repo
keeps its own `?since` watermark, so a capped run resumes without skipping an unvisited repo's
history), a newest-first stream stopping its page walk at the repo's watermark and, once capped,
resuming its backfill downward by `{high, until}` window via `?until`, the Actions runs stream
bounding every pass at its pinned floor with `created=>=`, and a grant that cannot enumerate orgs at
all (`/user/orgs` → 403) surfacing as `StreamSkipped` on the root so the run records a skip, not a
failure."""

import json
import logging
import re
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers import github
from ufo_ext_sources.providers.github import GitHubConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources import backend as backend_module
from ufo.runtime.sources import connector as connector_module
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import (
    ConnectorBackend,
    ConnectorSourceConfig,
    ParentPages,
    ParentRecord,
    syncing_streams,
)

ParentsReader = Callable[[Mapping[str, tuple[ParentRecord, ...]]], ParentPages]

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


def _partitions_in(cursor: str) -> dict[str, Any]:
    """The partition entries of a cursor map, without the walk's own pass marks."""
    return {
        key: value
        for key, value in json.loads(cursor).items()
        if key not in connector_module.RESERVED_KEYS
    }


def _walked(repo_ref: str, path: str) -> str:
    """The cursor entry one repository's slice of a stream checkpoints under: the repository page it
    fanned out from and the collection it asked of it."""
    return f"{repo_ref}\n{path}"


ORG_REF = "organizations/1"
ORG_SCOPE = "acme"
REPO_REF = "repositories/organizations/1/100"
REPO_SCOPE = "acme/repo1"
REPO2_REF = "repositories/organizations/1/101"
REPO2_SCOPE = "acme/repo2"
ORGS = (ParentRecord(ref=ORG_REF, fields={"login": "acme"}),)
REPO1_FIELDS = {"owner.login": "acme", "name": "repo1", "full_name": "acme/repo1"}
REPO2_FIELDS = {"owner.login": "acme", "name": "repo2", "full_name": "acme/repo2"}
LANDED: Mapping[str, tuple[ParentRecord, ...]] = {
    "organizations": ORGS,
    "repositories": (ParentRecord(ref=REPO_REF, fields=REPO1_FIELDS),),
}
TWO_REPOS: Mapping[str, tuple[ParentRecord, ...]] = {
    "organizations": ORGS,
    "repositories": (
        ParentRecord(ref=REPO_REF, fields=REPO1_FIELDS),
        ParentRecord(ref=REPO2_REF, fields=REPO2_FIELDS),
    ),
}
ISSUE_DIGEST = "sha256:29f53c9e2b10df7dc649ec35f51e9e52db0aed27b96604b93a3bc2a90b10b851"
BRANCH_DIGEST = "sha256:ae3be10a8962d4eac9b2f0be783f8c3b55d2dd551e2c649a0af68699427ca68e"
USER_DIGEST = "sha256:6525259dfb667635649b1e81100e9c3ea18afef2aa5f574958a3986d4ce3ef12"
REPOSITORY_DIGEST = "sha256:6d5e002296abd110530827a131e5dfe2cc5764747e224c00c03274c25149afb9"
HEAD_SHA = "abc123"
SECOND_HEAD_SHA = "def456"


def _pull_parent(number: int, head_sha: str) -> ParentRecord:
    """One landed pull request as the check-run edge below it reads it: the fields its path
    renders, projected onto the page when it landed."""
    return ParentRecord(
        ref=f"pull_requests/{REPO_SCOPE}/{number}",
        fields={
            "repository.owner.login": "acme",
            "repository.name": "repo1",
            "headRefOid": head_sha,
        },
    )


ONE_PULL: Mapping[str, tuple[ParentRecord, ...]] = {
    **LANDED,
    "pull_requests": (_pull_parent(7, HEAD_SHA),),
}
TWO_PULLS: Mapping[str, tuple[ParentRecord, ...]] = {
    **LANDED,
    "pull_requests": (_pull_parent(7, HEAD_SHA), _pull_parent(8, SECOND_HEAD_SHA)),
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
    parents: ParentPages,
    cursor: str | None = None,
    backfill_after: datetime | None = None,
    watched: tuple[str, ...] = (),
) -> SyncResult:
    async def pinned() -> tuple[str, ...]:
        return watched

    auth = SourceAuth(
        workspace_id=uuid4(),
        auth_proxy=_MockProxy(handler),
        parents=parents,
        watched=pinned,
    )
    return await ConnectorBackend(connector=GitHubConnector()).fetch(
        ConnectorSourceConfig(stream=stream, backfill_after=backfill_after),
        cursor,
        auth,
    )


def _refs(result: SyncResult) -> set[str]:
    return {page.source_ref for page in result.pages}


def _commit(sha: str, date: str) -> dict[str, object]:
    return {"sha": sha, "commit": {"committer": {"date": date}}}


PULL_3122 = "https://github.com/acme/repo1/pull/3122"
PULL_3122_REF = "pull_requests/acme/repo1/3122"
PULL_3560 = "https://github.com/acme/repo1/pull/3560"
BIG_CHECKS = 44
BIG_THREADS = 16
BIG_FILES = 116
RESET_AT = "2026-09-12T23:00:00Z"
REVIEWED_AT = "2026-02-01T00:00:00Z"


def _pull_node(
    number: int,
    updated_at: str,
    *,
    checks: str = "SUCCESS",
    title: str | None = None,
    database_id: int | None = None,
    head_sha: str = HEAD_SHA,
) -> dict[str, Any]:
    return {
        "databaseId": number if database_id is None else database_id,
        "number": number,
        "title": title or f"pr {number}",
        "state": "OPEN",
        "isDraft": False,
        "reviewDecision": None,
        "updatedAt": updated_at,
        "createdAt": "2026-01-01T00:00:00Z",
        "author": {"login": "ada"},
        "headRefName": "work",
        "headRefOid": head_sha,
        "baseRefName": "main",
        "url": f"https://github.com/acme/repo1/pull/{number}",
        "repository": {"owner": {"login": "acme"}, "name": "repo1"},
        "commits": {
            "nodes": [
                {
                    "commit": {
                        "statusCheckRollup": {
                            "state": checks,
                            "contexts": {
                                "nodes": [
                                    {
                                        "name": "ci",
                                        "conclusion": checks,
                                        "detailsUrl": "https://github.com/acme/repo1/runs/1",
                                    }
                                ]
                            },
                        }
                    }
                }
            ]
        },
        "reviews": {
            "nodes": [
                {"author": {"login": "bob"}, "state": "COMMENTED", "submittedAt": REVIEWED_AT}
            ]
        },
        "reviewThreads": {
            "nodes": [
                {
                    "isResolved": False,
                    "comments": {
                        "nodes": [{"path": "a.py", "body": "why", "author": {"login": "bob"}}]
                    },
                }
            ]
        },
        "files": {"nodes": [{"path": "a.py", "additions": 3, "deletions": 1}]},
        "timelineItems": {"nodes": [{"__typename": "READY_FOR_REVIEW_EVENT", "id": "T1"}]},
    }


def _variables(request: httpx.Request) -> dict[str, Any]:
    return dict(json.loads(request.content)["variables"])


def _answer(
    repository: dict[str, Any] | None,
    *,
    cost: int = 3,
    remaining: int = 4_000,
) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "data": {
                "rateLimit": {"cost": cost, "remaining": remaining, "resetAt": RESET_AT},
                "repository": repository,
            }
        },
    )


def _pulls_page(nodes: list[dict[str, Any]], *, end_cursor: str | None = None) -> dict[str, Any]:
    return {
        "pullRequests": {
            "nodes": nodes,
            "pageInfo": {"hasNextPage": end_cursor is not None, "endCursor": end_cursor},
        }
    }


def _pulls(
    request: httpx.Request,
    nodes: list[dict[str, Any]],
    *,
    end_cursor: str | None = None,
    remaining: int = 4_000,
) -> httpx.Response:
    """GitHub's two answers over one set of pull requests: the index page the walk lists, and the
    pull request the by-number read asks for."""
    variables = _variables(request)
    if "number" in variables:
        node = next(node for node in nodes if node["number"] == variables["number"])
        return _answer({"pullRequest": node}, cost=1, remaining=remaining)
    return _answer(_pulls_page(nodes, end_cursor=end_cursor), cost=1, remaining=remaining)


async def test_a_pinned_floor_bounds_commits_server_side(parents_reader: ParentsReader) -> None:
    """`commits` is the one newest-first repo feed GitHub will bound for us: the row's pinned floor
    goes out as `?since`, so a repo with years of history never sends the older commits at all. It
    crosses as the ISO string GitHub stamps `commit.committer.date` with, which is the value space
    the walk compares against — any other shape would compare wrong and bound nothing."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/commits":
            queries.append(dict(request.url.params))
            return httpx.Response(200, json=[_commit("c1", "2026-07-20T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("commits", handle, parents=parents_reader(LANDED), backfill_after=pinned)

    assert queries[0]["since"] == "2026-07-08T12:00:00Z"
    assert "until" not in queries[0]


async def test_a_page_entirely_below_the_floor_stops_the_descent(
    parents_reader: ParentsReader,
) -> None:
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

    result = await _fetch("events", handle, parents=parents_reader(LANDED), backfill_after=pinned)

    assert len(calls) == 1  # stopped at the floor rather than walking the history down
    assert _refs(result) == set()


async def test_a_pinned_floor_drops_events_below_it_client_side(
    parents_reader: ParentsReader,
) -> None:
    """`events` and `issue_events` expose no time filter, so the floor cannot be pushed server-side
    and is applied to the records instead: everything below it is dropped rather than landed. That
    caps what the window admits even where it cannot cap what is fetched, and the walk stopping at
    the floor is what bounds the paging."""
    pinned = datetime(2026, 7, 8, 12, 0, tzinfo=UTC)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/events":
            return httpx.Response(
                200,
                json=[
                    {"id": "e-inside", "created_at": "2026-07-20T00:00:00Z"},
                    {"id": "e-below", "created_at": "2020-01-01T00:00:00Z"},
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("events", handle, parents=parents_reader(LANDED), backfill_after=pinned)

    assert _refs(result) == {f"events/{REPO_SCOPE}/e-inside"}


async def test_a_pull_request_carries_its_checks_reviews_threads_and_files(
    parents_reader: ParentsReader,
) -> None:
    """One read answers what a reviewer is woken about. GitHub's REST timeline carries no check-run
    or status event at all, so a pull request read over REST cannot see CI; the rollup, the reviews,
    the unresolved threads and the files come back with the pull request or the stream is blind to
    them. The cursor stays page metadata, so a pass that only moves `updatedAt` moves no digest."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            posts.append(_variables(request))
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z", checks="FAILURE")])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert posts == [
        {"owner": "acme", "name": "repo1", "cursor": None},
        {"owner": "acme", "name": "repo1", "number": 7},
    ]
    (page,) = result.pages
    body = json.loads(page.body.split("\n\n", 1)[1])
    assert body["checks"] == {
        "state": "FAILURE",
        "contexts": [
            {
                "name": "ci",
                "conclusion": "FAILURE",
                "detailsUrl": "https://github.com/acme/repo1/runs/1",
            }
        ],
    }
    assert body["reviews"] == [
        {"author": {"login": "bob"}, "state": "COMMENTED", "submittedAt": REVIEWED_AT}
    ]
    assert body["reviewThreads"] == [
        {
            "isResolved": False,
            "comments": [{"path": "a.py", "body": "why", "author": {"login": "bob"}}],
        }
    ]
    assert body["files"] == [{"path": "a.py", "additions": 3, "deletions": 1}]
    assert body["timelineItems"] == [{"__typename": "READY_FOR_REVIEW_EVENT", "id": "T1"}]
    assert "updatedAt" not in body
    assert page.updated_at == "2026-09-02T00:00:00.000000+00:00"
    assert page.created_at == "2026-01-01T00:00:00.000000+00:00"


async def test_the_pull_request_read_never_asks_for_mergeability(
    parents_reader: ParentsReader,
) -> None:
    queries: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            queries.append(json.loads(request.content)["query"])
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert queries
    assert all("mergeable" not in query for query in queries)


async def test_the_rollup_and_the_check_run_pages_are_different_pages(
    parents_reader: ParentsReader,
) -> None:
    """Two readings of the same CI, each keeping its own rows. `statusCheckRollup` is the pull
    request's own summary of its checks and rides its page; a check run is a record under the head
    commit, addressed by the id GitHub gave it. The pull request's page carries the fields the
    check-run edge below it renders, so the run is read off the landed page rather than by walking
    the repo's pull requests again, and neither page lands under the other's address."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z", checks="FAILURE")])
        if request.url.path == f"/repos/acme/repo1/commits/{HEAD_SHA}/check-runs":
            return httpx.Response(
                200, json={"total_count": 1, "check_runs": [{"id": 55, "name": "ci"}]}
            )
        return httpx.Response(404, json={"path": request.url.path})

    pulls = await _fetch("pull_requests", handle, parents=parents_reader(LANDED))
    runs = await _fetch("check_runs", handle, parents=parents_reader(ONE_PULL))

    assert _refs(pulls) == {f"pull_requests/{REPO_SCOPE}/7"}
    assert _refs(runs) == {"check_runs/55"}
    assert json.loads(pulls.pages[0].body.split("\n\n", 1)[1])["checks"]["state"] == "FAILURE"
    assert pulls.pages[0].parent_fields == {
        "headRefOid": HEAD_SHA,
        "repository.name": "repo1",
        "repository.owner.login": "acme",
    }


async def test_two_pull_requests_check_runs_land_under_their_own_heads(
    parents_reader: ParentsReader,
) -> None:
    """A check run is published under a commit and nowhere else, so the edge reads the head ref off
    the pull request that carries it and asks GitHub at the path it publishes them at — the
    declaration renders the request and there is nothing beside it to drift from.

    Two pull requests are two heads, two reads and two sets of runs, and the sets stay apart: a
    check-run id is GitHub's across the account, so the stream keys `global` and a page is addressed
    by that id alone, with no repository scope to make one run two pages under two pull requests
    that share a head."""
    asked: list[str] = []
    runs = {f"/repos/acme/repo1/commits/{HEAD_SHA}/check-runs": (55, 7)}
    runs[f"/repos/acme/repo1/commits/{SECOND_HEAD_SHA}/check-runs"] = (66, 8)

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path not in runs:
            return httpx.Response(404, json={"path": request.url.path})
        run, number = runs[request.url.path]
        return httpx.Response(
            200,
            json={
                "total_count": 1,
                "check_runs": [
                    {
                        "id": run,
                        "name": "test",
                        "status": "completed",
                        "conclusion": "failure",
                        "started_at": "2026-09-12T21:22:30Z",
                        "pull_requests": [
                            {
                                "number": number,
                                "url": f"https://api.github.com/repos/acme/repo1/pulls/{number}",
                            }
                        ],
                    }
                ],
            },
        )

    result = await _fetch("check_runs", handle, parents=parents_reader(TWO_PULLS))

    [edge] = next(spec for spec in GitHubConnector().streams() if spec.name == "check_runs").parents
    assert asked == [
        edge.partition(parent, "check_runs").path for parent in TWO_PULLS["pull_requests"]
    ]
    assert _refs(result) == {"check_runs/55", "check_runs/66"}
    assert {page.source_identity for page in result.pages} == {"check_runs/55", "check_runs/66"}
    assert {
        page.source_identity: json.loads(page.body.split("\n\n", 1)[1])["pull_requests"][0][
            "number"
        ]
        for page in result.pages
    } == {"check_runs/55": 7, "check_runs/66": 8}


async def test_a_check_run_moves_the_digest_and_the_cursor_alone_does_not(
    parents_reader: ParentsReader,
) -> None:
    """The whole point of the read: a rollup flipping to `FAILURE` is a new revision of the page,
    and a pull request whose only movement is its own `updatedAt` is not."""

    def handler(node: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                return _pulls(request, [node])
            return httpx.Response(404, json={"path": request.url.path})

        return handle

    first = await _fetch(
        "pull_requests",
        handler(_pull_node(7, "2026-09-02T00:00:00Z")),
        parents=parents_reader(LANDED),
    )
    later = await _fetch(
        "pull_requests",
        handler(_pull_node(7, "2026-09-03T00:00:00Z")),
        parents=parents_reader(LANDED),
    )
    failed = await _fetch(
        "pull_requests",
        handler(_pull_node(7, "2026-09-02T00:00:00Z", checks="FAILURE")),
        parents=parents_reader(LANDED),
    )

    assert first.pages[0].digest == later.pages[0].digest
    assert first.pages[0].digest != failed.pages[0].digest


async def test_pull_requests_walk_newest_first_and_stop_at_the_repo_watermark(
    parents_reader: ParentsReader,
) -> None:
    """The walk descends `UPDATED_AT` — the order its cursor is in — and a steady-state pass stops
    on the first index page sitting strictly below the repo's watermark, so re-landing the moved
    pull requests costs one index page and one read per moved pull request, not the repository:
    the pull request below the watermark is listed and never read."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            variables = _variables(request)
            posts.append(variables)
            if "number" in variables:
                return _pulls(request, [_pull_node(2, "2026-09-02T00:00:00Z")])
            if variables["cursor"] is None:
                return _answer(
                    _pulls_page([_pull_node(2, "2026-09-02T00:00:00Z")], end_cursor="Y3Vy")
                )
            return _answer(_pulls_page([_pull_node(1, "2026-08-01T00:00:00Z")]))
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "pull_requests",
        handle,
        parents=parents_reader(LANDED),
        cursor=json.dumps({_walked(REPO_REF, "/repos/acme/repo1/pulls"): "2026-09-01T00:00:00Z"}),
    )

    assert [(post.get("cursor"), post.get("number")) for post in posts] == [
        (None, None),
        (None, 2),
        ("Y3Vy", None),
    ]
    assert _refs(result) == {f"pull_requests/{REPO_SCOPE}/2"}
    assert _partitions_in(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/pulls"): "2026-09-02T00:00:00Z"
    }


async def test_a_watched_pull_request_is_read_before_the_repo_walk(
    parents_reader: ParentsReader,
) -> None:
    """A live trigger on a pull request URL pins that one partition, and it is read first: the
    member asked to be told about it, so it waits behind no part of the catalog walk."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            variables = _variables(request)
            posts.append(variables)
            if variables.get("number") == 3122:
                return _answer({"pullRequest": _pull_node(3122, "2026-01-05T00:00:00Z")})
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "pull_requests", handle, watched=(PULL_3122,), parents=parents_reader(LANDED)
    )

    assert [post.get("number") for post in posts] == [3122, None, 7]
    assert _refs(result) == {PULL_3122_REF, f"pull_requests/{REPO_SCOPE}/7"}


async def test_a_watch_is_addressed_under_the_repository_the_catalog_spells(
    parents_reader: ParentsReader,
) -> None:
    """GitHub answers one repository under every spelling of its name, so a watch canonicalizes to
    the lower-cased one. The page it lands must still be the page the repository's own walk lands:
    taking the URL's spelling would file `Acme/Repo1`'s pull request at a second address, and both
    copies would then move on their own. A repository the connection does not sync pins nothing."""
    landed: Mapping[str, tuple[ParentRecord, ...]] = {
        "organizations": ORGS,
        "repositories": (
            ParentRecord(
                ref=REPO_REF,
                fields={"owner.login": "Acme", "name": "Repo1", "full_name": "Acme/Repo1"},
            ),
        ),
    }
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            variables = _variables(request)
            asked.append(f"{variables['owner']}/{variables['name']}")
            if "number" in variables:
                return _answer({"pullRequest": _pull_node(3122, "2026-01-05T00:00:00Z")})
            return _answer(_pulls_page([]))
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "pull_requests",
        handle,
        parents=parents_reader(landed),
        watched=(PULL_3122, "https://github.com/other/elsewhere/pull/9"),
    )

    assert asked == ["Acme/Repo1", "Acme/Repo1"]
    assert _refs(result) == {"pull_requests/Acme/Repo1/3122"}


async def test_both_paths_to_one_pull_request_land_one_page(
    parents_reader: ParentsReader,
) -> None:
    """The watch pins a partition by repository and number; the page it lands is addressed by the
    record's `databaseId`, which is what the repository's own walk addresses it by too. So the tick
    that reads a pull request twice — once because it is watched, once because it is new enough for
    the walk — lands one page and not two, whichever path fetched it."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            node = _pull_node(3122, "2026-09-02T00:00:00Z", database_id=4515114744)
            if "number" in _variables(request):
                return _answer({"pullRequest": node})
            return _answer(_pulls_page([node]))
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "pull_requests", handle, parents=parents_reader(LANDED), watched=(PULL_3122,)
    )

    assert _refs(result) == {"pull_requests/acme/repo1/4515114744"}
    assert {page.source_identity for page in result.pages} == {
        "pull_requests/acme/repo1/4515114744"
    }


async def test_the_walk_reads_the_index_then_each_listed_pull_request_by_number(
    parents_reader: ParentsReader,
) -> None:
    """The cost of the walk when nobody is watching: one index page a repository and one read per
    pull request it lists inside the bound — the by-number read is the only read that builds a
    body, so the walk spends it too rather than landing the index's clipped rows."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            posts.append(_variables(request))
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert [post.get("number") for post in posts] == [None, 7]


async def test_a_watched_pull_request_lands_though_the_repo_walk_stops_short_of_it(
    parents_reader: ParentsReader,
) -> None:
    """A check run flips without touching the pull request's own `updatedAt`, so a repo walk ordered
    by that field would stop at its watermark and never reach an old pull request again. The watched
    partition keeps its own cursor entry and is read by number, so the page lands every tick and its
    revision moves when the rollup does — the ordered walk's tie rule is what keeps it landing."""
    cursor = json.dumps(
        {
            _walked(REPO_REF, "/repos/acme/repo1/pulls"): "2026-09-01T00:00:00Z",
            PULL_3122_REF: "2026-01-05T00:00:00Z",
        }
    )

    def handler(checks: str) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                if "number" in _variables(request):
                    return _answer(
                        {"pullRequest": _pull_node(3122, "2026-01-05T00:00:00Z", checks=checks)}
                    )
                return _answer(_pulls_page([_pull_node(1, "2026-08-01T00:00:00Z")]))
            return httpx.Response(404, json={"path": request.url.path})

        return handle

    pending = await _fetch(
        "pull_requests",
        handler("PENDING"),
        cursor=cursor,
        watched=(PULL_3122,),
        parents=parents_reader(LANDED),
    )
    failure = await _fetch(
        "pull_requests",
        handler("FAILURE"),
        cursor=cursor,
        watched=(PULL_3122,),
        parents=parents_reader(LANDED),
    )

    assert _refs(pending) == {PULL_3122_REF}
    assert json.loads(pending.next_cursor)[PULL_3122_REF] == "2026-01-05T00:00:00Z"
    assert pending.pages[0].digest != failure.pages[0].digest


async def test_dropping_the_watch_drops_its_cursor_entry(
    parents_reader: ParentsReader,
) -> None:
    """Deleting the trigger unpins the partition and nothing else happens: the completed pass
    enumerates it no more, and the walk's own prune takes its watermark with it."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            return _answer(_pulls_page([_pull_node(1, "2026-08-01T00:00:00Z")]))
        return httpx.Response(404, json={"path": request.url.path})

    cursor = json.dumps(
        {
            _walked(REPO_REF, "/repos/acme/repo1/pulls"): "2026-09-01T00:00:00Z",
            PULL_3122_REF: "2026-01-05T00:00:00Z",
        }
    )
    result = await _fetch("pull_requests", handle, cursor=cursor, parents=parents_reader(LANDED))

    assert _partitions_in(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/pulls"): "2026-09-01T00:00:00Z"
    }
    assert connector_module.PASS_AT_KEY in json.loads(result.next_cursor)


async def test_every_graphql_read_records_what_it_cost(
    parents_reader: ParentsReader,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The spend of a connection is readable per read rather than inferred from a refusal: each
    answer carries its own price and each is recorded as it arrives, the watched read apart from the
    paged one."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            if _variables(request).get("number") == 3122:
                return _answer(
                    {"pullRequest": _pull_node(3122, "2026-01-05T00:00:00Z")},
                    cost=1,
                    remaining=4999,
                )
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")], remaining=4998)
        return httpx.Response(404, json={"path": request.url.path})

    with caplog.at_level(logging.INFO, logger="ufo"):
        await _fetch("pull_requests", handle, parents=parents_reader(LANDED), watched=(PULL_3122,))

    assert [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "source_sync.graphql_rate_limit"
    ] == [
        {
            "stream": "pull_requests",
            "cost": "1",
            "remaining": "4999",
            "reset_at": RESET_AT,
            "watched": "true",
        },
        {
            "stream": "pull_requests",
            "cost": "1",
            "remaining": "4998",
            "reset_at": RESET_AT,
            "watched": "false",
        },
        {
            "stream": "pull_requests",
            "cost": "1",
            "remaining": "4998",
            "reset_at": RESET_AT,
            "watched": "false",
        },
    ]


async def test_the_walk_stops_before_a_read_the_point_budget_cannot_pay_for(
    parents_reader: ParentsReader,
) -> None:
    """Every read is priced against an hourly 5,000 the whole connection shares, so a repository
    with pull requests to page through can spend it. The walk reads what the answer reports and
    stops at its last checkpoint rather than earning the refusal, which GraphQL serves as a 200
    carrying no records at all: the pull request already read whole lands, its window is stored,
    and the next index page waits for the refill."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            posts.append(_variables(request))
            if "number" in _variables(request):
                return _pulls(request, [_pull_node(2, "2026-09-02T00:00:00Z")], remaining=0)
            return _answer(
                _pulls_page([_pull_node(2, "2026-09-02T00:00:00Z")], end_cursor="Y3Vy"), cost=1
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert [post.get("number") for post in posts] == [None, 2]
    assert result.retry_after_seconds > 0
    assert _refs(result) == {f"pull_requests/{REPO_SCOPE}/2"}
    assert _partitions_in(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/pulls"): {
            "high": "2026-09-02T00:00:00Z",
            "until": "2026-09-02T00:00:00Z",
        }
    }


async def test_a_budget_met_between_two_pull_requests_lands_the_whole_ones_and_resumes_at_the_last(
    parents_reader: ParentsReader,
) -> None:
    """An index page lists more pull requests than the budget pays to read whole. The ones already
    read land, the page's low is the last of them rather than the page's oldest, so the window
    resumes at it inclusive and the next run reads on downward from there; the ones past it are not
    asked for at all."""
    posts: list[dict[str, Any]] = []
    nodes = [
        _pull_node(3, "2026-09-03T00:00:00Z"),
        _pull_node(2, "2026-09-02T00:00:00Z"),
        _pull_node(1, "2026-09-01T00:00:00Z"),
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            posts.append(_variables(request))
            remaining = 0 if _variables(request).get("number") == 2 else 4_000
            return _pulls(request, nodes, remaining=remaining)
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert [post.get("number") for post in posts] == [None, 3, 2]
    assert result.retry_after_seconds > 0
    assert _refs(result) == {f"pull_requests/{REPO_SCOPE}/3", f"pull_requests/{REPO_SCOPE}/2"}
    assert _partitions_in(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/pulls"): {
            "high": "2026-09-03T00:00:00Z",
            "until": "2026-09-02T00:00:00Z",
        }
    }


async def test_a_repeated_page_cursor_fails_rather_than_spins(
    parents_reader: ParentsReader,
) -> None:
    """A page whose records all sit below the resume bound lands nothing, so the adapter's record
    cap counts nothing and never trips. A provider that keeps saying there is a next page would
    then spin this walk forever, holding the row's claim and committing nothing — so a cursor the
    walk has already followed fails the run instead, before the page's pull requests are read."""
    posts: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            posts.append(_variables(request))
            return _pulls(request, [_pull_node(2, "2020-01-01T00:00:00Z")], end_cursor="Y3Vy")
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(StreamFault, match="repeated cursor"):
        await _fetch("pull_requests", handle, parents=parents_reader(LANDED))

    assert [post.get("number") for post in posts] == [None, 2, None]


async def test_a_graphql_error_about_one_repository_drops_only_that_partition(
    parents_reader: ParentsReader,
) -> None:
    """A repository deleted, renamed, or behind a SAML session this grant has not authorized comes
    back as HTTP 200 with a `NOT_FOUND` or `FORBIDDEN` entry in `errors` — the same fact REST spells
    403/404, and the REST walk dropped that partition and kept going. Failing the run instead would
    commit no page and advance no cursor for any repository of the connection, including the watched
    pull requests read first, and the catalog would re-enumerate the dead repository every tick."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            if _variables(request)["name"] == "repo1":
                return httpx.Response(
                    200,
                    json={"errors": [{"type": "FORBIDDEN", "message": "Resource not accessible"}]},
                )
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("pull_requests", handle, parents=parents_reader(TWO_REPOS))

    assert _refs(result) == {f"pull_requests/{REPO2_SCOPE}/7"}


async def test_a_graphql_error_about_the_query_fails_the_run_naming_what_it_asked(
    parents_reader: ParentsReader,
) -> None:
    """A refusal that is not about one repository — a field GitHub withdrew, a malformed query — is
    wrong for every partition, so it fails the run with the provider's own reason. Dropping it per
    repository would leave a stream that reads as quiet forever."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            return httpx.Response(
                200,
                json={"errors": [{"message": "Field 'headRefOid' doesn't exist"}]},
            )
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(StreamFault, match="Field 'headRefOid' doesn't exist"):
        await _fetch("pull_requests", handle, parents=parents_reader(LANDED))


async def test_a_repository_graphql_cannot_read_skips_that_partition(
    parents_reader: ParentsReader,
) -> None:
    """A repository the grant lists but cannot read into answers a null `repository` rather than a
    status code, which is the same fact REST spells 403/404: the partition drops out and the rest of
    the walk runs."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            if _variables(request)["name"] == "repo1":
                return _answer(None)
            return _pulls(request, [_pull_node(7, "2026-09-02T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("pull_requests", handle, parents=parents_reader(TWO_REPOS))

    assert _refs(result) == {f"pull_requests/{REPO2_SCOPE}/7"}


async def test_a_watch_reaches_the_pull_request_walk_and_no_other_streams(
    parents_reader: ParentsReader,
) -> None:
    """A watch names a pull request, and `/repos/{owner}/{repo}/pulls/3122` answers one pull request
    object. Put at the head of any other stream's walk it would be read as that stream's collection,
    land nothing, and cost a read a tick per stream per watched pull request — so the rule is
    answered for `pull_requests` and for no other stream."""
    asked: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        if request.url.path == "/graphql":
            return _answer(_pulls_page([]))
        return httpx.Response(200, json=[])

    await _fetch("issues", handle, parents=parents_reader(LANDED), watched=(PULL_3122,))
    await _fetch("branches", handle, parents=parents_reader(LANDED), watched=(PULL_3122,))

    assert asked == ["/repos/acme/repo1/issues", "/repos/acme/repo1/branches"]


async def test_a_first_walk_of_three_pull_requests_costs_four_reads_and_a_watch_one_more(
    parents_reader: ParentsReader,
) -> None:
    """The measurement this unit is bounded by, taken at the transport. GitHub priced one index
    page and one pull request by number at 1 point each against `metalcraftai/ufo` (3,560 pull
    requests, 2026-09-14); the request counts here are what those multiply."""
    counted: list[dict[str, Any]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/graphql":
            variables = _variables(request)
            counted.append(variables)
            if variables.get("number") == 3122:
                return _answer({"pullRequest": _pull_node(3122, "2026-01-05T00:00:00Z")}, cost=1)
            return _pulls(
                request,
                [
                    _pull_node(3, "2026-09-03T00:00:00Z"),
                    _pull_node(2, "2026-09-02T00:00:00Z"),
                    _pull_node(1, "2026-09-01T00:00:00Z"),
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("pull_requests", handle, parents=parents_reader(LANDED))
    unwatched = len(counted)
    counted.clear()
    await _fetch("pull_requests", handle, watched=(PULL_3122,), parents=parents_reader(LANDED))

    assert unwatched == 4
    assert len(counted) == 5


async def test_review_comments_climb_from_the_floor_on_a_first_pass(
    parents_reader: ParentsReader,
) -> None:
    """`/pulls/comments` answers `?since`, so a floor on this stream is a bound the API keeps: a
    first pass climbs from it and the older comments are never fetched. The window is the row's
    only cutoff — the walk itself climbs and cannot overshoot one."""
    pinned = datetime(2026, 9, 1, tzinfo=UTC)
    queries: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/pulls/comments":
            queries.append(dict(request.url.params))
            return httpx.Response(
                200, json=[{"id": 9, "body": "nit", "updated_at": "2026-09-06T00:00:00Z"}]
            )
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch(
        "review_comments", handle, parents=parents_reader(LANDED), backfill_after=pinned
    )

    assert queries[0]["since"] == "2026-09-01T00:00:00Z"
    assert queries[0]["sort"] == "updated"
    assert queries[0]["direction"] == "asc"
    assert _refs(result) == {f"review_comments/{REPO_SCOPE}/9"}
    assert json.loads(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/pulls/comments"): "2026-09-06T00:00:00Z"
    }


async def test_repositories_fan_out_over_the_landed_organizations(
    parents_reader: ParentsReader,
) -> None:
    """The repo catalog is a child of `organizations`: one request per landed org, and the archived
    repositories and forks are left out of what lands. The page carries the `owner.login` and `name`
    its 21 children template their paths from and the `full_name` they carry onto their records, so
    nothing below it walks this collection again. The run is a snapshot: the whole listing, swept
    against as authoritative."""
    archived = {**REPO, "id": 101, "full_name": "acme/old", "archived": True}

    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO, archived])
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("repositories", handle, parents=parents_reader(LANDED))
    assert result.snapshot is True
    assert result.deletes == ()
    assert _refs(result) == {f"repositories/{ORG_SCOPE}/100"}
    assert result.pages[0].parent_fields == {
        "full_name": "acme/repo1",
        "name": "repo1",
        "owner.login": "acme",
    }
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
        if request.url.path == "/repos/acme/repo1/issues":
            assert request.url.params.get("state") == "all"
            assert request.url.params.get("sort") == "updated"
            assert request.url.params.get("direction") == "asc"
            seen.append(request.url.params.get("since") or "")
            return httpx.Response(200, json=[issue, pull])
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_commits_backfill_windows_and_resumes_downward_with_until(
    parents_reader: ParentsReader,
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

    first = await _fetch("commits", handle, parents=parents_reader(LANDED))
    assert _refs(first) == {f"commits/{REPO_SCOPE}/c1", f"commits/{REPO_SCOPE}/c2"}
    assert json.loads(first.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/commits"): {
            "high": "2026-03-03T00:00:00Z",
            "until": "2026-03-02T00:00:00Z",
        }
    }

    second = await _fetch(
        "commits", handle, parents=parents_reader(LANDED), cursor=first.next_cursor
    )
    assert seen_until[-1] == "2026-03-02T00:00:00Z"
    assert _refs(second) == {f"commits/{REPO_SCOPE}/c2", f"commits/{REPO_SCOPE}/c3"}
    assert json.loads(second.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/commits"): "2026-03-03T00:00:00Z"
    }


@pytest.mark.parametrize("stream_name", ["events", "issue_events"])
async def test_events_backfill_filters_client_side_without_a_time_param(
    parents_reader: ParentsReader, stream_name: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Neither `events` nor `issue_events` has a server-side `until`, so a backfill resume walks
    from the newest each slice and the walk drops records above `until` client-side (inclusive
    `<=`), landing only the unsynced tail — no request carries a time bound."""
    monkeypatch.setattr(backend_module, "MAX_RECORDS_PER_RUN", 1)
    above = {"id": 902, "created_at": "2026-02-04T00:00:00Z"}
    boundary = {"id": 901, "created_at": "2026-02-03T00:00:00Z"}
    params_seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/repos/acme/repo1/{path_part}":
            params_seen.append(dict(request.url.params))
            return httpx.Response(200, json=[above, boundary])
        return httpx.Response(404, json={"path": request.url.path})

    path_part = "events" if stream_name == "events" else "issues/events"
    cursor = json.dumps(
        {
            _walked(REPO_REF, f"/repos/acme/repo1/{path_part}"): {
                "high": "2026-02-05T00:00:00Z",
                "until": "2026-02-03T00:00:00Z",
            }
        },
        sort_keys=True,
    )
    result = await _fetch(stream_name, handle, parents=parents_reader(LANDED), cursor=cursor)
    assert all("until" not in params and "since" not in params for params in params_seen)
    assert _refs(result) == {f"{stream_name}/{REPO_SCOPE}/901"}


async def test_org_enumeration_refusal_raises_stream_skipped(parents_reader: ParentsReader) -> None:
    """The root of the tree is where a missing org scope shows: `organizations` records the skip,
    and every stream below it finds no partition rather than failing a run of its own."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(403, json={"message": "SAML enforcement"})
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(StreamSkipped, match="org scope"):
        await _fetch("organizations", handle, parents=parents_reader(LANDED))


async def test_a_stream_whose_parent_landed_nothing_spends_no_request(
    parents_reader: ParentsReader,
) -> None:
    """A row registered before its parent's first pass, or one whose grant lost the org, enumerates
    no partition. It lands nothing and asks nothing — never a guess at a flat collection."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("branches", handle, parents=parents_reader({}))

    assert calls == []
    assert _refs(result) == set()


async def test_branches_walk_the_default_none_path_per_repo(parents_reader: ParentsReader) -> None:
    """The default `Ordering.none` streams — the majority of the catalog — ride the partition walk
    through the real connector: every repo's collection lands, and a completed pass leaves no
    markers behind, so the next run re-walks in full. A branch name is unique only inside its repo,
    so the repository's `full_name` is part of each page's address and every repo's `main` lands as
    its own page instead of rewriting one shared page every sync — and the body and title read the
    scoped name, `acme/repo1/main`, as every landed page does."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/branches":
            return httpx.Response(200, json=[{"name": "main"}])
        if request.url.path == "/repos/acme/repo2/branches":
            return httpx.Response(200, json=[{"name": "main"}, {"name": "dev"}])
        return httpx.Response(404, json={"path": request.url.path})

    expected = {
        f"branches/{REPO_SCOPE}/main",
        f"branches/{REPO2_SCOPE}/main",
        f"branches/{REPO2_SCOPE}/dev",
    }
    result = await _fetch("branches", handle, parents=parents_reader(TWO_REPOS))
    assert _refs(result) == expected
    assert result.snapshot is False
    bodies = {page.source_ref: json.loads(page.body.split("\n\n", 1)[1]) for page in result.pages}
    assert bodies[f"branches/{REPO_SCOPE}/main"]["name"] == f"{REPO_SCOPE}/main"
    assert bodies[f"branches/{REPO_SCOPE}/main"]["repo_full_name"] == REPO_SCOPE
    assert {page.title for page in result.pages} == {
        f"{REPO_SCOPE}/main",
        f"{REPO2_SCOPE}/main",
        f"{REPO2_SCOPE}/dev",
    }
    rerun = await _fetch(
        "branches", handle, parents=parents_reader(TWO_REPOS), cursor=result.next_cursor
    )
    assert _refs(rerun) == expected


def _contributor(total: int, commits: int) -> dict[str, object]:
    return {
        "author": {"id": 42, "login": "ada"},
        "total": total,
        "weeks": [{"w": 1767225600, "a": 10, "d": 2, "c": commits}],
    }


async def test_contributor_activity_keys_on_the_author_id_not_the_commit_counts(
    parents_reader: ParentsReader,
) -> None:
    counts = iter((3, 9))

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in {
            "/repos/acme/repo1/stats/contributors",
            "/repos/acme/repo2/stats/contributors",
        }:
            total = next(counts)
            return httpx.Response(200, json=[_contributor(total, total)])
        return httpx.Response(404, json={"path": request.url.path})

    identities = {
        f"contributor_activity/{REPO_SCOPE}/42",
        f"contributor_activity/{REPO2_SCOPE}/42",
    }
    result = await _fetch("contributor_activity", handle, parents=parents_reader(TWO_REPOS))
    assert {page.source_identity for page in result.pages} == identities
    refs = _refs(result)
    counts = iter((4, 10))
    rerun = await _fetch("contributor_activity", handle, parents=parents_reader(TWO_REPOS))
    assert {page.source_identity for page in rerun.pages} == identities
    assert _refs(rerun).isdisjoint(refs)


async def test_a_contributor_with_no_author_is_dropped_and_named(
    caplog: pytest.LogCaptureFixture,
    parents_reader: ParentsReader,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/stats/contributors":
            return httpx.Response(
                200, json=[{"author": None, "total": 1, "weeks": []}, _contributor(2, 2)]
            )
        return httpx.Response(404, json={"path": request.url.path})

    with caplog.at_level(logging.WARNING, logger="ufo"):
        result = await _fetch("contributor_activity", handle, parents=parents_reader(LANDED))

    assert {page.source_identity for page in result.pages} == {
        f"contributor_activity/{REPO_SCOPE}/42"
    }
    assert result.dropped == 1
    assert [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "source_sync.unkeyed_record"
    ] == [{"connector": "github", "stream": "contributor_activity", "primary_key": "author.id"}]


async def test_repo_refusal_mid_walk_skips_only_that_repo(parents_reader: ParentsReader) -> None:
    """A repo gone 404 mid-walk raises `PartitionSkipped` inside its page factory: the walk keeps
    the repo's stored state untouched — its mid-backfill window survives to resume, where a silent
    end would have dissolved it — and its neighbors still sync."""
    fresh = _commit("c9", "2026-03-05T00:00:00Z")

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/commits":
            return httpx.Response(404, json={"message": "Not Found"})
        if request.url.path == "/repos/acme/repo2/commits":
            return httpx.Response(200, json=[fresh])
        return httpx.Response(404, json={"path": request.url.path})

    cursor = json.dumps(
        {
            _walked(REPO_REF, "/repos/acme/repo1/commits"): {
                "high": "2026-03-04T00:00:00Z",
                "until": "2026-03-01T00:00:00Z",
            },
            _walked(REPO2_REF, "/repos/acme/repo2/commits"): "2026-03-02T00:00:00Z",
        },
        sort_keys=True,
    )
    result = await _fetch("commits", handle, parents=parents_reader(TWO_REPOS), cursor=cursor)
    assert _refs(result) == {f"commits/{REPO2_SCOPE}/c9"}
    assert json.loads(result.next_cursor) == {
        _walked(REPO_REF, "/repos/acme/repo1/commits"): {
            "high": "2026-03-04T00:00:00Z",
            "until": "2026-03-01T00:00:00Z",
        },
        _walked(REPO2_REF, "/repos/acme/repo2/commits"): "2026-03-05T00:00:00Z",
    }


async def test_org_scoped_streams_are_qualified_by_the_org_they_were_walked_from(
    parents_reader: ParentsReader,
) -> None:
    """A member of two granted orgs is one GitHub user id but two org-membership records, so the
    parent org page ref is part of each page's address rather than collapsing both onto one page."""
    beta_ref = "organizations/2"
    landed = {
        "organizations": (*ORGS, ParentRecord(ref=beta_ref, fields={"login": "beta"})),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path in {"/orgs/acme/members", "/orgs/beta/members"}:
            return httpx.Response(200, json=[{"id": 42, "login": "ada"}])
        if request.url.path == "/users/ada":
            return httpx.Response(200, json={"id": 42, "login": "ada", "name": "Ada"})
        return httpx.Response(404, json={"path": request.url.path})

    result = await _fetch("users", handle, parents=parents_reader(landed))
    assert _refs(result) == {f"users/{ORG_SCOPE}/42", "users/beta/42"}


async def test_the_actions_api_lands_its_records_from_a_counted_envelope(
    parents_reader: ParentsReader,
) -> None:
    """`/actions/runs` is the one GitHub collection that arrives wrapped — `{total_count,
    workflow_runs}` rather than the bare array every other stream answers with — so the walk reads
    the records out of the envelope. Reading the body as an array instead lands nothing while the
    link header still advances: no error, no refusal, and the page cap that ends a run counts landed
    records, so a run that lands none of them never reaches it and walks the whole history."""
    pages: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
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

    result = await _fetch("workflow_runs", handle, parents=parents_reader(LANDED))

    assert _refs(result) == {f"workflow_runs/{REPO_SCOPE}/11", f"workflow_runs/{REPO_SCOPE}/12"}
    assert len(pages) == 2


async def test_a_pinned_floor_bounds_workflow_runs_server_side(
    parents_reader: ParentsReader,
) -> None:
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

    result = await _fetch(
        "workflow_runs", handle, parents=parents_reader(LANDED), backfill_after=pinned
    )
    await _fetch("deployments", handle, parents=parents_reader(LANDED), backfill_after=pinned)

    spec = next(s for s in GitHubConnector().streams() if s.name == "workflow_runs")
    assert spec.backfill_window_days == 0
    assert runs[0]["created"] == ">=2026-09-08T04:45:00Z"
    assert "created" not in deployments[0]
    assert _refs(result) == {f"workflow_runs/{REPO_SCOPE}/11"}


async def test_workflows_lands_its_records_from_its_own_envelope_key(
    parents_reader: ParentsReader,
) -> None:
    """The second Actions collection wraps its items under `workflows`, not the `workflow_runs` key
    its sibling uses, so the record path is the stream's own rather than one spelling per API."""

    def handle(request: httpx.Request) -> httpx.Response:
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

    result = await _fetch("workflows", handle, parents=parents_reader(LANDED))

    assert _refs(result) == {f"workflows/{REPO_SCOPE}/21"}


def test_the_streams_a_run_of_the_machine_writes_about_itself_do_not_reach_memory() -> None:
    """Actions runs, check runs, star events and contributor tallies state what GitHub states
    again on demand; their pages land for triggers and `object_get` and derive no chunks and no
    facts. `workflows` is the list of workflow definitions, configuration a member may want, and
    stays indexed."""
    unindexed = {spec.name for spec in GitHubConnector().streams() if not spec.indexed}
    assert unindexed == {
        "check_runs",
        "contributor_activity",
        "stargazers",
        "workflow_runs",
    }


async def test_a_page_keeps_the_address_the_repo_scoped_key_gave_it(
    parents_reader: ParentsReader,
) -> None:
    """The deploy safety of the whole change. A page is addressed by the values its edge's path
    reads off the parent — `full_name` under a repository, `login` under an organization — which is
    what the repo-scoped primary key spelled out by hand before the tree, so every page of a
    connection that already syncs settles on the row it already has. Any other scope restamps every
    GitHub page in the workspace at once and re-derives all of them.

    The body holds the same way. A page is stored by the digest of its rendered body, and main
    stamped every fanned-out record with its partition (`repo_full_name`, `org_login`) and scoped
    its primary key to it (`acme/repo1/3122`) before rendering — so a body that renders differently
    rewrites every blob, bumps every revision and replays the whole corpus through `page_change` on
    the first pass after deploy, re-chunking and re-embedding pages the provider never changed. The
    four digests are main's, measured at `77185fc8d` by running that revision's `flatten` and
    `render` (`git show 77185fc8d:extensions/sources/ufo_ext_sources/providers/github.py`) over
    these same records stamped the way its walks stamped them. `pull_requests` pins none: it is read
    over GraphQL and its body is a different record by design.

    The expected strings are written out here rather than read off the connector, because a rule
    that computes both sides proves nothing about the addresses already in the table."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/issues":
            return httpx.Response(
                200, json=[{"id": 3122, "title": "Bug", "updated_at": "2026-02-04T00:00:00Z"}]
            )
        if request.url.path == "/orgs/acme/members":
            return httpx.Response(200, json=[{"id": 42, "login": "ada"}])
        if request.url.path == "/users/ada":
            return httpx.Response(200, json={"id": 42, "login": "ada", "name": "Ada"})
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/branches":
            return httpx.Response(200, json=[{"name": "main"}])
        if request.url.path == "/graphql":
            return _pulls(
                request, [_pull_node(3122, "2026-02-04T00:00:00Z", database_id=4515114744)]
            )
        return httpx.Response(404, json={"path": request.url.path})

    under_repo = (await _fetch("issues", handle, parents=parents_reader(LANDED))).pages[0]
    under_org = (await _fetch("users", handle, parents=parents_reader(LANDED))).pages[0]
    catalog = (await _fetch("repositories", handle, parents=parents_reader(LANDED))).pages[0]
    branch = (await _fetch("branches", handle, parents=parents_reader(LANDED))).pages[0]
    pull = (await _fetch("pull_requests", handle, parents=parents_reader(LANDED))).pages[0]

    assert pull.source_identity == "pull_requests/acme/repo1/4515114744"
    assert pull.source_ref == "pull_requests/acme/repo1/4515114744"
    assert under_repo.source_identity == "issues/acme/repo1/3122"
    assert under_repo.source_ref == "issues/acme/repo1/3122"
    assert under_org.source_identity == "users/acme/42"
    assert under_org.source_ref == "users/acme/42"
    assert catalog.source_identity == "repositories/acme/100"
    assert catalog.source_ref == "repositories/acme/100"
    assert branch.source_identity == "branches/acme/repo1/main"
    assert branch.source_ref == "branches/acme/repo1/main"
    assert under_repo.digest == ISSUE_DIGEST
    assert under_org.digest == USER_DIGEST
    assert catalog.digest == REPOSITORY_DIGEST
    assert branch.digest == BRANCH_DIGEST


async def test_the_other_canonical_repo_streams_keep_their_addresses(
    parents_reader: ParentsReader,
) -> None:
    """`comments`, `commit_comments` and `releases` are canonical on main, so their pages are in
    the table, and no other test lands one: their addresses are written out here the way the
    deploy-safety test writes the rest, so the rule is asserted for every stream a connection
    already syncs rather than inferred from the ones that happen to have a fixture."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/issues/comments":
            return httpx.Response(
                200, json=[{"id": 700, "body": "same here", "updated_at": "2026-02-04T00:00:00Z"}]
            )
        if request.url.path == "/repos/acme/repo1/comments":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 800,
                        "body": "typo",
                        "commit_id": HEAD_SHA,
                        "updated_at": "2026-02-04T00:00:00Z",
                    }
                ],
            )
        if request.url.path == "/repos/acme/repo1/releases":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 900,
                        "tag_name": "v1.0",
                        "name": "v1.0",
                        "created_at": "2026-02-04T00:00:00Z",
                    }
                ],
            )
        return httpx.Response(404, json={"path": request.url.path})

    comment = (await _fetch("comments", handle, parents=parents_reader(LANDED))).pages[0]
    commit_comment = (
        await _fetch("commit_comments", handle, parents=parents_reader(LANDED))
    ).pages[0]
    release = (await _fetch("releases", handle, parents=parents_reader(LANDED))).pages[0]

    assert comment.source_identity == "comments/acme/repo1/700"
    assert comment.source_ref == "comments/acme/repo1/700"
    assert commit_comment.source_identity == "commit_comments/acme/repo1/800"
    assert commit_comment.source_ref == "commit_comments/acme/repo1/800"
    assert release.source_identity == "releases/acme/repo1/900"
    assert release.source_ref == "releases/acme/repo1/900"


async def test_a_repo_stream_asks_only_its_own_endpoint(parents_reader: ParentsReader) -> None:
    """A repo-scoped stream's partitions are the landed `repositories` records, so its tick spends
    one request per repo and none re-deriving the repo catalog."""
    calls: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/repos/acme/repo1/branches":
            return httpx.Response(200, json=[{"name": "main"}])
        return httpx.Response(404, json={"path": request.url.path})

    await _fetch("branches", handle, parents=parents_reader(LANDED))

    assert calls == ["/repos/acme/repo1/branches"]


async def test_one_tick_of_every_stream_costs_one_request_per_repo(
    parents_reader: ParentsReader,
) -> None:
    """The redundancy the tree removes, counted. Every repo-scoped stream used to re-derive the repo
    catalog — `/user/orgs` then `/orgs/{org}/repos` — before asking its own endpoint, so a tick of
    the 21 of them spent 21 enumerations of the same thing. Under the tree the catalog is the
    `organizations` and `repositories` rows' own work, once."""
    streams = [spec.name for spec in GitHubConnector().streams()]

    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/graphql":
            return _answer(_pulls_page([]))
        return httpx.Response(200, json=[])

    calls: list[str] = []
    for stream in streams:
        await _fetch(stream, handle, parents=parents_reader(ONE_PULL))

    assert len(calls) == len(streams)
    assert calls.count("/user/orgs") == 1
    assert calls.count("/orgs/acme/repos") == 1


async def test_a_repo_record_missing_its_path_field_raises_at_fan_out(
    parents_reader: ParentsReader,
) -> None:
    """A repo page landed without the owner and name its 21 children template from cannot be asked
    about. `/repos//issues` would answer a collection nobody meant and land nothing, so the run
    fails naming the stream, the field and the record instead."""

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    landed = {"repositories": (ParentRecord(ref=REPO_REF, fields={}),)}

    with pytest.raises(RuntimeError, match=r"'issues' reads 'owner\.login' off 'repositories'"):
        await _fetch("issues", handle, parents=parents_reader(landed))


def test_the_root_of_the_tree_syncs_though_it_is_not_content() -> None:
    """`organizations` is a lookup list, not a collection a member asked for, and every other row's
    partitions descend from it — so it syncs by the ancestor closure while `assignees` and the other
    lookups beside it do not."""
    streams = GitHubConnector().streams()
    syncing = syncing_streams(streams)
    canonical = {spec.name for spec in streams if spec.canonical}

    assert "organizations" in syncing
    assert "organizations" not in canonical
    assert syncing == canonical | {"organizations"}
    assert "assignees" not in syncing


async def test_a_rate_limit_403_fails_the_run_rather_than_emptying_the_repo(
    parents_reader: ParentsReader,
) -> None:
    """GitHub answers an exhausted rate limit with a 403, which the request layer reads as a rate
    limit only on a 429. Skipping the partition would land an empty successful run — the error
    count reset, the same sweep re-issued next tick with no backoff — so the status fails the run
    and the row takes the error backoff. A repository that is gone still drops out alone."""

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/repos/acme/repo1/commits":
            return httpx.Response(403, json={"message": "API rate limit exceeded"})
        if request.url.path == "/repos/acme/repo2/commits":
            return httpx.Response(200, json=[_commit("c1", "2026-03-01T00:00:00Z")])
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(httpx.HTTPStatusError) as refused:
        await _fetch("commits", handle, parents=parents_reader(TWO_REPOS))

    assert refused.value.response.status_code == 403


async def test_a_repository_that_stops_qualifying_leaves_the_catalog_snapshot(
    parents_reader: ParentsReader,
) -> None:
    """`repositories` is the whole of `/orgs/{login}/repos` every pass, so its run is the
    authoritative set the driver sweeps against: a repository archived, forked or gone since the
    last pass is absent from it, its page is tombstoned by the driver's snapshot sweep, and a
    tombstoned page is no partition of the 21 streams below. `organizations` is the same kind of
    listing at the root. Neither leaves a cursor: the next pass reads the listing whole again."""
    repos = [REPO, {**REPO, "id": 101, "name": "repo2", "full_name": "acme/repo2"}]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/user/orgs":
            return httpx.Response(200, json=[ORG])
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=repos)
        return httpx.Response(404, json={"path": request.url.path})

    first = await _fetch("repositories", handle, parents=parents_reader(LANDED))
    repos[1] = {**repos[1], "archived": True}
    second = await _fetch("repositories", handle, parents=parents_reader(LANDED))
    orgs = await _fetch("organizations", handle, parents=parents_reader({}))

    assert _refs(first) == {f"repositories/{ORG_SCOPE}/100", f"repositories/{ORG_SCOPE}/101"}
    assert (first.snapshot, first.next_cursor) == (True, None)
    assert _refs(second) == {f"repositories/{ORG_SCOPE}/100"}
    assert (second.snapshot, second.next_cursor) == (True, None)
    assert (_refs(orgs), orgs.snapshot, orgs.next_cursor) == ({"organizations/1"}, True, None)


async def test_an_organization_the_grant_cannot_read_into_fails_the_repositories_run(
    parents_reader: ParentsReader,
) -> None:
    """A snapshot run is swept against as authoritative, so an organization answering 404 or 410
    cannot be passed over the way a repository under an incremental stream is: the run would hold
    none of that organization's repositories and the driver would tombstone every one of them,
    taking their partitions out from under the 21 streams below. The refusal fails the run instead,
    and nothing is committed or swept."""
    beta = ParentRecord(ref="organizations/2", fields={"login": "beta"})

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/orgs/acme/repos":
            return httpx.Response(200, json=[REPO])
        if request.url.path == "/orgs/beta/repos":
            return httpx.Response(404, json={"message": "Not Found"})
        return httpx.Response(404, json={"path": request.url.path})

    with pytest.raises(RuntimeError, match=r"'repositories' cannot snapshot '/orgs/beta/repos'"):
        await _fetch(
            "repositories", handle, parents=parents_reader({"organizations": (*ORGS, beta)})
        )


def _selection(query: str) -> dict[str, Any]:
    """The nested field selection a GraphQL document asks for, arguments skipped and an inline
    fragment folded into the selection it extends. A mock answering through it returns the fields
    the query names and nothing a fixture happens to carry, so a field dropped from the query drops
    out of the landed page the way it would against GitHub."""
    root: dict[str, Any] = {}
    stack: list[dict[str, Any]] = [root]
    pending: str | None = None
    fragment = False
    for token in re.findall(r"\.\.\. on \w+|[{}]|\([^()]*\)|[A-Za-z_][A-Za-z0-9_]*", query):
        if token.startswith("(") or token == "query":
            continue
        if token.startswith("..."):
            fragment = True
            continue
        if token == "{":
            stack.append(stack[-1] if fragment or pending is None else stack[-1][pending])
            fragment = False
            pending = None
        elif token == "}":
            stack.pop()
        else:
            pending = token
            stack[-1].setdefault(token, {})
    return root


def _asked(value: Any, selection: dict[str, Any]) -> Any:
    match value:
        case dict():
            return {
                key: _asked(item, selection[key]) for key, item in value.items() if key in selection
            }
        case list():
            return [_asked(item, selection) for item in value]
        case _:
            return value


@dataclass(frozen=True)
class _BigPull:
    """PR 3560's shape: 44 check contexts, 16 review threads and 116 files, each list past the clip
    the shared field set applies, paged as GitHub pages them — `first` read off the query, the
    cursor an offset. `flipped` is the index of the one check reporting FAILURE, `threads` how many
    threads exist, `comments` the pull request's comment total, and `endless` a connection that
    names a next page forever."""

    flipped: int | None = None
    threads: int = BIG_THREADS
    comments: int = 40
    endless: bool = False

    def node(self, query: str, after: str | None) -> dict[str, Any]:
        contexts = [
            {
                "name": f"check {index}",
                "conclusion": "FAILURE" if index == self.flipped else "SUCCESS",
                "detailsUrl": f"https://github.com/acme/repo1/runs/{index}",
            }
            for index in range(BIG_CHECKS)
        ]
        by_state = Counter(context["conclusion"] for context in contexts)
        threads = [
            {
                "isResolved": False,
                "comments": {
                    "nodes": [
                        {
                            "path": f"f{index}.py",
                            "body": f"thread {index}",
                            "author": {"login": "bob"},
                        }
                    ]
                },
            }
            for index in range(self.threads)
        ]
        files = [
            {"path": f"src/f{index}.py", "additions": index, "deletions": 1}
            for index in range(BIG_FILES)
        ]
        base = _pull_node(3560, "2026-09-02T00:00:00Z")
        return {
            **base,
            "totalCommentsCount": self.comments,
            "reviews": {"totalCount": 10, **base["reviews"]},
            "commits": {
                "totalCount": 132,
                "nodes": [
                    {
                        "commit": {
                            "statusCheckRollup": {
                                "state": "SUCCESS",
                                "contexts": {
                                    **(self._page(contexts, query, "contexts", after) or {}),
                                    "checkRunCountsByState": [
                                        {"state": state, "count": by_state[state]}
                                        for state in ("FAILURE", "SUCCESS")
                                    ],
                                    "statusContextCountsByState": [
                                        {"state": "SUCCESS", "count": 0}
                                    ],
                                },
                            }
                        }
                    }
                ],
            },
            "reviewThreads": self._page(threads, query, "reviewThreads", after),
            "files": self._page(files, query, "files", after),
        }

    def _page(
        self, items: list[dict[str, Any]], query: str, name: str, after: str | None
    ) -> dict[str, Any] | None:
        match = re.search(rf"{name}\(first: (\d+)", query)
        if match is None:
            return None
        start, first = int(after or 0), int(match.group(1))
        end = start + first
        return {
            "totalCount": len(items),
            "nodes": items[start:end],
            "pageInfo": {
                "hasNextPage": self.endless or end < len(items),
                "endCursor": str(start + 1 if self.endless else end),
            },
        }


BIG = _BigPull()


def _big_pull_handler(
    pull: _BigPull,
    posts: list[dict[str, Any]],
    *,
    catalog: bool = True,
    remaining: int = 4_000,
) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/graphql":
            return httpx.Response(404, json={"path": request.url.path})
        body = json.loads(request.content)
        variables = body["variables"]
        posts.append(variables)
        node = pull.node(body["query"], variables.get("after"))
        repository = (
            {"pullRequest": node}
            if "number" in variables
            else _pulls_page([node] if catalog else [])
        )
        return _answer(
            _asked(repository, _selection(body["query"])["repository"]),
            cost=1,
            remaining=remaining if "number" in variables else 4_000,
        )

    return handle


def _body(page: Any) -> dict[str, Any]:
    return json.loads(page.body.split("\n\n", 1)[1])


@pytest.mark.parametrize(
    "moved",
    [
        pytest.param(replace(BIG, flipped=39), id="the 40th check fails"),
        pytest.param(replace(BIG, comments=41), id="a comment lands in the 15th thread"),
        pytest.param(replace(BIG, threads=17), id="a 17th thread opens"),
    ],
)
async def test_activity_past_the_clip_moves_the_catalog_page(
    parents_reader: ParentsReader, moved: _BigPull
) -> None:
    """The by-number read clips its lists — 30 contexts, 10 threads, 20 files — for the 1 point it
    prices and pages each to its end before the page lands, and the catalog walk lands nothing but
    that read, so a check flipping or a comment landing past a clip is in the body that moves."""
    base = await _fetch("pull_requests", _big_pull_handler(BIG, []), parents=parents_reader(LANDED))
    after = await _fetch(
        "pull_requests", _big_pull_handler(moved, []), parents=parents_reader(LANDED)
    )

    assert base.pages[0].digest != after.pages[0].digest
    body = _body(base.pages[0])
    assert len(body["checks"]["contexts"]) == body["checks"]["contextsCount"] == BIG_CHECKS
    assert len(body["reviewThreads"]) == body["reviewThreadsCount"] == BIG_THREADS
    assert len(body["files"]) == body["filesCount"] == BIG_FILES
    assert body["checks"]["checkRunCountsByState"] == [
        {"state": "FAILURE", "count": 0},
        {"state": "SUCCESS", "count": BIG_CHECKS},
    ]
    assert body["totalCommentsCount"] == 40


@pytest.mark.parametrize(
    "watched", [pytest.param((PULL_3560,), id="watched"), pytest.param((), id="catalog")]
)
async def test_a_pull_request_lands_with_every_thread_check_and_file(
    parents_reader: ParentsReader, watched: tuple[str, ...]
) -> None:
    """Whichever read lands a pull request pages each clipped connection to its end, 100 a page, so
    the page carries all 116 files, 16 threads and 44 checks — one read for the pull request and
    one more per connection past a clip, as against GitHub."""
    posts: list[dict[str, Any]] = []
    result = await _fetch(
        "pull_requests",
        _big_pull_handler(BIG, posts, catalog=not watched),
        parents=parents_reader(LANDED),
        watched=watched,
    )

    (page,) = result.pages
    body = _body(page)
    assert page.source_identity == f"pull_requests/{REPO_SCOPE}/3560"
    assert len(body["files"]) == body["filesCount"] == BIG_FILES
    assert len(body["reviewThreads"]) == body["reviewThreadsCount"] == BIG_THREADS
    assert len(body["checks"]["contexts"]) == body["checks"]["contextsCount"] == BIG_CHECKS
    assert body["files"][-1]["path"] == f"src/f{BIG_FILES - 1}.py"
    assert body["checks"]["contexts"][-1]["name"] == f"check {BIG_CHECKS - 1}"
    assert [post.get("after") for post in posts if "number" in post] == [None, "10", "20", "30"]


async def test_the_watch_and_the_catalog_land_one_body(parents_reader: ParentsReader) -> None:
    """The tick that reads a pull request twice — once because it is watched, once because it is new
    enough for the walk — lands one digest. Two bodies for one page, the watch's whole and the
    walk's clipped, moved its revision on every pass and woke the watch each time."""
    result = await _fetch(
        "pull_requests",
        _big_pull_handler(BIG, []),
        parents=parents_reader(LANDED),
        watched=(PULL_3560,),
    )

    assert {page.source_identity for page in result.pages} == {f"pull_requests/{REPO_SCOPE}/3560"}
    assert len({page.digest for page in result.pages}) == 1


@pytest.mark.parametrize(
    "watched", [pytest.param((PULL_3560,), id="watched"), pytest.param((), id="catalog")]
)
async def test_a_budget_met_mid_tail_lands_no_partial_page(
    parents_reader: ParentsReader, watched: tuple[str, ...]
) -> None:
    """A page short of its files would read as files removed, so a read that spends the budget with
    pages still to fetch raises before anything lands: the run parks until the refill and the next
    tick reads the pull request whole."""
    posts: list[dict[str, Any]] = []
    result = await _fetch(
        "pull_requests",
        _big_pull_handler(BIG, posts, catalog=not watched, remaining=1),
        parents=parents_reader(LANDED),
        watched=watched,
    )

    assert result.pages == ()
    assert result.retry_after_seconds is not None
    assert [post.get("after") for post in posts] == [None] * (1 if watched else 2)


async def test_a_connection_that_never_ends_fails_the_run(
    parents_reader: ParentsReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(github, "PULL_REQUEST_TAIL_MAX_PAGES", 2)

    with pytest.raises(StreamFault, match="paged past 2 pages of reviewThreads"):
        await _fetch(
            "pull_requests",
            _big_pull_handler(replace(BIG, endless=True), [], catalog=False),
            parents=parents_reader(LANDED),
            watched=(PULL_3560,),
        )


def test_the_rows_a_connection_registers_are_budgeted_under_the_rest_pool() -> None:
    """GitHub's REST pool is 5,000 requests an hour — 83 a minute — for the whole connection, and a
    quiet stream spends one request per repository per tick, so the six canonical REST streams under
    a repository carry the budget that keeps them under it beside the catalog rows' own reads.
    `pull_requests` draws on GraphQL's own pool and carries the budget its 1-point index page
    prices, leaving the minute's other 63 points to the by-number reads. A stream that registers no
    row spends nothing and carries none."""
    streams = {spec.name: spec for spec in GitHubConnector().streams()}
    syncing = syncing_streams(list(streams.values()))
    rest_children = {
        name
        for name in syncing
        if name != "pull_requests"
        and any(edge.stream == "repositories" for edge in streams[name].parents)
    }
    per_minute = 5_000 // 60

    assert rest_children == {
        "comments",
        "commit_comments",
        "issues",
        "releases",
        "review_comments",
        "workflows",
    }
    assert {streams[name].fetch_budget for name in rest_children} == {
        github.REPO_STREAM_FETCH_BUDGET
    }
    assert streams["pull_requests"].fetch_budget == github.PULL_REQUEST_FETCH_BUDGET
    assert {streams[name].fetch_budget for name in ("organizations", "repositories")} == {None}
    assert {spec.fetch_budget for name, spec in streams.items() if name not in syncing} == {None}
    assert len(rest_children) * github.REPO_STREAM_FETCH_BUDGET + 2 <= per_minute
    assert github.PULL_REQUEST_FETCH_BUDGET + 63 == per_minute
