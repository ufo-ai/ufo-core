"""agent visibility"""

import sqlalchemy as sa
from alembic import op

revision: str = "0105"
down_revision: str | None = "0104"
branch_labels: str | None = None
depends_on: str | None = None

MAIN_VISIBILITY_UPDATE = sa.text("update agent set visibility = 'workspace' where is_main")


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("visibility", sa.Text, nullable=False, server_default=sa.text("'private'")),
    )
    with op.batch_alter_table("agent") as batch:
        batch.create_check_constraint(
            "agent_visibility",
            "visibility in ('private', 'workspace')",
        )
    op.execute(MAIN_VISIBILITY_UPDATE)


def downgrade() -> None:
    with op.batch_alter_table("agent") as batch:
        batch.drop_constraint("agent_visibility", type_="check")
    op.drop_column("agent", "visibility")
