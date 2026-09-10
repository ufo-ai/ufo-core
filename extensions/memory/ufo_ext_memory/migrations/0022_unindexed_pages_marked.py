"""Every workspace still holding chunks of pages that do not reach memory is marked for the drain —
RFC 0047 change 1.

Core's `20260910114436` marked the pages of GitHub's unindexed streams before `indexed` joined the
revision trigger, so the page indexer never replays them and their chunks and mirror rows stand.
This writes the drain's marker into the memory store of every workspace whose
`mem_page` mirrors such a page; the per-minute drain job walks each marked workspace's mirrors from
the cursor the marker carries, deletes the chunks from the index and then the mirror rows, and
deletes the marker when the walk ends. The outgoing image never writes `indexed` and reads its
default `true`; the column, this marker and the job that consumes it land together.
"""

from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

revision: str = "memory_0022"
down_revision: str | None = "memory_0021"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260910114436"

EXTENSION = "memory"
MARKER_KEY = "unindexed_pages_drain"

ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
    sa.column("value", sa.JSON()),
    sa.column("created_at", sa.DateTime(timezone=True)),
    sa.column("updated_at", sa.DateTime(timezone=True)),
)
mem_page = sa.table(
    "mem_page", sa.column("workspace_id", sa.Uuid()), sa.column("page_uid", sa.Uuid())
)
page = sa.table(
    "page",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("uid", sa.Uuid()),
    sa.column("indexed", sa.Boolean()),
)


def upgrade() -> None:
    marked = (
        sa.select(mem_page.c.workspace_id)
        .join(
            page,
            sa.and_(
                page.c.workspace_id == mem_page.c.workspace_id,
                page.c.uid == mem_page.c.page_uid,
            ),
        )
        .where(page.c.indexed.is_(False))
        .distinct()
    )
    connection = op.get_bind()
    workspace_ids = connection.execute(marked).scalars().all()
    if not workspace_ids:
        return
    now = datetime.now(UTC)
    insert = pg_insert if connection.dialect.name == "postgresql" else sqlite_insert
    connection.execute(
        insert(ext_store).on_conflict_do_nothing(
            index_elements=["workspace_id", "extension", "key"]
        ),
        [
            {
                "workspace_id": workspace_id,
                "extension": EXTENSION,
                "key": MARKER_KEY,
                "value": {"after": None},
                "created_at": now,
                "updated_at": now,
            }
            for workspace_id in workspace_ids
        ],
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == EXTENSION, ext_store.c.key == MARKER_KEY
        )
    )
