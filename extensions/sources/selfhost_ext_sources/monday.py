"""The monday.com connector — users, teams, workspaces, boards, items, updates, activity logs, and
tags synced into recallable pages. The GraphQL provider on the connector framework.

monday speaks only GraphQL: every stream is a query posted to `/`. Top-level collections page by
`(limit, page)` (`_paged_root`); board items page by an opaque `next_items_page` cursor threaded
across `items_page`/`next_items_page` calls; activity logs and item updates fan out per board or
per item. `_graphql` unwraps `data`, and a GraphQL `errors` array — monday's channel for a refused
or unavailable query — raises `StreamSkipped` so the run records a skip rather than committing a
partial page. An incremental stream filters each page past the stored watermark on its
`cursor_field` (`updated_at`/`created_at`); monday has no server-side `since`. A transport refusal
(HTTP 401/403)
also raises `StreamSkipped`. The credential is resolved through the auth proxy the runner threads —
this connector holds no token. The write path (mutations) is intentionally absent — the source seam
only reads."""

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
    list_or_empty,
)

_REFUSAL_STATUS = frozenset({401, 403})

USERS = StreamSpec(name="users", source_object="users", primary_key="id")
TEAMS = StreamSpec(name="teams", source_object="teams", primary_key="id", canonical=False)
WORKSPACES = StreamSpec(name="workspaces", source_object="workspaces", primary_key="id")
BOARDS = StreamSpec(
    name="boards",
    source_object="boards",
    primary_key="id",
    cursor_field="updated_at",
)
ITEMS = StreamSpec(
    name="items",
    source_object="items",
    primary_key="id",
    cursor_field="updated_at",
)
UPDATES = StreamSpec(
    name="updates",
    source_object="updates",
    primary_key="id",
    cursor_field="created_at",
    canonical=False,
)
ACTIVITY_LOGS = StreamSpec(
    name="activity_logs",
    source_object="activity_logs",
    primary_key="id",
    cursor_field="created_at",
    canonical=False,
)
TAGS = StreamSpec(name="tags", source_object="tags", primary_key="id", canonical=False)

ALL_STREAMS = [USERS, TEAMS, WORKSPACES, BOARDS, ITEMS, UPDATES, ACTIVITY_LOGS, TAGS]


def _extract_person_ids(column_values: Any) -> list[str]:
    """Hoist person-column ids out of monday's per-board `column_values`. monday stores assignments
    inside a board-specific column of `type="people"` whose `value` is a JSON blob shaped like
    `{"personsAndTeams":[{"id":12345,"kind":"person"},...]}`. The column id is per-board, so the
    only stable selector is the type discriminator; this returns the flat list of person ids across
    every people column on the row."""
    if not isinstance(column_values, list):
        return []
    ids: list[str] = []
    for column in column_values:
        if not isinstance(column, dict) or column.get("type") != "people":
            continue
        raw_value = column.get("value")
        if raw_value in (None, ""):
            continue
        try:
            parsed = json.loads(raw_value) if isinstance(raw_value, str) else raw_value
        except (TypeError, ValueError):
            continue
        persons_and_teams = parsed.get("personsAndTeams") if isinstance(parsed, dict) else None
        if not isinstance(persons_and_teams, list):
            continue
        for entry in persons_and_teams:
            if not isinstance(entry, dict) or entry.get("kind") != "person":
                continue
            entry_id = entry.get("id")
            if entry_id is not None:
                ids.append(str(entry_id))
    return ids


