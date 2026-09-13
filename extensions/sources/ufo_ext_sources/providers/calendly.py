"""The Calendly connector — the API user, event types, groups, memberships, scheduled events, and
their invitees synced into recallable pages.

Every collection is organization-scoped: the connector first reads `/users/me` for the account's
`current_organization`, then fans each stream out under `?organization=<uri>`. Collections page by
the `pagination.next_page_token` the response body carries (`?page_token=<token>&count=100`).
`event_types` and `scheduled_events` filter incrementally through Calendly's own `updated_since` /
`min_start_time` params. A grant whose account exposes no `current_organization` yields
`StreamSkipped`. Organization memberships lift the member's name and email, then drop the foreign
user object so profile changes do not change the membership. Auth is the OAuth bearer the resolved
`Credential` carries. A refusal (401/403) raises `StreamSkipped` too. The write path is
intentionally absent — the source seam only reads.

Calendly is HATEOAS: a scheduled event's `uri` is its own absolute address, so `event_invitees`
hangs under it at `{uri}/invitees` and the request needs no id lifted out of the URL. Invitees come
back `sort=created_at:desc`, which is the `cursor_field`'s own order, and the endpoint takes no time
filter, so the bound is applied to the records the page carries."""

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
    with_context,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 100
INVITEE_SORT = "created_at:desc"
_REFUSAL_STATUS = frozenset({401, 403})


def _cursor_bounds(records: list[dict[str, Any]], field: str) -> tuple[str | None, str | None]:
    values = sorted(str(record[field]) for record in records if isinstance(record.get(field), str))
    return (values[-1], values[0]) if values else (None, None)


CALENDLY_STREAMS: list[StreamSpec] = [
    StreamSpec(name="api_user", source_object="users/me", primary_key="uri"),
    StreamSpec(
        name="event_types",
        source_object="event_types",
        primary_key="uri",
        cursor_field="updated_at",
        updated_at_field="updated_at",
    ),
    StreamSpec(name="groups", source_object="groups", primary_key="uri"),
    StreamSpec(
        name="organization_memberships",
        source_object="organization_memberships",
        primary_key="uri",
    ),
    StreamSpec(
        name="scheduled_events",
        source_object="scheduled_events",
        primary_key="uri",
        cursor_field="start_time",
        canonical=True,
    ),
    StreamSpec(
        name="event_invitees",
        source_object="event_invitees",
        primary_key="uri",
        cursor_field="created_at",
        ordering=Ordering.newest_first,
        parents=(ParentEdge(stream="scheduled_events", path="{uri}/invitees"),),
    ),
]


class CalendlyConnector(RestConnector):
    name = "calendly"
    base_url = "https://api.calendly.com"
    streams_list = CALENDLY_STREAMS
    checkpoint = staticmethod(text_checkpoint)

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

    async def _invitee_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        async for page in self._paginate_collection(
            client, partition.path, params={"sort": INVITEE_SORT}
        ):
            landed = [
                record
                for record in page
                if isinstance(record.get("created_at"), str)
                and (bound.before is None or record["created_at"] <= bound.before)
                and (bound.since is None or record["created_at"] >= bound.since)
                and (bound.after is None or record["created_at"] > bound.after)
            ]
            high, _ = _cursor_bounds(landed, "created_at")
            _, low = _cursor_bounds(page, "created_at")
            yield WalkPage(records=landed, high=high, low=low)

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.name == "api_user":
                user = await self._current_user(client)
                if user:
                    yield [user]
                return
            if stream.name == "event_types":
                async for page in self._org_stream(
                    client, "/event_types", cursor=run.cursor, cursor_param="updated_since"
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
                    client, "/scheduled_events", cursor=run.cursor, cursor_param="min_start_time"
                ):
                    yield page
                return
            if stream.name == "event_invitees":
                pages = partial(self._invitee_pages, client)
                async for invitees in fanned_out(stream, run, pages):
                    yield invitees
                return
            raise StreamSkipped(f"calendly stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"calendly: {stream.name!r} refused ({error.response.status_code}); the grant "
                    "lacks the scope"
                ) from error
            raise

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
