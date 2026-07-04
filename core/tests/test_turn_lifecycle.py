import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel

from selfhost.blob import FilesystemBlobStore
from selfhost.config import Config
from selfhost.db import workspace_tx
from selfhost.hub import InProcessHub
from selfhost.jobs import SpendResume
from selfhost.loop import queue as loop_queue
from selfhost.loop.engine import EMPTY_RESPONSE_NUDGE
from selfhost.loop.subagents import SubagentProfile, SubagentRegistry
from selfhost.loop.transcript import Transcript
from selfhost.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from selfhost.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import TerminalFrame, Usage
from selfhost.surfaces.cli import router
from selfhost.transcript import Conversation

STREAM_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class StubMemory:
    """Stand-in memory service for the lifecycle turns: recall yields nothing, so the turn loop runs
    end to end without asserting memory behavior (recall/commit are proven in the memory tests)."""

    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


class RoundTripInput(BaseModel):
    value: int


class RoundTripOutput(BaseModel):
    echoed: int


ROUNDTRIP_PROFILE = SubagentProfile(
    name="roundtrip",
    prompt="ROUNDTRIP: echo the value back.",
    tool_names=(),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
)


@dataclass(frozen=True)
class StandInModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if "ROUNDTRIP" in request.system:
            payload = json.loads(request.messages[-1].content)
            yield TextDelta(text=json.dumps({"echoed": payload["value"]}))
            yield Usage(input_tokens=5, output_tokens=5)
            return
        contents = [m.content for m in request.messages]
        nudged = contents[-1] == EMPTY_RESPONSE_NUDGE
        inbound = contents[-2] if nudged else contents[-1]
        if isinstance(inbound, str) and "spawn-subagent" in inbound:
            yield ToolCallStart(id="s1", name="spawn_subagent")
            yield ToolCallDelta(
                id="s1", partial_json='{"profile": "roundtrip", "payload": {"value": 21}}'
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if "explode-after-usage" in inbound:
            yield TextDelta(text="partial")
            yield Usage(input_tokens=7, output_tokens=3)
            raise RuntimeError("late boom")
        if "explode" in inbound:
            raise RuntimeError("boom")
        if "slow" in inbound:
            await asyncio.sleep(30)
        if "mute" in inbound or ("shy" in inbound and not nudged):
            yield Usage(input_tokens=5)
            return
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


@dataclass(frozen=True)
class StandInCarrier:
    """Stands in for the Docker carrier through the full queue path: create-or-attach returns a
    handle, and exec is never reached because StandInModel makes no tool calls."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, InProcessHub, FilesystemBlobStore]]:
    config = dbos_launched
    hub = InProcessHub()
    blob = FilesystemBlobStore(root=config.blob.root)
    proxy = ProxyEndpoint(port=0, ca_cert="test-ca")
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            hub=hub,
            carrier=StandInCarrier(),
            proxy=proxy,
            dbos=dbos_client,
            subagents=SubagentRegistry((ROUNDTRIP_PROFILE,)),
            manifests=(),
            credentials=None,
            memory=StubMemory(),
            artifact_token_secret="",
        )
    )
    yield config, hub, blob
    dbos_client.destroy()
    loop_queue.reset_runtime()


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
                if "text" not in payload:
                    continue
                deltas.append(payload["text"])
    raise AssertionError("stream ended without a terminal frame")


async def _read_transcript(
    blob: FilesystemBlobStore, conversation_id: UUID, seq: int
) -> Conversation:
    transcript = Transcript(blob=blob, conversation_id=conversation_id)
    async with asyncio.timeout(5):
        while True:
            stored = await transcript.read()
            if stored is not None and stored.seq >= seq:
                return stored
            await asyncio.sleep(0.05)


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


async def test_workspace_mount_source_is_absolute_for_a_relative_blob_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative blob root — the default config's `./blobs` — must still yield an absolute bind
    mount source: Docker reads a relative `-v` source as a named volume, not a host directory, so a
    turn would otherwise fail at container create under the shipped default config."""
    monkeypatch.chdir(tmp_path)
    blob = FilesystemBlobStore(root=Path("blobs"))
    mount = await loop_queue._workspace_mount(blob, uuid4())
    assert Path(mount.host_path).is_absolute()
    assert await asyncio.to_thread(Path(mount.host_path).is_dir)


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
    stored = await _read_transcript(blob, conversation_id, 1)
    assert stored.seq == 1
    assert [m.content for m in stored.messages] == ["ping", "echo:1"]


async def test_cost_ticks_stream_as_a_turn_accrues_spend(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"ping", headers=headers)).json()[
        "turn_id"
    ]
    costs: list[dict[str, object]] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with surface.stream(
            "GET", f"/v1/turns/{turn_id}/stream", headers=headers
        ) as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if "cost_micro_usd" in payload:
                    costs.append(payload)
                if "frame" in payload:
                    break
    assert costs
    assert costs[-1] == {"cost_micro_usd": 110, "tokens": 10}


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
    stored = await _read_transcript(blob, conversation_id, 2)
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


async def test_failure_commits_terminal_bills_nothing_preserves_inbound(
    surface: AsyncClient,
) -> None:
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
    stored = await _read_transcript(blob, conversation_id, 1)
    assert [m.content for m in stored.messages] == ["explode"]


