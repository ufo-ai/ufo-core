"""The shared fleet resolves each request's workspace from its signed token, not a stored identity
row: one app serves two workspaces, and each request's turn is admitted under exactly the workspace
its token claims. The token *is* the identity — there is no surface_identity lookup to scope, which
is the chicken-and-egg the signed claim breaks. RLS enforcement itself is a Postgres concern proven
against the live database; here on sqlite we prove the token→workspace resolution and admission.

We also prove the binding *lifecycle* the RLS scope rests on: `WorkspaceScopeBoundary` holds the
request's workspace bound through the whole streamed response (the hub tail reads the durable turn
after the handler returns) and releases it once the response is sent — a handler that raises after
binding included — so no request leaves a stale binding for the next occupant of its task to
inherit, and a request whose task starts already carrying one begins clean. httpx's ASGITransport
runs each request in this test's own task/context, so — unlike a cross-request leak, which
set-before-read masks — the post-response binding is directly observable here.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from starlette.requests import Request
from starlette.responses import Response, StreamingResponse

from ufo.blob import blob_store_for
from ufo.config import BlobConfig
from ufo.db import current_workspace, workspace_tx
from ufo.ext.manifest import Manifest
from ufo.ext.surface import SurfaceContext, SurfaceRoute, SurfaceSpec
from ufo.hub import InProcessHub
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME, TerminalFrame
from ufo.serve import WorkspaceScopeBoundary, _mount_shared_surfaces
from ufo.session_token import SESSION_TOKEN_TTL_SECONDS, mint_session_token
from ufo.surfaces.cli import router

SECRET = "shared-fleet-secret"


@dataclass
class StubDbos:
    """Records what the surface placed on the turn queue — the (workspace_id, turn_id) the worker
    will self-scope from — so a test reads back that each turn carried its token's workspace."""

    enqueued: list[tuple[str, str]] = field(default_factory=list)

    async def enqueue_async(self, options: object, workspace_id: str, turn_id: str) -> None:
        self.enqueued.append((workspace_id, turn_id))


async def _seed_workspace(email: str) -> tuple[UUID, UUID]:
    workspace_id, member_id, agent_id = uuid4(), uuid4(), uuid4()
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
                email=email,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=DEFAULT_AGENT_NAME,
                prompt="be brief",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, member_id


def _token(secret: str, member_id: UUID, workspace_id: UUID) -> str:
    expires_at = int((datetime.now(UTC) + timedelta(seconds=SESSION_TOKEN_TTL_SECONDS)).timestamp())
    return mint_session_token(secret, member_id, workspace_id, expires_at)


def _app(dbos: StubDbos) -> FastAPI:
    app = FastAPI()
    app.add_middleware(WorkspaceScopeBoundary)
    app.state.hub = InProcessHub()
    app.state.dbos = dbos
    app.state.durable_surfaces = frozenset()
    app.state.shared_workspace = True
    app.state.session_token_secret = SECRET
    app.include_router(router)
    return app


async def _turn_workspace(turn_id: UUID) -> UUID:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.turn.c.workspace_id).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()


async def test_two_workspaces_share_one_app_each_scoped_by_its_token(db: None) -> None:
    ws_a, member_a = await _seed_workspace("a@x.test")
    ws_b, member_b = await _seed_workspace("b@x.test")
    dbos = StubDbos()
    app = _app(dbos)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        reply_a = await client.post(
            "/v1/chat",
            headers={
                "authorization": f"Bearer {_token(SECRET, member_a, ws_a)}",
                "x-ufo-session": "sess-a",
            },
            content=b"hi from a",
        )
        reply_b = await client.post(
            "/v1/chat",
            headers={
                "authorization": f"Bearer {_token(SECRET, member_b, ws_b)}",
                "x-ufo-session": "sess-b",
            },
            content=b"hi from b",
        )
    assert reply_a.status_code == 200, reply_a.text
    assert reply_b.status_code == 200, reply_b.text
    turn_a = UUID(reply_a.json()["turn_id"])
    turn_b = UUID(reply_b.json()["turn_id"])
    assert await _turn_workspace(turn_a) == ws_a
    assert await _turn_workspace(turn_b) == ws_b
    assert (str(ws_a), str(turn_a)) in dbos.enqueued
    assert (str(ws_b), str(turn_b)) in dbos.enqueued


