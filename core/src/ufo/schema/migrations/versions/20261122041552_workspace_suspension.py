"""Add the workspace suspension state and its operator attribution."""

import sqlalchemy as sa
from alembic import op

revision: str = "20261122041552"
down_revision: str | None = "20261110170000"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column("workspace", sa.Column("suspended_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("workspace", sa.Column("suspended_by_email", sa.Text(), nullable=True))
    op.add_column("workspace", sa.Column("suspension_reason", sa.Text(), nullable=True))
    if op.get_bind().dialect.name != "sqlite":
        op.create_check_constraint(
            "workspace_suspension_complete",
            "workspace",
            "(suspended_at is null and suspended_by_email is null and suspension_reason is null) "
            "or (suspended_at is not null and suspended_by_email is not null and "
            "suspension_reason is not null)",
        )
        op.create_check_constraint(
            "workspace_suspension_reason_nonempty",
            "workspace",
            "suspension_reason is null or suspension_reason <> ''",
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "sqlite":
        op.drop_constraint("workspace_suspension_reason_nonempty", "workspace", type_="check")
        op.drop_constraint("workspace_suspension_complete", "workspace", type_="check")
    op.drop_column("workspace", "suspension_reason")
    op.drop_column("workspace", "suspended_by_email")
    op.drop_column("workspace", "suspended_at")
