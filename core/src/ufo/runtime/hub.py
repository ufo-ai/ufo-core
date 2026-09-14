"""In-process live-frame fan-out with cursor replay: lossy on a full subscriber, publish never
blocks, and a bounded per-turn ring backs replay so a reconnecting subscriber resumes from a
cursor rather than redrawing from scratch."""

import asyncio
import threading
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from itertools import islice
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel

from ufo.harness.models.interface import TextDelta
from ufo.schema.records import TerminalFrame

SUBSCRIBER_QUEUE_FRAMES = 256
REPLAY_BUFFER_FRAMES = 10_000
ACTIVITY_PEEK_FRAMES = 500


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


class Activity(BaseModel):
    """The current one-line summary of a tool step."""

    text: str


class SourceRef(BaseModel):
    """One place a turn read while working: a web page by its address, a synced workspace page by
    its object ref and the provider that feeds it. `title` is what a surface names it by; the
    address or ref is what it opens."""

    kind: Literal["web", "workspace"]
    title: str
    url: str = ""
    ref: str = ""
    provider: str = ""


class Sources(BaseModel):
    """What one step of the turn consulted, pushed as the step lands so a live surface draws the
    places the answer is being drawn from — the prefetch a hook ran before the first model round,
    or a search a tool ran mid-turn. Non-terminal, and distinguished from the other frames by
    carrying `items`. Live only: the reply's citations are the durable record."""

    items: tuple[SourceRef, ...]


class ArtifactsChanged(BaseModel):
    """A turn committed a shared artifact that a live surface can read now."""


class Absorbed(BaseModel):
    """The member's inbound-queue rows a running turn just folded into its window, pushed at the
    round boundary that drained them so a surface holding a message it admitted mid-turn learns the
    agent has it. Non-terminal, and distinguished from the other frames by carrying the `arrivals`
    it absorbed — the `inbound_message` ids member admission returns, and only those: a drain also
    folds prompts an extension invoked and results a child delivered, which no member is waiting on
    and no surface may report as theirs. One frame per drain: the ids a round absorbed clear
    together, since they reach the model in the same round.

    These are the ids in the window now, not a once-per-row announcement: parking releases every row
    the attempt stamped, the absorbed ones included, because a resume is a fresh workflow that must
    re-drain them — so a parked-and-resumed turn publishes an id it already published. A surface
    acting on the frame acts idempotently."""

    arrivals: tuple[UUID, ...]


class Resumed(BaseModel):
    """A turn whose execution died mid-run and which this execution picked back up, pushed once by
    the execution that adopted it so a surface can say the wait it is still serving was interrupted.
    Non-terminal, and distinguished from the other frames by carrying the `attempt` that adopted the
    turn — the identity a surface reports against, so a frame the hub replays to a second reader
    tells it about the same resume rather than a new one."""

    attempt: str


class Reply(BaseModel):
    """One reply delivered to a member while a turn runs: the whole text of a span the model marked
    for delivery, or the source-surface notice for a comment admitted somewhere else, and the
    `message_ref` it answers when one is known.
    Non-terminal, and distinguished from a TextDelta by carrying text the member has been sent
    rather than a chunk in flight — a span's words never ride the delta stream, so this frame is the
    only place a live surface reads them.

    `id` is the span's durable identity, the same id its delivery record carries, so a surface draws
    one bubble per span: the hub replays frames after a cursor, and a recovered turn republishes the
    spans its recorded rounds produced. A surface acting on the frame acts idempotently."""

    id: UUID
    message_ref: UUID | None = None
    text: str = ""
    is_comment: bool = False


