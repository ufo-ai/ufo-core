"""inbound message queue"""

import sqlalchemy as sa
from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "inbound_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("admission_source", sa.Text(), nullable=False),
        sa.Column("context", sa.JSON(), nullable=True),
        sa.Column("speaker_member_id", sa.Uuid(), nullable=True),
        sa.Column("idempotency_key", sa.Text(), nullable=True),
        sa.Column("admitted_turn_id", sa.Uuid(), nullable=False),
        sa.Column("consumed_turn_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversation.id"]),
        sa.ForeignKeyConstraint(["speaker_member_id"], ["member.id"]),
        sa.ForeignKeyConstraint(["admitted_turn_id"], ["turn.id"]),
        sa.ForeignKeyConstraint(["consumed_turn_id"], ["turn.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("conversation_id", "seq"),
        sa.CheckConstraint(
            "admission_source in ('member', 'internal')",
            name="inbound_message_admission_source",
        ),
    )
    op.create_index(
        "inbound_message_idempotency_key",
        "inbound_message",
        ["workspace_id", "idempotency_key"],
        unique=True,
    )
    op.create_index(
        "inbound_message_pending",
        "inbound_message",
        ["conversation_id"],
        postgresql_where=sa.text("consumed_turn_id is null"),
        sqlite_where=sa.text("consumed_turn_id is null"),
    )


def downgrade() -> None:
    op.drop_index("inbound_message_pending", table_name="inbound_message")
    op.drop_index("inbound_message_idempotency_key", table_name="inbound_message")
    op.drop_table("inbound_message")
