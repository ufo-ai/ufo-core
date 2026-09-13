"""The ClickUp connector — the team hierarchy (teams, spaces, folders, lists), its members, tasks,
and per-list comments, custom fields and goals synced into recallable pages.

ClickUp has no flat collection: every collection below `/team` is published only under the one above
it — a team's spaces, a space's folders, a folder's lists, a list's tasks, comments and custom
fields, a team's goals. A space publishes its folderless lists at `/space/{id}/list` beside the
folders' own `/folder/{id}/list`, which is two collections and so two edges of `lists`.

A ClickUp id is unique across the workspace, so the four canonical streams declare
`key_scope="global"` and a page is addressed by that id alone — which is why those four carry the
parent id onto each record, where the three `local` streams read it off their own address instead.
`tasks` page by an integer `?page=N` and filter past the stored watermark on `date_updated`, which
ClickUp stamps as a thirteen-digit millisecond epoch; `list_comments` filters on its own `date`.
`users` are collapsed from the members embedded on each team record, so the team listing is a lookup
this connector joins through rather than content an account connects for, and is `indexed=False`.
Auth is the OAuth bearer the resolved `Credential` carries. A refusal (401/403) raises
`StreamSkipped`. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.sources import (
    Ordering,
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    dict_or_empty,
    fanned_out,
    records_at,
)
from ufo_ext_sources.watermark import integer_checkpoint

_REFUSAL_STATUS = frozenset({401, 403})
_RECORD_PATHS = {
    "spaces": "spaces",
    "folders": "folders",
    "lists": "lists",
    "tasks": "tasks",
    "list_comments": "comments",
    "list_custom_fields": "fields",
    "goals": "goals",
}
_ARCHIVED_PARAMS = {"archived": "false"}
_ARCHIVED_STREAMS = frozenset({"spaces", "folders", "lists"})
_TASK_PARAMS = {"archived": "false", "include_closed": "true", "subtasks": "true"}
SPACES_FETCH_BUDGET = 5
FOLDERS_FETCH_BUDGET = 10
LISTS_FETCH_BUDGET = 15
TASKS_FETCH_BUDGET = 15

CLICKUP_STREAMS: list[StreamSpec] = [
    StreamSpec(name="teams", source_object="team", primary_key="id", indexed=False),
    StreamSpec(name="users", source_object="users", primary_key="id"),
    StreamSpec(
        name="spaces",
        source_object="space",
        primary_key="id",
        canonical=True,
        key_scope="global",
        parents=(ParentEdge(stream="teams", path="/team/{id}/space", carry={"team_id": "id"}),),
        fetch_budget=SPACES_FETCH_BUDGET,
    ),
    StreamSpec(
        name="folders",
        source_object="folder",
        primary_key="id",
        canonical=True,
        key_scope="global",
        parents=(ParentEdge(stream="spaces", path="/space/{id}/folder", carry={"space_id": "id"}),),
        fetch_budget=FOLDERS_FETCH_BUDGET,
    ),
    StreamSpec(
        name="lists",
        source_object="list",
        primary_key="id",
        canonical=True,
        key_scope="global",
        parents=(
            ParentEdge(stream="folders", path="/folder/{id}/list", carry={"folder_id": "id"}),
            ParentEdge(stream="spaces", path="/space/{id}/list", carry={"space_id": "id"}),
        ),
        fetch_budget=LISTS_FETCH_BUDGET,
    ),
    StreamSpec(
        name="tasks",
        source_object="task",
        primary_key="id",
        cursor_field="date_updated",
        updated_at_field="date_updated",
        canonical=True,
        ordering=Ordering.ascending,
        key_scope="global",
        parents=(
            ParentEdge(
                stream="lists",
                path="/list/{id}/task",
                carry={"list_id": "id", "list_name": "name"},
            ),
        ),
        fetch_budget=TASKS_FETCH_BUDGET,
    ),
    StreamSpec(
        name="list_comments",
        source_object="comment",
        primary_key="id",
        cursor_field="date",
        created_at_field="date",
        updated_at_field=None,
        ordering=Ordering.ascending,
        parents=(
            ParentEdge(
                stream="lists",
                path="/list/{id}/comment",
                carry={"parent_external_id": "id", "list_name": "name"},
            ),
        ),
    ),
    StreamSpec(
        name="list_custom_fields",
        source_object="field",
        primary_key="id",
        parents=(ParentEdge(stream="lists", path="/list/{id}/field", carry={"list_name": "name"}),),
    ),
    StreamSpec(
        name="goals",
        source_object="goal",
        primary_key="id",
        parents=(ParentEdge(stream="teams", path="/team/{id}/goal"),),
    ),
]


class ClickUpConnector(RestConnector):
    name = "clickup"
    base_url = "https://api.clickup.com/api/v2"
    streams_list = CLICKUP_STREAMS
    checkpoint = staticmethod(integer_checkpoint)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "teams":
                teams = await self._teams(client)
                if teams:
                    yield teams
                return
            if stream.name == "users":
                users = await self._users(client)
                if users:
                    yield list(users.values())
                return
            pages = partial(self._partition_pages, client, stream)
            async for page in fanned_out(stream, run, pages):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"clickup: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

    async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/team")
        return records_at(data, "teams")

    async def _users(self, client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
        users: dict[str, dict[str, Any]] = {}
        for team in await self._teams(client):
            for member in team.get("members") or []:
                if not isinstance(member, dict):
                    continue
                user = member.get("user")
                if isinstance(user, dict) and user.get("id") is not None:
                    users[str(user["id"])] = {**user, "team_id": team.get("id")}
        return users

    async def _partition_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        partition: Partition,
        bound: PartitionBound,
    ) -> AsyncIterator[WalkPage]:
        """One parent's slice of this stream. Only `/list/{id}/task` pages, by an integer `?page`
        run to the first empty answer; every other collection ClickUp publishes under a parent
        answers whole. `goals` reads two arrays — see `_goals_of`. Neither the task endpoint nor the
        comment one takes a time bound, so the walk's resume filters the page here."""
        if stream.name != "tasks":
            data = await self._get(
                client,
                partition.path,
                params=dict(_ARCHIVED_PARAMS) if stream.name in _ARCHIVED_STREAMS else None,
            )
            records = (
                _goals_of(data)
                if stream.name == "goals"
                else records_at(data, _RECORD_PATHS[stream.name])
            )
            kept = _after(records, stream.cursor_field, bound.after)
            if kept:
                high, low = _span(kept, stream.cursor_field)
                yield WalkPage(records=kept, high=high, low=low)
            return
        page_number = 0
        while True:
            data = await self._get(
                client, partition.path, params={**_TASK_PARAMS, "page": page_number}
            )
            tasks = records_at(data, "tasks")
            kept = _after(tasks, stream.cursor_field, bound.after)
            if kept:
                high, low = _span(kept, stream.cursor_field)
                yield WalkPage(records=kept, high=high, low=low)
            if not tasks:
                return
            page_number += 1

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "users":
            return {
                **record,
                "name": record.get("username") or record.get("name"),
                "email": record.get("email"),
                "created_at": record.get("date_joined"),
            }
        if stream.name in {"spaces", "folders", "lists"}:
            return {
                **record,
                "name": record.get("name"),
                "api_url": record.get("url")
                or f"https://api.clickup.com/api/v2/{stream.source_object}/{record.get('id')}",
            }
        if stream.name == "tasks":
            return {
                **record,
                "name": record.get("name"),
                "status": (record.get("status") or {}).get("status")
                if isinstance(record.get("status"), dict)
                else record.get("status"),
                "due_date": record.get("due_date"),
                "created_at": record.get("date_created"),
            }
        if stream.name == "list_comments":
            user = dict_or_empty(record.get("user"))
            return {
                **record,
                "body": record.get("comment_text") or record.get("comment"),
                "author": user.get("username") or user.get("email") or user.get("id"),
                "created_at": record.get("date"),
            }
        return record


