"""What a turn says to a member before it ends: which spans of a round become replies, what the
window and the live stream carry instead of their markup, and how one span reaches a durable surface
exactly once across a replayed turn, a racing worker and a redelivery.

The mid-turn arrival harness is reused from the three closed branches this replaces
(`/workspace/mid-turn-response-fix-prs.md`): the engine-level tests run on `test_engine`'s
`_engine`/`_seed_turn`/`_queue_arrival`, the delivery tests on `test_surface`'s `RecordingSurface`
behind the real pollers, and the end-to-end aggregate on `test_turn_lifecycle`'s durable queue — the
only path a durable surface (Slack) is delivered on. Every value is read back from real state: hub
frames, `mid_turn_reply` rows, `writeback` rows, and the surface's own calls.
"""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest
import sqlalchemy as sa
from test_engine import RecordingHub, _engine, _queue_arrival, _seed_turn
from test_surface import (
    SURFACE,
    RecordingSurface,
    StubDbos,
    _context,
    _seed,
    _unused_identify,
    _unused_ingest,
    _writeback,
)
from test_surface import _poller as _writeback_poller
from test_surface import _seed_turn as _seed_surface_turn
from test_turn_lifecycle import (
    STREAM_GATE,
    StandInModel,
    Turns,
    _bootstrap,
    _runtime_parts,
    dbos_runtime,  # noqa: F401 — the lifecycle harness's session runtime, requested below
)
from ufo_ext_ufo.surface import directives_for
from ufo_testsupport.stream_gate import GatingHub, release_when_running

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    Usage,
)
from ufo.harness.replies import MarkedReply
from ufo.runtime import queue as loop_queue
from ufo.runtime.engine import FORCE_FINAL_PROMPT
from ufo.runtime.ext import surface as surface_module
from ufo.runtime.ext.surface import (
    WRITEBACK_DELIVERED,
    WRITEBACK_FAILED,
    WRITEBACK_MAX_AGE_SECONDS,
    WRITEBACK_PENDING,
    MidTurnReplyPoller,
    SurfaceRoute,
    SurfaceSpec,
    WritebackPoller,
    mid_turn_reply_workspaces,
    writeback_workspaces,
)
from ufo.runtime.hub import Reply
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.surfaces.admission import Admission
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import Turn, mid_turn_reply_id_for

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

ANSWERED = UUID("a532d68a-6724-5bd3-b34f-3ec90a57db80")
SPAN_TEXT = "Filed the launch issue as metalcraftai/ufo#1801."
CLOSING_TEXT = "Both are done."


def _span(message: str, body: str) -> str:
    return f'<reply-to message="{message}">\n{body}\n</reply-to>'


@dataclass
class SpeakingModel:
    """A round that works and speaks: it calls a tool, and marks part of its narration for the
    member it answers. Its second round closes the turn."""

    spans: tuple[str, ...] = (SPAN_TEXT,)
    message: str = str(ANSWERED)
    closing: str = CLOSING_TEXT
    rounds: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.rounds += 1
        if self.rounds > 1:
            yield TextDelta(text=self.closing)
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield TextDelta(text="Working on it. ")
        for span in self.spans:
            yield TextDelta(text=_span(self.message, span))
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "true"}')
        yield Usage(input_tokens=1, output_tokens=1)


async def _replies(turn_id: UUID) -> list[sa.Row]:
    async with workspace_tx() as connection:
        return list(
            (
                await connection.execute(
                    sa.select(tables.mid_turn_reply)
                    .where(tables.mid_turn_reply.c.turn_id == turn_id)
                    .order_by(
                        tables.mid_turn_reply.c.round_index, tables.mid_turn_reply.c.span_index
                    )
                )
            ).all()
        )


def _mid_turn_poller(
    workspace_id: UUID,
    surface: RecordingSurface,
    blob: FilesystemBlobStore,
    worker_id: str = "speaker-1",
    speaks: bool = True,
) -> MidTurnReplyPoller:
    spec = SurfaceSpec(
        name=SURFACE,
        routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
        identify=_unused_identify,
        post=surface.post,
        attach=surface.attach,
        speak=surface.speak if speaks else None,
    )
    context = _context(workspace_id, StubDbos(), blob)
    return MidTurnReplyPoller(
        worker_id=worker_id,
        surfaces={SURFACE: spec},
        context_for=lambda _workspace_id, _name: context,
        candidates=mid_turn_reply_workspaces(),
    )


