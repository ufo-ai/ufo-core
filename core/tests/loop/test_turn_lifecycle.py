import asyncio
import json
import logging
import subprocess
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_index_default import DefaultIndex
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

from evals.driver import WorkspaceDriver
from evals.harness.capability import CapabilityCase
from evals.harness.scorers import exact_scorer
from evals.harness.target import InProcessTarget
from evals.harness.timing import UNNAMED_TOOL
from ufo import o11y
from ufo.access.connectors import ConnectorRegistry
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.durability import replay_safe_client
from ufo.ext.context import context_for
from ufo.ext.loader import embed_backend, index_backend, skill_registry
from ufo.ext.manifest import EmbedBackendSpec, IndexBackendSpec, Manifest
from ufo.hub import CostTick, Hub, InProcessHub, Parked, SubagentActivity, Terminal
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
from ufo.runtime.jobs import TurnDispatcher
from ufo.sandbox.conversation import (
    SANDBOX_IMAGE_REF,
    UNSIGNED_RUN_TOKEN,
    ConversationSandbox,
)
from ufo.sandbox.local import LocalCarrier
from ufo.sandbox.session import ProxyEndpoint, RunTokenCodec
from ufo.schema import tables
from ufo.schema.records import ReasoningEffort, TerminalFrame, Turn, TurnContext, Usage
from ufo.surfaces import hub_tail
from ufo.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.tools.context import TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef
from ufo.turns.activity import ACTIVITY_PROMPT
from ufo.turns.audience import conversation_audience
from ufo.turns.transcript import Conversation
from ufo.turns.workspace_changes import WorkspaceChange, WorkspaceChanges
from ufo.workspace import ws

STREAM_TIMEOUT_SECONDS = 30
TRUNCATION_MESSAGE = (
    "Anthropic completion truncated at the max_tokens budget (stop_reason=max_tokens)"
)
STREAM_GATE = StreamGate()
SEEN_SYSTEM_PROMPTS: list[str] = []
SEEN_TOOLS: list[tuple[str, ...]] = []
SEEN_REASONING: list[ReasoningEffort] = []


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
    reasoning="high",
)

