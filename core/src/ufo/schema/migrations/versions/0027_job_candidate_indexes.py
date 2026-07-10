"""job candidate indexes: every sweep's candidate read rides an index, never a table scan"""

import sqlalchemy as sa
from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index("turn_conversation_activity", "turn", ["conversation_id", "updated_at"])
    op.create_index(
        "turn_parked",
        "turn",
        ["workspace_id"],
        postgresql_where=sa.text("status = 'parked'"),
        sqlite_where=sa.text("status = 'parked'"),
    )
    op.create_index("conversation_workspace", "conversation", ["workspace_id"])
    op.create_index(
        "conversation_sandbox",
        "conversation",
        ["workspace_id"],
        postgresql_where=sa.text("sandbox_handle is not null"),
        sqlite_where=sa.text("sandbox_handle is not null"),
    )
    op.create_index("ext_store_key", "ext_store", ["extension", "key"])


def downgrade() -> None:
    op.drop_index("ext_store_key", "ext_store")
    op.drop_index("conversation_sandbox", "conversation")
    op.drop_index("conversation_workspace", "conversation")
    op.drop_index("turn_parked", "turn")
    op.drop_index("turn_conversation_activity", "turn")
