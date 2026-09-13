"""`source.partition_cursor`: the cursor map of a stream that fans out over parents.

A tree stream's cursor is a JSON map of partition entries and the walk's own marks, while the image
being replaced reads `source.cursor` as a plain provider watermark and writes one back. Both images
run against one row while the fleet rolls, so a tree row keeps its map in a column the outgoing
image never reads and leaves `cursor` exactly as that image left it. Nothing is backfilled: a tree
row starts from an empty map and re-walks from its floor once.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913044546"
down_revision: str | None = "20260912192000"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("source", sa.Column("partition_cursor", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("source", "partition_cursor")
