"""The shared hub tail: it streams a turn's live frames with cursors until a terminal, and resumes
from a cursor the hub still covers, skipping frames the caller already rendered. No turn row exists,
so the durable poll yields None and the live leg alone drives the stream. A keepalive subscription
holds the turn's ring open (a terminal with no subscriber is dropped), standing in for the surface
that is always attached before the turn ends."""

import asyncio
from collections.abc import AsyncIterator
from uuid import UUID, uuid4

from ufo.hub import InProcessHub, LiveFrame, Terminal
from ufo.models.interface import TextDelta
from ufo.schema.records import TerminalFrame
from ufo.surfaces.hub_tail import tail_frames


async def _drain(stream: AsyncIterator[tuple[str, LiveFrame]]) -> None:
    async for _item in stream:
        pass


async def _keepalive(hub: InProcessHub, turn_id: UUID) -> asyncio.Task[None]:
    task = asyncio.create_task(_drain(hub.subscribe(turn_id)))
    await asyncio.sleep(0)
    return task


async def test_tail_streams_live_frames_until_a_terminal(db: None) -> None:
    hub = InProcessHub()
    turn_id = uuid4()
    keep = await _keepalive(hub, turn_id)
    await hub.publish(turn_id, TextDelta(text="a"))
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done", text="a")))
    frames = [frame async for _cursor, frame in tail_frames(hub, turn_id)]
    assert frames == [
        TextDelta(text="a"),
        Terminal(frame=TerminalFrame(status="done", text="a")),
    ]
    keep.cancel()


async def test_tail_resumes_from_a_covered_cursor(db: None) -> None:
    hub = InProcessHub()
    turn_id = uuid4()
    keep = await _keepalive(hub, turn_id)
    seen = await hub.publish(turn_id, TextDelta(text="already-rendered"))
    await hub.publish(turn_id, TextDelta(text="new"))
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    frames = [frame async for _cursor, frame in tail_frames(hub, turn_id, seen)]
    assert frames == [TextDelta(text="new"), Terminal(frame=TerminalFrame(status="done"))]
    keep.cancel()
