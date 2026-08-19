"""Make streamed-delta assertions deterministic when a fake model races a late stream consumer.

A fake test-model runs on the DBOS workflow loop and can flush its whole response and commit the
turn's terminal frame before a surface's stream consumer has drained it — so a test that connects
after admission intermittently catches only the terminal frame and sees empty streamed text. A real
(slow) provider never loses this race; only the instant fake model does. Two windows cause it: the
consumer subscribes after the turn's frames were published and dropped, and — narrower — the turn
commits its terminal between the consumer subscribing and the tail's initial durable-status check,
which short-circuits to that terminal and discards the buffered live frames.

`GatingHub` closes both without touching product code: for a turn a test asserts streamed deltas on,
it holds the turn's first streamed text delta at publish (keyed by the real turn id, which `turn.id`
carries even for a re-enqueued turn whose DBOS workflow id differs) until the consumer's tail is
past its durable-status check and actively draining. `release_when_running` supplies that release —
wrapping the tail's own `turn_status_frame`, it fires the instant a consumer's check confirms the
turn is still running, which is exactly when the tail begins draining live frames. Publisher (DBOS
loop) and consumer (surface loop) run on different event loops, so the release hops onto its loop.

The hold is opt-in: a test that asserts streamed deltas `arm`s the gate before admitting, so only
its turns are held; turns that run unstreamed (a subagent child, a spend-parked turn resumed by the
sweep) are never armed and so never wait. The wait is bounded: an armed turn whose consumer never
drains fails loud instead of hanging the suite.
"""

import asyncio
import threading
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from uuid import UUID

from ufo.hub import InProcessHub, LiveFrame, SkillLoad, ToolCall
from ufo.models.interface import TextDelta

FIRST_DELTA_GATE_TIMEOUT_SECONDS = 20


@dataclass
class _Gate:
    released: bool = False
    event: asyncio.Event | None = None
    loop: asyncio.AbstractEventLoop | None = None


@dataclass
class StreamGate:
    """Correlates a held publish (waiting on the DBOS loop) to its stream consumer (draining on the
    surface loop) by turn id. `release` frees the turn's held first delta; the hub awaits
    `hold_first_delta`. Either may arrive first: the flag covers release-before-publish, the event
    covers publish-before-release, and the lock makes the interleaving safe."""

    _armed: bool = False
    _gates: dict[str, _Gate] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def reset(self) -> None:
        with self._lock:
            self._armed = False
            self._gates.clear()

    def arm(self) -> None:
        with self._lock:
            self._armed = True

    def release(self, turn_id: str) -> None:
        with self._lock:
            gate = self._gates.setdefault(turn_id, _Gate())
            gate.released = True
            event, loop = gate.event, gate.loop
        if event is not None and loop is not None:
            loop.call_soon_threadsafe(event.set)

    async def hold_first_delta(self, turn_id: str) -> None:
        with self._lock:
            if not self._armed:
                return
            gate = self._gates.setdefault(turn_id, _Gate())
            if gate.released:
                return
            gate.event = gate.event or asyncio.Event()
            gate.loop = asyncio.get_running_loop()
            event = gate.event
        async with asyncio.timeout(FIRST_DELTA_GATE_TIMEOUT_SECONDS):
            await event.wait()


@dataclass(frozen=True)
class GatingHub:
    """Wraps the in-process hub so an armed turn's first streamed text delta is held at publish
    until its consumer's tail is draining. Every frame is delegated unchanged; the only added
    behaviour is the hold on the first text delta. A non-text frame and an unarmed turn pass
    straight through."""

    inner: InProcessHub
    gate: StreamGate

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> str:
        if isinstance(frame, TextDelta):
            await self.gate.hold_first_delta(str(turn_id))
        return await self.inner.publish(turn_id, frame)

    def subscribe(self, turn_id: UUID, cursor: str = "") -> AsyncIterator[tuple[str, LiveFrame]]:
        return self.inner.subscribe(turn_id, cursor)

    async def covers(self, turn_id: UUID, cursor: str) -> bool:
        return await self.inner.covers(turn_id, cursor)

    async def latest_activity(self, turn_id: UUID) -> ToolCall | SkillLoad | None:
        return await self.inner.latest_activity(turn_id)


def release_when_running(
    gate: StreamGate, turn_status_frame: Callable[[UUID], Awaitable[LiveFrame | None]]
) -> Callable[[UUID], Awaitable[LiveFrame | None]]:
    """Wrap the tail's `turn_status_frame` so a consumer's durable-status check releases that turn's
    held first delta the moment it confirms the turn is still running (a `None` result) — which is
    exactly when the tail stops short-circuiting to a durable terminal and begins draining live
    frames. Installed over the tail module for the duration of a test that arms the gate."""

    async def checked(turn_id: UUID) -> LiveFrame | None:
        frame = await turn_status_frame(turn_id)
        if frame is None:
            gate.release(str(turn_id))
        return frame

    return checked
