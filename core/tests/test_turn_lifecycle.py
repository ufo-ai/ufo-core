import asyncio
import json
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from dbos import DBOSClient
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_index_default import DefaultIndex
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

from evals.driver import WorkspaceDriver
from evals.harness.capability import CapabilityCase
from evals.harness.scorers import exact_scorer
from evals.harness.target import InProcessTarget
from ufo.audience import conversation_audience
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.connectors import ConnectorRegistry
from ufo.db import workspace_tx
from ufo.ext.context import context_for
from ufo.ext.loader import embed_backend, index_backend, skill_registry
from ufo.ext.manifest import EmbedBackendSpec, IndexBackendSpec, Manifest
from ufo.hub import CostTick, Hub, InProcessHub, Parked, Terminal
from ufo.jobs import TurnDispatcher
from ufo.loop import queue as loop_queue
from ufo.loop.engine import (
    EMPTY_RESPONSE_NUDGE,
    FINISH_TOOL,
    TRUNCATION_FEEDBACK,
)
from ufo.loop.subagents import SubagentProfile, SubagentRegistry, Subagents
from ufo.loop.transcript import Transcript
from ufo.models.catalog import CORE_MODEL_SPECS, CORE_PRICING
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.models.registry import ModelRegistry
from ufo.sandbox.session import (
    ExecResult,
    ProxyEndpoint,
    RunToken,
    RunTokenCodec,
    SandboxHandle,
    SandboxSpec,
)
from ufo.schema import tables
from ufo.schema.records import TerminalFrame, Turn, Usage
from ufo.surfaces import hub_tail
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.tools.context import TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.transcript import Conversation
from ufo.workspace import ws

