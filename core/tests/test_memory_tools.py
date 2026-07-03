from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.memory.chunk import TextChunker
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import index_backend_for
from selfhost.memory.indexer import MemoryIndexer
from selfhost.memory.service import MemoryService, member_subject
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS
from selfhost.tools.context import SpawnResult, ToolContext, ToolResult
from selfhost.tools.registry import ToolRegistry

REGISTRY = ToolRegistry(BUILTIN_TOOLS)


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


async def _unavailable_spawn(
    profile: str, payload: dict[str, object], background: bool = False
) -> SpawnResult:
    raise RuntimeError("spawn is not wired in the memory tool tests")


@pytest.fixture
async def clean(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    yield


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


def _wire(database_url: str, vector: tuple[float, ...]) -> tuple[MemoryService, MemoryIndexer]:
    embed = StubEmbed(vector)
    index = index_backend_for(database_url, embed)
    service = MemoryService(index=index, embed=embed)
    indexer = MemoryIndexer(
        index=index,
        embed=embed,
        chunker=TextChunker(),
        postgres=database_url.startswith("postgresql"),
    )
    return service, indexer


def _context(memory: MemoryService, member_id: UUID | None, tmp_path: Path) -> ToolContext:
    """A fresh per-turn context: only `memory` and `member_id` matter to the memory tools, so the
    sandbox is left unset — a memory tool that reached the sandbox would fail loud here."""
    return ToolContext(
        sandbox=None,
        blob=FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=uuid4(),
            conversation_id=uuid4(),
            agent_id=uuid4(),
            seq=1,
            status="running",
            inbound="hi",
        ),
        agent=Agent(prompt="p", model="claude-opus-4-8"),
        spawn=_unavailable_spawn,
        memory=memory,
        member_id=member_id,
        artifact_token_secret="",
    )


async def _run(name: str, ctx: ToolContext, **args: object) -> ToolResult:
    tool = REGISTRY.get(name)
    return await tool.handler(ctx, tool.input_model.model_validate(args))


async def test_memory_update_then_search_recalls_in_a_new_conversation(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    member = uuid4()
    memory, indexer = _wire(database_url, vec((6, 1.0)))

    stored = await _run(
        "memory_update", _context(memory, member, tmp_path), body="the deploy password is hunter2"
    )
    assert stored.is_error is False
    await indexer.run()

    found = await _run("memory_search", _context(memory, member, tmp_path), query="hunter2")
    assert "hunter2" in found.content[0].text


async def test_memory_update_scopes_to_member_by_default_and_shared_on_flag(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    member = uuid4()
    memory, _ = _wire(database_url, vec((0, 1.0)))
    ctx = _context(memory, member, tmp_path)
    await _run("memory_update", ctx, body="a private note")
    await _run("memory_update", ctx, body="a team note", shared=True)
    async with workspace_tx() as connection:
        subjects = sorted(
            row.subject
            for row in (await connection.execute(sa.select(tables.memory_item.c.subject))).all()
        )
    assert subjects == sorted([member_subject(member), "shared"])


async def test_memory_search_reports_no_match_on_empty_memory(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    memory, _ = _wire(database_url, vec((0, 1.0)))
    result = await _run("memory_search", _context(memory, uuid4(), tmp_path), query="anything")
    assert result.content[0].text == "No matching memory."
