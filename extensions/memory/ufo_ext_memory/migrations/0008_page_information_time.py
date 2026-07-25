"""page memory information time"""

from datetime import datetime
from uuid import UUID

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0008"
down_revision: str | None = "memory_0007"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "0049"

BATCH_SIZE = 500


def upgrade() -> None:
    memory_item = sa.table(
        "memory_item",
        sa.column("id", sa.Uuid()),
        sa.column("source_ref", sa.Text()),
        sa.column("as_of", sa.DateTime(timezone=True)),
    )
    page = sa.table(
        "page",
        sa.column("id", sa.Uuid()),
        sa.column("record_created_at", sa.Text()),
        sa.column("record_updated_at", sa.Text()),
    )
    connection = op.get_bind()
    source_rows = connection.execute(
        sa.select(memory_item.c.id, memory_item.c.source_ref).where(
            memory_item.c.source_ref.is_not(None),
            memory_item.c.as_of.is_(None),
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
        pages = connection.execute(
            sa.select(
                page.c.id,
                page.c.record_created_at,
                page.c.record_updated_at,
            ).where(page.c.id.in_(tuple(memory_ids)))
        )
        updates = [
            {
                "memory_id": memory_id,
                "information_time": datetime.fromisoformat(record_as_of),
            }
            for row in pages
            if (record_as_of := row.record_updated_at or row.record_created_at) is not None
            for memory_id in memory_ids[row.id]
        ]
        if updates:
            connection.execute(
                sa.update(memory_item)
                .where(memory_item.c.id == sa.bindparam("memory_id"))
                .values(as_of=sa.bindparam("information_time")),
                updates,
            )


def downgrade() -> None:
    pass
