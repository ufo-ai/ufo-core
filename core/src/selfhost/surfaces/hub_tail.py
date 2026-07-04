"""The shared hub tail: a streaming surface's live view of one turn.

A subscriber may attach after the publisher started — or after the turn already ended on a peer
loop — so two sources race into one queue: the hub subscription and a poll of the durable turn.
Whichever delivers a stream-ending frame first wins. A frame lost to a full queue costs a redrawn
token, never correctness — the durable terminal-or-parked state always arrives by the poll. A
parked turn is non-terminal, so the poll reads the turn's status, not only its terminal frame."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.hub import Hub, LiveFrame, Parked, Terminal
from selfhost.schema import tables
from selfhost.schema.records import PARKED, TerminalFrame

TERMINAL_POLL_SECONDS = 1.0
PARK_NOTICE = "This turn is parked: over a spend cap. It resumes when the cap is raised."


async def tail_frames(
    hub: Hub, turn_id: UUID, since: str = ""
) -> AsyncIterator[tuple[str, LiveFrame]]:
    """Yield a turn's live frames, each with its cursor, until it ends — a Terminal, or a Parked
    hold — whether the turn is still running or already committed when the caller attaches. A
    reconnecting caller passes the last cursor it saw as `since`: the hub resumes gaplessly from
    there when it still covers that cursor, else the tail redraws from the start of the retained
    ring. Frames sourced from the durable poll carry no cursor (the stream ends on them). The caller
    serializes each frame for its own transport."""
    frames: asyncio.Queue[tuple[str, LiveFrame]] = asyncio.Queue()
    start = since if since and await hub.covers(turn_id, since) else ""
    pump = asyncio.ensure_future(_pump(hub, turn_id, start, frames))
    poll = asyncio.ensure_future(_poll_status(turn_id, frames))
    try:
        stored = await turn_status_frame(turn_id)
        if stored is not None:
            yield "", stored
            return
        while True:
            cursor, frame = await frames.get()
            yield cursor, frame
            if isinstance(frame, Terminal | Parked):
                return
    finally:
        pump.cancel()
        poll.cancel()


async def _pump(
    hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]
) -> None:
    async for item in hub.subscribe(turn_id, since):
        await frames.put(item)


async def _poll_status(turn_id: UUID, frames: asyncio.Queue[tuple[str, LiveFrame]]) -> None:
    while True:
        await asyncio.sleep(TERMINAL_POLL_SECONDS)
        frame = await turn_status_frame(turn_id)
        if frame is not None:
            await frames.put(("", frame))
            return


async def turn_status_frame(turn_id: UUID) -> LiveFrame | None:
    """The frame that ends a turn's stream: its committed Terminal, or a Parked hold when the turn
    is parked. None while it is still queued or running."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one_or_none()
    if row is None:
        return None
    if row.terminal is not None:
        return Terminal(frame=TerminalFrame.model_validate(row.terminal))
    if row.status == PARKED:
        return Parked(message=PARK_NOTICE)
    return None


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
