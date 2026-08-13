"""agent sandbox size"""

import sqlalchemy as sa
from alembic import op

revision: str = "0081"
down_revision: str | None = "0080"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("sandbox_size", sa.Text, nullable=False, server_default=sa.text("'small'")),
    )
    with op.batch_alter_table("agent") as batch:
        batch.create_check_constraint(
            "agent_sandbox_size",
            "sandbox_size in ('small', 'medium', 'large')",
        )


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_constraint("agent_sandbox_size", type_="check")
    op.drop_column("agent", "sandbox_size")
