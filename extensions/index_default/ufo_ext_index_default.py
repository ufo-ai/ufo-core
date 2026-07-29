"""The base-pinned default index backend: dialect-native lexical + vector retrieval over `chunk`.

Every deploy needs an index, so this extension is base-pinned and registers `IndexBackendSpec`
name `"default"` — the backend core resolves when `memory.index_backend` is unset. One
`DefaultIndex` selects its SQL by the connection dialect: Postgres tsvector/GIN + pgvector
halfvec/HNSW, SQLite FTS5 + brute-force cosine. Dialect-only types (halfvec, tsvector, FTS5) never
leave this module — `Chunk`/`Hit`/`IndexScope` stay dialect-neutral. It owns the `chunk`/`chunk_fts`
tables (its migration), reached through the workspace-scoped `transaction()` core hands the
factory, and prunes a scope's chunks outside a keep-set so a re-chunked owner leaves no orphan.
"""

import math
import struct
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncConnection

from ufo.sdk.index import Chunk, Hit, IndexScope
from ufo.sdk.manifest import IndexBackendSpec, Manifest

NAME = "index_default"
VERSION = "0.1.0"
INDEX_BACKEND = "default"

Transaction = Callable[[], AbstractAsyncContextManager[AsyncConnection]]


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


def pack_embedding(vector: tuple[float, ...]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_embedding(blob: bytes) -> tuple[float, ...]:
    return struct.unpack(f"<{len(blob) // 4}f", blob)


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
    insert into chunk
      (workspace_id, chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding)
    values (current_setting('app.workspace_id')::uuid, :chunk_digest, :owner_kind, :owner_id,
            :subject, :ordinal, :text, cast(:embedding as halfvec))
    on conflict (workspace_id, chunk_digest) do update set
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
    where subject = any(:subjects) and owner_kind = :owner_kind
      and tsv @@ plainto_tsquery('english', :query)
    order by score desc
    limit :limit
    """
)
VECTOR_PG = sa.text(
    """
    select chunk_digest, owner_kind, owner_id, subject, ordinal, text,
           1 - (embedding <=> cast(:query as halfvec)) as score
    from chunk
    where subject = any(:subjects) and owner_kind = :owner_kind and embedding is not null
    order by (embedding <=> cast(:query as halfvec)) + 0.0
    limit :limit
    """
)
DELETE_PG = sa.text("delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id")
HAS_CHUNKS = sa.text(
    "select 1 from chunk where owner_kind = :owner_kind and owner_id = :owner_id limit 1"
)
PRUNE_PG = sa.text(
    "delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id "
    "and chunk_digest <> all(:keep)"
)
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
    where chunk_fts match :query and c.subject in :subjects and c.owner_kind = :owner_kind
    order by bm25(chunk_fts)
    limit :limit
    """
).bindparams(sa.bindparam("subjects", expanding=True))
VECTOR_ROWS_SQLITE = sa.text(
    """
    select chunk_digest, owner_kind, owner_id, subject, ordinal, text, embedding
    from chunk
    where subject in :subjects and owner_kind = :owner_kind and embedding is not null
    """
).bindparams(sa.bindparam("subjects", expanding=True))
DELETE_SQLITE = sa.text("delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id")
DELETE_FTS_SCOPE = sa.text(
    """
    delete from chunk_fts where chunk_digest in (
      select chunk_digest from chunk where owner_kind = :owner_kind and owner_id = :owner_id
    )
    """
)
PRUNE_FTS_SQLITE = sa.text(
    """
    delete from chunk_fts where chunk_digest in (
      select chunk_digest from chunk
      where owner_kind = :owner_kind and owner_id = :owner_id and chunk_digest not in :keep
    )
    """
).bindparams(sa.bindparam("keep", expanding=True))
PRUNE_SQLITE = sa.text(
    "delete from chunk where owner_kind = :owner_kind and owner_id = :owner_id "
    "and chunk_digest not in :keep"
).bindparams(sa.bindparam("keep", expanding=True))


