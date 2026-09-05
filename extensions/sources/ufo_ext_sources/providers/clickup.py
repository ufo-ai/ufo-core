"""The ClickUp connector — the team hierarchy (teams, spaces, folders, lists), its members, tasks,
and per-list comments and custom fields synced into recallable pages.

ClickUp has no flat collection: the connector walks the hierarchy top-down — `/team`, then
`/team/{id}/space`, `/space/{id}/folder`, `/folder/{id}/list` (and folderless space lists) — and
fans the leaf reads (tasks, comments, custom fields) out over the discovered lists, stamping each
record with its parent-id context. `tasks` page by an integer `?page=N` and filter past the stored
watermark on `date_updated`; per-list child streams filter on their own `cursor_field`. `users` are
collapsed from the members embedded on each team. Auth is the OAuth bearer the resolved `Credential`
carries. A refusal (401/403) raises `StreamSkipped`. The write path is intentionally absent —
the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
    records_at,
    with_context,
)
from ufo_ext_sources.watermark import integer_checkpoint

_REFUSAL_STATUS = frozenset({401, 403})

CLICKUP_STREAMS: list[StreamSpec] = [
    StreamSpec(name="teams", source_object="team", primary_key="id"),
    StreamSpec(name="users", source_object="users", primary_key="id"),
    StreamSpec(name="spaces", source_object="space", primary_key="id"),
    StreamSpec(name="folders", source_object="folder", primary_key="id"),
    StreamSpec(name="lists", source_object="list", primary_key="id"),
    StreamSpec(
        name="tasks",
        source_object="task",
        primary_key="id",
        cursor_field="date_updated",
        updated_at_field="date_updated",
    ),
    StreamSpec(
        name="list_comments",
        source_object="comment",
        primary_key="id",
        cursor_field="date",
        created_at_field="date",
        updated_at_field=None,
        canonical=False,
    ),
    StreamSpec(name="list_custom_fields", source_object="field", primary_key="id", canonical=False),
    StreamSpec(name="goals", source_object="goal", primary_key="id", canonical=False),
]


class ClickUpConnector(RestConnector):
    name = "clickup"
    base_url = "https://api.clickup.com/api/v2"
    streams_list = CLICKUP_STREAMS
    checkpoint = staticmethod(integer_checkpoint)

    async def _teams(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        data = await self._get(client, "/team")
        return records_at(data, "teams")

    async def _spaces(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for team in await self._teams(client):
            team_id = team.get("id")
            if not isinstance(team_id, str) or not team_id:
                continue
            data = await self._get(client, f"/team/{team_id}/space", params={"archived": "false"})
            out.extend(with_context(records_at(data, "spaces"), team_id=team_id))
        return out

    async def _folders(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for space in await self._spaces(client):
            space_id = space.get("id")
            if not isinstance(space_id, str) or not space_id:
                continue
            data = await self._get(
                client, f"/space/{space_id}/folder", params={"archived": "false"}
            )
            out.extend(with_context(records_at(data, "folders"), space_id=space_id))
        return out

    async def _lists(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for folder in await self._folders(client):
            folder_id = folder.get("id")
            if not isinstance(folder_id, str) or not folder_id:
                continue
            data = await self._get(
                client, f"/folder/{folder_id}/list", params={"archived": "false"}
            )
            out.extend(with_context(records_at(data, "lists"), folder_id=folder_id))
        for space in await self._spaces(client):
            space_id = space.get("id")
            if not isinstance(space_id, str) or not space_id:
                continue
            data = await self._get(client, f"/space/{space_id}/list", params={"archived": "false"})
            out.extend(with_context(records_at(data, "lists"), space_id=space_id))
        return out

    async def _tasks(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for task_list in await self._lists(client):
            list_id = task_list.get("id")
            if not isinstance(list_id, str) or not list_id:
                continue
            page_num = 0
            while True:
                params: dict[str, Any] = {
                    "archived": "false",
                    "include_closed": "true",
                    "subtasks": "true",
                    "page": page_num,
                }
                data = await self._get(client, f"/list/{list_id}/task", params=params)
                tasks = with_context(
                    records_at(data, "tasks"), list_id=list_id, list_name=task_list.get("name")
                )
                if cursor:
                    tasks = [t for t in tasks if int(t.get("date_updated") or 0) > int(cursor)]
                if tasks:
                    yield tasks
                if not records_at(data, "tasks"):
                    break
                page_num += 1

    async def _list_child_stream(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for task_list in await self._lists(client):
            list_id = task_list.get("id")
            if not isinstance(list_id, str) or not list_id:
                continue
            path = {
                "list_comments": f"/list/{list_id}/comment",
                "list_custom_fields": f"/list/{list_id}/field",
            }[stream.name]
            data = await self._get(client, path)
            key = "comments" if stream.name == "list_comments" else "fields"
            records = with_context(
                records_at(data, key), list_id=list_id, list_name=task_list.get("name")
            )
            if cursor and stream.cursor_field:
                records = [r for r in records if int(r.get(stream.cursor_field) or 0) > int(cursor)]
            if records:
                yield records

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name in {"teams", "spaces", "folders", "lists"}:
                records = await self._root_records(client, stream.name)
                if records:
                    yield records
                return
            if stream.name == "users":
                users = await self._users(client)
                if users:
                    yield list(users.values())
                return
            if stream.name == "tasks":
                async for page in self._tasks(client, cursor=cursor):
                    yield page
                return
            if stream.name in {"list_comments", "list_custom_fields"}:
                async for page in self._list_child_stream(client, stream, cursor=cursor):
                    yield page
                return
            if stream.name == "goals":
                out = await self._goals(client)
                if out:
                    yield out
                return
            raise StreamSkipped(f"clickup stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"clickup: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

    async def _root_records(self, client: httpx.AsyncClient, name: str) -> list[dict[str, Any]]:
        match name:
            case "teams":
                return await self._teams(client)
            case "spaces":
                return await self._spaces(client)
            case "folders":
                return await self._folders(client)
            case "lists":
                return await self._lists(client)
            case _:
                raise ValueError(f"clickup has no root collection {name!r}")

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

    async def _goals(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        goals: list[dict[str, Any]] = []
        for team in await self._teams(client):
            team_id = team.get("id")
            if not isinstance(team_id, str) or not team_id:
                continue
            data = await self._get(client, f"/team/{team_id}/goal")
            goals.extend(with_context(records_at(data, "goals"), team_id=team_id))
        return goals

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
                "parent_external_id": record.get("list_id"),
            }
        return record
