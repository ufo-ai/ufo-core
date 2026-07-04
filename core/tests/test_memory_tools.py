import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_embed_openai import EMBED_DIM
from selfhost_ext_index_default import DefaultIndex

from selfhost.blob import FilesystemBlobStore
from selfhost.db import workspace_tx
from selfhost.indexing import TextChunker
from selfhost.memory.indexer import MemoryIndexer
from selfhost.memory.service import MemoryService, member_subject
from selfhost.models.interface import Message, TextBlock, ToolUseBlock
from selfhost.schema import tables
from selfhost.schema.records import Agent, Turn
from selfhost.tools.builtins import BUILTIN_TOOLS, MEMORY_SEARCH_LIMIT, _date_bound
from selfhost.tools.context import SpawnResult, ToolContext, ToolResult
from selfhost.tools.registry import ToolRegistry
from selfhost.transcript import Conversation, encode, transcript_key

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


class BrokenEmbed:
    """A query-embed that always fails, so recall degrades to its lexical leg — the multi-query
    tests need each query to surface only its own lexical matches, not every seeded item at once."""

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("embed provider unreachable")


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
    index = DefaultIndex(embed=embed, transaction=workspace_tx)
    service = MemoryService(index=index, embed=embed)
    indexer = MemoryIndexer(
        index=index,
        embed=embed,
        chunker=TextChunker(),
        postgres=database_url.startswith("postgresql"),
    )
    return service, indexer


def _wire_lexical(database_url: str) -> tuple[MemoryService, MemoryIndexer]:
    """Index chunks with a working embed but recall through a broken one, so each query matches
    only its lexical terms — the setup the multi-query merge and bound assertions rely on."""
    working = StubEmbed(vec((0, 1.0)))
    index = DefaultIndex(embed=working, transaction=workspace_tx)
    service = MemoryService(index=index, embed=BrokenEmbed())
    indexer = MemoryIndexer(
        index=index,
        embed=working,
        chunker=TextChunker(),
        postgres=database_url.startswith("postgresql"),
    )
    return service, indexer


def _context(
    memory: MemoryService,
    member_id: UUID | None,
    tmp_path: Path,
    *,
    workspace_id: UUID | None = None,
    blob: FilesystemBlobStore | None = None,
) -> ToolContext:
    """A fresh per-turn context: `memory` and `member_id` matter to search/update, while
    `workspace_id` and `blob` matter to load_sessions — the sandbox is left unset so a memory tool
    that reached it would fail loud here."""
    return ToolContext(
        sandbox=None,
        blob=blob if blob is not None else FilesystemBlobStore(root=tmp_path),
        turn=Turn(
            id=uuid4(),
            workspace_id=workspace_id if workspace_id is not None else uuid4(),
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

    found = await _run(
        "memory_search", _context(memory, member, tmp_path), queries=["hunter2"]
    )
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
    result = await _run(
        "memory_search", _context(memory, uuid4(), tmp_path), queries=["anything"]
    )
    assert result.content[0].text == "No matching memory."


def test_date_bound_reads_a_bare_end_date_as_the_whole_day() -> None:
    assert _date_bound(None, end=False) is None
    assert _date_bound("2026-01-31", end=False) == datetime(2026, 1, 31, tzinfo=UTC)
    assert _date_bound("2026-01-31", end=True) == datetime(2026, 2, 1, tzinfo=UTC)
    assert _date_bound("2026-01-31T12:00:00", end=True) == datetime(2026, 1, 31, 12, tzinfo=UTC)


async def test_memory_search_merges_and_dedups_across_queries(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    member = uuid4()
    memory, indexer = _wire_lexical(database_url)
    ctx = _context(memory, member, tmp_path)
    for body in ("apple orchard notes", "banana bread recipe", "apple and banana smoothie"):
        await _run("memory_update", ctx, body=body)
    await indexer.run()

    found = await _run("memory_search", ctx, queries=["apple", "banana"])
    text = found.content[0].text
    assert "apple orchard notes" in text
    assert "banana bread recipe" in text
    assert text.count("apple and banana smoothie") == 1


async def test_memory_search_bounds_the_merged_result_across_queries(
    clean: None, database_url: str, tmp_path: Path
) -> None:
    await _workspace()
    member = uuid4()
    memory, indexer = _wire_lexical(database_url)
    ctx = _context(memory, member, tmp_path)
    for index in range(6):
        await _run("memory_update", ctx, body=f"alpha memo {index}")
        await _run("memory_update", ctx, body=f"beta memo {index}")
    await indexer.run()

    found = await _run("memory_search", ctx, queries=["alpha", "beta"])
    assert len(found.content[0].text.splitlines()) == MEMORY_SEARCH_LIMIT


async def test_load_sessions_returns_named_transcripts_and_reports_the_rest(
    db: None, database_url: str, tmp_path: Path
) -> None:
    workspace_id = await _workspace()
    blob = FilesystemBlobStore(root=tmp_path)
    member, other = uuid4(), uuid4()
    conversation_id, other_conversation_id, unknown = uuid4(), uuid4(), uuid4()
    async with workspace_tx() as connection:
        for member_id, email in ((member, "a@example.test"), (other, "b@example.test")):
            await connection.execute(
                sa.insert(tables.member).values(
                    id=member_id,
                    workspace_id=workspace_id,
                    email=email,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        for cid, owner in ((conversation_id, member), (other_conversation_id, other)):
            await connection.execute(
                sa.insert(tables.conversation).values(
                    id=cid,
                    workspace_id=workspace_id,
                    surface="slack",
                    queue_key=cid.hex,
                    member_id=owner,
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
    await blob.put(
        transcript_key(conversation_id),
        encode(
            Conversation(
                seq=2,
                messages=(
                    Message(role="user", content="remind me about the launch"),
                    Message(
                        role="assistant",
                        content=(
                            ToolUseBlock(id="t1", name="memory_search", input={"queries": ["x"]}),
                            TextBlock(text="the launch is march 3"),
                        ),
                    ),
                ),
            )
        ),
    )
    await blob.put(
        transcript_key(other_conversation_id),
        encode(Conversation(seq=1, messages=(Message(role="user", content="private"),))),
    )
    memory, _ = _wire(database_url, vec((0, 1.0)))
    ctx = _context(memory, member, tmp_path, workspace_id=workspace_id, blob=blob)

    result = await _run(
        "load_sessions",
        ctx,
        session_ids=[str(conversation_id), str(other_conversation_id), str(unknown)],
    )
    payload = json.loads(result.content[0].text)

    assert [session["session_id"] for session in payload["sessions"]] == [str(conversation_id)]
    session = payload["sessions"][0]
    assert session["surface"] == "slack"
    assert [message["text"] for message in session["messages"]] == [
        "remind me about the launch",
        "the launch is march 3",
    ]
    assert "memory_search" not in result.content[0].text
    assert set(payload["failed"]) == {str(other_conversation_id), str(unknown)}
