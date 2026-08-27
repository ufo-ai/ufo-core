"""mark a row retired by curation, separately from being superseded.

`superseded_by` says a newer statement stands in a row's place, and `commit` clears it on the
content address it re-derives — so a page still synced re-commits the identical body every pass and
the row returns. That is right for a restatement and wrong for a judgement: the page pass reads the
whole wiki at once and retires the rows that repeat one another or carry only the motion of a tool,
and nothing re-derived should undo it. `retired_at` is that judgement, and it is the one column the
upsert leaves alone.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "memory_0013"
down_revision: str | None = "memory_0012"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.add_column(sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("memory_item") as batch:
        batch.drop_column("retired_at")
