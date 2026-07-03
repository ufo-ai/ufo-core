from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

import sqlalchemy as sa

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.hub import InProcessHub
from selfhost.loop.engine import TurnEngine
from selfhost.loop.transcript import Transcript
from selfhost.models import ModelEvent, ModelRequest, TextDelta
from selfhost.schema import tables
from selfhost.schema.records import Agent, TerminalFrame, Turn, Usage


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


async def _seed_turn(status: str, terminal: TerminalFrame | None) -> Turn:
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
        seq=1,
        status=status,
        inbound="hi",
        terminal=terminal,
    )


def _engine(turn: Turn, model: object, tmp_path: Path) -> TurnEngine:
    return TurnEngine(
        turn=turn,
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        model=model,
        transcript=Transcript(
            blob=FilesystemBlobStore(root=tmp_path), conversation_id=turn.conversation_id
        ),
        hub=InProcessHub(),
    )


async def test_already_terminal_turn_republishes_without_side_effects(
    db: None, tmp_path: Path
) -> None:
    stored = TerminalFrame(status="done", text="original")
    turn = await _seed_turn("done", stored)
    engine = _engine(turn, EchoModel(), tmp_path)
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
    assert await engine.transcript.read() is None


async def test_cancel_winning_mid_round_keeps_cancelled_terminal_and_bills(
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
    assert await engine.transcript.read() is None
