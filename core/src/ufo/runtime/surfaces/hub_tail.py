"""The shared hub tail: a streaming surface's live view of one turn.

A subscriber may attach after the publisher started — or after the turn already ended on a peer
loop — so two sources race into one queue: the hub subscription and a poll of the durable turn.
Whichever delivers a stream-ending frame first wins. A frame lost to a full queue costs a redrawn
token, never correctness — the durable terminal-or-parked state always arrives by the poll. A
parked turn is non-terminal, so the poll reads the turn's status, not only its terminal frame.

The poll is what makes that arrival unconditional, so a read that fails costs one interval and
nothing else: it is the only source that sees a turn committed on a peer loop, and a poll that
stopped on a blip would leave the stream open until the caller gave up."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, aclosing
from dataclasses import dataclass
from uuid import UUID

import sqlalchemy as sa

from ufo.db import workspace_tx
from ufo.harness.o11y import log
from ufo.runtime.authority import authority_member_id, turn_authority
from ufo.runtime.billing.accounting import (
    ALLOW,
    BalanceGate,
    SpendEvaluator,
    applicable_caps_absent,
)
from ufo.runtime.billing.balance import balance_park_message
from ufo.runtime.hub import Activity, ArrivalQueued, Hub, LiveFrame, Parked, Terminal
from ufo.runtime.seats import SEAT_REVOKED_MESSAGE, Seats
from ufo.schema import tables
from ufo.schema.records import PARKED, TerminalFrame

TERMINAL_POLL_SECONDS = 1.0
PARK_NOTICE = "This turn is paused. It resumes on its own."


async def tail_frames(
    hub: Hub,
    turn_id: UUID,
    since: str = "",
    billing_url: str | None = None,
    key_slot_for: Callable[[str], str | None] | None = None,
) -> AsyncGenerator[tuple[str, LiveFrame]]:
    """Yield a turn's live frames, each with its cursor, until it ends — a Terminal, or a Parked
    hold — whether the turn is still running or already committed when the caller attaches. A
    reconnecting caller passes the last cursor it saw as `since`: the hub resumes gaplessly from
    there when it still covers that cursor, else the tail redraws from the start of the retained
    ring. Frames sourced from the durable poll carry no cursor (the stream ends on them). The caller
    serializes each frame for its own transport.

    The pump, the poll, and the hub subscription behind them live exactly as long as this generator:
    closing it runs the `finally` that ends all three. `HubTailer.tail` hands it out inside that
    scope, so a caller that answers on the first terminal frame — leaving the generator suspended at
    its yield — releases all three at its own block's exit."""
    frames: asyncio.Queue[tuple[str, LiveFrame]] = asyncio.Queue()
    start = since if since and await hub.covers(turn_id, since) else ""
    pump = asyncio.ensure_future(_pump(hub, turn_id, start, frames))
    tasks = [pump]
    try:
        stored = await _read_status_frame(turn_id, billing_url, key_slot_for)
        if stored is not None:
            yield "", stored
            return
        tasks.append(
            asyncio.ensure_future(_poll_status(turn_id, frames, billing_url, key_slot_for))
        )
        while True:
            cursor, frame = await frames.get()
            yield cursor, frame
            if isinstance(frame, Terminal | Parked):
                return
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def _pump(
    hub: Hub, turn_id: UUID, since: str, frames: asyncio.Queue[tuple[str, LiveFrame]]
) -> None:
    try:
        async for cursor, frame in hub.subscribe(turn_id, since):
            if isinstance(frame, ArrivalQueued):
                continue
            await frames.put((cursor, frame))
    except Exception as error:
        log("hub_tail.pump_failed", turn=str(turn_id), error=repr(error))


async def _poll_status(
    turn_id: UUID,
    frames: asyncio.Queue[tuple[str, LiveFrame]],
    billing_url: str | None,
    key_slot_for: Callable[[str], str | None] | None,
) -> None:
    while True:
        await asyncio.sleep(TERMINAL_POLL_SECONDS)
        try:
            frame = await _read_status_frame(turn_id, billing_url, key_slot_for)
        except Exception as error:
            log("hub_tail.poll_failed", turn=str(turn_id), error=repr(error))
            continue
        if frame is not None:
            await frames.put(("", frame))
            return


