"""The Jira connector — projects, issues, their comments, users, boards, and sprints from an
Atlassian Cloud site synced into recallable pages.

Atlassian Cloud fans out from the OAuth token itself: `GET /oauth/token/accessible-resources` names
every site (its `cloud_id`) the grant reaches, and each stream's requests are scoped under
`/ex/jira/{cloud_id}/...`, so a newly-shared site lands on the next sync with no manual config. Jira
paginates its REST collections with `startAt`+`maxResults` and terminates on `isLast`/`total`;
`users/search` answers a bare JSON array read as a single page. `issues` are incremental: the run
filters `ORDER BY updated ASC` past the stored `updated` watermark with a JQL `updated > "<cursor>"`
clause, and `issue_comments`/`sprints` fan out per issue/board and advance their own watermark. A
grant that can't reach a site or resource (`401`/`403`) yields `StreamSkipped` so the run records a
skip, not a failure. `render` lifts an issue into its summary, status, assignee, and description
text (Jira's description and comment bodies are Atlassian Document Format trees, walked by
`_doc_text`) rather than the raw JSON. The credential is resolved through the auth proxy the runner
threads — this connector holds no token. The write path is intentionally absent — the source seam
only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import StreamSkipped
from selfhost_ext_sources.connector import StreamSpec
from selfhost_ext_sources.rest import RestConnector, list_or_empty, records_at, with_context

PAGE_SIZE = 100
ISSUE_FIELDS = "summary,description,status,priority,created,updated,project,assignee,reporter"
_REFUSAL_STATUS = frozenset({401, 403})

JIRA_STREAMS: list[StreamSpec] = [
    StreamSpec(name="projects", source_object="project", primary_key="id"),
    StreamSpec(name="issues", source_object="issue", primary_key="id", cursor_field="updated"),
    StreamSpec(
        name="issue_comments",
        source_object="comment",
        primary_key="id",
        cursor_field="updated",
        canonical=False,
    ),
    StreamSpec(name="users", source_object="user", primary_key="accountId"),
    StreamSpec(name="boards", source_object="board", primary_key="id", canonical=False),
    StreamSpec(
        name="sprints",
        source_object="sprint",
        primary_key="id",
        cursor_field="updatedDate",
        canonical=False,
    ),
]


class JiraConnector(RestConnector):
    name = "jira"
    base_url = "https://api.atlassian.com"
    streams_list = JIRA_STREAMS

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            match stream.name:
                case "projects":
                    async for page in self._projects(client):
                        yield page
                case "issues":
                    async for page in self._issues(client, cursor=cursor):
                        yield page
                case "issue_comments":
                    async for page in self._comments(client, cursor=cursor):
                        yield page
                case "users":
                    async for page in self._users(client):
                        yield page
                case "boards":
                    async for page in self._boards(client):
                        yield page
                case "sprints":
                    async for page in self._sprints(client, cursor=cursor):
                        yield page
                case _:
                    raise StreamSkipped(f"jira stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"jira: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks access to this site or resource"
                ) from error
            raise

    async def _sites(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        """The sites the grant reaches, each carrying the `cloud_id` (`id`) every stream scopes its
        paths under. A refusal here means the grant reaches no site, so every stream skips."""
        response = await self._get_raw(client, "/oauth/token/accessible-resources")
        return list_or_empty(response.json() if response.content else [])

    async def _offset_values(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        result_key: str = "values",
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """A `startAt`+`maxResults` collection read from an envelope list path, terminating on
        `isLast`/`total`."""
        start_at = 0
        while True:
            query = {"startAt": start_at, "maxResults": PAGE_SIZE, **(params or {})}
            data = await self._get(client, path, params=query)
            records = records_at(data, result_key)
            if records:
                yield records
            start_at += len(records)
            total = data.get("total")
            if data.get("isLast") or not records or (isinstance(total, int) and start_at >= total):
                return

    async def _projects(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        for site in await self._sites(client):
            cloud_id = site.get("id")
            if not isinstance(cloud_id, str) or not cloud_id:
                continue
            path = f"/ex/jira/{cloud_id}/rest/api/3/project/search"
            async for page in self._offset_values(client, path):
                yield with_context(page, cloud_id=cloud_id, site_url=site.get("url"))

    async def _issues(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        jql = f'updated > "{cursor}" ORDER BY updated ASC' if cursor else "ORDER BY updated ASC"
        for site in await self._sites(client):
            cloud_id = site.get("id")
            if not isinstance(cloud_id, str) or not cloud_id:
                continue
            path = f"/ex/jira/{cloud_id}/rest/api/3/search"
            async for page in self._offset_values(
                client, path, params={"jql": jql, "fields": ISSUE_FIELDS}, result_key="issues"
            ):
                yield with_context(page, cloud_id=cloud_id, site_url=site.get("url"))

    async def _comments(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for issues in self._issues(client, cursor=None):
            for issue in issues:
                issue_id = issue.get("id")
                cloud_id = issue.get("cloud_id")
                if not isinstance(issue_id, str) or not isinstance(cloud_id, str):
                    continue
                path = f"/ex/jira/{cloud_id}/rest/api/3/issue/{issue_id}/comment"
                async for comments in self._offset_values(client, path, result_key="comments"):
                    if cursor:
                        comments = [c for c in comments if str(c.get("updated") or "") > cursor]
                    if comments:
                        yield with_context(
                            comments,
                            cloud_id=cloud_id,
                            site_url=issue.get("site_url"),
                            issue_id=issue_id,
                            issue_key=issue.get("key"),
                        )

    async def _users(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        for site in await self._sites(client):
            cloud_id = site.get("id")
            if not isinstance(cloud_id, str) or not cloud_id:
                continue
            path = f"/ex/jira/{cloud_id}/rest/api/3/users/search"
            response = await self._get_raw(
                client, path, params={"startAt": 0, "maxResults": PAGE_SIZE}
            )
            users = list_or_empty(response.json() if response.content else [])
            if users:
                yield with_context(users, cloud_id=cloud_id, site_url=site.get("url"))

    async def _boards(self, client: httpx.AsyncClient) -> AsyncIterator[list[dict[str, Any]]]:
        for site in await self._sites(client):
            cloud_id = site.get("id")
            if not isinstance(cloud_id, str) or not cloud_id:
                continue
            path = f"/ex/jira/{cloud_id}/rest/agile/1.0/board"
            async for page in self._offset_values(client, path):
                yield with_context(page, cloud_id=cloud_id, site_url=site.get("url"))

    async def _sprints(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for boards in self._boards(client):
            for board in boards:
                board_id = board.get("id")
                cloud_id = board.get("cloud_id")
                if board_id is None or not isinstance(cloud_id, str):
                    continue
                path = f"/ex/jira/{cloud_id}/rest/agile/1.0/board/{board_id}/sprint"
                async for sprints in self._offset_values(client, path):
                    if cursor:
                        sprints = [s for s in sprints if str(s.get("updatedDate") or "") > cursor]
                    if sprints:
                        yield with_context(sprints, cloud_id=cloud_id, board_id=board_id)

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        """Lift the nested `fields.updated` an issue carries into the flat `updated` the sync
        advances its watermark over; every other stream's cursor and primary key already sit at the
        top level, so they pass through untouched."""
        if stream.name == "issues":
            fields = _dict_or_empty(record.get("fields"))
            return {**record, "updated": fields.get("updated")}
        return record

    def render(self, record: dict[str, Any], stream: StreamSpec) -> tuple[str, str]:
        match stream.name:
            case "issues":
                fields = _dict_or_empty(record.get("fields"))
                title = _str(fields.get("summary"))
                status = _dict_or_empty(fields.get("status")).get("name")
                priority = _dict_or_empty(fields.get("priority")).get("name")
                meta = "\n".join(
                    line
                    for line in (
                        _field_line("Status", _str(status)),
                        _field_line("Priority", _str(priority)),
                        _field_line("Assignee", _person(fields.get("assignee"))),
                        _field_line("Reporter", _person(fields.get("reporter"))),
                    )
                    if line
                )
                body = "\n\n".join(
                    part for part in (meta, _doc_text(fields.get("description"))) if part
                )
            case "issue_comments":
                title = _person(record.get("author"))
                body = _doc_text(record.get("body"))
            case _:
                return super().render(record, stream)
        heading = f"# jira {stream.name}: {title}".rstrip()
        return title, f"{heading}\n\n{body}".rstrip()


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


def _dict_or_empty(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _person(value: Any) -> str:
    person = _dict_or_empty(value)
    return _str(person.get("displayName")) or _str(person.get("emailAddress"))


def _field_line(label: str, value: str) -> str:
    return f"{label}: {value}" if value else ""


def _doc_text(value: Any) -> str:
    """Walk an Atlassian Document Format tree into its readable text (its `text` leaves joined by
    newline). A plain string or absent body walks to empty."""
    chunks: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str):
                chunks.append(text)
            for child in node.get("content") or []:
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(value)
    return "\n".join(part for part in chunks if part).strip()
