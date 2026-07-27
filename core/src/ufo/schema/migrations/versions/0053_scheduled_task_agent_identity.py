"""scheduled task names are per-agent"""

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_constraint("scheduled_task_name", type_="unique")
        batch.create_unique_constraint("scheduled_task_name", ("workspace_id", "agent_id", "name"))


def downgrade() -> None:
    with op.batch_alter_table("scheduled_task") as batch:
        batch.drop_constraint("scheduled_task_name", type_="unique")
        batch.create_unique_constraint("scheduled_task_name", ("workspace_id", "name"))