@dataclass(frozen=True)
class DefaultIndex:
    """The dialect-native `IndexBackend`. The Postgres vector leg orders by
    `(embedding <=> query) + 0.0` — an expression the HNSW index cannot serve, so the scan is
    structurally exact on every plan: an HNSW scan under a selective subject/owner filter starves
    before reaching a small subject's vectors (approximate search post-filters, so a two-row
    subject can return nothing at all), and this backend's corpus scale is sized for a full scan.
    Holds the workspace-scoped `transaction()` opener; each operation opens one transaction and
    selects Postgres or SQLite SQL by the connection dialect."""

    transaction: Transaction

    async def upsert(self, chunks: tuple[Chunk, ...]) -> None:
        if not chunks:
            return
        async with self.transaction() as connection:
            postgres = connection.dialect.name == "postgresql"
            for chunk in chunks:
                if postgres:
                    await connection.execute(
                        UPSERT_PG,
                        {
                            "chunk_digest": chunk.chunk_digest,
                            "owner_kind": chunk.owner_kind,
                            "owner_id": chunk.owner_id,
                            "subject": chunk.subject,
                            "ordinal": chunk.ordinal,
                            "text": chunk.text,
                            "embedding": (
                                pgvector_literal(chunk.embedding) if chunk.embedding else None
                            ),
                        },
                    )
                    continue
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
        params = {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
        async with self.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(DELETE_PG, params)
                return
            await connection.execute(DELETE_FTS_SCOPE, params)
            await connection.execute(DELETE_SQLITE, params)

    async def has_chunks(self, scope: IndexScope) -> bool:
        params = {"owner_kind": scope.owner_kind, "owner_id": scope.owner_id}
        async with self.transaction() as connection:
            return (await connection.execute(HAS_CHUNKS, params)).first() is not None

    async def prune(self, scope: IndexScope, keep: frozenset[str]) -> None:
        if not keep:
            await self.delete(scope)
            return
        params = {
            "owner_kind": scope.owner_kind,
            "owner_id": scope.owner_id,
            "keep": list(keep),
        }
        async with self.transaction() as connection:
            if connection.dialect.name == "postgresql":
                await connection.execute(PRUNE_PG, params)
                return
            await connection.execute(PRUNE_FTS_SQLITE, params)
            await connection.execute(PRUNE_SQLITE, params)

    async def lexical(
        self, query: str, subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        if not subjects:
            return ()
        async with self.transaction() as connection:
            if connection.dialect.name == "postgresql":
                if not query.strip():
                    return ()
                rows = (
                    await connection.execute(
                        LEXICAL_PG,
                        {
                            "query": query,
                            "subjects": list(subjects),
                            "owner_kind": owner_kind,
                            "limit": limit,
                        },
                    )
                ).mappings()
                return tuple(_hit(row, row["score"]) for row in rows)
            literal_terms = query.replace('"', " ").split()
            match = " ".join(f'"{term}"' for term in literal_terms)
            if not match:
                return ()
            rows = (
                await connection.execute(
                    LEXICAL_SQLITE,
                    {
                        "query": match,
                        "subjects": list(subjects),
                        "owner_kind": owner_kind,
                        "limit": limit,
                    },
                )
            ).mappings()
            return tuple(_hit(row, row["score"]) for row in rows)

    async def vector(
        self, embedding: tuple[float, ...], subjects: frozenset[str], owner_kind: str, limit: int
    ) -> tuple[Hit, ...]:
        if not embedding or not subjects:
            return ()
        async with self.transaction() as connection:
            if connection.dialect.name == "postgresql":
                rows = (
                    await connection.execute(
                        VECTOR_PG,
                        {
                            "query": pgvector_literal(embedding),
                            "subjects": list(subjects),
                            "owner_kind": owner_kind,
                            "limit": limit,
                        },
                    )
                ).mappings()
                return tuple(_hit(row, row["score"]) for row in rows)
            sqlite_rows = (
                (
                    await connection.execute(
                        VECTOR_ROWS_SQLITE, {"subjects": list(subjects), "owner_kind": owner_kind}
                    )
                )
                .mappings()
                .all()
            )
        scored = sorted(
            ((row, cosine(unpack_embedding(row["embedding"]), embedding)) for row in sqlite_rows),
            key=lambda item: item[1],
            reverse=True,
        )
        return tuple(_hit(row, score) for row, score in scored[:limit])


def manifest() -> Manifest:
    return Manifest(
        name=NAME,
        version=VERSION,
        indexes=(
            IndexBackendSpec(
                name=INDEX_BACKEND,
                factory=lambda ctx: DefaultIndex(transaction=ctx.transaction),
            ),
        ),
    )
