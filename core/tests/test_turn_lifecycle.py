import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from conftest import reset_postgres_database
from dbos import DBOS, DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.engine import make_url

from selfhost.blob import FilesystemBlobStore
from selfhost.config import BlobConfig, Config, DatabaseConfig
from selfhost.db import workspace_tx
from selfhost.hub import InProcessHub
from selfhost.loop import queue as loop_queue
from selfhost.loop.transcript import Transcript
from selfhost.models import ModelEvent, ModelRequest, TextDelta
from selfhost.schema import tables
from selfhost.schema.records import DBOS_APP_NAME, DBOS_APP_VERSION, Usage
from selfhost.surfaces.cli import router

STREAM_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class StandInModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        message = request.messages[-1].content
        if "explode" in message:
            raise RuntimeError("boom")
        if "slow" in message:
            await asyncio.sleep(30)
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


@pytest.fixture(scope="session")
def dbos_runtime(
    database_url: str, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore]]:
    blob_root = tmp_path_factory.mktemp("blobs")
    config = Config(
        database=DatabaseConfig(url=database_url),
        blob=BlobConfig(backend="filesystem", root=blob_root),
    )
    system_url = config.database.system_url
    if system_url.startswith("postgresql"):
        asyncio.run(reset_postgres_database(make_url(system_url).database))
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=blob_root)
    loop_queue.init_runtime(loop_queue.Runtime(config=config, blob=blob, hub=hub))
    DBOS(
        config={
            "name": DBOS_APP_NAME,
            "application_version": DBOS_APP_VERSION,
            "system_database_url": system_url,
            "run_admin_server": False,
        }
    )
    DBOS.launch()
    yield config, hub, blob
    DBOS.destroy()
    loop_queue._runtime = None


@pytest.fixture
async def surface(
    db: None,
    dbos_runtime: tuple[Config, InProcessHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    config, hub, _ = dbos_runtime
    monkeypatch.setattr(loop_queue, "_model_client", lambda model, config: StandInModel())
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = DBOSClient(system_database_url=config.database.system_url)
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        yield client
    app.state.dbos.destroy()


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
                prompt="be brief",
                model="claude-opus-4-8",
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
    return {"authorization": f"Bearer {token}", "x-selfhost-session": uuid4().hex}


async def _consume(
    client: AsyncClient, headers: dict[str, str], turn_id: str
) -> tuple[str, dict[str, object]]:
    deltas: list[str] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET", f"/v1/turns/{turn_id}/stream", headers=headers
        ) as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if "frame" in payload:
                    return "".join(deltas), payload["frame"]
                deltas.append(payload["text"])
    raise AssertionError("stream ended without a terminal frame")


async def _turn_row(turn_id: str) -> tuple[str, UUID]:
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.conversation_id).where(
                    tables.turn.c.id == UUID(turn_id)
                )
            )
        ).one()
    return row.status, row.conversation_id


async def test_turn_round_trip_bills_and_persists(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    admitted = await surface.post("/v1/chat", content=b"ping", headers=headers)
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    assert terminal["tokens"] == 10
    assert terminal["cost_micro_usd"] == 110
    assert terminal["model"] == "claude-opus-4-8"
    status, conversation_id = await _turn_row(turn_id)
    assert status == "done"
    async with workspace_tx() as connection:
        billed = (
            await connection.execute(
                sa.select(tables.ledger.c.amount, tables.ledger.c.priced_micro_usd).where(
                    tables.ledger.c.turn_id == UUID(turn_id)
                )
            )
        ).one()
    assert (int(billed.amount), int(billed.priced_micro_usd)) == (10, 110)
    _, _, blob = _runtime_parts(surface)
    stored = await Transcript(blob=blob, conversation_id=conversation_id).read()
    assert stored is not None
    assert stored.seq == 1
    assert [m.content for m in stored.messages] == ["ping", "echo:1"]


async def test_second_turn_continues_the_conversation(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"one", headers=headers)).json()["turn_id"]
    await _consume(surface, headers, first)
    second = (await surface.post("/v1/chat", content=b"two", headers=headers)).json()["turn_id"]
    streamed, terminal = await _consume(surface, headers, second)
    assert terminal["status"] == "done"
    assert streamed == "echo:3"
    _, conversation_id = await _turn_row(second)
    _, _, blob = _runtime_parts(surface)
    stored = await Transcript(blob=blob, conversation_id=conversation_id).read()
    assert stored is not None
    assert stored.seq == 2
    assert len(stored.messages) == 4


async def test_back_to_back_turns_serialize_per_conversation(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"one", headers=headers)).json()["turn_id"]
    second = (await surface.post("/v1/chat", content=b"two", headers=headers)).json()["turn_id"]
    _, first_terminal = await _consume(surface, headers, first)
    streamed, second_terminal = await _consume(surface, headers, second)
    assert first_terminal["status"] == "done"
    assert second_terminal["status"] == "done"
    assert streamed == "echo:3"


async def test_failure_commits_terminal_and_bills_nothing(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"explode", headers=headers)).json()[
        "turn_id"
    ]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert streamed == ""
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    status, conversation_id = await _turn_row(turn_id)
    assert status == "failed"
    async with workspace_tx() as connection:
        billed = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == UUID(turn_id))
            )
        ).scalar_one()
    assert billed == 0
    _, _, blob = _runtime_parts(surface)
    assert await Transcript(blob=blob, conversation_id=conversation_id).read() is None


async def test_cancel_commits_terminal_while_model_runs(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"slow", headers=headers)).json()[
        "turn_id"
    ]
    await asyncio.sleep(1.0)
    cancelled = await surface.post(f"/v1/turns/{turn_id}/cancel", headers=headers)
    assert cancelled.json() == {"status": "cancelled"}
    _, terminal = await _consume(surface, headers, turn_id)
    assert terminal["status"] == "cancelled"
    status, _ = await _turn_row(turn_id)
    assert status == "cancelled"


async def test_foreign_token_cannot_reach_the_turn(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    other = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"ping", headers=headers)).json()[
        "turn_id"
    ]
    await _consume(surface, headers, turn_id)
    denied = await surface.post(f"/v1/turns/{turn_id}/cancel", headers=other)
    assert denied.status_code == 403


def _runtime_parts(surface: AsyncClient) -> tuple[Config, InProcessHub, FilesystemBlobStore]:
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.blob, FilesystemBlobStore)
    return runtime.config, runtime.hub, runtime.blob
