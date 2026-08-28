import json
from collections import namedtuple
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
import ufo_ext_sample as sample
import yaml
from cryptography.fernet import Fernet
from pydantic import BaseModel, ConfigDict

from ufo.access.connectors import ConnectorRegistry
from ufo.access.credentials import CredentialStore
from ufo.blob import FilesystemBlobStore
from ufo.db import workspace_tx
from ufo.ext.context import ScopedStore, context_for
from ufo.ext.loader import (
    BoundHook,
    HookChain,
    load_manifests,
    turn_hooks,
    turn_tools,
    validate_ext_tools,
)
from ufo.ext.manifest import HookContext, HookOutcome, HookSpec, ModifyOutput, PostToolUse
from ufo.hub import InProcessHub
from ufo.loop.compaction import Compaction
from ufo.loop.engine import (
    EffectiveCall,
    TurnEngine,
    _dispatch_segments,
    _RejectedToolCall,
)
from ufo.loop.prompts.render import rendered_prompt
from ufo.loop.queue import (
    IMPLIED_GRANTS,
    _agent_actions,
    _agent_tools,
    _with_action_verbs,
)
from ufo.loop.subagents import SubagentRegistry
from ufo.loop.tool_bridge import ToolBridge
from ufo.loop.transcript import Transcript
from ufo.models.interface import (
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
)
from ufo.objects import (
    BoundAction,
    BoundKind,
    ObjectActionInput,
    ObjectDetail,
    ObjectKind,
    ObjectListQuery,
    ObjectPage,
    ObjectVerbs,
    action_registry,
    object_registry,
)
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import (
    INTENT_ADMISSION,
    MEMBER_ADMISSION,
    Agent,
    AskQuestion,
    AskUserInput,
    ConnectRequest,
    ToolIntent,
    Turn,
    Usage,
)
from ufo.tools.bridge import ToolBridgeIntent, bridge_tools
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import ObjectBinding, ToolDef, ToolRegistry
from ufo.turns.activity import ActivitySummarizer
from ufo.turns.audience import conversation_audience
from ufo.turns.untrusted import UNTRUSTED_OPEN
from ufo.workspace import ws

DIVINE_ID = f"action:{sample.WIDGET_KIND}:{sample.DIVINE_ACTION}"
ENGRAVE_ID = f"action:{sample.WIDGET_KIND}:{sample.ENGRAVE_ACTION}"
POLISH_ID = f"action:{sample.WIDGET_KIND}:{sample.POLISH_ACTION}"
AUDIT_ID = f"action:{sample.WORKSPACE_KIND}:{sample.AUDIT_ACTION}"


class _ActivityModel:
    model = "gpt-5.6-luna"

    def __init__(self) -> None:
        self.payloads: list[str] = []

    async def complete(self, request: ModelRequest) -> str:
        content = request.messages[0].content
        assert isinstance(content, str)
        self.payloads.append(content)
        return "Working on the request."


class _CallsModel:
    def __init__(self, calls: tuple[tuple[str, dict[str, object]], ...]) -> None:
        self.calls = calls

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        answered = any(
            isinstance(message.content, tuple)
            and any(isinstance(block, ToolResultBlock) for block in message.content)
            for message in request.messages
        )
        if answered:
            yield TextDelta(text="done")
            yield Usage(input_tokens=1, output_tokens=1)
            return
        for index, (name, args) in enumerate(self.calls):
            call_id = f"c{index + 1}"
            yield ToolCallStart(id=call_id, name=name)
            yield ToolCallDelta(id=call_id, partial_json=json.dumps(args))
        yield Usage(input_tokens=1, output_tokens=1)


def _action_model(action: str, kind: str = sample.WIDGET_KIND, **wire: object) -> _CallsModel:
    return _CallsModel((("object_action", {"kind": kind, "action": action, **wire}),))


class _StubCarrier:
    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def write(self, handle: SandboxHandle, path: str, content: bytes) -> None: ...

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this test")


