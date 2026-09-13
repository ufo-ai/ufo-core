"""`source.synced_at`: the latest complete run this row committed.

A child may treat a parent stream with no live pages as an empty catalog only after that parent has
completed. Null means no complete run is known. Existing live pages prove the same fact at read
time, so the nullable addition needs no backfill.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913055555"
down_revision: str | None = "20260913044546"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("source", "synced_at")
