from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from ufo_ext_embed_openai import EMBED_DIM
from ufo_ext_index_default import (
    DefaultIndex,
    pack_embedding,
    pgvector_literal,
    unpack_embedding,
)

from ufo.db import current_workspace, workspace_tx
from ufo.indexing import Chunk, IndexScope

WORKSPACE = UUID("11111111-1111-1111-1111-111111111111")
SUBJECT = "member:me"
FOREIGN = "member:other"


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


@pytest.fixture
async def clean_chunk(db: None, database_url: str) -> AsyncIterator[None]:
    token = current_workspace.set(WORKSPACE)
    try:
        async with workspace_tx() as connection:
            await connection.execute(sa.text("delete from chunk"))
            if database_url.startswith("sqlite"):
                await connection.execute(sa.text("delete from chunk_fts"))
        yield
    finally:
        current_workspace.reset(token)


async def test_upsert_lexical_and_vector_return_ordered_hits(
    clean_chunk: None, database_url: str
) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    e0 = vec((0, 1.0))
    e01 = vec((0, 1.0), (1, 1.0))
    await backend.upsert(
        (
            Chunk("d-alpha", "memory_item", "m1", SUBJECT, 0, "apple apple apple", e0),
            Chunk("d-beta", "memory_item", "m2", SUBJECT, 0, "apple cherry date", e01),
            Chunk("d-foreign", "memory_item", "m3", FOREIGN, 0, "apple apple apple", e0),
        )
    )

    lexical = await backend.lexical("apple", frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in lexical] == ["d-alpha", "d-beta"]
    assert all(hit.subject == SUBJECT for hit in lexical)

    vector = await backend.vector(vec((0, 1.0)), frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in vector] == ["d-alpha", "d-beta"]
    assert vector[0].score == pytest.approx(1.0, abs=1e-2)
    assert vector[1].score == pytest.approx(0.7071, abs=1e-2)


PUNCTUATED_RECALL_QUERIES = (
    "velvet, harbor.",
    'said "velvet harbor"',
    'she "said velvet',
    'said"velvet harbor',
    "velvet - harbor",
    "(velvet) harbor",
    "velvet AND harbor",
)
NO_TOKEN_QUERIES = ('"', '" "', ",")
LITERAL_MISS_QUERIES = ('vel"vet', "NEAR(wild rumpus)", "wild*", "^wild", "title:wild", "NOT wild")


async def test_lexical_recalls_through_punctuated_queries(
    clean_chunk: None, database_url: str
) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk(
                "d-punct",
                "memory_item",
                "m1",
                SUBJECT,
                0,
                "she said velvet harbor and moved on",
                vec((0, 1.0)),
            ),
        )
    )
    for query in PUNCTUATED_RECALL_QUERIES:
        hits = await backend.lexical(query, frozenset({SUBJECT}), "memory_item", 10)
        assert [hit.chunk_digest for hit in hits] == ["d-punct"], query
    for query in NO_TOKEN_QUERIES:
        assert await backend.lexical(query, frozenset({SUBJECT}), "memory_item", 10) == (), query
    for query in LITERAL_MISS_QUERIES:
        assert await backend.lexical(query, frozenset({SUBJECT}), "memory_item", 10) == (), query


async def test_foreign_subject_is_excluded(clean_chunk: None, database_url: str) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk("mine", "memory_item", "m1", SUBJECT, 0, "shared secret token", vec((0, 1.0))),
            Chunk("theirs", "memory_item", "m2", FOREIGN, 0, "shared secret token", vec((0, 1.0))),
        )
    )
    lexical = await backend.lexical("secret", frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in lexical] == ["mine"]
    vector = await backend.vector(vec((0, 1.0)), frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in vector] == ["mine"]


