"""The reactive-hook seam: the HookChain's composition/failure model directly, and every
turn-lifecycle fire point wired in the engine — proven end to end through the installed sample's
hooks.

`fire` is pure control flow, so its folding, deny-wins ordering, matcher, timeout, and fail
policies are asserted directly against the resolution it returns (a closure list observes which
real handlers ran — the fire logic under test, never a stand-in dependency). The engine wiring is
proven through the sample extension registering a hook per turn event — a pre_tool_use gate, a
post_tool_use recorder, a post_tool_use_failure recorder, a stop recorder, and pre/post_compact
recorders: the sample records what each hook received through its own scoped store, and the tests
read those rows back through the public ScopedStore — no mock call-log. The data-plane page_change
seam and its runner are proven in test_page_change."""

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa
import ufo_ext_sample as sample
from cryptography.fernet import Fernet
from pydantic import BaseModel

import ufo.ext.loader as loader
from ufo.blob import FilesystemBlobStore
from ufo.browser import SandboxCdpProvider
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore, context_for
from ufo.ext.loader import BoundHook, HookChain, load_manifests, turn_hooks, turn_tools
from ufo.ext.manifest import (
    Deny,
    HookContext,
    HookOutcome,
    HookSpec,
    InjectContext,
    ModifyInput,
    ModifyOutput,
    PostToolUse,
    PreToolUse,
    UserPromptSubmit,
)
from ufo.hub import InProcessHub
from ufo.loop.compaction import (
    COMPACTED_CONTEXT_PREFIX,
    COMPACTION_KEEP_MESSAGES,
    Compaction,
    CompactionSummary,
)
from ufo.loop.engine import TurnEngine
from ufo.loop.prompts.render import COMPACTION_SYSTEM_PROMPT, rendered_prompt
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    Message,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    Usage,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult
from ufo.tools.registry import ToolRegistry

REWRITTEN_COMMAND = "echo modified"


class Args(BaseModel):
    value: str


def _ext(workspace_id: UUID) -> ExtensionContext:
    return context_for(
        workspace_id, "probe", frozenset(), CredentialStore(fernet=Fernet(Fernet.generate_key()))
    )


def _chain(event: str, ext: ExtensionContext, *specs: HookSpec) -> HookChain:
    bound = tuple(BoundHook(spec=spec, ext=ext) for spec in specs)
    return HookChain(hooks={event: bound})


async def _fire(chain: HookChain, event: str, payload: object) -> loader.HookResolution:
    turn = Turn(
        id=uuid4(),
        workspace_id=uuid4(),
        conversation_id=uuid4(),
        agent_id=uuid4(),
        seq=1,
        status="running",
        inbound="hi",
    )
    return await chain.fire(event, payload, turn, Agent(prompt="p", model="claude-opus-4-8"), None)


def _pre(tool_name: str = "t") -> PreToolUse:
    return PreToolUse(tool_name=tool_name, tool_input=Args(value="x"))


# --- composition model, asserted directly against fire's resolution -----------------------------


async def test_modify_input_folds_left_to_right_each_seeing_the_prior() -> None:
    async def append_a(ctx: HookContext) -> HookOutcome:
        return ModifyInput(
            tool_input=ctx.payload.tool_input.model_copy(
                update={"value": ctx.payload.tool_input.value + "A"}
            )
        )

    async def append_b(ctx: HookContext) -> HookOutcome:
        return ModifyInput(
            tool_input=ctx.payload.tool_input.model_copy(
                update={"value": ctx.payload.tool_input.value + "B"}
            )
        )

    ext = _ext(uuid4())
    chain = _chain(
        "pre_tool_use",
        ext,
        HookSpec(event="pre_tool_use", handler=append_a),
        HookSpec(event="pre_tool_use", handler=append_b),
    )
    resolution = await _fire(chain, "pre_tool_use", _pre())
    assert resolution.denied is None
    assert isinstance(resolution.tool_input, Args)
    assert resolution.tool_input.value == "xAB"


