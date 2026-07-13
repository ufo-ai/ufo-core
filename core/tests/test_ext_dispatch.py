from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from cryptography.fernet import Fernet
from pydantic import BaseModel

from ufo.blob import FilesystemBlobStore
from ufo.connectors import ConnectorRegistry
from ufo.credentials import CredentialStore
from ufo.db import workspace_tx
from ufo.ext.context import ExtensionContext, ScopedStore, context_for
from ufo.ext.loader import HookChain, turn_tools, validate_ext_tools
from ufo.ext.manifest import CredentialSlot, Manifest
from ufo.ext.surface import SurfaceSpec
from ufo.hub import InProcessHub
from ufo.loop.compaction import Compaction
from ufo.loop.engine import TurnEngine
from ufo.loop.prompts.render import rendered_prompt
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
from ufo.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from ufo.schema import tables
from ufo.schema.records import Agent, Turn, Usage
from ufo.tools.builtins import BUILTIN_TOOLS
from ufo.tools.context import SpawnResult, TextContent, ToolContext, ToolResult
from ufo.tools.registry import ToolDef, ToolRegistry
from ufo.workspace import ws

EXTENSION = "sample"


@dataclass(frozen=True)
class StubMemory:
    """Stand-in memory service: recall yields nothing, so these dispatch tests run the turn loop
    without asserting recall behavior."""

    async def recall(self, query: str, subjects: frozenset[str], limit: int) -> tuple:
        return ()

    async def commit(self, write: object) -> None:
        return None


class NoteInput(BaseModel):
    note: str


async def _record_note(ctx: ToolContext, args: NoteInput) -> ToolResult:
    assert ctx.ext is not None
    await ctx.ext.store.put("note", {"text": args.note})
    return ToolResult(content=(TextContent(text="recorded"),))


NOTE_TOOL = ToolDef(
    name="record_note",
    description="Persist a note through the extension's scoped store.",
    input_model=NoteInput,
    handler=_record_note,
)


class ProbeInput(BaseModel): ...


async def _report_ext(ctx: ToolContext, args: ProbeInput) -> ToolResult:
    text = "ext-is-none" if ctx.ext is None else f"ext-is-{ctx.ext.store.extension}"
    return ToolResult(content=(TextContent(text=text),))


async def _report_idem(ctx: ToolContext, args: ProbeInput) -> ToolResult:
    return ToolResult(content=(TextContent(text=f"key={ctx.idempotency_key}"),))


IDEM_READ_TOOL = ToolDef(
    name="idem_read",
    description="A read tool: reports the folded idempotency key (expected None).",
    input_model=ProbeInput,
    handler=_report_idem,
)
IDEM_WRITE_TOOL = ToolDef(
    name="idem_write",
    description="A side-effecting tool: reports the folded idempotency key.",
    input_model=ProbeInput,
    handler=_report_idem,
    side_effecting=True,
)


PROBE_TOOL = ToolDef(
    name="report_ext",
    description="Report whether the dispatched context carries an ExtensionContext.",
    input_model=ProbeInput,
    handler=_report_ext,
)


@dataclass(frozen=True)
class OneToolModel:
    """Emits a single named tool call with the given JSON args, then answers once its result
    returns — so the engine's dispatch runs end to end without a real model."""

    tool_name: str
    args_json: str = "{}"

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
        yield ToolCallStart(id="c1", name=self.tool_name)
        yield ToolCallDelta(id="c1", partial_json=self.args_json)
        yield Usage(input_tokens=1, output_tokens=1)


@dataclass(frozen=True)
class StubCarrier:
    """A carrier whose exec is never reached: the extension tool and probe never use the sandbox."""

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        return ExecResult(stdout="", stderr="", exit_code=0)

    async def destroy(self, handle: SandboxHandle) -> None: ...


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this test")


async def _seed_turn() -> Turn:
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
        inbound="hi",
        created_at=datetime(2026, 7, 9, tzinfo=UTC),
        terminal=None,
    )


def _engine(
    turn: Turn,
    model: object,
    tmp_path: Path,
    tools: tuple[ToolDef, ...],
    tool_ext: dict[str, ExtensionContext],
) -> TurnEngine:
    blob = FilesystemBlobStore(root=tmp_path)
    handle = SandboxHandle(conversation_id=turn.conversation_id, container_id="test")
    return TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        system_prompt=rendered_prompt("p"),
        model=model,
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=Compaction(
            client=model, model="claude-opus-4-8", blob=blob, conversation_id=turn.conversation_id
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=StubCarrier(), handle=handle),
        cdp_provider=None,
        search_provider=None,
        connectors=ConnectorRegistry(entries={}),
        tools=ToolRegistry(tools),
        tool_ext=tool_ext,
        hooks=HookChain(),
        blob=blob,
        spawn=_unavailable_spawn,
        audience_member_id=None,
        artifact_token_secret="",
        grants=None,
    )


