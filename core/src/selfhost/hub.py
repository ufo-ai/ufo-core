"""In-process live-frame fan-out: lossy by contract, publish never blocks."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol
from uuid import UUID

from pydantic import BaseModel

from selfhost.models import TextDelta
from selfhost.schema.records import TerminalFrame

SUBSCRIBER_QUEUE_FRAMES = 256


class Terminal(BaseModel):
    frame: TerminalFrame


LiveFrame = TextDelta | Terminal


class Hub(Protocol):
    async def publish(self, turn_id: UUID, frame: LiveFrame) -> None: ...

    def subscribe(self, turn_id: UUID) -> AsyncIterator[LiveFrame]: ...


@dataclass(frozen=True)
class InProcessHub:
    """Fan out frames per turn; a full subscriber loses its oldest frame, never the publisher."""

    queues: dict[UUID, list[asyncio.Queue[LiveFrame]]] = field(default_factory=dict)

    async def publish(self, turn_id: UUID, frame: LiveFrame) -> None:
        for queue in self.queues.get(turn_id, []):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(frame)

    async def subscribe(self, turn_id: UUID) -> AsyncIterator[LiveFrame]:
        queue: asyncio.Queue[LiveFrame] = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_FRAMES)
        self.queues.setdefault(turn_id, []).append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            remaining = self.queues[turn_id]
            remaining.remove(queue)
            if not remaining:
                del self.queues[turn_id]
