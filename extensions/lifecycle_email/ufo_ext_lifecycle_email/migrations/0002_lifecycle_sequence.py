"""lifecycle_sequence"""

import sqlalchemy as sa
from alembic import op

revision: str = "lifecycle_email_0002"
down_revision: str | None = "lifecycle_email_0001"
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_table(
        "lifecycle_event",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "lifecycle_event_reached",
        "lifecycle_event",
        ["workspace_id", "member_id", "name"],
        unique=True,
    )
    op.create_table(
        "lifecycle_enrollment",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("member_id", sa.Uuid(), nullable=False),
        sa.Column("sequence", sa.Text(), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("step", sa.Integer(), nullable=False),
        sa.Column("next_due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("claimed_by", sa.Text(), nullable=True),
        sa.Column("claim_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["member_id"], ["member.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["event_id"], ["lifecycle_event.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("state in ('live', 'ended')", name="lifecycle_enrollment_state"),
        sa.CheckConstraint("step >= 0", name="lifecycle_enrollment_step"),
    )
    op.create_index(
        "lifecycle_enrollment_live",
        "lifecycle_enrollment",
        ["workspace_id", "member_id", "sequence"],
        unique=True,
        sqlite_where=sa.text("state = 'live'"),
        postgresql_where=sa.text("state = 'live'"),
    )
    op.create_index(
        "lifecycle_enrollment_due",
        "lifecycle_enrollment",
        ["next_due_at"],
        sqlite_where=sa.text("state = 'live'"),
        postgresql_where=sa.text("state = 'live'"),
    )


def downgrade() -> None:
    op.drop_index("lifecycle_enrollment_due", "lifecycle_enrollment")
    op.drop_index("lifecycle_enrollment_live", "lifecycle_enrollment")
    op.drop_table("lifecycle_enrollment")
    op.drop_index("lifecycle_event_reached", "lifecycle_event")
    op.drop_table("lifecycle_event")