async def test_next_turn_sees_a_failed_turns_inbound(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"explode", headers=headers)).json()[
        "turn_id"
    ]
    _, first_terminal = await _consume(surface, headers, first)
    assert first_terminal["status"] == "failed"
    second = (await surface.post("/v1/chat", content=b"ok", headers=headers)).json()["turn_id"]
    _, second_terminal = await _consume(surface, headers, second)
    assert second_terminal["status"] == "done"
    _, conversation_id = await _turn_row(second)
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 2)
    assert [m.content for m in stored.messages][:2] == ["explode", "ok"]


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


async def _consume_park(client: AsyncClient, headers: dict[str, str], turn_id: str) -> str:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream(
            "GET", f"/v1/turns/{turn_id}/stream", headers=headers
        ) as stream:
            assert stream.status_code == 200
            async for line in stream.aiter_lines():
                if not line:
                    continue
                payload = json.loads(line)
                if "message" in payload:
                    return payload["message"]
    raise AssertionError("stream ended without a park frame")


async def _await_status(turn_id: str, target: str) -> None:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            status, _ = await _turn_row(turn_id)
            if status == target:
                return
            await asyncio.sleep(0.05)


async def test_member_cap_parks_a_turn_in_surface_then_resumes_when_raised(
    surface: AsyncClient,
) -> None:
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"ping", headers=headers)).json()["turn_id"]
    _, first_terminal = await _consume(surface, headers, first)
    assert first_terminal["status"] == "done"
    async with workspace_tx() as connection:
        workspace_id = (
            await connection.execute(sa.select(tables.workspace.c.id))
        ).scalar_one()
        member_id = (await connection.execute(sa.select(tables.member.c.id))).scalar_one()
        cap_id = uuid4()
        await connection.execute(
            sa.insert(tables.spend_cap).values(
                id=cap_id,
                workspace_id=workspace_id,
                scope="member",
                subject_id=member_id,
                window_seconds=3600,
                limit_micro_usd=1,
                on_breach="park",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    second = (await surface.post("/v1/chat", content=b"again", headers=headers)).json()[
        "turn_id"
    ]
    park_message = await _consume_park(surface, headers, second)
    assert "parked" in park_message
    status, _ = await _turn_row(second)
    assert status == "parked"
    async with workspace_tx() as connection:
        billed_while_parked = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == UUID(second))
            )
        ).scalar_one()
    assert billed_while_parked == 0
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.spend_cap)
            .values(limit_micro_usd=10_000_000, updated_at=sa.func.now())
            .where(tables.spend_cap.c.id == cap_id)
        )
    assert loop_queue._runtime is not None
    await SpendResume(client=loop_queue._runtime.dbos).run()
    await _await_status(second, "done")
    async with workspace_tx() as connection:
        billed = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == UUID(second))
            )
        ).scalar_one()
    assert billed == 1


async def test_failure_after_usage_bills_partial_usage(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (
        await surface.post("/v1/chat", content=b"explode-after-usage", headers=headers)
    ).json()["turn_id"]
    _, terminal = await _consume(surface, headers, turn_id)
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["tokens"] == 10
    async with workspace_tx() as connection:
        billed = (
            await connection.execute(
                sa.select(tables.ledger.c.amount).where(
                    tables.ledger.c.turn_id == UUID(turn_id)
                )
            )
        ).scalar_one()
    assert int(billed) == 10


async def test_empty_response_nudge_recovers_and_bills_both_calls(
    surface: AsyncClient,
) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"shy", headers=headers)).json()[
        "turn_id"
    ]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert terminal["status"] == "done"
    assert streamed == "echo:2"
    assert terminal["tokens"] == 15
    _, conversation_id = await _turn_row(turn_id)
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 1)
    assert [m.content for m in stored.messages] == ["shy", EMPTY_RESPONSE_NUDGE, "echo:2"]


async def test_empty_response_twice_fails_loud(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"mute", headers=headers)).json()[
        "turn_id"
    ]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert streamed == ""
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["tokens"] == 10


async def test_concurrent_admissions_allocate_unique_seqs(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    responses = await asyncio.gather(
        *(
            surface.post("/v1/chat", content=f"burst {n}".encode(), headers=headers)
            for n in range(10)
        )
    )
    turn_ids = [response.json()["turn_id"] for response in responses]
    assert len(set(turn_ids)) == 10
    async with workspace_tx() as connection:
        seqs = (
            await connection.execute(
                sa.select(tables.turn.c.seq).where(
                    tables.turn.c.id.in_([UUID(t) for t in turn_ids])
                )
            )
        ).scalars()
        assert sorted(seqs) == list(range(1, 11))
    await asyncio.gather(*(_consume(surface, headers, turn_id) for turn_id in turn_ids))


async def test_typed_subagent_round_trips_schema(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    parent = (
        await surface.post("/v1/chat", content=b"spawn-subagent", headers=headers)
    ).json()["turn_id"]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(
                    tables.turn.c.subagent_profile,
                    tables.turn.c.status,
                    tables.turn.c.terminal,
                ).where(tables.turn.c.parent_turn_id == UUID(parent))
            )
        ).one()
    assert child.subagent_profile == "roundtrip"
    assert child.status == "done"
    child_output = TerminalFrame.model_validate(child.terminal).text
    assert RoundTripOutput.model_validate_json(child_output).echoed == 21
    _, conversation_id = await _turn_row(parent)
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 1)
    tool_result = next(
        block
        for message in stored.messages
        if isinstance(message.content, tuple)
        for block in message.content
        if isinstance(block, ToolResultBlock)
    )
    assert RoundTripOutput.model_validate_json(tool_result.content).echoed == 21
    assert tool_result.is_error is False
