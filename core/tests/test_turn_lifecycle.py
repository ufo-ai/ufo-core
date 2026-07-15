import asyncio
import hashlib
import json
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel
from ufo_ext_index_default import DefaultIndex
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

from ufo.accounting import CORE_PRICING
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.loader import embed_backend, index_backend, skill_registry
from ufo.ext.manifest import EmbedBackendSpec, IndexBackendSpec, Manifest, ModelProviderSpec
from ufo.hub import Hub, InProcessHub
from ufo.jobs import TurnDispatcher
from ufo.loop import queue as loop_queue
from ufo.loop.engine import EMPTY_RESPONSE_NUDGE, FORCE_FINAL_PROMPT
from ufo.loop.subagents import SubagentProfile, SubagentRegistry, Subagents
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.models.registry import ModelRegistry
from ufo.sandbox.session import ExecResult, ProxyEndpoint, SandboxHandle, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Turn, Usage
from ufo.surfaces import hub_tail
from ufo.surfaces.cli import router
from ufo.tools.context import TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.transcript import Conversation
from ufo.workspace import ws

STREAM_TIMEOUT_SECONDS = 30
STREAM_GATE = StreamGate()
SEEN_SYSTEM_PROMPTS: list[str] = []
SEEN_TOOLS: list[tuple[str, ...]] = []


