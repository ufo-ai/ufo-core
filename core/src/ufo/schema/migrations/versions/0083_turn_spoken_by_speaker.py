"""key the spoken-turn index on the speaker the rail asks about"""

import sqlalchemy as sa
from alembic import op

revision: str = "0083"
down_revision: str | None = "0082"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_index("turn_spoken", table_name="turn")
    op.create_index(
        "turn_spoken",
        "turn",
        ["workspace_id", "conversation_id", "speaker_member_id"],
        postgresql_where=sa.text("speaker_member_id is not null"),
        sqlite_where=sa.text("speaker_member_id is not null"),
    )


def downgrade() -> None:
    op.drop_index("turn_spoken", table_name="turn")
    op.create_index(
        "turn_spoken",
        "turn",
        ["workspace_id", "conversation_id", "seq"],
        postgresql_where=sa.text("speaker_member_id is not null"),
        sqlite_where=sa.text("speaker_member_id is not null"),
    )
