"""chunk index"""

import sqlalchemy as sa
from alembic import op

revision: str = "index_default_0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = ("index_default",)
depends_on: str | None = "0001"

CREATE_EXTENSION_VECTOR = "create extension if not exists vector"
CREATE_CHUNK_PG = """
create table chunk (
    chunk_digest text not null primary key,
    owner_kind text not null,
    owner_id text not null,
    subject text not null,
    ordinal integer not null,
    text text not null,
    embedding halfvec(3072),
    tsv tsvector generated always as (to_tsvector('english', text)) stored
)
"""
CREATE_CHUNK_TSV_GIN = "create index chunk_tsv on chunk using gin (tsv)"
CREATE_CHUNK_EMBEDDING_HNSW = (
    "create index chunk_embedding on chunk using hnsw (embedding halfvec_cosine_ops)"
)
CREATE_CHUNK_FTS_SQLITE = (
    "create virtual table chunk_fts using fts5 (chunk_digest unindexed, text)"
)


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute(CREATE_EXTENSION_VECTOR)
        op.execute(CREATE_CHUNK_PG)
        op.execute(CREATE_CHUNK_TSV_GIN)
        op.execute(CREATE_CHUNK_EMBEDDING_HNSW)
        op.create_index("chunk_subject", "chunk", ["subject"])
        return
    op.create_table(
        "chunk",
        sa.Column("chunk_digest", sa.Text(), nullable=False),
        sa.Column("owner_kind", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Text(), nullable=False),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", sa.LargeBinary(), nullable=True),
        sa.PrimaryKeyConstraint("chunk_digest"),
    )
    op.create_index("chunk_subject", "chunk", ["subject"])
    op.execute(CREATE_CHUNK_FTS_SQLITE)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("drop table chunk")
        return
    op.execute("drop table chunk_fts")
    op.drop_index("chunk_subject", "chunk")
    op.drop_table("chunk")