async def test_inject_context_user_prompt_submit_concatenates_in_order() -> None:
    async def first(ctx: HookContext) -> HookOutcome:
        return InjectContext(text="first")

    async def second(ctx: HookContext) -> HookOutcome:
        return InjectContext(text="second")

    ext = _ext(uuid4())
    chain = _chain(
        "user_prompt_submit",
        ext,
        HookSpec(event="user_prompt_submit", handler=first),
        HookSpec(event="user_prompt_submit", handler=second),
    )
    resolution = await _fire(chain, "user_prompt_submit", UserPromptSubmit(text="hi"))
    assert resolution.denied is None
    assert resolution.injected == "first\nsecond"


async def test_deny_wins_and_short_circuits_later_hooks_preserving_order() -> None:
    ran: list[str] = []

    async def modify_then_record(ctx: HookContext) -> HookOutcome:
        ran.append("modify")
        return ModifyInput(tool_input=ctx.payload.tool_input)

    async def deny(ctx: HookContext) -> HookOutcome:
        return Deny(reason="policy")

    async def never(ctx: HookContext) -> HookOutcome:
        ran.append("after")
        return None

    ext = _ext(uuid4())
    chain = _chain(
        "pre_tool_use",
        ext,
        HookSpec(event="pre_tool_use", handler=modify_then_record),
        HookSpec(event="pre_tool_use", handler=deny),
        HookSpec(event="pre_tool_use", handler=never),
    )
    resolution = await _fire(chain, "pre_tool_use", _pre())
    assert resolution.denied == "policy"
    assert ran == ["modify"]


async def test_matcher_limits_a_tool_hook_to_its_named_tools() -> None:
    async def deny(ctx: HookContext) -> HookOutcome:
        return Deny(reason="no")

    ext = _ext(uuid4())
    chain = _chain(
        "pre_tool_use", ext, HookSpec(event="pre_tool_use", handler=deny, tools=("bash",))
    )
    matched = await _fire(chain, "pre_tool_use", _pre("bash"))
    skipped = await _fire(chain, "pre_tool_use", _pre("other"))
    assert matched.denied == "no"
    assert skipped.denied is None


async def test_raising_gating_hook_fails_closed() -> None:
    async def boom(ctx: HookContext) -> HookOutcome:
        raise RuntimeError("hook exploded")

    ext = _ext(uuid4())
    chain = _chain("pre_tool_use", ext, HookSpec(event="pre_tool_use", handler=boom))
    resolution = await _fire(chain, "pre_tool_use", _pre())
    assert resolution.denied is not None
    assert "failed closed" in resolution.denied


async def test_gating_hook_exceeding_the_timeout_fails_closed(monkeypatch: object) -> None:
    import asyncio

    async def slow(ctx: HookContext) -> HookOutcome:
        await asyncio.sleep(1.0)
        return None

    monkeypatch.setattr(loader, "HOOK_TIMEOUT_SECONDS", 0.05)
    ext = _ext(uuid4())
    chain = _chain("user_prompt_submit", ext, HookSpec(event="user_prompt_submit", handler=slow))
    resolution = await _fire(chain, "user_prompt_submit", UserPromptSubmit(text="hi"))
    assert resolution.denied is not None
    assert "failed closed" in resolution.denied


async def test_raising_observe_hook_is_swallowed_leaving_the_output_unchanged() -> None:
    async def boom(ctx: HookContext) -> HookOutcome:
        raise RuntimeError("observe exploded")

    ext = _ext(uuid4())
    chain = _chain("post_tool_use", ext, HookSpec(event="post_tool_use", handler=boom))
    resolution = await _fire(
        chain,
        "post_tool_use",
        PostToolUse(tool_name="t", tool_input=Args(value="x"), output="original"),
    )
    assert resolution.denied is None
    assert resolution.output == "original"


async def test_a_disallowed_outcome_on_an_observe_event_is_ignored() -> None:
    async def deny(ctx: HookContext) -> HookOutcome:
        return Deny(reason="post cannot deny")

    ext = _ext(uuid4())
    chain = _chain("post_tool_use", ext, HookSpec(event="post_tool_use", handler=deny))
    resolution = await _fire(
        chain,
        "post_tool_use",
        PostToolUse(tool_name="t", tool_input=Args(value="x"), output="original"),
    )
    assert resolution.denied is None
    assert resolution.output == "original"


