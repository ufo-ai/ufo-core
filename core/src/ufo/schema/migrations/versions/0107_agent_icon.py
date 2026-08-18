"""agent icon"""

import sqlalchemy as sa
from alembic import op

revision: str = "0107"
down_revision: str | None = "0106"
branch_labels: str | None = None
depends_on: str | None = None

MAIN_ICON_UPDATE = sa.text("update agent set icon = 'ufo' where is_main")


def upgrade() -> None:
    op.add_column(
        "agent",
        sa.Column("icon", sa.Text, nullable=False, server_default=sa.text("'robot'")),
    )
    op.execute(MAIN_ICON_UPDATE)


def downgrade() -> None:
    op.drop_column("agent", "icon")
