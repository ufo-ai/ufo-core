"""Every page is sent back through the index job, so its chunks carry the digest of the body they
were derived from.

A chunk's identity now holds the owner's content digest (`chunk_digest` in `ufo.runtime.indexing`),
which is the claim a reader of page chunks fences on: the digest recomputed over a hit under the
page's live digest is the hit's own digest only where the chunks belong to the body the page holds
now. Chunks written by the image this replaces carry the identity without that digest and can make
no claim, and only a page change would replace them. Deleting the index job's cursor replays every
page through the job that owns those rows: it rewrites each page's chunks under the new identity
and prunes the ones it replaced. The outgoing image reads chunk identity as opaque, so it serves
the same rows either way while the replay runs.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0026"
down_revision: str | None = "memory_0025"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None

EXTENSION = "memory"
INDEX_CURSOR_KEY = "page_change_cursor:index_pages"

ext_store = sa.table(
    "ext_store",
    sa.column("workspace_id", sa.Uuid()),
    sa.column("extension", sa.Text()),
    sa.column("key", sa.Text()),
)


def upgrade() -> None:
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == EXTENSION, ext_store.c.key == INDEX_CURSOR_KEY
        )
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == EXTENSION, ext_store.c.key == INDEX_CURSOR_KEY
        )
    )
