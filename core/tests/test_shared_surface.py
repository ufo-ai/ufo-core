"""The shared fleet resolves each request's workspace from its signed token, not a stored identity
row: one app serves two workspaces, and each request's turn is admitted under exactly the workspace
its token claims. The token *is* the identity — there is no surface_identity lookup to scope, which
is the chicken-and-egg the signed claim breaks. RLS enforcement itself is a Postgres concern proven
against the live database; here on sqlite we prove the token→workspace resolution and admission.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import sqlalchemy as sa
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.db import workspace_tx
from ufo.hub import InProcessHub
from ufo.schema import tables
from ufo.schema.records import DEFAULT_AGENT_NAME
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
