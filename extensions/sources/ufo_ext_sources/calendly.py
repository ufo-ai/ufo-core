"""The Calendly connector — the API user, event types, groups, memberships, scheduled events, and
their invitees synced into recallable pages.

Every collection is organization-scoped: the connector first reads `/users/me` for the account's
`current_organization`, then fans each stream out under `?organization=<uri>`. Collections page by
the `pagination.next_page_token` the response body carries (`?page_token=<token>&count=100`).
`event_types` and `scheduled_events` filter incrementally through Calendly's own `updated_since` /
`min_start_time` params; `event_invitees` fans out per scheduled event and filters past the stored
watermark on `created_at`. A grant whose account exposes no `current_organization` yields
`StreamSkipped`. Organization memberships lift the member's name and email, then drop the foreign
user object so profile changes do not change the membership. Auth is the OAuth bearer the resolved
`Credential` carries. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.sources import (
    RestConnector,
    StreamSkipped,
    StreamSpec,
    dict_or_empty,
    with_context,
)

PAGE_SIZE = 100

CALENDLY_STREAMS: list[StreamSpec] = [
    StreamSpec(name="api_user", source_object="users/me", primary_key="uri"),
    StreamSpec(
        name="event_types",
        source_object="event_types",
        primary_key="uri",
        cursor_field="updated_at",
        updated_at_field="updated_at",
    ),
    StreamSpec(name="groups", source_object="groups", primary_key="uri", canonical=False),
    StreamSpec(
        name="organization_memberships",
        source_object="organization_memberships",
        primary_key="uri",
        canonical=False,
    ),
    StreamSpec(
        name="scheduled_events",
        source_object="scheduled_events",
        primary_key="uri",
        cursor_field="start_time",
    ),
    StreamSpec(
        name="event_invitees",
        source_object="event_invitees",
        primary_key="uri",
        cursor_field="created_at",
        canonical=False,
    ),
]


def _uuid_from_uri(uri: Any) -> str | None:
    if not isinstance(uri, str) or not uri:
        return None
    return uri.rstrip("/").rsplit("/", 1)[-1]


class CalendlyConnector(RestConnector):
    name = "calendly"
    base_url = "https://api.calendly.com"
    streams_list = CALENDLY_STREAMS

    async def _current_user(self, client: httpx.AsyncClient) -> dict[str, Any]:
        data = await self._get(client, "/users/me")
        user = data.get("resource")
        return user if isinstance(user, dict) else {}

    async def _paginate_collection(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for page in self._get_cursor_pages(
            client,
            path,
            records_path="collection",
            next_cursor_path="pagination.next_page_token",
            params=params,
            cursor_param="page_token",
            page_size_param="count",
            page_size=PAGE_SIZE,
        ):
            yield page

    async def _org_stream(
        self,
        client: httpx.AsyncClient,
        path: str,
        *,
        cursor: str | None = None,
        cursor_param: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        user = await self._current_user(client)
        org = user.get("current_organization")
        if not isinstance(org, str) or not org:
            raise StreamSkipped("calendly account did not expose current_organization")
        params = {"organization": org}
        if cursor and cursor_param:
            params[cursor_param] = cursor
        async for page in self._paginate_collection(client, path, params=params):
            yield with_context(page, organization=org)

    async def _invitees(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for events in self._org_stream(client, "/scheduled_events"):
            for event in events:
                event_uuid = _uuid_from_uri(event.get("uri"))
                if not event_uuid:
                    continue
                async for invitees in self._paginate_collection(
                    client, f"/scheduled_events/{event_uuid}/invitees"
                ):
                    if cursor:
                        invitees = [i for i in invitees if str(i.get("created_at") or "") > cursor]
                    if invitees:
                        yield with_context(
                            invitees,
                            scheduled_event_uri=event.get("uri"),
                            scheduled_event_uuid=event_uuid,
                        )

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if stream.name == "api_user":
            user = await self._current_user(client)
            if user:
                yield [user]
            return
        if stream.name == "event_types":
            async for page in self._org_stream(
                client, "/event_types", cursor=cursor, cursor_param="updated_since"
            ):
                yield page
            return
        if stream.name == "groups":
            async for page in self._org_stream(client, "/groups"):
                yield page
            return
        if stream.name == "organization_memberships":
            async for page in self._org_stream(client, "/organization_memberships"):
                yield page
            return
        if stream.name == "scheduled_events":
            async for page in self._org_stream(
                client, "/scheduled_events", cursor=cursor, cursor_param="min_start_time"
            ):
                yield page
            return
        if stream.name == "event_invitees":
            async for page in self._invitees(client, cursor=cursor):
                yield page
            return
        raise StreamSkipped(f"calendly stream {stream.name!r} is not implemented")

    def flatten(self, record: dict[str, Any], stream: StreamSpec) -> dict[str, Any]:
        if stream.name == "api_user":
            return {
                **record,
                "name": record.get("name"),
                "email": record.get("email"),
                "created_at": record.get("created_at"),
            }
        if stream.name == "organization_memberships":
            user = dict_or_empty(record.get("user"))
            return {
                **{key: value for key, value in record.items() if key != "user"},
                "name": user.get("name"),
                "email": user.get("email"),
                "created_at": record.get("created_at"),
            }
        if stream.name == "event_invitees":
            return {
                **record,
                "name": record.get("name"),
                "email": record.get("email"),
                "created_at": record.get("created_at"),
            }
        if stream.name == "scheduled_events":
            location = record.get("location")
            return {
                **record,
                "title": record.get("name"),
                "description": record.get("description"),
                "start_at": record.get("start_time"),
                "end_at": record.get("end_time"),
                "location": location.get("location") if isinstance(location, dict) else location,
            }
        if stream.name == "event_types":
            return {
                **record,
                "name": record.get("name"),
                "api_url": record.get("uri"),
                "created_at": record.get("created_at"),
            }
        return record
