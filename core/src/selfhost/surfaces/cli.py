"""The CLI surface: bearer-token identity, turn admission, live stream, cancel."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient, EnqueueOptions
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from selfhost.db import workspace_tx
from selfhost.hub import Hub, LiveFrame, Terminal
from selfhost.o11y import log
from selfhost.schema import tables
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    DEFAULT_AGENT_NAME,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    turn_id_for,
)

MAX_INBOUND_CHARS = 200_000
TERMINAL_POLL_SECONDS = 1.0

router = APIRouter(prefix="/v1")


@dataclass(frozen=True)
class CliIdentity:
    member_id: UUID
    workspace_id: UUID


async def _authenticate(authorization: str) -> CliIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    digest = hashlib.sha256(token.encode()).hexdigest()
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.surface_identity.c.member_id, tables.surface_identity.c.workspace_id
                ).where(
                    tables.surface_identity.c.surface == "cli",
                    tables.surface_identity.c.external_id == digest,
                )
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(401, "unknown token")
    return CliIdentity(member_id=row.member_id, workspace_id=row.workspace_id)


@router.post("/chat")
async def chat(
    request: Request,
    authorization: str = Header(default=""),
    x_selfhost_session: str = Header(default=""),
    x_selfhost_agent: str = Header(default=DEFAULT_AGENT_NAME),
) -> dict[str, str]:
    identity = await _authenticate(authorization)
    if not x_selfhost_session:
        raise HTTPException(400, "missing x-selfhost-session header")
    inbound = (await request.body()).decode()
    if not inbound.strip():
        raise HTTPException(400, "empty message")
    if len(inbound) > MAX_INBOUND_CHARS:
        raise HTTPException(413, f"message exceeds {MAX_INBOUND_CHARS} characters")
    conversation = await _conversation_for(identity, x_selfhost_session)
    if conversation.member_id != identity.member_id:
        raise HTTPException(403, "conversation belongs to another member")
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == identity.workspace_id,
                    tables.agent.c.name == x_selfhost_agent,
                )
            )
        ).one_or_none()
        if agent is None:
            raise HTTPException(404, f"no agent named {x_selfhost_agent!r}")
        await connection.execute(
            sa.select(tables.conversation.c.id)
            .where(tables.conversation.c.id == conversation.id)
            .with_for_update()
        )
        seq = (
            await connection.execute(
                sa.select(sa.func.coalesce(sa.func.max(tables.turn.c.seq), 0) + 1).where(
                    tables.turn.c.conversation_id == conversation.id
                )
            )
        ).scalar_one()
        turn_id = turn_id_for(identity.workspace_id, conversation.id, seq)
        already_admitted = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
        if already_admitted is None:
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=identity.workspace_id,
                    conversation_id=conversation.id,
                    agent_id=agent.id,
                    seq=seq,
                    status="queued",
                    inbound=inbound,
                    terminal=None,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    options: EnqueueOptions = {
        "queue_name": TURN_QUEUE_NAME,
        "workflow_name": TURN_WORKFLOW_NAME,
        "workflow_id": str(turn_id),
        "queue_partition_key": str(conversation.id),
        "app_version": DBOS_APP_VERSION,
    }
    client: DBOSClient = request.app.state.dbos
    try:
        await client.enqueue_async(options, str(turn_id))
    except Exception:
        frame = TerminalFrame(status="failed", error_class="EnqueueFailed")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    status="failed",
                    terminal=frame.model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == turn_id,
                    tables.turn.c.status.in_(("queued", "running")),
                )
            )
        raise
    return {"turn_id": str(turn_id)}


@router.get("/turns/{turn_id}/stream")
async def stream_turn(
    turn_id: UUID, request: Request, authorization: str = Header(default="")
) -> StreamingResponse:
    identity = await _authenticate(authorization)
    await _require_turn(turn_id, identity)
    hub: Hub = request.app.state.hub
    return StreamingResponse(_frame_lines(hub, turn_id), media_type="application/x-ndjson")


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(
    turn_id: UUID, request: Request, authorization: str = Header(default="")
) -> dict[str, str]:
    identity = await _authenticate(authorization)
    await _require_turn(turn_id, identity)
    frame = TerminalFrame(status="cancelled")
    async with workspace_tx() as connection:
        updated = await connection.execute(
            sa.update(tables.turn)
            .values(
                status="cancelled",
                terminal=frame.model_dump(mode="json"),
                updated_at=sa.func.now(),
            )
            .where(tables.turn.c.id == turn_id, tables.turn.c.status.in_(("queued", "running")))
        )
    if updated.rowcount == 1:
        hub: Hub = request.app.state.hub
        await hub.publish(turn_id, Terminal(frame=frame))
        client: DBOSClient = request.app.state.dbos
        await client.cancel_workflow_async(str(turn_id))
        return {"status": "cancelled"}
    stored = await _terminal_frame(turn_id)
    if stored is None:
        raise HTTPException(409, "turn could not be cancelled")
    return {"status": stored.status}


async def _conversation_for(identity: CliIdentity, queue_key: str) -> sa.Row:
    """Get-or-create outside the admission transaction: a concurrent creator's unique
    violation is swallowed and the surviving row re-read."""
    conversation_filter = (tables.conversation.c.surface == "cli") & (
        tables.conversation.c.queue_key == queue_key
    )
    lookup = sa.select(tables.conversation.c.id, tables.conversation.c.member_id).where(
        conversation_filter
    )
    async with workspace_tx() as connection:
        found = (await connection.execute(lookup)).one_or_none()
    if found is not None:
        return found
    try:
        async with workspace_tx() as connection:
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=uuid4(),
                    workspace_id=identity.workspace_id,
                    surface="cli",
                    queue_key=queue_key,
                    member_id=identity.member_id,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    except sa.exc.IntegrityError:
        log("conversation.create_lost_race", queue_key=queue_key)
    async with workspace_tx() as connection:
        return (await connection.execute(lookup)).one()


async def _require_turn(turn_id: UUID, identity: CliIdentity) -> None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.conversation.c.member_id)
                .select_from(tables.turn.join(tables.conversation))
                .where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(404, "no such turn")
    if row.member_id != identity.member_id:
        raise HTTPException(403, "turn belongs to another member")


async def _frame_lines(hub: Hub, turn_id: UUID) -> AsyncIterator[bytes]:
    frames: asyncio.Queue[LiveFrame] = asyncio.Queue()
    pump = asyncio.ensure_future(_pump(hub, turn_id, frames))
    poll = asyncio.ensure_future(_poll_terminal(turn_id, frames))
    try:
        stored = await _terminal_frame(turn_id)
        if stored is not None:
            yield _line(Terminal(frame=stored))
            return
        while True:
            frame = await frames.get()
            yield _line(frame)
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
        frame = await _terminal_frame(turn_id)
        if frame is not None:
            await frames.put(Terminal(frame=frame))
            return


async def _terminal_frame(turn_id: UUID) -> TerminalFrame | None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn_id)
            )
        ).one_or_none()
    if row is None or row.terminal is None:
        return None
    return TerminalFrame.model_validate(row.terminal)


def _line(frame: LiveFrame) -> bytes:
    return frame.model_dump_json().encode() + b"\n"
