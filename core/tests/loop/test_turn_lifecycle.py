import asyncio
import json
import logging
import subprocess
import threading
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing, asynccontextmanager
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncConnection
from ufo_ext_context_compact import manifest as compact_manifest_module
from ufo_ext_context_rollover import manifest as rollover_manifest_module
from ufo_ext_context_rollover import rollover as rollover_module
from ufo_ext_index_default import DefaultIndex
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

from evals.driver import WorkspaceDriver
from evals.harness.capability import CapabilityCase
from evals.harness.scorers import exact_scorer
from evals.harness.target import InProcessTarget
from evals.harness.timing import UNNAMED_TOOL
from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness import o11y
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import (
    ANTHROPIC_KEY_SLOT,
    CORE_MODEL_SPECS,
    CORE_PRICING,
    OPENAI_KEY_SLOT,
)
from ufo.harness.models.grant import Grant
from ufo.harness.models.interface import (
    PROVIDER_ANTHROPIC,
    Message,
    ModelEvent,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
)
from ufo.harness.models.registry import ModelRegistry, ServingModel
from ufo.harness.models.spec import RepeatedToolRollover
from ufo.harness.sandbox.conversation import (
    SANDBOX_IMAGE_REF,
    UNSIGNED_RUN_TOKEN,
    ConversationSandbox,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    ProxyEndpoint,
    RunTokenCodec,
    SandboxProviderUnavailable,
)
from ufo.host import assemble as host_assemble
from ufo.host.assemble import HostEnvironment
from ufo.host.environment import store_environment_document, store_environment_file
from ufo.host.ext.loader import embed_backend, index_backend, skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime import workspace as workspace_module
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.credentials import CredentialStore, member_slot
from ufo.runtime.billing.balance import balance_park_message, credit, debit
from ufo.runtime.engine import (
    EMPTY_RESPONSE_NUDGE,
    FINISH_TOOL,
    SANDBOX_PROVIDER_RETRY_SECONDS,
    TRUNCATION_FEEDBACK,
)
from ufo.runtime.ext.context import Trajectory, context_for
from ufo.runtime.ext.manifest import (
    EmbedBackendSpec,
    IndexBackendSpec,
    Manifest,
    NotRegisteredError,
)
from ufo.runtime.hub import CostTick, Hub, InProcessHub, Parked, SubagentActivity, Terminal
from ufo.runtime.jobs import TurnDispatcher
from ufo.runtime.seats import create_member
from ufo.runtime.subagents import FINISH_CONTRACT, SubagentProfile, SubagentRegistry, Subagents
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.surfaces.admission import Admission, AdmissionInvoker, MemberAdmission
from ufo.runtime.tools.context import SPAWN_CONNECT_PATH, TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import ACTIVITY_PROMPT
from ufo.runtime.turns.audience import conversation_audience
from ufo.runtime.turns.transcript import Conversation, ParkedTurn
from ufo.runtime.turns.workspace_changes import WorkspaceChange, WorkspaceChanges
from ufo.runtime.workspace import init_workspace_credentials, ws, ws_current
from ufo.schema import tables
from ufo.schema.records import (
    INTENT_ADMISSION,
    MEMBER_ADMISSION,
    SCHEDULED_ADMISSION,
    ModelAccountCapability,
    ReasoningEffort,
    TerminalFrame,
    Turn,
    TurnContext,
    TurnRuntimeConfig,
    Usage,
)

pytestmark = [
    pytest.mark.usefixtures("database_url"),
    pytest.mark.parametrize("database_url", ["sqlite"], indirect=True),
]

STREAM_TIMEOUT_SECONDS = 30
HOLD_RELEASE = threading.Event()
HOLD_TWO_STARTED = threading.Event()
SLOW_ROUND_STARTED = threading.Event()
HOLD_STARTED: list[str] = []
TRUNCATION_MESSAGE = (
    "Anthropic completion truncated at the max_tokens budget (stop_reason=max_tokens)"
)
STREAM_GATE = StreamGate()
SEEN_SYSTEM_PROMPTS: list[str] = []
SEEN_TOOLS: list[tuple[str, ...]] = []
SEEN_TOOL_DESCRIPTIONS: list[dict[str, str]] = []
SEEN_SPAWN_PAYLOAD: list[str] = []
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
            binds_member_authority=False,
        ),
    ),
)

INSTALLED_MANIFESTS = (
    STUB_BACKENDS,
    compact_manifest_module.manifest(),
    rollover_manifest_module.manifest(),
)
"""The extension set a turn here runs against. Core registers no context strategy, and the
config's default is `compact`, so the compaction provider must be installed for a turn to
reach its boundary at all; `rollover` rides along so tests that name it explicitly resolve."""


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
        SEEN_TOOL_DESCRIPTIONS.append({tool.name: tool.description for tool in request.tools})
        SEEN_SPAWN_PAYLOAD.extend(
            str(tool.input_schema["properties"]["payload"]["description"])
            for tool in request.tools
            if tool.name == "spawn"
        )
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
        if isinstance(inbound, str) and "hold-slot" in inbound:
            HOLD_STARTED.append(inbound)
            if len(HOLD_STARTED) >= 2:
                HOLD_TWO_STARTED.set()
            await asyncio.to_thread(HOLD_RELEASE.wait)
            yield TextDelta(text="held")
            yield Usage(input_tokens=2, output_tokens=1)
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
            SLOW_ROUND_STARTED.set()
            await asyncio.sleep(30)
        if "mute" in inbound or ("shy" in inbound and not nudged):
            yield Usage(input_tokens=5)
            return
        yield TextDelta(text="echo:")
        yield TextDelta(text=str(len(request.messages)))
        yield Usage(input_tokens=7, output_tokens=3)