async def _seed_turn(
    *,
    speaker: bool = False,
    admin: bool = True,
    main: bool = True,
    admission_source: str = MEMBER_ADMISSION,
    inbound: str = "hi",
) -> Turn:
    workspace_id, member_id, agent_id, conversation_id, turn_id = (uuid4() for _ in range(5))
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
                is_admin=admin,
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
                is_main=main,
                visibility="workspace",
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
        await connection.execute(
            sa.insert(tables.turn).values(
                id=turn_id,
                workspace_id=workspace_id,
                conversation_id=conversation_id,
                agent_id=agent_id,
                seq=1,
                status="queued",
                inbound=inbound,
                admission_source=admission_source,
                speaker_member_id=member_id if speaker else None,
                terminal=None,
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
        inbound=inbound,
        admission_source=admission_source,  # type: ignore[arg-type]
        speaker_member_id=member_id if speaker else None,
        created_at=datetime(2026, 8, 27, tzinfo=UTC),
        terminal=None,
    )


async def _seed_member(workspace_id: UUID, *, admin: bool) -> UUID:
    member_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.member).values(
                id=member_id,
                workspace_id=workspace_id,
                email=f"{member_id.hex[:8]}@b.c",
                is_admin=admin,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return member_id


async def _seed_agent(
    workspace_id: UUID, name: str, *, visibility: str, owner_member_id: UUID | None = None
) -> UUID:
    agent_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.agent).values(
                id=agent_id,
                workspace_id=workspace_id,
                name=name,
                prompt="p",
                model="claude-opus-4-8",
                visibility=visibility,
                owner_member_id=owner_member_id,
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return agent_id


def _sample_toolset() -> tuple[tuple[ToolDef, ...], dict, ObjectVerbs]:
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    return turn_tools(
        (manifest,),
        CredentialStore(fernet=Fernet(Fernet.generate_key())),
        audience=conversation_audience(None),
    )


def _engine(
    turn: Turn,
    model: object,
    tmp_path: Path,
    granted: frozenset[str] | None = None,
    hooks: HookChain | None = None,
) -> TurnEngine:
    all_tools, tool_ext, verbs = _sample_toolset()
    granted_actions = (
        _agent_actions(verbs.actions, None, MEMBER_ADMISSION) if granted is None else granted
    )
    registry = ToolRegistry(
        _with_action_verbs(
            _agent_tools(all_tools, None, MEMBER_ADMISSION), all_tools, granted_actions
        )
    )
    blob = FilesystemBlobStore(root=tmp_path)
    handle = SandboxHandle(conversation_id=turn.conversation_id, container_id="test")
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    return TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        byok=False,
        system_prompt=rendered_prompt("p"),
        model=model,
        activity_summarizer=ActivitySummarizer(_ActivityModel()),
        provider="anthropic",
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model, model="claude-opus-4-8", blob=blob, conversation_id=turn.conversation_id
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=_StubCarrier(), handle=handle),
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=registry,
        tool_ext=tool_ext,
        hooks=hooks
        or turn_hooks(
            (manifest,),
            CredentialStore(fernet=Fernet(Fernet.generate_key())),
            audience=conversation_audience(None),
        ),
        blob=blob,
        spawn=_unavailable_spawn,
        audience=conversation_audience(None),
        artifact_token_secret="",
        grants=None,
        verbs=verbs,
        granted_actions=granted_actions,
    )


async def _seed_widget(name: str = "anvil") -> UUID:
    generation = uuid4()
    now = datetime.now(UTC)
    await ScopedStore(extension=sample.NAME).put(
        sample.WIDGET_KEY_PREFIX + name,
        sample.StoredWidget(
            spec=sample.WidgetSpec(color="teal", size=1),
            created_at=now,
            updated_at=now,
            generation=generation,
        ).model_dump(mode="json"),
    )
    return generation


async def _tool_results(engine: TurnEngine) -> tuple[ToolResultBlock, ...]:
    stored = await engine.transcript.read()
    assert stored is not None
    return next(
        tuple(block for block in message.content if isinstance(block, ToolResultBlock))
        for message in stored.messages
        if isinstance(message.content, tuple)
        and any(isinstance(block, ToolResultBlock) for block in message.content)
    )


async def test_instance_action_reaches_its_handler_with_the_resolved_target(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        generation = await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.POLISH_ACTION, name="anvil", input={"coats": 3}),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY)
        assert recorded == {
            "coats": 3,
            "target": {
                "kind": sample.WIDGET_KIND,
                "name": "anvil",
                "agent": None,
                "generation": str(generation),
                "expected_generation": None,
            },
        }


async def test_kind_owner_visibility_runs_before_contributor_code(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = _engine(turn, _action_model(sample.POLISH_ACTION, name="missing"), tmp_path)
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        [result] = await _tool_results(engine)
        assert result.is_error is True
        assert "no sample_widget object named 'missing'" in str(result.content)
        assert await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY) is None


