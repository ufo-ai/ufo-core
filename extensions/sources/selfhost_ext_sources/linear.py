"""The Linear connector — work-tracking (issues, projects, cycles, teams, …) synced into recallable
pages. The GraphQL provider on the connector framework.

Linear speaks only GraphQL: every stream is one top-level query against `POST /graphql` shaped
`{ <resource>(first: N, after: $after, filter: $filter, orderBy: $orderBy) { nodes { … }
pageInfo { hasNextPage endCursor } } }`. `paginate` POSTs one page at a time through the shared
`_post` retry envelope, threads `pageInfo.endCursor` into the next request's `after`, and stops when
`hasNextPage` is false. An incremental stream (one with a `cursor_field`) sorts by `updatedAt` and,
once a watermark exists, gates the server with `filter: { updatedAt: { gte: <cursor> } }` so only
rows changed since the last run come back; the adapter advances the watermark over `updatedAt`. The
four enumerable full-collections Linear exposes without an `updatedAt` filter (statuses, tiers,
issue relations) re-enumerate each run as `delete_missing` snapshots, so a record that vanished is
tombstoned. `render` lifts an issue/project/comment/user into readable prose (title, description,
state, assignee) rather than the default GraphQL-node JSON dump. A refusal — an auth/permission
`errors` code, or a 401/403 — raises `StreamSkipped` so the run records a skip, not a failure. The
credential is resolved through the auth proxy the runner threads (an OAuth bearer works on the base
client unchanged); this connector holds no token. The write path (mutations) is intentionally
absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import StreamSkipped
from selfhost_ext_sources.connector import StreamSpec
from selfhost_ext_sources.rest import RestConnector, list_or_empty

GRAPHQL_PATH = "/graphql"
ORDER_BY_UPDATED_AT = "updatedAt"
_REFUSAL_STATUS = frozenset({401, 403})
_GRAPHQL_REFUSAL_CODES = frozenset(
    {
        "authentication_error",
        "authentication error",
        "forbidden",
        "feature_not_accessible",
        "feature not accessible",
        "unauthorized",
    }
)


def _incremental(name: str, *, canonical: bool = False) -> StreamSpec:
    """A Linear stream Linear filters by `updatedAt`: incremental, watermarked on `updatedAt`."""
    return StreamSpec(
        name=name, source_object=name, cursor_field=ORDER_BY_UPDATED_AT, canonical=canonical
    )


def _snapshot(name: str) -> StreamSpec:
    """A Linear collection with no `updatedAt` filter: re-enumerated whole each run as a
    `delete_missing` snapshot, so a vanished record is tombstoned."""
    return StreamSpec(name=name, source_object=name, delete_missing=True)


# Stream set mirrors Airbyte's source-linear catalog (16 streams): the five canonical content
# streams (projects, issues, project_milestones, comments, users) plus the L0 metadata streams.
LINEAR_STREAMS: list[StreamSpec] = [
    _incremental("projects", canonical=True),
    _incremental("issues", canonical=True),
    _incremental("project_milestones", canonical=True),
    _incremental("comments", canonical=True),
    _incremental("users", canonical=True),
    _incremental("attachments"),
    _incremental("customer_needs"),
    _incremental("customers"),
    _incremental("cycles"),
    _incremental("issue_labels"),
    _incremental("teams"),
    _incremental("workflow_states"),
    _snapshot("customer_statuses"),
    _snapshot("customer_tiers"),
    _snapshot("issue_relations"),
    _snapshot("project_statuses"),
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


# stream name → (GraphQL query, response root field). The root field is the camelCase resource key
# under `data` — not always the snake_case stream name (e.g. `customer_needs` → `customerNeeds`).
_STREAM_QUERIES: dict[str, tuple[str, str]] = {
    "issues": (ISSUES_QUERY, "issues"),
    "customers": (CUSTOMERS_QUERY, "customers"),
    "users": (USERS_QUERY, "users"),
    "comments": (COMMENTS_QUERY, "comments"),
    "cycles": (CYCLES_QUERY, "cycles"),
    "customer_needs": (CUSTOMER_NEEDS_QUERY, "customerNeeds"),
    "projects": (PROJECTS_QUERY, "projects"),
    "project_milestones": (PROJECT_MILESTONES_QUERY, "projectMilestones"),
    "project_statuses": (PROJECT_STATUSES_QUERY, "projectStatuses"),
    "issue_labels": (ISSUE_LABELS_QUERY, "issueLabels"),
    "workflow_states": (WORKFLOW_STATES_QUERY, "workflowStates"),
    "teams": (TEAMS_QUERY, "teams"),
    "attachments": (ATTACHMENTS_QUERY, "attachments"),
    "issue_relations": (ISSUE_RELATIONS_QUERY, "issueRelations"),
    "customer_statuses": (CUSTOMER_STATUSES_QUERY, "customerStatuses"),
    "customer_tiers": (CUSTOMER_TIERS_QUERY, "customerTiers"),
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
        query, root_field = entry
        variables: dict[str, Any] = {}
        if stream.cursor_field:
            variables["orderBy"] = ORDER_BY_UPDATED_AT
            if cursor:
                variables["filter"] = {stream.cursor_field: {"gte": cursor}}
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
            envelope = self._envelope(data, root_field=root_field, stream=stream)
            records = list_or_empty(envelope.get("nodes"))
            if records:
                yield records
            page_info = envelope.get("pageInfo")
            if not isinstance(page_info, dict) or not page_info.get("hasNextPage"):
                return
            after = page_info.get("endCursor")
            if not isinstance(after, str) or not after:
                return

    def _envelope(
        self, data: dict[str, Any], *, root_field: str, stream: StreamSpec
    ) -> dict[str, Any]:
        """The resource envelope (`nodes`/`pageInfo`) under `data.<root_field>`. GraphQL reports
        failure as a 200 with an `errors` array: an auth/permission code raises `StreamSkipped` so
        the run records a skip; any other error fails loud rather than sync a partial page."""
        errors = data.get("errors")
        if isinstance(errors, list) and errors:
            refusal = _refusal_code(errors)
            if refusal is not None:
                raise StreamSkipped(
                    f"linear: {stream.name!r} refused ({refusal}); the grant lacks the capability"
                )
            raise RuntimeError(f"linear: graphql error on {stream.name!r}: {errors}")
        payload = data.get("data")
        if not isinstance(payload, dict):
            raise RuntimeError(f"linear: graphql response for {stream.name!r} carried no data")
        envelope = payload.get(root_field)
        return envelope if isinstance(envelope, dict) else {}

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "issues":
                title = _str(record.get("title"))
                body = _issue_body(record)
            case "projects":
                title = _str(record.get("name"))
                body = _project_body(record)
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


def _refusal_code(errors: list[Any]) -> str | None:
    """The first auth/permission code among GraphQL errors (Linear carries it in
    `extensions.code`/`extensions.type`), or None if every error is a genuine query fault."""
    for error in errors:
        extensions = error.get("extensions") if isinstance(error, dict) else None
        if not isinstance(extensions, dict):
            continue
        for key in ("code", "type"):
            value = extensions.get(key)
            if isinstance(value, str) and value.strip().lower() in _GRAPHQL_REFUSAL_CODES:
                return value
    return None


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _ref_id(value: Any) -> str:
    return _str(value.get("id")) if isinstance(value, dict) else ""


def _labeled(pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"{label}: {value}" for label, value in pairs if value)


def _issue_body(record: dict[str, Any]) -> str:
    state = record.get("state")
    meta = _labeled(
        [
            ("id", _str(record.get("identifier"))),
            ("state", _str(state.get("type")) if isinstance(state, dict) else ""),
            ("priority", _str(record.get("priorityLabel"))),
            ("assignee", _ref_id(record.get("assignee"))),
        ]
    )
    return f"{meta}\n\n{_str(record.get('description'))}".strip()


def _project_body(record: dict[str, Any]) -> str:
    meta = _labeled(
        [
            ("state", _str(record.get("state"))),
            ("lead", _ref_id(record.get("lead"))),
            ("target", _str(record.get("targetDate"))),
        ]
    )
    return f"{meta}\n\n{_str(record.get('description'))}".strip()