async def test_a_token_signed_with_another_secret_admits_nothing(db: None) -> None:
    ws, member = await _seed_workspace("c@x.test")
    dbos = StubDbos()
    app = _app(dbos)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
        reply = await client.post(
            "/v1/chat",
            headers={
                "authorization": f"Bearer {_token('forged-secret', member, ws)}",
                "x-ufo-session": "sess-c",
            },
            content=b"let me in",
        )
    assert reply.status_code == 401
    assert dbos.enqueued == []


PROBE_SURFACE = "probe"


def _probe_workspace(request: Request) -> UUID | None:
    """The stub `SurfaceSpec.identify` this test drives the shared mount through: it reads the
    request's asserted workspace off a header. The real ufo surface verifies a signed bearer — the
    tests above cover that signature check; this one exercises how core *binds* identify's result
    and releases it, so identify itself is the cheapest resolver that returns a workspace."""
    raw = request.headers.get("x-workspace")
    return UUID(raw) if raw else None


async def _probe_handler(ctx: SurfaceContext, request: Request) -> Response:
    """A live surface's route: it streams the ambient workspace as the body is read. The body runs
    after the handler returns (Starlette streams it), so it observes the binding the mount must keep
    live through the whole streamed response — exactly where the real hub tail reads the durable
    turn under RLS. A binding cut when the handler returned (a `with ws(...)` regression) would
    surface here as an unbound `stream-ws`."""

    async def frames() -> AsyncIterator[bytes]:
        yield f"ctx-ws={ctx.workspace_id}\n".encode()
        yield f"stream-ws={current_workspace.get()}\n".encode()

    return StreamingResponse(frames(), media_type="text/plain")


async def _probe_boom(ctx: SurfaceContext, request: Request) -> Response:
    """A route that raises after the mount has bound the request's workspace: the error path where
    no response object ever exists to carry a release, so only the boundary's `finally` covers
    it."""
    raise HTTPException(404, "no such probe")


def _probe_app(tmp_path: Path) -> FastAPI:
    surface = SurfaceSpec(
        name=PROBE_SURFACE,
        routes=(
            SurfaceRoute(method="POST", path="ping", handler=_probe_handler),
            SurfaceRoute(method="POST", path="boom", handler=_probe_boom),
        ),
        identify=_probe_workspace,
    )
    app = FastAPI()
    _mount_shared_surfaces(
        app,
        (Manifest(name="probe_ext", version="0", surfaces=(surface,)),),
        None,
        blob_store_for(BlobConfig(backend="filesystem", root=tmp_path)),
        InProcessHub(),
        StubDbos(),
        "",
        None,
    )
    return app


async def test_shared_surface_holds_the_binding_through_the_stream_then_releases_it(
    tmp_path: Path,
) -> None:
    """The shared endpoint keeps the request's workspace bound while the live response streams — the
    hub tail reads the durable turn under RLS after the handler returns — then releases it once the
    body is sent. Removing the reset would leave `current_workspace` bound to this request's
    workspace after it returns (visible here, since ASGITransport shares this task's context);
    resetting when the handler returned would leave `stream-ws` unbound."""
    baseline = current_workspace.set(None)
    ws = uuid4()
    try:
        app = _probe_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post("/surface/probe/ping", headers={"x-workspace": str(ws)})
        assert reply.status_code == 200, reply.text
        assert f"ctx-ws={ws}" in reply.text
        assert f"stream-ws={ws}" in reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_shared_surface_scopes_over_a_stale_inherited_binding(tmp_path: Path) -> None:
    """A request whose task begins already carrying another workspace's binding — the cpython
    per-task context-copy leak the boundary defends against — still scopes to its own: the boundary
    clears the stale value on entry and the endpoint binds its own before anything reads, so the
    stale value never reaches the stream, and no binding survives the request."""
    stale, own = uuid4(), uuid4()
    stale_token = current_workspace.set(stale)
    try:
        app = _probe_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post("/surface/probe/ping", headers={"x-workspace": str(own)})
        assert reply.status_code == 200, reply.text
        assert f"stream-ws={own}" in reply.text
        assert str(stale) not in reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(stale_token)


