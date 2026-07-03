"""The CLI surface: bearer-token identity, turn admission, live stream, cancel."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

from dbos import DBOSClient, EnqueueOptions
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import text

from selfhost.db import workspace_tx
from selfhost.hub import Hub, LiveFrame, Terminal
from selfhost.schema.records import (
    DBOS_APP_VERSION,
    TURN_QUEUE_NAME,
    TURN_WORKFLOW_NAME,
    TerminalFrame,
    turn_id_for,
)

MAX_INBOUND_CHARS = 200_000
TERMINAL_POLL_SECONDS = 1.0
DEFAULT_AGENT_NAME = "assistant"

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
                text(
                    "select member_id, workspace_id from surface_identity"
                    " where surface = 'cli' and external_id = :external_id"
                ),
                {"external_id": digest},
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
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                text("select id from agent where workspace_id = :ws and name = :name"),
                {"ws": identity.workspace_id, "name": x_selfhost_agent},
            )
        ).one_or_none()
        if agent is None:
            raise HTTPException(404, f"no agent named {x_selfhost_agent!r}")
        await connection.execute(
            text(
                "insert into conversation"
                " (id, workspace_id, surface, queue_key, member_id, created_at, updated_at)"
                " values (:id, :ws, 'cli', :queue_key, :member, now(), now())"
                " on conflict (surface, queue_key) do nothing"
            ),
            {
                "id": uuid4(),
                "ws": identity.workspace_id,
                "queue_key": x_selfhost_session,
                "member": identity.member_id,
            },
        )
        conversation = (
            await connection.execute(
                text(
                    "select id, member_id from conversation"
                    " where surface = 'cli' and queue_key = :queue_key"
                ),
                {"queue_key": x_selfhost_session},
            )
        ).one()
        if conversation.member_id != identity.member_id:
            raise HTTPException(403, "conversation belongs to another member")
        await connection.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:conversation, 0))"),
            {"conversation": str(conversation.id)},
        )
        seq = (
            await connection.execute(
                text(
                    "select coalesce(max(seq), 0) + 1 as seq from turn"
                    " where conversation_id = :conversation"
                ),
                {"conversation": conversation.id},
            )
        ).one()[0]
        turn_id = turn_id_for(identity.workspace_id, conversation.id, seq)
        await connection.execute(
            text(
                "insert into turn (id, workspace_id, conversation_id, agent_id, seq, status,"
                " inbound, terminal, created_at, updated_at)"
                " values (:id, :ws, :conversation, :agent, :seq, 'queued', :inbound, null,"
                " now(), now()) on conflict (id) do nothing"
            ),
            {
                "id": turn_id,
                "ws": identity.workspace_id,
                "conversation": conversation.id,
                "agent": agent.id,
                "seq": seq,
                "inbound": inbound,
            },
        )
    options: EnqueueOptions = {
        "queue_name": TURN_QUEUE_NAME,
        "workflow_name": TURN_WORKFLOW_NAME,
        "workflow_id": str(turn_id),
        "queue_partition_key": str(conversation.id),
        "app_version": DBOS_APP_VERSION,
    }
    client: DBOSClient = request.app.state.dbos
    await client.enqueue_async(options, str(turn_id))
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
            text(
                "update turn set status = 'cancelled', terminal = cast(:terminal as jsonb),"
                " updated_at = now() where id = :id and status in ('queued', 'running')"
            ),
            {"terminal": frame.model_dump_json(), "id": turn_id},
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


async def _require_turn(turn_id: UUID, identity: CliIdentity) -> None:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                text(
                    "select c.member_id from turn t"
                    " join conversation c on c.id = t.conversation_id where t.id = :id"
                ),
                {"id": turn_id},
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
                text("select terminal from turn where id = :id"), {"id": turn_id}
            )
        ).one_or_none()
    if row is None or row.terminal is None:
        return None
    return TerminalFrame.model_validate(row.terminal)


def _line(frame: LiveFrame) -> bytes:
    return frame.model_dump_json().encode() + b"\n"
