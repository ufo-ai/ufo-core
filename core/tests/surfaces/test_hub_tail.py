"""The shared hub tail: it streams a turn's live frames with cursors until a terminal, and resumes
from a cursor the hub still covers, skipping frames the caller already rendered. No turn row exists,
so the durable poll yields None and the live leg alone drives the stream. A keepalive subscription
holds the turn's ring open (a terminal with no subscriber is dropped), standing in for the surface
that is always attached before the turn ends."""

import asyncio
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.models.interface import TextDelta
from ufo.runtime.hub import ArrivalQueued, HubFrame, InProcessHub, LiveFrame, Terminal
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.surfaces.hub_tail import tail_frames
from ufo.schema import tables
from ufo.schema.records import TerminalFrame


async def _drain(stream: AsyncIterator[tuple[str, HubFrame]]) -> None:
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


async def test_tail_filters_arrival_rendezvous_frames(db: None) -> None:
    hub = InProcessHub()
    turn_id = uuid4()
    keep = await _keepalive(hub, turn_id)
    await hub.publish(turn_id, ArrivalQueued(arrival_id=uuid4()))
    await hub.publish(turn_id, TextDelta(text="answering"))
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    frames = [frame async for _cursor, frame in tail_frames(hub, turn_id)]
    assert frames == [TextDelta(text="answering"), Terminal(frame=TerminalFrame(status="done"))]
    keep.cancel()


async def test_ended_tail_leaves_no_pump_or_poll_behind(db: None) -> None:
    hub = InProcessHub()
    turn_id = uuid4()
    keep = await _keepalive(hub, turn_id)
    await hub.publish(turn_id, Terminal(frame=TerminalFrame(status="done")))
    await _drain(tail_frames(hub, turn_id))
    lingering = [
        task
        for task in asyncio.all_tasks()
        if task.get_coro().__qualname__ in ("_pump", "_poll_status")
    ]
    assert lingering == []
    keep.cancel()


async def test_poll_starts_after_the_durable_precheck(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    poll_started = asyncio.Event()

    async def status(turn_id: UUID, billing_url: str | None = None) -> LiveFrame | None:
        started.set()
        await release.wait()
        return None

    async def poll(
        turn_id: UUID,
        frames: asyncio.Queue[tuple[str, LiveFrame]],
        billing_url: str | None,
    ) -> None:
        poll_started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(hub_tail, "turn_status_frame", status)
    monkeypatch.setattr(hub_tail, "_poll_status", poll)
    task = asyncio.create_task(_drain(tail_frames(InProcessHub(), uuid4())))
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        await asyncio.sleep(0)
        assert not poll_started.is_set()
        release.set()
        await asyncio.wait_for(poll_started.wait(), timeout=1)
    finally:
        release.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_cancel_waits_for_the_durable_read_to_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    finished = asyncio.Event()

    async def status(turn_id: UUID, billing_url: str | None = None) -> LiveFrame | None:
        started.set()
        await release.wait()
        finished.set()
        raise RuntimeError("the closing read failed")

    monkeypatch.setattr(hub_tail, "turn_status_frame", status)
    task = asyncio.create_task(_drain(tail_frames(InProcessHub(), uuid4())))
    await started.wait()
    task.cancel()
    await asyncio.sleep(0)

    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()


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


@dataclass(frozen=True)
class PoisonedHub:
    """A hub whose subscribe dies on its first pull — a poisoned backend deserialization or wire
    shape — so the tail must log the pump's death and still end on the durable poll."""

    async def publish(self, turn_id: UUID, frame: HubFrame) -> str:
        raise AssertionError("the tail never publishes")

    def subscribe(self, turn_id: UUID, cursor: str = "") -> AsyncIterator[tuple[str, HubFrame]]:
        async def poisoned() -> AsyncIterator[tuple[str, HubFrame]]:
            raise RuntimeError("unreadable XREAD response")
            yield "", TextDelta(text="")

        return poisoned()

    async def covers(self, turn_id: UUID, cursor: str) -> bool:
        return False


async def _seed_running_turn() -> UUID:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="running",
                inbound="hi",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_a_poisoned_subscribe_logs_and_the_poll_still_ends_the_tail(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="ufo")
    monkeypatch.setattr(hub_tail, "TERMINAL_POLL_SECONDS", 0.05)
    turn_id = await _seed_running_turn()
    collected: list[LiveFrame] = []

    async def consume() -> None:
        async for _cursor, frame in tail_frames(PoisonedHub(), turn_id):
            collected.append(frame)

    task = asyncio.create_task(consume())
    await asyncio.sleep(0.1)
    terminal = TerminalFrame(status="done", text="answered")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(status="done", terminal=terminal.model_dump(mode="json"))
        )
    await asyncio.wait_for(task, timeout=5)
    assert collected == [Terminal(frame=terminal)]
    assert any(record.message == "hub_tail.pump_failed" for record in caplog.records)