@dataclass(frozen=True)
class StubEmbed:
    """Stand-in embed client for the lifecycle Runtime: these turns run with no extensions, so the
    index/embed backends are never reached (memory recall is proven in the memory tests)."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(() for _ in texts)


class RoundTripInput(BaseModel):
    value: int


class RoundTripOutput(BaseModel):
    echoed: int


async def _hidden_probe(ctx: ToolContext, args: RoundTripInput) -> ToolResult:
    return ToolResult(content=(TextContent(text="probed"),))


STUB_BACKENDS = Manifest(
    name="stub_backends",
    version="1",
    embeds=(EmbedBackendSpec(name="default", factory=lambda ctx: StubEmbed()),),
    indexes=(
        IndexBackendSpec(
            name="default",
            factory=lambda embed, ctx: DefaultIndex(embed=embed, transaction=workspace_tx),
        ),
    ),
    tools=(
        ToolDef(
            name="hidden_probe",
            description="a profile-only capability no main agent may hold",
            input_model=RoundTripInput,
            handler=_hidden_probe,
            profile_only=True,
        ),
    ),
)


ROUNDTRIP_PROFILE = SubagentProfile(
    name="roundtrip",
    prompt="ROUNDTRIP: echo the value back.",
    tool_names=("hidden_probe",),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
)


EXHAUST_PROFILE = SubagentProfile(
    name="exhaust",
    prompt="EXHAUST: burn a round, then echo the value back.",
    tool_names=("bash",),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
    max_rounds=1,
)


PINNED_MODEL = "gpt-5.4"
PINNED_PROFILE = SubagentProfile(
    name="pinned",
    prompt="ROUNDTRIP: echo the value back on a pinned, cross-provider model.",
    tool_names=(),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
    model=PINNED_MODEL,
)

FORCED_ECHO = -1
FOLLOWUP_INBOUND = "continue"
FOLLOWUP_ECHO = 99


class ExtendInput(BaseModel):
    value: int
    extended_context: bool | None = None


EXTEND_PROFILE = SubagentProfile(
    name="extend",
    prompt="EXTEND: burn a round, then echo the value back unless forced to a close.",
    tool_names=("bash",),
    input_model=ExtendInput,
    output_model=RoundTripOutput,
    max_rounds=1,
)


class PreloadInput(BaseModel):
    value: int
    preload_skills: tuple[str, ...] | None = None


PRELOAD_PROFILE = SubagentProfile(
    name="preload",
    prompt="ROUNDTRIP: echo the value back after preloading a skill.",
    tool_names=(),
    input_model=PreloadInput,
    output_model=RoundTripOutput,
)


@dataclass(frozen=True)
class StandInModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        SEEN_SYSTEM_PROMPTS.append(request.system)
        SEEN_TOOLS.append(tuple(tool.name for tool in request.tools))
        if "ROUNDTRIP" in request.system:
            if request.messages[-1].content == FOLLOWUP_INBOUND:
                yield TextDelta(text=json.dumps({"echoed": FOLLOWUP_ECHO}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            payload = json.loads(request.messages[-1].content)
            yield TextDelta(text=json.dumps({"echoed": payload["value"]}))
            yield Usage(input_tokens=5, output_tokens=5)
            return
        if "EXTEND" in request.system:
            last = request.messages[-1].content
            if last == FORCE_FINAL_PROMPT:
                yield TextDelta(text=json.dumps({"echoed": FORCED_ECHO}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            if isinstance(last, str):
                yield ToolCallStart(id="x1", name="bash")
                yield ToolCallDelta(id="x1", partial_json='{"command": "true"}')
                yield Usage(input_tokens=2, output_tokens=2)
                return
            payload = json.loads(request.messages[0].content)
            yield TextDelta(text=json.dumps({"echoed": payload["value"]}))
            yield Usage(input_tokens=5, output_tokens=5)
            return
        if "EXHAUST" in request.system:
            if request.messages[-1].content == FORCE_FINAL_PROMPT:
                payload = json.loads(request.messages[0].content)
                yield TextDelta(text=json.dumps({"echoed": payload["value"]}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            yield ToolCallStart(id="e1", name="bash")
            yield ToolCallDelta(id="e1", partial_json='{"command": "true"}')
            yield Usage(input_tokens=2, output_tokens=2)
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
        if isinstance(inbound, str) and "spawn-exhaust" in inbound:
            yield ToolCallStart(id="s2", name="spawn_subagent")
            yield ToolCallDelta(
                id="s2", partial_json='{"profile": "exhaust", "payload": {"value": 99}}'
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-pinned" in inbound:
            yield ToolCallStart(id="s3", name="spawn_subagent")
            yield ToolCallDelta(
                id="s3", partial_json='{"profile": "pinned", "payload": {"value": 7}}'
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-extended" in inbound:
            yield ToolCallStart(id="s4", name="spawn_subagent")
            yield ToolCallDelta(
                id="s4",
                partial_json='{"profile": "extend", "payload": '
                '{"value": 42, "extended_context": true}}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-capped" in inbound:
            yield ToolCallStart(id="s5", name="spawn_subagent")
            yield ToolCallDelta(
                id="s5", partial_json='{"profile": "extend", "payload": {"value": 42}}'
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-preload" in inbound:
            yield ToolCallStart(id="s6", name="spawn_subagent")
            yield ToolCallDelta(
                id="s6",
                partial_json='{"profile": "preload", "payload": '
                '{"value": 5, "preload_skills": ["sandbox"]}}',
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


STANDIN_REGISTRY = ModelRegistry(
    providers=(
        ModelProviderSpec(
            name="standin",
            matches=lambda model: True,
            client=lambda model, key: StandInModel(),
        ),
    ),
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


@dataclass(frozen=True)
class StandInCarrier:
    """Stands in for the Docker carrier through the full queue path: create-or-attach returns a
    handle, and exec is never reached because StandInModel makes no tool calls."""

    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        if argv[:2] == ("sh", "-c") and argv[2].startswith("mkdir -p"):
            self.writes.append((argv[-1], stdin))
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
) -> Iterator[tuple[Config, GatingHub, FilesystemBlobStore]]:
    config = dbos_launched
    hub = GatingHub(InProcessHub(), STREAM_GATE)
    blob = FilesystemBlobStore(root=config.blob.root)
    proxy = ProxyEndpoint(port=0, ca_cert="test-ca")
    dbos_client = DBOSClient(system_database_url=config.database.system_url)
    embed = embed_backend((STUB_BACKENDS,), None, None)
    index = index_backend((STUB_BACKENDS,), None, embed, None)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            workspace_fs=None,
            hub=hub,
            carrier=StandInCarrier(),
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            proxy=proxy,
            dbos=dbos_client,
            subagents=SubagentRegistry(
                (
                    ROUNDTRIP_PROFILE,
                    EXHAUST_PROFILE,
                    PINNED_PROFILE,
                    EXTEND_PROFILE,
                    PRELOAD_PROFILE,
                )
            ),
            subagent_grants={},
            manifests=(STUB_BACKENDS,),
            registry=STANDIN_REGISTRY,
            skills=skill_registry((STUB_BACKENDS,)),
            credentials=None,
            index=index,
            embed=embed,
            artifact_token_secret="",
        )
    )
    yield config, hub, blob
    dbos_client.destroy()
    loop_queue.reset_runtime()


@pytest.fixture
async def surface(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[AsyncClient]:
    config, hub, _ = dbos_runtime
    STREAM_GATE.reset()
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )
    app = FastAPI()
    app.state.hub = hub
    app.state.dbos = DBOSClient(system_database_url=config.database.system_url)
    app.state.durable_surfaces = frozenset()
    app.state.shared_workspace = False
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://surface") as client:
        yield client
    app.state.dbos.destroy()


async def _bootstrap(model: str = "claude-opus-4-8") -> dict[str, str]:
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
                model=model,
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


def _bodies(stored: Conversation) -> list[object]:
    """Message contents with each member inbound's <context> tag stripped — these tests assert the
    bodies; the tag itself is proven in test_engine."""
    return [
        m.content.split("</context>\n", 1)[-1] if isinstance(m.content, str) else m.content
        for m in stored.messages
    ]


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
    mount = await loop_queue._workspace_mount(blob, None, uuid4())
    assert Path(mount.host_path).is_absolute()
    assert await asyncio.to_thread(Path(mount.host_path).is_dir)


async def test_turn_round_trip_bills_and_persists(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    STREAM_GATE.arm()
    admitted = await surface.post("/v1/chat", content=b"ping", headers=headers)
    assert admitted.status_code == 200
    turn_id = admitted.json()["turn_id"]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    assert terminal["tokens"] == 10
    assert terminal["cost_micro_usd"] == 110
    assert terminal["cache_percent"] == 0
    assert terminal["model"] == "claude-opus-4-8"
    assert terminal["reasoning"] == "high"
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
    assert _bodies(stored) == ["ping", "echo:1"]


async def test_auto_model_resolves_to_the_configured_default(surface: AsyncClient) -> None:
    """An agent authored model-agnostic (`model = "auto"`) resolves at turn time to the deploy's
    configured default, so the run selects a backend, bills, and reports under the concrete model —
    never the sentinel, which no provider serves."""
    headers = await _bootstrap(model="auto")
    admitted = await surface.post("/v1/chat", content=b"ping", headers=headers)
    assert admitted.status_code == 200
    _, terminal = await _consume(surface, headers, admitted.json()["turn_id"])
    assert terminal["status"] == "done"
    assert terminal["model"] == "claude-opus-4-8"


async def test_cost_ticks_stream_as_a_turn_accrues_spend(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = (await surface.post("/v1/chat", content=b"ping", headers=headers)).json()["turn_id"]
    costs: list[dict[str, object]] = []
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with surface.stream("GET", f"/v1/turns/{turn_id}/stream", headers=headers) as stream:
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
    STREAM_GATE.arm()
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


async def test_redelivery_of_a_finished_turn_republishes_through_the_worker(
    surface: AsyncClient,
) -> None:
    """A redelivery of an already-finished turn, replayed through the real worker entrypoint: the
    claim fails on the terminal row, and the repair flow persists the founding inbound and returns
    superseded — the member's own message survives even if the original run crashed before writing
    its transcript. The full exchange (arrivals, answer) is written by the original run's normal
    path, not reconstructed here."""
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"hi", headers=headers)).json()["turn_id"]
    await _consume(surface, headers, first)
    _, conversation_id = await _turn_row(first)
    crashed = uuid4()
    async with workspace_tx() as connection:
        scope = (
            await connection.execute(
                sa.select(tables.turn.c.workspace_id, tables.turn.c.agent_id).where(
                    tables.turn.c.id == UUID(first)
                )
            )
        ).one()
        await connection.execute(
            sa.insert(tables.turn).values(
                id=crashed,
                workspace_id=scope.workspace_id,
                conversation_id=conversation_id,
                agent_id=scope.agent_id,
                seq=2,
                status="done",
                inbound="follow-up",
                terminal={"status": "done", "text": "covers both"},
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=scope.workspace_id,
                conversation_id=conversation_id,
                seq=1,
                body="folded message",
                admission_source="member",
                admitted_turn_id=crashed,
                consumed_turn_id=crashed,
                created_at=sa.func.now(),
            )
        )
    runtime = loop_queue._runtime
    assert runtime is not None
    with ws(scope.workspace_id):
        outcome = await loop_queue._run_turn(runtime, str(crashed))
    assert outcome == "superseded"
    stored = await Transcript(blob=runtime.blob, conversation_id=conversation_id).read()
    assert stored is not None
    assert stored.seq == 2
    texts = [message.content for message in stored.messages if isinstance(message.content, str)]
    assert any(text.endswith("follow-up") for text in texts)


async def test_mid_turn_messages_absorb_into_the_running_turn(surface: AsyncClient) -> None:
    """Messages sent while a turn runs land on the conversation's inbound queue and the running
    turn absorbs them: the done-commit refuses to close over pending arrivals, the next round
    drains each as its own context-tagged user message, and one reply answers everything. The
    armed gate holds the first turn mid-stream — past its first (empty) drain, before its answer —
    so both sends land in the guarded window deterministically."""
    headers = await _bootstrap()
    STREAM_GATE.arm()
    first = (await surface.post("/v1/chat", content=b"one", headers=headers)).json()["turn_id"]
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            if first in STREAM_GATE._gates:
                break
            await asyncio.sleep(0.01)
    second = (await surface.post("/v1/chat", content=b"two", headers=headers)).json()["turn_id"]
    third = (await surface.post("/v1/chat", content=b"three", headers=headers)).json()["turn_id"]
    assert second == first
    assert third == first
    streamed, terminal = await _consume(surface, headers, first)
    assert terminal["status"] == "done"
    assert streamed == "echo:1echo:4"
    assert terminal["text"] == "echo:4"
    status, conversation_id = await _turn_row(first)
    assert status == "done"
    async with workspace_tx() as connection:
        turns = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.turn)
                .where(tables.turn.c.conversation_id == conversation_id)
            )
        ).scalar_one()
        unconsumed = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.inbound_message)
                .where(tables.inbound_message.c.consumed_turn_id.is_(None))
            )
        ).scalar_one()
    assert turns == 1
    assert unconsumed == 0
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 1)
    assert stored.seq == 1
    assert _bodies(stored) == ["one", "echo:1", "two", "three", "echo:4"]


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
    assert _bodies(stored) == ["explode"]


async def test_next_turn_sees_a_failed_turns_inbound(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    first = (await surface.post("/v1/chat", content=b"explode", headers=headers)).json()["turn_id"]
    _, first_terminal = await _consume(surface, headers, first)
    assert first_terminal["status"] == "failed"
    second = (await surface.post("/v1/chat", content=b"ok", headers=headers)).json()["turn_id"]
    _, second_terminal = await _consume(surface, headers, second)
    assert second_terminal["status"] == "done"
    _, conversation_id = await _turn_row(second)
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 2)
    assert _bodies(stored)[:2] == ["explode", "ok"]


async def test_cancel_commits_terminal_while_model_runs(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"slow", headers=headers)).json()["turn_id"]
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
    turn_id = (await surface.post("/v1/chat", content=b"ping", headers=headers)).json()["turn_id"]
    await _consume(surface, headers, turn_id)
    denied = await surface.post(f"/v1/turns/{turn_id}/cancel", headers=other)
    assert denied.status_code == 403


def _runtime_parts(surface: AsyncClient) -> tuple[Config, Hub, FilesystemBlobStore]:
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.blob, FilesystemBlobStore)
    return runtime.config, runtime.hub, runtime.blob


async def _consume_park(client: AsyncClient, headers: dict[str, str], turn_id: str) -> str:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        async with client.stream("GET", f"/v1/turns/{turn_id}/stream", headers=headers) as stream:
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
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
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
    second = (await surface.post("/v1/chat", content=b"again", headers=headers)).json()["turn_id"]
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
    with ws(workspace_id):
        await TurnDispatcher(client=loop_queue._runtime.dbos).run()
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
                sa.select(tables.ledger.c.amount).where(tables.ledger.c.turn_id == UUID(turn_id))
            )
        ).scalar_one()
    assert int(billed) == 10


async def test_empty_response_nudge_recovers_and_bills_both_calls(
    surface: AsyncClient,
) -> None:
    headers = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = (await surface.post("/v1/chat", content=b"shy", headers=headers)).json()["turn_id"]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert terminal["status"] == "done"
    assert streamed == "echo:2"
    assert terminal["tokens"] == 15
    _, conversation_id = await _turn_row(turn_id)
    _, _, blob = _runtime_parts(surface)
    stored = await _read_transcript(blob, conversation_id, 1)
    assert _bodies(stored) == ["shy", EMPTY_RESPONSE_NUDGE, "echo:2"]


async def test_empty_response_twice_fails_loud(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    turn_id = (await surface.post("/v1/chat", content=b"mute", headers=headers)).json()["turn_id"]
    streamed, terminal = await _consume(surface, headers, turn_id)
    assert streamed == ""
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["tokens"] == 10


async def test_concurrent_admissions_land_every_message_once(surface: AsyncClient) -> None:
    """A burst of concurrent sends serializes on the conversation lock: each message either opens
    a turn or lands exactly once on the live turn's inbound queue, and every reply covers what it
    drained — no message is doubled, none is dropped."""
    headers = await _bootstrap()
    responses = await asyncio.gather(
        *(
            surface.post("/v1/chat", content=f"burst {n}".encode(), headers=headers)
            for n in range(10)
        )
    )
    turn_ids = {response.json()["turn_id"] for response in responses}
    async with workspace_tx() as connection:
        foundings = (await connection.execute(sa.select(tables.turn.c.inbound))).scalars().all()
        queued = (
            (await connection.execute(sa.select(tables.inbound_message.c.body))).scalars().all()
        )
    assert sorted([*foundings, *queued]) == sorted(f"burst {n}" for n in range(10))
    results = await asyncio.gather(*(_consume(surface, headers, turn_id) for turn_id in turn_ids))
    assert all(terminal["status"] == "done" for _, terminal in results)


async def test_typed_subagent_round_trips_schema(surface: AsyncClient) -> None:
    headers = await _bootstrap()
    parent = (await surface.post("/v1/chat", content=b"spawn-subagent", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
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


async def test_subagent_exhausting_its_round_budget_does_not_detonate_its_parent(
    surface: AsyncClient,
) -> None:
    headers = await _bootstrap()
    parent = (await surface.post("/v1/chat", content=b"spawn-exhaust", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.parent_turn_id == UUID(parent)
                )
            )
        ).one()
    assert child.status == "done"
    child_output = TerminalFrame.model_validate(child.terminal).text
    assert RoundTripOutput.model_validate_json(child_output).echoed == 99


async def test_subagent_bills_under_its_profile_model_not_the_parents(
    surface: AsyncClient,
) -> None:
    headers = await _bootstrap()
    parent = (await surface.post("/v1/chat", content=b"spawn-pinned", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    assert terminal["model"] == "claude-opus-4-8"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.subagent_profile,
                    tables.turn.c.status,
                    tables.turn.c.terminal,
                ).where(tables.turn.c.parent_turn_id == UUID(parent))
            )
        ).one()
    assert child.subagent_profile == "pinned"
    assert child.status == "done"
    assert (
        RoundTripOutput.model_validate_json(
            TerminalFrame.model_validate(child.terminal).text
        ).echoed
        == 7
    )
    async with workspace_tx() as connection:
        child_model = (
            await connection.execute(
                sa.select(tables.ledger.c.model).where(tables.ledger.c.turn_id == child.id)
            )
        ).scalar_one()
        parent_model = (
            await connection.execute(
                sa.select(tables.ledger.c.model).where(tables.ledger.c.turn_id == UUID(parent))
            )
        ).scalar_one()
    assert child_model == PINNED_MODEL
    assert parent_model == "claude-opus-4-8"


async def _child_echo(parent: str) -> int:
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(tables.turn.c.terminal).where(
                    tables.turn.c.parent_turn_id == UUID(parent)
                )
            )
        ).one()
    return RoundTripOutput.model_validate_json(
        TerminalFrame.model_validate(child.terminal).text
    ).echoed


async def test_subagent_extended_context_lifts_the_round_ceiling(surface: AsyncClient) -> None:
    """A subagent whose payload carries `extended_context: true` runs under MAIN_ROUND_LIMIT, not
    its own smaller `max_rounds`. The `extend` profile burns a tool round and echoes on the next —
    a reach its 1-round budget cannot make: capped it force-finals to the sentinel, extended it
    reaches the real echo."""
    headers = await _bootstrap()
    extended = (await surface.post("/v1/chat", content=b"spawn-extended", headers=headers)).json()[
        "turn_id"
    ]
    _, extended_terminal = await _consume(surface, headers, extended)
    assert extended_terminal["status"] == "done"
    capped = (await surface.post("/v1/chat", content=b"spawn-capped", headers=headers)).json()[
        "turn_id"
    ]
    _, capped_terminal = await _consume(surface, headers, capped)
    assert capped_terminal["status"] == "done"
    assert await _child_echo(extended) == 42
    assert await _child_echo(capped) == FORCED_ECHO


async def test_subagent_preload_skills_mounts_and_injects_the_skill(surface: AsyncClient) -> None:
    """A subagent whose payload carries `preload_skills` starts with the skill mounted into its
    sandbox and its instructions already in the system prompt — no `load_skill` round needed."""
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.carrier, StandInCarrier)
    skill = runtime.skills.named("sandbox")
    runtime.carrier.writes.clear()
    SEEN_SYSTEM_PROMPTS.clear()
    headers = await _bootstrap()
    parent = (await surface.post("/v1/chat", content=b"spawn-preload", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    assert await _child_echo(parent) == 5
    mounted = dict(runtime.carrier.writes)
    skill_md = next(path for path in mounted if path.endswith(f"/.skills/{skill.name}/SKILL.md"))
    assert mounted[skill_md] == skill.raw_skill_md.encode()
    assert any(skill.prompt_body() in system for system in SEEN_SYSTEM_PROMPTS)


async def test_subagent_plain_text_followup_runs_without_a_spawn_payload(
    surface: AsyncClient,
) -> None:
    """`message_subagent` stores free text as the follow-up turn's inbound — only the child's
    spawn turn (seq 1) carries the JSON payload, so the follow-up must run to its own terminal
    without parsing one."""
    runtime = loop_queue._runtime
    assert runtime is not None
    headers = await _bootstrap()
    parent_id = (await surface.post("/v1/chat", content=b"spawn-subagent", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent_id)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        parent_row = (
            await connection.execute(
                sa.select(tables.turn).where(tables.turn.c.id == UUID(parent_id))
            )
        ).one()
        child_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.parent_turn_id == UUID(parent_id))
            )
        ).scalar_one()
    parent = Turn(
        id=parent_row.id,
        workspace_id=parent_row.workspace_id,
        conversation_id=parent_row.conversation_id,
        agent_id=parent_row.agent_id,
        seq=parent_row.seq,
        status=parent_row.status,
        inbound=parent_row.inbound,
        created_at=parent_row.created_at,
        terminal=TerminalFrame.model_validate(parent_row.terminal),
    )
    subagents = Subagents(client=runtime.dbos, registry=runtime.subagents, parent=parent)
    queued = await subagents.message(child_id, FOLLOWUP_INBOUND)
    (followup,) = await subagents.wait((queued.turn_id,))
    assert followup.status == "done"
    assert RoundTripOutput.model_validate_json(followup.text).echoed == FOLLOWUP_ECHO


async def test_profile_only_tools_stay_out_of_main_agent_turns(surface: AsyncClient) -> None:
    """Both ends of the profile-only seam through the real turn path: the main agent's registry
    never offers the tool, and the profile that names it still resolves it for its child turn."""
    headers = await _bootstrap()
    SEEN_TOOLS.clear()
    parent = (await surface.post("/v1/chat", content=b"spawn-subagent", headers=headers)).json()[
        "turn_id"
    ]
    _, terminal = await _consume(surface, headers, parent)
    assert terminal["status"] == "done"
    main_offers = [names for names in SEEN_TOOLS if "spawn_subagent" in names]
    child_offers = [names for names in SEEN_TOOLS if "hidden_probe" in names]
    assert main_offers
    assert all("hidden_probe" not in names for names in main_offers)
    assert child_offers
    assert all("spawn_subagent" not in names for names in child_offers)
