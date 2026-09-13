"""a monitor's audience is its conversation's, read live

The kind reads who may see a monitor off the conversation it watches, so nothing writes this
column any more. The outgoing image still selects and writes it, so the column stays, nullable,
until the revision after that image is gone drops it.
"""

import sqlalchemy as sa
from alembic import op

revision: str = "monitors_0002"
down_revision: str | None = "monitors_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("monitor") as batch:
        batch.alter_column("audience", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.execute(
        sa.text(
            "update monitor set audience = "
            "(select audience from conversation where conversation.id = monitor.conversation_id) "
            "where audience is null"
        )
    )
    with op.batch_alter_table("monitor") as batch:
        batch.alter_column("audience", existing_type=sa.Text(), nullable=False)
