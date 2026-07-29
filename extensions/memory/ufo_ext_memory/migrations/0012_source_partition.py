"""partition source-derived memory by an additive source link, not by identity.

`memory_0010` binds every page derivation to a page revision and asks the page-change consumers to
replay. This adds `memory_item.source_id` (the source the row currently binds to) and a
`memory_source` link table keyed one link per `(memory_item, page)` it was derived from — with an
`on delete cascade` back to the row — so the same fact learned from two feeds is one row a reader
may reach through either, and dropping the row drops its links. The id stays content-addressed over
`(workspace, subject, item_class, body)` exactly as `commit` writes it, so no existing row is
re-keyed and nothing in the index is orphaned. Each row that resolves a complete origin is given one
link from it; a page-derived row that cannot — its page gone, or its revision never restated by the
replay `memory_0010` asked for — has its origin cleared and takes no link, so the check below never
sees a partial origin.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0012"
down_revision: str | None = "memory_0011"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

PAGE_SOURCE_CHECK = (
    "(created_from_page_id is null and created_from_page_revision is null "
    "and source_id is null) or (created_from_page_id is not null "
    "and created_from_page_revision is not null and source_id is not null)"
)

memory_item = sa.table(
    "memory_item",
    sa.column("id", sa.Uuid()),
    sa.column("workspace_id", sa.Uuid()),
    sa.column("created_from_page_id", sa.Uuid()),
    sa.column("created_from_page_revision", sa.BigInteger()),
    sa.column("source_id", sa.Uuid()),
)
page = sa.table("page", sa.column("id", sa.Uuid()), sa.column("source_id", sa.Uuid()))
memory_source = sa.table(
    "memory_source",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("memory_item_id", sa.Uuid()),
    sa.column("source_id", sa.Uuid()),
    sa.column("page_id", sa.Uuid()),
    sa.column("revision", sa.BigInteger()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.add_column(sa.Column("source_id", sa.Uuid(), nullable=True))
    connection = op.get_bind()
    page_source = (
        sa.select(page.c.source_id)
        .where(page.c.id == memory_item.c.created_from_page_id, page.c.source_id.is_not(None))
        .scalar_subquery()
    )
    connection.execute(
        sa.update(memory_item)
        .where(
            memory_item.c.created_from_page_id.is_not(None),
            sa.or_(memory_item.c.created_from_page_revision.is_(None), page_source.is_(None)),
        )
        .values(created_from_page_id=None, created_from_page_revision=None)
    )
    connection.execute(
        sa.update(memory_item)
        .where(memory_item.c.created_from_page_id.is_not(None))
        .values(source_id=page_source)
    )
    with op.batch_alter_table("memory_item") as batch:
        batch.create_check_constraint("memory_item_page_source", PAGE_SOURCE_CHECK)
    op.create_table(
        "memory_source",
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("memory_item_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("page_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["memory_item_id"],
            ["memory_item.id"],
            ondelete="CASCADE",
            name="memory_source_memory_item_id_fkey",
        ),
        sa.PrimaryKeyConstraint("memory_item_id", "page_id", name="memory_source_pkey"),
    )
    connection.execute(
        sa.insert(memory_source).from_select(
            [
                memory_source.c.workspace_id,
                memory_source.c.memory_item_id,
                memory_source.c.source_id,
                memory_source.c.page_id,
                memory_source.c.revision,
                memory_source.c.created_at,
                memory_source.c.updated_at,
            ],
            sa.select(
                memory_item.c.workspace_id,
                memory_item.c.id,
                memory_item.c.source_id,
                memory_item.c.created_from_page_id,
                memory_item.c.created_from_page_revision,
                sa.func.now(),
                sa.func.now(),
            ).where(memory_item.c.source_id.is_not(None)),
        )
    )


def downgrade() -> None:
    op.drop_table("memory_source")
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_constraint("memory_item_page_source", type_="check")
        batch.drop_column("source_id")
