"""page alert data absence"""

import sqlalchemy as sa
from alembic import op

revision: str = "0058"
down_revision: str | None = "0057"
branch_labels: str | None = None
depends_on: str | None = None

PAGE_ALERTS = "page_alerts"


def upgrade() -> None:
    ext_store = sa.table("ext_store", sa.column("extension", sa.Text()))
    op.get_bind().execute(sa.delete(ext_store).where(ext_store.c.extension == PAGE_ALERTS))


def downgrade() -> None:
    pass
