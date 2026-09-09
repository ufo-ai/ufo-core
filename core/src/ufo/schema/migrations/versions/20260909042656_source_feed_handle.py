"""A source row carries the identity its id was hashed from.

`source.id` is `uuid5(workspace/source/backend/<feed handle>/connection/<connection_id>)`, and until
now the feed handle — the row's config minus the fields its model declares non-identity, dumped with
sorted keys — lived nowhere but inside that hash. Nothing but `id` was unique, and sync found an
existing row only by recomputing the hash. Storing the handle gives the row a natural key a later
revision can hold unique without hashing anything into the primary key.

The backfill reproduces each row's own id from its config: the two writers disagree on the
exclusion set (`register_source` passes the model's `non_identity_fields`; the `[[sources]]` boot
path passes none), so a row's handle is whichever candidate hashes back to its `id`. A row neither
reproduces is a row this revision does not understand, and it stops rather than guess.
"""

import json
from collections.abc import Mapping
from uuid import NAMESPACE_URL, UUID, uuid5

import sqlalchemy as sa
from alembic import op

revision: str = "20260909042656"
down_revision: str | None = "20260907150257"
branch_labels: str | None = None
depends_on: str | None = None

FEED_HANDLE_UNIQUE = "source_feed_handle"
MODEL_NON_IDENTITY = frozenset({"backfill_days", "backfill_after"})
BOOT_NON_IDENTITY: frozenset[str] = frozenset()

source = sa.table(
    "source",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("backend", sa.Text()),
    sa.column("config", sa.JSON()),
    sa.column("connection_id", sa.Uuid()),
    sa.column("feed_handle", sa.Text()),
)


def _handle(config: Mapping[str, object], non_identity: frozenset[str]) -> str:
    return json.dumps({k: v for k, v in config.items() if k not in non_identity}, sort_keys=True)


def _row_id(workspace_id: UUID, backend: str, handle: str, connection_id: UUID) -> UUID:
    return uuid5(
        NAMESPACE_URL, f"{workspace_id}/source/{backend}/{handle}/connection/{connection_id}"
    )


def upgrade() -> None:
    bind = op.get_bind()
    op.add_column("source", sa.Column("feed_handle", sa.Text(), nullable=True))
    for row in bind.execute(sa.select(source)).mappings():
        config = row["config"] if isinstance(row["config"], Mapping) else json.loads(row["config"])
        matched = [
            handle
            for handle in {_handle(config, MODEL_NON_IDENTITY), _handle(config, BOOT_NON_IDENTITY)}
            if _row_id(row["workspace_id"], row["backend"], handle, row["connection_id"])
            == row["id"]
        ]
        if len(matched) != 1:
            raise RuntimeError(
                f"source {row['id']} does not hash back from its config under either exclusion set"
            )
        bind.execute(
            sa.update(source).where(source.c.id == row["id"]).values(feed_handle=matched[0])
        )
    with op.batch_alter_table("source") as batch:
        batch.alter_column("feed_handle", existing_type=sa.Text(), nullable=False)
    columns = ["workspace_id", "connection_id", "backend", "feed_handle"]
    if bind.dialect.name == "postgresql":
        # Built outside the migration's transaction so the index takes no exclusive lock on a live
        # table; the constraint then adopts the finished index as a metadata-only step.
        with op.get_context().autocommit_block():
            op.create_index(
                FEED_HANDLE_UNIQUE, "source", columns, unique=True, postgresql_concurrently=True
            )
        op.execute(
            f"alter table source add constraint {FEED_HANDLE_UNIQUE} "
            f"unique using index {FEED_HANDLE_UNIQUE}"
        )
    else:
        # SQLite cannot add a table constraint after the fact and alembic's batch mode quietly
        # skips one; a unique index is the same rule and SQLite builds it natively.
        op.create_index(FEED_HANDLE_UNIQUE, "source", columns, unique=True)


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(FEED_HANDLE_UNIQUE, "source", type_="unique")
    else:
        op.drop_index(FEED_HANDLE_UNIQUE, table_name="source")
    with op.batch_alter_table("source") as batch:
        batch.drop_column("feed_handle")
