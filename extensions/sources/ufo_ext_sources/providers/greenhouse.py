"""The Greenhouse connector — the Harvest v1 recruiting surface (candidates, jobs, applications,
interviews, offers, users, and their per-parent substreams) synced into recallable pages.

Harvest returns every collection as a flat top-level JSON array — no envelope to lift — so records
arrive flat and the default passthrough stands. Auth is HTTP Basic with the API key as the username
and an empty password (`base64("<api_key>:")`): when the resolved `Credential` carries the key
host-side (the direct/BYOK backend) the client sends it as Basic auth; under a broker the proxying
transport injects auth and the client is left as the base built it. Pagination is RFC 5988
`Link: rel=next` on every endpoint (`?per_page=500`). Most streams hit a top-level path; the nine
substreams Harvest publishes only under a parent — per-application, per-candidate, per-job,
per-user, per-question — name that parent instead. Incremental top-level
streams filter server-side by `?updated_after` (with `applications` on `created_after` and `eeoc` on
`submitted_after`); the provider computes a watermark over each stream's cursor field. A grant the
account can't read (`401`/`403`) raises `StreamSkipped` so the run records a skip, not a
failure. The credential is resolved through the auth proxy the runner threads — this connector holds
no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from functools import partial
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import (
    ParentEdge,
    Partition,
    PartitionBound,
    RestConnector,
    Run,
    StreamPage,
    StreamSkipped,
    StreamSpec,
    WalkPage,
    fanned_out,
)
from ufo_ext_sources.watermark import text_checkpoint

PAGE_SIZE = 500
_REFUSAL_STATUS = frozenset({401, 403})

_SIMPLE_PATHS: dict[str, str] = {
    "candidates": "/v1/candidates",
    "jobs": "/v1/jobs",
    "applications": "/v1/applications",
    "interviews": "/v1/scheduled_interviews",
    "offers": "/v1/offers",
    "users": "/v1/users",
    "close_reasons": "/v1/close_reasons",
    "custom_fields": "/v1/custom_fields",
    "degrees": "/v1/degrees",
    "demographics_answer_options": "/v1/demographics/answer_options",
    "demographics_answers": "/v1/demographics/answers",
    "demographics_question_sets": "/v1/demographics/question_sets",
    "demographics_questions": "/v1/demographics/questions",
    "departments": "/v1/departments",
    "disciplines": "/v1/disciplines",
    "eeoc": "/v1/eeoc",
    "email_templates": "/v1/email_templates",
    "job_posts": "/v1/job_posts",
    "job_stages": "/v1/job_stages",
    "offices": "/v1/offices",
    "prospect_pools": "/v1/prospect_pools",
    "rejection_reasons": "/v1/rejection_reasons",
    "schools": "/v1/schools",
    "scorecards": "/v1/scorecards",
    "sources": "/v1/sources",
    "tags": "/v1/tags/candidate",
    "user_roles": "/v1/user_roles",
}

_CURSOR_PARAM: dict[str, str] = {
    "applications": "created_after",
    "eeoc": "submitted_after",
}


def _stream(
    name: str,
    *,
    source_object: str | None = None,
    primary_key: str = "id",
    cursor_field: str | None = None,
    created_at_field: str | None = "created_at",
    updated_at_field: str | None = "updated_at",
    canonical: bool = False,
    parent: str | None = None,
    path: str | None = None,
) -> StreamSpec:
    if (parent is None) != (path is None):
        raise ValueError(
            f"greenhouse: stream {name!r} names a parent without a path, or the reverse"
        )
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        created_at_field=created_at_field,
        updated_at_field=updated_at_field,
        canonical=canonical,
        parents=() if parent is None or path is None else (ParentEdge(stream=parent, path=path),),
    )


CANDIDATES = _stream("candidates", cursor_field="updated_at", canonical=True)
JOBS = _stream("jobs", cursor_field="updated_at", canonical=True)
APPLICATIONS = _stream(
    "applications", cursor_field="applied_at", created_at_field="applied_at", canonical=True
)
INTERVIEWS = _stream(
    "interviews", source_object="scheduled_interviews", cursor_field="updated_at", canonical=True
)
OFFERS = _stream("offers", cursor_field="updated_at", canonical=True)
USERS = _stream("users", cursor_field="updated_at")

APPLICATIONS_DEMOGRAPHICS_ANSWERS = _stream(
    "applications_demographics_answers",
    source_object="demographics/answers",
    parent="applications",
    path="/v1/applications/{id}/demographics/answers",
)
APPLICATIONS_INTERVIEWS = _stream(
    "applications_interviews",
    source_object="scheduled_interviews",
    parent="applications",
    path="/v1/applications/{id}/scheduled_interviews",
)
ACTIVITY_FEED = _stream(
    "activity_feed",
    source_object="activity_feed",
    parent="candidates",
    path="/v1/candidates/{id}/activity_feed",
)
APPROVALS = _stream(
    "approvals",
    source_object="approval_flows",
    parent="jobs",
    path="/v1/jobs/{id}/approval_flows",
)
JOBS_OPENINGS = _stream(
    "jobs_openings", source_object="openings", parent="jobs", path="/v1/jobs/{id}/openings"
)
JOBS_STAGES = _stream(
    "jobs_stages", source_object="stages", parent="jobs", path="/v1/jobs/{id}/stages"
)
USER_PERMISSIONS = _stream(
    "user_permissions",
    source_object="permissions/jobs",
    parent="users",
    path="/v1/users/{id}/permissions/jobs",
)
DEMOGRAPHICS_ANSWERS_ANSWER_OPTIONS = _stream(
    "demographics_answers_answer_options",
    source_object="answer_options",
    parent="demographics_questions",
    path="/v1/demographics/questions/{id}/answer_options",
)
DEMOGRAPHICS_QUESTION_SETS_QUESTIONS = _stream(
    "demographics_question_sets_questions",
    source_object="questions",
    parent="demographics_question_sets",
    path="/v1/demographics/question_sets/{id}/questions",
)

CLOSE_REASONS = _stream("close_reasons")
CUSTOM_FIELDS = _stream("custom_fields")
DEGREES = _stream("degrees")
DEMOGRAPHICS_ANSWER_OPTIONS = _stream(
    "demographics_answer_options", source_object="demographics/answer_options"
)
DEMOGRAPHICS_ANSWERS = _stream(
    "demographics_answers",
    source_object="demographics/answers",
    cursor_field="updated_at",
)
DEMOGRAPHICS_QUESTION_SETS = _stream(
    "demographics_question_sets", source_object="demographics/question_sets"
)
DEMOGRAPHICS_QUESTIONS = _stream("demographics_questions", source_object="demographics/questions")
DEPARTMENTS = _stream("departments")
DISCIPLINES = _stream("disciplines")
EEOC = _stream("eeoc", cursor_field="submitted_at", created_at_field="submitted_at")
EMAIL_TEMPLATES = _stream("email_templates", cursor_field="updated_at")
JOB_POSTS = _stream("job_posts", cursor_field="updated_at", canonical=True)
JOB_STAGES = _stream("job_stages", cursor_field="updated_at")
OFFICES = _stream("offices")
PROSPECT_POOLS = _stream("prospect_pools")
REJECTION_REASONS = _stream("rejection_reasons")
SCHOOLS = _stream("schools")
SCORECARDS = _stream("scorecards", cursor_field="updated_at", canonical=True)
SOURCES = _stream("sources")
TAGS = _stream("tags", source_object="tags/candidate")
USER_ROLES = _stream("user_roles")


ALL_STREAMS = [
    CANDIDATES,
    JOBS,
    APPLICATIONS,
    INTERVIEWS,
    OFFERS,
    USERS,
    ACTIVITY_FEED,
    APPLICATIONS_DEMOGRAPHICS_ANSWERS,
    APPLICATIONS_INTERVIEWS,
    APPROVALS,
    JOBS_OPENINGS,
    JOBS_STAGES,
    USER_PERMISSIONS,
    DEMOGRAPHICS_ANSWERS_ANSWER_OPTIONS,
    DEMOGRAPHICS_QUESTION_SETS_QUESTIONS,
    CLOSE_REASONS,
    CUSTOM_FIELDS,
    DEGREES,
    DEMOGRAPHICS_ANSWER_OPTIONS,
    DEMOGRAPHICS_ANSWERS,
    DEMOGRAPHICS_QUESTION_SETS,
    DEMOGRAPHICS_QUESTIONS,
    DEPARTMENTS,
    DISCIPLINES,
    EEOC,
    EMAIL_TEMPLATES,
    JOB_POSTS,
    JOB_STAGES,
    OFFICES,
    PROSPECT_POOLS,
    REJECTION_REASONS,
    SCHOOLS,
    SCORECARDS,
    SOURCES,
    TAGS,
    USER_ROLES,
]


class GreenhouseConnector(RestConnector):
    name = "greenhouse"
    base_url = "https://harvest.greenhouse.io"
    streams_list = ALL_STREAMS
    checkpoint = staticmethod(text_checkpoint)

    def _make_client(self, base_url: str, credential: Credential) -> httpx.AsyncClient:
        """Greenhouse Harvest uses HTTP Basic auth — the API key is the username, the password
        empty. When the resolved `Credential` carries the key host-side, send Basic auth (replacing
        the base client's bearer header); under a broker the proxying transport injects auth and the
        base client stands unchanged."""
        client = super()._make_client(base_url, credential)
        if credential.bearer is not None:
            client.auth = httpx.BasicAuth(username=credential.bearer, password="")
            client.headers.pop("Authorization", None)
        return client

    @staticmethod
    def _cursor_param(stream_name: str) -> str:
        """The source-side cursor query-param for a stream: `updated_after` by default, with the few
        Harvest endpoints that name it differently listed in `_CURSOR_PARAM`."""
        return _CURSOR_PARAM.get(stream_name, "updated_after")

    async def paginate(
        self, client: httpx.AsyncClient, stream: StreamSpec, run: Run
    ) -> AsyncIterator[list[dict[str, Any]] | StreamPage]:
        try:
            if stream.parents:
                pages = partial(self._partition_pages, client)
                async for stream_page in fanned_out(stream, run, pages):
                    yield stream_page
                return
            path = _SIMPLE_PATHS.get(stream.name)
            if not path:
                raise NotImplementedError(
                    f"greenhouse: stream {stream.name!r} has no paginate dispatch"
                )
            params: dict[str, Any] = {"per_page": PAGE_SIZE}
            if stream.cursor_field and run.cursor:
                params[self._cursor_param(stream.name)] = run.cursor
            async for page in self._paginate_link_header(client, path, params=params):
                yield page
        except httpx.HTTPStatusError as error:
            if error.response.status_code in _REFUSAL_STATUS:
                raise StreamSkipped(
                    f"greenhouse: {stream.name!r} refused ({error.response.status_code}); "
                    "the grant lacks access or the key is invalid"
                ) from error
            raise

    async def _paginate_link_header(
        self, client: httpx.AsyncClient, path: str, *, params: dict[str, Any] | None = None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """RFC 5988 link-header walk from `?per_page=500`, following `Link: rel=next` while set."""
        async for page in self._get_link_header_pages(
            client, path, params=params, page_size=PAGE_SIZE
        ):
            yield page

    async def _partition_pages(
        self, client: httpx.AsyncClient, partition: Partition, bound: PartitionBound
    ) -> AsyncIterator[WalkPage]:
        """One parent's child collection, walked whole. Harvest publishes no cursor filter on a
        per-parent endpoint, so these streams are ordered `none` and the walk hands down no bound —
        the partition boundary is all that is checkpointed, and a completed pass re-walks."""
        async for page in self._paginate_link_header(client, partition.path):
            if page:
                yield WalkPage(records=page)