async def _seed_spoken_reply(
    workspace_id: UUID,
    turn_id: UUID,
    text: str = SPAN_TEXT,
    round_index: int = 1,
    span_index: int = 0,
    message_ref: UUID | None = ANSWERED,
    created_at: object | None = None,
) -> UUID:
    reply_id = mid_turn_reply_id_for(turn_id, round_index, span_index)
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.mid_turn_reply).values(
                id=reply_id,
                workspace_id=workspace_id,
                turn_id=turn_id,
                round_index=round_index,
                span_index=span_index,
                message_ref=message_ref,
                text=text,
                status=WRITEBACK_PENDING,
                created_at=created_at or sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return reply_id


async def test_a_marked_span_is_delivered_mid_turn_and_its_words_stay_in_the_window(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A round that still calls a tool answers the member it names: the span becomes one delivery
    row and one Reply frame published before the turn's Terminal, the markup reaches neither, and
    the round's words stay in the window so the model reads what it already said."""
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        hub = RecordingHub()
        model = SpeakingModel()
        engine = replace(_engine(turn, model, tmp_path), hub=hub)
        with caplog.at_level(logging.INFO, logger="ufo"):
            frame = await engine.run()
        rows = await _replies(turn.id)
        stored = await engine.transcript.read()

    assert frame is not None
    assert (frame.status, frame.text) == ("done", CLOSING_TEXT)

    assert [
        (row.round_index, row.span_index, row.message_ref, row.text, row.status) for row in rows
    ] == [(1, 0, ANSWERED, SPAN_TEXT, WRITEBACK_PENDING)]

    replies = [replied for replied in hub.frames if isinstance(replied, Reply)]
    kinds = [type(published).__name__ for published in hub.frames]
    assert [(reply.id, reply.message_ref, reply.text) for reply in replies] == [
        (rows[0].id, ANSWERED, SPAN_TEXT)
    ]
    assert kinds.index("Reply") < kinds.index("Terminal")

    streamed = "".join(f.text for f in hub.frames if isinstance(f, TextDelta))
    assert "reply-to" not in streamed
    assert SPAN_TEXT not in streamed
    assert streamed == "Working on it. " + CLOSING_TEXT

    assert stored is not None
    said = [
        message.content if isinstance(message.content, str) else message.content[0].text
        for message in stored.messages
        if message.role == "assistant"
    ]
    assert any(SPAN_TEXT in text and "reply-to" not in text for text in said)

    spoken = [record.ufo for record in caplog.records if record.getMessage() == "turn.reply_spoken"]
    assert [(entry["message_ref"], entry["round"], entry["chars"]) for entry in spoken] == [
        (str(ANSWERED), 1, len(SPAN_TEXT))
    ]


async def test_a_replayed_round_re_derives_the_same_span_and_writes_no_second_delivery(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A pod roll replays a turn's recorded rounds, so the engine re-runs this body over the same
    round text. The span's identity is derived from the turn, the round and the position, so the
    delivery row it writes again is the row a poller already has — one row, and one telemetry
    event."""
    turn = await _seed_turn("running", None)
    with ws(turn.workspace_id):
        engine = _engine(turn, object(), tmp_path)
        spoken = (MarkedReply(message_ref=ANSWERED, text=SPAN_TEXT),)
        with caplog.at_level(logging.INFO, logger="ufo"):
            await engine._speak(spoken, 3)
            await engine._speak(spoken, 3)
        rows = await _replies(turn.id)

    assert [row.id for row in rows] == [mid_turn_reply_id_for(turn.id, 3, 0, engine.attempt)]
    assert [record.getMessage() for record in caplog.records].count("turn.reply_spoken") == 1


async def test_a_resumed_attempt_speaks_the_spans_it_marked_rather_than_the_parked_ones(
    db: None, tmp_path: Path
) -> None:
    """A turn parked on the spend cap is re-offered under a fresh DBOS workflow id. The resumed run
    has an empty step log: it rebuilds the window, meters its rounds from one again, and marks spans
    of its own under round and span numbers the parked attempt already used. Those are words no
    member has read, so the attempt is part of the span's identity and each set is written and
    delivered — a shared id would drop the resumed words on the conflict and leave the member with a
    reply frame naming text their page already drew."""
    turn = await _seed_turn("running", None)
    with ws(turn.workspace_id):
        engine = _engine(turn, object(), tmp_path)
        await replace(engine, attempt="parked-run")._speak(
            (MarkedReply(message_ref=ANSWERED, text=SPAN_TEXT),), 1
        )
        await replace(engine, attempt="resumed-run")._speak(
            (MarkedReply(message_ref=ANSWERED, text=CLOSING_TEXT),), 1
        )
        rows = await _replies(turn.id)

    assert len(rows) == 2
    assert {(row.id, row.text) for row in rows} == {
        (mid_turn_reply_id_for(turn.id, 1, 0, "parked-run"), SPAN_TEXT),
        (mid_turn_reply_id_for(turn.id, 1, 0, "resumed-run"), CLOSING_TEXT),
    }


async def test_a_subagent_turn_speaks_to_no_member(db: None, tmp_path: Path) -> None:
    """A child's conversation is its parent's private channel: a tag in a child's output is text
    the parent reads, never a member's message. Nothing is delivered, and the markup is stripped
    from its window text all the same."""
    turn = (await _seed_turn("queued", None)).model_copy(update={"subagent_profile": "coding"})
    with ws(turn.workspace_id):
        hub = RecordingHub()
        engine = replace(
            _engine(turn, SpeakingModel(), tmp_path),
            hub=hub,
            output_model=None,
        )
        frame = await engine.run()
        rows = await _replies(turn.id)

    assert frame is not None
    assert rows == []
    assert [published for published in hub.frames if isinstance(published, Reply)] == []
    streamed = "".join(f.text for f in hub.frames if isinstance(f, TextDelta))
    assert "reply-to" not in streamed


async def test_two_spans_in_one_round_are_two_replies_in_the_order_written(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        model = SpeakingModel(spans=("first thing", "second thing"))
        frame = await replace(_engine(turn, model, tmp_path)).run()
        rows = await _replies(turn.id)

    assert frame is not None
    assert [(row.span_index, row.text) for row in rows] == [(0, "first thing"), (1, "second thing")]
    assert [row.id for row in rows] == [
        mid_turn_reply_id_for(turn.id, 1, 0),
        mid_turn_reply_id_for(turn.id, 1, 1),
    ]


@dataclass
class WorkingThenClosingSpanModel:
    """Narrates while it calls a tool, then closes the turn with an answer the model wrapped in a
    reply tag — a first round that streams and a closing round the redaction withholds whole."""

    rounds: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.rounds += 1
        if self.rounds > 1:
            yield TextDelta(text=_span(str(ANSWERED), CLOSING_TEXT))
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield TextDelta(text="Working on it. ")
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "true"}')
        yield Usage(input_tokens=1, output_tokens=1)


def _terminal_lines(frames: list[object]) -> list[list[str]]:
    """What the ufo terminal prints for a turn's frames, keeping the `streamed` bookkeeping
    `stream_directives` keeps: once a delta has been rendered the terminal frame's answer is taken
    to have reached the transcript already, so it is not said a second time."""
    printed: list[list[str]] = []
    streamed = False
    for frame in frames:
        lines = directives_for(frame, streamed)
        if lines and isinstance(frame, TextDelta):
            streamed = True
        printed.extend(line.decode().rstrip("\n").split("\t") for line in lines)
    return printed


async def test_the_closing_span_reaches_the_ufo_terminal_exactly_once(
    db: None, tmp_path: Path
) -> None:
    """The ufo terminal prints the delta stream and caps the turn on the terminal frame, saying that
    frame's text only when no delta preceded it. A closing round's span is delivered by the answer
    and by no Reply frame, so the words the redaction withheld have to ride the delta stream: the
    member reads the answer once, on the surface the whole session is printed on."""
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        hub = RecordingHub()
        engine = replace(_engine(turn, WorkingThenClosingSpanModel(), tmp_path), hub=hub)
        frame = await engine.run()
        rows = await _replies(turn.id)

    assert frame is not None
    assert (frame.status, frame.text.strip()) == ("done", CLOSING_TEXT)
    assert rows == []
    printed = _terminal_lines(hub.frames)
    assert [line for line in printed if CLOSING_TEXT in line[-1]] == [["txt", CLOSING_TEXT]]
    assert printed[-1][0] == "ask"


async def test_the_closing_round_delivers_its_span_as_the_terminal_and_not_twice(
    db: None, tmp_path: Path
) -> None:
    """A tag in the closing round names the same words the terminal reply carries, so the span is
    unwrapped into that reply rather than sent a second time — and the markup never reaches it."""
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        model = ClosingSpanModel()
        frame = await _engine(turn, model, tmp_path).run()
        rows = await _replies(turn.id)

    assert frame is not None
    assert frame.text == f"\n{SPAN_TEXT}\n"
    assert "reply-to" not in frame.text
    assert rows == []


@dataclass
class ClosingSpanModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text=_span(str(ANSWERED), SPAN_TEXT))
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass
class ForcedClosingSpanModel:
    """Spends its round budget on tool calls, then answers with a tagged span at the forced closing
    round — the one round with no tools offered."""

    turn: Turn
    forced: bool = False

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.messages[-1].content == FORCE_FINAL_PROMPT:
            self.forced = True
            yield TextDelta(text=_span(str(ANSWERED), SPAN_TEXT))
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "true"}')
        yield Usage(input_tokens=1, output_tokens=1)


async def test_the_forced_closing_round_carries_its_span_as_the_answer(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        model = ForcedClosingSpanModel(turn=turn)
        engine = replace(_engine(turn, model, tmp_path), max_rounds=1)
        frame = await engine.run()
        rows = await _replies(turn.id)

    assert model.forced
    assert frame is not None
    assert "reply-to" not in frame.text
    assert SPAN_TEXT in frame.text
    assert rows == []


async def test_a_malformed_span_breaks_no_round_and_leaks_no_markup(
    db: None, tmp_path: Path
) -> None:
    """An unclosed tag delivers nothing — the words a member reads are only the ones the model
    closed — and the round still answers."""
    turn = await _seed_turn("queued", None)
    with ws(turn.workspace_id):
        hub = RecordingHub()
        model = UnclosedSpanModel()
        engine = replace(_engine(turn, model, tmp_path), hub=hub)
        frame = await engine.run()
        rows = await _replies(turn.id)

    assert frame is not None
    assert frame.status == "done"
    assert rows == []
    streamed = "".join(f.text for f in hub.frames if isinstance(f, TextDelta))
    assert "reply-to" not in streamed


@dataclass
class UnclosedSpanModel:
    rounds: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.rounds += 1
        if self.rounds > 1:
            yield TextDelta(text=CLOSING_TEXT)
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield TextDelta(text=f'<reply-to message="{ANSWERED}">half a thought')
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "true"}')
        yield Usage(input_tokens=1, output_tokens=1)


async def test_a_spoken_reply_reaches_a_durable_surface_once(db: None, tmp_path: Path) -> None:
    """The delivery leg: one claim, one `speak`, the row delivered with the message's ref recorded.
    A second drain of the same workspace posts nothing — the row is no longer due."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "C1:1.0", "running", "")
    reply_id = await _seed_spoken_reply(workspace_id, turn_id)
    surface = RecordingSurface(ref="C1:9.9")
    poller = _mid_turn_poller(workspace_id, surface, blob)
    with ws(workspace_id):
        await poller.drain()
        await poller.drain()
    rows = await _replies(turn_id)

    assert surface.spoken == [(reply_id, ANSWERED, SPAN_TEXT)]
    assert [(row.status, row.reply_ref) for row in rows] == [(WRITEBACK_DELIVERED, "C1:9.9:1")]
    assert surface.posted == []


async def test_a_second_worker_posts_nothing_a_delivered_row_already_carried(
    db: None, tmp_path: Path
) -> None:
    """Two replicas run the poller. The claim is a compare-and-swap on the worker id, so the row the
    first delivered is not due for the second, and the member reads one message."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "C2:1.0", "running", "")
    await _seed_spoken_reply(workspace_id, turn_id)
    first, second = RecordingSurface(ref="C2:1"), RecordingSurface(ref="C2:2")
    with ws(workspace_id):
        await _mid_turn_poller(workspace_id, first, blob, worker_id="one").drain()
        await _mid_turn_poller(workspace_id, second, blob, worker_id="two").drain()

    assert len(first.spoken) == 1
    assert second.spoken == []


async def test_a_slow_mid_turn_batch_renews_every_claim_before_a_peer_can_recover_it(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "CLEASE:1.0", "running", "")
    reply_ids = (
        await _seed_spoken_reply(workspace_id, turn_id, text="first"),
        await _seed_spoken_reply(workspace_id, turn_id, text="second", span_index=1),
    )
    surface = RecordingSurface(ref="CLEASE:9.9")
    blocked = asyncio.Event()
    release = asyncio.Event()
    speak = surface.speak

    async def blocking_speak(ctx, reply):
        if not blocked.is_set():
            blocked.set()
            await release.wait()
        return await speak(ctx, reply)

    monkeypatch.setattr(surface, "speak", blocking_speak)
    refresh_gate = asyncio.Event()
    all_refreshed = asyncio.Event()
    hold_refresh = asyncio.Event()
    refreshed: set[UUID] = set()
    refresh_claim = MidTurnReplyPoller._refresh_claim

    async def controlled_refresh(self: MidTurnReplyPoller, reply_id: UUID) -> None:
        await refresh_gate.wait()
        await refresh_claim(self, reply_id)
        refreshed.add(reply_id)
        if refreshed == set(reply_ids):
            all_refreshed.set()
        await hold_refresh.wait()

    monkeypatch.setattr(surface_module, "WRITEBACK_CLAIM_REFRESH_SECONDS", 0.0)
    monkeypatch.setattr(MidTurnReplyPoller, "_refresh_claim", controlled_refresh)
    first = _mid_turn_poller(workspace_id, surface, blob, worker_id="worker-1")
    with ws(workspace_id):
        running = asyncio.create_task(first.drain())
        try:
            await asyncio.wait_for(blocked.wait(), 5)
            async with workspace_tx() as connection:
                await connection.execute(
                    sa.update(tables.mid_turn_reply)
                    .where(tables.mid_turn_reply.c.id.in_(reply_ids))
                    .values(claim_expires_at=datetime.now(UTC) - timedelta(seconds=1))
                )
            refresh_gate.set()
            await asyncio.wait_for(all_refreshed.wait(), 5)
            peer_surface = RecordingSurface(ref="CLEASE:peer")
            await _mid_turn_poller(workspace_id, peer_surface, blob, worker_id="worker-2").drain()
            assert peer_surface.spoken == []
        finally:
            release.set()
            await running

    rows = await _replies(turn_id)
    assert [row.id for row in rows] == list(reply_ids)
    assert [row.status for row in rows] == [WRITEBACK_DELIVERED, WRITEBACK_DELIVERED]
    assert [text for _reply, _message, text in surface.spoken] == ["first", "second"]


async def test_a_failed_send_retries_and_ages_out_without_holding_the_turn(
    db: None, tmp_path: Path
) -> None:
    """A refused send returns the row to pending with its error, and a row older than the delivery
    window fails terminally — which is what stops one undeliverable reply silencing the turn."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "C3:1.0", "running", "")
    await _seed_spoken_reply(workspace_id, turn_id)
    surface = RecordingSurface(fail_speak=True)
    with ws(workspace_id):
        await _mid_turn_poller(workspace_id, surface, blob).drain()
        retried = await _replies(turn_id)
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.mid_turn_reply).values(
                    claim_expires_at=None,
                    created_at=datetime.now(UTC)
                    - timedelta(seconds=WRITEBACK_MAX_AGE_SECONDS + 60),
                )
            )
        await _mid_turn_poller(workspace_id, surface, blob).drain()
        aged = await _replies(turn_id)

    assert [(row.status, row.last_error) for row in retried] == [
        (WRITEBACK_PENDING, "speak failed")
    ]
    assert [row.status for row in aged] == [WRITEBACK_FAILED]


async def test_the_terminal_reply_waits_for_the_replies_the_turn_already_spoke(
    db: None, tmp_path: Path
) -> None:
    """Order is the model's: a turn whose spoken reply is still undelivered holds its closing reply
    back, and the writeback lands once that span has left."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "C4:1.0", "done", CLOSING_TEXT)
    await _seed_spoken_reply(workspace_id, turn_id)
    surface = RecordingSurface(ref="C4:9.9")
    writeback, _ = _writeback_poller(workspace_id, surface, blob)
    with ws(workspace_id):
        await writeback.drain()
        held = await _writeback(turn_id)
        posted_while_held = list(surface.posted)
        await _mid_turn_poller(workspace_id, surface, blob).drain()
        await writeback.drain()

    assert (held.status, posted_while_held) == (WRITEBACK_PENDING, [])
    assert [text for _id, _ref, text in surface.spoken] == [SPAN_TEXT]
    assert surface.posted == [turn_id]
    assert (await _writeback(turn_id)).status == WRITEBACK_DELIVERED


async def test_a_surface_that_cannot_speak_settles_the_row_and_blocks_no_terminal(
    db: None, tmp_path: Path
) -> None:
    """A live surface's member read the reply off the hub as the round produced it, so its row is
    settled untouched rather than waiting for a send that has nowhere to go."""
    workspace_id, _, _ = await _seed()
    blob = FilesystemBlobStore(root=tmp_path)
    turn_id = await _seed_surface_turn(workspace_id, "C6:1.0", "done", CLOSING_TEXT)
    await _seed_spoken_reply(workspace_id, turn_id)
    surface = RecordingSurface(ref="C6:9.9")
    with ws(workspace_id):
        await _mid_turn_poller(workspace_id, surface, blob, speaks=False).drain()
        rows = await _replies(turn_id)
        writeback, _ = _writeback_poller(workspace_id, surface, blob)
        await writeback.drain()

    assert surface.spoken == []
    assert [(row.status, row.reply_ref) for row in rows] == [(WRITEBACK_DELIVERED, None)]
    assert surface.posted == [turn_id]


async def test_the_arrival_lifecycle_is_logged_from_the_fold_to_the_window(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A message read but never answered had to be reconstructed from turn starts and writebacks.
    Each arrival event names the turn and the rows it moved, so a coverage gap is measurable without
    being enforced."""
    turn = await _seed_turn("running", None)
    with ws(turn.workspace_id):
        engine = _engine(turn, object(), tmp_path)
        first = await _queue_arrival(turn, "one")
        second = await _queue_arrival(turn, "two", admission_source="internal")
        with caplog.at_level(logging.INFO, logger="ufo"):
            absorbed_ids: list[UUID] = []
            await engine._absorb_arrivals((), [], absorbed_ids, {})
    claimed = [
        record.ufo for record in caplog.records if record.getMessage() == "turn.arrivals_claimed"
    ]
    absorbed = [
        record.ufo for record in caplog.records if record.getMessage() == "turn.arrivals_absorbed"
    ]
    assert [entry["arrivals"] for entry in claimed] == [f"{first} {second}"]
    assert [(entry["arrivals"], entry["members"]) for entry in absorbed] == [
        (f"{first} {second}", 1)
    ]
    assert absorbed_ids == [first, second]


async def test_a_pending_arrival_refuses_the_commit_and_the_refusal_is_logged(
    db: None, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The one rule the engine holds at the terminal: a queued message stops the commit, the row
    stays non-terminal, and both the refusal and the answer it recycled are on the record."""
    turn = await _seed_turn("queued", None)
    member = await _conversation_member(turn.conversation_id)
    with ws(turn.workspace_id):
        model = ArrivingModel(turn=turn, member_id=member)
        engine = _engine(turn, model, tmp_path, member_id=member)
        with caplog.at_level(logging.INFO, logger="ufo"):
            frame = await engine.run()

    assert frame is not None
    assert frame.status == "done"
    guard = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "turn.commit_refused_by_arrivals"
    ]
    recycled = [
        record.ufo for record in caplog.records if record.getMessage() == "turn.answer_recycled"
    ]
    assert [(entry["status"], entry["pending"]) for entry in guard] == [("done", 1)]
    assert [entry["answer_chars"] for entry in recycled] == [len("first answer")]


@dataclass
class ArrivingModel:
    """Answers, and a member message lands while that answer streams — the incident's shape. The
    refused commit recycles the answer and the next round closes over both messages."""

    turn: Turn
    member_id: UUID
    rounds: int = 0

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.rounds += 1
        if self.rounds == 1:
            await _queue_arrival(self.turn, "one more thing", self.member_id)
            yield TextDelta(text="first answer")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield TextDelta(text=CLOSING_TEXT)
        yield Usage(input_tokens=1, output_tokens=1)


async def _conversation_member(conversation_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        member = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()
    assert member is not None
    return member


@pytest.fixture
async def durable(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],  # noqa: F811 — the lifecycle rig
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Turns]:
    """`test_turn_lifecycle`'s own harness, admitting onto a durable surface so admission registers
    the writeback row a Slack-style surface is delivered on."""
    _config, hub, _blob = dbos_runtime
    STREAM_GATE.reset()
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )
    assert loop_queue._runtime is not None
    yield Turns(
        hub=hub,
        admission=Admission(dbos=loop_queue._runtime.dbos, durable_surfaces=frozenset(("cli",))),
    )


def _cli_pollers(
    workspace_id: UUID, blob: FilesystemBlobStore
) -> tuple[MidTurnReplyPoller, WritebackPoller, RecordingSurface]:
    """The real pollers over one recording surface registered as `cli` — one `speak` per reply the
    turn spoke, one `post` per turn."""
    recorder = RecordingSurface(ref="cli:1.0")
    spec = SurfaceSpec(
        name="cli",
        routes=(SurfaceRoute(method="POST", path="", handler=_unused_ingest),),
        identify=_unused_identify,
        post=recorder.post,
        attach=recorder.attach,
        speak=recorder.speak,
    )
    context = _context(workspace_id, StubDbos(), blob)
    surfaces = {"cli": spec}
    return (
        MidTurnReplyPoller(
            worker_id="mid-turn-worker",
            surfaces=surfaces,
            context_for=lambda _workspace_id, _name: context,
            candidates=mid_turn_reply_workspaces(),
        ),
        WritebackPoller(
            worker_id="mid-turn-worker",
            surfaces=surfaces,
            context_for=lambda _workspace_id, _name: context,
            candidates=writeback_workspaces(),
        ),
        recorder,
    )


async def test_a_spoken_reply_and_the_closing_reply_reach_a_durable_surface_in_order(
    durable: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End to end, on the path a durable surface is really delivered on: admission, the engine's
    rounds, both pollers. The turn marks an answer in a round that still calls a tool, so the member
    is sent that reply while the turn works, and the closing reply follows it."""
    original = StandInModel.complete
    rounds: list[int] = []

    def speaking_complete(self: StandInModel, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        async def events() -> AsyncIterator[ModelEvent]:
            contents = [message.content for message in request.messages]
            if not any(
                isinstance(content, str) and "speak-then-close" in content for content in contents
            ):
                async for event in original(self, request):
                    yield event
                return
            rounds.append(1)
            if len(rounds) == 1:
                yield TextDelta(text=f"Working. {_span(str(ANSWERED), SPAN_TEXT)}")
                yield ToolCallStart(id="m1", name="bash")
                yield ToolCallDelta(
                    id="m1",
                    partial_json='{"command": "true"}',
                )
                yield Usage(input_tokens=2, output_tokens=2)
                return
            yield TextDelta(text=CLOSING_TEXT)
            yield Usage(input_tokens=2, output_tokens=2)

        return events()

    monkeypatch.setattr(StandInModel, "complete", speaking_complete)

    seed = await _bootstrap()
    turn_id = await durable.admit(seed, "speak-then-close")
    streamed, terminal = await durable.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert terminal["text"] == CLOSING_TEXT
    assert "reply-to" not in streamed
    assert SPAN_TEXT not in streamed

    rows = await _replies(UUID(turn_id))
    assert [(row.text, row.message_ref) for row in rows] == [(SPAN_TEXT, ANSWERED)]

    _, _, blob = _runtime_parts()
    mid_turn, writeback, recorder = _cli_pollers(seed.workspace_id, blob)
    with ws(seed.workspace_id):
        await writeback.drain()
        posted_while_held = list(recorder.posted)
        await mid_turn.drain()
        await writeback.drain()

    assert posted_while_held == []
    assert [text for _id, _ref, text in recorder.spoken] == [SPAN_TEXT]
    assert recorder.posted == [UUID(turn_id)]
    assert [row.status for row in await _replies(UUID(turn_id))] == [WRITEBACK_DELIVERED]