async def test_cross_extension_action_runs_with_the_contributor_context(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = _engine(
            turn, _action_model(sample.AUDIT_ACTION, kind=sample.WORKSPACE_KIND), tmp_path
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.AUDIT_KEY)
        assert recorded == {
            "subject": "",
            "extension": sample.NAME,
            "target": {
                "kind": sample.WORKSPACE_KIND,
                "name": None,
                "agent": None,
                "generation": None,
                "expected_generation": None,
            },
        }


async def test_an_ungranted_action_is_refused_before_its_handler(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.POLISH_ACTION, name="anvil"),
            tmp_path,
            granted=frozenset({DIVINE_ID}),
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        [result] = await _tool_results(engine)
        assert result.is_error is True
        assert f"action {POLISH_ID} is not granted" in str(result.content)
        assert await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY) is None


async def test_arity_generation_and_agent_target_hold_before_dispatch(
    db: None, tmp_path: Path
) -> None:
    cases = (
        (_action_model(sample.POLISH_ACTION), "is an instance action"),
        (_action_model(sample.DIVINE_ACTION, name="anvil"), "takes no name"),
        (
            _action_model(sample.DIVINE_ACTION, generation=str(uuid4())),
            "takes no generation",
        ),
        (
            _action_model(
                sample.ENGRAVE_ACTION,
                name="anvil",
                generation=str(uuid4()),
                input={"text": "stale"},
            ),
            "changed after your read",
        ),
        (
            _action_model(sample.BLESS_ACTION, name="anvil", agent="other"),
            "takes no agent target",
        ),
        (
            _action_model("unheard_of"),
            "no action 'unheard_of' on kind 'sample_widget'",
        ),
        (
            _action_model(sample.POLISH_ACTION, kind="unregistered", name="anvil"),
            "no object kind 'unregistered'",
        ),
    )
    for model, expected in cases:
        turn = await _seed_turn()
        with ws(turn.workspace_id):
            await _seed_widget()
            engine = _engine(turn, model, tmp_path)
            frame = await engine.run()
            assert frame is not None and frame.status == "done"
            [result] = await _tool_results(engine)
            assert result.is_error is True, expected
            assert expected in str(result.content)
            assert await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY) is None


async def test_a_bad_requester_ref_rejects_the_call_before_dispatch(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.POLISH_ACTION, name="anvil", requested_by=str(uuid4())),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        [result] = await _tool_results(engine)
        assert result.is_error is True
        assert "does not name an active inbound message" in str(result.content)
        assert await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY) is None


def _dispatch_context(engine: TurnEngine) -> ToolContext:
    return ToolContext(
        sandbox=engine.sandbox,
        blob=engine.blob,
        turn=engine.turn,
        agent=engine.agent,
        spawn=engine.spawn,
        speaker_member_id=None,
        audience=engine.audience,
        artifact_token_secret="",
        granted_actions=engine.granted_actions,
    )