async def test_vector_returns_a_small_owner_kinds_rows_under_a_dominant_corpus(
    clean_chunk: None, database_url: str
) -> None:
    """The filtered vector leg returns exactly the best rows within the filter when the corpus is
    dominated by another owner kind's vectors sitting nearest the query — the shape where an
    approximate ordered scan starves (observed live: a two-chunk owner kind invisible in a 36k
    corpus). The order expression is structurally unservable by the HNSW index, so the result is
    exact on every plan; this pins the contract at real-database scale the suite can afford."""
    if database_url.startswith("sqlite"):
        pytest.skip("the starvation shape is Postgres-only")
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk("target-0", "memory_item", "t0", SUBJECT, 0, "ledger decision", vec((1, 1.0))),
            Chunk("target-1", "memory_item", "t1", SUBJECT, 0, "orion rollout", vec((1, 1.0))),
        )
    )
    async with workspace_tx() as connection:
        await connection.execute(
            sa.text(
                "insert into chunk"
                " (workspace_id, chunk_digest, owner_kind, owner_id, subject, ordinal, text,"
                " embedding)"
                " select current_setting('app.workspace_id')::uuid, 'noise-' || n, 'page',"
                " 'noise-' || n, :subject, 0, 'noise', cast(:embedding as halfvec)"
                " from generate_series(1, 1500) as n"
            ),
            {"subject": SUBJECT, "embedding": pgvector_literal(vec((0, 1.0)))},
        )
        await connection.execute(sa.text("analyze chunk"))
    hits = await backend.vector(vec((0, 1.0)), frozenset({SUBJECT}), "memory_item", 8)
    assert {hit.chunk_digest for hit in hits} == {"target-0", "target-1"}


async def test_delete_removes_only_its_scope(clean_chunk: None, database_url: str) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk("keep", "memory_item", "keep-owner", SUBJECT, 0, "apple", vec((0, 1.0))),
            Chunk("drop", "memory_item", "drop-owner", SUBJECT, 0, "apple", vec((0, 1.0))),
        )
    )
    await backend.delete(IndexScope("memory_item", "drop-owner"))
    lexical = await backend.lexical("apple", frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in lexical] == ["keep"]
    vector = await backend.vector(vec((0, 1.0)), frozenset({SUBJECT}), "memory_item", 10)
    assert [hit.chunk_digest for hit in vector] == ["keep"]


async def test_prune_drops_the_scopes_chunks_outside_the_keep_set(
    clean_chunk: None, database_url: str
) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk("stale", "page", "p1", SUBJECT, 0, "apple", vec((0, 1.0))),
            Chunk("fresh", "page", "p1", SUBJECT, 1, "apple", vec((0, 1.0))),
            Chunk("other", "page", "p2", SUBJECT, 0, "apple", vec((0, 1.0))),
        )
    )
    await backend.prune(IndexScope("page", "p1"), frozenset({"fresh"}))
    lexical = await backend.lexical("apple", frozenset({SUBJECT}), "page", 10)
    assert sorted(hit.chunk_digest for hit in lexical) == ["fresh", "other"]
    vector = await backend.vector(vec((0, 1.0)), frozenset({SUBJECT}), "page", 10)
    assert sorted(hit.chunk_digest for hit in vector) == ["fresh", "other"]


async def test_prune_with_an_empty_keep_set_drops_the_whole_scope(
    clean_chunk: None, database_url: str
) -> None:
    backend = DefaultIndex(transaction=workspace_tx)
    await backend.upsert(
        (
            Chunk("gone-a", "page", "p1", SUBJECT, 0, "apple", vec((0, 1.0))),
            Chunk("gone-b", "page", "p1", SUBJECT, 1, "apple", vec((0, 1.0))),
            Chunk("kept", "page", "p2", SUBJECT, 0, "apple", vec((0, 1.0))),
        )
    )
    await backend.prune(IndexScope("page", "p1"), frozenset())
    lexical = await backend.lexical("apple", frozenset({SUBJECT}), "page", 10)
    assert [hit.chunk_digest for hit in lexical] == ["kept"]


def test_pack_unpack_embedding_roundtrips() -> None:
    vector = vec((0, 1.0), (7, -0.5), (100, 0.25))
    assert unpack_embedding(pack_embedding(vector)) == pytest.approx(vector)