FORCED_ECHO = -1
FOLLOWUP_INBOUND = "continue"
FOLLOWUP_ECHO = 99
RUN_A_COMMAND = "run a command"
"""The inbound that makes the stand-in call `bash` before it answers — what a turn that reaches its
sandbox looks like, since a sandbox is created for the first operation that needs one and a turn
answering out of its context creates none."""
WORKFLOW_POLL_SECONDS = 0.05


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
        if request.system == ACTIVITY_PROMPT:
            yield TextDelta(text="Running a check.")
            yield Usage(input_tokens=2, output_tokens=2)
            return
        SEEN_SYSTEM_PROMPTS.append(request.system)
        SEEN_TOOLS.append(tuple(tool.name for tool in request.tools))
        SEEN_REASONING.append(request.reasoning)
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
                    partial_json='{"command": "true"}',
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
            yield ToolCallDelta(id="e1", partial_json='{"command": "true"}')
            yield Usage(input_tokens=2, output_tokens=2)
            return
        contents = [m.content for m in request.messages]
        nudged = contents[-1] == EMPTY_RESPONSE_NUDGE
        inbound = contents[-2] if nudged else contents[-1]
        if isinstance(inbound, str) and RUN_A_COMMAND in inbound:
            yield ToolCallStart(id="b1", name="bash")
            yield ToolCallDelta(id="b1", partial_json='{"command": "true"}')
            yield Usage(input_tokens=2, output_tokens=2)
            return
        if isinstance(inbound, str) and "spawn-subagent" in inbound:
            yield ToolCallStart(id="s1", name="spawn")
            yield ToolCallDelta(
                id="s1",
                partial_json='{"target": "roundtrip", "payload": {"value": 21}}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-exhaust" in inbound:
            yield ToolCallStart(id="s2", name="spawn")
            yield ToolCallDelta(
                id="s2",
                partial_json='{"target": "exhaust", "payload": {"value": 99}, '
                '"name": "Fixture check"}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-pinned" in inbound:
            yield ToolCallStart(id="s3", name="spawn")
            yield ToolCallDelta(
                id="s3",
                partial_json='{"target": "pinned", "payload": {"value": 7}}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-extended" in inbound:
            yield ToolCallStart(id="s4", name="spawn")
            yield ToolCallDelta(
                id="s4",
                partial_json='{"target": "extend", "payload": '
                '{"value": 42, "extended_context": true}}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-capped" in inbound:
            yield ToolCallStart(id="s5", name="spawn")
            yield ToolCallDelta(
                id="s5",
                partial_json='{"target": "extend", "payload": {"value": 42}}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-background" in inbound:
            yield ToolCallStart(id="s7", name="spawn")
            yield ToolCallDelta(
                id="s7",
                partial_json='{"target": "roundtrip", "payload": {"value": 21}, '
                '"background": true}',
            )
            yield Usage(input_tokens=4, output_tokens=4)
            return
        if isinstance(inbound, str) and "spawn-preload" in inbound:
            yield ToolCallStart(id="s6", name="spawn")
            yield ToolCallDelta(
                id="s6",
                partial_json='{"target": "preload", "payload": '
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


@pytest.fixture(scope="session")
def dbos_runtime(
    dbos_launched: Config,
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Config, GatingHub, FilesystemBlobStore]]:
    config = dbos_launched
    hub = GatingHub(InProcessHub(), STREAM_GATE)
    blob = FilesystemBlobStore(root=config.blob.root)
    dbos_client = replay_safe_client(config.database.system_url)
    embed = embed_backend((STUB_BACKENDS,), None, None)
    index = index_backend((STUB_BACKENDS,), None, None)
    loop_queue.reset_runtime()
    loop_queue.init_runtime(
        loop_queue.Runtime(
            config=config,
            blob=blob,
            sandboxes=ConversationSandbox(
                carrier=LocalCarrier(),
                backend="local",
                off_cluster=False,
                image_ref=SANDBOX_IMAGE_REF,
                proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
                workspace_root=tmp_path_factory.mktemp("workspaces"),
            ),
            hub=hub,
            cdp_provider=None,
            search_provider=None,
            connectors=ConnectorRegistry(entries={}),
            run_tokens=RunTokenCodec(b"turn-lifecycle-test-secret"),
            dbos=dbos_client,
            invoker_for=invoker_factory(dbos_client),
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
            async with (
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id))) as frames,
                asyncio.timeout(STREAM_TIMEOUT_SECONDS),
            ):
                async for _cursor, frame in frames:
                    match frame:
                        case TextDelta():
                            deltas.append(frame.text)
                        case Terminal():
                            return "".join(deltas), frame.frame.model_dump(mode="json")
        raise AssertionError("stream ended without a terminal frame")

    async def consume_park(self, seed: Seed, turn_id: str) -> str:
        with ws(seed.workspace_id):
            async with (
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id))) as frames,
                asyncio.timeout(STREAM_TIMEOUT_SECONDS),
            ):
                async for _cursor, frame in frames:
                    if isinstance(frame, Parked):
                        return frame.message
        raise AssertionError("stream ended without a park frame")

    async def consume_costs(self, seed: Seed, turn_id: str) -> list[dict[str, object]]:
        costs: list[dict[str, object]] = []
        with ws(seed.workspace_id):
            async with (
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id))) as frames,
                asyncio.timeout(STREAM_TIMEOUT_SECONDS),
            ):
                async for _cursor, frame in frames:
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


async def _bootstrap(model: str = "claude-opus-4-8", reasoning: ReasoningEffort = "low") -> Seed:
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
                is_admin=True,
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
                reasoning=reasoning,
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


async def _sandbox_handle(conversation_id: UUID) -> str | None:
    async with workspace_tx() as connection:
        return (
            await connection.execute(
                sa.select(tables.conversation.c.sandbox_handle).where(
                    tables.conversation.c.id == conversation_id
                )
            )
        ).scalar_one()


