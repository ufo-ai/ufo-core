"""In-process live-frame fan-out with cursor replay: lossy on a full subscriber, publish never
blocks, and a bounded per-turn ring backs replay so a reconnecting subscriber resumes from a
cursor rather than redrawing from scratch."""

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from selfhost.models.interface import TextDelta
from selfhost.schema.records import TerminalFrame

SUBSCRIBER_QUEUE_FRAMES = 256
REPLAY_BUFFER_FRAMES = 10_000


class Terminal(BaseModel):
    frame: TerminalFrame


class Parked(BaseModel):
    """A turn held by a spend cap: non-terminal, so the durable turn stays resumable, but it ends a
    surface's live stream with the reason the way a Terminal does. Carries only `message` so the CLI
    and web streams distinguish it from a TextDelta (`text`) and a Terminal (`frame`)."""

    message: str


class CostTick(BaseModel):
    """A running spend total pushed as a turn accrues cost — the priced micro-USD and tokens spent
    so far this turn — so a surface shows a live cost meter before the terminal frame lands.
    Non-terminal (the stream continues), and distinguished from a TextDelta (`text`), Terminal
    (`frame`), and Parked (`message`) by its own `cost_micro_usd`."""

    cost_micro_usd: int
    tokens: int


class ToolCall(BaseModel):
    """A tool call entering dispatch, pushed so a surface shows live activity — 'running bash' — on
    a long multi-tool turn instead of an idle bubble carrying only a cost meter. Non-terminal, and
    distinguished from the other frames by its `tool` name and bounded args `preview`."""

    tool: str
    preview: str


class SkillLoad(BaseModel):
    """A skill mounting into the workspace as `load_skill` dispatches, pushed so a surface shows the
    workflow the agent is pulling in. Non-terminal, and distinguished from a ToolCall by carrying
    the `skill` name it loads rather than a tool name."""

    skill: str


LiveFrame = TextDelta | Terminal | Parked | CostTick | ToolCall | SkillLoad


class Hub(Protocol):
    """The per-turn live-frame stream a surface tails. Cursor-replayable: `publish` returns the
    opaque cursor of the frame it appended, `subscribe(cursor)` replays the frames after that cursor
    before streaming live ones, and `covers` reports whether the hub still holds a cursor so a
    reconnecting surface knows to resume gaplessly or redraw. Core ships the in-process backend; a
    shared backend an extension registers through its Manifest `hubs` point fans out across
    processes, which is what lifts the single-instance boot guard."""

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> str: ...

    def subscribe(
        self, turn_id: UUID, cursor: str = ""
    ) -> AsyncIterator[tuple[str, LiveFrame]]: ...

    async def covers(self, turn_id: UUID, cursor: str) -> bool: ...


def _offer(queue: asyncio.Queue[tuple[str, LiveFrame]], item: tuple[str, LiveFrame]) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


@dataclass
class _TurnStream:
    """One turn's live state: the replay ring, the live subscribers, and the monotonic cursor
    sequence. Mutated only under the hub's lock."""

    buffer: deque[tuple[str, LiveFrame]]
    subscribers: list[tuple[asyncio.Queue[tuple[str, LiveFrame]], asyncio.AbstractEventLoop]]
    seq: int = 0


@dataclass(frozen=True)
class InProcessHub:
    """Fan out frames per turn and retain a bounded ring for replay; a full subscriber loses its
    oldest frame, never the publisher.

    Publishers and subscribers may live on different event loops (DBOS runs dequeued workflows on
    its own loop thread), so a lock guards the shared per-turn state and delivery hops onto the
    subscriber's loop. Cursors are a per-turn monotonic sequence. The ring is dropped when a turn's
    stream ends (a Terminal or Parked) with no subscriber attached, and when the last subscriber
    leaves, so retained memory is bounded to in-flight turns; a subscriber attaching after the ring
    is gone replays nothing and relies on the durable poll for the terminal state.
    """

    _turns: dict[UUID, _TurnStream] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> str:
        with self._lock:
            stream = self._turns.get(turn_id)
            if stream is None:
                stream = _TurnStream(buffer=deque(maxlen=REPLAY_BUFFER_FRAMES), subscribers=[])
                self._turns[turn_id] = stream
            stream.seq += 1
            cursor = str(stream.seq)
            stream.buffer.append((cursor, frame))
            targets = list(stream.subscribers)
            if isinstance(frame, Terminal | Parked) and not targets:
                del self._turns[turn_id]
        for queue, loop in targets:
            loop.call_soon_threadsafe(_offer, queue, (cursor, frame))
        return cursor

    async def subscribe(
        self, turn_id: UUID, cursor: str = ""
    ) -> AsyncIterator[tuple[str, LiveFrame]]:
        """Replay the buffered frames after `cursor`, then stream live ones. Registering the live
        queue and snapshotting the buffer happen under one lock, and publish appends then snapshots
        subscribers under the same lock, so every frame reaches this subscriber exactly once: a
        frame the snapshot missed was published after registration and so was fanned to the
        just-registered queue, and a frame in the snapshot was published before registration and so
        was not fanned. Live frames therefore always follow the replay, never overlap it."""
        queue: asyncio.Queue[tuple[str, LiveFrame]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_FRAMES)
        entry = (queue, asyncio.get_running_loop())
        after = int(cursor) if cursor else 0
        with self._lock:
            stream = self._turns.get(turn_id)
            if stream is None:
                stream = _TurnStream(buffer=deque(maxlen=REPLAY_BUFFER_FRAMES), subscribers=[])
                self._turns[turn_id] = stream
            stream.subscribers.append(entry)
            replay = [item for item in stream.buffer if int(item[0]) > after]
        try:
            for item in replay:
                yield item
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                stream = self._turns.get(turn_id)
                if stream is not None and entry in stream.subscribers:
                    stream.subscribers.remove(entry)
                    if not stream.subscribers:
                        del self._turns[turn_id]

    async def covers(self, turn_id: UUID, cursor: str) -> bool:
        if not cursor:
            return False
        with self._lock:
            stream = self._turns.get(turn_id)
            if stream is None or not stream.buffer:
                return False
            earliest = stream.buffer[0][0]
        return int(earliest) <= int(cursor)