async def test_a_stale_generation_reaches_the_handler_beside_the_live_one(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        generation = await _seed_widget()
        stale = uuid4()
        engine = _engine(
            turn, _action_model(sample.POLISH_ACTION, name="anvil", generation=str(stale)), tmp_path
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY)
        assert recorded is not None
        assert recorded["target"]["generation"] == str(generation)
        assert recorded["target"]["expected_generation"] == str(stale)


async def test_an_interrupted_self_mutating_action_resumes_with_its_first_target(
    db: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        generation = await _seed_widget()
        engine = _engine(turn, _CallsModel(()), tmp_path)
        call = ToolUseBlock(
            id="c1",
            name="object_action",
            input={
                "kind": sample.WIDGET_KIND,
                "action": sample.ENGRAVE_ACTION,
                "name": "anvil",
                "generation": str(generation),
                "input": {"text": "once", "interrupt_once": True},
            },
        )
        bound = await engine._bind_or_error(
            _dispatch_context(engine), engine._resolve_call(call), {}
        )
        interrupted = await engine._dispatch_step(bound)
        assert interrupted.interrupted
        scoped = ScopedStore(extension=sample.NAME)
        first = await scoped.get(sample.ENGRAVE_KEY)
        assert first is not None
        minted = first["minted"]
        assert minted != str(generation)

        original_dispatch_step = TurnEngine._dispatch_step
        replayed = False

        async def replay_then_retry(
            dispatching: TurnEngine, retry_bound: object, target: object = None
        ) -> object:
            nonlocal replayed
            if not replayed:
                replayed = True
                return interrupted
            return await original_dispatch_step(dispatching, retry_bound, target)

        monkeypatch.setattr(TurnEngine, "_dispatch_step", replay_then_retry)
        result = await replace(engine)._dispatch_step_recovering(bound, [])
        assert replayed
        assert result.is_error is False
        assert result.text == "engraved 'once'"
        widget = await scoped.get(sample.WIDGET_KEY_PREFIX + "anvil")
        assert widget is not None
        assert widget["generation"] == minted
        again = await scoped.get(sample.ENGRAVE_KEY)
        assert again == first
        assert first["idempotency_key"] == f"{turn.id}/{ENGRAVE_ID}/c1"


async def test_an_agent_targetable_action_resolves_a_visible_agent_for_the_live_speaker(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(speaker=True)
    with ws(turn.workspace_id):
        await _seed_agent(turn.workspace_id, "helper", visibility="workspace")
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(
                sample.POLISH_ACTION,
                name="anvil",
                agent="helper",
                requested_by=str(turn.id),
            ),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY)
        assert recorded is not None
        assert recorded["target"]["agent"] == "helper"
        assert recorded["target"]["name"] == "anvil"

        tools, _, _ = _sample_toolset()
        get = next(tool for tool in tools if tool.name == "object_get")
        ctx = replace(_dispatch_context(engine), speaker_member_id=turn.speaker_member_id)
        result = await get.handler(
            ctx,
            get.input_model.model_validate(
                {"kind": sample.WIDGET_KIND, "name": "anvil", "agent": "helper"}
            ),
        )
        block = result.content[0]
        assert isinstance(block, TextContent)
        fetched = yaml.safe_load(block.text)
        assert fetched["agent"] == "helper"
        assert [view["name"] for view in fetched["actions"]] == [sample.POLISH_ACTION]
        assert [view["call"]["agent"] for view in fetched["actions"]] == ["helper"]
        for index, view in enumerate(fetched["actions"]):
            resolved = engine._resolve_call(
                ToolUseBlock(id=f"t{index + 1}", name="object_action", input=view["call"])
            )
            assert not isinstance(resolved, _RejectedToolCall), view["name"]

        own = await get.handler(
            ctx,
            get.input_model.model_validate({"kind": sample.WIDGET_KIND, "name": "anvil"}),
        )
        own_block = own.content[0]
        assert isinstance(own_block, TextContent)
        own_read = yaml.safe_load(own_block.text)
        assert [view["name"] for view in own_read["actions"]] == [
            sample.BLESS_ACTION,
            sample.ENGRAVE_ACTION,
            sample.POLISH_ACTION,
        ]
        assert all("agent" not in view["call"] for view in own_read["actions"])


async def test_the_cross_agent_gate_refuses_every_lane_but_the_main_agents_live_speaker(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn(speaker=True)
    with ws(turn.workspace_id):
        assert turn.speaker_member_id is not None
        await _seed_agent(turn.workspace_id, "helper", visibility="workspace")
        await _seed_agent(
            turn.workspace_id,
            "secret",
            visibility="private",
            owner_member_id=turn.speaker_member_id,
        )
        await _seed_widget()
        engine = _engine(turn, _CallsModel(()), tmp_path)
        polish = engine.verbs.actions[sample.WIDGET_KIND][sample.POLISH_ACTION].action
        speaking = replace(_dispatch_context(engine), speaker_member_id=turn.speaker_member_id)

        def wire(agent: str) -> ObjectActionInput:
            return ObjectActionInput(
                kind=sample.WIDGET_KIND, action=sample.POLISH_ACTION, name="anvil", agent=agent
            )

        admitted = await engine.verbs.action_target(speaking, polish, wire("helper"))
        assert admitted.agent is not None and admitted.agent.name == "helper"
        own = await engine.verbs.action_target(speaking, polish, wire("assistant"))
        assert own.agent is None
        unnamed = await engine.verbs.action_target(speaking, polish, wire(""))
        assert unnamed.agent is None

        speakerless = replace(speaking, speaker_member_id=None)
        with pytest.raises(ValueError, match="exact live member-requested call"):
            await engine.verbs.action_target(speakerless, polish, wire("helper"))

        subagent = replace(
            speaking, turn=turn.model_copy(update={"subagent_profile": sample.SUBAGENT_NAME})
        )
        with pytest.raises(ValueError, match="typed subagent may not target"):
            await engine.verbs.action_target(subagent, polish, wire("helper"))

        joiner = await _seed_member(turn.workspace_id, admin=False)
        non_admin = replace(speaking, speaker_member_id=joiner)
        with pytest.raises(ValueError, match="no agent named 'secret'"):
            await engine.verbs.action_target(non_admin, polish, wire("secret"))
        visible_to_all = await engine.verbs.action_target(non_admin, polish, wire("helper"))
        assert visible_to_all.agent is not None and visible_to_all.agent.name == "helper"

    child_turn = await _seed_turn(speaker=True, main=False)
    with ws(child_turn.workspace_id):
        await _seed_agent(child_turn.workspace_id, "helper", visibility="workspace")
        await _seed_widget()
        child = _engine(child_turn, _CallsModel(()), tmp_path)
        polish = child.verbs.actions[sample.WIDGET_KIND][sample.POLISH_ACTION].action
        speaking = replace(_dispatch_context(child), speaker_member_id=child_turn.speaker_member_id)
        with pytest.raises(ValueError, match="only the workspace main agent"):
            await child.verbs.action_target(
                speaking,
                polish,
                ObjectActionInput(
                    kind=sample.WIDGET_KIND,
                    action=sample.POLISH_ACTION,
                    name="anvil",
                    agent="helper",
                ),
            )


async def test_side_effecting_action_receives_the_semantic_idempotency_key(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.ENGRAVE_ACTION, name="anvil", input={"text": "ad astra"}),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.ENGRAVE_KEY)
        assert recorded is not None
        assert recorded["idempotency_key"] == f"{turn.id}/{ENGRAVE_ID}/c1"


class _DownStore:
    async def list(self, ctx: ToolContext, query: ObjectListQuery) -> ObjectPage:
        raise RuntimeError("store down")

    async def get(self, ctx: ToolContext, name: str) -> ObjectDetail[BaseModel] | None:
        raise RuntimeError("store down")

    async def status(
        self, ctx: ToolContext, name: str, *, expected_generation: UUID | None
    ) -> None:
        raise RuntimeError("store down")

    async def apply(
        self,
        ctx: ToolContext,
        name: str,
        spec: BaseModel,
        old: BaseModel | None,
        *,
        expected_generation: UUID | None,
    ) -> None:
        raise RuntimeError("store down")

    async def delete(
        self, ctx: ToolContext, name: str, *, expected_generation: UUID | None
    ) -> None:
        raise RuntimeError("store down")


class _DownSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")


async def _poke(ctx: ToolContext, args: _DownSpec) -> ToolResult:
    raise AssertionError("the handler must not run when the target read faults")


async def test_a_kind_store_fault_on_the_target_read_is_the_engines_failure(
    db: None, tmp_path: Path
) -> None:
    down = ObjectKind(
        name="downed", description="d", guidance="g", spec_model=_DownSpec, store=_DownStore()
    )
    poke = ToolDef(
        name="poke",
        description="d",
        input_model=_DownSpec,
        handler=_poke,
        bound=ObjectBinding(kind="downed", binding="instance"),
    )
    kinds = object_registry((BoundKind(kind=down, extension=None, context=None),))
    actions = action_registry((BoundAction(action=poke, extension=None, context=None),), kinds)
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = replace(
            _engine(turn, _action_model("poke", kind="downed", name="x"), tmp_path),
            verbs=ObjectVerbs(kinds, actions),
            granted_actions=frozenset({poke.canonical_id}),
        )
        with pytest.raises(RuntimeError, match="store down"):
            await engine.run()
        async with workspace_tx() as connection:
            terminal = (
                await connection.execute(
                    sa.select(tables.turn.c.terminal).where(tables.turn.c.id == turn.id)
                )
            ).scalar_one()
        assert terminal["status"] == "failed"
        assert terminal["error_class"] == "RuntimeError"
        assert terminal["error_message"] == "store down"


async def test_parallel_safe_actions_batch_and_a_malformed_call_stays_a_barrier(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        model = _CallsModel(
            (
                ("object_action", {"kind": sample.WIDGET_KIND, "action": sample.DIVINE_ACTION}),
                ("object_action", {"kind": sample.WIDGET_KIND, "action": "unheard_of"}),
                ("object_action", {"kind": sample.WIDGET_KIND, "action": sample.DIVINE_ACTION}),
            )
        )
        engine = _engine(turn, model, tmp_path)
        resolved = tuple(
            engine._resolve_call(ToolUseBlock(id=f"c{index + 1}", name=name, input=args))
            for index, (name, args) in enumerate(
                (
                    *model.calls,
                    (
                        "object_action",
                        {
                            "kind": sample.WIDGET_KIND,
                            "action": sample.DIVINE_ACTION,
                        },
                    ),
                    (
                        "object_action",
                        {
                            "kind": sample.WIDGET_KIND,
                            "action": sample.POLISH_ACTION,
                            "name": "anvil",
                        },
                    ),
                )
            )
        )
        segments = [
            tuple(item.call.id for item in segment) for segment in _dispatch_segments(resolved)
        ]
        assert segments == [("c1",), ("c2",), ("c3", "c4"), ("c5",)]
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        results = await _tool_results(engine)
        assert [result.tool_use_id for result in results] == ["c1", "c2", "c3"]
        assert [result.is_error for result in results] == [False, True, False]
        assert await ScopedStore(extension=sample.NAME).get(sample.DIVINE_KEY) is not None


async def test_rejections_meter_unregistered_only_before_the_action_resolves(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = _engine(turn, _CallsModel(()), tmp_path)
        unknown = engine._resolve_call(
            ToolUseBlock(
                id="u1",
                name="object_action",
                input={"kind": sample.WIDGET_KIND, "action": "unheard_of"},
            )
        )
        assert isinstance(unknown, _RejectedToolCall)
        assert unknown.dimensions == {"call": "unregistered"}
        arity = engine._resolve_call(
            ToolUseBlock(
                id="a1",
                name="object_action",
                input={"kind": sample.WIDGET_KIND, "action": sample.POLISH_ACTION},
            )
        )
        assert isinstance(arity, _RejectedToolCall)
        assert dict(arity.dimensions) == {
            "call": POLISH_ID,
            "kind": sample.WIDGET_KIND,
            "binding": "instance",
            "contributor": sample.NAME,
        }
        resolved = engine._resolve_call(
            ToolUseBlock(
                id="r1",
                name="object_action",
                input={"kind": sample.WIDGET_KIND, "action": sample.POLISH_ACTION, "name": "x"},
            )
        )
        assert isinstance(resolved, EffectiveCall)
        assert resolved.meter_dimensions()["call"] == POLISH_ID


async def test_hooks_match_canonical_ids_and_keep_modify_semantics(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        generation = await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.BLESS_ACTION, name="anvil", input={"phrase": "per aspera"}),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        scoped = ScopedStore(extension=sample.NAME)
        blessed = await scoped.get(sample.BLESS_KEY)
        assert blessed is not None
        assert blessed["phrase"] == "per aspera" + sample.BLESS_FOLD_SUFFIX
        pre = await scoped.get(sample.HOOK_BLESS_PRE_KEY)
        assert pre == {
            "call": sample.BLESS_CANONICAL_ID,
            "tool_name": "object_action",
            "target": {
                "kind": sample.WIDGET_KIND,
                "name": "anvil",
                "agent": None,
                "generation": str(generation),
                "expected_generation": None,
            },
        }
        post = await scoped.get(sample.HOOK_BLESS_POST_KEY)
        assert post is not None and post["call"] == sample.BLESS_CANONICAL_ID
        [result] = await _tool_results(engine)
        assert result.content == sample.BLESS_REWRITE


async def test_a_failing_action_fires_the_targeted_failure_hook(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.BLESS_ACTION, name="anvil", input={"fail": True}),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        scoped = ScopedStore(extension=sample.NAME)
        failure = await scoped.get(sample.HOOK_BLESS_FAILURE_KEY)
        assert failure is not None
        assert failure["call"] == sample.BLESS_CANONICAL_ID
        assert sample.BLESS_FAILURE in failure["output"]
        assert await scoped.get(sample.BLESS_KEY) is None
        assert await scoped.get(sample.HOOK_BLESS_POST_KEY) is None


async def test_untrusted_action_output_is_walled_under_the_canonical_id(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = _engine(turn, _action_model(sample.DIVINE_ACTION), tmp_path)
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        [result] = await _tool_results(engine)
        assert isinstance(result.content, str)
        assert UNTRUSTED_OPEN.format(source=DIVINE_ID) in result.content
        assert sample.DIVINATION in result.content


async def test_activity_summarizes_the_semantic_action_not_the_wire_name(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(
            turn,
            _action_model(sample.POLISH_ACTION, name="anvil", input={"coats": 2}),
            tmp_path,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        payloads = engine.activity_summarizer.model.payloads  # type: ignore[attr-defined]
        [payload] = [text for text in payloads if "polish" in text]
        assert POLISH_ID in payload
        assert "anvil" in payload
        assert '"object_action"' not in payload


async def test_final_acts_read_the_resolved_declaration_and_stay_hook_suppressible(
    db: None, tmp_path: Path
) -> None:
    member_id = uuid4()
    payload = ConnectRequest(provider="sample", requester_member_id=member_id)

    class _NoArgs(BaseModel): ...

    async def handoff(ctx: ToolContext, args: _NoArgs) -> ToolResult:
        text = "connect privately\n" + payload.model_dump_json()
        return ToolResult(content=(TextContent(text=text),))

    tool = ToolDef(
        name="handoff_probe",
        description="d",
        input_model=_NoArgs,
        handler=handoff,
        final_act_model=ConnectRequest,
    )

    async def rewrite(ctx: HookContext) -> HookOutcome:
        match ctx.payload:
            case PostToolUse(call="handoff_probe"):
                return ModifyOutput(output="rewritten past recognition")
        return None

    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = replace(
            _engine(turn, _CallsModel((("handoff_probe", {}),)), tmp_path),
            tools=ToolRegistry((tool,)),
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        assert frame.connect_request == payload

    suppressed_turn = await _seed_turn()
    with ws(suppressed_turn.workspace_id):
        chain = HookChain(
            hooks={
                "post_tool_use": (
                    BoundHook(
                        spec=HookSpec(
                            event="post_tool_use", handler=rewrite, tools=("handoff_probe",)
                        ),
                        ext=context_for("sample", frozenset()),
                    ),
                )
            },
            audience=conversation_audience(None),
        )
        engine = replace(
            _engine(suppressed_turn, _CallsModel((("handoff_probe", {}),)), tmp_path),
            tools=ToolRegistry((tool,)),
            hooks=chain,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        assert frame.connect_request is None


async def test_a_final_act_survives_the_dispatcher_and_a_hook_can_suppress_it(
    db: None, tmp_path: Path
) -> None:
    question = "Which widget should shine?"
    expected = AskUserInput(title=sample.BESEECH_TITLE, questions=(AskQuestion(question=question),))
    turn = await _seed_turn()
    with ws(turn.workspace_id):
        engine = _engine(
            turn, _action_model(sample.BESEECH_ACTION, input={"question": question}), tmp_path
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        assert frame.question == expected
        assert await ScopedStore(extension=sample.NAME).get(sample.BESEECH_KEY) is not None

    async def rewrite(ctx: HookContext) -> HookOutcome:
        return ModifyOutput(output="rewritten past recognition")

    suppressed_turn = await _seed_turn()
    with ws(suppressed_turn.workspace_id):
        chain = HookChain(
            hooks={
                "post_tool_use": (
                    BoundHook(
                        spec=HookSpec(
                            event="post_tool_use",
                            handler=rewrite,
                            tools=(f"action:{sample.WIDGET_KIND}:{sample.BESEECH_ACTION}",),
                        ),
                        ext=context_for("sample", frozenset()),
                    ),
                )
            },
            audience=conversation_audience(None),
        )
        engine = _engine(
            suppressed_turn,
            _action_model(sample.BESEECH_ACTION, input={"question": question}),
            tmp_path,
            hooks=chain,
        )
        frame = await engine.run()
        assert frame is not None and frame.status == "done"
        assert frame.question is None


async def test_a_speaking_prepared_intent_dispatches_the_action_verbatim(
    db: None, tmp_path: Path
) -> None:
    intent = ToolIntent(
        tool="object_action",
        input={
            "kind": sample.WIDGET_KIND,
            "action": sample.ENGRAVE_ACTION,
            "name": "anvil",
            "input": {"text": "sic itur"},
        },
    )
    turn = await _seed_turn(
        speaker=True, admission_source=INTENT_ADMISSION, inbound=intent.model_dump_json()
    )
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(turn, _CallsModel(()), tmp_path)
        frame = await engine.run_intent()
        assert frame is not None and frame.status == "done"
        recorded = await ScopedStore(extension=sample.NAME).get(sample.ENGRAVE_KEY)
        assert recorded is not None
        assert recorded["text"] == "sic itur"
        assert recorded["target"]["name"] == "anvil"


async def test_a_speaking_intent_refuses_an_action_with_no_presentation(
    db: None, tmp_path: Path
) -> None:
    intent = ToolIntent(
        tool="object_action",
        input={
            "kind": sample.WIDGET_KIND,
            "action": sample.POLISH_ACTION,
            "name": "anvil",
            "input": {"coats": 1},
        },
    )
    turn = await _seed_turn(
        speaker=True, admission_source=INTENT_ADMISSION, inbound=intent.model_dump_json()
    )
    with ws(turn.workspace_id):
        await _seed_widget()
        engine = _engine(turn, _CallsModel(()), tmp_path)
        frame = await engine.run_intent()
        assert frame is not None and frame.status == "failed"
        assert frame.error_class == "IntentRefused"
        assert frame.error_message is not None
        assert "declares no presentation" in frame.error_message
        assert await ScopedStore(extension=sample.NAME).get(sample.POLISH_KEY) is None


async def test_a_refused_intent_terminates_as_intent_refused(db: None, tmp_path: Path) -> None:
    intent = ToolIntent(
        tool="object_action",
        input={"kind": sample.WIDGET_KIND, "action": "unheard_of", "input": {}},
    )
    turn = await _seed_turn(
        speaker=True, admission_source=INTENT_ADMISSION, inbound=intent.model_dump_json()
    )
    with ws(turn.workspace_id):
        engine = _engine(turn, _CallsModel(()), tmp_path)
        frame = await engine.run_intent()
        assert frame is not None and frame.status == "failed"
        assert frame.error_class == "IntentRefused"
        assert frame.error_message is not None
        assert "no action 'unheard_of'" in frame.error_message


async def test_a_speakerless_bridge_intent_stays_inside_the_action_grant(
    db: None, tmp_path: Path
) -> None:
    bridge = ToolBridgeIntent(
        request_id=uuid4(),
        tool="object_action",
        input={"kind": sample.WORKSPACE_KIND, "action": sample.AUDIT_ACTION, "input": {}},
    )
    turn = await _seed_turn(
        speaker=False, admission_source=INTENT_ADMISSION, inbound=bridge.model_dump_json()
    )
    with ws(turn.workspace_id):
        engine = _engine(turn, _CallsModel(()), tmp_path, granted=frozenset({AUDIT_ID}))
        frame = await engine.run_intent()
        assert frame is not None and frame.status == "done"
        assert await ScopedStore(extension=sample.NAME).get(sample.AUDIT_KEY) is not None

    refused_turn = await _seed_turn(
        speaker=False, admission_source=INTENT_ADMISSION, inbound=bridge.model_dump_json()
    )
    with ws(refused_turn.workspace_id):
        engine = _engine(refused_turn, _CallsModel(()), tmp_path, granted=frozenset())
        frame = await engine.run_intent()
        assert frame is not None and frame.status == "failed"
        assert frame.error_class == "IntentRefused"
        assert frame.error_message is not None
        assert "is not granted" in frame.error_message


def test_bridge_lists_the_dispatcher_and_gates_it_on_the_action_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = next(m for m in load_manifests() if m.name == sample.NAME)
    tools = bridge_tools((manifest,))
    dispatcher = next(tool for tool in tools if tool.name == "object_action")
    actions = validate_ext_tools((manifest,), CredentialStore(fernet=Fernet(Fernet.generate_key())))
    bridge = ToolBridge(
        dbos=None,  # type: ignore[arg-type]
        tailer=None,  # type: ignore[arg-type]
        tools=tools,
        subagents=SubagentRegistry((sample.manifest().subagents[0],)),
        subagent_grants={},
        actions=actions,
    )
    Parent = namedtuple("Parent", ("subagent_profile", "tools"))
    assert bridge._allowed(Parent(None, None), dispatcher) is True
    assert bridge._allowed(Parent(None, (POLISH_ID,)), dispatcher) is True
    assert bridge._allowed(Parent(None, ("bash",)), dispatcher) is False
    profile_parent = Parent(sample.SUBAGENT_NAME, None)
    assert bridge._allowed(profile_parent, dispatcher) is False
    granted = ToolBridge(
        dbos=None,  # type: ignore[arg-type]
        tailer=None,  # type: ignore[arg-type]
        tools=tools,
        subagents=SubagentRegistry((sample.manifest().subagents[0],)),
        subagent_grants={sample.SUBAGENT_NAME: frozenset({POLISH_ID})},
        actions=actions,
    )
    assert granted._allowed(profile_parent, dispatcher) is True
    reads = [tool for tool in tools if tool.name in {"object_list", "object_get"}]
    assert len(reads) == 2
    for read in reads:
        assert bridge._allowed(Parent(None, (POLISH_ID,)), read) is True
        assert bridge._allowed(Parent(None, ("bash",)), read) is False
        assert bridge._allowed(Parent(None, ("bash", read.name)), read) is True
        assert bridge._allowed(profile_parent, read) is False
        assert granted._allowed(profile_parent, read) is True
    writes = [tool for tool in tools if tool.name in {"object_apply", "object_delete"}]
    assert len(writes) == 2
    for write in writes:
        assert bridge._allowed(Parent(None, (POLISH_ID,)), write) is False
        assert granted._allowed(profile_parent, write) is False
    monkeypatch.setitem(IMPLIED_GRANTS, "load_skill", ("skill_search", POLISH_ID))
    assert bridge._allowed(Parent(None, ("load_skill",)), dispatcher) is True
    implied = ToolBridge(
        dbos=None,  # type: ignore[arg-type]
        tailer=None,  # type: ignore[arg-type]
        tools=tools,
        subagents=SubagentRegistry((sample.manifest().subagents[0],)),
        subagent_grants={sample.SUBAGENT_NAME: frozenset({"load_skill"})},
        actions=actions,
    )
    assert implied._allowed(profile_parent, dispatcher) is True
