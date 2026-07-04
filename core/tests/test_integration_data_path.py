"""Data/security-lane validation (phase #17): the memory data path and the metering→spend-cap
enforcement chain, exercised end to end against the REAL database and the REAL index / service /
sync driver / spend evaluator. The only stand-in is the embedding provider — a deterministic
`EmbedClient` double for the external, paid OpenAI embedding API — and it is never the thing
asserted: every assertion reads real rows, real recall results, and real spend decisions back from
the real backend. On the sqlite param this runs against the real DefaultIndex over SQLite FTS5; on
the postgres param against real Postgres + pgvector."""

from pathlib import Path
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from selfhost_ext_embed_openai import EMBED_DIM
from selfhost_ext_index_default import DefaultIndex
from selfhost_ext_memory.store import (
    MemoryIndexer,
    MemoryStore,
    MemoryWrite,
    recall_subjects,
)
from sqlalchemy.ext.asyncio import AsyncConnection

from selfhost.accounting import SpendEvaluator, record_sandbox_tokens
from selfhost.blob import FilesystemBlobStore
from selfhost.config import SourceConfig, SourceEntry
from selfhost.db import workspace_tx
from selfhost.indexing import TextChunker
from selfhost.memory.indexer import PageIndexer
from selfhost.memory.sources import FOLDER_BACKEND, FolderSource, SyncDriver, register_sources
from selfhost.schema import tables
from selfhost.schema.records import Usage
from selfhost.subjects import SHARED_SUBJECT, member_subject

pytestmark = pytest.mark.integration

# Enough opus tokens to price well past the small caps below (see test_accounting: this shape
# prices at 81_500 micro-USD), so a single metered call decisively breaches a 50 micro-USD cap.
HEAVY_USAGE = Usage(
    input_tokens=1000, output_tokens=2000, cache_read_tokens=3000, cache_write_tokens=4000
)


class StubEmbed:
    """Deterministic stand-in for the external OpenAI embedding API: every text embeds to the same
    unit vector, so the query and its document collide on the vector leg and recall is exact. A
    dependency of the index, never the thing asserted — the assertions read recalled bodies and
    spend decisions back from the real backend."""

    def __init__(self, axis: int) -> None:
        vector = [0.0] * EMBED_DIM
        vector[axis] = 1.0
        self._vector = tuple(vector)

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


async def _workspace() -> UUID:
    workspace_id = uuid4()
    async with workspace_tx() as connection:
        await connection.execute(
            sa.insert(tables.workspace).values(
                id=workspace_id, created_at=sa.func.now(), updated_at=sa.func.now()
            )
        )
    return workspace_id


async def _seed_billable(connection: AsyncConnection) -> tuple[UUID, UUID, UUID, UUID]:
    """A workspace with a member, an agent, and a conversation — the identities a spend cap scopes
    to and a metered turn attributes against."""
    workspace_id, member_id, agent_id, conversation_id = (uuid4() for _ in range(4))
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
    return workspace_id, member_id, agent_id, conversation_id


async def _seed_running_turn(
    connection: AsyncConnection, workspace_id: UUID, conversation_id: UUID, agent_id: UUID
) -> UUID:
    turn_id = uuid4()
    await connection.execute(
        sa.insert(tables.turn).values(
            id=turn_id,
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            agent_id=agent_id,
            seq=1,
            status="running",
            inbound="hi",
            terminal=None,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )
    return turn_id


async def _set_member_cap(
    connection: AsyncConnection,
    workspace_id: UUID,
    member_id: UUID,
    limit_micro_usd: int,
    on_breach: str,
) -> None:
    await connection.execute(
        sa.insert(tables.spend_cap).values(
            id=uuid4(),
            workspace_id=workspace_id,
            scope="member",
            subject_id=member_id,
            window_seconds=3600,
            limit_micro_usd=limit_micro_usd,
            on_breach=on_breach,
            created_at=sa.func.now(),
            updated_at=sa.func.now(),
        )
    )


async def _chunk_count() -> int:
    async with workspace_tx() as connection:
        return (await connection.execute(sa.text("select count(*) from chunk"))).scalar_one()


async def _clear_chunks(database_url: str) -> None:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))


