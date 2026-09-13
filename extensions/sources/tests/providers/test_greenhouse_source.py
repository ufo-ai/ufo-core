"""Greenhouse Harvest connector over a mock transport: the RFC 5988 Link-header cursor walk, the
nine substreams reached as their parents' landed records, the server-side `updated_after`
incremental filter, and the refusal → `StreamSkipped` boundary. Offline — a canned transport, no DB,
no token, no broker."""

from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.providers.greenhouse import GreenhouseConnector

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig, ParentPages, ParentRecord

NEXT_JOBS_PAGE = "https://harvest.greenhouse.io/v1/jobs?per_page=500&page=2"
Landed = Mapping[str, tuple[ParentRecord, ...]]
ParentsReader = Callable[[Landed], ParentPages]


async def _no_parents(stream: str) -> AsyncIterator[ParentRecord]:
    return
    yield


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    stream: str,
    handler: Callable[[httpx.Request], httpx.Response],
    cursor: str | None = None,
    parents: ParentPages = _no_parents,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler), parents=parents)
    return await ConnectorBackend(connector=GreenhouseConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


JOB_1 = {"id": 1, "name": "Staff Engineer", "updated_at": "2026-01-01T00:00:00Z"}
JOB_2 = {"id": 2, "name": "Product Manager", "updated_at": "2026-02-01T00:00:00Z"}
JOB_3 = {"id": 3, "name": "Designer", "updated_at": "2026-03-01T00:00:00Z"}


def _jobs_handler(seen_cursor: list[str | None]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "harvest.greenhouse.io"
        if request.url.path == "/v1/jobs":
            if request.url.params.get("page") == "2":
                return httpx.Response(200, json=[JOB_3])
            seen_cursor.append(request.url.params.get("updated_after"))
            return httpx.Response(
                200,
                json=[JOB_1, JOB_2],
                headers={"Link": f'<{NEXT_JOBS_PAGE}>; rel="next"'},
            )
        return httpx.Response(404, json={"path": request.url.path})

    return handle


async def test_jobs_link_header_walk_advances_the_watermark() -> None:
    result = await _fetch("jobs", _jobs_handler([]))

    assert {page.source_ref for page in result.pages} == {"jobs/1", "jobs/2", "jobs/3"}
    assert result.snapshot is False
    assert result.deletes == ()
    assert result.next_cursor == "2026-03-01T00:00:00Z"

    page = next(page for page in result.pages if page.source_ref == "jobs/1")
    assert page.updated_at == "2026-01-01T00:00:00.000000+00:00"
    assert "Staff Engineer" in page.body


async def test_incremental_sends_updated_after_from_the_cursor() -> None:
    seen_cursor: list[str | None] = []
    await _fetch("jobs", _jobs_handler(seen_cursor), cursor="2026-01-15T00:00:00Z")
    assert seen_cursor == ["2026-01-15T00:00:00Z"]


CHILD_EDGES = {
    "activity_feed": ("candidates", "/v1/candidates/11/activity_feed"),
    "applications_demographics_answers": (
        "applications",
        "/v1/applications/21/demographics/answers",
    ),
    "applications_interviews": ("applications", "/v1/applications/21/scheduled_interviews"),
    "approvals": ("jobs", "/v1/jobs/1/approval_flows"),
    "demographics_answers_answer_options": (
        "demographics_questions",
        "/v1/demographics/questions/51/answer_options",
    ),
    "demographics_question_sets_questions": (
        "demographics_question_sets",
        "/v1/demographics/question_sets/61/questions",
    ),
    "jobs_openings": ("jobs", "/v1/jobs/1/openings"),
    "jobs_stages": ("jobs", "/v1/jobs/1/stages"),
    "user_permissions": ("users", "/v1/users/41/permissions/jobs"),
}
PARENT_IDS = {
    "candidates": "11",
    "applications": "21",
    "demographics_questions": "51",
    "demographics_question_sets": "61",
    "jobs": "1",
    "users": "41",
}


def _child_handler(asked: list[str], body: object) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        return httpx.Response(200, json=body)

    return handle


@pytest.mark.parametrize(("stream", "edge"), sorted(CHILD_EDGES.items()))
async def test_each_child_asks_its_parents_path_and_nothing_above_it(
    stream: str, edge: tuple[str, str], parents_reader: ParentsReader
) -> None:
    parent, path = edge
    asked: list[str] = []
    landed = {parent: (ParentRecord(ref=f"{parent}/1", fields={"id": PARENT_IDS[parent]}),)}

    result = await _fetch(
        stream, _child_handler(asked, [{"id": 100, "name": "row"}]), None, parents_reader(landed)
    )

    assert asked == [path]
    assert {page.source_identity for page in result.pages} == {f"{stream}/{PARENT_IDS[parent]}/100"}


async def test_a_child_whose_parent_landed_nothing_spends_no_request() -> None:
    asked: list[str] = []
    result = await _fetch("jobs_openings", _child_handler(asked, []))
    assert asked == []
    assert result.pages == ()


async def test_two_parents_each_address_their_own_child_rows(
    parents_reader: ParentsReader,
) -> None:
    asked: list[str] = []
    landed = {
        "jobs": (
            ParentRecord(ref="jobs/1", fields={"id": "1"}),
            ParentRecord(ref="jobs/2", fields={"id": "2"}),
        )
    }
    result = await _fetch(
        "jobs_openings",
        _child_handler(asked, [{"id": 100, "name": "opening"}]),
        None,
        parents_reader(landed),
    )

    assert asked == ["/v1/jobs/1/openings", "/v1/jobs/2/openings"]
    assert {page.source_identity for page in result.pages} == {
        "jobs_openings/1/100",
        "jobs_openings/2/100",
    }


async def test_activity_feed_lands_nothing_because_its_body_is_three_sibling_arrays(
    parents_reader: ParentsReader,
) -> None:
    """Harvest answers `/v1/candidates/{id}/activity_feed` with `{notes, emails, activities}` —
    three sibling arrays, not the bare array every other Harvest endpoint returns, and no single
    record path can name three."""
    asked: list[str] = []
    landed = {"candidates": (ParentRecord(ref="candidates/11", fields={"id": "11"}),)}
    body = {"notes": [{"id": 101, "body": "note"}], "emails": [], "activities": []}

    result = await _fetch(
        "activity_feed", _child_handler(asked, body), None, parents_reader(landed)
    )

    assert asked == ["/v1/candidates/11/activity_feed"]
    assert result.pages == ()


async def test_the_scoped_child_identities_restamp_nothing() -> None:
    """`jobs_openings/100` is what main addresses a child row by. On main, `_create` registered
    canonical streams only, so none of these nine has ever landed a page and scoping them under
    their parent restamps nothing."""
    by_name = {stream.name: stream for stream in GreenhouseConnector().streams()}
    assert [name for name in CHILD_EDGES if by_name[name].canonical] == []
    assert by_name["jobs"].canonical is True


async def test_refusal_maps_to_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"message": "Invalid Basic Auth credentials"})

    with pytest.raises(StreamSkipped):
        await _fetch("jobs", handle)
