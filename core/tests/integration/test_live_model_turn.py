"""A real streamed Anthropic turn through the DBOS queue — the live-model proof the StandIn
lifecycle tests can't give. The model client is the real `AnthropicClient` (not monkeypatched), so
`/v1/chat` admits a turn, the DBOS worker runs it against the live API, and the durable turn row,
the priced ledger row, and the streamed frames are asserted from what the real call produced. Gated
on `ANTHROPIC_API_KEY`; the carrier is the StandIn (this proves the model path, not the sandbox).

Runs only where the key is set (skips with a clear reason otherwise), and serially — it shares the
process-singleton DBOS executor with the rest of the suite."""

import asyncio
import hashlib
import json
import os
import secrets
from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.hub import InProcessHub
from ufo.loop import queue as loop_queue
from ufo.loop.subagents import SubagentRegistry
from ufo.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from ufo.schema import tables
from ufo.surfaces.cli import router

pytestmark = pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY for a live model turn"
)

STREAM_TIMEOUT_SECONDS = 120
LIVE_MODEL = "claude-opus-4-8"
LIVE_PROMPT = b"Reply with exactly the single word: pong. Do not use any tools."


@dataclass(frozen=True)
class _StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


@dataclass(frozen=True)
class _StandInCarrier:
    """A live model turn that answers in text touches no sandbox; create returns a handle and exec
    is never reached. The real DockerCarrier is proven end to end in test_docker_turn."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="live-model")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


@pytest.fixture
async def live_surface(
    db: None, dbos_launched: Config
) -> AsyncIterator[tuple[AsyncClient, FilesystemBlobStore]]:
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    runtime_dbos = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            workspace_fs=None,
            hub=hub,
            carrier=_StandInCarrier(),
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
            dbos=runtime_dbos,
            subagents=SubagentRegistry(()),
            subagent_grants={},
            manifests=(),
            credentials=None,
            index=None,
            embed=None,
            artifact_token_secret="",
        )
    )
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = DBOSClient(system_database_url=config.database.system_url)
    app.state.durable_surfaces = frozenset()
    app.state.shared_workspace = False
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://live") as client:
        yield client, blob
    app.state.dbos.destroy()
    runtime_dbos.destroy()
    loop_queue.reset_runtime()


async def _bootstrap() -> dict[str, str]:
    token = secrets.token_hex(16)
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
                email=f"{token[:8]}@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="You are a terse assistant.",
                model=LIVE_MODEL,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.surface_identity).values(
                workspace_id=workspace_id,
                member_id=member_id,
                surface="cli",
                external_id=hashlib.sha256(token.encode()).hexdigest(),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return {"authorization": f"Bearer {token}", "x-ufo-session": uuid4().hex}


async def _consume(
    client: AsyncClient, headers: dict[str, str], turn_id: str
) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream("GET", f"/v1/turns/{turn_id}/stream", headers=headers) as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if "frame" in payload:
                    return "".join(deltas), payload["frame"]
                if "text" in payload:
                    deltas.append(payload["text"])
    raise AssertionError("stream ended without a terminal frame")


async def test_live_anthropic_turn_streams_and_bills(
    live_surface: tuple[AsyncClient, FilesystemBlobStore],
) -> None:
    client, _ = live_surface
    headers = await _bootstrap()
    admitted = await client.post("/v1/chat", content=LIVE_PROMPT, headers=headers)
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]

    streamed, terminal = await _consume(client, headers, turn_id)

    assert terminal["status"] == "done", terminal
    assert terminal["model"] == LIVE_MODEL
    assert int(terminal["tokens"]) > 0
    assert streamed.strip() != ""
    async with workspace_tx() as connection:
        status = (
            await connection.execute(
                sa.select(tables.turn.c.status).where(tables.turn.c.id == UUID(turn_id))
            )
        ).scalar_one()
        billed = (
            await connection.execute(
                sa.select(
                    tables.ledger.c.amount,
                    tables.ledger.c.priced_micro_usd,
                    tables.ledger.c.model,
                ).where(tables.ledger.c.turn_id == UUID(turn_id))
            )
        ).one()
    assert status == "done"
    assert int(billed.amount) > 0
    assert int(billed.priced_micro_usd) > 0
    assert billed.model == LIVE_MODEL