async def test_workspace_host_path_is_absolute_for_a_relative_workspace_root(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A relative workspace root — the default config's `./workspaces` — must still hand the
    carrier an absolute host path: Docker reads a relative `-v` source as a named volume, not a
    host directory, so a turn would otherwise fail at container create under the shipped default
    config."""
    monkeypatch.chdir(tmp_path)
    seed = await _bootstrap()
    sandboxes = ConversationSandbox(
        carrier=LocalCarrier(),
        backend="local",
        off_cluster=False,
        image_ref=SANDBOX_IMAGE_REF,
        proxy=ProxyEndpoint(port=0, ca_cert="test-ca"),
        workspace_root=Path("workspaces"),
    )
    with ws(seed.workspace_id):
        handle = (await sandboxes.open(seed.conversation_id, None, UNSIGNED_RUN_TOKEN, {})).handle
    assert handle.workspace_host_path is not None
    assert Path(handle.workspace_host_path).is_absolute()
    assert await asyncio.to_thread(Path(handle.workspace_host_path).is_dir)


async def test_member_turn_trace_joins_admission_and_names_its_stages(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One member message yields one trace: the admission SERVER span roots it, the turn span
    parents on the traceparent admission stored — the gap between them is the queue hop — and the
    stages inside the turn (claim, load, extension load, transcript, model round) are children of
    the turn span, so one waterfall attributes the turn's wall-clock. This turn answers out of its
    context, so no sandbox open is among them: the create waits for an operation that needs one."""
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(o11y.trace, "get_tracer", provider.get_tracer)
    seed = await _bootstrap()
    STREAM_GATE.arm()
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            spans = {finished.name: finished for finished in exporter.get_finished_spans()}
            if "turn" in spans:
                break
            await asyncio.sleep(0.05)
    admission, turn = spans["admission"], spans["turn"]
    assert turn.context.trace_id == admission.context.trace_id
    assert turn.parent is not None and turn.parent.span_id == admission.context.span_id
    assert turn.attributes["ufo.profile"] == "main"
    assert turn.attributes["ufo.turn_id"] == turn_id
    stages = {
        finished.name
        for finished in exporter.get_finished_spans()
        if finished.parent is not None and finished.parent.span_id == turn.context.span_id
    }
    assert {
        "turn.claim",
        "turn.load",
        "extensions.load",
        "transcript.load",
        "model.round",
    } <= stages
    assert "sandbox.open" not in {finished.name for finished in exporter.get_finished_spans()}
    assert await _sandbox_handle(seed.conversation_id) is None
    assert [event.name for event in spans["model.round"].events] == ["model.first_visible_event"]


async def test_turn_round_trip_bills_and_persists(surface: Turns) -> None:
    seed = await _bootstrap()
    SEEN_REASONING.clear()
    STREAM_GATE.arm()
    turn_id = await surface.admit(seed, "ping")
    streamed, terminal = await surface.consume(seed, turn_id)
    assert streamed == "echo:1"
    assert terminal["status"] == "done"
    assert terminal["tokens"] == 10
    assert terminal["cost_micro_usd"] == 110
    assert terminal["cache_percent"] == 0
    assert terminal["model"] == "claude-opus-4-8"
    assert terminal["reasoning"] == "low"
    assert SEEN_REASONING == ["low"]
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


async def test_a_committed_turn_records_what_git_reports_in_its_workspace(
    surface: Turns,
) -> None:
    """The projection is written by the turn, not by the tool that wrote the file: the checkout is
    changed by neither, and the scan still lands. The turn runs a command, which is what creates its
    sandbox — the scan reads the sandbox the turn already has and creates none of its own."""
    seed = await _bootstrap()
    STREAM_GATE.arm()
    runtime = loop_queue._runtime
    assert runtime is not None
    workspace = runtime.sandboxes.workspace_root / str(seed.conversation_id)
    checkout = workspace / "checkout"
    checkout.mkdir(parents=True)
    _init_repository(checkout, {"mod.py": "x = 1\n"})
    (checkout / "mod.py").write_text("x = 2\n")
    (workspace / "findings.md").write_text("what I found\n")

    turn_id = await surface.admit(seed, RUN_A_COMMAND)
    await surface.consume(seed, turn_id)

    assert await _sandbox_handle(seed.conversation_id) is not None
    scan = await _await_scan(seed.conversation_id)
    assert WorkspaceChanges.model_validate(scan) == WorkspaceChanges(
        changes=(
            WorkspaceChange(
                path="checkout/mod.py",
                patch="--- a/mod.py\n+++ b/mod.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n",
                truncated=False,
            ),
        ),
        truncated=False,
    )


async def _await_scan(conversation_id: UUID) -> object:
    """The scan is recorded once the terminal frame is already published — the member has their
    reply before the workspace is read — so a reader waits on the row rather than on the turn."""
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        while True:
            async with workspace_tx() as connection:
                scan = (
                    await connection.execute(
                        sa.select(tables.conversation_change.c.scan).where(
                            tables.conversation_change.c.conversation_id == conversation_id
                        )
                    )
                ).scalar_one_or_none()
            if scan is not None:
                return scan
            await asyncio.sleep(0.05)


def _init_repository(path: Path, files: dict[str, str]) -> None:
    for name, text in files.items():
        (path / name).write_text(text)
    for args in (
        ("init", "-q", "."),
        ("add", "-A"),
        ("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base"),
    ):
        completed = subprocess.run(
            ["git", "-C", str(path), *args], capture_output=True, text=True, check=False
        )
        assert completed.returncode == 0, completed.stderr


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


async def test_a_turn_reads_its_provider_from_the_spec_registered_for_its_model(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers: list[str] = []
    engine = loop_queue.TurnEngine

    def capture_provider(**kwargs: object) -> object:
        providers.append(str(kwargs["provider"]))
        return engine(**kwargs)

    monkeypatch.setattr(loop_queue, "TurnEngine", capture_provider)
    seed = await _bootstrap(model=PINNED_MODEL)
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert providers == [STANDIN_REGISTRY.spec(PINNED_MODEL).provider]
    assert providers != [STANDIN_REGISTRY.spec("claude-opus-4-8").provider]


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
    path, not reconstructed here.

    The redelivery is staged only once the first turn's workflow has returned: the queue partitions
    on the conversation and admits one turn at a time, and this replay reaches the worker entrypoint
    directly, so the wait is what keeps the two writers of one conversation apart here as the queue
    does in serve."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    first = await surface.admit(seed, "hi")
    await surface.consume(seed, first)
    handle = await runtime.dbos.retrieve_workflow_async(first)
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        await handle.get_result(polling_interval_sec=0.05)
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


async def _running_turn(subagent_profile: str | None = None) -> tuple[UUID, UUID]:
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
                subagent_profile=subagent_profile,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return workspace_id, turn_id


async def test_backstop_logs_the_stack_that_failed_the_setup(
    db: None, caplog: pytest.LogCaptureFixture
) -> None:
    """A setup fault records no step, so the terminal row is the only trace it leaves — and a class
    name names no call. The 2026-08-01 recovery failures surfaced as a bare `TimeoutException` with
    nothing saying which provider call hung, so the stack rides the log the write emits.

    An engine failure reaches the same backstop having already committed its terminal and logged
    its own stack, so it matches no row: the second call here stands for that turn and must log
    nothing rather than name a model or tool failure a setup fault."""
    _, turn_id = await _running_turn()

    def open_sandbox() -> None:
        raise TimeoutError("the provider stopped answering")

    try:
        open_sandbox()
    except TimeoutError as error:
        with caplog.at_level(logging.ERROR, logger="ufo"):
            await loop_queue._commit_failed_terminal(InProcessHub(), turn_id, error)
            await loop_queue._commit_failed_terminal(InProcessHub(), turn_id, error)

    (event,) = [
        record.ufo for record in caplog.records if record.getMessage() == "turn.setup_failed"
    ]
    assert event["turn_id"] == str(turn_id)
    assert event["error_class"] == "TimeoutError"
    assert "open_sandbox" in str(event["stack"])


async def test_backstop_terminal_carries_class_and_message(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failure outside the engine commits a terminal carrying the class AND the message — a bare
    class name gives the debugger and CLI nothing to act on (the 2026-07-21 wedge surfaced as a
    naked \"RuntimeError\"). No engine ran, so this write is also where the terminal is counted, and
    the second call — the turn already failed — writes and counts nothing. The count carries the
    profile off the row this write matched, so a setup fault that only ever hits subagents is
    readable as that profile's rather than as the fleet's."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(o11y.metrics, "get_meter", provider.get_meter)
    monkeypatch.setattr(o11y, "_counters", {})
    _, turn_id = await _running_turn("coding")
    await loop_queue._commit_failed_terminal(
        InProcessHub(), turn_id, RuntimeError("boom outside the engine")
    )
    await loop_queue._commit_failed_terminal(
        InProcessHub(), turn_id, RuntimeError("boom outside the engine")
    )
    (terminal,) = [
        point
        for resource in reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "ufo.turn_terminal_total"
        for point in metric.data.data_points
    ]
    assert (
        terminal.value,
        terminal.attributes["status"],
        terminal.attributes["error_class"],
        terminal.attributes["profile"],
    ) == (1, "failed", "RuntimeError", "coding")
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
        runtime.sandboxes.workspace_root,
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
    status, conversation_id = await _turn_row(str(overdue.trajectory.turn_id))
    assert status == "cancelled"
    assert await _sandbox_handle(conversation_id) is None
    handle = await runtime.dbos.retrieve_workflow_async(str(overdue.trajectory.turn_id))
    assert (await handle.get_status()).status == "CANCELLED"
    assert followup.clean


async def test_eval_timing_reads_the_engines_own_step_record_for_every_turn(
    db: None, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """The latency record is the driver reading DBOS's durable step log: the evaluated turn's rounds
    and its `spawn` dispatch, the delegated child's own rounds, and each turn's spend from
    its terminal row. The step names and the tool-use id on a dispatch step's memoized output are
    the engine's, so this is what pins `_stream_once`, `_dispatch_step` and the call-id join."""
    _, _, blob = dbos_runtime
    STREAM_GATE.reset()
    await _bootstrap()
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
    runtime = loop_queue._runtime
    assert runtime is not None
    driver = WorkspaceDriver(
        workspace_id,
        agent_id,
        "be brief",
        blob,
        runtime.dbos,
        runtime.sandboxes.workspace_root,
        poll_interval_seconds=0.05,
        workflow_wait_seconds=EVAL_FOLLOWUP_WAIT_SECONDS,
    )
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
        conversations=driver,
        outcome=driver,
        blob=blob,
        turn_steps=driver,
    )

    with ws(workspace_id):
        result = await target.run(
            CapabilityCase("delegating", "spawn-subagent", exact_scorer("unused"))
        )

    assert result.clean
    timing = result.output.timing
    assert timing is not None
    assert not timing.error
    assert timing.wall_ms > 0
    evaluated, child = timing.turns
    assert (evaluated.role, child.role) == ("evaluated", "child")
    assert result.trajectory is not None
    assert evaluated.turn_id == result.trajectory.turn_id
    async with workspace_tx() as connection:
        rows = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.terminal).where(
                    tables.turn.c.id.in_([evaluated.turn_id, child.turn_id])
                )
            )
        ).all()
        speaker_email = (
            await connection.execute(
                sa.select(tables.member.c.email)
                .join(tables.turn, tables.turn.c.speaker_member_id == tables.member.c.id)
                .where(tables.turn.c.id == evaluated.turn_id)
            )
        ).scalar_one()
        admitted_context = (
            await connection.execute(
                sa.select(tables.turn.c.context).where(tables.turn.c.id == evaluated.turn_id)
            )
        ).scalar_one()
    assert TurnContext.model_validate(admitted_context).sender == speaker_email
    spend = {row.id: TerminalFrame.model_validate(row.terminal) for row in rows}
    assert spend.keys() == {evaluated.turn_id, child.turn_id}
    assert spend[child.turn_id].tokens > 0
    for turn in timing.turns:
        assert "model round" in [step.name for step in turn.steps]
        assert turn.model_round_ms + turn.tool_call_ms + turn.unaccounted_ms == turn.span_ms
        assert turn.tokens == spend[turn.turn_id].tokens
        assert turn.cost_micro_usd == spend[turn.turn_id].cost_micro_usd
        model_steps = [step for step in turn.steps if step.kind == "model_round"]
        assert sum(step.tokens or 0 for step in model_steps) == turn.tokens
        assert sum(step.cost_micro_usd or 0 for step in model_steps) == turn.cost_micro_usd
        assert [step.number for step in turn.steps] == sorted(step.number for step in turn.steps)
    assert (evaluated.rounds, child.rounds) == (2, 1)
    assert evaluated.tool_calls == 1
    assert evaluated.tool_call_ms > 0
    assert evaluated.span_ms >= evaluated.tool_call_ms
    assert "spawn" in [step.name for step in evaluated.steps]
    assert "spawn" in [step.name for step in timing.slowest]
    assert UNNAMED_TOOL not in [step.name for step in timing.slowest]
    spawn = next(step for step in evaluated.steps if step.name == "spawn")
    assert spawn.call_id
    assert spawn.message_index is not None
    assert all(
        step.message_index is not None for step in evaluated.steps if step.kind == "model_round"
    )


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
    client = replay_safe_client(config.database.system_url)
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


async def test_a_background_child_wakes_its_parent_with_its_own_result(surface: Turns) -> None:
    """The whole chain on the real turn loop: the parent backgrounds a child and ends its turn,
    freeing the conversation's partition; the child runs on its own partition and, on committing
    its terminal, admits the parent's next turn carrying its schema-validated output. No parent
    ever holds a turn open waiting, and the result still reaches the conversation that asked."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    parent_id = await surface.admit(seed, "spawn-background")
    _, terminal = await surface.consume(seed, parent_id)
    assert terminal["status"] == "done"

    _, conversation_id = await _turn_row(parent_id)
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
        speaker_member_id=parent_row.speaker_member_id,
        on_behalf_of_member_id=parent_row.on_behalf_of_member_id,
        created_at=parent_row.created_at,
        terminal=TerminalFrame.model_validate(parent_row.terminal),
    )
    subagents = Subagents(
        client=runtime.dbos,
        registry=runtime.subagents,
        parent=parent,
        audience=conversation_audience(None),
    )
    (finished,) = await subagents.wait((child_id,))
    assert finished.status == "done"

    handle = await runtime.dbos.retrieve_workflow_async(str(child_id))
    async with asyncio.timeout(STREAM_TIMEOUT_SECONDS):
        assert await handle.get_result(polling_interval_sec=WORKFLOW_POLL_SECONDS) == "done"

    async def _woken() -> list[sa.Row[tuple[int, str, str]]]:
        async with workspace_tx() as connection:
            return list(
                (
                    await connection.execute(
                        sa.select(
                            tables.turn.c.seq,
                            tables.turn.c.inbound,
                            tables.turn.c.admission_source,
                        )
                        .where(
                            tables.turn.c.conversation_id == conversation_id,
                            tables.turn.c.id != UUID(parent_id),
                        )
                        .order_by(tables.turn.c.seq)
                    )
                ).all()
            )

    woken = await _woken()
    assert [row.seq for row in woken] == [2]
    assert woken[0].admission_source == "internal"
    assert f'spawn_id="{child_id}"' in woken[0].inbound
    assert '{"echoed":21}' in woken[0].inbound


async def test_a_foreground_child_does_not_also_arrive_as_a_message(surface: Turns) -> None:
    """A parent that awaited its child already holds the answer as the spawn's tool result.
    Delivering it again would put the same output in the window twice, so an awaited child
    declares no delivery and the parent's conversation stays at the one turn it ran."""
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    _, conversation_id = await _turn_row(parent)
    async with workspace_tx() as connection:
        arrivals = (
            await connection.execute(
                sa.select(tables.inbound_message.c.body).where(
                    tables.inbound_message.c.conversation_id == conversation_id
                )
            )
        ).all()
        turns = (
            (
                await connection.execute(
                    sa.select(tables.turn.c.seq).where(
                        tables.turn.c.conversation_id == conversation_id
                    )
                )
            )
            .scalars()
            .all()
        )
    assert [row.body for row in arrivals] == [], f"DUPLICATE ARRIVALS: {[r.body for r in arrivals]}"
    assert sorted(turns) == [1], f"EXTRA TURNS: {sorted(turns)}"


async def test_a_child_whose_profile_vanished_names_it_in_the_setup_failure(
    surface: Turns, caplog: pytest.LogCaptureFixture
) -> None:
    """Issue #1721: two children died in setup under `UnknownSubagentProfile`, and the telemetry the
    failure left named a class and a stack — not the profile that failed, and not what the fleet was
    registered to hold. A registry that shrank under an admitted child is the one way a child still
    reaches setup with a name nothing resolves, so the resolution logs both facts, and the terminal
    the backstop commits carries them to whoever awaits the child."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    parent_id, child_conversation, child_id = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=parent_id,
                workspace_id=seed.workspace_id,
                conversation_id=seed.conversation_id,
                agent_id=seed.agent_id,
                seq=1,
                status="running",
                inbound="parent",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=child_conversation,
                workspace_id=seed.workspace_id,
                agent_id=seed.agent_id,
                surface="subagent",
                queue_key=str(child_id),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.turn).values(
                id=child_id,
                workspace_id=seed.workspace_id,
                conversation_id=child_conversation,
                agent_id=seed.agent_id,
                seq=1,
                status="queued",
                inbound='{"value": 1}',
                parent_turn_id=parent_id,
                subagent_profile="vanished",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    with ws(seed.workspace_id), caplog.at_level(logging.ERROR, logger="ufo"):
        assert await loop_queue._run_turn(runtime, str(child_id)) == "failed"
    (event,) = [
        record.ufo
        for record in caplog.records
        if record.getMessage() == "turn.unknown_subagent_profile"
    ]
    assert event["turn_id"] == str(child_id)
    assert event["requested_profile"] == "vanished"
    assert "roundtrip" in str(event["registered_profiles"])
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == child_id
                )
            )
        ).one()
    assert row.status == "failed"
    assert row.terminal["error_class"] == "UnknownSubagentProfile"
    assert "vanished" in row.terminal["error_message"]
    assert "roundtrip" in row.terminal["error_message"]


async def test_subagent_runs_at_the_parent_agents_reasoning_effort(surface: Turns) -> None:
    """A profile names a model but never an effort, so the child inherits the parent agent's row —
    every round of both turns asks the model for the seeded effort, not the record default."""
    seed = await _bootstrap(reasoning="medium")
    SEEN_REASONING.clear()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    assert SEEN_REASONING
    assert set(SEEN_REASONING) == {"medium"}


async def test_subagent_profile_reasoning_overrides_the_parent_agent(surface: Turns) -> None:
    seed = await _bootstrap(reasoning="low")
    SEEN_REASONING.clear()
    parent = await surface.admit(seed, "spawn-pinned")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    assert SEEN_REASONING.count("high") == 1
    assert SEEN_REASONING.count("low") >= 1


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
    parent_terminal = TerminalFrame.model_validate(terminal)
    child_terminal = TerminalFrame.model_validate(child.terminal)
    assert parent_terminal.incomplete_reason is None
    assert child_terminal.incomplete_reason == "round_budget"
    assert RoundTripOutput.model_validate_json(child_terminal.text).echoed == 99


async def test_a_childs_activity_mirrors_onto_the_parents_stream(surface: Turns) -> None:
    """A surface tails the admitted turn's stream and nothing else, so the child engine mirrors
    its member-facing moments there as SubagentActivity: the run starting, each dispatch it makes,
    and the terminal that ends its row — every frame stamped with the run's identity and the
    display name the spawn gave it, which also lands on the child turn and names its
    conversation."""
    seed = await _bootstrap()
    _, hub, _ = _runtime_parts()
    parent = await surface.admit(seed, "spawn-exhaust")

    async def _collect() -> list[SubagentActivity]:
        collected: list[SubagentActivity] = []
        async for _cursor, frame in hub.subscribe(UUID(parent)):
            if isinstance(frame, SubagentActivity):
                collected.append(frame)
            if isinstance(frame, Terminal):
                break
        return collected

    collector = asyncio.ensure_future(_collect())
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(
                    tables.turn.c.id,
                    tables.turn.c.conversation_id,
                    tables.turn.c.subagent_name,
                ).where(tables.turn.c.parent_turn_id == UUID(parent))
            )
        ).one()
        title = (
            await connection.execute(
                sa.select(tables.conversation.c.title).where(
                    tables.conversation.c.id == child.conversation_id
                )
            )
        ).scalar_one()
    assert child.subagent_name == "Fixture check"
    assert title == "Fixture check"
    runs = await asyncio.wait_for(collector, STREAM_TIMEOUT_SECONDS)
    assert [frame.status for frame in runs] == ["", "", "done"]
    started, worked, _done = runs
    assert started.activity == ""
    assert worked.activity == "Running a check."
    assert {frame.turn_id for frame in runs} == {child.id}
    assert {frame.parent_turn_id for frame in runs} == {UUID(parent)}
    assert {frame.conversation_id for frame in runs} == {child.conversation_id}
    assert {frame.profile for frame in runs} == {"exhaust"}
    assert {frame.name for frame in runs} == {"Fixture check"}


async def test_subagent_bills_under_its_profile_model_not_the_parents(
    surface: Turns,
) -> None:
    """An escalation rung ships as a sibling profile: `pinned` and `roundtrip` answer the same
    input and output contract, and the pinned one alone moves the model. So the second half here
    bills an unpinned sibling under the parent's model on the same run."""
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

    sibling_parent = await surface.admit(seed, "spawn-subagent")
    _, sibling_terminal = await surface.consume(seed, sibling_parent)
    assert sibling_terminal["status"] == "done"
    async with workspace_tx() as connection:
        sibling = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.subagent_profile).where(
                    tables.turn.c.parent_turn_id == UUID(sibling_parent)
                )
            )
        ).one()
        sibling_model = (
            await connection.execute(
                sa.select(tables.ledger.c.model).where(tables.ledger.c.turn_id == sibling.id)
            )
        ).scalar_one()
    assert sibling.subagent_profile == "roundtrip"
    assert ROUNDTRIP_PROFILE.model is None
    assert PINNED_PROFILE.output_model is ROUNDTRIP_PROFILE.output_model
    assert sibling_model == "claude-opus-4-8"


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
    """A subagent whose payload carries `preload_skills` starts with the skill written into the
    workspace it runs in and its instructions already in the system prompt — no `load_skill` round
    needed. That workspace is the spawning turn's, since a subagent reuses its sandbox: the skill
    lands under the parent's conversation and the child's own never gets a directory."""
    runtime = loop_queue._runtime
    assert runtime is not None
    skill = runtime.skills.named("sandbox")
    SEEN_SYSTEM_PROMPTS.clear()
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-preload")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    assert await _child_echo(parent) == 5
    async with workspace_tx() as connection:
        child_conversation = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id).where(
                    tables.turn.c.parent_turn_id == UUID(parent)
                )
            )
        ).scalar_one()
        parent_conversation = (
            await connection.execute(
                sa.select(tables.turn.c.conversation_id).where(tables.turn.c.id == UUID(parent))
            )
        ).scalar_one()
    assert child_conversation != parent_conversation
    skill_md = (
        runtime.sandboxes.workspace_root
        / str(parent_conversation)
        / f".skills/{skill.name}/SKILL.md"
    )
    assert await asyncio.to_thread(skill_md.read_bytes) == skill.raw_skill_md.encode()
    assert not (runtime.sandboxes.workspace_root / str(child_conversation)).exists()
    assert any(skill.instructions in system for system in SEEN_SYSTEM_PROMPTS)


async def test_subagent_plain_text_followup_runs_without_a_spawn_payload(
    surface: Turns,
) -> None:
    """`message_spawn` stores free text as the follow-up turn's inbound — only the child's
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
        speaker_member_id=parent_row.speaker_member_id,
        on_behalf_of_member_id=parent_row.on_behalf_of_member_id,
        created_at=parent_row.created_at,
        terminal=TerminalFrame.model_validate(parent_row.terminal),
    )
    subagents = Subagents(
        client=runtime.dbos,
        registry=runtime.subagents,
        parent=parent,
        audience=conversation_audience(None),
    )
    queued = await subagents.message(
        child_id, FOLLOWUP_INBOUND, dedup_key="turn-1/message_spawn/call-1"
    )
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
    main_offers = [names for names in SEEN_TOOLS if "spawn" in names]
    child_offers = [names for names in SEEN_TOOLS if "hidden_probe" in names]
    assert main_offers
    assert all("hidden_probe" not in names for names in main_offers)
    assert child_offers
    assert all("spawn" not in names for names in child_offers)
