"""The PagerDuty connector — users, teams, services, incidents, incident notes, escalation policies,
schedules, and on-calls synced into recallable pages.

PagerDuty paginates by `offset`+`limit` with the response reporting its own continuation: a `more`
boolean says whether another page exists and a `limit` echo gives the applied page size the next
offset advances by (`_get_offset_pages(more_path=..., response_limit_path=...)`). Incidents read
incrementally with `?since=<cursor>` sorted by `updated_at`; incident notes fan out per incident.
Records arrive flat under a stream-named envelope key, so keying and the watermark read the raw
fields directly. A refusal (HTTP 401/403) raises `StreamSkipped` so the run records a skip, not a
failure. Auth pins PagerDuty's versioned media type. The credential is resolved through the auth
proxy the runner threads — this connector holds no token. The write path is intentionally absent —
the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec, records_at, with_context
from ufo_ext_sources.watermark import text_checkpoint

_REFUSAL_STATUS = frozenset({401, 403})
_PAGERDUTY_ACCEPT = "application/vnd.pagerduty+json;version=2"

USERS = StreamSpec(name="users", source_object="users", primary_key="id")
TEAMS = StreamSpec(name="teams", source_object="teams", primary_key="id", canonical=False)
SERVICES = StreamSpec(name="services", source_object="services", primary_key="id")
INCIDENTS = StreamSpec(
    name="incidents",
    source_object="incidents",
    primary_key="id",
    cursor_field="updated_at",
    updated_at_field="updated_at",
)
INCIDENT_NOTES = StreamSpec(
    name="incident_notes",
    source_object="notes",
    primary_key="id",
    cursor_field="created_at",
    updated_at_field=None,
    canonical=False,
)
ESCALATION_POLICIES = StreamSpec(
    name="escalation_policies",
    source_object="escalation_policies",
    primary_key="id",
    canonical=False,
)
SCHEDULES = StreamSpec(
    name="schedules",
    source_object="schedules",
    primary_key="id",
    canonical=False,
)
ONCALLS = StreamSpec(name="oncalls", source_object="oncalls", primary_key="id", canonical=False)

ALL_STREAMS = [
    USERS,
    TEAMS,
    SERVICES,
    INCIDENTS,
    INCIDENT_NOTES,
    ESCALATION_POLICIES,
    SCHEDULES,
    ONCALLS,
]


class PagerDutyConnector(RestConnector):
    name = "pagerduty"
    base_url = "https://api.pagerduty.com"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        client = super()._make_client(base_url, credential)
        client.headers["Accept"] = _PAGERDUTY_ACCEPT
        return client

    async def _offset_pages(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        params: dict[str, Any] | None = None,
        cursor: str | None = None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for records in self._get_offset_pages(
            client,
            f"/{stream.source_object}",
            records_path=stream.source_object,
            limit=100,
            params=params,
            more_path="more",
            response_limit_path="limit",
        ):
            if cursor and stream.cursor_field:
                records = [r for r in records if str(r.get(stream.cursor_field) or "") > cursor]
            if records:
                yield records

    async def _incidents(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        params = {"sort_by": "updated_at:asc"}
        if cursor:
            params["since"] = cursor
        async for page in self._offset_pages(
            client,
            next(s for s in self.streams_list if s.name == "incidents"),
            params=params,
            cursor=cursor,
        ):
            yield page

    async def _incident_notes(
        self, client: httpx.AsyncClient, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        async for incidents in self._incidents(client, cursor=None):
            for incident in incidents:
                incident_id = incident.get("id")
                if not isinstance(incident_id, str) or not incident_id:
                    continue
                data = await self._get(client, f"/incidents/{incident_id}/notes")
                notes = records_at(data, "notes")
                if cursor:
                    notes = [n for n in notes if str(n.get("created_at") or "") > cursor]
                if notes:
                    yield with_context(notes, incident_id=incident_id)

    async def paginate(
        self,
        client: httpx.AsyncClient,
        stream: StreamSpec,
        *,
        cursor: str | None,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            if stream.name == "incidents":
                async for page in self._incidents(client, cursor=cursor):
                    yield page
                return
            if stream.name == "incident_notes":
                async for page in self._incident_notes(client, cursor=cursor):
                    yield page
                return
            if stream.name in {
                "users",
                "teams",
                "services",
                "escalation_policies",
                "schedules",
                "oncalls",
            }:
                async for page in self._offset_pages(client, stream, cursor=cursor):
                    yield page
                return
            raise StreamSkipped(f"pagerduty stream {stream.name!r} is not implemented")
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"pagerduty: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks scope or the token is invalid"
                ) from error
            raise
