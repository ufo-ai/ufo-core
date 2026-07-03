from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.hub import InProcessHub
from selfhost.loop.compaction import COMPACTED_CONTEXT_PREFIX, Compaction
from selfhost.loop.engine import TurnEngine
from selfhost.loop.transcript import Conversation, Transcript
from selfhost.models.interface import (
    Message,
    ModelEvent,
    ModelRequest,
    TextDelta,
    ToolCallDelta,
    ToolCallStart,
    ToolResultBlock,
    ToolUseBlock,
)
from selfhost.sandbox.session import ExecResult, SandboxHandle, SandboxSession, SandboxSpec
from selfhost.schema import tables
from selfhost.schema.records import Agent, TerminalFrame, Turn, Usage
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult
from selfhost.tools.registry import ToolRegistry


@dataclass(frozen=True)
class EchoModel:
    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        yield TextDelta(text="answer")
        yield Usage(input_tokens=7, output_tokens=3)


@dataclass(frozen=True)
class CancelRacingModel:
    """Stands in for the model while the cancel endpoint wins the race mid-round."""

    turn_id: UUID

    async def complete(self, request: ModelRequest) -> AsyncIterator[ModelEvent]:
        frame = TerminalFrame(status="cancelled")
        async with workspace_tx() as connection:
            await connection.execute(
                sa.update(tables.turn)
                .values(
                    status="cancelled",
                    terminal=frame.model_dump(mode="json"),
                    updated_at=sa.func.now(),
                )
                .where(
                    tables.turn.c.id == self.turn_id,
                    tables.turn.c.status.in_(("queued", "running")),
                )
            )
        yield TextDelta(text="answer")
        yield Usage(input_tokens=7, output_tokens=3)


@dataclass(frozen=True)
class ToolCallingModel:
    """Emits one bash tool call, then answers with text once the tool result comes back — so the
    engine's multi-round dispatch loop runs end to end without a real model."""

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
        yield ToolCallStart(id="c1", name="bash")
        yield ToolCallDelta(id="c1", partial_json='{"command": "echo hi"}')
        yield Usage(input_tokens=2, output_tokens=2)


@dataclass
class RecordingCarrier:
    """Stands in for the Docker carrier: records each exec argv and returns a canned result, so a
    tool call is dispatched through the real SandboxSession without a container."""

    result: ExecResult = field(
        default_factory=lambda: ExecResult(stdout="", stderr="", exit_code=0)
    )
    calls: list[tuple[str, ...]] = field(default_factory=list)

    async def create(self, spec: SandboxSpec) -> SandboxHandle:
        return SandboxHandle(conversation_id=spec.conversation_id, container_id="test")

    async def exec(
        self, handle: SandboxHandle, argv: tuple[str, ...], stdin: bytes, timeout_s: int
    ) -> ExecResult:
        self.calls.append(argv)
        return self.result

    async def route(self, handle: SandboxHandle, port: int) -> str:
        return "http://test"

    async def destroy(self, handle: SandboxHandle) -> None: ...


async def _seed_turn(status: str, terminal: TerminalFrame | None, seq: int = 1) -> Turn:
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
                seq=seq,
                status=status,
                inbound="hi",
                terminal=None if terminal is None else terminal.model_dump(mode="json"),
                created_at=sa.func.now(),
                updated_at=sa.func.now(),
            )
        )
    return Turn(
        id=turn_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        agent_id=agent_id,
        seq=seq,
        status=status,
        inbound="hi",
        terminal=terminal,
    )


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in this engine test")


def _engine(
    turn: Turn,
    model: object,
    tmp_path: Path,
    carrier: RecordingCarrier | None = None,
    compaction: Compaction | None = None,
) -> TurnEngine:
    carrier = carrier or RecordingCarrier()
    blob = FilesystemBlobStore(root=tmp_path)
    handle = SandboxHandle(conversation_id=turn.conversation_id, container_id="test")
    return TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        model=model,
        transcript=Transcript(blob=blob, conversation_id=turn.conversation_id),
        compaction=compaction
        or Compaction(
            client=model, model="claude-opus-4-8", blob=blob, conversation_id=turn.conversation_id
        ),
        hub=InProcessHub(),
        sandbox=SandboxSession(carrier=carrier, handle=handle),
        tools=ToolRegistry(BUILTIN_TOOLS),
        tool_ext={},
        blob=blob,
        spawn=_unavailable_spawn,
    )


