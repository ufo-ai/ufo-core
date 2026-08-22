"""carry each agent's use of the workspace skill set

The workspace's saved skills are one set, and the portal's workspace page manages it. Every agent
states in this column whether its turns load that set, so the column answers true for every row the
workspace already holds — the reach each agent had before the move.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260822090000"
down_revision: str | None = "20260821155315"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("use_workspace_skills", sa.Boolean(), nullable=False, server_default=sa.true()),
    )


def downgrade() -> None:
    op.drop_column("agent", "use_workspace_skills")