STANDIN_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(
            spec,
            client=lambda spec, key: StandInModel(),
            key_slot="",
            key_env="",
            rollover_trigger_tokens=(
                123_000 if spec.id == PINNED_MODEL else spec.rollover_trigger_tokens
            ),
            repeated_tool_rollover=(
                RepeatedToolRollover(consecutive_turns=4, trigger_percent=50)
                if spec.id == PINNED_MODEL
                else spec.repeated_tool_rollover
            ),
        )
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
            manifests=INSTALLED_MANIFESTS,
            environment=HostEnvironment(
                manifests=INSTALLED_MANIFESTS,
                credentials=None,
                index=index,
                embed=embed,
                blob=blob,
            ),
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

    async def admit(
        self,
        seed: Seed,
        body: str,
        idempotency_key: str | None = None,
        runtime_config: TurnRuntimeConfig | None = None,
    ) -> str:
        admitted = await MemberAdmission(
            admission=self.admission, workspace_id=seed.workspace_id
        ).admit(
            seed.conversation_id,
            body,
            idempotency_key=idempotency_key,
            speaker_member_id=seed.member_id,
            runtime_config=runtime_config,
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
        "environment.assemble",
        "transcript.load",
        "model.round",
    } <= stages
    assert "sandbox.open" not in {finished.name for finished in exporter.get_finished_spans()}
    assert await _sandbox_handle(seed.conversation_id) is None
    assert spans["model.round"].attributes["ufo.round"] == 0
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
    render = host_assemble.render_system_prompt

    def capture_knowledge_cutoff(*args: object, **kwargs: object) -> object:
        cutoffs.append(str(kwargs["knowledge_cutoff"]))
        return render(*args, **kwargs)

    monkeypatch.setattr(host_assemble, "render_system_prompt", capture_knowledge_cutoff)
    seed = await _bootstrap(model=PINNED_MODEL)
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert cutoffs == [STANDIN_REGISTRY.spec(PINNED_MODEL).knowledge_cutoff]
    assert cutoffs != [STANDIN_REGISTRY.spec("claude-opus-4-8").knowledge_cutoff]


async def test_turn_rollover_uses_the_models_policy(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    windows: list[int] = []
    triggers: list[int | None] = []
    repeated: list[RepeatedToolRollover | None] = []
    rollover = rollover_module.ContextRollover

    def capture_context_window(**kwargs: object) -> object:
        spec = cast(ServingModel, kwargs["serving"]).spec
        windows.append(spec.context_window)
        triggers.append(spec.rollover_trigger_tokens)
        repeated.append(spec.repeated_tool_rollover)
        return rollover(**kwargs)

    monkeypatch.setattr(rollover_manifest_module, "ContextRollover", capture_context_window)
    seed = await _bootstrap(model=PINNED_MODEL)
    turn_id = await surface.admit(seed, "ping")
    _, terminal = await surface.consume(seed, turn_id)
    assert terminal["status"] == "done"
    assert windows == [STANDIN_REGISTRY.spec(PINNED_MODEL).context_window]
    assert windows != [STANDIN_REGISTRY.spec("claude-opus-4-8").context_window]
    assert triggers == [STANDIN_REGISTRY.spec(PINNED_MODEL).rollover_trigger_tokens]
    assert triggers != [STANDIN_REGISTRY.spec("claude-opus-4-8").rollover_trigger_tokens]
    assert repeated == [STANDIN_REGISTRY.spec(PINNED_MODEL).repeated_tool_rollover]
    assert repeated != [STANDIN_REGISTRY.spec("claude-opus-4-8").repeated_tool_rollover]


async def test_a_turn_reads_its_provider_from_the_spec_registered_for_its_model(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    providers: list[str] = []
    engine = loop_queue.TurnEngine

    def capture_provider(**kwargs: object) -> object:
        providers.append(cast(ServingModel, kwargs["serving"]).spec.provider)
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
                running_attempt=str(turn_id),
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
            await loop_queue._commit_failed_terminal(InProcessHub(), turn_id, str(turn_id), error)
            await loop_queue._commit_failed_terminal(InProcessHub(), turn_id, str(turn_id), error)

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
        InProcessHub(), turn_id, str(turn_id), RuntimeError("boom outside the engine")
    )
    await loop_queue._commit_failed_terminal(
        InProcessHub(), turn_id, str(turn_id), RuntimeError("boom outside the engine")
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


async def test_stale_setup_failure_cannot_end_the_live_attempt(db: None) -> None:
    _, turn_id = await _running_turn()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .values(running_attempt="live-attempt")
            .where(tables.turn.c.id == turn_id)
        )

    await loop_queue._commit_failed_terminal(
        InProcessHub(),
        turn_id,
        "stale-attempt",
        RuntimeError("stale attempt failed during setup"),
    )

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.turn.c.terminal).where(
                    tables.turn.c.id == turn_id
                )
            )
        ).one()
    assert row.status == "running"
    assert row.terminal is None


OWN_ACCOUNT_MODEL = "gpt-5.6-sol"
BACKGROUND_MODEL = "gpt-5.6-luna"

OWN_ACCOUNT_PROFILE = SubagentProfile(
    name="own_account",
    prompt="OWN ACCOUNT: run on the provider account the member connected.",
    tool_names=(),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
    own_key_models={"openai": OWN_ACCOUNT_MODEL},
    needs_own_model_key=True,
)


async def test_only_a_turn_that_needs_the_members_account_runs_on_it(
    db: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A turn carrying an exact member-account capability reads that key and is billed as the
    workspace's own spend. The same member's ordinary turn carries no account, reads the deploy's
    key, and is billed to the platform. Neither turn reaches the admin's own key."""
    workspace_id, turn_id = await _running_turn()
    async with workspace_tx() as connection:
        admin = await create_member(connection, workspace_id, "admin@work.com", is_admin=True)
        member = await create_member(connection, workspace_id, "member@work.com")
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(speaker_member_id=member)
        )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    init_workspace_credentials(store)
    monkeypatch.setenv("OPENAI_API_KEY", "platform-default")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, admin), "admin-key")
    await store.put(workspace_id, member_slot(OPENAI_KEY_SLOT, member), "member-key")
    served: list[tuple[str, bool]] = []
    background: list[str] = []

    async def record(_runtime: object, running: str) -> str:
        served.append(
            (
                await ws_current().credential(OPENAI_KEY_SLOT, None, OWN_ACCOUNT_MODEL),
                await loop_queue._frozen_byok(
                    UUID(running),
                    await ws_current().credential_is_stored(OPENAI_KEY_SLOT, OWN_ACCOUNT_MODEL),
                    f"attempt-{len(served)}",
                ),
            )
        )
        background.append(await ws_current().credential(OPENAI_KEY_SLOT, None, BACKGROUND_MODEL))
        return "done"

    async def no_provisions(_runtime: object, _workspace_id: UUID) -> None:
        return None

    monkeypatch.setattr(loop_queue, "_run_turn", record)
    monkeypatch.setattr(loop_queue, "_apply_provisions", no_provisions)
    monkeypatch.setattr(
        loop_queue,
        "_runtime",
        SimpleNamespace(
            hub=InProcessHub(),
            subagents=SubagentRegistry((OWN_ACCOUNT_PROFILE,)),
            registry=SimpleNamespace(resolve=lambda model: model),
        ),
    )

    assert await loop_queue._execute_turn(str(workspace_id), str(turn_id)) == "done"

    async with workspace_tx() as connection:
        await connection.execute(
            sa.update(tables.turn)
            .where(tables.turn.c.id == turn_id)
            .values(
                subagent_profile=OWN_ACCOUNT_PROFILE.name,
                model_accounts=[
                    ModelAccountCapability(
                        provider="openai",
                        slot=member_slot(OPENAI_KEY_SLOT, member),
                    ).model_dump(mode="json")
                ],
            )
        )
    assert await loop_queue._execute_turn(str(workspace_id), str(turn_id)) == "done"

    assert served == [("platform-default", False), ("member-key", True)]
    """The deploy's own background work inside the very turn the member's account serves still
    reads the deploy's key: the binding covers the models that account was connected for, and the
    summarizer's is not one of them."""
    assert background == ["platform-default", "platform-default"]
    async with workspace_tx() as connection:
        byok = (
            await connection.execute(
                sa.select(tables.turn.c.byok).where(tables.turn.c.id == turn_id)
            )
        ).scalar_one()
    assert byok is True


