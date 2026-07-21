"""turn on_behalf_of and scheduled_task creator

The initiator a non-member turn acts as: a scheduled fire runs as the member who
created the schedule, a subagent as the member who spawned its chain. Distinct from
speaker_member_id (a live message, gates granting) and the conversation's disclosure member.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.add_column(sa.Column("on_behalf_of_member_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "turn_on_behalf_of_member_id_fkey", "member", ["on_behalf_of_member_id"], ["id"]
        )
    with op.batch_alter_table("scheduled_task") as batch:
        batch.add_column(sa.Column("created_by_member_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            "scheduled_task_created_by_member_id_fkey", "member", ["created_by_member_id"], ["id"]
        )


def downgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_constraint("scheduled_task_created_by_member_id_fkey", type_="foreignkey")
        batch.drop_column("created_by_member_id")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_on_behalf_of_member_id_fkey", type_="foreignkey")
        batch.drop_column("on_behalf_of_member_id")
