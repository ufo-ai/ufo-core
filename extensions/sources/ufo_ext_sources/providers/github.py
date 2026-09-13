"""The GitHub connector — repositories, issues, comments, users, and their siblings synced into
recallable pages.

GitHub paginates uniformly: every list endpoint returns records as a bare JSON array and ships an
RFC 5988 `Link: rel=next` header until the last page (`?per_page=100`). Auth is the
two required GitHub headers (the v3 media type and the API version) layered on whichever client the
base built from the resolved `Credential`.

The catalog is a tree three deep: `organizations` is the root at `/user/orgs`, `repositories`,
`teams` and `users` hang under an org, and the other 21 streams hang under a repository. A stream's
partitions are its parent's landed pages, so the repo catalog is walked once by the `repositories`
row and the 21 repo-scoped streams fan out over what it landed rather than re-deriving it, and the
owner and name its path reads off the repository are part of every child page's address — a key
unique only inside one repo (a branch or tag `name`, a commit `sha`, a starrer's user id) addresses
one page per repo instead of landing every repo's `main` on one. The edge carries the repository's
`full_name` onto each record as `repo_full_name`, and an organization's `login` as `org_login`, and
`render` scopes the primary key to it (`acme/ufo/main`), so a page reads and is titled as every
page already landed does — the provider's own `number`, `login`, `commit.sha` and `url` carry the
unscoped values. The two catalog rows are full listings and snapshots: a repository that stops
qualifying — archived, forked, or gone from the grant — is tombstoned on the next pass and leaves
the partition set of the 21 streams under it, and an organization the grant lists but cannot read
into fails the `repositories` run rather than emptying that organization's repositories. Each repo
is a partition of the SDK's `PartitionWalk`, which owns the cursor map and resume state; this
connector only produces one repo's bounded pages per stream `Ordering`.
`issues`/`comments`/`review_comments` are `ascending`: a `sort=updated&direction=asc&since` walk
whose running watermark is a sound resume point, and whose first pass starts at the row's floor,
the one bound these three endpoints take server-side.
`commits`/`events`/`issue_events`/`pull_requests` are `newest_first`: a first backfill walks the
repo newest-first as a descending `{high, until}` window (`commits` bounds it server-side with
`?until`, the others client-side since their API takes no time filter), so a capped
run resumes downward without the position drift that
would lose records prepended between slices; steady-state stops early once a page sits strictly
below the repo watermark (a tying page re-yields, so a tied-but-new record lands and the repeats
dedup downstream). Every other repo-scoped stream is `none` — checkpointed at
the repo boundary only, and re-walked whole on every completed pass.

`pull_requests` is read over GraphQL, alone among the streams. A reviewer is woken by a pull
request's checks, and REST publishes them nowhere a pull request read reaches: the timeline of
`metalcraftai/ufo` 3560 answered 19 entries over five event types and not one check-run or status
event, beside the 44 check runs its head commit carried, so a walk over `/issues/{n}/timeline` sees
a green branch go red and reports nothing. One GraphQL read answers the rollup, the reviews, the
unresolved threads, the files and the timeline with the pull request, and `databaseId` is its key,
so every page settles on the address its REST record already had. The answer's own `rateLimit`
ends a walk that cannot pay for its next page, because GitHub serves the refusal as a 200 carrying
no records. The field set clips every nested list — 30 check contexts, 5 reviews, 10 threads, 20
files — and carries beside each list its total, with the rollup's counts by state and
`totalCommentsCount`, so the page moves whenever anything on the pull request moves, inside a
clipped list or past it: 3560 carried 44 checks, 16 threads and 116 files, every one
past a clip, and a comment in its 15th thread would have moved GitHub's `updatedAt` and no byte of
the body without the totals. They cost no points — a page of 20 still prices 3 and one pull request
by number 1 (measured 2026-09-13).

**This stream is not deployable at a 60 s tick without a pass interval over the bulk walk.** Every
limit above and `PULL_REQUEST_PAGE_SIZE` are what GitHub priced, measured against
`metalcraftai/ufo` (3,560 pull requests, 2026-09-12): one page of 20 costs 3 of the hourly 5,000
points, 225 KB in 2.8 s, and one pull request by number costs 1 point and 19 KB in 0.8 s. A
`newest_first` steady state still reads the first page of every repository each tick to see whether
anything sits above the watermark, so the bulk walk alone costs `3 x repositories x 60` points an
hour: 3,600 against a 20-repository connection, and past roughly 27 repositories it spends the whole
budget and takes every watched pull request's 1-point read down with it. The nested limits are what
hold that price: the same query at 50/20/50/100/30 with a page of 25 priced 14, and `first: 100`
answers HTTP 504 at either width. What closes the gap is the pass interval — the bulk walk every N
minutes, watched partitions every tick — which is unit 3.

A pull request a live `source_trigger` watches is a partition of its own, keyed
`pull_requests/<full_name>/<number>` and read by number ahead of every repo in the walk: a check run
flips without moving the pull request's `updatedAt`, so the newest-first walk would stop above an
old pull request and never see it again. The watched partition carries its own watermark, which its
own record always ties, so it lands every tick; dropping the trigger stops enumerating it and the
walk's own prune takes the entry with it. It is the one read that pages its threads, checks and
files to the end — `PULL_REQUEST_TAIL_PAGE_SIZE` at a time past the clip, 1 point a page, so 3560
spends 4 a tick where a pull request inside the clips spends 1 — and a read that meets the budget
with pages still to fetch lands nothing that tick, since a page short of its files would read as
files removed.

`workflow_runs` is the one unordered stream
that carries a floor: Actions runs outnumber every other collection a busy repo publishes, so
the stream declares a zero-day backfill window and each repo slice sends the pinned floor as the
Actions API's `created=>=` range. A `none` stream re-walks whole every pass, so that bound governs
every pass, not just the first. `check_runs` hangs under the pull request whose head they ran on:
GitHub publishes a commit's check runs under a ref and nowhere else, and a pull request carries the
ref, so the edge reads it off the parent and asks the collection at the path GitHub publishes it at.
A check-run id is unique across the account, so the stream keys `global` and a page is addressed by
its id alone. It is syncable and not canonical: one read per pull request per pass is noisy against
the hourly budget, and the rollup on the pull request's own page already says whether its checks
passed — so it joins no connection by declaration and the rows that hold it are retired. A commit's
combined status has no stream at all: those contexts are on that same rollup, where they are the
pull request's summary of its checks rather than a second set of pages under a key a fork would
collide on.
GitHub surfaces no delete signal for the streams under a repository, so the sync runner's row-level
cursor skips already-seen rows. A grant that can't enumerate orgs at all (`/user/orgs` refused with
a 403) has no root to hang the tree off, so that row raises `StreamSkipped` and records a skip, not
a failure; every stream below it enumerates no partition and spends no request. The write path is
intentionally absent — the source seam only reads."""

