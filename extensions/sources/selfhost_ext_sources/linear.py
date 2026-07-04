"""The Linear connector — work tracking (issues, projects, cycles, teams, …) synced into recallable
pages. The GraphQL provider on the connector framework.

Linear speaks only GraphQL: every stream is one top-level query against `POST /graphql` shaped
`{ <resource>(first: N, after: $after, filter: $filter, orderBy: $orderBy) { nodes { … }
pageInfo { hasNextPage endCursor } } }`. `paginate` POSTs one page at a time through the shared
`_post` retry envelope, threads `pageInfo.endCursor` into the next request's `after`, and stops when
`hasNextPage` is false. A stream Linear filters by `updatedAt` (`accepts_filter`) sorts by
`updatedAt` and, once a watermark exists, gates the server with `filter: { updatedAt: { gte:
<cursor> } }` so only rows changed since the last run come back; the adapter advances the watermark
over `updatedAt`. The four collections Linear exposes without an `updatedAt` filter (customer
statuses/tiers, issue relations, project statuses) full-refresh each run. `render` lifts an
issue/project/comment/user into readable prose (title, description, state, assignee) rather than the
default GraphQL-node JSON dump. A refusal (HTTP 401/403) raises `StreamSkipped` so the run records a
skip; a GraphQL `errors` array fails loud rather than commit a partial page. The credential is
resolved through the auth proxy the runner threads (an OAuth bearer works on the base client
unchanged); this connector holds no token. The write path (mutations) is intentionally absent — the
source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import RestConnector, StreamSkipped, StreamSpec, list_or_empty

GRAPHQL_PATH = "/graphql"
ORDER_BY_UPDATED_AT = "updatedAt"
_REFUSAL_STATUS = frozenset({401, 403})


def _stream(
    name: str, *, cursor_field: str | None = ORDER_BY_UPDATED_AT, canonical: bool = False
) -> StreamSpec:
    return StreamSpec(name=name, source_object=name, cursor_field=cursor_field, canonical=canonical)


# Stream set mirrors Airbyte's source-linear catalog (16 streams): the five canonical content
# streams (projects, issues, project_milestones, comments, users) plus the L0 metadata streams. The
# four collections Linear exposes without an `updatedAt` filter (customer_statuses, customer_tiers,
# issue_relations, project_statuses) carry no cursor and full-refresh each run.
LINEAR_STREAMS: list[StreamSpec] = [
    _stream("projects", canonical=True),
    _stream("issues", canonical=True),
    _stream("project_milestones", canonical=True),
    _stream("comments", canonical=True),
    _stream("users", canonical=True),
    _stream("attachments"),
    _stream("customer_needs"),
    _stream("customer_statuses", cursor_field=None),
    _stream("customer_tiers", cursor_field=None),
    _stream("customers"),
    _stream("cycles"),
    _stream("issue_labels"),
    _stream("issue_relations", cursor_field=None),
    _stream("project_statuses", cursor_field=None),
    _stream("teams"),
    _stream("workflow_states"),
]


# GraphQL read query per stream, transcribed from Airbyte's source-linear manifest. Each selects the
# `nodes` a page carries plus `pageInfo { hasNextPage endCursor }`. Streams Linear filters by
# `updatedAt` declare the nullable `$filter`/`$orderBy` variables `paginate` threads for incremental
# runs; the four full-refresh collections take only `$after`.
ISSUES_QUERY = (
    "query Issues($after: String, $filter: IssueFilter, $orderBy: PaginationOrderBy) "
    "{ issues(after: $after, first: 25, filter: $filter, orderBy: $orderBy) "
    "{ nodes { id addedToCycleAt addedToProjectAt addedToTeamAt archivedAt "
    "assignee { id } autoArchivedAt autoClosedAt branchName canceledAt "
    "completedAt createdAt creator { id } customerTicketCount cycle { id } "
    "description descriptionState dueDate estimate identifier "
    "integrationSourceType labelIds number parent { id } previousIdentifiers "
    "priority priorityLabel prioritySortOrder project { id } "
    "projectMilestone { id } reactionData snoozedBy { id } snoozedUntilAt "
    "sortOrder startedAt state { id type } startedTriageAt subIssueSortOrder "
    "team { id } title trashed triagedAt updatedAt url "
    "attachments { nodes { id } } sourceComment { id } "
    "labels { nodes { id name } } slaType slaStartedAt slaMediumRiskAt "
    "slaHighRiskAt slaBreachesAt relations { nodes { id } } "
    "subscribers { nodes { id } } } "
    "pageInfo { hasNextPage endCursor } } }"
)

CUSTOMERS_QUERY = (
    "query Customers($after: String, $filter: CustomerFilter, $orderBy: PaginationOrderBy) "
    "{ customers(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { approximateNeedCount archivedAt createdAt domains externalIds "
    "id logoUrl name owner { id } revenue size slackChannelId slugId "
    "status { id } tier { id } updatedAt } "
    "pageInfo { endCursor hasNextPage } } }"
)

USERS_QUERY = (
    "query Users($after: String, $filter: UserFilter, $orderBy: PaginationOrderBy) "
    "{ users(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { active admin archivedAt avatarBackgroundColor avatarUrl "
    "calendarHash createdAt createdIssueCount description disableReason "
    "displayName email guest id initials inviteHash isMe lastSeen name "
    "statusEmoji statusLabel statusUntilAt teams { nodes { id } } timezone "
    "updatedAt url } pageInfo { hasNextPage endCursor } } }"
)

COMMENTS_QUERY = (
    "query Comments($after: String, $filter: CommentFilter, $orderBy: PaginationOrderBy) "
    "{ comments(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt body bodyData createdAt editedAt id "
    "issue { id } parent { id } quotedText resolvedAt "
    "resolvingComment { id } resolvingUser { id } updatedAt url "
    "user { id } } pageInfo { hasNextPage endCursor } } }"
)

CYCLES_QUERY = (
    "query Cycles($after: String, $filter: CycleFilter, $orderBy: PaginationOrderBy) "
    "{ cycles(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ pageInfo { endCursor hasNextPage } "
    "nodes { archivedAt autoArchivedAt completedAt completedIssueCountHistory "
    "completedScopeHistory createdAt description id endsAt "
    "inProgressScopeHistory inheritedFrom { id } issueCountHistory name "
    "number progress scopeHistory startsAt team { id } "
    "uncompletedIssuesUponClose { nodes { id } } updatedAt } } }"
)

CUSTOMER_NEEDS_QUERY = (
    "query CustomerNeeds($after: String, $filter: CustomerNeedFilter, "
    "$orderBy: PaginationOrderBy) "
    "{ customerNeeds(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt body bodyData comment { id } createdAt "
    "creator { id } customer { id } id issue { id } priority "
    "project { id } updatedAt attachment { id } } "
    "pageInfo { hasNextPage endCursor } } }"
)

PROJECTS_QUERY = (
    "query Projects($after: String, $filter: ProjectFilter, $orderBy: PaginationOrderBy) "
    "{ projects(after: $after, first: 25, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt autoArchivedAt canceledAt color completedAt "
    "completedIssueCountHistory completedScopeHistory content contentState "
    "convertedFromIssue { id } createdAt creator { id } description health "
    "healthUpdatedAt icon id inProgressScopeHistory issueCountHistory "
    "lead { id } name priority prioritySortOrder progress "
    "projectUpdateRemindersPausedUntilAt scope scopeHistory slugId sortOrder "
    "startDate startDateResolution startedAt state status { id } targetDate "
    "targetDateResolution teams { nodes { id } } trashed "
    "updateReminderFrequency updateReminderFrequencyInWeeks "
    "updateRemindersDay updateRemindersHour updatedAt url } "
    "pageInfo { endCursor hasNextPage } } }"
)

PROJECT_MILESTONES_QUERY = (
    "query ProjectMilestones($after: String, $filter: ProjectMilestoneFilter, "
    "$orderBy: PaginationOrderBy) "
    "{ projectMilestones(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt createdAt description descriptionState id name "
    "progress project { id } sortOrder status targetDate updatedAt } "
    "pageInfo { hasNextPage endCursor } } }"
)

PROJECT_STATUSES_QUERY = (
    "query ProjectStatus($after: String) "
    "{ projectStatuses(after: $after, first: 50) "
    "{ nodes { archivedAt color createdAt description id indefinite name "
    "position type updatedAt } pageInfo { endCursor hasNextPage } } }"
)

ISSUE_LABELS_QUERY = (
    "query IssueLabels($after: String, $filter: IssueLabelFilter, "
    "$orderBy: PaginationOrderBy) "
    "{ issueLabels(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt color createdAt creator { id } description id "
    "inheritedFrom { id } isGroup name parent { id } team { id } updatedAt } "
    "pageInfo { endCursor hasNextPage } } }"
)

WORKFLOW_STATES_QUERY = (
    "query WorkflowStates($after: String, $filter: WorkflowStateFilter, "
    "$orderBy: PaginationOrderBy) "
    "{ workflowStates(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt color createdAt description id "
    "inheritedFrom { id } name position team { id } type updatedAt } "
    "pageInfo { hasNextPage endCursor } } }"
)

TEAMS_QUERY = (
    "query Teams($after: String, $filter: TeamFilter, $orderBy: PaginationOrderBy) "
    "{ teams(after: $after, first: 25, filter: $filter, orderBy: $orderBy) "
    "{ nodes { activeCycle { id } archivedAt autoArchivePeriod "
    "autoCloseChildIssues autoCloseParentIssues autoClosePeriod "
    "autoCloseStateId color createdAt cycleCalenderUrl cycleCooldownTime "
    "cycleDuration cycleIssueAutoAssignCompleted cycleIssueAutoAssignStarted "
    "cycleLockToActive cycleStartDay cyclesEnabled defaultIssueEstimate "
    "defaultIssueState { id } description groupIssueHistory icon id "
    "inviteHash issueCount issueEstimationAllowZero issueEstimationExtended "
    "issueEstimationType joinByDefault key markedAsDuplicateWorkflowState "
    "{ id } name parent { id } private requirePriorityToLeaveTriage "
    "scimGroupName scimManaged setIssueSortOrderOnStateChange timezone "
    "triageEnabled updatedAt upcomingCycleCount triageIssueState { id } } "
    "pageInfo { endCursor hasNextPage } } }"
)

ATTACHMENTS_QUERY = (
    "query Attachments($after: String, $filter: AttachmentFilter, "
    "$orderBy: PaginationOrderBy) "
    "{ attachments(after: $after, first: 50, filter: $filter, orderBy: $orderBy) "
    "{ nodes { archivedAt createdAt creator { id } groupBySource id "
    "issue { id } sourceType subtitle title updatedAt url } "
    "pageInfo { endCursor hasNextPage } } }"
)

ISSUE_RELATIONS_QUERY = (
    "query IssueRelations($after: String) "
    "{ issueRelations(after: $after, first: 50) "
    "{ nodes { archivedAt createdAt id issue { id } relatedIssue { id } "
    "type updatedAt } pageInfo { endCursor hasNextPage } } }"
)

CUSTOMER_STATUSES_QUERY = (
    "query CustomerStatus($after: String) "
    "{ customerStatuses(after: $after, first: 50) "
    "{ nodes { archivedAt color createdAt description id name position type "
    "updatedAt } pageInfo { endCursor hasNextPage } } }"
)

CUSTOMER_TIERS_QUERY = (
    "query CustomerTiers($after: String) "
    "{ customerTiers(after: $after, first: 50) "
    "{ nodes { archivedAt color createdAt description displayName id name "
    "position updatedAt } pageInfo { endCursor hasNextPage } } }"
)


# stream name → (GraphQL query, response root field, accepts_filter). The root field is the
# camelCase resource key under `data` — not always the snake_case stream name (e.g. `customer_needs`
# → `customerNeeds`). `accepts_filter` governs whether `paginate` threads the `orderBy: updatedAt`
# sort and the `filter: { updatedAt: { gte } }` cursor gate; the four full-refresh collections take
# none.
_STREAM_QUERIES: dict[str, tuple[str, str, bool]] = {
    "issues": (ISSUES_QUERY, "issues", True),
    "customers": (CUSTOMERS_QUERY, "customers", True),
    "users": (USERS_QUERY, "users", True),
    "comments": (COMMENTS_QUERY, "comments", True),
    "cycles": (CYCLES_QUERY, "cycles", True),
    "customer_needs": (CUSTOMER_NEEDS_QUERY, "customerNeeds", True),
    "projects": (PROJECTS_QUERY, "projects", True),
    "project_milestones": (PROJECT_MILESTONES_QUERY, "projectMilestones", True),
    "project_statuses": (PROJECT_STATUSES_QUERY, "projectStatuses", False),
    "issue_labels": (ISSUE_LABELS_QUERY, "issueLabels", True),
    "workflow_states": (WORKFLOW_STATES_QUERY, "workflowStates", True),
    "teams": (TEAMS_QUERY, "teams", True),
    "attachments": (ATTACHMENTS_QUERY, "attachments", True),
    "issue_relations": (ISSUE_RELATIONS_QUERY, "issueRelations", False),
    "customer_statuses": (CUSTOMER_STATUSES_QUERY, "customerStatuses", False),
    "customer_tiers": (CUSTOMER_TIERS_QUERY, "customerTiers", False),
}


class LinearConnector(RestConnector):
    name = "linear"
    base_url = "https://api.linear.app"
    streams_list = LINEAR_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        entry = _STREAM_QUERIES.get(stream.name)
        if entry is None:
            raise NotImplementedError(f"linear: stream {stream.name!r} has no GraphQL query")
        query, root_field, accepts_filter = entry
        variables: dict[str, Any] = {}
        if accepts_filter:
            variables["orderBy"] = ORDER_BY_UPDATED_AT
            if stream.cursor_field and cursor:
                variables["filter"] = {ORDER_BY_UPDATED_AT: {"gte": cursor}}
        after: str | None = None
        while True:
            page_vars = dict(variables)
            if after is not None:
                page_vars["after"] = after
            body: dict[str, Any] = {"query": query}
            if page_vars:
                body["variables"] = page_vars
            try:
                data = await self._post(client, GRAPHQL_PATH, json=body)
            except httpx.HTTPStatusError as error:
                if error.response.status_code in _REFUSAL_STATUS:
                    raise StreamSkipped(
                        f"linear: {stream.name!r} refused (HTTP {error.response.status_code}); "
                        "the grant is missing scope or the token is invalid"
                    ) from error
                raise
            if data.get("errors"):
                raise RuntimeError(f"linear: graphql error on {stream.name!r}: {data['errors']}")
            payload = data.get("data")
            envelope = payload.get(root_field) if isinstance(payload, dict) else None
            if not isinstance(envelope, dict):
                return
            records = list_or_empty(envelope.get("nodes"))
            if records:
                yield records
            page_info = envelope.get("pageInfo")
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
                return
            after = page_info.get("endCursor")
            if not isinstance(after, str) or not after:
                return

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "issues":
                title = _str(record.get("title"))
                state = record.get("state")
                meta = _labeled(
                    [
                        ("id", _str(record.get("identifier"))),
                        ("state", _str(state.get("type")) if isinstance(state, dict) else ""),
                        ("priority", _str(record.get("priorityLabel"))),
                        ("assignee", _ref_id(record.get("assignee"))),
                    ]
                )
                body = f"{meta}\n\n{_str(record.get('description'))}".strip()
            case "projects":
                title = _str(record.get("name"))
                meta = _labeled(
                    [
                        ("state", _str(record.get("state"))),
                        ("lead", _ref_id(record.get("lead"))),
                        ("target", _str(record.get("targetDate"))),
                    ]
                )
                body = f"{meta}\n\n{_str(record.get('description'))}".strip()
            case "comments":
                title = ""
                body = _str(record.get("body"))
            case "users":
                title = _str(record.get("name")) or _str(record.get("displayName"))
                body = _labeled(
                    [
                        ("name", _str(record.get("name"))),
                        ("email", _str(record.get("email"))),
                        ("display", _str(record.get("displayName"))),
                    ]
                )
            case _:
                return super().render(record, stream)
        heading = f"# linear {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _ref_id(value: Any) -> str:
    return _str(value.get("id")) if isinstance(value, dict) else ""


def _labeled(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in pairs if value)
