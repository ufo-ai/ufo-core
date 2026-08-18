"""keep the iMessage project binding only in surface_installation"""

import sqlalchemy as sa
from alembic import op

revision: str = "0109"
down_revision: str | None = "0108"
branch_labels: str | None = None
depends_on: str | None = None

IMESSAGE_EXTENSION = "imessage"
PROJECT_KEY = "project"


def upgrade() -> None:
    ext_store = sa.table(
        "ext_store",
        sa.column("extension", sa.Text()),
        sa.column("key", sa.Text()),
    )
    op.get_bind().execute(
        sa.delete(ext_store).where(
            ext_store.c.extension == IMESSAGE_EXTENSION,
            ext_store.c.key == PROJECT_KEY,
        )
    )


def downgrade() -> None:
    pass
