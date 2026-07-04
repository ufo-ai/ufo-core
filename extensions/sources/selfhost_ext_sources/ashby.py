"""The Ashby connector — recruiting objects (candidates, applications, jobs, interviews, offers,
users, and their metadata collections) synced into recallable pages.

Ashby's REST surface is uniformly POST-based: every list endpoint sits at `/<resource>.list` and
accepts a JSON body `{cursor, limit, syncToken?}`, returning `{results, moreDataAvailable,
nextCursor}`. The connector pages until `moreDataAvailable` is false, threading `nextCursor` into
the next request. The run cursor doubles as Ashby's `syncToken` on the first request — a known token
scopes the read to "changed since", an unknown one backfills — so an incremental stream advances a
watermark over `updatedAt`. `application_criteria_evaluations` fans out per application id.

Auth is HTTP Basic with the API key as the username and an empty password: when the resolved
`Credential` carries a direct key (`bearer`), `_make_client` encodes it as a Basic header; a
broker's proxying `transport` is honored unchanged. The write path is intentionally absent — the
source seam only reads."""

import base64
from collections.abc import AsyncIterator
from typing import Any

import httpx

from selfhost.sdk.authproxy import Credential
from selfhost.sdk.sources import RestConnector, StreamSpec

PAGE_SIZE = 100


def _stream(
    name: str,
    *,
    path: str,
    primary_key: str = "id",
    cursor_field: str | None = None,
    canonical: bool = False,
) -> StreamSpec:
    """`source_object` carries the Ashby endpoint path — the connector reads it verbatim when
    issuing the POST."""
    return StreamSpec(
        name=name,
        source_object=path,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


# Stream set mirrors Airbyte's source-ashby catalog (18 streams). `application_criteria_evaluations`
# is a per-application substream; `applications_for_criteria` is its parent enumeration.
ASHBY_STREAMS: list[StreamSpec] = [
    _stream("candidates", path="/candidate.list", cursor_field="updatedAt", canonical=True),
    _stream("job_postings", path="/jobPosting.list", cursor_field="updatedAt", canonical=True),
    _stream("applications", path="/application.list", cursor_field="updatedAt", canonical=True),
    _stream("interviews", path="/interview.list", cursor_field="updatedAt", canonical=True),
    _stream("offers", path="/offer.list", cursor_field="updatedAt", canonical=True),
    _stream("users", path="/user.list", cursor_field="updatedAt", canonical=True),
    _stream(
        "application_criteria_evaluations",
        path="/application.listCriteriaEvaluations",
        primary_key="applicationId",
    ),
    _stream("applications_for_criteria", path="/application.list", cursor_field="updatedAt"),
    _stream("archive_reasons", path="/archiveReason.list"),
    _stream("candidate_tags", path="/candidateTag.list"),
    _stream("custom_fields", path="/customField.list"),
    _stream("departments", path="/department.list"),
    _stream("feedback_form_definitions", path="/feedbackFormDefinition.list"),
    _stream("interview_schedules", path="/interviewSchedule.list", cursor_field="updatedAt"),
    _stream("interview_stages", path="/interviewStage.list"),
    _stream("jobs", path="/job.list", cursor_field="updatedAt"),
    _stream("locations", path="/location.list"),
    _stream("sources", path="/source.list"),
]


class AshbyConnector(RestConnector):
    name = "ashby"
    base_url = "https://api.ashbyhq.com"
    streams_list = ASHBY_STREAMS

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        if credential.transport is not None:
            return super()._make_client(base_url, credential)
        if credential.bearer is None:
            raise RuntimeError("ashby: credential carries no api key")
        token = base64.b64encode(f"{credential.bearer}:".encode()).decode()
        return super()._make_client(
            base_url, Credential(headers={"Authorization": f"Basic {token}"})
        )

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        if stream.name == "application_criteria_evaluations":
            async for page in self._paginate_application_criteria(client):
                yield page
            return
        async for page in self._paginate_default(client, stream, cursor=cursor):
            yield page

    async def _paginate_default(
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        body: dict[str, Any] = {"limit": PAGE_SIZE}
        if cursor:
            body["syncToken"] = cursor
        next_cursor: str | None = None
        first_page = True
        while True:
            page_body = dict(body)
            if not first_page and next_cursor is not None:
                page_body["cursor"] = next_cursor
            data = await self._post(client, stream.source_object, json=page_body)
            records = data.get("results") or []
            if records:
                yield records
            if not data.get("moreDataAvailable"):
                return
            next_cursor = data.get("nextCursor")
            if not next_cursor:
                return
            first_page = False

    async def _paginate_application_criteria(
        self, client: httpx.AsyncClient
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Per-application fanout: criteria evaluations live under each application id. Walk
        `/application.list`, then POST `/application.listCriteriaEvaluations` per id."""
        list_body: dict[str, Any] = {"limit": PAGE_SIZE}
        next_cursor: str | None = None
        first_page = True
        while True:
            page_body = dict(list_body)
            if not first_page and next_cursor is not None:
                page_body["cursor"] = next_cursor
            apps = await self._post(client, "/application.list", json=page_body)
            for app in apps.get("results") or []:
                if not isinstance(app, dict):
                    continue
                app_id = app.get("id")
                if not app_id:
                    continue
                detail = await self._post(
                    client, "/application.listCriteriaEvaluations", json={"applicationId": app_id}
                )
                evals = detail.get("results") or []
                stamped = []
                for ev in evals:
                    if isinstance(ev, dict):
                        row = dict(ev)
                        row.setdefault("applicationId", app_id)
                        stamped.append(row)
                if stamped:
                    yield stamped
            if not apps.get("moreDataAvailable"):
                return
            next_cursor = apps.get("nextCursor")
            if not next_cursor:
                return
            first_page = False