# --- engine wiring at the three fire points -----------------------------------------------------


@dataclass(frozen=True)
class StubMemory:
    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


@dataclass
class CapturingModel:
    seen: list[tuple[Message, ...]] = field(default_factory=list)
    seen_system: list[str] = field(default_factory=list)

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        self.seen.append(request.messages)
        self.seen_system.append(request.system)
        yield TextDelta(text="ok")
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class BashThenAnswerModel:
    """Round 1: one bash call; round 2 (tool result returned): answer."""

    command: str = "echo hi"

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if _answered(request):
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json=json.dumps({"command": self.command}))
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass(frozen=True)
class EchoAndBashModel:
    """Round 1: the sample's sentinel echo call (denied by its pre hook) and a bash call (which
    dispatches); round 2: answer once both tool results return."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if _answered(request):
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name=sample.TOOL_NAME)
        yield ToolCallDelta(id="c1", partial_json=json.dumps({"message": "hi"}))
        yield ToolCallStart(id="c2", name="bash")
        yield ToolCallDelta(id="c2", partial_json=json.dumps({"command": "echo hi"}))
        yield Usage(input_tokens=2, output_tokens=2)


def _answered(request: ModelRequest) -> bool:
    return any(
        isinstance(message.content, tuple)
        and any(isinstance(block, ToolResultBlock) for block in message.content)
        for message in request.messages
    )


@dataclass(frozen=True)
class CompactingModel:
    """Round 1: one bash call, which grows the window past keep_messages; round 2: answer. On the
    compaction summarize request (identified by its COMPACTION_SYSTEM_PROMPT system prompt) it
    returns fixed summary text, so a proactive compaction mid-turn yields a real summary and fires
    the pre_compact/post_compact hooks."""

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        if request.system == COMPACTION_SYSTEM_PROMPT:
            summary = CompactionSummary(
                intent="condensed history", current_work="mid-turn", next_step="answer"
            )
            yield TextDelta(text=summary.model_dump_json())
            yield Usage(input_tokens=1, output_tokens=1)
            return
        if _answered(request):
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json=json.dumps({"command": "echo hi"}))
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass
class RecordingCarrier:
    result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="hi\n", stderr="", exit_code=0)
    )
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.calls.append(argv)
        return self.result

    async def destroy(self, handle: SandboxHandle) -> None: ...


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise AssertionError("these hook tests must not spawn a subagent")


async def _seed_turn(workspace_id: UUID) -> Turn:
    member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(4))
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
                email="a@b.c",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name="assistant",
                prompt="p",
                model="claude-opus-4-8",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
        await connection.execute(
            sa.insert(tables.conversation).values(
                id=conversation_id,
                workspace_id=workspace_id,
                surface="cli",
                queue_key=uuid4().hex,
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
                status="queued",
                inbound="hi",
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=1,
        status="queued",
        inbound="hi",
    )


def _engine(
    turn: Turn,
    model: object,
    tmp_path: Path,
    hooks: HookChain,
    tools: tuple = BUILTIN_TOOLS,
    tool_ext: dict | None = None,
    carrier: RecordingCarrier | None = None,
    compaction_trigger: int | None = None,
    compaction_keep: int = COMPACTION_KEEP_MESSAGES,
) -> TurnEngine:
    blob = FilesystemBlobStore(root=tmp_path)
    handle = SandboxHandle(conversation_id=turn.conversation_id, container_id="test")
    agent = Agent(prompt="p", model="claude-opus-4-8")
    return TurnEngine(
        turn=turn,
        agent=agent,
        system_prompt=rendered_prompt("p"),
        model=model,
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model,
            model="claude-opus-4-8",
            blob=blob,
            conversation_id=turn.conversation_id,
            trigger_tokens=compaction_trigger,
            keep_messages=compaction_keep,
            hooks=hooks,
            turn=turn,
            agent=agent,
            member_id=None,
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=carrier or RecordingCarrier(), handle=handle),
        cdp_provider=SandboxCdpProvider(endpoint=None),
        search_provider=None,
        tools=ToolRegistry(tools),
        tool_ext=tool_ext or {},
        hooks=hooks,
        blob=blob,
        spawn=_unavailable_spawn,
        member_id=None,
        artifact_token_secret="",
        grants=None,
    )


def _sample_manifest() -> object:
    found = next((m for m in load_manifests() if m.name == sample.NAME), None)
    assert found is not None, "sample extension not discovered — run `uv sync`"
    return found


async def test_sample_pre_deny_short_circuits_and_post_captures_the_other(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())
    manifest = _sample_manifest()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    tools, tool_ext = turn_tools((manifest,), turn.workspace_id, store)
    hooks = turn_hooks((manifest,), turn.workspace_id, store)
    engine = _engine(turn, EchoAndBashModel(), tmp_path, hooks, tools=tools, tool_ext=tool_ext)
    frame = await engine.run()
    assert frame.status == "done"

    scoped = ScopedStore(workspace_id=turn.workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.TOOL_KEY) is None
    assert await scoped.get(sample.HOOK_POST_KEY) == {"tool": "bash"}
    assert await scoped.get(sample.HOOK_POST_FAILURE_KEY) is None

    stored = await engine.transcript.read()
    assert stored is not None
    results = stored.messages[2].content
    assert isinstance(results, tuple)
    assert results[0].is_error is True and results[0].content == sample.HOOK_DENY_REASON
    assert results[1].is_error is False


async def test_stop_fires_with_the_final_answer(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn(uuid4())
    manifest = _sample_manifest()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    tools, tool_ext = turn_tools((manifest,), turn.workspace_id, store)
    hooks = turn_hooks((manifest,), turn.workspace_id, store)
    frame = await _engine(
        turn, CapturingModel(), tmp_path, hooks, tools=tools, tool_ext=tool_ext
    ).run()
    assert frame.status == "done"
    scoped = ScopedStore(workspace_id=turn.workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.HOOK_STOP_KEY) == {"answer": "ok"}


async def test_tool_failure_reaches_post_tool_use_failure_not_post_tool_use(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())
    manifest = _sample_manifest()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    tools, tool_ext = turn_tools((manifest,), turn.workspace_id, store)
    hooks = turn_hooks((manifest,), turn.workspace_id, store)
    carrier = RecordingCarrier(result=ExecResult(stdout="", stderr="boom", exit_code=1))
    frame = await _engine(
        turn,
        BashThenAnswerModel(),
        tmp_path,
        hooks,
        tools=tools,
        tool_ext=tool_ext,
        carrier=carrier,
    ).run()
    assert frame.status == "done"
    scoped = ScopedStore(workspace_id=turn.workspace_id, extension=sample.NAME)
    assert await scoped.get(sample.HOOK_POST_FAILURE_KEY) == {"tool": "bash"}
    assert await scoped.get(sample.HOOK_POST_KEY) is None


async def test_compaction_fires_pre_and_post_compact(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn(uuid4())
    manifest = _sample_manifest()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    tools, tool_ext = turn_tools((manifest,), turn.workspace_id, store)
    hooks = turn_hooks((manifest,), turn.workspace_id, store)
    frame = await _engine(
        turn,
        CompactingModel(),
        tmp_path,
        hooks,
        tools=tools,
        tool_ext=tool_ext,
        compaction_trigger=1,
        compaction_keep=2,
    ).run()
    assert frame.status == "done"
    scoped = ScopedStore(workspace_id=turn.workspace_id, extension=sample.NAME)
    pre = await scoped.get(sample.HOOK_PRE_COMPACT_KEY)
    post = await scoped.get(sample.HOOK_POST_COMPACT_KEY)
    assert pre is not None and pre["reason"] == "auto" and pre["before_tokens"] > 0
    assert post is not None
    assert post["summary"].startswith(COMPACTED_CONTEXT_PREFIX)
    assert "condensed history" in post["summary"]
    assert post["before_tokens"] == pre["before_tokens"]
    assert post["after_tokens"] > 0


async def test_user_prompt_submit_inject_reaches_the_system_context(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())

    async def inject(ctx: HookContext) -> HookOutcome:
        return InjectContext(text="INJECTED-GUIDANCE")

    chain = _chain(
        "user_prompt_submit",
        _ext(turn.workspace_id),
        HookSpec(event="user_prompt_submit", handler=inject),
    )
    model = CapturingModel()
    frame = await _engine(turn, model, tmp_path, chain).run()
    assert frame.status == "done"
    assert "INJECTED-GUIDANCE" in model.seen_system[0]


async def test_user_prompt_submit_deny_refuses_the_turn_before_the_model(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())

    async def deny(ctx: HookContext) -> HookOutcome:
        return Deny(reason="inbound refused by policy")

    chain = _chain(
        "user_prompt_submit",
        _ext(turn.workspace_id),
        HookSpec(event="user_prompt_submit", handler=deny),
    )
    model = CapturingModel()
    frame = await _engine(turn, model, tmp_path, chain).run()
    assert frame.status == "done"
    assert frame.text == "inbound refused by policy"
    assert model.seen == []


async def test_pre_modify_input_alters_the_dispatched_args(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn(uuid4())

    async def rewrite(ctx: HookContext) -> HookOutcome:
        return ModifyInput(
            tool_input=ctx.payload.tool_input.model_copy(update={"command": REWRITTEN_COMMAND})
        )

    chain = _chain(
        "pre_tool_use",
        _ext(turn.workspace_id),
        HookSpec(event="pre_tool_use", handler=rewrite, tools=("bash",)),
    )
    carrier = RecordingCarrier()
    frame = await _engine(
        turn, BashThenAnswerModel(command="echo original"), tmp_path, chain, carrier=carrier
    ).run()
    assert frame.status == "done"
    assert any(REWRITTEN_COMMAND in " ".join(argv) for argv in carrier.calls)
    assert not any("echo original" in " ".join(argv) for argv in carrier.calls)


async def test_post_modify_output_and_inject_reach_the_tool_result(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())

    async def replace_output(ctx: HookContext) -> HookOutcome:
        return ModifyOutput(output="REPLACED")

    async def inject(ctx: HookContext) -> HookOutcome:
        return InjectContext(text="NOTE")

    chain = _chain(
        "post_tool_use",
        _ext(turn.workspace_id),
        HookSpec(event="post_tool_use", handler=replace_output),
        HookSpec(event="post_tool_use", handler=inject),
    )
    frame = await _engine(turn, BashThenAnswerModel(), tmp_path, chain).run()
    assert frame.status == "done"
    engine_transcript = Transcript(
        blob=FilesystemBlobStore(root=tmp_path), conversation_id=turn.conversation_id
    )
    stored = await engine_transcript.read()
    assert stored is not None
    result = stored.messages[2].content[0]
    assert isinstance(result, ToolResultBlock)
    assert result.content == "REPLACED\nNOTE"


async def test_pre_hook_that_raises_fails_closed_and_the_tool_never_dispatches(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(uuid4())

    async def boom(ctx: HookContext) -> HookOutcome:
        raise RuntimeError("gate exploded")

    chain = _chain(
        "pre_tool_use",
        _ext(turn.workspace_id),
        HookSpec(event="pre_tool_use", handler=boom, tools=("bash",)),
    )
    carrier = RecordingCarrier()
    frame = await _engine(turn, BashThenAnswerModel(), tmp_path, chain, carrier=carrier).run()
    assert frame.status == "done"
    assert carrier.calls == []
    stored = await Transcript(
        blob=FilesystemBlobStore(root=tmp_path), conversation_id=turn.conversation_id
    ).read()
    assert stored is not None
    result = stored.messages[2].content[0]
    assert isinstance(result, ToolResultBlock)
    assert result.is_error is True and "failed closed" in result.content


async def test_a_tool_outside_the_matcher_is_not_denied(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn(uuid4())

    async def deny(ctx: HookContext) -> HookOutcome:
        return Deny(reason="never for bash")

    chain = _chain(
        "pre_tool_use",
        _ext(turn.workspace_id),
        HookSpec(event="pre_tool_use", handler=deny, tools=("some_other_tool",)),
    )
    carrier = RecordingCarrier()
    frame = await _engine(turn, BashThenAnswerModel(), tmp_path, chain, carrier=carrier).run()
    assert frame.status == "done"
    assert any("echo hi" in " ".join(argv) for argv in carrier.calls)