class MondayConnector(RestConnector):
    name = "monday"
    base_url = "https://api.monday.com/v2"
    streams_list = ALL_STREAMS

    async def _graphql(
        self,
        client: httpx.AsyncClient,
        query: str,
        *,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        body = await self._post(client, "/", json={"query": query, "variables": variables or {}})
        errors = body.get("errors")
        if errors:
            raise StreamSkipped(f"monday GraphQL error: {errors}")
        data = body.get("data")
        return data if isinstance(data, dict) else {}

    async def _paged_root(
        self,
        client: httpx.AsyncClient,
        *,
        field: str,
        selection: str,
        cursor: str | None = None,
        cursor_field: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        page = 1
        while True:
            data = await self._graphql(
                client,
                f"query($page: Int!) {{{field}(limit: 100, page: $page) {{{selection}}}}}",
                variables={"page": page},
            )
            records = list_or_empty(data.get(field))
            if cursor and cursor_field:
                records = [r for r in records if str(r.get(cursor_field) or "") > cursor]
            if records:
                yield records
            if not records:
                return
            page += 1

    async def _boards(self, client: httpx.AsyncClient) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        async for page in self._paged_root(
            client,
            field="boards",
            selection=(
                "id name description state board_kind type updated_at url "
                "workspace{id name kind description}"
            ),
        ):
            out.extend(page)
        return out

    async def _items(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for board in await self._boards(client):
            board_id = board.get("id")
            if board_id is None:
                continue
            item_cursor: str | None = None
            while True:
                if item_cursor:
                    data = await self._graphql(
                        client,
                        """
                        query($cursor: String!) {
                          next_items_page(limit: 100, cursor: $cursor) {
                            cursor
                            items {
                                id name state created_at updated_at url
                                board { id name }
                                group { id title }
                                column_values { id type value }
                              }
                          }
                        }
                        """,
                        variables={"cursor": item_cursor},
                    )
                    page_obj = dict_or_empty(data.get("next_items_page"))
                else:
                    data = await self._graphql(
                        client,
                        """
                        query($board_ids: [ID!]!) {
                          boards(ids: $board_ids) {
                            items_page(limit: 100) {
                              cursor
                              items {
                                id name state created_at updated_at url
                                board { id name }
                                group { id title }
                                column_values { id type value }
                              }
                            }
                          }
                        }
                        """,
                        variables={"board_ids": [str(board_id)]},
                    )
                    boards = list_or_empty(data.get("boards"))
                    page_obj = dict_or_empty(boards[0].get("items_page") if boards else None)
                records = list_or_empty(page_obj.get("items"))
                for record in records:
                    record["assignee_ids"] = _extract_person_ids(record.get("column_values"))
                if cursor:
                    records = [r for r in records if str(r.get("updated_at") or "") > cursor]
                if records:
                    yield records
                item_cursor = page_obj.get("cursor")
                if not isinstance(item_cursor, str) or not item_cursor:
                    break

    async def _activity_logs(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        for board in await self._boards(client):
            board_id = board.get("id")
            if board_id is None:
                continue
            data = await self._graphql(
                client,
                """
                query($board_ids: [ID!]!) {
                  boards(ids: $board_ids) {
                    id
                    activity_logs(limit: 100) { id event data entity created_at user_id }
                  }
                }
                """,
                variables={"board_ids": [str(board_id)]},
            )
            boards = list_or_empty(data.get("boards"))
            records: list[dict[str, Any]] = []
            for found in boards:
                logs = found.get("activity_logs")
                if isinstance(logs, list):
                    records.extend({**log, "board_id": found.get("id")} for log in logs)
            if cursor:
                records = [r for r in records if str(r.get("created_at") or "") > cursor]
            if records:
                yield records

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "users":
                async for page in self._paged_root(
                    client,
                    field="users",
                    selection="id name email created_at enabled is_guest",
                ):
                    yield page
                return
            if stream.name == "teams":
                data = await self._graphql(client, "{ teams { id name users { id name email } } }")
                teams = data.get("teams") if isinstance(data.get("teams"), list) else []
                if teams:
                    yield teams
                return
            if stream.name == "workspaces":
                async for page in self._paged_root(
                    client,
                    field="workspaces",
                    selection="id name kind description",
                ):
                    yield page
                return
            if stream.name == "boards":
                async for page in self._paged_root(
                    client,
                    field="boards",
                    selection=(
                        "id name description state board_kind type updated_at url "
                        "workspace{id name kind description}"
                    ),
                    cursor=cursor,
                    cursor_field="updated_at",
                ):
                    yield page
                return
            if stream.name == "items":
                async for page in self._items(client, cursor=cursor):
                    yield page
                return
            if stream.name == "updates":
                async for page in self._paged_root(
                    client,
                    field="updates",
                    selection="id body created_at text_body creator { id name email } item_id",
                    cursor=cursor,
                    cursor_field="created_at",
                ):
                    yield page
                return
            if stream.name == "activity_logs":
                async for page in self._activity_logs(client, cursor=cursor):
                    yield page
                return
            if stream.name == "tags":
                data = await self._graphql(client, "{ tags { id name color } }")
                tags = data.get("tags") if isinstance(data.get("tags"), list) else []
                if tags:
                    yield tags
                return
            raise StreamSkipped(f"monday stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"monday: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks scope or the token is invalid"
                ) from error
            raise

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "users":
            return {
                **record,
                "name": record.get("name"),
                "email": record.get("email"),
                "created_at": record.get("created_at"),
            }
        if stream.name in {"workspaces", "boards"}:
            return {
                **record,
                "name": record.get("name"),
                "api_url": record.get("url")
                or f"https://api.monday.com/v2/{stream.source_object}/{record.get('id')}",
                "created_at": record.get("created_at"),
            }
        if stream.name == "items":
            return {
                **record,
                "name": record.get("name"),
                "status": record.get("state"),
                "created_at": record.get("created_at"),
            }
        if stream.name == "updates":
            creator = dict_or_empty(record.get("creator"))
            return {
                **record,
                "body": record.get("text_body") or record.get("body"),
                "author": creator.get("email") or creator.get("name") or creator.get("id"),
                "created_at": record.get("created_at"),
                "parent_external_id": record.get("item_id"),
            }
        if stream.name == "activity_logs":
            return {
                **record,
                "subject": record.get("event"),
                "body": record.get("data"),
                "author": record.get("user_id"),
                "created_at": record.get("created_at"),
                "parent_external_id": record.get("board_id"),
            }
        return record