async def test_already_terminal_turn_republishes_without_clobbering_transcript(
    db: None, tmp_path: Path
) -> None:
    stored = TerminalFrame(status="done", text="original")
    turn = await _seed_turn("done", stored)
    engine = _engine(turn, EchoModel(), tmp_path)
    done_transcript = Conversation(
        seq=turn.seq,
        messages=(Message(role="user", content="q"), Message(role="assistant", content="a")),
    )
    await engine.transcript.write(done_transcript)
    frame = await engine.run()
    assert frame == stored
    async with workspace_tx() as connection:
        billed = (
            await connection.execute(
                sa.select(sa.func.count())
                .select_from(tables.ledger)
                .where(tables.ledger.c.turn_id == turn.id)
            )
        ).scalar_one()
    assert billed == 0
    assert await engine.transcript.read() == done_transcript


async def test_tool_call_round_dispatches_in_sandbox_then_answers(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None)
    carrier = RecordingCarrier(result=ExecResult(stdout="hi\n", stderr="", exit_code=0))
    engine = _engine(turn, ToolCallingModel(), tmp_path, carrier=carrier)
    frame = await engine.run()
    assert frame.status == "done"
    assert frame.text == "done"
    assert any("echo hi" in " ".join(argv) for argv in carrier.calls)
    stored = await engine.transcript.read()
    assert stored is not None
    tool_use = stored.messages[1].content
    tool_result = stored.messages[2].content
    assert isinstance(tool_use, tuple) and isinstance(tool_use[0], ToolUseBlock)
    assert tool_use[0].name == "bash"
    assert isinstance(tool_result, tuple) and isinstance(tool_result[0], ToolResultBlock)
    assert "hi" in tool_result[0].content
    assert stored.messages[-1] == Message(role="assistant", content="done")


async def test_engine_compacts_history_before_the_round_and_bills_the_summary(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None, seq=2)
    blob = FilesystemBlobStore(root=tmp_path)
    transcript = Transcript(blob=blob, conversation_id=turn.conversation_id)
    await transcript.write(
        Conversation(
            seq=1,
            messages=tuple(
                Message(
                    role="user" if index % 2 == 0 else "assistant",
                    content=f"history {index} " + "y" * 80,
                )
                for index in range(6)
            ),
        )
    )
    compaction = Compaction(
        client=EchoModel(),
        model="claude-opus-4-8",
        blob=blob,
        conversation_id=turn.conversation_id,
        trigger_tokens=10,
        keep_messages=2,
    )
    engine = _engine(turn, EchoModel(), tmp_path, compaction=compaction)
    frame = await engine.run()
    assert frame.status == "done"
    assert frame.tokens == 20
    stored = await transcript.read()
    assert stored is not None and stored.seq == 2
    assert isinstance(stored.messages[0].content, str)
    assert stored.messages[0].content.startswith(COMPACTED_CONTEXT_PREFIX)
    record = await compaction.read_record(1)
    assert record is not None
    assert any("history 0" in str(message.content) for message in record.before)


async def test_compaction_fires_mid_round_when_a_tool_loop_grows_the_window(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None)
    carrier = RecordingCarrier(result=ExecResult(stdout="ok", stderr="", exit_code=0))
    compaction = Compaction(
        client=EchoModel(),
        model="claude-opus-4-8",
        blob=FilesystemBlobStore(root=tmp_path),
        conversation_id=turn.conversation_id,
        trigger_tokens=1,
        keep_messages=2,
    )
    engine = _engine(turn, ToolCallingModel(), tmp_path, carrier=carrier, compaction=compaction)
    frame = await engine.run()
    assert frame.status == "done"
    assert await compaction.read_record(1) is not None


async def test_cancel_winning_mid_round_keeps_cancelled_terminal_bills_and_preserves_inbound(
    db: None, tmp_path: Path
) -> None:
    turn = await _seed_turn("queued", None)
    engine = _engine(turn, CancelRacingModel(turn_id=turn.id), tmp_path)
    frame = await engine.run()
    assert frame.status == "cancelled"
    async with workspace_tx() as connection:
        row = (
            await connection.execute(
                sa.select(tables.turn.c.status, tables.ledger.c.amount)
                .select_from(tables.turn.join(tables.ledger, isouter=True))
                .where(tables.turn.c.id == turn.id)
            )
        ).one()
    assert row.status == "cancelled"
    assert int(row.amount) == 10
    stored = await engine.transcript.read()
    assert stored is not None
    assert stored.messages == (Message(role="user", content="hi"),)