async def test_extension_tool_runs_in_turn_with_its_scoped_context(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    context = context_for(EXTENSION, frozenset())
    engine = _engine(
        turn,
        OneToolModel(tool_name="record_note", args_json='{"note": "from the turn"}'),
        tmp_path,
        (*BUILTIN_TOOLS, NOTE_TOOL),
        {"record_note": context},
    )
    with ws(turn.workspace_id):
        frame = await engine.run()
        assert frame.status == "done"
        scoped = ScopedStore(extension=EXTENSION)
        assert await scoped.get("note") == {"text": "from the turn"}


async def test_builtin_shaped_tool_dispatches_with_ext_none(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn()
    engine = _engine(
        turn,
        OneToolModel(tool_name="report_ext"),
        tmp_path,
        (*BUILTIN_TOOLS, PROBE_TOOL),
        {},
    )
    frame = await engine.run()
    assert frame.status == "done"
    stored = await engine.transcript.read()
    assert stored is not None
    tool_result = stored.messages[2].content
    assert isinstance(tool_result, tuple) and isinstance(tool_result[0], ToolResultBlock)
    assert tool_result[0].content == "ext-is-none"
    assert tool_result[0].is_error is False
    tool_use = stored.messages[1].content
    assert isinstance(tool_use, tuple) and isinstance(tool_use[0], ToolUseBlock)
    assert tool_use[0].name == "report_ext"


async def test_side_effecting_tool_receives_the_stable_idempotency_key(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn()
    engine = _engine(
        turn, OneToolModel(tool_name="idem_write"), tmp_path, (*BUILTIN_TOOLS, IDEM_WRITE_TOOL), {}
    )
    frame = await engine.run()
    assert frame.status == "done"
    stored = await engine.transcript.read()
    assert stored is not None
    tool_result = stored.messages[2].content
    assert isinstance(tool_result, tuple) and isinstance(tool_result[0], ToolResultBlock)
    assert tool_result[0].content == f"key={turn.id}/idem_write/c1"


async def test_read_tool_receives_no_idempotency_key(db: None, tmp_path: Path) -> None:
    turn = await _seed_turn()
    engine = _engine(
        turn, OneToolModel(tool_name="idem_read"), tmp_path, (*BUILTIN_TOOLS, IDEM_READ_TOOL), {}
    )
    frame = await engine.run()
    assert frame.status == "done"
    stored = await engine.transcript.read()
    assert stored is not None
    tool_result = stored.messages[2].content
    assert isinstance(tool_result, tuple) and isinstance(tool_result[0], ToolResultBlock)
    assert tool_result[0].content == "key=None"


def test_turn_tools_maps_extension_tools_to_owning_context_and_leaves_builtins_unmapped() -> None:
    workspace_id = uuid4()
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    manifest = Manifest(
        name=EXTENSION,
        version="0.1.0",
        tools=(NOTE_TOOL,),
        credentials=(CredentialSlot(name="sample_api", description="key"),),
        surfaces=(SurfaceSpec(name="slack", routes=()),),
    )
    tools, ext_by_tool = turn_tools((manifest,), store)
    names = {tool.name for tool in tools}
    assert {builtin.name for builtin in BUILTIN_TOOLS} <= names
    assert "record_note" in names
    assert set(ext_by_tool) == {"record_note"}
    context = ext_by_tool["record_note"]
    assert context.store.extension == EXTENSION
    with ws(workspace_id):
        assert context.store.workspace_id == workspace_id
    assert context.credentials.declared == frozenset({"sample_api"})
    assert context.installations.declared == frozenset({"slack"})
    assert not any(builtin.name in ext_by_tool for builtin in BUILTIN_TOOLS)


def test_turn_tools_without_manifests_is_builtins_and_empty_map() -> None:
    tools, ext_by_tool = turn_tools((), None)
    assert tools == BUILTIN_TOOLS
    assert ext_by_tool == {}


def test_turn_tools_fails_loud_when_credential_slots_declared_without_key() -> None:
    manifest = Manifest(
        name=EXTENSION,
        version="0.1.0",
        tools=(NOTE_TOOL,),
        credentials=(CredentialSlot(name="k", description="key"),),
    )
    with pytest.raises(RuntimeError, match="declares credential slots"):
        turn_tools((manifest,), None)


def test_turn_tools_builds_slot_free_tools_without_a_credential_key() -> None:
    manifest = Manifest(name=EXTENSION, version="0.1.0", tools=(NOTE_TOOL,))
    tools, ext_by_tool = turn_tools((manifest,), None)
    assert NOTE_TOOL in tools
    assert NOTE_TOOL.name in ext_by_tool


def test_validate_ext_tools_rejects_a_name_colliding_with_a_builtin() -> None:
    collision = ToolDef(name="bash", description="dup", input_model=ProbeInput, handler=_report_ext)
    manifest = Manifest(
        name=EXTENSION,
        version="0.1.0",
        tools=(collision,),
        credentials=(CredentialSlot(name="k", description="key"),),
    )
    store = CredentialStore(fernet=Fernet(Fernet.generate_key()))
    with pytest.raises(ValueError, match="bash"):
        validate_ext_tools((manifest,), store)


def test_validate_ext_tools_fails_loud_without_a_credential_key_at_boot() -> None:
    manifest = Manifest(
        name=EXTENSION,
        version="0.1.0",
        tools=(NOTE_TOOL,),
        credentials=(CredentialSlot(name="k", description="key"),),
    )
    with pytest.raises(RuntimeError, match="credential"):
        validate_ext_tools((manifest,), None)