def _goals_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    """A team's goals, both the loose ones and the ones a goal folder holds. ClickUp answers
    `/team/{id}/goal` with `goals` and `folders` side by side, each folder carrying its own `goals`
    (https://developer.clickup.com/reference/getgoals)."""
    held = [goal for folder in records_at(data, "folders") for goal in records_at(folder, "goals")]
    return records_at(data, "goals") + held


def _after(
    records: list[dict[str, Any]], cursor_field: str | None, after: str | None
) -> list[dict[str, Any]]:
    """The records past the walk's resume mark, which the two endpoints that take no time bound
    apply to their own page."""
    if not cursor_field or not after:
        return records
    mark = int(after)
    return [
        record
        for record in records
        if (stamp := _epoch(record.get(cursor_field))) is not None and stamp > mark
    ]


def _span(records: list[dict[str, Any]], cursor_field: str | None) -> tuple[str | None, str | None]:
    """The highest and lowest cursor value on a page, for the walk's watermark. ClickUp's epochs are
    thirteen digits wide, so the walk's own string comparison of them is the numeric one."""
    if not cursor_field:
        return None, None
    stamps = [
        stamp for record in records if (stamp := _epoch(record.get(cursor_field))) is not None
    ]
    return (str(max(stamps)), str(min(stamps))) if stamps else (None, None)


def _epoch(value: Any) -> int | None:
    """ClickUp stamps its instants as millisecond epochs, which it sends as a string and sometimes
    as a number."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    text = str(value)
    return int(text) if text.isdecimal() else None
