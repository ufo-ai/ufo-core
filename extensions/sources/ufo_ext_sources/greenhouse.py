"""The Greenhouse connector — the Harvest v1 recruiting surface (candidates, jobs, applications,
interviews, offers, users, and their per-parent substreams) synced into recallable pages.

Harvest returns every collection as a flat top-level JSON array — no envelope to lift — so records
arrive flat and the default passthrough stands. Auth is HTTP Basic with the API key as the username
and an empty password (`base64("<api_key>:")`): when the resolved `Credential` carries the key
host-side (the direct/BYOK backend) the client sends it as Basic auth; under a broker the proxying
transport injects auth and the client is left as the base built it. Pagination is RFC 5988
`Link: rel=next` on every endpoint (`?per_page=500`). Most streams hit a top-level path; the
substreams (per-application, per-candidate, per-job, per-user, per-question) walk the parent
collection first and fetch the nested collection per parent, stamping each child with its parent id.
Incremental streams filter server-side by `?updated_after` (with `applications` on `created_after`
and `eeoc` on `submitted_after`); the adapter advances a watermark over each stream's cursor field.
A grant the account can't read (`401`/`403`) raises `StreamSkipped` so the run records a skip, not a
failure. The credential is resolved through the auth proxy the runner threads — this connector holds
no token. The write path is intentionally absent — the source seam only reads."""

from collections.abc import AsyncIterator
from typing import Any

import httpx

from ufo.sdk.authproxy import Credential
from ufo.sdk.sources import RestConnector, StreamSkipped, StreamSpec

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
    canonical: bool = False,
) -> StreamSpec:
    return StreamSpec(
        name=name,
        source_object=source_object or name,
        primary_key=primary_key,
        cursor_field=cursor_field,
        canonical=canonical,
    )


# Stream set mirrors Airbyte's source-greenhouse manifest: the six canonical content streams
# (candidates, jobs, applications, interviews, offers, users) plus the passthrough top-level
# endpoints and the per-parent substreams. Cursors track Airbyte's manifest exactly.
CANDIDATES = _stream("candidates", cursor_field="updated_at", canonical=True)
JOBS = _stream("jobs", cursor_field="updated_at", canonical=True)
APPLICATIONS = _stream("applications", cursor_field="applied_at", canonical=True)
INTERVIEWS = _stream(
    "interviews", source_object="scheduled_interviews", cursor_field="updated_at", canonical=True
)
OFFERS = _stream("offers", cursor_field="updated_at", canonical=True)
USERS = _stream("users", cursor_field="updated_at", canonical=True)

APPLICATIONS_DEMOGRAPHICS_ANSWERS = _stream(
    "applications_demographics_answers",
    source_object="applications/{application_id}/demographics/answers",
    cursor_field="updated_at",
)
APPLICATIONS_INTERVIEWS = _stream(
    "applications_interviews",
    source_object="applications/{application_id}/scheduled_interviews",
    cursor_field="updated_at",
)
ACTIVITY_FEED = _stream("activity_feed", source_object="candidates/{candidate_id}/activity_feed")
APPROVALS = _stream("approvals", source_object="jobs/{job_id}/approval_flows")
JOBS_OPENINGS = _stream("jobs_openings", source_object="jobs/{job_id}/openings")
JOBS_STAGES = _stream(
    "jobs_stages", source_object="jobs/{job_id}/stages", cursor_field="updated_at"
)
USER_PERMISSIONS = _stream("user_permissions", source_object="users/{user_id}/permissions/jobs")
DEMOGRAPHICS_ANSWERS_ANSWER_OPTIONS = _stream(
    "demographics_answers_answer_options",
    source_object="demographics/questions/{question_id}/answer_options",
)
DEMOGRAPHICS_QUESTION_SETS_QUESTIONS = _stream(
    "demographics_question_sets_questions",
    source_object="demographics/question_sets/{question_set_id}/questions",
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
EEOC = _stream("eeoc", cursor_field="submitted_at")
EMAIL_TEMPLATES = _stream("email_templates", cursor_field="updated_at")
JOB_POSTS = _stream("job_posts", cursor_field="updated_at")
JOB_STAGES = _stream("job_stages", cursor_field="updated_at")
OFFICES = _stream("offices")
PROSPECT_POOLS = _stream("prospect_pools")
REJECTION_REASONS = _stream("rejection_reasons")
SCHOOLS = _stream("schools")
SCORECARDS = _stream("scorecards", cursor_field="updated_at")
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

_PER_PARENT: dict[str, tuple[str, str, str]] = {
    "activity_feed": ("/v1/candidates", "/v1/candidates/{id}/activity_feed", "candidate_id"),
    "applications_demographics_answers": (
        "/v1/applications",
        "/v1/applications/{id}/demographics/answers",
        "application_id",
    ),
    "applications_interviews": (
        "/v1/applications",
        "/v1/applications/{id}/scheduled_interviews",
        "application_id",
    ),
    "approvals": ("/v1/jobs", "/v1/jobs/{id}/approval_flows", "job_id"),
    "jobs_openings": ("/v1/jobs", "/v1/jobs/{id}/openings", "job_id"),
    "jobs_stages": ("/v1/jobs", "/v1/jobs/{id}/stages", "job_id"),
    "user_permissions": ("/v1/users", "/v1/users/{id}/permissions/jobs", "user_id"),
    "demographics_answers_answer_options": (
        "/v1/demographics/questions",
        "/v1/demographics/questions/{id}/answer_options",
        "question_id",
    ),
    "demographics_question_sets_questions": (
        "/v1/demographics/question_sets",
        "/v1/demographics/question_sets/{id}/questions",
        "question_set_id",
    ),
}


class GreenhouseConnector(RestConnector):
    name = "greenhouse"
    base_url = "https://harvest.greenhouse.io"
    streams_list = ALL_STREAMS

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
        self, client: httpx.AsyncClient, stream: StreamSpec, *, cursor: str | None
    ) -> AsyncIterator[list[dict[str, Any]]]:
        try:
            parent = _PER_PARENT.get(stream.name)
            if parent is not None:
                parent_path, child_template, stamp_key = parent
                async for page in self._paginate_per_parent(
                    client,
                    parent_path=parent_path,
                    child_path_template=child_template,
                    stamp_key=stamp_key,
                ):
                    yield page
                return
            path = _SIMPLE_PATHS.get(stream.name)
            if not path:
                raise NotImplementedError(
                    f"greenhouse: stream {stream.name!r} has no paginate dispatch"
                )
            params: dict[str, Any] = {"per_page": PAGE_SIZE}
            if stream.cursor_field and cursor:
                params[self._cursor_param(stream.name)] = cursor
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

    async def _paginate_per_parent(
        self,
        client: httpx.AsyncClient,
        *,
        parent_path: str,
        child_path_template: str,
        stamp_key: str,
    ) -> AsyncIterator[list[dict[str, Any]]]:
        """Walk the parent collection, then fetch each parent's child collection, stamping every
        child row with the parent id under `stamp_key` so a downstream resolve keeps the origin."""
        async for parent_page in self._paginate_link_header(client, parent_path):
            for parent in parent_page:
                pid = parent.get("id") if isinstance(parent, dict) else None
                if pid is None:
                    continue
                async for child_page in self._paginate_link_header(
                    client, child_path_template.format(id=pid)
                ):
                    for child in child_page:
                        if isinstance(child, dict):
                            child.setdefault(stamp_key, pid)
                    yield child_page
