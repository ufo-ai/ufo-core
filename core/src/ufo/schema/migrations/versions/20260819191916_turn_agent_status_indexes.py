"""index turns by agent for the live-status and last-activity reads"""

import sqlalchemy as sa
from alembic import op

revision: str = "20260819191916"
down_revision: str | None = "20260819175749"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "turn_agent_live",
        "turn",
        ["agent_id", "status"],
        postgresql_where=sa.text("terminal is null"),
        sqlite_where=sa.text("terminal is null"),
    )
    op.create_index("turn_agent_activity", "turn", ["agent_id", "updated_at", "id"])


def downgrade() -> None:
    op.drop_index("turn_agent_activity", table_name="turn")
    op.drop_index("turn_agent_live", table_name="turn")
