"""The GitHub connector — repositories, issues, comments, users, and their siblings synced into
recallable pages.

GitHub paginates uniformly: every list endpoint returns records as a bare JSON array and ships an
RFC 5988 `Link: rel=next` header until the last page (`?per_page=100`). Records arrive flat, so
`flatten` stays the identity passthrough. Auth is the two required GitHub headers (the v3 media type
and the API version) layered on whichever client the base built from the resolved `Credential`.

Most streams hit a per-repo path, but the repo catalog is derived from the organizations the grant
exposes: the connector walks `/user/orgs`, then `/orgs/{org}/repos`, and fans repo-scoped streams
out over that org-owned repo set, so a fresh issue lands on the next sync with no manual repo
config. Each repo is a partition of the SDK's `PartitionWalk`, which owns the cursor map and resume
state; this connector only enumerates repos and produces one repo's bounded pages per stream
`Ordering`. `issues`/`comments` are `ascending` — a `sort=updated&direction=asc&since` walk whose
running watermark is a sound resume point. `commits`/`events`/`issue_events` are `newest_first`
append-only feeds: a first backfill walks the repo newest-first as a descending `{high, until}`
window (`commits` bounds it server-side with `?until`, `events`/`issue_events` client-side since
their API takes no time filter), so a capped run resumes downward without the position drift that
would lose records prepended between slices; steady-state stops early once a page sits strictly
below the repo watermark (a tying page re-yields, so a tied-but-new record lands and the repeats
dedup downstream). Every other repo-scoped stream is `none` — checkpointed at the repo boundary
only. GitHub surfaces no delete signal, so the sync runner's row-level cursor skips already-seen
rows. A grant that can't enumerate orgs at all (`/user/orgs` refused with a 403) can read no stream,
so the walk raises `StreamSkipped` and the run records a skip, not a failure. The write path is
intentionally absent — the source seam only reads."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    Ordering,
    PartitionBound,
    PartitionSkipped,
    PartitionWalk,
    RestConnector,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
)

PAGE_SIZE = 100
_REPO_LIST_PARAMS = {"per_page": PAGE_SIZE, "type": "all", "sort": "pushed", "direction": "desc"}
_USERS_ENRICH_CONCURRENCY = 8
_GITHUB_ACCEPT = "application/vnd.github+json"
_GITHUB_API_VERSION = "2022-11-28"
_STATE_ALL_STREAMS = frozenset({"issues", "pull_requests"})
_UNTIL_STREAMS = frozenset({"commits"})
_REPO_SKIP_STATUS = frozenset({404, 409, 410})
_ORG_SKIP_STATUS = frozenset({403, 404, 410})
_ORG_SCOPE_GATE_STATUS = frozenset({403})


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    ordering: Ordering = Ordering.none,
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        ordering=ordering,
        canonical=canonical,
    )


# Stream list mirrors Airbyte's source-github configured catalog: name, primary key, cursor field.
# Cursor fields follow Airbyte's `default_cursor_field` — `updated_at` for mutable collections,
# `created_at` for append-only feeds, None for full-refresh-only streams. `issues` and `comments`
# are the two GitHub reads with a server-side `?since` filter.
ALL_STREAMS: list[StreamSpec] = [
    _stream("repositories", cursor_field="updated_at", canonical=True),
    _stream("issues", cursor_field="updated_at", ordering=Ordering.ascending, canonical=True),
    _stream("issue_milestones", cursor_field="updated_at", canonical=True),
    _stream("comments", cursor_field="updated_at", ordering=Ordering.ascending, canonical=True),
    _stream("users", cursor_field=None, canonical=True),
    _stream("assignees", cursor_field=None),
    _stream("branches", primary_key="name", cursor_field=None),
    _stream("collaborators", cursor_field=None),
    _stream("commit_comment_reactions", cursor_field=None),
    _stream("commit_comments", cursor_field="updated_at"),
    _stream(
        "commits", primary_key="sha", cursor_field="created_at", ordering=Ordering.newest_first
    ),
    _stream("contributor_activity", cursor_field=None),
    _stream("deployments", cursor_field="updated_at"),
    _stream("events", cursor_field="created_at", ordering=Ordering.newest_first),
    _stream("issue_comment_reactions", cursor_field=None),
    _stream("issue_events", cursor_field="created_at", ordering=Ordering.newest_first),
    _stream("issue_labels", cursor_field=None),
    _stream("issue_reactions", cursor_field=None),
    _stream("issue_timeline_events", cursor_field="created_at"),
    _stream("organizations", cursor_field=None),
    _stream("project_cards", cursor_field="updated_at"),
    _stream("project_columns", cursor_field="updated_at"),
    _stream("projects", cursor_field="updated_at"),
    _stream("pull_request_comment_reactions", cursor_field=None),
    _stream("pull_request_commits", primary_key="sha", cursor_field=None),
    _stream("pull_request_stats", cursor_field="updated_at"),
    _stream("pull_requests", cursor_field="updated_at"),
    _stream("releases", cursor_field="created_at"),
    _stream("review_comments", cursor_field="updated_at"),
    _stream("reviews", cursor_field=None),
    _stream("stargazers", cursor_field="starred_at"),
    _stream("tags", primary_key="name", cursor_field=None),
    _stream("team_members", cursor_field=None),
    _stream("team_memberships", cursor_field=None),
    _stream("teams", cursor_field=None),
    _stream("workflow_jobs", cursor_field="completed_at"),
    _stream("workflow_runs", cursor_field="updated_at"),
    _stream("workflows", cursor_field="updated_at"),
]


# Per-stream API paths. `{owner}`/`{repo}`/`{org}` are resolved at fetch time from the granted-org
# repo catalog. Only streams with a wired path are runnable; sub-streams that need a bespoke
# parent-id walk (reactions, project cards/columns, PR commits/stats/reviews, workflow_jobs,
# team_members/team_memberships, issue_timeline_events) are catalogued for parity, not yet driven.
_PATHS: dict[str, str] = {
    "assignees": "/repos/{owner}/{repo}/assignees",
    "branches": "/repos/{owner}/{repo}/branches",
    "collaborators": "/repos/{owner}/{repo}/collaborators",
    "comments": "/repos/{owner}/{repo}/issues/comments",
    "commit_comments": "/repos/{owner}/{repo}/comments",
    "commits": "/repos/{owner}/{repo}/commits",
    "contributor_activity": "/repos/{owner}/{repo}/stats/contributors",
    "deployments": "/repos/{owner}/{repo}/deployments",
    "events": "/repos/{owner}/{repo}/events",
    "issue_events": "/repos/{owner}/{repo}/issues/events",
    "issue_labels": "/repos/{owner}/{repo}/labels",
    "issue_milestones": "/repos/{owner}/{repo}/milestones",
    "issues": "/repos/{owner}/{repo}/issues",
    "projects": "/repos/{owner}/{repo}/projects",
    "pull_requests": "/repos/{owner}/{repo}/pulls",
    "releases": "/repos/{owner}/{repo}/releases",
    "review_comments": "/repos/{owner}/{repo}/pulls/comments",
    "stargazers": "/repos/{owner}/{repo}/stargazers",
    "tags": "/repos/{owner}/{repo}/tags",
    "workflow_runs": "/repos/{owner}/{repo}/actions/runs",
    "workflows": "/repos/{owner}/{repo}/actions/workflows",
    "repositories": "/orgs/{org}/repos",
    "organizations": "/user/orgs",
    "teams": "/orgs/{org}/teams",
    "users": "/orgs/{org}/members",
}


class GitHubConnector(RestConnector):
    name = "github"
    base_url = "https://api.github.com"
    streams_list = ALL_STREAMS

    def streams(self) -> list[StreamSpec]:
        """The runnable subset: streams whose `_PATHS` dispatch is wired. Adding a path promotes a
        catalogued stream into the runnable set automatically."""
        return [stream for stream in self.streams_list if stream.name in _PATHS]

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Accept"] = _GITHUB_ACCEPT
        client.headers["X-GitHub-Api-Version"] = _GITHUB_API_VERSION
        return client

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        path = _PATHS.get(stream.name)
        if not path:
            raise NotImplementedError(f"github: stream {stream.name!r} has no paginate dispatch")

        params: dict[str, Any] = {"per_page": PAGE_SIZE}
        if stream.name in _STATE_ALL_STREAMS:
            params["state"] = "all"

        if stream.name == "repositories":
            async for _org, page in self._iter_granted_org_repo_pages(client):
                yield page
            return

        if "{owner}" in path and "{repo}" in path:

            async def repos() -> AsyncIterator[str]:
                async for owner, repo in self._iter_user_repos(client):
                    yield f"{owner}/{repo}"

            def repo_pages(repo_key: str, bound: PartitionBound) -> AsyncIterator[WalkPage]:
                return self._repo_pages(client, stream, path, repo_key, bound)

            walk = PartitionWalk(
                ordering=stream.ordering, partitions=repos, pages=repo_pages
            ).stream(cursor)
            try:
                async for repo_page in walk:
                    yield repo_page
            finally:
                if isinstance(walk, AsyncGenerator):
                    await walk.aclose()
            return

        if "{org}" in path:
            semaphore = (
                asyncio.Semaphore(_USERS_ENRICH_CONCURRENCY) if stream.name == "users" else None
            )
            async for org in self._iter_user_orgs(client):
                scoped = path.format(org=org)
                try:
                    async for page in self._paginate_link_header(
                        client, scoped, params=dict(params)
                    ):
                        if stream.name == "users" and semaphore is not None:
                            page = await self._enrich_users(client, page, semaphore=semaphore)
                        yield page
                except httpx.HTTPStatusError as error:
                    if error.response.status_code in {404, 410}:
                        continue
                    raise
            return

        async for page in self._paginate_link_header(client, path, params=params):
            yield page

    async def _repo_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        path: str,
        repo_key: str,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One repo's bounded page slice for `PartitionWalk`, applying the resume `bound` in
        GitHub's own terms: an ascending `?since` walk sends `sort=updated&direction=asc&since`;
        `commits` bounds a newest-first backfill server-side with `?until`; `events`/`issue_events`
        expose no time filter, so a backfill is bounded client-side by dropping records at or above
        `before`. Each page reports its cursor-value span so the walk tracks the watermark/window;
        a repo the grant can't read (404/409/410) drops out without failing the run."""
        owner, _, repo = repo_key.partition("/")
        scoped = path.format(owner=owner, repo=repo)
        params: dict[str, Any] = {"per_page": PAGE_SIZE}
        if stream.name in _STATE_ALL_STREAMS:
            params["state"] = "all"
        if stream.ordering is Ordering.ascending:
            params |= {"sort": "updated", "direction": "asc"}
            if bound.after:
                params["since"] = bound.after
        elif stream.name in _UNTIL_STREAMS and bound.before:
            params["until"] = bound.before
        try:
            async for page in self._paginate_link_header(client, scoped, params=dict(params)):
                if stream.name == "issues":
                    page = [record for record in page if "pull_request" not in record]
                if (
                    stream.ordering is Ordering.newest_first
                    and stream.name not in _UNTIL_STREAMS
                    and bound.before
                ):
                    before = bound.before
                    field = stream.cursor_field
                    page = [
                        record
                        for record in page
                        if field and isinstance(record.get(field), str) and record[field] <= before
                    ]
                if not page:
                    continue
                high, low = _cursor_bounds(page, stream.cursor_field)
                yield WalkPage(records=page, high=high, low=low)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REPO_SKIP_STATUS:
                raise PartitionSkipped(f"github: {repo_key} refused") from error
            raise

    async def _iter_user_repos(self, client: httpx.AsyncClient) -> AsyncIterator[tuple[str, str]]:
        """Yield `(owner, repo)` from granted-org repos. `/user/repos` is too broad (personal,
        collaborator, archived, forks); the org grant is the scope, so discovery walks `/user/orgs`
        then `/orgs/{org}/repos`."""
        async for org, page in self._iter_granted_org_repo_pages(client):
            for record in page:
                identity = _repo_identity(record, fallback_owner=org)
                if identity is not None:
                    yield identity

    async def _iter_granted_org_repo_pages(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[tuple[str, list[dict[str, Any]]]]:
        """Yield non-archived, non-fork repository pages for granted orgs."""
        async for org in self._iter_user_orgs(client):
            try:
                async for page in self._paginate_link_header(
                    client, f"/orgs/{org}/repos", params=dict(_REPO_LIST_PARAMS)
                ):
                    repos = [
                        record
                        for record in page
                        if not record.get("archived") and not record.get("fork")
                    ]
                    if repos:
                        yield org, repos
            except httpx.HTTPStatusError as error:
                if error.response.status_code in _ORG_SKIP_STATUS:
                    continue
                raise

    async def _iter_user_orgs(self, client: httpx.AsyncClient) -> AsyncIterator[str]:
        """Yield each org login the grant exposes, driving org-scoped fan-out. Every runnable stream
        fans out from this enumeration, so a grant refused it here — the whole account lacks org
        scope or is policy-gated (`/user/orgs` → 403) — can read no stream at all: raise
        `StreamSkipped` so the run records a skip rather than failing. A per-org refusal deeper in
        the walk is skipped there and the other orgs still sync; only the root refusal skips the
        stream."""
        try:
            async for page in self._paginate_link_header(
                client, "/user/orgs", params={"per_page": PAGE_SIZE}
            ):
                for record in page:
                    login = record.get("login")
                    if isinstance(login, str) and login:
                        yield login
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _ORG_SCOPE_GATE_STATUS:
                raise StreamSkipped(
                    f"github: org enumeration refused ({error.response.status_code}); "
                    "the grant is missing org scope"
                ) from error
            raise

    async def _enrich_users(
        self, client: httpx.AsyncClient, page: list[dict[str, Any]], *, semaphore: asyncio.Semaphore
    ) -> list[dict[str, Any]]:
        """Replace each simple-user member (`login` + `id`) with the public-user record from
        `GET /users/{login}`, which carries `name`/`email` when public. On 404 keep the member."""

        async def one(member: dict[str, Any]) -> dict[str, Any]:
            login = member.get("login")
            if not isinstance(login, str) or not login:
                return member
            async with semaphore:
                try:
                    response = await self._get_raw(client, f"/users/{login}")
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 404:
                        return member
                    raise
                body = response.json() if response.content else None
                return body if isinstance(body, dict) else member

        return list(await asyncio.gather(*[one(member) for member in page]))

    async def _paginate_link_header(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """RFC 5988 link-header walk. Empty/202 responses (GitHub's stats endpoints answer 202 with
        an empty body while computing) yield nothing rather than raise."""
        async for page in self._get_link_header_pages(
            client, path, params=params, page_size=PAGE_SIZE, parse_records=_parse_records
        ):
            yield page


def _parse_records(response: httpx.Response) -> list[dict[str, Any]]:
    if not response.content:
        return []
    body = response.json()
    return body if isinstance(body, list) else []


def _repo_identity(
    record: dict[str, Any], *, fallback_owner: str | None = None
) -> tuple[str, str] | None:
    full = record.get("full_name")
    if isinstance(full, str) and "/" in full:
        full_owner, _, repo = full.partition("/")
        if full_owner and repo:
            return full_owner, repo
    owner_record = record.get("owner")
    owner = owner_record.get("login") if isinstance(owner_record, dict) else None
    repo_name = record.get("name")
    if isinstance(repo_name, str) and repo_name and isinstance(owner, str) and owner:
        return owner, repo_name
    if isinstance(repo_name, str) and repo_name and fallback_owner:
        return fallback_owner, repo_name
    return None


def _cursor_bounds(
    page: list[dict[str, Any]], cursor_field: str | None
) -> tuple[str | None, str | None]:
    """The highest and lowest `cursor_field` values on a page, for the walk's watermark/window
    tracking. None when the stream carries no cursor field."""
    if not cursor_field:
        return None, None
    values = [record[cursor_field] for record in page if isinstance(record.get(cursor_field), str)]
    if not values:
        return None, None
    return max(values), min(values)
