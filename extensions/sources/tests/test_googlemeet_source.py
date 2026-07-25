"""Google Meet connector over a mock transport: conference-record paging with a start-time
lookback filter, transcript and smart-note artifact fan-out, structured transcript entries rendered
as speaker-grouped dialogue, smart-note Google Docs text inlined when readable, empty artifact pages
still advancing the cursor, and `StreamSkipped` on Meet scope refusal. Offline - a canned
transport, no DB, no token, no broker."""

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID, uuid4

import httpx
import pytest
from ufo_ext_sources.googlemeet import CONFERENCE_PAGE_SIZE, GoogleMeetConnector

from ufo.connectors import Credential
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig
from ufo.sources.sync import SourceAuth, StreamSkipped, SyncResult

ACCOUNT = "acct-1"
CONFERENCE = "conferenceRecords/conf-1"
TRANSCRIPT = f"{CONFERENCE}/transcripts/tr-1"
SMART_NOTE = f"{CONFERENCE}/smartNotes/sn-1"


@dataclass(frozen=True)
class _MockProxy:
    handler: Callable[[httpx.Request], httpx.Response]

    async def credential(self, workspace_id: UUID, provider: str, account: str) -> Credential:
        return Credential(transport=httpx.MockTransport(self.handler))


async def _fetch(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    cursor: str | None = None,
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_MockProxy(handler=handler))
    return await ConnectorBackend(connector=GoogleMeetConnector()).fetch(
        ConnectorSourceConfig(account=ACCOUNT, stream="meeting_artifacts"), cursor, auth
    )


def _conference(name: str = CONFERENCE, start: str = "2026-02-01T10:00:00.000Z") -> dict[str, str]:
    return {
        "name": name,
        "startTime": start,
        "endTime": "2026-02-01T11:00:00.000Z",
        "expireTime": "2026-03-03T11:00:00.000Z",
        "space": "spaces/aaa-bbbb-ccc",
    }


def _transcript() -> dict[str, object]:
    return {
        "name": TRANSCRIPT,
        "state": "FILE_GENERATED",
        "startTime": "2026-02-01T10:01:00.000Z",
        "endTime": "2026-02-01T10:58:00.000Z",
        "docsDestination": {
            "document": "doc-transcript",
            "exportUri": "https://docs.google.com/document/d/doc-transcript/view",
        },
    }


def _smart_note() -> dict[str, object]:
    return {
        "name": SMART_NOTE,
        "state": "FILE_GENERATED",
        "startTime": "2026-02-01T10:02:00.000Z",
        "endTime": "2026-02-01T10:59:00.000Z",
        "docsDestination": {
            "document": "doc-smart",
            "exportUri": "https://docs.google.com/document/d/doc-smart/view",
        },
    }


SMART_NOTE_TEXT = "Summary: retention risk on the platform team.\nAction: Riley owns churn."
SMART_NOTE_DOC: dict[str, object] = {
    "documentId": "doc-smart",
    "title": "Platform 1:1 notes",
    "body": {
        "content": [
            {
                "paragraph": {
                    "elements": [
                        {"textRun": {"content": SMART_NOTE_TEXT}},
                    ]
                }
            }
        ]
    },
}


def _ok_handler(requests: list[httpx.Request]) -> Callable[[httpx.Request], httpx.Response]:
    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        assert request.url.host in {"meet.googleapis.com", "docs.googleapis.com"}
        if request.url.path == "/v2/conferenceRecords":
            assert request.url.params.get("pageSize") == str(CONFERENCE_PAGE_SIZE)
            return httpx.Response(
                200,
                json={"conferenceRecords": [_conference()], "nextPageToken": None},
            )
        if request.url.path == f"/v2/{CONFERENCE}/transcripts":
            return httpx.Response(200, json={"transcripts": [_transcript()]})
        if request.url.path == f"/v2/{TRANSCRIPT}/entries":
            return httpx.Response(
                200,
                json={
                    "transcriptEntries": [
                        {
                            "name": f"{TRANSCRIPT}/entries/e1",
                            "participant": f"{CONFERENCE}/participants/marshall",
                            "text": "Retention worries me.",
                            "languageCode": "en-US",
                            "startTime": "2026-02-01T10:03:00.000Z",
                            "endTime": "2026-02-01T10:03:04.000Z",
                        },
                        {
                            "name": f"{TRANSCRIPT}/entries/e2",
                            "participant": f"{CONFERENCE}/participants/marshall",
                            "text": "Third mention this week.",
                            "languageCode": "en-US",
                            "startTime": "2026-02-01T10:03:05.000Z",
                            "endTime": "2026-02-01T10:03:08.000Z",
                        },
                        {
                            "name": f"{TRANSCRIPT}/entries/e3",
                            "participant": f"{CONFERENCE}/participants/riley",
                            "text": "I will pull the churn numbers.",
                            "languageCode": "en-US",
                            "startTime": "2026-02-01T10:04:00.000Z",
                            "endTime": "2026-02-01T10:04:04.000Z",
                        },
                    ]
                },
            )
        if request.url.path == f"/v2/{CONFERENCE}/smartNotes":
            return httpx.Response(200, json={"smartNotes": [_smart_note()]})
        if (
            request.url.host == "docs.googleapis.com"
            and request.url.path == "/v1/documents/doc-smart"
        ):
            return httpx.Response(200, json=SMART_NOTE_DOC)
        raise AssertionError(f"unexpected request {request.method} {request.url}")

    return handle