async def test_folder_source_syncs_indexes_and_is_recalled(
    db: None, database_url: str, tmp_path: Path
) -> None:
    """A registered folder source syncs a document to a page, the page indexer embeds it to chunks,
    and `search_sources` recalls it — the whole source→index→recall data path over the real index
    and blob store."""
    await _clear_chunks(database_url)
    await _workspace()
    root = tmp_path / "src"
    root.mkdir()
    (root / "runbook.md").write_text("the incident escalation contact is the on-call captain")
    embed = StubEmbed(axis=1)
    index = DefaultIndex(embed=embed, transaction=workspace_tx)
    blob = FilesystemBlobStore(root=tmp_path / "blobs")
    postgres = database_url.startswith("postgresql")
    driver = SyncDriver(backends={FOLDER_BACKEND: FolderSource()}, blob=blob, postgres=postgres)
    page_indexer = PageIndexer(
        index=index, embed=embed, chunker=TextChunker(), blob=blob, postgres=postgres
    )
    service = MemoryStore(
        index=index, embed=embed, transaction=workspace_tx, workspace_id=uuid4()
    )
    await register_sources(
        (SourceEntry(backend=FOLDER_BACKEND, config=SourceConfig(root=str(root))),)
    )

    await driver.run()
    async with workspace_tx() as connection:
        page_count = (
            await connection.execute(sa.select(sa.func.count()).select_from(tables.page))
        ).scalar_one()
    assert page_count == 1
    assert await _chunk_count() == 0  # embedding is a job, never inline on the sync write

    await page_indexer.run()
    assert await _chunk_count() >= 1

    pages = await service.search_sources(
        "incident escalation contact", frozenset({SHARED_SUBJECT}), 5
    )
    assert len(pages) == 1
    assert "on-call captain" in pages[0].text


async def test_member_fact_recall_is_isolated_from_other_members(
    db: None, database_url: str
) -> None:
    """A committed member fact recalls for its owner after indexing but is invisible to another
    member — the {member, shared} isolation enforced in the real index query, end to end."""
    await _clear_chunks(database_url)
    workspace_id = await _workspace()
    alice, bob = uuid4(), uuid4()
    embed = StubEmbed(axis=2)
    index = DefaultIndex(embed=embed, transaction=workspace_tx)
    service = MemoryStore(
        index=index, embed=embed, transaction=workspace_tx, workspace_id=workspace_id
    )
    indexer = MemoryIndexer(
        index=index, embed=embed, transaction=workspace_tx, chunker=TextChunker()
    )
    await service.commit(
        MemoryWrite(subject=member_subject(alice), body="alice keeps the vault combination")
    )
    await indexer.run()

    mine = await service.recall("vault combination", recall_subjects(alice), 5)
    assert len(mine) == 1 and "alice" in mine[0].body
    assert await service.recall("vault combination", recall_subjects(bob), 5) == ()


async def test_metered_sandbox_tokens_breach_a_member_cap_and_park(db: None) -> None:
    """The billing-bug fix's output flows into enforcement: a `sandbox_tokens` ledger row written by
    `record_sandbox_tokens` (an in-sandbox model call metered through the proxy) counts toward the
    member's spend, so once it crosses a park cap the evaluator parks — proven against the real
    ledger and the real evaluator, no dimension allow-list."""
    async with workspace_tx() as connection:
        workspace_id, member_id, agent_id, conversation_id = await _seed_billable(connection)
        await _set_member_cap(
            connection, workspace_id, member_id, limit_micro_usd=50, on_breach="park"
        )
        evaluator = SpendEvaluator(workspace_id, member_id, agent_id)
        assert (await evaluator.decide(connection, 0)).outcome == "allow"

        turn_id = await _seed_running_turn(connection, workspace_id, conversation_id, agent_id)
        await record_sandbox_tokens(
            connection, workspace_id, turn_id, "claude-opus-4-8", HEAVY_USAGE
        )
        decision = await evaluator.decide(connection, 0)
    assert decision.outcome == "park"
    assert "parked" in decision.message
