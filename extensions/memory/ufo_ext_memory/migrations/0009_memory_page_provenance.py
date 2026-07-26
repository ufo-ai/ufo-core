"""memory page provenance"""

from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0009"
down_revision: str | None = "memory_0008"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

BATCH_SIZE = 500


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.add_column(sa.Column("created_from_page_id", sa.Uuid, nullable=True))
    memory_item = sa.table(
        "memory_item",
        sa.column("id", sa.Uuid()),
        sa.column("source_ref", sa.Text()),
        sa.column("created_from_page_id", sa.Uuid()),
    )
    page = sa.table("page", sa.column("id", sa.Uuid()))
    connection = op.get_bind()
    source_rows = connection.execute(
        sa.select(memory_item.c.id, memory_item.c.source_ref).where(
            memory_item.c.source_ref.is_not(None)
        )
    )
    while rows := source_rows.fetchmany(BATCH_SIZE):
        memory_ids: dict[UUID, list[UUID]] = {}
        for row in rows:
            try:
                page_id = UUID(row.source_ref)
            except ValueError:
                continue
            memory_ids.setdefault(page_id, []).append(row.id)
        if not memory_ids:
            continue
        page_ids = {
            row.id
            for row in connection.execute(
                sa.select(page.c.id).where(page.c.id.in_(tuple(memory_ids)))
            )
        }
        updates = [
            {"memory_id": memory_id, "page_id": page_id}
            for page_id in page_ids
            for memory_id in memory_ids[page_id]
        ]
        if updates:
            connection.execute(
                sa.update(memory_item)
                .where(memory_item.c.id == sa.bindparam("memory_id"))
                .values(created_from_page_id=sa.bindparam("page_id"), source_ref=None),
                updates,
            )


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_column("created_from_page_id")
