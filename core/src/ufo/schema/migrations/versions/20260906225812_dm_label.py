"""a direct conversation's origin reads DM"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260906225812"
down_revision: str | None = "20260906070956"
branch_labels: str | None = None
depends_on: str | None = None
DM_LABEL_UPDATE = sa.text(
    "update conversation set surface_label = 'DM' where surface_label = 'Direct message'"
)
"""Slack and iMessage wrote `Direct message` as a direct conversation's origin and write `DM` now.
A surface corrects a label only on the next message that carries it, so every direct conversation
already listed would otherwise keep the old words until it next spoke. `updated_at` is left alone:
the list orders on it and draws a moved row bold, and nothing moved."""


def upgrade() -> None:
    op.execute(DM_LABEL_UPDATE)


def downgrade() -> None:
    pass
