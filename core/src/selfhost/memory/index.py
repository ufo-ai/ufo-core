"""The index backend seam: dialect-native lexical + vector retrieval over the `chunk` table.

Two impls, selected by database dialect: Postgres tsvector/GIN + pgvector halfvec/HNSW, SQLite
FTS5 + brute-force cosine. The service (later unit) owns query embedding and lexical/vector
fusion; a backend does storage, ANN/FTS, and the subject filter in the query. Dialect-only types
(halfvec, tsvector, FTS5) never leave these classes — `Chunk`/`Hit` are dialect-neutral.
"""

import math
import struct
from dataclasses import dataclass
from typing import Protocol

import sqlalchemy as sa

from selfhost.db import workspace_tx
from selfhost.memory.chunk import Chunk, Hit, IndexScope
from selfhost.memory.embed import EmbedClient


class IndexBackend(Protocol):
    async def upsert(self, chunks: tuple[Chunk, ...]) -> None: ...

    async def delete(self, scope: IndexScope) -> None: ...

    async def lexical(
        self, query: str, subjects: frozenset[str], limit: int
    ) -> tuple[Hit, ...]: ...

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], limit: int
    ) -> tuple[Hit, ...]: ...

    async def reindex(self, scope: IndexScope) -> None: ...


def pgvector_literal(vector: tuple[float, ...]) -> str:
    return "[" + ",".join(repr(float(value)) for value in vector) + "]"


def cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    """Brute-force cosine, O(n·dim) in-process — sized for dev scale; the pgvector HNSW path is
    the deploy default when the corpus outgrows a single-process scan."""
    denom = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denom == 0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denom


def _hit(row: sa.RowMapping, score: float) -> Hit:
    return Hit(
        chunk_digest=row["chunk_digest"],
        owner_kind=row["owner_kind"],
        owner_id=row["owner_id"],
        subject=row["subject"],
        ordinal=row["ordinal"],
        text=row["text"],
        score=float(score),
    )


UPSERT_PG = sa.text(
    """
    insert into chunk (chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding)
    values (:chunk_digest, :owner_kind, :owner_id, :subject, :ordinal, :text,
            cast(:embedding as halfvec))
    on conflict (chunk_digest) do update set
      owner_kind = excluded.owner_kind, owner_id = excluded.owner_id,
      subject = excluded.subject, ordinal = excluded.ordinal,
      text = excluded.text, embedding = excluded.embedding
    """
)
LEXICAL_PG = sa.text(
    """
    select chunk_digest, owner_kind, owner_id, subject, ordinal, text,
           ts_rank(tsv, plainto_tsquery('english', :query)) as score
    from chunk
    where subject = any(:subjects) and tsv @@ plainto_tsquery('english', :query)
    order by score desc
    limit :limit
    """
)
VECTOR_PG = sa.text(
    """
    select chunk_digest, owner_kind, owner_id, subject, ordinal, text,
           1 - (embedding <=> cast(:query as halfvec)) as score
    from chunk
    where subject = any(:subjects) and embedding is not null
    order by embedding <=> cast(:query as halfvec)
    limit :limit
    """
)
DELETE_PG = sa.text("delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id")
SCOPE_TEXT_PG = sa.text(
    "select chunk_digest, text from chunk where owner_kind = :owner_kind and owner_id = :owner_id"
)
REEMBED_PG = sa.text(
    "update chunk set embedding = cast(:embedding as halfvec) where chunk_digest = :chunk_digest"
)


@dataclass(frozen=True)
class PgvectorIndex:
    embed: EmbedClient

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        if not chunks:
            return
        async with workspace_tx() as connection:
            for chunk in chunks:
                await connection.execute(
                    UPSERT_PG,
                    {
                        "chunk_digest": chunk.chunk_digest,
                        "owner_kind": chunk.owner_kind,
                        "owner_id": chunk.owner_id,
                        "subject": chunk.subject,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "embedding": pgvector_literal(chunk.embedding) if chunk.embedding else None,
                    },
                )

    async def delete(self, scope: IndexScope) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                DELETE_PG, {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
            )

    async def lexical(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]:
        if not query.strip() or not subjects:
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    LEXICAL_PG, {"query": query, "subjects": list(subjects), "limit": limit}
                )
            ).mappings()
            return tuple(_hit(row, row["score"]) for row in rows)

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], limit: int
    ) -> tuple[Hit, ...]:
        if not embedding or not subjects:
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    VECTOR_PG,
                    {
                        "query": pgvector_literal(embedding),
                        "subjects": list(subjects),
                        "limit": limit,
                    },
                )
            ).mappings()
            return tuple(_hit(row, row["score"]) for row in rows)

    async def reindex(self, scope: IndexScope) -> None:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    SCOPE_TEXT_PG, {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
                )
            ).mappings().all()
        if not rows:
            return
        vectors = await self.embed.embed(tuple(row["text"] for row in rows))
        async with workspace_tx() as connection:
            for row, vector in zip(rows, vectors, strict=True):
                await connection.execute(
                    REEMBED_PG,
                    {"embedding": pgvector_literal(vector), "chunk_digest": row["chunk_digest"]},
                )