class SubagentActivity(BaseModel):
    """One subagent run's member-facing progress, published on the stream of the root turn its
    lineage serves — the turn a surface tails — so a page draws the child working while the parent
    is blocked in the spawn. `turn_id` names the run, `parent_turn_id` the run it nests under, and
    `conversation_id` the record that holds its whole transcript; `name` is the display name the
    spawn gave the run, empty when it gave none, and `profile` stands in for it then. Three moments,
    told apart by which fields carry values: empty `activity` and `status` marks the run starting;
    `activity` carries its current tool-run summary; a non-empty `status` ends its row."""

    turn_id: UUID
    parent_turn_id: UUID
    conversation_id: UUID
    profile: str
    name: str = ""
    activity: str = ""
    status: str = ""


class ArrivalQueued(BaseModel):
    """An arrival joined a turn already running — a member's message, or an internally admitted
    one such as a child's result or a `message_spawn` follow-up. This internal rendezvous wakes
    whatever waits on the turn; a foreground spawn's wait reads it, and surface tails filter it
    out."""

    arrival_id: UUID


LiveFrame = (
    TextDelta
    | Terminal
    | Parked
    | CostTick
    | Activity
    | ArtifactsChanged
    | Absorbed
    | Resumed
    | Reply
    | SubagentActivity
    | Sources
)
HubFrame = LiveFrame | ArrivalQueued


class Hub(Protocol):
    """The per-turn live-frame stream a surface tails. Cursor-replayable: `publish` returns the
    opaque cursor of the frame it appended, `subscribe(cursor)` replays the frames after that cursor
    before streaming live ones, and `covers` reports whether the hub still holds a cursor so a
    reconnecting surface knows to resume gaplessly or redraw. `latest_activity` peeks the newest
    retained Activity without subscribing — a status read's one-frame view of what a
    running turn is doing. Core ships the in-process backend; a shared backend an extension
    registers through its Manifest `hubs` point fans out across processes, which is what lifts the
    single-instance boot guard."""

    async def publish(self, turn_id: UUID, frame: HubFrame) -> str: ...

    def subscribe(self, turn_id: UUID, cursor: str = "") -> AsyncIterator[tuple[str, HubFrame]]: ...

    async def covers(self, turn_id: UUID, cursor: str) -> bool: ...

    async def latest_activity(self, turn_id: UUID) -> Activity | None: ...


def _offer(queue: asyncio.Queue[tuple[str, HubFrame]], item: tuple[str, HubFrame]) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(item)


@dataclass
class _TurnStream:
    """One turn's live state: the replay ring, the live subscribers, the monotonic cursor
    sequence, and whether the turn's stream has ended. Mutated only under the hub's lock."""

    buffer: deque[tuple[str, HubFrame]]
    subscribers: list[tuple[asyncio.Queue[tuple[str, HubFrame]], asyncio.AbstractEventLoop]]
    seq: int = 0
    ended: bool = False