async def test_a_failing_poll_logs_and_the_live_leg_still_ends_the_tail(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The pre-check read returns None once, then every poll read raises — each failure must land
    in the event log while the healthy hub still ends the stream on its Terminal."""
    caplog.set_level(logging.INFO, logger="ufo")
    monkeypatch.setattr(hub_tail, "TERMINAL_POLL_SECONDS", 0.01)
    reads = {"count": 0}

    async def wedged_after_precheck(
        turn_id: UUID, billing_url: str | None = None
    ) -> LiveFrame | None:
        reads["count"] += 1
        if reads["count"] == 1:
            return None
        raise RuntimeError("wedged query")

    monkeypatch.setattr(hub_tail, "turn_status_frame", wedged_after_precheck)
    hub = InProcessHub()
    turn_id = uuid4()
    keep = await _keepalive(hub, turn_id)
    collected: list[LiveFrame] = []

    async def consume() -> None:
        async for _cursor, frame in tail_frames(hub, turn_id):
            collected.append(frame)

    task = asyncio.create_task(consume())
    deadline = asyncio.get_running_loop().time() + 5
    while not any(record.message == "hub_tail.poll_failed" for record in caplog.records):
        assert asyncio.get_running_loop().time() < deadline, "poll failure never logged"
        await asyncio.sleep(0.01)
    terminal = TerminalFrame(status="done", text="answered")
    await hub.publish(turn_id, Terminal(frame=terminal))
    await asyncio.wait_for(task, timeout=5)
    assert collected == [Terminal(frame=terminal)]
    keep.cancel()


async def test_a_read_that_fails_once_still_ends_the_tail_on_the_durable_state(
    db: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A turn committed on a peer loop publishes nothing here, so the poll is the only thing that
    can end this stream. One failed read must therefore cost one interval and nothing more: the
    next read finds the committed terminal and the tail ends on it."""
    caplog.set_level(logging.INFO, logger="ufo")
    monkeypatch.setattr(hub_tail, "TERMINAL_POLL_SECONDS", 0.01)
    turn_id = await _seed_running_turn()
    durable = hub_tail.turn_status_frame
    reads = {"count": 0}

    async def blips_on_the_first_poll(
        turn: UUID, billing_url: str | None = None
    ) -> LiveFrame | None:
        reads["count"] += 1
        if reads["count"] == 2:
            raise RuntimeError("wedged query")
        return await durable(turn, billing_url)

    monkeypatch.setattr(hub_tail, "turn_status_frame", blips_on_the_first_poll)
    hub = InProcessHub()
    keep = await _keepalive(hub, turn_id)
    collected: list[LiveFrame] = []

    async def consume() -> None:
        async for _cursor, frame in tail_frames(hub, turn_id):
            collected.append(frame)

    task = asyncio.create_task(consume())
    deadline = asyncio.get_running_loop().time() + 5
    while not any(record.message == "hub_tail.poll_failed" for record in caplog.records):
        assert asyncio.get_running_loop().time() < deadline, "poll failure never logged"
        await asyncio.sleep(0.01)
    terminal = TerminalFrame(status="done", text="answered")
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(status="done", terminal=terminal.model_dump(mode="json"))
        )

    await asyncio.wait_for(task, timeout=5)
    assert collected == [Terminal(frame=terminal)]
    keep.cancel()
