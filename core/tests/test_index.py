from collections.abc import AsyncIterator

import pytest
import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.memory.chunk import Chunk, IndexScope
from selfhost.memory.embed import EMBED_DIM
from selfhost.memory.index import index_backend_for, pack_embedding, unpack_embedding

SUBJECT = "member:me"
FOREIGN = "member:other"


def vec(*axes: tuple[int, float]) -> tuple[float, ...]:
    values = [0.0] * EMBED_DIM
    for index, value in axes:
        values[index] = value
    return tuple(values)


class StubEmbed:
    """Deterministic stand-in EmbedClient the backend re-embeds through in reindex. A dependency,
    never the asserted thing — tests assert the index's Hit ordering read back through vector()."""

    def __init__(self, vector: tuple[float, ...]) -> None:
        self._vector = vector

    async def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector for _ in texts)


@pytest.fixture
async def clean_chunk(db: None, database_url: str) -> AsyncIterator[None]:
    async with workspace_tx() as connection:
        await connection.execute(sa.text("delete from chunk"))
        if database_url.startswith("sqlite"):
            await connection.execute(sa.text("delete from chunk_fts"))
    yield


async def test_upsert_lexical_and_vector_return_ordered_hits(
    clean_chunk: None, database_url: str
) -> None:
    backend = index_backend_for(database_url, StubEmbed(vec((0, 1.0))))
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


async def test_foreign_subject_is_excluded(clean_chunk: None, database_url: str) -> None:
    backend = index_backend_for(database_url, StubEmbed(vec((0, 1.0))))
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


async def test_delete_removes_only_its_scope(clean_chunk: None, database_url: str) -> None:
    backend = index_backend_for(database_url, StubEmbed(vec((0, 1.0))))
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


async def test_reindex_reembeds_scope_through_client(clean_chunk: None, database_url: str) -> None:
    backend = index_backend_for(database_url, StubEmbed(vec((5, 1.0))))
    await backend.upsert((Chunk("c", "memory_item", "o", SUBJECT, 0, "apple", vec((0, 1.0))),))
    probe = vec((5, 1.0))
    before = await backend.vector(probe, frozenset({SUBJECT}), "memory_item", 10)
    assert before[0].score == pytest.approx(0.0, abs=1e-2)
    await backend.reindex(IndexScope("memory_item", "o"))
    after = await backend.vector(probe, frozenset({SUBJECT}), "memory_item", 10)
    assert after[0].score == pytest.approx(1.0, abs=1e-2)


def test_index_backend_for_rejects_unknown_dialect() -> None:
    with pytest.raises(RuntimeError):
        index_backend_for("mysql://host/db", StubEmbed(vec((0, 1.0))))


def test_pack_unpack_embedding_roundtrips() -> None:
    vector = vec((0, 1.0), (7, -0.5), (100, 0.25))
    assert unpack_embedding(pack_embedding(vector)) == pytest.approx(vector)
