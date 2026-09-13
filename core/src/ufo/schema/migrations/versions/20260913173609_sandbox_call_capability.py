"""Bind selected-member sandbox capabilities to one live call."""

import sqlalchemy as sa
from alembic import op

revision: str = "20260913173609"
down_revision: str | None = "20260913065756"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "sandbox_call_capability",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("turn_id", sa.Uuid(), nullable=False),
        sa.Column("call", sa.Text(), nullable=False),
        sa.Column("connections", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "call <> ''",
            name="sandbox_call_capability_call_nonempty",
        ),
        sa.ForeignKeyConstraint(["turn_id"], ["turn.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workspace_id", "turn_id", "call"),
    )


def downgrade() -> None:
    op.drop_table("sandbox_call_capability")
