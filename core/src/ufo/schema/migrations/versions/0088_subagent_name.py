"""a subagent turn carries the display name its spawn gave it

A run's row in a conversation's activity feed reads as the work it was handed — 'UK sports news' —
not the profile that ran it. The name is the spawn's to give, so it lands on the child turn at
admission; the live SubagentActivity frames and the durable transcript projection both read it from
here, which is what keeps the row a member watched and the row a reload draws the same. Existing
child turns have no name and their rows state the profile.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0088"
down_revision: str | None = "0087"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("subagent_name", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_column("subagent_name")
