"""The turn lifecycle rig: a real DBOS runtime, the stub model registry and manifest set every
turn resolves through, and the surface that admits turns against them. The engine's own lifecycle
proofs and the eval driver's proofs against that engine both open a workspace this way, and the
driver ships in another distribution, so the rig is shared here rather than in either suite."""

import asyncio
import json
import threading
from collections.abc import AsyncIterator, Iterator
from contextlib import aclosing
from dataclasses import dataclass, replace
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from pydantic import BaseModel
from ufo_ext_context_compact import manifest as compact_manifest_module
from ufo_ext_context_rollover import manifest as rollover_manifest_module

from ufo.blob import FilesystemBlobStore
from ufo.config import Config
from ufo.db import workspace_tx
from ufo.harness.durability import replay_safe_client
from ufo.harness.models.catalog import (
    CORE_MODEL_SPECS,
    CORE_PRICING,
)
from ufo.harness.models.interface import (
    ModelEvent,
    ModelRequest,
    ModelResponseTruncated,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
)
from ufo.harness.models.registry import ModelRegistry
from ufo.harness.models.spec import RepeatedToolRollover
from ufo.harness.sandbox.conversation import (
    SANDBOX_IMAGE_REF,
    ConversationSandbox,
)
from ufo.harness.sandbox.local import LocalCarrier
from ufo.harness.sandbox.session import (
    RunTokenCodec,
)
from ufo.host.assemble import HostEnvironment
from ufo.host.ext.loader import embed_backend, index_backend, skill_registry
from ufo.runtime import queue as loop_queue
from ufo.runtime.access.connectors import ConnectorRegistry
from ufo.runtime.access.egress_resolver import PerAgentRules
from ufo.runtime.billing.spend import NO_SPEND_GATES, SpendGates
from ufo.runtime.engine import (
    EMPTY_RESPONSE_NUDGE,
    FINISH_TOOL,
    TRUNCATION_FEEDBACK,
)
from ufo.runtime.ext.manifest import (
    EmbedBackendSpec,
    IndexBackendSpec,
    Manifest,
)
from ufo.runtime.hub import CostTick, Hub, InProcessHub, Parked, Terminal
from ufo.runtime.subagents import SubagentProfile, SubagentRegistry
from ufo.runtime.surfaces import hub_tail
from ufo.runtime.surfaces.admission import Admission, MemberAdmission
from ufo.runtime.tools.context import TextContent, ToolContext, ToolResult
from ufo.runtime.tools.registry import ToolDef
from ufo.runtime.transcript import Transcript
from ufo.runtime.turns.activity import ACTIVITY_PROMPT
from ufo.runtime.turns.transcript import Conversation
from ufo.runtime.workspace import ws
from ufo.schema import tables
from ufo.schema.records import (
    ReasoningEffort,
    TurnRuntimeConfig,
    Usage,
)
from ufo_testsupport.index import default_index
from ufo_testsupport.invoker import invoker_factory
from ufo_testsupport.stream_gate import GatingHub, StreamGate, release_when_running

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
            factory=lambda ctx: default_index(),
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
    models=(PINNED_MODEL,),
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
            rules=PerAgentRules(),
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
    spend: SpendGates = NO_SPEND_GATES

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
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id), spend=self.spend)) as frames,
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
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id), spend=self.spend)) as frames,
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
                aclosing(hub_tail.tail_frames(self.hub, UUID(turn_id), spend=self.spend)) as frames,
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


def _runtime_parts() -> tuple[Config, Hub, FilesystemBlobStore]:
    runtime = loop_queue._runtime
    assert runtime is not None
    assert isinstance(runtime.blob, FilesystemBlobStore)
    return runtime.config, runtime.hub, runtime.blob