@dataclass(frozen=True)
class InProcessHub:
    """Fan out frames per turn and retain a bounded ring for replay; a full subscriber loses its
    oldest frame, never the publisher.

    Publishers and subscribers may live on different event loops (DBOS runs dequeued workflows on
    its own loop thread), so a lock guards the shared per-turn state and delivery hops onto the
    subscriber's loop. The ring is dropped once the turn's stream has ended (a Terminal or Parked):
    at that publish when no subscriber is attached, else when the last one leaves — so retained
    memory is bounded to in-flight turns. An in-flight turn keeps its ring while no subscriber is
    attached, because the terminal client disconnects at every op it hands the member's machine and
    a frame taken off the dying subscription but never rendered — the tool note racing the op
    directive — must replay on the cursor the reconnect carries rather than vanish with the ring.
    A subscriber with no cursor replays the whole retained ring, exactly as the Redis hub replays
    its stream; one attaching after the ring is gone replays nothing and relies on the durable poll
    for the terminal state.

    Cursors are a per-turn monotonic sequence, and the turn keeps it across those drops: a sequence
    restarting at one would issue cursors a client already passed, so frames published after the
    restart would be filtered out as seen and lost. The mark is dropped on the turn's terminal,
    never on its park: a parked turn resumes under the same id, and its resumed run must not
    reissue the cursors the client already holds.

    A SubagentActivity frame never founds or revives a stream: it mirrors a child's work onto the
    root turn a surface tails, an in-flight root always holds its ring, and a root that ended has
    no tail the frame could reach — so a background child that outlives its root drops these
    frames rather than rebuilding the dropped ring and pinning it for the life of the process.
    """

    _turns: dict[UUID, _TurnStream] = field(default_factory=dict)
    _marks: dict[UUID, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _stream(self, turn_id: UUID) -> _TurnStream:
        """This turn's ring, opened at the cursor it last reached. Call under the lock."""
        stream = self._turns.get(turn_id)
        if stream is None:
            stream = _TurnStream(
                buffer=deque(maxlen=REPLAY_BUFFER_FRAMES),
                subscribers=[],
                seq=self._marks.get(turn_id, 0),
            )
            self._turns[turn_id] = stream
        return stream

    async def publish(self, turn_id: UUID, frame: HubFrame) -> str:
        with self._lock:
            held = self._turns.get(turn_id)
            if isinstance(frame, SubagentActivity) and (held is None or held.ended):
                return ""
            stream = self._stream(turn_id)
            stream.seq += 1
            cursor = str(stream.seq)
            self._marks[turn_id] = stream.seq
            stream.buffer.append((cursor, frame))
            targets = list(stream.subscribers)
            if isinstance(frame, Terminal):
                del self._marks[turn_id]
            if isinstance(frame, Terminal | Parked):
                stream.ended = True
                if not targets:
                    del self._turns[turn_id]
        for queue, loop in targets:
            loop.call_soon_threadsafe(_offer, queue, (cursor, frame))
        return cursor

    async def subscribe(
        self, turn_id: UUID, cursor: str = ""
    ) -> AsyncIterator[tuple[str, HubFrame]]:
        """Replay the buffered frames after `cursor`, then stream live ones. Registering the live
        queue and snapshotting the buffer happen under one lock, and publish appends then snapshots
        subscribers under the same lock, so every frame reaches this subscriber exactly once: a
        frame the snapshot missed was published after registration and so was fanned to the
        just-registered queue, and a frame in the snapshot was published before registration and so
        was not fanned. Live frames therefore always follow the replay, never overlap it."""
        queue: asyncio.Queue[tuple[str, HubFrame]] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_FRAMES)
        entry = (queue, asyncio.get_running_loop())
        with self._lock:
            stream = self._stream(turn_id)
            after = int(cursor) if cursor else 0
            stream.subscribers.append(entry)
            replay = [item for item in stream.buffer if int(item[0]) > after]
        try:
            for item in replay:
                yield item
            while True:
                yield await queue.get()
        finally:
            with self._lock:
                held = self._turns.get(turn_id)
                if held is not None and entry in held.subscribers:
                    held.subscribers.remove(entry)
                    if not held.subscribers and (held.ended or not held.buffer):
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

    async def latest_activity(self, turn_id: UUID) -> Activity | None:
        """The newest activity frame among the last `ACTIVITY_PEEK_FRAMES` the ring retains for this
        turn — what the turn is doing right now, for a status read that never subscribes. None when
        the hub holds no ring for the turn, or none of those frames is an Activity.

        The bound is the peek's whole point: a turn only streaming text carries no activity frame
        at all, and searching a ten-thousand-frame ring to learn that is work a four-second poll
        repeats for every such agent. A turn that has published this many frames since its last
        tool call or skill load has been narrating prose for thousands of tokens, so that frame no
        longer names what it is doing and the honest answer is None."""
        with self._lock:
            stream = self._turns.get(turn_id)
            if stream is None:
                return None
            for _cursor, frame in islice(reversed(stream.buffer), ACTIVITY_PEEK_FRAMES):
                if isinstance(frame, Activity):
                    return frame
        return None