import asyncio
import re
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import partial
from typing import Any, Literal

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.o11y import log
from ufo.sdk.sources import (
    REPO_BACKFILL_WINDOW_DAYS,
    Ordering,
    ParentEdge,
    Partition,
    PartitionBound,
    PartitionSkipped,
    PinnedPartitions,
    ProviderRateLimited,
    RestConnector,
    Run,
    StreamFault,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    dict_or_empty,
    fanned_out,
    get_path,
    list_or_empty,
    records_at,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
PULL_REQUEST_PAGE_SIZE = 20
PULL_REQUEST_PASS_INTERVAL_SECONDS = 300
"""How long the catalog walk of `pull_requests` waits between passes. GitHub priced one page of
`PULL_REQUEST_PAGE_SIZE` at 3 of the hourly 5,000 GraphQL points and one pull request by number at
1, measured against `metalcraftai/ufo` (3,560 pull requests, 2026-09-12). A `newest_first` steady
state reads the first page of every repository each pass to see whether anything sits above the
watermark, so the walk costs `3 x repositories` points a pass: at every 60 s tick that is 3,600 an
hour against 20 repositories and the whole budget past 27, which is what makes the interval the
difference between deployable and not. At 300 s a 100-repository connection spends `3 x 100 x 12` =
3,600 — under the 5,000 with 1,400 left, which buys 23 watched pull requests at a read a tick each,
and the pass itself still completes inside one tick where the fetch budget allows. A watched
partition ignores this and is read every tick, which is the whole reason a member sets a watch."""
REPO_STREAM_FETCH_BUDGET = 12
"""Repositories one run of a canonical REST stream under a repository may fetch. GitHub's REST pool
is 5,000 requests an hour per token or installation — 83 a minute, shared by every row of the
connection and apart from GraphQL's points (`/rate_limit` reports `core` and `graphql`
separately). A quiet `?since=` stream spends one request per repository per tick, so the six
canonical REST streams under a repository spend `6 x 12` = 72 a minute and the catalog rows above
them — one request per organization and one for the root — fit in what is left. A pass over more
repositories than the budget resumes on the next tick, so a large catalog sweeps slower and never
over the pool. A stream that registers no row spends nothing and carries none."""
PULL_REQUEST_FETCH_BUDGET = 20
"""Repositories one `pull_requests` run reads. GraphQL's own 5,000 points an hour are 83 a minute
and a trimmed page prices 3, so 20 pages spend 60 and leave 23 for the 1-point watched reads that
go first in the budget; `PULL_REQUEST_PASS_INTERVAL_SECONDS` bounds the smaller catalog that
completes a pass inside one tick."""
GRAPHQL_PATH = "/graphql"
MERGEABILITY_PENDING = "UNKNOWN"
COMMENT_BACKFILL_WINDOW_DAYS = 7
ISSUE_BACKFILL_WINDOW_DAYS = 365
_REPO_LIST_PARAMS = {"per_page": PAGE_SIZE, "type": "all", "sort": "pushed", "direction": "desc"}
_USERS_ENRICH_CONCURRENCY = 8
_GITHUB_ACCEPT = "application/vnd.github+json, application/vnd.github.star+json"
_GITHUB_API_VERSION = "2022-11-28"
_UNTIL_STREAMS = frozenset({"commits"})
_CREATED_FLOOR_STREAMS = frozenset({"workflow_runs"})
WORKFLOW_RUNS_BACKFILL_WINDOW_DAYS = 0
_PARTITION_SKIP_STATUS = frozenset({404, 409, 410})
REPO_PARTITION_FIELD = "repo_full_name"
ORG_PARTITION_FIELD = "org_login"
_CARRIED: dict[str, dict[str, str]] = {
    "repositories": {REPO_PARTITION_FIELD: "full_name"},
    "organizations": {ORG_PARTITION_FIELD: "login"},
}


@dataclass(frozen=True)
class _PinnedPull:
    """One pull request a standing watch visits every tick: the repository it belongs to, which is
    what its page is addressed under, and the number GitHub answers it by."""

    repo: str
    number: int


GRAPHQL_ERROR_CHARS = 200
GRAPHQL_PARTITION_REFUSALS = frozenset({"NOT_FOUND", "FORBIDDEN", "SAML_PROTECTED"})
PULL_REQUEST_MAX_PAGES = 200
PULL_REQUEST_TAIL_PAGE_SIZE = 100
PULL_REQUEST_TAIL_MAX_PAGES = 50
GRAPHQL_BUDGET_MIN_WAIT_SECONDS = 1.0
_PAGE_INFO = "pageInfo { hasNextPage endCursor }"
_CONTEXT_NODE = """... on CheckRun { name conclusion detailsUrl }
    ... on StatusContext { context state targetUrl }"""
_CONTEXT_COUNTS = (
    "totalCount checkRunCountsByState { state count } statusContextCountsByState { state count }"
)
_THREAD_NODE = "isResolved comments(first: 1) { nodes { path body author { login } } }"
_FILE_NODE = "path additions deletions"
PULL_REQUEST_FIELDS = f"""
  databaseId number title state isDraft mergeable reviewDecision updatedAt createdAt
  totalCommentsCount author {{ login }} headRefName headRefOid baseRefName url
  repository {{ owner {{ login }} name }}
  commits(last: 1) {{ totalCount nodes {{ commit {{ statusCheckRollup {{ state
    contexts(first: 30) {{ {_CONTEXT_COUNTS} nodes {{ {_CONTEXT_NODE} }} {_PAGE_INFO} }}
  }} }} }} }}
  reviews(last: 5) {{ totalCount nodes {{ author {{ login }} state submittedAt }} }}
  reviewThreads(first: 10) {{ totalCount nodes {{ {_THREAD_NODE} }} {_PAGE_INFO} }}
  files(first: 20) {{ totalCount nodes {{ {_FILE_NODE} }} {_PAGE_INFO} }}
  timelineItems(last: 10, itemTypes: [HEAD_REF_FORCE_PUSHED_EVENT, READY_FOR_REVIEW_EVENT,
    CONVERT_TO_DRAFT_EVENT, MERGED_EVENT, CLOSED_EVENT, REOPENED_EVENT, REVIEW_REQUESTED_EVENT])
    {{ nodes {{ __typename ... on Node {{ id }} }} }}
"""
_RATE_LIMIT_FIELDS = "rateLimit { cost remaining resetAt }"


def _tail_query(selection: str) -> str:
    return f"""
query($owner: String!, $name: String!, $number: Int!, $after: String) {{
  {_RATE_LIMIT_FIELDS}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{ {selection} }}
  }}
}}
"""


def _rollup_contexts(node: dict[str, Any]) -> dict[str, Any] | None:
    commits = list_or_empty(dict_or_empty(node.get("commits")).get("nodes"))
    return get_path(commits[0], "commit.statusCheckRollup.contexts") if commits else None


@dataclass(frozen=True)
class _Tail:
    """One nested connection of a watched pull request, read to its end after the page the shared
    field set clipped: the query asking its next page, the connection as it sits in a pull request
    node GitHub answers, and the list on the landed record its nodes extend."""

    name: str
    query: str
    connection: Callable[[dict[str, Any]], dict[str, Any] | None]
    landed: Callable[[dict[str, Any]], list[dict[str, Any]]]


PULL_REQUEST_TAILS: tuple[_Tail, ...] = (
    _Tail(
        name="reviewThreads",
        query=_tail_query(
            f"reviewThreads(first: {PULL_REQUEST_TAIL_PAGE_SIZE}, after: $after) "
            f"{{ nodes {{ {_THREAD_NODE} }} {_PAGE_INFO} }}"
        ),
        connection=lambda node: node.get("reviewThreads"),
        landed=lambda record: record["reviewThreads"],
    ),
    _Tail(
        name="files",
        query=_tail_query(
            f"files(first: {PULL_REQUEST_TAIL_PAGE_SIZE}, after: $after) "
            f"{{ nodes {{ {_FILE_NODE} }} {_PAGE_INFO} }}"
        ),
        connection=lambda node: node.get("files"),
        landed=lambda record: record["files"],
    ),
    _Tail(
        name="contexts",
        query=_tail_query(
            "commits(last: 1) { nodes { commit { statusCheckRollup { "
            f"contexts(first: {PULL_REQUEST_TAIL_PAGE_SIZE}, after: $after) "
            f"{{ nodes {{ {_CONTEXT_NODE} }} {_PAGE_INFO} }} }} }} }} }}"
        ),
        connection=_rollup_contexts,
        landed=lambda record: record["checks"]["contexts"],
    ),
)
PULL_REQUESTS_QUERY = f"""
query($owner: String!, $name: String!, $cursor: String) {{
  {_RATE_LIMIT_FIELDS}
  repository(owner: $owner, name: $name) {{
    pullRequests(first: {PULL_REQUEST_PAGE_SIZE},
      orderBy: {{field: UPDATED_AT, direction: DESC}}, after: $cursor) {{
      nodes {{ {PULL_REQUEST_FIELDS} }}
      pageInfo {{ hasNextPage endCursor }}
    }}
  }}
}}
"""
PULL_REQUEST_QUERY = f"""
query($owner: String!, $name: String!, $number: Int!) {{
  {_RATE_LIMIT_FIELDS}
  repository(owner: $owner, name: $name) {{
    pullRequest(number: $number) {{ {PULL_REQUEST_FIELDS} }}
  }}
}}
"""
_OWNER = r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})"
_REPO = r"[A-Za-z0-9_.-]{1,100}"
_RESOURCE_URLS = (
    re.compile(
        rf"^https://(?:www\.)?github\.com/(?P<owner>{_OWNER})/(?P<repo>{_REPO})"
        r"/(?P<path>pull|issues)/(?P<number>[0-9]{1,10})(?=$|[/#?])"
    ),
    re.compile(
        rf"^https://api\.github\.com/repos/(?P<owner>{_OWNER})/(?P<repo>{_REPO})"
        r"/(?P<path>pulls|issues)/(?P<number>[0-9]{1,10})(?=$|[/#?])"
    ),
)


