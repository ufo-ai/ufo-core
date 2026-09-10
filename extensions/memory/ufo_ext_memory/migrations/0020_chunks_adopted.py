"""Every page's chunks answer to its uid before the content-id columns go — RFC 0046 unit D.

`memory_0019` left `mem_page.page_id` as the marker for chunks the index still filed under a
content id, drained page by page by the adopt job and by the page indexer as pages changed. This
release removes both, so it may only land once nothing is left to adopt: a marked row here means
the drain is still running, and the deploy waits for it rather than orphaning a page's chunks.
The `_id` columns themselves fall with the contract half.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0020"
down_revision: str | None = "memory_0019"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = "20260909204543"


def upgrade() -> None:
    pending = op.get_bind().scalar(
        sa.text("select count(*) from mem_page where page_id is not null")
    )
    if pending:
        raise RuntimeError(f"{pending} pages' chunks are still filed under a content id")


def downgrade() -> None:
    pass