async def test_meeting_artifacts_render_transcripts_and_ai_summaries() -> None:
    requests: list[httpx.Request] = []
    result = await _fetch(_ok_handler(requests))

    assert {page.source_ref for page in result.pages} == {"meeting_artifacts/conf-1"}
    assert result.next_cursor == "2026-02-01T10:00:00.000Z"
    assert result.snapshot is False
    assert result.pages[0].created_at == "2026-02-01T10:00:00.000000+00:00"
    assert result.pages[0].updated_at is None
    body = result.pages[0].body
    assert "# googlemeet meeting_artifacts: Google Meet conf-1" in body
    assert "conference: conferenceRecords/conf-1" in body
    assert "space: spaces/aaa-bbbb-ccc" in body
    assert "## Transcripts" in body
    assert "doc: https://docs.google.com/document/d/doc-transcript/view" in body
    assert "marshall: Retention worries me. Third mention this week." in body
    assert "riley: I will pull the churn numbers." in body
    assert "## AI summaries" in body
    assert "doc: https://docs.google.com/document/d/doc-smart/view" in body
    assert "Summary: retention risk on the platform team." in body
    assert "language_code" not in body
    assert "transcriptEntries" not in body


async def test_incremental_filters_conferences_with_the_lookback() -> None:
    seen: list[dict[str, str]] = []

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/conferenceRecords":
            seen.append(dict(request.url.params))
            return httpx.Response(200, json={"conferenceRecords": []})
        raise AssertionError(f"unexpected request {request.url}")

    result = await _fetch(handle, cursor="2026-02-10T12:00:00.000Z")
    assert seen[0]["filter"] == 'start_time >= "2026-02-09T12:00:00.000Z"'
    assert result.next_cursor == "2026-02-10T12:00:00.000Z"
    assert result.pages == ()


async def test_empty_artifact_conferences_still_advance_the_cursor() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/conferenceRecords":
            return httpx.Response(
                200,
                json={
                    "conferenceRecords": [
                        _conference("conferenceRecords/conf-empty", "2026-02-11T09:00:00.000Z")
                    ]
                },
            )
        if request.url.path.endswith("/transcripts"):
            return httpx.Response(200, json={"transcripts": []})
        if request.url.path.endswith("/smartNotes"):
            return httpx.Response(200, json={"smartNotes": []})
        raise AssertionError(f"unexpected request {request.url}")

    result = await _fetch(handle, cursor="2026-02-10T12:00:00.000Z")
    assert result.pages == ()
    assert result.next_cursor == "2026-02-11T09:00:00.000Z"


async def test_docs_refusal_leaves_smart_note_link_instead_of_failing() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v2/conferenceRecords":
            return httpx.Response(200, json={"conferenceRecords": [_conference()]})
        if request.url.path == f"/v2/{CONFERENCE}/transcripts":
            return httpx.Response(200, json={"transcripts": []})
        if request.url.path == f"/v2/{CONFERENCE}/smartNotes":
            return httpx.Response(200, json={"smartNotes": [_smart_note()]})
        if request.url.host == "docs.googleapis.com":
            return httpx.Response(403, json={"error": {"code": 403}})
        raise AssertionError(f"unexpected request {request.url}")

    result = await _fetch(handle)
    assert {page.source_ref for page in result.pages} == {"meeting_artifacts/conf-1"}
    body = result.pages[0].body
    assert "## AI summaries" in body
    assert "doc: https://docs.google.com/document/d/doc-smart/view" in body
    assert "Summary:" not in body


async def test_scope_refusal_yields_stream_skipped() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"error": {"code": 403, "message": "insufficientScopes"}})

    with pytest.raises(StreamSkipped, match="googlemeet"):
        await _fetch(handle)