async def _read_status_frame(
    turn_id: UUID, billing_url: str | None, key_slot_for: Callable[[str], str | None] | None
) -> LiveFrame | None:
    read = asyncio.ensure_future(turn_status_frame(turn_id, billing_url, key_slot_for))
    cancelled: asyncio.CancelledError | None = None
    while not read.done():
        try:
            await asyncio.shield(read)
        except asyncio.CancelledError as cancel:
            cancelled = cancel
        except BaseException:
            break
    try:
        frame = read.result()
    except BaseException:
        if cancelled is not None:
            raise cancelled from None
        raise
    if cancelled is not None:
        raise cancelled
    return frame


async def turn_status_frame(
    turn_id: UUID,
    billing_url: str | None = None,
    key_slot_for: Callable[[str], str | None] | None = None,
) -> LiveFrame | None:
    """The frame that ends a turn's stream: its committed Terminal, or a Parked hold when the turn
    is parked. None while it is still queued or running.

    A park's reason reaches the live stream and is never stored, so a poll that finds a parked row
    has to ask what holds it — the same gates the resume sweep re-decides against, in the order
    that names the stop a member can act on. Asking the gates rather than reading a stored string
    keeps the words true as the hold changes: a workspace credited since it stopped reads whatever
    cap still holds it instead of the balance it has already cleared.

    The balance is asked of `BalanceGate.admits` with the same key resolution the resume sweep
    passes it, so its own-key exemption decides here too: a workspace serving turns on its own key
    at its reserve reads the cap that actually holds it, not a refill that would not resume it."""
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.terminal,
                    tables.turn.c.workspace_id,
                    tables.turn.c.agent_id,
                    tables.turn.c.conversation_id,
                    tables.turn.c.speaker_member_id,
                    tables.turn.c.on_behalf_of_member_id,
                    tables.turn.c.admission_source,
                ).where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
        if row is None:
            return None
        if row.terminal is not None:
            return Terminal(frame=TerminalFrame.model_validate(row.terminal))
        if row.status != PARKED:
            return None
        authority = turn_authority(row.speaker_member_id, row.on_behalf_of_member_id)
        if not await Seats(row.workspace_id).admits(connection, authority):
            return Parked(message=SEAT_REVOKED_MESSAGE)
        balance = await BalanceGate(row.workspace_id).admits(
            connection, row.agent_id, key_slot_for, turn_id
        )
        if balance.outcome != ALLOW:
            return Parked(message=balance_park_message(billing_url))
        member_id = authority_member_id(authority)
        if not applicable_caps_absent(row.workspace_id, member_id, row.agent_id):
            decision = await SpendEvaluator(row.workspace_id, member_id, row.agent_id).decide(
                connection, 0
            )
            if decision.outcome != ALLOW:
                return Parked(message=decision.message)
    return Parked(message=PARK_NOTICE)


@dataclass(frozen=True)
class HubTailer:
    """The live-surface seam's tail primitive bound to the process hub: tails one turn's frames off
    the hub, ending on the durable terminal-or-parked state. Structurally a `TurnTailer`, injected
    into a `SurfaceContext` exactly as `MemberAdmission` injects admit — so a surface extension
    tails a turn without importing the hub or this role package."""

    hub: Hub
    billing_url: str | None = None
    key_slot_for: Callable[[str], str | None] | None = None

    def tail(
        self, turn_id: UUID, since: str = ""
    ) -> AbstractAsyncContextManager[AsyncIterator[tuple[str, LiveFrame]]]:
        return aclosing(tail_frames(self.hub, turn_id, since, self.billing_url, self.key_slot_for))

    async def latest_activity(self, turn_id: UUID) -> Activity | None:
        return await self.hub.latest_activity(turn_id)