def _stream(
    name: str,
    *,
    parent: str | None = None,
    path: str | None = None,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str | None = "created_at",
    updated_at_field: str | None = "updated_at",
    ordering: Ordering = Ordering.none,
    canonical: bool = False,
    backfill_window_days: int | None = None,
    indexed: bool = True,
    key_scope: Literal["local", "global"] = "local",
    pass_interval_seconds: int | None = None,
    delete_missing: bool = False,
    fetch_budget: int | None = None,
) -> StreamSpec:
    if (parent is None) != (path is None):
        raise ValueError(f"github: stream {name!r} names a parent without a path, or the reverse")
    if fetch_budget is None and canonical and parent == "repositories":
        fetch_budget = REPO_STREAM_FETCH_BUDGET
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        ordering=ordering,
        canonical=canonical,
        backfill_window_days=backfill_window_days,
        indexed=indexed,
        key_scope=key_scope,
        pass_interval_seconds=pass_interval_seconds,
        delete_missing=delete_missing,
        fetch_budget=fetch_budget,
        parents=(
            ()
            if parent is None or path is None
            else (ParentEdge(stream=parent, path=path, carry=_CARRIED.get(parent, {})),)
        ),
    )


ALL_STREAMS: list[StreamSpec] = [
    _stream("organizations", cursor_field=None, delete_missing=True),
    _stream(
        "repositories",
        parent="organizations",
        path="/orgs/{login}/repos",
        cursor_field=None,
        canonical=True,
        delete_missing=True,
    ),
    _stream("teams", parent="organizations", path="/orgs/{login}/teams", cursor_field=None),
    _stream("users", parent="organizations", path="/orgs/{login}/members", cursor_field=None),
    _stream(
        "issues",
        parent="repositories",
        path="/repos/{owner.login}/{name}/issues",
        cursor_field="updated_at",
        ordering=Ordering.ascending,
        backfill_window_days=ISSUE_BACKFILL_WINDOW_DAYS,
        canonical=True,
    ),
    _stream(
        "issue_milestones",
        parent="repositories",
        path="/repos/{owner.login}/{name}/milestones",
        cursor_field="updated_at",
    ),
    _stream(
        "comments",
        parent="repositories",
        path="/repos/{owner.login}/{name}/issues/comments",
        cursor_field="updated_at",
        ordering=Ordering.ascending,
        backfill_window_days=COMMENT_BACKFILL_WINDOW_DAYS,
        canonical=True,
    ),
    _stream(
        "assignees",
        parent="repositories",
        path="/repos/{owner.login}/{name}/assignees",
        cursor_field=None,
    ),
    _stream(
        "branches",
        parent="repositories",
        path="/repos/{owner.login}/{name}/branches",
        primary_key="name",
        cursor_field=None,
    ),
    _stream(
        "check_runs",
        parent="pull_requests",
        path="/repos/{repository.owner.login}/{repository.name}/commits/{headRefOid}/check-runs",
        cursor_field=None,
        created_at_field="started_at",
        indexed=False,
        key_scope="global",
    ),
    _stream(
        "collaborators",
        parent="repositories",
        path="/repos/{owner.login}/{name}/collaborators",
        cursor_field=None,
    ),
    _stream(
        "commit_comments",
        parent="repositories",
        path="/repos/{owner.login}/{name}/comments",
        cursor_field="updated_at",
        canonical=True,
    ),
    _stream(
        "commits",
        parent="repositories",
        path="/repos/{owner.login}/{name}/commits",
        primary_key="sha",
        cursor_field="commit.committer.date",
        created_at_field="commit.committer.date",
        ordering=Ordering.newest_first,
        backfill_window_days=REPO_BACKFILL_WINDOW_DAYS,
    ),
    _stream(
        "contributor_activity",
        parent="repositories",
        path="/repos/{owner.login}/{name}/stats/contributors",
        primary_key="author.id",
        cursor_field=None,
        indexed=False,
    ),
    _stream(
        "deployments",
        parent="repositories",
        path="/repos/{owner.login}/{name}/deployments",
        cursor_field="updated_at",
    ),
    _stream(
        "events",
        parent="repositories",
        path="/repos/{owner.login}/{name}/events",
        cursor_field="created_at",
        ordering=Ordering.newest_first,
        backfill_window_days=REPO_BACKFILL_WINDOW_DAYS,
    ),
    _stream(
        "issue_events",
        parent="repositories",
        path="/repos/{owner.login}/{name}/issues/events",
        cursor_field="created_at",
        ordering=Ordering.newest_first,
        backfill_window_days=REPO_BACKFILL_WINDOW_DAYS,
    ),
    _stream(
        "issue_labels",
        parent="repositories",
        path="/repos/{owner.login}/{name}/labels",
        cursor_field=None,
    ),
    _stream(
        "projects",
        parent="repositories",
        path="/repos/{owner.login}/{name}/projects",
        cursor_field="updated_at",
    ),
    _stream(
        "pull_requests",
        parent="repositories",
        path="/repos/{owner.login}/{name}/pulls",
        primary_key="databaseId",
        cursor_field="updatedAt",
        created_at_field="createdAt",
        updated_at_field="updatedAt",
        ordering=Ordering.newest_first,
        backfill_window_days=REPO_BACKFILL_WINDOW_DAYS,
        canonical=True,
        pass_interval_seconds=PULL_REQUEST_PASS_INTERVAL_SECONDS,
        fetch_budget=PULL_REQUEST_FETCH_BUDGET,
    ),
    _stream(
        "releases",
        parent="repositories",
        path="/repos/{owner.login}/{name}/releases",
        cursor_field="created_at",
        canonical=True,
    ),
    _stream(
        "review_comments",
        parent="repositories",
        path="/repos/{owner.login}/{name}/pulls/comments",
        cursor_field="updated_at",
        ordering=Ordering.ascending,
        backfill_window_days=COMMENT_BACKFILL_WINDOW_DAYS,
        canonical=True,
    ),
    _stream(
        "stargazers",
        parent="repositories",
        path="/repos/{owner.login}/{name}/stargazers",
        cursor_field="starred_at",
        created_at_field="starred_at",
        indexed=False,
    ),
    _stream(
        "tags",
        parent="repositories",
        path="/repos/{owner.login}/{name}/tags",
        primary_key="name",
        cursor_field=None,
    ),
    _stream(
        "workflow_runs",
        parent="repositories",
        path="/repos/{owner.login}/{name}/actions/runs",
        cursor_field="updated_at",
        backfill_window_days=WORKFLOW_RUNS_BACKFILL_WINDOW_DAYS,
        indexed=False,
    ),
    _stream(
        "workflows",
        parent="repositories",
        path="/repos/{owner.login}/{name}/actions/workflows",
        cursor_field="updated_at",
        canonical=True,
    ),
]

