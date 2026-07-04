"""In-process live-frame fan-out: lossy by contract, publish never blocks."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from selfhost.models.interface import TextDelta
from selfhost.schema.records import TerminalFrame

SUBSCRIBER_QUEUE_FRAMES = 256


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
    async def publish(self, turn_id: UUID, frame: LiveFrame) -> None: ...

    def subscribe(self, turn_id: UUID) -> AsyncIterator[LiveFrame]: ...


def _offer(queue: asyncio.Queue[LiveFrame], frame: LiveFrame) -> None:
    if queue.full():
        queue.get_nowait()
    queue.put_nowait(frame)


@dataclass(frozen=True)
class InProcessHub:
    """Fan out frames per turn; a full subscriber loses its oldest frame, never the publisher.

    Publishers and subscribers may live on different event loops (DBOS runs dequeued
    workflows on its own loop thread), so delivery hops onto the subscriber's loop.
    """

    queues: dict[UUID, list[tuple[asyncio.Queue[LiveFrame], asyncio.AbstractEventLoop]]] = field(
        default_factory=dict
    )

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> None:
        for queue, loop in list(self.queues.get(turn_id, [])):
            loop.call_soon_threadsafe(_offer, queue, frame)

    async def subscribe(self, turn_id: UUID) -> AsyncIterator[LiveFrame]:
        queue: asyncio.Queue[LiveFrame] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_FRAMES)
        entry = (queue, asyncio.get_running_loop())
        self.queues.setdefault(turn_id, []).append(entry)
        try:
            while True:
                yield await queue.get()
        finally:
            remaining = self.queues[turn_id]
            remaining.remove(entry)
            if not remaining:
                del self.queues[turn_id]
