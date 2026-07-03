"""turn single-owner claim and resume-enqueue dedup"""

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("running_attempt", sa.Text(), nullable=True))
    op.add_column(
        "turn", sa.Column("resume_enqueued_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("turn", "resume_enqueued_at")
    op.drop_column("turn", "running_attempt")