STREAM_TIMEOUT_SECONDS = 30
TRUNCATION_MESSAGE = (
    "Anthropic completion truncated at the max_tokens budget (stop_reason=max_tokens)"
)
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
            factory=lambda ctx: DefaultIndex(transaction=workspace_tx),
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
                yield ToolCallStart(id="r2", name=FINISH_TOOL)
                yield ToolCallDelta(id="r2", partial_json=json.dumps({"echoed": FOLLOWUP_ECHO}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            payload = json.loads(request.messages[-1].content)
            yield ToolCallStart(id="r1", name=FINISH_TOOL)
            yield ToolCallDelta(id="r1", partial_json=json.dumps({"echoed": payload["value"]}))
            yield Usage(input_tokens=5, output_tokens=5)
            return
        if "EXTEND" in request.system:
            if request.tool_choice is not None:
                yield ToolCallStart(id="x2", name=FINISH_TOOL)
                yield ToolCallDelta(id="x2", partial_json=json.dumps({"echoed": FORCED_ECHO}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            if isinstance(request.messages[-1].content, str):
                yield ToolCallStart(id="x1", name="bash")
                yield ToolCallDelta(
                    id="x1",
                    partial_json='{"command": "true", "user_description": "running a check"}',
                )
                yield Usage(input_tokens=2, output_tokens=2)
                return
            payload = json.loads(request.messages[0].content)
            yield ToolCallStart(id="x3", name=FINISH_TOOL)
            yield ToolCallDelta(id="x3", partial_json=json.dumps({"echoed": payload["value"]}))
            yield Usage(input_tokens=5, output_tokens=5)
            return
        if "EXHAUST" in request.system:
            if request.tool_choice is not None:
                payload = json.loads(request.messages[0].content)
                yield ToolCallStart(id="e2", name=FINISH_TOOL)
                yield ToolCallDelta(id="e2", partial_json=json.dumps({"echoed": payload["value"]}))
                yield Usage(input_tokens=5, output_tokens=5)
                return
            yield ToolCallStart(id="e1", name="bash")
            yield ToolCallDelta(
                id="e1", partial_json='{"command": "true", "user_description": "running a check"}'
            )
            yield Usage(input_tokens=2, output_tokens=2)
            return
        contents = [m.content for m in request.messages]
        nudged = contents[-1] == EMPTY_RESPONSE_NUDGE
        inbound = contents[-2] if nudged else contents[-1]
        if isinstance(inbound, str) and "spawn-subagent" in inbound:
            yield ToolCallStart(id="s1", name="spawn_subagent")
            yield ToolCallDelta(
                id="s1",
                partial_json='{"profile": "roundtrip", "payload": {"value": 21}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-exhaust" in inbound:
            yield ToolCallStart(id="s2", name="spawn_subagent")
            yield ToolCallDelta(
                id="s2",
                partial_json='{"profile": "exhaust", "payload": {"value": 99}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-pinned" in inbound:
            yield ToolCallStart(id="s3", name="spawn_subagent")
            yield ToolCallDelta(
                id="s3",
                partial_json='{"profile": "pinned", "payload": {"value": 7}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-extended" in inbound:
            yield ToolCallStart(id="s4", name="spawn_subagent")
            yield ToolCallDelta(
                id="s4",
                partial_json='{"profile": "extend", "payload": '
                '{"value": 42, "extended_context": true}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-capped" in inbound:
            yield ToolCallStart(id="s5", name="spawn_subagent")
            yield ToolCallDelta(
                id="s5",
                partial_json='{"profile": "extend", "payload": {"value": 42}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-preload" in inbound:
            yield ToolCallStart(id="s6", name="spawn_subagent")
            yield ToolCallDelta(
                id="s6",
                partial_json='{"profile": "preload", "payload": '
                '{"value": 5, "preload_skills": ["sandbox"]}, '
                '"user_description": "handing off the research"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if "explode-after-usage" in inbound:
            yield TextDelta(text="partial")
            yield Usage(input_tokens=7, output_tokens=3)
            raise RuntimeError("late boom")
        if "explode" in inbound:
            raise RuntimeError("boom")
        if (
            any(isinstance(content, str) and "truncate" in content for content in contents)
            and TRUNCATION_FEEDBACK not in contents
        ):
            raise ModelResponseTruncated(TRUNCATION_MESSAGE)
        if "slow" in inbound:
            await asyncio.sleep(30)
        if "mute" in inbound or ("shy" in inbound and not nudged):
            yield Usage(input_tokens=5)
            return
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


STANDIN_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(spec, client=lambda spec, key: StandInModel(), key_slot="", key_env="")
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model="claude-opus-4-8",
)


@dataclass(frozen=True)
class StandInCarrier:
    """Stands in for the Docker carrier through the full queue path: create-or-attach returns a
    handle, and exec is never reached because StandInModel makes no tool calls."""

    writes: list[tuple[str, bytes]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        proxy = f"http://{spec.run_token}:@proxy"
        return SandboxHandle(
            conversation_id=spec.conversation_id,
            container_id="test",
            run_token=spec.run_token,
            egress_env={
                "HTTP_PROXY": proxy,
                "HTTPS_PROXY": proxy,
                "http_proxy": proxy,
                "https_proxy": proxy,
            },
        )

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None:
        self.writes.append((path, content))

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
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
    index = index_backend((STUB_BACKENDS,), None, None)
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
            run_tokens=RunTokenCodec(b"turn-lifecycle-test-secret"),
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


@dataclass(frozen=True)
class Seed:
    workspace_id: UUID
    member_id: UUID
    agent_id: UUID
    conversation_id: UUID


@dataclass(frozen=True)
class Turns:
    """The turn-engine harness: admit a member turn onto the durable queue and read its live frames
    off the hub — the surface-agnostic halves (`MemberAdmission`, `hub_tail`) every surface reaches
    through, exercised directly so these tests bind the engine, not a surface."""

    hub: Hub
    admission: Admission

    async def admit(self, seed: Seed, body: str, idempotency_key: str | None = None) -> str:
        admitted = await MemberAdmission(
            admission=self.admission, workspace_id=seed.workspace_id
        ).admit(
            seed.conversation_id,
            body,
            idempotency_key=idempotency_key,
            speaker_member_id=seed.member_id,
        )
        return str(admitted.turn_id)

    async def consume(self, seed: Seed, turn_id: str) -> tuple[str, dict[str, object]]:
        deltas: list[str] = []
        with ws(seed.workspace_id):
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                async for _cursor, frame in hub_tail.tail_frames(self.hub, UUID(turn_id)):
                    match frame:
                        case TextDelta():
                            deltas.append(frame.text)
                        case Terminal():
                            return "".join(deltas), frame.frame.model_dump(mode="json")
        raise AssertionError("stream ended without a terminal frame")

    async def consume_park(self, seed: Seed, turn_id: str) -> str:
        with ws(seed.workspace_id):
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                async for _cursor, frame in hub_tail.tail_frames(self.hub, UUID(turn_id)):
                    if isinstance(frame, Parked):
                        return frame.message
        raise AssertionError("stream ended without a park frame")

    async def consume_costs(self, seed: Seed, turn_id: str) -> list[dict[str, object]]:
        costs: list[dict[str, object]] = []
        with ws(seed.workspace_id):
            async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
                async for _cursor, frame in hub_tail.tail_frames(self.hub, UUID(turn_id)):
                    match frame:
                        case CostTick():
                            costs.append(frame.model_dump(mode="json"))
                        case Terminal():
                            return costs
        raise AssertionError("stream ended without a terminal frame")


@pytest.fixture
async def surface(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[Turns]:
    _config, hub, _ = dbos_runtime
    STREAM_GATE.reset()
    monkeypatch.setattr(
        hub_tail, "turn_status_frame", release_when_running(STREAM_GATE, hub_tail.turn_status_frame)
    )
    assert loop_queue._runtime is not None
    admission = Admission(dbos=loop_queue._runtime.dbos, durable_surfaces=frozenset())
    yield Turns(hub=hub, admission=admission)


async def _bootstrap(model: str = "claude-opus-4-8") -> Seed:
    workspace_id, member_id, agent_id, conversation_id = uuid4(), uuid4(), uuid4(), uuid4()
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
                email=f"{member_id.hex[:8]}@example.com",
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
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key=uuid4().hex,
                member_id=member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Seed(workspace_id, member_id, agent_id, conversation_id)


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
    mount = await loop_queue._workspace_mount(
        blob, None, uuid4(), RunToken(workspace_id=uuid4(), turn_id=uuid4()), fresh_sandbox=True
    )
    assert Path(mount.host_path).is_absolute()
    assert await asyncio.to_thread(Path(mount.host_path).is_dir)


async def test_turn_round_trip_bills_and_persists(surface: Turns) -> None:
    seed = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = await surface.admit(seed, "ping")
    streamed, terminal = await surface.consume(seed, turn_id)
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
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 1)
    assert stored.seq == 1
    assert _bodies(stored) == ["ping", "echo:1"]


async def test_auto_model_resolves_to_the_configured_default(surface: Turns) -> None:
    """An agent authored model-agnostic (`model = "auto"`) resolves at turn time to the deploy's
    configured default, so the run selects a backend, bills, and reports under the concrete model —
    never the sentinel, which no provider serves."""
    seed = await _bootstrap(model="auto")
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert terminal["model"] == "claude-opus-4-8"


async def test_turn_prompt_uses_the_models_knowledge_cutoff(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    cutoffs: list[str] = []
    render = loop_queue.render_system_prompt

    def capture_knowledge_cutoff(*args: object, **kwargs: object) -> object:
        cutoffs.append(str(kwargs["knowledge_cutoff"]))
        return render(*args, **kwargs)

    monkeypatch.setattr(loop_queue, "render_system_prompt", capture_knowledge_cutoff)
    seed = await _bootstrap(model=PINNED_MODEL)
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert cutoffs == [STANDIN_REGISTRY.spec(PINNED_MODEL).knowledge_cutoff]
    assert cutoffs != [STANDIN_REGISTRY.spec("claude-opus-4-8").knowledge_cutoff]


async def test_turn_compaction_uses_the_models_context_window(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows: list[int] = []
    compaction = loop_queue.Compaction

    def capture_context_window(**kwargs: object) -> object:
        windows.append(int(kwargs["context_window"]))
        return compaction(**kwargs)

    monkeypatch.setattr(loop_queue, "Compaction", capture_context_window)
    seed = await _bootstrap(model=PINNED_MODEL)
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert windows == [STANDIN_REGISTRY.spec(PINNED_MODEL).context_window]
    assert windows != [STANDIN_REGISTRY.spec("claude-opus-4-8").context_window]


async def test_cost_ticks_stream_as_a_turn_accrues_spend(surface: Turns) -> None:
    seed = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = await surface.admit(seed, "ping")
    costs = await surface.consume_costs(seed, turn_id)
    assert costs
    assert costs[-1] == {"cost_micro_usd": 110, "tokens": 10}


async def test_second_turn_continues_the_conversation(surface: Turns) -> None:
    seed = await _bootstrap()
    STREAM_GATE.arm()
    first = await surface.admit(seed, "one")
    await surface.consume(seed, first)
    second = await surface.admit(seed, "two")
    streamed, terminal = await surface.consume(seed, second)
    assert terminal["status"] == "done"
    assert streamed == "echo:3"
    _, conversation_id = await _turn_row(second)
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 2)
    assert stored.seq == 2
    assert len(stored.messages) == 4


async def test_redelivery_of_a_finished_turn_republishes_through_the_worker(
    surface: Turns,
) -> None:
    """A redelivery of an already-finished turn, replayed through the real worker entrypoint: the
    claim fails on the terminal row, and the repair flow persists the founding inbound and returns
    superseded — the member's own message survives even if the original run crashed before writing
    its transcript. The full exchange (arrivals, answer) is written by the original run's normal
    path, not reconstructed here."""
    seed = await _bootstrap()
    first = await surface.admit(seed, "hi")
    await surface.consume(seed, first)
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


async def test_mid_turn_messages_absorb_into_the_running_turn(surface: Turns) -> None:
    """A1, B1, A2 remain one FIFO turn, terminal, and durable writeback."""
    seed = await _bootstrap()
    second_member = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=second_member,
                workspace_id=seed.workspace_id,
                email=f"{second_member.hex[:8]}@example.com",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    durable = Turns(
        hub=surface.hub,
        admission=Admission(
            dbos=surface.admission.dbos,
            durable_surfaces=frozenset(("cli",)),
        ),
    )
    STREAM_GATE.arm()
    first = await durable.admit(seed, "one")
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            if first in STREAM_GATE._gates:
                break
            await asyncio.sleep(0.01)
    second = await durable.admit(replace(seed, member_id=second_member), "two")
    third = await durable.admit(seed, "three")
    assert second == first
    assert third == first
    streamed, terminal = await durable.consume(seed, first)
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
        writebacks = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.writeback)
                .where(tables.writeback.c.turn_id == UUID(first))
            )
        ).scalar_one()
        speakers = (
            (
                await connection.execute(
                    sa.select(tables.inbound_message.c.speaker_member_id)
                    .where(tables.inbound_message.c.conversation_id == conversation_id)
                    .order_by(tables.inbound_message.c.seq)
                )
            )
            .scalars()
            .all()
        )
    assert turns == 1
    assert unconsumed == 0
    assert writebacks == 1
    assert speakers == [second_member, seed.member_id]
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 1)
    assert stored.seq == 1
    assert _bodies(stored) == ["one", "echo:1", "two", "three", "echo:4"]


async def test_failure_commits_terminal_bills_nothing_preserves_inbound(
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    turn_id = await surface.admit(seed, "explode")
    streamed, terminal = await surface.consume(seed, turn_id)
    assert streamed == ""
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["error_message"] == "boom"
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
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 1)
    assert _bodies(stored) == ["explode"]


async def _running_turn() -> tuple[UUID, UUID]:
    workspace_id, agent_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
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
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
                surface="cli",
                queue_key="session",
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
                status="running",
                inbound="explode",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, turn_id


async def test_backstop_terminal_carries_class_and_message(db: None) -> None:
    """A failure outside the engine commits a terminal carrying the class AND the message — a bare
    class name gives the debugger and CLI nothing to act on (the 2026-07-21 wedge surfaced as a
    naked \"RuntimeError\")."""
    _, turn_id = await _running_turn()
    await loop_queue._commit_failed_terminal(
        InProcessHub(), turn_id, RuntimeError("boom outside the engine")
    )
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert row.status == "failed"
    assert row.terminal["error_class"] == "RuntimeError"
    assert row.terminal["error_message"] == "boom outside the engine"


async def test_agent_scope_lookup_failure_commits_a_terminal(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace_id, turn_id = await _running_turn()
    original = loop_queue.workspace_tx
    first = True

    @asynccontextmanager
    async def fail_agent_lookup() -> AsyncIterator[AsyncConnection]:
        nonlocal first
        if first:
            first = False
            raise RuntimeError("agent lookup failed")
        async with original() as connection:
            yield connection

    monkeypatch.setattr(loop_queue, "workspace_tx", fail_agent_lookup)
    monkeypatch.setattr(loop_queue, "_runtime", SimpleNamespace(hub=InProcessHub()))

    assert await loop_queue._execute_turn(str(workspace_id), str(turn_id)) == "failed"

    async with original() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert row.status == "failed"
    assert row.terminal["error_class"] == "RuntimeError"
    assert row.terminal["error_message"] == "agent lookup failed"


async def test_next_turn_sees_a_failed_turns_inbound(surface: Turns) -> None:
    seed = await _bootstrap()
    first = await surface.admit(seed, "explode")
    _, first_terminal = await surface.consume(seed, first)
    assert first_terminal["status"] == "failed"
    second = await surface.admit(seed, "ok")
    _, second_terminal = await surface.consume(seed, second)
    assert second_terminal["status"] == "done"
    _, conversation_id = await _turn_row(second)
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 2)
    assert _bodies(stored)[:2] == ["explode", "ok"]


EVAL_OVERDUE_DEADLINE_SECONDS = 1.0
EVAL_FOLLOWUP_WAIT_SECONDS = 60.0


async def test_eval_settle_deadline_cancels_the_turn_before_the_runner_advances(
    db: None, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """A case whose model round outlives the eval driver's wait must not leak a running turn into
    the next case: settle's deadline commits the cancelled terminal and durably cancels the DBOS
    workflow before returning, the harness records the case as an infrastructure failure, and the
    following case runs clean on a turn whose predecessor is already terminal."""
    _, _, blob = dbos_runtime
    STREAM_GATE.reset()
    await _bootstrap()
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
    runtime = loop_queue._runtime
    assert runtime is not None
    overdue_driver = WorkspaceDriver(
        workspace_id,
        agent_id,
        "be brief",
        blob,
        runtime.dbos,
        poll_interval_seconds=0.05,
        workflow_wait_seconds=EVAL_OVERDUE_DEADLINE_SECONDS,
    )
    followup_driver = replace(overdue_driver, workflow_wait_seconds=EVAL_FOLLOWUP_WAIT_SECONDS)
    target = InProcessTarget(
        ctx=context_for(
            "evals",
            frozenset(),
            invoker=AdmissionInvoker(
                admission=Admission(dbos=runtime.dbos, durable_surfaces=frozenset()),
                workspace_id=workspace_id,
            ),
        ),
        agent_id=agent_id,
        conversations=overdue_driver,
        outcome=overdue_driver,
    )

    with ws(workspace_id):
        overdue = await target.run(CapabilityCase("deadline", "slow", exact_scorer("unused")))
        followup = await replace(target, outcome=followup_driver).run(
            CapabilityCase("follow-up", "ping", exact_scorer("unused"))
        )

    assert not overdue.clean
    assert overdue.failure_reason == "turn produced no terminal transcript"
    assert overdue.trajectory is not None
    assert overdue.trajectory.turn_id is not None
    assert overdue.trajectory.status == "cancelled"
    status, _ = await _turn_row(str(overdue.trajectory.turn_id))
    assert status == "cancelled"
    handle = await runtime.dbos.retrieve_workflow_async(str(overdue.trajectory.turn_id))
    assert (await handle.get_status()).status == "CANCELLED"
    assert followup.clean


def _runtime_parts() -> tuple[Config, Hub, FilesystemBlobStore]:
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.blob, FilesystemBlobStore)
    return runtime.config, runtime.hub, runtime.blob


async def _await_status(turn_id: str, target: str) -> None:
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            status, _ = await _turn_row(turn_id)
            if status == target:
                return
            await asyncio.sleep(0.05)


async def test_member_cap_parks_a_turn_in_surface_then_resumes_when_raised(
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    first = await surface.admit(seed, "ping")
    _, first_terminal = await surface.consume(seed, first)
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
    second = await surface.admit(seed, "again")
    park_message = await surface.consume_park(seed, second)
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


async def test_failure_after_usage_bills_partial_usage(surface: Turns) -> None:
    seed = await _bootstrap()
    turn_id = await surface.admit(seed, "explode-after-usage")
    _, terminal = await surface.consume(seed, turn_id)
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
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = await surface.admit(seed, "shy")
    streamed, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert streamed == "echo:2"
    assert terminal["tokens"] == 15
    _, conversation_id = await _turn_row(turn_id)
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 1)
    assert _bodies(stored) == ["shy", EMPTY_RESPONSE_NUDGE, "echo:2"]


async def test_empty_response_twice_fails_loud(surface: Turns) -> None:
    seed = await _bootstrap()
    turn_id = await surface.admit(seed, "mute")
    streamed, terminal = await surface.consume(seed, turn_id)
    assert streamed == ""
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["error_message"] == "model returned an empty response twice"
    assert terminal["tokens"] == 10


async def test_a_truncated_round_recovers_and_the_turn_completes(surface: Turns) -> None:
    """A stream dying at max_tokens is recovered: the correction is fed back as a user message
    and the retried round answers the turn — through the full surface/DBOS runtime."""
    seed = await _bootstrap()
    turn_id = await surface.admit(seed, "truncate")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert terminal["text"] == "echo:2"


async def test_a_model_round_error_commits_a_terminal_without_leaking_an_orphaned_future(
    surface: Turns, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """A fatal mid-stream model error commits ONE durable failed terminal carrying the model's
    error class AND message, and the turn workflow completes cleanly — it does NOT re-raise into
    the queue runner's discarded workflow task, where the exception is retrieved by no one and
    surfaces only as an asyncio "Future exception was never retrieved" that never reaches the
    client (issue #568). The client's wait ends on the committed terminal; DBOS records the
    workflow SUCCESS with the turn's terminal status as its output, so the retrieval leg returns
    that status instead of raising a pickled exception nobody consumes."""
    config, _, _ = dbos_runtime
    seed = await _bootstrap()
    turn_id = await surface.admit(seed, "explode")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "failed"
    assert terminal["error_class"] == "RuntimeError"
    assert terminal["error_message"] == "boom"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == UUID(turn_id)
                )
            )
        ).one()
    assert row.status == "failed"
    durable = TerminalFrame.model_validate(row.terminal)
    assert durable.error_class == "RuntimeError"
    assert durable.error_message == "boom"
    client = DBOSClient(system_database_url=config.database.system_url)
    try:
        handle = await client.retrieve_workflow_async(turn_id)
        async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
            outcome = await handle.get_result(polling_interval_sec=0.05)
        workflow = await handle.get_status()
    finally:
        client.destroy()
    assert outcome == "failed"
    assert workflow.status == "SUCCESS"


async def test_concurrent_admissions_land_every_message_once(surface: Turns) -> None:
    """A burst of concurrent sends serializes on the conversation lock: each message either opens
    a turn or lands exactly once on the live turn's inbound queue, and every reply covers what it
    drained — no message is doubled, none is dropped."""
    seed = await _bootstrap()
    turn_ids = set(await asyncio.gather(*(surface.admit(seed, f"burst {n}") for n in range(10))))
    async with workspace_tx() as connection:
        foundings = (await connection.execute(sa.select(tables.turn.c.inbound))).scalars().all()
        queued = (
            (await connection.execute(sa.select(tables.inbound_message.c.body))).scalars().all()
        )
    assert sorted([*foundings, *queued]) == sorted(f"burst {n}" for n in range(10))
    results = await asyncio.gather(*(surface.consume(seed, turn_id) for turn_id in turn_ids))
    assert all(terminal["status"] == "done" for _, terminal in results)


async def test_typed_subagent_round_trips_schema(surface: Turns) -> None:
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)
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
    _, _, blob = _runtime_parts()
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
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-exhaust")
    _, terminal = await surface.consume(seed, parent)
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
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-pinned")
    _, terminal = await surface.consume(seed, parent)
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


async def test_subagent_extended_context_lifts_the_round_ceiling(surface: Turns) -> None:
    """A subagent whose payload carries `extended_context: true` runs under MAIN_ROUND_LIMIT, not
    its own smaller `max_rounds`. The `extend` profile burns a tool round and echoes on the next —
    a reach its 1-round budget cannot make: capped it force-finals to the sentinel, extended it
    reaches the real echo."""
    seed = await _bootstrap()
    extended = await surface.admit(seed, "spawn-extended")
    _, extended_terminal = await surface.consume(seed, extended)
    assert extended_terminal["status"] == "done"
    capped = await surface.admit(seed, "spawn-capped")
    _, capped_terminal = await surface.consume(seed, capped)
    assert capped_terminal["status"] == "done"
    assert await _child_echo(extended) == 42
    assert await _child_echo(capped) == FORCED_ECHO


async def test_subagent_preload_skills_mounts_and_injects_the_skill(surface: Turns) -> None:
    """A subagent whose payload carries `preload_skills` starts with the skill mounted into its
    sandbox and its instructions already in the system prompt — no `load_skill` round needed."""
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.carrier, StandInCarrier)
    skill = runtime.skills.named("sandbox")
    runtime.carrier.writes.clear()
    SEEN_SYSTEM_PROMPTS.clear()
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-preload")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    assert await _child_echo(parent) == 5
    mounted = dict(runtime.carrier.writes)
    skill_md = next(path for path in mounted if path.endswith(f"/.skills/{skill.name}/SKILL.md"))
    assert mounted[skill_md] == skill.raw_skill_md.encode()
    assert any(skill.instructions in system for system in SEEN_SYSTEM_PROMPTS)


async def test_subagent_plain_text_followup_runs_without_a_spawn_payload(
    surface: Turns,
) -> None:
    """`message_subagent` stores free text as the follow-up turn's inbound — only the child's
    spawn turn (seq 1) carries the JSON payload, so the follow-up must run to its own terminal
    without parsing one."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    parent_id = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent_id)
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
    subagents = Subagents(
        client=runtime.dbos,
        registry=runtime.subagents,
        parent=parent,
        audience=conversation_audience(None),
    )
    queued = await subagents.message(child_id, FOLLOWUP_INBOUND)
    (followup,) = await subagents.wait((queued.turn_id,))
    assert followup.status == "done"
    assert RoundTripOutput.model_validate_json(followup.text).echoed == FOLLOWUP_ECHO


async def test_profile_only_tools_stay_out_of_main_agent_turns(surface: Turns) -> None:
    """Both ends of the profile-only seam through the real turn path: the main agent's registry
    never offers the tool, and the profile that names it still resolves it for its child turn."""
    seed = await _bootstrap()
    SEEN_TOOLS.clear()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    main_offers = [names for names in SEEN_TOOLS if "spawn_subagent" in names]
    child_offers = [names for names in SEEN_TOOLS if "hidden_probe" in names]
    assert main_offers
    assert all("hidden_probe" not in names for names in main_offers)
    assert child_offers
    assert all("spawn_subagent" not in names for names in child_offers)
