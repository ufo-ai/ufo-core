"""loop depth"""

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("turn", sa.Column("parent_turn_id", sa.Uuid(), nullable=True))
    op.add_column("turn", sa.Column("subagent_profile", sa.Text(), nullable=True))
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint("conversation_surface", "surface in ('cli', 'subagent')")


def downgrade() -> None:
    with op.batch_alter_table("conversation") as batch:
        batch.drop_constraint("conversation_surface", type_="check")
        batch.create_check_constraint("conversation_surface", "surface in ('cli')")
    op.drop_column("turn", "subagent_profile")
    op.drop_column("turn", "parent_turn_id")