async def test_shared_surface_releases_the_binding_when_the_handler_raises(tmp_path: Path) -> None:
    """A handler that raises after the mount bound the request's workspace still releases it: the
    raise means no response object ever exists to carry a release (the 404 is rendered by the app's
    exception handler), so only the boundary's `finally`, outside the route, reaches this path."""
    baseline = current_workspace.set(None)
    ws = uuid4()
    try:
        app = _probe_app(tmp_path)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post("/surface/probe/boom", headers={"x-workspace": str(ws)})
        assert reply.status_code == 404, reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def _seed_terminal_turn(workspace_id: UUID, member_id: UUID) -> UUID:
    """A committed turn on its own conversation, so `/v1/turns/{id}/stream` tails one durable
    terminal frame and ends at once — exercising the shared CLI's StreamingResponse path without a
    live worker."""
    conversation_id, turn_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        agent_id = (
            await connection.execute(
                sa.select(tables.agent.c.id).where(tables.agent.c.workspace_id == workspace_id)
            )
        ).scalar_one()
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=f"q-{turn_id}",
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
                status="done",
                inbound="hi",
                terminal=TerminalFrame(status="done").model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return turn_id


async def test_shared_cli_chat_releases_the_binding_and_scopes_over_a_stale_one(db: None) -> None:
    """The shared CLI's `/v1/chat` binds the token's workspace in `_authenticate_shared` and admits
    its turn there even when the request's task starts carrying a stale binding, and
    `WorkspaceScopeBoundary` releases the binding once the response is sent. Removing the boundary
    would leave the request's workspace bound in this task's context after it returns (observable
    here, since ASGITransport shares the context)."""
    ws, member = await _seed_workspace("cli-a@x.test")
    stale = uuid4()
    stale_token = current_workspace.set(stale)
    dbos = StubDbos()
    app = _app(dbos)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post(
                "/v1/chat",
                headers={
                    "authorization": f"Bearer {_token(SECRET, member, ws)}",
                    "x-ufo-session": "sess-cli-a",
                },
                content=b"hi from cli",
            )
        assert reply.status_code == 200, reply.text
        turn_id = UUID(reply.json()["turn_id"])
        assert await _turn_workspace(turn_id) == ws
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(stale_token)


async def test_shared_cli_stream_releases_the_binding_after_the_streamed_response(db: None) -> None:
    """The shared CLI's turn stream is a StreamingResponse whose hub tail reads the durable turn
    under RLS after the handler returns, so the binding must persist through it — and be released
    once the whole body has streamed. After the stream completes, no binding survives in the
    task."""
    ws, member = await _seed_workspace("cli-b@x.test")
    turn_id = await _seed_terminal_turn(ws, member)
    baseline = current_workspace.set(None)
    dbos = StubDbos()
    app = _app(dbos)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.get(
                f"/v1/turns/{turn_id}/stream",
                headers={"authorization": f"Bearer {_token(SECRET, member, ws)}"},
            )
        assert reply.status_code == 200, reply.text
        assert reply.text.strip(), "the stream delivered no frame"
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)


async def test_shared_cli_releases_the_binding_when_the_route_raises_after_binding() -> None:
    """`/v1/chat` raises 400 (missing x-ufo-session) after `_authenticate_shared` has bound the
    token's workspace: no response object ever exists to carry a release, so only the boundary's
    `finally`, outside the route, keeps the binding from surviving into the task's next request."""
    ws, member = uuid4(), uuid4()
    baseline = current_workspace.set(None)
    app = _app(StubDbos())
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fleet") as client:
            reply = await client.post(
                "/v1/chat",
                headers={"authorization": f"Bearer {_token(SECRET, member, ws)}"},
                content=b"hi",
            )
        assert reply.status_code == 400, reply.text
        assert current_workspace.get() is None
    finally:
        current_workspace.reset(baseline)
