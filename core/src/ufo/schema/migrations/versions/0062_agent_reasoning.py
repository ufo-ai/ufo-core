"""agent reasoning effort"""

import sqlalchemy as sa
from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("reasoning", sa.Text, nullable=False, server_default=sa.text("'auto'")),
    )
    with op.batch_alter_table("agent") as batch:
        batch.create_check_constraint(
            "agent_reasoning",
            "reasoning in ('auto', 'off', 'low', 'medium', 'high')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_constraint("agent_reasoning", type_="check")
    op.drop_column("agent", "reasoning")
