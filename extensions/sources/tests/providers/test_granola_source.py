"""The Granola connector over a mock tool executor: the first sync's declared window, the notes
hydration in batches, the incremental sync that drops the meetings at or below the cursor and
advances it, an empty window that spends no detail call and leaves the cursor where it stands, an
unsuccessful tool answer as a `StreamFault`, and a credential that executes no tools failing loud.
The meetings stream lands the written notes through the listing and detail calls alone, spending no
transcript call. The transcripts stream reads one call per meeting, renders the speaker turns,
passes a meeting Granola has not transcribed yet, passes a meeting the tool refuses on its own, and
takes the plan gate as a `StreamSkipped`.
Offline — canned tool answers, no DB, no token, no broker."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID, uuid4

import pytest
from ufo_ext_sources.providers.granola import (
    DEFAULT_TIME_RANGE,
    DETAIL_BATCH,
    GET_MEETINGS_TOOL,
    GET_TRANSCRIPT_TOOL,
    LIST_MEETINGS_TOOL,
    MEETINGS_STREAM,
    TRANSCRIPTS_STREAM,
    GranolaConnector,
)

from ufo.runtime.access.connectors import Credential
from ufo.runtime.sources.sync import SourceAuth, StreamFault, StreamSkipped, SyncResult
from ufo.sdk.sources import ConnectorBackend, ConnectorSourceConfig

FIRST = "not_aaa"
SECOND = "not_bbb"
CURSOR = "2026-02-01T10:00:00Z"

ToolAnswer = Callable[[str, Mapping[str, Any]], Mapping[str, Any]]


@dataclass
class _MockProxy:
    answer: ToolAnswer
    calls: list[tuple[str, Mapping[str, Any]]] = field(default_factory=list)

    async def credential(self, workspace_id: UUID, provider: str) -> Credential:
        return Credential(execute=self._execute)

    async def _execute(self, slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        self.calls.append((slug, arguments))
        return self.answer(slug, arguments)


async def _fetch(
    proxy: _MockProxy, *, cursor: str | None = None, stream: str = MEETINGS_STREAM
) -> SyncResult:
    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=proxy)
    return await ConnectorBackend(connector=GranolaConnector()).fetch(
        ConnectorSourceConfig(stream=stream), cursor, auth
    )


def _listed(meeting_id: str, created: str, title: str) -> dict[str, Any]:
    return {"id": meeting_id, "title": title, "created_at": created}


def _answers(
    listed: list[dict[str, Any]], details: list[dict[str, Any]] | None = None
) -> ToolAnswer:
    """Composio's execute envelope over the MCP server's own shapes: the listing under `meetings`,
    the details under `notes`."""

    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {"successful": True, "data": {"meetings": listed}}
        return {"successful": True, "data": {"notes": details or []}}

    return answer


async def test_first_sync_reads_the_declared_window_and_lands_hydrated_meetings() -> None:
    proxy = _MockProxy(
        answer=_answers(
            [
                _listed(SECOND, "2026-02-02T09:00:00Z", "Pricing review"),
                _listed(FIRST, CURSOR, "Roadmap sync"),
            ],
            [
                {
                    "id": FIRST,
                    "created_at": CURSOR,
                    "title": "Roadmap sync",
                    "attendees": [{"name": "Riley"}, "sam@example.com"],
                    "summary": "Ship the connector in March.",
                }
            ],
        )
    )

    result = await _fetch(proxy)

    assert [page.source_ref for page in result.pages] == [
        f"meetings/{FIRST}",
        f"meetings/{SECOND}",
    ]
    assert result.next_cursor == "2026-02-02T09:00:00Z"
    assert result.snapshot is False
    assert proxy.calls[0] == (LIST_MEETINGS_TOOL, {"time_range": DEFAULT_TIME_RANGE})
    assert proxy.calls[1][0] == GET_MEETINGS_TOOL
    assert proxy.calls[1][1] == {"meeting_ids": [FIRST, SECOND]}
    body = result.pages[0].body
    assert "Roadmap sync" in body
    assert "attendees: Riley, sam@example.com" in body
    assert "Ship the connector in March." in body
    assert result.pages[0].created_at is not None


async def test_incremental_sync_re_reads_the_lookback_window_and_drops_what_is_older() -> None:
    """Granola writes the notes after the meeting, so a run lists a day behind the cursor and lands
    the meeting again with what was written by now. A meeting older than that window is dropped."""
    proxy = _MockProxy(
        answer=_answers(
            [
                _listed("not_old", "2026-01-20T09:00:00Z", "Kickoff"),
                _listed(FIRST, CURSOR, "Roadmap sync"),
                _listed(SECOND, "2026-02-03T09:00:00Z", "Pricing review"),
            ]
        )
    )

    result = await _fetch(proxy, cursor=CURSOR)

    assert [page.source_ref for page in result.pages] == [
        f"meetings/{FIRST}",
        f"meetings/{SECOND}",
    ]
    assert result.next_cursor == "2026-02-03T09:00:00Z"
    assert proxy.calls[0][1]["time_range"] == "custom"
    assert proxy.calls[0][1]["custom_start"] == "2026-01-31T10:00:00Z"
    assert proxy.calls[1][1] == {"meeting_ids": [FIRST, SECOND]}


async def test_meetings_hydrate_one_detail_call_per_batch() -> None:
    listed = [
        _listed(f"not_{index:03d}", f"2026-03-{index + 1:02d}T09:00:00Z", f"Meeting {index}")
        for index in range(DETAIL_BATCH + 1)
    ]
    proxy = _MockProxy(answer=_answers(listed))

    result = await _fetch(proxy)

    assert len(result.pages) == DETAIL_BATCH + 1
    detail_calls = [call for call in proxy.calls if call[0] == GET_MEETINGS_TOOL]
    assert [len(call[1]["meeting_ids"]) for call in detail_calls] == [DETAIL_BATCH, 1]


async def test_empty_window_lands_nothing_and_keeps_the_cursor() -> None:
    proxy = _MockProxy(answer=_answers([]))

    result = await _fetch(proxy, cursor=CURSOR)

    assert result.pages == ()
    assert result.next_cursor == CURSOR
    assert [call[0] for call in proxy.calls] == [LIST_MEETINGS_TOOL]


async def test_an_unsuccessful_tool_answer_faults_with_the_tool_name() -> None:
    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"successful": False, "error": "granola: plan does not allow this"}

    with pytest.raises(StreamFault, match=LIST_MEETINGS_TOOL):
        await _fetch(_MockProxy(answer=answer))


async def test_the_meetings_stream_lands_the_written_notes_without_a_transcript_call() -> None:
    """Granola gates the transcript tool behind its paid plans, so the notes a member recalls by
    come from the listing and detail calls alone."""
    proxy = _MockProxy(
        answer=_answers(
            [_listed(FIRST, CURSOR, "Roadmap sync")],
            [
                {
                    "id": FIRST,
                    "created_at": CURSOR,
                    "title": "Roadmap sync",
                    "summary": "Ship the connector in March.",
                }
            ],
        )
    )

    result = await _fetch(proxy, stream=MEETINGS_STREAM)

    assert [call[0] for call in proxy.calls] == [LIST_MEETINGS_TOOL, GET_MEETINGS_TOOL]
    assert "Ship the connector in March." in result.pages[0].body


async def test_the_transcripts_stream_lands_what_was_said_in_each_meeting() -> None:
    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {
                "successful": True,
                "data": {"meetings": [_listed(FIRST, CURSOR, "Roadmap sync")]},
            }
        return {
            "successful": True,
            "data": {
                "transcript": [
                    {"speaker": "Riley", "text": "Ship the connector in March."},
                    {"speaker": "Sam", "text": "Agreed."},
                ]
            },
        }

    proxy = _MockProxy(answer=answer)

    result = await _fetch(proxy, stream=TRANSCRIPTS_STREAM)

    assert [page.source_ref for page in result.pages] == [f"transcripts/{FIRST}"]
    assert result.next_cursor == CURSOR
    assert proxy.calls[1] == (GET_TRANSCRIPT_TOOL, {"meeting_id": FIRST})
    body = result.pages[0].body
    assert "Riley: Ship the connector in March." in body
    assert "Sam: Agreed." in body


async def test_a_meeting_granola_has_not_transcribed_yet_lands_nothing_and_the_run_goes_on() -> (
    None
):
    """A run reads a meeting about a minute after it starts, so an empty transcript means Granola
    has not finished it. That meeting lands no record and the checkpoint passes it, and the
    lookback window is what reads it once the transcript exists."""
    later = "2026-02-03T09:00:00Z"

    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {
                "successful": True,
                "data": {
                    "meetings": [
                        _listed(FIRST, CURSOR, "Roadmap sync"),
                        _listed(SECOND, later, "Pricing review"),
                    ]
                },
            }
        if arguments["meeting_id"] == FIRST:
            return {"successful": True, "data": {"transcript": ""}}
        return {"successful": True, "data": {"transcript": "Ship it."}}

    proxy = _MockProxy(answer=answer)

    result = await _fetch(proxy, cursor="2026-01-01T00:00:00Z", stream=TRANSCRIPTS_STREAM)

    assert [page.source_ref for page in result.pages] == [f"transcripts/{SECOND}"]
    assert result.next_cursor == later
    assert [call[1]["meeting_id"] for call in proxy.calls if call[0] == GET_TRANSCRIPT_TOOL] == [
        FIRST,
        SECOND,
    ]


async def test_a_refused_meeting_lands_no_record_and_the_cursor_passes_it() -> None:
    """A refusal for one meeting — a deleted recording, a meeting Granola holds no transcript for —
    is not the plan gate: the run keeps reading and commits the meetings it could read, so no
    single meeting can hold the stream forever."""
    later = "2026-02-03T09:00:00Z"

    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {
                "successful": True,
                "data": {
                    "meetings": [
                        _listed(FIRST, CURSOR, "Roadmap sync"),
                        _listed(SECOND, later, "Pricing review"),
                    ]
                },
            }
        if arguments["meeting_id"] == FIRST:
            return {"successful": False, "error": "granola: recording was deleted"}
        return {"successful": True, "data": {"transcript": "Ship it."}}

    result = await _fetch(_MockProxy(answer=answer), stream=TRANSCRIPTS_STREAM)

    assert [page.source_ref for page in result.pages] == [f"transcripts/{SECOND}"]
    assert result.next_cursor == later


async def test_the_transcript_plan_gate_skips_the_stream_rather_than_failing_it() -> None:
    """Granola gates transcripts behind its paid plans, and a refusal that holds until the member
    upgrades is not a data failure: the run is skipped and the meetings stream keeps syncing."""

    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {
                "successful": True,
                "data": {"meetings": [_listed(FIRST, CURSOR, "Roadmap sync")]},
            }
        return {"successful": False, "error": "granola: plan does not allow this"}

    with pytest.raises(StreamSkipped, match=GET_TRANSCRIPT_TOOL):
        await _fetch(_MockProxy(answer=answer), stream=TRANSCRIPTS_STREAM)


async def test_the_transcripts_stream_checkpoints_every_detail_batch() -> None:
    listed = [
        _listed(f"not_{index:03d}", f"2026-03-{index + 1:02d}T09:00:00Z", f"Meeting {index}")
        for index in range(DETAIL_BATCH + 1)
    ]

    def answer(slug: str, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if slug == LIST_MEETINGS_TOOL:
            return {"successful": True, "data": {"meetings": listed}}
        return {"successful": True, "data": {"transcript": "Ship it."}}

    proxy = _MockProxy(answer=answer)

    result = await _fetch(proxy, stream=TRANSCRIPTS_STREAM)

    assert len(result.pages) == DETAIL_BATCH + 1
    assert result.next_cursor == listed[-1]["created_at"]
    assert len([call for call in proxy.calls if call[0] == GET_TRANSCRIPT_TOOL]) == DETAIL_BATCH + 1


async def test_a_credential_that_executes_no_tools_fails_loud() -> None:
    @dataclass(frozen=True)
    class _KeyedProxy:
        async def credential(self, workspace_id: UUID, provider: str) -> Credential:
            return Credential(bearer="a-member-added-key")

    auth = SourceAuth(workspace_id=uuid4(), auth_proxy=_KeyedProxy())
    with pytest.raises(RuntimeError, match="executes no broker tools"):
        await ConnectorBackend(connector=GranolaConnector()).fetch(
            ConnectorSourceConfig(stream="meetings"), None, auth
        )
