"""The CLI surface: bearer-token identity, turn admission, live stream, cancel, OAuth callback."""

import hashlib
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from fastapi.routing import APIRoute

from ufo.db import current_workspace, workspace_tx
from ufo.governance import Governance
from ufo.grants import (
    ConnectStateInvalid,
    ConnectUnavailable,
    UnknownProvider,
    installed_connect_flow,
)
from ufo.hub import Hub, Terminal
from ufo.o11y import log
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, TerminalFrame
from ufo.session_token import SessionTokenError, verify_session_token
from ufo.surfaces.admission import Admission
from ufo.surfaces.hub_tail import tail_frames, terminal_frame
from ufo.surfaces.scope import ScopedResponse

MAX_INBOUND_CHARS = 200_000
CORE_PROPOSER = "core"
CONNECT_CALLBACK_PATH = "/v1/connect/callback"


class _SharedScopeRoute(APIRoute):
    """The shared fleet binds each request's workspace as the ambient `current_workspace` inside the
    handler (`_authenticate_shared`) and must release it once the response — a live turn tails the
    hub, reading the durable turn under RLS after the handler returns — has fully streamed. This
    wraps every route's response in a `ScopedResponse` on the shared fleet, releasing the binding in
    the request's own task at the response's true end, never left for the next request reusing the
    task's context to inherit. A per-tenant deploy pins its one workspace by role default and binds
    nothing here, so its responses return unwrapped."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        inner = super().get_route_handler()

        async def scoped(request: Request) -> Response:
            response = await inner(request)
            if request.app.state.shared_workspace:
                return ScopedResponse(response)
            return response

        return scoped


router = APIRouter(prefix="/v1", route_class=_SharedScopeRoute)


@dataclass(frozen=True)
class CliIdentity:
    member_id: UUID
    workspace_id: UUID


async def _authenticate(request: Request, authorization: str) -> CliIdentity:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "missing bearer token")
    if request.app.state.shared_workspace:
        return _authenticate_shared(request, token)
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


def _authenticate_shared(request: Request, token: str) -> CliIdentity:
    """On the shared fleet the token *is* the identity: verify its signature, trust its workspace
    claim, and bind it as the ambient workspace so every RLS-scoped read this request makes scopes
    to it. There is no surface_identity lookup — that read would itself need the scope the token
    supplies, the chicken-and-egg the signed claim exists to break. The binding outlives this call
    (a live turn's StreamingResponse tails the hub, reading the durable turn under RLS after the
    handler returns) and is released once the whole response is sent by `_SharedScopeRoute`, so no
    request leaves it bound for the next request reusing the task's context."""
    try:
        claims = verify_session_token(
            token, request.app.state.session_token_secret, datetime.now(UTC)
        )
    except SessionTokenError as error:
        raise HTTPException(401, str(error)) from error
    current_workspace.set(claims.workspace_id)
    return CliIdentity(member_id=claims.member_id, workspace_id=claims.workspace_id)


@router.post("/chat")
async def chat(
    request: Request,
    authorization: str = Header(default=""),
    x_ufo_session: str = Header(default=""),
    x_ufo_agent: str = Header(default=DEFAULT_AGENT_NAME),
) -> dict[str, str]:
    identity = await _authenticate(request, authorization)
    if not x_ufo_session:
        raise HTTPException(400, "missing x-ufo-session header")
    inbound = (await request.body()).decode()
    if not inbound.strip():
        raise HTTPException(400, "empty message")
    if len(inbound) > MAX_INBOUND_CHARS:
        raise HTTPException(413, f"message exceeds {MAX_INBOUND_CHARS} characters")
    conversation = await _conversation_for(identity, x_ufo_session)
    if conversation.member_id != identity.member_id:
        raise HTTPException(403, "conversation belongs to another member")
    async with workspace_tx() as connection:
        agent = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(
                    tables.agent.c.workspace_id == identity.workspace_id,
                    tables.agent.c.name == x_ufo_agent,
                )
            )
        ).one_or_none()
    if agent is None:
        raise HTTPException(404, f"no agent named {x_ufo_agent!r}")
    turn_id = await Admission(
        dbos=request.app.state.dbos, durable_surfaces=request.app.state.durable_surfaces
    ).admit(identity.workspace_id, conversation.id, agent.id, inbound)
    return {"turn_id": str(turn_id)}


@router.get("/turns/{turn_id}/stream")
async def stream_turn(
    turn_id: UUID, request: Request, authorization: str = Header(default="")
) -> StreamingResponse:
    identity = await _authenticate(request, authorization)
    await _require_turn(turn_id, identity)
    hub: Hub = request.app.state.hub
    return StreamingResponse(_frame_lines(hub, turn_id), media_type="application/x-ndjson")


@router.post("/turns/{turn_id}/cancel")
async def cancel_turn(
    turn_id: UUID, request: Request, authorization: str = Header(default="")
) -> dict[str, str]:
    identity = await _authenticate(request, authorization)
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
    stored = await terminal_frame(turn_id)
    if stored is None:
        raise HTTPException(409, "turn could not be cancelled")
    return {"status": stored.status}


@router.post("/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: UUID, request: Request, authorization: str = Header(default="")
) -> dict[str, str]:
    identity = await _authenticate(request, authorization)
    governance = Governance(workspace_id=identity.workspace_id, extension=CORE_PROPOSER)
    await governance.approve_proposal(proposal_id, identity.member_id)
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.proposal.c.status, tables.proposal.c.approved_by).where(
                    tables.proposal.c.id == proposal_id,
                    tables.proposal.c.workspace_id == identity.workspace_id,
                )
            )
        ).one_or_none()
    if row is None:
        raise HTTPException(404, "no such proposal")
    return {"status": row.status, "approved_by": str(row.approved_by or "")}


@router.get("/connect/callback")
async def connect_callback(state: str = "", code: str = "") -> PlainTextResponse:
    """Complete the OAuth handoff the provider redirects to: verify the sealed state, exchange the
    code for the account and token, and land the grant. The grant a turn's `connect_account` began
    lands here. State-verified, not bearer-authenticated — the browser carries no token, only the
    state the connect tool sealed with the speaking member, agent, and conversation."""
    try:
        flow = installed_connect_flow()
    except ConnectUnavailable as error:
        raise HTTPException(503, str(error)) from error
    if not state or not code:
        raise HTTPException(400, "missing state or code")
    try:
        recorded = await flow.complete(state=state, code=code)
    except ConnectStateInvalid as error:
        raise HTTPException(400, str(error)) from error
    except UnknownProvider:
        raise HTTPException(404, "connector provider is not installed") from None
    return PlainTextResponse(
        f"connected {recorded.provider} account {recorded.account_id}; you can close this window"
    )


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
    async for _cursor, frame in tail_frames(hub, turn_id):
        yield frame.model_dump_json().encode() + b"\n"
