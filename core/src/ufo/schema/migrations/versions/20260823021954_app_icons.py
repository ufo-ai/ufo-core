"""app agents take their declared icons"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260823021954"
down_revision: str | None = "20260822054846"
branch_labels: str | None = None
depends_on: str | None = None

APP_ICONS = {
    ("app_artifacts", "artifacts"): "books",
    ("app_chat", "chat"): "message-circle",
    ("app_radar", "radar"): "radar",
    ("app_tasks", "tasks"): "clock-play",
    ("app_wiki", "wiki"): "book",
}


def upgrade() -> None:
    agent = sa.table(
        "agent",
        sa.column("provisioned_by", sa.Text()),
        sa.column("provisioned_name", sa.Text()),
        sa.column("icon", sa.Text()),
    )
    for (provisioned_by, provisioned_name), icon in APP_ICONS.items():
        op.execute(
            agent.update()
            .where(
                agent.c.provisioned_by == provisioned_by,
                agent.c.provisioned_name == provisioned_name,
            )
            .values(icon=icon)
        )


def downgrade() -> None:
    pass