class ConnectedAccountModel:
    """One finishing round of a fixed size, so what the ledger says about the turn is decided by
    the account that served it and by nothing else."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield ToolCallStart(id="p1", name=FINISH_TOOL)
        yield ToolCallDelta(id="p1", partial_json=json.dumps({"echoed": 1}))
        yield Usage(input_tokens=5, output_tokens=5)


PLAN_ACCOUNT_MODEL = "claude-opus-4-8"

PLAN_ACCOUNT_PROFILE = SubagentProfile(
    name="plan_account",
    prompt="Run on the provider account the member connected.",
    tool_names=(),
    input_model=RoundTripInput,
    output_model=RoundTripOutput,
    own_key_models={"anthropic": PLAN_ACCOUNT_MODEL},
    needs_own_model_key=True,
)

KEYED_REGISTRY = ModelRegistry(
    specs={
        spec.id: replace(
            spec,
            client=lambda spec, key: ConnectedAccountModel(),
            key_slot=ANTHROPIC_KEY_SLOT,
            key_env="",
        )
        for spec in CORE_MODEL_SPECS
    },
    pricing=CORE_PRICING,
    auto_model=PLAN_ACCOUNT_MODEL,
)


async def test_a_turn_a_members_plan_serves_costs_nothing_and_one_on_their_key_costs_its_rate(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The two accounts a member can connect are metered on different terms, and the same turn on
    each is what tells them apart. A grant serves the turn under a subscription its holder already
    bought, so its tokens are recorded against no money at all; a pasted API key is metered by the
    provider, so its tokens carry their real rate. Neither debits the workspace, which is why
    pricing both against the rate card would put a number on the member's screen that nobody is
    owed."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    monkeypatch.setattr(workspace_module, "_store", store)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "platform-default")
    monkeypatch.setattr(
        loop_queue,
        "_runtime",
        replace(
            runtime,
            registry=KEYED_REGISTRY,
            subagents=SubagentRegistry((PLAN_ACCOUNT_PROFILE,)),
        ),
    )

    async def spend(email: str, connected: str) -> sa.Row[tuple[int, int, int]]:
        conversation_id, turn_id = uuid4(), uuid4()
        async with workspace_tx() as connection:
            member = await create_member(connection, seed.workspace_id, email)
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=conversation_id,
                    workspace_id=seed.workspace_id,
                    agent_id=seed.agent_id,
                    surface="subagent",
                    queue_key=str(turn_id),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
            await connection.execute(
                sa.insert(tables.turn).values(
                    id=turn_id,
                    workspace_id=seed.workspace_id,
                    conversation_id=conversation_id,
                    agent_id=seed.agent_id,
                    seq=1,
                    status="queued",
                    inbound='{"value": 1}',
                    subagent_profile=PLAN_ACCOUNT_PROFILE.name,
                    model_accounts=[
                        ModelAccountCapability(
                            provider=PROVIDER_ANTHROPIC,
                            slot=member_slot(ANTHROPIC_KEY_SLOT, member),
                        ).model_dump(mode="json")
                    ],
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        await store.put(seed.workspace_id, member_slot(ANTHROPIC_KEY_SLOT, member), connected)
        assert await loop_queue._execute_turn(str(seed.workspace_id), str(turn_id)) == "done"
        async with workspace_tx() as connection:
            return (
                await connection.execute(
                    sa.select(
                        tables.ledger.c.amount,
                        tables.ledger.c.priced_micro_usd,
                        tables.ledger.c.debited_micro_usd,
                    ).where(tables.ledger.c.turn_id == turn_id)
                )
            ).one()

    grant = Grant(access="oat-token", refresh="refresh", expires_at=time.time() + 3600)
    on_plan = await spend("planned@work.com", grant.stored())
    on_key = await spend("keyed@work.com", "sk-ant-api-pasted")

    assert on_plan.amount == on_key.amount == 10
    assert on_plan.priced_micro_usd == 0
    assert on_key.priced_micro_usd > 0
    assert on_plan.debited_micro_usd == on_key.debited_micro_usd == 0


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


class _SettleAfterRoundStarts(WorkspaceDriver):
    """A driver whose settle deadline starts once the turn is streaming its first model round.
    The deadline otherwise races the turn's own start — the claim, the arrival drain and the
    round's setup all run inside it."""

    async def settle(self, conversation_id: UUID, turn_id: UUID) -> Trajectory | None:
        await asyncio.to_thread(SLOW_ROUND_STARTED.wait, STREAM_TIMEOUT_SECONDS)
        return await super().settle(conversation_id, turn_id)


