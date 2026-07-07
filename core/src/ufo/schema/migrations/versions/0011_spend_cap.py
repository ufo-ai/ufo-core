"""spend_cap"""

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_status", type_="check")
        batch.create_check_constraint(
            "turn_status",
            "status in ('queued', 'running', 'parked', 'done', 'failed', 'cancelled')",
        )
        batch.drop_constraint("turn_terminal", type_="check")
        batch.create_check_constraint(
            "turn_terminal", "(status in ('queued', 'running', 'parked')) = (terminal is null)"
        )
    op.create_table(
        "spend_cap",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("window_seconds", sa.Integer(), nullable=False),
        sa.Column("limit_micro_usd", sa.BigInteger(), nullable=False),
        sa.Column("on_breach", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["workspace_id"], ["workspace.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("scope in ('workspace', 'member', 'agent')", name="spend_cap_scope"),
        sa.CheckConstraint(
            "(scope = 'workspace') = (subject_id is null)", name="spend_cap_subject"
        ),
        sa.CheckConstraint("window_seconds > 0", name="spend_cap_window"),
        sa.CheckConstraint("limit_micro_usd > 0", name="spend_cap_limit"),
        sa.CheckConstraint("on_breach in ('park', 'reject')", name="spend_cap_on_breach"),
        sa.UniqueConstraint(
            "workspace_id", "scope", "subject_id", "window_seconds", name="spend_cap_identity"
        ),
    )
    op.create_index("spend_cap_workspace", "spend_cap", ["workspace_id"])


def downgrade() -> None:
    op.drop_index("spend_cap_workspace", "spend_cap")
    op.drop_table("spend_cap")
    with op.batch_alter_table("turn") as batch:
        batch.drop_constraint("turn_terminal", type_="check")
        batch.create_check_constraint(
            "turn_terminal", "(status in ('queued', 'running')) = (terminal is null)"
        )
        batch.drop_constraint("turn_status", type_="check")
        batch.create_check_constraint(
            "turn_status", "status in ('queued', 'running', 'done', 'failed', 'cancelled')"
        )