ORGANIZATIONS_PATH = "/user/orgs"

_RECORD_PATHS: dict[str, str] = {
    "check_runs": "check_runs",
    "workflow_runs": "workflow_runs",
    "workflows": "workflows",
}


class GitHubConnector(RestConnector):
    name = "github"
    base_url = "https://api.github.com"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Accept"] = _GITHUB_ACCEPT
        client.headers["X-GitHub-Api-Version"] = _GITHUB_API_VERSION
        return client

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift a starred-at envelope's user onto the record it wraps."""
        if stream.name != "stargazers":
            return record
        user = record.get("user")
        return {**user, **record} if isinstance(user, dict) else record

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        """The record with its primary key scoped to the partition its edge carried onto it
        (`acme/ufo/main`), which is how every page already landed reads and is titled: a key unique
        only inside one repository names the repository in the body, while the address the adapter
        composes from the scope keeps the key as GitHub spelled it. A dotted key (`author.id`) is
        left as the provider wrote it. A pull request's update cursor is page metadata, not page
        content."""
        content = (
            {key: value for key, value in record.items() if key != stream.updated_at_field}
            if stream.name == "pull_requests"
            else record
        )
        field = next((name for edge in stream.parents for name in edge.carry), None)
        if field is None:
            return super().render(content, stream)
        partition = content.get(field)
        if not isinstance(partition, str) or not partition:
            raise RuntimeError(
                f"github: stream {stream.name!r} fans out over {field!r} but a record carries no "
                "such value"
            )
        if stream.primary_key not in content:
            return super().render(content, stream)
        scoped = {**content, stream.primary_key: f"{partition}/{content[stream.primary_key]}"}
        return super().render(scoped, stream)

    def pinned_partitions(
        self, stream: StreamSpec, resources: tuple[str, ...]
    ) -> PinnedPartitions | None:
        """The pull requests a standing watch names, put ahead of the `pull_requests` catalog and no
        other stream's: a watched resource is a pull request, and `/repos/{owner}/{repo}/pulls/N`
        answers one pull request object, which every other stream's walk would read as a collection
        of none. A resource naming an issue rather than a pull request pins nothing: GitHub numbers
        both in one sequence, so asking for it by number would spend a read a tick on a pull request
        that does not exist."""
        if stream.name != "pull_requests":
            return None
        pinned = _pinned_pulls(resources)
        return partial(_watched_first, pinned) if pinned else None

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        if stream.name == "organizations":
            async for records in self._organization_pages(client):
                yield records
            return
        if not stream.parents:
            raise NotImplementedError(f"github: stream {stream.name!r} has no paginate dispatch")
        floor = (
            None
            if run.backfill_after is None
            else run.backfill_after.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        pages = (
            partial(self._pull_pages, client)
            if stream.name == "pull_requests"
            else partial(self._partition_pages, client, stream, floor)
        )
        async for page in fanned_out(stream, run, pages, floor):
            yield page

    async def _pull_pages(
        self,
        client: httpx.AsyncClient,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One partition of `pull_requests`. A watched partition is one read by number and reports
        its own record's `updatedAt` as the whole span, so the walk's tie rule re-yields it every
        tick however long the pull request has been quiet — the row's floor does not bound it,
        because a member watching a two-year-old pull request asked for that one.

        A repository partition pages the connection newest-first until the walk stops it at the
        watermark or the answer says the point budget cannot pay for the next page. A pull request
        below the resume bound is dropped here, GraphQL taking no time filter — and a page landing
        none of its records grows nothing the adapter's record cap counts, so the page count and the
        repeated cursor are what bound a provider that never drops its next-page signal."""
        repo = partition.scope or ""
        if partition.watched:
            number = partition.path.rsplit("/", 1)[-1]
            variables = _repo_variables(repo) | {"number": int(number)}
            data, spent = await self._graphql(
                client, PULL_REQUEST_QUERY, variables, partition=partition
            )
            node = get_path(data, "repository.pullRequest")
            if node is None:
                raise PartitionSkipped(f"github: {partition.ref} refused")
            record = _pull_record(node)
            spent = await self._pull_tails(client, partition, variables, node, record, spent)
            yield WalkPage(records=[record], high=record["updatedAt"], low=record["updatedAt"])
            if spent is not None:
                raise ProviderRateLimited(spent)
            return
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(PULL_REQUEST_MAX_PAGES):
            data, spent = await self._graphql(
                client,
                PULL_REQUESTS_QUERY,
                _repo_variables(repo) | {"cursor": cursor},
                partition=partition,
            )
            connection = get_path(data, "repository.pullRequests")
            if connection is None:
                raise PartitionSkipped(f"github: {partition.ref} refused")
            page = [_pull_record(node) for node in list_or_empty(connection.get("nodes"))]
            landed = [
                record
                for record in page
                if (bound.before is None or record["updatedAt"] <= bound.before)
                and (bound.since is None or record["updatedAt"] >= bound.since)
            ]
            yield WalkPage(
                records=landed,
                high=max((record["updatedAt"] for record in landed), default=None),
                low=min((record["updatedAt"] for record in page), default=None),
            )
            info = dict_or_empty(connection.get("pageInfo"))
            cursor = info.get("endCursor") if info.get("hasNextPage") else None
            if cursor is None:
                return
            if cursor in seen:
                raise StreamFault(f"github: {partition.ref} repeated cursor {cursor!r}")
            seen.add(cursor)
            if spent is not None:
                raise ProviderRateLimited(spent)
        raise StreamFault(
            f"github: {partition.ref} paged past {PULL_REQUEST_MAX_PAGES} pages of pull requests"
        )

    async def _pull_tails(
        self,
        client: httpx.AsyncClient,
        partition: Partition,
        variables: dict[str, Any],
        node: dict[str, Any],
        record: dict[str, Any],
        spent: float | None,
    ) -> float | None:
        """The rest of a watched pull request's threads, checks and files, a page of
        `PULL_REQUEST_TAIL_PAGE_SIZE` at a time until each connection answers no next page. The
        first read shares the catalog's clipped field set, so a pull request inside the clips spends
        nothing here. A read that spends the budget while pages remain raises before the page lands:
        a watched page missing files or checks would read as files removed and checks gone. Returns
        the wait the last read reported, for the caller to raise once the page has landed."""
        for tail in PULL_REQUEST_TAILS:
            cursor = _next_page(tail.connection(node))
            for _ in range(PULL_REQUEST_TAIL_MAX_PAGES):
                if cursor is None:
                    break
                if spent is not None:
                    raise ProviderRateLimited(spent)
                data, spent = await self._graphql(
                    client, tail.query, variables | {"after": cursor}, partition=partition
                )
                answered = dict_or_empty(get_path(data, "repository.pullRequest"))
                connection = tail.connection(answered)
                if connection is None:
                    raise StreamFault(
                        f"github: {partition.ref} answered no {tail.name} page after {cursor!r}"
                    )
                tail.landed(record).extend(
                    _unwrapped(item) for item in list_or_empty(connection.get("nodes"))
                )
                cursor = _next_page(connection)
            else:
                raise StreamFault(
                    f"github: {partition.ref} paged past {PULL_REQUEST_TAIL_MAX_PAGES} pages of "
                    f"{tail.name}"
                )
        return spent

    async def _graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        variables: dict[str, Any],
        *,
        partition: Partition,
    ) -> tuple[dict[str, Any], float | None]:
        """One GraphQL read, and the seconds until the point budget refills when the answer says
        this read spent more than is left. GraphQL answers a refusal with HTTP 200 and an `errors`
        array, so a caller reading `data` alone lands nothing and reads as a quiet stream.

        A refusal that is about this repository alone — deleted, renamed, access withdrawn, or
        behind a SAML session this grant has not authorized — skips the partition the way a REST
        403 or 404 does. The rest fail the run: a malformed query or a field GitHub withdrew is
        wrong for every partition, and dropping those one repository at a time would leave a stream
        that reads as quiet forever.

        Every query asks for its own price, and each answer is recorded as it arrives:
        `source_sync.graphql_rate_limit` carries what this read cost and what the hour has left, so
        the spend of a connection is readable per read rather than inferred from a refusal. It
        reports and refuses nothing on its own — what closes the loop is a monitor on `remaining`
        falling under the next pass's projected cost, which is `3 x repositories` for this stream
        and which nothing here can see, since a row knows only its own connection."""
        body = await self._post(client, GRAPHQL_PATH, json={"query": query, "variables": variables})
        errors = list_or_empty(body.get("errors"))
        if errors:
            reason = str(errors[0].get("message", ""))[:GRAPHQL_ERROR_CHARS]
            if {str(error.get("type", "")) for error in errors} <= GRAPHQL_PARTITION_REFUSALS:
                raise PartitionSkipped(f"github: {partition.ref} refused: {reason}")
            raise StreamFault(f"github: graphql refused {partition.ref}: {reason}")
        data = dict_or_empty(body.get("data"))
        rate_limit = dict_or_empty(data.get("rateLimit"))
        log(
            "source_sync.graphql_rate_limit",
            stream="pull_requests",
            cost=str(rate_limit.get("cost", "")),
            remaining=str(rate_limit.get("remaining", "")),
            reset_at=str(rate_limit.get("resetAt", "")),
            watched=str(partition.watched).lower(),
        )
        return data, _budget_spent(rate_limit)

    async def _organization_pages(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """The root of the tree. A grant the whole account lacks org scope for, or one a policy
        gates, is refused here with a 403 and has no organization to hang anything under: raise
        `StreamSkipped` so this row records a skip rather than failing, and every stream below it
        finds no partition and spends no request."""
        try:
            async for page in self._paginate_link_header(
                client, ORGANIZATIONS_PATH, params={"per_page": PAGE_SIZE}
            ):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 403:
                raise StreamSkipped(
                    "github: org enumeration refused (403); the grant is missing org scope"
                ) from error
            raise

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        floor: str | None,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One parent's bounded page slice for `PartitionWalk`, applying the resume `bound` in
        GitHub's own terms: an ascending `?since` walk sends `sort=updated&direction=asc&since`;
        `commits` bounds a newest-first backfill server-side with `?until`; `events` and
        `issue_events` expose no time filter, so a backfill is bounded
        client-side by dropping records at or above `before`. Each page reports
        its cursor-value span so the walk tracks the watermark/window; a partition that is gone or
        disabled (404/409/410) drops out without failing the run. A 403 fails it: GitHub answers an
        exhausted rate limit with a 403, and skipping it would sweep the run as complete, reset the
        error count and re-issue the same sweep on the next tick with no backoff.

        The pinned floor arrives as `bound.since` on a descending walk and as `bound.after` on a
        climbing one, and takes the same two roads: `commits` and the three ascending streams send
        it as `?since`, so the older history is never fetched;
        `events` and `issue_events` filter it client-side, which caps what lands but not
        what is fetched, their API having no time filter. All compare as the ISO strings GitHub
        returns.

        `workflow_runs` is ordered `none`, so `PartitionWalk` hands it no bound at all and its floor
        arrives as `floor` instead — sent as the Actions API's `created=>=` range, which bounds the
        repo's runs server-side on every pass."""
        params = _partition_params(stream, floor, bound)
        semaphore = asyncio.Semaphore(_USERS_ENRICH_CONCURRENCY) if stream.name == "users" else None
        try:
            async for page in self._paginate_link_header(
                client,
                partition.path,
                params=dict(params),
                record_path=_RECORD_PATHS.get(stream.name),
            ):
                if semaphore is not None:
                    page = await self._enrich_users(client, page, semaphore=semaphore)
                page = _kept(page, stream)
                if not page:
                    continue
                landed = page
                if (
                    stream.ordering is Ordering.newest_first
                    and stream.name not in _UNTIL_STREAMS
                    and (bound.before or bound.since)
                ):
                    before, since = bound.before, bound.since
                    field = stream.cursor_field
                    landed = [
                        record
                        for record in page
                        if field
                        and isinstance(record.get(field), str)
                        and (before is None or record[field] <= before)
                        and (since is None or record[field] >= since)
                    ]
                high, _ = _cursor_bounds(landed, stream.cursor_field)
                _, low = _cursor_bounds(page, stream.cursor_field)
                yield WalkPage(records=landed, high=high, low=low)
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _PARTITION_SKIP_STATUS:
                raise PartitionSkipped(f"github: {partition.ref} refused") from error
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
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        record_path: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """RFC 5988 link-header walk. Empty/202 responses (GitHub's stats endpoints answer 202 with
        an empty body while computing) yield nothing rather than raise. `record_path` reaches into
        an enveloped collection; the repo and org catalog walks pass none and read the array."""
        async for page in self._get_link_header_pages(
            client,
            path,
            params=params,
            page_size=PAGE_SIZE,
            parse_records=partial(_parse_records, record_path=record_path),
        ):
            yield page


def _watched_first(
    pinned: Mapping[str, _PinnedPull], enumerated: Sequence[Partition]
) -> Sequence[Partition]:
    """The watched pull requests, addressed under their repository as the landed repository spells
    it and carrying what its catalog partition carries: GitHub answers one repository under every
    spelling of its name and a watch canonicalizes to the lower-cased one, so taking the URL's
    spelling would file `Acme/Repo1`'s pull requests at a second address. A watch on a repository
    this connection does not sync pins nothing — the pull request is outside what the catalog reads,
    so there is nothing to be told about."""
    spelled = {partition.scope.lower(): partition for partition in enumerated if partition.scope}
    ahead: list[Partition] = []
    for ref, pull in pinned.items():
        repo = spelled.get(pull.repo)
        if repo is not None:
            ahead.append(
                Partition(
                    ref=ref,
                    path=f"/repos/{repo.scope}/pulls/{pull.number}",
                    scope=repo.scope,
                    carried=repo.carried,
                    watched=True,
                )
            )
    return ahead


def _pinned_pulls(resources: tuple[str, ...]) -> dict[str, _PinnedPull]:
    """The partitions the watched resources pin, keyed by the cursor entry each one owns. A resource
    naming an issue rather than a pull request pins nothing here: GitHub numbers both in one
    sequence, so asking for it by number would spend a read per tick on a pull request that does not
    exist."""
    pinned: dict[str, _PinnedPull] = {}
    for resource in resources:
        match = _RESOURCE_URLS[0].match(resource)
        if match is None or match.group("path") != "pull":
            continue
        repo = f"{match.group('owner')}/{match.group('repo')}".lower()
        number = int(match.group("number"))
        pinned[f"pull_requests/{repo}/{number}"] = _PinnedPull(repo=repo, number=number)
    return pinned


def _repo_variables(repo: str) -> dict[str, Any]:
    owner, _, name = repo.partition("/")
    return {"owner": owner, "name": name}


def _pull_record(node: dict[str, Any]) -> dict[str, Any]:
    """One pull request as it lands: every GraphQL connection replaced by the list it wraps with its
    total beside it, and the commit's check rollup lifted to `checks` — the field the walk exists to
    carry, which GraphQL publishes only under the last commit. The totals and the rollup's counts by
    state are what make the digest honest where the lists are clipped: a check flipping or a comment
    landing past the clip moves a count, so the page moves with it.

    A `mergeable` of `UNKNOWN` is dropped rather than stored. GitHub answers it while its background
    merge test runs, so storing it would make one recompute two page changes; `MERGEABLE` and
    `CONFLICTING` are facts about the branch and change the page when they flip."""
    record = _unwrapped(node)
    commits = record.pop("commits")
    record["checks"] = get_path(commits[0], "commit.statusCheckRollup") if commits else None
    if record.get("mergeable") == MERGEABILITY_PENDING:
        record.pop("mergeable")
    return record


_CONNECTION_META = frozenset({"nodes", "totalCount", "pageInfo"})


def _unwrapped(value: Any) -> Any:
    """A GraphQL connection as the list it wraps, `totalCount` kept beside it as `<name>Count`, its
    page cursor dropped, and whatever else it carries — the rollup's counts by state — lifted beside
    the list."""
    match value:
        case dict():
            flat: dict[str, Any] = {}
            for key, item in value.items():
                match item:
                    case {"nodes": list() as nodes}:
                        flat[key] = [_unwrapped(node) for node in nodes]
                        if "totalCount" in item:
                            flat[f"{key}Count"] = item["totalCount"]
                        flat |= {
                            name: _unwrapped(extra)
                            for name, extra in item.items()
                            if name not in _CONNECTION_META
                        }
                    case _:
                        flat[key] = _unwrapped(item)
            return flat
        case list():
            return [_unwrapped(item) for item in value]
        case _:
            return value


def _next_page(connection: dict[str, Any] | None) -> str | None:
    info = dict_or_empty(dict_or_empty(connection).get("pageInfo"))
    return info.get("endCursor") if info.get("hasNextPage") else None


def _budget_spent(rate_limit: dict[str, Any]) -> float | None:
    """The seconds until GitHub's hourly points refill, when what is left will not pay for another
    read of the size just made, and None while the budget holds."""
    cost = rate_limit.get("cost")
    remaining = rate_limit.get("remaining")
    reset = rate_limit.get("resetAt")
    if not isinstance(cost, int) or not isinstance(remaining, int) or remaining > cost:
        return None
    refill = datetime.strptime(str(reset), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return max((refill - datetime.now(UTC)).total_seconds(), GRAPHQL_BUDGET_MIN_WAIT_SECONDS)


def _partition_params(
    stream: StreamSpec, floor: str | None, bound: PartitionBound
) -> dict[str, Any]:
    """One partition request's query, carrying the walk's resume `bound` in GitHub's own terms. See
    `_partition_pages`."""
    params: dict[str, Any] = {"per_page": PAGE_SIZE}
    if stream.name == "issues":
        params["state"] = "all"
    if stream.name == "repositories":
        params |= _REPO_LIST_PARAMS
    if stream.ordering is Ordering.ascending:
        params |= {"sort": "updated", "direction": "asc"}
        if bound.after:
            params["since"] = bound.after
    elif stream.name in _UNTIL_STREAMS:
        if bound.before:
            params["until"] = bound.before
        if bound.since:
            params["since"] = bound.since
    elif stream.name in _CREATED_FLOOR_STREAMS and floor:
        params["created"] = f">={floor}"
    return params


def _kept(page: list[dict[str, Any]], stream: StreamSpec) -> list[dict[str, Any]]:
    """The records of a page this stream lands: `/issues` returns pull requests too and
    `pull_requests` carries them already, and the repo catalog leaves out what nobody reads — an
    archived repository and a fork, whose collections restate their upstream's."""
    match stream.name:
        case "issues":
            return [record for record in page if "pull_request" not in record]
        case "repositories":
            return [
                record for record in page if not record.get("archived") and not record.get("fork")
            ]
        case _:
            return page


def _parse_records(response: httpx.Response, record_path: str | None) -> list[dict[str, Any]]:
    if not response.content:
        return []
    return records_at(response.json(), record_path)


def _cursor_bounds(
    page: list[dict[str, Any]], cursor_field: str | None
) -> tuple[str | None, str | None]:
    """The highest and lowest `cursor_field` values on a page, for the walk's watermark/window
    tracking. None when the stream carries no cursor field."""
    if not cursor_field:
        return None, None
    values = [value for record in page if isinstance(value := get_path(record, cursor_field), str)]
    if not values:
        return None, None
    return max(values), min(values)


def resource_url(url: str) -> str | None:
    """The canonical link of the pull request or issue `url` names —
    `https://github.com/<owner>/<repo>/pull/<n>` or `.../issues/<n>` — or None: a repository, a
    commit, or a listing is not a resource a trigger narrows to. The repository is case-folded
    because GitHub answers one repository under every spelling of its name, and the API form, a
    sub-page and a fragment all name the same resource, so one pull request is one watch however it
    was linked."""
    for pattern in _RESOURCE_URLS:
        match = pattern.match(url.strip())
        if match is None:
            continue
        repo = f"{match.group('owner')}/{match.group('repo')}".lower()
        path = "issues" if match.group("path") == "issues" else "pull"
        return f"https://github.com/{repo}/{path}/{match.group('number')}"
    return None


def resource_aliases(resource: str) -> tuple[str, ...]:
    """The URL forms GitHub's own records carry for one canonical resource: its page's `html_url`,
    and the API forms a comment's `issue_url`, a review comment's `pull_request_url` and a workflow
    run's `pull_requests[].url` link with. GitHub numbers pull requests and issues in one sequence
    per repository and files a pull request's comments under `issues/<number>`, so both paths name
    the one resource."""
    match = _RESOURCE_URLS[0].match(resource)
    if match is None:
        return ()
    repo = f"{match.group('owner')}/{match.group('repo')}"
    number = match.group("number")
    return (
        f"github.com/{repo}/pull/{number}",
        f"github.com/{repo}/issues/{number}",
        f"api.github.com/repos/{repo}/pulls/{number}",
        f"api.github.com/repos/{repo}/issues/{number}",
    )