async def test_eval_settle_deadline_cancels_the_turn_before_the_runner_advances(
    db: None, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """A case whose model round outlives the eval driver's wait must not leak a running turn into
    the next case: settle's deadline commits the cancelled terminal and durably cancels the DBOS
    workflow before returning, the harness records the case as an infrastructure failure, and the
    following case runs clean on a turn whose predecessor is already terminal. The record also
    carries what the harness archives that failure under: the status the cancel wrote over, and
    that the turn had reached the engine — a turn cancelled before its first step holds `running`
    from the dispatch claim alone and its expiry belongs to the rig."""
    _, _, blob = dbos_runtime
    STREAM_GATE.reset()
    SLOW_ROUND_STARTED.clear()
    await _bootstrap()
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
    runtime = loop_queue._runtime
    assert runtime is not None
    overdue_driver = _SettleAfterRoundStarts(
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
        turn_steps=overdue_driver,
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
    assert overdue.expiry_status == "running"
    assert overdue.work_started
    status, conversation_id = await _turn_row(str(overdue.trajectory.turn_id))
    assert status == "cancelled"
    assert await _sandbox_handle(conversation_id) is None
    handle = await runtime.dbos.retrieve_workflow_async(str(overdue.trajectory.turn_id))
    assert (await handle.get_status()).status == "CANCELLED"
    assert followup.clean


async def test_eval_reads_a_setup_faults_class_off_the_turn_row(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fault in turn setup writes a terminal frame and no transcript at all, so the harness's
    reconstruction reads `turn produced no terminal transcript` — the one reason every case of the
    2026-09-09 nightly carried while the frame beside it named the class and the message. The
    reason stays what it is, and the class and message ride the result and the trajectory, so a red
    case says which fault it stood for and a provider-owned transient in setup is still excluded."""
    _, _, blob = dbos_runtime
    STREAM_GATE.reset()
    await _bootstrap()
    async with workspace_tx() as connection:
        workspace_id = (await connection.execute(sa.select(tables.workspace.c.id))).scalar_one()
        agent_id = (await connection.execute(sa.select(tables.agent.c.id))).scalar_one()
    runtime = loop_queue._runtime
    assert runtime is not None

    async def refuse_the_boundary(*_args: object, **_kwargs: object) -> object:
        raise NotRegisteredError("config selects context strategy 'rollover'")

    monkeypatch.setattr(loop_queue, "flagged_context_boundary", refuse_the_boundary)
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
    )

    with ws(workspace_id):
        result = await target.run(CapabilityCase("setup-fault", "ping", exact_scorer("unused")))

    assert not result.clean
    assert result.failure_reason == "turn produced no terminal transcript"
    assert result.error_class == "NotRegisteredError"
    assert "rollover" in result.error_message
    assert result.trajectory is not None
    assert result.trajectory.status == "failed"
    assert "NotRegisteredError" in result.trajectory.error


async def test_a_cancelled_turn_still_names_the_status_the_wait_expired_on(
    db: None, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """`cancel_one_turn` commits `cancelled` over `queued`, `parked` and `running` alike, so once
    the cancel lands the row cannot say who held the turn when the eval driver's wait expired. The
    driver reads that status first and keeps it: a turn still behind our own workers reads back
    `queued`, and the nightly cohort gate needs that to refuse the exclusion as ours instead of
    archiving it as provider weather."""
    _, _, blob = dbos_runtime
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    driver = WorkspaceDriver(
        seed.workspace_id,
        seed.agent_id,
        "be brief",
        blob,
        runtime.dbos,
        runtime.sandboxes.workspace_root,
    )
    turn_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=seed.workspace_id,
                conversation_id=seed.conversation_id,
                agent_id=seed.agent_id,
                seq=1,
                status="queued",
                inbound="slow",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )

    with ws(seed.workspace_id):
        assert await driver.cancel(turn_id)

    status, _ = await _turn_row(str(turn_id))
    assert status == "cancelled"
    assert driver.cancelled_from(turn_id) == "queued"
    assert driver.cancelled_from(uuid4()) is None


async def test_eval_driver_opens_as_the_named_speaker_else_the_founding_admin(
    db: None, dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore]
) -> None:
    """A run against a real workspace speaks as the member running it, never as whichever admin was
    seated first: `speaker` is the fallback for a case that names no member, and the conversation
    it opens belongs to that member."""
    _, _, blob = dbos_runtime
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    colleague_id = uuid4()
    colleague = f"{colleague_id.hex[:8]}@example.com"
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=colleague_id,
                workspace_id=seed.workspace_id,
                email=colleague,
                is_admin=False,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    driver = WorkspaceDriver(
        seed.workspace_id,
        seed.agent_id,
        "be brief",
        blob,
        runtime.dbos,
        runtime.sandboxes.workspace_root,
    )
    with ws(seed.workspace_id):
        default_conversation = await driver.open("default")
        named_conversation = await replace(driver, speaker=colleague).open("named")
    async with workspace_tx() as connection:
        owners = dict(
            (
                await connection.execute(
                    sa.select(tables.conversation.c.id, tables.conversation.c.member_id).where(
                        tables.conversation.c.id.in_((default_conversation, named_conversation))
                    )
                )
            ).all()
        )
    assert owners == {default_conversation: seed.member_id, named_conversation: colleague_id}


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


async def test_a_spent_balance_holds_member_messages_then_answers_them_on_credit(
    surface: Turns,
) -> None:
    """The whole chain a refill has to wake: a member's message parks at the door with the hold
    sentence on its stream, the next message joins that turn instead of founding one, the sweep
    holds while the balance is spent, and once credit lands it enqueues the one turn, which runs
    and answers both messages."""
    seed = await _bootstrap()
    dollar = 1_000_000
    first = await surface.admit(seed, "ping")
    _, first_terminal = await surface.consume(seed, first)
    assert first_terminal["status"] == "done"
    async with workspace_tx() as connection:
        await credit(connection, seed.workspace_id, dollar, dollar, "seed")
        await debit(connection, seed.workspace_id, 2 * dollar)
    held = await surface.admit(seed, "again")
    assert await surface.consume_park(seed, held) == balance_park_message(None)
    assert await surface.admit(seed, "and more") == held
    status, conversation_id = await _turn_row(held)
    assert status == "parked"
    assert loop_queue._runtime is not None
    with ws(seed.workspace_id):
        await TurnDispatcher(client=loop_queue._runtime.dbos).run()
    status, _ = await _turn_row(held)
    assert status == "parked"
    async with workspace_tx() as connection:
        await credit(connection, seed.workspace_id, 20 * dollar, 20 * dollar, "top-up")
    with ws(seed.workspace_id):
        await TurnDispatcher(client=loop_queue._runtime.dbos).run()
    await _await_status(held, "done")
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
    assert turns == 2
    assert unconsumed == 0
    _, _, blob = _runtime_parts()
    stored = await _read_transcript(blob, conversation_id, 2)
    bodies = _bodies(stored)
    assert "again" in bodies
    assert "and more" in bodies
    assert str(bodies[-1]).startswith("echo:")


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


async def test_the_spawn_offer_shows_the_turns_targets_and_their_keys(surface: Turns) -> None:
    """The keys reach the model, not only the catalog document. A caller cannot read a schema it
    was never handed, and an agent whose prompt forbids loading a skill has no second place to
    look — which is how a review app sent twelve spawns with no payload at all."""
    SEEN_SPAWN_PAYLOAD.clear()
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)

    assert terminal["status"] == "done"
    assert SEEN_SPAWN_PAYLOAD
    for described in SEEN_SPAWN_PAYLOAD:
        assert described.startswith("Arguments matching the target's input schema")
        assert "roundtrip takes `value`" in described


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
    assert worked.activity == "Running a check"
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


async def test_runtime_config_model_overrides_the_parent_and_profile_models(
    surface: Turns,
) -> None:
    seed = await _bootstrap()
    runtime_config = TurnRuntimeConfig(model="claude-sonnet-5")
    parent = await surface.admit(seed, "spawn-pinned", runtime_config=runtime_config)
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.runtime_config).where(
                    tables.turn.c.parent_turn_id == UUID(parent)
                )
            )
        ).one()
        models = (
            await connection.execute(
                sa.select(tables.ledger.c.model)
                .where(tables.ledger.c.turn_id.in_((UUID(parent), child.id)))
                .order_by(tables.ledger.c.turn_id)
            )
        ).scalars()
    assert set(models) == {"claude-sonnet-5"}
    assert TurnRuntimeConfig.model_validate(child.runtime_config) == runtime_config.model_copy(
        update={"connections": ()}
    )


OVERRIDDEN_PROMPT = "OVERRIDDEN: answer as the environment document rewrote you."
OVERRIDDEN_BASH = "OVERRIDDEN: the bash description this arm measures."


async def test_a_stored_document_digest_drives_a_turn_with_no_host_anywhere(
    surface: Turns,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
) -> None:
    """The whole rail: the overrides live as content-addressed bytes in the blob store, the turn
    pins their digest, and the model round runs with the replacement prompt, the rewritten
    description, and the withheld tool gone — while the surviving tools keep their platform-bound
    handlers (the turn still ends clean). No server dialed, nothing listening anywhere."""
    _config, _hub, blob = dbos_runtime
    seed = await _bootstrap()
    SEEN_SYSTEM_PROMPTS.clear()
    SEEN_TOOLS.clear()
    SEEN_TOOL_DESCRIPTIONS.clear()
    pinned_file = await store_environment_file(blob, b"from the arm")
    digest = await store_environment_document(
        blob,
        (
            "main:\n"
            f"  prompt:\n    text: '{OVERRIDDEN_PROMPT}'\n"
            f"  tools:\n    bash:\n      description: '{OVERRIDDEN_BASH}'\n"
            "    spawn:\n      enabled: false\n"
            f"files:\n  data/pinned.txt: {pinned_file}\n"
        ).encode(),
    )
    turn = await surface.admit(
        seed,
        "hello there",
        runtime_config=TurnRuntimeConfig(environment=digest),
    )
    text, terminal = await surface.consume(seed, turn)
    assert terminal["status"] == "done"
    assert terminal["environment"] == digest
    assert text.startswith("echo:")
    round_index = SEEN_SYSTEM_PROMPTS.index(OVERRIDDEN_PROMPT)
    offered = SEEN_TOOLS[round_index]
    assert "bash" in offered
    assert "spawn" not in offered
    assert SEEN_TOOL_DESCRIPTIONS[round_index]["bash"] == OVERRIDDEN_BASH
    runtime = loop_queue._runtime
    assert runtime is not None
    seeded = sorted(runtime.sandboxes.workspace_root.rglob("pinned.txt"))
    assert seeded and seeded[-1].read_bytes() == b"from the arm"


async def test_a_missing_document_fails_the_turn(surface: Turns) -> None:
    seed = await _bootstrap()
    turn = await surface.admit(
        seed,
        "hello there",
        runtime_config=TurnRuntimeConfig(environment="sha256:" + "0" * 64),
    )
    _text, terminal = await surface.consume(seed, turn)
    assert terminal["status"] == "failed"


async def test_a_document_pins_one_profiles_model_and_leaves_the_parent_alone(
    surface: Turns,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
) -> None:
    _config, _hub, blob = dbos_runtime
    seed = await _bootstrap()
    digest = await store_environment_document(
        blob, b'{"profiles": {"pinned": {"model": "claude-sonnet-5"}}}'
    )
    parent = await surface.admit(
        seed, "spawn-pinned", runtime_config=TurnRuntimeConfig(environment=digest)
    )
    _text, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    async with workspace_tx() as connection:
        child = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(tables.turn.c.parent_turn_id == UUID(parent))
            )
        ).scalar_one()
        models = dict(
            (
                await connection.execute(
                    sa.select(tables.ledger.c.turn_id, tables.ledger.c.model).where(
                        tables.ledger.c.turn_id.in_((UUID(parent), child))
                    )
                )
            ).all()
        )
    assert models[child] == "claude-sonnet-5"
    assert models[UUID(parent)] != "claude-sonnet-5"


REPLACED_CHILD_PROMPT = "OVERRIDDEN ROUNDTRIP: echo the value back under the arm's prompt."


async def test_a_replaced_prompt_on_a_spawned_child_keeps_the_finish_contract(
    surface: Turns,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
) -> None:
    """A document block that replaces a spawned profile's prompt cannot displace the platform's
    finish contract: the host re-appends it after the override, so the child still ends with a
    valid finish call — and the block never touches the parent, whose target the document does
    not name."""
    _config, _hub, blob = dbos_runtime
    seed = await _bootstrap()
    SEEN_SYSTEM_PROMPTS.clear()
    SEEN_TOOLS.clear()
    SEEN_TOOL_DESCRIPTIONS.clear()
    digest = await store_environment_document(
        blob,
        json.dumps({"profiles": {"pinned": {"prompt": {"text": REPLACED_CHILD_PROMPT}}}}).encode(),
    )
    parent = await surface.admit(
        seed,
        "spawn-pinned",
        runtime_config=TurnRuntimeConfig(model="claude-sonnet-5", environment=digest),
    )
    _text, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"
    assert await _child_echo(parent) == 7
    child_prompts = [
        prompt for prompt in SEEN_SYSTEM_PROMPTS if prompt.startswith(REPLACED_CHILD_PROMPT)
    ]
    assert child_prompts
    assert all(prompt.endswith(FINISH_CONTRACT) for prompt in child_prompts)


async def test_an_override_naming_a_tool_the_turn_does_not_offer_fails_the_turn(
    surface: Turns,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
) -> None:
    _config, _hub, blob = dbos_runtime
    seed = await _bootstrap()
    digest = await store_environment_document(
        blob, b"main:\n  tools:\n    no-such-tool:\n      enabled: false\n"
    )
    turn = await surface.admit(
        seed,
        "hello there",
        runtime_config=TurnRuntimeConfig(environment=digest),
    )
    _text, terminal = await surface.consume(seed, turn)
    assert terminal["status"] == "failed"


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
    """A subagent preload resolves the local skill and injects its instructions without a
    `load_skill` round or a child workspace."""
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
    assert isinstance(runtime.sandboxes.carrier, LocalCarrier)
    skill_md = runtime.sandboxes.carrier.ufo_home / "skills" / skill.name / "SKILL.md"
    assert await asyncio.to_thread(skill_md.read_bytes) == skill.raw_skill_md.encode()
    assert not (runtime.sandboxes.workspace_root / str(child_conversation)).exists()
    assert any(skill.instructions in system for system in SEEN_SYSTEM_PROMPTS)


async def _seed_preload_child(seed: Seed, delivers_result: bool) -> tuple[UUID, UUID]:
    """A queued subagent child of a running parent, preloading the sandbox skill. `delivers_result`
    is the background spawn: the child hands back its own result, so no parent waits on it."""
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
                inbound='{"value": 1, "preload_skills": ["sandbox"]}',
                parent_turn_id=parent_id,
                subagent_profile=PRELOAD_PROFILE.name,
                result_delivery="pending" if delivers_result else None,
                spawn_delivers_result=delivers_result,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return child_conversation, child_id


async def test_a_sandbox_provider_outage_parks_during_skill_setup(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    child_conversation, child_id = await _seed_preload_child(seed, delivers_result=True)
    absorbed_id, unabsorbed_id = uuid4(), uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.inbound_message),
            (
                {
                    "id": absorbed_id,
                    "workspace_id": seed.workspace_id,
                    "conversation_id": child_conversation,
                    "seq": 1,
                    "body": "absorbed",
                    "admission_source": "member",
                    "admitted_turn_id": child_id,
                    "consumed_turn_id": child_id,
                    "created_at": datetime.now(UTC),
                },
                {
                    "id": unabsorbed_id,
                    "workspace_id": seed.workspace_id,
                    "conversation_id": child_conversation,
                    "seq": 2,
                    "body": "unabsorbed",
                    "admission_source": "member",
                    "admitted_turn_id": child_id,
                    "consumed_turn_id": child_id,
                    "created_at": datetime.now(UTC),
                },
            ),
        )

    with ws(seed.workspace_id):
        assert await Transcript(blob=runtime.blob, conversation_id=child_conversation).write(
            Conversation(
                seq=1,
                messages=(Message(role="user", content="absorbed"),),
                from_run=True,
                parked=ParkedTurn(absorbed=(absorbed_id,), requesters=()),
            )
        )

    async def unavailable(*args: object) -> None:
        raise SandboxProviderUnavailable("e2b")

    monkeypatch.setattr(loop_queue, "load_skills", unavailable)
    before = datetime.now(UTC)
    with ws(seed.workspace_id):
        assert await loop_queue._run_turn(runtime, str(child_id)) == "parked"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.retry_at,
                    tables.turn.c.external_retry_count,
                    tables.turn.c.terminal,
                ).where(tables.turn.c.id == child_id)
            )
        ).one()
        arrivals = (
            await connection.execute(
                sa.select(
                    tables.inbound_message.c.id,
                    tables.inbound_message.c.consumed_turn_id,
                )
                .where(tables.inbound_message.c.id.in_((absorbed_id, unabsorbed_id)))
                .order_by(tables.inbound_message.c.seq)
            )
        ).all()
    assert row.status == "parked"
    assert row.retry_at is not None
    assert row.retry_at.replace(tzinfo=UTC) >= before + timedelta(
        seconds=SANDBOX_PROVIDER_RETRY_SECONDS - 1
    )
    assert row.external_retry_count == 1
    assert row.terminal is None
    assert [(arrival.id, arrival.consumed_turn_id) for arrival in arrivals] == [
        (absorbed_id, child_id),
        (unabsorbed_id, None),
    ]


async def test_a_sandbox_provider_outage_fails_a_child_its_parent_awaits(
    db: None,
    dbos_runtime: tuple[Config, GatingHub, FilesystemBlobStore],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parent waiting inline cancels the child that parks, so the retry schedule would never run:
    the setup fault stays the child's failure, naming the provider the parent can report."""
    runtime = loop_queue._runtime
    assert runtime is not None
    seed = await _bootstrap()
    _, child_id = await _seed_preload_child(seed, delivers_result=False)

    async def unavailable(*args: object) -> None:
        raise SandboxProviderUnavailable("e2b")

    monkeypatch.setattr(loop_queue, "load_skills", unavailable)
    with ws(seed.workspace_id):
        assert await loop_queue._run_turn(runtime, str(child_id)) == "failed"

    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(
                    tables.turn.c.status,
                    tables.turn.c.retry_at,
                    tables.turn.c.external_retry_count,
                    tables.turn.c.terminal,
                ).where(tables.turn.c.id == child_id)
            )
        ).one()
    assert row.status == "failed"
    assert row.retry_at is None
    assert row.external_retry_count == 0
    terminal = TerminalFrame.model_validate(row.terminal)
    assert terminal.error_class == "SandboxProviderUnavailable"
    assert terminal.error_message == "e2b"


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
        created_at=parent_row.created_at,
        terminal=TerminalFrame.model_validate(parent_row.terminal),
    )
    subagents = Subagents(
        client=runtime.dbos,
        registry=runtime.subagents,
        parent=parent,
        audience=conversation_audience(None),
        invoker=runtime.invoker_for(parent.workspace_id),
    )
    queued = await subagents.message(
        child_id, FOLLOWUP_INBOUND, dedup_key="turn-1/message_spawn/call-1"
    )
    (followup,) = await subagents.wait((queued.turn_id,))
    assert followup.status == "done"
    assert RoundTripOutput.model_validate_json(followup.text).echoed == FOLLOWUP_ECHO


