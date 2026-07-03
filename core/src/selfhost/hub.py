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


LiveFrame = TextDelta | Terminal


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
