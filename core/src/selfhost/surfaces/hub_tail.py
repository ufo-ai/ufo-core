"""The shared hub tail: a streaming surface's live view of one turn.

A subscriber may attach after the publisher started — or after the turn already ended on a peer
loop — so two sources race into one queue: the hub subscription and a poll of the durable terminal.
Whichever delivers the Terminal first ends the stream. A frame lost to a full queue costs a redrawn
token, never correctness — the durable terminal always arrives by the poll."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.hub import Hub, LiveFrame, Terminal
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame

TERMINAL_POLL_SECONDS = 1.0


async def tail_frames(hub: Hub, turn_id: UUID) -> AsyncIterator[LiveFrame]:
    """Yield a turn's live frames until its Terminal, whether the turn is still running or already
    committed when the caller attaches. The caller serializes each frame for its own transport."""
    frames: asyncio.Queue[LiveFrame] = asyncio.Queue()
    pump = asyncio.ensure_future(_pump(hub, turn_id, frames))
    poll = asyncio.ensure_future(_poll_terminal(turn_id, frames))
    try:
        stored = await terminal_frame(turn_id)
        if stored is not None:
            yield Terminal(frame=stored)
            return
        while True:
            frame = await frames.get()
            yield frame
            if isinstance(frame, Terminal):
                return
    finally:
        pump.cancel()
        poll.cancel()


async def _pump(hub: Hub, turn_id: UUID, frames: asyncio.Queue[LiveFrame]) -> None:
    async for frame in hub.subscribe(turn_id):
        await frames.put(frame)


async def _poll_terminal(turn_id: UUID, frames: asyncio.Queue[LiveFrame]) -> None:
    while True:
        await asyncio.sleep(TERMINAL_POLL_SECONDS)
        frame = await terminal_frame(turn_id)
        if frame is not None:
            await frames.put(Terminal(frame=frame))
            return


async def terminal_frame(turn_id: UUID) -> TerminalFrame | None:
    """The durable terminal frame a turn committed, or None while it is still queued or running."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
    if row is None or row.terminal is None:
        return None
    return TerminalFrame.model_validate(row.terminal)