def pack_embedding(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


UPSERT_SQLITE = sa.text(
    """
    insert into chunk (chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding)
    values (:chunk_digest, :owner_kind, :owner_id, :subject, :ordinal, :text, :embedding)
    on conflict (chunk_digest) do update set
      owner_kind = excluded.owner_kind, owner_id = excluded.owner_id,
      subject = excluded.subject, ordinal = excluded.ordinal,
      text = excluded.text, embedding = excluded.embedding
    """
)
DELETE_FTS_ONE = sa.text("delete from chunk_fts where chunk_digest = :chunk_digest")
INSERT_FTS = sa.text("insert into chunk_fts (chunk_digest, text) values (:chunk_digest, :text)")
LEXICAL_SQLITE = sa.text(
    """
    select c.chunk_digest, c.owner_kind, c.owner_id, c.subject, c.ordinal, c.text,
           -bm25(chunk_fts) as score
    from chunk_fts
    join chunk c on c.chunk_digest = chunk_fts.chunk_digest
    where chunk_fts match :query and c.subject in :subjects
    order by bm25(chunk_fts)
    limit :limit
    """
).bindparams(sa.bindparam("subjects", expanding=True))
VECTOR_ROWS_SQLITE = sa.text(
    """
    select chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding
    from chunk
    where subject in :subjects and embedding is not null
    """
).bindparams(sa.bindparam("subjects", expanding=True))
DELETE_SQLITE = sa.text(
    "delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id"
)
DELETE_FTS_SCOPE = sa.text(
    """
    delete from chunk_fts where chunk_digest in (
      select chunk_digest from chunk where owner_kind = :owner_kind and owner_id = :owner_id
    )
    """
)
SCOPE_TEXT_SQLITE = sa.text(
    "select chunk_digest, text from chunk where owner_kind = :owner_kind and owner_id = :owner_id"
)
REEMBED_SQLITE = sa.text(
    "update chunk set embedding = :embedding where chunk_digest = :chunk_digest"
)


@dataclass(frozen=True)
class SqliteFtsIndex:
    embed: EmbedClient

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        if not chunks:
            return
        async with workspace_tx() as connection:
            for chunk in chunks:
                await connection.execute(
                    UPSERT_SQLITE,
                    {
                        "chunk_digest": chunk.chunk_digest,
                        "owner_kind": chunk.owner_kind,
                        "owner_id": chunk.owner_id,
                        "subject": chunk.subject,
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "embedding": pack_embedding(chunk.embedding) if chunk.embedding else None,
                    },
                )
                await connection.execute(DELETE_FTS_ONE, {"chunk_digest": chunk.chunk_digest})
                await connection.execute(
                    INSERT_FTS, {"chunk_digest": chunk.chunk_digest, "text": chunk.text}
                )

    async def delete(self, scope: IndexScope) -> None:
        async with workspace_tx() as connection:
            await connection.execute(
                DELETE_FTS_SCOPE, {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
            )
            await connection.execute(
                DELETE_SQLITE, {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
            )

    async def lexical(self, query: str, subjects: frozenset[str], limit: int) -> tuple[Hit, ...]:
        match = " ".join(f'"{term}"' for term in query.split() if term)
        if not match or not subjects:
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    LEXICAL_SQLITE, {"query": match, "subjects": list(subjects), "limit": limit}
                )
            ).mappings()
            return tuple(_hit(row, row["score"]) for row in rows)

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], limit: int
    ) -> tuple[Hit, ...]:
        if not embedding or not subjects:
            return ()
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(VECTOR_ROWS_SQLITE, {"subjects": list(subjects)})
            ).mappings().all()
        scored = sorted(
            ((row, cosine(unpack_embedding(row["embedding"]), embedding)) for row in rows),
            key=lambda item: item[1],
            reverse=True,
        )
        return tuple(_hit(row, score) for row, score in scored[:limit])

    async def reindex(self, scope: IndexScope) -> None:
        async with workspace_tx() as connection:
            rows = (
                await connection.execute(
                    SCOPE_TEXT_SQLITE, {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
                )
            ).mappings().all()
        if not rows:
            return
        vectors = await self.embed.embed(tuple(row["text"] for row in rows))
        async with workspace_tx() as connection:
            for row, vector in zip(rows, vectors, strict=True):
                await connection.execute(
                    REEMBED_SQLITE,
                    {"embedding": pack_embedding(vector), "chunk_digest": row["chunk_digest"]},
                )


def index_backend_for(database_url: str, embed: EmbedClient) -> IndexBackend:
    """Dialect selection by url scheme; fail loud on anything but sqlite/postgresql."""
    if database_url.startswith("sqlite"):
        return SqliteFtsIndex(embed=embed)
    if database_url.startswith("postgresql"):
        return PgvectorIndex(embed=embed)
    raise RuntimeError(f"no index backend for database url {database_url!r}")