async def test_a_followup_left_pending_by_an_ended_child_runs_as_its_next_turn(
    surface: Turns,
) -> None:
    """The exit handoff re-admits what the ended turn left pending: a follow-up folded into a child
    whose turn ended without draining it — a failure or a cancel — becomes the child's next turn and
    runs to its own terminal, so a correction never sits on a conversation nothing runs."""
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
        child = (
            await connection.execute(
                sa.select(tables.turn.c.id, tables.turn.c.conversation_id).where(
                    tables.turn.c.parent_turn_id == UUID(parent_id)
                )
            )
        ).one()
        await connection.execute(
            sa.insert(tables.inbound_message).values(
                id=uuid4(),
                workspace_id=parent_row.workspace_id,
                conversation_id=child.conversation_id,
                seq=sa.select(sa.func.coalesce(sa.func.max(tables.inbound_message.c.seq), 0) + 1)
                .where(tables.inbound_message.c.conversation_id == child.conversation_id)
                .scalar_subquery(),
                body=FOLLOWUP_INBOUND,
                admission_source="internal",
                speaker_member_id=None,
                idempotency_key="turn-1/message_spawn/call-1",
                admitted_turn_id=child.id,
                created_at=sa.func.now(),
            )
        )

    await loop_queue._offer_next_turn(
        runtime, parent_row.workspace_id, child.conversation_id, child.id
    )

    async with workspace_tx() as connection:
        followup_id = (
            await connection.execute(
                sa.select(tables.turn.c.id).where(
                    tables.turn.c.conversation_id == child.conversation_id,
                    tables.turn.c.seq == 2,
                )
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
        created_at=parent_row.created_at,
        terminal=TerminalFrame.model_validate(parent_row.terminal),
    )
    subagents = Subagents(
        client=runtime.dbos,
        registry=runtime.subagents,
        parent=parent,
        audience=conversation_audience(None),
    )
    (followup,) = await subagents.wait((followup_id,))
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


def test_a_profile_on_the_members_account_has_no_deploy_model_to_fall_back_to() -> None:
    """The account can be disconnected between admitting a turn and running it, and the deploy's
    key is exactly what this profile exists not to spend — so the model resolves from the
    connected provider or the turn fails saying so. Every other profile keeps its own pin, else
    the agent's."""
    agent = SimpleNamespace(model="workspace-model")
    runtime = SimpleNamespace(
        registry=SimpleNamespace(resolve=lambda model: model),
        config=SimpleNamespace(connect=SimpleNamespace(public_base_url="https://ufo.example")),
        manifests=(SimpleNamespace(connects_member_accounts=True),),
    )

    assert (
        loop_queue._subagent_model(OWN_ACCOUNT_PROFILE, "openai", agent, runtime, None, None)
        == OWN_ACCOUNT_MODEL
    )
    with pytest.raises(loop_queue.SubagentKeyWithdrawn):
        loop_queue._subagent_model(OWN_ACCOUNT_PROFILE, None, agent, runtime, None, None)
    with pytest.raises(loop_queue.SubagentKeyWithdrawn):
        loop_queue._subagent_model(OWN_ACCOUNT_PROFILE, "anthropic", agent, runtime, None, None)

    ordinary = replace(OWN_ACCOUNT_PROFILE, needs_own_model_key=False, own_key_models={})
    assert loop_queue._subagent_model(ordinary, None, agent, runtime, None, None) == (
        "workspace-model"
    )


def test_a_pinned_model_cannot_move_a_coding_turn_onto_the_deploys_key() -> None:
    """A member pinning a model on their conversation pins it for the work that runs on the
    deploy's account. The coding agent does not: the pinned id is not one their account was bound
    to serve, so honouring it drops the member slot on every credential read and the deploy funds
    the run — the one outcome this profile exists to prevent. Every other profile takes the pin."""
    agent = SimpleNamespace(model="workspace-model")
    runtime = SimpleNamespace(
        registry=SimpleNamespace(resolve=lambda model: model),
        config=SimpleNamespace(connect=SimpleNamespace(public_base_url="https://ufo.example")),
        manifests=(SimpleNamespace(connects_member_accounts=True),),
    )

    assert (
        loop_queue._subagent_model(
            OWN_ACCOUNT_PROFILE, "openai", agent, runtime, "pinned-model", None
        )
        == OWN_ACCOUNT_MODEL
    )
    with pytest.raises(loop_queue.SubagentKeyWithdrawn):
        loop_queue._subagent_model(OWN_ACCOUNT_PROFILE, None, agent, runtime, "pinned-model", None)

    ordinary = replace(OWN_ACCOUNT_PROFILE, needs_own_model_key=False, own_key_models={})
    assert (
        loop_queue._subagent_model(ordinary, None, agent, runtime, "pinned-model", None)
        == "pinned-model"
    )


def test_an_environment_document_moves_a_coding_turn_onto_the_workspaces_key() -> None:
    """The stored document is the workspace's explicit, attested spend choice, so its model
    reaches even the own-account profile — connected or not — where a bare pin never does."""
    agent = SimpleNamespace(model="workspace-model")
    runtime = SimpleNamespace(
        registry=SimpleNamespace(resolve=lambda model: model),
        config=SimpleNamespace(connect=SimpleNamespace(public_base_url="https://ufo.example")),
        manifests=(SimpleNamespace(connects_member_accounts=True),),
    )

    for connected in ("openai", None):
        assert (
            loop_queue._subagent_model(
                OWN_ACCOUNT_PROFILE, connected, agent, runtime, "document-model", "document-model"
            )
            == "document-model"
        )


def test_a_withdrawn_account_hands_over_the_same_address_the_spawn_refusal_does() -> None:
    """A member whose account went missing between admitting a turn and running it is in the same
    position as one who never connected: the fix is the same screen. Naming their uuid instead
    hands the model something no member can act on."""
    runtime = SimpleNamespace(
        registry=SimpleNamespace(resolve=lambda model: model),
        config=SimpleNamespace(connect=SimpleNamespace(public_base_url="https://ufo.example/")),
        manifests=(SimpleNamespace(connects_member_accounts=True),),
    )
    agent = SimpleNamespace(model="workspace-model")

    with pytest.raises(loop_queue.SubagentKeyWithdrawn) as withdrawn:
        loop_queue._subagent_model(OWN_ACCOUNT_PROFILE, None, agent, runtime, None, None)

    named = str(withdrawn.value)
    assert f"https://ufo.example{SPAWN_CONNECT_PATH}" in named
    assert "ChatGPT or Claude" in named
    assert OWN_ACCOUNT_PROFILE.name in named


def test_a_rate_limited_turn_moves_onto_every_other_account_the_member_connected() -> None:
    """A member who connected two accounts bought two subscriptions, so the limit of the one the
    turn started on is not the end of the work. The models it moves onto are the other accounts',
    in the declaration order the first was chosen by, and the account in hand is never listed
    twice. A member with one account has none to move to, and the turn still runs on theirs."""
    runtime = SimpleNamespace(registry=SimpleNamespace(resolve=lambda model: model))
    both = replace(
        OWN_ACCOUNT_PROFILE,
        own_key_models={"openai": OWN_ACCOUNT_MODEL, "anthropic": "claude-opus-5"},
    )

    assert loop_queue._own_account_alternates(
        both, ("openai", "anthropic"), OWN_ACCOUNT_MODEL, None, runtime
    ) == ("claude-opus-5",)
    assert loop_queue._own_account_alternates(
        both, ("anthropic", "openai"), "claude-opus-5", None, runtime
    ) == (OWN_ACCOUNT_MODEL,)
    assert (
        loop_queue._own_account_alternates(both, ("openai",), OWN_ACCOUNT_MODEL, None, runtime)
        == ()
    )


def test_a_turn_the_workspace_pays_for_runs_on_no_member_account() -> None:
    """An environment document is the workspace's own spend choice and an ordinary profile runs on
    the deploy's key: neither asked for an account of the member's, so neither holds any to move
    onto."""
    runtime = SimpleNamespace(registry=SimpleNamespace(resolve=lambda model: model))
    both = replace(
        OWN_ACCOUNT_PROFILE,
        own_key_models={"openai": OWN_ACCOUNT_MODEL, "anthropic": "claude-opus-5"},
    )
    ordinary = replace(both, needs_own_model_key=False, own_key_models={})

    assert (
        loop_queue._own_account_alternates(
            both, ("openai", "anthropic"), "document-model", "document-model", runtime
        )
        is None
    )
    assert (
        loop_queue._own_account_alternates(
            ordinary, ("openai", "anthropic"), "workspace-model", None, runtime
        )
        is None
    )


def test_a_profile_run_on_the_deploys_key_holds_no_member_account() -> None:
    """A deploy with no extension that connects an account runs the profile on its own declared pin
    and the deploy's key, with no account of the member's behind it. That turn is not on a member
    account with none left to move to — it is on no member account at all — so it holds no
    accounts, and a rate limit on it fails as the provider's fault rather than sending the member
    to connect an account no deploy of theirs could hold."""
    runtime = SimpleNamespace(registry=SimpleNamespace(resolve=lambda model: model))
    pinned_profile = replace(OWN_ACCOUNT_PROFILE, model="profile-pin")

    assert (
        loop_queue._own_account_alternates(pinned_profile, (), "profile-pin", None, runtime) is None
    )
    assert (
        loop_queue._own_account_alternates(
            pinned_profile, ("openai",), OWN_ACCOUNT_MODEL, None, runtime
        )
        == ()
    )


def test_an_exhausted_account_names_the_limit_rather_than_a_missing_connection() -> None:
    """A member whose accounts are all rate limited did not disconnect anything: telling them to
    reconnect sends them to a screen that shows the account already there. Both arms hand over the
    same address, and the cause names what to fix."""
    limited = loop_queue.SubagentKeyWithdrawn(
        OWN_ACCOUNT_PROFILE.name, "https://ufo.example/", rate_limited=True
    )
    withdrawn = loop_queue.SubagentKeyWithdrawn(OWN_ACCOUNT_PROFILE.name, "https://ufo.example/")

    assert limited.rate_limited
    assert "every account they connected is rate limited right now" in str(limited)
    assert "no longer connected" not in str(limited)
    assert not withdrawn.rate_limited
    assert "that account is no longer connected" in str(withdrawn)
    for fault in (limited, withdrawn):
        assert f"https://ufo.example{SPAWN_CONNECT_PATH}" in str(fault)


async def test_spawned_children_run_outside_the_turns_queue_claim(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the turns queue's worker bound at one, the parent holds the process's only claim on
    it for its whole run — the spawn completes only because children ride the express queue."""
    monkeypatch.setattr(loop_queue.TURN_QUEUE, "_worker_concurrency", 1)
    seed = await _bootstrap()
    parent = await surface.admit(seed, "spawn-subagent")
    _, terminal = await surface.consume(seed, parent)
    assert terminal["status"] == "done"


async def test_member_turns_hold_to_the_worker_claim_bound(
    surface: Turns, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(loop_queue.TURN_QUEUE, "_worker_concurrency", 2)
    HOLD_STARTED.clear()
    HOLD_RELEASE.clear()
    HOLD_TWO_STARTED.clear()
    try:
        seeds = [await _bootstrap() for _ in range(3)]
        turn_ids = [
            await surface.admit(seed, f"hold-slot {index}") for index, seed in enumerate(seeds)
        ]
        assert await asyncio.to_thread(HOLD_TWO_STARTED.wait, STREAM_TIMEOUT_SECONDS)
        await asyncio.sleep(0.5)
        assert len(HOLD_STARTED) == 2
        HOLD_RELEASE.set()
        results = await asyncio.gather(
            *(surface.consume(seed, turn_id) for seed, turn_id in zip(seeds, turn_ids, strict=True))
        )
        assert len(HOLD_STARTED) == 3
        assert all(terminal["status"] == "done" for _, terminal in results)
    finally:
        HOLD_RELEASE.set()


async def test_the_scheduled_slot_gates_only_scheduled_model_loop_turns() -> None:
    assert loop_queue._turn_gates(None, MEMBER_ADMISSION) == ()
    assert loop_queue._turn_gates(None, INTENT_ADMISSION) == ()
    assert loop_queue._turn_gates(uuid4(), SCHEDULED_ADMISSION) == ()
    scheduled = loop_queue._turn_gates(None, SCHEDULED_ADMISSION)
    assert len(scheduled) == 1 and isinstance(scheduled[0], asyncio.Semaphore)
    assert scheduled[0]._value == loop_queue.SCHEDULED_TURN_CONCURRENCY


def test_a_deploy_that_cannot_hold_an_account_runs_the_profile_on_its_own_key() -> None:
    """A pack shipping `coding` and no extension that connects an account — the eval packs are
    exactly that shape — has no member to refuse. Requiring one there takes the capability away on
    every deploy that could never have offered it, so the profile falls to its own declared pin on
    the deploy's key. Where an account *can* be connected, skipping still costs the capability."""
    agent = SimpleNamespace(model="workspace-model")
    pinned_profile = replace(OWN_ACCOUNT_PROFILE, model="profile-pin")

    def runtime_with(connectable: bool) -> SimpleNamespace:
        return SimpleNamespace(
            registry=SimpleNamespace(resolve=lambda model: model),
            config=SimpleNamespace(connect=SimpleNamespace(public_base_url="https://ufo.example")),
            manifests=(SimpleNamespace(connects_member_accounts=connectable),),
        )

    assert (
        loop_queue._subagent_model(pinned_profile, None, agent, runtime_with(False), None, None)
        == "profile-pin"
    )
    with pytest.raises(loop_queue.SubagentKeyWithdrawn):
        loop_queue._subagent_model(pinned_profile, None, agent, runtime_with(True), None, None)

    assert (
        loop_queue._subagent_model(pinned_profile, "openai", agent, runtime_with(False), None, None)
        == OWN_ACCOUNT_MODEL
    )
